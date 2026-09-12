# -*- coding: utf-8 -*-
"""
gdal_native
~~~~~~~~~~~

Self-contained replacement for the `geobridge` PyPI package, built only on
GDAL (already bundled with QGIS) + `requests` + the Python standard library.

No `geobridge`, `xarray`, `zarr`, `dask`, `rioxarray`, `pyproj`, or
`netcdf4` anywhere in this package — see CLAUDE.md / the gdal-native branch
history for why: those pulled in a version of `pyproj` that segfaulted
QGIS (Windows access violation inside pyproj's compiled `_CRS.__init__`,
called from a background QgsTask thread) and repeatedly fought QGIS's own
bundled numpy/pandas/pyarrow over ABI-compatible versions. GDAL's own
drivers (confirmed: NetCDF, GRIB, WMTS, and Zarr, all multidim-capable) and
CRS handling are already what QGIS itself relies on everywhere else in the
same process, so this package uses those instead of a second, separately
pip-installed geospatial stack.

Modules
-------
- ``catalog_data`` (a data directory, not a module) — JSON snapshots
  converted once from geobridge's own bundled YAML catalogue
  (arco_snapshot.yaml, cds_snapshot.yaml, arco_overrides.yaml,
  vocabulary.yaml as of the geobridge 0.1.13/0.1.14 source), so this
  package needs no YAML parser at runtime.
- ``discover`` — dataset/variable catalogue lookup (LayerDescriptor),
  ported from geobridge/modules/discover.py with yaml.safe_load swapped
  for json.load — same field logic, same behaviour.
- ``catalog_search`` — TF-IDF + cosine-similarity free-text search,
  ported near-verbatim from geobridge/semantic/catalog.py (already pure
  stdlib there — math/re/collections.Counter — only the loader changed).
- ``wmts`` — WMTS XYZ tile URL construction for QGIS's raster layer,
  ported from geobridge/modules/wmts.py (already pure stdlib there too).
"""
