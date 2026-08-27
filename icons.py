# -*- coding: utf-8 -*-
"""
icons
~~~~~~

Small QPainter-drawn icons shared across the dialog (geobridge_plugin_
dialog.py) and the Browse tab (browse_tab.py — built in code, so it can't
import from geobridge_plugin_dialog.py without a circular import). Drawn
rather than shipped as image files, so the plugin's asset footprint stays
at just icon.png and rendering is identical on every platform.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QPainter, QPen, QPixmap


def info_icon(size: int = 16) -> QPixmap:
    """Small circled-"i" info icon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    ring_color = QColor("#5a8fd6")
    pen = QPen(ring_color)
    pen.setWidthF(1.2)
    painter.setPen(pen)
    painter.setBrush(QColor("#eaf1fb"))
    margin = 0.8
    painter.drawEllipse(QRectF(margin, margin, size - 2 * margin, size - 2 * margin))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(ring_color)
    cx = size / 2
    painter.drawEllipse(QPointF(cx, size * 0.29), 1.3, 1.3)  # dot
    painter.drawRoundedRect(QRectF(cx - 1.1, size * 0.44, 2.2, size * 0.36), 1, 1)  # stem

    painter.end()
    return pixmap
