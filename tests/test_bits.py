import pytest

from arinc717_reader.decoder.bit_extractor import (
    BitRangeError,
    bit_width,
    extract_bits,
    normalize_bit_range,
)


def test_extract_spec_example():
    # Design spec §9: word 3412 (0b110101010100), bits 12-3 -> 1101010101
    assert extract_bits(3412, 3, 12) == 0b1101010101 == 853


def test_extract_reversed_order_normalizes():
    # lsb/msb source conventions differ; both orders must give the same field
    assert extract_bits(3412, 12, 3) == 853


def test_extract_single_bit():
    assert extract_bits(0b100000000000, 12, 12) == 1
    assert extract_bits(0b100000000000, 1, 1) == 0


def test_extract_full_word():
    assert extract_bits(4095, 1, 12) == 4095
    assert extract_bits(0, 1, 12) == 0


def test_bit_width():
    assert bit_width(3, 12) == 10
    assert bit_width(12, 3) == 10
    assert bit_width(5, 5) == 1


def test_normalize_returns_low_high():
    assert normalize_bit_range(12, 3) == (3, 12)
    assert normalize_bit_range(3, 12) == (3, 12)


@pytest.mark.parametrize("lsb,msb", [(0, 12), (1, 13), (-1, 5), (13, 13)])
def test_invalid_bit_range_rejected(lsb, msb):
    with pytest.raises(BitRangeError):
        extract_bits(0, lsb, msb)


def test_invalid_word_value_rejected():
    with pytest.raises(Exception):
        extract_bits(4096, 1, 12)
