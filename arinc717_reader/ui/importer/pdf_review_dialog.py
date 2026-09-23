"""PDF import review dialog (design spec §28 "Manual Review", §30, §32, §46).

Shows every extracted row with its review state, the verbatim cells it came
from (page, table, bounding box), how each canonical value was derived, and
the issues that sent it to review.  Rows are edited with the same
``ParameterEditDialog`` the Dataframe page uses; approving, excluding and
publishing go through ``ImportSession`` so the state machine — not the
widgets — is the source of truth.
"""

from __future__ import annotations

import copy

import dataclasses

from PySide6.QtCore import QEvent, QItemSelectionModel, Qt
from PySide6.QtGui import QColor, QFont, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...dataframe.editor import STANDARD_WPS_VALUES, format_subframes
from ...dataframe.pdf_importer import (
    ImportItem,
    ImportProfile,
    ImportSession,
    ReviewError,
    renormalize_session,
)
from ...dataframe.pdf_importer.ingest import pdf_support_available
from ...dataframe.pdf_importer.normalize import (
    BITS_AUTO,
    BITS_PER_WORD,
    BITS_RANGE_PER_WORD,
    FREQUENCY_AUTO,
    FREQUENCY_HZ,
    FREQUENCY_SECONDS,
    MULTIWORD_AUTO,
    MULTIWORD_OCCURRENCES,
    MULTIWORD_SEGMENTS,
    PAIR_AUTO,
    PAIR_OFFSET_RESOLUTION,
    PAIR_RESOLUTION_OFFSET,
)
from ...dataframe.pdf_importer.render import render_page_png, render_region_png
from ...dataframe.pdf_importer.review import (
    STATE_APPROVED,
    STATE_PUBLISHED,
    STATE_REVIEW_REQUIRED,
    STATE_VALIDATED,
)
from ...domain.frame import SUBFRAME_COUNT, WORD_MAX
from ...domain.parameter import ParameterDefinition, normalize_source_type
from ...services import ServiceError
from ..common import COLOR_ERROR, COLOR_OK, COLOR_WARNING, monospace_font
from ..dataframe_view.parameter_edit_dialog import ParameterEditDialog

COLUMNS = [
    "State", "Pg", "Parameter", "Type", "Words", "Bits", "SF", "Freq",
    "Unit", "Res", "Offset", "Issues", "Reason",
]
(
    COL_STATE, COL_PAGE, COL_NAME, COL_TYPE, COL_WORDS, COL_BITS, COL_SF,
    COL_FREQ, COL_UNIT, COL_RES, COL_OFFSET, COL_ISSUES, COL_REASON,
) = range(len(COLUMNS))

FREQUENCY_CHOICES = (
    (FREQUENCY_AUTO, "auto (from document evidence)"),
    (FREQUENCY_HZ, "Hz (samples per second)"),
    (FREQUENCY_SECONDS, "seconds between samples"),
)
BITS_CHOICES = (
    (BITS_AUTO, "auto"),
    (BITS_PER_WORD, "one MSB/LSB per word"),
    (BITS_RANGE_PER_WORD, "MSB/LSB ranges over word pairs"),
)
MEANING_CHOICES = (
    (MULTIWORD_AUTO, "auto (from the rate)"),
    (MULTIWORD_OCCURRENCES, "repeated occurrences"),
    (MULTIWORD_SEGMENTS, "segments of one value"),
)
PAIR_CHOICES = (
    (PAIR_AUTO, "ask (flag for review)"),
    (PAIR_OFFSET_RESOLUTION, "offset, resolution"),
    (PAIR_RESOLUTION_OFFSET, "resolution, offset"),
)

FILTER_ALL = "All rows"
FILTER_REVIEW = "Needs review"
FILTER_VALIDATED = "Validated, not approved"
FILTER_APPROVED = "Approved"
FILTER_EXCLUDED = "Excluded"
FILTERS = (FILTER_ALL, FILTER_REVIEW, FILTER_VALIDATED, FILTER_APPROVED, FILTER_EXCLUDED)

# Row tints per review state.  Text and background are always set together
# so a row stays readable whatever the widget theme's default text colour is:
# dark ink on pale tints for a light theme, pale ink on deep tints for a dark
# theme (spec §46: the review queue must be legible at a glance).
LIGHT_STATE_COLORS: dict[str, tuple[QColor, QColor]] = {
    STATE_REVIEW_REQUIRED: (QColor(255, 222, 218), QColor(120, 20, 16)),
    STATE_VALIDATED: (QColor(255, 243, 200), QColor(110, 70, 0)),
    STATE_APPROVED: (QColor(218, 242, 224), QColor(16, 88, 40)),
    STATE_PUBLISHED: (QColor(214, 230, 250), QColor(20, 60, 120)),
}
DARK_STATE_COLORS: dict[str, tuple[QColor, QColor]] = {
    STATE_REVIEW_REQUIRED: (QColor(104, 40, 36), QColor(255, 214, 208)),
    STATE_VALIDATED: (QColor(98, 78, 18), QColor(255, 236, 170)),
    STATE_APPROVED: (QColor(32, 84, 48), QColor(200, 240, 210)),
    STATE_PUBLISHED: (QColor(34, 64, 104), QColor(206, 226, 255)),
}
LIGHT_EXCLUDED_FOREGROUND = QColor(130, 130, 130)
DARK_EXCLUDED_FOREGROUND = QColor(150, 150, 150)
# Summary counters ("approved 7 · needs review 5"): brighter on dark themes.
LIGHT_STATUS_COLORS = {"ok": COLOR_OK, "warning": COLOR_WARNING, "error": COLOR_ERROR}
DARK_STATUS_COLORS = {
    "ok": QColor(110, 210, 140),
    "warning": QColor(240, 190, 80),
    "error": QColor(255, 120, 110),
}


def is_dark_palette(palette: QPalette) -> bool:
    return palette.color(QPalette.ColorRole.Base).lightness() < 128


def state_colors(palette: QPalette) -> dict[str, tuple[QColor, QColor]]:
    """(background, foreground) per review state for the given theme."""
    return DARK_STATE_COLORS if is_dark_palette(palette) else LIGHT_STATE_COLORS


def excluded_foreground(palette: QPalette) -> QColor:
    return DARK_EXCLUDED_FOREGROUND if is_dark_palette(palette) else LIGHT_EXCLUDED_FOREGROUND


def status_colors(palette: QPalette) -> dict[str, QColor]:
    return DARK_STATUS_COLORS if is_dark_palette(palette) else LIGHT_STATUS_COLORS


SOURCE_REGION_DPI = 200
SOURCE_PAGE_DPI = 150
SOURCE_CONTEXT_MARGIN_X = 14.0   # points left/right of the extracted row
SOURCE_CONTEXT_MARGIN_Y = 70.0   # points above/below: the neighbouring rows


class PdfReviewDialog(QDialog):
    def __init__(self, ctx, session: ImportSession, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._session = session
        self.setWindowTitle(
            f"Review PDF Import — {session.source_filename or 'document'} (Experimental)"
        )
        self.resize(1280, 820)

        layout = QVBoxLayout(self)

        # -- document summary + metadata + conventions ------------------------
        # One container row: the boxes share a height (the tallest natural
        # size), and the row never grows when the dialog does — the space
        # goes to the rows table and the details pane instead.
        header = QWidget()
        header.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        top = QHBoxLayout(header)
        top.setContentsMargins(0, 0, 0, 0)
        summary_box = QGroupBox("Document")
        summary_layout = QVBoxLayout(summary_box)
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        summary_layout.addWidget(self._summary)
        self._document_issues = QPlainTextEdit()
        self._document_issues.setReadOnly(True)
        self._document_issues.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._document_issues.setPlaceholderText("No document-level issues.")
        self._document_issues.setToolTip("Document-level extraction and validation issues")
        self._document_issues.setMaximumHeight(84)
        summary_layout.addWidget(self._document_issues, 1)
        top.addWidget(summary_box, 3)

        meta_box = QGroupBox("Dataframe metadata — confirm before publishing")
        meta_layout = QVBoxLayout(meta_box)
        form = QFormLayout()
        meta_layout.addLayout(form)
        meta_layout.addStretch(1)
        self._name = QLineEdit(session.dataframe_name)
        form.addRow("Dataframe name:", self._name)
        wps_row = QHBoxLayout()
        self._wps = QComboBox()
        self._wps.setEditable(True)
        self._wps.addItems([str(v) for v in STANDARD_WPS_VALUES])
        self._wps.setCurrentText(str(session.wps))
        wps_row.addWidget(self._wps)
        wps_hint = QLabel(
            f"stated in document: {session.detected_wps}"
            if session.detected_wps
            else "not stated in document — verify"
        )
        wps_row.addWidget(wps_hint, 1)
        form.addRow("Words per second:", wps_row)
        sync_row = QHBoxLayout()
        self._sync: list[QLineEdit] = []
        for i in range(SUBFRAME_COUNT):
            edit = QLineEdit(
                str(session.sync_words[i]) if i < len(session.sync_words) else ""
            )
            edit.setFont(monospace_font())
            edit.setMaximumWidth(80)
            edit.setPlaceholderText(f"SF{i + 1}")
            sync_row.addWidget(edit)
            self._sync.append(edit)
        sync_row.addStretch(1)
        form.addRow("Sync words:", sync_row)
        apply_button = QPushButton("Apply Metadata")
        apply_button.setToolTip("Re-validate every row against the metadata above")
        apply_button.clicked.connect(self._apply_metadata)
        form.addRow("", apply_button)
        top.addWidget(meta_box, 2)

        conventions = QGroupBox("Document conventions")
        grid = QGridLayout(conventions)
        grid.setHorizontalSpacing(12)
        self._frequency_unit = self._choice_combo(FREQUENCY_CHOICES)
        self._bits_layout = self._choice_combo(BITS_CHOICES)
        self._multiword = self._choice_combo(MEANING_CHOICES)
        self._pair_order = self._choice_combo(PAIR_CHOICES)
        for row, (label, combo) in enumerate(
            (
                ("Frequency column:", self._frequency_unit),
                ("Multi-word bits:", self._bits_layout),
                ("Several words:", self._multiword),
                ("Two-number resolution:", self._pair_order),
            )
        ):
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(combo, row, 1)
        self._blank_subframe = QCheckBox("blank subframe = every subframe")
        self._zero_subframe = QCheckBox("subframe 0 = every subframe")
        self._decimal_comma = QCheckBox("decimal comma (0,0625)")
        grid.addWidget(self._blank_subframe, 0, 2)
        grid.addWidget(self._zero_subframe, 1, 2)
        grid.addWidget(self._decimal_comma, 2, 2)
        confidence_row = QHBoxLayout()
        confidence_row.addWidget(QLabel("OCR confidence needed:"))
        self._confidence = QDoubleSpinBox()
        self._confidence.setRange(0.0, 1.0)
        self._confidence.setSingleStep(0.05)
        self._confidence.setDecimals(2)
        confidence_row.addWidget(self._confidence)
        confidence_row.addStretch(1)
        grid.addLayout(confidence_row, 3, 2)
        self._detected = QLabel()
        self._detected.setWordWrap(True)
        grid.addWidget(self._detected, 4, 0, 1, 3)
        renormalize_button = QPushButton("Re-normalize")
        renormalize_button.setToolTip(
            "Re-run normalization with these conventions (resets approvals and edits)"
        )
        renormalize_button.clicked.connect(lambda: self._renormalize())
        grid.addWidget(renormalize_button, 5, 2, alignment=Qt.AlignmentFlag.AlignRight)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        grid.setRowStretch(6, 1)
        top.addWidget(conventions, 3)
        layout.addWidget(header)
        self._load_profile(session.profile)

        # -- filter -------------------------------------------------------------
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Show:"))
        self._filter = QComboBox()
        self._filter.addItems(FILTERS)
        self._filter.currentIndexChanged.connect(lambda *_: self._apply_filter())
        filter_row.addWidget(self._filter)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search parameter / type / words / issue text…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda *_: self._apply_filter())
        filter_row.addWidget(self._search, 1)
        layout.addLayout(filter_row)

        # -- rows + details -------------------------------------------------------
        splitter = QSplitter(Qt.Orientation.Vertical)
        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        self._table.currentCellChanged.connect(lambda *_: self._selection_changed())
        # A Shift+click range or Ctrl+A also selects rows the filter hides;
        # keep the selection inside the visible rows so Approve / Exclude act
        # only on what the reviewer can see.
        self._deselecting = False
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(lambda *_: self._edit_selected())
        splitter.addWidget(self._table)

        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        self._details.setFont(monospace_font())
        self._details.setPlaceholderText(
            "Select a row to see the verbatim extraction, how each value was "
            "derived, and why it needs review."
        )
        splitter.addWidget(self._details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        # -- row actions ---------------------------------------------------------
        actions = QHBoxLayout()
        self._edit_button = QPushButton("Edit…")
        self._edit_button.clicked.connect(self._edit_selected)
        self._approve_button = QPushButton("Approve Selected")
        self._approve_button.setToolTip("Approve every selected row without errors")
        self._approve_button.clicked.connect(self._approve_selected)
        self._exclude_button = QPushButton("Exclude")
        self._exclude_button.setToolTip("Exclude (or include again) the selected rows")
        self._exclude_button.clicked.connect(self._toggle_exclude_selected)
        self._source_button = QPushButton("Show Source…")
        self._source_button.setToolTip("Render the page region this row was extracted from")
        self._source_button.clicked.connect(self._show_source)
        approve_all = QPushButton("Approve All Validated")
        approve_all.setToolTip("Approve every row that validated (warnings allowed, no errors)")
        approve_all.clicked.connect(self._approve_all_validated)
        for button in (
            self._edit_button,
            self._approve_button,
            self._exclude_button,
            self._source_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        actions.addWidget(approve_all)
        layout.addLayout(actions)

        self._error = QLabel("")
        self._error.setWordWrap(True)
        palette = self._error.palette()
        palette.setColor(self._error.foregroundRole(), COLOR_ERROR)
        self._error.setPalette(palette)
        layout.addWidget(self._error)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._publish_button = box.addButton(
            "Publish to Workspace", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._publish_button.setToolTip(
            "Load the approved parameters as the working dataframe "
            "(only approved rows are published)"
        )
        box.accepted.connect(self._try_publish)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

        self._rebuild()
        if self._table.rowCount():
            self._table.selectRow(0)

    @property
    def session(self) -> ImportSession:
        return self._session

    # -- conventions ---------------------------------------------------------------

    @staticmethod
    def _choice_combo(choices) -> QComboBox:
        combo = QComboBox()
        for value, label in choices:
            combo.addItem(label, value)
        return combo

    @staticmethod
    def _select_value(combo: QComboBox, value: str) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def _load_profile(self, profile: ImportProfile) -> None:
        self._select_value(self._frequency_unit, profile.frequency_unit)
        self._select_value(self._bits_layout, profile.multiword_bits)
        self._select_value(self._multiword, profile.multiword_meaning)
        self._select_value(self._pair_order, profile.resolution_pair)
        self._blank_subframe.setChecked(profile.blank_subframe_means_all)
        self._zero_subframe.setChecked(profile.zero_subframe_means_all)
        self._decimal_comma.setChecked(profile.decimal_comma)
        self._confidence.setValue(profile.ocr_confidence_threshold)

    def profile_from_form(self) -> ImportProfile:
        return dataclasses.replace(
            self._session.profile,
            frequency_unit=self._frequency_unit.currentData(),
            multiword_bits=self._bits_layout.currentData(),
            multiword_meaning=self._multiword.currentData(),
            resolution_pair=self._pair_order.currentData(),
            blank_subframe_means_all=self._blank_subframe.isChecked(),
            zero_subframe_means_all=self._zero_subframe.isChecked(),
            decimal_comma=self._decimal_comma.isChecked(),
            ocr_confidence_threshold=float(self._confidence.value()),
        )

    def _renormalize(self, confirm: bool = True) -> None:
        if confirm:
            answer = QMessageBox.question(
                self,
                "Re-normalize",
                "Re-normalizing re-reads every row with the conventions above and "
                "resets approvals and manual edits (exclusions are kept). Continue?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._error.setText("")
        if not self._apply_metadata():
            return
        self._session = renormalize_session(self._session, self.profile_from_form())
        self._rebuild()

    # -- rendering --------------------------------------------------------------

    def _rebuild(self) -> None:
        selected = self.selected_index()
        session = self._session
        palette = self._table.palette()
        colors = state_colors(palette)
        excluded_fg = excluded_foreground(palette)
        state_font = QFont(self._table.font())
        state_font.setBold(True)
        self._table.setRowCount(0)
        for item in session.items:
            row = self._table.rowCount()
            self._table.insertRow(row)
            tint = None if item.excluded else colors.get(item.state)
            for column, value in enumerate(self._row_values(item)):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, item.index)
                if item.excluded:
                    cell.setForeground(excluded_fg)
                elif tint is not None:
                    background, foreground = tint
                    cell.setBackground(background)
                    cell.setForeground(foreground)
                if column == COL_STATE:
                    cell.setFont(state_font)
                self._table.setItem(row, column, cell)
        self._apply_filter()
        self._render_summary()
        if selected is not None:
            self.select_index(selected)
        self._selection_changed()

    @staticmethod
    def _row_values(item: ImportItem) -> list[str]:
        raw = item.raw
        candidate = item.candidate
        state = item.state + (" (excluded)" if item.excluded else "")
        if candidate is not None:
            segments = [s for o in candidate.occurrences for s in o.segments]
            words = ", ".join(str(s.word) for s in segments)
            bits = "/".join(
                dict.fromkeys(f"{max(s.msb, s.lsb)}-{min(s.msb, s.lsb)}" for s in segments)
            )
            subframes = format_subframes(segments[0].subframes) if segments else ""
            parameter_type = candidate.parameter_type
            unit = candidate.unit or ""
            resolution = format(candidate.conversion.resolution, "g")
            offset = format(candidate.conversion.offset, "g")
        else:
            words, bits, subframes = raw.raw("word_location"), raw.raw("bits") or (
                f"{raw.raw('msb')}/{raw.raw('lsb')}" if raw.raw("msb") or raw.raw("lsb") else ""
            ), raw.raw("subframe")
            parameter_type = raw.raw("parameter_type")
            unit, resolution, offset = raw.raw("units"), raw.raw("resolution"), raw.raw("offset")
        issues = []
        if item.error_count:
            issues.append(f"{item.error_count}E")
        if item.warning_count:
            issues.append(f"{item.warning_count}W")
        reasons = [i.rule for i in item.normalization_issues if i.needs_review]
        reasons += [i.rule_name for i in item.validation_issues]
        return [
            state,
            str(raw.page_number or ""),
            item.display_name,
            parameter_type,
            words,
            bits,
            subframes,
            " ".join(raw.raw("frequency").split()),
            unit,
            resolution,
            offset,
            " ".join(issues),
            ", ".join(dict.fromkeys(reasons)),
        ]

    def _apply_filter(self) -> None:
        mode = self._filter.currentText()
        needle = self._search.text().strip().lower()
        for row in range(self._table.rowCount()):
            item = self._session.items[self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)]
            visible = {
                FILTER_ALL: True,
                FILTER_REVIEW: not item.excluded and item.state == STATE_REVIEW_REQUIRED,
                FILTER_VALIDATED: not item.excluded and item.state == STATE_VALIDATED,
                FILTER_APPROVED: not item.excluded
                and item.state in (STATE_APPROVED, STATE_PUBLISHED),
                FILTER_EXCLUDED: item.excluded,
            }[mode]
            if visible and needle:
                haystack = " ".join(
                    [self._table.item(row, c).text() for c in (COL_NAME, COL_TYPE, COL_WORDS, COL_REASON)]
                    + [i.message for i in item.normalization_issues]
                    + [i.message for i in item.validation_issues]
                ).lower()
                visible = needle in haystack
            self._table.setRowHidden(row, not visible)
        self._deselect_hidden()

    def _render_summary(self) -> None:
        session = self._session
        counts = session.state_counts()
        pending = len(session.pending_items())
        status = status_colors(self._summary.palette())
        self._summary.setText(
            f"<b>{session.source_filename or '—'}</b> — {session.page_count} page(s), "
            f"{session.tables_found} table(s), {len(session.items)} row(s)<br>"
            f"sha256 {(session.source_hash or '—')[:16]}<br>"
            f"<span style='color:{status['ok'].name()}'><b>approved {counts[STATE_APPROVED]}</b></span> · "
            f"<span style='color:{status['warning'].name()}'><b>validated {counts[STATE_VALIDATED]}</b></span> · "
            f"<span style='color:{status['error'].name()}'><b>needs review {counts[STATE_REVIEW_REQUIRED]}</b></span> · "
            f"excluded {counts['EXCLUDED']}<br>"
            + (
                f"<b>{pending} row(s) block publishing.</b>"
                if pending
                else "Ready to publish."
            )
        )
        lines = [f"{issue.severity.upper()} {issue.code}: {issue.message}" for issue in session.issues]
        lines += [
            f"{issue.severity.upper()} {issue.rule_name}: {issue.message}"
            for issue in session.session_validation_issues
        ]
        self._document_issues.setPlainText("\n".join(lines))
        effective = session.effective
        detected = []
        if session.profile.frequency_unit == FREQUENCY_AUTO:
            detected.append(f"frequency read as {effective.frequency_unit}")
        if session.profile.multiword_bits == BITS_AUTO and effective.multiword_bits != BITS_AUTO:
            detected.append("MSB/LSB ranges over word pairs")
        self._detected.setText(
            "Detected from the document: " + "; ".join(detected) if detected else "Nothing auto-detected."
        )
        self._publish_button.setEnabled(pending == 0 and counts[STATE_APPROVED] > 0)

    # -- selection ---------------------------------------------------------------

    def selected_index(self) -> int | None:
        """Import-row index of the current row, when the filter shows it."""
        row = self._table.currentRow()
        if row < 0 or self._table.isRowHidden(row):
            return None
        cell = self._table.item(row, 0)
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    def _reference_item(self) -> ImportItem | None:
        """The item the buttons and the details pane refer to: the current
        row, else the first selected visible row (after Ctrl+A there may be
        a selection without a current row)."""
        index = self.selected_index()
        if index is None:
            indexes = self.selected_indexes()
            index = indexes[0] if indexes else None
        return self._session.items[index] if index is not None else None

    def selected_indexes(self) -> list[int]:
        """Import-row indexes of the selected rows that the filter shows.

        Rows hidden by "Show" or the search box are never included, even
        when a Shift+click range or Ctrl+A selected them: acting on rows the
        reviewer cannot see would exclude or approve them unnoticed.
        """
        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()})
        indexes = [
            self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            for row in rows
            if not self._table.isRowHidden(row)
        ]
        current_row = self._table.currentRow()
        if current_row >= 0 and not self._table.isRowHidden(current_row):
            current = self._table.item(current_row, 0).data(Qt.ItemDataRole.UserRole)
            if current not in indexes:
                indexes.append(current)
        return indexes

    def _on_selection_changed(self) -> None:
        self._deselect_hidden()
        if self._table.currentRow() < 0:
            # Ctrl+A selects without making a row current: give the details
            # pane and Edit / Show Source the first visible selected row,
            # without touching the selection.
            selected = self.selected_indexes()
            if selected:
                self._set_current_row(selected[0])
        self._selection_changed()

    def _set_current_row(self, index: int) -> None:
        for row in range(self._table.rowCount()):
            if self._table.item(row, 0).data(Qt.ItemDataRole.UserRole) == index:
                self._table.setCurrentCell(
                    row, COL_NAME, QItemSelectionModel.SelectionFlag.NoUpdate
                )
                return

    def _deselect_hidden(self) -> None:
        """Drop hidden rows from the selection (and from the current row)."""
        if self._deselecting:
            return
        model = self._table.selectionModel()
        hidden = [index for index in model.selectedRows() if self._table.isRowHidden(index.row())]
        current_row = self._table.currentRow()
        current_hidden = current_row >= 0 and self._table.isRowHidden(current_row)
        if not hidden and not current_hidden:
            return
        self._deselecting = True
        try:
            flags = QItemSelectionModel.SelectionFlag.Deselect | QItemSelectionModel.SelectionFlag.Rows
            for index in hidden:
                model.select(index, flags)
            if current_hidden:
                visible = [
                    index.row()
                    for index in model.selectedRows()
                    if not self._table.isRowHidden(index.row())
                ]
                if visible:
                    # NoUpdate: move the current row without clearing the
                    # rest of the selection.
                    self._table.setCurrentCell(
                        visible[0], COL_NAME, QItemSelectionModel.SelectionFlag.NoUpdate
                    )
                else:
                    self._table.setCurrentItem(None)
        finally:
            self._deselecting = False

    def select_indexes(self, indexes: list[int]) -> None:
        self._table.clearSelection()
        wanted = set(indexes)
        for row in range(self._table.rowCount()):
            if self._table.item(row, 0).data(Qt.ItemDataRole.UserRole) in wanted:
                self._table.selectRow(row)
        if indexes:
            self.select_index(indexes[-1])

    def select_visible(self) -> None:
        """Select every row the current filter shows (bulk approve after filtering)."""
        self._table.clearSelection()
        for row in range(self._table.rowCount()):
            if not self._table.isRowHidden(row):
                self._table.selectRow(row)

    def select_index(self, index: int) -> None:
        for row in range(self._table.rowCount()):
            if self._table.item(row, 0).data(Qt.ItemDataRole.UserRole) == index:
                self._table.selectRow(row)
                self._table.setCurrentCell(row, COL_NAME)
                return

    def _selection_changed(self) -> None:
        item = self._reference_item()
        has_item = item is not None and item.state != STATE_PUBLISHED
        # The buttons say how many (visible) rows they will act on.
        count = len(self.selected_indexes()) if has_item else 0
        suffix = f" ({count})" if count > 1 else ""
        self._edit_button.setEnabled(has_item)
        self._approve_button.setEnabled(has_item)
        self._approve_button.setText(f"Approve Selected{suffix}")
        self._exclude_button.setEnabled(has_item)
        verb = "Include" if item is not None and item.excluded else "Exclude"
        self._exclude_button.setText(f"{verb}{suffix}")
        self._source_button.setEnabled(
            item is not None
            and item.raw.page_number is not None
            and bool(self._session.source_path)
            and pdf_support_available()
        )
        self._details.setPlainText(self._details_text(item) if item else "")

    @staticmethod
    def _details_text(item: ImportItem) -> str:
        raw = item.raw
        lines = [
            f"Row {item.index + 1} — page {raw.page_number}, table {raw.table_index}, "
            f"row {raw.row_index}    state: {item.state}"
            + ("  (EXCLUDED)" if item.excluded else ""),
            f"Parameter id: {item.parameter_id}",
            "",
            "Verbatim extraction (column → text)",
        ]
        for header, text in zip(raw.columns, raw.cells):
            lines.append(f"  {' '.join(header.split()):<26}: {text!r}")
        if raw.provenance:
            lines.append("  mapped fields : " + ", ".join(sorted(raw.provenance)))
        if raw.extra:
            lines.append(f"  unmapped      : {raw.extra}")
        if raw.bbox:
            bbox = ", ".join(f"{v:.0f}" for v in raw.bbox)
            lines.append(f"  bbox (pt)     : ({bbox})   confidence {raw.confidence}")
        lines += ["", "Interpretation"]
        if item.interpretation:
            for key, value in item.interpretation.items():
                lines.append(f"  {key:<11}: {value}")
        else:
            lines.append("  (none — the row could not be normalized)")
        candidate = item.candidate
        lines += ["", "Candidate mapping"]
        if candidate is None:
            lines.append("  (none)")
        else:
            for occurrence in candidate.occurrences:
                for segment in occurrence.segments:
                    lines.append(
                        f"  occ {occurrence.index} seg {segment.sequence}: SF "
                        f"{format_subframes(segment.subframes)}  word {segment.word:03d}  "
                        f"bits {max(segment.msb, segment.lsb)}-{min(segment.msb, segment.lsb)}"
                    )
            lines.append(
                f"  type {candidate.parameter_type}  unit {candidate.unit or '—'}  "
                f"resolution {candidate.conversion.resolution:g}  "
                f"offset {candidate.conversion.offset:g}  "
                f"states 1={candidate.true_state or '—'} 0={candidate.false_state or '—'}"
            )
        lines += ["", "Issues"]
        for issue in item.normalization_issues:
            lines.append(f"  {issue.severity.upper():<7} {issue.rule}: {issue.message}")
        for issue in item.validation_issues:
            lines.append(f"  {issue.severity.upper():<7} {issue.rule_name}: {issue.message}")
        if not item.normalization_issues and not item.validation_issues:
            lines.append("  (none)")
        lines += ["", "History"]
        for event in item.history:
            lines.append(
                f"  {event.timestamp} {event.from_state or '-'} → {event.to_state}: {event.note}"
            )
        return "\n".join(lines)

    # -- actions ----------------------------------------------------------------

    def _current_item(self) -> ImportItem | None:
        return self._reference_item()

    def _run(self, action) -> None:
        self._error.setText("")
        try:
            action()
        except (ReviewError, ServiceError, ValueError) as exc:
            self._error.setText(str(exc))
        self._rebuild()

    def _edit_selected(self) -> None:
        item = self._current_item()
        if item is None or item.state == STATE_PUBLISHED:
            return
        parameter = (
            copy.deepcopy(item.candidate)
            if item.candidate is not None
            else self._blank_candidate(item)
        )
        dialog = ParameterEditDialog(
            self._ctx,
            parameter,
            self,
            commit=lambda candidate: self._session.update_candidate(item.index, candidate),
            dataframe_for_check=self._session.preview_dataframe(),
        )
        dialog.setWindowTitle(f"Review Row {item.index + 1} — {item.display_name}")
        if dialog.exec():
            self._error.setText("")
            self._rebuild()

    @staticmethod
    def _blank_candidate(item: ImportItem) -> ParameterDefinition:
        raw = item.raw
        source_type = raw.raw("parameter_type") or None
        return ParameterDefinition(
            id=item.parameter_id,
            mnemonic=raw.raw("parameter_name") or f"ROW {item.index + 1}",
            description=raw.raw("description"),
            source_parameter_type=source_type,
            parameter_type=normalize_source_type(source_type),
            unit=raw.raw("units") or None,
            notes=raw.raw("notes") or None,
            occurrences=[],
        )

    def _approve_selected(self) -> None:
        indexes = self.selected_indexes()
        if not indexes:
            return
        self._error.setText("")
        approved, refused = 0, []
        for index in indexes:
            item = self._session.items[index]
            if item.excluded or item.state in (STATE_APPROVED, STATE_PUBLISHED):
                continue
            try:
                self._session.approve(index)
                approved += 1
            except ReviewError as exc:
                refused.append(str(exc))
        if refused:
            self._error.setText(
                f"approved {approved}; not approved {len(refused)}: " + " | ".join(refused[:3])
                + (" …" if len(refused) > 3 else "")
            )
        self._rebuild()
        self.select_indexes(indexes)

    def _toggle_exclude_selected(self) -> None:
        indexes = self.selected_indexes()
        current = self._current_item()
        if not indexes or current is None:
            return
        exclude = not current.excluded

        def action() -> None:
            for index in indexes:
                if exclude:
                    self._session.exclude(index)
                else:
                    self._session.include(index)

        self._run(action)
        self.select_indexes(indexes)

    def _approve_all_validated(self) -> None:
        self._run(self._session.approve_all_validated)

    def _show_source(self) -> None:
        item = self._current_item()
        if item is None or item.raw.page_number is None or not self._session.source_path:
            return
        try:
            viewer = SourceViewerDialog(
                self._session.source_path,
                item.raw.page_number,
                item.raw.bbox,
                f"Source — page {item.raw.page_number}, row {item.raw.row_index}: {item.display_name}",
                self,
            )
        except Exception as exc:  # rendering is an aid; never block review on it
            self._error.setText(f"could not render source: {exc}")
            return
        viewer.exec()

    def _parse_metadata(self) -> dict:
        name = self._name.text().strip()
        if not name:
            raise ValueError("dataframe name is required")
        try:
            wps = int(self._wps.currentText().strip())
        except ValueError as exc:
            raise ValueError(f"WPS must be an integer, got {self._wps.currentText()!r}") from exc
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
        return {"dataframe_name": name, "wps": wps, "sync_words": sync_words}

    def _apply_metadata(self) -> bool:
        self._error.setText("")
        try:
            self._session.set_metadata(**self._parse_metadata())
        except (ValueError, ReviewError) as exc:
            self._error.setText(str(exc))
            self._rebuild()
            return False
        self._rebuild()
        return True

    def _try_publish(self) -> None:
        if not self._apply_metadata():
            return
        try:
            self._ctx.dataframe_service.publish_import(self._session)
        except ServiceError as exc:
            self._error.setText(str(exc))
            self._rebuild()
            return
        self.accept()


class SourceViewerDialog(QDialog):
    """Rendered source of one extracted row (spec §30 "where did this come from?").

    Opens as wide as the review dialog and fits the render to that width, so
    a full table row is readable without sideways scrolling.  "Row" shows the
    extracted row with the neighbouring rows for context; "Page" shows the
    whole page.  The extracted row is outlined in both views.  Zoom can be
    changed with the slider; "Fit width" follows the window size.
    """

    VIEW_ROW = "row"
    VIEW_PAGE = "page"

    def __init__(self, source_path: str, page_number: int, bbox, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self._pixmaps: dict[str, QPixmap] = {}
        self._fit_width = True
        self._zoom = 100

        row_png = None
        if bbox:
            row_png = render_region_png(
                source_path,
                page_number,
                bbox,
                margin=SOURCE_CONTEXT_MARGIN_X,
                dpi=SOURCE_REGION_DPI,
                margin_y=SOURCE_CONTEXT_MARGIN_Y,
                highlight=True,
            )
        page_png = render_page_png(
            source_path, page_number, dpi=SOURCE_PAGE_DPI, highlight=bbox or None
        )
        for key, png in ((self.VIEW_ROW, row_png), (self.VIEW_PAGE, page_png)):
            if png is None:
                continue
            pixmap = QPixmap()
            pixmap.loadFromData(png, "PNG")
            self._pixmaps[key] = pixmap

        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Show:"))
        self._view = QComboBox()
        if self.VIEW_ROW in self._pixmaps:
            self._view.addItem("Row with context", self.VIEW_ROW)
        self._view.addItem(f"Whole page {page_number}", self.VIEW_PAGE)
        self._view.currentIndexChanged.connect(lambda *_: self._render())
        toolbar.addWidget(self._view)
        toolbar.addSpacing(16)
        self._fit_check = QCheckBox("Fit width")
        self._fit_check.setChecked(True)
        self._fit_check.toggled.connect(self._fit_toggled)
        toolbar.addWidget(self._fit_check)
        toolbar.addWidget(QLabel("Zoom:"))
        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setRange(25, 300)
        self._zoom_slider.setValue(100)
        self._zoom_slider.setEnabled(False)
        self._zoom_slider.setMaximumWidth(220)
        self._zoom_slider.valueChanged.connect(self._zoom_changed)
        toolbar.addWidget(self._zoom_slider)
        self._zoom_label = QLabel("100%")
        self._zoom_label.setMinimumWidth(44)
        toolbar.addWidget(self._zoom_label)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        # Fit-to-width must follow the *viewport*, which only gets its real
        # size once the dialog is shown and laid out; a resize of the dialog
        # itself is not enough (the first render happens before that).
        self._scroll.viewport().installEventFilter(self)
        self._image = QLabel()
        self._image.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(self._image)
        layout.addWidget(self._scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        if parent is not None:
            self.resize(int(parent.width() * 0.95), int(parent.height() * 0.85))
        else:
            self.resize(1200, 700)
        self._render()

    # -- state ----------------------------------------------------------------

    def current_view(self) -> str:
        return self._view.currentData()

    def _fit_toggled(self, checked: bool) -> None:
        self._fit_width = checked
        self._zoom_slider.setEnabled(not checked)
        self._render()

    def _zoom_changed(self, value: int) -> None:
        self._zoom = value
        if not self._fit_width:
            self._render()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt override)
        if watched is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            if self._fit_width:
                self._render()
        return super().eventFilter(watched, event)

    # -- rendering --------------------------------------------------------------

    def _scale(self, pixmap: QPixmap) -> float:
        if not self._fit_width:
            return self._zoom / 100.0
        available = self._scroll.viewport().width() - 4
        if available <= 0 or pixmap.width() == 0:
            return 1.0
        return available / pixmap.width()

    def _render(self) -> None:
        pixmap = self._pixmaps.get(self.current_view())
        if pixmap is None:
            return
        scale = self._scale(pixmap)
        width = max(1, int(pixmap.width() * scale))
        scaled = pixmap.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
        self._image.setPixmap(scaled)
        self._image.resize(scaled.size())
        self._zoom_label.setText(f"{int(round(scale * 100))}%")
