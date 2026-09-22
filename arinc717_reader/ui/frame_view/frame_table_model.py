"""Qt table model over the FrameStore (design spec §8).

Rows are word addresses (1..WPS), columns are SF1..SF4.  The model only
formats values for display; changing the representation never touches the
canonical frame data.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from ...domain.frame import SUBFRAME_COUNT
from ..common import monospace_font
from ..representation import format_word


class FrameTableModel(QAbstractTableModel):
    def __init__(self, frame_store, parent=None):
        super().__init__(parent)
        self._store = frame_store
        self._representation = "HEX"
        self._font = monospace_font()
        frame_store.subscribe(self._on_store_event)

    # -- store events -------------------------------------------------------

    def _on_store_event(self, event: dict) -> None:
        if event.get("type") == "frame":
            self.beginResetModel()
            self.endResetModel()
        elif event.get("type") == "word":
            index = self.index(event["word"] - 1, event["subframe"] - 1)
            if index.isValid():
                self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole])

    # -- representation -----------------------------------------------------

    @property
    def representation(self) -> str:
        return self._representation

    def set_representation(self, representation: str) -> None:
        if representation == self._representation:
            return
        self._representation = representation
        rows = self.rowCount()
        if rows:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(rows - 1, SUBFRAME_COUNT - 1),
                [Qt.ItemDataRole.DisplayRole],
            )

    # -- QAbstractTableModel ------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid() or self._store.frame is None:
            return 0
        return self._store.frame.wps

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else SUBFRAME_COUNT

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        frame = self._store.frame
        if frame is None or not index.isValid():
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            value = frame.subframes[index.column()][index.row()]
            return format_word(value, self._representation)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.FontRole:
            return self._font
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return f"SF{section + 1}"
        return f"{section + 1:03d}"
