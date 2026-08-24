# -*- coding: utf-8 -*-
"""
aoi_utils
~~~~~~~~~

Pure logic for the "Area of interest" control — bbox formatting and
size validation. Zero Qt/qgis imports, unit-testable without QGIS.

A bbox is always (west, south, east, north) in WGS-84 degrees, matching
geobridge's own bbox convention (see geobridge/modules/extract.py).
"""

from __future__ import annotations

from typing import Optional, Tuple

BBox = Tuple[float, float, float, float]

DEFAULT_MIN_SIDE_DEG = 0.1
DEFAULT_MAX_SIDE_DEG = 50.0


def format_bbox(bbox: BBox) -> str:
    """Human-readable bbox text for display in the UI."""
    west, south, east, north = bbox
    return f"West: {west:.4f}   South: {south:.4f}   East: {east:.4f}   North: {north:.4f}"


def bbox_side_lengths(bbox: BBox) -> Tuple[float, float]:
    """Return (width, height) in degrees. May be negative/zero for a
    degenerate bbox — callers should check validate_bbox_size first."""
    west, south, east, north = bbox
    return (east - west, north - south)


def validate_bbox_size(
    bbox: BBox,
    min_side_deg: float = DEFAULT_MIN_SIDE_DEG,
    max_side_deg: float = DEFAULT_MAX_SIDE_DEG,
) -> Optional[str]:
    """Return a warning message if bbox is degenerate/too small/too large,
    else None if it's within acceptable bounds.

    Parameters
    ----------
    bbox : (west, south, east, north)
    min_side_deg : float
        Warn if either side is narrower than this (default 0.1 degrees —
        roughly one CAMS-Europe grid cell; likely a misclick/misdraw).
    max_side_deg : float
        Warn if either side is wider than this (default 50 degrees —
        roughly a large country/small continent; likely to make an
        extraction impractically slow or large).
    """
    width, height = bbox_side_lengths(bbox)

    if width <= 0 or height <= 0:
        return "Invalid area: east/north must be greater than west/south."

    if width < min_side_deg or height < min_side_deg:
        return (
            f"This area is very small ({width:.3f}° × {height:.3f}°) — "
            f"smaller than {min_side_deg}° on a side. Results may cover only a "
            f"single grid cell."
        )

    if width > max_side_deg or height > max_side_deg:
        return (
            f"This area is very large ({width:.1f}° × {height:.1f}°) — "
            f"larger than {max_side_deg}° on a side. An export over this area "
            f"may be slow or produce a very large file."
        )

    return None
