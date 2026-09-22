"""PDF document ingestion (design spec §28, step 1).

Opens the document with PyMuPDF, fingerprints it and records, per page,
whether a native text layer exists.  Pages without a text layer would need
OCR, which this build does not ship: they are reported as explicit issues
downstream, never silently skipped.  No PyMuPDF object leaves this module —
the returned model is plain data so the rest of the pipeline stays testable
without PDFs.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path


class PdfImportNotAvailable(RuntimeError):
    """Raised when the optional PDF dependency (PyMuPDF) is missing."""


class PdfIngestError(ValueError):
    """Raised when a file cannot be opened as a PDF."""


def pdf_support_available() -> bool:
    try:
        import pymupdf  # noqa: F401
    except ImportError:
        return False
    return True


def load_pymupdf():
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PdfImportNotAvailable(
            "PDF dataframe import needs the 'pymupdf' package "
            "(pip install pymupdf)."
        ) from exc
    # PyMuPDF prints a one-off "consider pymupdf_layout" hint from the table
    # finder; it is advice for developers, not for reviewers of a dataframe.
    silence = getattr(pymupdf, "no_recommend_layout", None)
    if silence is not None:
        silence()
    return pymupdf


# A page whose images cover at least this fraction of its area is a scan.
SCANNED_COVERAGE = 0.8


@dataclass
class PdfPage:
    number: int  # 1-based
    width: float
    height: float
    text: str
    has_text_layer: bool
    image_count: int = 0
    image_coverage: float = 0.0  # fraction of the page area covered by images

    @property
    def is_scanned(self) -> bool:
        return self.image_coverage >= SCANNED_COVERAGE


@dataclass
class PdfDocument:
    path: Path
    filename: str
    sha256: str
    page_count: int
    pages: list[PdfPage] = field(default_factory=list)
    info: dict = field(default_factory=dict)  # PDF metadata dictionary

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    def pages_without_text(self) -> list[int]:
        return [page.number for page in self.pages if not page.has_text_layer]


def ingest_pdf(path: str | Path) -> PdfDocument:
    pymupdf = load_pymupdf()
    path = Path(path)
    raw = path.read_bytes()
    try:
        document = pymupdf.open(stream=raw, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several unrelated types here
        raise PdfIngestError(f"{path.name}: not a readable PDF ({exc})") from exc
    with document:
        if document.needs_pass:
            raise PdfIngestError(f"{path.name}: document is password protected")
        pages: list[PdfPage] = []
        for page in document:
            text = page.get_text("text") or ""
            pages.append(
                PdfPage(
                    number=page.number + 1,
                    width=float(page.rect.width),
                    height=float(page.rect.height),
                    text=text,
                    has_text_layer=bool(text.strip()),
                    image_count=len(page.get_images(full=False)),
                    image_coverage=_image_coverage(page, pymupdf),
                )
            )
        info = {k: v for k, v in (document.metadata or {}).items() if v}
    return PdfDocument(
        path=path,
        filename=path.name,
        sha256=hashlib.sha256(raw).hexdigest(),
        page_count=len(pages),
        pages=pages,
        info=info,
    )


def _image_coverage(page, pymupdf) -> float:
    area = float(page.rect.width * page.rect.height)
    if area <= 0:
        return 0.0
    covered = 0.0
    try:
        infos = page.get_image_info()
    except Exception:  # pragma: no cover - defensive: malformed image dictionaries
        return 0.0
    rotated = 0.0
    for info in infos:
        bbox = pymupdf.Rect(info["bbox"])
        # Image boxes may be reported in unrotated page space; measure both
        # readings and keep the larger so rotated scans are still recognized.
        clipped = bbox & page.rect
        if not clipped.is_empty:
            covered += float(clipped.width * clipped.height)
        clipped = (bbox * page.rotation_matrix) & page.rect
        if not clipped.is_empty:
            rotated += float(clipped.width * clipped.height)
    return min(1.0, max(covered, rotated) / area)


_WPS_PATTERNS = (
    re.compile(r"\b(\d{2,4})\s*(?:WPS|W/S)\b", re.IGNORECASE),
    re.compile(
        r"\b(\d{2,4})\s*WORDS?\s*(?:PER|/)\s*(?:SECOND|SEC|S)\b", re.IGNORECASE
    ),
)


def detect_wps(text: str) -> int | None:
    """WPS stated in the document text, if it is stated exactly once.

    Several different values (or none) return ``None`` — the user confirms
    WPS in the review dialog either way; it is never guessed.
    """
    found = {int(m.group(1)) for pattern in _WPS_PATTERNS for m in pattern.finditer(text)}
    if len(found) == 1:
        return found.pop()
    return None
