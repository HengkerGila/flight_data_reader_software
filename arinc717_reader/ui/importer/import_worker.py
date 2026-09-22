"""Background PDF import (scanned documents take minutes with OCR).

The pipeline is pure Python and touches no Qt objects, so it runs on a
``QThread``; progress and the finished session come back through signals.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ...dataframe.pdf_importer import ImportProfile
from ...services import ServiceError


class PdfImportWorker(QThread):
    progress = Signal(int, int, str)
    finished_ok = Signal(object)  # ImportSession
    failed = Signal(str)

    def __init__(self, dataframe_service, path: str, profile: ImportProfile | None = None, parent=None):
        super().__init__(parent)
        self._service = dataframe_service
        self._path = path
        self._profile = profile

    def run(self) -> None:  # executed on the worker thread
        try:
            session = self._service.import_pdf(
                self._path,
                self._profile,
                progress=lambda done, total, message: self.progress.emit(done, total, message),
            )
        except ServiceError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # never let a worker die silently
            self.failed.emit(f"unexpected error during import: {exc!r}")
        else:
            self.finished_ok.emit(session)
