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
from typing import List, Optional

# Ordered so the UI combo box lists them from finest to coarsest granularity.
# "1 month"/"1 year" are fixed 30-/365-day intervals rather than true
# calendar months/years (timedelta has no calendar concept) — close enough
# for a UI step size, and their total_seconds() are what let
# exact_step_choice() recognize a genuinely monthly/yearly dataset's native
# resolution (several ARCO datasets report time_step="month" or "year";
# without an entry here for it to match, those could never get a raw
# match at all).
STEP_CHOICES = OrderedDict(
    [
        ("1 hour", timedelta(hours=1)),
        ("3 hours", timedelta(hours=3)),
        ("6 hours", timedelta(hours=6)),
        ("1 day", timedelta(days=1)),
        ("1 week", timedelta(days=7)),
        ("1 month", timedelta(days=30)),
        ("1 year", timedelta(days=365)),
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
    # Approximate — only used for picking/matching a STEP_CHOICES entry,
    # never for actual date arithmetic (see generate_time_steps).
    "month": 30 * 86400,
    "months": 30 * 86400,
    "year": 365 * 86400,
    "years": 365 * 86400,
}

# Raw time_step values seen in geobridge's catalogue with no fixed periodic
# cadence at all (as opposed to one _UNIT_SECONDS just doesn't recognize
# yet) — never treat these as any specific interval.
_NONPERIODIC = {"nonperiodic", "irregular", "variable"}


def _parse_time_step_seconds(raw: str) -> Optional[int]:
    """Best-effort parse of a dataset's time_step string into seconds, or
    None if it can't be parsed (including explicitly non-periodic datasets).

    Handles forms like "1h", "3 hours", "3-hour", "day", "month",
    "P1D" (ISO-8601 duration, day/hour components only — the only ones
    GeoBridge's snapshots use).

    IMPORTANT: a failed parse must return None here, not a guessed
    fallback value — a caller (originally default_step_choice, before
    exact_step_choice existed) silently defaulting an unrecognized string
    to "1 hour" is exactly what let unsupported units like "month" or
    "3-hour" (hyphenated forms didn't match the old regex) get
    misidentified as an *exact* match to the "1 hour" STEP_CHOICES entry,
    flagging monthly/3-hourly datasets as "1 hour (raw)" in the UI.
    """
    if not raw:
        return None
    raw = raw.strip().lower()
    if raw in _NONPERIODIC:
        return None

    iso_match = re.fullmatch(r"p(?:(\d+)d)?(?:t(?:(\d+)h)?)?", raw)
    if iso_match and (iso_match.group(1) or iso_match.group(2)):
        days = int(iso_match.group(1) or 0)
        hours = int(iso_match.group(2) or 0)
        return days * 86400 + hours * 3600

    # Allow a hyphen or whitespace (or nothing) between the number and unit
    # — geobridge's snapshots use both "3 hours" and "3-hour" forms.
    match = re.fullmatch(r"(\d+)[\s-]*([a-z]+)", raw)
    if match:
        n, unit = match.groups()
        unit_seconds = _UNIT_SECONDS.get(unit)
        if unit_seconds:
            return int(n) * unit_seconds
        return None

    return _UNIT_SECONDS.get(raw)


def default_step_choice(raw_time_step: str) -> str:
    """Return the STEP_CHOICES label closest to a dataset's raw time_step,
    or the finest (first) choice if the raw value is unparseable/unknown —
    picking *something* reasonable to preselect, same as before, just no
    longer via a fake "as if it were 1 hour" seconds value feeding into the
    closest-match comparison below."""
    target_seconds = _parse_time_step_seconds(raw_time_step)
    if target_seconds is None:
        return next(iter(STEP_CHOICES))
    best_label = next(iter(STEP_CHOICES))
    best_diff = None
    for label, delta in STEP_CHOICES.items():
        diff = abs(delta.total_seconds() - target_seconds)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_label = label
    return best_label


def finer_than_native_steps(raw_time_step: str) -> set:
    """Return the STEP_CHOICES labels strictly finer (shorter interval)
    than a dataset's native time_step — e.g. native "month" ->
    {"1 hour", "3 hours", "6 hours", "1 day", "1 week"}. There is no real
    data at those intervals, so a caller can use this to grey them out
    rather than let a user pick a granularity the dataset simply doesn't
    have.

    Returns an empty set (never disable anything) when the native step
    can't be parsed, and also when *every* choice would come out finer
    (a native step coarser than the coarsest offered choice, "1 year") —
    that would leave nothing selectable, which is worse than not
    filtering at all.
    """
    target_seconds = _parse_time_step_seconds(raw_time_step)
    if target_seconds is None:
        return set()
    finer = {
        label for label, delta in STEP_CHOICES.items()
        if delta.total_seconds() < target_seconds
    }
    if len(finer) >= len(STEP_CHOICES):
        return set()
    return finer


def native_step_seconds(raw_time_step: str) -> Optional[int]:
    """Public wrapper around the module's raw-time_step parser — a
    dataset's native time_step converted to seconds, or None if it can't
    be parsed. Lets callers compare against granularities that aren't
    themselves STEP_CHOICES entries (e.g. export_utils's aggregation
    keys), without duplicating the parsing regexes."""
    return _parse_time_step_seconds(raw_time_step)


def monthly_alignment_warning(raw_time_step: str, start: datetime) -> Optional[str]:
    """Warn when `start` looks misaligned with a monthly/yearly-native
    dataset's actual available timestamps.

    Monthly/yearly composite layers (e.g. WMTS "monthly-mean" products) are
    published on fixed calendar dates — normally the 1st of the month (or
    year). generate_time_steps() has no concept of calendar months (its
    "1 month"/"1 year" STEP_CHOICES entries are fixed 30-/365-day
    timedeltas — see the module docstring), so nothing else stops a user
    from picking an arbitrary start day. A start that doesn't land on the
    1st then asks the WMTS server for a TIME the dataset simply has no
    tile for at all, which fails identically for every tile in the
    request (not a random subset) after retries — the "max retry" /
    "repeat tileRequest" warnings this is meant to head off.

    Returns None when raw_time_step isn't monthly/yearly, or start is
    already aligned.
    """
    raw = (raw_time_step or "").strip().lower()
    if raw in ("month", "months"):
        if start.day != 1:
            return (
                f"This dataset's native resolution is monthly — timestamps are "
                f"normally dated the 1st of each month. Your start date "
                f"({start:%Y-%m-%d}) isn't the 1st, so the server may have no "
                f"tile at that exact time and every tile request could fail. "
                f"Consider starting from {start.replace(day=1):%Y-%m-%d} instead."
            )
    elif raw in ("year", "years"):
        if not (start.month == 1 and start.day == 1):
            return (
                f"This dataset's native resolution is yearly — timestamps are "
                f"normally dated January 1st. Your start date ({start:%Y-%m-%d}) "
                f"isn't Jan 1st, so the server may have no tile at that exact "
                f"time and every tile request could fail. Consider starting "
                f"from {start.year}-01-01 instead."
            )
    return None


def exact_step_choice(raw_time_step: str) -> Optional[str]:
    """Return the STEP_CHOICES label whose interval exactly matches a
    dataset's raw time_step (within 1 second, to absorb float rounding), or
    None if no entry matches exactly.

    Unlike default_step_choice() (always returns the *closest* entry, even
    when that means resampling), this tells the caller whether the
    dataset's native resolution is actually one of the offered choices —
    e.g. a dataset natively at 12-hour resolution has no exact match among
    1h/3h/6h/1day/1week, so every choice for it involves resampling.
    """
    target_seconds = _parse_time_step_seconds(raw_time_step)
    if target_seconds is None:
        return None
    for label, delta in STEP_CHOICES.items():
        if abs(delta.total_seconds() - target_seconds) < 1:
            return label
    return None
