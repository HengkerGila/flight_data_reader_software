"""The CN235-220 Flight Data Systems dataframe layout document (scanned).

Pages 3–13 carry an Acrobat text layer and import in seconds; pages 14–21
need OCR (minutes) and are only exercised with ARINC717_OCR_TESTS=1.
"""

import glob
import os

import pytest

pytest.importorskip("pymupdf")
pytest.importorskip("numpy")

from arinc717_reader.dataframe.pdf_importer import ImportProfile, import_pdf
from arinc717_reader.dataframe.pdf_importer.normalize import BITS_RANGE_PER_WORD
from arinc717_reader.dataframe.pdf_importer.review import STATE_APPROVED, STATE_VALIDATED
from arinc717_reader.dataframe.pdf_importer.scan import RapidOcrEngine

DOCUMENTS = glob.glob("examples/Scanned_from_UK_Lexmark*data frame.pdf")
pytestmark = pytest.mark.skipif(not DOCUMENTS, reason="real dataframe document not present")


def by_name(session, prefix):
    return next(item for item in session.items if item.display_name.startswith(prefix))


def mapping(parameter):
    return [[(s.word, s.msb, s.lsb) for s in o.segments] for o in parameter.occurrences]


def test_text_layer_pages():
    session = import_pdf(DOCUMENTS[0], ImportProfile(page_range=(3, 13)))
    assert session.page_count == 21 and session.tables_found == 11
    assert 190 <= len(session.items) <= 200
    assert session.effective.frequency_unit == "seconds"
    assert session.effective.multiword_bits == BITS_RANGE_PER_WORD
    assert {"PDF_SCANNED", "PDF_FREQUENCY_UNIT", "PDF_BITS_LAYOUT"} <= {i.code for i in session.issues}
    assert sum(1 for item in session.items if item.candidate is None) <= 3
    counts = session.state_counts()
    assert counts[STATE_APPROVED] >= 120

    power = by_name(session, "28VDC Power Input")
    assert mapping(power.candidate) == [[(247, 12, 1)]]
    assert power.candidate.occurrences[0].segments[0].subframes == (1, 2, 3, 4)
    assert (power.candidate.conversion.offset, power.candidate.conversion.resolution) == (3.5, 0.01)
    assert {i.rule for i in power.normalization_issues if i.needs_review} >= {
        "normalize.resolution_repaired", "normalize.resolution_pair",
    }

    aoa = by_name(session, "AOAL")
    assert aoa.candidate.description == "LH Angle Of Attack"
    assert mapping(aoa.candidate) == [[(14, 9, 1), (15, 12, 9)], [(142, 9, 1), (143, 12, 9)]]
    assert aoa.candidate.parameter_type == "analog_unsigned"
    assert aoa.candidate.conversion.resolution == 0.044
    assert aoa.state in (STATE_APPROVED, STATE_VALIDATED)
    assert aoa.raw.text_source == "text_layer" and aoa.raw.page_number == 3

    armed = by_name(session, "AP Armed Mode: Alt Mode Armed")
    assert armed.candidate.parameter_type == "discrete"
    assert mapping(armed.candidate) == [[(178, 4, 4)]]
    assert armed.candidate.true_state == "Alt Mode Armed" and armed.candidate.false_state is None
    assert armed.state == STATE_APPROVED

    month = by_name(session, "Date: Date Month Ones Digit")
    assert month.candidate.parameter_type == "bcd" and month.error_count == 0


@pytest.mark.skipif(
    os.environ.get("ARINC717_OCR_TESTS") != "1" or not RapidOcrEngine.available(),
    reason="set ARINC717_OCR_TESTS=1 (needs rapidocr-onnxruntime; takes minutes)",
)
def test_full_document_with_ocr():
    session = import_pdf(DOCUMENTS[0])
    assert session.tables_found == 19
    assert 300 <= len(session.items) <= 340
    ocr_rows = [item for item in session.items if item.raw.text_source == "ocr"]
    assert len(ocr_rows) >= 120
    mfd = by_name(session, "MFD #2 Format:")
    assert "Nav Log Display" in mfd.candidate.description  # page 12 row continued on page 14
    pitch = by_name(session, "Pitch")
    assert len(pitch.candidate.occurrences) == 4
