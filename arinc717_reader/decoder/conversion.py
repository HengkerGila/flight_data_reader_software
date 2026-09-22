"""Linear engineering conversion (design spec §16).

Supports positive/negative resolution and positive/negative/zero offset.
"""

from __future__ import annotations


class ConversionError(ValueError):
    """Raised when a conversion cannot be applied or inverted."""


def apply_linear(decoded_decimal: int | float, resolution: float, offset: float) -> float:
    return decoded_decimal * resolution + offset


def invert_linear(engineering: float, resolution: float, offset: float) -> float:
    """Inverse conversion used by the scenario encoder (design spec §24)."""
    if resolution == 0:
        raise ConversionError("cannot invert conversion with resolution 0")
    return (engineering - offset) / resolution
