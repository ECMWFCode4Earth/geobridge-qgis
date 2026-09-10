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

from . import gdal_wrapper as gb_wrapper

LOG_TAG = "GeoBridge"


class ExportTask(QgsTask):
    """Runs a zarr_to_geotiff/cds_to_geotiff export off the main thread.

    `run()` executes on a QGIS worker thread — never touch QWidgets or
    QGIS layer-tree objects there. `finished()` is guaranteed by QGIS to
    run back on the main thread, which is where it's safe to add the
    resulting layer to the project.

    No incremental progress reporting: gdal_wrapper's export functions run
    as a single GDAL read/write call with no per-chunk callback hook (the
    previous geobridge-backed version reported dask-graph task completion
    as a chunks-downloaded proxy; there's no dask here to hook into
    anymore). The dialog's progress bar is set to its indeterminate
    "busy" mode for the duration instead — same pattern already used for
    the Time Series tab's "Full history" bulk read, which has the same
    "one long call, nothing to report mid-flight" shape.
    """

    def __init__(self, description: str, kind: str, params: dict, add_to_map: bool = True):
        super().__init__(description, QgsTask.Flag.CanCancel)
        if kind not in ("zarr", "cds"):
            raise ValueError(f"kind must be 'zarr' or 'cds', got {kind!r}")
        self.kind = kind
        self.params = params
        self.add_to_map = add_to_map
        self.result_path = None
        self.exception = None

    def run(self) -> bool:
        try:
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
                        Qgis.MessageLevel.Warning,
                    )
        else:
            QgsMessageLog.logMessage(
                f"GeoBridge export failed: {self.exception}", LOG_TAG, Qgis.MessageLevel.Critical
            )
