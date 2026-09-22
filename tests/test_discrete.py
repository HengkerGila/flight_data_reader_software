import pytest

from arinc717_reader.decoder.encoding.discrete import (
    DiscreteError,
    decode_discrete,
    encode_discrete,
)


def test_dataframe_defined_states():
    # Design spec §53.5: 0 -> DOWN, 1 -> UP must display the defined state
    assert decode_discrete(1, "UP", "DOWN") == "UP"
    assert decode_discrete(0, "UP", "DOWN") == "DOWN"


def test_active_low_states_come_from_dataframe():
    # Design spec §18: never assume 1 = active; labels may be inverted
    assert decode_discrete(0, "NORMAL", "WARNING") == "WARNING"
    assert decode_discrete(1, "NORMAL", "WARNING") == "NORMAL"


def test_default_labels():
    assert decode_discrete(1) == "TRUE"
    assert decode_discrete(0) == "FALSE"


def test_multibit_nonzero_is_true():
    assert decode_discrete(0b10, "ON", "OFF") == "ON"


def test_encode_state_labels():
    assert encode_discrete("DOWN", "DOWN", "UP") == 1
    assert encode_discrete("UP", "DOWN", "UP") == 0
    assert encode_discrete("up", "DOWN", "UP") == 0  # case-insensitive


def test_encode_bool_and_int():
    assert encode_discrete(True) == 1
    assert encode_discrete(False) == 0
    assert encode_discrete(1) == 1
    assert encode_discrete(0) == 0


def test_encode_unknown_state_rejected():
    with pytest.raises(DiscreteError):
        encode_discrete("SIDEWAYS", "DOWN", "UP")
    with pytest.raises(DiscreteError):
        encode_discrete(2)
