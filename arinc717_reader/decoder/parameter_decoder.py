"""The core runtime decoding engine (design spec §10, §11).

For every parameter occurrence:
resolve segments → select subframe(s) → read word(s) → extract bit ranges →
assemble segments in sequence → interpret by type → convert → engineering
value, with a full trace stored for diagnostics.

A segment mapped to several subframes means the parameter is recorded in each
of them, so one occurrence yields one decoded sample per subframe.  All
segments of an occurrence must agree on the subframe set.

Errors are reported as explicit statuses on the produced values; the decoder
does not raise for bad mappings (design spec §47).
"""

from __future__ import annotations

from ..domain.dataframe import DataframeDefinition
from ..domain.engineering import (
    STATUS_INVALID_BCD,
    STATUS_INVALID_MAPPING,
    STATUS_OUT_OF_RANGE,
    STATUS_UNSUPPORTED_TYPE,
    STATUS_VALID,
    DecodeTrace,
    EngineeringValue,
    SegmentTrace,
)
from ..domain.frame import SUBFRAME_COUNT, Arinc717Frame
from ..domain.parameter import (
    TYPE_ANALOG_SIGNED,
    TYPE_ANALOG_UNSIGNED,
    TYPE_BCD,
    TYPE_DISCRETE,
    TYPE_RAW,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterSegment,
)
from .bit_extractor import BitRangeError, extract_bits, normalize_bit_range
from .conversion import apply_linear
from .encoding.bcd import BcdError, decode_bcd
from .encoding.discrete import decode_discrete
from .encoding.signed import twos_complement
from .segment_assembler import assemble_segments


class ParameterDecoder:
    def decode_frame(
        self, frame: Arinc717Frame, dataframe: DataframeDefinition
    ) -> list[EngineeringValue]:
        values: list[EngineeringValue] = []
        for parameter in dataframe.parameters:
            values.extend(self.decode_parameter(frame, parameter))
        return values

    def decode_subframe(
        self, frame: Arinc717Frame, dataframe: DataframeDefinition, subframe: int
    ) -> list[EngineeringValue]:
        """Decode only the occurrences recorded in ``subframe`` (spec v2 §26G).

        Used by live streaming so values are published as soon as their
        subframe arrives.  Segments of one occurrence always share a subframe
        set, so a subframe is self-contained.  Mapping errors are reported
        once, with SF1, so a bad parameter still shows up in the live view.
        """
        values: list[EngineeringValue] = []
        for parameter in dataframe.parameters:
            if not parameter.occurrences:
                if subframe == 1:
                    values.extend(self.decode_parameter(frame, parameter))
                continue
            for occurrence in parameter.occurrences:
                segments = sorted(occurrence.segments, key=lambda s: s.sequence)
                subframe_sets = {tuple(sorted(set(s.subframes))) for s in segments}
                if not segments or len(subframe_sets) != 1 or not subframe_sets.copy().pop():
                    if subframe == 1:
                        values.extend(self.decode_occurrence(frame, parameter, occurrence))
                    continue
                if subframe in subframe_sets.pop():
                    values.append(
                        self._decode_sample(frame, parameter, occurrence, segments, subframe)
                    )
        return values

    def decode_parameter(
        self, frame: Arinc717Frame, parameter: ParameterDefinition
    ) -> list[EngineeringValue]:
        if not parameter.occurrences:
            return [
                _error_value(
                    parameter, 0, None, [],
                    STATUS_INVALID_MAPPING, "parameter has no occurrences",
                )
            ]
        values: list[EngineeringValue] = []
        for occurrence in parameter.occurrences:
            values.extend(self.decode_occurrence(frame, parameter, occurrence))
        return values

    def decode_occurrence(
        self,
        frame: Arinc717Frame,
        parameter: ParameterDefinition,
        occurrence: ParameterOccurrence,
    ) -> list[EngineeringValue]:
        segments = sorted(occurrence.segments, key=lambda s: s.sequence)
        if not segments:
            return [
                _error_value(
                    parameter, occurrence.index, None, [],
                    STATUS_INVALID_MAPPING, "occurrence has no segments",
                )
            ]
        subframe_sets = {tuple(sorted(set(s.subframes))) for s in segments}
        if len(subframe_sets) != 1:
            return [
                _error_value(
                    parameter, occurrence.index, None, [],
                    STATUS_INVALID_MAPPING,
                    f"segments disagree on subframes: {sorted(subframe_sets)}",
                )
            ]
        subframes = subframe_sets.pop()
        if not subframes:
            return [
                _error_value(
                    parameter, occurrence.index, None, [],
                    STATUS_INVALID_MAPPING, "segment has no subframes",
                )
            ]
        return [
            self._decode_sample(frame, parameter, occurrence, segments, sf)
            for sf in subframes
        ]

    def _decode_sample(
        self,
        frame: Arinc717Frame,
        parameter: ParameterDefinition,
        occurrence: ParameterOccurrence,
        segments: list[ParameterSegment],
        subframe: int,
    ) -> EngineeringValue:
        traces: list[SegmentTrace] = []
        parts: list[tuple[int, int]] = []
        for segment in segments:
            lo_hi_error = None
            try:
                normalize_bit_range(segment.lsb, segment.msb)
            except BitRangeError as exc:
                lo_hi_error = str(exc)
            trace = SegmentTrace(
                sequence=segment.sequence,
                subframe=subframe,
                word=segment.word,
                lsb=segment.lsb,
                msb=segment.msb,
                width=segment.width if lo_hi_error is None else 0,
            )
            traces.append(trace)
            if lo_hi_error is not None:
                return _error_value(
                    parameter, occurrence.index, subframe, traces,
                    STATUS_INVALID_MAPPING,
                    f"segment #{segment.sequence}: {lo_hi_error}",
                )
            if not 1 <= subframe <= SUBFRAME_COUNT:
                return _error_value(
                    parameter, occurrence.index, subframe, traces,
                    STATUS_INVALID_MAPPING,
                    f"segment #{segment.sequence}: subframe {subframe} "
                    f"outside 1..{SUBFRAME_COUNT}",
                )
            if not 1 <= segment.word <= frame.wps:
                return _error_value(
                    parameter, occurrence.index, subframe, traces,
                    STATUS_INVALID_MAPPING,
                    f"segment #{segment.sequence}: word {segment.word} "
                    f"outside 1..{frame.wps}",
                )
            word_value = frame.word(subframe, segment.word)
            extracted = extract_bits(word_value, segment.lsb, segment.msb)
            trace.word_value = word_value
            trace.extracted_value = extracted
            trace.extracted_bits = format(extracted, f"0{segment.width}b")
            parts.append((extracted, segment.width))

        assembled, width = assemble_segments(parts)
        bits = format(assembled, f"0{width}b")
        return self._interpret(
            parameter, occurrence, subframe, traces, assembled, width, bits
        )

    def _interpret(
        self,
        parameter: ParameterDefinition,
        occurrence: ParameterOccurrence,
        subframe: int,
        traces: list[SegmentTrace],
        assembled: int,
        width: int,
        bits: str,
    ) -> EngineeringValue:
        rule = parameter.conversion
        ptype = parameter.parameter_type
        trace = DecodeTrace(
            parameter_id=parameter.id,
            occurrence_index=occurrence.index,
            subframe=subframe,
            segments=traces,
            assembled_bits=bits,
            assembled_value=assembled,
            bit_width=width,
        )

        decoded: int | float | None = None
        engineering: int | float | str | None = None
        unit = parameter.unit
        status = STATUS_VALID
        message: str | None = None

        if ptype in (TYPE_ANALOG_SIGNED, TYPE_ANALOG_UNSIGNED, TYPE_BCD):
            if rule.formula_type != "linear":
                status = STATUS_UNSUPPORTED_TYPE
                message = f"unsupported formula_type {rule.formula_type!r}"
            elif ptype == TYPE_BCD:
                try:
                    decoded = decode_bcd(assembled, width)
                except BcdError as exc:
                    status = STATUS_INVALID_BCD
                    message = str(exc)
                else:
                    engineering = apply_linear(decoded, rule.resolution, rule.offset)
                    trace.resolution = rule.resolution
                    trace.offset = rule.offset
            else:
                decoded = (
                    twos_complement(assembled, width)
                    if ptype == TYPE_ANALOG_SIGNED
                    else assembled
                )
                engineering = apply_linear(decoded, rule.resolution, rule.offset)
                trace.resolution = rule.resolution
                trace.offset = rule.offset
        elif ptype == TYPE_DISCRETE:
            decoded = assembled
            engineering = decode_discrete(
                assembled, parameter.true_state, parameter.false_state
            )
            unit = None
        elif ptype == TYPE_RAW:
            decoded = assembled
            engineering = assembled
        else:
            status = STATUS_UNSUPPORTED_TYPE
            message = f"unsupported parameter type {ptype!r}"

        if status == STATUS_VALID and isinstance(engineering, (int, float)):
            if parameter.minimum is not None and engineering < parameter.minimum:
                status = STATUS_OUT_OF_RANGE
                message = f"{engineering:g} below minimum {parameter.minimum:g}"
            elif parameter.maximum is not None and engineering > parameter.maximum:
                status = STATUS_OUT_OF_RANGE
                message = f"{engineering:g} above maximum {parameter.maximum:g}"

        trace.decoded_decimal = decoded
        trace.engineering_value = engineering
        trace.status = status
        trace.message = message
        return EngineeringValue(
            parameter_id=parameter.id,
            parameter_name=parameter.mnemonic,
            occurrence_index=occurrence.index,
            subframe=subframe,
            raw_bits=bits,
            raw_integer=assembled,
            decoded_decimal=decoded,
            engineering_value=engineering,
            unit=unit,
            status=status,
            trace=trace,
        )


def _error_value(
    parameter: ParameterDefinition,
    occurrence_index: int,
    subframe: int | None,
    traces: list[SegmentTrace],
    status: str,
    message: str,
) -> EngineeringValue:
    trace = DecodeTrace(
        parameter_id=parameter.id,
        occurrence_index=occurrence_index,
        subframe=subframe,
        segments=traces,
        status=status,
        message=message,
    )
    return EngineeringValue(
        parameter_id=parameter.id,
        parameter_name=parameter.mnemonic,
        occurrence_index=occurrence_index,
        subframe=subframe,
        raw_bits=None,
        raw_integer=None,
        decoded_decimal=None,
        engineering_value=None,
        unit=parameter.unit,
        status=status,
        trace=trace,
    )
