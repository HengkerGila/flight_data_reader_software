import pytest

from arinc717_reader.decoder.conversion import (
    ConversionError,
    apply_linear,
    invert_linear,
)


def test_pitch_conversion():
    # Design spec §53.3
    assert apply_linear(-171, 0.176, 0.0) == pytest.approx(-30.096)


def test_flap_offset_conversion():
    # Design spec §53.4
    assert apply_linear(2738, 0.0062, -1.6) == pytest.approx(15.3756)


def test_airspeed_conversion():
    assert apply_linear(601, 0.25, 0.0) == pytest.approx(150.25)


def test_negative_resolution():
    # Aileron-style: resolution -0.0153, offset +31.2 (spec §16)
    assert apply_linear(2039, -0.0153, 31.2) == pytest.approx(0.0033, abs=1e-9)


def test_invert_roundtrip():
    for decoded, resolution, offset in [
        (-171, 0.176, 0.0),
        (2738, 0.0062, -1.6),
        (2039, -0.0153, 31.2),
    ]:
        engineering = apply_linear(decoded, resolution, offset)
        assert invert_linear(engineering, resolution, offset) == pytest.approx(decoded)


def test_invert_zero_resolution_rejected():
    with pytest.raises(ConversionError):
        invert_linear(1.0, 0.0, 0.0)
