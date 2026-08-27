Search Tab
===========

.. figure:: _static/screenshots/search_tab.png
   :alt: The Search tab, showing results for "urban heat island" and a built time-slider
   :width: 500px

The Search tab resolves a plain-language description of what you're after
(a "use case") into concrete Copernicus datasets/variables, then lets you
preview them as a time-stepped WMTS layer and optionally export a
GeoTIFF.

Semantic search
-----------------

Type a phrase like *"urban heat island"* or *"PM2.5 exposure"* into the
search box and click **Search** (or press Enter). Results are ranked by
confidence and show the matched use case, dataset, and variable. Clicking
a row selects it — the line below the table ("Selected: …") confirms the
dataset and variable, with a link to the dataset's public CDS catalogue
page.

Search combines two signals (``geobridge.semantic_resources()``, geobridge
0.1.10+): curated matching against a hand-written taxonomy of common use
cases, and a catalog-wide search over every dataset/variable geobridge
knows about. The **Use case** column shows the curated label when a
result matches one, and a dash (—) when it doesn't — a dash doesn't mean
the result is a worse match, only that no one has written a named use
case for that particular dataset/variable yet.

Area of interest
-------------------

Optional — leave unset to work with the whole globe. Two radio buttons
pick the method — **Draw on map** / **From layer** — and grey out
whichever one isn't selected, so only the active method's controls are
actually clickable.

* **Draw rectangle on map** — hides the dialog, drag a rectangle on the
  QGIS map canvas, and the dialog reappears with the bounds filled in.
* **Layer** + **Use extent** — pick an existing layer from the dropdown
  and click **Use extent** to use its bounding box instead. The dropdown
  only lists polygon vector layers — rasters (including this plugin's own
  WMTS preview layers) and point/line layers are filtered out, since their
  extent is rarely a meaningful "area of interest."

The chosen area is shown as West/South/East/North in degrees, prefixed
with how it was set — "Drawn on map" or "From layer: <name>" — so it's
always clear which method actually produced the area currently in effect,
even if you draw a rectangle and later click around the layer dropdown
without pressing "Use extent" again.

.. note::
   This tab's area of interest is independent from the
   :doc:`browse_tab`'s — setting one does not affect the other.

Time range and WMTS preview
------------------------------

Set **Start**, **End**, and a **Step** (e.g. daily/weekly/monthly), then
click **Build layers**. This adds one WMTS raster layer per time step to
the map (grouped together in the layers panel). The slider below scrubs
through the built steps, and **Play** animates through them automatically.

.. note::
   The info icon next to **Step** is worth reading — it's a common
   point of confusion given Export's separate **Aggregation** dropdown
   looks similar. **Step is not an aggregation.** Each layer is a
   snapshot at that exact timestamp, not a value averaged over the
   interval — "1 week" shows one instantaneous reading every 7 days; the
   6 days in between are skipped entirely, not folded into what you see.
   For an actual statistical reduction (mean/max/min) over a period, use
   **Aggregation** in Export to GeoTIFF (below) instead.

Whichever **Step** choice exactly matches the selected dataset's own
native time resolution is marked **(raw)** in blue — e.g. an hourly
dataset shows "1 hour (raw)". Any other choice means QGIS is resampling
from that native resolution rather than reading it as-is. Not every
dataset has an exact match among the offered steps (a 12-hourly dataset,
say) — when none does, no choice is marked, since every option
necessarily resamples.

Choices *finer* than the dataset's native resolution are greyed out and
can't be picked at all — e.g. a monthly-only dataset offers just "1
month"/"1 year"; "1 hour" through "1 week" are disabled, since there's no
real data at those intervals to show (picking one would just repeat the
same monthly value, not display anything actually varying that often).
Coarser choices stay available — skipping ahead by a year on a monthly
dataset is a legitimate way to sample less densely. This filtering only
applies when the dataset's native resolution can be parsed to a fixed
interval; a handful of multi-frequency datasets (e.g. CORDEX, which
genuinely offers several native cadences) leave every choice enabled,
since geobridge's catalogue only records one of their several native
resolutions and greying out based on that alone could hide a genuinely
valid option.

Export to GeoTIFF
--------------------

Only available for datasets on the ARCO Zarr path (``has_zarr``) — an area
of interest must be set first.

* **Aggregation** — how to reduce the time range: raw (every timestep as
  its own band), or daily/monthly/annual mean/max/min (annual min isn't
  offered — geobridge itself has no such option). The granularity part
  (daily/monthly/annual) is the same ladder as the Time range tab's
  **Step** above, just as an aggregation rather than a WMTS render
  interval; mean/max/min is a separate choice of which statistic to
  reduce with. Gets the same **(raw)**-style treatment as Step: **Raw**
  is always marked in bold blue (it's unconditionally the true native
  data), and **Daily mean**/**Monthly mean**/**Annual mean** additionally
  get marked when their granularity exactly matches the dataset's native
  resolution — e.g. a dataset that's already natively daily gains nothing
  from "Daily mean" over "Raw". The max/min variants are never flagged
  this way, even though max-of-one and min-of-one are also technically
  no-ops for a same-granularity dataset — mean/max/min all lighting up
  together read as noise rather than useful information. Same greying-out
  as Step, too: **Daily**/**Monthly**/**Annual** entries finer than the
  dataset's native resolution are disabled (**Raw** never is — it's
  always valid).
* **Save as** — output ``.tif`` path (**Browse…** to pick one).
* **Add to map after export** — load the resulting GeoTIFF as a layer once
  done.
* **Export to GeoTIFF** — runs the export as a background QGIS task; the
  status line below shows progress and the final output path.

Legend
---------

Below Export to GeoTIFF: a color-scale bar for the currently selected
variable's WMTS preview — QGIS's own Layers panel can't show a legend for
these layers (WMTS tiles are pre-rendered images with no numeric pixel
data attached on the QGIS side), so this lives in the plugin's own window
instead. Calibrated from the same source geobridge itself uses to color
the WMTS tiles (its bundled per-variable catalogue snapshot), so it
matches what's actually on screen. It's a fixed "typical range" though,
not recalculated for your specific date/area — a value outside it still
just clips to the nearest end color on the map. Hidden when nothing's
selected yet, the dataset has no WMTS preview, or no calibration data
exists for it (e.g. a CDS-only dataset with no ARCO backing).
