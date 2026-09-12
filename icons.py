# -*- coding: utf-8 -*-
"""
icons
~~~~~~

Small QPainter-drawn icons and tooltip helpers shared across the dialog
(geobridge_plugin_dialog.py) and the Browse tab (browse_tab.py — built in
code, so it can't import from geobridge_plugin_dialog.py without a
circular import). Icons are drawn rather than shipped as image files, so
the plugin's asset footprint stays at just icon.png and rendering is
identical on every platform.
"""

from __future__ import annotations

import html

from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QPainter, QPen, QPixmap


def wrap_tooltip(text: str, max_width: int = 320) -> str:
    """Force Qt to word-wrap a long plain-text tooltip into a readable
    multi-line box instead of one unreadably wide horizontal line — plain
    (non-rich-text) tooltips don't auto-wrap in Qt, only HTML ones do.
    ``\\n\\n`` in `text` becomes a paragraph break, a single ``\\n`` a line
    break.
    """
    paragraphs = [
        "<br>".join(html.escape(line) for line in para.split("\n"))
        for para in text.split("\n\n")
    ]
    return f'<p style="max-width:{max_width}px">' + "</p><p>".join(paragraphs) + "</p>"


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
