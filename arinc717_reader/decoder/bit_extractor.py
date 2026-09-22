"""Bit extraction from 12-bit ARINC words (design spec §12).

Bit numbering is 12..1 with bit 1 the least significant.  Source dataframes
disagree on whether the first number is the LSB or the MSB, so ``lsb``/``msb``
are accepted in either order and normalized here (spec: "Do not assume
msb >= lsb without normalizing source conventions").
"""

from __future__ import annotations

from ..domain.frame import WORD_BITS, validate_word_value


class BitRangeError(ValueError):
    """Raised when a bit range is outside 1..12."""


def normalize_bit_range(lsb: int, msb: int) -> tuple[int, int]:
    """Return the normalized (low, high) bit numbers."""
    for name, bit in (("lsb", lsb), ("msb", msb)):
        if not isinstance(bit, int) or isinstance(bit, bool):
            raise BitRangeError(f"{name} must be an integer, got {bit!r}")
        if not 1 <= bit <= WORD_BITS:
            raise BitRangeError(f"{name}={bit} outside 1..{WORD_BITS}")
    return (min(lsb, msb), max(lsb, msb))


def bit_width(lsb: int, msb: int) -> int:
    lo, hi = normalize_bit_range(lsb, msb)
    return hi - lo + 1


def extract_bits(word_value: int, lsb: int, msb: int) -> int:
    """Extract the bit field ``msb..lsb`` from a raw 12-bit word."""
    validate_word_value(word_value)
    lo, hi = normalize_bit_range(lsb, msb)
    width = hi - lo + 1
    return (word_value >> (lo - 1)) & ((1 << width) - 1)
