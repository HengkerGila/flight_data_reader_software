"""Parameters page with search/filters and full decode trace (spec §43)."""

from __future__ import annotations

from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ...domain.engineering import ALL_STATUSES, format_decode_chain
from ...domain.parameter import CANONICAL_TYPES
from ..common import monospace_font
from .engineering_model import EngineeringTableModel

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
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx

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
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.selectionModel().currentRowChanged.connect(self._row_changed)
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

    def _filters_changed(self, *_args) -> None:
        self._proxy.search = self._search.text().strip()
        self._proxy.type_filter = self._type_combo.currentText()
        self._proxy.status_filter = self._status_combo.currentText()
        self._proxy.subframe_filter = self._subframe_combo.currentText()
        self._proxy.invalidateFilter()

    def _row_changed(self, current, _previous) -> None:
        if not current.isValid():
            self._trace.setPlainText("")
            return
        source_index = self._proxy.mapToSource(current)
        value, _parameter = self._model.value_at(source_index.row())
        self._trace.setPlainText(format_decode_chain(value))
