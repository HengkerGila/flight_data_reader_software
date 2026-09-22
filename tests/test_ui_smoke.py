"""Offscreen GUI smoke test (spec §53.1 acceptance criteria).

Runs in a subprocess because a failed Qt platform init aborts the process.
Skipped when PySide6 is not installed.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from arinc717_reader.app import build_context
from arinc717_reader.demo import build_demo_dataframe
from arinc717_reader.ui.main_window import MainWindow

app = QApplication([])
ctx = build_context()
window = MainWindow(ctx)
window.show()

# Load the demo dataframe and generate a deterministic random frame.
ctx.dataframe_service.set_dataframe(build_demo_dataframe())
ctx.frame_service.new_random(seed=1)
app.processEvents()

model = window.frame_view_page._model
assert model.rowCount() == 256, model.rowCount()   # exactly WPS rows
assert model.columnCount() == 4                     # SF1..SF4

# Representation switching must not modify canonical data (spec §8.2)
before = [list(sf) for sf in ctx.frame_store.frame.subframes]
for rep in ("BIN", "OCT", "DEC", "HEX"):
    model.set_representation(rep)
    app.processEvents()
assert [list(sf) for sf in ctx.frame_store.frame.subframes] == before

# Editing one word updates only that canonical cell and re-decodes
ctx.frame_service.new_blank(with_sync_words=True)
app.processEvents()
ctx.frame_service.set_word(1, 4, 3412)
app.processEvents()
assert ctx.frame_store.frame.word(1, 4) == 3412
assert ctx.frame_store.frame.word(2, 4) == 0
pitch = [
    v for v in ctx.engineering_store.values
    if v.parameter_id == "demo-pitch" and v.subframe == 1 and v.occurrence_index == 1
]
assert len(pitch) == 1
assert abs(pitch[0].engineering_value - (-30.096)) < 1e-9, pitch[0].engineering_value

# Word inspector renders the decode chain for the edited cell
inspector = window.frame_view_page._inspector
inspector.show_word(1, 4)
text = inspector._text.toPlainText()
assert "PITCH ATT #1" in text and "1101010101" in text, text[:400]

# Engineering table shows one row per decoded sample
assert window.parameters_page._model.rowCount() == len(ctx.engineering_store.values)
assert model.rowCount() == 256

# Scenario: closed-loop through the simulation service
raws = ctx.simulation_service.apply_scenario({"demo-pitch": -20.0, "demo-gear": "DOWN"})
app.processEvents()
report = ctx.simulation_service.closed_loop_report(
    {"demo-pitch": -20.0, "demo-gear": "DOWN"}, raws
)
assert all(entry.passed for entry in report), [
    (e.parameter.mnemonic, e.decoded) for e in report
]

# Dataframe editor (spec §44 "edit mappings", §1.1 manual creation)
from arinc717_reader.ui.dataframe_view.metadata_dialog import MetadataDialog
from arinc717_reader.ui.dataframe_view.parameter_edit_dialog import (
    COL_SF, COL_WORD, ParameterEditDialog,
)

page = window.dataframe_page
dlg = MetadataDialog(ctx, "new", window)
dlg._name.setText("HANDMADE")
dlg._wps.setCurrentText("64")
dlg._sync[0].setText("abc")
dlg._try_accept()                                   # invalid sync word keeps it open
assert "SF1" in dlg._error.text() and ctx.dataframe_store.dataframe.metadata.wps == 256
dlg._sync[0].setText("0x247")
dlg._try_accept()
app.processEvents()
assert ctx.dataframe_store.dataframe.metadata.wps == 64
assert ctx.dataframe_store.dataframe.parameters == []
assert ctx.dataframe_store.dirty and window.windowTitle().endswith("HANDMADE*")

ctx.frame_service.new_blank(with_sync_words=True)
ctx.frame_service.set_word(1, 4, 3412)
dlg = ParameterEditDialog(ctx, None, window)
dlg._mnemonic.setText("PITCH ATT #1")
dlg._source_type.setCurrentText("Signed Analog")
dlg._resolution.setText("0.176")
dlg._table.item(0, COL_SF).setText("1,9")
dlg._try_accept()                                   # bad subframes keep it open
assert "subframes" in dlg._error.text() and ctx.dataframe_store.dataframe.parameters == []
dlg._table.item(0, COL_SF).setText("1")
dlg._table.item(0, COL_WORD).setText("4")
dlg._table.item(0, 4).setText("3")
dlg._check()
assert dlg._issue_summary.text() == "No validation issues.", dlg._issue_summary.text()
dlg._try_accept()
app.processEvents()
assert [p.id for p in ctx.dataframe_store.dataframe.parameters] == ["man-pitch-att-1"]
values = ctx.engineering_store.values                # edit re-decoded the frame
assert len(values) == 1 and abs(values[0].engineering_value + 30.096) < 1e-9
assert page._tree.topLevelItemCount() == 1
page._tree.setCurrentItem(page._tree.topLevelItem(0))
assert page._edit_button.isEnabled()
page._search.setText("nomatch")
assert page._tree.topLevelItem(0).isHidden()
page._search.setText("")

# PDF importer review dialog (spec §28 manual review, §32, §46 review queue)
import importlib.util
if importlib.util.find_spec("pymupdf") is not None:
    import tempfile
    from pathlib import Path
    from arinc717_reader.dataframe.pdf_importer.synth import write_dataframe_pdf
    from arinc717_reader.ui.importer.pdf_review_dialog import PdfReviewDialog

    pdf_path = write_dataframe_pdf(
        build_demo_dataframe(), Path(tempfile.mkdtemp()) / "demo.pdf", rows_per_page=5
    )
    import_page = window.import_page
    session = import_page.run_import_blocking(str(pdf_path))   # import_pdf() runs this on a worker
    assert len(session.items) == 12 and session.page_count == 3
    assert "1 need review" in import_page._review_label.text(), import_page._review_label.text()
    assert import_page._review_button.isEnabled()

    dlg = PdfReviewDialog(ctx, session, window)
    assert dlg._table.rowCount() == 12
    assert not dlg._publish_button.isEnabled()           # SPARE 15 blocks publishing
    dlg._try_publish()                                   # refused; dialog stays open
    assert "SPARE 15" in dlg._error.text(), dlg._error.text()
    dlg._filter.setCurrentText("Needs review")
    visible = [r for r in range(dlg._table.rowCount()) if not dlg._table.isRowHidden(r)]
    assert len(visible) == 1, visible
    dlg.select_visible()                                 # bulk approve of the filtered rows
    details = dlg._details.toPlainText()
    assert "Verbatim extraction" in details and "normalize.type" in details, details[:600]
    dlg._approve_selected()
    assert session.items[11].state == "APPROVED"
    assert dlg._publish_button.isEnabled()
    # conventions: re-normalizing swaps the session and resets approvals
    dlg._filter.setCurrentText("All rows")
    dlg._pair_order.setCurrentIndex(1)                  # offset, resolution
    dlg._renormalize(confirm=False)
    assert dlg.session is not session and dlg.session.profile.resolution_pair == "offset_resolution"
    session = dlg.session
    assert session.items[11].state == "REVIEW_REQUIRED"
    dlg.select_index(11)
    dlg._approve_selected()
    assert session.items[11].state == "APPROVED"
    dlg._wps.setCurrentText("64")                        # word 132 no longer fits
    dlg._apply_metadata()
    assert session.items[0].state == "REVIEW_REQUIRED" and not dlg._publish_button.isEnabled()
    dlg._wps.setCurrentText("256")
    dlg._apply_metadata()
    assert session.items[0].state == "VALIDATED"
    dlg._approve_all_validated()
    dlg._try_publish()
    app.processEvents()
    published = ctx.dataframe_store.dataframe
    assert published.metadata.source_type == "pdf" and len(published.parameters) == 12
    assert ctx.dataframe_store.dirty and window.windowTitle().endswith("demo.pdf*")
    assert window.dataframe_page._tree.topLevelItemCount() == 12
    print("PDF_REVIEW_OK")

window.close()
print("UI_SMOKE_OK")
"""


@pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed"
)
def test_ui_smoke():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "UI_SMOKE_OK" in result.stdout
    if importlib.util.find_spec("pymupdf") is not None:
        assert "PDF_REVIEW_OK" in result.stdout
