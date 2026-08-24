GeoBridge QGIS Plugin
======================

GeoBridge is a QGIS 3 plugin that wraps the `geobridge
<https://github.com/ECMWFCode4Earth/GeoBridge>`_ Python library: discover,
semantically search, and preview Copernicus climate data (ERA5, CAMS, CEMS)
as WMTS layers directly in QGIS, and export selections to GeoTIFF.

This plugin is a thin wrapper — all discovery, semantic search, and WMTS/
GeoTIFF logic lives in the ``geobridge`` library; nothing is reimplemented
here.

Plugin source: https://github.com/ECMWFCode4Earth/geobridge-qgis

.. toctree::
   :maxdepth: 2
   :caption: Contents

   installation
   api_key_tab
   search_tab
   browse_tab
   time_series_tab
