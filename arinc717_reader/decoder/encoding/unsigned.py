"""Unsigned interpretation (design spec §15)."""

from __future__ import annotations


class UnsignedRangeError(ValueError):
    """Raised when a value does not fit the unsigned bit width."""


def decode_unsigned(value: int, width: int) -> int:
    if width <= 0:
        raise UnsignedRangeError(f"width must be positive, got {width}")
    if value < 0 or value >> width:
        raise UnsignedRangeError(f"value {value} does not fit in {width} bits")
    return value


def encode_unsigned(value: int, width: int) -> int:
    return decode_unsigned(value, width)
