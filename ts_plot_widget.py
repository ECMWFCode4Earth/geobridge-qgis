# -*- coding: utf-8 -*-
"""
ts_plot_widget
~~~~~~~~~~~~~~

A dependency-free line-chart widget for the Time Series tab. Drawn with
plain QPainter rather than matplotlib/pyqtgraph/QtCharts — none of those
are guaranteed to be importable inside QGIS's bundled Python, whereas
PyQt itself always is.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import QPointF, QRectF, Qt
from qgis.PyQt.QtGui import QColor, QPainter, QPen
from qgis.PyQt.QtWidgets import QWidget

_MARGIN_LEFT = 60
_MARGIN_RIGHT = 12
_MARGIN_TOP = 14
_MARGIN_BOTTOM = 26
_LINE_COLOR = QColor("#2b7de9")


class TimeSeriesPlotWidget(QWidget):
    """Simple line plot of PointSample.time (x) vs .value (y).

    set_samples() accepts the list of geobridge.PointSample the Time
    Series tab fetches; None values (point outside the data mask) break
    the line rather than being interpolated across.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._samples = []
        self.setMinimumHeight(120)

    def set_samples(self, samples):
        self._samples = samples or []
        self.update()

    def paintEvent(self, event):  # noqa: N802 — Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, self.palette().base())

        valid = [s for s in self._samples if s.value is not None]
        if len(valid) < 2:
            painter.setPen(QPen(self.palette().text().color()))
            painter.drawText(rect, Qt.AlignCenter, "No data to plot yet.")
            painter.end()
            return

        plot_rect = QRectF(
            _MARGIN_LEFT, _MARGIN_TOP,
            max(rect.width() - _MARGIN_LEFT - _MARGIN_RIGHT, 1),
            max(rect.height() - _MARGIN_TOP - _MARGIN_BOTTOM, 1),
        )

        times = [s.time for s in valid]
        values = [s.value for s in valid]
        t_min, t_max = min(times), max(times)
        v_min, v_max = min(values), max(values)
        if v_min == v_max:
            v_min -= 1
            v_max += 1
        t_span = (t_max - t_min).total_seconds() or 1.0

        def x_of(t):
            return plot_rect.left() + (t - t_min).total_seconds() / t_span * plot_rect.width()

        def y_of(v):
            return plot_rect.bottom() - (v - v_min) / (v_max - v_min) * plot_rect.height()

        axis_pen = QPen(self.palette().mid().color())
        painter.setPen(axis_pen)
        painter.drawLine(plot_rect.bottomLeft(), plot_rect.bottomRight())
        painter.drawLine(plot_rect.topLeft(), plot_rect.bottomLeft())

        text_pen = QPen(self.palette().text().color())
        painter.setPen(text_pen)
        painter.drawText(
            QRectF(0, plot_rect.top() - 8, _MARGIN_LEFT - 6, 16),
            Qt.AlignRight | Qt.AlignVCenter, f"{v_max:.3g}",
        )
        painter.drawText(
            QRectF(0, plot_rect.bottom() - 8, _MARGIN_LEFT - 6, 16),
            Qt.AlignRight | Qt.AlignVCenter, f"{v_min:.3g}",
        )
        painter.drawText(
            QRectF(plot_rect.left(), plot_rect.bottom() + 4, 160, 20),
            Qt.AlignLeft, t_min.strftime("%Y-%m-%d"),
        )
        painter.drawText(
            QRectF(plot_rect.right() - 160, plot_rect.bottom() + 4, 160, 20),
            Qt.AlignRight, t_max.strftime("%Y-%m-%d"),
        )

        # Draw across *all* samples (not just `valid`) so a None value
        # breaks the line into separate segments instead of interpolating
        # straight through a gap in the data.
        line_pen = QPen(_LINE_COLOR)
        line_pen.setWidthF(1.6)
        painter.setPen(line_pen)
        prev_point = None
        for sample in self._samples:
            if sample.value is None:
                prev_point = None
                continue
            point = QPointF(x_of(sample.time), y_of(sample.value))
            if prev_point is not None:
                painter.drawLine(prev_point, point)
            prev_point = point

        painter.end()
