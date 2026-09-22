"""Parameter add/edit dialog (design spec §44 "edit mappings").

The upper form edits the definition; the lower table edits the mapping as
one row per segment (occurrence, sequence, subframes, word, lsb, msb).  The
canonical type is always derived from the source type through the same
normalization the importer uses — never chosen independently.

"Check" previews the validation issues the edit would raise without
committing; OK commits through the dataframe service even when the
validator reports issues (the domain is permissive; the Dataframe page and
status bar keep showing the issues until they are fixed).
"""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...dataframe.editor import (
    SegmentRow,
    build_occurrences,
    candidate_issues,
    format_subframes,
    parse_subframes,
    segment_rows,
)
from ...domain.dataframe import DataframeDefinition
from ...domain.frame import WORD_BITS
from ...domain.parameter import (
    TYPE_UNKNOWN,
    ConversionRule,
    ParameterDefinition,
    normalize_source_type,
)
from ...services import ServiceError
from ..common import (
    COLOR_ERROR,
    COLOR_OK,
    COLOR_WARNING,
    fill_issues_table,
    make_issues_table,
    monospace_font,
)

# Offered in the source-type combo; any other text is accepted too and
# normalizes to "unknown" (with a validator warning), exactly like an import.
SOURCE_TYPE_CHOICES = (
    "Signed Analog",
    "Unsigned Analog",
    "BNR",
    "BCD",
    "Discrete",
    "Raw",
)

COL_OCC, COL_SEQ, COL_SF, COL_WORD, COL_LSB, COL_MSB = range(6)
MAPPING_HEADERS = ["Occ", "Seq", "Subframes", "Word", "LSB", "MSB"]


class ParameterEditDialog(QDialog):
    """``parameter`` is ``None`` to add a parameter, else the one to edit."""

    def __init__(
        self,
        ctx,
        parameter: ParameterDefinition | None = None,
        parent=None,
        *,
        commit=None,
        dataframe_for_check: "DataframeDefinition | None" = None,
    ):
        """``commit(candidate)`` replaces the dataframe-service commit (the PDF
        review dialog edits session rows, not the loaded dataframe);
        ``dataframe_for_check`` is the dataframe "Check" validates against."""
        super().__init__(parent)
        self._ctx = ctx
        self._original = parameter
        self._commit = commit
        self._check_dataframe = dataframe_for_check
        self.setWindowTitle(
            "Add Parameter" if parameter is None else f"Edit Parameter — {parameter.mnemonic}"
        )
        self.resize(760, 640)

        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter, 1)

        # -- definition form --------------------------------------------------
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        p = parameter
        self._mnemonic = QLineEdit(p.mnemonic if p else "")
        form.addRow("Mnemonic:", self._mnemonic)
        self._description = QLineEdit(p.description if p else "")
        form.addRow("Description:", self._description)

        type_row = QHBoxLayout()
        self._source_type = QComboBox()
        self._source_type.setEditable(True)
        self._source_type.addItems(SOURCE_TYPE_CHOICES)
        self._source_type.setCurrentText(
            (p.source_parameter_type or "") if p else SOURCE_TYPE_CHOICES[0]
        )
        self._source_type.currentTextChanged.connect(self._update_canonical_label)
        self._source_type.editTextChanged.connect(self._update_canonical_label)
        type_row.addWidget(self._source_type, 1)
        self._canonical_label = QLabel()
        type_row.addWidget(self._canonical_label)
        form.addRow("Source type:", type_row)

        self._unit = QLineEdit(p.unit or "" if p else "")
        form.addRow("Unit:", self._unit)
        conv_row = QHBoxLayout()
        self._resolution = QLineEdit(format(p.conversion.resolution, "g") if p else "1")
        self._offset = QLineEdit(format(p.conversion.offset, "g") if p else "0")
        for edit in (self._resolution, self._offset):
            edit.setFont(monospace_font())
        conv_row.addWidget(QLabel("resolution"))
        conv_row.addWidget(self._resolution)
        conv_row.addWidget(QLabel("offset"))
        conv_row.addWidget(self._offset)
        form.addRow("Conversion (linear):", conv_row)

        range_row = QHBoxLayout()
        self._minimum = QLineEdit(_opt(p.minimum) if p else "")
        self._maximum = QLineEdit(_opt(p.maximum) if p else "")
        for edit in (self._minimum, self._maximum):
            edit.setFont(monospace_font())
            edit.setPlaceholderText("none")
        range_row.addWidget(QLabel("min"))
        range_row.addWidget(self._minimum)
        range_row.addWidget(QLabel("max"))
        range_row.addWidget(self._maximum)
        form.addRow("Range:", range_row)

        state_row = QHBoxLayout()
        self._true_state = QLineEdit(p.true_state or "" if p else "")
        self._false_state = QLineEdit(p.false_state or "" if p else "")
        state_row.addWidget(QLabel("1 ="))
        state_row.addWidget(self._true_state)
        state_row.addWidget(QLabel("0 ="))
        state_row.addWidget(self._false_state)
        form.addRow("Discrete states:", state_row)

        self._notes = QLineEdit(p.notes or "" if p else "")
        form.addRow("Notes:", self._notes)
        splitter.addWidget(form_widget)

        # -- mapping table ----------------------------------------------------
        mapping_widget = QWidget()
        mapping_layout = QVBoxLayout(mapping_widget)
        mapping_layout.setContentsMargins(0, 0, 0, 0)
        mapping_layout.addWidget(
            QLabel(
                "Mapping — one row per segment. Subframes: 1,3 / 2-4 / all. "
                f"Bits {WORD_BITS}..1, bit 1 is the LSB. Segment sequence 1 holds "
                "the most significant bits of a multi-segment value."
            )
        )
        self._table = QTableWidget(0, len(MAPPING_HEADERS))
        self._table.setHorizontalHeaderLabels(MAPPING_HEADERS)
        self._table.setFont(monospace_font())
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_SF, QHeaderView.ResizeMode.Stretch)
        mapping_layout.addWidget(self._table, 1)

        buttons = QHBoxLayout()
        add_segment = QPushButton("Add Segment")
        add_segment.clicked.connect(self._add_segment)
        add_occurrence = QPushButton("Add Occurrence")
        add_occurrence.clicked.connect(self._add_occurrence)
        remove_row = QPushButton("Remove Row")
        remove_row.clicked.connect(self._remove_row)
        for button in (add_segment, add_occurrence, remove_row):
            buttons.addWidget(button)
        buttons.addStretch(1)
        mapping_layout.addLayout(buttons)
        splitter.addWidget(mapping_widget)

        # -- validation preview -----------------------------------------------
        issues_widget = QWidget()
        issues_layout = QVBoxLayout(issues_widget)
        issues_layout.setContentsMargins(0, 0, 0, 0)
        self._issue_summary = QLabel("Press Check to preview validation.")
        issues_layout.addWidget(self._issue_summary)
        self._issues_table = make_issues_table(self)
        issues_layout.addWidget(self._issues_table, 1)
        splitter.addWidget(issues_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 1)

        self._error = QLabel("")
        self._error.setWordWrap(True)
        palette = self._error.palette()
        palette.setColor(self._error.foregroundRole(), COLOR_ERROR)
        self._error.setPalette(palette)
        layout.addWidget(self._error)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        check = box.addButton("Check", QDialogButtonBox.ButtonRole.ActionRole)
        check.clicked.connect(self._check)
        box.accepted.connect(self._try_accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        if parameter is not None:
            for row in segment_rows(parameter):
                self._append_row(row)
        else:
            self._append_row(SegmentRow(1, 1, (1, 2, 3, 4), 2, 1, WORD_BITS))
        self._update_canonical_label()

    # -- mapping rows -------------------------------------------------------

    def _append_row(self, segment: SegmentRow) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        values = [
            str(segment.occurrence),
            str(segment.sequence),
            format_subframes(segment.subframes),
            str(segment.word),
            str(segment.lsb),
            str(segment.msb),
        ]
        for column, value in enumerate(values):
            self._table.setItem(row, column, QTableWidgetItem(value))

    def _cell(self, row: int, column: int) -> str:
        item = self._table.item(row, column)
        return item.text().strip() if item else ""

    def _add_segment(self) -> None:
        current = self._table.currentRow()
        occurrence, sequence = 1, 1
        if current >= 0:
            occurrence = _int_or(self._cell(current, COL_OCC), 1)
            sequence = (
                max(
                    _int_or(self._cell(r, COL_SEQ), 0)
                    for r in range(self._table.rowCount())
                    if _int_or(self._cell(r, COL_OCC), 1) == occurrence
                )
                + 1
            )
        elif self._table.rowCount():
            sequence = self._table.rowCount() + 1
        self._append_row(SegmentRow(occurrence, sequence, (1, 2, 3, 4), 2, 1, WORD_BITS))
        self._table.setCurrentCell(self._table.rowCount() - 1, COL_WORD)

    def _add_occurrence(self) -> None:
        indexes = [_int_or(self._cell(r, COL_OCC), 0) for r in range(self._table.rowCount())]
        occurrence = (max(indexes) if indexes else 0) + 1
        self._append_row(SegmentRow(occurrence, 1, (1, 2, 3, 4), 2, 1, WORD_BITS))
        self._table.setCurrentCell(self._table.rowCount() - 1, COL_WORD)

    def _remove_row(self) -> None:
        current = self._table.currentRow()
        if current >= 0:
            self._table.removeRow(current)

    # -- building the candidate ----------------------------------------------

    def _update_canonical_label(self, *_args) -> None:
        canonical = normalize_source_type(self._source_type.currentText())
        self._canonical_label.setText(f"→ {canonical}")
        palette = self._canonical_label.palette()
        palette.setColor(
            self._canonical_label.foregroundRole(),
            COLOR_WARNING if canonical == TYPE_UNKNOWN else COLOR_OK,
        )
        self._canonical_label.setPalette(palette)

    def _rows(self) -> list[SegmentRow]:
        rows: list[SegmentRow] = []
        for r in range(self._table.rowCount()):
            where = f"mapping row {r + 1}"
            try:
                subframes = parse_subframes(self._cell(r, COL_SF))
            except ValueError as exc:
                raise ValueError(f"{where}: {exc}") from exc
            rows.append(
                SegmentRow(
                    occurrence=_int_field(self._cell(r, COL_OCC), f"{where}: occurrence"),
                    sequence=_int_field(self._cell(r, COL_SEQ), f"{where}: sequence"),
                    subframes=subframes,
                    word=_int_field(self._cell(r, COL_WORD), f"{where}: word"),
                    lsb=_int_field(self._cell(r, COL_LSB), f"{where}: lsb"),
                    msb=_int_field(self._cell(r, COL_MSB), f"{where}: msb"),
                )
            )
        return rows

    def build_candidate(self) -> ParameterDefinition:
        """Parse the form into a ParameterDefinition; raises ValueError."""
        mnemonic = self._mnemonic.text().strip()
        if not mnemonic:
            raise ValueError("mnemonic is required")
        source_type = self._source_type.currentText().strip() or None
        original = self._original
        candidate = ParameterDefinition(
            id=original.id if original else "",
            mnemonic=mnemonic,
            description=self._description.text().strip(),
            source_parameter_type=source_type,
            parameter_type=normalize_source_type(source_type),
            unit=self._unit.text().strip() or None,
            minimum=_opt_float(self._minimum.text(), "minimum"),
            maximum=_opt_float(self._maximum.text(), "maximum"),
            conversion=ConversionRule(
                resolution=_float_field(self._resolution.text(), "resolution"),
                offset=_float_field(self._offset.text(), "offset"),
            ),
            true_state=self._true_state.text().strip() or None,
            false_state=self._false_state.text().strip() or None,
            occurrences=build_occurrences(self._rows(), previous=original),
            notes=self._notes.text().strip() or None,
            provenance=copy.deepcopy(original.provenance) if original else None,
        )
        return candidate

    # -- actions ------------------------------------------------------------

    def _check(self) -> None:
        dataframe = (
            self._check_dataframe
            if self._check_dataframe is not None
            else self._ctx.dataframe_store.dataframe
        )
        try:
            candidate = self.build_candidate()
        except ValueError as exc:
            self._error.setText(str(exc))
            return
        self._error.setText("")
        if dataframe is None:
            self._issue_summary.setText("No dataframe loaded.")
            return
        if not candidate.id:
            candidate.id = "<new>"
        issues = candidate_issues(dataframe, candidate)
        fill_issues_table(self._issues_table, issues)
        errors = sum(1 for i in issues if i.severity == "error")
        warnings = len(issues) - errors
        self._issue_summary.setText(
            "No validation issues." if not issues else f"{errors} errors, {warnings} warnings"
        )

    def _try_accept(self) -> None:
        try:
            candidate = self.build_candidate()
            if self._commit is not None:
                self._commit(candidate)
            elif self._original is None:
                self._ctx.dataframe_service.add_parameter(candidate)
            else:
                self._ctx.dataframe_service.update_parameter(candidate)
        except (ValueError, ServiceError) as exc:
            self._error.setText(str(exc))
            return
        self.accept()


def _opt(value: float | None) -> str:
    return "" if value is None else format(value, "g")


def _int_or(text: str, default: int) -> int:
    try:
        return int(text)
    except ValueError:
        return default


def _int_field(text: str, label: str) -> int:
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"{label} must be an integer, got {text!r}") from exc


def _float_field(text: str, label: str) -> float:
    try:
        return float(text.strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be a number, got {text!r}") from exc


def _opt_float(text: str, label: str) -> float | None:
    if not text.strip():
        return None
    return _float_field(text, label)
