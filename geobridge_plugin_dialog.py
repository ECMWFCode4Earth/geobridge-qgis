# -*- coding: utf-8 -*-
"""
geobridge_plugin_dialog
~~~~~~~~~~~~~~~~~~~~~~~

The plugin's main (and only) dialog: a QTabWidget host with four tabs, in
visible order: API Key, Search, Browse by Variable, Time Series.

Tab 1 (API key): enter/save a Copernicus CDS API key (persisted via
QSettings), plus a dependency banner + "Install dependencies" button shown
only while `geobridge` isn't importable yet.

Tab 2 (Search): free-text search via geobridge.semantic_search(), results
sorted descending by confidence; selecting a WMTS-capable result reveals a
time-range control that builds one WMTS layer per time step (always via
gb.wmts_layer().to_qgis() — the XYZ approach, never
LayerDescriptor.to_qgis()) and cycles through them with a play/pause
slider, mirroring test_temporal_dialog.py's ERA5 block. Export to GeoTIFF
(ARCO Zarr-backed datasets only) lives in this tab too, as a group box.

Tab 3 (Browse by Variable, browse_tab.py — built in code, inserted between
Search and Time Series, not defined in the .ui): pick a dataset/variable/
date explicitly via a cascading selection instead of free-text search,
then preview as WMTS or download a GeoTIFF via the CDS API job queue.

Tab 4 (Time Series): pick a point on the map canvas for the dataset/
variable currently selected in Tab 2's search results, then fetch its
value over time via one of two geobridge methods (radio buttons):

- "Quick" — geobridge.point_time_series(), one WMTS GetFeatureInfo
  request per timestep. No auth, no extra install; best for exploratory
  ranges up to a few dozen/hundred steps.
- "Full history" — geobridge.zarr_point_time_series(), one bulk read off
  the ARCO Zarr archive. Needs geobridge[zarr] installed and an
  authenticated CDS API key (Tab 1); best for long/dense ranges.

Clicking a new point cancels any in-flight fetch and starts a fresh one;
results are drawn as a line plot (ts_plot_widget.py — plain QPainter, no
matplotlib/QtChart dependency) with a "Download as CSV" button. The dialog is
kept pinned above the QGIS main window (Qt.WindowStaysOnTopHint) so it
stays visible while the user clicks around the map.
"""

from __future__ import annotations

import csv
import os
from datetime import timedelta

from qgis.PyQt import uic
from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import (
    QDate, QDateTime, QPointF, QRectF, QSize, Qt, QSettings, QTime, QTimer, QUrl,
)
from qgis.PyQt.QtGui import (
    QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from qgis.PyQt.QtWidgets import QFileDialog, QLineEdit, QMessageBox
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapToolEmitPoint, QgsMapToolExtent

from . import aoi_utils
from . import export_utils
from . import gb_wrapper
from . import icons
from . import time_utils
from . import variable_labels
from .browse_tab import BrowseTab
from .dependency_installer import DependencyInstaller
from .export_task import ExportTask
from .legend_widget import VariableLegendWidget
from .timeseries_task import TimeSeriesTask
from .ts_plot_widget import TimeSeriesPlotWidget

FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), "geobridge_plugin_dialog_base.ui")
)

# QSettings storage path for the user's saved CDS credential — not a secret
# value itself, just the settings-tree location it's read from/written to.
SETTINGS_PATH_CDS_CREDENTIAL = "GeoBridge/cds_credential"
SETTINGS_KEY_LAST_EXPORT_DIR = "GeoBridge/last_export_dir"
SETTINGS_KEY_LAST_TS_CSV_DIR = "GeoBridge/last_ts_csv_dir"
DOCS_URL = "https://geobridge-qgis.readthedocs.io/en/latest/"
PLAY_INTERVAL_MS = 2000
PREFETCH_INTERVAL_MS = 150
DEFAULT_WINDOW_DAYS = 3
DEFAULT_TS_WINDOW_DAYS = 30
TS_STEP_CHOICES = (("Daily", 1), ("Weekly", 7), ("Monthly", 30))


def _disable_temporal(layer):
    """Prevent QGIS from auto-parsing dates from filenames/metadata.

    Same helper as test_temporal_dialog.py — the plugin's own time-slider
    is the source of truth for "current time", not QGIS's temporal
    controller.
    """
    try:
        layer.temporalProperties().setIsActive(False)
    except (RuntimeError, AttributeError):
        # RuntimeError: underlying C++ layer object already deleted by QGIS.
        # AttributeError: layer type has no temporalProperties().
        pass


def _to_qdatetime(dt) -> QDateTime:
    """Convert a Python datetime to a QDateTime (date/time components only,
    tzinfo is dropped — QDateTimeEdit displays local wall-clock values)."""
    return QDateTime(QDate(dt.year, dt.month, dt.day), QTime(dt.hour, dt.minute, dt.second))


def _cds_dataset_url(dataset_id: str) -> str:
    """Public CDS catalogue page for a dataset, built from its id.

    CDS uses the hyphenated dataset id in its dataset URLs, e.g.
    reanalysis_era5_single_levels ->
    https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels
    The page documents the dataset and every variable (so "t2m" etc. become
    understandable to a non-specialist).
    """
    return f"https://cds.climate.copernicus.eu/datasets/{dataset_id.replace('_', '-')}"


def _eye_icon(crossed: bool) -> QIcon:
    """Small line-drawn "eye" / "eye with a slash through it" icon for the
    API key show/hide toggle.

    Drawn with QPainter rather than shipped as an image file, so the
    plugin's asset footprint stays at just icon.png and the icon renders
    identically on every platform (no emoji-font variance).
    """
    size = 16
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#5a5a5a"))
    pen.setWidthF(1.4)
    painter.setPen(pen)

    # Almond-shaped outline: two quadratic curves meeting at the corners.
    path = QPainterPath()
    path.moveTo(1.5, 8)
    path.quadTo(8, 1.5, 14.5, 8)
    path.quadTo(8, 14.5, 1.5, 8)
    painter.drawPath(path)
    painter.drawEllipse(QPointF(8, 8), 2.2, 2.2)  # pupil

    if crossed:
        painter.drawLine(QPointF(2, 2), QPointF(14, 14))

    painter.end()
    return QIcon(pixmap)


_info_icon = icons.info_icon


def _rect_to_wgs84_bbox(rect, source_crs):
    """Convert a QgsRectangle in `source_crs` to a (west, south, east, north)
    tuple in WGS-84 degrees, matching geobridge's bbox convention."""
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    if source_crs != wgs84:
        transform = QgsCoordinateTransform(source_crs, wgs84, QgsProject.instance())
        rect = transform.transformBoundingBox(rect)
    return (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())


class _SliderTickMarks(QtWidgets.QWidget):
    """Tick marks below slider_time, redrawn to match its current range —
    a replacement for QSlider's native TicksBelow rendering, which isn't
    customizable: no Qt style (Fusion, Windows, ...) exposes a stylable
    tick-mark subcontrol, so QSS can't restyle them at all. This draws its
    own instead, one tick per slider step, in a light/muted weight.
    """

    def __init__(self, slider, parent=None):
        super().__init__(parent)
        self._slider = slider
        slider.rangeChanged.connect(lambda *_args: self.update())

    def paintEvent(self, event):  # noqa: N802 — Qt override
        count = self._slider.maximum() - self._slider.minimum()
        if count <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.palette().mid().color())
        pen.setWidthF(1.0)
        painter.setPen(pen)
        # Approximates QSlider's own groove margin (half the handle width)
        # so ticks roughly line up with where the handle actually stops —
        # exact for typical Fusion/Windows handle sizes, a minor cosmetic
        # approximation for others. Not worth QStyleOptionSlider precision
        # for a purely visual tick bar.
        margin = 8
        width = max(self.width() - 2 * margin, 1)
        for i in range(count + 1):
            x = margin + width * i / count
            painter.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        painter.end()


class GeoBridgePluginDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, iface, parent=None):
        super(GeoBridgePluginDialog, self).__init__(parent)
        self.setupUi(self)
        self.iface = iface

        # The .ui positions tabWidget with a fixed 560px geometry and no
        # layout on the dialog, so it never grew with the window — leaving
        # empty space on the right and clipping the Browse tab's sidebar. A
        # dialog layout didn't take on this Qt build, so we drive the
        # tabWidget's geometry directly (see resizeEvent / _fit_tab_widget).

        # Keep the dialog pinned above the QGIS main window: the Time Series
        # tab's whole workflow is "click a point on the map canvas, watch
        # this dialog update live", which only works if the dialog can't
        # get buried behind the main window the moment the canvas gets focus.
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        # Tab 2 state
        self._matches = []
        self._current_match = None
        self._current_descriptor = None
        self._layer_configs = []
        self._layer_ids = []
        self._group = None
        self._playing = False
        self._timer = QTimer(self)
        self._timer.setInterval(PLAY_INTERVAL_MS)
        self._timer.timeout.connect(self._advance_time)

        # Quick warm-up pass over every step right after building layers, so
        # QGIS's tile cache already has each step's tiles by the time the
        # user presses Play — without it, the first Play-through visibly
        # waits on network tile fetches per step.
        self._prefetch_index = 0
        self._prefetch_timer = QTimer(self)
        self._prefetch_timer.setInterval(PREFETCH_INTERVAL_MS)
        self._prefetch_timer.timeout.connect(self._advance_prefetch)

        # keeps a strong reference alive while a QThread install is running
        self._installer = None

        # Area-of-interest state — independent per tab (Search vs Browse):
        # drawing/selecting an AOI on one tab must never affect the other's.
        # The QgsMapToolExtent instance itself is shared/reused (only one
        # can be active on the canvas at a time anyway), but which bbox a
        # completed draw is written to is decided by _aoi_target, set right
        # before the tool is activated.
        self._search_aoi_bbox = None
        self._browse_aoi_bbox = None
        self._aoi_target = None
        self._aoi_tool = None
        self._previous_map_tool = None

        # Export-to-GeoTIFF state
        self._export_task = None
        self._browse_export_task = None  # CDS download from the Browse tab

        # Time Series tab state
        self._ts_tool = None
        self._ts_previous_map_tool = None
        self._ts_point = None
        self._ts_task = None
        self._ts_samples = []

        # plot_ts_container is a plain placeholder in the .ui (absolute
        # positioning everywhere else in this dialog doesn't support
        # dropping a custom-drawn widget in directly) — host the actual
        # plot inside it via a zero-margin layout.
        plot_layout = QtWidgets.QVBoxLayout(self.plot_ts_container)
        plot_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_ts = TimeSeriesPlotWidget(self.plot_ts_container)
        plot_layout.addWidget(self.plot_ts)

        self._populate_step_combo()
        self._populate_aggregation_combo()
        self._populate_ts_step_combo()

        # Info icon next to the Step dropdown clarifying it's a sampling
        # interval, not an aggregation — a common point of confusion given
        # Export's separate Aggregation dropdown looks superficially
        # similar. Parented to groupBox_time_range itself (not tab_search)
        # since its geometry is relative to that group box's own origin,
        # matching lbl_step/cmb_step's coordinate space.
        self.lbl_step_info_icon = QtWidgets.QLabel(self.groupBox_time_range)
        self.lbl_step_info_icon.setGeometry(250, 90, 18, 18)
        self.lbl_step_info_icon.setPixmap(_info_icon(18))
        self.lbl_step_info_icon.setToolTip(
            "Step picks which individual timestamps to render as separate "
            "layers — it is not an aggregation. Each layer shows the value "
            "at that exact moment, not a value processed or averaged over "
            "the interval. E.g. \"1 week\" shows one snapshot every 7 days; "
            "the 6 days in between are skipped, not averaged in.\n\n"
            "For an actual statistical reduction over a period (mean/max/"
            "min), use Aggregation in the Export to GeoTIFF section instead."
        )

        # Info icon in the group box's own header, top-right corner (the
        # title text "Area of interest" only occupies the left side of that
        # strip, so this doesn't collide with it) — clarifies that AOI only
        # scopes the Export to GeoTIFF download; "Build layers" (WMTS
        # preview) never reads _search_aoi_bbox at all and always requests
        # global tiles, cropped only by whatever the QGIS canvas happens to
        # show on screen.
        self.lbl_aoi_scope_info_icon = QtWidgets.QLabel(self.groupBox_aoi)
        self.lbl_aoi_scope_info_icon.setGeometry(516, 3, 16, 16)
        self.lbl_aoi_scope_info_icon.setPixmap(_info_icon(16))
        self.lbl_aoi_scope_info_icon.setToolTip(
            "This area only scopes the Export to GeoTIFF download below.\n\n"
            "It has no effect on Build layers / the WMTS preview above — "
            "that always fetches the entire globe; only what the QGIS map "
            "canvas happens to be showing looks cropped."
        )

        # Info icon next to "Use extent" clarifying that a polygon layer's
        # *rectangular bounding box* is what gets used, not the polygon's
        # actual outline — CDS/ARCO requests are always a west/south/east/
        # north rectangle, there is no "clip to this shape" concept on the
        # request side. Squeezed into the ~20px gap left after
        # btn_use_layer_extent inside groupBox_aoi's 540px width.
        self.lbl_aoi_layer_info_icon = QtWidgets.QLabel(self.groupBox_aoi)
        self.lbl_aoi_layer_info_icon.setGeometry(521, 105, 16, 16)
        self.lbl_aoi_layer_info_icon.setPixmap(_info_icon(16))
        self.lbl_aoi_layer_info_icon.setToolTip(
            "Using a layer sets the area of interest to that layer's "
            "rectangular bounding box — not the actual outline of its "
            "polygon(s). An irregular region (e.g. a watershed or admin "
            "boundary) will download data for the smallest rectangle that "
            "fully contains it, so the result will extend beyond the "
            "polygon's edges.\n\n"
            "To keep only the pixels inside the polygon, clip the "
            "exported GeoTIFF afterwards in QGIS (Raster → Extraction → "
            "Clip Raster by Mask Layer), using this layer as the mask."
        )

        # Custom tick marks below slider_time — see _SliderTickMarks for why
        # (QSlider's native ticks aren't stylable). slider_time's own height
        # was trimmed from 30 to 22 in the .ui to make room for this 8px
        # strip directly below it; lbl_current_time/btn_play were then
        # moved down a bit further for breathing room from the ticks.
        self._slider_ticks = _SliderTickMarks(self.slider_time, self.groupBox_time_range)
        self._slider_ticks.setGeometry(10, 146, 520, 8)

        # Sane default range until a search result's own coverage narrows it
        now = QDateTime.currentDateTime()
        self.dt_ts_end.setDateTime(now)
        self.dt_ts_start.setDateTime(now.addDays(-DEFAULT_TS_WINDOW_DAYS))

        # --- signals — Tab 1 ---
        self.btn_save_key.clicked.connect(self._on_save_key_clicked)
        self.btn_install_core.clicked.connect(self._on_install_core_clicked)

        # Eye icon inside the API key field to toggle Password/Normal echo
        # mode — inline QLineEdit action rather than a separate button, so
        # it doesn't need its own spot in the tab's absolute-positioned
        # layout.
        self._key_visible = False
        self._key_visibility_action = self.txt_api_key.addAction(
            _eye_icon(crossed=False), QLineEdit.ActionPosition.TrailingPosition
        )
        self._key_visibility_action.setToolTip("Show API key")
        self._key_visibility_action.triggered.connect(self._on_toggle_key_visibility)

        # --- signals — Tab 2 ---
        self.btn_search.clicked.connect(self._on_search_clicked)
        self.txt_query.returnPressed.connect(self._on_search_clicked)
        self.list_results.verticalHeader().setVisible(False)
        self.list_results.setColumnWidth(0, 70)
        self.list_results.setColumnWidth(1, 180)
        self.list_results.setColumnWidth(2, 190)
        # Fixed rather than auto-stretched (horizontalHeaderStretchLastSection
        # is off in the .ui): most variable codes are short ("t2m"), so
        # letting this column consume all leftover table width looked
        # oversized. A long code (some run 40+ chars, e.g.
        # "Vapour_Pressure_Deficit_at_Maximum_Temperature") still elides with
        # "…" rather than being cut off silently — hovering shows the full
        # friendly name via the tooltip set in _populate_results regardless.
        self.list_results.setColumnWidth(3, 90)
        self.list_results.currentCellChanged.connect(
            lambda cur_row, cur_col, prev_row, prev_col: self._on_result_selected(cur_row)
        )
        self.btn_draw_aoi.clicked.connect(self._on_draw_aoi_clicked)
        self.btn_use_layer_extent.clicked.connect(self._on_use_layer_extent_clicked)
        self.rad_aoi_draw.toggled.connect(self._on_aoi_mode_changed)
        self.rad_aoi_layer.toggled.connect(self._on_aoi_mode_changed)
        self._on_aoi_mode_changed()
        self.btn_build_layers.clicked.connect(self._on_build_layers_clicked)
        self.slider_time.valueChanged.connect(self._show_time_step)
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_browse_output.clicked.connect(self._on_browse_output_clicked)
        self.btn_export_geotiff.clicked.connect(self._on_export_clicked)

        # Legend for the WMTS preview's color scale — right column, upper
        # part (above Export to GeoTIFF, which starts at y=120 in the .ui —
        # the legend is relevant the moment a result is selected, before
        # export settings even matter). Built in code, not the .ui, since
        # it hosts a custom-painted widget rather than standard Designer
        # widgets. Hidden via VariableLegendWidget itself until a result
        # with WMTS + calibration data is selected.
        self.groupBox_legend = QtWidgets.QGroupBox("Legend", self.tab_search)
        self.groupBox_legend.setGeometry(570, 10, 540, 90)
        legend_layout = QtWidgets.QVBoxLayout(self.groupBox_legend)
        legend_layout.setContentsMargins(10, 14, 10, 10)
        self.legend_widget = VariableLegendWidget(self.groupBox_legend)
        legend_layout.addWidget(self.legend_widget)
        self.legend_widget.set_style({})  # hidden until a result is selected

        # --- signals — Tab 3 (Time Series) ---
        self.btn_pick_point.toggled.connect(self._on_pick_point_toggled)
        self.btn_ts_refresh.clicked.connect(self._on_ts_refresh_clicked)
        self.btn_ts_download_csv.clicked.connect(self._on_ts_download_csv_clicked)
        self.rad_ts_quick.toggled.connect(self._on_ts_method_changed)
        self.rad_ts_zarr.toggled.connect(self._on_ts_method_changed)

        # Two-line "Selected dataset: <title>" / "variable <code> (<friendly
        # name>)" block, plus a hoverable info icon clarifying that this tab
        # has no dataset picker of its own; it always reflects whatever is
        # currently selected on the Search tab. Built in code (not the .ui)
        # since the .ui's absolute layout has no spare row for it; the rest
        # of the tab is shifted down by _TS_LAYOUT_SHIFT to make room.
        self.lbl_ts_selected = QtWidgets.QLabel(self.tab_timeseries)
        self.lbl_ts_selected.setGeometry(10, 8, 470, 40)
        self.lbl_ts_selected.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_ts_selected.setWordWrap(True)  # matches lbl_search_selection

        self.lbl_ts_info_icon = QtWidgets.QLabel(self.tab_timeseries)
        self.lbl_ts_info_icon.setGeometry(484, 10, 16, 16)
        self.lbl_ts_info_icon.setPixmap(_info_icon())
        self.lbl_ts_info_icon.setToolTip(
            "This tab has no dataset picker of its own — it always uses "
            "whatever dataset/variable is currently selected on the Search tab."
        )

        _TS_LAYOUT_SHIFT = 44
        self.lbl_ts_hint.setGeometry(10, 52, 520, 34)
        for _name in (
            "lbl_ts_method", "rad_ts_quick", "rad_ts_zarr", "btn_pick_point",
            "lbl_ts_coords", "lbl_ts_start", "dt_ts_start", "lbl_ts_end", "dt_ts_end",
            "lbl_ts_step", "cmb_ts_step", "btn_ts_refresh",
            "progress_ts", "lbl_ts_status", "plot_ts_container", "btn_ts_download_csv",
        ):
            _w = getattr(self, _name)
            _w.move(_w.x(), _w.y() + _TS_LAYOUT_SHIFT)

        # --- Browse by Variable — built in code, not the .ui. Inserted at
        # index 2 (between Search and Time Series) rather than appended, so
        # the visible tab order is API Key, Search, Browse by Variable,
        # Time Series. Everything downstream (_tab_sizes, _apply_tab_size)
        # keys off the widget itself rather than a hardcoded index, so this
        # doesn't need any other change.
        self.browse_tab = BrowseTab(self)
        self.tabWidget.insertTab(2, self.browse_tab, "Browse by Variable")
        self.browse_tab.downloadRequested.connect(self._on_browse_download)
        # The Browse tab's AOI widgets mirror the Search tab's layout, but
        # each tab keeps its own independent bbox — see _on_draw_aoi_clicked.
        self.browse_tab.btn_draw_aoi.clicked.connect(self._on_browse_draw_aoi_clicked)
        self.browse_tab.btn_use_layer_extent.clicked.connect(
            self._on_browse_use_layer_extent_clicked
        )

        # Per-tab dialog sizes: the API Key tab is small, the Browse tab is
        # wide (six cascade columns + sidebar). _on_tab_changed applies these
        # and lets only the visible tab drive the minimum size.
        # Per-tab dialog sizes: the API Key tab is small, the Browse tab is
        # wide. After every resize the window is clamped fully on-screen so
        # the title-bar close button is always visible (see _apply_tab_size).
        self._default_size = QSize(700, 700)
        self._tab_sizes = {
            self.tab_auth: QSize(640, 360),
            # Wide enough for the two-column layout: results/AOI/time-range
            # on the left (x=10, 540 wide), Export to GeoTIFF on the right
            # (x=570, 540 wide) — see groupBox_export's geometry in the .ui.
            self.tab_search: QSize(1140, 760),
            self.tab_timeseries: QSize(720, 700),
            self.browse_tab: QSize(1040, 780),
        }
        self.tabWidget.currentChanged.connect(self._on_tab_changed)

        # A labelled "Close" button in a strip at the bottom of the dialog,
        # below the tabWidget — visible at the bottom of every tab, never
        # overlapping tab content (the tabWidget is sized to leave room).
        self.btn_close = QtWidgets.QPushButton("Close", self)
        self.btn_close.setFixedSize(110, 30)
        self.btn_close.setToolTip("Close the GeoBridge window")
        self.btn_close.clicked.connect(self.close)

        # Sits immediately left of Close, same strip — opens the plugin's
        # Read the Docs site in the system's default browser.
        self.btn_help = QtWidgets.QPushButton("Help", self)
        self.btn_help.setFixedSize(110, 30)
        self.btn_help.setToolTip("Open the GeoBridge documentation")
        self.btn_help.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DOCS_URL)))

        QgsProject.instance().cleared.connect(self._on_project_cleared)
        self._needs_reload = False

        self._load_saved_key()
        self._refresh_dependency_banner()
        self._refresh_aoi_layer_combo()
        self._on_ts_method_changed()
        if gb_wrapper.is_core_available():
            self.browse_tab.refresh_datasets()

    # ------------------------------------------------------------------ #
    # Tab 1 — API key
    # ------------------------------------------------------------------ #

    def _load_saved_key(self):
        key = QSettings().value(SETTINGS_PATH_CDS_CREDENTIAL, "", type=str)
        self.txt_api_key.setText(key)
        if key and gb_wrapper.is_core_available():
            try:
                gb_wrapper.authenticate(key)
                self.lbl_auth_status.setText("Authenticated (saved key).")
            except gb_wrapper.GeobridgeNotInstalled:
                pass
            except Exception as exc:
                self.lbl_auth_status.setText(f"Saved key failed to authenticate: {exc}")

    def _on_save_key_clicked(self):
        key = self.txt_api_key.text().strip()
        QSettings().setValue(SETTINGS_PATH_CDS_CREDENTIAL, key)
        try:
            gb_wrapper.authenticate(key)
            self.lbl_auth_status.setText("Authenticated.")
        except gb_wrapper.GeobridgeNotInstalled:
            self.lbl_auth_status.setText("geobridge is not installed yet — see above.")
        except Exception as exc:
            # geobridge.AuthenticationError, or any other geobridge exception
            self.lbl_auth_status.setText(f"Authentication failed: {exc}")

    def _on_toggle_key_visibility(self):
        self._key_visible = not self._key_visible
        if self._key_visible:
            self.txt_api_key.setEchoMode(QLineEdit.EchoMode.Normal)
            self._key_visibility_action.setToolTip("Hide API key")
        else:
            self.txt_api_key.setEchoMode(QLineEdit.EchoMode.Password)
            self._key_visibility_action.setToolTip("Show API key")
        self._key_visibility_action.setIcon(_eye_icon(crossed=self._key_visible))

    # ------------------------------------------------------------------ #
    # Dependency install (core tier)
    # ------------------------------------------------------------------ #

    def _refresh_dependency_banner(self):
        core_ok = gb_wrapper.is_core_available()
        zarr_ok = gb_wrapper.is_zarr_extra_available()
        fully_installed = core_ok and zarr_ok

        # The button always stays visible — it either invites installation
        # or confirms it already happened, so it never looks like it just
        # vanished after a successful install.
        self.btn_install_core.setVisible(True)
        if fully_installed:
            version = gb_wrapper.geobridge_version()
            label = f"✓ geobridge installed ({version})" if version else "✓ geobridge installed"
            self.btn_install_core.setText(label)
            self.btn_install_core.setEnabled(False)
            self.lbl_dep_banner.setVisible(False)
        else:
            self.btn_install_core.setText("Install dependencies")
            self.btn_install_core.setEnabled(True)
            if core_ok and not zarr_ok:
                self.lbl_dep_banner.setText(
                    "geobridge is installed, but export support (geobridge[zarr]) is "
                    "missing. Install it to enable Export to GeoTIFF."
                )
            else:
                self.lbl_dep_banner.setText(
                    "geobridge is not installed yet. Install it to enable authentication, "
                    "search, and export."
                )
            self.lbl_dep_banner.setVisible(True)

        # Absolute-positioned tab: when the banner is hidden (fully
        # installed), lift the install button and the key-entry widgets up so
        # the Save button isn't pushed low by the now-empty banner slot.
        if fully_installed:
            self.btn_install_core.move(10, 10)
            lift = 44
        else:
            self.btn_install_core.move(10, 52)
            lift = 0
        self.lbl_key_title.move(10, 96 - lift)
        self.txt_api_key.move(10, 118 - lift)
        self.btn_save_key.move(10, 154 - lift)
        self.lbl_auth_status.move(120, 154 - lift)
        self.lbl_key_hint.move(10, 196 - lift)

        # Search/auth only need the base package — don't block them on the
        # heavier zarr extra being present too.
        for widget in (self.txt_api_key, self.btn_save_key, self.txt_query, self.btn_search):
            widget.setEnabled(core_ok)

    def _on_install_core_clicked(self):
        self.btn_install_core.setEnabled(False)
        self.btn_install_core.setText("Installing…")
        # Installs the [zarr] extra too (xarray/rasterio/zarr/rioxarray/fsspec/dask) —
        # needed for Export to GeoTIFF, not just the pyyaml-only core.
        #
        # aiohttp/requests are added explicitly here because the currently
        # published geobridge[zarr] on PyPI declares plain "fsspec" rather
        # than "fsspec[http]", so fsspec's HTTPFileSystem (used to open the
        # ARCO Zarr stores over https://) is otherwise missing them — fixed
        # at the source for the next geobridge release, but this install
        # needs to work against what's on PyPI today.
        #
        # netcdf4 is added explicitly too: gb.cds_to_geotiff() (the CDS API
        # download path used for datasets not yet in the ARCO Zarr lake,
        # e.g. ERA5-Land via the Browse tab) reads the NetCDF file CDS
        # returns via this package, but it isn't part of geobridge[zarr]
        # itself (that extra only covers the ARCO/Zarr read path).
        self._installer = DependencyInstaller(
            ["geobridge[zarr]>=0.1.8", "aiohttp", "requests", "netcdf4"]
        )
        self._installer.finished_ok.connect(self._on_core_install_done)
        self._installer.finished_err.connect(self._on_core_install_failed)
        self._installer.start()

    def _on_core_install_done(self, log_text):
        QMessageBox.information(
            self,
            "GeoBridge",
            "geobridge (with export support) installed successfully.\n\n"
            "This pulls in GDAL/rasterio-adjacent packages alongside QGIS's own — "
            "please fully restart QGIS (not just Plugin Reloader) before using "
            "Export to GeoTIFF, to avoid native-library conflicts. A Plugin Reloader "
            "reload is enough for authentication and search.",
        )
        self._refresh_dependency_banner()

    def _on_core_install_failed(self, log_text):
        self.btn_install_core.setText("Install dependencies")
        self.btn_install_core.setEnabled(True)
        QMessageBox.critical(self, "GeoBridge", f"Install failed:\n\n{log_text}")

    # ------------------------------------------------------------------ #
    # Tab 2 — search
    # ------------------------------------------------------------------ #

    def _populate_step_combo(self):
        self.cmb_step.clear()
        # itemData always holds the plain STEP_CHOICES key, even once
        # _mark_raw_step_choice() below appends " (raw)" to an item's
        # *displayed* text — every lookup against STEP_CHOICES must go
        # through currentData(), never currentText(), so that suffix can
        # never break the dict lookup in _on_build_layers_clicked.
        for label in time_utils.STEP_CHOICES:
            self.cmb_step.addItem(label, label)

    def _populate_aggregation_combo(self):
        self.cmb_aggregation.clear()
        self.cmb_aggregation.addItems(list(export_utils.AGGREGATION_LABELS.keys()))
        # "Raw" (no aggregation — every timestep kept as its own band) is
        # always a valid choice regardless of dataset, unlike the Search
        # tab's Step dropdown's raw match — so this is a one-time static
        # style, not something recomputed per dataset. Same bold-blue
        # treatment as that dropdown's raw entry, for visual consistency;
        # the label already says "Raw" so no extra "(raw)" suffix needed.
        bold_font = QFont(self.cmb_aggregation.font())
        bold_font.setBold(True)
        for i in range(self.cmb_aggregation.count()):
            if export_utils.AGGREGATION_LABELS[self.cmb_aggregation.itemText(i)] == "raw":
                self.cmb_aggregation.setItemData(
                    i, QColor("#2f6fed"), Qt.ItemDataRole.ForegroundRole
                )
                self.cmb_aggregation.setItemData(i, bold_font, Qt.ItemDataRole.FontRole)

    def _populate_ts_step_combo(self):
        self.cmb_ts_step.clear()
        for label, step_days in TS_STEP_CHOICES:
            self.cmb_ts_step.addItem(label, step_days)

    def _on_search_clicked(self):
        query = self.txt_query.text().strip()
        if not query:
            return
        try:
            matches = gb_wrapper.semantic_resources(query, max_results=15)
        except gb_wrapper.GeobridgeNotInstalled:
            QMessageBox.warning(self, "GeoBridge", "Install geobridge first (API Key tab).")
            return
        except Exception as exc:
            QMessageBox.warning(self, "GeoBridge", f"Search failed: {exc}")
            return

        matches = sorted(matches, key=lambda m: m.confidence, reverse=True)
        self._matches = matches
        self._populate_results(matches)
        self.groupBox_time_range.setVisible(False)
        self.groupBox_export.setVisible(False)
        self.lbl_search_selection.setText("")

    def _populate_results(self, matches):
        # clearContents() only clears item data — it does NOT reset cell
        # spans set via setSpan() (Qt behavior, not a bug in our code). The
        # "no matches" branch below spans row 0 across all 4 columns; without
        # clearSpans() first, that span survives into the *next* search, so
        # a search that follows a zero-result one renders its real row 0 as
        # one merged cell — only the Confidence column visible, Use
        # case/Dataset/Variable hidden underneath even though their item
        # text is set correctly.
        self.list_results.clearSpans()
        self.list_results.clearContents()
        if not matches:
            self.list_results.setRowCount(1)
            item = QtWidgets.QTableWidgetItem("No matches — try a different phrase.")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.list_results.setItem(0, 0, item)
            self.list_results.setSpan(0, 0, 1, self.list_results.columnCount())
            return

        # ResourceMatch.use_cases holds raw curated-use-case ids (often
        # empty — most of this function's extra coverage over the older
        # semantic_search() comes from TF-IDF catalog matches with no
        # curated use case at all); resolve to labels once per search
        # rather than per row, and fall back to "—" when there isn't one.
        try:
            uc_labels = gb_wrapper.use_case_labels()
        except gb_wrapper.GeobridgeNotInstalled:
            uc_labels = {}

        self.list_results.setRowCount(len(matches))
        for row, m in enumerate(matches):
            use_case_text = (
                ", ".join(uc_labels.get(uc, uc) for uc in m.use_cases) if m.use_cases else "—"
            )
            self.list_results.setItem(row, 0, QtWidgets.QTableWidgetItem(f"{m.confidence:.2f}"))
            self.list_results.setItem(row, 1, QtWidgets.QTableWidgetItem(use_case_text))
            self.list_results.setItem(row, 2, QtWidgets.QTableWidgetItem(m.dataset_id))
            # Hovering the short variable code shows its readable name.
            var_item = QtWidgets.QTableWidgetItem(m.variable)
            var_item.setToolTip(variable_labels.friendly_name(m.variable))
            self.list_results.setItem(row, 3, var_item)

    def _on_result_selected(self, row):
        if row < 0 or row >= len(self._matches):
            self.groupBox_time_range.setVisible(False)
            self.groupBox_export.setVisible(False)
            self.lbl_search_selection.setText("")
            self.legend_widget.set_style({})
            return

        match = self._matches[row]
        self._current_match = match

        try:
            descriptor = gb_wrapper.discover_one(match.dataset_id)
        except gb_wrapper.GeobridgeNotInstalled:
            descriptor = None
        self._current_descriptor = descriptor

        # Blue selection line with a link to the dataset's CDS page — helps a
        # non-specialist see the full dataset name and decode a variable like
        # "t2m" (the page documents every variable).
        title = getattr(descriptor, "title", None) or match.dataset_id
        url = _cds_dataset_url(match.dataset_id)
        variable_html = f"<b>{match.variable}</b>"
        if variable_labels.has_label(match.variable):
            variable_html += f" ({variable_labels.friendly_name(match.variable)})"
        self.lbl_search_selection.setText(
            f"Selected: <b>{title}</b>"
            f"<br>variable {variable_html}"
            f" &nbsp; <a href=\"{url}\">Dataset details ↗</a>"
        )

        has_wmts = bool(descriptor and descriptor.has_wmts)
        self.groupBox_time_range.setVisible(has_wmts)
        self._update_legend(descriptor, match, has_wmts)

        has_zarr = bool(descriptor and getattr(descriptor, "has_zarr", False))

        # "Full history" on the Time Series tab (Tab 4) is a bulk ARCO Zarr
        # read (gb_wrapper.zarr_point_time_series) — only meaningful for
        # datasets that actually have a Zarr archive. Force back to "Quick"
        # if the newly selected dataset doesn't support it and Full history
        # was still checked, rather than leaving a disabled-but-checked
        # radio.
        self.rad_ts_zarr.setEnabled(has_zarr)
        if not has_zarr and self.rad_ts_zarr.isChecked():
            self.rad_ts_quick.setChecked(True)

        self.groupBox_export.setVisible(bool(descriptor))
        self.btn_export_geotiff.setEnabled(has_zarr)
        self.txt_output_path.clear()
        if not has_zarr:
            self.lbl_export_status.setText(
                "Export not available for this dataset yet — only ARCO Zarr-backed "
                "datasets support export right now."
            )
        else:
            self.lbl_export_status.setText("")
        # Unconditional on has_zarr/descriptor, not has_wmts (unlike
        # _mark_raw_step_choice below) — the Export section, and so this
        # marking, is relevant for zarr-only datasets with no WMTS preview
        # too, which return early below before ever reaching that call.
        self._mark_raw_aggregation_choice(descriptor)
        self._disable_finer_than_native_aggregations(descriptor)
        self._move_to_nearest_enabled(self.cmb_aggregation)

        # Seed a sane start/end regardless of WMTS support — export (below)
        # uses these same fields even for a has_zarr-but-no-WMTS dataset.
        if descriptor is not None:
            end = descriptor.time_range[1]
            start = end - timedelta(days=DEFAULT_WINDOW_DAYS)
            self.dt_end.setDateTime(_to_qdatetime(end))
            self.dt_start.setDateTime(_to_qdatetime(start))

            # Time Series tab: same dataset coverage, but a longer default
            # window since a one-point series is cheap per step, unlike a
            # full-grid WMTS/export request.
            ts_start = end - timedelta(days=DEFAULT_TS_WINDOW_DAYS)
            self.dt_ts_end.setDateTime(_to_qdatetime(end))
            self.dt_ts_start.setDateTime(_to_qdatetime(ts_start))

        self._refresh_ts_hint()

        if not has_wmts:
            return

        self._mark_raw_step_choice(descriptor)
        self._disable_finer_than_native_steps(descriptor)
        default_label = time_utils.default_step_choice(getattr(descriptor, "time_step", ""))
        index = self.cmb_step.findData(default_label)
        if index >= 0:
            self.cmb_step.setCurrentIndex(index)
        self._move_to_nearest_enabled(self.cmb_step)

    # geobridge's LayerDescriptor.time_step is a single string, but some
    # datasets (e.g. CORDEX — "3h, 6h, daily, monthly and seasonal" per its
    # own CDS documentation) genuinely offer several native frequencies;
    # geobridge's snapshot can only record one of them. So a "(raw)" match
    # here means "matches geobridge's recorded resolution", not
    # necessarily "the only native resolution this dataset has".
    _RAW_STEP_TOOLTIP = (
        "Matches geobridge's recorded native resolution for this dataset. "
        "Some datasets offer multiple native frequencies (e.g. CORDEX: "
        "3h, 6h, daily, monthly, seasonal) — geobridge's catalogue records "
        "only one of them, so this may not be the only genuinely raw option."
    )

    def _mark_raw_step_choice(self, descriptor):
        """Flag whichever cmb_step entry matches the dataset's raw time_step
        exactly (not just the closest one, unlike default_step_choice) with
        an "(raw)" suffix in bold blue — every other choice for that
        dataset involves resampling from its native resolution, and stays
        plain (never bold/colored) so the flagged entry actually stands out
        rather than just being one of several styled items."""
        raw_time_step = getattr(descriptor, "time_step", "") if descriptor else ""
        raw_label = time_utils.exact_step_choice(raw_time_step)
        bold_font = QFont(self.cmb_step.font())
        bold_font.setBold(True)
        for i in range(self.cmb_step.count()):
            label = self.cmb_step.itemData(i)
            if label == raw_label:
                self.cmb_step.setItemText(i, f"{label} (raw)")
                self.cmb_step.setItemData(i, QColor("#2f6fed"), Qt.ItemDataRole.ForegroundRole)
                self.cmb_step.setItemData(i, bold_font, Qt.ItemDataRole.FontRole)
                self.cmb_step.setItemData(i, self._RAW_STEP_TOOLTIP, Qt.ItemDataRole.ToolTipRole)
            else:
                self.cmb_step.setItemText(i, label)
                self.cmb_step.setItemData(i, None, Qt.ItemDataRole.ForegroundRole)
                self.cmb_step.setItemData(i, None, Qt.ItemDataRole.FontRole)
                self.cmb_step.setItemData(i, None, Qt.ItemDataRole.ToolTipRole)

    _FINER_THAN_NATIVE_TOOLTIP = (
        "Disabled — finer than this dataset's native resolution. There is "
        "no real data at this interval; the value shown would just be the "
        "same native sample repeated, not something actually varying at "
        "this granularity."
    )

    def _disable_finer_than_native_steps(self, descriptor):
        """Grey out cmb_step entries strictly finer than the dataset's
        native time_step — e.g. a monthly-native dataset has no real
        hourly/daily/weekly data, so picking one of those just repeats the
        same monthly value. Call *after* _mark_raw_step_choice, since that
        method resets ToolTipRole on every non-raw entry and would
        otherwise wipe the tooltip this sets on disabled ones.

        Never disables anything when the native step can't be parsed (see
        time_utils.finer_than_native_steps) — includes multi-frequency
        datasets like CORDEX, where geobridge's single recorded time_step
        may not be the only genuinely native resolution (same caveat as
        _RAW_STEP_TOOLTIP above)."""
        raw_time_step = getattr(descriptor, "time_step", "") if descriptor else ""
        finer_labels = time_utils.finer_than_native_steps(raw_time_step)
        model = self.cmb_step.model()
        for i in range(self.cmb_step.count()):
            disabled = self.cmb_step.itemData(i) in finer_labels
            model.item(i).setEnabled(not disabled)
            if disabled:
                self.cmb_step.setItemData(
                    i, self._FINER_THAN_NATIVE_TOOLTIP, Qt.ItemDataRole.ToolTipRole
                )

    @staticmethod
    def _move_to_nearest_enabled(combo):
        """If combo's current item just got disabled, move to the nearest
        still-enabled one. Searches coarser (later in the list) first —
        disabled entries are always the finer ones earlier in the list
        (see _disable_finer_than_native_steps/_aggregations), so a later
        entry is always guaranteed to exist and be enabled."""
        model = combo.model()
        idx = combo.currentIndex()
        if idx < 0 or model.item(idx).isEnabled():
            return
        for i in range(idx + 1, combo.count()):
            if model.item(i).isEnabled():
                combo.setCurrentIndex(i)
                return
        for i in range(idx - 1, -1, -1):
            if model.item(i).isEnabled():
                combo.setCurrentIndex(i)
                return

    # Aggregation choices whose interval, if it exactly matches the
    # dataset's native time_step, means that choice performs no real
    # averaging at all — same underlying time_utils.exact_step_choice()
    # check as cmb_step above, just against export_utils's aggregation
    # keys instead of STEP_CHOICES labels directly. "raw" itself is
    # excluded here — it's unconditionally the true native data regardless
    # of cadence, and is already always marked in _populate_aggregation_combo.
    # Only the *_mean choices get flagged — max/min are a genuinely
    # different statistic even when there's only one native sample a day
    # (max-of-one and min-of-one both trivially equal that one value, same
    # as mean-of-one, but flagging all three blue at once for a daily
    # dataset read as confusing/redundant rather than informative).
    _AGGREGATION_STEP_EQUIVALENT = {
        "daily_mean": "1 day",
        "monthly_mean": "1 month",
        "annual_mean": "1 year",
    }

    def _mark_raw_aggregation_choice(self, descriptor):
        """Flag "Daily mean"/"Monthly mean" too when they exactly match the
        dataset's native time_step — e.g. a dataset that's already natively
        daily gains nothing from "Daily mean" over "Raw", since there's
        only one native sample per day to begin with."""
        raw_time_step = getattr(descriptor, "time_step", "") if descriptor else ""
        exact_label = time_utils.exact_step_choice(raw_time_step)
        bold_font = QFont(self.cmb_aggregation.font())
        bold_font.setBold(True)
        for i in range(self.cmb_aggregation.count()):
            key = export_utils.AGGREGATION_LABELS[self.cmb_aggregation.itemText(i)]
            if key == "raw":
                continue  # always marked in _populate_aggregation_combo; never reset here
            if self._AGGREGATION_STEP_EQUIVALENT.get(key) == exact_label:
                self.cmb_aggregation.setItemData(
                    i, QColor("#2f6fed"), Qt.ItemDataRole.ForegroundRole
                )
                self.cmb_aggregation.setItemData(i, bold_font, Qt.ItemDataRole.FontRole)
                self.cmb_aggregation.setItemData(
                    i, self._RAW_STEP_TOOLTIP, Qt.ItemDataRole.ToolTipRole
                )
            else:
                self.cmb_aggregation.setItemData(i, None, Qt.ItemDataRole.ForegroundRole)
                self.cmb_aggregation.setItemData(i, None, Qt.ItemDataRole.FontRole)
                self.cmb_aggregation.setItemData(i, None, Qt.ItemDataRole.ToolTipRole)

    # Granularity of each non-raw aggregation key, in seconds — same
    # buckets as _AGGREGATION_STEP_EQUIVALENT's labels, just expressed as
    # a duration so every mean/max/min variant of a granularity can be
    # compared against the dataset's native step, not only the *_mean one.
    _AGGREGATION_GRANULARITY_SECONDS = {
        "daily_mean": 86400,
        "daily_max": 86400,
        "daily_min": 86400,
        "monthly_mean": 30 * 86400,
        "monthly_max": 30 * 86400,
        "monthly_min": 30 * 86400,
        "annual_mean": 365 * 86400,
        "annual_max": 365 * 86400,
    }

    def _disable_finer_than_native_aggregations(self, descriptor):
        """Grey out Aggregation entries strictly finer than the dataset's
        native time_step, mirroring _disable_finer_than_native_steps —
        e.g. a monthly-native dataset has no daily data to average, so
        "Daily mean/max/min" are disabled. "raw" is never disabled — it's
        always valid regardless of cadence. Call after
        _mark_raw_aggregation_choice for the same tooltip-ordering reason."""
        raw_time_step = getattr(descriptor, "time_step", "") if descriptor else ""
        native_seconds = time_utils.native_step_seconds(raw_time_step)
        model = self.cmb_aggregation.model()
        for i in range(self.cmb_aggregation.count()):
            key = export_utils.AGGREGATION_LABELS[self.cmb_aggregation.itemText(i)]
            granularity_secs = self._AGGREGATION_GRANULARITY_SECONDS.get(key)
            disabled = (
                key != "raw"
                and native_seconds is not None
                and granularity_secs is not None
                and granularity_secs < native_seconds
            )
            model.item(i).setEnabled(not disabled)
            if disabled:
                self.cmb_aggregation.setItemData(
                    i, self._FINER_THAN_NATIVE_TOOLTIP, Qt.ItemDataRole.ToolTipRole
                )

    def _update_legend(self, descriptor, match, has_wmts):
        """Refresh the WMTS color-scale legend for the newly selected
        result — hidden when there's no WMTS preview to show a scale for,
        or no ARCO calibration data (e.g. a CDS-only dataset)."""
        if not has_wmts:
            self.legend_widget.set_style({})
            return
        style = gb_wrapper.variable_style(match.dataset_id, match.variable)
        label = match.variable
        if variable_labels.has_label(match.variable):
            label += f" ({variable_labels.friendly_name(match.variable)})"
        self.legend_widget.set_style(style, variable_label=label)

    # ------------------------------------------------------------------ #
    # Area of interest — shared "draw on map" tool, independent bboxes per
    # tab (Search / Tab 2 and Browse by Variable / Tab 3)
    # ------------------------------------------------------------------ #

    def _refresh_aoi_layer_combo(self):
        combos = [self.cmb_aoi_layer]
        if hasattr(self, "browse_tab"):
            combos.append(self.browse_tab.cmb_aoi_layer)
        # Polygon vector layers only — "use extent" from a raster (e.g. this
        # plugin's own WMTS preview layers) or a point/line layer is
        # technically possible but essentially never what "area of
        # interest" actually means; this keeps the dropdown to layers where
        # picking one is a meaningful choice.
        layers = [
            layer
            for layer in QgsProject.instance().mapLayers().values()
            if isinstance(layer, QgsVectorLayer)
            and QgsWkbTypes.geometryType(layer.wkbType()) == QgsWkbTypes.GeometryType.PolygonGeometry
        ]
        for combo in combos:
            combo.clear()
            for layer in layers:
                combo.addItem(layer.name(), layer.id())

    def _on_aoi_mode_changed(self):
        """Grey out whichever AOI method isn't selected via the radio
        buttons. Both control sets stay visible rather than being hidden —
        groupBox_aoi uses absolute positioning (no layout to reflow into
        the gap hiding one would leave), and leaving both visible-but-one-
        disabled also means the other method stays discoverable at a
        glance instead of vanishing behind a mode toggle."""
        draw_mode = self.rad_aoi_draw.isChecked()
        self.btn_draw_aoi.setEnabled(draw_mode)
        self.lbl_aoi_layer.setEnabled(not draw_mode)
        self.cmb_aoi_layer.setEnabled(not draw_mode)
        self.btn_use_layer_extent.setEnabled(not draw_mode)

    def _on_draw_aoi_clicked(self):
        self._start_aoi_draw("search")

    def _on_browse_draw_aoi_clicked(self):
        self._start_aoi_draw("browse")

    def _start_aoi_draw(self, target):
        self._aoi_target = target
        canvas = self.iface.mapCanvas()
        if self._aoi_tool is None:
            self._aoi_tool = QgsMapToolExtent(canvas)
            self._aoi_tool.extentChanged.connect(self._on_aoi_extent_drawn)
            # Fires whenever the tool stops being the active one — whether
            # because a rectangle was completed (we switch tools ourselves
            # below) or the user cancelled by picking another tool. Either
            # way, that's the signal to bring the dialog back.
            self._aoi_tool.deactivated.connect(self._on_aoi_tool_deactivated)
        self._previous_map_tool = canvas.mapTool()
        canvas.setMapTool(self._aoi_tool)

        # Get the dialog out of the way so the map is fully visible while
        # drawing; restored by _on_aoi_tool_deactivated once the tool exits.
        # This also rules out the other tab's own "Draw on map" button being
        # clicked mid-draw, since the whole dialog (both tabs) is hidden.
        self.hide()
        # Bring QGIS's own main window to the front — hiding this dialog
        # alone doesn't guarantee the canvas is what's actually on top if
        # some other window/app was in front of QGIS itself, which would
        # otherwise leave the user needing to click over to QGIS manually
        # before they could actually draw anything.
        main_window = self.iface.mainWindow()
        main_window.raise_()
        main_window.activateWindow()
        self.iface.messageBar().pushInfo(
            "GeoBridge", "Drag a rectangle on the map to set the area of interest."
        )

    def _on_aoi_extent_drawn(self, rect):
        canvas = self.iface.mapCanvas()
        source_crs = canvas.mapSettings().destinationCrs()
        bbox = _rect_to_wgs84_bbox(rect, source_crs)
        self._set_aoi_bbox(bbox, self._aoi_target or "search", "Drawn on map")
        if self._previous_map_tool is not None:
            canvas.setMapTool(self._previous_map_tool)
            self._previous_map_tool = None

    def _on_aoi_tool_deactivated(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_use_layer_extent_clicked(self):
        self._use_layer_extent(self.cmb_aoi_layer, "search")

    def _on_browse_use_layer_extent_clicked(self):
        self._use_layer_extent(self.browse_tab.cmb_aoi_layer, "browse")

    def _use_layer_extent(self, combo, target):
        layer_id = combo.currentData()
        if not layer_id:
            QMessageBox.warning(self, "GeoBridge", "No layer selected.")
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            QMessageBox.warning(self, "GeoBridge", "Selected layer is no longer available.")
            return
        bbox = _rect_to_wgs84_bbox(layer.extent(), layer.crs())
        self._set_aoi_bbox(bbox, target, f"From layer: {layer.name()}")

    def _set_aoi_bbox(self, bbox, target, source):
        """Store `bbox` for `target` ("search" or "browse") only — the two
        tabs' areas of interest are independent, so this never touches the
        other tab's stored bbox or displayed label.

        `source` ("Drawn on map" / "From layer: <name>") is prefixed onto
        the displayed text so it's always clear *how* the current area was
        set — drawing then later fiddling with the layer combo (without
        clicking "Use extent") previously left no way to tell which one was
        actually still in effect, since both actions just updated the same
        coordinates with no indication of where they came from.
        """
        if target == "browse":
            self._browse_aoi_bbox = bbox
            lbl_bbox, lbl_warn = self.browse_tab.lbl_aoi_bbox, self.browse_tab.lbl_aoi_warning
        else:
            self._search_aoi_bbox = bbox
            lbl_bbox, lbl_warn = self.lbl_aoi_bbox, self.lbl_aoi_warning
        text = f"{source} — {aoi_utils.format_bbox(bbox)}"
        warning = aoi_utils.validate_bbox_size(bbox)
        lbl_bbox.setText(text)
        lbl_warn.setText(warning or "")
        lbl_warn.setVisible(bool(warning))

    def _on_build_layers_clicked(self):
        if self._current_match is None:
            return

        start = self.dt_start.dateTime().toPyDateTime()
        end = self.dt_end.dateTime().toPyDateTime()
        step = time_utils.STEP_CHOICES[self.cmb_step.currentData()]

        try:
            steps = time_utils.generate_time_steps(start, end, step)
        except time_utils.TooManyStepsError as exc:
            answer = QMessageBox.question(
                self,
                "GeoBridge",
                f"This range/step would build {exc.count} layers "
                f"(cap is {exc.max_steps}). Build the first {exc.max_steps} instead?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            steps = time_utils.generate_time_steps(start, end, step, truncate=True)
        except ValueError as exc:
            QMessageBox.warning(self, "GeoBridge", str(exc))
            return

        if not steps:
            QMessageBox.warning(self, "GeoBridge", "End must be after start.")
            return

        try:
            self._layer_configs = gb_wrapper.build_wmts_layer_configs(
                self._current_match.dataset_id,
                self._current_match.variable,
                steps,
                descriptor=self._current_descriptor,
            )
        except Exception as exc:
            QMessageBox.warning(self, "GeoBridge", f"Could not build layers: {exc}")
            return

        self._load_wmts_layers()

    def _on_browse_download(self, payload):
        """Download a Browse-by-Variable selection as a GeoTIFF via the CDS API.

        Runs geobridge.cds_to_geotiff off the UI thread (ExportTask kind=cds):
        it submits the request dict the tab built to the CDS queue, waits for
        the job, then converts the result to GeoTIFF. Needs the export deps
        and an authenticated key, both checked up front.
        """
        dataset_id = payload["dataset_id"]
        variable = payload["variable"]
        request = dict(payload["request"])

        if not gb_wrapper.is_core_available():
            QMessageBox.warning(self, "GeoBridge", "Install geobridge first (API Key tab).")
            return
        if not gb_wrapper.is_zarr_extra_available():
            QMessageBox.warning(
                self, "GeoBridge",
                "Download needs the export dependencies — use \"Install "
                "dependencies\" on the API Key tab first.",
            )
            return
        if not gb_wrapper.is_authenticated():
            QMessageBox.warning(
                self, "GeoBridge",
                "Download needs an authenticated CDS API key — save one on the "
                "API Key tab first.",
            )
            return

        # Warn (but let the user override) if the request looks invalid.
        try:
            errors = gb_wrapper.validate_request(dataset_id, request)
        except Exception:  # noqa: BLE001 — validation is best-effort
            errors = []
        if errors:
            answer = QMessageBox.question(
                self, "GeoBridge",
                "This request may not be valid:\n\n- " + "\n- ".join(errors)
                + "\n\nSubmit to CDS anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        default_name = f"{dataset_id}_{variable}.tif"
        start_dir = QSettings().value(SETTINGS_KEY_LAST_EXPORT_DIR, "", type=str)
        start_path = os.path.join(start_dir, default_name) if start_dir else default_name
        path, _ = QFileDialog.getSaveFileName(
            self, "Save GeoTIFF as", start_path, "GeoTIFF (*.tif *.tiff)"
        )
        if not path:
            return
        QSettings().setValue(SETTINGS_KEY_LAST_EXPORT_DIR, os.path.dirname(path))

        params = {
            "dataset": dataset_id,
            "request": request,
            "variable": variable,
            "bbox": self._browse_aoi_bbox,   # optional area from this tab; None = global
            "output_path": path,
            "cog": True,
        }
        self.browse_tab.lbl_status.setText(
            "Submitting to CDS — this can take minutes to hours; it runs in the "
            "background."
        )
        self._browse_export_task = ExportTask(
            f"GeoBridge: downloading {dataset_id}", "cds", params, add_to_map=True
        )
        self._browse_export_task.taskCompleted.connect(self._on_browse_download_ok)
        self._browse_export_task.taskTerminated.connect(self._on_browse_download_err)
        QgsApplication.taskManager().addTask(self._browse_export_task)

    def _on_browse_download_ok(self):
        path = self._browse_export_task.result_path if self._browse_export_task else None
        self.browse_tab.lbl_status.setText(f"Downloaded: {path}")

    def _on_browse_download_err(self):
        exc = self._browse_export_task.exception if self._browse_export_task else None
        self.browse_tab.lbl_status.setText(f"Download failed: {exc}")

    # ------------------------------------------------------------------ #
    # Tab 2 — WMTS layer-tree management
    # (mirrors test_temporal_dialog.py's _load_era5_layers / _show_era5_step
    # / _toggle_era5_play / _stop_era5 / _advance_era5)
    # ------------------------------------------------------------------ #

    def _load_wmts_layers(self, group_label=None):
        root = QgsProject.instance().layerTreeRoot()

        # Replace any previous series rather than accumulating groups/layers.
        for layer_id in self._layer_ids:
            if QgsProject.instance().mapLayer(layer_id):
                QgsProject.instance().removeMapLayer(layer_id)
        self._layer_ids = []
        if self._group is not None:
            try:
                root.removeChildNode(self._group)
            except RuntimeError:
                # Underlying C++ layer-tree node already deleted by QGIS.
                pass
            self._group = None

        if group_label is None:
            group_label = self._current_match.dataset_id
        group_name = f"GeoBridge — {group_label}"
        self._group = root.insertGroup(0, group_name)
        self._group.setExpanded(False)

        for conf in self._layer_configs:
            layer = QgsRasterLayer(conf["uri"], conf["name"], conf["provider"])
            if layer.isValid():
                _disable_temporal(layer)
                QgsProject.instance().addMapLayer(layer, addToLegend=False)
                self._group.addLayer(layer)
                self._layer_ids.append(layer.id())
            else:
                self.lbl_current_time.setText("Layer failed to load — check connection.")

        self.slider_time.setMaximum(max(0, len(self._layer_ids) - 1))
        self.slider_time.blockSignals(True)
        self.slider_time.setValue(0)
        self.slider_time.blockSignals(False)
        if self._layer_ids:
            self._start_prefetch()

    def _show_time_step(self, index):
        root = QgsProject.instance().layerTreeRoot()
        for i, layer_id in enumerate(self._layer_ids):
            node = root.findLayer(layer_id)
            if node:
                node.setItemVisibilityChecked(i == index)
        if 0 <= index < len(self._layer_configs):
            self.lbl_current_time.setText(self._layer_configs[index]["label"])
        self.iface.mapCanvas().refresh()

    def _start_prefetch(self):
        """Flick through every step once, fast, right after building layers.

        Each QgsRasterLayer only starts fetching XYZ tiles once it's
        actually been made visible at the current extent — this triggers
        that fetch for every step in the background ahead of time, so the
        first real Play-through doesn't visibly stall per step waiting on
        the network. Doesn't block the UI thread: tile fetching itself
        stays asynchronous, this just kicks it off early for every step.
        """
        self._prefetch_index = 0
        self._prefetch_timer.start()

    def _advance_prefetch(self):
        self._show_time_step(self._prefetch_index)
        self._prefetch_index += 1
        if self._prefetch_index >= len(self._layer_ids):
            self._prefetch_timer.stop()
            self._show_time_step(0)  # land back on the first step

    def _toggle_play(self):
        if self._playing:
            self._stop_play()
        else:
            self._playing = True
            self.btn_play.setText("||  Pause")
            self._timer.start()

    def _stop_play(self):
        self._timer.stop()
        self._playing = False
        self.btn_play.setText("▶  Play")

    def _advance_time(self):
        if self._layer_ids:
            self.slider_time.setValue((self.slider_time.value() + 1) % len(self._layer_ids))

    # ------------------------------------------------------------------ #
    # Tab 2 — export to GeoTIFF (ARCO Zarr path only, see export_task.py)
    # ------------------------------------------------------------------ #

    def _on_browse_output_clicked(self):
        if self._current_match is None:
            return
        aggregation = export_utils.AGGREGATION_LABELS[self.cmb_aggregation.currentText()]
        default_name = export_utils.default_output_filename(
            self._current_match.dataset_id, self._current_match.variable, aggregation
        )
        start_dir = QSettings().value(SETTINGS_KEY_LAST_EXPORT_DIR, "", type=str)
        start_path = os.path.join(start_dir, default_name) if start_dir else default_name

        path, _ = QFileDialog.getSaveFileName(
            self, "Save GeoTIFF as", start_path, "GeoTIFF (*.tif *.tiff)"
        )
        if not path:
            return
        self.txt_output_path.setText(path)
        QSettings().setValue(SETTINGS_KEY_LAST_EXPORT_DIR, os.path.dirname(path))

    def _on_export_clicked(self):
        if self._current_match is None or self._current_descriptor is None:
            return
        if not getattr(self._current_descriptor, "has_zarr", False):
            QMessageBox.warning(
                self, "GeoBridge", "This dataset isn't available via the ARCO Zarr path yet."
            )
            return
        if self._search_aoi_bbox is None:
            QMessageBox.warning(
                self, "GeoBridge", "Draw or select an area of interest first (above)."
            )
            return
        output_path = self.txt_output_path.text().strip()
        if not output_path:
            QMessageBox.warning(
                self, "GeoBridge", "Choose where to save the GeoTIFF first (Browse…)."
            )
            return

        start = self.dt_start.dateTime().toPyDateTime()
        end = self.dt_end.dateTime().toPyDateTime()
        if end <= start:
            QMessageBox.warning(self, "GeoBridge", "End must be after start.")
            return

        aggregation = export_utils.AGGREGATION_LABELS[self.cmb_aggregation.currentText()]
        if aggregation == "raw":
            span_days = (end - start).total_seconds() / 86400
            warning = export_utils.raw_aggregation_span_warning(span_days)
            if warning:
                answer = QMessageBox.question(
                    self, "GeoBridge", warning,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

        params = {
            "dataset": self._current_match.dataset_id,
            "variable": self._current_match.variable,
            "bbox": self._search_aoi_bbox,
            "time_range": (start.isoformat(), end.isoformat()),
            "aggregation": aggregation,
            "output_path": output_path,
            "cog": True,
        }

        self.btn_export_geotiff.setEnabled(False)
        self.lbl_export_status.setText("Exporting…")
        self.progress_export.setValue(0)
        self.progress_export.setVisible(True)

        self._export_task = ExportTask(
            f"GeoBridge: exporting {self._current_match.dataset_id}",
            "zarr",
            params,
            add_to_map=self.chk_add_to_map.isChecked(),
        )
        self._export_task.progressChanged.connect(self._on_export_progress)
        self._export_task.taskCompleted.connect(self._on_export_finished_ok)
        self._export_task.taskTerminated.connect(self._on_export_finished_err)
        QgsApplication.taskManager().addTask(self._export_task)

    def _on_export_progress(self, progress: float):
        self.progress_export.setValue(int(progress))

    def _on_export_finished_ok(self):
        self.btn_export_geotiff.setEnabled(True)
        self.progress_export.setVisible(False)
        path = self._export_task.result_path if self._export_task else None
        self.lbl_export_status.setText(f"Done: {path}")

    def _on_export_finished_err(self):
        self.btn_export_geotiff.setEnabled(True)
        self.progress_export.setVisible(False)
        exc = self._export_task.exception if self._export_task else None
        self.lbl_export_status.setText(f"Export failed: {exc}")

    # ------------------------------------------------------------------ #
    # Tab 3 — point time series
    # ------------------------------------------------------------------ #

    def _ts_method(self) -> str:
        """'zarr' or 'quick', from the Time Series tab's method radios."""
        return "zarr" if self.rad_ts_zarr.isChecked() else "quick"

    def _on_ts_method_changed(self, checked: bool = True):
        # Each radio's toggled() fires twice per switch (once for the
        # button losing the check, once for the one gaining it) — only
        # react to the "gained" half, and once at __init__ time via the
        # default checked=True.
        if not checked:
            return
        is_zarr = self._ts_method() == "zarr"
        # geo_chunked/time_chunked archive reads come back at the
        # dataset's native time resolution — there's no per-step loop to
        # thin out, unlike the WMTS method.
        self.lbl_ts_step.setEnabled(not is_zarr)
        self.cmb_ts_step.setEnabled(not is_zarr)
        self._refresh_ts_hint()

    def _refresh_ts_hint(self):
        if self._current_match is None:
            self.lbl_ts_selected.setText("")
            self.lbl_ts_info_icon.setVisible(False)
            self.lbl_ts_hint.setText(
                'Pick a dataset/variable in the Search tab first, then click '
                '"Pick point on map" and click anywhere on the map canvas.'
            )
            return

        # Same title/friendly-variable info as the Search tab's own
        # selection line, but on two lines here — the dataset title alone
        # can be long enough that cramming the variable onto the same line
        # crowds this tab's narrower layout.
        title = getattr(self._current_descriptor, "title", None) or self._current_match.dataset_id
        variable = self._current_match.variable
        variable_html = f"<b>{variable}</b>"
        if variable_labels.has_label(variable):
            variable_html += f" ({variable_labels.friendly_name(variable)})"
        self.lbl_ts_selected.setText(
            f"Selected dataset: <b>{title}</b><br>variable {variable_html}"
        )
        self.lbl_ts_info_icon.setVisible(True)

        note = (
            "Full history needs geobridge[zarr] installed and an authenticated "
            "CDS API key (API Key tab)."
            if self._ts_method() == "zarr" else
            "Quick mode works without auth; keep ranges to a few dozen/hundred steps."
        )
        self.lbl_ts_hint.setText(
            'Click "Pick point on map" then click anywhere on the map canvas. ' + note
        )

    def _on_pick_point_toggled(self, checked: bool):
        canvas = self.iface.mapCanvas()
        if not checked:
            if canvas.mapTool() is self._ts_tool:
                if self._ts_previous_map_tool is not None:
                    canvas.setMapTool(self._ts_previous_map_tool)
                else:
                    canvas.unsetMapTool(self._ts_tool)
            self._ts_previous_map_tool = None
            return

        if self._current_match is None:
            QMessageBox.warning(
                self, "GeoBridge", "Select a dataset in the Search tab first."
            )
            self.btn_pick_point.setChecked(False)
            return

        if self._ts_tool is None:
            self._ts_tool = QgsMapToolEmitPoint(canvas)
            self._ts_tool.canvasClicked.connect(self._on_ts_point_clicked)
            # Fires when the user switches to a different QGIS tool directly
            # (not by un-checking our button) — keep the button in sync.
            self._ts_tool.deactivated.connect(self._on_ts_tool_deactivated)
        self._ts_previous_map_tool = canvas.mapTool()
        canvas.setMapTool(self._ts_tool)
        self.iface.messageBar().pushInfo(
            "GeoBridge",
            "Click anywhere on the map to sample its time series. "
            "Click again elsewhere to update it.",
        )

    def _on_ts_tool_deactivated(self):
        self.btn_pick_point.setChecked(False)

    def _on_ts_point_clicked(self, point, button):
        canvas = self.iface.mapCanvas()
        source_crs = canvas.mapSettings().destinationCrs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        if source_crs != wgs84:
            transform = QgsCoordinateTransform(source_crs, wgs84, QgsProject.instance())
            point = transform.transform(point)

        self._ts_point = (point.x(), point.y())
        self.lbl_ts_coords.setText(f"Lon: {point.x():.4f}   Lat: {point.y():.4f}")
        self.btn_ts_refresh.setEnabled(True)
        self._start_ts_fetch()

    def _on_ts_refresh_clicked(self):
        self._start_ts_fetch()

    def _start_ts_fetch(self):
        if self._current_match is None or self._ts_point is None:
            return

        start = self.dt_ts_start.dateTime().toPyDateTime()
        end = self.dt_ts_end.dateTime().toPyDateTime()
        if end <= start:
            QMessageBox.warning(self, "GeoBridge", "End must be after start.")
            return

        # Cancelling is non-blocking (it just flags the task); any results
        # it still emits after this point are discarded by the sender()
        # checks in the _on_ts_* slots below, since self._ts_task will
        # already point at the new task by then. Guarded because QGIS's
        # task manager deletes a task's underlying C++ object once it's
        # finished — self._ts_task can be a dangling wrapper by the time a
        # later click reaches this line, and calling .cancel() on it raises
        # "wrapped C/C++ object ... has been deleted" instead of just
        # being a safe no-op.
        method = self._ts_method()
        if method == "zarr":
            if not (gb_wrapper.is_core_available() and gb_wrapper.is_zarr_extra_available()):
                QMessageBox.warning(
                    self, "GeoBridge",
                    "Full history needs geobridge[zarr] installed — use the "
                    "\"Install dependencies\" button on the API Key tab.",
                )
                return
            if not gb_wrapper.is_authenticated():
                QMessageBox.warning(
                    self, "GeoBridge",
                    "Full history needs an authenticated CDS API key — save "
                    "one on the API Key tab first.",
                )
                return

        if self._ts_task is not None:
            try:
                self._ts_task.cancel()
            except RuntimeError:
                pass

        lon, lat = self._ts_point

        self.btn_ts_download_csv.setEnabled(False)
        self.plot_ts.set_samples([])
        self.lbl_ts_status.setText("Fetching…")
        if method == "zarr":
            # One bulk read, not a per-step loop — nothing to report
            # incremental progress on, so show a busy indicator instead.
            self.progress_ts.setRange(0, 0)
        else:
            self.progress_ts.setRange(0, 100)
            self.progress_ts.setValue(0)
        self.progress_ts.setVisible(True)

        params = {
            "dataset": self._current_match.dataset_id,
            "variable": self._current_match.variable,
            "lon": lon,
            "lat": lat,
            "start": start,
            "end": end,
        }
        if method == "zarr":
            params["chunking"] = None
        else:
            params["step_days"] = self.cmb_ts_step.currentData()

        self._ts_task = TimeSeriesTask(
            f"GeoBridge: time series at ({lon:.3f}, {lat:.3f})", method, params
        )
        self._ts_task.progressChanged.connect(self._on_ts_progress)
        self._ts_task.taskCompleted.connect(self._on_ts_finished_ok)
        self._ts_task.taskTerminated.connect(self._on_ts_finished_err)
        QgsApplication.taskManager().addTask(self._ts_task)

    def _on_ts_progress(self, progress: float):
        if self.sender() is not self._ts_task:
            return  # stale signal from a task superseded by a newer click
        if self.progress_ts.maximum() > 0:  # ignore for the zarr busy indicator
            self.progress_ts.setValue(int(progress))

    def _on_ts_finished_ok(self):
        if self.sender() is not self._ts_task:
            return
        self.progress_ts.setVisible(False)
        self.progress_ts.setRange(0, 100)
        self._ts_samples = self._ts_task.samples or []
        self._ts_task = None  # done — nothing left to ever .cancel()
        # Not descriptor.colormap['unit'] — that's dataset-level (only the
        # dataset's *first* variable's unit, e.g. reanalysis_era5_single_
        # levels' is "blh"/"m"), wrong for whichever variable is actually
        # selected. gb_wrapper.variable_unit() looks up this specific one.
        unit = ""
        if self._current_match is not None:
            unit = gb_wrapper.variable_unit(
                self._current_match.dataset_id, self._current_match.variable
            )
        self.plot_ts.set_samples(self._ts_samples, unit=unit)
        self.lbl_ts_status.setText(f"Done: {len(self._ts_samples)} point(s).")
        self.btn_ts_download_csv.setEnabled(bool(self._ts_samples))

    def _on_ts_finished_err(self):
        if self.sender() is not self._ts_task:
            return  # a superseded task's late cancellation notice — ignore
        self.progress_ts.setVisible(False)
        self.progress_ts.setRange(0, 100)
        exc = self._ts_task.exception
        self._ts_task = None  # done — nothing left to ever .cancel()
        self.lbl_ts_status.setText(
            "Cancelled." if exc is None else f"Time series failed: {exc}"
        )

    def _on_ts_download_csv_clicked(self):
        if not self._ts_samples:
            return
        default_dir = QSettings().value(SETTINGS_KEY_LAST_TS_CSV_DIR, "", type=str)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save time series as CSV", default_dir, "CSV files (*.csv)"
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow(["time", "value"])
            for sample in self._ts_samples:
                value = "" if sample.value is None else sample.value
                writer.writerow([sample.time.isoformat(), value])
        QSettings().setValue(SETTINGS_KEY_LAST_TS_CSV_DIR, os.path.dirname(path))
        self.lbl_ts_status.setText(f"Saved: {path}")

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def _on_project_cleared(self):
        """Called when the user creates/opens a new project — layers are gone."""
        self._stop_play()
        self._prefetch_timer.stop()
        self._layer_ids = []
        self._group = None
        self.lbl_current_time.setText("-")
        self._needs_reload = False  # nothing to reload; state is simply reset
        self._refresh_aoi_layer_combo()

    def _fit_tab_widget(self):
        """Make the tabWidget fill the dialog above a bottom strip that holds
        the Close button. The .ui gives the tabWidget a fixed geometry and a
        dialog layout didn't take on this Qt build, so we place both directly
        on every resize."""
        margin = 6
        bottom_strip = 42  # room for the Close button below the tabs
        w, h = self.width(), self.height()
        self.tabWidget.setGeometry(
            margin, margin,
            max(0, w - 2 * margin),
            max(0, h - 2 * margin - bottom_strip),
        )
        if hasattr(self, "btn_close") and hasattr(self, "btn_help"):
            gap = 10
            cw, ch = self.btn_close.width(), self.btn_close.height()
            hw, hh = self.btn_help.width(), self.btn_help.height()
            total_w = hw + gap + cw
            start_x = (w - total_w) // 2      # pair centred along the bottom
            y = h - margin - max(ch, hh)
            help_x = max(margin, start_x)
            close_x = help_x + hw + gap
            self.btn_help.move(help_x, max(margin, y))
            self.btn_close.move(close_x, max(margin, y))
            self.btn_help.raise_()
            self.btn_close.raise_()

    def _apply_tab_size(self, index):
        """Resize the dialog to the active tab's size, keeping it fully on
        screen so the title-bar close button is always reachable.

        Only the current page drives the minimum size (others -> Ignored),
        so the small API Key tab can shrink below the wide Browse tab.
        """
        for i in range(self.tabWidget.count()):
            page = self.tabWidget.widget(i)
            if page is None:
                continue
            policy = (
                QtWidgets.QSizePolicy.Preferred if i == index
                else QtWidgets.QSizePolicy.Ignored
            )
            page.setSizePolicy(policy, policy)

        size = self._tab_sizes.get(self.tabWidget.widget(index), self._default_size)
        if size is not None:
            pos = self.pos()
            self.resize(size)
            # Clamp the top-left so the whole window (and its close button)
            # stays within the screen's available area.
            avail = None
            try:
                avail = self.screen().availableGeometry()
            except Exception:
                app_screen = QtWidgets.QApplication.primaryScreen()
                if app_screen is not None:
                    avail = app_screen.availableGeometry()
            if avail is not None:
                x = min(pos.x(), avail.right() - size.width())
                y = min(pos.y(), avail.bottom() - size.height())
                x = max(x, avail.left())
                y = max(y, avail.top())
                self.move(x, y)
        self._fit_tab_widget()

    def _on_tab_changed(self, index):
        self._apply_tab_size(index)

    def resizeEvent(self, event):
        super(GeoBridgePluginDialog, self).resizeEvent(event)
        self._fit_tab_widget()

    def showEvent(self, event):
        super(GeoBridgePluginDialog, self).showEvent(event)
        self._refresh_dependency_banner()
        self._refresh_aoi_layer_combo()
        if gb_wrapper.is_core_available():
            self.browse_tab.refresh_datasets()
        self._apply_tab_size(self.tabWidget.currentIndex())

    def reject(self):
        self._stop_play()
        self._prefetch_timer.stop()
        self.btn_pick_point.setChecked(False)
        super(GeoBridgePluginDialog, self).reject()

    def closeEvent(self, event):
        self._stop_play()
        self._prefetch_timer.stop()
        self.btn_pick_point.setChecked(False)
        super(GeoBridgePluginDialog, self).closeEvent(event)

    def cleanup(self):
        """Remove all plugin-created layers/groups. Called from plugin.unload()."""
        self._stop_play()
        self._prefetch_timer.stop()
        if self._ts_task is not None:
            # Same guard as _start_ts_fetch(): if a previous fetch already
            # finished, QGIS may have deleted its underlying C++ object
            # already, and cleanup() is called from plugin.unload() — the
            # one place an uncaught exception here would abort Plugin
            # Reloader's reload sequence right after it removes the old
            # toolbar icon/menu action, before ever re-registering the new
            # one, leaving the plugin looking like it vanished.
            try:
                self._ts_task.cancel()
            except RuntimeError:
                pass
        if self._aoi_tool is not None:
            try:
                self._aoi_tool.extentChanged.disconnect(self._on_aoi_extent_drawn)
            except (TypeError, RuntimeError):
                # TypeError: signal was never connected. RuntimeError:
                # underlying C++ map tool object already deleted.
                pass
            try:
                self._aoi_tool.deactivated.disconnect(self._on_aoi_tool_deactivated)
            except (TypeError, RuntimeError):
                pass
        if self._ts_tool is not None:
            try:
                self._ts_tool.canvasClicked.disconnect(self._on_ts_point_clicked)
            except (TypeError, RuntimeError):
                pass
            try:
                self._ts_tool.deactivated.disconnect(self._on_ts_tool_deactivated)
            except (TypeError, RuntimeError):
                pass
        try:
            QgsProject.instance().cleared.disconnect(self._on_project_cleared)
        except (TypeError, RuntimeError):
            pass

        try:
            root = QgsProject.instance().layerTreeRoot()
        except Exception:
            return

        for layer_id in self._layer_ids:
            if QgsProject.instance().mapLayer(layer_id):
                QgsProject.instance().removeMapLayer(layer_id)

        if self._group is not None:
            try:
                root.removeChildNode(self._group)
            except RuntimeError:
                # Underlying C++ layer-tree node already deleted by QGIS.
                pass

        self._layer_ids = []
        self._group = None
