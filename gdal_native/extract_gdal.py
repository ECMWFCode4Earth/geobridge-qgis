# -*- coding: utf-8 -*-
"""
gdal_native.extract_gdal
~~~~~~~~~~~~~~~~~~~~~~~~

GDAL-native replacement for geobridge's xarray/rioxarray/zarr-based
extraction (modules/extract.py, modules/cds_download.py's conversion
half). This is the module that exists specifically to avoid the crash
this branch was started to fix: a Windows access violation inside
pyproj's compiled `_CRS.__init__`, reached via
`rioxarray.write_crs()` <- `xarray` <- `geobridge.zarr_to_geotiff()`.

Design difference from the original: rather than hand-detecting lat/lon
dimensions and manually stacking non-spatial dimensions (time, level,
ensemble) into bands the way xarray/geobridge did, this leans on GDAL's
own NetCDF/GRIB/Zarr drivers, which already unroll non-spatial dimensions
into bands natively (one band per time/level combination, with
descriptive band metadata) - simpler and it's the same driver code QGIS
itself already uses everywhere else in the process, not a second,
separately pip-installed geospatial stack.

Known simplification vs. the original (documented, not hidden): CDS's
occasional ZIP-of-structurally-different-products response merges
mismatched grids less gracefully here than the original's per-file
DataArray list did. Revisit if/when that turns out to matter in practice.
"""

from __future__ import annotations

import re
import tempfile
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from osgeo import gdal


def _safe_use_exceptions() -> None:
    """gdal.UseExceptions() imports gdal_array, which needs a numpy build
    compatible with however GDAL's Python bindings were compiled. That's
    always true for QGIS's own bundled numpy (same install, guaranteed
    matching); it can only break if some other, separately pip-installed
    numpy is shadowing it on sys.path — exactly the class of problem this
    whole branch exists to stop causing. Never let that take the plugin
    down: everything in this module operates on gdal.Dataset/MDArray
    objects directly, not raw numpy arrays, so exceptions-mode is a nicer
    error-reporting style here, not a hard requirement.
    """
    try:
        gdal.UseExceptions()
    except Exception:  # nosec B110
        # Deliberately unconditional - see docstring above.
        pass


_safe_use_exceptions()


class ExtractError(Exception):
    """Raised when a downloaded/remote file can't be converted to GeoTIFF."""


_SHARING_VIOLATION_MARKERS = ("being used by another process", "winerror 32", "sharing violation")


def _gdal_open_with_retry(path: str, attempts: int = 5, delay: float = 0.2, allowed_drivers=None):
    """gdal.Open(path), retrying briefly on a Windows sharing-violation.

    This is the first GDAL-level open of a file cds_to_geotiff() just
    finished downloading and closed moments ago - sniff_format()'s own
    plain-Python open() already retries the *very first* read for the
    same reason (Defender's real-time scan can briefly lock a freshly-
    written file), but that clearing one lock doesn't guarantee GDAL's
    own open() right after won't hit a fresh one - confirmed happening
    in practice even with sniff_format's retry already in place. GDAL
    exceptions here are a RuntimeError with the OS's own message text
    embedded (no `.winerror` attribute to check, unlike a plain Python
    OSError), so this matches on that text instead.

    `allowed_drivers` restricts which driver GDAL is allowed to pick,
    rather than letting it auto-detect by content — see
    _open_netcdf_variable's docstring for why this matters for NetCDF4
    files specifically (their container format is byte-for-byte HDF5).
    """
    last_exc = None
    for _ in range(attempts):
        try:
            if allowed_drivers:
                return gdal.OpenEx(path, allowed_drivers=allowed_drivers)
            return gdal.Open(path)
        except RuntimeError as exc:
            if not any(marker in str(exc).lower() for marker in _SHARING_VIOLATION_MARKERS):
                raise
            last_exc = exc
            time.sleep(delay)
    raise last_exc


def variable_not_found_message(variable: str, available: list, detail: str = "") -> str:
    """Shared "variable not found in Zarr store" text for extract_gdal.py
    and timeseries.py. An *empty* `available` list is the tell: GDAL
    could open the store's connection but found nothing to list at all,
    which in practice has meant an old bundled GDAL that doesn't
    understand this store's Zarr V3 metadata (zarr.json) - it opens the
    connection fine, just can't enumerate anything in it, and any
    specific variable then looks like it "doesn't exist" - confirmed
    happening for real: QGIS 3.34 (GDAL ~3.7/3.8, predates GDAL 3.9's
    Zarr V3 support) failed exactly this way on a store this plugin
    reads fine elsewhere on QGIS 3.40 (GDAL 3.11). Not every dataset
    needs Zarr V3 (many are still V2), so this is a hint, not a
    diagnosis - only shown when the symptom actually matches.
    """
    detail_part = f" ({detail})" if detail else ""
    msg = f"Variable '{variable}' not found in Zarr store{detail_part}. Available: {available}"
    if not available:
        msg += (
            " — an empty list here usually means your QGIS/GDAL version is too old "
            "to read this store's format (this dataset may need Zarr V3 support, "
            "added in GDAL 3.9 / roughly QGIS 3.38+); try upgrading QGIS."
        )
    return msg


def _open_variable_array(group: "gdal.Group", variable: str):
    """group.OpenMDArray(variable), raising ExtractError with the list of
    what's actually in the store either way it can fail: returning None
    (checked explicitly), or - with GDAL exceptions enabled - raising its
    own terse RuntimeError ("<name> does not exist") that on its own
    gives no clue what the array is actually called instead."""
    try:
        array = group.OpenMDArray(variable)
    except RuntimeError as exc:
        raise ExtractError(
            variable_not_found_message(variable, group.GetMDArrayNames(), str(exc))
        ) from exc
    if array is None:
        raise ExtractError(variable_not_found_message(variable, group.GetMDArrayNames()))
    return array


# ---------------------------------------------------------------------------
# Variable matching — mirrors geobridge's normalise-and-compare approach
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _band_matches(want_norm: str, band: "gdal.Band", subdataset_desc: str = "") -> bool:
    if not want_norm:
        return True
    md = band.GetMetadata() or {}
    candidates = [subdataset_desc]
    for key in (
        "NETCDF_VARNAME", "long_name", "standard_name",
        "GRIB_ELEMENT", "GRIB_SHORT_NAME", "GRIB_COMMENT",
    ):
        if key in md:
            candidates.append(md[key])
    return any(want_norm == _norm(c) for c in candidates if c)


# ---------------------------------------------------------------------------
# NetCDF / GRIB → classic GDAL dataset
# ---------------------------------------------------------------------------

def _open_netcdf_variable(path: Path, variable: str) -> "gdal.Dataset":
    """Open the NetCDF subdataset matching *variable* (or the only one, or
    all bands of the first grid subdataset if no match/variable given).

    Forces GDAL's netCDF driver rather than letting it auto-detect: a
    NetCDF4 file's container format is literally HDF5 (same magic
    bytes our own sniff_format() checks), so an unrestricted gdal.Open()
    can end up handed to GDAL's plain HDF5 driver instead — which has
    no idea about CF-convention lat/lon coordinates, so every subdataset
    it opens comes back with no affine geotransform and no GCPs at all
    (confirmed happening in practice for some CDS "derived" datasets,
    e.g. derived-era5-land-daily-statistics: "HDF5:...://u10" instead of
    "NETCDF:...":u10", failing every warp/export downstream with "There
    is no affine transformation and no GCPs").
    """
    root = _gdal_open_with_retry(str(path), allowed_drivers=["netCDF"])
    if root is None:
        raise ExtractError(f"GDAL could not open {path} as NetCDF.")

    subdatasets = root.GetSubDatasets()
    if not subdatasets:
        # Single-variable file with no subdataset split — use directly.
        return root

    want = _norm(variable) if variable else ""
    if want:
        for sub_path, desc in subdatasets:
            if want == _norm(desc.split(" ")[0]) or want in _norm(desc):
                ds = gdal.Open(sub_path)
                if ds is not None:
                    return ds

    if len(subdatasets) == 1 or not want:
        ds = gdal.Open(subdatasets[0][0])
        if ds is not None:
            return ds

    raise ExtractError(
        f"Variable '{variable}' not found among NetCDF subdatasets: "
        f"{[desc for _, desc in subdatasets]}"
    )


def _open_grib_bands(path: Path, variable: str) -> "gdal.Dataset":
    """Open a GRIB file, keeping only bands matching *variable* (or all,
    if no variable given / nothing matches)."""
    ds = _gdal_open_with_retry(str(path))
    if ds is None:
        raise ExtractError(f"GDAL could not open {path} as GRIB.")

    want = _norm(variable) if variable else ""
    if not want:
        return ds

    matching = [i for i in range(1, ds.RasterCount + 1) if _band_matches(want, ds.GetRasterBand(i))]
    if not matching:
        return ds  # nothing matched — caller gets every band, same fallback as the original
    if len(matching) == ds.RasterCount:
        return ds

    band_list_path = f"/vsimem/{path.stem}_bands.vrt"
    vrt = gdal.BuildVRT(band_list_path, [str(path)] * len(matching), bandList=matching)
    vrt = None  # noqa: F841 — flush
    return gdal.Open(band_list_path)


# ---------------------------------------------------------------------------
# Public: local NetCDF/GRIB/ZIP file → GeoTIFF
# ---------------------------------------------------------------------------

def raster_file_to_geotiff(
    src_path: Path,
    variable: str,
    bbox: Optional[tuple],
    output_path: Path,
    cog: bool = True,
) -> Path:
    """Convert a downloaded CDS result (NetCDF, GRIB, or ZIP of either) to
    GeoTIFF using GDAL directly — see module docstring for why not xarray."""
    from .cds_download import sniff_format

    fmt = sniff_format(src_path)

    if fmt == "zip":
        extract_dir = Path(tempfile.mkdtemp(prefix="cds_zip_"))
        with zipfile.ZipFile(src_path) as zf:
            zf.extractall(extract_dir)
        members = sorted(
            p for p in extract_dir.rglob("*")
            if p.suffix.lower() in (".nc", ".nc4", ".grib", ".grib2", ".grb")
        )
        if not members:
            raise ExtractError(
                f"CDS returned a ZIP with no NetCDF/GRIB files: "
                f"{[p.name for p in extract_dir.rglob('*')]}"
            )
        if len(members) == 1:
            return raster_file_to_geotiff(members[0], variable, bbox, output_path, cog=cog)
        # Multiple structurally-different products: convert each to its own
        # GeoTIFF and let the caller/UI know there's more than one result
        # rather than silently merging mismatched grids.
        results = []
        for i, member in enumerate(members):
            member_out = output_path.with_name(f"{output_path.stem}_{i}{output_path.suffix}")
            results.append(
                raster_file_to_geotiff(member, variable, bbox, member_out, cog=cog)
            )
        return results[0]  # primary result; the rest are written alongside it

    if fmt == "netcdf":
        ds = _open_netcdf_variable(src_path, variable)
    elif fmt == "grib":
        ds = _open_grib_bands(src_path, variable)
    else:
        raise ExtractError(
            f"{src_path.name} is not a recognised NetCDF/GRIB file "
            "(the CDS API may have returned an error page instead of data)."
        )

    if ds.GetSpatialRef() is None:
        ds.SetSpatialRef(_wgs84_srs())

    return _write_geotiff(ds, bbox, output_path, cog=cog)


def _wgs84_srs():
    from osgeo import osr
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    return srs


def _write_geotiff(ds: "gdal.Dataset", bbox: Optional[tuple], output_path: Path, cog: bool) -> Path:
    """Write *ds* to *output_path*, cropped to *bbox* if given.

    Uses gdal.Warp (not gdal.Translate's projWin) for the bbox case:
    projWin assumes a standard north-up (negative pixel-height)
    geotransform and silently computes a negative window height on a
    south-up source — confirmed by testing against a real ARCO Zarr slice,
    which GDAL auto-orients south-up (positive pixel height) from that
    store's own coordinate array. Warp's outputBounds handles either
    orientation correctly since it works in georeferenced coordinates, not
    raw pixel offsets.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_format = "COG" if cog else "GTiff"
    creation_options = [] if cog else ["COMPRESS=DEFLATE"]

    try:
        if bbox:
            west, south, east, north = bbox
            src_srs = ds.GetSpatialRef()
            src_srs_wkt = src_srs.ExportToWkt() if src_srs else "EPSG:4326"
            gdal.Warp(
                str(output_path), ds,
                outputBounds=(west, south, east, north),
                srcSRS=src_srs_wkt, dstSRS=src_srs_wkt,
                format=out_format, creationOptions=creation_options,
            )
        else:
            gdal.Translate(
                str(output_path), ds,
                format=out_format, creationOptions=creation_options,
            )
    except RuntimeError as exc:
        if bbox and ("no intersection" in str(exc).lower() or "empty" in str(exc).lower()):
            raise ExtractError(
                f"Spatial subset {bbox} selects no data from this file. "
                "bbox must be (west, south, east, north) and overlap the request area."
            ) from exc
        raise
    return output_path


# ---------------------------------------------------------------------------
# Public: ARCO Zarr → GeoTIFF, via GDAL's Zarr multidim driver
# ---------------------------------------------------------------------------

_LAT_TOKENS = ("latitude", "lat", "rlat", "grid_latitude", "y")
_LON_TOKENS = ("longitude", "lon", "rlon", "grid_longitude", "x")


def _parse_time_unit(time_array: "gdal.MDArray"):
    """Return (epoch: datetime, unit_seconds: int) from *time_array*'s
    `units`/`unit` attribute ("<days|hours|seconds> since <epoch>", CF
    convention) — same convention the ARCO store's own time coordinate
    uses (verified: "days since 1970-01-01" during this branch's testing).
    """
    import re as _re
    from datetime import datetime

    attrs = {a.GetName(): a.Read() for a in time_array.GetAttributes()}
    unit_str = attrs.get("units") or attrs.get("unit") or "days since 1970-01-01"
    m = _re.match(r"(\w+)\s+since\s+(.+)", unit_str.strip())
    if not m:
        raise ExtractError(f"Unrecognised time unit on Zarr store: {unit_str!r}")
    unit_name, epoch_str = m.group(1).lower(), m.group(2).strip()
    epoch = datetime.fromisoformat(epoch_str.replace("Z", "+00:00").split("+")[0])
    unit_seconds = {"days": 86400, "hours": 3600, "minutes": 60, "seconds": 1}.get(
        unit_name.rstrip("s") + "s", 86400
    )
    return epoch, unit_seconds


def _datetime_to_time_value(dt_like, epoch, unit_seconds) -> float:
    from datetime import datetime as _datetime
    if isinstance(dt_like, _datetime):
        dt = dt_like
    else:
        dt = _datetime.fromisoformat(str(dt_like).replace("Z", "+00:00").split("+")[0])
    return (dt - epoch).total_seconds() / unit_seconds


class TimeGrid:
    """A time coordinate's (epoch, unit_seconds, start_value, step, length)
    — enough to compute any index/timestamp arithmetically.

    Deliberately avoids ever reading the full time coordinate array: on
    the "timeChunked" ARCO flavour (short time-run per chunk — the one
    the bbox+range export path needs, for the opposite reason a point
    query wants "geoChunked"), the time coordinate mirrors that same
    fine chunking, so a full read touches thousands of tiny chunks —
    confirmed by timing it directly: 13440 elements via .ReadAsArray()
    took 119s, while reading any 1-2 elements at an arbitrary position
    took under a second. This assumes evenly-spaced steps, which is true
    for every ARCO time coordinate checked so far (confirmed against real
    data: three widely-separated single-element reads all landed exactly
    on start + index*step).
    """

    def __init__(self, epoch, unit_seconds, start_value, step, length):
        self.epoch = epoch
        self.unit_seconds = unit_seconds
        self.start_value = start_value
        self.step = step
        self.length = length

    @classmethod
    def read(cls, time_array: "gdal.MDArray") -> "TimeGrid":
        epoch, unit_seconds = _parse_time_unit(time_array)
        length = time_array.GetDimensions()[0].GetSize()
        first_two = time_array[0:min(2, length)].ReadAsArray()
        start_value = float(first_two[0])
        step = float(first_two[1] - first_two[0]) if length > 1 else 0.0

        # _parse_time_unit falls back to a hardcoded "days since
        # 1970-01-01" when it can't find/parse a units attribute on
        # time_array - confirmed actually happening against a real store
        # (ERA5's geoChunked time coordinate), where the raw values
        # turned out to be in seconds, not days. That mismatch doesn't
        # raise or produce an obviously-wrong result: index_for() still
        # returns *some* in-bounds index, it's just the wrong one, and
        # because the mismatch dwarfs any real requested date range, the
        # computed start/end index for two dates weeks apart round to the
        # same value - a point-series fetch that silently comes back with
        # exactly 1 sample regardless of the requested range, no error at
        # all. Sanity-check against the actual first value: if the
        # resulting date isn't plausible for climate data (or wildly
        # outside datetime's own year 1-9999 range, which the wrong unit
        # can easily overflow), unit_seconds is wrong - try the other
        # common CF units and keep whichever puts that first timestamp
        # closest to now.
        def _date_at(candidate_unit_seconds):
            try:
                return epoch + timedelta(seconds=start_value * candidate_unit_seconds)
            except (OverflowError, OSError):
                return None

        first_date = _date_at(unit_seconds)
        if first_date is None or not (datetime(1850, 1, 1) <= first_date <= datetime(2100, 1, 1)):
            # Naive local time, not UTC - fine here, the 4 unit_seconds
            # candidates are years/centuries apart, so a same-day offset
            # never changes which one is closest.
            now = datetime.now()

            def _distance(candidate_unit_seconds):
                candidate_date = _date_at(candidate_unit_seconds)
                if candidate_date is None:
                    return float("inf")
                return abs((candidate_date - now).total_seconds())

            unit_seconds = min((1, 60, 3600, 86400), key=_distance)

        return cls(epoch, unit_seconds, start_value, step, length)

    def index_for(self, dt_like) -> int:
        target = _datetime_to_time_value(dt_like, self.epoch, self.unit_seconds)
        if self.step == 0:
            return 0
        idx = round((target - self.start_value) / self.step)
        return max(0, min(self.length - 1, idx))

    def timestamp_at(self, index: int):
        value = self.start_value + index * self.step
        # round() the seconds: floating-point day/hour-fraction arithmetic
        # otherwise leaves sub-second noise (confirmed: ~1.4ms) on what are
        # exact on-the-hour timestamps in every real ARCO store checked.
        return self.epoch + timedelta(seconds=round(value * self.unit_seconds))


def _find_time_index(time_array: "gdal.MDArray", time_value: str) -> int:
    """Resolve an ISO datetime string to the nearest index in *time_array*."""
    return TimeGrid.read(time_array).index_for(time_value)


def _time_range_indices(time_array: "gdal.MDArray", start, end):
    """Return (start_idx, end_idx inclusive, TimeGrid) covering [start, end]
    in *time_array*'s own coordinate."""
    grid = TimeGrid.read(time_array)
    start_idx = grid.index_for(start)
    end_idx = grid.index_for(end)
    if end_idx < start_idx:
        raise ExtractError(f"No timesteps found between {start!r} and {end!r} in this Zarr store.")
    return start_idx, end_idx, grid


def _coord_index_bounds(coord_array: "gdal.MDArray", lo: float, hi: float):
    """Return (start_idx, end_idx inclusive, values) of *coord_array*
    covering [lo, hi] — with a 1-cell margin on each side so a following
    exact Warp crop always has real data up to the requested edge, and
    handling a descending coordinate (common for latitude, high-to-low)
    by sorting the matched positions rather than assuming ascending
    order. Returns the full coordinate array too (already read to find
    the bounds) so the caller can build a geotransform from real values —
    see _geotransform_from_coords for why that's necessary here."""
    import numpy as np

    values = coord_array.ReadAsArray()
    matches = np.where((values >= min(lo, hi)) & (values <= max(lo, hi)))[0]
    if len(matches) == 0:
        # Nothing exactly inside the range — fall back to the single
        # nearest index so a small/edge bbox still returns *something*
        # rather than an empty selection.
        mid = (lo + hi) / 2
        nearest = int(np.argmin(np.abs(values - mid)))
        return nearest, nearest, values
    start_idx = max(0, int(matches.min()) - 1)
    end_idx = min(len(values) - 1, int(matches.max()) + 1)
    return start_idx, end_idx, values


def _geotransform_from_coords(lat_values, lat_lo: int, lon_values, lon_lo: int):
    """Build a geotransform directly from real coordinate values, for the
    (lat_lo:..., lon_lo:...) sub-window — bypassing AsClassicDataset's own
    geotransform inference.

    Necessary because AsClassicDataset returns a bogus identity
    geotransform (origin (0,0), 1-degree pixels) when BOTH spatial
    dimensions are simultaneously range-sliced — confirmed by testing:
    a single-axis-sliced 2-D view (zarr_to_geotiff's case, only time
    sliced to one index, lat/lon kept as a bare ":" full slice) gets a
    correct geotransform; slicing lat AND lon to sub-ranges together
    (this function's case, needed to avoid downloading a whole
    region's worth of chunks for a tight bbox) does not.

    Matches the convention already confirmed correct for a full-extent
    slice in this same store (ascending lat/lon index order, positive
    pixel height - "south-up" storage; the final gdal.Warp step already
    normalises orientation regardless, same as the single-instant path).
    """
    lat_step = float(lat_values[1] - lat_values[0]) if len(lat_values) > 1 else 0.1
    lon_step = float(lon_values[1] - lon_values[0]) if len(lon_values) > 1 else 0.1
    origin_x = float(lon_values[lon_lo]) - 0.5 * lon_step
    origin_y = float(lat_values[lat_lo]) - 0.5 * lat_step
    return (origin_x, lon_step, 0.0, origin_y, 0.0, lat_step)


def _spatial_axis_indices(dim_names: list) -> tuple:
    """Return (x_local_index, y_local_index) into a 2-remaining-dimension
    slice, identified by name rather than assumed position — mirrors
    geobridge's own _spatial_dims() name-matching approach.

    Substring match, not exact: GDAL renames a dimension that was actually
    range-sliced (not a bare ":" full slice) to something like
    "subset_latitude_99_1_102" rather than keeping "latitude" — confirmed
    by testing zarr_range_to_geotiff's bbox-restricted read, which an
    exact-equality version of this check missed entirely.
    """
    lower = [n.lower() for n in dim_names]
    x_idx = next((i for i, n in enumerate(lower) if any(t in n for t in _LON_TOKENS)), None)
    y_idx = next((i for i, n in enumerate(lower) if any(t in n for t in _LAT_TOKENS)), None)
    if x_idx is None or y_idx is None:
        raise ExtractError(
            f"Could not identify latitude/longitude among dimensions {dim_names!r}"
        )
    return x_idx, y_idx


def zarr_to_geotiff(
    zarr_url: str,
    variable: str,
    time_value: Optional[str],
    bbox: Optional[tuple],
    output_path: Path,
    auth_header: Optional[dict] = None,
    cog: bool = True,
) -> Path:
    """Read one variable at one time step from a remote ARCO Zarr store and
    write a GeoTIFF, using GDAL's Zarr driver over /vsicurl/ — validated
    working against a real authenticated ECMWF ARCO store during this
    branch's investigation (gdalmdiminfo against
    arco.datastores.ecmwf.int/.../geoChunked.zarr, full 4-D structure read
    back correctly once the Authorization: Bearer header was supplied; a
    single time-slice band written out correctly with the right
    lon/lat-ordered geotransform, confirmed against known raw coordinates).

    *time_value* is an ISO datetime string resolved to the nearest index in
    the store's own `time` coordinate. Every other non-spatial dimension
    (elevation, ensemble member, ...) is sliced to index 0 — the primary
    surface/first level, matching what the WMTS preview for the same
    dataset shows by default.
    """
    headers_opt = ",".join(f"{k}: {v}" for k, v in auth_header.items()) if auth_header else None
    connection = f'ZARR:"/vsicurl/{zarr_url}"'

    # GDAL_PAM_ENABLED=NO: without it, GDAL tries to persist statistics it
    # gathers while reading back to a .aux.xml sidecar next to the *source*
    # — which is a read-only remote HTTPS URL here, so that write always
    # fails. Harmless (caught during dataset cleanup, doesn't affect the
    # actual output file) but noisy; confirmed during testing.
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
            raise ExtractError(f"GDAL could not open Zarr store: {zarr_url}")

        group = root.GetRootGroup()
        array = _open_variable_array(group, variable)

        dims = array.GetDimensions()
        dim_names = [d.GetName() for d in dims]

        # Build one slice index per dimension: the time dim resolved to the
        # requested value, every other non-spatial dim to index 0, spatial
        # dims kept in full (":").
        index_slice = []
        for i, name in enumerate(dim_names):
            lname = name.lower()
            if lname in _LAT_TOKENS or lname in _LON_TOKENS:
                index_slice.append(slice(None))
            elif lname == "time" and time_value:
                time_array = group.OpenMDArray(name) or group.OpenMDArray("time")
                index_slice.append(_find_time_index(time_array, time_value))
            else:
                index_slice.append(0)  # primary level/member, same as WMTS default

        sliced = array[tuple(index_slice)]
        if sliced.GetDimensionCount() != 2:
            raise ExtractError(
                f"'{variable}' still has {sliced.GetDimensionCount()} dimensions "
                f"after slicing to {dim_names} — expected exactly latitude+longitude "
                "to remain."
            )

        x_idx, y_idx = _spatial_axis_indices([d.GetName() for d in sliced.GetDimensions()])
        classic_ds = sliced.AsClassicDataset(x_idx, y_idx)
        if classic_ds is None:
            raise ExtractError(f"GDAL could not convert '{variable}' to a classic raster.")
        if classic_ds.GetSpatialRef() is None:
            classic_ds.SetSpatialRef(_wgs84_srs())

        return _write_geotiff(classic_ds, bbox, output_path, cog=cog)
    finally:
        for key, val in prev.items():
            gdal.SetConfigOption(key, val)


# ---------------------------------------------------------------------------
# Public: ARCO Zarr, a time RANGE (+ optional binned aggregation) -> GeoTIFF
# ---------------------------------------------------------------------------

_BIN_KEY = {
    "daily": lambda dt: (dt.year, dt.month, dt.day),
    "monthly": lambda dt: (dt.year, dt.month),
    "annual": lambda dt: (dt.year,),
}
_BIN_LABEL = {
    "daily": lambda dt: dt.strftime("%Y-%m-%d"),
    "monthly": lambda dt: dt.strftime("%Y-%m"),
    "annual": lambda dt: dt.strftime("%Y"),
}
_REDUCERS = {"mean": "mean", "max": "max", "min": "min"}


def zarr_range_to_geotiff(
    zarr_url: str,
    variable: str,
    time_range: tuple,
    bbox: Optional[tuple],
    output_path: Path,
    aggregation: str = "raw",
    auth_header: Optional[dict] = None,
    cog: bool = True,
) -> Path:
    """Read one variable over a time RANGE from a remote ARCO Zarr store
    and write a (usually multi-band) GeoTIFF — the Search tab's "Export to
    GeoTIFF" feature. Companion to zarr_to_geotiff() (one instant) and
    zarr_point_time_series() (one point, full range); this is the third
    combination, a bbox over a range.

    *aggregation*: "raw" writes one band per timestep found in the range
    (band description = that timestep's ISO string). Otherwise
    "<daily|monthly|annual>_<mean|max|min>" groups timesteps into that
    bin and reduces each bin with that statistic — one band per bin
    (description = the bin's date/month/year), matching
    export_utils.AGGREGATION_LABELS' 9 modes exactly.

    Reads only the index range actually covering *bbox* (not the whole
    store) to avoid pulling a huge, mostly-irrelevant extent over the
    network for a tight crop — same reasoning as fetching chunked slices
    lazily. A final gdal.Warp crop to the exact bbox edges still runs
    afterward (see _write_geotiff), same as the single-instant path.
    """
    import numpy as np

    if aggregation != "raw":
        bin_kind, _, reducer = aggregation.partition("_")
        if bin_kind not in _BIN_KEY or reducer not in _REDUCERS:
            raise ExtractError(
                f"Unrecognised aggregation {aggregation!r}. Expected 'raw' or "
                f"'<daily|monthly|annual>_<mean|max|min>'."
            )

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
            raise ExtractError(f"GDAL could not open Zarr store: {zarr_url}")

        group = root.GetRootGroup()
        array = _open_variable_array(group, variable)

        dims = array.GetDimensions()
        dim_names = [d.GetName() for d in dims]
        lower = [n.lower() for n in dim_names]

        lat_i = next((i for i, n in enumerate(lower) if n in _LAT_TOKENS), None)
        lon_i = next((i for i, n in enumerate(lower) if n in _LON_TOKENS), None)
        time_i = next((i for i, n in enumerate(lower) if n == "time"), None)
        if lat_i is None or lon_i is None or time_i is None:
            raise ExtractError(
                f"Could not identify latitude/longitude/time among dimensions {dim_names!r}"
            )

        time_array = group.OpenMDArray(dim_names[time_i])
        t_start, t_end, time_grid = _time_range_indices(
            time_array, time_range[0], time_range[1]
        )

        lat_array = group.OpenMDArray(dim_names[lat_i])
        lon_array = group.OpenMDArray(dim_names[lon_i])
        if bbox:
            west, south, east, north = bbox
            lat_lo, lat_hi, lat_values = _coord_index_bounds(lat_array, south, north)
            lon_lo, lon_hi, lon_values = _coord_index_bounds(lon_array, west, east)
        else:
            lat_values = lat_array.ReadAsArray()
            lon_values = lon_array.ReadAsArray()
            lat_lo, lat_hi = 0, len(lat_values) - 1
            lon_lo, lon_hi = 0, len(lon_values) - 1

        index_slice = []
        for i, name in enumerate(dim_names):
            if i == time_i:
                index_slice.append(slice(t_start, t_end + 1))
            elif i == lat_i:
                index_slice.append(slice(lat_lo, lat_hi + 1))
            elif i == lon_i:
                index_slice.append(slice(lon_lo, lon_hi + 1))
            else:
                index_slice.append(0)

        sliced = array[tuple(index_slice)]
        # Note: GDAL renames a range-sliced dimension (e.g. "time" becomes
        # something like "subset_time_13344_1_6") rather than keeping the
        # original name, so only dimension *count* is checked here — order
        # is still guaranteed to be [time, lat, lon] since that's the fixed
        # relative order of the three axes left un-collapsed (any others
        # were sliced to a single index above, which drops them entirely).
        if sliced.GetDimensionCount() != 3:
            raise ExtractError(
                f"'{variable}' has {sliced.GetDimensionCount()} dimensions after "
                f"slicing (expected 3: time, lat, lon)."
            )

        cube = sliced.ReadAsArray()  # (ntime, nlat, nlon) — see dim-order
        # assumption in the comment below.

        # Built directly from real coordinate values, not AsClassicDataset —
        # see _geotransform_from_coords' docstring for why (it returns a
        # bogus identity geotransform when both spatial axes are range-
        # sliced together, confirmed by testing this exact code path).
        geotransform = _geotransform_from_coords(lat_values, lat_lo, lon_values, lon_lo)
        srs = _wgs84_srs()
        # cube's axis order is (time, lat, lon) because that's dim_names'
        # relative order in every ARCO dataset checked so far (time,
        # [elevation/level], latitude, longitude) — any other dims were
        # sliced to a single index above, which drops them, leaving the
        # three kept axes in their original relative order.

        timestamps = [time_grid.timestamp_at(t_start + offset) for offset in range(cube.shape[0])]

        if aggregation == "raw":
            band_arrays = [cube[t] for t in range(cube.shape[0])]
            band_labels = [dt.strftime("%Y-%m-%dT%H:%M:%SZ") for dt in timestamps]
        else:
            bin_kind, _, reducer = aggregation.partition("_")
            key_fn = _BIN_KEY[bin_kind]
            label_fn = _BIN_LABEL[bin_kind]
            reduce_fn = getattr(np, reducer)

            bins: dict = {}  # key -> (label, [slice indices into cube axis 0])
            for offset, dt in enumerate(timestamps):
                key = key_fn(dt)
                bins.setdefault(key, (label_fn(dt), []))[1].append(offset)

            band_arrays = []
            band_labels = []
            for key in sorted(bins):
                label, offsets = bins[key]
                band_arrays.append(reduce_fn(cube[offsets, :, :], axis=0))
                band_labels.append(f"{label} ({reducer})")

        mem_ds = gdal.GetDriverByName("MEM").Create(
            "", cube.shape[2], cube.shape[1], len(band_arrays), gdal.GDT_Float32,
        )
        mem_ds.SetGeoTransform(geotransform)
        mem_ds.SetSpatialRef(srs)
        for i, (arr, label) in enumerate(zip(band_arrays, band_labels), start=1):
            band = mem_ds.GetRasterBand(i)
            band.WriteArray(np.asarray(arr, dtype="float32"))
            band.SetDescription(label)

        return _write_geotiff(mem_ds, bbox, output_path, cog=cog)
    finally:
        for key, val in prev.items():
            gdal.SetConfigOption(key, val)
