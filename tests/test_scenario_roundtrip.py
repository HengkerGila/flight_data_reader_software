"""Closed-loop validation: encode a scenario, decode it back (spec §25, §26)."""

import pytest

from arinc717_reader.decoder.parameter_decoder import ParameterDecoder
from arinc717_reader.encoder.frame_builder import build_scenario_frame
from arinc717_reader.encoder.parameter_encoder import (
    EncodeError,
    ParameterEncoder,
    is_encodable,
    quantization_tolerance,
)


def decoded_sample(values, parameter_id, subframe=1, occurrence=1):
    return next(
        v
        for v in values
        if v.parameter_id == parameter_id
        and v.subframe == subframe
        and v.occurrence_index == occurrence
    )


def test_spec_pitch_closed_loop(demo_dataframe):
    # Spec §26: -20 deg -> raw -114 -> bits 1110001110 -> decode -20.064 deg
    frame, raw = build_scenario_frame(demo_dataframe, {"demo-pitch": -20.0})
    assert raw["demo-pitch"] == 0b1110001110
    values = ParameterDecoder().decode_frame(frame, demo_dataframe)
    pitch = decoded_sample(values, "demo-pitch")
    assert pitch.decoded_decimal == -114
    assert pitch.engineering_value == pytest.approx(-20.064)
    tolerance = quantization_tolerance(demo_dataframe.get_parameter("demo-pitch"))
    assert abs(pitch.engineering_value - (-20.0)) <= tolerance


def test_scenario_frame_has_sync_words(demo_dataframe):
    frame, _ = build_scenario_frame(demo_dataframe, {"demo-pitch": 0.0})
    assert [frame.word(sf, 1) for sf in (1, 2, 3, 4)] == [583, 1464, 2631, 3512]


def test_full_scenario_roundtrip_within_quantization(demo_dataframe):
    scenario = {
        "demo-pitch": -20.0,
        "demo-roll": 5.5,
        "demo-heading": 180.0,
        "demo-ias": 150.25,
        "demo-altitude": 10000.0,
        "demo-flap": 15.3,
        "demo-aoa": 4.2,
        "demo-aileron": 0.0,
        "demo-gear": "DOWN",
        "demo-gpws": "NORMAL",
        "demo-course": 245.0,
    }
    frame, _ = build_scenario_frame(demo_dataframe, scenario)
    values = ParameterDecoder().decode_frame(frame, demo_dataframe)
    for parameter_id, requested in scenario.items():
        parameter = demo_dataframe.get_parameter(parameter_id)
        subframe = parameter.occurrences[0].segments[0].subframes[0]
        decoded = decoded_sample(values, parameter_id, subframe=subframe)
        if isinstance(requested, str):
            assert decoded.engineering_value == requested
        else:
            tolerance = quantization_tolerance(parameter)
            assert decoded.engineering_value == pytest.approx(
                requested, abs=tolerance
            ), parameter_id
        assert decoded.status == "VALID", (parameter_id, decoded.trace.message)


def test_exact_values_survive_exactly(demo_dataframe):
    frame, _ = build_scenario_frame(demo_dataframe, {"demo-ias": 150.25})
    values = ParameterDecoder().decode_frame(frame, demo_dataframe)
    assert decoded_sample(values, "demo-ias").engineering_value == pytest.approx(150.25)
    assert decoded_sample(values, "demo-ias").decoded_decimal == 601


def test_multi_segment_encode_splits_msb_first(demo_dataframe):
    frame, raw = build_scenario_frame(demo_dataframe, {"demo-altitude": 10000.0})
    assert raw["demo-altitude"] == 40000
    assert frame.word(1, 154) == 9
    assert frame.word(1, 153) == 3136


def test_every_occurrence_and_subframe_written(demo_dataframe):
    frame, _ = build_scenario_frame(demo_dataframe, {"demo-pitch": -20.0})
    expected = 0b1110001110 << 2  # bits 12..3
    for subframe in (1, 2, 3, 4):
        assert frame.word(subframe, 4) == expected
        assert frame.word(subframe, 132) == expected


def test_encode_only_touches_mapped_bits(demo_dataframe):
    frame, _ = build_scenario_frame(
        demo_dataframe, {"demo-gear": "DOWN", "demo-gpws": "WARNING"}
    )
    # gear bit 1 set, gpws bit 2 clear (WARNING is the zero/false label)
    assert frame.word(1, 13) == 0b01


def test_encode_out_of_capacity_rejected(demo_dataframe):
    with pytest.raises(EncodeError):
        build_scenario_frame(demo_dataframe, {"demo-pitch": 200.0})


def test_encode_unknown_parameter_rejected(demo_dataframe):
    with pytest.raises(EncodeError):
        build_scenario_frame(demo_dataframe, {"nope": 1.0})


def test_encode_bad_discrete_state_rejected(demo_dataframe):
    with pytest.raises(EncodeError):
        build_scenario_frame(demo_dataframe, {"demo-gear": "SIDEWAYS"})


def test_encodability(demo_dataframe):
    encodable = {p.id for p in demo_dataframe.parameters if is_encodable(p)}
    assert "demo-pitch" in encodable
    assert "demo-gear" in encodable
    assert "demo-course" in encodable
    assert "demo-spare" not in encodable  # unknown type


def test_encoder_updates_existing_frame_in_place(demo_dataframe, demo_frame):
    encoder = ParameterEncoder()
    parameter = demo_dataframe.get_parameter("demo-ias")
    pattern = encoder.encode_into_frame(demo_frame, parameter, 100.0)
    assert pattern == 400
    assert demo_frame.word(1, 7) == 400
    assert demo_frame.word(1, 1) == 583  # sync word untouched
