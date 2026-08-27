# -*- coding: utf-8 -*-
"""
gb_wrapper
~~~~~~~~~~

The single point of contact between this plugin and the `geobridge` library.

No PyQt / qgis imports anywhere in this file, and no `import geobridge` at
module scope: if `geobridge` isn't installed yet, QGIS must still be able to
import this plugin package cleanly (otherwise QGIS disables the plugin with
a red error icon before the user ever sees the "Install dependencies"
button in Tab 1). Every function below imports `geobridge` internally and
converts a missing install into `GeobridgeNotInstalled` instead of letting
an `ImportError` propagate from an unexpected place.

Map layers are always built through `geobridge.wmts_layer(...).to_qgis()`
(the XYZ-tile approach) — never through `LayerDescriptor.to_qgis()`
directly, which builds a WMS-provider URI that QGIS cannot reliably parse
against ECMWF's WMTS server (see `geobridge/modules/wmts.py`'s module
docstring in the main GeoBridge repo).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Callable, Optional


class GeobridgeNotInstalled(RuntimeError):
    """Raised when `geobridge` (or an optional extra) isn't importable yet."""


class _NullStream:
    """No-op file-like object — see _quiet_stderr()."""

    def write(self, *args, **kwargs):
        pass

    def flush(self, *args, **kwargs):
        pass


@contextmanager
def _quiet_stderr():
    """Temporarily swallow stderr writes for the duration of the `with` block.

    gb.semantic_resources() (geobridge>=0.1.10) pulls in scikit-learn's
    optional pandas integration; on a QGIS install whose bundled pyarrow
    was built against NumPy 1.x, that hits numpy's own deprecated-attribute
    shim on every single call. The shim writes a full traceback string
    straight to sys.stderr as a side effect — bypassing Python's `warnings`
    module entirely, so warning filters can't catch it — even though the
    ImportError it's reporting is fully caught inside scikit-learn and
    results still come back correctly. Left alone, this would dump a
    scary-looking (but harmless) traceback into QGIS's log on every single
    search. Scoped tightly to just the one call rather than swallowing
    stderr for the plugin's whole lifetime.
    """
    original = sys.stderr
    sys.stderr = _NullStream()
    try:
        yield
    finally:
        sys.stderr = original


# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------

def is_core_available() -> bool:
    """True once `import geobridge` succeeds (pulls in only pyyaml)."""
    try:
        import geobridge  # noqa: F401
    except ImportError:
        return False
    return True


def is_zarr_extra_available() -> bool:
    """True once the heavy `geobridge[zarr]` deps are importable.

    Needed only for Phase 2 (zarr_to_geotiff / cds_to_geotiff / fuse).

    Also checks `aiohttp`/`requests`/`netCDF4` even though none of the
    three are part of `geobridge[zarr]` itself:

    - published geobridge[zarr] on PyPI declares plain "fsspec" rather
      than "fsspec[http]", so fsspec's HTTPFileSystem (used to open the
      ARCO Zarr stores over https://) silently lacks aiohttp/requests
      otherwise.
    - gb.cds_to_geotiff() (the CDS API download path for datasets not yet
      in the ARCO lake, e.g. ERA5-Land via the Browse tab) needs netCDF4
      to read the NetCDF file CDS returns, but that's outside the [zarr]
      extra's scope (ARCO/Zarr reads only).

    Without this check, a QGIS install that already has the six core
    zarr-tier packages but not these three reports as "fully installed"
    and hides the only button that would fix it — see
    `_on_install_core_clicked` in geobridge_plugin_dialog.py, which
    installs all four together for exactly this reason.
    """
    try:
        import dask  # noqa: F401
        import fsspec  # noqa: F401
        import rasterio  # noqa: F401
        import rioxarray  # noqa: F401
        import xarray  # noqa: F401
        import zarr  # noqa: F401
        import aiohttp  # noqa: F401
        import requests  # noqa: F401
        import netCDF4  # noqa: F401
    except ImportError:
        return False
    return True


def geobridge_version() -> Optional[str]:
    """Return the installed geobridge version, or None if not installed."""
    try:
        import geobridge
    except ImportError:
        return None
    return geobridge.__version__


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def authenticate(key: Optional[str]) -> None:
    """Initialise a geobridge CDS API session for this process.

    Raises
    ------
    GeobridgeNotInstalled
        If geobridge isn't installed yet.
    geobridge.AuthenticationError
        If the key is empty/invalid and no other credential source exists.
    """
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc
    gb.authenticate(key=key or None)


def is_authenticated() -> bool:
    """True if authenticate() has succeeded in this process."""
    try:
        import geobridge as gb
    except ImportError:
        return False
    return gb.is_authenticated()


# ---------------------------------------------------------------------------
# Discovery / semantic search
# ---------------------------------------------------------------------------

def semantic_resources(query: str, max_results: int = 15, min_confidence: float = 0.1) -> list:
    """Return a list of geobridge.ResourceMatch, unsorted (caller sorts).

    Powers the Search tab. Uses gb.semantic_resources() (geobridge>=0.1.10)
    rather than the older gb.semantic_search() — it combines the same
    curated rule-based matching (vocabulary.yaml's ~40 hand-written use
    cases) with TF-IDF cosine-similarity retrieval over the *entire*
    catalog, so results aren't limited to datasets a curator happened to
    write a use case for. A ResourceMatch has `themes`/`use_cases` (lists,
    often empty for TF-IDF-only matches) rather than SemanticMatch's single
    `use_case_label` string — see _populate_results in
    geobridge_plugin_dialog.py for how the Use case column handles that.
    """
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc
    with _quiet_stderr():
        return gb.semantic_resources(query, max_results=max_results, min_confidence=min_confidence)


def use_case_labels() -> dict:
    """Map every curated use-case id to its human-readable label, e.g.
    {"extreme_precipitation": "Extreme precipitation event mapping"} —
    ResourceMatch.use_cases carries raw ids, not labels."""
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc
    return {uc["id"]: uc["label"] for uc in gb.list_use_cases()}


def discover_one(dataset_id: str):
    """Return a geobridge.LayerDescriptor for dataset_id, or None."""
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc
    return gb.discover_one(dataset_id)


def _cds_snapshot_dataset_ids() -> Optional[set]:
    """Dataset ids (hyphenated, CDS-style) present in geobridge's bundled
    `semantic/cds_snapshot.yaml` — the STAC catalogue snapshot geobridge
    ships and refreshes itself, as opposed to `arco_snapshot.yaml` (the
    narrower ARCO/Zarr-backed subset merged into the same discover()
    result). Returns None if the snapshot can't be located/read, so the
    caller can fall back to showing everything rather than an empty list.
    """
    try:
        import geobridge
        import yaml
    except ImportError:
        return None
    from pathlib import Path

    snapshot_path = Path(geobridge.__file__).parent / "semantic" / "cds_snapshot.yaml"
    if not snapshot_path.exists():
        return None
    try:
        with snapshot_path.open(encoding="utf-8") as fp:
            data = yaml.safe_load(fp) or {}
    except (OSError, yaml.YAMLError):
        return None
    return set((data.get("datasets") or {}).keys())


@lru_cache(maxsize=1)
def _load_arco_snapshot() -> dict:
    """Cache of geobridge's bundled `semantic/arco_snapshot.yaml` — the ARCO
    catalogue snapshot, keyed by dataset id (underscored form). Empty dict
    if it can't be located/read."""
    try:
        import geobridge
        import yaml
    except ImportError:
        return {}
    from pathlib import Path

    snapshot_path = Path(geobridge.__file__).parent / "semantic" / "arco_snapshot.yaml"
    if not snapshot_path.exists():
        return {}
    try:
        with snapshot_path.open(encoding="utf-8") as fp:
            data = yaml.safe_load(fp) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data.get("datasets") or {}


def _variable_meta(dataset_id: str, variable: str) -> dict:
    """Raw per-variable metadata dict from arco_snapshot.yaml (unit,
    colormap, value_min, value_max, log_scale, ...) — {} if the dataset
    isn't ARCO-backed or the variable isn't found.

    LayerDescriptor.colormap (what discover()/discover_one() expose) is a
    *dataset*-level field populated from only the dataset's first variable
    (see geobridge/modules/discover.py's _descriptor_from_arco) — using it
    for whichever variable happens to be currently selected is wrong
    whenever that isn't the first one (e.g. reanalysis_era5_single_levels's
    first variable is "blh", unit "m"; naively using descriptor.colormap
    for a t2m plot would mislabel its axis "m" instead of "K"). This reads
    the per-variable metadata directly from arco_snapshot.yaml instead —
    the same file gb.wmts_layer(style="default") itself reads to pick the
    palette/style sent to the WMTS server, so it matches what's actually
    rendered on screen (see modules/wmts.py), unlike the separate/
    inconsistent preset table modules/style.py's to_qgis_style() uses.
    """
    arco = _load_arco_snapshot()
    entry = arco.get(dataset_id.replace("-", "_"))
    if not entry:
        return {}
    subsets = entry.get("subsets") or {}
    if not subsets:
        return {}
    sub = None
    for preferred in ("sfc", "all", "surface"):
        if preferred in subsets:
            sub = subsets[preferred]
            break
    if sub is None:
        sub = next(iter(subsets.values()))
    return (sub.get("variables") or {}).get(variable) or {}


def variable_unit(dataset_id: str, variable: str) -> str:
    """Physical unit for one specific variable of one dataset, e.g. "K" for
    reanalysis_era5_single_levels/t2m — "" if unknown."""
    return _variable_meta(dataset_id, variable).get("unit", "") or ""


def variable_style(dataset_id: str, variable: str) -> dict:
    """Legend-ready style info for one variable: {"unit", "colormap",
    "value_min", "value_max"} — same source gb.wmts_layer(style="default")
    itself reads to color the WMTS tiles this plugin displays, so it
    describes what's actually on screen. Empty dict if unknown (e.g. a
    CDS-only dataset with no ARCO backing) — caller should hide/skip the
    legend in that case rather than show misleading blanks."""
    meta = _variable_meta(dataset_id, variable)
    if not meta or meta.get("value_min") is None or meta.get("value_max") is None:
        return {}
    return {
        "unit": meta.get("unit", "") or "",
        "colormap": meta.get("colormap", "") or "viridis",
        "value_min": meta.get("value_min"),
        "value_max": meta.get("value_max"),
    }


def discover_all(keyword: Optional[str] = None) -> list:
    """Return geobridge LayerDescriptors restricted to datasets present in
    the bundled cds_snapshot.yaml catalogue (used to populate the dataset
    dropdown on the Browse-by-Variable tab).

    `geobridge.discover()` on its own merges in `arco_snapshot.yaml` too,
    which can include ARCO/Zarr-only entries that were never part of the
    CDS STAC catalogue snapshot; those are filtered back out here so the
    Browse tab shows only genuine CDS-catalogue datasets. ARCO-sourced
    descriptors that *do* have a matching cds_snapshot.yaml entry (the
    common case — most ARCO datasets are also in the CDS catalogue) are
    kept; only entries with no cds_snapshot.yaml backing at all are
    dropped. Reads local snapshot files only — no network call.
    """
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc
    results = gb.discover(keyword=keyword) if keyword else gb.discover()

    cds_ids = _cds_snapshot_dataset_ids()
    if cds_ids is None:
        return results
    # ARCO-sourced descriptor ids are underscored; cds_snapshot.yaml's are
    # hyphenated — normalise before comparing.
    return [ds for ds in results if ds.id.replace("_", "-") in cds_ids]


# ---------------------------------------------------------------------------
# CDS form / constraints — powers the "Browse by Variable" tab.
#
# Two separate data sources with different jobs:
#
#   * gb.fetch_form(ds)        — the display schema: one widget per CDS
#     parameter with its {value,label} pairs. Used for LABELS and for the
#     handful of parameters that have no constraints entry.
#   * gb.fetch_constraints(ds) — the list of jointly-valid combinations.
#     This is the source of truth for *what is actually selectable*, and it
#     is what drives the grey-out cascade.
#
# IMPORTANT (learned the hard way against real datasets): for ERA5 the form's
# `variable` widget is a grouped `StringListArrayWidget` whose values the
# geobridge form parser does NOT unpack, so `FormSchema.variables()` comes
# back EMPTY. The variable list must therefore be derived from the
# constraints, not the form — which `available_values(ds, "variable", {})`
# does. Never populate the variable dropdown straight from fetch_form().
# ---------------------------------------------------------------------------

def get_form(dataset_id: str):
    """Return the geobridge FormSchema for dataset_id, or None.

    Used only for display labels and for parameters absent from the
    constraints. For *which values are valid*, use available_values().
    """
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc
    return gb.fetch_form(dataset_id)


def extra_required_field_defaults(dataset_id: str, known_fields) -> dict:
    """Best-effort {field: value} for CDS form fields that are required but
    fall outside the Browse tab's fixed cascade (`known_fields` — product_
    type/variable/year/month/day/time).

    Some datasets have additional required parameters the cascade has no
    column for — e.g. derived-near-surface-meteorological-variables needs
    "reference_dataset" (no default; picks its first listed choice, "cru")
    and "version" (has a declared default, "2.1" -> raw value "2_1").
    Without these, CDS rejects the request outright with a 400 even though
    every cascade field looks correctly filled in — nothing in the UI
    hints that a *different*, non-cascade field is what's actually
    missing. Returns {} (never raises) if the form can't be fetched.
    """
    try:
        schema = get_form(dataset_id)
    except GeobridgeNotInstalled:
        return {}
    if schema is None:
        return {}

    result = {}
    for widget in schema.widgets:
        if not widget.required or widget.name in known_fields:
            continue
        if widget.widget_type == "LicenceWidget":
            continue  # terms-of-use acceptance, not a data parameter
        if not widget.values:
            continue

        details = widget.details or {}
        default_labels = details.get("default") or []
        value = None
        if default_labels:
            wanted_label = default_labels[0]
            # widget.values[i]["label"] just echoes the raw value (e.g.
            # "2_0"), not the human-readable label CDS uses in "default"
            # (e.g. "2.0") — the raw_value -> pretty_label map lives
            # separately in details["labels"], so look up the default
            # there instead of against widget.values directly.
            pretty_labels = details.get("labels") or {}
            for raw_value, pretty_label in pretty_labels.items():
                if pretty_label == wanted_label:
                    value = raw_value
                    break
            if value is None:
                # Fall back to a verbatim match in case this widget has
                # no separate labels map and "default" already holds a
                # raw value.
                available = {entry.get("value") for entry in widget.values}
                if wanted_label in available:
                    value = wanted_label
        if value is None:
            value = widget.values[0].get("value")
        if value is not None:
            result[widget.name] = value
    return result


def get_constraints(dataset_id: str) -> list:
    """Return the raw list of valid-combination dicts for dataset_id.

    Each dict maps a CDS parameter name to a list of values; the whole
    dict is valid only if every parameter is satisfied jointly. Returns an
    empty list if the dataset has no constraints file.
    """
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc
    return gb.fetch_constraints(dataset_id)


def validate_request(dataset_id: str, request: dict) -> list:
    """Return a list of human-readable problems with a CDS request, or [].

    Wraps geobridge.validate_request — checks the request's values against
    the form's allowed enums and the constraints' valid combinations. An
    empty list means the request looks submittable.
    """
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc
    return gb.validate_request(dataset_id, request)


def value_labels(dataset_id: str, param: str) -> dict:
    """Return a {value: display_label} map for one parameter from the form.

    Best-effort: returns {} when the form is unavailable or the parameter's
    widget carries no labels (e.g. ERA5's grouped variable widget). Callers
    should fall back to showing the raw value when a label is missing.
    """
    schema = get_form(dataset_id)
    if schema is None:
        return {}
    widget = schema.get_widget(param)
    if widget is None:
        return {}
    return widget.label_map


def available_values_from_constraints(
    constraints: list,
    param: str,
    fixed: Optional[dict] = None,
) -> set:
    """Return the set of still-valid values for *param*, given *fixed*.

    Pure function (no network, no geobridge import) so it can be unit
    tested against captured constraint fixtures — this is the core of the
    grey-out cascade.

    A combo is *compatible* with the current selection when, for every
    already-chosen parameter in `fixed`, the combo either does not mention
    that parameter (unconstrained) or lists the chosen value among its
    allowed values. The available set for `param` is then the union of
    `param`'s values across all compatible combos.

    Parameters
    ----------
    constraints : list[dict]
        As returned by :func:`get_constraints`.
    param : str
        The parameter whose selectable values we want (e.g. "day").
    fixed : dict, optional
        The user's current selections, {param: value or [values]}. The
        entry for `param` itself is ignored, so a widget never constrains
        its own option list.

    Notes
    -----
    Compatibility uses set intersection, so a single-value cascade (one
    year, one month, ...) and a multi-select both work: a combo counts as
    compatible if it permits *at least one* chosen value for each fixed
    parameter.
    """
    fixed = fixed or {}
    # Normalise each fixed selection to a non-empty set of strings.
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
                break  # this combo rules out the current selection
        else:
            values = combo.get(param)
            if values:
                available.update(values)
    return available


def available_values(
    dataset_id: str,
    param: str,
    fixed: Optional[dict] = None,
) -> set:
    """Fetch constraints for dataset_id and return valid values for *param*.

    Thin fetching wrapper around
    :func:`available_values_from_constraints`. Call with
    ``param="variable", fixed={}`` to get the full variable list (the
    correct source for the variable dropdown — see the module note above).
    """
    constraints = get_constraints(dataset_id)
    return available_values_from_constraints(constraints, param, fixed)


def field_states_from_constraints(
    constraints: list,
    fields,
    selection: Optional[dict] = None,
) -> dict:
    """Return, for each field, an ordered {value: enabled} map for the cascade.

    Pure function (no network) — the direct driver of the grey-out UI.

    For every field:

    * its *universe* (all values that ever appear, ignoring the current
      selection) is what the widget displays, so greyed options stay
      visible rather than disappearing; and
    * a value is *enabled* iff it is still valid given the selections made
      in the OTHER fields — i.e. it appears in
      :func:`available_values_from_constraints` for that field. A field
      never disables its own currently-chosen value.

    Parameters
    ----------
    constraints : list[dict]
        As returned by :func:`get_constraints`.
    fields : iterable[str]
        Parameter names to compute, e.g. ("product_type", "variable",
        "year", "month", "day", "time").
    selection : dict, optional
        Current user choices, {param: value or [values]}.

    Returns
    -------
    dict[str, dict[str, bool]]
        {field: {value: enabled}}, values sorted, ready to paint onto a
        list/combo where enabled=False means "grey it out".
    """
    selection = selection or {}
    states: dict = {}
    for field in fields:
        universe = available_values_from_constraints(constraints, field, {})
        enabled = available_values_from_constraints(constraints, field, selection)
        states[field] = {v: (v in enabled) for v in sorted(universe)}
    return states


def form_universes(dataset_id: str, fields) -> dict:
    """Return {field: [values]} taken from the form's enum widgets.

    Fallback source of options for datasets that ship *no* constraints
    (e.g. derived-utci-historical-timeseries), whose variable list lives
    only in the form. Only fields whose widget carries a flat value list
    are included; grouped widgets (ERA5's variable) yield nothing here and
    are covered by the constraints instead.
    """
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
    constraints: list,
    form_fallback: Optional[dict],
    fields,
    selection: Optional[dict] = None,
) -> dict:
    """Cascade states with a form fallback for constraint-less datasets.

    Pure function. For each field:

    * if the constraints define a universe for it, behave exactly like
      :func:`field_states_from_constraints` (greying driven by the joint
      combinations); otherwise
    * fall back to ``form_fallback[field]`` as the universe and mark every
      value enabled — with no constraints there is no co-dependency to
      grey against, so all options stay selectable.

    Parameters
    ----------
    constraints : list[dict]
        As returned by :func:`get_constraints` (may be empty).
    form_fallback : dict, optional
        {field: [values]} from :func:`form_universes`.
    fields : iterable[str]
    selection : dict, optional
    """
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
            enabled = universe  # no constraints -> nothing to grey out
        states[field] = {v: (v in enabled) for v in sorted(universe)}
    return states


def field_states(
    dataset_id: str,
    fields,
    selection: Optional[dict] = None,
) -> dict:
    """Fetch constraints (and form fallback) and compute the cascade states.

    Thin fetching wrapper around :func:`field_states_from_sources`.
    """
    constraints = get_constraints(dataset_id)
    fallback = form_universes(dataset_id, fields)
    return field_states_from_sources(constraints, fallback, fields, selection)


# ---------------------------------------------------------------------------
# WMTS layer construction — always via WmtsLayer.to_qgis() (XYZ, working)
# ---------------------------------------------------------------------------

def build_wmts_layer_configs(
    dataset_id: str,
    variable: str,
    datetimes: list,
    descriptor: Any = None,
) -> list:
    """Build one QgsRasterLayer-ready config dict per timestamp.

    Each dict has keys {"uri", "name", "provider", "label"} — pass
    uri/name/provider straight into QgsRasterLayer(uri, name, provider).
    "label" is the timestamp string, for UI display.

    Always resolves through geobridge.wmts_layer(...).to_qgis() (the XYZ
    tile approach) — never LayerDescriptor.to_qgis() (the broken WMS-
    provider approach for this server).
    """
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc

    configs = []
    for dt in datetimes:
        wmts_layer = gb.wmts_layer(
            dataset=dataset_id,
            variable=variable,
            datetime=dt,
            descriptor=descriptor,
        )
        conf = wmts_layer.to_qgis()
        conf["label"] = dt
        configs.append(conf)
    return configs


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
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc
    path = gb.zarr_to_geotiff(
        dataset=dataset,
        variable=variable,
        bbox=bbox,
        time_range=time_range,
        aggregation=aggregation,
        output_path=output_path,
        cog=cog,
        target_crs=target_crs,
        chunking=chunking,
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
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc
    path = gb.cds_to_geotiff(
        dataset=dataset,
        request=request,
        variable=variable,
        bbox=bbox,
        output_path=output_path,
        timeout=timeout,
        progress_callback=progress_callback,
        cog=cog,
    )
    return str(path)


# ---------------------------------------------------------------------------
# Point time series — one WMTS GetFeatureInfo request per timestep.
# ---------------------------------------------------------------------------

def point_time_series(
    *,
    dataset: str,
    variable: str,
    lon: float,
    lat: float,
    start,
    end,
    step_days: float = 1,
    zoom: int = 8,
    style: str = "default",
    progress_callback: Optional[Callable[[int, int], None]] = None,
    is_canceled: Optional[Callable[[], bool]] = None,
) -> list:
    """Return a list of geobridge.PointSample for a point over time.

    Reimplements geobridge.point_time_series()'s loop (rather than calling
    it directly) so each step can report progress and be cancelled early:
    it issues one WMTS GetFeatureInfo request per timestep
    (geobridge.point_value()), and a long range run from inside a QgsTask
    needs both — the Time Series tab cancels an in-flight fetch as soon as
    the user clicks a new point.
    """
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc

    import time
    from datetime import datetime, timedelta

    if isinstance(start, str):
        start = datetime.fromisoformat(start.replace("Z", "+00:00") if "T" in start else start)
    if isinstance(end, str):
        end = datetime.fromisoformat(end.replace("Z", "+00:00") if "T" in end else end)
    step = timedelta(days=step_days)

    descriptor = gb.discover_one(dataset)
    if descriptor is None:
        raise gb.TimeSeriesError(
            f"Could not discover dataset {dataset!r}. "
            "Check the identifier or call gb.discover() to list options."
        )

    # ECMWF's WMTS server can reset a connection (WinError 10054 on Windows)
    # if GetFeatureInfo requests land back-to-back with no pacing, which a
    # long range easily produces since this loop fires one request per
    # timestep. A small delay between requests plus a short retry on
    # failure means one transient reset costs a step, not the whole fetch.
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
                value = gb.point_value(
                    dataset, variable, lon, lat, current,
                    zoom=zoom, style=style, descriptor=descriptor,
                )
                break
            except gb.TimeSeriesError:
                if attempt == _MAX_RETRIES:
                    # Persistent failure for this one timestep — record it
                    # as missing (same as "outside the data mask") rather
                    # than aborting every other timestep in the range.
                    value = None
                else:
                    time.sleep(_RETRY_DELAY_SECONDS)

        samples.append(gb.PointSample(time=current, value=value))
        done += 1
        if progress_callback is not None:
            progress_callback(done, total)
        current += step
        time.sleep(_REQUEST_PACING_SECONDS)

    return samples


# ---------------------------------------------------------------------------
# Point time series — bulk ARCO Zarr read (long/dense series).
# ---------------------------------------------------------------------------

def zarr_point_time_series(
    *,
    dataset: str,
    variable: str,
    lon: float,
    lat: float,
    start,
    end,
    chunking: Optional[str] = None,
) -> list:
    """Return a list of geobridge.PointSample for a point over time.

    Thin pass-through to geobridge.zarr_point_time_series(), unlike
    point_time_series() above: the whole range comes back from one
    (chunked) archive read rather than a per-step loop, so there's no
    progress to report incrementally and nothing to cancel mid-flight —
    the caller can only decide not to start it.

    Requires the geobridge[zarr] extra and a prior gb.authenticate() call;
    both are enforced inside geobridge itself, which raises
    geobridge.modules.extract.ExtractionError with a specific message for
    whichever one is missing.

    Raises
    ------
    GeobridgeNotInstalled
        If geobridge isn't installed yet.
    """
    try:
        import geobridge as gb
    except ImportError as exc:
        raise GeobridgeNotInstalled(str(exc)) from exc

    return gb.zarr_point_time_series(
        dataset=dataset,
        variable=variable,
        lon=lon,
        lat=lat,
        start=start,
        end=end,
        chunking=chunking,
    )
