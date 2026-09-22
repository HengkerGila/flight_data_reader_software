"""Qt table model over the EngineeringStore (design spec §20)."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ...domain.engineering import STATUS_OUT_OF_RANGE, STATUS_VALID, format_engineering_value
from ...domain.parameter import TYPE_ANALOG_SIGNED, TYPE_ANALOG_UNSIGNED, TYPE_BCD
from ..common import COLOR_ERROR, COLOR_WARNING, monospace_font

COLUMNS = (
    "Mnemonic",
    "Description",
    "Type",
    "Occ",
    "SF",
    "Raw",
    "Decimal",
    "Resolution",
    "Offset",
    "Engineering",
    "Unit",
    "Source",
    "Status",
)
MONO_COLUMNS = {5, 6, 9}


class EngineeringTableModel(QAbstractTableModel):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._font = monospace_font()
        self._parameters_by_id = {}
        self._rebuild_lookup()
        ctx.engineering_store.subscribe(self._on_store_event)
        ctx.dataframe_store.subscribe(self._on_store_event)

    def _on_store_event(self, event: dict) -> None:
        if event.get("type") == "dataframe":
            self._rebuild_lookup()
        self.beginResetModel()
        self.endResetModel()

    def _rebuild_lookup(self) -> None:
        dataframe = self._ctx.dataframe_store.dataframe
        self._parameters_by_id = (
            {p.id: p for p in dataframe.parameters} if dataframe else {}
        )

    def value_at(self, row: int):
        value = self._ctx.engineering_store.values[row]
        return value, self._parameters_by_id.get(value.parameter_id)

    # -- QAbstractTableModel ------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._ctx.engineering_store.values)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return section + 1
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        value, parameter = self.value_at(index.row())
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            has_conversion = parameter is not None and parameter.parameter_type in (
                TYPE_ANALOG_SIGNED,
                TYPE_ANALOG_UNSIGNED,
                TYPE_BCD,
            )
            if column == 0:
                return value.parameter_name
            if column == 1:
                return parameter.description if parameter else ""
            if column == 2:
                return parameter.parameter_type if parameter else "?"
            if column == 3:
                return str(value.occurrence_index)
            if column == 4:
                return "—" if value.subframe is None else str(value.subframe)
            if column == 5:
                return value.raw_bits or "—"
            if column == 6:
                return "—" if value.decoded_decimal is None else str(value.decoded_decimal)
            if column == 7:
                return (
                    format(parameter.conversion.resolution, "g")
                    if has_conversion
                    else "—"
                )
            if column == 8:
                return (
                    format(parameter.conversion.offset, "g") if has_conversion else "—"
                )
            if column == 9:
                return format_engineering_value(value.engineering_value)
            if column == 10:
                return value.unit or "—"
            if column == 11:
                if parameter and parameter.provenance:
                    return parameter.provenance.source_type or ""
                return self._source_name()
            if column == 12:
                return value.status
        if role == Qt.ItemDataRole.FontRole and column in MONO_COLUMNS:
            return self._font
        if role == Qt.ItemDataRole.ForegroundRole and column == 12:
            if value.status == STATUS_OUT_OF_RANGE:
                return COLOR_WARNING
            if value.status != STATUS_VALID:
                return COLOR_ERROR
        return None

    def _source_name(self) -> str:
        dataframe = self._ctx.dataframe_store.dataframe
        return dataframe.metadata.source_type if dataframe else ""
