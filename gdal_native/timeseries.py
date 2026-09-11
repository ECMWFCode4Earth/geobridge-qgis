# -*- coding: utf-8 -*-
"""
gdal_native.timeseries
~~~~~~~~~~~~~~~~~~~~~~

Point value time series, two ways — ported from
geobridge/modules/timeseries.py.

`point_value`/`point_time_series` (the "Quick" path): WMTS GetFeatureInfo,
one lightweight HTTP request per time step. Already pure stdlib in the
original (json/urllib.request/math) — ported near-verbatim, using this
package's own wmts.wmts_layer() instead of geobridge's.

`zarr_point_time_series` (the "Full history" path): the original read the
whole point series out of the ARCO Zarr archive via xarray's `.sel(...,
method="nearest")` + `.sel(time=slice(start, end))`. This version does the
same access pattern through GDAL's Zarr multidim driver instead — find
the nearest lat/lon index, the time-range index bounds, slice the MDArray
down to a 1-D (time,) view at that point, and read it directly (no
`AsClassicDataset` needed here — there's no raster to write, just a value
series).
"""

from __future__ import annotations

import json
import logging
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional, Union

from . import wmts as wmts_mod
from .discover import discover_one

logger = logging.getLogger(__name__)

DateLike = Union[str, datetime]

_TILE_SIZE = 256
_USER_AGENT = "gdal_native"
_REQUEST_TIMEOUT = 30


class TimeSeriesError(RuntimeError):
    """Raised when a GetFeatureInfo request/response, or a Zarr point
    read, is invalid."""


@dataclass
class PointSample:
    time: datetime
    value: Optional[float]


# ---------------------------------------------------------------------------
# Client-side aggregation (Full history only — a point series is already
# scalars, so binning it costs nothing extra, unlike export_task's raster
# aggregation which needs GDAL to reduce 2-D arrays per bin)
# ---------------------------------------------------------------------------

_AGG_PERIOD_KEY = {
    "daily": lambda dt: dt.date().isoformat(),
    "weekly": lambda dt: "%d-W%02d" % dt.isocalendar()[:2],
    "monthly": lambda dt: "%04d-%02d" % (dt.year, dt.month),
    "annual": lambda dt: "%04d" % dt.year,
}
_AGG_STAT = {
    "mean": lambda values: sum(values) / len(values),
    "max": max,
    "min": min,
}


def aggregate_samples(samples: list, aggregation: str = "raw") -> list:
    """Bin chronological PointSamples into daily/weekly/monthly/annual
    buckets and reduce each bucket with mean/max/min.

    aggregation is "raw" (returns samples unchanged) or "{period}_{stat}"
    (e.g. "monthly_mean"), matching export_utils.AGGREGATION_LABELS'
    naming convention. None-valued samples are dropped before binning; a
    returned sample's time is its bucket's first real sample's timestamp
    (not a synthetic period start), so it still plots at a meaningful x
    position.
    """
    if aggregation == "raw" or not samples:
        return list(samples)

    period, _, stat = aggregation.partition("_")
    key_fn = _AGG_PERIOD_KEY.get(period)
    stat_fn = _AGG_STAT.get(stat)
    if key_fn is None or stat_fn is None:
        raise ValueError(f"Unknown aggregation: {aggregation!r}")

    buckets: dict = {}
    order = []
    for sample in samples:
        if sample.value is None:
            continue
        key = key_fn(sample.time)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(sample)

    return [
        PointSample(time=buckets[key][0].time, value=stat_fn([s.value for s in buckets[key]]))
        for key in order
    ]


# ---------------------------------------------------------------------------
# WMTS GetFeatureInfo path ("Quick")
# ---------------------------------------------------------------------------

def _lonlat_to_tile_pixel(lon: float, lat: float, zoom: int):
    """Convert lon/lat to (col, row, pixel_i, pixel_j) for Web Mercator
    (EPSG:3857) — standard slippy-map tile scheme, matching the XYZ tile
    convention wmts.py already relies on for QGIS."""
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n

    col = int(x)
    row = int(y)
    pixel_i = int((x - col) * _TILE_SIZE)
    pixel_j = int((y - row) * _TILE_SIZE)
    return col, row, pixel_i, pixel_j


def _fetch_value(url: str) -> Optional[float]:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError) as exc:
        raise TimeSeriesError(f"GetFeatureInfo request failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise TimeSeriesError(f"GetFeatureInfo response was not valid JSON: {exc}") from exc

    features = data.get("features") or []
    if not features:
        return None
    return features[0].get("properties", {}).get("value")


def point_value(
    dataset: str, variable: str, lon: float, lat: float, time: DateLike,
    zoom: int = 8, style: str = "default", descriptor=None,
) -> Optional[float]:
    """Query the cell value at a single point and time via GetFeatureInfo.
    Returns None if the point falls outside the data mask."""
    layer = wmts_mod.wmts_layer(dataset, variable, time, style=style, descriptor=descriptor)
    col, row, i, j = _lonlat_to_tile_pixel(lon, lat, zoom)
    url = layer.feature_info_url(zoom, col, row, i, j) if hasattr(layer, "feature_info_url") \
        else _feature_info_url(layer, zoom, col, row, i, j)
    return _fetch_value(url)


def _feature_info_url(layer, zoom, col, row, pixel_i, pixel_j):
    """wmts.WmtsLayer doesn't carry feature_info_url (only to_qgis()) —
    build the GetFeatureInfo URL the same way geobridge's WmtsLayer does."""
    import urllib.parse as _up
    params = {
        "SERVICE": "WMTS", "REQUEST": "GetFeatureInfo", "VERSION": "1.0.0",
        "LAYER": layer.layer_name, "STYLE": layer.style,
        "FORMAT": "image/png", "TILEMATRIXSET": layer.tile_matrix_set,
        "TILEMATRIX": str(zoom), "TILEROW": str(row), "TILECOL": str(col),
        "TIME": layer.datetime_str, "INFOFORMAT": "application/json",
        "I": str(pixel_i), "J": str(pixel_j),
    }
    return layer.base_url + "?" + _up.urlencode(params)


def point_time_series(
    *,
    dataset: str, variable: str, lon: float, lat: float,
    start: DateLike, end: DateLike, step_days: float = 1,
    zoom: int = 8, style: str = "default",
    progress_callback: Optional[Callable[[int, int], None]] = None,
    is_canceled: Optional[Callable[[], bool]] = None,
) -> list:
    """Extract a value time series at a point using WMTS GetFeatureInfo,
    one request per time step, with pacing/retry against the connection
    resets ECMWF's WMTS server can produce under back-to-back requests,
    and optional progress/cancellation for a long-running background task.
    """
    if isinstance(start, str):
        start = datetime.fromisoformat(start.replace("Z", "+00:00") if "T" in start else start)
    if isinstance(end, str):
        end = datetime.fromisoformat(end.replace("Z", "+00:00") if "T" in end else end)
    step = timedelta(days=step_days)

    descriptor = discover_one(dataset)
    if descriptor is None:
        raise TimeSeriesError(
            f"Could not discover dataset {dataset!r}. "
            "Check the identifier or call discover() to list options."
        )

    _REQUEST_PACING_SECONDS = 0.1
    _MAX_RETRIES = 2
    _RETRY_DELAY_SECONDS = 1.0

    total = max(int((end - start) / step) + 1, 1)
    samples = []
    current = start
    done = 0
    while current <= end:
        if is_canceled is not None and is_canceled():
            break

        value = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                value = point_value(
                    dataset, variable, lon, lat, current,
                    zoom=zoom, style=style, descriptor=descriptor,
                )
                break
            except TimeSeriesError:
                if attempt == _MAX_RETRIES:
                    value = None
                else:
                    time.sleep(_RETRY_DELAY_SECONDS)

        samples.append(PointSample(time=current, value=value))
        done += 1
        if progress_callback is not None:
            progress_callback(done, total)
        current += step
        time.sleep(_REQUEST_PACING_SECONDS)

    return samples


# ---------------------------------------------------------------------------
# ARCO Zarr point path ("Full history")
# ---------------------------------------------------------------------------

_LAT_TOKENS = ("latitude", "lat", "rlat", "grid_latitude", "y")
_LON_TOKENS = ("longitude", "lon", "rlon", "grid_longitude", "x")


def _nearest_index(coord_array, target: float) -> int:
    import numpy as np
    values = coord_array.ReadAsArray()
    return int(np.argmin(np.abs(values - target)))


def _time_bounds_indices(time_array, start: DateLike, end: DateLike):
    """Return (start_idx, end_idx inclusive, TimeGrid) covering [start, end]
    in the store's own time coordinate.

    Uses extract_gdal.TimeGrid rather than reading the full time array —
    confirmed by timing it directly (see TimeGrid's docstring) that a full
    read of a 13440-element time coordinate on the "timeChunked" ARCO
    flavour takes ~2 minutes (many tiny chunks), while any 1-2 element
    read takes under a second; TimeGrid computes every index/timestamp
    arithmetically from just the first two values instead.
    """
    from .extract_gdal import TimeGrid

    grid = TimeGrid.read(time_array)
    start_idx = grid.index_for(start)
    end_idx = grid.index_for(end)
    if end_idx < start_idx:
        raise TimeSeriesError(f"No timesteps found between {start!r} and {end!r} in this Zarr store.")
    return start_idx, end_idx, grid


def zarr_point_time_series(
    *,
    zarr_url: str,
    variable: str,
    lon: float,
    lat: float,
    start: DateLike,
    end: DateLike,
    auth_header: Optional[dict] = None,
) -> list:
    """Read one point's full time series directly out of an ARCO Zarr
    store via GDAL's Zarr driver — the whole range in one set of chunked
    HTTPS range-requests, rather than point_time_series()'s one
    GetFeatureInfo request per step. Snaps to the nearest grid cell (no
    interpolation).
    """
    from osgeo import gdal

    from .extract_gdal import _safe_use_exceptions  # noqa: F401 (import triggers the guard)

    headers_opt = ",".join(f"{k}: {v}" for k, v in auth_header.items()) if auth_header else None
    connection = f'ZARR:"/vsicurl/{zarr_url}"'

    config = {"GDAL_PAM_ENABLED": "NO"}
    if headers_opt:
        config["GDAL_HTTP_HEADERS"] = headers_opt

    prev = {}
    for key, val in config.items():
        prev[key] = gdal.GetConfigOption(key)
        gdal.SetConfigOption(key, val)
    try:
        root = gdal.OpenEx(connection, gdal.OF_MULTIDIM_RASTER)
        if root is None:
            raise TimeSeriesError(f"GDAL could not open Zarr store: {zarr_url}")

        group = root.GetRootGroup()
        array = group.OpenMDArray(variable)
        if array is None:
            raise TimeSeriesError(
                f"Variable '{variable}' not found in Zarr store. "
                f"Available: {group.GetMDArrayNames()}"
            )

        dims = array.GetDimensions()
        dim_names = [d.GetName() for d in dims]

        lat_i = next((i for i, n in enumerate(dim_names) if n.lower() in _LAT_TOKENS), None)
        lon_i = next((i for i, n in enumerate(dim_names) if n.lower() in _LON_TOKENS), None)
        time_i = next((i for i, n in enumerate(dim_names) if n.lower() == "time"), None)
        if lat_i is None or lon_i is None or time_i is None:
            raise TimeSeriesError(
                f"Could not identify latitude/longitude/time among dimensions {dim_names!r}"
            )

        lat_array = group.OpenMDArray(dim_names[lat_i])
        lon_array = group.OpenMDArray(dim_names[lon_i])
        time_array = group.OpenMDArray(dim_names[time_i])

        lat_idx = _nearest_index(lat_array, lat)
        lon_idx = _nearest_index(lon_array, lon)
        t_start, t_end, time_grid = _time_bounds_indices(time_array, start, end)

        # "Done: 1 point(s)" reports with no visible error have happened
        # more than once and couldn't be reproduced offline (this path
        # needs a real, authenticated Zarr read) - log the numbers behind
        # t_start/t_end so a real occurrence can actually be diagnosed
        # from QGIS's Log Messages panel (tag "GeoBridge") instead of
        # guessed at blind. Cheap enough to leave in permanently.
        try:
            from qgis.core import Qgis, QgsMessageLog
            QgsMessageLog.logMessage(
                f"zarr_point_time_series: requested {start!r}..{end!r} -> "
                f"t_start={t_start} t_end={t_end} "
                f"(grid: start_value={time_grid.start_value} step={time_grid.step} "
                f"length={time_grid.length}, epoch={time_grid.epoch} "
                f"unit_seconds={time_grid.unit_seconds})",
                "GeoBridge", Qgis.MessageLevel.Info,
            )
        except Exception:
            pass

        index_slice = []
        for i, name in enumerate(dim_names):
            if i == lat_i:
                index_slice.append(lat_idx)
            elif i == lon_i:
                index_slice.append(lon_idx)
            elif i == time_i:
                index_slice.append(slice(t_start, t_end + 1))
            else:
                index_slice.append(0)  # primary level/member

        sliced = array[tuple(index_slice)]
        raw_values = sliced.ReadAsArray()

        samples = []
        for offset, raw in enumerate(raw_values):
            py_time = time_grid.timestamp_at(t_start + offset)
            py_value = None if (raw is None or _is_nan(raw)) else float(raw)
            samples.append(PointSample(time=py_time, value=py_value))
        return samples
    finally:
        for key, val in prev.items():
            gdal.SetConfigOption(key, val)


def _is_nan(value) -> bool:
    try:
        import math as _math
        return _math.isnan(float(value))
    except (TypeError, ValueError):
        return False
