"""Signed (two's-complement) interpretation (design spec §14).

Signedness always comes from the dataframe definition, never from the bit
pattern.  Validated ground-test case: 1101010101 (10 bits) -> 853 -> -171.
"""

from __future__ import annotations


class SignedRangeError(ValueError):
    """Raised when a value does not fit the signed bit width."""


def twos_complement(value: int, width: int) -> int:
    """Interpret an unsigned ``width``-bit pattern as two's complement."""
    if width <= 0:
        raise SignedRangeError(f"width must be positive, got {width}")
    if value < 0 or value >> width:
        raise SignedRangeError(f"value {value} does not fit in {width} bits")
    sign_bit = 1 << (width - 1)
    if value & sign_bit:
        return value - (1 << width)
    return value


def to_twos_complement(value: int, width: int) -> int:
    """Encode a signed integer as an unsigned ``width``-bit pattern."""
    if width <= 0:
        raise SignedRangeError(f"width must be positive, got {width}")
    lo = -(1 << (width - 1))
    hi = (1 << (width - 1)) - 1
    if not lo <= value <= hi:
        raise SignedRangeError(
            f"signed value {value} outside {lo}..{hi} for width {width}"
        )
    return value & ((1 << width) - 1)
