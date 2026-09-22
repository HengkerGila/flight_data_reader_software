"""Engineering data model and decode trace (design spec §19, §58).

Every displayed engineering value must be traceable back through
segments → words → bits → decoded decimal → conversion.  The trace is a
first-class part of the model, not an afterthought.
"""

from __future__ import annotations

from dataclasses import dataclass, field

STATUS_VALID = "VALID"
STATUS_INVALID_MAPPING = "INVALID_MAPPING"
STATUS_INVALID_BCD = "INVALID_BCD"
STATUS_OUT_OF_RANGE = "OUT_OF_RANGE"
STATUS_UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
STATUS_MISSING_DATA = "MISSING_DATA"

ALL_STATUSES = (
    STATUS_VALID,
    STATUS_INVALID_MAPPING,
    STATUS_INVALID_BCD,
    STATUS_OUT_OF_RANGE,
    STATUS_UNSUPPORTED_TYPE,
    STATUS_MISSING_DATA,
)


@dataclass
class SegmentTrace:
    sequence: int
    subframe: int
    word: int
    lsb: int
    msb: int
    width: int
    word_value: int | None = None
    extracted_value: int | None = None
    extracted_bits: str | None = None


@dataclass
class DecodeTrace:
    parameter_id: str
    occurrence_index: int
    subframe: int | None
    segments: list[SegmentTrace] = field(default_factory=list)

    assembled_bits: str | None = None
    assembled_value: int | None = None
    bit_width: int | None = None

    decoded_decimal: int | float | None = None
    resolution: float | None = None
    offset: float | None = None

    engineering_value: int | float | str | None = None

    status: str = STATUS_VALID
    message: str | None = None


@dataclass
class EngineeringValue:
    parameter_id: str
    parameter_name: str

    occurrence_index: int
    # The subframe this sample was decoded from.  A multi-subframe mapping
    # yields one EngineeringValue per subframe.
    subframe: int | None

    raw_bits: str | None
    raw_integer: int | None
    decoded_decimal: int | float | None

    engineering_value: int | float | str | None
    unit: str | None

    status: str

    trace: DecodeTrace


def format_engineering_value(value: int | float | str | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def format_decode_chain(ev: EngineeringValue) -> str:
    """Human-readable decode chain for diagnostics (design spec §9, §58)."""
    t = ev.trace
    lines = [
        f"Parameter      : {ev.parameter_name}",
        f"Occurrence     : {ev.occurrence_index}"
        + (f"   Subframe: {ev.subframe}" if ev.subframe is not None else ""),
        f"Status         : {ev.status}",
    ]
    if t.message:
        lines.append(f"Message        : {t.message}")
    if t.segments:
        lines.append("")
        lines.append("Segments (assembly order, first = most significant):")
        for seg in t.segments:
            word_txt = "—" if seg.word_value is None else (
                f"{seg.word_value:4d} (0x{seg.word_value:03X})"
            )
            bits_txt = seg.extracted_bits if seg.extracted_bits is not None else "—"
            lines.append(
                f"  #{seg.sequence}  SF{seg.subframe}  word {seg.word:03d}  "
                f"bits {seg.msb}-{seg.lsb}  word={word_txt}  extracted={bits_txt}"
            )
    if t.assembled_bits is not None:
        lines.append("")
        lines.append(f"Assembled bits : {t.assembled_bits}  (width {t.bit_width})")
        lines.append(f"Assembled int  : {t.assembled_value}")
    if t.decoded_decimal is not None:
        lines.append(f"Decoded decimal: {t.decoded_decimal}")
    if t.resolution is not None:
        lines.append(f"Resolution     : {t.resolution:g}")
    if t.offset is not None:
        lines.append(f"Offset         : {t.offset:g}")
    eng = format_engineering_value(ev.engineering_value)
    unit = f" {ev.unit}" if ev.unit else ""
    lines.append(f"Engineering    : {eng}{unit}")
    return "\n".join(lines)
