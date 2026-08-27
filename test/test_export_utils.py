# -*- coding: utf-8 -*-
"""Plain-Python tests for export_utils.py — no QGIS required, run with pytest."""

from export_utils import (
    AGGREGATION_LABELS,
    default_output_filename,
    raw_aggregation_span_warning,
)


def test_aggregation_labels_map_to_geobridge_strings():
    assert AGGREGATION_LABELS["Raw (all timesteps as bands)"] == "raw"
    assert AGGREGATION_LABELS["Daily mean"] == "daily_mean"
    assert AGGREGATION_LABELS["Daily max"] == "daily_max"
    assert AGGREGATION_LABELS["Daily min"] == "daily_min"
    assert AGGREGATION_LABELS["Monthly mean"] == "monthly_mean"
    assert AGGREGATION_LABELS["Monthly max"] == "monthly_max"
    assert AGGREGATION_LABELS["Monthly min"] == "monthly_min"
    assert AGGREGATION_LABELS["Annual mean"] == "annual_mean"
    assert AGGREGATION_LABELS["Annual max"] == "annual_max"
    assert "Annual min" not in AGGREGATION_LABELS  # geobridge has no such option


def test_default_output_filename():
    assert (
        default_output_filename("reanalysis_era5_single_levels", "t2m", "raw")
        == "reanalysis_era5_single_levels_t2m_raw.tif"
    )


def test_default_output_filename_sanitises_slashes_in_dataset_id():
    assert (
        default_output_filename("reanalysis-era5-single-levels/sfc", "t2m", "daily_mean")
        == "reanalysis-era5-single-levels_sfc_t2m_daily_mean.tif"
    )


def test_raw_aggregation_span_warning_none_for_short_range():
    assert raw_aggregation_span_warning(3.0) is None
    assert raw_aggregation_span_warning(31.0) is None


def test_raw_aggregation_span_warning_present_for_long_range():
    warning = raw_aggregation_span_warning(90.0)
    assert warning is not None
    assert "90" in warning


def test_raw_aggregation_span_warning_respects_custom_threshold():
    assert raw_aggregation_span_warning(10.0, threshold_days=5.0) is not None
    assert raw_aggregation_span_warning(3.0, threshold_days=5.0) is None
