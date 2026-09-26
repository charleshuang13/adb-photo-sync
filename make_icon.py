#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 app 图标：深蓝圆角底 + 白色手机 + 绿色向下箭头(导出)"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QPainter, QColor, QBrush, QLinearGradient, QPen, QPolygonF, QFont
from PySide6.QtCore import Qt, QPointF, QRectF

SIZE = 1024
app = QApplication(sys.argv)

pm = QPixmap(SIZE, SIZE)
pm.fill(Qt.GlobalColor.transparent)
p = QPainter(pm)
p.setRenderHint(QPainter.RenderHint.Antialiasing)

# 圆角背景（渐变蓝）
grad = QLinearGradient(0, 0, 0, SIZE)
grad.setColorAt(0.0, QColor("#3b82f6"))
grad.setColorAt(1.0, QColor("#1e3a8a"))
p.setBrush(QBrush(grad))
p.setPen(Qt.PenStyle.NoPen)
p.drawRoundedRect(QRectF(40, 40, SIZE - 80, SIZE - 80), 180, 180)

# 手机轮廓（白）
phone = QRectF(322, 130, 380, 640)
p.setBrush(QColor("#ffffff"))
p.drawRoundedRect(phone, 60, 60)
# 屏幕（深色内屏）
screen = QRectF(352, 190, 320, 520)
p.setBrush(QColor("#0f172a"))
p.drawRoundedRect(screen, 28, 28)
# 手机顶部听筒小条
p.setBrush(QColor("#94a3b8"))
p.drawRoundedRect(QRectF(452, 152, 120, 14), 7, 7)

# 内屏里一张小照片（山+太阳 简化：圆太阳 + 山形）
p.setBrush(QColor("#fbbf24"))
p.drawEllipse(QPointF(512, 330), 44, 44)
p.setBrush(QColor("#34d399"))
tri = QPolygonF([QPointF(340, 700), QPointF(480, 480), QPointF(620, 700)])
p.drawPolygon(tri)
p.setBrush(QColor("#22d3ee"))
tri2 = QPolygonF([QPointF(470, 700), QPointF(590, 540), QPointF(700, 700)])
p.drawPolygon(tri2)

# 绿色下载箭头（从手机底部出来到屏幕底）
p.setBrush(QColor("#22c55e"))
arrow = QPolygonF([QPointF(452, 890), QPointF(572, 890),
                   QPointF(572, 812), QPointF(512, 812),
                   QPointF(452, 812)])  # 圆角柱简化
p.drawPolygon(arrow)
# 箭头头部
p.setBrush(QColor("#22c55e"))
head = QPolygonF([QPointF(448, 822), QPointF(576, 822),
                  QPointF(512, 900)])
p.drawPolygon(head)
# 箭头柱
p.drawRect(QRectF(494, 830, 36, 70))

p.end()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
pm.save(out)
print("saved", out)
