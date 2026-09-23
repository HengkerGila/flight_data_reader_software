"""Qt table model over the FrameStore (design spec §8).

Rows are word addresses (1..WPS), columns are SF1..SF4.  The model only
formats values for display; changing the representation never touches the
canonical frame data.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ...domain.frame import SUBFRAME_COUNT
from ...sources.serial.subframe_assembler import (
    SF_INVALID,
    SF_LATE,
    SF_MISSING,
    SF_PENDING,
)
from ..common import monospace_font
from ..representation import format_word

# Live-stream cell tints (spec v2 §26T): received cells keep the normal
# background, and so do pending cells, which show the previous frame's
# words until their subframe arrives (one column changes per second); the
# problem states are visually distinct in light and dark themes.
STATE_TINTS = {
    SF_MISSING: QColor(230, 140, 30, 110),
    SF_INVALID: QColor(210, 60, 60, 110),
    SF_LATE: QColor(60, 120, 210, 90),
}
INVALID_WORD_TINT = QColor(210, 60, 60, 170)
PLACEHOLDER = "----"


class FrameTableModel(QAbstractTableModel):
    def __init__(self, frame_store, parent=None):
        super().__init__(parent)
        self._store = frame_store
        self._representation = "HEX"
        self._font = monospace_font()
        self._last_rows = 0
        frame_store.subscribe(self._on_store_event)

    # -- store events -------------------------------------------------------

    def _on_store_event(self, event: dict) -> None:
        if event.get("type") == "frame":
            rows = self.rowCount()
            if rows and rows == self._last_rows:
                # Same shape (a live subframe arrival, a regenerated frame):
                # refresh in place so the view keeps its selection and scroll
                # position instead of resetting four times a second (§26T).
                self.dataChanged.emit(self.index(0, 0), self.index(rows - 1, SUBFRAME_COUNT - 1), [])
                self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, SUBFRAME_COUNT - 1)
            else:
                self.beginResetModel()
                self.endResetModel()
            self._last_rows = rows
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
        states = self._store.subframe_states
        state = states[index.column()] if states else None
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() + 1 in self._store.blank_subframes:
                return PLACEHOLDER  # no data has ever arrived for this subframe
            value = frame.subframes[index.column()][index.row()]
            return format_word(value, self._representation)
        if role == Qt.ItemDataRole.BackgroundRole and states:
            cell = (index.column() + 1, index.row() + 1)
            if cell in self._store.invalid_words:
                return INVALID_WORD_TINT
            tint = STATE_TINTS.get(state)
            return tint
        if role == Qt.ItemDataRole.ToolTipRole and state is not None:
            if state == SF_PENDING:
                return f"SF{index.column() + 1}: pending — showing the previous frame's words"
            if state == SF_MISSING:
                return f"SF{index.column() + 1}: missing in this frame — showing the previous frame's words"
            return f"SF{index.column() + 1}: {state}"
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.FontRole:
            return self._font
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            states = self._store.subframe_states
            if states:
                return f"SF{section + 1} · {states[section].lower()}"
            return f"SF{section + 1}"
        return f"{section + 1:03d}"
