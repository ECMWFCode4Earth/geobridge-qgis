# -*- coding: utf-8 -*-
"""
legend_widget
~~~~~~~~~~~~~

Color-scale legend for the Search tab's WMTS preview. Drawn with plain
QPainter, same approach as ts_plot_widget.py — no matplotlib/QtCharts
dependency.

Calibrated from gb_wrapper.variable_style(), which reads arco_snapshot.yaml
directly — the same file geobridge.wmts_layer(style="default") itself
reads to pick the palette sent to ECMWF's WMTS server as the `style`
request parameter (see gb_wrapper.py's _variable_meta() docstring), so
this describes what the WMTS tiles actually show rather than an unrelated
approximation. It's a fixed "typical range" baked into that snapshot, not
recalculated per request — a value outside [value_min, value_max] for the
currently viewed date/area still just clips to the nearest end color.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QRectF, Qt
from qgis.PyQt.QtGui import QColor, QLinearGradient, QPainter, QPen
from qgis.PyQt.QtWidgets import QWidget

_MARGIN = 10
_BAR_HEIGHT = 14

# Approximate RGB stops for the palette names actually used across
# arco_snapshot.yaml (checked against the installed geobridge: viridis and
# rainbow alone cover ~98% of variable entries; the rest are cmocean
# palettes). Not pixel-exact reproductions of matplotlib/cmocean's ramps —
# close enough for a compact reference gradient, same spirit as the
# 5-point interpolated ramps geobridge's own modules/style.py uses for its
# (separate, unrelated) QML export styling.
_RAMPS = {
    "viridis": [
        (0.0, 68, 1, 84), (0.25, 59, 82, 139), (0.5, 33, 145, 140),
        (0.75, 94, 201, 98), (1.0, 253, 231, 37),
    ],
    "plasma": [
        (0.0, 13, 8, 135), (0.25, 126, 3, 168), (0.5, 204, 71, 120),
        (0.75, 248, 149, 64), (1.0, 240, 249, 33),
    ],
    "rainbow": [
        (0.0, 110, 0, 220), (0.25, 0, 120, 255), (0.5, 0, 220, 120),
        (0.75, 230, 230, 0), (1.0, 220, 0, 0),
    ],
    "balance": [
        (0.0, 24, 28, 86), (0.25, 66, 110, 161), (0.5, 246, 246, 246),
        (0.75, 172, 73, 52), (1.0, 60, 9, 17),
    ],
    "haline": [
        (0.0, 41, 24, 107), (0.25, 22, 116, 139), (0.5, 62, 164, 133),
        (0.75, 158, 190, 90), (1.0, 253, 238, 153),
    ],
    "ice": [
        (0.0, 3, 5, 18), (0.25, 32, 45, 105), (0.5, 76, 102, 166),
        (0.75, 154, 177, 209), (1.0, 255, 255, 255),
    ],
    "thermal": [
        (0.0, 3, 7, 58), (0.25, 97, 25, 113), (0.5, 176, 50, 90),
        (0.75, 224, 110, 42), (1.0, 255, 222, 52),
    ],
}
_FALLBACK_RAMP = _RAMPS["viridis"]


class VariableLegendWidget(QWidget):
    """Horizontal gradient bar + min/max/unit labels for the variable
    currently selected on the Search tab. Hidden (via set_style({})) when
    there's nothing to show — no selection yet, or the dataset isn't
    ARCO-backed so no calibration data exists."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._style = {}
        self._variable_label = ""
        self.setFixedHeight(56)

    def set_style(self, style: dict, variable_label: str = ""):
        self._style = style or {}
        self._variable_label = variable_label
        self.setVisible(bool(self._style))
        self.update()

    def paintEvent(self, event):  # noqa: N802 — Qt override
        if not self._style:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()

        vmin = self._style.get("value_min", 0.0)
        vmax = self._style.get("value_max", 1.0)
        unit = self._style.get("unit", "")
        palette = self._style.get("colormap", "viridis")
        ramp = _RAMPS.get(palette, _FALLBACK_RAMP)

        if self._variable_label:
            painter.setPen(QPen(self.palette().text().color()))
            painter.drawText(
                QRectF(_MARGIN, 0, rect.width() - 2 * _MARGIN, 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self._variable_label,
            )

        bar_rect = QRectF(
            _MARGIN, 20, max(rect.width() - 2 * _MARGIN, 1), _BAR_HEIGHT
        )
        gradient = QLinearGradient(bar_rect.left(), 0, bar_rect.right(), 0)
        for pos, r, g, b in ramp:
            gradient.setColorAt(pos, QColor(r, g, b))
        painter.fillRect(bar_rect, gradient)
        painter.setPen(QPen(self.palette().mid().color()))
        painter.drawRect(bar_rect)

        painter.setPen(QPen(self.palette().text().color()))
        unit_suffix = f" {unit}" if unit else ""
        painter.drawText(
            QRectF(_MARGIN, bar_rect.bottom() + 2, 150, 16),
            Qt.AlignmentFlag.AlignLeft, f"{vmin:.3g}{unit_suffix}",
        )
        painter.drawText(
            QRectF(rect.width() - _MARGIN - 150, bar_rect.bottom() + 2, 150, 16),
            Qt.AlignmentFlag.AlignRight, f"{vmax:.3g}{unit_suffix}",
        )

        painter.end()
