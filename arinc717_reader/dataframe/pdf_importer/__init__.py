"""Dataframe PDF import pipeline (design spec §28–§32, Phase 8).

    PDF → ingestion → page rendering / native extraction → table detection
        → cell extraction → raw extraction model → normalization → validation
        → manual review → canonical dataframe

Born-digital pages use PyMuPDF's table finder; scanned pages get their ruled
grid detected on the rendered image and text from the embedded text layer or
the optional RapidOCR engine.  Nothing here is imported by the runtime
decoder: OCR/extraction code and parameter decoding stay separate (spec §54).
"""

from .ingest import PdfImportNotAvailable, PdfIngestError, pdf_support_available
from .normalize import ImportProfile, NormalizationIssue, normalize_raw_row
from .pipeline import build_session, import_pdf, renormalize_session
from .raw import ImportIssue, RawFieldProvenance, RawParameterRow
from .review import ImportItem, ImportSession, ReviewError

__all__ = [
    "ImportIssue",
    "ImportItem",
    "ImportProfile",
    "ImportSession",
    "NormalizationIssue",
    "PdfImportNotAvailable",
    "PdfIngestError",
    "RawFieldProvenance",
    "RawParameterRow",
    "ReviewError",
    "build_session",
    "import_pdf",
    "normalize_raw_row",
    "pdf_support_available",
    "renormalize_session",
]
