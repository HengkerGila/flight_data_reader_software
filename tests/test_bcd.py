import pytest

from arinc717_reader.decoder.encoding.bcd import (
    BcdError,
    decode_bcd,
    encode_bcd,
    split_bcd_digits,
)


def test_two_digit_decode():
    assert decode_bcd(0b0100_0010, 8) == 42


def test_three_digit_decode():
    assert decode_bcd(0b0010_0100_0101, 12) == 245


def test_partial_leading_digit():
    # width 6: one 4-bit digit plus a 2-bit leading digit
    assert decode_bcd(0b10_0101, 6) == 25


def test_invalid_digit_is_error_not_binary():
    # Design spec §17: digits > 9 must be INVALID_BCD, never reinterpreted
    with pytest.raises(BcdError):
        decode_bcd(0b1010, 4)
    with pytest.raises(BcdError):
        decode_bcd(0xABC, 12)


def test_split_digits_msb_first():
    assert split_bcd_digits(0b0010_0100_0101, 12) == [2, 4, 5]


def test_encode():
    assert encode_bcd(42, 8) == 0b0100_0010
    assert encode_bcd(245, 12) == 0b0010_0100_0101
    assert encode_bcd(25, 6) == 0b10_0101
    assert encode_bcd(0, 12) == 0


def test_encode_decode_roundtrip():
    for value in (0, 9, 10, 99, 245, 999):
        assert decode_bcd(encode_bcd(value, 12), 12) == value


def test_encode_does_not_fit():
    with pytest.raises(BcdError):
        encode_bcd(999, 8)  # needs 3 digits
    with pytest.raises(BcdError):
        encode_bcd(45, 6)  # leading digit 4 does not fit in 2 bits
    with pytest.raises(BcdError):
        encode_bcd(-1, 8)
