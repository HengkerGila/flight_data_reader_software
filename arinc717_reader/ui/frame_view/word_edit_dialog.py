"""Manual word edit dialog (design spec §8.3).

Validates input in the chosen representation, converts to the canonical
integer, and stores it via the frame service.  Invalid values are rejected
with the dialog kept open.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from ...services import ServiceError
from ..common import COLOR_ERROR, monospace_font
from ..representation import REPRESENTATIONS, all_representations, format_word, parse_word


class WordEditDialog(QDialog):
    def __init__(self, ctx, subframe: int, word: int, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._subframe = subframe
        self._word = word
        self.setWindowTitle(f"Edit Word — SF{subframe} / {word:03d}")

        current = ctx.frame_store.frame.word(subframe, word)
        reps = all_representations(current)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Subframe:", QLabel(str(subframe)))
        form.addRow("Word address:", QLabel(f"{word:03d}"))
        current_label = QLabel(
            f"BIN {reps['BIN']}   OCT {reps['OCT']}   "
            f"DEC {reps['DEC']}   HEX {reps['HEX']}"
        )
        current_label.setFont(monospace_font())
        form.addRow("Current value:", current_label)

        self._rep_combo = QComboBox()
        self._rep_combo.addItems(REPRESENTATIONS)
        self._rep_combo.setCurrentText("HEX")
        self._rep_combo.currentTextChanged.connect(self._refill)
        form.addRow("Representation:", self._rep_combo)

        self._input = QLineEdit(format_word(current, "HEX"))
        self._input.setFont(monospace_font())
        self._input.selectAll()
        form.addRow("New value:", self._input)
        layout.addLayout(form)

        self._error = QLabel("")
        palette = self._error.palette()
        palette.setColor(self._error.foregroundRole(), COLOR_ERROR)
        self._error.setPalette(palette)
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _refill(self, representation: str) -> None:
        current = self._ctx.frame_store.frame.word(self._subframe, self._word)
        self._input.setText(format_word(current, representation))
        self._input.selectAll()
        self._error.setText("")

    def _try_accept(self) -> None:
        try:
            value = parse_word(self._input.text(), self._rep_combo.currentText())
            self._ctx.frame_service.set_word(self._subframe, self._word, value)
        except (ValueError, ServiceError) as exc:
            self._error.setText(str(exc))
            return
        self.accept()
