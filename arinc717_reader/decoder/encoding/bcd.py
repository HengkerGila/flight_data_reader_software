"""BCD interpretation (design spec §17).

BCD is never treated as ordinary binary.  The bit field is split into 4-bit
digits from the least significant end; when the width is not a multiple of 4
the leading (most significant) digit uses the remaining bits.  Any digit
greater than 9 is an error — it is not silently reinterpreted as binary.
"""

from __future__ import annotations


class BcdError(ValueError):
    """Raised when a BCD field contains an invalid digit or does not fit."""


def split_bcd_digits(value: int, width: int) -> list[int]:
    """Split a raw field into digits, most significant first (no validation)."""
    if width <= 0:
        raise BcdError(f"width must be positive, got {width}")
    if value < 0 or value >> width:
        raise BcdError(f"value {value} does not fit in {width} bits")
    digits: list[int] = []
    remaining = width
    while remaining > 0:
        take = 4 if remaining >= 4 else remaining
        digits.append(value & ((1 << take) - 1))
        value >>= take
        remaining -= take
    digits.reverse()
    return digits


def decode_bcd(value: int, width: int) -> int:
    """Decode a BCD field to its decimal integer, raising on invalid digits."""
    digits = split_bcd_digits(value, width)
    for position, digit in enumerate(digits, start=1):
        if digit > 9:
            raise BcdError(
                f"invalid BCD digit {digit} (nibble {position} of {len(digits)})"
            )
    result = 0
    for digit in digits:
        result = result * 10 + digit
    return result


def encode_bcd(decimal: int, width: int) -> int:
    """Encode a non-negative decimal into a BCD field of ``width`` bits."""
    if decimal < 0:
        raise BcdError(f"cannot BCD-encode negative value {decimal}")
    if width <= 0:
        raise BcdError(f"width must be positive, got {width}")
    # Digit slots, least significant first: full nibbles plus a partial
    # leading digit if the width is not a multiple of 4.
    slot_widths: list[int] = []
    remaining = width
    while remaining > 0:
        take = 4 if remaining >= 4 else remaining
        slot_widths.append(take)
        remaining -= take
    value = 0
    shift = 0
    n = decimal
    for slot in slot_widths:
        digit = n % 10
        n //= 10
        if digit >> slot:
            raise BcdError(
                f"digit {digit} does not fit in leading {slot}-bit slot "
                f"while encoding {decimal} into {width} bits"
            )
        value |= digit << shift
        shift += slot
    if n:
        raise BcdError(f"decimal {decimal} does not fit in {width}-bit BCD field")
    return value
