"""Frame View page (design spec §8, §42).

The Word Inspector follows the table's *current* cell, so a mouse click and
the arrow keys behave the same; double-click opens the manual edit dialog.
The row of the current cell is tinted so the word address can be read across
the four subframes.  Above the inspector, the Parameter Bits panel shows the
words of a sample chosen on the Parameters page (one row per segment) and
those words are outlined in the grid.  Representation switching is
display-only.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPen

from ...services import ServiceError
from ..representation import REPRESENTATIONS
from .frame_table_model import FrameTableModel
from .parameter_bits import ParameterBitsWidget
from .word_edit_dialog import WordEditDialog
from .word_inspector import WordInspectorWidget

FRAME_FILE_FILTER = "Frame files (*.json);;All files (*)"

# Opacity of the tint laid over the other cells of the current row.  Low
# enough to keep the live-state tints (missing / invalid / late) and the text
# readable underneath, in light and dark themes alike.
ROW_TINT_ALPHA = 55
LINKED_CELL_BORDER = 2  # outline width of the words shown in the Parameter Bits panel


class RowHighlightDelegate(QStyledItemDelegate):
    """Tints every cell of the current cell's row with the theme's highlight
    colour; the current cell itself keeps the normal selection colour, so the
    selected word stands out and its word address reads across SF1..SF4.
    ``linked_cells`` (1-based (subframe, word)) are outlined: the words of
    the sample shown in the Parameter Bits panel."""

    def __init__(self, view: QTableView):
        super().__init__(view)
        self._view = view
        self.linked_cells: frozenset[tuple[int, int]] = frozenset()

    def paint(self, painter, option, index) -> None:
        super().paint(painter, option, index)
        highlight = option.palette.highlight().color()
        current = self._view.currentIndex()
        if current.isValid() and index.row() == current.row() and index != current:
            tint = QColor(highlight)
            tint.setAlpha(ROW_TINT_ALPHA)
            painter.fillRect(option.rect, tint)
        if (index.column() + 1, index.row() + 1) in self.linked_cells:
            painter.save()
            painter.setPen(QPen(highlight, LINKED_CELL_BORDER))
            inset = LINKED_CELL_BORDER // 2 + 1
            painter.drawRect(option.rect.adjusted(inset, inset, -inset, -inset))
            painter.restore()


class FrameViewPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._remembered_cell: tuple[int, int] | None = None

        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        generate = QPushButton("Generate Frame (random)")
        generate.clicked.connect(self._generate_random)
        clear = QPushButton("Clear Frame")
        clear.clicked.connect(self._clear_frame)
        load = QPushButton("Load Frame…")
        load.clicked.connect(self._load_frame)
        save = QPushButton("Save Frame…")
        save.clicked.connect(self._save_frame)
        for button in (generate, clear, load, save):
            controls.addWidget(button)
        controls.addStretch(1)
        controls.addWidget(QLabel("Representation:"))
        self._rep_combo = QComboBox()
        self._rep_combo.addItems(REPRESENTATIONS)
        self._rep_combo.setCurrentText("HEX")
        self._rep_combo.currentTextChanged.connect(self._representation_changed)
        controls.addWidget(self._rep_combo)
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._model = FrameTableModel(ctx.frame_store, self)
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._delegate = RowHighlightDelegate(self._table)
        self._table.setItemDelegate(self._delegate)
        # currentChanged fires for mouse clicks and keyboard navigation alike
        # (clicked() only for the mouse), so the inspector follows the arrow
        # keys too.
        self._table.selectionModel().currentChanged.connect(self._current_changed)
        self._table.doubleClicked.connect(self._cell_double_clicked)
        # A model reset (a frame with another WPS) drops the current cell;
        # bring it back when the address still exists in the new frame.
        self._model.modelAboutToBeReset.connect(self._remember_cell)
        self._model.modelReset.connect(self._restore_cell)
        splitter.addWidget(self._table)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.parameter_bits = ParameterBitsWidget(ctx)
        self.parameter_bits.word_activated.connect(self.select_cell)
        self.parameter_bits.cells_changed.connect(self._set_linked_cells)
        right_layout.addWidget(self.parameter_bits)
        self._inspector = WordInspectorWidget(ctx)
        right_layout.addWidget(self._inspector, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

    # -- parameter bits -----------------------------------------------------

    def show_sample(self, key) -> None:
        """Show the words of one decoded sample (parameter id, occurrence,
        subframe) in the Parameter Bits panel and select its first word."""
        self.parameter_bits.show_sample(key)
        cells = self.parameter_bits.cells()
        if cells:
            self.select_cell(*cells[0])

    def linked_cells(self) -> frozenset[tuple[int, int]]:
        """(subframe, word) cells outlined in the grid."""
        return self._delegate.linked_cells

    def _set_linked_cells(self, cells) -> None:
        self._delegate.linked_cells = frozenset(cells)
        self._table.viewport().update()

    # -- selection ----------------------------------------------------------

    def current_cell(self) -> tuple[int, int] | None:
        """(subframe, word address) of the current cell, 1-based, if any."""
        index = self._table.currentIndex()
        if not index.isValid():
            return None
        return index.column() + 1, index.row() + 1

    def select_cell(self, subframe: int, word: int) -> bool:
        """Make (subframe, word) current; False when outside the frame."""
        index = self._model.index(word - 1, subframe - 1)
        if not index.isValid():
            return False
        self._table.setCurrentIndex(index)
        self._table.scrollTo(index)
        return True

    def _remember_cell(self) -> None:
        self._remembered_cell = self.current_cell()

    def _restore_cell(self) -> None:
        cell, self._remembered_cell = self._remembered_cell, None
        if cell is not None:
            self.select_cell(*cell)

    # -- control handlers ---------------------------------------------------

    def _generate_random(self) -> None:
        self._ctx.frame_service.new_random()

    def _clear_frame(self) -> None:
        self._ctx.frame_service.new_blank(with_sync_words=False)

    def _load_frame(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Frame", "", FRAME_FILE_FILTER
        )
        if not path:
            return
        try:
            self._ctx.frame_service.load(path)
        except ServiceError as exc:
            QMessageBox.critical(self, "Load Frame", str(exc))

    def _save_frame(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Frame", "frame.json", FRAME_FILE_FILTER
        )
        if not path:
            return
        try:
            self._ctx.frame_service.save(path)
        except ServiceError as exc:
            QMessageBox.critical(self, "Save Frame", str(exc))

    def _representation_changed(self, representation: str) -> None:
        self._model.set_representation(representation)

    # -- cell handlers ------------------------------------------------------

    def _current_changed(self, current, _previous) -> None:
        # The view repaints only the two cells itself; the row tint spans
        # the old and the new row.
        self._table.viewport().update()
        if current.isValid():
            self._inspector.show_word(current.column() + 1, current.row() + 1)

    def _cell_double_clicked(self, index) -> None:
        if self._ctx.frame_store.frame is None:
            return
        dialog = WordEditDialog(self._ctx, index.column() + 1, index.row() + 1, self)
        dialog.exec()
        self._inspector.show_word(index.column() + 1, index.row() + 1)
