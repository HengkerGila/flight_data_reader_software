"""Scanned-page table extraction (design spec §28: page rendering → table
detection → OCR / cell extraction).

A scanned dataframe document carries its ruled table only as pixels.  This
module renders a page, estimates its skew from the long horizontal rules,
re-renders it deskewed, finds the ruling lines by projecting long ink runs,
and drops text boxes into the resulting cells.  Text comes from the page's
own text layer when a scanner or Acrobat embedded one, otherwise from the
optional OCR engine (RapidOCR, ``pip install rapidocr-onnxruntime``).

All boxes handed out are in *displayed page* coordinates (points, page
rotation applied) so ``render_region_png`` can show them again.  Only numpy
is needed here; OCR is loaded lazily.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .ingest import load_pymupdf
from .raw import SOURCE_OCR, SOURCE_TEXT_LAYER, BBox, RawCell

DARK_THRESHOLD = 200        # gray level below which a pixel is ink
OPENING_DIVISOR = 40        # opening window = max(30, dimension // 40) pixels
PROJECTION_FRACTION = 0.05  # ink-run projection threshold (fraction of width/height)
BAND_GAP = 4                # merge rule bands closer than this many pixels
MIN_RULE_FRACTION = 0.4     # a horizontal rule spans at least this much of the width
MIN_VRULE_FRACTION = 0.2    # a vertical rule spans at least this much of the height
MAX_SKEW_DEGREES = 5.0
MIN_SKEW_DEGREES = 0.03
DEFAULT_DPI = 150


@dataclass
class TextBox:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    confidence: float | None = None
    source: str = SOURCE_TEXT_LAYER

    @property
    def bbox(self) -> BBox:
        return (self.x0, self.y0, self.x1, self.y1)


class OcrUnavailable(RuntimeError):
    """Raised when no OCR engine can be loaded."""


# --------------------------------------------------------------------------
# Rendering with deskew
# --------------------------------------------------------------------------


class PageRaster:
    """A grayscale render of one page with the page↔pixel transforms.

    ``skew_degrees`` is the angle of the page's horizontal rules; the render
    is rotated back by that angle so rules are axis-aligned.
    """

    def __init__(self, page, dpi: int = DEFAULT_DPI, skew_degrees: float = 0.0):
        pymupdf = load_pymupdf()
        zoom = dpi / 72.0
        self.dpi = dpi
        self.skew_degrees = skew_degrees
        self.matrix = pymupdf.Matrix(zoom, zoom).prerotate(-skew_degrees)
        self._page = page
        self._pymupdf = pymupdf
        pixmap = page.get_pixmap(matrix=self.matrix, colorspace=pymupdf.csGRAY)
        self.origin = (float(pixmap.x), float(pixmap.y))
        self.gray = (
            np.frombuffer(pixmap.samples, dtype=np.uint8)
            .reshape(pixmap.height, pixmap.width)
            .copy()
        )

    @property
    def height(self) -> int:
        return int(self.gray.shape[0])

    @property
    def width(self) -> int:
        return int(self.gray.shape[1])

    def rgb(self) -> np.ndarray:
        pixmap = self._page.get_pixmap(matrix=self.matrix, colorspace=self._pymupdf.csRGB)
        return (
            np.frombuffer(pixmap.samples, dtype=np.uint8)
            .reshape(pixmap.height, pixmap.width, 3)
            .copy()
        )

    def to_pixels(self, bbox: BBox) -> BBox:
        rect = self._pymupdf.Rect(*bbox) * self.matrix
        ox, oy = self.origin
        return (rect.x0 - ox, rect.y0 - oy, rect.x1 - ox, rect.y1 - oy)

    def to_page(self, bbox: BBox) -> BBox:
        ox, oy = self.origin
        x0, y0, x1, y1 = bbox
        rect = self._pymupdf.Rect(x0 + ox, y0 + oy, x1 + ox, y1 + oy) * ~self.matrix
        return (rect.x0, rect.y0, rect.x1, rect.y1)


def render_deskewed(page, dpi: int = DEFAULT_DPI) -> PageRaster:
    """Render a page, measure its skew, and render it again deskewed."""
    first = PageRaster(page, dpi=dpi)
    skew = estimate_skew(first.gray)
    if abs(skew) < MIN_SKEW_DEGREES:
        return first
    return PageRaster(page, dpi=dpi, skew_degrees=skew)


# --------------------------------------------------------------------------
# Rule detection
# --------------------------------------------------------------------------


def _open_1d(binary: np.ndarray, size: int, axis: int) -> np.ndarray:
    """Morphological opening with a straight window along ``axis``."""
    before, after = size // 2, size - 1 - size // 2
    pad = [(0, 0), (0, 0)]
    pad[axis] = (before, after)
    eroded = sliding_window_view(np.pad(binary, pad), size, axis=axis).all(axis=-1)
    return sliding_window_view(np.pad(eroded, pad), size, axis=axis).any(axis=-1)


def _bands(profile: np.ndarray, threshold: float, gap: int = BAND_GAP) -> list[tuple[int, int]]:
    indexes = np.nonzero(profile > threshold)[0]
    bands: list[list[int]] = []
    for index in indexes.tolist():
        if bands and index - bands[-1][-1] <= gap:
            bands[-1].append(index)
        else:
            bands.append([index])
    return [(band[0], band[-1]) for band in bands]


def _window(length: int) -> int:
    return max(30, length // OPENING_DIVISOR)


def estimate_skew(gray: np.ndarray) -> float:
    """Angle (degrees) of the long horizontal rules; 0 when there are none."""
    binary = gray < DARK_THRESHOLD
    height, width = binary.shape
    horizontal = _open_1d(binary, _window(width), axis=1)
    angles: list[float] = []
    weights: list[float] = []
    for y0, y1 in _bands(horizontal.sum(axis=1), PROJECTION_FRACTION * width, gap=6):
        ys, xs = np.nonzero(horizontal[y0 : y1 + 1])
        if xs.size < 2 or np.ptp(xs) < 0.3 * width:
            continue
        slope = np.polyfit(xs, ys + y0, 1)[0]
        angles.append(math.degrees(math.atan(float(slope))))
        weights.append(float(np.ptp(xs)))
    if not angles:
        return 0.0
    order = np.argsort(angles)
    cumulative = np.cumsum(np.array(weights)[order])
    angle = float(np.array(angles)[order][np.searchsorted(cumulative, cumulative[-1] / 2)])
    return angle if abs(angle) <= MAX_SKEW_DEGREES else 0.0


@dataclass
class Grid:
    """Ruling-line positions in deskewed pixel coordinates."""

    ys: list[float]
    xs: list[float]

    @property
    def row_count(self) -> int:
        return len(self.ys) - 1

    @property
    def col_count(self) -> int:
        return len(self.xs) - 1

    def locate(self, x: float, y: float) -> tuple[int, int] | None:
        row = bisect_right(self.ys, y) - 1
        col = bisect_right(self.xs, x) - 1
        if 0 <= row < self.row_count and 0 <= col < self.col_count:
            return row, col
        return None

    def cell(self, row: int, col: int) -> BBox:
        return (self.xs[col], self.ys[row], self.xs[col + 1], self.ys[row + 1])

    @property
    def bbox(self) -> BBox:
        return (self.xs[0], self.ys[0], self.xs[-1], self.ys[-1])


def detect_grid(gray: np.ndarray) -> Grid | None:
    """Find the ruled table grid on a deskewed render; None when there is none."""
    binary = gray < DARK_THRESHOLD
    height, width = binary.shape
    horizontal = _open_1d(binary, _window(width), axis=1)
    vertical = _open_1d(binary, _window(height), axis=0)

    h_rules: list[tuple[float, int, int]] = []
    for y0, y1 in _bands(horizontal.sum(axis=1), PROJECTION_FRACTION * width):
        cols = np.nonzero(horizontal[y0 : y1 + 1].any(axis=0))[0]
        if cols.size and np.ptp(cols) >= MIN_RULE_FRACTION * width:
            h_rules.append(((y0 + y1) / 2, int(cols.min()), int(cols.max())))
    v_rules: list[tuple[float, int, int]] = []
    for x0, x1 in _bands(vertical.sum(axis=0), PROJECTION_FRACTION * height):
        rows = np.nonzero(vertical[:, x0 : x1 + 1].any(axis=1))[0]
        if rows.size and np.ptp(rows) >= MIN_VRULE_FRACTION * height:
            v_rules.append(((x0 + x1) / 2, int(rows.min()), int(rows.max())))
    if len(h_rules) < 2 or len(v_rules) < 2:
        return None

    # Keep only rules that belong to the same table: horizontal rules inside
    # the vertical rules' span and vice versa (drops signature lines, logos).
    tolerance = 12
    v_top = min(r[1] for r in v_rules) - tolerance
    v_bottom = max(r[2] for r in v_rules) + tolerance
    h_left = min(r[1] for r in h_rules) - tolerance
    h_right = max(r[2] for r in h_rules) + tolerance
    ys = [y for y, _, _ in h_rules if v_top <= y <= v_bottom]
    xs = [x for x, _, _ in v_rules if h_left <= x <= h_right]
    if len(ys) < 2 or len(xs) < 2:
        return None
    return Grid(ys=sorted(ys), xs=sorted(xs))


# --------------------------------------------------------------------------
# Text sources
# --------------------------------------------------------------------------


def text_layer_boxes(page) -> list[TextBox]:
    """Words of the page's text layer in displayed page coordinates."""
    pymupdf = load_pymupdf()
    rotation = page.rotation_matrix
    boxes: list[TextBox] = []
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        rect = pymupdf.Rect(x0, y0, x1, y1) * rotation
        if text.strip():
            boxes.append(TextBox(rect.x0, rect.y0, rect.x1, rect.y1, text, None, SOURCE_TEXT_LAYER))
    return boxes


class RapidOcrEngine:
    """RapidOCR (ONNX runtime) wrapper; models ship with the package."""

    name = "rapidocr"

    def __init__(self):
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise OcrUnavailable(
                "OCR needs the 'rapidocr-onnxruntime' package "
                "(pip install rapidocr-onnxruntime)."
            ) from exc
        self._engine = RapidOCR()

    @staticmethod
    def available() -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError:
            return False
        return True

    def recognize_cell(self, rgb_crop: np.ndarray) -> tuple[str, float] | None:
        """Recognition only (no detection) on one cell image.

        The detector skips isolated small glyphs such as a lone "0" and
        sometimes whole small-print cells; running the recognizer directly on
        the cell's text lines recovers them.  Lines are split by the ink
        profile, recognized one by one and joined with newlines; the score is
        the lowest line score.
        """
        lines: list[str] = []
        scores: list[float] = []
        for band in _ink_line_bands(rgb_crop):
            result, _elapsed = self._engine(band, use_det=False, use_cls=False, use_rec=True)
            if not result:
                continue
            text = str(result[0][0]).strip()
            if text:
                lines.append(text)
                scores.append(float(result[0][1]))
        if not lines:
            return None
        return "\n".join(lines), min(scores)

    def recognize(self, rgb: np.ndarray) -> list[TextBox]:
        """Text lines with confidences, in pixel coordinates of ``rgb``."""
        result, _elapsed = self._engine(rgb)
        boxes: list[TextBox] = []
        for quad, text, score in result or []:
            xs = [point[0] for point in quad]
            ys = [point[1] for point in quad]
            if str(text).strip():
                boxes.append(
                    TextBox(
                        float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)),
                        str(text), float(score), SOURCE_OCR,
                    )
                )
        return boxes


def ocr_boxes(raster: PageRaster, engine, rgb: np.ndarray | None = None) -> list[TextBox]:
    """Run ``engine`` on the deskewed render; boxes come back in page space."""
    boxes: list[TextBox] = []
    for box in engine.recognize(raster.rgb() if rgb is None else rgb):
        x0, y0, x1, y1 = raster.to_page(box.bbox)
        boxes.append(TextBox(x0, y0, x1, y1, box.text, box.confidence, box.source))
    return boxes


def _ink_line_bands(rgb_crop: np.ndarray, margin: int = 2, min_height: int = 5) -> list[np.ndarray]:
    """Split a cell image into its text lines (horizontal ink projection)."""
    ink = rgb_crop.min(axis=2) < DARK_THRESHOLD
    profile = ink.sum(axis=1)
    bands: list[np.ndarray] = []
    for y0, y1 in _bands(profile, 0, gap=2):
        if y1 - y0 + 1 < min_height:
            continue
        top = max(0, y0 - margin)
        bottom = min(rgb_crop.shape[0], y1 + 1 + margin)
        bands.append(rgb_crop[top:bottom])
    return bands or [rgb_crop]


CELL_INSET = 3          # pixels kept clear of the ruling lines
MIN_CELL_INK = 0.004    # fraction of dark pixels below which a cell is blank
SECOND_PASS_NOTE = "second-pass OCR on the cell crop (detector found no text)"


def second_pass_cells(
    rgb: np.ndarray, grid: Grid, cells: list[RawCell], engine
) -> int:
    """Recognize text in cells the detector left empty; returns how many were filled.

    Only cells with visible ink are tried, so blank cells stay blank.
    """
    filled = 0
    height, width = rgb.shape[:2]
    for cell in cells:
        if cell.text or cell.grid_cell is None:
            continue
        x0, y0, x1, y1 = grid.cell(*cell.grid_cell)
        x0, y0 = int(x0) + CELL_INSET, int(y0) + CELL_INSET
        x1, y1 = min(int(x1) - CELL_INSET, width), min(int(y1) - CELL_INSET, height)
        if x1 - x0 < 6 or y1 - y0 < 6:
            continue
        crop = rgb[y0:y1, x0:x1]
        if float((crop.min(axis=2) < DARK_THRESHOLD).mean()) < MIN_CELL_INK:
            continue
        recognized = engine.recognize_cell(crop)
        if recognized is None:
            continue
        cell.text, cell.confidence = recognized
        cell.source = SOURCE_OCR
        cell.note = SECOND_PASS_NOTE
        filled += 1
    return filled


# --------------------------------------------------------------------------
# Cells
# --------------------------------------------------------------------------


def grid_cells(raster: PageRaster, grid: Grid, boxes: list[TextBox]) -> list[list[RawCell]]:
    """Drop text boxes into grid cells; returns one RawCell row per grid row.

    Cell text keeps line breaks (stacked mnemonic/name, wrapped names), which
    the normalizer relies on.  Cell bounding boxes are the grid cell mapped
    back to page space.
    """
    placed: dict[tuple[int, int], list[tuple[float, float, float, TextBox]]] = {}
    for box in boxes:
        x0, y0, x1, y1 = raster.to_pixels(box.bbox)
        location = grid.locate((x0 + x1) / 2, (y0 + y1) / 2)
        if location is not None:
            placed.setdefault(location, []).append((y0, x0, y1, box))
    rows: list[list[RawCell]] = []
    for row in range(grid.row_count):
        cells: list[RawCell] = []
        for col in range(grid.col_count):
            items = placed.get((row, col), [])
            text = _cell_text(items)
            confidences = [item[3].confidence for item in items if item[3].confidence is not None]
            sources = {item[3].source for item in items}
            cells.append(
                RawCell(
                    text=text,
                    bbox=raster.to_page(grid.cell(row, col)),
                    confidence=min(confidences) if confidences else None,
                    source=SOURCE_OCR if SOURCE_OCR in sources else SOURCE_TEXT_LAYER,
                    grid_cell=(row, col),
                )
            )
        rows.append(cells)
    return rows


def _cell_text(items: list[tuple[float, float, float, TextBox]]) -> str:
    """Join boxes into lines by vertical overlap, left to right within a line."""
    if not items:
        return ""
    items = sorted(items, key=lambda item: (item[0], item[1]))
    lines: list[list[tuple[float, str]]] = []
    current: list[tuple[float, str]] = []
    line_bottom: float | None = None
    for y0, x0, y1, box in items:
        height = max(y1 - y0, 1.0)
        if line_bottom is not None and y0 > line_bottom - 0.3 * height:
            lines.append(current)
            current, line_bottom = [], None
        current.append((x0, box.text))
        line_bottom = y1 if line_bottom is None else max(line_bottom, y1)
    if current:
        lines.append(current)
    return "\n".join(" ".join(text for _, text in sorted(line)) for line in lines)
