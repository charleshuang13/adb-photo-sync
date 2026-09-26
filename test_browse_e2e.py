#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用假 adb 端到端验证浏览对话框:树加载、展开、勾选、selected_dirs。

假文件系统:
/sdcard
├── DCIM/            → Camera/ Screenshots/
│   ├── Camera/      → (空)
│   └── Screenshots/ → (空)
├── Download/        → WeChat/
│   └── WeChat/      → (空)
├── Pictures/        → (空)
└── a photo.jpg      (文件,不应出现)
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TREE = {
    "/sdcard": ["DCIM", "Download", "Pictures", "a photo.jpg"],
    "/sdcard/DCIM": ["Camera", "Screenshots"],
    "/sdcard/DCIM/Camera": [],
    "/sdcard/DCIM/Screenshots": [],
    "/sdcard/Download": ["WeChat"],
    "/sdcard/Download/WeChat": [],
    "/sdcard/Pictures": [],
}

fake = os.path.join(tempfile.mkdtemp(), "fake-adb")
with open(fake, "w") as f:
    f.write("""#!/bin/bash
# 只模拟: <adb> shell ls -p -1 <dir>
if [ "$1" = "shell" ] && [ "$2" = "ls" ]; then
  dir="$5"
  case "$dir" in
""")
    for d, items in TREE.items():
        f.write(f'    {d!r}) echo -e "{chr(10).join(i + "/" if d + "/" + i in TREE else i for i in items)}"; exit 0 ;;\n')
    f.write("""  esac
fi
exit 1
""")
os.chmod(fake, 0o755)

import browse_dialog as bd
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

dlg = bd.BrowserDialog(fake, dest="~/Downloads", autostart=False)
root = dlg.tree.topLevelItem(0)
dlg.worker.start()

failures = []


def pump(times=40):
    for _ in range(times):
        app.processEvents()
        import time
        time.sleep(0.02)


# 1. 展开根 → 只出现文件夹
root.setExpanded(True)
pump()
kids = [root.child(i).text(0) for i in range(root.childCount())]
assert kids == ["DCIM", "Download", "Pictures"], f"根目录子项错误: {kids}"
print("PASS 展开根目录,只列文件夹,文件被过滤")

# 2. 逐层展开 DCIM → Camera
dcim = next(root.child(i) for i in range(root.childCount()) if root.child(i).text(0) == "DCIM")
dcim.setExpanded(True)
pump()
camera = next(dcim.child(i) for i in range(dcim.childCount()) if dcim.child(i).text(0) == "Camera")
camera.setExpanded(True)
pump()
assert camera.childCount() == 1 and camera.child(0).text(0) == "（空）", "空目录应显示（空）"
print("PASS 深层展开 + 空目录显示")

# 3. 勾选 DCIM/Camera + Download/WeChat + 父目录 Download → 去重只留 DCIM/Camera 与 Download
camera.setCheckState(0, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.CheckState.Checked)
pump()
download = next(root.child(i) for i in range(root.childCount()) if root.child(i).text(0) == "Download")
download.setCheckState(0, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.CheckState.Checked)
pump()
wechat = download.child(0)
wechat.setExpanded(True)
pump()
wechat.setCheckState(0, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.CheckState.Checked)
pump()

sel = dlg.selected_dirs()
dlg._confirm()  # 触发收集+去重
sel = dlg.selected_dirs()
assert set(sel) == {"/sdcard/DCIM/Camera", "/sdcard/Download"}, f"去重结果错误: {sel}"
print(f"PASS 勾选+嵌套去重 → {sorted(sel)}")

dlg._shutdown()
print("\nALL E2E PASS")
