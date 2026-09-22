"""Dataframe editor: manual creation and mapping edits (spec §1.1, §27, §44)."""

import pytest

from arinc717_reader.app import build_context
from arinc717_reader.dataframe.adb_codec import dataframe_to_adb_text, parse_adb_text
from arinc717_reader.dataframe.compare import dataframe_differences
from arinc717_reader.dataframe.editor import (
    DEFAULT_SYNC_WORDS,
    SegmentRow,
    build_occurrences,
    candidate_issues,
    duplicate_parameter,
    format_subframes,
    make_parameter_id,
    new_dataframe,
    parse_subframes,
    segment_rows,
)
from arinc717_reader.domain.parameter import (
    TYPE_ANALOG_SIGNED,
    TYPE_DISCRETE,
    ConversionRule,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterSegment,
)
from arinc717_reader.services import ServiceError

# -- helpers -----------------------------------------------------------------


def test_parse_subframes_accepts_common_spellings():
    assert parse_subframes("1,3") == (1, 3)
    assert parse_subframes("3 1") == (1, 3)
    assert parse_subframes("13") == (1, 3)
    assert parse_subframes("2-4") == (2, 3, 4)
    assert parse_subframes("all") == (1, 2, 3, 4)
    assert parse_subframes("0") == (1, 2, 3, 4)
    assert parse_subframes("1234") == (1, 2, 3, 4)
    for bad in ("", "5", "1,9", "x", "1-", "0,2"):
        with pytest.raises(ValueError):
            parse_subframes(bad)
    assert format_subframes((4, 2)) == "2,4"


def test_make_parameter_id_is_unique_and_readable():
    assert make_parameter_id("PITCH ATT #1", []) == "man-pitch-att-1"
    assert make_parameter_id("PITCH ATT #1", ["man-pitch-att-1"]) == "man-pitch-att-1-2"
    assert make_parameter_id("   ", []) == "man-parameter"


def test_segment_rows_roundtrip_preserves_raw_fields():
    parameter = ParameterDefinition(
        id="x",
        mnemonic="X",
        occurrences=[
            ParameterOccurrence(
                index=1,
                segments=[
                    ParameterSegment(
                        sequence=1,
                        subframes=(1, 2, 3, 4),
                        word=20,
                        lsb=1,
                        msb=12,
                        source_raw={"subframe_selector_raw": "0", "legacy_flag": "F1"},
                    )
                ],
            )
        ],
    )
    rows = segment_rows(parameter)
    assert rows == [SegmentRow(1, 1, (1, 2, 3, 4), 20, 1, 12)]

    # unchanged subframes: raw selector text "0" survives
    same = build_occurrences(rows, previous=parameter)
    assert same[0].segments[0].source_raw == {
        "subframe_selector_raw": "0",
        "legacy_flag": "F1",
    }

    # changed subframes: selector text is dropped, legacy flag kept
    rows[0].subframes = (1, 3)
    changed = build_occurrences(rows, previous=parameter)
    assert changed[0].segments[0].source_raw == {"legacy_flag": "F1"}
    assert changed[0].segments[0].subframes == (1, 3)

    # a brand new row has no raw fields at all
    rows.append(SegmentRow(2, 1, (2,), 30, 1, 8))
    two = build_occurrences(rows, previous=parameter)
    assert [o.index for o in two] == [1, 2]
    assert two[1].segments[0].source_raw is None


def test_candidate_issues_preview_without_commit(demo_dataframe):
    pitch = demo_dataframe.get_parameter("demo-pitch")
    before = len(demo_dataframe.parameters)
    candidate = ParameterDefinition(
        id="new-one",
        mnemonic="NEW",
        parameter_type=TYPE_ANALOG_SIGNED,
        conversion=ConversionRule(resolution=0.0),
        occurrences=[
            ParameterOccurrence(
                index=1,
                segments=[ParameterSegment(1, (1,), 4, 3, 12)],  # overlaps pitch
            )
        ],
    )
    issues = candidate_issues(demo_dataframe, candidate)
    rules = {issue.rule_name for issue in issues}
    assert "structural.resolution" in rules
    assert "semantic.overlap" in rules
    assert any(pitch.mnemonic in issue.message for issue in issues)
    assert len(demo_dataframe.parameters) == before  # preview did not mutate


def test_duplicate_parameter_drops_provenance(demo_dataframe):
    pitch = demo_dataframe.get_parameter("demo-pitch")
    clone = duplicate_parameter(pitch, ["demo-pitch"])
    assert clone.id != pitch.id and clone.mnemonic == "PITCH ATT #1 COPY"
    assert clone.provenance is None
    assert clone.occurrences[0].segments[0] is not pitch.occurrences[0].segments[0]


# -- service -----------------------------------------------------------------


def _analog(mnemonic, word, lsb=1, msb=12, subframes=(1, 2, 3, 4), **kw):
    return ParameterDefinition(
        id="",
        mnemonic=mnemonic,
        source_parameter_type="Signed Analog",
        parameter_type=TYPE_ANALOG_SIGNED,
        unit="deg",
        conversion=ConversionRule(resolution=0.176, offset=0.0),
        occurrences=[
            ParameterOccurrence(
                index=1, segments=[ParameterSegment(1, tuple(subframes), word, lsb, msb)]
            )
        ],
        **kw,
    )


def test_manual_dataframe_lifecycle_and_export(tmp_path):
    ctx = build_context()
    events = []
    ctx.dataframe_store.subscribe(lambda e: events.append(e["type"]))

    ctx.dataframe_service.new_dataframe("MANUAL_64", 64, aircraft_type="TEST")
    store = ctx.dataframe_store
    assert store.dataframe.metadata.wps == 64
    assert store.dataframe.metadata.sync_words == list(DEFAULT_SYNC_WORDS)
    assert store.dataframe.metadata.source_type == "manual"
    assert store.dirty and store.issues == []

    pitch = ctx.dataframe_service.add_parameter(_analog("PITCH", 4, 3, 12))
    assert pitch.id == "man-pitch"
    gear = ctx.dataframe_service.add_parameter(
        ParameterDefinition(
            id="",
            mnemonic="GEAR",
            source_parameter_type="Discrete",
            parameter_type=TYPE_DISCRETE,
            true_state="DOWN",
            false_state="UP",
            occurrences=[
                ParameterOccurrence(index=1, segments=[ParameterSegment(1, (1,), 5, 1, 1)])
            ],
        )
    )
    assert [p.id for p in store.dataframe.parameters] == ["man-pitch", "man-gear"]

    # editing replaces by id and revalidates
    edited = _analog("PITCH", 99, 3, 12)  # word 99 > 64 WPS
    edited.id = pitch.id
    issues = ctx.dataframe_service.update_parameter(edited)
    assert any(i.rule_name == "structural.word_range" for i in issues)
    assert store.dataframe.get_parameter("man-pitch").occurrences[0].segments[0].word == 99

    ctx.dataframe_service.update_metadata(wps=128, sync_words=[1, 2, 3, 4])
    assert store.dataframe.metadata.wps == 128
    assert not any(i.rule_name == "structural.word_range" for i in store.issues)

    ctx.dataframe_service.duplicate_parameter(gear.id)
    ctx.dataframe_service.move_parameter("man-gear-copy", -2)
    assert [p.id for p in store.dataframe.parameters] == [
        "man-gear-copy",
        "man-pitch",
        "man-gear",
    ]
    ctx.dataframe_service.remove_parameter("man-gear-copy")
    assert [p.id for p in store.dataframe.parameters] == ["man-pitch", "man-gear"]

    # every edit fired a "dataframe" event (decoder + pages refresh on it)
    assert events.count("dataframe") >= 7

    # export → reparse is semantically identical, and clears the dirty flag
    out = tmp_path / "manual.adb"
    ctx.dataframe_service.export_adb(out)
    assert not store.dirty
    reparsed = parse_adb_text(out.read_text(), source_filename="manual.adb")
    assert dataframe_differences(store.dataframe, reparsed) == []
    assert reparsed.metadata.wps == 128 and reparsed.metadata.sync_words == [1, 2, 3, 4]
    assert reparsed.parameters[1].true_state == "DOWN"


def test_edits_redecode_current_frame():
    ctx = build_context()
    ctx.dataframe_service.new_dataframe("M", 64)
    ctx.frame_service.new_blank(with_sync_words=True)
    ctx.frame_service.set_word(1, 4, 3412)
    assert ctx.engineering_store.values == []

    ctx.dataframe_service.add_parameter(_analog("PITCH", 4, 3, 12, subframes=(1,)))
    values = ctx.engineering_store.values
    assert len(values) == 1
    assert abs(values[0].engineering_value - (-30.096)) < 1e-9

    ctx.dataframe_service.remove_parameter("man-pitch")
    assert ctx.engineering_store.values == []


def test_service_errors_are_explicit():
    ctx = build_context()
    with pytest.raises(ServiceError, match="MISSING_DATAFRAME"):
        ctx.dataframe_service.add_parameter(_analog("X", 1))
    with pytest.raises(ServiceError, match="INVALID_WPS"):
        ctx.dataframe_service.new_dataframe("bad", 0)
    ctx.dataframe_service.new_dataframe("M", 64)
    with pytest.raises(ServiceError, match="UNKNOWN_PARAMETER"):
        ctx.dataframe_service.remove_parameter("nope")
    with pytest.raises(ServiceError, match="INVALID_FIELD"):
        ctx.dataframe_service.update_metadata(source_hash="x")
    ctx.dataframe_service.add_parameter(_analog("X", 1))
    with pytest.raises(ServiceError, match="DUPLICATE_ID"):
        ctx.dataframe_service.add_parameter(
            ParameterDefinition(id="man-x", mnemonic="X2")
        )
    # a failed edit leaves the store untouched
    assert [p.id for p in ctx.dataframe_store.dataframe.parameters] == ["man-x"]


def test_editing_imported_adb_keeps_unknown_fields():
    text = (
        "Setting,1,57,9,12,256,0,583,1464,2631,3512,EXTRA1\r\n"
        "PITCH ATT,Pitch,Signed Analog,deg,0.176,0,-90,90,,,,1,1,0,4,3,12,LEG7,TRAIL\r\n"
    )
    ctx = build_context()
    ctx.dataframe_service.set_dataframe(parse_adb_text(text, source_filename="s.adb"))
    original = ctx.dataframe_store.dataframe.parameters[0]

    edited = ParameterDefinition(
        id=original.id,
        mnemonic="PITCH ATT",
        description="Pitch (edited)",
        source_parameter_type="Signed Analog",
        parameter_type=TYPE_ANALOG_SIGNED,
        unit="deg",
        conversion=ConversionRule(resolution=0.176),
        occurrences=build_occurrences(segment_rows(original), previous=original),
        provenance=original.provenance,
    )
    ctx.dataframe_service.update_parameter(edited)
    ctx.dataframe_service.update_metadata(dataframe_name="renamed")
    out = dataframe_to_adb_text(ctx.dataframe_store.dataframe)
    lines = out.split("\r\n")
    assert lines[0] == "Setting,1,57,9,12,256,0,583,1464,2631,3512,EXTRA1"
    assert lines[1].endswith(",1,1,0,4,3,12,LEG7,TRAIL")  # selector "0", flag, trailing
    assert ",Pitch (edited)," in lines[1]
