"""`.adb` parser (design spec §33–§35).

Observed file properties: plain ASCII, CSV-like positional fields, CRLF line
endings, quoted fields may contain embedded newlines, first record begins
with ``Setting``.  A real CSV parser is mandatory — never ``line.split(",")``.

Unknown fields are preserved losslessly: the full raw settings record lands
in ``DataframeMetadata.adb_settings_raw``, each parameter's full raw record in
``ParameterProvenance.raw_record``, trailing unrecognized fields in
``provenance.extra["trailing_fields"]`` and per-segment legacy flags in
``segment.source_raw``.
"""

from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path

from ...domain.dataframe import DataframeDefinition, DataframeMetadata
from ...domain.parameter import (
    ConversionRule,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterProvenance,
    ParameterSegment,
    normalize_source_type,
)
from .mappings import (
    PARAM_DESCRIPTION,
    PARAM_FALSE_STATE,
    PARAM_MAXIMUM,
    PARAM_MINIMUM,
    PARAM_MNEMONIC,
    PARAM_NOTES,
    PARAM_OCCURRENCE_COUNT,
    PARAM_OFFSET,
    PARAM_RESOLUTION,
    PARAM_SOURCE_TYPE,
    PARAM_TRUE_STATE,
    PARAM_UNIT,
    PARAM_FIXED_FIELD_COUNT,
    SEGMENT_FIELD_COUNT,
    SETTING_MIN_FIELDS,
    SETTING_SYNC_WORD_INDEXES,
    SETTING_TAG,
    SETTING_WPS_INDEX,
    SubframeSelectorError,
    decode_subframe_selector,
)


class AdbParseError(ValueError):
    def __init__(self, message: str, record_index: int | None = None):
        self.record_index = record_index
        prefix = f"record {record_index}: " if record_index is not None else ""
        super().__init__(f"{prefix}{message}")


def parse_adb_file(path: str | Path) -> DataframeDefinition:
    path = Path(path)
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    return parse_adb_text(
        text,
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
    wps = _int_field(settings, SETTING_WPS_INDEX, "WPS", record_index=0)
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
        _parse_parameter_record(record, index)
        for index, record in enumerate(records[1:], start=1)
    ]
    return DataframeDefinition(metadata=metadata, parameters=parameters)


def _parse_parameter_record(record: list[str], index: int) -> ParameterDefinition:
    def field(i: int) -> str:
        return record[i].strip() if i < len(record) else ""

    mnemonic = field(PARAM_MNEMONIC) or f"PARAM_{index:04d}"
    resolution = _float_or_default(field(PARAM_RESOLUTION), 1.0, "resolution", index)
    offset = _float_or_default(field(PARAM_OFFSET), 0.0, "offset", index)

    occurrence_count = _int_or_default(
        field(PARAM_OCCURRENCE_COUNT), 0, "occurrence count", index
    )
    position = PARAM_FIXED_FIELD_COUNT
    occurrences: list[ParameterOccurrence] = []
    for occurrence_index in range(1, occurrence_count + 1):
        if position >= len(record):
            raise AdbParseError(
                f"{mnemonic}: record ends before occurrence {occurrence_index}",
                record_index=index,
            )
        segment_count = _int_or_default(
            record[position].strip(), None, "segment count", index
        )
        if segment_count is None:
            raise AdbParseError(
                f"{mnemonic}: missing segment count for occurrence {occurrence_index}",
                record_index=index,
            )
        position += 1
        segments: list[ParameterSegment] = []
        for sequence in range(1, segment_count + 1):
            group = record[position : position + SEGMENT_FIELD_COUNT]
            if len(group) < SEGMENT_FIELD_COUNT:
                raise AdbParseError(
                    f"{mnemonic}: record ends inside occurrence "
                    f"{occurrence_index} segment {sequence}",
                    record_index=index,
                )
            selector_raw, word_raw, lsb_raw, msb_raw, legacy_flag = (
                g.strip() for g in group
            )
            position += SEGMENT_FIELD_COUNT
            try:
                subframes = decode_subframe_selector(selector_raw)
            except SubframeSelectorError as exc:
                raise AdbParseError(f"{mnemonic}: {exc}", record_index=index) from exc
            segments.append(
                ParameterSegment(
                    sequence=sequence,
                    subframes=subframes,
                    word=_int_or_raise(word_raw, "word", mnemonic, index),
                    lsb=_int_or_raise(lsb_raw, "lsb", mnemonic, index),
                    msb=_int_or_raise(msb_raw, "msb", mnemonic, index),
                    source_raw={
                        "subframe_selector_raw": selector_raw,
                        "legacy_flag": legacy_flag,
                    },
                )
            )
        occurrences.append(
            ParameterOccurrence(index=occurrence_index, segments=segments)
        )

    trailing = [f for f in record[position:]]
    extra: dict = {}
    if trailing:
        extra["trailing_fields"] = trailing

    source_type = field(PARAM_SOURCE_TYPE) or None
    return ParameterDefinition(
        id=f"adb-{index:04d}",
        mnemonic=mnemonic,
        description=field(PARAM_DESCRIPTION),
        source_parameter_type=source_type,
        parameter_type=normalize_source_type(source_type),
        unit=field(PARAM_UNIT) or None,
        minimum=_opt_float(field(PARAM_MINIMUM), "minimum", index),
        maximum=_opt_float(field(PARAM_MAXIMUM), "maximum", index),
        conversion=ConversionRule(resolution=resolution, offset=offset),
        true_state=field(PARAM_TRUE_STATE) or None,
        false_state=field(PARAM_FALSE_STATE) or None,
        occurrences=occurrences,
        notes=field(PARAM_NOTES) or None,
        provenance=ParameterProvenance(
            source_type="adb",
            record_index=index,
            raw_record=tuple(record),
            extra=extra,
        ),
    )


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


def _int_or_default(text: str, default, label: str, record_index: int):
    if not text:
        return default
    try:
        return int(text)
    except ValueError as exc:
        raise AdbParseError(f"invalid {label} {text!r}", record_index) from exc


def _float_or_default(
    text: str, default: float, label: str, record_index: int
) -> float:
    if not text:
        return default
    try:
        return float(text)
    except ValueError as exc:
        raise AdbParseError(f"invalid {label} {text!r}", record_index) from exc


def _opt_float(text: str, label: str, record_index: int) -> float | None:
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise AdbParseError(f"invalid {label} {text!r}", record_index) from exc
