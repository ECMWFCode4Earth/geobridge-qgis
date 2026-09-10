# -*- coding: utf-8 -*-
"""
gdal_native.wmts
~~~~~~~~~~~~~~~~

WMTS tile layer construction for ECMWF Copernicus ARCO datasets — ported
from geobridge/modules/wmts.py (already pure stdlib there: urllib.parse,
dataclasses, datetime — no xarray/zarr/pyproj involved at all, so there
was never a dependency reason to touch this piece, only an organisational
one: it shouldn't import from the `geobridge` package it's replacing).

QGIS integration note (from the original module, verified true): QGIS's
native WMS/WMTS provider cannot reliably parse this server's
GetCapabilities XML ("Cannot calculate extent"), so this deliberately
never calls GetCapabilities — it hand-builds XYZ tile URLs with
{x}/{y}/{z} placeholders that QGIS substitutes per-tile, exactly like the
already-working approach this replaces.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Union

from .discover import (
    LayerDescriptor,
    _arco_subset_for_variable,
    _colormap_for_variable,
    _load_overrides,
    discover_one,
)

logger = logging.getLogger(__name__)

DateLike = Union[str, datetime]

WMTS_BASE = "https://wmts.datastores.ecmwf.int/teroWmts"

ZOOM_MIN = 0
ZOOM_MAX = 10

_TMS_BY_CRS = {
    "EPSG:4326": "EPSG:4326",
    "EPSG:3857": "EPSG:3857",
}


@dataclass
class WmtsLayer:
    """A resolved WMTS layer ready for use in QGIS."""

    dataset: str
    variable: str
    datetime_str: str
    layer_name: str
    base_url: str
    tile_matrix_set: str
    crs: str
    style: str = "cmap:viridis"
    legend_url: str = ""
    colormap: dict = field(default_factory=dict)
    service: str = ""

    def to_qgis(self) -> dict:
        """Return a dict ready for constructing a QgsRasterLayer.

        XYZ tile approach (see module docstring for why, not the QGIS WMTS
        provider) — bypasses GetCapabilities entirely.
        """
        tile_url = (
            f"{self.base_url}"
            f"?SERVICE%3DWMTS"
            f"%26REQUEST%3DGetTile"
            f"%26VERSION%3D1.0.0"
            f"%26LAYER%3D{self.layer_name}"
            f"%26STYLE%3D{self.style}"
            f"%26FORMAT%3Dimage/png"
            f"%26TILEMATRIXSET%3D{self.tile_matrix_set}"
            f"%26TILEMATRIX%3D{{z}}"
            f"%26TILEROW%3D{{y}}"
            f"%26TILECOL%3D{{x}}"
            f"%26TIME%3D{self.datetime_str}"
        )
        uri = f"type=xyz&url={tile_url}&zmin={ZOOM_MIN}&zmax={ZOOM_MAX}"
        return {
            "uri": uri,
            "name": f"{self.dataset} — {self.variable} ({self.datetime_str})",
            "provider": "wms",
        }

    def __repr__(self) -> str:
        return (
            f"WmtsLayer(dataset={self.dataset!r}, "
            f"variable={self.variable!r}, time={self.datetime_str!r})"
        )


def _format_datetime(dt: DateLike) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    s = str(dt).strip()
    if "T" not in s and len(s) == 10:
        s = s + "T00:00:00"
    if not s.endswith("Z"):
        s = s.rstrip("+00:00") + "Z" if s.endswith("+00:00") else s + "Z"
    return s


def _resolve_tile_matrix_set(descriptor: LayerDescriptor, target_crs: Optional[str]):
    if target_crs:
        for tms in descriptor.tile_matrix_sets:
            if tms.epsg_code == target_crs:
                return tms.identifier, target_crs
        if target_crs in _TMS_BY_CRS:
            return _TMS_BY_CRS[target_crs], target_crs
    return "EPSG:3857", "EPSG:3857"


def wmts_layer(
    dataset: str,
    variable: str,
    datetime: DateLike,
    style: str = "default",
    target_crs: Optional[str] = None,
    descriptor: Optional[LayerDescriptor] = None,
) -> WmtsLayer:
    """Resolve a time-specific WMTS layer with all parameters filled in."""
    if descriptor is None:
        descriptor = discover_one(dataset)
        if descriptor is None:
            raise ValueError(
                f"Could not discover dataset {dataset!r}. "
                "Check the identifier or call discover() to list options."
            )

    if not descriptor.has_wmts:
        raise ValueError(
            f"Dataset {dataset!r} has no WMTS preview endpoint.\n"
            "WMTS is available for ARCO datasets only (those with has_wmts=True)."
        )

    aliases = (_load_overrides() or {}).get("variable_aliases", {})
    arco_variable = aliases.get(variable, variable)

    resolved = (
        _arco_subset_for_variable(dataset, arco_variable)
        or _arco_subset_for_variable(dataset, variable)
    )
    if resolved:
        layer_prefix, _subset = resolved
    else:
        layer_prefix, _subset = descriptor.wmts_layer_name, None
        logger.debug(
            "No ARCO subset lists %r for %s — falling back to primary subset %r",
            arco_variable, dataset, descriptor.wmts_layer_name,
        )

    layer_name = f"{layer_prefix}/{arco_variable}"

    _var_meta = (_subset.get("variables") or {}).get(arco_variable, {}) if _subset else {}

    if style and style != "default":
        wmts_style = style
    else:
        palette = (
            _var_meta.get("colormap")
            or (descriptor.colormap or {}).get("palette")
            or "viridis"
        )
        wmts_style = f"cmap:{palette}"

    tms_id, resolved_crs = _resolve_tile_matrix_set(descriptor, target_crs)
    iso_time = _format_datetime(datetime)

    legend_url = (
        f"{descriptor.wmts_url}?SERVICE=WMTS&REQUEST=GetLegend"
        f"&LAYER={urllib.parse.quote(layer_name, safe='/')}"
        f"&STYLE={urllib.parse.quote(wmts_style)}"
        f"&FORMAT=image%2Fsvg%2Bxml"
    )

    if _var_meta:
        colormap = {
            "palette": _var_meta.get("colormap", "viridis"),
            "unit": _var_meta.get("unit", ""),
        }
    else:
        colormap = descriptor.colormap or _colormap_for_variable(variable)

    return WmtsLayer(
        dataset=dataset,
        variable=arco_variable,
        datetime_str=iso_time,
        layer_name=layer_name,
        base_url=descriptor.wmts_url,
        tile_matrix_set=tms_id,
        crs=resolved_crs,
        style=wmts_style,
        legend_url=legend_url,
        colormap=colormap,
        service=descriptor.service,
    )
