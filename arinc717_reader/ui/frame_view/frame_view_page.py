"""Frame View page (design spec §8, §42).

Single-click opens the word inspector; double-click opens the manual edit
dialog.  Representation switching is display-only.
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
    QTableView,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

from ...services import ServiceError
from ..representation import REPRESENTATIONS
from .frame_table_model import FrameTableModel
from .word_edit_dialog import WordEditDialog
from .word_inspector import WordInspectorWidget

FRAME_FILE_FILTER = "Frame files (*.json);;All files (*)"


class FrameViewPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx

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
        self._table.clicked.connect(self._cell_clicked)
        self._table.doubleClicked.connect(self._cell_double_clicked)
        splitter.addWidget(self._table)

        self._inspector = WordInspectorWidget(ctx)
        splitter.addWidget(self._inspector)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

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

    def _cell_clicked(self, index) -> None:
        self._inspector.show_word(index.column() + 1, index.row() + 1)

    def _cell_double_clicked(self, index) -> None:
        if self._ctx.frame_store.frame is None:
            return
        dialog = WordEditDialog(self._ctx, index.column() + 1, index.row() + 1, self)
        dialog.exec()
        self._inspector.show_word(index.column() + 1, index.row() + 1)
