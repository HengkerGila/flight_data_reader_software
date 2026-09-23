"""Virtual STM32 device end to end: connect, stream, decode, faults, reconnect (spec v2 §53.7)."""

from __future__ import annotations

import time

import pytest

from arinc717_reader.app import build_context
from arinc717_reader.demo import build_demo_dataframe
from arinc717_reader.services import ServiceError
from arinc717_reader.sources.serial import sim_a717_protocol as proto
from arinc717_reader.sources.serial.virtual_device import VirtualSimDevice
from arinc717_reader.state.stream_store import CONN_CONNECTED, CONN_STREAMING

SPEED = 200.0  # virtual time runs 200x faster than wall time


def pump_for(ctx, seconds: float, until=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ctx.pump()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else True


@pytest.fixture
def ctx():
    context = build_context()
    context.dataframe_service.set_dataframe(build_demo_dataframe())
    yield context
    context.shutdown()


def test_device_command_channel():
    device = VirtualSimDevice(wps=256)
    assert device.handle_command("PING") == "+OK PONG"
    assert device.handle_command("SET_WPS 128").startswith("+OK WPS 128")
    assert device.wps == 128
    assert device.handle_command("SET_MODE BOGUS").startswith("+ERR")
    assert device.handle_command("FAULT DROP_WORD 3") == "+OK FAULT DROP_WORD 3"
    assert device.faults["DROP_WORD"] == 3
    assert device.handle_command("LOAD_SF 1 " + "000" * 128) == "+OK LOADED 1"
    assert device.handle_command("LOAD_SF 1 000").startswith("+ERR")


def test_stepping_engine_emits_at_word_rate():
    device = VirtualSimDevice(wps=256)
    host = device.connect()
    device.handle_command("START")
    device.step(0.0)
    device.step(1.0)  # exactly one subframe of words is due
    data = host.read(65536, timeout=0.0)
    assert len(data) == 2 * 256 + 2  # 256 words + the word due at t=1.0
    assert data[:2] == proto.encode_word(583)


def test_connect_stream_decode_and_stop(ctx):
    info = ctx.serial_service.connect("VIRTUAL", virtual_speed=SPEED)
    assert info.startswith("SIM-A717 v1")
    assert ctx.stream_store.connection == CONN_CONNECTED
    ctx.serial_service.set_signal_source("engineering")
    ctx.serial_service.start_stream()
    assert ctx.stream_store.connection == CONN_STREAMING
    assert pump_for(ctx, 5.0, until=lambda: ctx.stream_store.diagnostics.frames_received >= 3)
    diag = ctx.stream_store.diagnostics
    assert diag.sync_state == "LOCKED"
    assert diag.sync_losses == 0 and diag.invalid_words == 0
    assert ctx.frame_store.live and ctx.frame_store.subframe_states is not None
    ias = ctx.timeseries_store.stats("demo-ias")
    assert ias.count >= 8 and 0 <= ias.minimum and ias.maximum <= 450
    assert ctx.engineering_store.values  # live values published per subframe
    ctx.serial_service.stop_stream()
    ctx.pump()
    assert ctx.stream_store.connection == CONN_CONNECTED
    assert not ctx.frame_store.live  # last frame became a static, editable frame
    ctx.serial_service.disconnect()


def test_faults_are_detected_and_reported(ctx):
    ctx.serial_service.connect("VIRTUAL", virtual_speed=SPEED)
    ctx.serial_service.start_stream()
    assert pump_for(ctx, 5.0, until=lambda: ctx.stream_store.diagnostics.frames_received >= 1)
    ctx.serial_service.inject_fault(proto.FAULT_DROP_SUBFRAME, 1)
    # Wait for the event itself: the diagnostics counter is bumped by the
    # reader thread a moment before the event is queued, so a counter-based
    # wait could observe the loss before the event has been pumped.
    assert pump_for(
        ctx, 5.0, until=lambda: any(e.kind == "STREAM_SYNC_LOST" for e in ctx.stream_store.events)
    )
    assert ctx.stream_store.diagnostics.sync_losses >= 1
    kinds = {e.kind for e in ctx.stream_store.events}
    assert "FAULT_INJECTED" in kinds
    assert pump_for(ctx, 5.0, until=lambda: ctx.stream_store.diagnostics.sync_state == "LOCKED")
    with pytest.raises(ServiceError):
        ctx.serial_service.inject_fault("NOT_A_FAULT")


def test_disconnect_fault_triggers_reconnect(ctx):
    ctx.serial_service.connect("VIRTUAL", virtual_speed=SPEED)
    ctx.serial_service.start_stream()
    assert pump_for(ctx, 5.0, until=lambda: ctx.stream_store.diagnostics.frames_received >= 1)
    ctx.serial_service.inject_fault(proto.FAULT_DISCONNECT_RECONNECT, 1)
    assert pump_for(ctx, 5.0, until=lambda: ctx.stream_store.connection == "RECONNECTING")
    assert pump_for(ctx, 8.0, until=lambda: ctx.stream_store.connection == CONN_STREAMING)
    kinds = [e.kind for e in ctx.stream_store.events]
    assert "SERIAL_DISCONNECTED" in kinds and "RECONNECTED" in kinds
    frames_before = ctx.stream_store.diagnostics.frames_received
    assert pump_for(ctx, 5.0, until=lambda: ctx.stream_store.diagnostics.frames_received > frames_before)


def test_framed_protocol_mode(ctx):
    ctx.serial_service.connect("VIRTUAL", protocol_mode=proto.MODE_FRAMED, virtual_speed=SPEED)
    ctx.serial_service.start_stream()
    assert pump_for(ctx, 5.0, until=lambda: ctx.stream_store.diagnostics.frames_received >= 2)
    ctx.serial_service.inject_fault(proto.FAULT_BAD_CRC, 1)
    assert pump_for(ctx, 5.0, until=lambda: ctx.stream_store.diagnostics.bad_packets >= 1)
    # The slot is reported missing once the following packet arrives.
    assert pump_for(ctx, 5.0, until=lambda: ctx.stream_store.diagnostics.dropped_subframes >= 1)


def test_connect_requires_dataframe():
    context = build_context()
    with pytest.raises(ServiceError) as info:
        context.serial_service.connect("VIRTUAL")
    assert info.value.state == "MISSING_DATAFRAME"
