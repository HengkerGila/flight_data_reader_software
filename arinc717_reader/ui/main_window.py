"""Main window (design spec §41).

Header shows dataframe / WPS / source / frame index; tabs hold the pages;
the status bar shows source, dataframe validity, WPS, parameter count and
decode success/failure counts.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..dataframe.validator import error_count, warning_count
from ..domain.engineering import STATUS_VALID
from ..services import ServiceError
from .dataframe_view.dataframe_page import ADB_FILE_FILTER, DataframePage
from .frame_view.frame_view_page import FRAME_FILE_FILTER, FrameViewPage
from .importer.import_page import ImportPage
from .parameter_view.parameters_page import ParametersPage
from .simulator.simulator_page import SimulatorPage


class MainWindow(QMainWindow):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self.setWindowTitle("ARINC 717 Data Reader")
        self.resize(1280, 840)

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
        self.dataframe_page = DataframePage(ctx)
        self.simulator_page = SimulatorPage(ctx)
        self.import_page = ImportPage(ctx)
        self.tabs.addTab(self.frame_view_page, "Frame View")
        self.tabs.addTab(self.parameters_page, "Parameters")
        self.tabs.addTab(self.dataframe_page, "Dataframe")
        self.tabs.addTab(self.simulator_page, "Simulator")
        self.tabs.addTab(self.import_page, "Import")
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        self._status_source = QLabel()
        self._status_dataframe = QLabel()
        self._status_wps = QLabel()
        self._status_parameters = QLabel()
        self._status_decode = QLabel()
        for widget in (
            self._status_source,
            self._status_dataframe,
            self._status_wps,
            self._status_parameters,
            self._status_decode,
        ):
            self.statusBar().addPermanentWidget(widget)

        self._build_menu()

        ctx.dataframe_store.subscribe(lambda event: self._refresh())
        ctx.frame_store.subscribe(lambda event: self._refresh())
        ctx.engineering_store.subscribe(lambda event: self._refresh())
        self._refresh()

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
        quit_action = file_menu.addAction("Quit")
        quit_action.triggered.connect(self.close)

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

    # -- header / status ----------------------------------------------------

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
        self._source_label.setText(
            f"Source: <b>{self._ctx.frame_store.source_name}</b>"
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
