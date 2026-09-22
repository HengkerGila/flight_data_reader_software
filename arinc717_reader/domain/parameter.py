"""Canonical parameter model (design spec §6.3–§6.7).

Domain models are deliberately permissive: an importer may hold rows that are
not yet valid.  Enforcement lives in the validator layer and in the decoder,
which reports explicit statuses instead of raising.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Canonical parameter types (design spec §6.4).
TYPE_ANALOG_SIGNED = "analog_signed"
TYPE_ANALOG_UNSIGNED = "analog_unsigned"
TYPE_BCD = "bcd"
TYPE_DISCRETE = "discrete"
TYPE_RAW = "raw"
TYPE_UNKNOWN = "unknown"

CANONICAL_TYPES = (
    TYPE_ANALOG_SIGNED,
    TYPE_ANALOG_UNSIGNED,
    TYPE_BCD,
    TYPE_DISCRETE,
    TYPE_RAW,
    TYPE_UNKNOWN,
)

# Source-type normalization table.  Keys are upper-cased source strings.
# Signedness must come from the dataframe definition (spec §14); a bare "BNR"
# carries no sign marker, so it normalizes to unsigned — a dataframe that
# means signed must say so ("Signed Analog", "BNR SIGNED", ...).
SOURCE_TYPE_MAP: dict[str, str] = {
    "SIGNED ANALOG": TYPE_ANALOG_SIGNED,
    "SIGNED": TYPE_ANALOG_SIGNED,
    "BNR SIGNED": TYPE_ANALOG_SIGNED,
    "UNSIGNED ANALOG": TYPE_ANALOG_UNSIGNED,
    "UNSIGNED": TYPE_ANALOG_UNSIGNED,
    "BNR": TYPE_ANALOG_UNSIGNED,
    "ANALOG": TYPE_ANALOG_UNSIGNED,
    "BCD": TYPE_BCD,
    "DISCRETE": TYPE_DISCRETE,
    "STATUS": TYPE_DISCRETE,
    "RAW": TYPE_RAW,
}


def normalize_source_type(source_type: str | None) -> str:
    """Map a source parameter type string to a canonical type.

    Exact table matches win.  Composite source text that carries an
    explicit keyword ("BNR (signed)", "Discrete - 2 states", "BCD 3 digits",
    "two's complement") is classified by that keyword; anything without a
    recognized keyword normalizes to ``unknown`` — never guessed.  Every
    importer and the editor use this one function so a parameter's canonical
    type never changes between import and a later edit.
    """
    if source_type is None:
        return TYPE_UNKNOWN
    key = " ".join(source_type.strip().upper().split())
    if not key:
        return TYPE_UNKNOWN
    mapped = SOURCE_TYPE_MAP.get(key)
    if mapped is not None:
        return mapped
    return _classify_source_type(key)


_SIGNED_TOKENS = frozenset({"SIGNED", "2'S", "TWO'S", "TWOS", "2S", "COMPLEMENT"})
_DISCRETE_TOKENS = frozenset({"DISCRETE", "DISC", "DIS", "BOOLEAN", "BOOL", "STATUS", "LOGIC"})
_BINARY_TOKENS = frozenset({"BNR", "BINARY", "ANALOG", "ANALOGUE", "ANLG"})


def _classify_source_type(key: str) -> str:
    tokens = set(re.findall(r"[A-Z0-9']+", key))
    if "BCD" in tokens:
        return TYPE_BCD
    if tokens & _DISCRETE_TOKENS:
        return TYPE_DISCRETE
    if "UNSIGNED" in tokens:
        return TYPE_ANALOG_UNSIGNED
    if tokens & _SIGNED_TOKENS:
        return TYPE_ANALOG_SIGNED
    if tokens & _BINARY_TOKENS:
        return TYPE_ANALOG_UNSIGNED
    if "RAW" in tokens:
        return TYPE_RAW
    return TYPE_UNKNOWN


@dataclass
class ConversionRule:
    resolution: float = 1.0
    offset: float = 0.0
    formula_type: str = "linear"


@dataclass
class ParameterSegment:
    """One bit field inside one word (design spec §6.7).

    ``subframes`` lists every subframe the segment is recorded in; a
    multi-subframe segment yields one decoded sample per subframe.
    Bit numbering is 12..1 with bit 1 the least significant (spec §12);
    ``lsb``/``msb`` may arrive in either order and are normalized on use.
    """

    sequence: int
    subframes: tuple[int, ...]
    word: int
    lsb: int
    msb: int
    source_raw: dict | None = None

    @property
    def bit_range(self) -> tuple[int, int]:
        """Normalized (low, high) bit numbers."""
        return (min(self.lsb, self.msb), max(self.lsb, self.msb))

    @property
    def width(self) -> int:
        lo, hi = self.bit_range
        return hi - lo + 1


@dataclass
class ParameterOccurrence:
    index: int
    segments: list[ParameterSegment] = field(default_factory=list)


@dataclass
class ParameterProvenance:
    """Where a parameter definition came from (design spec §30, §58)."""

    source_type: str | None = None
    source_filename: str | None = None
    record_index: int | None = None
    raw_record: tuple[str, ...] | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class ParameterDefinition:
    id: str
    mnemonic: str
    description: str = ""

    source_parameter_type: str | None = None
    parameter_type: str = TYPE_UNKNOWN

    unit: str | None = None

    minimum: float | None = None
    maximum: float | None = None

    conversion: ConversionRule = field(default_factory=ConversionRule)

    true_state: str | None = None
    false_state: str | None = None

    occurrences: list[ParameterOccurrence] = field(default_factory=list)

    notes: str | None = None

    provenance: ParameterProvenance | None = None
