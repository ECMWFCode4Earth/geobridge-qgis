# -*- coding: utf-8 -*-
"""
export_task
~~~~~~~~~~~

GeoTIFF export via QgsTask, wired to the "Export to GeoTIFF" button for the
ARCO Zarr path (`kind="zarr"`) as of this release. The CDS API path
(`kind="cds"`) is implemented here but not yet reachable from the UI —
`cds_to_geotiff()` needs a full request dict (product_type, year/month/day,
etc.) that would require a dynamic form built from `fetch_form()`/
`fetch_constraints()`, a separate feature on its own.

A note on the `geobridge[zarr]` dependency tier
------------------------------------------------
gb_wrapper.export_zarr_to_geotiff()/export_cds_to_geotiff() need the heavy
`geobridge[zarr]` extra (xarray, rasterio, zarr, rioxarray, fsspec, dask).
Installing that into QGIS's bundled Python risks conflicting with the
GDAL/PROJ shared libraries QGIS has already loaded into the process before
any plugin code runs — a `pip install` can't safely undo already-resident
native libraries the way a fresh process restart can. If exports start
failing with GDAL/PROJ-related errors after installing the zarr extra, a
full QGIS restart (not just a Plugin Reloader reload) is the first thing
to try.

Usage
-----
    task = ExportTask("GeoBridge: exporting <dataset>", "zarr", {
        "dataset": ..., "variable": ..., "bbox": ..., "time_range": ...,
    }, add_to_map=True)
    QgsApplication.taskManager().addTask(task)
    # keep `task` referenced on the dialog instance — QgsApplication's task
    # manager does not keep Python-side references alive on its own.
"""

from __future__ import annotations

import os

from qgis.core import Qgis, QgsMessageLog, QgsProject, QgsRasterLayer, QgsTask

from . import gb_wrapper

LOG_TAG = "GeoBridge"


class _DaskChunkProgress:
    """Reports zarr_to_geotiff's dask-graph execution as 0-100 task progress.

    geobridge's zarr_to_geotiff() stays lazy (xarray/dask) until
    da.rio.to_raster() forces computation, at which point dask's threaded
    scheduler runs one task per Zarr chunk fetch/decompress/write step —
    each chunk is its own HTTPS request against the ARCO store, so "tasks
    completed" is a reasonable proxy for "chunks downloaded". Falls back to
    a no-op context manager if dask isn't importable yet (surfaced instead
    as GeobridgeNotInstalled by gb_wrapper itself).
    """

    def __init__(self, task: QgsTask):
        self._task = task
        self._callback = None

    def __enter__(self):
        try:
            from dask.callbacks import Callback
        except ImportError:
            return self

        task = self._task
        state = {"total": 0, "done": 0}

        class _Cb(Callback):
            def _start(self, dsk):
                state["total"] = len(dsk)
                state["done"] = 0

            def _posttask(self, key, result, dsk, s, worker_id):
                state["done"] += 1
                if state["total"]:
                    task.setProgress(min(99, 100 * state["done"] / state["total"]))

        self._callback = _Cb()
        self._callback.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._callback is not None:
            self._callback.__exit__(exc_type, exc_val, exc_tb)
        if exc_type is None:
            self._task.setProgress(100)


class ExportTask(QgsTask):
    """Runs a zarr_to_geotiff/cds_to_geotiff export off the main thread.

    `run()` executes on a QGIS worker thread — never touch QWidgets or
    QGIS layer-tree objects there. `finished()` is guaranteed by QGIS to
    run back on the main thread, which is where it's safe to add the
    resulting layer to the project.
    """

    def __init__(self, description: str, kind: str, params: dict, add_to_map: bool = True):
        super().__init__(description, QgsTask.CanCancel)
        if kind not in ("zarr", "cds"):
            raise ValueError(f"kind must be 'zarr' or 'cds', got {kind!r}")
        self.kind = kind
        self.params = params
        self.add_to_map = add_to_map
        self.result_path = None
        self.exception = None

    def run(self) -> bool:
        try:
            with _DaskChunkProgress(self):
                if self.kind == "zarr":
                    self.result_path = gb_wrapper.export_zarr_to_geotiff(**self.params)
                else:
                    self.result_path = gb_wrapper.export_cds_to_geotiff(**self.params)
        except Exception as exc:  # noqa: BLE001 — surfaced via self.exception
            self.exception = exc
            return False
        return True

    def finished(self, result: bool):
        if result and self.result_path:
            if self.add_to_map:
                layer = QgsRasterLayer(self.result_path, os.path.basename(self.result_path))
                if layer.isValid():
                    QgsProject.instance().addMapLayer(layer)
                else:
                    QgsMessageLog.logMessage(
                        f"GeoBridge export produced an invalid raster: {self.result_path}",
                        LOG_TAG,
                        Qgis.Warning,
                    )
        else:
            QgsMessageLog.logMessage(
                f"GeoBridge export failed: {self.exception}", LOG_TAG, Qgis.Critical
            )
