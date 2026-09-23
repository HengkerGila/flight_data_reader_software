"""Parameters page with search/filters and full decode trace (spec §43).

Built for monitoring a live stream: values refresh in place, the selected
row (and its trace) stays selected across updates, and the columns keep
the widths they were given until the user drags a header edge.  "Show in
Frame View" (or a double-click) asks the main window to show the selected
sample's words in the Frame View's Parameter Bits panel.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...domain.engineering import ALL_STATUSES, format_decode_chain
from ...domain.parameter import CANONICAL_TYPES
from ..common import monospace_font
from .engineering_model import (
    DEFAULT_COLUMN_WIDTHS,
    STRETCH_COLUMN,
    EngineeringTableModel,
    SampleKey,
)

ALL = "All"


class ParameterFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.search = ""
        self.type_filter = ALL
        self.status_filter = ALL
        self.subframe_filter = ALL

    def filterAcceptsRow(self, source_row, source_parent):
        value, parameter = self.sourceModel().value_at(source_row)
        if self.search:
            haystack = value.parameter_name.lower()
            if parameter:
                haystack += " " + parameter.description.lower()
            if self.search.lower() not in haystack:
                return False
        if self.type_filter != ALL:
            if parameter is None or parameter.parameter_type != self.type_filter:
                return False
        if self.status_filter != ALL and value.status != self.status_filter:
            return False
        if self.subframe_filter != ALL:
            if str(value.subframe) != self.subframe_filter:
                return False
        return True


class ParametersPage(QWidget):
    # Emitted with a sample key when the user asks to see the sample's words
    # in the Frame View; the main window switches tabs and forwards it.
    show_in_frame_view = Signal(object)

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._remembered_key: SampleKey | None = None

        layout = QVBoxLayout(self)

        filters = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search mnemonic / description…")
        self._search.textChanged.connect(self._filters_changed)
        filters.addWidget(self._search, 2)

        filters.addWidget(QLabel("Type:"))
        self._type_combo = QComboBox()
        self._type_combo.addItems([ALL, *CANONICAL_TYPES])
        self._type_combo.currentTextChanged.connect(self._filters_changed)
        filters.addWidget(self._type_combo)

        filters.addWidget(QLabel("Status:"))
        self._status_combo = QComboBox()
        self._status_combo.addItems([ALL, *ALL_STATUSES])
        self._status_combo.currentTextChanged.connect(self._filters_changed)
        filters.addWidget(self._status_combo)

        filters.addWidget(QLabel("Subframe:"))
        self._subframe_combo = QComboBox()
        self._subframe_combo.addItems([ALL, "1", "2", "3", "4"])
        self._subframe_combo.currentTextChanged.connect(self._filters_changed)
        filters.addWidget(self._subframe_combo)
        filters.addSpacing(12)
        self._show_button = QPushButton("Show in Frame View")
        self._show_button.setToolTip(
            "Show the words and bits this sample is decoded from in the Frame View "
            "(double-clicking a row does the same)"
        )
        self._show_button.setEnabled(False)
        self._show_button.clicked.connect(self.request_frame_view)
        filters.addWidget(self._show_button)
        layout.addLayout(filters)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self._model = EngineeringTableModel(ctx, self)
        self._proxy = ParameterFilterProxy(self)
        self._proxy.setSourceModel(self._model)
        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Fixed, user-adjustable widths: nothing is re-measured when values
        # change, so the layout holds still during a stream.
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(STRETCH_COLUMN, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        for column, width in DEFAULT_COLUMN_WIDTHS.items():
            self._table.setColumnWidth(column, width)
        self._table.verticalHeader().setVisible(False)
        self._table.selectionModel().currentRowChanged.connect(self._row_changed)
        self._table.doubleClicked.connect(lambda _index: self.request_frame_view())
        # A model reset (the set of samples changed) drops the view's
        # selection; remember the selected sample before and re-select it
        # after.  In-place refreshes only need the trace redrawn.
        self._proxy.modelAboutToBeReset.connect(self._remember_selection)
        self._proxy.modelReset.connect(self._restore_selection)
        self._model.dataChanged.connect(self._refresh_trace)
        splitter.addWidget(self._table)

        self._trace = QPlainTextEdit()
        self._trace.setReadOnly(True)
        self._trace.setFont(monospace_font())
        self._trace.setPlaceholderText(
            "Select a parameter row to see its full decode trace."
        )
        splitter.addWidget(self._trace)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

    # -- selection ------------------------------------------------------------

    def current_key(self) -> SampleKey | None:
        """(parameter id, occurrence, subframe) of the selected row, if any."""
        index = self._table.currentIndex()
        if not index.isValid():
            return None
        return self._model.key_at(self._proxy.mapToSource(index).row())

    def select_sample(self, key: SampleKey) -> bool:
        """Select the row showing ``key``; False when it is absent or filtered out."""
        row = self._model.row_for_key(key)
        index = (
            self._proxy.mapFromSource(self._model.index(row, 0))
            if row is not None
            else QModelIndex()
        )
        if not index.isValid():
            return False
        self._table.setCurrentIndex(index)
        self._table.scrollTo(index)
        return True

    def request_frame_view(self) -> bool:
        """Ask for the selected sample in the Frame View; False when none is selected."""
        key = self.current_key()
        if key is None:
            return False
        self.show_in_frame_view.emit(key)
        return True

    def _remember_selection(self) -> None:
        self._remembered_key = self.current_key()

    def _restore_selection(self) -> None:
        key, self._remembered_key = self._remembered_key, None
        if key is None or not self.select_sample(key):
            self._trace.setPlainText("")

    # -- handlers -------------------------------------------------------------

    def _filters_changed(self, *_args) -> None:
        self._proxy.search = self._search.text().strip()
        self._proxy.type_filter = self._type_combo.currentText()
        self._proxy.status_filter = self._status_combo.currentText()
        self._proxy.subframe_filter = self._subframe_combo.currentText()
        self._proxy.invalidateFilter()

    def _row_changed(self, current, _previous) -> None:
        self._show_button.setEnabled(current.isValid())
        if not current.isValid():
            self._trace.setPlainText("")
            return
        self._show_trace(self._proxy.mapToSource(current).row())

    def _refresh_trace(self, *_args) -> None:
        index = self._table.currentIndex()
        if index.isValid():
            self._show_trace(self._proxy.mapToSource(index).row())

    def _show_trace(self, source_row: int) -> None:
        value, _parameter = self._model.value_at(source_row)
        text = format_decode_chain(value)
        if text == self._trace.toPlainText():
            return
        # Keep the reader's place in the trace while its numbers change.
        scrollbar = self._trace.verticalScrollBar()
        position = scrollbar.value()
        self._trace.setPlainText(text)
        scrollbar.setValue(position)
