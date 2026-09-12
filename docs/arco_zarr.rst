ARCO Zarr datasets
====================

Two features in this plugin only work for datasets backed by an **ARCO Zarr**
archive — a cloud-optimized, "analysis ready" copy of the dataset that this
plugin can read directly with GDAL's Zarr driver over HTTPS, one exact
region/time slice at a time, instead of having to submit a job to the CDS
API queue and download the whole result:

* :doc:`search_tab`'s **Export to GeoTIFF**
* :doc:`time_series_tab` (point time series) as a whole

Every other feature — free-text search, the WMTS preview/time slider, and
:doc:`browse_tab`'s cascading picker/CDS download — works for *any* dataset
in the catalogue, ARCO-backed or not.

Not every dataset has an ARCO Zarr archive. When the currently selected
dataset doesn't, **Export to GeoTIFF** is disabled and **Pick point on
map** on the Time Series tab is disabled, both with an explanation,
rather than failing after you click them.

Datasets with an ARCO Zarr archive
-------------------------------------

As of this plugin's bundled catalogue snapshot:

.. list-table::
   :header-rows: 1
   :widths: 55 25 20

   * - Dataset
     - Dataset ID
     - Notes
   * - Agrometeorological indicators from 1979 to present derived from reanalysis
     - ``sis_agrometeorological_indicators``
     -
   * - CAMS global reanalysis (EAC4)
     - ``cams_global_reanalysis_eac4``
     -
   * - CAMS global reanalysis (EAC4) monthly averaged fields
     - ``cams_global_reanalysis_eac4_monthly``
     -
   * - CAMS: European air quality forecasts
     - ``cams_europe_air_quality_forecasts``
     -
   * - CAMS: European air quality reanalyses
     - ``cams_europe_air_quality_reanalyses``
     -
   * - CAMS: Global atmospheric composition forecast
     - ``cams_global_atmospheric_composition_forecasts``
     -
   * - CERRA sub-daily regional reanalysis data for Europe on single levels from 1984 to present
     - ``reanalysis_cerra_single_levels``
     -
   * - CERRA-Land sub-daily regional reanalysis data for Europe from 1984 to present
     - ``reanalysis_cerra_land``
     -
   * - CORDEX regional climate model data on single levels
     - ``projections_cordex_domains_single_levels``
     -
   * - Climate and energy related variables from the Pan-European Climate Database derived from reanalysis and climate projections
     - ``sis_energy_pecd``
     -
   * - Cloud properties global gridded monthly and daily data from 1979 to present derived from satellite observations
     - ``satellite_cloud_properties``
     -
   * - ERA5 hourly data on pressure levels from 1940 to present
     - ``reanalysis_era5_pressure_levels``
     -
   * - ERA5 hourly data on single levels from 1940 to present
     - ``reanalysis_era5_single_levels``
     -
   * - ERA5-Land hourly data from 1950 to present
     - ``reanalysis_era5_land``
     -
   * - Earth's radiation budget from 1979 to present derived from satellite observations
     - ``satellite_earth_radiation_budget``
     -
   * - Fire danger indices historical data from the Copernicus Emergency Management Service
     - ``cems_fire_historical_v1``
     -
   * - Global sea surface temperature derived from satellite observations
     - ``satellite_sea_surface_temperature``
     -
   * - Lake water levels from 1992 to present derived from satellite observations
     - ``satellite_lake_water_level``
     - Partial — its two subsets are catalogued without a Zarr archive.
   * - Land cover classification gridded maps from 1992 to present derived from satellite observations
     - ``satellite_land_cover``
     -
   * - Leaf area index and fraction absorbed of photosynthetically active radiation 10-daily gridded data from 1981 to present
     - ``satellite_lai_fapar``
     -
   * - ORAS5 global ocean reanalysis monthly data from 1958 to present
     - ``reanalysis_oras5``
     -
   * - Ocean colour daily data from 1997 to present derived from satellite observations
     - ``satellite_ocean_colour``
     -
   * - Precipitation monthly and daily gridded data from 1979 to present derived from satellite measurements
     - ``satellite_precipitation``
     -
   * - Seasonal forecast monthly statistics on single levels
     - ``seasonal_monthly_single_levels``
     -
   * - Soil moisture gridded data from 1978 to present
     - ``satellite_soil_moisture``
     -
   * - Surface albedo 10-daily gridded data from 1981 to present
     - ``satellite_albedo``
     -
   * - Thermal comfort indices derived from ERA5 reanalysis
     - ``derived_utci_historical``
     -

.. note::
   A dataset appearing on this list doesn't guarantee *every* variable on it
   has ARCO backing (see ``satellite_lake_water_level`` above). In-app, the
   enabled/disabled state of **Export to GeoTIFF** / **Full history** for
   your specific dataset+variable selection is always the source of truth —
   this table is a general reference, not a per-variable guarantee.

   Datasets *not* on this list (CDS-API-only, reached via
   :doc:`browse_tab`'s cascading picker) can still be searched, previewed as
   WMTS, and downloaded via the CDS API job queue — they just can't use
   **Export to GeoTIFF** or **Full history**.
