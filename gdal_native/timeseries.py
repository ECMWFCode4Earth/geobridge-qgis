# -*- coding: utf-8 -*-
"""
gdal_native.timeseries
~~~~~~~~~~~~~~~~~~~~~~

Point value time series, read out of an ARCO Zarr archive — ported from
geobridge/modules/timeseries.py's zarr_point_time_series. The original
read the whole point series via xarray's `.sel(..., method="nearest")` +
`.sel(time=slice(start, end))`; this version does the same access pattern
through GDAL's Zarr multidim driver instead — find the nearest lat/lon
index, the time-range index bounds, slice the MDArray down to a 1-D
(time,) view at that point, and read it directly (no `AsClassicDataset`
needed here — there's no raster to write, just a value series).

The tab this powers used to offer a second, WMTS-GetFeatureInfo-based
"Quick" method (no auth needed, one HTTP request per timestep) alongside
this one; that method (point_value/point_time_series) has been removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Union

DateLike = Union[str, datetime]


class TimeSeriesError(RuntimeError):
    """Raised when a Zarr point read is invalid."""


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
    HTTPS range-requests. Snaps to the nearest grid cell (no
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
        # With GDAL exceptions enabled (_safe_use_exceptions above),
        # OpenMDArray() raises its own terse RuntimeError ("<name> does
        # not exist") for a missing array instead of returning None - the
        # `array is None` branch below is dead code for that case, and
        # the user only ever sees GDAL's bare message with no list of
        # what *is* actually in the store to compare against. Catch it
        # and re-raise with that list, same as the `is None` branch does.
        from .extract_gdal import variable_not_found_message

        try:
            array = group.OpenMDArray(variable)
        except RuntimeError as exc:
            raise TimeSeriesError(
                variable_not_found_message(variable, group.GetMDArrayNames(), str(exc))
            ) from exc
        if array is None:
            raise TimeSeriesError(variable_not_found_message(variable, group.GetMDArrayNames()))

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
        except Exception:  # nosec B110
            # Diagnostic logging must never break the actual read.
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
