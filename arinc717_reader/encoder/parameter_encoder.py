"""Parameter encoder for the scenario generator (design spec §24–§26).

engineering → inverse conversion → encoded decimal → binary encoding →
insert into mapped frame segments.  The split across segments mirrors the
decoder's assembly convention exactly (see ``segment_assembler``), which is
what makes closed-loop validation meaningful.
"""

from __future__ import annotations

from ..domain.frame import Arinc717Frame
from ..domain.parameter import (
    TYPE_ANALOG_SIGNED,
    TYPE_ANALOG_UNSIGNED,
    TYPE_BCD,
    TYPE_DISCRETE,
    TYPE_RAW,
    ParameterDefinition,
    ParameterOccurrence,
)
from ..decoder.bit_extractor import BitRangeError, normalize_bit_range
from ..decoder.conversion import ConversionError, invert_linear
from ..decoder.encoding.bcd import BcdError, encode_bcd
from ..decoder.encoding.discrete import DiscreteError, encode_discrete
from ..decoder.encoding.signed import SignedRangeError, to_twos_complement
from ..decoder.segment_assembler import AssemblyError, split_segments

ENCODABLE_TYPES = (
    TYPE_ANALOG_SIGNED,
    TYPE_ANALOG_UNSIGNED,
    TYPE_BCD,
    TYPE_DISCRETE,
    TYPE_RAW,
)


class EncodeError(ValueError):
    """Raised when a value cannot be encoded into a parameter's mapping."""


def is_encodable(parameter: ParameterDefinition) -> bool:
    """Whether the scenario simulator can safely encode this parameter."""
    if parameter.parameter_type not in ENCODABLE_TYPES:
        return False
    if not parameter.occurrences:
        return False
    if not all(occ.segments for occ in parameter.occurrences):
        return False
    if parameter.parameter_type in (TYPE_ANALOG_SIGNED, TYPE_ANALOG_UNSIGNED, TYPE_BCD):
        if parameter.conversion.formula_type != "linear":
            return False
        if parameter.conversion.resolution == 0:
            return False
    return True


def quantization_tolerance(parameter: ParameterDefinition) -> float:
    """Quantization-aware tolerance for closed-loop comparison (spec §26)."""
    if parameter.parameter_type in (TYPE_ANALOG_SIGNED, TYPE_ANALOG_UNSIGNED, TYPE_BCD):
        return abs(parameter.conversion.resolution) / 2 + 1e-9
    return 0.0


class ParameterEncoder:
    def encode_into_frame(
        self,
        frame: Arinc717Frame,
        parameter: ParameterDefinition,
        value: int | float | str | bool,
        occurrence_index: int | None = None,
        only_subframes=None,
    ) -> int:
        """Encode ``value`` into the frame; returns the raw bit pattern written.

        By default every occurrence (and every subframe of each segment) is
        written, so a subsequent decode of any sample returns the value.
        ``only_subframes`` restricts the write to those subframes, which is
        how the live simulator gives each subframe its own sample value.
        """
        occurrences = [
            occ
            for occ in parameter.occurrences
            if occurrence_index is None or occ.index == occurrence_index
        ]
        if not occurrences:
            raise EncodeError(
                f"{parameter.mnemonic}: no matching occurrence "
                f"(requested index {occurrence_index!r})"
            )
        pattern = 0
        selected = set(only_subframes) if only_subframes is not None else None
        for occurrence in occurrences:
            pattern = self._encode_occurrence(frame, parameter, occurrence, value, selected)
        return pattern

    def _encode_occurrence(
        self,
        frame: Arinc717Frame,
        parameter: ParameterDefinition,
        occurrence: ParameterOccurrence,
        value: int | float | str | bool,
        only_subframes: set[int] | None = None,
    ) -> int:
        segments = sorted(occurrence.segments, key=lambda s: s.sequence)
        if not segments:
            raise EncodeError(
                f"{parameter.mnemonic}: occurrence {occurrence.index} has no segments"
            )
        widths = [s.width for s in segments]
        total_width = sum(widths)
        pattern = self._value_to_pattern(parameter, value, total_width)
        try:
            parts = split_segments(pattern, widths)
        except AssemblyError as exc:
            raise EncodeError(f"{parameter.mnemonic}: {exc}") from exc
        for segment, part in zip(segments, parts):
            try:
                lo, _hi = normalize_bit_range(segment.lsb, segment.msb)
            except BitRangeError as exc:
                raise EncodeError(
                    f"{parameter.mnemonic}: segment #{segment.sequence}: {exc}"
                ) from exc
            mask = ((1 << segment.width) - 1) << (lo - 1)
            for subframe in segment.subframes:
                if only_subframes is not None and subframe not in only_subframes:
                    continue
                if not 1 <= segment.word <= frame.wps:
                    raise EncodeError(
                        f"{parameter.mnemonic}: word {segment.word} outside 1..{frame.wps}"
                    )
                old = frame.word(subframe, segment.word)
                frame.set_word(
                    subframe, segment.word, (old & ~mask) | (part << (lo - 1))
                )
        return pattern

    def _value_to_pattern(
        self,
        parameter: ParameterDefinition,
        value: int | float | str | bool,
        width: int,
    ) -> int:
        ptype = parameter.parameter_type
        rule = parameter.conversion
        try:
            if ptype in (TYPE_ANALOG_SIGNED, TYPE_ANALOG_UNSIGNED, TYPE_BCD):
                if rule.formula_type != "linear":
                    raise EncodeError(
                        f"{parameter.mnemonic}: unsupported formula_type "
                        f"{rule.formula_type!r}"
                    )
                raw = round(invert_linear(float(value), rule.resolution, rule.offset))
                if ptype == TYPE_ANALOG_SIGNED:
                    return to_twos_complement(raw, width)
                if ptype == TYPE_BCD:
                    return encode_bcd(raw, width)
                if raw < 0 or raw >> width:
                    raise EncodeError(
                        f"{parameter.mnemonic}: encoded decimal {raw} does not "
                        f"fit in {width} unsigned bits"
                    )
                return raw
            if ptype == TYPE_DISCRETE:
                return encode_discrete(
                    value, parameter.true_state, parameter.false_state
                )
            if ptype == TYPE_RAW:
                raw = int(value)
                if raw < 0 or raw >> width:
                    raise EncodeError(
                        f"{parameter.mnemonic}: raw value {raw} does not fit "
                        f"in {width} bits"
                    )
                return raw
        except EncodeError:
            raise
        except (
            SignedRangeError,
            BcdError,
            DiscreteError,
            ConversionError,
            TypeError,
            ValueError,
        ) as exc:
            raise EncodeError(f"{parameter.mnemonic}: {exc}") from exc
        raise EncodeError(
            f"{parameter.mnemonic}: cannot encode parameter type {ptype!r}"
        )
