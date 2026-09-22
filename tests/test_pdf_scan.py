"""Scanned-page extraction: grid detection, text layer and OCR (spec §28)."""

import dataclasses

import pytest

pymupdf = pytest.importorskip("pymupdf")
np = pytest.importorskip("numpy")

from arinc717_reader.dataframe.compare import dataframe_differences
from arinc717_reader.dataframe.pdf_importer import ImportProfile, import_pdf
from arinc717_reader.dataframe.pdf_importer.extract import (
    RawCell,
    RawTable,
    header_matches,
    map_columns,
    rows_from_tables,
)
from arinc717_reader.dataframe.pdf_importer.ingest import ingest_pdf
from arinc717_reader.dataframe.pdf_importer.normalize import BITS_RANGE_PER_WORD
from arinc717_reader.dataframe.pdf_importer.scan import (
    RapidOcrEngine,
    detect_grid,
    estimate_skew,
    render_deskewed,
)
from arinc717_reader.dataframe.pdf_importer.synth import (
    FDS_COLUMNS,
    SCAN_IMAGE,
    SCAN_TEXT_LAYER,
    STYLE_FDS,
    write_dataframe_pdf,
)

FDS_PROFILE = ImportProfile(multiword_bits=BITS_RANGE_PER_WORD, resolution_pair="offset_resolution")


def expected_from(demo_dataframe):
    """The FDS layout carries no min/max columns."""
    import copy

    expected = copy.deepcopy(demo_dataframe)
    for parameter in expected.parameters:
        parameter.minimum = parameter.maximum = None
    return expected


def test_fuzzy_headers_and_stacked_name_column():
    headers = ["Parameter Mnemonic & Name", "Parameter\nTvpe", "Frequency", "Word\nLocation",
               "A717\nMSB", "A717\nLSB", "Subframe", "Units", "Resolution", "True\nState",
               "False\nState", "Notes"]
    mapping = map_columns(headers)
    assert mapping["mnemonic_and_name"] == 0
    assert mapping["parameter_type"] == 1 and mapping["word_location"] == 3
    assert mapping["msb"] == 4 and mapping["lsb"] == 5
    assert mapping["true_state"] == 9 and mapping["false_state"] == 10
    how = dict((h, m) for h, _, m in header_matches(headers))
    assert how["Parameter\nTvpe"].startswith("fuzzy")
    assert map_columns(["ParameterMnemonic&Name", "Type", "WordLocation"])  # OCR without spaces
    assert "lsb" not in map_columns(["MSB", "Notes"])  # no MSB/LSB confusion


def test_continuation_rows_are_merged():
    def table(page, rows):
        return RawTable(
            page_number=page, index=page, bbox=(0, 0, 1, 1), strategy="scan+text_layer",
            header=list(FDS_COLUMNS), mapping=map_columns(list(FDS_COLUMNS)),
            rows=[[RawCell(text=t) for t in row] for row in rows], text_source="text_layer",
        )

    first = table(1, [
        ["MFD #2 Format\n- Map Display", "Discrete", "1", "208-209", "8-1", "12-5", "0", "-", "1", "0-0\n1-1", "-", "Raw decimal"],
    ])
    second = table(2, [
        ["- Nav Log Display\n- Engine Display", "", "", "", "", "", "", "", "", "6-6\n7-7", "", ""],
        ["PFD #2 Format\nEssential Mode", "Discrete", "1", "207", "2", "2", "0", "-", "1", "Essential\nMode", "-", ""],
    ])
    rows = rows_from_tables([first, second])
    assert len(rows) == 2
    merged = rows[0]
    assert "Nav Log Display" in merged.mnemonic_and_name
    assert merged.true_state == "0-0\n1-1\n6-6\n7-7"
    assert merged.extraction_notes == ["continued by page 2 table 2 row 1"]
    assert rows[1].mnemonic_and_name.startswith("PFD #2 Format")


def test_scanned_page_with_text_layer_round_trips(tmp_path, demo_dataframe):
    path = write_dataframe_pdf(
        demo_dataframe, tmp_path / "scan.pdf", style=STYLE_FDS, rows_per_page=7,
        scanned=SCAN_TEXT_LAYER, skew_degrees=0.4,
    )
    document = ingest_pdf(path)
    assert all(page.is_scanned and page.has_text_layer for page in document.pages)

    with pymupdf.open(str(path)) as pdf:
        raster = render_deskewed(pdf[0])
        assert abs(abs(raster.skew_degrees) - 0.4) < 0.15
        grid = detect_grid(raster.gray)
        assert grid is not None and grid.col_count == len(FDS_COLUMNS)
        assert grid.row_count == 8  # header + 7 rows

    session = import_pdf(path, FDS_PROFILE)
    assert session.tables_found == 2 and len(session.items) == 12
    assert all(item.raw.text_source == "text_layer" for item in session.items)
    [pending] = session.pending_items()
    assert pending.display_name == "SPARE 15"
    session.approve(pending.index)
    published = session.publish()
    assert dataframe_differences(expected_from(demo_dataframe), published) == []
    pitch = published.parameters[0]
    assert pitch.provenance.extra["text_source"] == "text_layer"
    assert pitch.provenance.extra["raw_fields"]["mnemonic_and_name"].startswith("PITCH ATT #1")


def test_scanned_page_without_grid_is_reported(tmp_path):
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "A scanned page with no table, 256 WPS")
    pixmap = page.get_pixmap(dpi=100)
    scan = pymupdf.open()
    target = scan.new_page(width=page.rect.width, height=page.rect.height)
    target.insert_image(target.rect, pixmap=pixmap)
    scan.save(str(tmp_path / "prose_scan.pdf"))
    session = import_pdf(tmp_path / "prose_scan.pdf", ImportProfile(ocr="never"))
    assert session.items == []
    assert "PDF_NO_GRID" in {issue.code for issue in session.issues}


@pytest.mark.skipif(not RapidOcrEngine.available(), reason="rapidocr-onnxruntime not installed")
def test_scanned_image_only_page_is_read_by_ocr(tmp_path, demo_dataframe):
    path = write_dataframe_pdf(
        demo_dataframe, tmp_path / "ocr.pdf", style=STYLE_FDS, rows_per_page=16,
        scanned=SCAN_IMAGE, skew_degrees=-0.3, font_size=8,
    )
    session = import_pdf(path, dataclasses.replace(FDS_PROFILE, ocr_dpi=200))
    assert session.tables_found == 1
    assert len(session.items) >= 10
    assert all(item.raw.text_source == "ocr" for item in session.items)
    candidates = [item.candidate for item in session.items if item.candidate is not None]
    assert len(candidates) >= 10
    pitch = next(c for c in candidates if c.mnemonic.replace(" ", "").startswith("PITCHATT"))
    assert [o.segments[0].word for o in pitch.occurrences] == [4, 132]
    assert pitch.provenance.extra["confidence"] is not None
