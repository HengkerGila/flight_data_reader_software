from arinc717_reader.dataframe.validator import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    error_count,
    validate_dataframe,
    warning_count,
)
from arinc717_reader.domain.dataframe import DataframeDefinition, DataframeMetadata
from arinc717_reader.domain.parameter import (
    ConversionRule,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterSegment,
)


def make_dataframe(parameters):
    return DataframeDefinition(
        metadata=DataframeMetadata(
            dataframe_name="TEST",
            wps=64,
            sync_words=[583, 1464, 2631, 3512],
        ),
        parameters=parameters,
    )


def seg(word, lsb=1, msb=12, subframes=(1,), sequence=1):
    return ParameterSegment(
        sequence=sequence, subframes=subframes, word=word, lsb=lsb, msb=msb
    )


def param(pid, occurrences, ptype="analog_unsigned", **kwargs):
    return ParameterDefinition(
        id=pid, mnemonic=pid.upper(), parameter_type=ptype,
        occurrences=occurrences, **kwargs,
    )


def test_demo_dataframe_is_clean(demo_dataframe):
    issues = validate_dataframe(demo_dataframe)
    assert error_count(issues) == 0
    # the SPARE parameter with unknown type is a warning, not an error
    assert any(i.rule_name == "semantic.type" for i in issues)


def test_word_outside_wps_is_error():
    df = make_dataframe([param("p", [ParameterOccurrence(1, [seg(word=65)])])])
    issues = validate_dataframe(df)
    assert any(
        i.rule_name == "structural.word_range" and i.severity == SEVERITY_ERROR
        for i in issues
    )


def test_bit_outside_range_is_error():
    df = make_dataframe([param("p", [ParameterOccurrence(1, [seg(2, lsb=0, msb=13)])])])
    issues = validate_dataframe(df)
    assert sum(i.rule_name == "structural.bit_range" for i in issues) == 2


def test_empty_mapping_is_error():
    df = make_dataframe([param("p", [])])
    issues = validate_dataframe(df)
    assert any(i.rule_name == "structural.mapping_empty" for i in issues)


def test_zero_resolution_is_error():
    df = make_dataframe(
        [
            param(
                "p",
                [ParameterOccurrence(1, [seg(2)])],
                conversion=ConversionRule(resolution=0.0),
            )
        ]
    )
    issues = validate_dataframe(df)
    assert any(i.rule_name == "structural.resolution" for i in issues)


def test_duplicate_occurrence_index_is_error():
    df = make_dataframe(
        [
            param(
                "p",
                [
                    ParameterOccurrence(1, [seg(2)]),
                    ParameterOccurrence(1, [seg(3)]),
                ],
            )
        ]
    )
    issues = validate_dataframe(df)
    assert any(i.rule_name == "mapping.occurrence_index" for i in issues)


def test_subframe_mismatch_is_error():
    df = make_dataframe(
        [
            param(
                "p",
                [
                    ParameterOccurrence(
                        1,
                        [
                            seg(2, subframes=(1,), sequence=1),
                            seg(3, subframes=(2,), sequence=2),
                        ],
                    )
                ],
            )
        ]
    )
    issues = validate_dataframe(df)
    assert any(i.rule_name == "mapping.subframe_consistency" for i in issues)


def test_overlap_between_parameters_is_warning_not_error():
    df = make_dataframe(
        [
            param("a", [ParameterOccurrence(1, [seg(2, lsb=1, msb=8)])]),
            param("b", [ParameterOccurrence(1, [seg(2, lsb=6, msb=12)])]),
        ]
    )
    issues = validate_dataframe(df)
    overlap = [i for i in issues if i.rule_name == "semantic.overlap"]
    assert len(overlap) == 1
    assert overlap[0].severity == SEVERITY_WARNING


def test_non_overlapping_bits_no_warning():
    df = make_dataframe(
        [
            param("a", [ParameterOccurrence(1, [seg(2, lsb=1, msb=6)])]),
            param("b", [ParameterOccurrence(1, [seg(2, lsb=7, msb=12)])]),
        ]
    )
    assert not any(
        i.rule_name == "semantic.overlap" for i in validate_dataframe(df)
    )


def test_min_greater_than_max_is_error():
    df = make_dataframe(
        [
            param(
                "p",
                [ParameterOccurrence(1, [seg(2)])],
                minimum=10.0,
                maximum=-10.0,
            )
        ]
    )
    issues = validate_dataframe(df)
    assert any(i.rule_name == "semantic.range" for i in issues)


def test_discrete_without_states_is_warning():
    df = make_dataframe(
        [param("p", [ParameterOccurrence(1, [seg(2, msb=1)])], ptype="discrete")]
    )
    issues = validate_dataframe(df)
    assert any(i.rule_name == "semantic.discrete_states" for i in issues)
    assert warning_count(issues) >= 1
