"""ADB semantic round-trip and lossless preservation (spec §33–§37, §53.6)."""

import pytest

from arinc717_reader.dataframe.adb_codec import (
    AdbParseError,
    dataframe_to_adb_text,
    decode_subframe_selector,
    encode_subframe_selector,
    parse_adb_text,
)
from arinc717_reader.dataframe.adb_codec.legacy import legacy_setting_fields
from arinc717_reader.dataframe.adb_codec.mappings import SubframeSelectorError
from arinc717_reader.dataframe.compare import dataframe_differences

SAMPLE_ADB = (
    "Setting,1,57,9,12,256,0,583,1464,2631,3512,EXTRA1,EXTRA2\r\n"
    "PITCH ATT,Pitch attitude,Signed Analog,deg,0.176,0,-90,90,,,note x,"
    "1,1,1234,4,3,12,LEG7,TRAILA,TRAILB\r\n"
    'MULTI SEG,"Line one\r\nline two",BNR,ft,0.25,0,,,,,,'
    "1,2,1234,154,1,9,F1,1234,153,1,12,F2\r\n"
    "GEAR,Landing gear,Discrete,,1,0,,,DOWN,UP,,1,1,13,13,1,1,FLAGX\r\n"
    "ALLSF,Zero selector,Unsigned Analog,kt,0.25,0,,,,,,1,1,0,20,1,12,\r\n"
)


def test_subframe_selector_codec():
    # Spec §36 examples
    assert decode_subframe_selector("1") == (1,)
    assert decode_subframe_selector("2") == (2,)
    assert decode_subframe_selector("13") == (1, 3)
    assert decode_subframe_selector("24") == (2, 4)
    assert decode_subframe_selector("1234") == (1, 2, 3, 4)
    assert decode_subframe_selector("0") == (1, 2, 3, 4)  # provisional convention
    assert encode_subframe_selector((1, 3)) == "13"
    assert encode_subframe_selector((4, 2)) == "24"
    with pytest.raises(SubframeSelectorError):
        decode_subframe_selector("5")
    with pytest.raises(SubframeSelectorError):
        decode_subframe_selector("x")
    with pytest.raises(SubframeSelectorError):
        encode_subframe_selector(())


def test_parse_settings_record():
    dataframe = parse_adb_text(SAMPLE_ADB, source_filename="sample.adb")
    md = dataframe.metadata
    assert md.wps == 256
    assert md.sync_words == [583, 1464, 2631, 3512]
    assert md.source_type == "adb"
    # unknown header fields preserved verbatim, including the extra trailing ones
    assert md.adb_settings_raw[-2:] == ("EXTRA1", "EXTRA2")
    legacy = legacy_setting_fields(md)
    assert legacy["legacy_setting_1"] == "1"
    assert legacy["legacy_setting_2"] == "57"
    assert legacy["legacy_setting_11"] == "EXTRA1"


def test_parse_parameter_records():
    dataframe = parse_adb_text(SAMPLE_ADB, source_filename="sample.adb")
    assert len(dataframe.parameters) == 4

    pitch = dataframe.parameters[0]
    assert pitch.mnemonic == "PITCH ATT"
    assert pitch.parameter_type == "analog_signed"
    assert pitch.conversion.resolution == pytest.approx(0.176)
    assert pitch.minimum == -90 and pitch.maximum == 90
    assert pitch.notes == "note x"
    occurrence = pitch.occurrences[0]
    segment = occurrence.segments[0]
    assert segment.subframes == (1, 2, 3, 4)
    assert (segment.word, segment.lsb, segment.msb) == (4, 3, 12)
    assert segment.source_raw["legacy_flag"] == "LEG7"
    assert pitch.provenance.extra["trailing_fields"] == ["TRAILA", "TRAILB"]

    multi = dataframe.parameters[1]
    # embedded CRLF inside a quoted description must survive CSV parsing
    assert "Line one" in multi.description and "line two" in multi.description
    assert len(multi.occurrences[0].segments) == 2
    assert multi.parameter_type == "analog_unsigned"  # BNR default

    gear = dataframe.parameters[2]
    assert gear.parameter_type == "discrete"
    assert (gear.true_state, gear.false_state) == ("DOWN", "UP")

    allsf = dataframe.parameters[3]
    assert allsf.occurrences[0].segments[0].subframes == (1, 2, 3, 4)
    assert allsf.occurrences[0].segments[0].source_raw["subframe_selector_raw"] == "0"


def test_semantic_roundtrip_preserves_everything():
    # Spec §37: decode(existing) == decode(generated) at the semantic level
    first = parse_adb_text(SAMPLE_ADB, source_filename="sample.adb")
    regenerated = dataframe_to_adb_text(first)
    second = parse_adb_text(regenerated, source_filename="sample.adb")
    assert dataframe_differences(first, second) == []
    # raw selector text ("0") and unknown fields survive verbatim
    assert second.parameters[3].occurrences[0].segments[0].source_raw[
        "subframe_selector_raw"
    ] == "0"
    assert second.metadata.adb_settings_raw == first.metadata.adb_settings_raw
    assert second.parameters[0].provenance.extra["trailing_fields"] == [
        "TRAILA",
        "TRAILB",
    ]


def test_demo_dataframe_roundtrip(demo_dataframe):
    text = dataframe_to_adb_text(demo_dataframe)
    parsed = parse_adb_text(text, source_filename="demo_256wps.adb")
    assert dataframe_differences(demo_dataframe, parsed) == []
    assert parsed.metadata.wps == 256
    assert parsed.metadata.sync_words == [583, 1464, 2631, 3512]


def test_edited_sync_words_written_back():
    dataframe = parse_adb_text(SAMPLE_ADB, source_filename="sample.adb")
    dataframe.metadata.sync_words[0] = 999
    reparsed = parse_adb_text(dataframe_to_adb_text(dataframe))
    assert reparsed.metadata.sync_words == [999, 1464, 2631, 3512]
    # other unknown settings fields untouched
    assert reparsed.metadata.adb_settings_raw[-2:] == ("EXTRA1", "EXTRA2")


def test_uses_real_csv_parsing_not_split():
    quoted = (
        "Setting,1,57,9,12,64,0,1,2,3,4\r\n"
        'P1,"Contains, comma",BNR,,1,0,,,,,,1,1,1,5,1,12,\r\n'
    )
    dataframe = parse_adb_text(quoted)
    assert dataframe.parameters[0].description == "Contains, comma"
    assert dataframe.parameters[0].occurrences[0].segments[0].word == 5


def test_parse_errors_are_explicit():
    with pytest.raises(AdbParseError):
        parse_adb_text("")
    with pytest.raises(AdbParseError):
        parse_adb_text("NotSetting,1,2,3,4,5,6,7,8,9,10\r\n")
    with pytest.raises(AdbParseError):
        parse_adb_text("Setting,1,57,9,12,256,0,583,1464,2631,3512\r\nP1,,,,,,,,,,,1,1\r\n")
    with pytest.raises(AdbParseError):  # non-numeric WPS
        parse_adb_text("Setting,1,57,9,12,abc,0,583,1464,2631,3512\r\n")


def test_output_uses_crlf():
    text = dataframe_to_adb_text(parse_adb_text(SAMPLE_ADB))
    assert "\r\n" in text
    body_lines = [ln for ln in text.split("\r\n") if ln]
    assert body_lines[0].startswith("Setting,")
