# -*- coding: utf-8 -*-
"""
gdal_native.discover
~~~~~~~~~~~~~~~~~~~~

Dataset catalogue discovery — ported from geobridge/modules/discover.py
(geobridge 0.1.13 source), with ``yaml.safe_load`` swapped for
``json.load`` against the pre-converted snapshots in ``catalog_data/``
(see that directory's provenance note). Field logic is otherwise
unchanged from the original.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

_DATA_DIR = Path(__file__).parent.parent / "catalog_data"
_ARCO_SNAPSHOT_PATH = _DATA_DIR / "arco_snapshot.json"
_CDS_SNAPSHOT_PATH = _DATA_DIR / "cds_snapshot.json"
_OVERRIDES_PATH = _DATA_DIR / "arco_overrides.json"

_VARIABLE_COLORMAPS: dict = {
    "2m_temperature":      {"palette": "RdBu_r",   "unit": "K"},
    "t2m":                 {"palette": "RdBu_r",   "unit": "K"},
    "total_precipitation": {"palette": "YlGnBu",   "unit": "m"},
    "tp":                  {"palette": "YlGnBu",   "unit": "m"},
    "pm2p5":               {"palette": "YlOrRd",   "unit": "kg/m³"},
    "no2":                 {"palette": "Purples",  "unit": "kg/m³"},
    "utci":                {"palette": "RdYlBu_r", "unit": "K"},
    "sst":                 {"palette": "RdBu_r",   "unit": "K"},
    "skt":                 {"palette": "RdBu_r",   "unit": "K"},
}
_DEFAULT_COLORMAP = {"palette": "viridis", "unit": ""}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TileMatrixSet:
    identifier: str
    crs: str
    min_zoom: int = 0
    max_zoom: int = 10

    @property
    def epsg_code(self) -> Optional[str]:
        match = re.search(r"EPSG[:/]+(\d+)", self.crs, re.IGNORECASE)
        return f"EPSG:{match.group(1)}" if match else None


@dataclass
class LayerDescriptor:
    """Unified description of a Copernicus dataset.

    Branch on has_zarr / has_wmts before calling any access-method-specific
    code — not every dataset supports both.
    """

    # Core metadata
    id: str
    title: str
    service: str
    abstract: str = ""
    variables: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    license: str = "unknown"
    providers: list = field(default_factory=list)
    thumbnail: str = ""

    # Spatial / temporal extent
    bbox: tuple = (-180.0, -90.0, 180.0, 90.0)
    crs: str = "EPSG:4326"
    time_range: tuple = field(
        default_factory=lambda: (
            datetime(1940, 1, 1, tzinfo=timezone.utc),
            datetime.now(timezone.utc),
        )
    )
    time_step: str = "1h"

    # Access methods — None / empty means not available for this dataset
    zarr_time_chunked: Optional[str] = None
    zarr_geo_chunked: Optional[str] = None
    wmts_url: str = ""
    wmts_layer_name: str = ""
    tile_matrix_sets: list = field(default_factory=list)
    cds_retrieve_url: Optional[str] = None
    cds_form_url: Optional[str] = None
    cds_constraints_url: Optional[str] = None
    cds_download_supported: bool = False

    # Styling hint
    colormap: dict = field(default_factory=dict)

    @property
    def has_zarr(self) -> bool:
        return bool(self.zarr_time_chunked or self.zarr_geo_chunked)

    @property
    def has_wmts(self) -> bool:
        return bool(self.wmts_url and self.wmts_layer_name)

    @property
    def has_cds_retrieve(self) -> bool:
        return bool(self.cds_retrieve_url)

    @property
    def extraction_supported(self) -> bool:
        return self.has_zarr or self.has_cds_retrieve

    def __repr__(self) -> str:
        access = []
        if self.has_zarr:
            access.append("zarr")
        if self.has_wmts:
            access.append("wmts")
        if self.has_cds_retrieve:
            access.append("cds" if self.cds_download_supported else "cds?")
        return (
            f"LayerDescriptor(id={self.id!r}, service={self.service!r}, "
            f"access={access or ['metadata-only']})"
        )


# ---------------------------------------------------------------------------
# JSON loading (see catalog_data/README — converted once from geobridge's
# own bundled YAML; no yaml/PyYAML dependency needed at runtime here).
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_arco_snapshot() -> dict:
    if not _ARCO_SNAPSHOT_PATH.exists():
        return {}
    with _ARCO_SNAPSHOT_PATH.open(encoding="utf-8") as fp:
        data = json.load(fp) or {}
    return data.get("datasets", {}) or {}


@lru_cache(maxsize=1)
def _load_cds_snapshot() -> dict:
    if not _CDS_SNAPSHOT_PATH.exists():
        return {}
    with _CDS_SNAPSHOT_PATH.open(encoding="utf-8") as fp:
        data = json.load(fp) or {}
    return data.get("datasets", {}) or {}


@lru_cache(maxsize=1)
def _load_overrides() -> dict:
    if not _OVERRIDES_PATH.exists():
        return {}
    with _OVERRIDES_PATH.open(encoding="utf-8") as fp:
        data = json.load(fp) or {}
    return data.get("overrides", {}) or {}


# ---------------------------------------------------------------------------
# Building LayerDescriptors
# ---------------------------------------------------------------------------

def _colormap_for_variable(variable: str) -> dict:
    key = variable.lower().replace("-", "_").replace(" ", "_")
    return dict(_VARIABLE_COLORMAPS.get(key, _DEFAULT_COLORMAP))


def _parse_datetime(value: Optional[str], default: datetime) -> datetime:
    if not value:
        return default
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return default


def _pick_primary_subset(arco_entry: dict) -> Optional[dict]:
    subsets = arco_entry.get("subsets") or {}
    if not subsets:
        return None
    for preferred in ("sfc", "all", "surface"):
        if preferred in subsets:
            return subsets[preferred]
    return list(subsets.values())[0]


_WIND_LAYER_NAMES = {"wind", "wind10", "wind100", "wind_10m", "wind_100m"}


def _subset_wmts_prefix(subset: dict, dataset_id: str, subset_id: str) -> str:
    raw = (subset.get("wmts") or "").split("?")[0]
    parts = raw.split("/teroWmts/")
    if len(parts) == 2 and parts[1]:
        return parts[1].rstrip("/")
    return f"{dataset_id}/{subset_id}"


def _arco_subset_for_variable(dataset_id: str, variable: str):
    """Find the ARCO subset that actually serves *variable*.

    Returns (layer_prefix, subset_dict), or None when no subset lists it.
    """
    arco = _load_arco_snapshot()
    arco_id = dataset_id.replace("-", "_")
    entry = arco.get(arco_id)
    if not entry:
        return None
    subsets = entry.get("subsets") or {}

    for sub_id, sub in subsets.items():
        if variable in (sub.get("variables") or {}):
            return _subset_wmts_prefix(sub, arco_id, sub_id), sub

    if variable.lower() in _WIND_LAYER_NAMES:
        components = ("u100", "v100") if "100" in variable else ("u10", "v10")
        for sub_id, sub in subsets.items():
            svars = sub.get("variables") or {}
            if any(c in svars for c in components):
                return _subset_wmts_prefix(sub, arco_id, sub_id), sub

    return None


def _descriptor_from_arco(dataset_id: str, arco_entry: dict,
                           cds_entry: Optional[dict],
                           global_aliases: dict) -> LayerDescriptor:
    sub = _pick_primary_subset(arco_entry) or {}

    title = arco_entry.get("title", dataset_id)
    abstract = arco_entry.get("description", "")
    service = arco_entry.get("service", "C3S")
    licence = arco_entry.get("license", "unknown")
    providers = arco_entry.get("providers", []) or []
    thumbnail = arco_entry.get("thumbnail", "")

    bbox_list = sub.get("bbox") or [-180.0, -90.0, 180.0, 90.0]
    if len(bbox_list) >= 4:
        bbox = tuple(float(v) for v in bbox_list[:4])
    else:
        bbox = (-180.0, -90.0, 180.0, 90.0)

    time_start = _parse_datetime(
        sub.get("time_start"), datetime(1940, 1, 1, tzinfo=timezone.utc))
    time_end = _parse_datetime(
        sub.get("time_end"), datetime.now(timezone.utc))

    var_meta = sub.get("variables") or {}
    variables = sorted(var_meta.keys())

    zarr = sub.get("zarr") or {}
    zarr_time = zarr.get("timeChunked") or zarr.get("time_chunked")
    zarr_geo = zarr.get("geoChunked") or zarr.get("geo_chunked")

    wmts_raw = sub.get("wmts") or ""
    wmts_url = ""
    wmts_layer = ""
    if wmts_raw:
        base = wmts_raw.split("?")[0]
        parts = base.split("/teroWmts/")
        if len(parts) == 2:
            wmts_url = parts[0] + "/teroWmts"
            wmts_layer = parts[1]
        else:
            wmts_url = base

    cds_retrieve = None
    cds_form = None
    cds_constraints = None
    if cds_entry:
        links = cds_entry.get("links") or {}
        cds_retrieve = links.get("retrieve")
        cds_form = links.get("form")
        cds_constraints = links.get("constraints")

    keywords = []
    if cds_entry:
        keywords = cds_entry.get("keywords", []) or []

    cds_download_supported = bool(
        cds_entry.get("cds_download_supported")) if cds_entry else False

    colormap = dict(_DEFAULT_COLORMAP)
    if variables:
        first_var = var_meta.get(variables[0], {})
        cm_id = first_var.get("colormap")
        if cm_id:
            colormap = {"palette": cm_id, "unit": first_var.get("unit", "")}
        else:
            colormap = _colormap_for_variable(variables[0])

    return LayerDescriptor(
        id=dataset_id, title=title, service=service, abstract=abstract,
        variables=variables, keywords=keywords, license=licence,
        providers=[p for p in providers if p], thumbnail=thumbnail,
        bbox=bbox, crs="EPSG:4326", time_range=(time_start, time_end),
        time_step=sub.get("sample_period") or "1h",
        zarr_time_chunked=zarr_time, zarr_geo_chunked=zarr_geo,
        wmts_url=wmts_url, wmts_layer_name=wmts_layer, tile_matrix_sets=[],
        cds_retrieve_url=cds_retrieve, cds_form_url=cds_form,
        cds_constraints_url=cds_constraints,
        cds_download_supported=cds_download_supported, colormap=colormap,
    )


def _descriptor_from_cds_only(dataset_id: str, cds_entry: dict) -> LayerDescriptor:
    title = cds_entry.get("title") or dataset_id
    abstract = cds_entry.get("description", "")
    service = cds_entry.get("service", "C3S")
    keywords = cds_entry.get("keywords", []) or []
    licence = cds_entry.get("license", "unknown")
    providers = cds_entry.get("providers", []) or []
    thumbnail = cds_entry.get("thumbnail", "")

    bbox_list = cds_entry.get("spatial_bbox") or [-180.0, -90.0, 180.0, 90.0]
    if len(bbox_list) >= 4 and all(v is not None for v in bbox_list[:4]):
        bbox = tuple(float(v) for v in bbox_list[:4])
    else:
        bbox = (-180.0, -90.0, 180.0, 90.0)

    temporal = cds_entry.get("temporal_interval") or [None, None]
    time_start = _parse_datetime(temporal[0], datetime(1940, 1, 1, tzinfo=timezone.utc))
    time_end = _parse_datetime(
        temporal[1] if len(temporal) > 1 else None,
        datetime.now(timezone.utc))

    links = cds_entry.get("links") or {}
    cds_retrieve = links.get("retrieve")
    cds_form = links.get("form")
    cds_constraints = links.get("constraints")

    return LayerDescriptor(
        id=dataset_id, title=title, service=service, abstract=abstract,
        variables=[], keywords=keywords, license=licence,
        providers=[p for p in providers if p], thumbnail=thumbnail,
        bbox=bbox, crs="EPSG:4326", time_range=(time_start, time_end),
        cds_retrieve_url=cds_retrieve, cds_form_url=cds_form,
        cds_constraints_url=cds_constraints,
        cds_download_supported=bool(cds_entry.get("cds_download_supported")),
        colormap=dict(_DEFAULT_COLORMAP),
    )


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _matches_keyword(d: LayerDescriptor, keyword: str) -> bool:
    needle = keyword.lower()
    haystack = " ".join([d.id, d.title, d.abstract, " ".join(d.keywords)]).lower()
    return needle in haystack


def _matches_variable(d: LayerDescriptor, variable: str) -> bool:
    needle = variable.lower().replace("-", "_")
    return any(needle in v.lower() for v in d.variables)


def _matches_bbox(d: LayerDescriptor, bbox: tuple) -> bool:
    w, s, e, n = bbox
    dw, ds_, de, dn = d.bbox
    return not (de < w or dw > e or dn < s or ds_ > n)


def _matches_time(d: LayerDescriptor, time_after: datetime) -> bool:
    if time_after.tzinfo is None:
        time_after = time_after.replace(tzinfo=timezone.utc)
    end = d.time_range[1]
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end >= time_after


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def discover(
    keyword: Optional[str] = None,
    services: Optional[list] = None,
    variable: Optional[str] = None,
    bbox: Optional[tuple] = None,
    time_after: Optional[datetime] = None,
    extraction_only: bool = False,
    arco_only: bool = False,
    cds_download_only: bool = False,
) -> list:
    """Return Copernicus datasets matching the given filters, from the
    locally bundled snapshot — no network call."""
    arco = _load_arco_snapshot()
    cds = _load_cds_snapshot()
    aliases = (_load_overrides() or {}).get("variable_aliases", {})

    all_ids: dict = {}
    for ds_id in arco:
        all_ids[ds_id] = "arco"
    for ds_id in cds:
        arco_form = ds_id.replace("-", "_")
        if arco_form not in arco:
            all_ids[ds_id] = "cds"

    valid_services = {"C3S", "CAMS", "CEMS"}
    if services:
        unknown = set(services) - valid_services
        if unknown:
            raise ValueError(
                f"Unknown service(s): {unknown}. Valid: {sorted(valid_services)}"
            )
        services_set = set(services)
    else:
        services_set = valid_services

    results = []
    for ds_id, source in all_ids.items():
        if source == "arco":
            cds_id = ds_id.replace("_", "-")
            desc = _descriptor_from_arco(ds_id, arco[ds_id], cds.get(cds_id), aliases)
        else:
            desc = _descriptor_from_cds_only(ds_id, cds[ds_id])

        if desc.service not in services_set:
            continue
        if keyword and not _matches_keyword(desc, keyword):
            continue
        if variable and not _matches_variable(desc, variable):
            continue
        if bbox and not _matches_bbox(desc, bbox):
            continue
        if time_after and not _matches_time(desc, time_after):
            continue
        if extraction_only and not desc.extraction_supported:
            continue
        if arco_only and not desc.has_zarr:
            continue
        if cds_download_only and not desc.cds_download_supported:
            continue
        results.append(desc)

    results.sort(key=lambda d: d.id)
    return results


def discover_one(dataset_id: str, **kwargs) -> Optional[LayerDescriptor]:
    """Return a single LayerDescriptor by id, or None if not found.

    Accepts both CDS-style (hyphenated) and ARCO-style (underscored) ids.
    """
    if kwargs:
        for ds in discover(**kwargs):
            if dataset_id.lower() in ds.id.lower():
                return ds
        return None

    arco = _load_arco_snapshot()
    cds = _load_cds_snapshot()
    aliases = (_load_overrides() or {}).get("variable_aliases", {})

    arco_id = dataset_id.replace("-", "_")
    if arco_id in arco:
        cds_id = arco_id.replace("_", "-")
        return _descriptor_from_arco(arco_id, arco[arco_id], cds.get(cds_id), aliases)

    if dataset_id in cds:
        return _descriptor_from_cds_only(dataset_id, cds[dataset_id])

    return None
