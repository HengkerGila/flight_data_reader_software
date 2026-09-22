import pytest

from arinc717_reader.decoder.parameter_decoder import ParameterDecoder
from arinc717_reader.domain.engineering import (
    STATUS_INVALID_BCD,
    STATUS_INVALID_MAPPING,
    STATUS_OUT_OF_RANGE,
    STATUS_UNSUPPORTED_TYPE,
    STATUS_VALID,
)
from arinc717_reader.domain.parameter import (
    ConversionRule,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterSegment,
)


def sample(values, parameter_id, occurrence=1, subframe=1):
    hits = [
        v
        for v in values
        if v.parameter_id == parameter_id
        and v.occurrence_index == occurrence
        and v.subframe == subframe
    ]
    assert len(hits) == 1, f"expected one sample, got {len(hits)}"
    return hits[0]


def test_signed_pitch_spec_example(demo_dataframe, demo_frame):
    # Spec §9 inspector example: SF1 word 4 = 3412 -> bits 12-3 -> -171 -> -30.096
    demo_frame.set_word(1, 4, 3412)
    values = ParameterDecoder().decode_frame(demo_frame, demo_dataframe)
    pitch = sample(values, "demo-pitch")
    assert pitch.status == STATUS_VALID
    assert pitch.raw_bits == "1101010101"
    assert pitch.raw_integer == 853
    assert pitch.decoded_decimal == -171
    assert pitch.engineering_value == pytest.approx(-30.096)
    assert pitch.unit == "deg"
    # trace exposes the full chain (spec §58)
    assert pitch.trace.segments[0].word_value == 3412
    assert pitch.trace.resolution == pytest.approx(0.176)


def test_multi_subframe_parameter_yields_one_sample_per_subframe(
    demo_dataframe, demo_frame
):
    demo_frame.set_word(1, 6, 1024)
    demo_frame.set_word(3, 6, 2048)
    values = ParameterDecoder().decode_frame(demo_frame, demo_dataframe)
    heading = [v for v in values if v.parameter_id == "demo-heading"]
    assert sorted(v.subframe for v in heading) == [1, 3]
    assert sample(values, "demo-heading", subframe=1).decoded_decimal == 1024
    assert sample(values, "demo-heading", subframe=3).decoded_decimal == 2048


def test_multi_segment_assembly_msb_first(demo_dataframe, demo_frame):
    # PRESS ALT: seg1 = word 154 bits 9..1 (most significant), seg2 = word 153
    # 40000 raw (10000 ft at 0.25): high 9 bits = 9, low 12 bits = 3136
    demo_frame.set_word(1, 154, 9)
    demo_frame.set_word(1, 153, 3136)
    values = ParameterDecoder().decode_frame(demo_frame, demo_dataframe)
    altitude = sample(values, "demo-altitude")
    assert altitude.raw_integer == 40000
    assert altitude.trace.bit_width == 21
    assert altitude.decoded_decimal == 40000
    assert altitude.engineering_value == pytest.approx(10000.0)


def test_multi_segment_negative_altitude(demo_dataframe, demo_frame):
    # raw -400 -> -100 ft: 21-bit two's complement split 9/12 = 511 / 3696
    demo_frame.set_word(1, 154, 511)
    demo_frame.set_word(1, 153, 3696)
    values = ParameterDecoder().decode_frame(demo_frame, demo_dataframe)
    altitude = sample(values, "demo-altitude")
    assert altitude.decoded_decimal == -400
    assert altitude.engineering_value == pytest.approx(-100.0)


def test_bcd_decode(demo_dataframe, demo_frame):
    demo_frame.set_word(2, 14, 0x245)
    values = ParameterDecoder().decode_frame(demo_frame, demo_dataframe)
    course = sample(values, "demo-course", subframe=2)
    assert course.status == STATUS_VALID
    assert course.decoded_decimal == 245
    assert course.engineering_value == pytest.approx(245.0)


def test_invalid_bcd_flagged_not_reinterpreted(demo_dataframe, demo_frame):
    demo_frame.set_word(2, 14, 0xABC)
    values = ParameterDecoder().decode_frame(demo_frame, demo_dataframe)
    course = sample(values, "demo-course", subframe=2)
    assert course.status == STATUS_INVALID_BCD
    assert course.engineering_value is None


def test_discrete_states_from_dataframe(demo_dataframe, demo_frame):
    demo_frame.set_word(1, 13, 0b01)  # gear bit set, GPWS bit clear
    values = ParameterDecoder().decode_frame(demo_frame, demo_dataframe)
    assert sample(values, "demo-gear").engineering_value == "DOWN"
    # active-low: raw 0 means WARNING
    assert sample(values, "demo-gpws").engineering_value == "WARNING"

    demo_frame.set_word(1, 13, 0b10)
    values = ParameterDecoder().decode_frame(demo_frame, demo_dataframe)
    assert sample(values, "demo-gear").engineering_value == "UP"
    assert sample(values, "demo-gpws").engineering_value == "NORMAL"


def test_unknown_type_reported_unsupported(demo_dataframe, demo_frame):
    values = ParameterDecoder().decode_frame(demo_frame, demo_dataframe)
    spare = sample(values, "demo-spare")
    assert spare.status == STATUS_UNSUPPORTED_TYPE


def test_out_of_range_flagged(demo_dataframe, demo_frame):
    demo_frame.set_word(1, 7, 4000)  # 1000 kt > max 450
    values = ParameterDecoder().decode_frame(demo_frame, demo_dataframe)
    ias = sample(values, "demo-ias")
    assert ias.status == STATUS_OUT_OF_RANGE
    assert ias.engineering_value == pytest.approx(1000.0)


def test_invalid_word_mapping(demo_frame):
    parameter = ParameterDefinition(
        id="bad-word",
        mnemonic="BAD WORD",
        parameter_type="analog_unsigned",
        conversion=ConversionRule(),
        occurrences=[
            ParameterOccurrence(
                index=1,
                segments=[
                    ParameterSegment(
                        sequence=1, subframes=(1,), word=300, lsb=1, msb=12
                    )
                ],
            )
        ],
    )
    values = ParameterDecoder().decode_parameter(demo_frame, parameter)
    assert values[0].status == STATUS_INVALID_MAPPING
    assert "300" in values[0].trace.message


def test_unmapped_parameter(demo_frame):
    parameter = ParameterDefinition(id="empty", mnemonic="EMPTY")
    values = ParameterDecoder().decode_parameter(demo_frame, parameter)
    assert len(values) == 1
    assert values[0].status == STATUS_INVALID_MAPPING


def test_segments_disagreeing_on_subframes(demo_frame):
    parameter = ParameterDefinition(
        id="mismatch",
        mnemonic="MISMATCH",
        parameter_type="analog_unsigned",
        occurrences=[
            ParameterOccurrence(
                index=1,
                segments=[
                    ParameterSegment(sequence=1, subframes=(1,), word=2, lsb=1, msb=6),
                    ParameterSegment(sequence=2, subframes=(2,), word=3, lsb=1, msb=6),
                ],
            )
        ],
    )
    values = ParameterDecoder().decode_parameter(demo_frame, parameter)
    assert values[0].status == STATUS_INVALID_MAPPING


def test_decode_full_demo_frame_value_count(demo_dataframe, demo_frame):
    values = ParameterDecoder().decode_frame(demo_frame, demo_dataframe)
    # pitch 2 occ x4 SF + roll 4 + heading 2 + ias 4 + alt 4 + flap 2 + aoa 2
    # + aileron 4 + gear 4 + gpws 4 + course 1 + spare 1 = 40 samples
    assert len(values) == 40
