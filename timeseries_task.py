# -*- coding: utf-8 -*-
"""
timeseries_task
~~~~~~~~~~~~~~~~

Point time-series fetch via QgsTask, wired to the Time Series tab's
map-click handler. Runs off the main thread and supports cancellation —
clicking a new point on the map while a previous fetch is still in flight
cancels it rather than queuing behind it.

One bulk ARCO Zarr read for the whole range
(gb_wrapper.zarr_point_time_series) — the tab's old WMTS-GetFeatureInfo
"Quick" method (one request per timestep, no auth needed) has been
removed, so there is nothing to report incremental progress on or cancel
once the read has started; the isCanceled() check just skips starting it
at all.
"""

from __future__ import annotations

import traceback

from qgis.core import Qgis, QgsMessageLog, QgsTask

from . import gdal_wrapper as gb_wrapper

LOG_TAG = "GeoBridge"


class TimeSeriesTask(QgsTask):
    def __init__(self, description: str, params: dict):
        super().__init__(description, QgsTask.Flag.CanCancel)
        self.params = params
        self.samples = None
        self.exception = None
        self.traceback = None

    def run(self) -> bool:
        try:
            if self.isCanceled():
                return False
            self.samples = gb_wrapper.zarr_point_time_series(**self.params)
        except Exception as exc:  # noqa: BLE001 — surfaced via self.exception
            self.exception = exc
            # Captured here, on the worker thread, while it's still live —
            # see ExportTask.run()'s matching comment for why.
            self.traceback = traceback.format_exc()
            return False
        return True

    def finished(self, result: bool):
        if not result and self.exception is not None:
            QgsMessageLog.logMessage(
                f"GeoBridge time series failed: {self.exception}\n{self.traceback or ''}",
                LOG_TAG,
                Qgis.MessageLevel.Critical,
            )
