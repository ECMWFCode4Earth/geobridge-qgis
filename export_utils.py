# -*- coding: utf-8 -*-
"""
export_utils
~~~~~~~~~~~~

Pure logic for the "Export to GeoTIFF" panel. Zero Qt/qgis imports,
unit-testable without QGIS.
"""

from __future__ import annotations

from typing import Optional

# Display label -> geobridge aggregation string (see
# geobridge/modules/extract.py's zarr_to_geotiff `aggregation` parameter).
AGGREGATION_LABELS = {
    "Raw (all timesteps as bands)": "raw",
    "Daily mean": "daily_mean",
    "Monthly mean": "monthly_mean",
}

# "Raw" keeps every timestep as a separate band in one file. Past this many
# days in the selected range, that's likely to produce an impractically
# large multi-band GeoTIFF — worth a confirmation prompt, not a hard block.
RAW_AGGREGATION_WARNING_DAYS = 31.0


def default_output_filename(dataset_id: str, variable: str, aggregation: str) -> str:
    """Suggested filename for the Save dialog — mirrors geobridge's own
    default (`{dataset}_{variable}_{aggregation}.tif`, see
    geobridge/modules/extract.py's zarr_to_geotiff)."""
    safe_dataset = dataset_id.replace("/", "_")
    return f"{safe_dataset}_{variable}_{aggregation}.tif"


def raw_aggregation_span_warning(
    span_days: float, threshold_days: float = RAW_AGGREGATION_WARNING_DAYS
) -> Optional[str]:
    """Return a confirmation prompt if a 'raw' export's time range is long
    enough to likely produce a very large multi-band file, else None."""
    if span_days > threshold_days:
        return (
            f"With 'Raw' aggregation, a {span_days:.0f}-day range may produce a very "
            f"large multi-band file. Continue anyway?"
        )
    return None
