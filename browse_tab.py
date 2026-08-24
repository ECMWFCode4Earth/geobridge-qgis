# -*- coding: utf-8 -*-
"""
browse_tab
~~~~~~~~~~

The "Browse by Variable" tab: pick a dataset, then narrow down
product_type / variable / year / month / day / time through cascading
lists whose invalid options grey out live, driven entirely by the CDS
constraints (see gb_wrapper.field_states_from_constraints).

Built entirely in code (no .ui entry) and added to the dialog's tab widget
at runtime — the rest of the dialog uses absolute-positioned .ui layouts,
but a cascade of six lists that show/hide per dataset wants a real layout
manager, so this widget carries its own.

All the decision logic lives in gb_wrapper as pure, unit-tested functions;
this file is only the Qt glue that paints their output and reads the user's
selections back. It emits `previewRequested` with a payload dict when the
user asks to preview the current selection as a WMTS layer; the dialog
connects that to its existing WMTS layer-building path.
"""

from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import gb_wrapper
from . import time_utils

# Cascade order is only cosmetic (upstream-ish first); the grey-out maths
# is order-independent because each field is evaluated against all the
# others jointly.
FIELDS = ("product_type", "variable", "year", "month", "day", "time")

# Date-ish fields allow multiple picks (a CDS download naturally takes many
# years/months/days at once). variable/product_type stay single-select — one
# variable per GeoTIFF, and a single product type per request.
MULTI_FIELDS = ("year", "month", "day", "time")

# Human labels for the column headers.
FIELD_LABELS = {
    "product_type": "Product type",
    "variable": "Variable",
    "year": "Year",
    "month": "Month",
    "day": "Day",
    "time": "Time",
}

# Fields needed to pin a single timestamp for a WMTS preview.
_DATETIME_FIELDS = ("year", "month", "day")

# Keep a list item's selection visibly blue even when the list doesn't have
# keyboard focus — Qt otherwise fades it to grey the moment you click another
# list, which made the current pick hard to spot while scrolling.
_LIST_QSS = (
    "QListWidget::item:selected { background:#2f6fed; color:white; }"
    "QListWidget::item:selected:!active { background:#2f6fed; color:white; }"
)


class BrowseTab(QWidget):
    """Dataset → variable → date cascade with live constraint-driven greying."""

    previewRequested = pyqtSignal(object)   # payload dict (see _on_preview_clicked)
    downloadRequested = pyqtSignal(object)  # payload dict (see _on_download_clicked)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._constraints: list = []
        self._form_universes: dict = {}  # field -> [values] fallback when no constraints
        self._labels: dict = {}          # field -> {value: display_label}
        self._dataset_id = None
        self._descriptor = None
        self._datasets: list = []        # cached discover_all() result
        self._updating = False           # re-entrancy guard for repaint

        self._dataset_title = None       # human title of the picked dataset
        self._lists: dict = {}           # field -> QListWidget
        self._columns: dict = {}         # field -> container QWidget (to hide)
        self._sel_labels: dict = {}      # field -> QLabel echoing its current pick

        self._build_ui()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        outer = QVBoxLayout(self)

        self.lbl_hint = QLabel(
            "Pick a dataset, then narrow down the variable and date. "
            "Greyed-out options have no data for the current selection."
        )
        self.lbl_hint.setWordWrap(True)
        outer.addWidget(self.lbl_hint)

        # Running summary of the whole current selection — always visible at
        # the top, so scrolling the lists below never hides what's chosen.
        self.lbl_summary = QLabel("Nothing selected yet.")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_summary.setStyleSheet("font-weight:bold; padding:2px 0;")
        outer.addWidget(self.lbl_summary)

        # Two columns: the dataset + cascade selectors on the left, and the
        # area-of-interest + action panel on the right.
        body = QHBoxLayout()
        body.setSpacing(16)
        left = QVBoxLayout()
        right = QVBoxLayout()
        right.setSpacing(16)
        right.setContentsMargins(4, 0, 0, 0)

        # --- LEFT: dataset picker ---
        ds_header = QHBoxLayout()
        ds_header.addWidget(QLabel("Dataset:"))
        self.txt_dataset_filter = QLineEdit()
        self.txt_dataset_filter.setPlaceholderText("Filter datasets…")
        self.txt_dataset_filter.setClearButtonEnabled(True)
        self.txt_dataset_filter.textChanged.connect(self._apply_dataset_filter)
        ds_header.addWidget(self.txt_dataset_filter, 1)
        left.addLayout(ds_header)

        self.list_dataset = QListWidget()
        self.list_dataset.setMaximumHeight(90)
        self.list_dataset.setStyleSheet(_LIST_QSS)
        self.list_dataset.currentItemChanged.connect(self._on_dataset_changed)
        left.addWidget(self.list_dataset)

        # Blue echo of the chosen dataset, matching the per-column labels below.
        self.lbl_dataset_sel = QLabel("—")
        self.lbl_dataset_sel.setWordWrap(True)
        self.lbl_dataset_sel.setStyleSheet("color:#2f6fed;")
        left.addWidget(self.lbl_dataset_sel)

        # --- LEFT: the six cascade columns ---
        grid = QGridLayout()
        for i, field in enumerate(FIELDS):
            column = QWidget()
            col_layout = QVBoxLayout(column)
            col_layout.setContentsMargins(0, 0, 0, 0)
            col_layout.addWidget(QLabel(FIELD_LABELS[field]))

            # The variable list is long (~260 for ERA5) — give it a filter box.
            if field == "variable":
                self.txt_var_filter = QLineEdit()
                self.txt_var_filter.setPlaceholderText("Filter variables…")
                self.txt_var_filter.setClearButtonEnabled(True)
                self.txt_var_filter.textChanged.connect(self._apply_variable_filter)
                col_layout.addWidget(self.txt_var_filter)

            lst = QListWidget()
            mode = (
                QListWidget.ExtendedSelection
                if field in MULTI_FIELDS
                else QListWidget.SingleSelection
            )
            lst.setSelectionMode(mode)
            lst.setMaximumHeight(170)
            lst.setStyleSheet(_LIST_QSS)
            # A user click anywhere in the cascade re-runs the grey-out.
            lst.itemSelectionChanged.connect(self._on_selection_changed)
            col_layout.addWidget(lst)

            # Text echo of this column's current pick, so it's readable even
            # after the blue highlight scrolls out of view.
            sel_label = QLabel("—")
            sel_label.setWordWrap(True)
            sel_label.setStyleSheet("color:#2f6fed;")
            col_layout.addWidget(sel_label)

            self._lists[field] = lst
            self._columns[field] = column
            self._sel_labels[field] = sel_label
            # product_type/variable get a wider column than year/month/day/time.
            span = 3 if field in ("product_type", "variable") else 2
            grid.addWidget(column, 0 if i < 2 else 1, self._grid_col(i), 1, span)
        left.addLayout(grid)
        left.addStretch(1)

        # --- RIGHT: area of interest (optional) — mirrors the Search tab's
        #     controls; the dialog wires these to its shared AOI handlers. ---
        aoi_box = QGroupBox("Area of interest (optional)")
        aoi_layout = QVBoxLayout(aoi_box)
        aoi_layout.setSpacing(10)
        aoi_layout.setContentsMargins(12, 14, 12, 14)
        self.btn_draw_aoi = QPushButton("Draw on map")
        aoi_layout.addWidget(self.btn_draw_aoi)
        aoi_layout.addWidget(QLabel("or from layer:"))
        self.cmb_aoi_layer = QComboBox()
        aoi_layout.addWidget(self.cmb_aoi_layer)
        self.btn_use_layer_extent = QPushButton("Use extent")
        aoi_layout.addWidget(self.btn_use_layer_extent)
        self.lbl_aoi_bbox = QLabel("Whole globe (no area set).")
        self.lbl_aoi_bbox.setWordWrap(True)
        aoi_layout.addWidget(self.lbl_aoi_bbox)
        self.lbl_aoi_warning = QLabel("")
        self.lbl_aoi_warning.setWordWrap(True)
        self.lbl_aoi_warning.setStyleSheet("color:#c0392b;")
        self.lbl_aoi_warning.setVisible(False)
        aoi_layout.addWidget(self.lbl_aoi_warning)
        right.addWidget(aoi_box)

        # --- RIGHT: selected-request preview + actions (stacked) ---
        box = QGroupBox("Selection")
        box_layout = QVBoxLayout(box)
        box_layout.setSpacing(10)
        box_layout.setContentsMargins(12, 14, 12, 14)
        self.lbl_request = QLabel("Nothing selected yet.")
        self.lbl_request.setWordWrap(True)
        self.lbl_request.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box_layout.addWidget(self.lbl_request)

        self.btn_preview = QPushButton("Preview as WMTS layer")
        self.btn_preview.setEnabled(False)
        self.btn_preview.setMinimumHeight(32)
        self.btn_preview.clicked.connect(self._on_preview_clicked)
        box_layout.addWidget(self.btn_preview)
        self.btn_download = QPushButton("Download GeoTIFF")
        self.btn_download.setEnabled(False)
        self.btn_download.setMinimumHeight(32)
        self.btn_download.clicked.connect(self._on_download_clicked)
        box_layout.addWidget(self.btn_download)
        self.btn_clear = QPushButton("Clear selection")
        self.btn_clear.setMinimumHeight(32)
        self.btn_clear.clicked.connect(self._on_clear_clicked)
        box_layout.addWidget(self.btn_clear)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        box_layout.addWidget(self.lbl_status)
        right.addWidget(box)
        right.addStretch(1)

        # Left flexes; right stays a fixed-width sidebar.
        left_container = QWidget()
        left_container.setLayout(left)
        right_container = QWidget()
        right_container.setLayout(right)
        right_container.setMinimumWidth(300)
        right_container.setMaximumWidth(360)
        body.addWidget(left_container, 1)
        body.addWidget(right_container, 0)
        outer.addLayout(body)

    @staticmethod
    def _grid_col(i: int) -> int:
        """Column index within the row for the i-th field (2 per top row,
        4 per bottom row), matching the span widths set in _build_ui."""
        if i < 2:
            return i * 3          # product_type at 0, variable at 3
        return (i - 2) * 2        # year/month/day/time at 0,2,4,6

    # ------------------------------------------------------------------ #
    # Dataset list — populated lazily so the tab is cheap until first shown
    # ------------------------------------------------------------------ #

    def refresh_datasets(self):
        """(Re)populate the dataset list. Safe to call repeatedly.

        Called by the dialog when the tab is shown; needs geobridge, so it
        degrades to a hint if the library isn't installed yet.
        """
        if self._datasets:
            return  # already populated once
        try:
            datasets = gb_wrapper.discover_all()
        except gb_wrapper.GeobridgeNotInstalled:
            self.lbl_hint.setText(
                "Install geobridge first (API Key tab) to browse datasets by variable."
            )
            self.list_dataset.setEnabled(False)
            return
        except Exception as exc:  # noqa: BLE001 — surface, don't crash the tab
            self.lbl_status.setText(f"Could not list datasets: {exc}")
            return

        self._datasets = datasets
        self.list_dataset.setEnabled(True)
        self.list_dataset.blockSignals(True)
        self.list_dataset.clear()
        for ds in datasets:
            item = QListWidgetItem(getattr(ds, "title", None) or ds.id)
            item.setData(Qt.UserRole, ds.id)
            self.list_dataset.addItem(item)
        self.list_dataset.blockSignals(False)

    # ------------------------------------------------------------------ #
    # Cascade
    # ------------------------------------------------------------------ #

    def _on_dataset_changed(self, current, _previous=None):
        if current is None:
            return
        dataset_id = current.data(Qt.UserRole)
        self._dataset_id = dataset_id
        self._dataset_title = current.text()
        self.lbl_dataset_sel.setText(current.text())
        self.lbl_status.setText("Loading constraints…")
        self.btn_preview.setEnabled(False)

        try:
            self._constraints = gb_wrapper.get_constraints(dataset_id)
            self._form_universes = gb_wrapper.form_universes(dataset_id, FIELDS)
            self._descriptor = gb_wrapper.discover_one(dataset_id)
        except gb_wrapper.GeobridgeNotInstalled:
            self.lbl_status.setText("Install geobridge first (API Key tab).")
            return
        except Exception as exc:  # noqa: BLE001
            self.lbl_status.setText(f"Could not load dataset: {exc}")
            return

        # Cache display labels once per dataset (best-effort; may be empty,
        # e.g. ERA5's grouped variable widget — we fall back to the raw value).
        self._labels = {}
        for field in FIELDS:
            try:
                self._labels[field] = gb_wrapper.value_labels(dataset_id, field)
            except Exception:  # noqa: BLE001
                self._labels[field] = {}

        # Neither source has anything to browse (no constraints AND no
        # enumerable form fields — e.g. a purely free-text/date dataset).
        if not self._constraints and not self._form_universes:
            self.lbl_status.setText(
                "This dataset exposes no selectable variables or constraints "
                "here. Try the Search tab instead."
            )
            for field in FIELDS:
                self._columns[field].setVisible(False)
            return

        # Fresh dataset -> clear any previous selection, show only the fields
        # this dataset actually has, then run the first cascade pass.
        for field in FIELDS:
            lst = self._lists[field]
            lst.blockSignals(True)
            lst.clearSelection()
            lst.blockSignals(False)
        self._recompute(initial=True)

    def _on_selection_changed(self):
        if self._updating:
            return
        self._recompute()

    def _read_selection(self) -> dict:
        """Current picks per field, omitting the unselected.

        Multi-select fields (year/month/day/time) yield a list of values;
        single-select fields (variable/product_type) yield a bare string.
        Both shapes are accepted downstream by the cascade helpers.
        """
        selection = {}
        for field, lst in self._lists.items():
            values = self._selected_values(lst)
            if not values:
                continue
            selection[field] = values if field in MULTI_FIELDS else values[0]
        return selection

    @staticmethod
    def _selected_values(lst: QListWidget) -> list:
        """All selected values in list order (0, 1, or many)."""
        return [it.data(Qt.UserRole) for it in lst.selectedItems()]

    def _recompute(self, initial: bool = False):
        """Repaint every field's enabled/greyed state from the constraints.

        Iterates to a fixed point: applying states can invalidate an
        earlier choice (e.g. a variable that isn't available in a newly
        picked year), which is then dropped and the pass repeats. Each pass
        only ever removes selections, so it converges in at most one round
        per field.
        """
        self._updating = True
        try:
            for _ in range(len(FIELDS) + 1):
                selection = self._read_selection()
                states = gb_wrapper.field_states_from_sources(
                    self._constraints, self._form_universes, FIELDS, selection
                )
                dropped = self._apply_states(states, initial)
                initial = False
                if not dropped:
                    break
        finally:
            self._updating = False
        self._update_preview()

    def _apply_states(self, states: dict, initial: bool) -> bool:
        """Paint each list from `states`. Returns True if a previously
        selected value had to be dropped because it is now greyed."""
        dropped_any = False
        for field in FIELDS:
            state_map = states.get(field, {})
            column = self._columns[field]
            # Hide fields this dataset doesn't use (empty universe). Only
            # decide visibility on the initial pass so it doesn't flicker.
            if initial:
                column.setVisible(bool(state_map))
            if not state_map:
                continue

            lst = self._lists[field]
            prev = set(self._selected_values(lst))  # may hold several picks
            lst.blockSignals(True)
            lst.clear()
            for value, enabled in state_map.items():
                item = QListWidgetItem(self._label_for(field, value))
                item.setData(Qt.UserRole, value)
                if not enabled:
                    item.setFlags(
                        item.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable
                    )
                lst.addItem(item)
                if value in prev and enabled:
                    item.setSelected(True)
                    lst.setCurrentItem(item)
            lst.blockSignals(False)

            # The variable list was just rebuilt from scratch — re-hide the
            # items filtered out by the search box.
            if field == "variable":
                self._apply_variable_filter()

            # If any previously-picked value is now greyed, it was dropped —
            # repaint again so downstream fields reflect the smaller selection.
            if any(not state_map.get(v, False) for v in prev):
                dropped_any = True
        return dropped_any

    def _apply_dataset_filter(self, *_args):
        """Hide dataset items that don't match the filter text (substring,
        case-insensitive). Matches against the visible title."""
        text = self.txt_dataset_filter.text().strip().lower()
        for i in range(self.list_dataset.count()):
            item = self.list_dataset.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _apply_variable_filter(self, *_args):
        """Hide variable items that don't contain the filter text (substring,
        case-insensitive). Purely visual — never changes the selection or the
        cascade, and is re-applied after every repaint of the variable list."""
        text = self.txt_var_filter.text().strip().lower()
        lst = self._lists.get("variable")
        if lst is None:
            return
        for i in range(lst.count()):
            item = lst.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _label_for(self, field: str, value: str) -> str:
        label = self._labels.get(field, {}).get(value)
        if label:
            return label
        # Fall back to a lightly prettified raw value (variables especially).
        return value.replace("_", " ") if field == "variable" else value

    def _display(self, field: str, value) -> str:
        """Human string for a pick that may be a single value or a list."""
        if isinstance(value, list):
            if len(value) > 4:
                return f"{len(value)} selected"
            return ", ".join(self._label_for(field, v) for v in value)
        return self._label_for(field, value)

    # ------------------------------------------------------------------ #
    # Selection preview + actions
    # ------------------------------------------------------------------ #

    def _current_request(self) -> dict:
        """CDS-style request dict {param: [values]} from the current picks."""
        request = {}
        for field, value in self._read_selection().items():
            request[field] = value if isinstance(value, list) else [value]
        return request

    @staticmethod
    def _is_single(value) -> bool:
        """True when a pick resolves to exactly one value (str, or 1-list)."""
        return len(value) == 1 if isinstance(value, list) else value is not None

    def _update_preview(self):
        selection = self._read_selection()

        # 1. Per-column echo labels — readable even when the highlight scrolls
        #    out of view.
        for field, label in self._sel_labels.items():
            value = selection.get(field)
            label.setText(self._display(field, value) if value else "—")

        # 2. Top running summary of the whole selection.
        self._update_summary(selection)

        if not selection:
            self.lbl_request.setText("Nothing selected yet.")
            self.btn_preview.setEnabled(False)
            self.btn_download.setEnabled(False)
            self.lbl_status.setText("")
            return

        parts = [f"dataset={self._dataset_id}"] if self._dataset_id else []
        parts += [f"{f}={self._display(f, selection[f])}" for f in FIELDS if f in selection]
        self.lbl_request.setText("  ".join(parts))

        has_wmts = bool(self._descriptor and getattr(self._descriptor, "has_wmts", False))
        has_variable = "variable" in selection

        # Download just needs at least one temporal pick (some datasets are
        # monthly and have no 'day' field at all).
        has_temporal = any(f in selection for f in MULTI_FIELDS)
        # Preview builds one WMTS timestamp — needs a single year, month AND day.
        ymd_single = all(
            f in selection and self._is_single(selection[f]) for f in _DATETIME_FIELDS
        )

        self.btn_preview.setEnabled(has_wmts and has_variable and ymd_single)
        self.btn_download.setEnabled(has_variable and has_temporal)

        if not has_variable:
            self.lbl_status.setText("Pick a variable.")
        elif not has_temporal:
            self.lbl_status.setText("Pick at least one date value.")
        elif not ymd_single and has_wmts:
            self.lbl_status.setText(
                "Ready to Download. Preview needs a single year, month and day."
            )
        elif not has_wmts:
            self.lbl_status.setText("No WMTS preview for this dataset — Download only.")
        else:
            self.lbl_status.setText("")

    def _update_summary(self, selection: dict):
        """Set the always-visible top line: dataset + each chosen field."""
        parts = []
        if self._dataset_title:
            parts.append(self._dataset_title)
        for field in FIELDS:
            value = selection.get(field)
            if value:
                parts.append(f"{FIELD_LABELS[field]}: {self._display(field, value)}")
        self.lbl_summary.setText("  ·  ".join(parts) if parts else "Nothing selected yet.")

    def _on_clear_clicked(self):
        """Wipe the whole selection, dataset included, back to a blank tab."""
        self._updating = True
        try:
            self.list_dataset.blockSignals(True)
            self.list_dataset.clearSelection()
            self.list_dataset.setCurrentItem(None)
            self.list_dataset.blockSignals(False)
            for lst in self._lists.values():
                lst.blockSignals(True)
                lst.clear()
                lst.blockSignals(False)
        finally:
            self._updating = False

        self._dataset_id = None
        self._dataset_title = None
        self._descriptor = None
        self._constraints = []
        self._form_universes = {}
        self._labels = {}

        self.txt_var_filter.clear()
        self.txt_dataset_filter.clear()
        self.lbl_dataset_sel.setText("—")
        for label in self._sel_labels.values():
            label.setText("—")
        self.lbl_summary.setText("Nothing selected yet.")
        self.lbl_request.setText("Nothing selected yet.")
        self.lbl_status.setText("")
        self.btn_preview.setEnabled(False)
        self.btn_download.setEnabled(False)

    @staticmethod
    def _one(value) -> str:
        """First value of a (possibly multi-select) pick."""
        return value[0] if isinstance(value, list) else value

    def _on_preview_clicked(self):
        selection = self._read_selection()
        try:
            time_sel = selection.get("time")
            time_value = self._one(time_sel) if time_sel else "00:00"
            datetime_iso = time_utils.iso_from_parts(
                self._one(selection["year"]),
                self._one(selection["month"]),
                self._one(selection["day"]),
                time_value,
            )
        except (KeyError, ValueError, IndexError) as exc:
            self.lbl_status.setText(f"Can't build a timestamp: {exc}")
            return

        payload = {
            "dataset_id": self._dataset_id,
            "variable": selection["variable"],
            "datetime": datetime_iso,
            "descriptor": self._descriptor,
            "request": self._current_request(),
        }
        self.previewRequested.emit(payload)

    def _on_download_clicked(self):
        selection = self._read_selection()
        if "variable" not in selection:
            self.lbl_status.setText("Pick a variable before downloading.")
            return
        payload = {
            "dataset_id": self._dataset_id,
            "variable": selection["variable"],
            "descriptor": self._descriptor,
            "request": self._current_request(),
        }
        self.downloadRequested.emit(payload)
