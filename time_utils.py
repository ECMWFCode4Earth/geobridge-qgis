# -*- coding: utf-8 -*-
"""
time_utils
~~~~~~~~~~

Pure time-step math for the Tab 2 "time range" control. Zero Qt/qgis
imports — runs under plain pytest, no QGIS installation required.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import List

# Ordered so the UI combo box lists them from finest to coarsest granularity.
STEP_CHOICES = OrderedDict(
    [
        ("1 hour", timedelta(hours=1)),
        ("3 hours", timedelta(hours=3)),
        ("6 hours", timedelta(hours=6)),
        ("1 day", timedelta(days=1)),
        ("1 week", timedelta(days=7)),
    ]
)

DEFAULT_MAX_STEPS = 40


class TooManyStepsError(ValueError):
    """Raised when a (start, end, step) combination would build more than
    max_steps layers. Carries the actual count so the caller can offer a
    capped/truncated alternative instead of silently building hundreds of
    layers.
    """

    def __init__(self, count: int, max_steps: int):
        self.count = count
        self.max_steps = max_steps
        super().__init__(
            f"Requested range would build {count} layers, "
            f"exceeding the cap of {max_steps}."
        )


def generate_time_steps(
    start: datetime,
    end: datetime,
    step: timedelta,
    max_steps: int = DEFAULT_MAX_STEPS,
    truncate: bool = False,
) -> List[str]:
    """Return ISO-8601 'Z' timestamp strings from start to end inclusive.

    Parameters
    ----------
    start, end : datetime
        Inclusive range. If end < start, returns an empty list.
    step : timedelta
        Interval between generated steps. Must be positive.
    max_steps : int
        Safety cap on how many layers a single "Build layers" click can
        create.
    truncate : bool
        If False (default) and the un-truncated count would exceed
        max_steps, raises TooManyStepsError so the caller can ask the user
        whether to proceed with a capped subset. If True, silently returns
        only the first max_steps timestamps.

    Raises
    ------
    ValueError
        If step is not positive.
    TooManyStepsError
        If truncate is False and the range would exceed max_steps.
    """
    if step.total_seconds() <= 0:
        raise ValueError("step must be a positive timedelta")

    if end < start:
        return []

    total_seconds = (end - start).total_seconds()
    count = int(total_seconds // step.total_seconds()) + 1

    if count > max_steps and not truncate:
        raise TooManyStepsError(count, max_steps)

    count = min(count, max_steps)

    steps = []
    current = start
    for _ in range(count):
        steps.append(_to_iso_z(current))
        current += step
    return steps


def _to_iso_z(dt: datetime) -> str:
    """Format a (possibly naive) datetime as an ISO-8601 'Z' string."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_from_parts(year: str, month: str, day: str, time: str = "00:00") -> str:
    """Build an ISO-8601 'Z' timestamp from CDS form parts.

    The Browse-by-Variable tab collects year/month/day (and optionally a
    "HH:MM" time) as the string values CDS uses. This assembles them into
    the single timestamp the WMTS preview needs.

    Parameters
    ----------
    year, month, day : str
        Zero-padded CDS values, e.g. "2024", "07", "01".
    time : str
        "HH:MM" (default "00:00"). A bare "HH" is also accepted.

    Raises
    ------
    ValueError
        If the parts do not form a real calendar date/time.
    """
    time = (time or "00:00").strip()
    if ":" not in time:
        time = f"{time}:00"
    dt = datetime.strptime(f"{year}-{month}-{day} {time}", "%Y-%m-%d %H:%M")
    return _to_iso_z(dt)


# ---------------------------------------------------------------------------
# Mapping a dataset's raw time_step (e.g. "1h", "day", "P1D") to the closest
# entry in STEP_CHOICES, so the dialog can preselect a sensible default.
# ---------------------------------------------------------------------------

_UNIT_SECONDS = {
    "h": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
}


def _parse_time_step_seconds(raw: str) -> int:
    """Best-effort parse of a dataset's time_step string into seconds.

    Handles forms like "1h", "3 hours", "day", "P1D" (ISO-8601 duration,
    day/hour components only — the only ones GeoBridge's snapshots use).
    Falls back to 1 hour if the string can't be parsed.
    """
    if not raw:
        return _UNIT_SECONDS["hour"]
    raw = raw.strip().lower()

    iso_match = re.fullmatch(r"p(?:(\d+)d)?(?:t(?:(\d+)h)?)?", raw)
    if iso_match and (iso_match.group(1) or iso_match.group(2)):
        days = int(iso_match.group(1) or 0)
        hours = int(iso_match.group(2) or 0)
        return days * 86400 + hours * 3600

    match = re.fullmatch(r"(\d+)\s*([a-z]+)", raw)
    if match:
        n, unit = match.groups()
        unit_seconds = _UNIT_SECONDS.get(unit)
        if unit_seconds:
            return int(n) * unit_seconds

    unit_seconds = _UNIT_SECONDS.get(raw)
    if unit_seconds:
        return unit_seconds

    return _UNIT_SECONDS["hour"]


def default_step_choice(raw_time_step: str) -> str:
    """Return the STEP_CHOICES label closest to a dataset's raw time_step."""
    target_seconds = _parse_time_step_seconds(raw_time_step)
    best_label = next(iter(STEP_CHOICES))
    best_diff = None
    for label, delta in STEP_CHOICES.items():
        diff = abs(delta.total_seconds() - target_seconds)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_label = label
    return best_label
