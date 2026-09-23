"""Session recording and replay (spec v2 §26R, §53.10)."""

from __future__ import annotations

import time

from arinc717_reader.app import build_context
from arinc717_reader.demo import build_demo_dataframe
from arinc717_reader.encoder.frame_builder import build_scenario_frame
from arinc717_reader.recording.session import SessionHeader, SessionReader, SessionWriter, SubframeRecord
from arinc717_reader.sources.replay_source import RecordedSessionSource
from arinc717_reader.sources.serial.subframe_assembler import SubframeAssembler
from arinc717_reader.state.stream_store import CONN_DISCONNECTED


def _write_session(path, frames, t0=1000.0):
    header = SessionHeader(created=t0, dataframe_name="DEMO_256WPS", wps=256, sync_words=[583, 1464, 2631, 3512])
    writer = SessionWriter(path, header)
    t = t0
    for frame in frames:
        for sf in range(1, 5):
            t += 1.0
            writer.write_subframe(SubframeRecord(t, frame.frame_index, sf, "RECEIVED", frame.subframes[sf - 1]))
    writer.close()
    return path


def test_session_file_round_trip(tmp_path):
    dataframe = build_demo_dataframe()
    frame, _ = build_scenario_frame(dataframe, {"demo-ias": 150.0}, frame_index=7)
    path = _write_session(tmp_path / "s.a717session", [frame])
    reader = SessionReader(path)
    assert reader.header.wps == 256 and reader.header.dataframe_name == "DEMO_256WPS"
    records = list(reader.subframes())
    assert [r.sf for r in records] == [1, 2, 3, 4]
    assert records[2].words == frame.subframes[2]
    assert reader.summary()["subframes"] == 4


def test_truncated_session_replays_complete_lines(tmp_path):
    dataframe = build_demo_dataframe()
    frame, _ = build_scenario_frame(dataframe, {"demo-ias": 150.0})
    path = _write_session(tmp_path / "s.a717session", [frame])
    lines = path.read_text(encoding="utf-8").splitlines()
    # Drop the end record and cut the last subframe line in half.
    path.write_text("\n".join(lines[:-2] + [lines[-2][:50]]), encoding="utf-8")
    reader = SessionReader(path)
    assert len(list(reader.subframes())) == 3
    assert reader.summary()["subframes"] == 3


def test_record_then_replay_reproduces_samples(tmp_path):
    ctx = build_context()
    dataframe = build_demo_dataframe()
    ctx.dataframe_service.set_dataframe(dataframe)

    # Live: pretend a source delivered two frames; record through the service.
    class FakeSource:
        wps = 256
        events = None

    ctx.serial_service.source = FakeSource()
    recorder = ctx.recording_service.start_recording(tmp_path / "live")
    asm = SubframeAssembler(256)
    live_samples = []
    t = 2000.0
    for index, ias in enumerate((100.0, 140.0)):
        frame, _ = build_scenario_frame(dataframe, {"demo-ias": ias, "demo-pitch": -5.0}, frame_index=index)
        for sf in range(1, 5):
            t += 1.0
            for arrival in asm.take(sf, frame.subframes[sf - 1], t):
                ctx.recording_service._on_arrival(arrival)
                live_samples.extend(ctx.streaming_service.on_arrival(arrival))
    path = ctx.recording_service.stop_recording()
    assert path.suffix == ".a717session"
    assert recorder.subframes == 8 and recorder.samples == len(live_samples)
    ctx.serial_service.source = None

    # Replay through the normal pipeline into a fresh context.
    replay_ctx = build_context()
    replay_ctx.dataframe_service.set_dataframe(dataframe)
    replay_ctx.timeseries_store.clear()
    source = replay_ctx.recording_service.start_replay(path, realtime=False)
    assert isinstance(source, RecordedSessionSource)
    deadline = time.monotonic() + 10.0
    while replay_ctx.stream_store.connection != CONN_DISCONNECTED:
        replay_ctx.pump()
        assert time.monotonic() < deadline, "replay did not finish"
        time.sleep(0.005)
    replayed = replay_ctx.timeseries_store.window("demo-ias")
    live = ctx.timeseries_store.window("demo-ias")
    assert replayed == live  # identical timestamps and values
    assert replay_ctx.stream_store.connection == CONN_DISCONNECTED  # finished → detached
    # The replayed sample records in the file agree with what was decoded.
    reader = SessionReader(path)
    file_samples = [s for s in reader.samples() if s.parameter_id == "demo-ias"]
    assert [s.engineering_value for s in file_samples] == live[1]


def test_replay_rejects_wps_mismatch(tmp_path):
    ctx = build_context()
    dataframe = build_demo_dataframe()
    ctx.dataframe_service.set_dataframe(dataframe)
    header = SessionHeader(wps=128, sync_words=[1, 2, 3, 4])
    SessionWriter(tmp_path / "x.a717session", header).close()
    try:
        ctx.recording_service.start_replay(tmp_path / "x.a717session", realtime=False)
    except Exception as exc:
        assert "REPLAY_ERROR" in str(exc)
    else:
        raise AssertionError("expected REPLAY_ERROR")
