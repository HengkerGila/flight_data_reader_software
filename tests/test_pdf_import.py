"""PDF pipeline end-to-end on synthetic documents (spec §28–§30).

Closed loop: dataframe → PDF → import → review → publish → same dataframe.
Skipped when PyMuPDF is not installed.
"""

import hashlib

import pytest

pymupdf = pytest.importorskip("pymupdf")

from arinc717_reader.dataframe.compare import dataframe_differences
from arinc717_reader.dataframe.pdf_importer import ImportProfile, PdfIngestError, import_pdf
from arinc717_reader.dataframe.pdf_importer.extract import extract_tables, rows_from_tables
from arinc717_reader.dataframe.pdf_importer.ingest import detect_wps, ingest_pdf
from arinc717_reader.dataframe.pdf_importer.render import render_page_png, render_region_png
from arinc717_reader.dataframe.pdf_importer.review import (
    STATE_APPROVED,
    STATE_REVIEW_REQUIRED,
)
from arinc717_reader.dataframe.pdf_importer.synth import DOCUMENT_COLUMNS, write_dataframe_pdf
from arinc717_reader.decoder.parameter_decoder import ParameterDecoder
from arinc717_reader.encoder.frame_builder import build_blank_frame
from arinc717_reader.services import ServiceError
from arinc717_reader.services.dataframe_service import DataframeService
from arinc717_reader.state.dataframe_store import DataframeStore

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def demo_pdf(tmp_path, demo_dataframe):
    return write_dataframe_pdf(demo_dataframe, tmp_path / "demo.pdf", rows_per_page=5)


def test_ingest_reports_pages_and_wps(demo_pdf):
    document = ingest_pdf(demo_pdf)
    assert document.page_count == 3
    assert document.pages_without_text() == []
    assert document.sha256 == hashlib.sha256(demo_pdf.read_bytes()).hexdigest()
    assert detect_wps(document.text) == 256
    assert detect_wps("no rate stated here") is None
    assert detect_wps("64 WPS on one page, 256 WPS on another") is None  # ambiguous


def test_extract_tables_across_pages(demo_pdf):
    document = ingest_pdf(demo_pdf)
    tables, issues = extract_tables(document)
    assert issues == []
    assert [table.page_number for table in tables] == [1, 2, 3]
    assert all(table.header == list(DOCUMENT_COLUMNS) for table in tables)
    assert not any(table.header_inherited for table in tables)
    rows = rows_from_tables(tables)
    assert len(rows) == 12
    assert [row.page_number for row in rows] == [1] * 5 + [2] * 5 + [3] * 2
    first = rows[0]
    assert first.parameter_name == "PITCH ATT #1"
    assert first.word_location == "4, 132"
    assert first.bbox is not None and first.confidence is None  # native text: no OCR score
    assert first.text_source == "native"
    assert first.provenance["word_location"].page_number == 1
    assert first.provenance["word_location"].bbox is not None
    assert first.columns == tuple(DOCUMENT_COLUMNS) and first.cells[0] == "PITCH ATT #1"
    assert first.extra == {}


def test_continuation_tables_inherit_header(tmp_path, demo_dataframe):
    path = write_dataframe_pdf(
        demo_dataframe, tmp_path / "cont.pdf", rows_per_page=5, repeat_header=False
    )
    tables, issues = extract_tables(ingest_pdf(path))
    assert issues == []
    assert [table.header_inherited for table in tables] == [False, True, True]
    assert len(rows_from_tables(tables)) == 12


def test_import_roundtrip_matches_demo(demo_pdf, demo_dataframe):
    session = import_pdf(demo_pdf)
    assert [issue for issue in session.issues if issue.severity != "info"] == []
    assert session.effective.frequency_unit == "hz"  # generic layout states Hz
    assert session.detected_wps == 256 and session.wps == 256
    assert session.source_path == str(demo_pdf)
    counts = session.state_counts()
    assert counts[STATE_APPROVED] == 11 and counts[STATE_REVIEW_REQUIRED] == 1
    [pending] = session.pending_items()
    assert pending.display_name == "SPARE 15"  # unknown source type → review
    session.approve(pending.index)
    published = session.publish()
    assert dataframe_differences(demo_dataframe, published) == []
    assert published.metadata.source_type == "pdf"
    assert published.metadata.source_hash == session.source_hash
    pitch = published.parameters[0]
    assert pitch.provenance.source_type == "pdf"
    assert pitch.provenance.extra["page"] == 1 and pitch.provenance.extra["bbox"]
    assert pitch.provenance.extra["raw_fields"]["word_location"] == "4, 132"
    assert pitch.provenance.extra["confidence"] is None  # native text carries no OCR score
    assert pitch.provenance.raw_record[0] == "PITCH ATT #1"


def test_published_dataframe_decodes_spec_oracle(demo_pdf):
    session = import_pdf(demo_pdf)
    for item in session.pending_items():
        session.approve(item.index)
    dataframe = session.publish()
    frame = build_blank_frame(
        dataframe.metadata.wps, frame_index=0, sync_words=dataframe.metadata.sync_words
    )
    frame.set_word(1, 4, 3412)  # spec §9 example word
    values = ParameterDecoder().decode_frame(frame, dataframe)
    pitch = [
        v
        for v in values
        if v.parameter_id == "pdf-0001" and v.subframe == 1 and v.occurrence_index == 1
    ]
    assert len(pitch) == 1
    assert pitch[0].engineering_value == pytest.approx(-30.096)


def test_image_only_page_is_reported_when_ocr_is_off(tmp_path, demo_dataframe):
    path = write_dataframe_pdf(demo_dataframe, tmp_path / "scan.pdf", image_only_pages=1)
    session = import_pdf(path, ImportProfile(ocr="never"))
    assert len(session.items) == 12
    [issue] = [issue for issue in session.issues if issue.severity == "error"]
    assert issue.code == "PDF_EXTRACTION_REVIEW_REQUIRED"
    assert issue.page_number == 2 and "OCR" in issue.message


def test_document_without_parameter_table(tmp_path):
    path = tmp_path / "prose.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Just prose about a 256 WPS recorder.")
    document.save(str(path))
    document.close()
    session = import_pdf(path)
    assert session.items == []
    assert [issue.code for issue in session.issues] == ["PDF_NO_PARAMETER_TABLE"]
    assert session.detected_wps == 256


def test_unreadable_files_are_explicit_errors(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_text("this is not a pdf")
    with pytest.raises(PdfIngestError):
        ingest_pdf(bad)
    service = DataframeService(DataframeStore())
    with pytest.raises(ServiceError) as exc:
        service.import_pdf(bad)
    assert exc.value.state == "PDF_INGEST_ERROR"
    with pytest.raises(ServiceError) as exc:
        service.import_pdf(tmp_path / "missing.pdf")
    assert exc.value.state == "PDF_INGEST_ERROR"


def test_render_source_region(demo_pdf):
    session = import_pdf(demo_pdf)
    row = session.items[0].raw
    assert render_region_png(demo_pdf, row.page_number, row.bbox).startswith(PNG_SIGNATURE)
    assert render_page_png(demo_pdf, 1).startswith(PNG_SIGNATURE)
    with pytest.raises(ValueError):
        render_page_png(demo_pdf, 99)


def test_service_publish_loads_store_only_after_review(demo_pdf):
    store = DataframeStore()
    service = DataframeService(store)
    session = service.import_pdf(demo_pdf)
    with pytest.raises(ServiceError) as exc:
        service.publish_import(session)
    assert exc.value.state == "PDF_EXTRACTION_REVIEW_REQUIRED"
    assert store.dataframe is None
    session.exclude(11)
    dataframe, issues = service.publish_import(session)
    assert store.dataframe is dataframe and store.dirty
    assert len(dataframe.parameters) == 11 and issues == []
