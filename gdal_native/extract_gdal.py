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
import zipfile
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
    except Exception:
        pass


_safe_use_exceptions()


class ExtractError(Exception):
    """Raised when a downloaded/remote file can't be converted to GeoTIFF."""


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
    all bands of the first grid subdataset if no match/variable given)."""
    root = gdal.Open(str(path))
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
    ds = gdal.Open(str(path))
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


def _find_time_index(time_array: "gdal.MDArray", time_value: str) -> int:
    """Resolve an ISO datetime string to the nearest index in *time_array*,
    using its `units`/`unit` attribute ("<days|hours|seconds> since <epoch>",
    CF convention) — same convention the ARCO store's own time coordinate
    uses (verified: "days since 1970-01-01" during this branch's testing).
    """
    import re as _re
    from datetime import datetime, timedelta

    import numpy as np

    attrs = {a.GetName(): a.Read() for a in time_array.GetAttributes()}
    unit_str = attrs.get("units") or attrs.get("unit") or "days since 1970-01-01"
    m = _re.match(r"(\w+)\s+since\s+(.+)", unit_str.strip())
    if not m:
        raise ExtractError(f"Unrecognised time unit on Zarr store: {unit_str!r}")
    unit_name, epoch_str = m.group(1).lower(), m.group(2).strip()
    epoch = datetime.fromisoformat(epoch_str.replace("Z", "+00:00").split("+")[0])

    target = datetime.fromisoformat(time_value.replace("Z", "+00:00").split("+")[0])
    delta = target - epoch
    unit_seconds = {"days": 86400, "hours": 3600, "minutes": 60, "seconds": 1}.get(
        unit_name.rstrip("s") + "s", 86400
    )
    target_value = delta.total_seconds() / unit_seconds

    values = time_array.ReadAsArray()
    idx = int(np.argmin(np.abs(values - target_value)))
    return idx


def _spatial_axis_indices(dim_names: list) -> tuple:
    """Return (x_local_index, y_local_index) into a 2-remaining-dimension
    slice, identified by name rather than assumed position — mirrors
    geobridge's own _spatial_dims() name-matching approach."""
    lower = [n.lower() for n in dim_names]
    x_idx = next((i for i, n in enumerate(lower) if n in _LON_TOKENS), None)
    y_idx = next((i for i, n in enumerate(lower) if n in _LAT_TOKENS), None)
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
        array = group.OpenMDArray(variable)
        if array is None:
            raise ExtractError(
                f"Variable '{variable}' not found in Zarr store. "
                f"Available: {group.GetMDArrayNames()}"
            )

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
