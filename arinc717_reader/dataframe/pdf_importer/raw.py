"""Raw extraction model (design spec §29, §30).

Everything a table yields before any interpretation: cells with verbatim
text, page, bounding box, text source and confidence.  ``RawParameterRow``
is what the normalizer consumes; it never holds derived values.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BBox = tuple[float, float, float, float]

SOURCE_NATIVE = "native"          # born-digital text
SOURCE_TEXT_LAYER = "text_layer"  # OCR text embedded by the scanner / Acrobat
SOURCE_OCR = "ocr"                # recognized by this application's OCR engine

# Mappable raw fields, in the order they are shown to reviewers.
RAW_FIELDS = (
    "parameter_name",
    "mnemonic_and_name",
    "description",
    "parameter_type",
    "sign",
    "frequency",
    "word_location",
    "subframe",
    "msb",
    "lsb",
    "bits",
    "units",
    "resolution",
    "offset",
    "minimum",
    "maximum",
    "true_state",
    "false_state",
    "notes",
)

# Fields whose text decides the mapping / conversion: low OCR confidence on
# these sends a row to review; on the others it is only noted.
CRITICAL_FIELDS = (
    "parameter_name",
    "mnemonic_and_name",
    "parameter_type",
    "sign",
    "frequency",
    "word_location",
    "subframe",
    "msb",
    "lsb",
    "bits",
    "resolution",
    "offset",
)


@dataclass
class RawFieldProvenance:
    page_number: int | None = None
    bbox: BBox | None = None
    raw_text: str | None = None
    confidence: float | None = None
    source: str = SOURCE_NATIVE
    note: str | None = None
    review_status: str = "EXTRACTED"


@dataclass
class RawCell:
    text: str
    bbox: BBox | None = None
    confidence: float | None = None
    source: str = SOURCE_NATIVE
    note: str | None = None                    # e.g. "second-pass OCR on the cell crop"
    grid_cell: tuple[int, int] | None = None   # (row, col) on a scanned page grid


@dataclass
class RawParameterRow:
    """One extracted table row, verbatim (design spec §29)."""

    parameter_name: str | None = None
    mnemonic_and_name: str | None = None
    description: str | None = None
    parameter_type: str | None = None
    sign: str | None = None
    frequency: str | None = None
    word_location: str | None = None
    subframe: str | None = None
    msb: str | None = None
    lsb: str | None = None
    bits: str | None = None
    units: str | None = None
    resolution: str | None = None
    offset: str | None = None
    minimum: str | None = None
    maximum: str | None = None
    true_state: str | None = None
    false_state: str | None = None
    notes: str | None = None

    # Columns the header mapping did not recognize, keyed by header text.
    extra: dict[str, str] = field(default_factory=dict)
    provenance: dict[str, RawFieldProvenance] = field(default_factory=dict)
    # Notes the extraction stage attached (continuation merges, etc.).
    extraction_notes: list[str] = field(default_factory=list)

    page_number: int | None = None
    table_index: int | None = None
    row_index: int | None = None
    bbox: BBox | None = None
    columns: tuple[str, ...] = ()
    cells: tuple[str, ...] = ()
    text_source: str = SOURCE_NATIVE

    def raw(self, fld: str) -> str:
        value = getattr(self, fld, None)
        return value if isinstance(value, str) else ""

    @property
    def confidence(self) -> float | None:
        values = [p.confidence for p in self.provenance.values() if p.confidence is not None]
        return min(values) if values else None

    def field_confidence(self, fld: str) -> float | None:
        provenance = self.provenance.get(fld)
        return provenance.confidence if provenance else None


@dataclass
class RawTable:
    page_number: int
    index: int  # 1-based, per document
    bbox: BBox
    strategy: str
    header: list[str]
    mapping: dict[str, int]
    rows: list[list[RawCell]] = field(default_factory=list)
    header_inherited: bool = False
    text_source: str = SOURCE_NATIVE


@dataclass
class ImportIssue:
    """Document-level extraction problem (a page or table, not a row)."""

    severity: str
    code: str
    message: str
    page_number: int | None = None
