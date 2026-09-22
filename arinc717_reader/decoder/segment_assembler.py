"""Multi-segment assembly (design spec §13).

Assembly convention: segments are combined in canonical ``sequence`` order and
the FIRST segment contributes the MOST significant bits.  The inverse split
used by the parameter encoder lives here too, so the convention has exactly
one home.  The order comes from the canonical model, never from word numbers.
"""

from __future__ import annotations

from collections.abc import Sequence


class AssemblyError(ValueError):
    """Raised when segment parts cannot be assembled or split."""


def assemble_segments(parts: Sequence[tuple[int, int]]) -> tuple[int, int]:
    """Assemble ``(value, width)`` parts into ``(assembled_value, total_width)``."""
    if not parts:
        raise AssemblyError("no segment parts to assemble")
    value = 0
    total = 0
    for i, (part_value, width) in enumerate(parts, start=1):
        if width <= 0:
            raise AssemblyError(f"segment part {i} has non-positive width {width}")
        if part_value < 0 or part_value >> width:
            raise AssemblyError(
                f"segment part {i} value {part_value} does not fit in {width} bits"
            )
        value = (value << width) | part_value
        total += width
    return value, total


def split_segments(value: int, widths: Sequence[int]) -> list[int]:
    """Split an assembled value back into per-segment parts (encoder path)."""
    if not widths:
        raise AssemblyError("no segment widths to split into")
    total = sum(widths)
    if any(w <= 0 for w in widths):
        raise AssemblyError(f"segment widths must be positive, got {list(widths)}")
    if value < 0 or value >> total:
        raise AssemblyError(f"value {value} does not fit in {total} bits")
    parts: list[int] = []
    shift = total
    for width in widths:
        shift -= width
        parts.append((value >> shift) & ((1 << width) - 1))
    return parts
