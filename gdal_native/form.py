# -*- coding: utf-8 -*-
"""
gdal_native.form
~~~~~~~~~~~~~~~~

CDS form/constraints schema fetching — ported near-verbatim from
geobridge/modules/form.py (already pure stdlib there: urllib.request,
json, dataclasses, functools.lru_cache). Only the internal import of
`_load_cds_snapshot` was retargeted to this package's own discover module.

Fetches and parses the CDS form schema (every download parameter with its
display label, allowed values, and widget type) and the constraints JSON
(which parameter combinations are jointly valid) for a dataset — this is
what drives the Browse tab's cascading dropdown UI.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FormWidget:
    """One parameter widget in the CDS download form."""

    name: str
    label: str
    widget_type: str
    values: list
    required: bool = True
    details: dict = field(default_factory=dict)

    @property
    def value_list(self) -> list:
        return [v.get("value", v) for v in self.values
                if isinstance(v, dict) and "value" in v]

    @property
    def label_map(self) -> dict:
        return {v.get("value", ""): v.get("label", v.get("value", ""))
                for v in self.values if isinstance(v, dict)}


@dataclass
class FormSchema:
    """Parsed CDS form schema for one dataset."""

    dataset_id: str
    widgets: list = field(default_factory=list)
    raw: list = field(default_factory=list)

    def get_widget(self, name: str) -> Optional[FormWidget]:
        for w in self.widgets:
            if w.name == name:
                return w
        return None

    @property
    def variable_widget(self) -> Optional[FormWidget]:
        return self.get_widget("variable")

    @property
    def product_type_widget(self) -> Optional[FormWidget]:
        return self.get_widget("product_type")

    @property
    def year_widget(self) -> Optional[FormWidget]:
        return self.get_widget("year")

    @property
    def pressure_level_widget(self) -> Optional[FormWidget]:
        return self.get_widget("pressure_level")

    def parameter_names(self) -> list:
        return [w.name for w in self.widgets]

    def variables(self) -> list:
        w = self.variable_widget
        return w.values if w else []

    def years(self) -> list:
        w = self.year_widget
        return w.value_list if w else []

    def product_types(self) -> list:
        w = self.product_type_widget
        return w.values if w else []

    def pressure_levels(self) -> list:
        w = self.pressure_level_widget
        return w.value_list if w else []


# ---------------------------------------------------------------------------
# Fetching helpers
# ---------------------------------------------------------------------------

_FORM_CACHE: dict = {}
_CONSTRAINTS_CACHE: dict = {}


def _fetch_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "gdal_native/0.1"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


_CATALOGUE_API = "https://cds.climate.copernicus.eu/api/catalogue/v1/collections"


@lru_cache(maxsize=64)
def _fetch_live_links(cds_id: str) -> dict:
    """Fetch the current form/constraints link URLs for *cds_id* live from
    CDS — the catalogue rotates these to a new content-hashed filename
    whenever a dataset's form/rules change, and an old hashed URL keeps
    serving now-stale content instead of 404ing, so a URL bundled in the
    snapshot can go silently out of date. Returns {} on any failure."""
    try:
        data = _fetch_json(f"{_CATALOGUE_API}/{cds_id}", timeout=15)
    except Exception as exc:
        logger.debug("Live catalogue lookup failed for %s: %s", cds_id, exc)
        return {}

    links: dict = {}
    for link in data.get("links", []):
        rel = link.get("rel")
        href = link.get("href", "")
        if not href:
            continue
        if rel == "form" or "form.json" in href:
            links.setdefault("form", href)
        elif rel == "constraints" or "constraints.json" in href:
            links.setdefault("constraints", href)
    return links


def _snapshot_link(dataset_id: str, cds_id: str, rel: str) -> Optional[str]:
    """Look up link relation *rel* for *dataset_id* in the bundled CDS snapshot."""
    try:
        from .discover import _load_cds_snapshot
        snapshot = _load_cds_snapshot()
        entry = snapshot.get(cds_id) or snapshot.get(dataset_id)
        if entry:
            return (entry.get("links") or {}).get(rel)
    except Exception:
        pass
    return None


def _get_form_url(dataset_id: str) -> Optional[str]:
    cds_id = dataset_id.replace("_", "-")
    url = _fetch_live_links(cds_id).get("form")
    if url:
        return url
    url = _snapshot_link(dataset_id, cds_id, "form")
    if url:
        logger.debug("Using bundled snapshot form URL for %s (live lookup failed).", cds_id)
        return url
    return None


def _get_constraints_url(dataset_id: str) -> Optional[str]:
    cds_id = dataset_id.replace("_", "-")
    url = _fetch_live_links(cds_id).get("constraints")
    if url:
        return url
    url = _snapshot_link(dataset_id, cds_id, "constraints")
    if url:
        logger.debug("Using bundled snapshot constraints URL for %s (live lookup failed).", cds_id)
        return url
    return None


def _parse_widget(raw: dict) -> Optional[FormWidget]:
    """Parse one form widget definition.

    CDS choice widgets store their allowed values in two different shapes:
    a flat details.values list with a separate details.labels map, or
    values split across accordion groups under details.groups, each with
    its own values/labels. Both are flattened into one {value, label} list.
    """
    name = raw.get("name")
    label = raw.get("label", name or "")
    widget_type = raw.get("type", "")

    if not name:
        return None

    details = raw.get("details", {}) or {}

    values: list = []
    seen: set = set()

    def _add(value: str, display: Optional[str] = None) -> None:
        if not value or value in seen:
            return
        seen.add(value)
        values.append({"value": value, "label": display or value})

    def _consume(items: Any, label_map: dict) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, dict):
                v = item.get("value", "")
                _add(v, item.get("label", label_map.get(v, v)))
            elif isinstance(item, str):
                _add(item, label_map.get(item, item))

    _consume(details.get("values"), details.get("labels", {}) or {})

    for group_key in ("groups", "accordionGroups"):
        groups = details.get(group_key)
        if not isinstance(groups, list):
            continue
        for group in groups:
            if isinstance(group, dict):
                _consume(group.get("values"), group.get("labels", {}) or {})

    return FormWidget(
        name=name, label=label, widget_type=widget_type, values=values,
        required=raw.get("required", True), details=details,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_form(dataset_id: str, timeout: int = 20) -> Optional[FormSchema]:
    """Fetch and parse the CDS form schema for *dataset_id*. Cached per
    session in memory."""
    cds_id = dataset_id.replace("_", "-")

    if cds_id in _FORM_CACHE:
        return _FORM_CACHE[cds_id]

    url = _get_form_url(dataset_id)
    if not url:
        logger.warning("No form URL found for %s.", dataset_id)
        return None

    try:
        raw = _fetch_json(url, timeout=timeout)
    except urllib.error.URLError as exc:
        logger.warning("Could not fetch form for %s: %s", dataset_id, exc)
        return None
    except Exception as exc:
        logger.warning("Error parsing form for %s: %s", dataset_id, exc)
        return None

    widgets = []
    if isinstance(raw, list):
        for item in raw:
            w = _parse_widget(item)
            if w:
                widgets.append(w)

    schema = FormSchema(dataset_id=cds_id, widgets=widgets, raw=raw)
    _FORM_CACHE[cds_id] = schema
    return schema


def fetch_constraints(dataset_id: str, timeout: int = 20) -> list:
    """Fetch the constraints JSON for *dataset_id*: a list of valid
    parameter-combination dicts (param -> list of jointly-valid values)."""
    cds_id = dataset_id.replace("_", "-")

    if cds_id in _CONSTRAINTS_CACHE:
        return _CONSTRAINTS_CACHE[cds_id]

    url = _get_constraints_url(dataset_id)
    if not url:
        logger.warning("No constraints URL for %s.", dataset_id)
        return []

    try:
        data = _fetch_json(url, timeout=timeout)
        result = data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Could not fetch constraints for %s: %s", dataset_id, exc)
        return []

    _CONSTRAINTS_CACHE[cds_id] = result
    return result


def validate_request(dataset_id: str, request: dict) -> list:
    """Validate a CDS request dict against the form schema and constraints.
    Returns a list of human-readable error strings (empty = looks valid)."""
    errors = []

    schema = fetch_form(dataset_id)
    if schema:
        for param, val in request.items():
            if param == "data_format":
                continue
            widget = schema.get_widget(param)
            if widget is None or not widget.value_list:
                continue
            allowed = set(widget.value_list)
            submitted = [val] if isinstance(val, str) else list(val)
            bad = [v for v in submitted if v not in allowed]
            if bad:
                errors.append(f"'{param}': invalid value(s) {bad}. Allowed: {sorted(allowed)}")
    else:
        logger.warning("Form schema not available for %s - skipping allowed-values check.", dataset_id)

    constraints = fetch_constraints(dataset_id)
    if constraints:
        req_sets = {}
        for param, val in request.items():
            if param == "data_format":
                continue
            req_sets[param] = {val} if isinstance(val, str) else set(val)

        def _combo_matches(combo: dict) -> bool:
            for param, allowed_vals in combo.items():
                if param not in req_sets:
                    continue
                if not req_sets[param].intersection(allowed_vals):
                    return False
            return True

        if not any(_combo_matches(c) for c in constraints):
            errors.append(
                "The parameter combination is not valid for this dataset. "
                "Check the CDS portal for allowed combinations: "
                f"https://cds.climate.copernicus.eu/datasets/{dataset_id.replace('_', '-')}"
            )

    return errors


def clear_form_cache():
    """Clear the in-memory form, constraints, and live-link caches."""
    _FORM_CACHE.clear()
    _CONSTRAINTS_CACHE.clear()
    _fetch_live_links.cache_clear()
