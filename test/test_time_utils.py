# -*- coding: utf-8 -*-
"""Plain-Python tests for time_utils.py — no QGIS required, run with pytest."""

from datetime import datetime, timedelta

import pytest

from time_utils import (
    STEP_CHOICES,
    TooManyStepsError,
    default_step_choice,
    finer_than_native_steps,
    generate_time_steps,
    iso_from_parts,
    native_step_seconds,
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


def test_native_step_seconds_parses_known_forms():
    assert native_step_seconds("1h") == 3600
    assert native_step_seconds("month") == 30 * 86400
    assert native_step_seconds("year") == 365 * 86400


def test_native_step_seconds_none_for_unparseable():
    assert native_step_seconds("") is None
    assert native_step_seconds("nonperiodic") is None
    assert native_step_seconds("nonsense") is None


def test_finer_than_native_steps_monthly_dataset():
    finer = finer_than_native_steps("month")
    assert finer == {"1 hour", "3 hours", "6 hours", "1 day", "1 week"}
    assert "1 month" not in finer  # exact match, not finer
    assert "1 year" not in finer  # coarser, not finer


def test_finer_than_native_steps_hourly_dataset_disables_nothing():
    # 1 hour is already the finest offered choice — nothing is finer.
    assert finer_than_native_steps("1h") == set()


def test_finer_than_native_steps_unparseable_disables_nothing():
    assert finer_than_native_steps("") == set()
    assert finer_than_native_steps("nonperiodic") == set()


def test_finer_than_native_steps_daily_dataset():
    finer = finer_than_native_steps("day")
    assert finer == {"1 hour", "3 hours", "6 hours"}
