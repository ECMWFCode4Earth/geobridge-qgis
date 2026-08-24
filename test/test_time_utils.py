# -*- coding: utf-8 -*-
"""Plain-Python tests for time_utils.py — no QGIS required, run with pytest."""

from datetime import datetime, timedelta

import pytest

from time_utils import (
    STEP_CHOICES,
    TooManyStepsError,
    default_step_choice,
    generate_time_steps,
    iso_from_parts,
)


def test_generate_time_steps_basic():
    start = datetime(2023, 7, 15, 0, 0, 0)
    end = datetime(2023, 7, 15, 3, 0, 0)
    steps = generate_time_steps(start, end, timedelta(hours=1))
    assert steps == [
        "2023-07-15T00:00:00Z",
        "2023-07-15T01:00:00Z",
        "2023-07-15T02:00:00Z",
        "2023-07-15T03:00:00Z",
    ]


def test_generate_time_steps_end_before_start_returns_empty():
    start = datetime(2023, 7, 15, 3, 0, 0)
    end = datetime(2023, 7, 15, 0, 0, 0)
    assert generate_time_steps(start, end, timedelta(hours=1)) == []


def test_generate_time_steps_zero_or_negative_step_raises():
    start = datetime(2023, 7, 15, 0, 0, 0)
    end = datetime(2023, 7, 16, 0, 0, 0)
    with pytest.raises(ValueError):
        generate_time_steps(start, end, timedelta(0))
    with pytest.raises(ValueError):
        generate_time_steps(start, end, timedelta(hours=-1))


def test_generate_time_steps_over_cap_raises_with_count():
    start = datetime(2023, 1, 1)
    end = datetime(2023, 12, 31)
    with pytest.raises(TooManyStepsError) as exc_info:
        generate_time_steps(start, end, timedelta(hours=1), max_steps=40)
    assert exc_info.value.count > 40
    assert exc_info.value.max_steps == 40


def test_generate_time_steps_truncate_caps_at_max_steps():
    start = datetime(2023, 1, 1)
    end = datetime(2023, 12, 31)
    steps = generate_time_steps(
        start, end, timedelta(hours=1), max_steps=40, truncate=True
    )
    assert len(steps) == 40
    assert steps[0] == "2023-01-01T00:00:00Z"


def test_default_step_choice_hours():
    assert default_step_choice("1h") == "1 hour"
    assert default_step_choice("3h") == "3 hours"
    assert default_step_choice("6h") == "6 hours"


def test_default_step_choice_day():
    assert default_step_choice("day") == "1 day"
    assert default_step_choice("1 day") == "1 day"
    assert default_step_choice("P1D") == "1 day"


def test_default_step_choice_unparseable_falls_back_to_an_hour():
    assert default_step_choice("") == "1 hour"
    assert default_step_choice("nonsense") == "1 hour"


def test_step_choices_ordering_finest_to_coarsest():
    deltas = list(STEP_CHOICES.values())
    assert deltas == sorted(deltas)


def test_iso_from_parts_default_time():
    assert iso_from_parts("2024", "07", "01") == "2024-07-01T00:00:00Z"


def test_iso_from_parts_with_time():
    assert iso_from_parts("2026", "05", "04", "06:00") == "2026-05-04T06:00:00Z"
    assert iso_from_parts("2026", "05", "04", "18") == "2026-05-04T18:00:00Z"


def test_iso_from_parts_rejects_impossible_date():
    with pytest.raises(ValueError):
        iso_from_parts("2023", "02", "30")  # Feb 30 doesn't exist
