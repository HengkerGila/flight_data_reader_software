"""Main window (design spec §41, v2 §26O, §46C).

Header shows dataframe / WPS / source / frame index; tabs hold the pages
(Frame View, Parameters, Graphs, Dataframe, Hardware, Import, Help); the
toolbar holds the stream Start / Pause controls; the status bar shows
source, dataframe validity, WPS, parameter count, decode counts, stream
state and recording state.  Session recording and replay live in the File
menu (spec v2 §46C; moved off the toolbar so the toolbar controls the
stream itself).

A 50 ms timer pumps stream events from the acquisition thread onto the
stores (spec v2 §26Q: the acquisition path never touches widgets).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..dataframe.validator import error_count, warning_count
from ..domain.engineering import STATUS_VALID
from ..recording.session import SESSION_SUFFIX
from ..services import ServiceError
from ..state.stream_store import CONN_DISCONNECTED
from .dataframe_view.dataframe_page import ADB_FILE_FILTER, DataframePage
from .frame_view.frame_view_page import FRAME_FILE_FILTER, FrameViewPage
from .graph_view.graphs_page import GraphsPage
from .hardware.hardware_page import HardwarePage
from .help.help_page import HelpPage
from .importer.import_page import ImportPage
from .parameter_view.parameters_page import ParametersPage
from .stream_controls import StreamControls
from .theme import THEME_BY_ID, THEME_GROUPS, ThemeManager, save_theme_preference

PUMP_INTERVAL_MS = 50
SESSION_FILE_FILTER = f"Session files (*{SESSION_SUFFIX});;All files (*)"
REPLAY_SPEEDS = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0)


def application_version() -> str:
    try:
        return version("arinc717-reader")
    except PackageNotFoundError:  # running from a checkout without install
        return "dev"


class MainWindow(QMainWindow):
    def __init__(self, ctx, parent=None, theme_manager: ThemeManager | None = None):
        super().__init__(parent)
        self._ctx = ctx
        self._replay_speed = 1.0
        self._theme = theme_manager or ThemeManager(QApplication.instance())
        self.setWindowTitle("ARINC 717 Data Reader")
        self.resize(1360, 900)

        central = QWidget()
        layout = QVBoxLayout(central)

        header = QHBoxLayout()
        self._dataframe_label = QLabel()
        self._wps_label = QLabel()
        self._source_label = QLabel()
        self._frame_label = QLabel()
        for widget in (
            self._dataframe_label,
            self._wps_label,
            self._source_label,
            self._frame_label,
        ):
            header.addWidget(widget)
            header.addSpacing(24)
        header.addStretch(1)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.frame_view_page = FrameViewPage(ctx)
        self.parameters_page = ParametersPage(ctx)
        self.graphs_page = GraphsPage(ctx)
        self.dataframe_page = DataframePage(ctx)
        self.hardware_page = HardwarePage(ctx)
        self.import_page = ImportPage(ctx)
        self.help_page = HelpPage(ctx)
        self.tabs.addTab(self.frame_view_page, "Frame View")
        self.tabs.addTab(self.parameters_page, "Parameters")
        self.tabs.addTab(self.graphs_page, "Graphs")
        self.tabs.addTab(self.dataframe_page, "Dataframe")
        self.tabs.addTab(self.hardware_page, "Hardware")
        self.tabs.addTab(self.import_page, "Import")
        self.tabs.addTab(self.help_page, "Help")
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        toolbar = QToolBar("Stream")
        toolbar.setMovable(False)
        self.stream_controls = StreamControls(ctx, self.hardware_page)
        toolbar.addWidget(self.stream_controls)
        self.addToolBar(toolbar)

        self._status_source = QLabel()
        self._status_dataframe = QLabel()
        self._status_wps = QLabel()
        self._status_parameters = QLabel()
        self._status_decode = QLabel()
        self._status_stream = QLabel()
        self._status_recording = QLabel()
        for widget in (
            self._status_source,
            self._status_dataframe,
            self._status_wps,
            self._status_parameters,
            self._status_decode,
            self._status_stream,
            self._status_recording,
        ):
            self.statusBar().addPermanentWidget(widget)

        self._build_menu()

        self.parameters_page.show_in_frame_view.connect(self.show_sample_in_frame_view)

        ctx.dataframe_store.subscribe(lambda event: self._refresh())
        ctx.frame_store.subscribe(lambda event: self._refresh())
        ctx.engineering_store.subscribe(lambda event: self._refresh())
        ctx.stream_store.subscribe(self._on_stream_event)
        self._refresh()
        self._refresh_recording_actions()

        self._pump_timer = QTimer(self)
        self._pump_timer.setInterval(PUMP_INTERVAL_MS)
        self._pump_timer.timeout.connect(self._pump)
        self._pump_timer.start()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        new_dataframe = file_menu.addAction("New Dataframe…")
        new_dataframe.triggered.connect(self.dataframe_page.new_dataframe)
        open_adb = file_menu.addAction("Open ADB…")
        open_adb.triggered.connect(self._open_adb)
        import_pdf = file_menu.addAction("Import PDF…")
        import_pdf.triggered.connect(lambda: self.import_page.import_pdf())
        export_adb = file_menu.addAction("Export ADB…")
        export_adb.triggered.connect(self._export_adb)
        file_menu.addSeparator()
        load_frame = file_menu.addAction("Load Frame…")
        load_frame.triggered.connect(self._load_frame)
        save_frame = file_menu.addAction("Save Frame…")
        save_frame.triggered.connect(self._save_frame)
        file_menu.addSeparator()
        self._record_action = file_menu.addAction("Record Session…")
        self._record_action.triggered.connect(lambda: self.record_session())
        self._stop_record_action = file_menu.addAction("Stop Recording")
        self._stop_record_action.triggered.connect(self.stop_recording)
        file_menu.addSeparator()
        self._replay_action = file_menu.addAction("Replay Session…")
        self._replay_action.triggered.connect(lambda: self.replay_session())
        self._pause_replay_action = file_menu.addAction("Pause Replay")
        self._pause_replay_action.setCheckable(True)
        self._pause_replay_action.toggled.connect(self._toggle_replay_pause)
        self._stop_replay_action = file_menu.addAction("Stop Replay")
        self._stop_replay_action.triggered.connect(self.stop_replay)
        speed_menu = file_menu.addMenu("Replay Speed")
        self._speed_group = QActionGroup(self)
        self._speed_group.setExclusive(True)
        for speed in REPLAY_SPEEDS:
            action = speed_menu.addAction(f"{speed:g}×")
            action.setCheckable(True)
            action.setData(speed)
            action.setChecked(speed == self._replay_speed)
            action.triggered.connect(lambda _checked=False, s=speed: self.set_replay_speed(s))
            self._speed_group.addAction(action)
        file_menu.addSeparator()
        quit_action = file_menu.addAction("Quit")
        quit_action.triggered.connect(self.close)

        stream_menu = self.menuBar().addMenu("&Stream")
        connect = stream_menu.addAction("Connect")
        connect.triggered.connect(self.hardware_page.connect_device)
        disconnect = stream_menu.addAction("Disconnect")
        disconnect.triggered.connect(self.hardware_page.disconnect_device)
        stream_menu.addSeparator()
        start = stream_menu.addAction("Start Stream")
        start.triggered.connect(self.stream_controls.start)
        pause = stream_menu.addAction("Pause Stream")
        pause.triggered.connect(self.stream_controls.pause)

        settings_menu = self.menuBar().addMenu("&Settings")
        theme_menu = settings_menu.addMenu("Theme")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions = {}
        for index, (_group_label, theme_ids) in enumerate(THEME_GROUPS):
            if index:
                theme_menu.addSeparator()
            for theme_id in theme_ids:
                theme = THEME_BY_ID[theme_id]
                action = theme_menu.addAction(theme.label)
                action.setCheckable(True)
                action.setData(theme.id)
                action.setStatusTip(theme.description)
                action.setToolTip(theme.description)
                action.setChecked(theme.id == self._theme.current)
                action.triggered.connect(lambda _checked=False, t=theme.id: self.set_theme(t))
                self._theme_group.addAction(action)
                self._theme_actions[theme.id] = action
        theme_menu.setToolTipsVisible(True)

        help_menu = self.menuBar().addMenu("&Help")
        guide = help_menu.addAction("User Guide")
        guide.setShortcut(QKeySequence("F1"))
        guide.triggered.connect(lambda: self.show_help())
        about = help_menu.addAction("About")
        about.triggered.connect(self._about)

    # -- stream pump ---------------------------------------------------------

    def _pump(self) -> None:
        try:
            self._ctx.pump()
        except ServiceError as exc:  # never let a stream error kill the timer
            self._ctx.stream_store.add_event(0.0, exc.state, str(exc))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._pump_timer.stop()
        self._ctx.shutdown()
        super().closeEvent(event)

    # -- menu handlers ------------------------------------------------------

    def _open_adb(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open ADB", "", ADB_FILE_FILTER)
        if not path:
            return
        try:
            self._ctx.dataframe_service.load_adb(path)
        except ServiceError as exc:
            QMessageBox.critical(self, "Open ADB", str(exc))

    def _export_adb(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export ADB", "export.adb", ADB_FILE_FILTER
        )
        if not path:
            return
        try:
            self._ctx.dataframe_service.export_adb(path)
        except ServiceError as exc:
            QMessageBox.critical(self, "Export ADB", str(exc))

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

    # -- recording / replay (File menu) --------------------------------------

    def record_session(self, path: str | None = None) -> None:
        """Start recording; an explicit ``path`` skips the file dialog."""
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, "Record session", f"session{SESSION_SUFFIX}", SESSION_FILE_FILTER
            )
            if not path:
                return
        try:
            self._ctx.recording_service.start_recording(path)
        except ServiceError as exc:
            QMessageBox.critical(self, "Record Session", str(exc))

    def stop_recording(self) -> None:
        path = self._ctx.recording_service.stop_recording()
        if path is not None:
            self.statusBar().showMessage(f"Recorded {Path(path).name}", 8000)

    def replay_session(self, path: str | None = None) -> None:
        """Replay a session file; an explicit ``path`` skips the file dialog."""
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Replay session", "", SESSION_FILE_FILTER)
            if not path:
                return
        try:
            self._ctx.recording_service.start_replay(path, speed=self._replay_speed)
        except ServiceError as exc:
            QMessageBox.critical(self, "Replay Session", str(exc))
        self._pause_replay_action.setChecked(False)

    def _toggle_replay_pause(self, paused: bool) -> None:
        self._ctx.recording_service.pause_replay(paused)

    def stop_replay(self) -> None:
        self._ctx.recording_service.stop_replay()
        self._pause_replay_action.setChecked(False)

    def set_replay_speed(self, speed: float) -> None:
        self._replay_speed = float(speed)
        for action in self._speed_group.actions():
            action.setChecked(action.data() == self._replay_speed)

    @property
    def replay_speed(self) -> float:
        return self._replay_speed

    # -- settings -----------------------------------------------------------

    @property
    def theme(self) -> str:
        return self._theme.current

    def set_theme(self, theme: str, persist: bool = True) -> None:
        """Apply a theme from the catalogue (``system``, ``light``, ``dark``, ``nord`` …) and remember it."""
        self._theme.apply(theme)
        for name, action in self._theme_actions.items():
            action.setChecked(name == theme)
        if persist:
            try:
                save_theme_preference(theme)
            except Exception as exc:  # settings store unavailable: keep running
                self.statusBar().showMessage(f"Theme not saved: {exc}", 8000)

    # -- cross-page navigation ------------------------------------------------

    def show_sample_in_frame_view(self, key) -> None:
        """Show a decoded sample's words in the Frame View (from the Parameters page)."""
        self.frame_view_page.show_sample(key)
        self.tabs.setCurrentWidget(self.frame_view_page)

    # -- help ---------------------------------------------------------------

    def show_help(self, anchor: str | None = None) -> None:
        self.tabs.setCurrentWidget(self.help_page)
        if anchor:
            self.help_page.show_section(anchor)

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About ARINC 717 Data Reader",
            f"<b>ARINC 717 Data Reader</b> {application_version()}<br><br>"
            "Dataframe-driven ARINC 717 decoding, STM32 hardware-in-the-loop "
            "streaming, live graphs, session recording and PDF dataframe import.<br><br>"
            "The Help tab (F1) is the user guide.",
        )

    # -- header / status ----------------------------------------------------

    def _on_stream_event(self, event: dict) -> None:
        if event.get("field") in ("connection", "config", "replay"):
            self._refresh_stream_status()
            self._refresh_recording_actions()

    def _refresh_recording_actions(self) -> None:
        recording = self._ctx.recording_service
        has_source = self._ctx.serial_service.source is not None
        self._record_action.setEnabled(has_source and not recording.recording and not recording.replaying)
        self._stop_record_action.setEnabled(recording.recording)
        self._replay_action.setEnabled(not recording.recording and not recording.replaying)
        self._pause_replay_action.setEnabled(recording.replaying)
        self._stop_replay_action.setEnabled(recording.replaying)
        if recording.replaying and recording.replay is not None:
            paused = recording.replay.paused
            if self._pause_replay_action.isChecked() != paused:
                self._pause_replay_action.blockSignals(True)
                self._pause_replay_action.setChecked(paused)
                self._pause_replay_action.blockSignals(False)

    def _refresh_stream_status(self) -> None:
        store = self._ctx.stream_store
        if store.connection == CONN_DISCONNECTED:
            self._status_stream.setText("Stream: —")
        else:
            where = store.port or store.source_kind
            self._status_stream.setText(f"Stream: {store.connection} {where}".rstrip())
        recording = self._ctx.recording_service
        if recording.recording:
            self._status_recording.setText("● REC")
            self._status_recording.setStyleSheet("color: #c83c3c; font-weight: bold;")
        elif recording.replaying:
            self._status_recording.setText("▶ REPLAY")
            self._status_recording.setStyleSheet("color: #3c9660; font-weight: bold;")
        else:
            self._status_recording.setText("")
            self._status_recording.setStyleSheet("")

    def _refresh(self) -> None:
        dataframe = self._ctx.dataframe_store.dataframe
        issues = self._ctx.dataframe_store.issues
        frame = self._ctx.frame_store.frame
        values = self._ctx.engineering_store.values

        if dataframe:
            name = dataframe.metadata.source_filename or dataframe.metadata.dataframe_name
            modified = "*" if self._ctx.dataframe_store.dirty else ""
            self._dataframe_label.setText(f"Dataframe: <b>{name}{modified}</b>")
            self._wps_label.setText(f"WPS: <b>{dataframe.metadata.wps}</b>")
            self.setWindowTitle(f"ARINC 717 Data Reader — {name}{modified}")
        else:
            self._dataframe_label.setText("Dataframe: —")
            self._wps_label.setText("WPS: —")
            self.setWindowTitle("ARINC 717 Data Reader")
        live = " (live)" if self._ctx.frame_store.live else ""
        self._source_label.setText(
            f"Source: <b>{self._ctx.frame_store.source_name}</b>{live}"
        )
        self._frame_label.setText(
            f"Frame: <b>{frame.frame_index:06d}</b>" if frame else "Frame: —"
        )

        self._status_source.setText(f"Source: {self._ctx.frame_store.source_name}")
        if dataframe is None:
            self._status_dataframe.setText("DF: —")
        else:
            errors = error_count(issues)
            warnings = warning_count(issues)
            state = "VALID" if errors == 0 else f"{errors} ERRORS"
            if warnings:
                state += f", {warnings} warn"
            self._status_dataframe.setText(f"DF: {state}")
        self._status_wps.setText(f"WPS: {frame.wps if frame else '—'}")
        self._status_parameters.setText(
            f"Parameters: {len(dataframe.parameters) if dataframe else 0}"
        )
        ok = sum(1 for v in values if v.status == STATUS_VALID)
        failed = len(values) - ok
        self._status_decode.setText(f"Decode OK: {ok}  FAIL: {failed}")
        self._refresh_stream_status()
