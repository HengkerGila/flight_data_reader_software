"""Table detection and raw cell extraction (design spec §28–§30).

Two page paths produce the same ``RawTable`` model:

* born-digital pages — PyMuPDF's table finder on the vector content;
* scanned pages (an image covers the page) — the ruled grid is detected on
  the rendered image (``scan.py``) and text boxes are dropped into its
  cells: the page's embedded text layer when present, else the OCR engine.

Every cell keeps its page, bounding box, verbatim text, text source and
confidence so a reviewer can always answer "where did this value come
from?".  Nothing here interprets values.  Column meaning comes from the
table header through a synonym table with a fuzzy fallback for OCR noise;
an unrecognized header is reported and the table skipped — never guessed.
A table without a header inherits the previous table's header when the
column count matches (multi-page continuation).
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable

from .ingest import PdfDocument, PdfPage, load_pymupdf
from .raw import (  # noqa: F401  (re-exported for callers of the old module layout)
    CRITICAL_FIELDS,
    RAW_FIELDS,
    SOURCE_NATIVE,
    SOURCE_OCR,
    SOURCE_TEXT_LAYER,
    BBox,
    ImportIssue,
    RawCell,
    RawFieldProvenance,
    RawParameterRow,
    RawTable,
)

# Header text (normalized: upper-case, punctuation stripped) → raw field.
HEADER_SYNONYMS: dict[str, tuple[str, ...]] = {
    "mnemonic_and_name": (
        "PARAMETER MNEMONIC NAME", "PARAMETER MNEMONIC AND NAME", "MNEMONIC NAME",
        "MNEMONIC AND NAME", "MNEMONIC/NAME", "PARAMETER MNEMONIC/NAME",
    ),
    "parameter_name": (
        "PARAMETER", "PARAMETER NAME", "PARAM", "PARAM NAME", "NAME",
        "MNEMONIC", "SIGNAL", "SIGNAL NAME", "PARAMETER MNEMONIC",
    ),
    "description": ("DESCRIPTION", "DESC", "PARAMETER DESCRIPTION", "TITLE"),
    "parameter_type": (
        "TYPE", "PARAMETER TYPE", "DATA TYPE", "FORMAT", "ENCODING", "CODING",
        "SIGNAL TYPE", "DATA FORMAT", "CODE",
    ),
    "sign": ("SIGN", "SIGNED", "POLARITY", "S/U"),
    "frequency": (
        "FREQ", "FREQUENCY", "FREQ HZ", "FREQUENCY HZ", "HZ", "RATE",
        "SAMPLE RATE", "SAMPLES/SEC", "SAMPLES PER SECOND", "SAMPLES/S",
        "S/S", "SPS", "SAMPLING RATE", "SAMPLES SEC", "INTERVAL", "SAMPLE INTERVAL",
    ),
    "word_location": (
        "WORD", "WORDS", "WORD LOCATION", "WORD NO", "WORD NUMBER",
        "WORD ADDRESS", "WORD ADDR", "LOCATION", "SLOT", "WORD SLOT",
        "WORD LOCATIONS", "WORD POSITION", "A717 WORD",
    ),
    "subframe": ("SUBFRAME", "SUB FRAME", "SF", "SUBFRAMES", "SUB FRAMES", "SUBFRAME NO"),
    "msb": ("MSB", "MSB BIT", "MSB POSITION", "START BIT", "HIGH BIT", "A717 MSB"),
    "lsb": ("LSB", "LSB BIT", "LSB POSITION", "END BIT", "LOW BIT", "A717 LSB"),
    "bits": ("BITS", "BIT", "BIT RANGE", "BIT POSITION", "BIT POSITIONS", "BIT NO", "BIT NOS"),
    "units": ("UNITS", "UNIT", "EU", "ENG UNITS", "ENGINEERING UNITS", "ENG UNIT"),
    "resolution": (
        "RESOLUTION", "RES", "LSB VALUE", "LSB RESOLUTION", "SCALE",
        "SCALE FACTOR", "SCALING", "RESOLUTION LSB", "RES LSB", "SENSITIVITY",
    ),
    "offset": ("OFFSET", "BIAS", "ZERO OFFSET"),
    "minimum": ("MIN", "MINIMUM", "RANGE MIN", "LOW", "MIN VALUE", "MIN RANGE"),
    "maximum": ("MAX", "MAXIMUM", "RANGE MAX", "HIGH", "MAX VALUE", "MAX RANGE"),
    "true_state": ("1 =", "1=", "ONE", "TRUE", "TRUE STATE", "STATE 1", "LOGIC 1", "BIT 1", "1 STATE", "SET"),
    "false_state": ("0 =", "0=", "ZERO", "FALSE", "FALSE STATE", "STATE 0", "LOGIC 0", "BIT 0", "0 STATE", "RESET"),
    "notes": ("NOTES", "NOTE", "REMARKS", "REMARK", "COMMENTS", "COMMENT"),
}

_HEADER_LOOKUP: dict[str, str] = {
    synonym: fld for fld, synonyms in HEADER_SYNONYMS.items() for synonym in synonyms
}

# Fields a header row must map before a table is trusted as a parameter table.
NAME_FIELDS = ("parameter_name", "mnemonic_and_name")
MIN_MAPPED_COLUMNS = 3
FUZZY_HEADER_RATIO = 0.75

NATIVE_TEXT_CONFIDENCE = None

ProgressCallback = Callable[[int, int, str], None]


def normalize_header(text: str) -> str:
    cleaned = re.sub(r"\(s\)", "s", text or "", flags=re.IGNORECASE)
    cleaned = re.sub(r"[^A-Za-z0-9=/]+", " ", cleaned)
    return " ".join(cleaned.upper().split())


def _fuzzy_field(key: str) -> tuple[str | None, str | None, float]:
    """Best synonym for an OCR-damaged header ("Tvpe", "Mnemonk")."""
    if len(key) < 3:
        return None, None, 0.0
    compact = key.replace(" ", "")
    best_field, best_synonym, best_score = None, None, 0.0
    for synonym, fld in _HEADER_LOOKUP.items():
        score = max(
            difflib.SequenceMatcher(None, key, synonym).ratio(),
            difflib.SequenceMatcher(None, compact, synonym.replace(" ", "")).ratio(),
        )
        if score > best_score:
            best_field, best_synonym, best_score = fld, synonym, score
    if best_score >= FUZZY_HEADER_RATIO:
        return best_field, best_synonym, best_score
    return None, None, best_score


def header_matches(
    headers: list[str], overrides: dict[str, str] | None = None
) -> list[tuple[str, str | None, str]]:
    """(header text, field or None, how it matched) for every column."""
    normalized_overrides = {normalize_header(k): v for k, v in (overrides or {}).items()}
    result: list[tuple[str, str | None, str]] = []
    for header in headers:
        key = normalize_header(header)
        if not key:
            result.append((header, None, "empty"))
        elif key in normalized_overrides:
            result.append((header, normalized_overrides[key], "override"))
        elif key in _HEADER_LOOKUP:
            result.append((header, _HEADER_LOOKUP[key], "exact"))
        else:
            fld, synonym, score = _fuzzy_field(key)
            result.append((header, fld, f"fuzzy {score:.2f} ~ {synonym}" if fld else "unmapped"))
    return result


def map_columns(
    headers: list[str], overrides: dict[str, str] | None = None
) -> dict[str, int]:
    """Map header cells to raw fields; exact matches win, first match per field."""
    matches = header_matches(headers, overrides)
    mapping: dict[str, int] = {}
    for index, (_, fld, how) in enumerate(matches):
        if fld and not how.startswith("fuzzy") and fld not in mapping:
            mapping[fld] = index
    for index, (_, fld, how) in enumerate(matches):
        if fld and how.startswith("fuzzy") and fld not in mapping and index not in mapping.values():
            mapping[fld] = index
    return mapping


def is_parameter_header(mapping: dict[str, int]) -> bool:
    return (
        len(mapping) >= MIN_MAPPED_COLUMNS
        and any(fld in mapping for fld in NAME_FIELDS)
        and "word_location" in mapping
    )


def clean_cell_text(text) -> str:
    """Normalize spaces but keep line breaks (stacked mnemonic / name)."""
    if text is None:
        return ""
    lines = [" ".join(line.split()) for line in str(text).splitlines()]
    return "\n".join(line for line in lines if line)


# --------------------------------------------------------------------------
# Document → tables
# --------------------------------------------------------------------------


class _OcrHolder:
    """Creates the OCR engine on first use; remembers when it is unavailable."""

    def __init__(
        self,
        mode: str,
        recognizer: str | None = None,
        keys_path: str | None = None,
        angle_classifier: bool = False,
    ):
        self.mode = mode
        self.recognizer = recognizer
        self.keys_path = keys_path
        self.angle_classifier = angle_classifier
        self._engine = None
        self.error: str | None = None
        self.used = False

    @property
    def name(self) -> str | None:
        return self._engine.name if self._engine is not None else None

    def get(self):
        if self.mode == "never":
            self.error = "OCR disabled by the import profile"
            return None
        if self._engine is None and self.error is None:
            from .scan import OcrUnavailable, RapidOcrEngine

            try:
                self._engine = RapidOcrEngine(
                    self.recognizer, self.keys_path, angle_classifier=self.angle_classifier
                )
            except OcrUnavailable as exc:
                self.error = str(exc)
        if self._engine is not None:
            self.used = True
        return self._engine


def extract_tables(
    document: PdfDocument,
    overrides: dict[str, str] | None = None,
    *,
    ocr: str = "auto",
    dpi: int = 150,
    page_range: tuple[int, int] | None = None,
    progress: ProgressCallback | None = None,
    ocr_recognizer: str | None = None,
    ocr_keys_path: str | None = None,
    ocr_angle_classifier: bool = False,
    ocr_cells: bool = True,
) -> tuple[list[RawTable], list[ImportIssue]]:
    """Detect parameter tables on every page (native or scanned).

    ``ocr_recognizer`` selects the OCR recognizer model (see
    ``scan.resolve_recognizer``); ``ocr_keys_path`` its character list when
    the model does not embed one; ``ocr_angle_classifier`` enables
    RapidOCR's 180° line classifier (off for deskewed tables);
    ``ocr_cells`` recognizes every grid cell on its own crop instead of
    detecting text lines on the whole page.
    """
    pymupdf = load_pymupdf()
    tables: list[RawTable] = []
    issues: list[ImportIssue] = []
    previous: RawTable | None = None
    engine = _OcrHolder(ocr, ocr_recognizer, ocr_keys_path, ocr_angle_classifier)
    with pymupdf.open(str(document.path)) as pdf:
        for page_model in document.pages:
            if page_range and not (page_range[0] <= page_model.number <= page_range[1]):
                continue
            if progress is not None:
                progress(
                    page_model.number,
                    document.page_count,
                    f"page {page_model.number}: "
                    + ("scanned page" if page_model.is_scanned else "reading tables"),
                )
            page = pdf[page_model.number - 1]
            if page_model.is_scanned:
                found = _scanned_page_tables(
                    page, page_model, len(tables) + 1, previous, overrides, engine, dpi, issues,
                    cells=ocr_cells,
                )
            elif page_model.has_text_layer:
                found = _native_page_tables(
                    page, page_model, len(tables) + 1, previous, overrides, issues
                )
            else:
                issues.append(
                    ImportIssue(
                        "warning",
                        "PDF_EMPTY_PAGE",
                        f"page {page_model.number} has neither text nor a page image",
                        page_model.number,
                    )
                )
                found = []
            for table in found:
                tables.append(table)
                previous = table
    if engine.used:
        how = "per grid cell" if ocr_cells else "page text detection"
        issues.append(
            ImportIssue(
                "info", "PDF_OCR_ENGINE", f"text recognized with {engine.name} at {dpi} dpi, {how}"
            )
        )
    return tables, issues


def _find_tables(page):
    finder = page.find_tables()
    if finder.tables:
        return list(finder.tables), "lines"
    finder = page.find_tables(strategy="text")
    return list(finder.tables), "text"


def _native_page_tables(page, page_model, next_index, previous, overrides, issues):
    found: list[RawTable] = []
    tables, strategy = _find_tables(page)
    for table in tables:
        data = table.extract()
        if not data:
            continue
        row_boxes = [list(row.cells) for row in table.rows]
        cell_rows: list[list[RawCell]] = []
        for row_index, row in enumerate(data):
            boxes = row_boxes[row_index] if row_index < len(row_boxes) else []
            cell_rows.append(
                [
                    RawCell(
                        text=clean_cell_text(cell),
                        bbox=tuple(boxes[column]) if column < len(boxes) and boxes[column] else None,
                        confidence=NATIVE_TEXT_CONFIDENCE,
                        source=SOURCE_NATIVE,
                    )
                    for column, cell in enumerate(row)
                ]
            )
        external = None
        if table.header is not None and table.header.external and table.header.names:
            external = [clean_cell_text(name) for name in table.header.names]
        raw_table = _build_table(
            cell_rows,
            external_header=external,
            page_number=page_model.number,
            index=next_index + len(found),
            strategy=strategy,
            bbox=tuple(table.bbox),
            previous=previous if not found else found[-1],
            overrides=overrides,
            issues=issues,
            text_source=SOURCE_NATIVE,
        )
        if raw_table is not None:
            found.append(raw_table)
    return found


def _scanned_page_tables(
    page, page_model, index, previous, overrides, engine, dpi, issues, cells: bool = True
):
    from .scan import (
        detect_grid,
        grid_cells,
        ocr_boxes,
        recognize_cells,
        render_deskewed,
        second_pass_cells,
        text_layer_boxes,
    )

    raster = render_deskewed(page, dpi=dpi)
    grid = detect_grid(raster.gray)
    rgb = None
    if grid is None:
        issues.append(
            ImportIssue(
                "warning",
                "PDF_NO_GRID",
                f"page {page_model.number}: no ruled table found on the scanned page",
                page_model.number,
            )
        )
        return []
    if page_model.has_text_layer:
        cell_rows = grid_cells(raster, grid, text_layer_boxes(page))
        source = SOURCE_TEXT_LAYER
    else:
        ocr_engine = engine.get()
        if ocr_engine is None:
            issues.append(
                ImportIssue(
                    "error",
                    "PDF_EXTRACTION_REVIEW_REQUIRED",
                    f"page {page_model.number} has no text layer and OCR is not "
                    f"available ({engine.error}); nothing was extracted from it",
                    page_model.number,
                )
            )
            return []
        rgb = raster.rgb()
        source = SOURCE_OCR
        if cells:
            cell_rows = recognize_cells(raster, grid, ocr_engine, rgb)
            rgb = None  # every cell was read already; no second pass
        else:
            cell_rows = grid_cells(raster, grid, ocr_boxes(raster, ocr_engine, rgb))
    strategy = f"scan+{source}" + (
        f" (deskewed {raster.skew_degrees:.2f}°)" if raster.skew_degrees else ""
    )
    raw_table = _build_table(
        cell_rows,
        external_header=None,
        page_number=page_model.number,
        index=index,
        strategy=strategy,
        bbox=raster.to_page(grid.bbox),
        previous=previous,
        overrides=overrides,
        issues=issues,
        text_source=source,
    )
    if raw_table is None:
        return []
    if rgb is not None:
        # Page text detection: mapped cells the detector left empty get a
        # recognition-only pass on their crop (isolated digits are often
        # missed by text detectors).
        mapped_columns = set(raw_table.mapping.values())
        targets = [
            cell
            for row in raw_table.rows
            for column, cell in enumerate(row)
            if column in mapped_columns and not cell.text
        ]
        second_pass_cells(rgb, grid, targets, engine.get(), dpi=dpi)
    return [raw_table]


def _build_table(
    cell_rows: list[list[RawCell]],
    *,
    external_header: list[str] | None,
    page_number: int,
    index: int,
    strategy: str,
    bbox: BBox,
    previous: RawTable | None,
    overrides: dict[str, str] | None,
    issues: list[ImportIssue],
    text_source: str,
) -> RawTable | None:
    texts = [[cell.text for cell in row] for row in cell_rows]
    non_empty = [i for i, row in enumerate(texts) if any(row)]
    if not non_empty:
        return None
    first = non_empty[0]

    header: list[str] | None = None
    inherited = False
    first_data_row = first
    if external_header:
        header = external_header
    elif is_parameter_header(map_columns(texts[first], overrides)):
        header = texts[first]
        first_data_row = first + 1
    if header is None:
        if previous is not None and len(previous.header) == len(texts[first]):
            header = list(previous.header)
            inherited = True
        else:
            issues.append(
                ImportIssue(
                    "warning",
                    "PDF_TABLE_SKIPPED",
                    f"page {page_number}: table {index} skipped — header not "
                    f"recognized: {[t.replace(chr(10), ' ') for t in texts[first]]}",
                    page_number,
                )
            )
            return None
    mapping = map_columns(header, overrides)
    if not is_parameter_header(mapping):
        issues.append(
            ImportIssue(
                "warning",
                "PDF_TABLE_SKIPPED",
                f"page {page_number}: table {index} skipped — header lacks "
                f"parameter/word columns: {header}",
                page_number,
            )
        )
        return None
    header_key = [normalize_header(h) for h in header]

    rows: list[list[RawCell]] = []
    for row_index in range(first_data_row, len(texts)):
        row_text = texts[row_index]
        if not any(row_text):
            continue
        if [normalize_header(t) for t in row_text] == header_key:
            continue  # repeated header inside a long table
        rows.append(cell_rows[row_index])
    return RawTable(
        page_number=page_number,
        index=index,
        bbox=bbox,
        strategy=strategy,
        header=list(header),
        mapping=mapping,
        rows=rows,
        header_inherited=inherited,
        text_source=text_source,
    )


# --------------------------------------------------------------------------
# Tables → raw rows
# --------------------------------------------------------------------------

STRUCTURE_FIELDS = ("word_location", "parameter_type", "msb", "lsb", "bits", "frequency", "resolution")
CONTINUATION_FIELDS = (
    "parameter_name", "mnemonic_and_name", "description", "units",
    "true_state", "false_state", "notes",
)


def rows_from_tables(tables: list[RawTable]) -> list[RawParameterRow]:
    """Flatten detected tables into raw rows (document order).

    A row that carries no structural cells (word, type, bits, frequency,
    resolution) but continues text columns is the tail of the previous row
    split by a page break; it is merged into that row and noted.
    """
    rows: list[RawParameterRow] = []
    for table in tables:
        for row_index, cells in enumerate(table.rows, start=1):
            row = row_from_cells(table, row_index, cells)
            if rows and _is_continuation(row):
                _merge_continuation(rows[-1], row)
                continue
            rows.append(row)
    return rows


def _is_continuation(row: RawParameterRow) -> bool:
    if any(row.raw(fld).strip() for fld in STRUCTURE_FIELDS):
        return False
    return any(row.raw(fld).strip() for fld in CONTINUATION_FIELDS)


def _merge_continuation(target: RawParameterRow, tail: RawParameterRow) -> None:
    for fld in CONTINUATION_FIELDS:
        text = tail.raw(fld).strip()
        if not text:
            continue
        existing = target.raw(fld).strip()
        setattr(target, fld, f"{existing}\n{text}" if existing else text)
        if fld not in target.provenance and fld in tail.provenance:
            target.provenance[fld] = tail.provenance[fld]
    target.cells = tuple(
        f"{a}\n{b}" if a and b else (a or b) for a, b in zip(target.cells, tail.cells)
    )
    target.extraction_notes.append(
        f"continued by page {tail.page_number} table {tail.table_index} row {tail.row_index}"
    )


def row_from_cells(table: RawTable, row_index: int, cells: list[RawCell]) -> RawParameterRow:
    row = RawParameterRow(
        page_number=table.page_number,
        table_index=table.index,
        row_index=row_index,
        columns=tuple(table.header),
        cells=tuple(cell.text for cell in cells),
        text_source=table.text_source,
    )
    boxes = [cell.bbox for cell in cells if cell.bbox]
    if boxes:
        row.bbox = (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )
    mapped_columns = set(table.mapping.values())
    for fld, column in table.mapping.items():
        if column >= len(cells):
            continue
        cell = cells[column]
        setattr(row, fld, cell.text)
        row.provenance[fld] = RawFieldProvenance(
            page_number=table.page_number,
            bbox=cell.bbox,
            raw_text=cell.text,
            confidence=cell.confidence,
            source=cell.source,
            note=cell.note,
        )
    for column, cell in enumerate(cells):
        if column in mapped_columns or not cell.text:
            continue
        header = table.header[column] if column < len(table.header) else ""
        row.extra[header or f"column {column + 1}"] = cell.text
    return row
