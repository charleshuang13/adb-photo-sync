#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手机文件导出 —— 安卓设备目录一键增量同步到 Mac
替代每次手动 adb：自动找设备、自动救活 adb server、可选任意目录、只拷新文件。
"""

import os
import shlex
import sys
import subprocess
import time
from datetime import datetime

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog, QCheckBox,
    QPlainTextEdit, QProgressBar, QMessageBox, QGroupBox, QGridLayout,
    QDialog,
)
from PySide6.QtGui import QFont

APP_NAME = "手机文件导出"

# 常用目录预设：(显示名, 设备路径)
PRESETS = [
    ("相册 DCIM", "/sdcard/DCIM"),
    ("下载 Download", "/sdcard/Download"),
    ("图片 Pictures", "/sdcard/Pictures"),
    ("文档 Documents", "/sdcard/Documents"),
    ("视频 Movies", "/sdcard/Movies"),
    ("音乐 Music", "/sdcard/Music"),
]


# ---------------------------------------------------------------- adb 定位
def _bundle_adb():
    """PyInstaller 打包后优先用内置 adb（彻底不依赖系统环境）。
    macOS bundle 布局：真二进制在 Contents/Frameworks/adb/adb，
    Resources/adb 可能只是符号链接，所以按绝对路径探测。"""
    if not getattr(sys, "frozen", False):
        return None
    contents = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    candidates = [
        os.path.join(contents, "Frameworks", "adb", "adb"),
        os.path.join(contents, "Resources", "adb", "adb"),
    ]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates += [os.path.join(meipass, "adb", "adb"),
                       os.path.join(meipass, "adb")]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def find_adb():
    """优先用打包自带的 adb，找不到再退回系统常见路径。"""
    bundled = _bundle_adb()
    if bundled:
        return bundled
    candidates = [
        os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
        "/usr/bin/adb",
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return "adb"  # 最后赌 PATH


def run(cmd, timeout=30):
    """跑命令，返回 (returncode, stdout, stderr)。不经过 shell。"""
    proc = subprocess.run(
        cmd, capture_output=True, timeout=timeout,
        text=True, errors="replace",
    )
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------- 纯逻辑（可单测）
def parse_devices(text):
    """解析 adb devices -l 输出 → [(serial, state, model)]"""
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        model = ""
        for kv in parts[2:]:
            if kv.startswith("model:"):
                model = kv[len("model:"):].replace("_", " ")
        result.append((serial, state, model))
    return result


def status_from_devices(devs):
    """设备列表 → 状态灯。返回 (颜色键, 主文案, 副文案)。

    抽成纯函数是为了能单测 —— 直接测 poll_device 会被「本机到底插没插手机」影响，
    开发机上开着模拟器时结果就不一样了。
    """
    online = [d for d in devs if d[1] == "device"]
    if online:
        serial, _, model = online[0]
        return "online", "已连接", (model or serial)
    if [d for d in devs if d[1] == "unauthorized"]:
        return "unauth", "手机未授权", "看手机，点「允许 USB 调试」"
    if [d for d in devs if d[1] in ("offline", "bootloader")]:
        return "bad", "设备状态异常", "拔插一次数据线试试"
    return "off", "未连接", "插上数据线，开启 USB 调试"


def plan_transfer(remote_files, local_files, full, dir_threshold=0.5):
    """决定怎么拷。remote_files/local_files 都是相对路径列表。
    返回 (use_dir_pull, missing_files)
    - 本地空目录 → 目录级一把梭（快）
    - 缺失文件太多（>50%）→ 目录级覆盖拉（重复拷也认了，比逐个快）
    - 增量 → 只返回本地缺失的文件
    - 强制全量 → 目录级
    """
    local_set = set(local_files)
    if full:
        return True, []
    if not local_set:
        return True, []
    missing = [f for f in remote_files if f not in local_set]
    if not missing:
        return False, []  # 已是最新，一个都不用拷
    if len(remote_files) and len(missing) / len(remote_files) > dir_threshold:
        return True, []
    if len(missing) == len(remote_files):
        return True, []
    return False, missing


def stamp():
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------- 导出线程
class SyncWorker(QThread):
    log = Signal(str)
    phase = Signal(str)          # 底部当前操作文字
    busy = Signal(bool)          # 进度条忙碌态
    progress = Signal(int)       # 0-100
    done = Signal(int, int, int)  # 新增, 跳过, 失败
    failed = Signal(str)

    def __init__(self, adb, sources, dest, full, parent=None):
        super().__init__(parent)
        self.adb = adb
        self.sources = sources      # 设备目录列表，如 /sdcard/DCIM
        self.dest = dest
        self.full = full
        self._want_stop = False
        self.new_total = 0
        self.skip_total = 0
        self.fail_total = 0

    def stop(self):
        self._want_stop = True

    # ---- 工具
    def _sh(self, args, timeout=30):
        rc, out, err = run([self.adb] + args, timeout=timeout)
        return rc, out, err

    def _adb_devices(self):
        rc, out, err = self._sh(["devices", "-l"], timeout=10)
        if rc != 0:
            return []
        return parse_devices(out)

    def _rescue_server(self):
        """adb 连不上时的自救：杀掉旧 server 重启。"""
        self.log.emit(f"[{stamp()}] adb 连接异常，尝试重启 adb server…")
        try:
            self._sh(["kill-server"], timeout=10)
            time.sleep(0.5)
            self._sh(["start-server"], timeout=15)
            time.sleep(1.2)
        except Exception as e:  # noqa
            self.log.emit(f"[{stamp()}] 重启 adb 失败: {e}")

    def _wait_device(self, tries=2):
        for i in range(tries):
            devs = self._adb_devices()
            online = [d for d in devs if d[1] == "device"]
            if online:
                return online[0]
            unauth = [d for d in devs if d[1] == "unauthorized"]
            if unauth:
                self.log.emit(f"[{stamp()}] 手机已连上但没授权 → 看手机屏幕，点「允许 USB 调试」")
                time.sleep(2)
                continue
            if i < tries - 1:
                self.log.emit(f"[{stamp()}] 没找到设备（第 {i+1} 次），重启 adb 再试…")
                self._rescue_server()
            time.sleep(1.5)
        return None

    # ---- 单个目录同步（增量核心）
    def _remote_files_recursive(self, remote_dir):
        """find 出目录下全部文件路径列表。目录不存在返回 None。"""
        rc, out, err = self._sh(["shell", "find", shlex.quote(remote_dir), "-type", "f"], timeout=30)
        if rc != 0:
            return None
        prefix = remote_dir.rstrip("/") + "/"
        files = []
        for line in out.splitlines():
            line = line.strip()
            if not line or not line.startswith(prefix):
                continue
            files.append(line[len(prefix):])  # 相对路径
        return files

    def _sync_one_dir(self, remote_dir):
        """同步单个设备目录 → dest/basename(remote_dir)。返回 (new, skip, fail)"""
        name = os.path.basename(remote_dir.rstrip("/"))
        local_root = os.path.join(self.dest, name)

        remote_files = self._remote_files_recursive(remote_dir)
        if remote_files is None:
            self.log.emit(f"[{stamp()}] [{name}] 目录不存在或无法读取，跳过: {remote_dir}")
            return 0, 0, 0
        if not remote_files:
            self.log.emit(f"[{stamp()}] [{name}] 空目录，跳过")
            return 0, 0, 0

        # 本地已有文件（相对路径）
        local_files = []
        if os.path.isdir(local_root):
            for root, _dirs, fnames in os.walk(local_root):
                for fn in fnames:
                    full = os.path.join(root, fn)
                    local_files.append(os.path.relpath(full, local_root))

        use_dir_pull, missing = plan_transfer(remote_files, local_files, self.full)
        os.makedirs(local_root, exist_ok=True)

        if use_dir_pull:
            self.log.emit(f"[{stamp()}] [{name}] 整目录拷贝 {len(remote_files)} 个文件…")
            self.phase.emit(f"正在拷贝 {name}（{len(remote_files)} 个文件）…")
            self.busy.emit(True)
            rc, _o, err = self._sh(["pull", remote_dir, self.dest], timeout=1800)
            self.busy.emit(False)
            if rc == 0:
                self.log.emit(f"[{stamp()}] [{name}] 完成，{len(remote_files)} 个")
                return len(remote_files), 0, 0
            self.log.emit(f"[{stamp()}] [{name}] 整目录拷贝失败，改逐文件重试: {err.strip()[:100]}")
            missing = remote_files

        if not missing:
            self.log.emit(f"[{stamp()}] [{name}] 已是最新（{len(remote_files)} 个都在），跳过")
            return 0, len(remote_files), 0

        self.log.emit(f"[{stamp()}] [{name}] 需要拷贝 {len(missing)} 个新文件")
        self.phase.emit(f"正在拷贝 {name}（缺 {len(missing)} 个）…")
        new = skip = fail = 0
        done_count = 0
        for rel in missing:
            if self._want_stop:
                return new, skip, fail
            remote_file = f"{remote_dir.rstrip('/')}/{rel}"
            local_file = os.path.join(local_root, rel)
            os.makedirs(os.path.dirname(local_file), exist_ok=True)
            rc, _o, err = self._sh(["pull", remote_file, local_file], timeout=600)
            if rc == 0:
                new += 1
                self.log.emit(f"[{stamp()}]     {rel}")
            else:
                fail += 1
                self.log.emit(f"[{stamp()}]     失败: {rel}  {err.strip()[:100]}")
            done_count += 1
            self.progress.emit(int(done_count / max(len(missing), 1) * 100))
        return new, skip, fail

    def _sync_dcim(self, remote_dir):
        """相册特殊处理：DCIM 下每个子目录（Camera、Screenshots…）
        平铺同步到 dest/子目录名（保持旧版行为，不套 DCIM 壳）。"""
        rc, out, _ = self._sh(["shell", "ls", shlex.quote(remote_dir)], timeout=15)
        if rc != 0:
            self.log.emit(f"[{stamp()}] [{remote_dir}] 目录不存在或无法读取，跳过")
            return 0, 0, 0
        subs = [l.strip() for l in out.splitlines()
                if l.strip() and not l.strip().startswith(".")]
        if not subs:
            self.log.emit(f"[{stamp()}] [{remote_dir}] 空目录，跳过")
            return 0, 0, 0
        self.log.emit(f"[{stamp()}] 相册子目录: {'、'.join(subs)}")
        total = (0, 0, 0)
        for sub in subs:
            n, s, f = self._sync_one_dir(f"{remote_dir.rstrip('/')}/{sub}")
            total = (total[0] + n, total[1] + s, total[2] + f)
        return total

    # ---- 主流程
    def run(self):
        self.log.emit(f"[{stamp()}] 使用的 adb: {self.adb}")
        self.log.emit(f"[{stamp()}] 目标目录: {self.dest}")
        self.log.emit(f"[{stamp()}] 模式: {'强制全量' if self.full else '增量(只拷新文件)'}")
        self.log.emit(f"[{stamp()}] 同步目录: {'、'.join(self.sources)}")

        dev = self._wait_device()
        if not dev:
            self.failed.emit("没检测到设备。\n1) 用数据线连好手机\n2) 手机上打开「开发者选项 → USB 调试」\n3) 连接后如果手机弹窗，点「允许」")
            return
        serial, _, model = dev
        self.log.emit(f"[{stamp()}] 设备在线: {serial} {model or ''}".rstrip())

        os.makedirs(self.dest, exist_ok=True)
        total = len(self.sources)
        for i, src in enumerate(self.sources, 1):
            if self._want_stop:
                self.log.emit("[!] 已取消")
                return
            self.log.emit(f"[{stamp()}] ==== ({i}/{total}) {src} ====")
            if src.rstrip("/").endswith("/DCIM"):
                n, s, f = self._sync_dcim(src)
            else:
                n, s, f = self._sync_one_dir(src)
            self.new_total += n
            self.skip_total += s
            self.fail_total += f
            self.progress.emit(0)

        self.phase.emit("完成")
        if self.new_total == 0 and self.fail_total == 0:
            self.log.emit(f"[{stamp()}] 全部都是最新的，无需拷贝")
        self.log.emit(f"[{stamp()}] 汇总: 新增 {self.new_total}，跳过 {self.skip_total}，失败 {self.fail_total}")
        self.done.emit(self.new_total, self.skip_total, self.fail_total)


# ---------------------------------------------------------------- 主窗口
class MainWindow(QMainWindow):
    COL_OFF = "#95a5a6"    # 灰 未连接
    COL_UNAUTH = "#f39c12"  # 橙 未授权
    COL_ONLINE = "#27ae60"  # 绿 在线
    COL_BAD = "#e74c3c"     # 红 异常

    def __init__(self):
        super().__init__()
        self.adb = find_adb()
        self.worker = None
        self.setWindowTitle(APP_NAME)
        self.resize(680, 700)
        self._build_ui()
        self._set_light("off", "未连接", "")
        # 状态轮询
        self.timer = QTimer(self)
        self.timer.setInterval(2500)
        self.timer.timeout.connect(self.poll_device)
        self.timer.start()
        self.log_line(f"adb: {self.adb}")

    # ---------- UI
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        # 状态行
        row = QHBoxLayout()
        self.dot = QLabel()
        self.dot.setFixedSize(14, 14)
        row.addWidget(self.dot)
        self.state_label = QLabel("未连接")
        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        self.state_label.setFont(f)
        row.addWidget(self.state_label)
        self.device_label = QLabel("")
        self.device_label.setStyleSheet("color: #888;")
        row.addWidget(self.device_label)
        row.addStretch(1)
        btn_open = QPushButton("打开目标文件夹")
        btn_open.clicked.connect(self.open_dest)
        row.addWidget(btn_open)
        root.addLayout(row)

        # 目标目录
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("保存到"))
        self.dest_edit = QLineEdit(os.path.expanduser("~/Downloads"))
        row2.addWidget(self.dest_edit, 1)
        btn_browse = QPushButton("浏览")
        btn_browse.clicked.connect(self.browse_dest)
        row2.addWidget(btn_browse)
        root.addLayout(row2)

        # 同步内容：预设勾选
        box = QGroupBox("同步内容（勾选要导出的目录）")
        grid = QGridLayout(box)
        grid.setContentsMargins(10, 6, 10, 6)
        self.dir_checks = []
        for idx, (label, path) in enumerate(PRESETS):
            chk = QCheckBox(label)
            chk.setProperty("dev_path", path)
            if label.startswith("相册"):
                chk.setChecked(True)
            self.dir_checks.append(chk)
            grid.addWidget(chk, idx // 3, idx % 3)
        root.addWidget(box)

        # 自定义路径
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("其他目录"))
        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("/sdcard/Download/WeChat、/sdcard/DCIM/Camera…（多个用逗号隔开）")
        row3.addWidget(self.custom_edit, 1)
        root.addLayout(row3)
        tip = QLabel("导出的文件放在「保存到」目录下对应名字的文件夹里，如 Camera、Download。相册会把每个子目录分开放。")
        tip.setStyleSheet("color: #888; font-size: 11px;")
        tip.setWordWrap(True)
        root.addWidget(tip)

        # 浏览手机文件（树形选择拷贝）
        self.btn_browse = QPushButton("浏览手机文件（树形勾选，拷贝指定文件夹）")
        self.btn_browse.setMinimumHeight(34)
        self.btn_browse.clicked.connect(self.browse_files)
        root.addWidget(self.btn_browse)

        # 模式
        self.inc_check = QCheckBox("只拷新文件（增量）— 取消勾选 = 全部重拷")
        self.inc_check.setChecked(True)
        root.addWidget(self.inc_check)

        # 大按钮
        self.btn_go = QPushButton("一键导出")
        self.btn_go.setMinimumHeight(46)
        self.btn_go.setStyleSheet(
            "QPushButton { font-size: 16px; font-weight: bold;"
            " background: #2d6cdf; color: white; border-radius: 8px; }"
            "QPushButton:hover { background: #2456b8; }"
            "QPushButton:disabled { background: #9db8e8; }"
        )
        self.btn_go.clicked.connect(self.start_export)
        root.addWidget(self.btn_go)

        # 进度
        self.progress = QProgressBar()
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        root.addWidget(self.progress)
        self.phase_label = QLabel(" ")
        self.phase_label.setStyleSheet("color: #666;")
        root.addWidget(self.phase_label)

        # 日志
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        mono = QFont("Menlo")
        mono.setPointSize(11)
        self.log_view.setFont(mono)
        root.addWidget(self.log_view, 1)

        self.setCentralWidget(central)

    # ---------- 日志
    def log_line(self, text):
        self.log_view.appendPlainText(text)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ---------- 状态灯
    def _set_light(self, color_key, text, device=""):
        color = {"off": self.COL_OFF, "unauth": self.COL_UNAUTH,
                 "online": self.COL_ONLINE, "bad": self.COL_BAD}[color_key]
        self.dot.setStyleSheet(
            f"background: {color}; border-radius: 7px;")
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"color: {color};")
        self.device_label.setText(device)

    # ---------- 设备轮询
    def poll_device(self):
        if self.worker and self.worker.isRunning():
            return
        try:
            rc, out, err = run([self.adb, "devices", "-l"], timeout=8)
        except Exception as e:  # noqa
            self._set_light("bad", "adb 异常", str(e)[:40])
            return
        if rc != 0:
            self._set_light("bad", "adb 异常", "")
            return
        devs = parse_devices(out)
        key, text, sub = status_from_devices(devs)
        self._set_light(key, text, sub)

    # ---------- 目录
    def browse_dest(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dest_edit.text())
        if d:
            self.dest_edit.setText(d)

    def open_dest(self):
        d = self.dest_edit.text().strip()
        if not d:
            return
        os.makedirs(d, exist_ok=True)
        subprocess.Popen(["open", d])

    # ---------- 收集来源
    def collect_sources(self):
        sources = []
        for chk in self.dir_checks:
            if chk.isChecked():
                sources.append(chk.property("dev_path"))
        custom = self.custom_edit.text().strip()
        for part in custom.replace("，", ",").replace(";", ",").replace("；", ",").split(","):
            part = part.strip().rstrip("/")
            if part:
                sources.append(part)
        return sources

    # ---------- 导出
    def start_export(self):
        if self.worker and self.worker.isRunning():
            return
        sources = self.collect_sources()
        if not sources:
            QMessageBox.warning(self, APP_NAME, "先勾选要导出的目录，或填一个设备路径")
            return
        dest = os.path.expanduser(self.dest_edit.text().strip())
        if not dest:
            QMessageBox.warning(self, APP_NAME, "请先选一个保存目录")
            return
        full = not self.inc_check.isChecked()
        self._launch(sources, dest, full)

    def _launch(self, sources, dest, full):
        """真正启动同步 worker（一键导出 / 浏览选中 共用）。"""
        self.worker = SyncWorker(self.adb, sources, dest, full)
        w = self.worker
        w.log.connect(self.log_line)
        w.phase.connect(self.phase_label.setText)
        w.busy.connect(lambda b: self.progress.setRange(0, 0) if b else self.progress.setRange(0, 100))
        w.progress.connect(self.progress.setValue)
        w.done.connect(self.on_done)
        w.failed.connect(self.on_failed)
        w.finished.connect(self.on_worker_finished)

        self.btn_go.setEnabled(False)
        self.btn_browse.setEnabled(False)
        self.btn_go.setText("导出中，别拔线…")
        self.progress.setValue(0)
        w.start()

    def browse_files(self):
        """打开手机文件树，勾选文件夹后拷贝到本地。"""
        if self.worker and self.worker.isRunning():
            return
        # 预检：设备不在线就不开浏览，直接提示
        try:
            rc, out, err = run([self.adb, "devices", "-l"], timeout=8)
        except Exception as e:  # noqa
            QMessageBox.warning(self, APP_NAME, f"adb 异常：{e}")
            return
        if rc == 0:
            online = [d for d in parse_devices(out) if d[1] == "device"]
            if not online:
                QMessageBox.warning(self, APP_NAME, "手机没连上。\n先用数据线连好手机，开启 USB 调试并允许，再点浏览。")
                return
        from browse_dialog import BrowserDialog
        dest = os.path.expanduser(self.dest_edit.text().strip())
        if not dest:
            QMessageBox.warning(self, APP_NAME, "请先选一个保存目录")
            return
        dlg = BrowserDialog(self.adb, dest, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        dirs = dlg.selected_dirs()
        if not dirs:
            return
        self.log_line(f"浏览选中 {len(dirs)} 个文件夹，开始同步…")
        self._launch(dirs, dest, full=False)

    def on_done(self, new_count, skip_count, fail_count):
        dest = self.dest_edit.text().strip()
        self.log_line(f"完成: 新增 {new_count}，跳过 {skip_count}，失败 {fail_count}")
        self.notify(
            f"新增 {new_count} 个文件" if new_count else "没有新文件",
            "已是最新" if new_count == 0 else "文件已导出到本地",
        )
        if new_count:
            subprocess.Popen(["open", os.path.expanduser(dest)])

    def on_failed(self, msg):
        self.log_line(f"失败: {msg}")
        self.notify("导出失败", msg.splitlines()[0] if msg else "")

    def on_worker_finished(self):
        self.btn_go.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.btn_go.setText("一键导出")
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.phase_label.setText(" ")

    @staticmethod
    def notify(title, text):
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{text}" with title "{title}"'],
                timeout=5, capture_output=True)
        except Exception:  # noqa
            pass

    def closeEvent(self, ev):
        if self.worker and self.worker.isRunning():
            ret = QMessageBox.question(
                self, APP_NAME, "正在导出，确定要退出吗？")
            if ret != QMessageBox.StandardButton.Yes:
                ev.ignore()
                return
            self.worker.stop()
            self.worker.wait(5000)
        ev.accept()


def main():
    QApplication.setApplicationName(APP_NAME)
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
