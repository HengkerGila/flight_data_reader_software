"""Hardware page (spec v2 §26S, §46B).

Connection (port, baud, protocol mode), stream controls, live progress,
diagnostics counters, fault injection, device signal mode / PC engineering
signals, and the stream event log.  Every action goes through the serial
service; the page only renders the StreamStore.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...services import ServiceError
from ...services.serial_service import SIGNAL_SOURCE_DEVICE, SIGNAL_SOURCE_ENGINEERING
from ...sources.serial import sim_a717_protocol as proto
from ...sources.serial.transport import VIRTUAL_PORT
from ...state.stream_store import (
    CONN_CONNECTED,
    CONN_CONNECTING,
    CONN_DISCONNECTED,
    CONN_RECONNECTING,
    CONN_REPLAYING,
    CONN_STREAMING,
)
from ...streaming.signal_generator import (
    SIGNAL_MODES,
    SignalConfig,
    format_script,
    parse_script,
)
from ..common import COLOR_ERROR, COLOR_OK, COLOR_WARNING, monospace_font

BAUD_RATES = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)
DEVICE_MODE_LABELS = (
    (proto.DEVICE_MODE_WALK, "Device: raw random walk"),
    (proto.DEVICE_MODE_RANDOM, "Device: raw random"),
    (proto.DEVICE_MODE_FIXED, "Device: fixed frame"),
)
DIAGNOSTIC_FIELDS = (
    ("bytes_received", "Bytes received"),
    ("words_received", "Words received"),
    ("subframes_received", "Subframes received"),
    ("frames_received", "Frames received"),
    ("sync_losses", "Sync losses"),
    ("invalid_words", "Invalid words"),
    ("invalid_subframes", "Invalid subframes"),
    ("dropped_subframes", "Dropped subframes"),
    ("out_of_order_subframes", "Out-of-order subframes"),
    ("bad_packets", "Bad packets"),
    ("discarded_bytes", "Discarded bytes"),
)


class HardwarePage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._last_diag_render = 0.0
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        right = QVBoxLayout()
        root.addLayout(left, 3)
        root.addLayout(right, 2)

        # -- connection
        conn_box = QGroupBox("Connection")
        conn = QGridLayout(conn_box)
        conn.addWidget(QLabel("Port:"), 0, 0)
        self._port_combo = QComboBox()
        self._port_combo.setEditable(True)
        conn.addWidget(self._port_combo, 0, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_ports)
        conn.addWidget(refresh, 0, 2)
        conn.addWidget(QLabel("Baud:"), 1, 0)
        self._baud_combo = QComboBox()
        for baud in BAUD_RATES:
            self._baud_combo.addItem(str(baud), baud)
        self._baud_combo.setCurrentText("115200")
        conn.addWidget(self._baud_combo, 1, 1)
        conn.addWidget(QLabel("Protocol:"), 2, 0)
        self._protocol_combo = QComboBox()
        self._protocol_combo.addItem("SIM-A717 v1 · continuous word stream", proto.MODE_STREAM)
        self._protocol_combo.addItem("SIM-A717 v1 · framed packets (diagnostics)", proto.MODE_FRAMED)
        conn.addWidget(self._protocol_combo, 2, 1)
        conn.addWidget(QLabel("Virtual speed:"), 3, 0)
        self._speed_spin = QDoubleSpinBox()
        self._speed_spin.setRange(0.1, 1000.0)
        self._speed_spin.setValue(1.0)
        self._speed_spin.setSuffix("×")
        self._speed_spin.setToolTip("Time factor for the in-process virtual device only")
        conn.addWidget(self._speed_spin, 3, 1)
        self._status_label = QLabel("DISCONNECTED")
        self._status_label.setFont(monospace_font())
        conn.addWidget(QLabel("Status:"), 4, 0)
        conn.addWidget(self._status_label, 4, 1, 1, 2)
        self._info_label = QLabel("")
        self._info_label.setWordWrap(True)
        conn.addWidget(self._info_label, 5, 0, 1, 3)
        buttons = QHBoxLayout()
        self._connect_button = QPushButton("Connect")
        self._connect_button.clicked.connect(self.connect_device)
        self._disconnect_button = QPushButton("Disconnect")
        self._disconnect_button.clicked.connect(self.disconnect_device)
        self._start_button = QPushButton("Start stream")
        self._start_button.clicked.connect(self.start_stream)
        self._stop_button = QPushButton("Stop stream")
        self._stop_button.clicked.connect(self.stop_stream)
        self._reset_button = QPushButton("Reset device")
        self._reset_button.clicked.connect(self.reset_device)
        for button in (
            self._connect_button,
            self._disconnect_button,
            self._start_button,
            self._stop_button,
            self._reset_button,
        ):
            buttons.addWidget(button)
        conn.addLayout(buttons, 6, 0, 1, 3)
        left.addWidget(conn_box)

        # -- stream progress
        stream_box = QGroupBox("Stream")
        form = QFormLayout(stream_box)
        self._wps_label = QLabel("—")
        self._frame_label = QLabel("—")
        self._subframe_label = QLabel("—")
        self._word_label = QLabel("—")
        self._rate_label = QLabel("—")
        self._sync_label = QLabel("—")
        for label in (
            self._wps_label,
            self._frame_label,
            self._subframe_label,
            self._word_label,
            self._rate_label,
            self._sync_label,
        ):
            label.setFont(monospace_font())
        form.addRow("WPS:", self._wps_label)
        form.addRow("Frame:", self._frame_label)
        form.addRow("Subframe:", self._subframe_label)
        form.addRow("Word:", self._word_label)
        form.addRow("Rate:", self._rate_label)
        form.addRow("Sync:", self._sync_label)
        left.addWidget(stream_box)

        # -- signal source
        signal_box = QGroupBox("Simulated signals")
        signal_layout = QVBoxLayout(signal_box)
        row = QHBoxLayout()
        row.addWidget(QLabel("Source:"))
        self._signal_combo = QComboBox()
        self._signal_combo.addItem("PC: engineering signals (encoded through the dataframe)", SIGNAL_SOURCE_ENGINEERING)
        for mode, label in DEVICE_MODE_LABELS:
            self._signal_combo.addItem(label, mode)
        # Reflect the service's current choice before listening for changes,
        # so the page never shows a source the device is not using.
        service = ctx.serial_service
        initial = (
            SIGNAL_SOURCE_ENGINEERING
            if service.signal_source == SIGNAL_SOURCE_ENGINEERING
            else service.device_mode
        )
        self._signal_combo.setCurrentIndex(max(self._signal_combo.findData(initial), 0))
        self._signal_combo.currentIndexChanged.connect(self._signal_source_changed)
        row.addWidget(self._signal_combo, 1)
        signal_layout.addLayout(row)
        self._signal_table = QTableWidget(0, 7)
        self._signal_table.setHorizontalHeaderLabels(
            ["Parameter", "Mode", "Low", "High", "Value", "Period (s)", "Script (t:v, …)"]
        )
        self._signal_table.setToolTip(
            "Low/High bound every mode. Value: Fixed value, Random Walk start, Step start.\n"
            "Period: Sine period, Ramp duration, Step interval (seconds).\n"
            "Script: time:value points for the Scripted mode, e.g. 0:0, 10:20, 20:0 "
            "(linear interpolation, seconds of simulated time)."
        )
        self._signal_table.verticalHeader().setVisible(False)
        header = self._signal_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._signal_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._signal_table.cellChanged.connect(self._signal_cell_changed)
        signal_layout.addWidget(self._signal_table)
        left.addWidget(signal_box, 1)

        # -- diagnostics
        diag_box = QGroupBox("Diagnostics")
        diag = QGridLayout(diag_box)
        self._diag_labels: dict[str, QLabel] = {}
        for i, (field, label) in enumerate(DIAGNOSTIC_FIELDS):
            diag.addWidget(QLabel(label + ":"), i // 2, (i % 2) * 2)
            value = QLabel("0")
            value.setFont(monospace_font())
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            diag.addWidget(value, i // 2, (i % 2) * 2 + 1)
            self._diag_labels[field] = value
        right.addWidget(diag_box)

        # -- fault injection
        fault_box = QGroupBox("Fault injection")
        fault = QHBoxLayout(fault_box)
        self._fault_combo = QComboBox()
        for name in proto.FAULTS:
            self._fault_combo.addItem(name, name)
        fault.addWidget(self._fault_combo, 1)
        self._fault_count = QSpinBox()
        self._fault_count.setRange(0, proto.FAULT_CONTINUOUS)
        self._fault_count.setValue(1)
        self._fault_count.setToolTip("How many times the fault fires (0 = off, 65535 = continuous)")
        fault.addWidget(self._fault_count)
        inject = QPushButton("Inject")
        inject.clicked.connect(self.inject_fault)
        fault.addWidget(inject)
        right.addWidget(fault_box)

        # -- event log
        events_box = QGroupBox("Stream events")
        events_layout = QVBoxLayout(events_box)
        self._events = QTableWidget(0, 3)
        self._events.setHorizontalHeaderLabels(["Time", "Event", "Message"])
        self._events.verticalHeader().setVisible(False)
        self._events.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        eh = self._events.horizontalHeader()
        eh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        eh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        events_layout.addWidget(self._events)
        right.addWidget(events_box, 1)

        self._rendered_events = 0
        ctx.stream_store.subscribe(self._on_stream_event)
        ctx.dataframe_store.subscribe(self._on_dataframe_event)
        self.refresh_ports()
        self._rebuild_signal_table()
        self._render_all()

    # -- actions ---------------------------------------------------------

    def refresh_ports(self) -> None:
        current = self._port_combo.currentText()
        self._port_combo.clear()
        for device, description in self._ctx.serial_service.available_ports():
            self._port_combo.addItem(f"{device}  ({description})" if description else device, device)
        if current:
            index = self._port_combo.findData(current)
            self._port_combo.setCurrentIndex(index if index >= 0 else 0)

    def selected_port(self) -> str:
        data = self._port_combo.currentData()
        if data:
            return str(data)
        text = self._port_combo.currentText().strip()
        return text.split()[0] if text else VIRTUAL_PORT

    def connect_device(self) -> None:
        try:
            self._ctx.serial_service.connect(
                self.selected_port(),
                baudrate=int(self._baud_combo.currentData()),
                protocol_mode=self._protocol_combo.currentData(),
                virtual_speed=float(self._speed_spin.value()),
            )
        except ServiceError as exc:
            QMessageBox.critical(self, "Connect", str(exc))

    def disconnect_device(self) -> None:
        self._ctx.serial_service.disconnect()

    def start_stream(self) -> None:
        try:
            self._ctx.serial_service.start_stream()
        except ServiceError as exc:
            QMessageBox.critical(self, "Start stream", str(exc))

    def stop_stream(self) -> None:
        try:
            self._ctx.serial_service.stop_stream()
        except ServiceError as exc:
            QMessageBox.critical(self, "Stop stream", str(exc))

    def reset_device(self) -> None:
        try:
            self._ctx.serial_service.reset_device()
        except ServiceError as exc:
            QMessageBox.critical(self, "Reset device", str(exc))

    def inject_fault(self) -> None:
        try:
            self._ctx.serial_service.inject_fault(self._fault_combo.currentData(), int(self._fault_count.value()))
        except ServiceError as exc:
            QMessageBox.critical(self, "Inject fault", str(exc))

    def _signal_source_changed(self, _index: int) -> None:
        choice = self._signal_combo.currentData()
        service = self._ctx.serial_service
        try:
            if choice == SIGNAL_SOURCE_ENGINEERING:
                service.set_signal_source(SIGNAL_SOURCE_ENGINEERING)
            else:
                service.set_device_mode(choice)
                service.set_signal_source(SIGNAL_SOURCE_DEVICE)
        except ServiceError as exc:
            QMessageBox.critical(self, "Signal source", str(exc))
        self._signal_table.setEnabled(choice == SIGNAL_SOURCE_ENGINEERING)

    # -- signal table ----------------------------------------------------

    def _on_dataframe_event(self, event: dict) -> None:
        if event.get("type") == "dataframe":
            self._rebuild_signal_table()

    def _rebuild_signal_table(self) -> None:
        table = self._signal_table
        table.blockSignals(True)
        table.setRowCount(0)
        dataframe = self._ctx.dataframe_store.dataframe
        if dataframe is not None and dataframe.metadata.sync_words:
            try:
                configs = self._ctx.serial_service.signal_configs()
            except ServiceError:
                configs = {}
            for parameter_id, config in configs.items():
                parameter = dataframe.get_parameter(parameter_id)
                row = table.rowCount()
                table.insertRow(row)
                name = QTableWidgetItem(parameter.mnemonic if parameter else parameter_id)
                name.setData(Qt.ItemDataRole.UserRole, parameter_id)
                name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, 0, name)
                combo = QComboBox()
                combo.addItems(SIGNAL_MODES)
                combo.setCurrentText(config.mode)
                combo.currentTextChanged.connect(lambda _t, r=row: self._signal_row_changed(r))
                table.setCellWidget(row, 1, combo)
                for column, value in zip((2, 3, 4, 5), (config.low, config.high, config.value, config.period)):
                    table.setItem(row, column, QTableWidgetItem(f"{value:g}"))
                table.setItem(row, 6, QTableWidgetItem(format_script(config.script)))
        table.blockSignals(False)
        self._signal_table.setEnabled(self._signal_combo.currentData() == SIGNAL_SOURCE_ENGINEERING)

    def _signal_cell_changed(self, row: int, _column: int) -> None:
        self._signal_row_changed(row)

    def _signal_row_changed(self, row: int) -> None:
        table = self._signal_table
        item = table.item(row, 0)
        if item is None:
            return
        parameter_id = item.data(Qt.ItemDataRole.UserRole)
        combo = table.cellWidget(row, 1)

        def number(column: int, default: float) -> float:
            cell = table.item(row, column)
            try:
                return float(cell.text()) if cell else default
            except ValueError:
                return default

        try:
            current = self._ctx.serial_service.signal_configs()[parameter_id]
        except (ServiceError, KeyError):
            return
        script_cell = table.item(row, 6)
        script = current.script
        if script_cell is not None:
            try:
                script = parse_script(script_cell.text())
                problem = ""
            except ValueError as exc:
                problem = str(exc)
            # Mark a malformed script in place (no modal dialog from a cell edit)
            # and keep the previous points until it is corrected.
            table.blockSignals(True)
            script_cell.setForeground(COLOR_ERROR if problem else table.palette().text())
            script_cell.setToolTip(problem)
            table.blockSignals(False)
        config = SignalConfig(
            mode=combo.currentText() if combo else current.mode,
            low=number(2, current.low),
            high=number(3, current.high),
            value=number(4, current.value),
            period=number(5, current.period),
            step_size=current.step_size,
            script=script,
            enabled=current.enabled,
        )
        try:
            self._ctx.serial_service.configure_signal(parameter_id, config)
        except ServiceError as exc:
            QMessageBox.critical(self, "Signal", str(exc))

    # -- rendering -------------------------------------------------------

    def _on_stream_event(self, event: dict) -> None:
        field = event.get("field")
        if field == "diagnostics":
            now = time.monotonic()
            if now - self._last_diag_render < 0.2:
                return
            self._last_diag_render = now
            self._render_diagnostics()
        elif field == "progress":
            self._render_progress()
        elif field == "event":
            self._render_events()
        else:
            self._render_all()

    def _render_all(self) -> None:
        self._render_connection()
        self._render_progress()
        self._render_diagnostics()
        self._render_events()

    def _render_connection(self) -> None:
        store = self._ctx.stream_store
        state = store.connection
        colors = {
            CONN_DISCONNECTED: COLOR_ERROR,
            CONN_CONNECTING: COLOR_WARNING,
            CONN_RECONNECTING: COLOR_WARNING,
            CONN_CONNECTED: COLOR_OK,
            CONN_STREAMING: COLOR_OK,
            CONN_REPLAYING: COLOR_OK,
        }
        text = state
        if state == CONN_RECONNECTING and store.last_error:
            text += f" — {store.last_error}"
        elif state == CONN_DISCONNECTED and store.last_error:
            text += f" — {store.last_error}"
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {colors.get(state, COLOR_WARNING).name()}; font-weight: bold;")
        self._info_label.setText(store.device_info if state in (CONN_CONNECTED, CONN_STREAMING) else "")
        connected = state in (CONN_CONNECTED, CONN_STREAMING)
        self._connect_button.setEnabled(state in (CONN_DISCONNECTED,))
        self._disconnect_button.setEnabled(state not in (CONN_DISCONNECTED, CONN_REPLAYING))
        self._start_button.setEnabled(state == CONN_CONNECTED)
        self._stop_button.setEnabled(state == CONN_STREAMING)
        self._reset_button.setEnabled(connected)
        for widget in (self._port_combo, self._baud_combo, self._protocol_combo, self._speed_spin):
            widget.setEnabled(state == CONN_DISCONNECTED)
        self._wps_label.setText(str(store.wps) if store.wps else "—")

    def _render_progress(self) -> None:
        store = self._ctx.stream_store
        wps = store.wps or 0
        self._frame_label.setText(f"{store.frame_index:06d}" if store.frame_index is not None else "—")
        if store.subframe_states:
            states = " ".join(f"SF{i + 1}:{s[:4].lower()}" for i, s in enumerate(store.subframe_states))
        else:
            states = ""
        self._subframe_label.setText(
            f"{store.subframe} / 4   {states}" if store.subframe is not None else (states or "—")
        )
        self._word_label.setText(f"{store.word_progress:3d} / {wps}" if wps else "—")
        if store.replay_position is not None:
            total = f" / {store.replay_duration:.1f} s" if store.replay_duration else ""
            self._rate_label.setText(f"replay {store.replay_position:.1f} s{total}")
        else:
            self._rate_label.setText(f"{store.diagnostics.word_rate:7.1f} WPS")
        sync = store.diagnostics.sync_state
        if store.diagnostics.detected_wps:
            sync += f"  (sync spacing looks like {store.diagnostics.detected_wps} WPS)"
        self._sync_label.setText(sync)

    def _render_diagnostics(self) -> None:
        snapshot = self._ctx.stream_store.diagnostics
        for field, label in self._diag_labels.items():
            label.setText(str(getattr(snapshot, field)))

    def _render_events(self) -> None:
        events = list(self._ctx.stream_store.events)
        if len(events) < self._rendered_events:
            self._events.setRowCount(0)
            self._rendered_events = 0
        # The store keeps a bounded deque; when it wrapped, re-render fully.
        if self._events.rowCount() and len(events) == self._ctx.stream_store.MAX_EVENTS:
            self._events.setRowCount(0)
            self._rendered_events = 0
        for record in events[self._rendered_events :]:
            row = self._events.rowCount()
            self._events.insertRow(row)
            stamp = time.strftime("%H:%M:%S", time.localtime(record.timestamp))
            self._events.setItem(row, 0, QTableWidgetItem(stamp))
            kind = QTableWidgetItem(record.kind)
            if "LOST" in record.kind or "ERROR" in record.kind or "DISCONNECTED" in record.kind:
                kind.setForeground(COLOR_ERROR)
            elif "INCOMPLETE" in record.kind or "MISMATCH" in record.kind or "FAULT" in record.kind:
                kind.setForeground(COLOR_WARNING)
            self._events.setItem(row, 1, kind)
            self._events.setItem(row, 2, QTableWidgetItem(record.message))
        self._rendered_events = len(events)
        self._events.scrollToBottom()
