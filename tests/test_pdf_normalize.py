"""PDF importer normalization rules (spec §28–§31.5, §54: no silent repair)."""

import pytest

from arinc717_reader.dataframe.pdf_importer.extract import (
    RawParameterRow,
    is_parameter_header,
    map_columns,
    normalize_header,
)
from arinc717_reader.dataframe.pdf_importer.normalize import (
    BITS_RANGE_PER_WORD,
    MULTIWORD_SEGMENTS,
    ImportProfile,
    infer_states,
    normalize_raw_row,
    parse_bit_tokens,
    parse_frequency,
    parse_int_list,
    parse_number,
    parse_subframe_field,
)
from arinc717_reader.domain.parameter import (
    TYPE_ANALOG_SIGNED,
    TYPE_ANALOG_UNSIGNED,
    TYPE_DISCRETE,
    TYPE_UNKNOWN,
)


def norm(profile=None, **fields):
    row = RawParameterRow(page_number=1, table_index=1, row_index=1, **fields)
    return normalize_raw_row(row, "pdf-0001", profile=profile, source_filename="doc.pdf")


def rules(result):
    return [(i.severity, i.rule) for i in result.issues]


PITCH = dict(
    parameter_name="PITCH ATT #1",
    parameter_type="Signed Analog",
    frequency="1",
    word_location="4",
    subframe="all",
    msb="12",
    lsb="3",
    units="deg",
    resolution="0.176",
    offset="0",
)


# -- text parsing -------------------------------------------------------------


def test_parse_int_list():
    assert parse_int_list("121") == [121]
    assert parse_int_list("121-124") == [121, 122, 123, 124]
    assert parse_int_list("122-121") == [122, 121]  # written order kept
    assert parse_int_list("4, 132") == [4, 132]
    assert parse_int_list("5/69/133/197") == [5, 69, 133, 197]
    assert parse_int_list("121 to 122") == [121, 122]
    assert parse_int_list("121 – 122") == [121, 122]  # en dash
    assert parse_int_list("4 and 132") == [4, 132]
    with pytest.raises(ValueError):
        parse_int_list("abc")
    with pytest.raises(ValueError):
        parse_int_list("")


def test_parse_bit_tokens():
    assert parse_bit_tokens("12") == [12]
    assert parse_bit_tokens("9/12") == [9, 12]
    assert parse_bit_tokens("12, 3") == [12, 3]
    assert parse_bit_tokens("9-1") == [(9, 1)]
    assert parse_bit_tokens("1-9") == [(9, 1)]
    assert parse_bit_tokens("12..1") == [(12, 1)]
    assert parse_bit_tokens("12:1") == [(12, 1)]
    with pytest.raises(ValueError):
        parse_bit_tokens("x")


def test_parse_number():
    assert parse_number("0.0625") == 0.0625
    assert parse_number("1/16") == 0.0625
    assert parse_number("2^-4") == 0.0625
    assert parse_number("−1.6") == -1.6  # unicode minus
    assert parse_number("1,000.5") == 1000.5
    assert parse_number("1E-3") == 0.001
    with pytest.raises(ValueError):
        parse_number("0,0625")  # decimal comma must be declared, not guessed
    assert parse_number("0,0625", decimal_comma=True) == 0.0625
    with pytest.raises(ValueError):
        parse_number("0.176 deg")


def test_parse_frequency():
    assert parse_frequency("1") == 1
    assert parse_frequency("4 Hz") == 4
    assert parse_frequency("1/4") == 0.25
    assert parse_frequency("0.5 sps") == 0.5
    assert parse_frequency("2 samples/sec") == 2


def test_parse_subframe_field():
    profile = ImportProfile()
    assert parse_subframe_field("all", profile) == ((1, 2, 3, 4), None)
    assert parse_subframe_field("1,3", profile)[0] == (1, 3)
    assert parse_subframe_field("1 & 3", profile)[0] == (1, 3)
    assert parse_subframe_field("13", profile)[0] == (1, 3)
    assert parse_subframe_field("2-4", profile)[0] == (2, 3, 4)
    assert parse_subframe_field("SF 2", profile)[0] == (2,)
    subframes, issue = parse_subframe_field("", profile)
    assert subframes == (1, 2, 3, 4) and issue.severity == "warning"
    assert parse_subframe_field("", ImportProfile(blank_subframe_means_all=True)) == (
        (1, 2, 3, 4),
        None,
    )
    subframes, issue = parse_subframe_field("0", profile)
    assert subframes == (1, 2, 3, 4) and issue.severity == "info"


def test_infer_states():
    assert infer_states("Gear lever (1 = UP, 0 = DOWN)") == ("UP", "DOWN")
    assert infer_states("1: WARNING; 0: NORMAL") == ("WARNING", "NORMAL")
    assert infer_states("no states here") == (None, None)


# -- header mapping -----------------------------------------------------------


def test_header_mapping_synonyms():
    headers = [
        "Parameter", "Description", "Type", "Freq (Hz)", "Word", "Subframe",
        "MSB", "LSB", "Units", "Resolution", "Offset", "Min", "Max", "1 =",
        "0 =", "Remarks",
    ]
    mapping = map_columns(headers)
    assert len(mapping) == 16 and is_parameter_header(mapping)
    assert mapping["frequency"] == 3 and mapping["true_state"] == 13
    assert normalize_header("Bit(s)") == "BITS"
    mapping = map_columns(["Mnemonic", "Word No.", "Bit(s)", "Sub-frame", "LSB Value"])
    assert mapping == {
        "parameter_name": 0,
        "word_location": 1,
        "bits": 2,
        "subframe": 3,
        "resolution": 4,
    }
    assert not is_parameter_header(map_columns(["Foo", "Bar", "Baz"]))
    assert "word_location" not in map_columns(["Parameter", "Slot Number", "Bits"])
    assert map_columns(["Parameter", "Slot Number", "Bits"], {"Slot Number": "word_location"})[
        "word_location"
    ] == 1


# -- row normalization ----------------------------------------------------------


def test_single_word_row_is_clean():
    result = norm(**PITCH)
    assert not result.needs_review and rules(result) == []
    p = result.parameter
    assert p.parameter_type == TYPE_ANALOG_SIGNED and p.unit == "deg"
    assert p.conversion.resolution == 0.176 and p.conversion.offset == 0
    [occurrence] = p.occurrences
    [segment] = occurrence.segments
    assert (segment.word, segment.lsb, segment.msb, segment.subframes) == (4, 3, 12, (1, 2, 3, 4))
    assert p.provenance.source_type == "pdf" and p.provenance.source_filename == "doc.pdf"
    assert p.provenance.extra["raw_fields"] == {}  # provenance dict is filled by extraction
    assert "layout" in p.provenance.extra["interpretation"]


def test_frequency_resolves_occurrences():
    result = norm(**{**PITCH, "word_location": "4, 132", "frequency": "2"})
    assert not result.needs_review
    occurrences = result.parameter.occurrences
    assert [o.index for o in occurrences] == [1, 2]
    assert [o.segments[0].word for o in occurrences] == [4, 132]
    assert all(len(o.segments) == 1 for o in occurrences)


def test_frequency_resolves_segments():
    result = norm(
        parameter_name="PRESS ALT",
        parameter_type="Signed Analog",
        frequency="1",
        word_location="154, 153",
        subframe="all",
        msb="9/12",
        lsb="1/1",
        units="ft",
        resolution="0.25",
    )
    assert not result.needs_review
    [occurrence] = result.parameter.occurrences
    assert [(s.sequence, s.word, s.msb, s.lsb) for s in occurrence.segments] == [
        (1, 154, 9, 1),
        (2, 153, 12, 1),
    ]


def test_half_rate_parameter_in_two_subframes():
    result = norm(**{**PITCH, "subframe": "1,3", "frequency": "0.5"})
    assert not result.needs_review
    assert result.parameter.occurrences[0].segments[0].subframes == (1, 3)


def test_multiword_without_frequency_needs_review():
    result = norm(**{**PITCH, "word_location": "4, 132", "frequency": ""})
    assert ("warning", "normalize.multiword") in rules(result)
    assert result.needs_review and not result.has_errors
    assert len(result.parameter.occurrences) == 2  # provisional reading still offered


def test_spec_example_range_columns_flagged_unless_declared():
    example = dict(
        parameter_name="Indicated Airspeed #1",
        parameter_type="BNR",
        frequency="1",
        word_location="121-122",
        msb="9-1",
        lsb="12-7",
        subframe="0",
        units="knots",
        resolution="0.0625",
    )
    result = norm(**example)
    assert ("warning", "normalize.bits_layout") in rules(result)
    assert result.needs_review
    p = result.parameter
    assert p.parameter_type == TYPE_ANALOG_UNSIGNED  # bare BNR is never assumed signed
    [occurrence] = p.occurrences
    assert [(s.word, s.msb, s.lsb) for s in occurrence.segments] == [(121, 9, 1), (122, 12, 7)]
    assert occurrence.segments[0].subframes == (1, 2, 3, 4)

    declared = norm(ImportProfile(multiword_bits=BITS_RANGE_PER_WORD), **example)
    assert not declared.needs_review
    assert all(i.severity == "info" for i in declared.issues)


def test_bits_column_variants():
    result = norm(**{**PITCH, "msb": "", "lsb": "", "bits": "12-3"})
    segment = result.parameter.occurrences[0].segments[0]
    assert (segment.msb, segment.lsb) == (12, 3) and not result.needs_review

    result = norm(**{**PITCH, "word_location": "4, 132", "frequency": "2", "msb": "", "lsb": "", "bits": "12-3"})
    assert not result.needs_review
    assert [o.segments[0].msb for o in result.parameter.occurrences] == [12, 12]

    result = norm(**{**PITCH, "bits": "12-3"})  # both given: bits wins, noted
    assert ("info", "normalize.bits_precedence") in rules(result) and not result.needs_review

    result = norm(**{**PITCH, "word_location": "4, 132", "frequency": "2", "msb": "", "lsb": "", "bits": "12-3/12-3/12-3"})
    assert result.has_errors and result.parameter is None


def test_frequency_mismatch_is_reported():
    result = norm(**{**PITCH, "frequency": "2"})  # 2 Hz but one word listed
    assert ("warning", "normalize.frequency") in rules(result)
    result = norm(**{**PITCH, "frequency": "1/64"})  # superframe rate
    assert ("error", "normalize.superframe") in rules(result) and result.has_errors
    result = norm(**{**PITCH, "frequency": "fast"})
    assert ("warning", "normalize.frequency") in rules(result)


def test_missing_bits_and_resolution_are_errors():
    result = norm(**{**PITCH, "msb": "", "lsb": ""})
    assert result.parameter is None and ("error", "normalize.bits_missing") in rules(result)
    result = norm(**{**PITCH, "resolution": ""})
    # conversion problems keep the candidate (the mapping is still reviewable)
    assert result.parameter is not None and result.has_errors
    assert ("error", "normalize.resolution") in rules(result)
    result = norm(**{**PITCH, "lsb": ""})
    assert result.parameter is None
    # discretes do not need a resolution
    result = norm(
        parameter_name="GEAR", parameter_type="Discrete", frequency="1",
        word_location="13", subframe="all", msb="1", lsb="1",
        true_state="DOWN", false_state="UP",
    )
    assert not result.needs_review and result.parameter.conversion.resolution == 1.0


def test_bad_word_and_subframe_are_errors():
    assert norm(**{**PITCH, "word_location": "four"}).has_errors
    assert norm(**{**PITCH, "subframe": "5"}).has_errors
    assert norm(**{**PITCH, "parameter_name": "  "}).has_errors
    assert norm(**{**PITCH, "offset": "abc"}).has_errors
    assert norm(**{**PITCH, "offset": "n/a"}).parameter.conversion.offset == 0  # placeholder


def test_sign_column():
    result = norm(**{**PITCH, "parameter_type": "BNR", "sign": "S"})
    assert result.parameter.parameter_type == TYPE_ANALOG_SIGNED
    assert result.parameter.source_parameter_type == "BNR (signed)"
    assert not result.needs_review
    result = norm(**{**PITCH, "sign": "U"})  # conflicts with "Signed Analog"
    assert ("warning", "normalize.sign_conflict") in rules(result)
    result = norm(**{**PITCH, "parameter_type": "BNR", "sign": "?"})
    assert ("warning", "normalize.sign_column") in rules(result)


def test_composite_type_text_and_unknown_type():
    result = norm(**{**PITCH, "parameter_type": "BNR (2's complement)"})
    assert result.parameter.parameter_type == TYPE_ANALOG_SIGNED and not result.needs_review
    result = norm(**{**PITCH, "parameter_type": "Special"})
    assert result.parameter.parameter_type == TYPE_UNKNOWN
    assert ("warning", "normalize.type") in rules(result) and result.needs_review


def test_discrete_states_inferred_from_text_need_review():
    result = norm(
        parameter_name="GEAR", parameter_type="Discrete", frequency="1",
        word_location="13", subframe="all", msb="1", lsb="1",
        description="Landing gear lever (1 = DOWN, 0 = UP)",
    )
    p = result.parameter
    assert p.parameter_type == TYPE_DISCRETE
    assert (p.true_state, p.false_state) == ("DOWN", "UP")
    assert ("warning", "normalize.states_inferred") in rules(result)


def test_low_to_high_bit_order_is_only_noted():
    result = norm(**{**PITCH, "msb": "3", "lsb": "12"})
    assert rules(result) == [("info", "normalize.bit_order")]
    assert result.parameter.occurrences[0].segments[0].bit_range == (3, 12)


def test_optional_numbers():
    result = norm(**{**PITCH, "minimum": "-90", "maximum": "ninety"})
    assert result.parameter.minimum == -90 and result.parameter.maximum is None
    assert ("warning", "normalize.maximum") in rules(result)


def test_profile_declared_multiword_meaning():
    result = norm(
        ImportProfile(multiword_meaning=MULTIWORD_SEGMENTS),
        **{**PITCH, "word_location": "4, 132", "frequency": ""},
    )
    assert not result.needs_review
    assert len(result.parameter.occurrences) == 1
    assert len(result.parameter.occurrences[0].segments) == 2


# -- FDS-style documents (stacked names, seconds, word pairs, pairs) ---------------

FDS_AOA = dict(
    mnemonic_and_name="AOAL\nLH Angle Of Attack",
    parameter_type="BNR",
    frequency="0.5",
    word_location="14-15,\n142-143",
    subframe="0 (all 4)",
    msb="9-1",
    lsb="12-9",
    units="-",
    resolution="0.044",
    true_state="-",
    false_state="-",
)
FDS_PROFILE = ImportProfile(
    frequency_unit="seconds", multiword_bits=BITS_RANGE_PER_WORD
)


def test_parse_word_groups():
    from arinc717_reader.dataframe.pdf_importer.normalize import parse_word_groups

    assert parse_word_groups("247") == [[247]]
    assert parse_word_groups("14-15, 142-143") == [[14, 15], [142, 143]]
    assert parse_word_groups("7, 71, 135,\n199") == [[7], [71], [135], [199]]
    assert parse_word_groups("154-153") == [[154, 153]]
    assert parse_word_groups("16.17") == [[16, 17]]  # OCR dot for dash


def test_fds_row_with_declared_conventions_is_clean():
    result = norm(FDS_PROFILE, **FDS_AOA)
    assert not result.needs_review, rules(result)
    p = result.parameter
    assert p.mnemonic == "AOAL" and p.description == "LH Angle Of Attack"
    assert p.unit is None and p.true_state is None and p.false_state is None
    assert [
        [(s.word, s.msb, s.lsb) for s in o.segments] for o in p.occurrences
    ] == [[(14, 9, 1), (15, 12, 9)], [(142, 9, 1), (143, 12, 9)]]
    assert p.occurrences[0].segments[0].subframes == (1, 2, 3, 4)
    assert "0.5 s interval → 2 Hz" in result.interpretation["frequency"]


def test_fds_row_without_declared_conventions_needs_review():
    result = norm(**FDS_AOA)  # frequency read as Hz, layout heuristic
    assert result.parameter is not None
    assert ("warning", "normalize.bits_layout") in rules(result)
    assert any(rule == "normalize.frequency" for _, rule in rules(result))


def test_session_detects_seconds_and_pair_layout():
    from arinc717_reader.dataframe.pdf_importer import build_session

    rows = [RawParameterRow(page_number=1, table_index=1, row_index=i, **FDS_AOA) for i in range(1, 5)]
    rows[1].word_location = "16-17, 144-145"
    rows[2].word_location = "18-19, 146-147"
    rows[3].word_location = "20-21, 148-149"
    session = build_session(rows)
    assert session.effective.frequency_unit == "seconds"
    assert session.effective.multiword_bits == BITS_RANGE_PER_WORD
    assert {issue.code for issue in session.issues} == {"PDF_FREQUENCY_UNIT", "PDF_BITS_LAYOUT"}
    # duplicate mnemonics qualified with the name; rows otherwise clean
    assert [item.candidate.mnemonic for item in session.items] == [
        "AOAL: LH Angle Of Attack"
    ] * 4
    assert all(not i.needs_review for item in session.items for i in item.normalization_issues)


def test_resolution_pair():
    result = norm(**{**PITCH, "resolution": "-40, 0.0195", "offset": ""})
    assert result.parameter.conversion.offset == -40
    assert result.parameter.conversion.resolution == 0.0195
    assert ("warning", "normalize.resolution_pair") in rules(result)
    declared = norm(ImportProfile(resolution_pair="offset_resolution"), **{**PITCH, "resolution": "3.5,0.01", "offset": ""})
    assert not declared.needs_review
    assert (declared.parameter.conversion.offset, declared.parameter.conversion.resolution) == (3.5, 0.01)
    swapped = norm(ImportProfile(resolution_pair="resolution_offset"), **{**PITCH, "resolution": "0.01, 3.5", "offset": ""})
    assert (swapped.parameter.conversion.offset, swapped.parameter.conversion.resolution) == (3.5, 0.01)
    conflict = norm(**{**PITCH, "resolution": "-40, 0.0195", "offset": "7"})
    assert ("warning", "normalize.offset_conflict") in rules(conflict)


def test_ocr_repairs_are_flagged():
    result = norm(**{**PITCH, "subframe": "。"})
    assert result.parameter.occurrences[0].segments[0].subframes == (1, 2, 3, 4)
    assert ("warning", "normalize.subframe_repaired") in rules(result)
    result = norm(**{**PITCH, "msb": "s", "lsb": "s"})
    assert (result.parameter.occurrences[0].segments[0].msb, result.parameter.occurrences[0].segments[0].lsb) == (5, 5)
    assert ("warning", "normalize.bits_repaired") in rules(result)
    result = norm(**{**PITCH, "resolution": "-- 1"})
    assert result.parameter.conversion.resolution == 1 and ("warning", "normalize.resolution_repaired") in rules(result)
    result = norm(**{**PITCH, "resolution": "3.5,0.Dl", "offset": ""})
    assert (result.parameter.conversion.offset, result.parameter.conversion.resolution) == (3.5, 0.01)
    assert ("warning", "normalize.resolution_repaired") in rules(result)
    result = norm(**{**PITCH, "word_location": "1S2-153", "frequency": "1", "msb": "9-1", "lsb": "12-7"})
    assert ("warning", "normalize.word_repaired") in rules(result)
    assert [s.word for s in result.parameter.occurrences[0].segments] == [152, 153]
    result = norm(**{**PITCH, "parameter_type": "Discret e", "resolution": ""})
    assert result.parameter.parameter_type == TYPE_DISCRETE
    assert ("warning", "normalize.type_repaired") in rules(result)
    result = norm(**{**PITCH, "parameter_type": "BCO"})
    assert result.parameter.parameter_type == "bcd"
    result = norm(**{**PITCH, "msb": "", "lsb": "", "bits": "9.1"})
    assert (result.parameter.occurrences[0].segments[0].msb, result.parameter.occurrences[0].segments[0].lsb) == (9, 1)


def test_bcd_without_resolution_is_not_an_error():
    result = norm(**{**PITCH, "parameter_type": "BCD", "resolution": ""})
    assert not result.has_errors and result.parameter.conversion.resolution == 1
    assert ("info", "normalize.resolution_default") in rules(result)


def test_discrete_states_and_width():
    wrapped = norm(
        parameter_name="AP Armed Mode", parameter_type="Discrete", frequency="1",
        word_location="178", subframe="all", msb="4", lsb="4",
        true_state="Alt Mode\nArmed", false_state="-",
    )
    assert not wrapped.needs_review
    assert wrapped.parameter.true_state == "Alt Mode Armed" and wrapped.parameter.false_state is None
    wide = norm(
        parameter_name="MFD #2 Format", parameter_type="Discrete", frequency="1",
        word_location="208-209", subframe="all", msb="8-1", lsb="12-5",
        true_state="0-0\n1-1\n2-2",
    )
    assert ("warning", "normalize.discrete_width") in rules(wide)
    assert wide.parameter.true_state is None and "States: 0-0; 1-1; 2-2" in wide.parameter.notes


def test_uneven_spacing_is_flagged():
    from arinc717_reader.dataframe.pdf_importer.normalize import normalize_raw_row

    row = RawParameterRow(**{**PITCH, "word_location": "7, 71, 135, 200", "frequency": "4"})
    result = normalize_raw_row(row, "pdf-0001", wps=256)
    assert ("warning", "normalize.spacing") in rules(result)
    row = RawParameterRow(**{**PITCH, "word_location": "7, 71, 135, 199", "frequency": "4"})
    assert not normalize_raw_row(row, "pdf-0001", wps=256).needs_review


def test_low_ocr_confidence_on_critical_fields():
    from arinc717_reader.dataframe.pdf_importer.raw import RawFieldProvenance

    row = RawParameterRow(**PITCH, text_source="ocr")
    row.provenance["word_location"] = RawFieldProvenance(confidence=0.4, raw_text="4", source="ocr")
    row.provenance["units"] = RawFieldProvenance(confidence=0.4, raw_text="deg", source="ocr")
    result = normalize_raw_row(row, "pdf-0001")
    flagged = [i for i in result.issues if i.rule == "normalize.ocr_confidence"]
    assert [i.field for i in flagged] == ["word_location"]  # units is not critical
