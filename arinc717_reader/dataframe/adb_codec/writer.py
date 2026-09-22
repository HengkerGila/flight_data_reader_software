"""`.adb` writer / exporter (design spec §37).

Emits CRLF-terminated CSV.  Preserved raw fields win over regenerated ones:
the original settings record is reused with WPS/sync words overridden from
the canonical metadata, segment selectors reuse their original raw text, and
trailing unknown fields are re-appended verbatim.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ...domain.dataframe import DataframeDefinition, DataframeMetadata
from ...domain.parameter import ParameterDefinition
from .mappings import (
    SEGMENT_FIELD_COUNT,
    SETTING_MIN_FIELDS,
    SETTING_SYNC_WORD_INDEXES,
    SETTING_TAG,
    SETTING_TEMPLATE_FIELD_6,
    SETTING_TEMPLATE_UNKNOWN_FIELDS,
    SETTING_WPS_INDEX,
    encode_subframe_selector,
)


def write_adb_file(path: str | Path, dataframe: DataframeDefinition) -> None:
    # Observed files are plain ASCII; utf-8 is written as a safe superset.
    Path(path).write_bytes(dataframe_to_adb_text(dataframe).encode("utf-8"))


def dataframe_to_adb_text(dataframe: DataframeDefinition) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_settings_record(dataframe.metadata))
    for parameter in dataframe.parameters:
        writer.writerow(_parameter_record(parameter))
    return buffer.getvalue()


def _settings_record(metadata: DataframeMetadata) -> list[str]:
    if metadata.adb_settings_raw:
        record = list(metadata.adb_settings_raw)
        while len(record) < SETTING_MIN_FIELDS:
            record.append("")
    else:
        record = [""] * SETTING_MIN_FIELDS
        record[0] = SETTING_TAG
        for i, value in enumerate(SETTING_TEMPLATE_UNKNOWN_FIELDS, start=1):
            record[i] = value
        record[6] = SETTING_TEMPLATE_FIELD_6
    record[SETTING_WPS_INDEX] = str(metadata.wps)
    for i, index in enumerate(SETTING_SYNC_WORD_INDEXES):
        if i < len(metadata.sync_words):
            record[index] = str(metadata.sync_words[i])
    return record


def _parameter_record(parameter: ParameterDefinition) -> list[str]:
    record = [
        parameter.mnemonic,
        parameter.description or "",
        parameter.source_parameter_type or "",
        parameter.unit or "",
        _format_number(parameter.conversion.resolution),
        _format_number(parameter.conversion.offset),
        _format_optional(parameter.minimum),
        _format_optional(parameter.maximum),
        parameter.true_state or "",
        parameter.false_state or "",
        parameter.notes or "",
        str(len(parameter.occurrences)),
    ]
    for occurrence in sorted(parameter.occurrences, key=lambda o: o.index):
        record.append(str(len(occurrence.segments)))
        for segment in sorted(occurrence.segments, key=lambda s: s.sequence):
            source_raw = segment.source_raw or {}
            selector = source_raw.get("subframe_selector_raw") or (
                encode_subframe_selector(segment.subframes)
            )
            group = [
                selector,
                str(segment.word),
                str(segment.lsb),
                str(segment.msb),
                source_raw.get("legacy_flag", ""),
            ]
            assert len(group) == SEGMENT_FIELD_COUNT
            record.extend(group)
    if parameter.provenance:
        record.extend(parameter.provenance.extra.get("trailing_fields", []))
    return record


def _format_number(value: float) -> str:
    return format(value, "g")


def _format_optional(value: float | None) -> str:
    return "" if value is None else format(value, "g")
