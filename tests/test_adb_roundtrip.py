"""ADB codec against the verified AFDA layout (spec §33–§37, §53.6).

The fixture rows mirror records of the real vendor file (docs/06-adb-format.md);
the real files themselves are exercised when the git-ignored
``afda_adb_sample/`` folder is present.
"""

import copy
import csv
import io
from pathlib import Path

import pytest

from arinc717_reader.dataframe.adb_codec import (
    AdbParseError,
    AdbWriteError,
    dataframe_to_adb_text,
    decode_subframe_selector,
    encode_subframe_selector,
    format_adb_number,
    parse_adb_file,
    parse_adb_text,
    write_adb_file,
)
from arinc717_reader.dataframe.adb_codec.legacy import adb_warnings, legacy_setting_fields
from arinc717_reader.dataframe.adb_codec.mappings import (
    PARAM_RECORD_LENGTH,
    PARTS_ORDER,
    PARTS_ORDER_LS_FIRST,
    SubframeSelectorError,
)
from arinc717_reader.dataframe.compare import dataframe_differences
from arinc717_reader.decoder.parameter_decoder import ParameterDecoder
from arinc717_reader.domain.dataframe import DataframeDefinition, DataframeMetadata
from arinc717_reader.domain.frame import Arinc717Frame
from arinc717_reader.domain.parameter import (
    TYPE_ANALOG_UNSIGNED,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterSegment,
)
from arinc717_reader.encoder.parameter_encoder import ParameterEncoder

HEADER = "Setting,1,64,1,12,256,0,583,1464,2631,3512"
SAMPLE_DIR = Path(__file__).resolve().parents[1] / "afda_adb_sample"


def afda_record(
    name, description, type_, unit, minimum, maximum, scale, offset, decimals, kind,
    values=(), labels=(), rate=1, parts=1, locations=(),
):
    """A 238-field AFDA parameter record as a list of fields."""
    record = [""] * PARAM_RECORD_LENGTH
    record[0:10] = [name, description, type_, unit, minimum, maximum, scale, offset, decimals, kind]
    for slot, value in enumerate(values):
        record[10 + slot] = value
    for slot, label in enumerate(labels):
        record[42 + slot] = label
    record[74] = str(rate)
    record[75] = str(parts)
    for slot, (selector, word, lsb, msb) in enumerate(locations):
        base = 76 + 5 * slot
        record[base : base + 4] = [selector, str(word), str(lsb), str(msb)]
    return record


def afda_row(*args, **kwargs) -> str:
    return ",".join(afda_record(*args, **kwargs))


FIXTURE_ROWS = [
    afda_row("ACC LATERAL", "LATERAL ACCELEROMETER", "Unsigned Analog", "G", "-1", "1",
             "0.002", "-1.08", "0", "-", rate=4,
             locations=[("1234", 4, 1, 12), ("1234", 68, 1, 12), ("1234", 132, 1, 12), ("1234", 196, 1, 12)]),
    afda_row("21 ELEVATOR", "ELEVATOR", "Unsigned Analog", "deg", "-20", "30", "0.01221", "-20",
             "0", "-", rate=8,
             locations=[("1234", w, 1, 12) for w in (22, 54, 86, 118, 150, 182, 214, 246)]),
    afda_row("07 LATITUDE", "LATITUDE", "Signed Analog", "deg", "-90", "90", "1.7166137E-4",
             "0", "4", "-", parts=2, locations=[("1234", 30, 3, 12), ("1234", 29, 3, 12)]),
    afda_row("01 YEAR", "YEAR", "BCD", "Yr", "1", "2099", "1", "2000", "0", "-",
             values=("1", "10"), parts=2, locations=[("4", 19, 5, 8), ("4", 19, 9, 12)]),
    afda_row("GEAR LH", "LH WOW", "Discrete", "Status", "0", "1", "1", "0", "0", "Discrete",
             values=("0", "1"), labels=("ON AIR", "ON GROUND"), locations=[("1234", 248, 3, 3)]),
    afda_row("MODE SEL", "FOUR STATE MODE", "Discrete", "", "0", "3", "1", "0", "0", "Discrete",
             values=("0", "1", "2", "3"), labels=("OFF", "LOW", "MID", "HIGH"),
             locations=[("1234", 12, 1, 2)]),
    afda_row("47 TAT 1", "TOTAL AIR TEMP", "Signed Analog", "deg C", "-99", "99", "0.25", "0",
             "0", "-", locations=[("13", 204, 3, 12)]),
    afda_row("BETA LOCKOUT", "BETA LOCKOUT", "Discrete", "", "0", "1", "1", "0", "0", "",
             rate=2, locations=[("1234", 60, 1, 1)]),
]
FIXTURE = HEADER + "\r\n" + "\r\n".join(FIXTURE_ROWS) + "\r\n"


def _by_name(dataframe, name):
    return next(p for p in dataframe.parameters if p.mnemonic == name)


def test_settings_record():
    df = parse_adb_text(FIXTURE, source_filename="fixture.adb")
    md = df.metadata
    assert md.wps == 64  # index 2, not the 256 words per frame at index 5
    assert md.sync_words == [583, 1464, 2631, 3512]
    assert md.source_type == "adb" and md.dataframe_name == "fixture"
    assert legacy_setting_fields(md) == {
        "legacy_setting_1": "1",
        "legacy_setting_3": "1",
        "legacy_setting_6": "0",
    }


def test_multi_rate_samples_merge_into_subframe_tuples():
    df = parse_adb_text(FIXTURE)
    acc = _by_name(df, "ACC LATERAL")
    assert acc.parameter_type == "analog_unsigned"
    assert (acc.minimum, acc.maximum) == (-1, 1)
    assert acc.conversion.resolution == 0.002 and acc.conversion.offset == -1.08
    assert acc.decimals == 0
    assert len(acc.occurrences) == 1
    [segment] = acc.occurrences[0].segments
    assert segment.subframes == (1, 2, 3, 4)
    assert (segment.word, segment.lsb, segment.msb) == (4, 1, 12)

    elevator = _by_name(df, "21 ELEVATOR")  # 8 Hz: two words per subframe
    assert [(o.index, o.segments[0].word, o.segments[0].subframes) for o in elevator.occurrences] == [
        (1, 22, (1, 2, 3, 4)),
        (2, 54, (1, 2, 3, 4)),
    ]


def test_concatenated_parts_follow_parts_order():
    lat = _by_name(parse_adb_text(FIXTURE), "07 LATITUDE")
    assert lat.decimals == 4
    assert lat.conversion.resolution == pytest.approx(1.7166137e-4)
    [occurrence] = lat.occurrences
    words = [s.word for s in sorted(occurrence.segments, key=lambda s: s.sequence)]
    assert words == ([29, 30] if PARTS_ORDER == PARTS_ORDER_LS_FIRST else [30, 29])
    assert all(s.subframes == (1,) for s in occurrence.segments)  # word 30 ≤ 64, rate 1


def test_bcd_digit_weights_and_relative_selector():
    year = _by_name(parse_adb_text(FIXTURE), "01 YEAR")
    [occurrence] = year.occurrences
    segments = sorted(occurrence.segments, key=lambda s: s.sequence)
    assert [(s.subframes, s.word, s.lsb, s.msb, s.bcd_weight) for s in segments] == [
        ((4,), 19, 9, 12, 10.0),  # sequence 1 = most significant digit
        ((4,), 19, 5, 8, 1.0),
    ]
    frame = Arinc717Frame.blank(64)
    frame.set_word(4, 19, (2 << 8) | (6 << 4))  # tens = 2, ones = 6
    [value] = ParameterDecoder().decode_parameter(frame, year)
    assert value.engineering_value == 2026

    other = Arinc717Frame.blank(64)
    ParameterEncoder().encode_into_frame(other, year, 2031)
    [again] = ParameterDecoder().decode_parameter(other, year)
    assert again.engineering_value == 2031


def test_discrete_state_tables():
    df = parse_adb_text(FIXTURE)
    gear = _by_name(df, "GEAR LH")
    [segment] = gear.occurrences[0].segments
    assert (segment.subframes, segment.word, segment.lsb, segment.msb) == ((4,), 56, 3, 3)
    # a table the two labels can express is kept as the two labels only
    assert gear.states == []
    assert (gear.true_state, gear.false_state) == ("ON GROUND", "ON AIR")
    assert [(s.value, s.label) for s in gear.effective_states()] == [(0, "ON AIR"), (1, "ON GROUND")]

    mode = _by_name(df, "MODE SEL")
    assert [(s.value, s.label) for s in mode.states] == [(0, "OFF"), (1, "LOW"), (2, "MID"), (3, "HIGH")]
    frame = Arinc717Frame.blank(64)
    frame.set_word(1, 12, 2)
    [value] = ParameterDecoder().decode_parameter(frame, mode)
    assert value.engineering_value == "MID"


def test_ignored_selector_and_count_mismatch_are_warned():
    df = parse_adb_text(FIXTURE)
    tat = _by_name(df, "47 TAT 1")
    [segment] = tat.occurrences[0].segments
    assert (segment.subframes, segment.word) == ((4,), 12)
    assert any("selector '13' ignored" in w for w in adb_warnings(tat))

    beta = _by_name(df, "BETA LOCKOUT")
    assert len(beta.occurrences) == 1
    assert any("declares 2 sample(s)" in w for w in adb_warnings(beta))


def test_unchanged_parameters_round_trip_byte_for_byte():
    df = parse_adb_text(FIXTURE)
    assert dataframe_to_adb_text(df) == FIXTURE


def test_regenerated_records_are_semantically_equal():
    df = parse_adb_text(FIXTURE)
    stripped = copy.deepcopy(df)
    for parameter in stripped.parameters:
        parameter.provenance = None
    text = dataframe_to_adb_text(stripped)
    rows = list(csv.reader(io.StringIO(text)))
    assert all(len(row) == PARAM_RECORD_LENGTH for row in rows[1:])
    again = parse_adb_text(text)
    assert dataframe_differences(stripped, again, ignore=("notes",)) == []

    year = rows[4]  # SF4 word 19 → frame-absolute 211, digits least significant first
    assert year[76:80] == ["1234", "211", "5", "8"]
    assert year[81:85] == ["1234", "211", "9", "12"]
    assert year[10:12] == ["1", "10"]
    assert year[74:76] == ["1", "2"]
    assert rows[3][6] == "0.00017166137"  # no exponent notation
    assert rows[5][9:12] == ["Discrete", "0", "1"] and rows[5][42:44] == ["ON AIR", "ON GROUND"]


def test_edited_parameter_is_regenerated_others_kept():
    df = parse_adb_text(FIXTURE)
    df.parameters[0].description = "edited"
    lines = dataframe_to_adb_text(df).split("\r\n")
    assert lines[1] != FIXTURE_ROWS[0] and ",edited," in lines[1]
    assert lines[2:] == FIXTURE_ROWS[1:] + [""]


def test_demo_dataframe_exports_in_afda_layout(demo_dataframe):
    text = dataframe_to_adb_text(demo_dataframe)
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["Setting", "1", "256", "1", "12", "1024", "0", "583", "1464", "2631", "3512"]
    assert all(len(row) == PARAM_RECORD_LENGTH for row in rows[1:])
    parsed = parse_adb_text(text, source_filename="demo_256wps.adb")
    assert dataframe_differences(demo_dataframe, parsed, ignore=("notes",)) == []
    assert parsed.metadata.wps == 256


def test_too_many_samples_refused():
    fast = ParameterDefinition(
        id="x", mnemonic="FAST", source_parameter_type="Unsigned Analog",
        parameter_type=TYPE_ANALOG_UNSIGNED,
        occurrences=[
            ParameterOccurrence(index=i, segments=[ParameterSegment(1, (1, 2, 3, 4), i, 1, 12)])
            for i in range(1, 10)  # 9 words × 4 subframes = 36 samples
        ],
    )
    df = DataframeDefinition(
        metadata=DataframeMetadata(dataframe_name="x", wps=64, sync_words=[583, 1464, 2631, 3512]),
        parameters=[fast],
    )
    with pytest.raises(AdbWriteError, match="36 sample locations"):
        dataframe_to_adb_text(df)


def test_number_formatting_is_plain_decimal():
    assert format_adb_number(1.7166137e-4) == "0.00017166137"
    assert format_adb_number(0.25) == "0.25"
    assert format_adb_number(-20.0) == "-20"
    assert format_adb_number(0.0) == "0"
    assert format_adb_number(None) == ""


def test_windows_code_page_round_trip(tmp_path):
    row = afda_row("DON’T SINK", "DON’T SINK", "Discrete", "", "0", "1", "1", "0", "0",
                   "Discrete", values=("0", "1"), labels=("OFF", "ON"),
                   locations=[("1234", 100, 1, 1)])
    raw = (HEADER + "\r\n" + row + "\r\n").encode("cp1252")
    path = tmp_path / "quote.adb"
    path.write_bytes(raw)
    df = parse_adb_file(path)
    assert df.parameters[0].mnemonic == "DON’T SINK"
    out = tmp_path / "out.adb"
    write_adb_file(out, df)
    assert out.read_bytes() == raw


def test_quoted_fields_survive():
    record = afda_record("ZZ AILERON", "ROLL AXIS, SURFACE\r\nPOS", "Unsigned Analog", "deg",
                         "-30", "30", "0.1", "0", "1", "-", locations=[("1234", 40, 1, 12)])
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADER.split(","))
    writer.writerow(record)
    text = buffer.getvalue()
    df = parse_adb_text(text)
    assert df.parameters[0].description == "ROLL AXIS, SURFACE\r\nPOS"
    assert dataframe_to_adb_text(df) == text


def test_old_provisional_layout_is_rejected():
    with pytest.raises(AdbParseError, match="expected 238"):
        parse_adb_text(
            "Setting,1,57,9,12,256,0,583,1464,2631,3512\r\n"
            "PITCH,Pitch,Signed Analog,deg,0.176,0,-90,90,,,,1,1,1234,4,3,12,\r\n"
        )


def test_parse_errors_are_explicit():
    with pytest.raises(AdbParseError):
        parse_adb_text("")
    with pytest.raises(AdbParseError):
        parse_adb_text("NotSetting,1,2,3,4,5,6,7,8,9,10\r\n")
    with pytest.raises(AdbParseError):  # non-numeric WPS
        parse_adb_text("Setting,1,abc,1,12,256,0,583,1464,2631,3512\r\n")
    bad = afda_row("P", "", "Unsigned Analog", "", "0", "1", "1", "0", "0", "-",
                   locations=[("1234", 300, 1, 12)])
    with pytest.raises(AdbParseError, match="beyond the 256-word frame"):
        parse_adb_text(HEADER + "\r\n" + bad + "\r\n")


def test_subframe_selector_codec():
    assert decode_subframe_selector("1") == (1,)
    assert decode_subframe_selector("13") == (1, 3)
    assert decode_subframe_selector("24") == (2, 4)
    assert decode_subframe_selector("1234") == (1, 2, 3, 4)
    assert decode_subframe_selector("0") == (1, 2, 3, 4)  # editor / PDF convention
    assert encode_subframe_selector((1, 3)) == "13"
    assert encode_subframe_selector((4, 2)) == "24"
    with pytest.raises(SubframeSelectorError):
        decode_subframe_selector("5")
    with pytest.raises(SubframeSelectorError):
        decode_subframe_selector("x")
    with pytest.raises(SubframeSelectorError):
        encode_subframe_selector(())


def test_edited_sync_words_written_back():
    df = parse_adb_text(FIXTURE)
    df.metadata.sync_words[0] = 999
    again = parse_adb_text(dataframe_to_adb_text(df))
    assert again.metadata.sync_words == [999, 1464, 2631, 3512]
    assert again.metadata.adb_settings_raw[1:7] == ("1", "64", "1", "12", "256", "0")


def test_changed_wps_updates_words_per_frame():
    df = parse_adb_text(FIXTURE)
    df.metadata.wps = 128
    df.parameters = []
    assert dataframe_to_adb_text(df).split("\r\n")[0] == "Setting,1,128,1,12,512,0,583,1464,2631,3512"


@pytest.mark.parametrize("name", ["DATA FRAME NC212i.adb", "FDS.adb"])
def test_real_afda_files_round_trip(name):
    path = SAMPLE_DIR / name
    if not path.exists():
        pytest.skip("real AFDA samples are not present")
    raw = path.read_bytes()
    df = parse_adb_file(path)
    assert df.metadata.wps == 64
    assert dataframe_to_adb_text(df).encode("cp1252") == raw
    stripped = copy.deepcopy(df)
    for parameter in stripped.parameters:
        parameter.provenance = None
    again = parse_adb_text(dataframe_to_adb_text(stripped))
    assert dataframe_differences(stripped, again, ignore=("notes",)) == []


def test_real_vendor_file_contents():
    path = SAMPLE_DIR / "DATA FRAME NC212i.adb"
    if not path.exists():
        pytest.skip("real AFDA samples are not present")
    df = parse_adb_file(path)
    assert len(df.parameters) == 442
    year = _by_name(df, "01 YEAR")
    assert year.occurrences[0].segments[0].subframes == (4,)
    pitch = _by_name(df, "24 PITCH ATT 1")
    [segment] = pitch.occurrences[0].segments
    assert segment.subframes == (1, 2, 3, 4) and (segment.word, segment.lsb, segment.msb) == (7, 4, 12)
    baro = _by_name(df, "BARO CORR (Hg) R")
    weights = [s.bcd_weight for s in sorted(baro.occurrences[0].segments, key=lambda s: s.sequence)]
    assert weights == [10.0, 1.0, 0.1, 0.01]
