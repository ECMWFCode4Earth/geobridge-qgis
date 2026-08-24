Time Series Tab
=================

.. figure:: _static/screenshots/time_series_tab.png
   :alt: The Time Series tab, showing a point-clicked time series plotted as a line chart
   :width: 500px

Fetches how a single point's value changes over time, for the
dataset/variable currently selected on the :doc:`search_tab`.

Picking a point
------------------

Click **Pick point on map**, then click anywhere on the QGIS map canvas.
The clicked point's longitude/latitude are shown next to the button.
Clicking a new point while a fetch is still running cancels it and starts
a fresh one for the new point.

Method
--------

* **Quick (no auth)** — one WMTS ``GetFeatureInfo`` request per time step.
  No authentication or extra installation needed; best for exploratory
  ranges up to a few dozen/hundred steps.
* **Full history (Zarr, needs auth)** — one bulk read off the ARCO Zarr
  archive. Needs the ``geobridge[zarr]`` extra installed and an
  authenticated CDS API key (see :doc:`api_key_tab`); best for long or
  dense ranges.

Range and results
--------------------

Set **Start**, **End**, and a **Step** (daily/weekly/monthly), then click
**Refresh** to (re-)fetch. The status line reports how many points came
back, and the chart plots value against time — a gap in the data (a
``None`` value) breaks the line rather than interpolating across it.

**Download as CSV…** saves the currently plotted samples to a file.
