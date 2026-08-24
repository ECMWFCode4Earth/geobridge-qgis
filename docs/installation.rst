Installation
============

Requirements
------------

* QGIS 3.28 or newer.
* A free Copernicus Climate Data Store (CDS) account, for authenticated
  features — register at `cds.climate.copernicus.eu
  <https://cds.climate.copernicus.eu>`_.

Installing the plugin
----------------------

From the QGIS Plugin Repository
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

#. In QGIS, open **Plugins → Manage and Install Plugins**.
#. Search for **GeoBridge**.
#. Click **Install Plugin**.

From a ZIP file
^^^^^^^^^^^^^^^^

#. Download the release ZIP from the `plugin repository
   <https://plugins.qgis.org/plugins/GeoBridge_Plugin/>`_ or the
   `GitHub repository <https://github.com/ECMWFCode4Earth/geobridge-qgis>`_.
#. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
#. Select the downloaded ZIP and click **Install Plugin**.

Installing the ``geobridge`` library
--------------------------------------

The plugin itself has almost no Python dependencies of its own — the actual
Copernicus discovery, search, and export logic lives in the separate
``geobridge`` PyPI package, which is **not** bundled with the plugin.

After installing the plugin:

#. Open it (toolbar icon or **Plugins → GeoBridge → GeoBridge**).
#. On the **API Key** tab, click **Install dependencies**.
#. This runs ``pip install`` against the exact Python interpreter QGIS is
   using — not your system Python — so it always lands in the right place.
   It installs the ``geobridge`` core package plus its optional
   ``[zarr]`` extra (needed for GeoTIFF export and full-history time
   series).
#. Once the button shows **"✓ geobridge installed (…)"**, reload the
   plugin (via the `Plugin Reloader
   <https://plugins.qgis.org/plugins/plugin_reloader/>`_ plugin, or a full
   QGIS restart) before using export features.

Adding your CDS API key
-------------------------

Authentication is only required for downloads and full-history time series
— free-text search and quick WMTS previews work without it.

#. Register at `cds.climate.copernicus.eu
   <https://cds.climate.copernicus.eu>`_ and copy your personal access
   token from your profile page.
#. Paste it into the **Copernicus CDS API key** field on the API Key tab
   and click **Save**.

.. figure:: _static/screenshots/api_key_tab.png
   :alt: The API Key tab, showing geobridge installed and an authenticated key
   :width: 500px

   The API Key tab once ``geobridge`` is installed and a key is saved.
