# -*- coding: utf-8 -*-
"""Plain-Python tests for aoi_utils.py — no QGIS required, run with pytest."""

from aoi_utils import bbox_side_lengths, format_bbox, validate_bbox_size


def test_format_bbox():
    text = format_bbox((23.5, 37.8, 24.1, 38.1))
    assert "West: 23.5000" in text
    assert "South: 37.8000" in text
    assert "East: 24.1000" in text
    assert "North: 38.1000" in text


def test_bbox_side_lengths():
    assert bbox_side_lengths((0.0, 0.0, 2.0, 3.0)) == (2.0, 3.0)


def test_validate_bbox_size_ok_for_reasonable_area():
    # Athens-ish bbox, a few tenths of a degree wide
    assert validate_bbox_size((23.5, 37.8, 24.1, 38.1)) is None


def test_validate_bbox_size_degenerate_returns_warning():
    assert validate_bbox_size((24.1, 38.1, 23.5, 37.8)) is not None
    assert validate_bbox_size((10.0, 10.0, 10.0, 10.0)) is not None


def test_validate_bbox_size_too_small_returns_warning():
    warning = validate_bbox_size((23.5, 37.8, 23.55, 37.85), min_side_deg=0.1)
    assert warning is not None
    assert "very small" in warning


def test_validate_bbox_size_too_large_returns_warning():
    warning = validate_bbox_size((-20.0, -20.0, 60.0, 60.0), max_side_deg=50.0)
    assert warning is not None
    assert "very large" in warning


def test_validate_bbox_size_respects_custom_limits():
    bbox = (0.0, 0.0, 0.5, 0.5)
    assert validate_bbox_size(bbox, min_side_deg=0.1, max_side_deg=50.0) is None
    assert validate_bbox_size(bbox, min_side_deg=1.0, max_side_deg=50.0) is not None
