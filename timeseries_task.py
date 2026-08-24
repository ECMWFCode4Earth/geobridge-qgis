# -*- coding: utf-8 -*-
"""
timeseries_task
~~~~~~~~~~~~~~~~

Point time-series fetch via QgsTask, wired to the Time Series tab's
map-click handler. Runs off the main thread and supports cancellation —
clicking a new point on the map while a previous fetch is still in flight
cancels it rather than queuing behind it.

Two methods, picked by the "method" constructor arg:

- "quick": gb_wrapper.point_time_series() — one WMTS GetFeatureInfo
  request per timestep, reports incremental progress and can be
  cancelled mid-fetch.
- "zarr": gb_wrapper.zarr_point_time_series() — one bulk ARCO Zarr read
  for the whole range. Nothing to report progress on and nothing to
  cancel once the read has started, since it isn't a per-step loop; the
  isCanceled() check just skips starting it at all.
"""

from __future__ import annotations

from qgis.core import QgsTask

from . import gb_wrapper

LOG_TAG = "GeoBridge"


class TimeSeriesTask(QgsTask):
    def __init__(self, description: str, method: str, params: dict):
        super().__init__(description, QgsTask.Flag.CanCancel)
        self.method = method
        self.params = params
        self.samples = None
        self.exception = None

    def run(self) -> bool:
        try:
            if self.method == "zarr":
                if self.isCanceled():
                    return False
                self.samples = gb_wrapper.zarr_point_time_series(**self.params)
            else:
                self.samples = gb_wrapper.point_time_series(
                    progress_callback=self._on_progress,
                    is_canceled=self.isCanceled,
                    **self.params,
                )
        except Exception as exc:  # noqa: BLE001 — surfaced via self.exception
            self.exception = exc
            return False
        return True

    def _on_progress(self, done: int, total: int):
        if total:
            self.setProgress(100 * done / total)
