"""Live subframe decoding → ParameterSample (spec v2 §26G, §26K, §26M, §53.8)."""

from __future__ import annotations

from arinc717_reader.app import build_context
from arinc717_reader.decoder.parameter_decoder import ParameterDecoder
from arinc717_reader.demo import build_demo_dataframe
from arinc717_reader.encoder.frame_builder import build_scenario_frame
from arinc717_reader.sources.serial.subframe_assembler import SubframeAssembler
from arinc717_reader.streaming.parameter_sample import sample_time


def _stream_frame(ctx, frame, t0: float):
    """Push a complete frame through the assembler and the streaming service."""
    asm = SubframeAssembler(frame.wps, first_frame_index=frame.frame_index)
    published = []
    for sf in range(1, 5):
        for arrival in asm.take(sf, frame.subframes[sf - 1], t0 + sf):
            published.extend(ctx.streaming_service.on_arrival(arrival))
    return published


def test_decode_subframe_matches_whole_frame_decode():
    dataframe = build_demo_dataframe()
    frame, _ = build_scenario_frame(dataframe, {"demo-pitch": -20.0, "demo-ias": 150.0})
    decoder = ParameterDecoder()
    whole = decoder.decode_frame(frame, dataframe)
    per_sf = [v for sf in (1, 2, 3, 4) for v in decoder.decode_subframe(frame, dataframe, sf)]
    key = lambda v: (v.parameter_id, v.occurrence_index, v.subframe or 0)
    assert sorted(map(key, whole)) == sorted(map(key, per_sf))
    assert {key(v): (v.status, v.engineering_value) for v in whole} == {
        key(v): (v.status, v.engineering_value) for v in per_sf
    }


def test_samples_respect_parameter_frequency_and_carry_timestamps():
    ctx = build_context()
    dataframe = build_demo_dataframe()
    ctx.dataframe_service.set_dataframe(dataframe)
    frame, _ = build_scenario_frame(dataframe, {"demo-ias": 150.0, "demo-course": 123, "demo-flap": 10.0})
    samples = _stream_frame(ctx, frame, t0=1000.0)
    by_id = {}
    for s in samples:
        by_id.setdefault(s.parameter_id, []).append(s)
    # IAS is mapped to all four subframes → 4 samples per frame (1 Hz).
    assert [s.subframe for s in by_id["demo-ias"]] == [1, 2, 3, 4]
    # Selected course lives in SF2 only → one sample per frame (0.25 Hz).
    assert [s.subframe for s in by_id["demo-course"]] == [2]
    # Flap in SF2 and SF4 → 0.5 Hz, no fabricated intermediate samples.
    assert [s.subframe for s in by_id["demo-flap"]] == [2, 4]
    # Timestamp: subframe end − 1 s + word offset (word 7 at 256 WPS).
    ias1 = by_id["demo-ias"][0]
    assert ias1.timestamp == sample_time(1001.0, 7, 256)
    assert abs(ias1.timestamp - (1000.0 + 6 / 256)) < 1e-9
    assert ias1.engineering_value == 150.0 and ias1.unit == "kt" and ias1.status == "VALID"
    # The time-series store received every numeric sample.
    assert ctx.timeseries_store.stats("demo-ias").count == 4
    assert ctx.timeseries_store.stats("demo-course").current == 123.0
    # Engineering store shows the latest value per (parameter, occurrence, subframe).
    ias_values = ctx.engineering_store.values_for_parameter("demo-ias")
    assert sorted(v.subframe for v in ias_values) == [1, 2, 3, 4]


def test_invalid_subframe_yields_no_samples():
    ctx = build_context()
    dataframe = build_demo_dataframe()
    ctx.dataframe_service.set_dataframe(dataframe)
    frame, _ = build_scenario_frame(dataframe, {"demo-ias": 150.0})
    asm = SubframeAssembler(256)
    arrival = asm.take(1, frame.subframes[0], 10.0, valid=False)[0]
    assert ctx.streaming_service.on_arrival(arrival) == []
    assert ctx.timeseries_store.keys() == []


def test_discrete_samples_are_plottable_as_0_1():
    ctx = build_context()
    dataframe = build_demo_dataframe()
    ctx.dataframe_service.set_dataframe(dataframe)
    frame, _ = build_scenario_frame(dataframe, {"demo-gear": "DOWN"})
    samples = _stream_frame(ctx, frame, t0=0.0)
    gear = [s for s in samples if s.parameter_id == "demo-gear"]
    assert gear[0].engineering_value == "DOWN"
    assert gear[0].numeric_value == 1.0
    assert ctx.timeseries_store.stats("demo-gear").current == 1.0
