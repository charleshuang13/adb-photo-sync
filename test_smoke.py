#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离屏冒烟测试：纯逻辑单测 + GUI 实例化"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main as m
import browse_dialog as bd

failures = []

def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        failures.append(name)
        print(f"FAIL  {name}  {detail}")

# ---- parse_devices
devs = m.parse_devices("""List of devices attached
ABC12345\tdevice product:panther model:Pixel_8 device:panther transport_id:1
ABC12346\tunauthorized transport_id:2
""")
check("parse_devices 数量", len(devs) == 2, str(devs))
check("parse_devices 在线型号", devs[0][1] == "device" and devs[0][2] == "Pixel 8", str(devs))
check("parse_devices 未授权", devs[1][1] == "unauthorized", str(devs))
check("parse_devices 空", m.parse_devices("List of devices attached\n\n") == [])

# ---- status_from_devices（状态灯逻辑，纯函数，不受本机插没插手机影响。
#      之前在测试里直接调 poll_device，开发机开着模拟器时就会假报失败）
check("无设备 → 未连接",
      m.status_from_devices([]) == ("off", "未连接", "插上数据线，开启 USB 调试"),
      str(m.status_from_devices([])))
check("已连接 → 显示型号",
      m.status_from_devices([("ABC12345", "device", "Pixel 8")])[:2] == ("online", "已连接"))
check("已连接但没型号 → 退回序列号",
      m.status_from_devices([("ABC12345", "device", "")])[2] == "ABC12345")
check("多设备 → 取第一台在线",
      m.status_from_devices([("A", "unauthorized", ""), ("B", "device", "Pixel 8")])[0] == "online")
check("未授权 → 提示看手机",
      m.status_from_devices([("A", "unauthorized", "")])[0] == "unauth")
check("offline → 状态异常",
      m.status_from_devices([("A", "offline", "")])[0] == "bad")

# ---- plan_transfer（目录级/增量判断）
use, missing = m.plan_transfer(["a.jpg", "b.jpg"], [], full=False)
check("首次空目录 → 目录级", use is True and missing == [])
use, missing = m.plan_transfer(["a.jpg"], ["a.jpg"], full=True)
check("强制全量 → 目录级", use is True and missing == [])
use, missing = m.plan_transfer(["a.jpg", "b.jpg"], ["a.jpg", "b.jpg"], full=False)
check("增量已最新 → 空", use is False and missing == [])
use, missing = m.plan_transfer(["a.jpg", "b.jpg", "c.jpg"], ["a.jpg", "b.jpg"], full=False)
check("增量缺1/3 → 逐个", use is False and missing == ["c.jpg"])
use, missing = m.plan_transfer(["a.jpg", "b.jpg", "c.jpg", "d.jpg"], ["a.jpg"], full=False)
check("缺3/4>50% → 目录级", use is True)
use, missing = m.plan_transfer(["x/y/a.jpg", "x/b.jpg"], ["a.jpg", "b.jpg"], full=False)
check("远端全缺(100%) → 目录级", use is True)
use, missing = m.plan_transfer(["x/y/a.jpg", "b.jpg"], ["b.jpg"], full=False)
check("缺1/2=50% → 逐个且深层路径透传", use is False and missing == ["x/y/a.jpg"], str(missing))
use, missing = m.plan_transfer(["x/y/a.jpg", "x/y/b.jpg", "c.jpg"], ["x/y/b.jpg", "c.jpg"], full=False)
check("缺1/3 → 逐个深层路径", use is False and missing == ["x/y/a.jpg"], str(missing))

# ---- collect_sources
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)
win = m.MainWindow()
app.processEvents()
check("主窗口能建起来", win.state_label.text() != "", win.state_label.text())
check("adb 路径存在", os.path.exists(win.adb), win.adb)

# 默认只勾了相册
sources = win.collect_sources()
check("默认勾选=DCIM", sources == ["/sdcard/DCIM"], str(sources))
# 勾 Download + 自定义（中文逗号、分号混用）
for chk in win.dir_checks:
    if "Download" in chk.text():
        chk.setChecked(True)
win.custom_edit.setText("/sdcard/DCIM/Camera，/sdcard/Download/WeChat;  /sdcard/Music/")
sources = win.collect_sources()
check("多选+自定义解析", sources == ["/sdcard/DCIM", "/sdcard/Download",
                                      "/sdcard/DCIM/Camera", "/sdcard/Download/WeChat",
                                      "/sdcard/Music"], str(sources))
# 全不勾
for chk in win.dir_checks:
    chk.setChecked(False)
win.custom_edit.setText("")
check("无勾选 → 空", win.collect_sources() == [])
win.close()

# ---- browse_dialog 纯逻辑
lst = bd.parse_dir_listing("""Android/
DCIM/
Download/
Music
Pictures/
.txt_hidden/
文档/
照片 2026/
""")
check("parse 只留目录", lst == ["Android", "DCIM", "Download", "Pictures",
                                 "文档", "照片 2026"], str(lst))
check("parse 空", bd.parse_dir_listing("") == [])
check("parse 全是文件", bd.parse_dir_listing("a.jpg\nb.png\n") == [])

paths = ["/sdcard/DCIM", "/sdcard/DCIM/Camera", "/sdcard/Download", "/sdcard/Download/WeChat"]
check("prune 去嵌套", bd.prune_nested(paths) == ["/sdcard/DCIM", "/sdcard/Download"], str(bd.prune_nested(paths)))
check("prune 同名前缀不误杀", bd.prune_nested(["/sdcard/DCIM", "/sdcard/DCIM2"]) == ["/sdcard/DCIM", "/sdcard/DCIM2"])
check("prune 空", bd.prune_nested([]) == [])

# collect_checked：造一棵假树
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
from PySide6.QtCore import Qt
tw = QTreeWidget()
r1 = QTreeWidgetItem(["r1"]); r1.setData(0, Qt.ItemDataRole.UserRole, "/sdcard/DCIM")
r1.setFlags(r1.flags() | Qt.ItemFlag.ItemIsUserCheckable); r1.setCheckState(0, Qt.CheckState.Checked)
c1 = QTreeWidgetItem(["c1"]); c1.setData(0, Qt.ItemDataRole.UserRole, "/sdcard/DCIM/Camera")
c1.setFlags(c1.flags() | Qt.ItemFlag.ItemIsUserCheckable); c1.setCheckState(0, Qt.CheckState.Checked)
r1.addChild(c1)
r2 = QTreeWidgetItem(["r2"]); r2.setData(0, Qt.ItemDataRole.UserRole, "/sdcard/Download")
r2.setFlags(r2.flags() | Qt.ItemFlag.ItemIsUserCheckable); r2.setCheckState(0, Qt.CheckState.Unchecked)
tw.addTopLevelItem(r1); tw.addTopLevelItem(r2)
check("collect 勾选含子", bd.collect_checked(tw.invisibleRootItem()) == ["/sdcard/DCIM", "/sdcard/DCIM/Camera"])
check("collect+prune 合并", bd.prune_nested(bd.collect_checked(tw.invisibleRootItem())) == ["/sdcard/DCIM"])

# ---- BrowserDialog 实例化（不联网：adb 指向不存在路径 → 不启动 worker 线程就不崩）
dlg = bd.BrowserDialog("/nonexistent/adb", dest="~/Downloads", autostart=False)
dlg.show()
app.processEvents()
check("对话框有根节点", dlg.tree.topLevelItemCount() == 1)
check("对话框初始计数", dlg.count_label.text().startswith("已选 0"))
dlg.close()

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("ALL PASS")
