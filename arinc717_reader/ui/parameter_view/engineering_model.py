"""Qt table model over the EngineeringStore (design spec §20).

A live stream rewrites the store once per subframe.  When the set of samples
(parameter, occurrence, subframe) is unchanged the model refreshes its cells
in place, so the view keeps its selection, scroll position and column
widths; only a change of shape (another dataframe, a parameter added or
removed) resets the model, and the page then restores the selection by
sample key.
"""

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
(
    COL_MNEMONIC,
    COL_DESCRIPTION,
    COL_TYPE,
    COL_OCCURRENCE,
    COL_SUBFRAME,
    COL_RAW,
    COL_DECIMAL,
    COL_RESOLUTION,
    COL_OFFSET,
    COL_ENGINEERING,
    COL_UNIT,
    COL_SOURCE,
    COL_STATUS,
) = range(len(COLUMNS))
MONO_COLUMNS = {COL_RAW, COL_DECIMAL, COL_ENGINEERING}

# Initial column widths in pixels.  They are fixed rather than fitted to the
# contents so the table holds still while values change; the user drags a
# header edge to adjust one.  Description takes whatever width is left.
DEFAULT_COLUMN_WIDTHS = {
    COL_MNEMONIC: 170,
    COL_TYPE: 120,
    COL_OCCURRENCE: 44,
    COL_SUBFRAME: 40,
    COL_RAW: 120,
    COL_DECIMAL: 90,
    COL_RESOLUTION: 90,
    COL_OFFSET: 80,
    COL_ENGINEERING: 110,
    COL_UNIT: 60,
    COL_SOURCE: 80,
    COL_STATUS: 150,
}
STRETCH_COLUMN = COL_DESCRIPTION

# Identity of one table row: the sample it shows.
SampleKey = tuple[str, int, int | None]


def sample_key(value) -> SampleKey:
    return (value.parameter_id, value.occurrence_index, value.subframe)


class EngineeringTableModel(QAbstractTableModel):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._font = monospace_font()
        self._dataframe = None
        self._parameters_by_id = {}
        # Keys of the rows the views currently know about.  Updated only
        # inside a reset, so during ``modelAboutToBeReset`` they still
        # describe the old rows (the page reads them to remember its
        # selection) and after ``modelReset`` the new ones.
        self._keys: list[SampleKey] = [sample_key(v) for v in ctx.engineering_store.values]
        self._rebuild_lookup()
        ctx.engineering_store.subscribe(self._on_store_event)
        ctx.dataframe_store.subscribe(self._on_store_event)

    def _on_store_event(self, event: dict) -> None:
        if event.get("type") not in ("engineering", "dataframe"):
            return
        self._rebuild_lookup()
        keys = [sample_key(v) for v in self._ctx.engineering_store.values]
        if keys == self._keys:
            # Same samples (a live subframe, an edited word, an edited
            # parameter): refresh in place so the view keeps its selection,
            # scroll position and column widths.
            if keys:
                self.dataChanged.emit(
                    self.index(0, 0), self.index(len(keys) - 1, len(COLUMNS) - 1), []
                )
            return
        self.beginResetModel()
        self._keys = keys
        self.endResetModel()

    def _rebuild_lookup(self) -> None:
        dataframe = self._ctx.dataframe_store.dataframe
        if dataframe is self._dataframe:
            return
        self._dataframe = dataframe
        self._parameters_by_id = (
            {p.id: p for p in dataframe.parameters} if dataframe else {}
        )

    def value_at(self, row: int):
        value = self._ctx.engineering_store.values[row]
        return value, self._parameters_by_id.get(value.parameter_id)

    def key_at(self, row: int) -> SampleKey | None:
        """The sample shown in ``row`` (of the rows the views currently hold)."""
        if 0 <= row < len(self._keys):
            return self._keys[row]
        return None

    def row_for_key(self, key: SampleKey) -> int | None:
        try:
            return self._keys.index(key)
        except ValueError:
            return None

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
            if column == COL_MNEMONIC:
                return value.parameter_name
            if column == COL_DESCRIPTION:
                return parameter.description if parameter else ""
            if column == COL_TYPE:
                return parameter.parameter_type if parameter else "?"
            if column == COL_OCCURRENCE:
                return str(value.occurrence_index)
            if column == COL_SUBFRAME:
                return "—" if value.subframe is None else str(value.subframe)
            if column == COL_RAW:
                return value.raw_bits or "—"
            if column == COL_DECIMAL:
                return "—" if value.decoded_decimal is None else str(value.decoded_decimal)
            if column == COL_RESOLUTION:
                return (
                    format(parameter.conversion.resolution, "g")
                    if has_conversion
                    else "—"
                )
            if column == COL_OFFSET:
                return (
                    format(parameter.conversion.offset, "g") if has_conversion else "—"
                )
            if column == COL_ENGINEERING:
                return format_engineering_value(value.engineering_value)
            if column == COL_UNIT:
                return value.unit or "—"
            if column == COL_SOURCE:
                if parameter and parameter.provenance:
                    return parameter.provenance.source_type or ""
                return self._source_name()
            if column == COL_STATUS:
                return value.status
        if role == Qt.ItemDataRole.FontRole and column in MONO_COLUMNS:
            return self._font
        if role == Qt.ItemDataRole.ForegroundRole and column == COL_STATUS:
            if value.status == STATUS_OUT_OF_RANGE:
                return COLOR_WARNING
            if value.status != STATUS_VALID:
                return COLOR_ERROR
        return None

    def _source_name(self) -> str:
        dataframe = self._ctx.dataframe_store.dataframe
        return dataframe.metadata.source_type if dataframe else ""
