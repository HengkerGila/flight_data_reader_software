"""Synthetic dataframe-document generator for closed-loop importer testing.

Renders a canonical dataframe as the kind of PDF the importer consumes so
the pipeline can be verified end-to-end without a vendor document:

    dataframe → PDF → import → review → publish → same dataframe

Two column layouts are available: the generic one (one column per canonical
field) and the "fds" layout that mirrors the CN235 Flight Data Systems
dataframe layout document (stacked mnemonic/name, sample interval in
seconds, word pairs with MSB/LSB ranges, "0 (all 4)" subframes, an
"offset, resolution" pair).  Pages can also be emitted as scans: an image of
the page, optionally skewed, with or without an invisible text layer.

This is test/example tooling only; it is not part of the runtime path.
"""

from __future__ import annotations

from pathlib import Path

from ...domain.dataframe import DataframeDefinition
from ...domain.parameter import ParameterDefinition
from ..editor import ALL_SUBFRAMES
from .ingest import load_pymupdf
from .normalize import FRAME_SECONDS

STYLE_GENERIC = "generic"
STYLE_FDS = "fds"

DOCUMENT_COLUMNS = (
    "Parameter",
    "Description",
    "Type",
    "Freq (Hz)",
    "Word",
    "Subframe",
    "MSB",
    "LSB",
    "Units",
    "Resolution",
    "Offset",
    "Min",
    "Max",
    "1 =",
    "0 =",
    "Remarks",
)
_GENERIC_WEIGHTS = (9, 15, 8, 5, 6, 6, 4, 4, 5, 6, 5, 5, 6, 6, 6, 12)

FDS_COLUMNS = (
    "Parameter Mnemonic & Name",
    "Parameter Type",
    "Frequency",
    "Word Location",
    "A717 MSB",
    "A717 LSB",
    "Subframe",
    "Units",
    "Resolution",
    "True State",
    "False State",
    "Notes",
)
_FDS_WEIGHTS = (17, 8, 7, 9, 5, 5, 7, 6, 8, 8, 8, 14)

SCAN_IMAGE = "image"            # page image only (needs OCR)
SCAN_TEXT_LAYER = "text_layer"  # page image plus invisible text (Acrobat-like)


def _segments_of(parameter: ParameterDefinition):
    occurrences = sorted(parameter.occurrences, key=lambda o: o.index)
    if not occurrences:
        raise ValueError(f"{parameter.mnemonic}: no mapping")
    per_occurrence = [sorted(o.segments, key=lambda s: s.sequence) for o in occurrences]
    subframes = {tuple(s.subframes) for segments in per_occurrence for s in segments}
    if len(subframes) != 1:
        raise ValueError(f"{parameter.mnemonic}: segments differ in subframes")
    return per_occurrence, subframes.pop()


def parameter_to_document_row(parameter: ParameterDefinition) -> list[str]:
    """Express one parameter the way a generic dataframe document lists it."""
    per_occurrence, subframe_tuple = _segments_of(parameter)
    if {len(s) for s in per_occurrence} == {1}:
        segments = [s[0] for s in per_occurrence]
    elif len(per_occurrence) == 1:
        segments = per_occurrence[0]
    else:
        raise ValueError(
            f"{parameter.mnemonic}: several multi-segment occurrences cannot be "
            "written as one generic document row"
        )
    words = ", ".join(str(s.word) for s in segments)
    msbs = [str(max(s.msb, s.lsb)) for s in segments]
    lsbs = [str(min(s.msb, s.lsb)) for s in segments]
    if len(set(msbs)) == 1 and len(set(lsbs)) == 1:
        msb, lsb = msbs[0], lsbs[0]
    else:
        msb, lsb = "/".join(msbs), "/".join(lsbs)
    frequency = len(per_occurrence) * len(subframe_tuple) / FRAME_SECONDS
    subframe_text = "all" if subframe_tuple == ALL_SUBFRAMES else ",".join(map(str, subframe_tuple))
    return [
        parameter.mnemonic,
        parameter.description or "",
        parameter.source_parameter_type or "",
        format(frequency, "g"),
        words,
        subframe_text,
        msb,
        lsb,
        parameter.unit or "",
        format(parameter.conversion.resolution, "g"),
        format(parameter.conversion.offset, "g"),
        "" if parameter.minimum is None else format(parameter.minimum, "g"),
        "" if parameter.maximum is None else format(parameter.maximum, "g"),
        parameter.true_state or "",
        parameter.false_state or "",
        parameter.notes or "",
    ]


def parameter_to_fds_row(parameter: ParameterDefinition) -> list[str]:
    """Express one parameter in the FDS dataframe-layout style."""
    per_occurrence, subframe_tuple = _segments_of(parameter)
    widths = {len(segments) for segments in per_occurrence}
    if widths == {1}:
        words = ", ".join(str(s[0].word) for s in per_occurrence)
        segments = [s[0] for s in per_occurrence]
        msb = "/".join(dict.fromkeys(str(max(s.msb, s.lsb)) for s in segments))
        lsb = "/".join(dict.fromkeys(str(min(s.msb, s.lsb)) for s in segments))
    elif widths == {2}:
        words = ", ".join(f"{s[0].word}-{s[1].word}" for s in per_occurrence)
        first = {(max(s[0].msb, s[0].lsb), min(s[0].msb, s[0].lsb)) for s in per_occurrence}
        second = {(max(s[1].msb, s[1].lsb), min(s[1].msb, s[1].lsb)) for s in per_occurrence}
        if len(first) != 1 or len(second) != 1:
            raise ValueError(f"{parameter.mnemonic}: word pairs differ in bit layout")
        msb = "{}-{}".format(*first.pop())
        lsb = "{}-{}".format(*second.pop())
    else:
        raise ValueError(f"{parameter.mnemonic}: FDS rows hold single words or word pairs")
    interval = FRAME_SECONDS / (len(per_occurrence) * len(subframe_tuple))
    subframe_text = "0 (all 4)" if subframe_tuple == ALL_SUBFRAMES else ",".join(map(str, subframe_tuple))
    name = parameter.mnemonic + (f"\n{parameter.description}" if parameter.description else "")
    resolution = format(parameter.conversion.resolution, "g")
    if parameter.conversion.offset:
        resolution = f"{parameter.conversion.offset:g}, {resolution}"
    return [
        name,
        parameter.source_parameter_type or "",
        format(interval, "g"),
        words,
        msb,
        lsb,
        subframe_text,
        parameter.unit or "-",
        resolution,
        parameter.true_state or "-",
        parameter.false_state or "-",
        parameter.notes or "",
    ]


def dataframe_to_document_rows(dataframe: DataframeDefinition, style: str = STYLE_GENERIC) -> list[list[str]]:
    convert = parameter_to_fds_row if style == STYLE_FDS else parameter_to_document_row
    return [convert(p) for p in dataframe.parameters]


def write_dataframe_pdf(
    dataframe: DataframeDefinition,
    path: str | Path,
    *,
    style: str = STYLE_GENERIC,
    rows_per_page: int = 16,
    repeat_header: bool = True,
    image_only_pages: int = 0,
    title: str | None = None,
    scanned: str | None = None,
    skew_degrees: float = 0.0,
    scan_dpi: int = 150,
    font_size: float = 6.5,
) -> Path:
    """Write ``dataframe`` as a PDF dataframe document and return its path.

    ``scanned`` = "image" turns every page into a page image (rotated by
    ``skew_degrees``) without text; "text_layer" adds the words back as
    invisible text at their (rotated) positions, like a scanner's OCR layer.
    """
    pymupdf = load_pymupdf()
    path = Path(path)
    columns = FDS_COLUMNS if style == STYLE_FDS else DOCUMENT_COLUMNS
    weights = _FDS_WEIGHTS if style == STYLE_FDS else _GENERIC_WEIGHTS
    rows = dataframe_to_document_rows(dataframe, style)
    md = dataframe.metadata
    page_width, page_height = 842.0, 595.0  # A4 landscape
    margin = 30.0
    row_height = max(26.0, 3.2 * font_size)
    usable = page_width - 2 * margin
    total_weight = sum(weights)
    widths = [usable * w / total_weight for w in weights]

    document = pymupdf.open()
    try:
        page = document.new_page(width=page_width, height=page_height)
        y = margin + 10
        page.insert_text(
            (margin, y),
            title or f"{md.aircraft_type or 'Aircraft'} — Flight Data Recorder Dataframe Document",
            fontsize=14,
            fontname="hebo",
        )
        y += 22
        for line in (
            f"Dataframe: {md.dataframe_name}",
            f"Revision: {md.revision or '-'}    Issue date: {md.issue_date or '-'}",
            f"Recording format: ARINC 717, {md.wps} WPS, 4 subframes of 1 second",
            "Sync words: " + ", ".join(str(w) for w in md.sync_words),
            "Bit numbering: 12 (MSB) .. 1 (LSB).  Engineering value = decimal × resolution + offset.",
        ):
            page.insert_text((margin, y), line, fontsize=9)
            y += 13
        y += 8

        def needed_height(cells, bold=False):
            """Row height that fits every cell's wrapped lines (insert_textbox
            writes nothing at all when the text does not fit)."""
            fontname = "hebo" if bold else "helv"
            lines = 1
            for width, text in zip(widths, cells):
                count = 0
                for line in text.split("\n"):
                    length = pymupdf.get_text_length(line, fontname=fontname, fontsize=font_size)
                    count += max(1, int(length // max(width - 6, 1)) + 1)
                lines = max(lines, count)
            # insert_textbox lays lines out at ~1.5 × font size and writes
            # nothing when they do not fit.
            return max(row_height, lines * font_size * 1.5 + 8)

        def draw_row(target_page, top, cells, height, bold=False):
            x = margin
            for width, text in zip(widths, cells):
                rect = pymupdf.Rect(x, top, x + width, top + height)
                target_page.draw_rect(rect, color=(0, 0, 0), width=0.5)
                target_page.insert_textbox(
                    rect + (2, 2, -2, -2),
                    text,
                    fontsize=font_size,
                    fontname="hebo" if bold else "helv",
                )
                x += width

        header_height = needed_height(columns, bold=True)
        draw_row(page, y, columns, header_height, bold=True)
        y += header_height
        on_page = 0
        for row in rows:
            height = needed_height(row)
            if on_page >= rows_per_page or y + height > page_height - margin:
                page = document.new_page(width=page_width, height=page_height)
                y = margin
                page.insert_text((margin, y), f"{md.dataframe_name} (continued)", fontsize=9)
                y += 16
                if repeat_header:
                    draw_row(page, y, columns, header_height, bold=True)
                    y += header_height
                on_page = 0
            draw_row(page, y, row, height)
            y += height
            on_page += 1

        for _ in range(image_only_pages):
            pixmap = document[0].get_pixmap(dpi=72)
            extra = document.new_page(width=page_width, height=page_height)
            extra.insert_image(extra.rect, pixmap=pixmap)

        path.parent.mkdir(parents=True, exist_ok=True)
        if scanned:
            _save_as_scan(document, path, scanned, skew_degrees, scan_dpi, pymupdf)
        else:
            document.save(str(path))
    finally:
        document.close()
    return path


def _save_as_scan(document, path, mode, skew_degrees, dpi, pymupdf) -> None:
    zoom = dpi / 72.0
    scan = pymupdf.open()
    try:
        for page in document:
            matrix = pymupdf.Matrix(zoom, zoom).prerotate(skew_degrees)
            pixmap = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB)
            target = scan.new_page(width=page.rect.width, height=page.rect.height)
            rect = pymupdf.Rect(
                pixmap.x, pixmap.y, pixmap.x + pixmap.width, pixmap.y + pixmap.height
            ) / zoom
            target.insert_image(rect, pixmap=pixmap)
            if mode == SCAN_TEXT_LAYER:
                # Invisible text at the rotated positions of the original
                # lines (whole lines keep natural word spacing).  The image
                # was rendered with ``prerotate(skew)`` about the origin, and
                # ``morph`` applies its matrix the other way round.
                pivot = pymupdf.Point(0, 0)
                morph = (pivot, pymupdf.Matrix(1, 1).prerotate(-skew_degrees))
                for block in page.get_text("dict")["blocks"]:
                    for line in block.get("lines", []):
                        spans = line.get("spans", [])
                        text = "".join(span["text"] for span in spans).strip()
                        if not text or not spans:
                            continue
                        origin = spans[0]["origin"]
                        target.insert_text(
                            origin, text, fontsize=spans[0]["size"],
                            render_mode=3, morph=morph,
                        )
        scan.save(str(path))
    finally:
        scan.close()
