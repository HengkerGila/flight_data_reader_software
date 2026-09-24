"""`.adb` parser (design spec §33–§35; layout verified against real AFDA files).

Every parameter record is 238 positional fields (see ``mappings``).  The
parser keeps the full raw record in ``ParameterProvenance.raw_record`` so
the writer can re-emit an unchanged parameter byte for byte, and lists
everything it had to guess or ignore in ``provenance.extra["adb_warnings"]``.

How a file location becomes a canonical segment:

* selector ``1234`` (or blank) — the word is frame-absolute (1..4×WPS) and
  the subframe is derived from it.  This is the convention of every
  location the vendor's own editor writes.
* any other selector with a word inside one subframe (≤ WPS) — the word is
  relative to the subframe(s) the selector names (the vendor file's
  date/time words: ``4,19`` = SF4 word 19).
* any other selector with a word beyond one subframe — PROVISIONAL: the
  word is taken as frame-absolute and the selector ignored, with a warning.
  The vendor file is internally inconsistent here; the probe file opened in
  AFDA settles it (docs/06-adb-format.md, "Open questions").

Samples that read the same word bits in different subframes are merged into
one segment with a multi-subframe tuple, which is what the decoder expects.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from ...domain.dataframe import DataframeDefinition, DataframeMetadata
from ...domain.frame import SUBFRAME_COUNT
from ...domain.parameter import (
    TYPE_BCD,
    ConversionRule,
    DiscreteState,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterProvenance,
    ParameterSegment,
    normalize_source_type,
)
from .mappings import (
    ADB_ENCODING,
    LOCATION_FIELD_COUNT,
    LOCATION_SLOTS,
    PARAM_CONVERSION_KIND,
    PARAM_DECIMALS,
    PARAM_DESCRIPTION,
    PARAM_LOCATIONS_START,
    PARAM_MAXIMUM,
    PARAM_MINIMUM,
    PARAM_NAME,
    PARAM_OFFSET,
    PARAM_PART_COUNT,
    PARAM_RECORD_LENGTH,
    PARAM_SAMPLE_COUNT,
    PARAM_SCALE,
    PARAM_STATE_LABELS_START,
    PARAM_STATE_VALUES_START,
    PARAM_TYPE,
    PARAM_UNIT,
    PARTS_ORDER,
    PARTS_ORDER_LS_FIRST,
    SELECTOR_ALL,
    SETTING_MIN_FIELDS,
    SETTING_SYNC_WORD_INDEXES,
    SETTING_TAG,
    SETTING_WPS_INDEX,
    STATE_SLOTS,
    SubframeSelectorError,
    decode_subframe_selector,
    encode_subframe_selector,
    split_absolute_word,
)


class AdbParseError(ValueError):
    def __init__(self, message: str, record_index: int | None = None):
        self.record_index = record_index
        prefix = f"record {record_index}: " if record_index is not None else ""
        super().__init__(f"{prefix}{message}")


def decode_adb_bytes(raw: bytes) -> str:
    """UTF-8 when the bytes are valid UTF-8 (which covers plain ASCII), else
    the Windows code page the vendor files use.  Never raises."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode(ADB_ENCODING, errors="replace")


def parse_adb_file(path: str | Path) -> DataframeDefinition:
    path = Path(path)
    raw = path.read_bytes()
    return parse_adb_text(
        decode_adb_bytes(raw),
        source_filename=path.name,
        source_hash=hashlib.sha256(raw).hexdigest(),
    )


def parse_adb_text(
    text: str,
    source_filename: str | None = None,
    source_hash: str | None = None,
) -> DataframeDefinition:
    records = [
        record
        for record in csv.reader(io.StringIO(text))
        if any(field.strip() for field in record)
    ]
    if not records:
        raise AdbParseError("empty ADB document")

    settings = records[0]
    if not settings or settings[0].strip() != SETTING_TAG:
        raise AdbParseError(
            f"first record must begin with {SETTING_TAG!r}, "
            f"got {settings[0]!r}" if settings else "first record is empty",
            record_index=0,
        )
    if len(settings) < SETTING_MIN_FIELDS:
        raise AdbParseError(
            f"settings record has {len(settings)} fields, "
            f"expected at least {SETTING_MIN_FIELDS}",
            record_index=0,
        )
    wps = _int_field(settings, SETTING_WPS_INDEX, "words per subframe", record_index=0)
    if wps <= 0:
        raise AdbParseError(f"words per subframe must be positive, got {wps}", 0)
    sync_words = [
        _int_field(settings, index, "sync word", record_index=0)
        for index in SETTING_SYNC_WORD_INDEXES
    ]

    name = Path(source_filename).stem if source_filename else "imported"
    metadata = DataframeMetadata(
        dataframe_name=name,
        wps=wps,
        sync_words=sync_words,
        source_type="adb",
        source_filename=source_filename,
        source_hash=source_hash,
        adb_settings_raw=tuple(settings),
    )

    parameters = [
        parse_parameter_record(record, index, wps)
        for index, record in enumerate(records[1:], start=1)
    ]
    return DataframeDefinition(metadata=metadata, parameters=parameters)


@dataclass
class _Location:
    slot: int
    subframes: tuple[int, ...]
    word: int
    lsb: int
    msb: int
    selector_raw: str
    word_raw: str
    spare: str


def parse_parameter_record(
    record: list[str], index: int, wps: int
) -> ParameterDefinition:
    """One 238-field parameter record → canonical definition.

    Public because the writer re-parses a preserved raw record to decide
    whether the parameter is still what the file said.
    """
    if len(record) < PARAM_RECORD_LENGTH:
        raise AdbParseError(
            f"parameter record has {len(record)} fields, expected "
            f"{PARAM_RECORD_LENGTH} (AFDA layout)",
            record_index=index,
        )

    def field(i: int) -> str:
        return record[i].strip()

    warnings: list[str] = []
    mnemonic = field(PARAM_NAME) or f"PARAM_{index:04d}"
    type_text = field(PARAM_TYPE) or None
    parameter_type = normalize_source_type(type_text)
    resolution = _float_or_default(field(PARAM_SCALE), 1.0, "scale", mnemonic, index)
    offset = _float_or_default(field(PARAM_OFFSET), 0.0, "offset", mnemonic, index)

    states, weights = _parse_state_slots(record, parameter_type, warnings)
    true_state = next((s.label for s in states if s.value == 1 and s.label), None)
    false_state = next((s.label for s in states if s.value == 0 and s.label), None)
    values = [s.value for s in states]
    if states and set(values) <= {0, 1} and len(values) == len(set(values)):
        states = []  # canonical form: the two labels say it all

    occurrences = _parse_locations(record, mnemonic, index, wps, weights, warnings)

    extra: dict = {}
    if warnings:
        extra["adb_warnings"] = warnings
    kind = field(PARAM_CONVERSION_KIND)
    if kind:
        extra["adb_conversion_kind"] = kind
    trailing = list(record[PARAM_RECORD_LENGTH:])
    if trailing:
        extra["trailing_fields"] = trailing

    return ParameterDefinition(
        id=f"adb-{index:04d}",
        mnemonic=mnemonic,
        description=field(PARAM_DESCRIPTION),
        source_parameter_type=type_text,
        parameter_type=parameter_type,
        unit=field(PARAM_UNIT) or None,
        minimum=_opt_float(field(PARAM_MINIMUM), "minimum", mnemonic, index),
        maximum=_opt_float(field(PARAM_MAXIMUM), "maximum", mnemonic, index),
        conversion=ConversionRule(resolution=resolution, offset=offset),
        decimals=_opt_int(field(PARAM_DECIMALS), "decimals", mnemonic, index),
        true_state=true_state,
        false_state=false_state,
        states=states,
        occurrences=occurrences,
        notes=None,
        provenance=ParameterProvenance(
            source_type="adb",
            record_index=index,
            raw_record=tuple(record),
            extra=extra,
        ),
    )


def _parse_state_slots(
    record: list[str], parameter_type: str, warnings: list[str]
) -> tuple[list[DiscreteState], list[float]]:
    """Columns 10..73: discrete state table, or BCD digit weights."""
    states: list[DiscreteState] = []
    weights: list[float] = []
    for slot in range(STATE_SLOTS):
        value_text = record[PARAM_STATE_VALUES_START + slot].strip()
        label = record[PARAM_STATE_LABELS_START + slot].strip()
        if parameter_type == TYPE_BCD:
            if value_text:
                try:
                    weights.append(float(value_text))
                except ValueError:
                    warnings.append(
                        f"BCD digit weight slot {slot + 1}: {value_text!r} "
                        "is not a number; ignored"
                    )
            continue
        if not value_text and not label:
            continue
        if value_text:
            try:
                value = int(value_text)
            except ValueError:
                warnings.append(
                    f"state slot {slot + 1}: value {value_text!r} is not an "
                    f"integer; slot index {slot} used"
                )
                value = slot
        else:
            warnings.append(
                f"state slot {slot + 1}: label {label!r} has no value; "
                f"slot index {slot} used"
            )
            value = slot
        states.append(DiscreteState(value=value, label=label))
    return states, weights


def _parse_locations(
    record: list[str],
    mnemonic: str,
    index: int,
    wps: int,
    weights: list[float],
    warnings: list[str],
) -> list[ParameterOccurrence]:
    sample_count = _opt_int(
        record[PARAM_SAMPLE_COUNT].strip(), "sample count", mnemonic, index
    )
    part_count = _opt_int(
        record[PARAM_PART_COUNT].strip(), "part count", mnemonic, index
    ) or 1
    if part_count < 1:
        part_count = 1

    locations: list[_Location] = []
    for slot in range(LOCATION_SLOTS):
        base = PARAM_LOCATIONS_START + slot * LOCATION_FIELD_COUNT
        selector, word_text, lsb_text, msb_text, spare = (
            record[base + i].strip() for i in range(LOCATION_FIELD_COUNT)
        )
        if not (selector or word_text or lsb_text or msb_text):
            continue
        where = f"location {slot + 1}"
        word = _int_or_raise(word_text, f"{where} word", mnemonic, index)
        lsb = _int_or_raise(lsb_text, f"{where} lsb", mnemonic, index)
        msb = _int_or_raise(msb_text, f"{where} msb", mnemonic, index)
        if word < 1:
            raise AdbParseError(
                f"{mnemonic}: {where}: word {word} outside the frame", index
            )
        if selector in ("", SELECTOR_ALL) or word > wps:
            subframe, relative = split_absolute_word(word, wps)
            if subframe > SUBFRAME_COUNT:
                raise AdbParseError(
                    f"{mnemonic}: {where}: word {word} beyond the "
                    f"{SUBFRAME_COUNT * wps}-word frame",
                    index,
                )
            if selector not in ("", SELECTOR_ALL):
                warnings.append(
                    f"{where}: subframe selector {selector!r} ignored, word "
                    f"{word} is frame-absolute (SF{subframe} word {relative})"
                )
            subframes: tuple[int, ...] = (subframe,)
        else:
            try:
                subframes = decode_subframe_selector(selector)
            except SubframeSelectorError as exc:
                raise AdbParseError(f"{mnemonic}: {where}: {exc}", index) from exc
            relative = word
        locations.append(
            _Location(slot, subframes, relative, lsb, msb, selector, word_text, spare)
        )

    if sample_count is not None and sample_count * part_count != len(locations):
        warnings.append(
            f"declares {sample_count} sample(s) x {part_count} part(s) but "
            f"{len(locations)} location(s) are present"
        )
    if len(locations) % part_count:
        warnings.append(
            f"{len(locations)} location(s) do not divide into parts of "
            f"{part_count}; the last sample is shorter"
        )
    if weights and len(weights) != part_count:
        warnings.append(
            f"{len(weights)} BCD digit weight(s) for {part_count} part(s); "
            "weights ignored"
        )
        weights = []

    occurrences: list[ParameterOccurrence] = []
    by_shape: dict[tuple, ParameterOccurrence] = {}
    for start in range(0, len(locations), part_count):
        sample = locations[start : start + part_count]
        shape = tuple((loc.word, loc.lsb, loc.msb) for loc in sample)
        existing = by_shape.get(shape)
        if existing is None:
            occurrence = ParameterOccurrence(
                index=len(occurrences) + 1, segments=_segments(sample, weights)
            )
            occurrences.append(occurrence)
            by_shape[shape] = occurrence
            continue
        # Same word bits in another subframe: one more sample of that segment.
        for segment, loc in zip(_file_order(existing.segments), sample):
            segment.subframes = tuple(sorted(set(segment.subframes) | set(loc.subframes)))
            if segment.source_raw is not None:
                segment.source_raw["subframe_selector_raw"] = encode_subframe_selector(
                    segment.subframes
                )
    return occurrences


def _segments(sample: list[_Location], weights: list[float]) -> list[ParameterSegment]:
    ordered = list(enumerate(sample))
    if PARTS_ORDER == PARTS_ORDER_LS_FIRST:
        ordered.reverse()  # sequence 1 = most significant (spec §13)
    segments: list[ParameterSegment] = []
    for sequence, (position, loc) in enumerate(ordered, start=1):
        segments.append(
            ParameterSegment(
                sequence=sequence,
                subframes=loc.subframes,
                word=loc.word,
                lsb=loc.lsb,
                msb=loc.msb,
                bcd_weight=weights[position] if position < len(weights) else None,
                source_raw={
                    "subframe_selector_raw": encode_subframe_selector(loc.subframes),
                    "legacy_flag": loc.spare,
                    "adb_selector": loc.selector_raw,
                    "adb_word": loc.word_raw,
                },
            )
        )
    return segments


def _file_order(segments: list[ParameterSegment]) -> list[ParameterSegment]:
    """Segments in the order their parts are listed in the file."""
    ordered = sorted(segments, key=lambda s: s.sequence)
    if PARTS_ORDER == PARTS_ORDER_LS_FIRST:
        ordered.reverse()
    return ordered


def _int_field(record: list[str], index: int, label: str, record_index: int) -> int:
    if index >= len(record) or not record[index].strip():
        raise AdbParseError(f"missing {label} at field {index}", record_index)
    try:
        return int(record[index].strip())
    except ValueError as exc:
        raise AdbParseError(
            f"invalid {label} {record[index]!r} at field {index}", record_index
        ) from exc


def _int_or_raise(text: str, label: str, mnemonic: str, record_index: int) -> int:
    try:
        return int(text)
    except ValueError as exc:
        raise AdbParseError(
            f"{mnemonic}: invalid {label} {text!r}", record_index
        ) from exc


def _opt_int(text: str, label: str, mnemonic: str, record_index: int) -> int | None:
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError as exc:
            raise AdbParseError(
                f"{mnemonic}: invalid {label} {text!r}", record_index
            ) from exc


def _float_or_default(
    text: str, default: float, label: str, mnemonic: str, record_index: int
) -> float:
    if not text:
        return default
    try:
        return float(text)
    except ValueError as exc:
        raise AdbParseError(
            f"{mnemonic}: invalid {label} {text!r}", record_index
        ) from exc


def _opt_float(text: str, label: str, mnemonic: str, record_index: int) -> float | None:
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise AdbParseError(
            f"{mnemonic}: invalid {label} {text!r}", record_index
        ) from exc
