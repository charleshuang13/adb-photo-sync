#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""浏览手机文件对话框：树形懒加载设备目录，勾选文件夹拷到电脑。

用法：
    dlg = BrowserDialog(adb路径, 保存目录, parent)
    if dlg.exec() == QDialog.Accepted:
        dirs = dlg.selected_dirs()   # ['/sdcard/DCIM/Camera', ...]（已去嵌套）

设计：
- 只列文件夹，不列文件（用户只要文件夹粒度）
- 懒加载：展开哪个目录才 adb shell ls 拉那一层，后台线程跑，不卡界面
- 勾选目录 = 整目录拷贝；勾了上层目录就不用再勾里面的（收集时自动去重）
"""

import os
import queue
import shlex

import main as m
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

ROOT = "/sdcard"


# ---------------------------------------------------------------- 纯逻辑（可单测）
def parse_dir_listing(text):
    """解析 `ls -p -1` 输出 → 文件夹名列表。

    -p 让目录名带 / 后缀，非目录不带，正好用来只挑文件夹。
    """
    dirs = []
    for line in text.splitlines():
        s = line.rstrip()
        if not s.endswith("/"):
            continue
        name = s[:-1]
        if not name or name.startswith("."):
            continue
        dirs.append(name)
    return dirs


def collect_checked(root_item):
    """遍历树（含未展开节点）收集所有勾选的目录完整路径。"""
    out = []

    def walk(item):
        if item.checkState(0) == Qt.CheckState.Checked:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path:
                out.append(path)
        for i in range(item.childCount()):
            walk(item.child(i))

    for i in range(root_item.childCount()):
        walk(root_item.child(i))
    return out


def prune_nested(paths):
    """去掉嵌套勾选：勾了 /a 又勾 /a/b 时，/a/b 是多余的（整目录拷贝已包含）。

    按路径层级排序后，保留祖先、删掉其后代。
    """
    out = []
    for p in sorted(paths, key=lambda s: (s.count("/"), s)):
        if not any(p == q or p.startswith(q.rstrip("/") + "/") for q in out):
            out.append(p)
    return out


# ---------------------------------------------------------------- 后台列目录线程
class BrowseWorker(QThread):
    loaded = Signal(str, list)   # (目录路径, 子文件夹名列表)
    failed = Signal(str, str)    # (目录路径, 错误信息)

    def __init__(self, adb, parent=None):
        super().__init__(parent)
        self.adb = adb
        self._q = queue.Queue()

    def request(self, path):
        """把要列的目录丢进队列，工作线程顺序执行。"""
        self._q.put(path)

    def stop(self):
        self._q.put(None)

    def run(self):
        while True:
            path = self._q.get()
            if path is None:
                break
            quoted = shlex.quote(path)  # 目录名可能有空格，交给远端 shell 前要包引号
            try:
                rc, out, err = m.run(
                    [self.adb, "shell", "ls", "-p", "-1", quoted], timeout=30)
            except Exception as e:  # noqa
                self.failed.emit(path, str(e))
                continue
            if rc != 0:
                self.failed.emit(path, (err or "目录无法读取").strip()[:120])
            else:
                self.loaded.emit(path, parse_dir_listing(out))


# ---------------------------------------------------------------- 浏览对话框
class BrowserDialog(QDialog):
    """树形浏览手机 /sdcard，勾选文件夹，返回要拷贝的目录列表。"""

    def __init__(self, adb, dest="", autostart=True, parent=None):
        super().__init__(parent)
        self.adb = adb
        self.dest = dest or "~/Downloads"
        self._selected = []
        self._loaded = set()          # 已拉过内容的目录（防重复请求）
        self._nodes = {}              # 目录路径 → 树节点

        self.setWindowTitle("浏览手机文件")
        self.resize(520, 640)

        root_l = QVBoxLayout(self)
        root_l.setContentsMargins(14, 12, 14, 12)
        root_l.setSpacing(8)

        # 当前路径提示
        self.path_label = QLabel(f"当前位置：{ROOT}")
        self.path_label.setStyleSheet("color: #666;")
        self.path_label.setWordWrap(True)
        root_l.addWidget(self.path_label)

        # 树
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["文件夹（勾选要拷贝的，展开可看下层）"])
        self.tree.header().setStretchLastSection(True)
        self.tree.itemExpanded.connect(self._on_expanded)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        self.tree.itemChanged.connect(self._on_item_changed)
        root_l.addWidget(self.tree, 1)

        # 计数 + 落点提示
        self.count_label = QLabel("已选 0 个文件夹")
        self.count_label.setStyleSheet("font-weight: bold;")
        root_l.addWidget(self.count_label)
        dest_tip = QLabel(f"拷贝到：{os_path_display(self.dest)}（每个文件夹按名字放一个子文件夹）")
        dest_tip.setStyleSheet("color: #888; font-size: 11px;")
        dest_tip.setWordWrap(True)
        root_l.addWidget(dest_tip)

        # 按钮
        btns = QDialogButtonBox()
        self.btn_ok = QPushButton("拷贝选中文件夹")
        self.btn_ok.setStyleSheet(
            "QPushButton { font-size: 15px; font-weight: bold; padding: 6px 14px;"
            " background: #2d6cdf; color: white; border-radius: 6px; }"
            "QPushButton:hover { background: #2456b8; }"
            "QPushButton:disabled { background: #9db8e8; }")
        btn_cancel = QPushButton("取消")
        btns.addButton(btn_cancel, QDialogButtonBox.ButtonRole.RejectRole)
        btns.addButton(self.btn_ok, QDialogButtonBox.ButtonRole.AcceptRole)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self._confirm)
        root_l.addWidget(btns)

        # 根节点（手机存储），展开即列 /sdcard
        root_item = QTreeWidgetItem([f"手机存储 ({ROOT})"])
        root_item.setData(0, Qt.ItemDataRole.UserRole, ROOT)
        root_item.setFlags(root_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        root_item.setCheckState(0, Qt.CheckState.Unchecked)
        root_item.addChild(QTreeWidgetItem(["加载中…"]))
        self.tree.addTopLevelItem(root_item)
        self._nodes[ROOT] = root_item

        self.worker = BrowseWorker(adb, self)
        self.worker.loaded.connect(self._fill)
        self.worker.failed.connect(self._fill_failed)
        if autostart:
            self.worker.start()
            root_item.setExpanded(True)   # 触发 _on_expanded → 请求 /sdcard

    # ---------- 树事件
    def _on_expanded(self, item):
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path or path in self._loaded:
            return
        # 清掉占位节点，挂上“加载中”，排队拉取
        item.takeChildren()
        item.addChild(QTreeWidgetItem(["加载中…"]))
        self.worker.request(path)

    def _on_current_changed(self, cur, _prev):
        if cur is None:
            return
        path = cur.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self.path_label.setText(f"当前位置：{path}")

    def _on_item_changed(self, _item, _col):
        # 勾选状态变化 → 刷新计数 + 按钮可用性
        n = len(prune_nested(collect_checked(self.tree.invisibleRootItem())))
        self.count_label.setText(f"已选 {n} 个文件夹（勾了上层就不用再勾下层）")
        self.btn_ok.setEnabled(n > 0)

    # ---------- 拉取结果回填
    def _fill(self, path, dirs):
        item = self._nodes.get(path)
        if item is None:
            return
        self._loaded.add(path)
        item.takeChildren()
        if not dirs:
            empty = QTreeWidgetItem(["（空）"])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            item.addChild(empty)
        for name in sorted(dirs):
            child_path = path.rstrip("/") + "/" + name
            child = QTreeWidgetItem([name])
            child.setData(0, Qt.ItemDataRole.UserRole, child_path)
            child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            child.setCheckState(0, Qt.CheckState.Unchecked)
            child.addChild(QTreeWidgetItem(["加载中…"]))
            item.addChild(child)
            self._nodes[child_path] = child
        item.setExpanded(True)

    def _fill_failed(self, path, msg):
        item = self._nodes.get(path)
        if item is None:
            return
        self._loaded.add(path)   # 失败也算“看过了”，避免反复请求
        item.takeChildren()
        bad = QTreeWidgetItem([f"（无法读取：{msg}）"])
        bad.setFlags(Qt.ItemFlag.NoItemFlags)
        item.addChild(bad)

    # ---------- 确认
    def _confirm(self):
        dirs = prune_nested(collect_checked(self.tree.invisibleRootItem()))
        if not dirs:
            QMessageBox.information(self, "浏览手机文件", "先勾选要拷贝的文件夹")
            return
        self._selected = dirs
        self._shutdown()
        self.accept()

    def selected_dirs(self):
        return list(self._selected)

    # ---------- 收尾
    def _shutdown(self):
        if self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(5000)

    def closeEvent(self, ev):
        self._shutdown()
        super().closeEvent(ev)


def os_path_display(p):
    """~ 展开成完整路径再显示。"""
    return os.path.expanduser(p)
