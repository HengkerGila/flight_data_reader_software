"""Import page (design spec §46): Import ADB, Import PDF, Review Queue,
Validation Results, Export ADB.

PDF import is labeled Experimental: native-text extraction is implemented,
OCR is not, and every imported row passes through the review dialog before
it can reach the working dataframe.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...dataframe.pdf_importer import ImportProfile, ImportSession, pdf_support_available
from ...dataframe.pdf_importer.review import STATE_APPROVED, STATE_REVIEW_REQUIRED
from ...dataframe.validator import error_count, warning_count
from ...demo import build_demo_dataframe
from ...services import ServiceError
from ..common import fill_issues_table, make_issues_table
from ..dataframe_view.dataframe_page import ADB_FILE_FILTER
from ..dataframe_view.metadata_dialog import MetadataDialog
from .import_worker import PdfImportWorker
from .pdf_review_dialog import PdfReviewDialog

PDF_FILE_FILTER = "PDF documents (*.pdf);;All files (*)"


class ImportPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._session: ImportSession | None = None
        self._last_published: str | None = None
        self._worker: PdfImportWorker | None = None
        self._progress: QProgressDialog | None = None

        layout = QVBoxLayout(self)

        adb_group = QGroupBox("ADB dataframes")
        adb_layout = QHBoxLayout(adb_group)
        import_button = QPushButton("Import ADB…")
        import_button.clicked.connect(self._import_adb)
        export_button = QPushButton("Export ADB…")
        export_button.clicked.connect(self._export_adb)
        demo_button = QPushButton("Load Demo Dataframe")
        demo_button.clicked.connect(self._load_demo)
        new_button = QPushButton("New Dataframe…")
        new_button.setToolTip("Create an empty dataframe to author manually")
        new_button.clicked.connect(self._new_dataframe)
        adb_layout.addWidget(import_button)
        adb_layout.addWidget(export_button)
        adb_layout.addWidget(demo_button)
        adb_layout.addWidget(new_button)
        adb_layout.addStretch(1)
        layout.addWidget(adb_group)

        pdf_group = QGroupBox("PDF dataframes — Experimental")
        pdf_layout = QVBoxLayout(pdf_group)
        pdf_note = QLabel(
            "Imports a dataframe document's parameter table: born-digital pages "
            "through the PDF text, scanned pages through grid detection on the page "
            "image with the embedded text layer or OCR. Extracted rows keep page / "
            "bounding box / verbatim text provenance, are normalized by explicit "
            "rules, validated, and go through manual review — only approved rows "
            "are published to the workspace."
        )
        pdf_note.setWordWrap(True)
        pdf_layout.addWidget(pdf_note)
        pdf_row = QHBoxLayout()
        self._pdf_button = QPushButton("Import PDF…")
        self._pdf_button.clicked.connect(lambda: self.import_pdf())
        if not pdf_support_available():
            self._pdf_button.setEnabled(False)
            self._pdf_button.setToolTip(
                "PDF import needs the 'pymupdf' package (pip install pymupdf)."
            )
        pdf_row.addWidget(self._pdf_button)
        pdf_row.addStretch(1)
        pdf_layout.addLayout(pdf_row)
        layout.addWidget(pdf_group)

        review_group = QGroupBox("Review queue")
        review_layout = QHBoxLayout(review_group)
        self._review_label = QLabel()
        self._review_label.setWordWrap(True)
        review_layout.addWidget(self._review_label, 1)
        self._review_button = QPushButton("Open Review…")
        self._review_button.clicked.connect(self.open_review)
        review_layout.addWidget(self._review_button)
        layout.addWidget(review_group)

        self._summary = QLabel("No dataframe loaded.")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        layout.addWidget(QLabel("Validation results:"))
        self._issues_table = make_issues_table(self)
        layout.addWidget(self._issues_table, 1)

        ctx.dataframe_store.subscribe(lambda event: self._refresh())
        self._refresh()
        self._refresh_review()

    @property
    def session(self) -> ImportSession | None:
        return self._session

    def _refresh(self) -> None:
        dataframe = self._ctx.dataframe_store.dataframe
        issues = self._ctx.dataframe_store.issues
        fill_issues_table(self._issues_table, issues)
        if dataframe is None:
            self._summary.setText("No dataframe loaded.")
            return
        md = dataframe.metadata
        self._summary.setText(
            f"Loaded <b>{md.dataframe_name}</b> "
            f"({md.source_type}{' ' + md.source_filename if md.source_filename else ''}) "
            f"— WPS {md.wps}, {len(dataframe.parameters)} parameters, "
            f"{error_count(issues)} validation errors, "
            f"{warning_count(issues)} warnings."
        )

    def _refresh_review(self) -> None:
        session = self._session
        if session is None:
            self._review_label.setText(
                self._last_published
                or "Empty — populated by Import PDF. Rows wait here until "
                "reviewed and published."
            )
            self._review_button.setEnabled(False)
            return
        counts = session.state_counts()
        pending = len(session.pending_items())
        self._review_label.setText(
            f"<b>{session.source_filename}</b>: {len(session.items)} row(s) — "
            f"{counts[STATE_APPROVED]} approved, "
            f"{counts[STATE_REVIEW_REQUIRED]} need review, "
            f"{counts['EXCLUDED']} excluded. "
            + (f"{pending} row(s) block publishing." if pending else "Ready to publish.")
        )
        self._review_button.setEnabled(True)

    # -- ADB ---------------------------------------------------------------------

    def _import_adb(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import ADB", "", ADB_FILE_FILTER
        )
        if not path:
            return
        try:
            self._ctx.dataframe_service.load_adb(path)
        except ServiceError as exc:
            QMessageBox.critical(self, "Import ADB", str(exc))

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

    def _load_demo(self) -> None:
        self._ctx.dataframe_service.set_dataframe(build_demo_dataframe())

    def _new_dataframe(self) -> None:
        if self._ctx.dataframe_store.dirty:
            answer = QMessageBox.question(
                self,
                "New Dataframe",
                "The current dataframe has edits that were not exported. Discard them?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        MetadataDialog(self._ctx, "new", self).exec()

    # -- PDF ---------------------------------------------------------------------

    def import_pdf(self, path: str | None = None, profile: ImportProfile | None = None) -> None:
        """Run the pipeline in the background (asks for a file when ``path`` is
        omitted), show progress, then open the review dialog."""
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Import PDF", "An import is already running.")
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Import PDF", "", PDF_FILE_FILTER)
            if not path:
                return
        progress = QProgressDialog("Opening document…", "", 0, 0, self)
        progress.setWindowTitle("Import PDF")
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self._progress = progress
        worker = PdfImportWorker(self._ctx.dataframe_service, path, profile, self)
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_imported)
        worker.failed.connect(self._on_import_failed)
        self._worker = worker
        progress.show()
        worker.start()

    def run_import_blocking(self, path: str, profile: ImportProfile | None = None) -> ImportSession:
        """Synchronous import (scripts, tests); raises ServiceError."""
        session = self._ctx.dataframe_service.import_pdf(path, profile)
        self._session = session
        self._refresh_review()
        return session

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if self._progress is None:
            return
        if total:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        self._progress.setLabelText(message)

    def _close_progress(self) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress = None

    def _on_imported(self, session) -> None:
        self._close_progress()
        self._session = session
        self._refresh_review()
        self.open_review()

    def _on_import_failed(self, message: str) -> None:
        self._close_progress()
        QMessageBox.critical(self, "Import PDF", message)

    def open_review(self) -> None:
        session = self._session
        if session is None:
            return
        dialog = PdfReviewDialog(self._ctx, session, self)
        accepted = dialog.exec()
        session = dialog.session  # re-normalizing replaces the session object
        if accepted:
            published = session.published
            self._last_published = (
                f"Published <b>{session.source_filename}</b>: "
                f"{len(published.parameters) if published else 0} parameter(s) "
                "loaded into the workspace (not exported yet)."
            )
            self._session = None
        else:
            self._session = session
        self._refresh_review()
