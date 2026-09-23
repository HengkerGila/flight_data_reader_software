"""Offscreen GUI smoke test for the live-stream pages (spec v2 §46A–§46C, §53.7–§53.10).

Drives the Hardware page against the in-process virtual SIM-A717 device,
checks the progressive Frame View, the Graphs page (overlays, pause without
stopping acquisition), fault injection, and record → replay through the
toolbar controls.  Runs in a subprocess like ``test_ui_smoke`` because a
failed Qt platform init aborts the interpreter.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SCRIPT = r"""
import os, sys, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from arinc717_reader.app import build_context
from arinc717_reader.demo import build_demo_dataframe
from arinc717_reader.ui.main_window import MainWindow

OUT = Path(sys.argv[1])
app = QApplication([])
ctx = build_context()
window = MainWindow(ctx)
window.show()
ctx.dataframe_service.set_dataframe(build_demo_dataframe())
app.processEvents()


def run(seconds, until=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else True


hw = window.hardware_page
store = ctx.stream_store
sc = window.stream_controls
tabs = [window.tabs.tabText(i) for i in range(window.tabs.count())]
assert tabs == ["Frame View", "Parameters", "Graphs", "Dataframe", "Hardware", "Import", "Help"], tabs
assert not sc._pause.isEnabled() and sc._start.isEnabled()
# The page reflects the service's signal source (PC engineering signals by default).
assert hw._signal_combo.currentData() == ctx.serial_service.signal_source == "engineering"

hw._port_combo.setCurrentIndex(hw._port_combo.findData("VIRTUAL"))
hw._speed_spin.setValue(50.0)
hw.connect_device()
app.processEvents()
assert store.connection == "CONNECTED", (store.connection, store.last_error)
assert not hw._connect_button.isEnabled() and hw._start_button.isEnabled()
assert "SIM-A717 v1" in hw._info_label.text()

# Scripted signal edited through the table (Script column, time:value pairs).
table = hw._signal_table
row = next(r for r in range(table.rowCount()) if table.item(r, 0).data(Qt.ItemDataRole.UserRole) == "demo-ias")
table.cellWidget(row, 1).setCurrentText("Scripted")
table.item(row, 6).setText("0:100, 40:300, 80:100")
app.processEvents()
cfg = ctx.serial_service.signal_configs()["demo-ias"]
assert cfg.mode == "Scripted" and cfg.script == [(0.0, 100.0), (40.0, 300.0), (80.0, 100.0)]
table.item(row, 6).setText("bogus")
app.processEvents()
assert ctx.serial_service.signal_configs()["demo-ias"].script == cfg.script
assert table.item(row, 6).toolTip()

assert sc.start()  # toolbar: connected → start
assert store.connection == "STREAMING" and sc._pause.isEnabled() and not sc._start.isEnabled()
assert run(15.0, until=lambda: store.diagnostics.frames_received >= 3), store.diagnostics
# Rolling display: after the first frame every column shows a value, pending ones
# keep the previous frame's words; no "----" placeholders remain.
model = window.frame_view_page._model
assert ctx.frame_store.blank_subframes == frozenset()
cells = [model.data(model.index(0, c), Qt.ItemDataRole.DisplayRole) for c in range(4)]
assert "----" not in cells and cells[0] == "247", cells  # SF1 sync word 583 = 0x247
assert model.data(model.index(0, 1), Qt.ItemDataRole.DisplayRole) == "5B8"
assert store.diagnostics.sync_state == "LOCKED"
assert hw._sync_label.text().startswith("LOCKED") and hw._word_label.text().endswith("/ 256")
assert ctx.frame_store.live
header = model.headerData(0, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
assert header.startswith("SF1 ·"), header
assert "(live)" in window._source_label.text()
assert window._status_stream.text() == "Stream: STREAMING VIRTUAL"
# No signal could fail to encode with the default ranges.
assert not any(e.kind == "ENCODE_ERROR" for e in store.events), [e.message for e in store.events]

# Graphs page: selection, same-unit overlays, stats, pause keeps acquiring.
gp = window.graphs_page
gp.select_parameter("demo-pitch")
run(1.0)
gp._refresh(force=True)
assert gp._parameter_combo.currentText() == "PITCH ATT #1 [deg]"
assert gp._plot._series and gp._plot._series[0].times
assert gp._status.text() == "VALID" and gp._current.text().endswith("deg")
overlays = [gp._overlay_list.item(i).text() for i in range(gp._overlay_list.count())]
assert "ROLL ATT" in overlays and "IAS" not in overlays, overlays
gp._overlay_list.item(0).setCheckState(Qt.CheckState.Checked)
run(0.3)
gp._refresh(force=True)
assert len(gp._plot._series) == 2
gp._pause_button.setChecked(True)
frozen = gp._plot._t_end
before = ctx.timeseries_store.stats("demo-pitch").count
run(0.8)
after = ctx.timeseries_store.stats("demo-pitch").count
assert gp._plot._t_end == frozen and after > before, (before, after)
gp._pause_button.setChecked(False)

# Fault injection through the page: a dropped subframe is a sync loss, then re-lock.
hw._fault_combo.setCurrentIndex(hw._fault_combo.findData("DROP_SUBFRAME"))
hw.inject_fault()
assert run(15.0, until=lambda: any(e.kind == "STREAM_SYNC_LOST" for e in store.events))
assert store.diagnostics.sync_losses >= 1
assert run(15.0, until=lambda: any(e.kind == "STREAM_SYNC_LOCKED" for e in list(store.events)[-3:]))
assert store.diagnostics.sync_state == "LOCKED"
kinds = [hw._events.item(r, 1).text() for r in range(hw._events.rowCount())]
assert "FAULT_INJECTED" in kinds and "STREAM_SYNC_LOST" in kinds, kinds

# Toolbar pause: the stream stops, the frame becomes static and editable; start resumes.
sc.pause()
app.processEvents()
assert store.connection == "CONNECTED" and not ctx.frame_store.live
assert sc._start.isEnabled() and not sc._pause.isEnabled()
assert "PAUSED" in sc._state.text(), sc._state.text()
ctx.frame_service.set_word(1, 4, 3412)  # editing works while paused
assert sc.start()
assert store.connection == "STREAMING"
frames0 = store.diagnostics.frames_received
assert run(15.0, until=lambda: store.diagnostics.frames_received >= frames0 + 2)
assert "LIVE VIRTUAL" in sc._state.text(), sc._state.text()

# Record through the File menu actions, then stop and disconnect.
session = OUT / "smoke.a717session"
assert window._record_action.isEnabled()
window.record_session(str(session))
app.processEvents()
assert ctx.recording_service.recording and window._status_recording.text() == "● REC"
assert not window._record_action.isEnabled() and window._stop_record_action.isEnabled()
frames0 = store.diagnostics.frames_received
assert run(15.0, until=lambda: store.diagnostics.frames_received >= frames0 + 3)
window.stop_recording()
app.processEvents()
assert session.exists() and not ctx.recording_service.recording
hw.stop_stream()
app.processEvents()
assert store.connection == "CONNECTED" and not ctx.frame_store.live
hw.disconnect_device()
app.processEvents()
assert store.connection == "DISCONNECTED" and hw._connect_button.isEnabled()

# Toolbar start from DISCONNECTED connects with the Hardware page settings and streams.
assert sc.start()
assert store.connection == "STREAMING"
hw.stop_stream()
hw.disconnect_device()
app.processEvents()

# Replay through the File menu (slowed down so the state is observable).
ctx.timeseries_store.clear()
window.set_replay_speed(0.25)
assert window.replay_speed == 0.25
window.replay_session(str(session))
app.processEvents()
assert ctx.recording_service.replaying, (store.connection, [e.kind for e in store.events][-4:])
assert window._status_recording.text() == "▶ REPLAY" and "REPLAYING" in window._status_stream.text()
assert window._pause_replay_action.isEnabled() and not window._record_action.isEnabled()
sc.pause()  # toolbar pause pauses the replay
run(0.3)
assert ctx.recording_service.replay.paused and window._pause_replay_action.isChecked()
assert "REPLAY paused" in sc._state.text(), sc._state.text()
assert sc.start()  # and start resumes it
assert not ctx.recording_service.replay.paused
assert run(60.0, until=lambda: store.connection == "DISCONNECTED"), store.connection
assert ctx.timeseries_store.stats("demo-pitch").count > 0
assert [e.kind for e in store.events][-1] == "REPLAY_FINISHED"

# Help tab: every tab and toolbar button is described; F1 opens it.
help_page = window.help_page
text = help_page.plain_text()
for needle in tabs + ["Start stream", "Pause stream", "Record Session", "Replay Session", "Word Inspector", "STM32"]:
    assert needle in text, needle
assert len(help_page.section_anchors) >= 15
window.show_help("hardware")
assert window.tabs.currentWidget() is help_page
assert help_page._sections.currentItem().data(Qt.ItemDataRole.UserRole) == "hardware"
help_page._search.setText("Fault injection")
help_page.find_next()
assert help_page._browser.textCursor().hasSelection()

window.close()
app.processEvents()
print("UI_STREAM_SMOKE_OK")
"""


@pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None, reason="PySide6 not installed"
)
def test_ui_stream_smoke():
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    with tempfile.TemporaryDirectory() as out:
        result = subprocess.run(
            [sys.executable, "-c", SCRIPT, out],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=240,
        )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "UI_STREAM_SMOKE_OK" in result.stdout
