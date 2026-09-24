"""Dataframe page (design spec §44): browse, search, inspect mappings and
provenance, edit mappings, validate, export."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...dataframe.adb_codec.legacy import (
    adb_warnings,
    legacy_setting_fields,
    segment_legacy_flag,
    trailing_legacy_fields,
)
from ...dataframe.validator import error_count, warning_count
from ...services import ServiceError
from ..common import fill_issues_table, make_issues_table, monospace_font
from .metadata_dialog import MetadataDialog
from .parameter_edit_dialog import ParameterEditDialog

ADB_FILE_FILTER = "ADB dataframes (*.adb);;All files (*)"


class DataframePage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx

        layout = QVBoxLayout(self)
        self._metadata_label = QLabel("No dataframe loaded.")
        self._metadata_label.setWordWrap(True)
        layout.addWidget(self._metadata_label)

        # -- editing toolbar ------------------------------------------------
        toolbar = QHBoxLayout()
        self._new_button = QPushButton("New Dataframe…")
        self._new_button.clicked.connect(self.new_dataframe)
        self._metadata_button = QPushButton("Edit Metadata…")
        self._metadata_button.clicked.connect(self.edit_metadata)
        self._add_button = QPushButton("Add Parameter…")
        self._add_button.clicked.connect(self.add_parameter)
        self._edit_button = QPushButton("Edit Parameter…")
        self._edit_button.clicked.connect(self.edit_selected)
        self._duplicate_button = QPushButton("Duplicate")
        self._duplicate_button.clicked.connect(self._duplicate_selected)
        self._remove_button = QPushButton("Remove")
        self._remove_button.clicked.connect(self._remove_selected)
        self._up_button = QPushButton("▲")
        self._up_button.setToolTip("Move parameter up (export order)")
        self._up_button.clicked.connect(lambda: self._move_selected(-1))
        self._down_button = QPushButton("▼")
        self._down_button.setToolTip("Move parameter down (export order)")
        self._down_button.clicked.connect(lambda: self._move_selected(+1))
        for button in (
            self._new_button,
            self._metadata_button,
            self._add_button,
            self._edit_button,
            self._duplicate_button,
            self._remove_button,
            self._up_button,
            self._down_button,
        ):
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search mnemonic / description…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        toolbar.addWidget(self._search, 1)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(
            ["Parameter", "Type", "Unit", "Res", "Offset", "Mapping"]
        )
        self._tree.setColumnWidth(0, 220)
        self._tree.currentItemChanged.connect(self._selection_changed)
        self._tree.itemDoubleClicked.connect(lambda *_: self.edit_selected())
        splitter.addWidget(self._tree)

        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        self._details.setFont(monospace_font())
        self._details.setPlaceholderText(
            "Select a parameter to inspect its mapping and source provenance."
        )
        splitter.addWidget(self._details)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 3)

        buttons = QHBoxLayout()
        validate = QPushButton("Validate Dataframe")
        validate.clicked.connect(self._validate)
        export = QPushButton("Export ADB…")
        export.clicked.connect(self._export)
        buttons.addWidget(validate)
        buttons.addWidget(export)
        buttons.addStretch(1)
        self._issue_summary = QLabel("")
        buttons.addWidget(self._issue_summary)
        layout.addLayout(buttons)

        self._issues_table = make_issues_table(self)
        layout.addWidget(self._issues_table, 1)

        ctx.dataframe_store.subscribe(self._on_store_event)
        self._refresh()

    def _on_store_event(self, event: dict) -> None:
        if event.get("type") == "dirty":
            self._render_metadata()  # only the "modified" marker changes
            return
        self._refresh()

    # -- rendering ----------------------------------------------------------

    def selected_parameter_id(self) -> str | None:
        item = self._tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _refresh(self) -> None:
        dataframe = self._ctx.dataframe_store.dataframe
        issues = self._ctx.dataframe_store.issues
        fill_issues_table(self._issues_table, issues)
        if issues:
            self._issue_summary.setText(
                f"{error_count(issues)} errors, {warning_count(issues)} warnings"
            )
        else:
            self._issue_summary.setText("")

        selected = self.selected_parameter_id()
        expanded = {
            self._tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(self._tree.topLevelItemCount())
            if self._tree.topLevelItem(i).isExpanded()
        }
        self._tree.clear()
        loaded = dataframe is not None
        for button in (self._metadata_button, self._add_button):
            button.setEnabled(loaded)
        self._render_metadata()
        if dataframe is None:
            self._details.setPlainText("")
            self._update_selection_buttons()
            return

        reselect = None
        for parameter in dataframe.parameters:
            item = QTreeWidgetItem(
                [
                    parameter.mnemonic,
                    parameter.parameter_type,
                    parameter.unit or "",
                    format(parameter.conversion.resolution, "g"),
                    format(parameter.conversion.offset, "g"),
                    f"{len(parameter.occurrences)} occurrence(s)",
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, parameter.id)
            item.setData(
                0,
                Qt.ItemDataRole.UserRole + 1,
                " ".join(
                    [
                        parameter.mnemonic,
                        parameter.description,
                        parameter.unit or "",
                        parameter.parameter_type,
                    ]
                ).lower(),
            )
            item.setToolTip(0, parameter.description or "")
            for occurrence in parameter.occurrences:
                occ_item = QTreeWidgetItem(
                    [f"Occurrence {occurrence.index}", "", "", "", "", ""]
                )
                occ_item.setData(0, Qt.ItemDataRole.UserRole, parameter.id)
                for segment in occurrence.segments:
                    sf = ",".join(str(s) for s in segment.subframes)
                    seg_item = QTreeWidgetItem(
                        [
                            f"Segment {segment.sequence}",
                            "",
                            "",
                            "",
                            "",
                            f"SF {sf}  word {segment.word:03d}  "
                            f"bits {segment.msb}-{segment.lsb}",
                        ]
                    )
                    seg_item.setData(0, Qt.ItemDataRole.UserRole, parameter.id)
                    occ_item.addChild(seg_item)
                item.addChild(occ_item)
            self._tree.addTopLevelItem(item)
            if parameter.id in expanded:
                item.setExpanded(True)
            if parameter.id == selected:
                reselect = item
        self._apply_filter(self._search.text())
        if reselect is not None:
            self._tree.setCurrentItem(reselect)
        else:
            self._selection_changed(None, None)
        self._update_selection_buttons()

    def _render_metadata(self) -> None:
        dataframe = self._ctx.dataframe_store.dataframe
        if dataframe is None:
            self._metadata_label.setText("No dataframe loaded.")
            return
        md = dataframe.metadata
        sync = ", ".join(str(s) for s in md.sync_words)
        dirty = " (modified, not exported)" if self._ctx.dataframe_store.dirty else ""
        self._metadata_label.setText(
            f"<b>{md.dataframe_name}</b>{dirty} — aircraft: {md.aircraft_type or '—'}, "
            f"revision: {md.revision or '—'}, issued: {md.issue_date or '—'}<br>"
            f"WPS: <b>{md.wps}</b>   sync words: {sync}   "
            f"superframe: {md.superframe_present if md.superframe_present is not None else '—'}<br>"
            f"source: {md.source_type} {md.source_filename or ''}   "
            f"sha256: {(md.source_hash or '—')[:16]}   "
            f"parameters: {len(dataframe.parameters)}"
        )

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            haystack = item.data(0, Qt.ItemDataRole.UserRole + 1) or ""
            item.setHidden(bool(needle) and needle not in haystack)

    def _update_selection_buttons(self) -> None:
        has_selection = self.selected_parameter_id() is not None
        for button in (
            self._edit_button,
            self._duplicate_button,
            self._remove_button,
            self._up_button,
            self._down_button,
        ):
            button.setEnabled(has_selection)

    def _selection_changed(self, current, _previous) -> None:
        self._update_selection_buttons()
        if current is None:
            self._details.setPlainText("")
            return
        parameter_id = current.data(0, Qt.ItemDataRole.UserRole)
        dataframe = self._ctx.dataframe_store.dataframe
        parameter = dataframe.get_parameter(parameter_id) if dataframe else None
        if parameter is None:
            self._details.setPlainText("")
            return
        lines = [
            f"Parameter id   : {parameter.id}",
            f"Mnemonic       : {parameter.mnemonic}",
            f"Description    : {parameter.description or '—'}",
            f"Source type    : {parameter.source_parameter_type or '—'}",
            f"Canonical type : {parameter.parameter_type}",
            f"Unit           : {parameter.unit or '—'}",
            f"Resolution     : {parameter.conversion.resolution:g}",
            f"Offset         : {parameter.conversion.offset:g}",
            f"Range          : {parameter.minimum} .. {parameter.maximum}",
            f"Decimals       : "
            f"{parameter.decimals if parameter.decimals is not None else 'auto'}",
            f"States         : 1={parameter.true_state or '—'}  "
            f"0={parameter.false_state or '—'}"
            + (
                "  table: "
                + ", ".join(f"{s.value}={s.label or '—'}" for s in parameter.states)
                if parameter.states
                else ""
            ),
            f"Notes          : {parameter.notes or '—'}",
            "",
            "Mapping",
        ]
        for occurrence in parameter.occurrences:
            lines.append(f"  Occurrence {occurrence.index}")
            for segment in occurrence.segments:
                sf = ",".join(str(s) for s in segment.subframes)
                flag = segment_legacy_flag(segment.source_raw)
                lines.append(
                    f"    seg {segment.sequence}: SF {sf}  word {segment.word:03d}  "
                    f"bits {segment.msb}-{segment.lsb}"
                    + (
                        f"  BCD weight {segment.bcd_weight:g}"
                        if segment.bcd_weight is not None
                        else ""
                    )
                    + (f"  legacy flag {flag!r}" if flag else "")
                )
        lines.append("")
        lines.append("Provenance")
        if parameter.provenance:
            p = parameter.provenance
            lines.append(f"  source        : {p.source_type or '—'}")
            lines.append(f"  record index  : {p.record_index}")
            trailing = trailing_legacy_fields(parameter)
            if trailing:
                lines.append(f"  trailing legacy fields: {trailing}")
            for warning in adb_warnings(parameter):
                lines.append(f"  import note   : {warning}")
            if p.raw_record:
                lines.append(f"  raw record    : {','.join(p.raw_record)}")
        else:
            lines.append("  (none — created in application)")
        if dataframe is not None:
            legacy = legacy_setting_fields(dataframe.metadata)
            if legacy:
                lines.append("")
                lines.append("Preserved unknown settings fields")
                for key, value in legacy.items():
                    lines.append(f"  {key} = {value!r}")
        self._details.setPlainText("\n".join(lines))

    # -- editing actions -----------------------------------------------------

    def new_dataframe(self) -> None:
        if self._ctx.dataframe_store.dirty:
            answer = QMessageBox.question(
                self,
                "New Dataframe",
                "The current dataframe has edits that were not exported. Discard them?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        MetadataDialog(self._ctx, "new", self).exec()

    def edit_metadata(self) -> None:
        if self._ctx.dataframe_store.dataframe is None:
            return
        MetadataDialog(self._ctx, "edit", self).exec()

    def add_parameter(self) -> None:
        if self._ctx.dataframe_store.dataframe is None:
            return
        ParameterEditDialog(self._ctx, None, self).exec()

    def edit_selected(self) -> None:
        dataframe = self._ctx.dataframe_store.dataframe
        parameter_id = self.selected_parameter_id()
        if dataframe is None or parameter_id is None:
            return
        parameter = dataframe.get_parameter(parameter_id)
        if parameter is not None:
            ParameterEditDialog(self._ctx, parameter, self).exec()

    def _duplicate_selected(self) -> None:
        parameter_id = self.selected_parameter_id()
        if parameter_id is None:
            return
        try:
            clone = self._ctx.dataframe_service.duplicate_parameter(parameter_id)
        except ServiceError as exc:
            QMessageBox.warning(self, "Duplicate Parameter", str(exc))
            return
        self._select(clone.id)

    def _remove_selected(self) -> None:
        dataframe = self._ctx.dataframe_store.dataframe
        parameter_id = self.selected_parameter_id()
        if dataframe is None or parameter_id is None:
            return
        parameter = dataframe.get_parameter(parameter_id)
        answer = QMessageBox.question(
            self,
            "Remove Parameter",
            f"Remove parameter {parameter.mnemonic if parameter else parameter_id!r}?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._ctx.dataframe_service.remove_parameter(parameter_id)
        except ServiceError as exc:
            QMessageBox.warning(self, "Remove Parameter", str(exc))

    def _move_selected(self, delta: int) -> None:
        parameter_id = self.selected_parameter_id()
        if parameter_id is None:
            return
        try:
            self._ctx.dataframe_service.move_parameter(parameter_id, delta)
        except ServiceError as exc:
            QMessageBox.warning(self, "Move Parameter", str(exc))

    def _select(self, parameter_id: str) -> None:
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == parameter_id:
                self._tree.setCurrentItem(item)
                return

    # -- validate / export ----------------------------------------------------

    def _validate(self) -> None:
        try:
            self._ctx.dataframe_service.revalidate()
        except ServiceError as exc:
            QMessageBox.warning(self, "Validate", str(exc))

    def _export(self) -> None:
        dataframe = self._ctx.dataframe_store.dataframe
        default = (
            f"{dataframe.metadata.dataframe_name}.adb" if dataframe else "export.adb"
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Export ADB", default, ADB_FILE_FILTER
        )
        if not path:
            return
        try:
            self._ctx.dataframe_service.export_adb(path)
        except ServiceError as exc:
            QMessageBox.critical(self, "Export ADB", str(exc))
