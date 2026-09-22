"""Dataframe metadata dialog: create a new dataframe or edit the loaded one.

Sync words accept decimal, ``0x`` hex or ``0o`` octal text.  Invalid input
is reported inline and the dialog stays open (spec §47: no silent defaults).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from ...dataframe.editor import DEFAULT_SYNC_WORDS, STANDARD_WPS_VALUES
from ...domain.frame import SUBFRAME_COUNT, WORD_MAX
from ...services import ServiceError
from ..common import COLOR_ERROR, monospace_font


class MetadataDialog(QDialog):
    """``mode`` is ``"new"`` (creates a dataframe) or ``"edit"`` (updates)."""

    def __init__(self, ctx, mode: str = "new", parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._mode = mode
        dataframe = ctx.dataframe_store.dataframe if mode == "edit" else None
        md = dataframe.metadata if dataframe else None
        self.setWindowTitle("New Dataframe" if mode == "new" else "Edit Dataframe Metadata")

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name = QLineEdit(md.dataframe_name if md else "NEW_DATAFRAME")
        form.addRow("Dataframe name:", self._name)
        self._aircraft = QLineEdit(md.aircraft_type or "" if md else "")
        form.addRow("Aircraft type:", self._aircraft)
        self._revision = QLineEdit(md.revision or "" if md else "")
        form.addRow("Revision:", self._revision)
        self._issue_date = QLineEdit(md.issue_date or "" if md else "")
        self._issue_date.setPlaceholderText("YYYY-MM-DD")
        form.addRow("Issue date:", self._issue_date)

        self._wps = QComboBox()
        self._wps.setEditable(True)
        self._wps.addItems([str(v) for v in STANDARD_WPS_VALUES])
        self._wps.setCurrentText(str(md.wps if md else 256))
        form.addRow("Words per second:", self._wps)

        self._superframe = QCheckBox("Superframe present")
        self._superframe.setChecked(bool(md.superframe_present) if md else False)
        form.addRow("", self._superframe)

        sync_row = QHBoxLayout()
        self._sync: list[QLineEdit] = []
        current_sync = list(md.sync_words) if md else list(DEFAULT_SYNC_WORDS)
        for i in range(SUBFRAME_COUNT):
            edit = QLineEdit(str(current_sync[i]) if i < len(current_sync) else "")
            edit.setFont(monospace_font())
            edit.setPlaceholderText(f"SF{i + 1}")
            edit.setMaximumWidth(90)
            sync_row.addWidget(edit)
            self._sync.append(edit)
        sync_row.addStretch(1)
        form.addRow("Sync words (SF1..SF4):", sync_row)
        layout.addLayout(form)

        hint = QLabel(
            "Sync words: decimal, 0x hex or 0o octal. "
            f"Standard pattern: {', '.join(str(w) for w in DEFAULT_SYNC_WORDS)}."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._error = QLabel("")
        self._error.setWordWrap(True)
        palette = self._error.palette()
        palette.setColor(self._error.foregroundRole(), COLOR_ERROR)
        self._error.setPalette(palette)
        layout.addWidget(self._error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- parsing ------------------------------------------------------------

    def _parse(self) -> dict:
        name = self._name.text().strip()
        if not name:
            raise ValueError("dataframe name is required")
        try:
            wps = int(self._wps.currentText().strip())
        except ValueError as exc:
            raise ValueError(f"WPS must be an integer, got {self._wps.currentText()!r}") from exc
        if wps <= 0:
            raise ValueError(f"WPS must be positive, got {wps}")
        sync_words: list[int] = []
        for i, edit in enumerate(self._sync, start=1):
            text = edit.text().strip()
            if not text:
                raise ValueError(f"sync word for SF{i} is required")
            try:
                value = int(text, 0)
            except ValueError as exc:
                raise ValueError(f"sync word for SF{i}: invalid number {text!r}") from exc
            if not 0 <= value <= WORD_MAX:
                raise ValueError(f"sync word for SF{i}: {value} outside 0..{WORD_MAX}")
            sync_words.append(value)
        return {
            "dataframe_name": name,
            "wps": wps,
            "aircraft_type": self._aircraft.text().strip() or None,
            "revision": self._revision.text().strip() or None,
            "issue_date": self._issue_date.text().strip() or None,
            "superframe_present": self._superframe.isChecked(),
            "sync_words": sync_words,
        }

    def _try_accept(self) -> None:
        try:
            fields = self._parse()
            if self._mode == "new":
                name = fields.pop("dataframe_name")
                wps = fields.pop("wps")
                self._ctx.dataframe_service.new_dataframe(name, wps, **fields)
            else:
                self._ctx.dataframe_service.update_metadata(**fields)
        except (ValueError, ServiceError) as exc:
            self._error.setText(str(exc))
            return
        self.accept()
