# -*- coding: utf-8 -*-
"""Plain-Python tests for the constraint grey-out core in gb_wrapper.py.

No QGIS and no geobridge needed: they exercise the pure function
`available_values_from_constraints` against small fixtures shaped like the
real CDS constraints captured from the live catalogue (ERA5 and UTCI).
Run with: pytest test/test_available_values.py
"""

import pytest

from gb_wrapper import available_values_from_constraints as avail
from gb_wrapper import field_states_from_constraints
from gb_wrapper import field_states_from_sources


# --- Fixtures shaped like real fetch_constraints() output ------------------

# ERA5-like: each combo pins a (year, month) and lists that month's days.
# Note there is NO combo for 2026 month 08 -> that month is not yet published.
ERA5 = [
    {
        "year": ["2024"], "month": ["01"],
        "day": [f"{d:02d}" for d in range(1, 32)],
        "product_type": ["reanalysis"],
        "variable": ["2m_temperature", "10m_u_component_of_wind"],
    },
    {
        "year": ["2024"], "month": ["02"],
        "day": [f"{d:02d}" for d in range(1, 30)],
        "product_type": ["reanalysis"],
        "variable": ["2m_temperature", "total_precipitation"],
    },
    {
        "year": ["2026"], "month": ["07"],
        "day": ["01", "02", "03", "04", "05"],
        "product_type": ["reanalysis"],
        "variable": ["2m_temperature"],
    },
]

# UTCI-like: no month/day at all; product_type partitions the year ranges.
UTCI = [
    {
        "product_type": ["consolidated_dataset"],
        "variable": ["universal_thermal_climate_index"],
        "year": [str(y) for y in range(1940, 2020)],
    },
    {
        "product_type": ["intermediate_dataset"],
        "variable": ["universal_thermal_climate_index"],
        "year": [str(y) for y in range(2020, 2026)],
    },
]


# --- Variable list comes from constraints, with empty selection ------------

def test_variable_list_is_union_over_all_combos():
    # This is the case that rescues ERA5, whose form widget yields no vars.
    assert avail(ERA5, "variable", {}) == {
        "2m_temperature",
        "10m_u_component_of_wind",
        "total_precipitation",
    }


def test_empty_constraints_gives_empty_set():
    assert avail([], "variable", {}) == set()
    assert avail([], "day", {"year": "2024"}) == set()


# --- Day-level grey-out near the present (the ERA5 latency case) -----------

def test_days_available_for_published_month():
    assert avail(ERA5, "day", {"year": "2026", "month": "07"}) == {
        "01", "02", "03", "04", "05",
    }


def test_days_all_grey_for_unpublished_month():
    # 2026-08 has no combo -> nothing selectable (all days greyed).
    assert avail(ERA5, "day", {"year": "2026", "month": "08"}) == set()


def test_february_day_count_differs_from_january():
    jan = avail(ERA5, "day", {"year": "2024", "month": "01"})
    feb = avail(ERA5, "day", {"year": "2024", "month": "02"})
    assert "31" in jan and "31" not in feb


# --- Cross-parameter grey-out (the UTCI product_type -> year case) ---------

def test_year_range_depends_on_product_type():
    assert avail(UTCI, "year", {"product_type": "intermediate_dataset"}) == {
        "2020", "2021", "2022", "2023", "2024", "2025",
    }
    consolidated = avail(UTCI, "year", {"product_type": "consolidated_dataset"})
    assert "1940" in consolidated and "2025" not in consolidated


# --- A widget must never constrain its own option list ---------------------

def test_param_ignores_its_own_current_selection():
    # User already has year=1940 selected; recomputing the year list for the
    # consolidated product must still offer the whole consolidated range,
    # not collapse to just {1940}.
    result = avail(
        UTCI, "year",
        {"product_type": "consolidated_dataset", "year": "1940"},
    )
    assert "1941" in result and "2000" in result


# --- Combos that omit a key are treated as unconstrained on that key -------

def test_missing_key_in_combo_is_compatible():
    combos = [
        {"variable": ["x"], "day": ["01", "02"]},  # no 'year' key at all
    ]
    # A year selection must not exclude a combo that never mentions year.
    assert avail(combos, "day", {"year": "2024"}) == {"01", "02"}


# --- Multi-select selection stays compatible if any value matches ----------

def test_multi_value_selection_matches_on_intersection():
    result = avail(UTCI, "year",
                   {"product_type": ["consolidated_dataset",
                                     "intermediate_dataset"]})
    # Both partitions permitted -> union of both year ranges.
    assert "1940" in result and "2025" in result


# --- field_states: the direct driver of the grey-out UI --------------------

def test_field_states_shows_full_universe_with_no_selection():
    states = field_states_from_constraints(ERA5, ["month"], {})
    # Every month that appears anywhere is present and enabled.
    assert states["month"]["01"] is True
    assert states["month"]["07"] is True


def test_field_states_greys_downstream_on_selection():
    # Selecting an unpublished year+month greys every day.
    states = field_states_from_constraints(
        ERA5, ["day"], {"year": "2026", "month": "08"},
    )
    assert states["day"]  # universe is non-empty (days exist for other months)
    assert all(enabled is False for enabled in states["day"].values())


def test_field_states_keeps_published_days_enabled():
    states = field_states_from_constraints(
        ERA5, ["day"], {"year": "2026", "month": "07"},
    )
    assert states["day"]["01"] is True
    assert states["day"]["05"] is True
    assert states["day"]["06"] is False  # 2026-07 only has days 01-05 here


def test_field_states_multiple_fields_at_once():
    states = field_states_from_constraints(
        UTCI, ["product_type", "year"], {"product_type": "intermediate_dataset"},
    )
    # product_type keeps its own options fully enabled (never self-constrains)
    assert states["product_type"]["consolidated_dataset"] is True
    assert states["product_type"]["intermediate_dataset"] is True
    # year is greyed down to the intermediate range
    assert states["year"]["2019"] is False
    assert states["year"]["2020"] is True
    assert states["year"]["2025"] is True


def test_field_states_values_are_sorted():
    states = field_states_from_constraints(ERA5, ["day"], {})
    days = list(states["day"].keys())
    assert days == sorted(days)


# --- field_states_from_sources: form fallback for constraint-less datasets -

def test_sources_uses_constraints_when_present():
    # With constraints, behaves exactly like the constraints-only path.
    from_sources = field_states_from_sources(ERA5, {}, ["day"],
                                             {"year": "2026", "month": "07"})
    from_constraints = field_states_from_constraints(ERA5, ["day"],
                                                     {"year": "2026", "month": "07"})
    assert from_sources == from_constraints


def test_sources_falls_back_to_form_when_no_constraints():
    # Mirrors derived-utci-historical-timeseries: no constraints, variables
    # only in the form. Every form value shows and is enabled (no cascade).
    form = {"variable": ["mean_radiant_temperature",
                         "universal_thermal_climate_index"]}
    states = field_states_from_sources([], form, ["variable", "day"], {})
    assert states["variable"] == {
        "mean_radiant_temperature": True,
        "universal_thermal_climate_index": True,
    }
    assert states["day"] == {}  # no day universe from either source


def test_sources_empty_everywhere_is_empty():
    assert field_states_from_sources([], {}, ["variable"], {}) == {"variable": {}}


# --- Optional live smoke test ----------------------------------------------
# Guarded so the offline tests above always run: this one is skipped unless
# BOTH geobridge is importable AND GEOBRIDGE_LIVE=1 is set (it hits the
# network). The skip is evaluated lazily, per-test, not at import time.

import importlib.util
import os

_HAVE_GEOBRIDGE = importlib.util.find_spec("geobridge") is not None


@pytest.mark.skipif(
    not (_HAVE_GEOBRIDGE and os.environ.get("GEOBRIDGE_LIVE")),
    reason="needs geobridge importable and GEOBRIDGE_LIVE=1 (hits the network)",
)
def test_live_era5_variable_list_is_nonempty():
    from gb_wrapper import available_values
    variables = available_values("reanalysis-era5-single-levels", "variable", {})
    assert len(variables) > 100  # ~262 at time of writing
    assert "2m_temperature" in variables
