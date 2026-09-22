"""Simulator page (design spec §22–§26, §45).

Scenario mode lists only safely encodable parameters, applies requested
engineering values through the ParameterEncoder, and shows the closed-loop
requested-vs-decoded comparison with quantization-aware tolerance.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...domain.engineering import format_engineering_value
from ...domain.parameter import TYPE_DISCRETE
from ...services import ServiceError
from ..common import COLOR_ERROR, COLOR_OK, monospace_font

SKIP = ""


class SimulatorPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx

        layout = QVBoxLayout(self)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Random", "Manual", "Scenario"])
        mode_row.addWidget(self._mode_combo)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_random_panel())
        self._stack.addWidget(self._build_manual_panel())
        self._stack.addWidget(self._build_scenario_panel())
        self._mode_combo.currentIndexChanged.connect(self._stack.setCurrentIndex)
        layout.addWidget(self._stack)

        ctx.dataframe_store.subscribe(self._on_dataframe_event)
        self._rebuild_scenario_rows()

    # -- Random -------------------------------------------------------------

    def _build_random_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        note = QLabel(
            "<b>RAW RANDOM</b> — every word is a uniform random 0..4095 value. "
            "Useful for rendering and decoder stress testing; engineering "
            "values will not be meaningful."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self._seed_check = QCheckBox("Use seed:")
        self._seed_spin = QSpinBox()
        self._seed_spin.setRange(0, 2**31 - 1)
        self._seed_spin.setEnabled(False)
        self._seed_check.toggled.connect(self._seed_spin.setEnabled)
        generate = QPushButton("Generate Random Frame")
        generate.clicked.connect(self._generate_random)
        row.addWidget(self._seed_check)
        row.addWidget(self._seed_spin)
        row.addWidget(generate)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return panel

    def _generate_random(self) -> None:
        seed = self._seed_spin.value() if self._seed_check.isChecked() else None
        self._ctx.frame_service.new_random(seed)

    # -- Manual -------------------------------------------------------------

    def _build_manual_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        note = QLabel(
            "Create a blank frame (all words 0), then edit individual words in "
            "the Frame View tab — double-click a cell to open the edit dialog. "
            "Use Save/Load Frame there to persist your work."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self._sync_check = QCheckBox("Insert sync words from dataframe")
        self._sync_check.setChecked(True)
        blank = QPushButton("Create Blank Frame")
        blank.clicked.connect(self._create_blank)
        row.addWidget(self._sync_check)
        row.addWidget(blank)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        return panel

    def _create_blank(self) -> None:
        self._ctx.frame_service.new_blank(
            with_sync_words=self._sync_check.isChecked()
        )

    # -- Scenario -----------------------------------------------------------

    def _build_scenario_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        self._scenario_hint = QLabel(
            "Enter target engineering values (blank = leave parameter alone), "
            "then Apply.  Only safely encodable parameters are listed."
        )
        self._scenario_hint.setWordWrap(True)
        layout.addWidget(self._scenario_hint)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self._scenario_table = QTableWidget(0, 5)
        self._scenario_table.setHorizontalHeaderLabels(
            ["Parameter", "Unit", "Min", "Max", "Target"]
        )
        self._scenario_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._scenario_table.verticalHeader().setVisible(False)
        header = self._scenario_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        splitter.addWidget(self._scenario_table)

        self._results_table = QTableWidget(0, 7)
        self._results_table.setHorizontalHeaderLabels(
            ["Parameter", "Requested", "Raw", "Decoded", "Δ", "Tolerance", "Result"]
        )
        self._results_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._results_table.verticalHeader().setVisible(False)
        results_header = self._results_table.horizontalHeader()
        results_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        results_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        splitter.addWidget(self._results_table)
        layout.addWidget(splitter)

        row = QHBoxLayout()
        self._from_current_check = QCheckBox("Start from current frame")
        apply_button = QPushButton("Apply Scenario")
        apply_button.clicked.connect(self._apply_scenario)
        row.addWidget(self._from_current_check)
        row.addWidget(apply_button)
        row.addStretch(1)
        layout.addLayout(row)
        return panel

    def _on_dataframe_event(self, event: dict) -> None:
        if event.get("type") == "dataframe":
            self._rebuild_scenario_rows()

    def _rebuild_scenario_rows(self) -> None:
        parameters = self._ctx.simulation_service.encodable_parameters()
        self._scenario_rows = parameters
        self._scenario_table.setRowCount(len(parameters))
        mono = monospace_font()
        for row, parameter in enumerate(parameters):
            self._scenario_table.setItem(
                row, 0, QTableWidgetItem(parameter.mnemonic)
            )
            self._scenario_table.setItem(
                row, 1, QTableWidgetItem(parameter.unit or "—")
            )
            self._scenario_table.setItem(
                row,
                2,
                QTableWidgetItem(
                    "—" if parameter.minimum is None else format(parameter.minimum, "g")
                ),
            )
            self._scenario_table.setItem(
                row,
                3,
                QTableWidgetItem(
                    "—" if parameter.maximum is None else format(parameter.maximum, "g")
                ),
            )
            if parameter.parameter_type == TYPE_DISCRETE:
                combo = QComboBox()
                combo.addItems(
                    [
                        SKIP,
                        parameter.false_state or "FALSE",
                        parameter.true_state or "TRUE",
                    ]
                )
                self._scenario_table.setCellWidget(row, 4, combo)
            else:
                edit = QLineEdit()
                edit.setPlaceholderText("blank = skip")
                edit.setFont(mono)
                self._scenario_table.setCellWidget(row, 4, edit)
        self._results_table.setRowCount(0)

    def _collect_scenario(self) -> dict:
        scenario: dict = {}
        bad: list[str] = []
        for row, parameter in enumerate(self._scenario_rows):
            widget = self._scenario_table.cellWidget(row, 4)
            if isinstance(widget, QComboBox):
                text = widget.currentText()
                if text != SKIP:
                    scenario[parameter.id] = text
            elif isinstance(widget, QLineEdit):
                text = widget.text().strip()
                if not text:
                    continue
                try:
                    scenario[parameter.id] = float(text)
                except ValueError:
                    bad.append(f"{parameter.mnemonic}: {text!r} is not a number")
        if bad:
            raise ServiceError("INVALID_SCENARIO_INPUT", "; ".join(bad))
        return scenario

    def _apply_scenario(self) -> None:
        try:
            scenario = self._collect_scenario()
            raw_patterns = self._ctx.simulation_service.apply_scenario(
                scenario,
                start_from_current=self._from_current_check.isChecked(),
            )
        except ServiceError as exc:
            QMessageBox.warning(self, "Apply Scenario", str(exc))
            return
        report = self._ctx.simulation_service.closed_loop_report(
            scenario, raw_patterns
        )
        self._fill_results(report)

    def _fill_results(self, report) -> None:
        mono = monospace_font()
        self._results_table.setRowCount(len(report))
        for row, entry in enumerate(report):
            requested = (
                entry.requested
                if isinstance(entry.requested, str)
                else format(float(entry.requested), "g")
            )
            decoded = format_engineering_value(entry.decoded)
            delta = "—" if entry.delta is None else format(entry.delta, ".4g")
            tolerance = (
                "exact" if entry.tolerance == 0 else format(entry.tolerance, ".4g")
            )
            cells = [
                entry.parameter.mnemonic,
                requested,
                format(entry.raw_pattern, "b"),
                decoded,
                delta,
                tolerance,
                "PASS" if entry.passed else "FAIL",
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column in (2, 3):
                    item.setFont(mono)
                if column == 6:
                    item.setForeground(COLOR_OK if entry.passed else COLOR_ERROR)
                self._results_table.setItem(row, column, item)
