Time Series Tab
=================

.. figure:: _static/screenshots/time_series_tab.png
   :alt: The Time Series tab, showing a point-clicked time series plotted as a line chart
   :width: 500px

Fetches how a single point's value changes over time, for the
dataset/variable currently selected on the :doc:`search_tab`.

This tab has no dataset picker of its own — a "Selected dataset: …" /
"variable …" block at the top (with a hoverable info icon) always shows
which dataset/variable it's currently reading from, so it's clear this
reflects the Search tab's selection rather than something chosen here.

.. note::
   This whole tab is only about ARCO Zarr products — see :doc:`arco_zarr`.
   It does one bulk read off the dataset's ARCO Zarr archive, so it needs
   an authenticated CDS API key (see :doc:`api_key_tab`) and a dataset
   that actually has a Zarr archive. **Pick point on map** is disabled,
   with an explanation, when the currently selected dataset doesn't have
   one.

Picking a point
------------------

Click **Pick point on map**, then click anywhere on the QGIS map canvas.
The clicked point's longitude/latitude are shown next to the button.
Picking is one-shot: the crosshair tool deactivates itself right after
the click (restoring whichever tool was active before) and the fetch
starts immediately — click **Pick point on map** again, or use
**Refresh**, to sample a different point or re-run the same one.

Range and results
--------------------

Set **Start** and **End**, pick an **Aggregation** (raw/daily/weekly/
monthly/annual mean/max/min — "Raw" plots every native timestep, the
others bin and reduce the read instead), then click **Refresh** to
(re-)fetch. Aggregation resets to "Raw" whenever a new dataset/variable
is selected on the Search tab, so a choice from a previous dataset never
silently carries over.

The status line reports how many points came back, and the chart plots
value against time — a gap in the data (a ``None`` value) breaks the
line rather than interpolating across it.

**Download as CSV…** saves the currently plotted samples to a file.
