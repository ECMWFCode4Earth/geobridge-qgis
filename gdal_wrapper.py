# -*- coding: utf-8 -*-
"""
gdal_wrapper
~~~~~~~~~~~~

The single point of contact between this plugin's UI code and the
`gdal_native` package — drop-in replacement for gb_wrapper.py, built
entirely on GDAL (already bundled with QGIS) + the Python standard
library, no `geobridge`/`xarray`/`zarr`/`dask`/`rioxarray`/`pyproj`/
`netcdf4` anywhere. See gdal_native/__init__.py for why: those pulled in
a `pyproj` build that segfaulted QGIS (Windows access violation inside
pyproj's compiled `_CRS.__init__`, reached from a background QgsTask
thread) and repeatedly fought QGIS's own bundled numpy/pandas/pyarrow
over ABI-compatible versions.

Mirrors gb_wrapper.py's function names/signatures wherever the two
approaches naturally align, so the dialog/browse_tab/export_task edits
that point at this module instead are mostly one-line import swaps — see
the exceptions noted per-function below (semantic_resources' return
shape, and the two export/point-series functions needing to resolve a
zarr_url + short variable name + auth header, which geobridge did
internally but gdal_native's lower-level functions take as direct
arguments instead).

No GeobridgeNotInstalled here: gdal_native is vendored into this plugin,
not pip-installed, so there is nothing to be "not installed" — every
is_core_available()/is_zarr_extra_available() check and the whole
"Install dependencies" flow in the dialog is being removed, not ported.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

# No `gdal_native.extract_gdal` import here at module level, deliberately:
# it imports `osgeo.gdal`, which doesn't exist outside QGIS's own Python.
# Every other gdal_native module is pure stdlib, so importing *this* file
# stays safe even without QGIS/GDAL present (e.g. the plain-Python pytest
# run in test/test_available_values.py, which only needs the constraint-
# cascade functions below) — same reason gb_wrapper.py, this file's
# predecessor, deferred its own `import geobridge` into each function
# rather than the module top. extract_gdal is imported lazily inside
# export_zarr_to_geotiff() instead, the only function that needs it.
try:
    # Normal case: loaded as part of the GeoBridge_Plugin package (QGIS
    # imports every plugin this way).
    from .gdal_native import auth as _auth
    from .gdal_native import catalog_search as _catalog_search
    from .gdal_native import cds_download as _cds_download
    from .gdal_native import discover as _discover
    from .gdal_native import form as _form
    from .gdal_native import timeseries as _timeseries
    from .gdal_native import wmts as _wmts
except ImportError:
    # Plain-Python test runs import this file directly (see test/
    # test_available_values.py) with only the plugin directory on
    # sys.path, not as part of a package — relative imports need a real
    # parent package, which that context doesn't have, so fall back to
    # importing gdal_native as a top-level package instead.
    from gdal_native import auth as _auth
    from gdal_native import catalog_search as _catalog_search
    from gdal_native import cds_download as _cds_download
    from gdal_native import discover as _discover
    from gdal_native import form as _form
    from gdal_native import timeseries as _timeseries
    from gdal_native import wmts as _wmts


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate(key: Optional[str]) -> None:
    """Initialise a CDS API session for this process."""
    _auth.authenticate(key=key or None)


def is_authenticated() -> bool:
    """True if authenticate() has succeeded in this process."""
    return _auth.is_authenticated()


# ---------------------------------------------------------------------------
# Discovery / semantic search
# ---------------------------------------------------------------------------

class SemanticMatch:
    """One search hit — dataset_id/variable/confidence/use_cases, matching
    the fields _populate_results in geobridge_plugin_dialog.py reads.

    `use_cases` holds the ids of any curated use cases (vocabulary.json,
    ported from geobridge's own use-case vocabulary) whose theme synonyms
    or label/typical_question matched the query and point at this
    (dataset_id, variable) pair — usually empty, since most results come
    from catalog_search's generic TF-IDF text matching alone.
    """

    __slots__ = ("dataset_id", "variable", "confidence", "use_cases")

    def __init__(self, dataset_id: str, variable: str, confidence: float, use_cases: Optional[list] = None):
        self.dataset_id = dataset_id
        self.variable = variable
        self.confidence = confidence
        self.use_cases: list = list(use_cases or [])


def semantic_resources(query: str, max_results: int = 15, min_confidence: float = 0.1) -> list:
    """Return a list of SemanticMatch, unsorted (caller sorts) — powers the
    Search tab. min_confidence is applied here since catalog_search.
    search() doesn't take that parameter itself."""
    hits = _catalog_search.search(query, top_k=max_results)
    return [
        SemanticMatch(dataset_id=ds_id, variable=variable, confidence=score, use_cases=use_cases)
        for ds_id, variable, score, use_cases in hits
        if score >= min_confidence
    ]


def use_case_labels() -> dict:
    """Map every curated use-case id to its human-readable label."""
    return _catalog_search.use_case_labels()


def use_case_detail(use_case_id: str) -> dict:
    """Full curated entry for one use case — label, typical_question,
    recommended_access/aggregation/style, notes, etc. {} if unknown."""
    return _catalog_search.use_case_detail(use_case_id)


def discover_one(dataset_id: str):
    """Return a gdal_native LayerDescriptor for dataset_id, or None."""
    return _discover.discover_one(dataset_id)


def discover_all(keyword: Optional[str] = None) -> list:
    """Return LayerDescriptors restricted to datasets present in the
    bundled cds_snapshot.json catalogue (used to populate the dataset
    dropdown on the Browse-by-Variable tab) — same filtering gb_wrapper.py
    applied against geobridge.discover()'s merged ARCO+CDS result."""
    results = _discover.discover(keyword=keyword) if keyword else _discover.discover()
    cds_ids = set(_discover._load_cds_snapshot().keys())
    if not cds_ids:
        return results
    return [ds for ds in results if ds.id.replace("_", "-") in cds_ids]


def _variable_meta(dataset_id: str, variable: str) -> dict:
    """Raw per-variable metadata dict from the ARCO snapshot (unit,
    colormap, value_min, value_max, ...) — {} if not found. Uses the same
    per-variable subset resolver as variable_time_extent (not a single
    guessed-at "sfc"/"all"/"surface" subset for the whole dataset): some
    datasets (e.g. reanalysis-era5-land) split their variables across
    several named subsets like "sfc-2m-temperature"/"sfc-soil-temperature"
    rather than one combined subset, so picking any one fixed subset name
    would silently miss most variables and return {} for them.

    value_min/value_max/colormap always come from the routed subset (they
    describe that specific subset's own calibration, e.g. a particular
    sensor's typical range — mixing them in from elsewhere would show a
    legend range that doesn't match what's actually on screen). unit is
    the one exception: some datasets (e.g. satellite_cloud_properties)
    catalogue the *same* short variable name under several sensor-specific
    subsets, and only some of those subsets happen to record a unit for
    it (e.g. "iwp_day" has unit "g/m2" in most of its subsets but "" in
    two others) — a missing unit here falls back to whatever unit a
    sibling subset records for the same variable name, since the physical
    unit doesn't vary by sensor even though the calibrated range does."""
    resolved = _discover._arco_subset_for_variable(dataset_id, variable)
    if not resolved:
        return {}
    _prefix, sub = resolved
    meta = dict((sub.get("variables") or {}).get(variable) or {})
    if not meta.get("unit"):
        fallback_unit = _fallback_unit(dataset_id, variable)
        if fallback_unit:
            meta["unit"] = fallback_unit
    # The bundled catalogue's source datasets spell "no real physical
    # unit" several different ways depending on which upstream product a
    # variable came from - CF convention's "1" (e.g. a 0-1 fraction like
    # cloud cover), but also "~", "dimensionless", "1.0", "Numeric" (fire-
    # danger indices), "Fraction"/"(0-1)" (ice concentration), "index
    # value", etc. - all technically "units", but showing one of these
    # literally next to a value ("0.52 ~", "12.4 Numeric") reads as a
    # typo, not a unit. Blank it out here so every caller (legend, Time
    # Series plot) gets the same "no unit to show" behavior a genuinely
    # missing unit already gets, and flag it separately so a caller can
    # still say so once, near the variable name, instead of on every
    # value.
    if (meta.get("unit") or "").strip().lower() in _DIMENSIONLESS_UNIT_MARKERS:
        meta["unit"] = ""
        meta["dimensionless"] = True
    return meta


_DIMENSIONLESS_UNIT_MARKERS = {
    "1", "1.0", "~", "dimensionless", "numeric", "fraction",
    "(0-1)", "(0 - 1)", "index value",
}


def _fallback_unit(dataset_id: str, variable: str) -> str:
    """First non-empty unit recorded for `variable` under any subset of
    `dataset_id` — see _variable_meta's docstring for why this is unit-
    only, not a general "pick a better subset" fallback."""
    arco = _discover._load_arco_snapshot()
    entry = arco.get(dataset_id.replace("-", "_")) or {}
    for sub in (entry.get("subsets") or {}).values():
        unit = ((sub.get("variables") or {}).get(variable) or {}).get("unit")
        if unit:
            return unit
    return ""


def variable_unit(dataset_id: str, variable: str) -> str:
    """Physical unit for one specific variable of one dataset, e.g. "K"."""
    return _variable_meta(dataset_id, variable).get("unit", "") or ""


def variable_time_extent(dataset_id: str, variable: str) -> Optional[Tuple[str, str]]:
    """(start_iso, end_iso) actually covered by data for `variable`, or
    None if unknown — the ARCO subset actually resolved for this variable
    (mirrors gdal_native.wmts's own subset-per-variable resolution, not
    _variable_meta's "sfc"-preferred policy, since this describes the
    subset a WMTS preview request is actually routed to)."""
    resolved = _discover._arco_subset_for_variable(dataset_id, variable)
    if not resolved:
        return None
    _prefix, sub = resolved
    start, end = sub.get("time_start"), sub.get("time_end")
    if not start or not end:
        return None
    return (start, end)


def variable_style(dataset_id: str, variable: str) -> dict:
    """Legend-ready style info: {"unit", "colormap", "value_min",
    "value_max", "dimensionless"} — same source wmts_layer(style="default")
    reads to colour the WMTS tiles this plugin displays. {} if unknown.
    "dimensionless" is True when the catalogue's own unit is CF's "1"
    (blanked out of "unit" itself — see _variable_meta)."""
    meta = _variable_meta(dataset_id, variable)
    if not meta or meta.get("value_min") is None or meta.get("value_max") is None:
        return {}
    return {
        "unit": meta.get("unit", "") or "",
        "colormap": meta.get("colormap", "") or "viridis",
        "value_min": meta.get("value_min"),
        "value_max": meta.get("value_max"),
        "dimensionless": bool(meta.get("dimensionless")),
    }


# ---------------------------------------------------------------------------
# CDS form / constraints — powers the "Browse by Variable" tab.
# ---------------------------------------------------------------------------

def get_form(dataset_id: str):
    """Return the FormSchema for dataset_id, or None."""
    return _form.fetch_form(dataset_id)


def get_constraints(dataset_id: str) -> list:
    """Return the raw list of valid-combination dicts for dataset_id."""
    return _form.fetch_constraints(dataset_id)


def validate_request(dataset_id: str, request: dict) -> list:
    """Return a list of human-readable problems with a CDS request, or []."""
    return _form.validate_request(dataset_id, request)


def value_labels(dataset_id: str, param: str) -> dict:
    """Return a {value: display_label} map for one parameter from the form."""
    schema = get_form(dataset_id)
    if schema is None:
        return {}
    widget = schema.get_widget(param)
    if widget is None:
        return {}
    return widget.label_map


def available_values_from_constraints(
    constraints: list, param: str, fixed: Optional[dict] = None,
) -> set:
    """Return the set of still-valid values for *param*, given *fixed* —
    pure function, the core of the grey-out cascade. Ported verbatim from
    gb_wrapper.py (already pure Python, no geobridge/gdal_native
    dependency at all)."""
    fixed = fixed or {}
    fixed_sets = {
        k: ({v} if isinstance(v, str) else set(v))
        for k, v in fixed.items()
        if k != param and v
    }

    available: set = set()
    for combo in constraints:
        for key, chosen in fixed_sets.items():
            allowed = combo.get(key)
            if allowed is not None and not (chosen & set(allowed)):
                break
        else:
            values = combo.get(param)
            if values:
                available.update(values)
    return available


def available_values(dataset_id: str, param: str, fixed: Optional[dict] = None) -> set:
    """Fetch constraints for dataset_id and return valid values for *param*."""
    constraints = get_constraints(dataset_id)
    return available_values_from_constraints(constraints, param, fixed)


def field_states_from_constraints(constraints: list, fields, selection: Optional[dict] = None) -> dict:
    """Return, for each field, an ordered {value: enabled} map for the
    cascade. Ported verbatim from gb_wrapper.py."""
    selection = selection or {}
    states: dict = {}
    for field in fields:
        universe = available_values_from_constraints(constraints, field, {})
        enabled = available_values_from_constraints(constraints, field, selection)
        states[field] = {v: (v in enabled) for v in sorted(universe)}
    return states


def form_universes(dataset_id: str, fields) -> dict:
    """Return {field: [values]} taken from the form's enum widgets —
    fallback source for datasets that ship no constraints."""
    schema = get_form(dataset_id)
    if schema is None:
        return {}
    result = {}
    for field in fields:
        widget = schema.get_widget(field)
        if widget is not None and widget.value_list:
            result[field] = list(widget.value_list)
    return result


def field_states_from_sources(
    constraints: list, form_fallback: Optional[dict], fields, selection: Optional[dict] = None,
) -> dict:
    """Cascade states with a form fallback for constraint-less datasets.
    Ported verbatim from gb_wrapper.py."""
    selection = selection or {}
    form_fallback = form_fallback or {}
    states: dict = {}
    for field in fields:
        constraint_universe = available_values_from_constraints(constraints, field, {})
        if constraint_universe:
            enabled = available_values_from_constraints(constraints, field, selection)
            universe = constraint_universe
        else:
            universe = set(form_fallback.get(field, []))
            enabled = universe
        states[field] = {v: (v in enabled) for v in sorted(universe)}
    return states


def field_states(dataset_id: str, fields, selection: Optional[dict] = None) -> dict:
    """Fetch constraints (and form fallback) and compute the cascade states."""
    constraints = get_constraints(dataset_id)
    fallback = form_universes(dataset_id, fields)
    return field_states_from_sources(constraints, fallback, fields, selection)


def extra_required_field_defaults(dataset_id: str, known_fields) -> dict:
    """Best-effort {field: value} for CDS form fields that are required but
    fall outside the Browse tab's fixed cascade. Ported verbatim from
    gb_wrapper.py. Returns {} (never raises) if the form can't be fetched."""
    schema = get_form(dataset_id)
    if schema is None:
        return {}

    result = {}
    for widget in schema.widgets:
        if not widget.required or widget.name in known_fields:
            continue
        if widget.widget_type == "LicenceWidget":
            continue
        if not widget.values:
            continue

        details = widget.details or {}
        default_labels = details.get("default") or []
        value = None
        if default_labels:
            wanted_label = default_labels[0]
            pretty_labels = details.get("labels") or {}
            for raw_value, pretty_label in pretty_labels.items():
                if pretty_label == wanted_label:
                    value = raw_value
                    break
            if value is None:
                available = {entry.get("value") for entry in widget.values}
                if wanted_label in available:
                    value = wanted_label
        if value is None:
            value = widget.values[0].get("value")
        if value is not None:
            result[widget.name] = value
    return result


# ---------------------------------------------------------------------------
# WMTS layer construction
# ---------------------------------------------------------------------------

def build_wmts_layer_configs(
    dataset_id: str, variable: str, datetimes: list, descriptor: Any = None,
) -> list:
    """Build one QgsRasterLayer-ready config dict per timestamp."""
    configs = []
    for dt in datetimes:
        layer = _wmts.wmts_layer(dataset=dataset_id, variable=variable, datetime=dt, descriptor=descriptor)
        conf = layer.to_qgis()
        conf["label"] = dt
        configs.append(conf)
    return configs


# ---------------------------------------------------------------------------
# ARCO Zarr URL resolution — geobridge did this internally; gdal_native's
# extract_gdal/timeseries take a zarr_url + auth_header directly instead,
# so this plugin resolves them here, once, in one place.
# ---------------------------------------------------------------------------

_GEO_CHUNK_THRESHOLD_DAYS = 90


def _resolve_zarr(dataset_id: str, variable: str, *, prefer_geo_chunked: bool):
    """Return (zarr_url, arco_short_variable_name) for dataset_id/variable,
    resolving aliases and the correct per-variable ARCO subset exactly
    like gdal_native.wmts.wmts_layer() does for WMTS routing."""
    aliases = (_discover._load_overrides() or {}).get("variable_aliases", {})
    arco_variable = aliases.get(variable, variable)

    resolved = (
        _discover._arco_subset_for_variable(dataset_id, arco_variable)
        or _discover._arco_subset_for_variable(dataset_id, variable)
    )
    if not resolved:
        raise ValueError(
            f"Could not find an ARCO subset serving variable {variable!r} "
            f"for dataset {dataset_id!r}."
        )
    _prefix, sub = resolved
    zarr_urls = sub.get("zarr") or {}
    time_url = zarr_urls.get("timeChunked") or zarr_urls.get("time_chunked")
    geo_url = zarr_urls.get("geoChunked") or zarr_urls.get("geo_chunked")
    if not time_url and not geo_url:
        raise ValueError(f"No Zarr archive for {dataset_id!r}/{variable!r}.")

    if prefer_geo_chunked and geo_url:
        return geo_url, arco_variable
    if not prefer_geo_chunked and time_url:
        return time_url, arco_variable
    return (geo_url or time_url), arco_variable


def _pick_chunking_for_export(time_range: tuple, bbox: Optional[tuple]) -> bool:
    """True (prefer geo_chunked) only for a tiny bbox over a long range —
    same heuristic as geobridge/modules/extract.py's _pick_chunking, which
    this mirrors exactly (tiny-bbox-long-range is the one shape where
    geo_chunked's "small area, long time per chunk" layout beats
    time_chunked's "large area, short time per chunk" for a map export)."""
    if not bbox:
        return False
    from datetime import datetime as _dt

    def _parse(x):
        if isinstance(x, str):
            return _dt.fromisoformat(x.replace("Z", "+00:00") if "T" in x else x)
        return x

    start, end = _parse(time_range[0]), _parse(time_range[1])
    n_days = max((end - start).days, 1)
    west, south, east, north = bbox
    bbox_area = max((east - west) * (north - south), 0.0001)
    return bbox_area < 1.0 and n_days > _GEO_CHUNK_THRESHOLD_DAYS


# ---------------------------------------------------------------------------
# Phase 2 — GeoTIFF export. NEVER call these from a UI button handler
# directly; they must only run inside export_task.py's QgsTask worker
# thread, since both can take anywhere from seconds to hours.
# ---------------------------------------------------------------------------

def export_zarr_to_geotiff(
    *,
    dataset: str,
    variable: str,
    bbox: tuple,
    time_range: tuple,
    aggregation: str = "raw",
    output_path: Optional[str] = None,
    cog: bool = True,
    target_crs: Optional[str] = None,
    chunking: Optional[str] = None,
) -> str:
    """Export a bbox+time-range Zarr read to GeoTIFF — the Search tab's
    "Export to GeoTIFF" feature. target_crs is accepted for interface
    compatibility with gb_wrapper.py's signature but not used: every ARCO
    store checked so far is native EPSG:4326, and _write_geotiff's Warp
    step already reprojects the crop correctly regardless."""
    from pathlib import Path

    try:  # see module-top note: extract_gdal needs osgeo.gdal, kept lazy
        from .gdal_native import extract_gdal as _extract_gdal
    except ImportError:
        from gdal_native import extract_gdal as _extract_gdal

    prefer_geo = (chunking == "geo_chunked") if chunking else _pick_chunking_for_export(time_range, bbox)
    zarr_url, arco_variable = _resolve_zarr(dataset, variable, prefer_geo_chunked=prefer_geo)
    path = _extract_gdal.zarr_range_to_geotiff(
        zarr_url, arco_variable, time_range=time_range, bbox=bbox,
        output_path=Path(output_path), aggregation=aggregation,
        auth_header=_auth.auth_header(), cog=cog,
    )
    return str(path)


def export_cds_to_geotiff(
    *,
    dataset: str,
    request: dict,
    variable: Optional[str] = None,
    bbox: Optional[tuple] = None,
    output_path: Optional[str] = None,
    timeout: float = 3600,
    progress_callback: Optional[Callable[[str], None]] = None,
    cog: bool = True,
) -> str:
    path = _cds_download.cds_to_geotiff(
        dataset=dataset, request=request, variable=variable, bbox=bbox,
        output_path=output_path, timeout=timeout,
        progress_callback=progress_callback, cog=cog,
    )
    return str(path)


# ---------------------------------------------------------------------------
# Point time series
# ---------------------------------------------------------------------------

def zarr_point_time_series(
    *, dataset: str, variable: str, lon: float, lat: float, start, end,
    chunking: Optional[str] = None, aggregation: str = "raw",
) -> list:
    """Return a list of PointSample for a point over time — one bulk ARCO
    Zarr read ("Full history"). Always prefers geo_chunked when available
    (unlike the export path's area/duration heuristic): a point query is
    always the "tiny area, long time" shape that flavour is built for,
    regardless of how long a range is requested — same policy
    geobridge/modules/timeseries.py's zarr_point_time_series documented.

    aggregation bins the raw native-resolution read down to daily/weekly/
    monthly/annual mean/max/min (see timeseries.aggregate_samples) —
    "raw" (default) returns every timestep untouched.
    """
    prefer_geo = (chunking != "time_chunked")
    zarr_url, arco_variable = _resolve_zarr(dataset, variable, prefer_geo_chunked=prefer_geo)
    samples = _timeseries.zarr_point_time_series(
        zarr_url=zarr_url, variable=arco_variable, lon=lon, lat=lat,
        start=start, end=end, auth_header=_auth.auth_header(),
    )
    return _timeseries.aggregate_samples(samples, aggregation)
