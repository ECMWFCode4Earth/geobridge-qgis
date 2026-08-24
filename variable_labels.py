# -*- coding: utf-8 -*-
"""
variable_labels
~~~~~~~~~~~~~~~

Readable names for the short variable codes the semantic search surfaces
(e.g. ``t2m`` -> "2-metre temperature"), for tooltips and the selection
line on the Search tab.

Why a curated map rather than geobridge lookups: geobridge only exposes the
short names publicly; the code->name mapping lives behind a private helper
over an internal alias file that could change between releases. The semantic
engine only ever recommends a small, stable set of variables (26 at the time
of writing — the full set this covers), so a plain dict here is both
complete for what users actually see and immune to geobridge internals.
Anything unmapped falls back to a lightly prettified code, and the Search
tab's "Dataset details" link documents it regardless.

Zero Qt/geobridge imports — runs under plain pytest.
"""

from __future__ import annotations

# Short/CDS code -> human-readable name. Keys are exactly the values
# geobridge.semantic_search puts in SemanticMatch.variable.
VARIABLE_LABELS = {
    "t2m": "2-metre temperature",
    "2m_temp_mean": "Mean 2-metre temperature",
    "tas": "Near-surface air temperature (2 m)",
    "d2m": "2-metre dewpoint temperature",
    "tp": "Total precipitation",
    "pr": "Precipitation",
    "rr": "Precipitation (rainfall)",
    "u10": "10-metre wind (U component)",
    "u100": "100-metre wind (U component)",
    "ssrd": "Surface solar radiation downwards",
    "sm": "Soil moisture",
    "LAI": "Leaf area index",
    "lccs_class": "Land cover class",
    "al_bb_dh": "Surface broadband albedo",
    "analysed_sst": "Analysed sea surface temperature",
    "zos": "Sea surface height above geoid",
    "water_surface_height_above_reference_datum": "Water surface height above reference datum",
    "aod550": "Aerosol optical depth at 550 nm",
    "no2": "Nitrogen dioxide (NO₂)",
    "pm2p5": "Particulate matter < 2.5 µm (PM2.5)",
    "cfc": "Cloud fractional cover",
    "toa_net_all_mon": "Top-of-atmosphere net radiation (monthly)",
    "fwi": "Fire Weather Index",
    "utci": "Universal Thermal Climate Index (UTCI)",
    "solar_pv_capacity_factor": "Solar PV capacity factor",
    "wind_onshore_capacity_factor": "Onshore wind capacity factor",
    "wind_offshore_capacity_factor": "Offshore wind capacity factor",
}


def friendly_name(variable: str) -> str:
    """Readable name for a variable code, or a prettified fallback.

    Returns the curated label if known; otherwise the code with underscores
    turned into spaces (never empty, never raises).
    """
    if not variable:
        return ""
    if variable in VARIABLE_LABELS:
        return VARIABLE_LABELS[variable]
    return variable.replace("_", " ")


def has_label(variable: str) -> bool:
    """True if `variable` has a curated (non-fallback) readable name."""
    return variable in VARIABLE_LABELS
