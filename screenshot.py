#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成界面截图 → docs/screenshot.png（README 用）。

用模拟数据展示「已连接 + 一次多目录增量导出」的完整效果，不含任何真实设备或私人路径。
跑法：QT_QPA_PLATFORM=offscreen python screenshot.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main as m
from PySide6.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "docs", "screenshot.png")

DEMO_DEVICE = "Pixel 8"
DEMO_DEST = "~/Downloads/PhoneBackup"

LOG = [
    "[12:01:05] 使用的 adb: /Applications/手机文件导出.app/Contents/Frameworks/adb/adb",
    "[12:01:06] 目标目录: /Users/me/Downloads/PhoneBackup",
    "[12:01:06] 模式: 增量(只拷新文件)",
    "[12:01:06] 同步目录: /sdcard/DCIM、/sdcard/Download、/sdcard/Pictures/Screenshots",
    "[12:01:07] 设备在线: ABC12345 Pixel 8",
    "[12:01:07] ==== (1/3) /sdcard/DCIM ====",
    "[12:01:08] 相册子目录: Camera、Screenshots",
    "[12:01:08] [Camera] 需要拷贝 32 个新文件",
    "[12:01:08]     IMG_20260907_103201.jpg",
    "[12:01:09]     IMG_20260907_103255.jpg",
    "[12:01:09] [Screenshots] 已是最新（25 个都在），跳过",
    "[12:01:09] ==== (2/3) /sdcard/Download ====",
    "[12:01:10] [Download] 整目录拷贝 84 个文件…",
    "[12:01:12] ==== (3/3) /sdcard/Pictures/Screenshots ====",
    "[12:01:12] [Screenshots] 需要拷贝 41 个新文件",
    "[12:01:13]     Screenshot_20260907_101045.png",
]


def main():
    app = QApplication(sys.argv)
    win = m.MainWindow()
    win.resize(680, 700)
    win.show()
    app.processEvents()

    win._set_light("online", "已连接", DEMO_DEVICE)
    win.dest_edit.setText(DEMO_DEST)
    for chk in win.dir_checks:
        if "Download" in chk.text():
            chk.setChecked(True)
    win.custom_edit.setText("/sdcard/Pictures/Screenshots")
    win.progress.setRange(0, 100)
    win.progress.setValue(64)
    win.phase_label.setText("正在拷贝 Screenshots（缺 41 个）…")
    win.log_view.clear()
    for line in LOG:
        win.log_line(line)

    app.processEvents()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    print("saved", OUT, win.grab().save(OUT))
    win.close()


if __name__ == "__main__":
    main()
