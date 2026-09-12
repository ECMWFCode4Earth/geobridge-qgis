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

Installing QGIS on Linux (Ubuntu) with a matching GDAL version
-------------------------------------------------------------------

On Windows and macOS, installing a recent QGIS is usually enough on its
own — the official installer bundles a matching GDAL. On Linux this can
take a couple of extra steps, because QGIS and GDAL can end up coming
from two different, mismatched package sources: QGIS reports one
version, but silently keeps using an older GDAL underneath, and nothing
about a plain package-manager check reveals that (confirmed in practice
on Ubuntu 24.04). Following the steps below in order avoids that.

**Step 1 — Open a terminal**

Press :kbd:`Ctrl+Alt+T`, or search for "Terminal" in your applications menu.

**Step 2 — Add the UbuntuGIS repository**

This is a trusted, widely-used software source that keeps QGIS and GDAL
versions matched to each other.

.. code-block:: bash

   sudo add-apt-repository ppa:ubuntugis/ubuntugis-unstable

Type your password when asked (nothing shows on screen as you type —
that's normal), and press Enter to continue if prompted.

**Step 3 — Refresh the list of available software**

.. code-block:: bash

   sudo apt update

**Step 4 — Check which version will actually be installed**

Don't assume — confirm it before committing:

.. code-block:: bash

   apt-cache policy qgis

Look at the ``Candidate:`` line — that's the exact version you'll get,
and the line below it shows which repository it's coming from.

**Step 5 — Install QGIS**

.. code-block:: bash

   sudo apt install qgis

Type ``y`` and press Enter if asked to confirm.

**Step 6 — Confirm it actually works**

Package versions can lie about what QGIS is really using, so check the
real thing:

1. Open QGIS, go to **Plugins → Python Console**, and run:

   .. code-block:: python

      from osgeo import gdal
      print(gdal.__version__)

   Only trust this check from *inside* QGIS — a terminal ``python3``
   check can show a different, wrong answer.
2. If it's below the version this plugin needs (GDAL 3.9), get proof of
   what QGIS is really linked to:

   .. code-block:: bash

      ldd $(which qgis) | grep -i gdal

3. Finally, actually run the plugin feature that matters to you (e.g.
   Export to GeoTIFF on a Zarr V3 dataset — see :doc:`arco_zarr`). A
   matching version number is a good sign, but running it for real is
   the only true confirmation.

If QGIS is already installed and something isn't working
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Instead of Step 5, do this:

.. code-block:: bash

   sudo apt remove --purge qgis qgis-common qgis-plugin-grass qgis-provider-grass qgis-providers python3-qgis
   sudo apt autoremove --purge
   sudo apt install qgis

Then repeat Step 6 to confirm.

For other distros (Fedora, openSUSE, Arch, …) or Flatpak/conda installs,
package names and repo setup differ enough that there's no single
command to give here — see `qgis.org's own installation guide
<https://qgis.org/resources/installation-guide/>`_ for your platform,
then check the GDAL version the same way (Step 6 above).

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

Troubleshooting
----------------

.. _zarr-v3-old-gdal:

"Variable not found in Zarr store" / "Your QGIS/GDAL is likely too old"
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You'll see this on the Search tab's "Export to GeoTIFF" or the Time
Series tab, worded roughly like:

    Export failed: Variable 't2m' not found in Zarr store (Array t2m
    does not exist). Your QGIS/GDAL is likely too old to read this
    dataset's Zarr V3 format.

On the Search tab, that's followed by a clickable link straight back
to this page. The underlying cause either way is the same — an empty
list of what GDAL *did* find is the tell: it opened the store's
connection fine but couldn't enumerate anything inside it at all, which
in practice has meant an older bundled GDAL that doesn't understand
this store's newer **Zarr V3** metadata yet. Nothing is wrong with the
dataset, your request, or this plugin — the fix is updating QGIS (which
brings a newer GDAL with it):

* **Windows/macOS:** install a recent QGIS — roughly **3.38+** bundles
  GDAL 3.9+. See the Requirements note above for how to check the
  exact GDAL version from QGIS's own Python Console.
* **Linux:** the QGIS version number alone isn't reliable evidence of
  the GDAL version underneath it — see
  `Installing QGIS on Linux (Ubuntu) with a matching GDAL version`_
  above, in particular **Step 6**, which is the same check to run here.

This only affects the handful of datasets stored in Zarr V3 — search,
WMTS preview, Browse by Variable, and CDS downloads all work regardless
of GDAL version.

Only part of an error message is visible
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The on-screen status line after a failed export/download is a small,
fixed-size box, so a long error can run past what it can show. The
full error — including a full stack trace — is always written to
**View → Panels → Log Messages**, under the **GeoBridge** tab, which is
worth checking any time an error looks cut off or you need the exact
detail to report an issue.
