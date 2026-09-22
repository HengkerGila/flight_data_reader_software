"""Word display representations (design spec §8.2).

Formatting/parsing only — the canonical integer word value never changes
when the representation changes.
"""

from __future__ import annotations

from ..domain.frame import WORD_MAX

REPRESENTATIONS = ("BIN", "OCT", "DEC", "HEX")

_BASES = {"BIN": 2, "OCT": 8, "DEC": 10, "HEX": 16}


def format_word(value: int, representation: str) -> str:
    if representation == "BIN":
        return format(value, "012b")
    if representation == "OCT":
        return format(value, "04o")
    if representation == "HEX":
        return format(value, "03X")
    if representation == "DEC":
        return str(value)
    raise ValueError(f"unknown representation {representation!r}")


def parse_word(text: str, representation: str) -> int:
    base = _BASES.get(representation)
    if base is None:
        raise ValueError(f"unknown representation {representation!r}")
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("empty value")
    try:
        value = int(cleaned, base)
    except ValueError as exc:
        raise ValueError(f"{cleaned!r} is not a valid {representation} value") from exc
    if not 0 <= value <= WORD_MAX:
        raise ValueError(f"value {value} outside 0..{WORD_MAX} (12 bits)")
    return value


def all_representations(value: int) -> dict[str, str]:
    return {rep: format_word(value, rep) for rep in REPRESENTATIONS}
