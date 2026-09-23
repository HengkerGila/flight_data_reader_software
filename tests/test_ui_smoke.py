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

# The inspector follows the current cell: arrow keys, not only mouse clicks.
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
fv = window.frame_view_page
assert fv.select_cell(1, 3) and fv.current_cell() == (1, 3)
assert "Word Address   : 003" in inspector._text.toPlainText()
QTest.keyClick(fv._table, Qt.Key.Key_Down)          # word 3 -> word 4
app.processEvents()
assert fv.current_cell() == (1, 4)
text = inspector._text.toPlainText()
assert "Word Address   : 004" in text and "PITCH ATT #1" in text, text[:400]
QTest.keyClick(fv._table, Qt.Key.Key_Right)         # SF1 -> SF2
app.processEvents()
assert fv.current_cell() == (2, 4)
assert "Subframe       : 2" in inspector._text.toPlainText()
ctx.frame_service.new_random(seed=2)                # same shape: cell kept
app.processEvents()
assert fv.current_cell() == (2, 4)
ctx.frame_service.new_blank(with_sync_words=True)   # back to the blank frame for the checks below
ctx.frame_service.set_word(1, 4, 3412)
app.processEvents()
assert fv.current_cell() == (2, 4)

# The current cell's row is tinted (other columns of word 4 differ from word 5).
def cell_pixel(row, column):
    rect = fv._table.visualRect(fv._model.index(row, column))
    image = fv._table.viewport().grab().toImage()
    return image.pixelColor(rect.left() + 3, rect.center().y()).name()
same_row, other_row, selected = cell_pixel(3, 0), cell_pixel(4, 0), cell_pixel(3, 1)
assert same_row != other_row, (same_row, other_row)
assert selected not in (same_row, other_row), (selected, same_row, other_row)

# Engineering table shows one row per decoded sample
assert window.parameters_page._model.rowCount() == len(ctx.engineering_store.values)
assert model.rowCount() == 256

# Parameters page: the selected row, its trace and the column widths survive
# value updates (in place) and shape changes (re-selected by sample key).
ppage = window.parameters_page
ptable, pmodel = ppage._table, ppage._model
key = ("demo-ias", 1, 2)
assert ppage.select_sample(key) and ppage.current_key() == key
assert "Engineering    : 0 kt" in ppage._trace.toPlainText(), ppage._trace.toPlainText()
widths = [ptable.columnWidth(c) for c in range(pmodel.columnCount())]
row_before = ptable.currentIndex().row()
ctx.frame_service.set_word(2, 7, 400)               # same rows: refreshed in place
app.processEvents()
assert ppage.current_key() == key and ptable.currentIndex().row() == row_before
assert "Engineering    : 100 kt" in ppage._trace.toPlainText(), ppage._trace.toPlainText()
assert [ptable.columnWidth(c) for c in range(pmodel.columnCount())] == widths
ctx.dataframe_service.remove_parameter("demo-roll")  # rows shift: the selection follows the sample
app.processEvents()
assert ppage.current_key() == key and ptable.currentIndex().row() == row_before - 4
assert "Engineering    : 100 kt" in ppage._trace.toPlainText()
assert [ptable.columnWidth(c) for c in range(pmodel.columnCount())] == widths

# Show in Frame View: the sample's words stacked one row per segment above the
# inspector, its cells outlined in the grid, the first one selected.
bits = fv.parameter_bits
assert not bits._table.isVisible() and not bits._clear.isVisible()
assert ppage.select_sample(("demo-altitude", 1, 1)) and ppage._show_button.isEnabled()
assert ppage.request_frame_view()
app.processEvents()
assert window.tabs.currentWidget() is fv
assert bits.segments() == [(1, 1, 154, 9, 1), (2, 1, 153, 12, 1)], bits.segments()
assert bits._table.isVisible() and bits._table.rowCount() == 2
assert fv.current_cell() == (1, 154) and fv.linked_cells() == {(1, 154), (1, 153)}
assert "PRESS ALT" in bits._summary.text() and "Assembled" in bits._assembled.text()
ctx.frame_service.set_word(1, 153, 0xA5A)          # the bits follow the frame
app.processEvents()
assert bits.bit_text(1) == "101001011010", bits.bit_text(1)
assert bits.highlighted_bits(1) == set(range(1, 13)) and bits.highlighted_bits(0) == set(range(1, 10))
value_cell = bits._table.item(1, bits._table.columnCount() - 1)
assert value_cell.text() == "2650" and "101001011010" in value_cell.toolTip()
assert "2650" in bits._assembled.text()
bits.activate_row(1)                                # a segment row selects its word in the grid
assert fv.current_cell() == (1, 153)
assert "Word Address   : 153" in inspector._text.toPlainText()
bits.show_sample(None)
app.processEvents()
assert not bits._table.isVisible() and fv.linked_cells() == frozenset()

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
    # Ctrl+A (or a Shift+click range) also selects rows the filter hides;
    # the actions must only touch the visible rows.
    dlg._table.selectAll()
    app.processEvents()
    assert dlg.selected_indexes() == [11], dlg.selected_indexes()
    assert not any(dlg._table.isRowHidden(i.row()) for i in dlg._table.selectionModel().selectedRows())
    approved_before = session.state_counts()["APPROVED"]
    dlg._toggle_exclude_selected()                       # excludes SPARE 15 only
    assert session.items[11].excluded and session.state_counts()["APPROVED"] == approved_before
    assert session.state_counts()["EXCLUDED"] == 1
    session.include(11)
    dlg._rebuild()
    dlg.select_visible()                                 # bulk approve of the filtered rows
    assert dlg._approve_button.text() == "Approve Selected" and dlg._exclude_button.text() == "Exclude"
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

# Settings → Theme switches the whole application palette (spec: user request);
# persist=False keeps the test from writing the user's settings store.
from PySide6.QtGui import QPalette
menus = [a.text() for a in window.menuBar().actions()]
assert menus == ["&File", "&Stream", "&Settings", "&Help"], menus
startup_window = app.palette().color(QPalette.ColorRole.Window)
window.set_theme("dark", persist=False)
app.processEvents()
assert app.palette().color(QPalette.ColorRole.Window).lightness() < 90
assert app.palette().color(QPalette.ColorRole.Text).lightness() > 180
assert window.theme == "dark" and window._theme_actions["dark"].isChecked()
assert not window._theme_actions["light"].isChecked()
window.set_theme("light", persist=False)
app.processEvents()
assert app.palette().color(QPalette.ColorRole.Window).lightness() > 200
from arinc717_reader.ui.theme import THEMES, theme_palette
assert set(window._theme_actions) == set(THEMES) and len(THEMES) >= 10
window.set_theme("nord", persist=False)
app.processEvents()
assert app.palette().color(QPalette.ColorRole.Window) == theme_palette("nord").color(QPalette.ColorRole.Window)
assert window._theme_actions["nord"].isChecked() and not window._theme_actions["dark"].isChecked()
# The Help guide re-renders its links in the new palette's link colour.
nord_link = theme_palette("nord").color(QPalette.ColorRole.Link).name()
assert nord_link in window.help_page._browser.document().defaultStyleSheet(), nord_link
window.set_theme("system", persist=False)
app.processEvents()
assert app.palette().color(QPalette.ColorRole.Window) == startup_window
assert window._theme_actions["system"].isChecked()

# Help tab screenshots: listed in the guide and loadable through the browser's search paths.
from PySide6.QtCore import QUrl
from PySide6.QtGui import QTextDocument
from arinc717_reader.ui.help.help_content import FIGURES, available_figures
help_page = window.help_page
if help_page.images_dir.is_dir():
    html = help_page._browser.document().toHtml()
    shown = [f for anchor in FIGURES for f in available_figures(anchor)]
    assert len(shown) >= 8, shown
    for figure in shown:
        assert figure.file in html, figure.file
        resource = help_page._browser.loadResource(QTextDocument.ResourceType.ImageResource, QUrl(figure.file))
        assert resource is not None and not (hasattr(resource, "isNull") and resource.isNull()), figure.file
    print("HELP_FIGURES_OK")

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
    if (Path(__file__).resolve().parents[1] / "img").is_dir():
        assert "HELP_FIGURES_OK" in result.stdout
