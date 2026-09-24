"""`.adb` writer / exporter (design spec §37; AFDA layout, see ``mappings``).

Two paths per parameter:

* An imported parameter that is still semantically identical to its raw
  record is written back as that record, byte for byte — number spelling,
  selector text, blank spare fields, everything AFDA wrote.
* Anything else (new, edited, from a PDF import, the demo) is generated from
  the canonical model: frame-absolute word numbering with selector ``1234``,
  one location per subframe sample, parts in ``PARTS_ORDER``, the discrete
  state table, BCD digit weights, AFDA's type vocabulary and plain decimal
  number spelling.  Notes have no column in the format and are not
  exported.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path

from ...domain.dataframe import DataframeDefinition, DataframeMetadata
from ...domain.frame import SUBFRAME_COUNT
from ...domain.parameter import (
    TYPE_ANALOG_SIGNED,
    TYPE_ANALOG_UNSIGNED,
    TYPE_BCD,
    TYPE_DISCRETE,
    ParameterDefinition,
)
from .mappings import (
    ADB_ENCODING,
    CONVERSION_KIND_ANALOG,
    CONVERSION_KIND_DISCRETE,
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
    SETTING_BITS_INDEX,
    SETTING_BITS_PER_WORD,
    SETTING_MIN_FIELDS,
    SETTING_SYNC_WORD_INDEXES,
    SETTING_TAG,
    SETTING_TEMPLATE_UNKNOWN,
    SETTING_WORDS_PER_FRAME_INDEX,
    SETTING_WPS_INDEX,
    STATE_SLOTS,
    absolute_word,
    afda_type_text,
)
from .parser import AdbParseError, parse_parameter_record


class AdbWriteError(ValueError):
    """Raised when a dataframe cannot be expressed in the AFDA record layout."""


def write_adb_file(path: str | Path, dataframe: DataframeDefinition) -> None:
    Path(path).write_bytes(
        dataframe_to_adb_text(dataframe).encode(ADB_ENCODING, errors="replace")
    )


def dataframe_to_adb_text(dataframe: DataframeDefinition) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_settings_record(dataframe.metadata))
    for parameter in dataframe.parameters:
        writer.writerow(parameter_record(parameter, dataframe.metadata.wps))
    return buffer.getvalue()


def _settings_record(metadata: DataframeMetadata) -> list[str]:
    wps = metadata.wps
    if metadata.adb_settings_raw:
        record = list(metadata.adb_settings_raw)
        while len(record) < SETTING_MIN_FIELDS:
            record.append("")
        old_wps = record[SETTING_WPS_INDEX].strip()
        old_wpf = record[SETTING_WORDS_PER_FRAME_INDEX].strip()
        # Follow the words-per-frame field only where the source file showed
        # it to be 4 × WPS; otherwise its meaning is not ours to change.
        if not old_wpf or (
            old_wps.isdigit()
            and old_wpf.isdigit()
            and int(old_wpf) == SUBFRAME_COUNT * int(old_wps)
        ):
            record[SETTING_WORDS_PER_FRAME_INDEX] = str(SUBFRAME_COUNT * wps)
    else:
        record = [""] * SETTING_MIN_FIELDS
        record[0] = SETTING_TAG
        for index, value in SETTING_TEMPLATE_UNKNOWN.items():
            record[index] = value
        record[SETTING_BITS_INDEX] = SETTING_BITS_PER_WORD
        record[SETTING_WORDS_PER_FRAME_INDEX] = str(SUBFRAME_COUNT * wps)
    record[SETTING_WPS_INDEX] = str(wps)
    for i, index in enumerate(SETTING_SYNC_WORD_INDEXES):
        if i < len(metadata.sync_words):
            record[index] = str(metadata.sync_words[i])
    return record


def parameter_record(parameter: ParameterDefinition, wps: int) -> list[str]:
    """The record to write: the preserved raw one when still accurate."""
    raw = parameter.provenance.raw_record if parameter.provenance else None
    if raw and len(raw) >= PARAM_RECORD_LENGTH and _still_matches(parameter, raw, wps):
        return list(raw)
    return _generate_record(parameter, wps)


def _still_matches(parameter: ParameterDefinition, raw, wps: int) -> bool:
    from ..compare import parameter_differences  # compare imports this package

    try:
        reparsed = parse_parameter_record(
            list(raw), parameter.provenance.record_index or 0, wps
        )
    except AdbParseError:
        return False
    return not parameter_differences(parameter, reparsed, ignore=("notes",))


def _generate_record(parameter: ParameterDefinition, wps: int) -> list[str]:
    record = [""] * PARAM_RECORD_LENGTH
    ptype = parameter.parameter_type
    record[PARAM_NAME] = parameter.mnemonic
    record[PARAM_DESCRIPTION] = parameter.description or ""
    record[PARAM_TYPE] = afda_type_text(ptype, parameter.source_parameter_type)
    record[PARAM_UNIT] = parameter.unit or ""
    record[PARAM_MINIMUM] = format_adb_number(parameter.minimum)
    record[PARAM_MAXIMUM] = format_adb_number(parameter.maximum)
    record[PARAM_SCALE] = format_adb_number(parameter.conversion.resolution)
    record[PARAM_OFFSET] = format_adb_number(parameter.conversion.offset)
    record[PARAM_DECIMALS] = str(parameter.effective_decimals())

    if ptype == TYPE_DISCRETE:
        states = parameter.effective_states()
        if len(states) > STATE_SLOTS:
            raise AdbWriteError(
                f"{parameter.mnemonic}: {len(states)} discrete states exceed "
                f"the {STATE_SLOTS} an AFDA record holds"
            )
        record[PARAM_CONVERSION_KIND] = CONVERSION_KIND_DISCRETE if states else ""
        for slot, state in enumerate(states):
            record[PARAM_STATE_VALUES_START + slot] = str(state.value)
            record[PARAM_STATE_LABELS_START + slot] = state.label
    elif ptype in (TYPE_ANALOG_SIGNED, TYPE_ANALOG_UNSIGNED, TYPE_BCD):
        record[PARAM_CONVERSION_KIND] = CONVERSION_KIND_ANALOG

    locations, parts, weights = _locations(parameter, wps)
    if len(locations) > LOCATION_SLOTS:
        raise AdbWriteError(
            f"{parameter.mnemonic}: {len(locations)} sample locations exceed "
            f"the {LOCATION_SLOTS} an AFDA record holds"
        )
    record[PARAM_SAMPLE_COUNT] = str(len(locations) // parts if parts else 0)
    record[PARAM_PART_COUNT] = str(parts)
    if ptype == TYPE_BCD:
        for slot, weight in enumerate(weights):
            record[PARAM_STATE_VALUES_START + slot] = format_adb_number(weight)
    for slot, (word, lsb, msb, flag) in enumerate(locations):
        base = PARAM_LOCATIONS_START + slot * LOCATION_FIELD_COUNT
        record[base] = SELECTOR_ALL
        record[base + 1] = str(word)
        record[base + 2] = str(lsb)
        record[base + 3] = str(msb)
        record[base + 4] = flag
    if parameter.provenance:
        record.extend(parameter.provenance.extra.get("trailing_fields", []))
    return record


def _locations(
    parameter: ParameterDefinition, wps: int
) -> tuple[list[tuple[int, int, int, str]], int, list[float]]:
    """Location groups in file order, the parts-per-sample count and, for
    BCD, the digit weights in file order."""
    occurrences = sorted(
        (o for o in parameter.occurrences if o.segments), key=lambda o: o.index
    )
    if not occurrences:
        return [], 1, []
    part_counts = {len(o.segments) for o in occurrences}
    uniform = len(part_counts) == 1 and all(
        len({len(set(s.subframes)) for s in o.segments}) == 1 for o in occurrences
    )
    parts = part_counts.pop() if uniform else 1
    # (subframe, listed occurrence position, locations of one sample)
    samples: list[tuple[int, int, list[tuple[int, int, int, str]]]] = []
    weights: list[float] = []
    for position, occurrence in enumerate(occurrences):
        segments = sorted(occurrence.segments, key=lambda s: s.sequence)
        file_order = (
            list(reversed(segments)) if PARTS_ORDER == PARTS_ORDER_LS_FIRST else segments
        )
        per_part = [sorted(set(s.subframes)) for s in file_order]
        if uniform and len({len(p) for p in per_part}) == 1:
            # Sample k = the k-th subframe of every part (parts of one value
            # may sit in different subframes, e.g. SF2 word 64 + SF3 word 1).
            for subframes in zip(*per_part):
                samples.append((
                    min(subframes),
                    position,
                    [_location(segment, subframe, wps) for segment, subframe in zip(file_order, subframes)],
                ))
            if not weights and all(s.bcd_weight is not None for s in segments):
                weights = [float(s.bcd_weight) for s in file_order]
        else:
            # Occurrences of different shapes cannot share one parts count;
            # every segment becomes a sample of its own (parts = 1).
            for segment in file_order:
                for subframe in sorted(set(segment.subframes)):
                    samples.append((subframe, position, [_location(segment, subframe, wps)]))
    # The vendor file lists samples in frame order (22, 54, 86, …): subframe by
    # subframe, occurrences in their listed order, parts in significance order.
    samples.sort(key=lambda sample: (sample[0], sample[1]))
    return [location for _sf, _pos, sample in samples for location in sample], parts, weights


def _location(segment, subframe: int, wps: int) -> tuple[int, int, int, str]:
    flag = (segment.source_raw or {}).get("legacy_flag", "")
    return (
        absolute_word(subframe, segment.word, wps),
        segment.lsb,
        segment.msb,
        flag,
    )


def format_adb_number(value: float | int | None) -> str:
    """Plain decimal spelling, no exponent: 1.7166137e-4 → 0.00017166137."""
    if value is None:
        return ""
    number = float(value)
    if number == 0:
        return "0"
    if number.is_integer() and abs(number) < 1e15:
        return str(int(number))
    text = format(Decimal(repr(number)), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
