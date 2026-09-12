Installation
============

Requirements
------------

* QGIS 3.28 or newer.
* **GDAL 3.9 or newer**, for full functionality (bundled with roughly
  **QGIS 3.38+** on Windows — on Linux this varies by distro/package
  source rather than tracking the QGIS version number, so check directly:
  in QGIS's own **Plugins → Python Console**, run
  ``from osgeo import gdal; print(gdal.__version__)``).
* A free Copernicus Climate Data Store (CDS) account, for authenticated
  features — register at `cds.climate.copernicus.eu
  <https://cds.climate.copernicus.eu>`_.

.. note::
   The GDAL version only matters for a handful of datasets stored in the
   newer **Zarr V3** format. On an older GDAL, those specific datasets
   fail with an error like *"Variable '<name>' not found in Zarr store"*
   even though nothing is actually wrong with the dataset or your
   request — everything else in the plugin (search, WMTS preview,
   Browse by Variable, CDS downloads) works regardless of GDAL version.
   If you hit that error, updating QGIS/GDAL is the fix, not anything in
   this plugin.

Updating QGIS/GDAL on Linux
------------------------------

On Windows and macOS, installing a recent QGIS is usually enough on its
own — the official installer bundles a matching GDAL. On Linux this can
need two separate steps, because your distro's own package repos are
often frozen at an older GDAL than what the *official QGIS* repo expects
(confirmed in practice: Ubuntu 24.04's own repos ship GDAL 3.8.4, below
the 3.9 threshold, even after upgrading QGIS itself to a current
version — QGIS just links against whatever GDAL the system already has).

**1. Remove any existing QGIS** installed from your distro's default repo:

.. code-block:: bash

   sudo apt remove --purge qgis qgis-common qgis-plugin-grass
   sudo apt autoremove

**2. Install QGIS from the official repo** (this example targets the LTR
line — use ``https://qgis.org/ubuntu`` instead of ``ubuntu-ltr`` for the
latest release rather than LTR):

.. code-block:: bash

   sudo apt install gnupg software-properties-common
   wget -qO - https://download.qgis.org/downloads/qgis-archive-keyring.gpg \
     | sudo tee /etc/apt/trusted.gpg.d/qgis-archive-keyring.gpg
   sudo add-apt-repository "deb https://qgis.org/ubuntu-ltr $(lsb_release -cs) main"
   sudo apt update
   sudo apt install qgis qgis-plugin-grass

**3. If GDAL is still below 3.9** after that (check with the command
below) — the official QGIS repo provides QGIS itself but not a newer
GDAL, so add the `UbuntuGIS <https://launchpad.net/~ubuntugis>`_ PPA,
the standard source most QGIS-on-Ubuntu users rely on for a current
GDAL (despite the name, ``ubuntugis-unstable`` is the normal, well-
established pairing with recent QGIS, not something fragile):

.. code-block:: bash

   sudo add-apt-repository ppa:ubuntugis/ubuntugis-unstable
   sudo apt update
   sudo apt upgrade

**4. Verify** — both should report 3.9 or newer; the second one (run
inside QGIS itself) is the one that actually determines whether the
plugin works, since a system package version and what QGIS's own Python
actually loads can differ:

.. code-block:: bash

   dpkg -l | grep -i gdal

.. code-block:: python

   # QGIS → Plugins → Python Console
   from osgeo import gdal
   print(gdal.__version__)

For other distros (Fedora, openSUSE, Arch, …) or Flatpak/conda installs,
package names and repo setup differ enough that there's no single
command to give here — see `qgis.org's own installation guide
<https://qgis.org/resources/installation-guide/>`_ for your platform,
then check the GDAL version the same way (step 4 above).

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

No extra Python packages to install
--------------------------------------

Unlike some earlier releases, this plugin has **zero Python dependencies
of its own** to install — discovery, search, WMTS preview, export, and
time series all run on QGIS's own bundled GDAL directly. There is no
"Install dependencies" step; open the plugin (toolbar icon or
**Plugins → GeoBridge → GeoBridge**) and it's ready to use.

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
