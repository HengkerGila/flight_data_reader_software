"""Engineering signal generators (spec v2 §26H) and per-subframe encoding."""

from __future__ import annotations

import pytest

from arinc717_reader.decoder.parameter_decoder import ParameterDecoder
from arinc717_reader.demo import build_demo_dataframe
from arinc717_reader.encoder.frame_builder import build_blank_frame
from arinc717_reader.encoder.parameter_encoder import ParameterEncoder, is_encodable
from arinc717_reader.streaming.signal_generator import (
    MODE_FIXED,
    MODE_RAMP,
    MODE_RANDOM_WALK,
    MODE_SCRIPTED,
    MODE_SINE,
    MODE_STEP,
    MODE_UNIFORM,
    ScenarioSignals,
    SignalConfig,
    SignalGenerator,
    default_config,
)


def _pitch():
    return build_demo_dataframe().get_parameter("demo-pitch")


def test_default_config_uses_declared_range():
    cfg = default_config(_pitch())
    assert (cfg.low, cfg.high) == (-90.0, 90.0)
    assert cfg.mode == MODE_RANDOM_WALK


def test_modes_stay_within_bounds():
    for mode in (MODE_FIXED, MODE_UNIFORM, MODE_RANDOM_WALK, MODE_SINE, MODE_RAMP, MODE_STEP):
        gen = SignalGenerator(_pitch(), SignalConfig(mode=mode, low=-10, high=10, value=3, period=4), seed=1)
        values = [gen.value_at(t) for t in range(0, 40)]
        assert all(-10 <= v <= 10 for v in values), mode
    gen = SignalGenerator(_pitch(), SignalConfig(mode=MODE_FIXED, low=-10, high=10, value=3))
    assert gen.value_at(5) == 3


def test_sine_ramp_step_shapes():
    sine = SignalGenerator(_pitch(), SignalConfig(mode=MODE_SINE, low=-10, high=10, period=4))
    assert abs(sine.value_at(0)) < 1e-9 and abs(sine.value_at(1) - 10) < 1e-9
    ramp = SignalGenerator(_pitch(), SignalConfig(mode=MODE_RAMP, low=0, high=10, period=10))
    assert ramp.value_at(2.5) == 2.5 and ramp.value_at(12.5) == 2.5
    step = SignalGenerator(_pitch(), SignalConfig(mode=MODE_STEP, low=0, high=10, period=5))
    assert step.value_at(1) == 0 and step.value_at(6) == 10 and step.value_at(11) == 0


def test_scripted_interpolates():
    gen = SignalGenerator(
        _pitch(), SignalConfig(mode=MODE_SCRIPTED, low=-90, high=90, script=[(0, 0), (10, 20), (20, 0)])
    )
    assert gen.value_at(5) == 10 and gen.value_at(15) == 10 and gen.value_at(50) == 0


def test_discrete_generator_returns_states():
    gear = build_demo_dataframe().get_parameter("demo-gear")
    gen = SignalGenerator(gear, default_config(gear))
    assert gen.value_at(0) == "UP" and gen.value_at(5) == "DOWN"


def test_per_subframe_encoding_gives_each_subframe_its_own_value():
    dataframe = build_demo_dataframe()
    ias = dataframe.get_parameter("demo-ias")
    frame = build_blank_frame(256, 0, dataframe.metadata.sync_words)
    encoder = ParameterEncoder()
    for sf, value in zip((1, 2, 3, 4), (100.0, 110.0, 120.0, 130.0)):
        encoder.encode_into_frame(frame, ias, value, only_subframes={sf})
    decoded = {v.subframe: v.engineering_value for v in ParameterDecoder().decode_parameter(frame, ias)}
    assert decoded == {1: 100.0, 2: 110.0, 3: 120.0, 4: 130.0}


def test_scenario_signals_cover_encodable_parameters():
    dataframe = build_demo_dataframe()
    signals = ScenarioSignals(dataframe, seed=3)
    assert "demo-spare" not in signals.generators  # unknown type is not encodable
    values = signals.values_at(0.0)
    assert set(values) == set(signals.generators)
    signals.configure("demo-ias", SignalConfig(mode=MODE_FIXED, low=0, high=450, value=222))
    assert signals.values_at(1.0)["demo-ias"] == 222


def test_script_text_round_trip():
    from arinc717_reader.streaming.signal_generator import format_script, parse_script

    assert parse_script("0:0, 10:20, 20:0") == [(0.0, 0.0), (10.0, 20.0), (20.0, 0.0)]
    assert parse_script(" 20:0 ;0:0\n10:20 ") == [(0.0, 0.0), (10.0, 20.0), (20.0, 0.0)]
    assert parse_script("") == []
    assert format_script([(20, 0), (0, 0), (10, 20.5)]) == "0:0, 10:20.5, 20:0"
    assert parse_script(format_script([(0, -5), (2.5, 7)])) == [(0.0, -5.0), (2.5, 7.0)]
    import pytest

    with pytest.raises(ValueError):
        parse_script("0:0, 10")
    with pytest.raises(ValueError):
        parse_script("a:b")


def test_default_range_is_clipped_to_what_the_field_can_encode():
    """Declared limits wider than the mapping (demo FLAP POS: −2..45 deg in a
    12-bit unsigned field holding −1.6..23.8) must not produce unencodable values."""
    from arinc717_reader.encoder.frame_builder import build_blank_frame
    from arinc717_reader.streaming.signal_generator import representable_range

    dataframe = build_demo_dataframe()
    flap = dataframe.get_parameter("demo-flap")
    lo, hi = representable_range(flap)
    assert lo == pytest.approx(-1.6, abs=0.01) and hi == pytest.approx(23.79, abs=0.01)
    cfg = default_config(flap)
    assert cfg.low == pytest.approx(-1.6, abs=0.01) and cfg.high == pytest.approx(23.79, abs=0.01)
    # Pitch: declared ±90 fits its signed 10-bit field (±90.1) and is kept.
    assert (default_config(_pitch()).low, default_config(_pitch()).high) == (-90.0, 90.0)

    encoder = ParameterEncoder()
    for parameter in dataframe.parameters:
        if not is_encodable(parameter) or parameter.parameter_type == "discrete":
            continue
        cfg = default_config(parameter)
        frame = build_blank_frame(256, 0, dataframe.metadata.sync_words)
        for value in (cfg.low, cfg.high, (cfg.low + cfg.high) / 2):
            encoder.encode_into_frame(frame, parameter, value)  # must not raise
    # Whole-dataframe generators over many simulated seconds: nothing fails to encode.
    signals = ScenarioSignals(dataframe, seed=11)
    frame = build_blank_frame(256, 0, dataframe.metadata.sync_words)
    for t in range(0, 400, 7):
        for pid, value in signals.values_at(float(t)).items():
            encoder.encode_into_frame(frame, dataframe.get_parameter(pid), value)
