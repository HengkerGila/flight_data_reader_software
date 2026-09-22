import pytest

from arinc717_reader.decoder.encoding.signed import (
    SignedRangeError,
    to_twos_complement,
    twos_complement,
)


def test_spec_ground_test_case():
    # Design spec §14 / §53.2: 1101010101 (10 bits) -> -171
    assert twos_complement(0b1101010101, 10) == -171


def test_positive_value_passthrough():
    assert twos_complement(0b0101010101, 10) == 341
    assert twos_complement(0, 10) == 0


def test_extremes():
    assert twos_complement(0b1000000000, 10) == -512
    assert twos_complement(0b0111111111, 10) == 511


def test_encode_spec_closed_loop_case():
    # Design spec §26: -114 encodes as 1110001110
    assert to_twos_complement(-114, 10) == 0b1110001110


def test_encode_decode_roundtrip():
    for value in (-512, -171, -1, 0, 1, 341, 511):
        assert twos_complement(to_twos_complement(value, 10), 10) == value


def test_encode_out_of_range():
    with pytest.raises(SignedRangeError):
        to_twos_complement(512, 10)
    with pytest.raises(SignedRangeError):
        to_twos_complement(-513, 10)


def test_decode_value_wider_than_width():
    with pytest.raises(SignedRangeError):
        twos_complement(1024, 10)
