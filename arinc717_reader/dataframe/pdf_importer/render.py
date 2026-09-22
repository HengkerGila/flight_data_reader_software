"""PDF page rendering (design spec §28 "Page Rendering", §30 provenance).

Used by the review UI to show *where a value came from*: a page, or the
region around an extracted row, rendered to PNG bytes.  Rendering is a
presentation aid only; it is never an input to decoding.
"""

from __future__ import annotations

from pathlib import Path

from .ingest import load_pymupdf

BBox = tuple[float, float, float, float]

HIGHLIGHT_COLOR = (0.85, 0.15, 0.15)  # outline drawn around the extracted row
HIGHLIGHT_WIDTH = 1.5


def render_page_png(
    path: str | Path,
    page_number: int,
    dpi: int = 110,
    highlight: BBox | None = None,
) -> bytes:
    """Render one page (1-based) to PNG bytes, outlining ``highlight`` if given."""
    return _render(path, page_number, dpi, clip=None, highlight=highlight)


def render_region_png(
    path: str | Path,
    page_number: int,
    bbox: BBox,
    margin: float = 14.0,
    dpi: int = 160,
    *,
    margin_y: float | None = None,
    highlight: bool = False,
) -> bytes:
    """Render the region around ``bbox`` (PDF points) to PNG bytes.

    ``margin`` is added left and right; ``margin_y`` (default ``margin``)
    above and below, so a reviewer can be shown the neighbouring rows for
    context.  ``highlight`` outlines ``bbox`` itself.
    """
    x0, y0, x1, y1 = bbox
    my = margin if margin_y is None else margin_y
    return _render(
        path,
        page_number,
        dpi,
        clip=(x0 - margin, y0 - my, x1 + margin, y1 + my),
        highlight=bbox if highlight else None,
    )


def _render(
    path, page_number: int, dpi: int, clip: BBox | None, highlight: BBox | None = None
) -> bytes:
    pymupdf = load_pymupdf()
    with pymupdf.open(str(path)) as document:
        if not 1 <= page_number <= len(document):
            raise ValueError(f"page {page_number} outside 1..{len(document)}")
        page = document[page_number - 1]
        rect = None
        if clip is not None:
            rect = pymupdf.Rect(*clip) & page.rect
        if highlight is not None:
            # The document is opened from disk for this call only and never
            # saved, so drawing on the page cannot alter the source file.
            page.draw_rect(
                pymupdf.Rect(*highlight), color=HIGHLIGHT_COLOR, width=HIGHLIGHT_WIDTH
            )
        pixmap = page.get_pixmap(dpi=dpi, clip=rect)
        return pixmap.tobytes("png")
