"""Review state machine and import session (spec §31.5, §32) — no PDF needed."""

import pytest

from arinc717_reader.dataframe.pdf_importer import ReviewError, build_session
from arinc717_reader.dataframe.pdf_importer.extract import RawParameterRow
from arinc717_reader.dataframe.pdf_importer.review import (
    STATE_APPROVED,
    STATE_EXTRACTED,
    STATE_NORMALIZED,
    STATE_PUBLISHED,
    STATE_REVIEW_REQUIRED,
    STATE_VALIDATED,
    ImportItem,
    can_transition,
)
from arinc717_reader.domain.parameter import (
    ConversionRule,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterSegment,
)


def raw(name, word="4", **fields):
    defaults = dict(
        parameter_name=name,
        parameter_type="Signed Analog",
        frequency="1",
        word_location=word,
        subframe="all",
        msb="12",
        lsb="3",
        units="deg",
        resolution="0.176",
        offset="0",
        page_number=1,
        table_index=1,
        row_index=1,
    )
    defaults.update(fields)
    return RawParameterRow(**defaults)


def rows():
    return [
        raw("PITCH"),
        raw("SPARE", word="15", parameter_type="Special"),  # unknown type → review
        raw("BROKEN", word="20", msb="", lsb=""),  # cannot be normalized
    ]


def test_transition_table():
    assert can_transition(STATE_EXTRACTED, STATE_NORMALIZED)
    assert can_transition(STATE_VALIDATED, STATE_APPROVED)
    assert can_transition(STATE_REVIEW_REQUIRED, STATE_APPROVED)
    assert not can_transition(STATE_EXTRACTED, STATE_APPROVED)
    assert not can_transition(STATE_PUBLISHED, STATE_APPROVED)
    item = ImportItem(index=0, parameter_id="pdf-0001", raw=raw("X"))
    with pytest.raises(ReviewError):
        item.transition(STATE_APPROVED, "skip ahead")
    item.transition(STATE_NORMALIZED, "ok")
    assert item.state == STATE_NORMALIZED and item.history[0].to_state == STATE_NORMALIZED


def test_session_settles_states_and_auto_approves_clean_rows():
    session = build_session(rows(), source_filename="doc.pdf")
    pitch, spare, broken = session.items
    assert pitch.state == STATE_APPROVED
    assert any("auto-approved" in e.note for e in pitch.history)
    assert spare.state == STATE_REVIEW_REQUIRED and spare.candidate is not None
    assert broken.state == STATE_REVIEW_REQUIRED and broken.candidate is None
    assert [e.to_state for e in broken.history] == [STATE_NORMALIZED, STATE_REVIEW_REQUIRED]
    assert session.state_counts()[STATE_REVIEW_REQUIRED] == 2
    assert [i.parameter_id for i in session.items] == ["pdf-0001", "pdf-0002", "pdf-0003"]


def test_publish_requires_every_included_row_approved():
    session = build_session(rows(), source_filename="doc.pdf", source_hash="abc")
    with pytest.raises(ReviewError, match="2 row"):
        session.publish()
    session.exclude(2)
    with pytest.raises(ReviewError, match="SPARE"):
        session.publish()
    session.approve(1)  # warnings only: explicit approval allowed
    dataframe = session.publish()
    assert [p.mnemonic for p in dataframe.parameters] == ["PITCH", "SPARE"]
    assert dataframe.metadata.source_type == "pdf"
    assert dataframe.metadata.source_filename == "doc.pdf"
    assert dataframe.metadata.source_hash == "abc"
    assert dataframe.metadata.sync_words == [583, 1464, 2631, 3512]
    assert session.items[0].state == STATE_PUBLISHED
    history = dataframe.parameters[0].provenance.extra["review_history"]
    assert history[-1].endswith("APPROVED → PUBLISHED: published")
    with pytest.raises(ReviewError, match="nothing to publish"):
        session.publish()


def test_approve_is_refused_with_validation_errors():
    session = build_session([raw("FAR", word="300")], wps=256)
    [item] = session.items
    assert item.state == STATE_REVIEW_REQUIRED
    assert [i.rule_name for i in item.validation_issues] == ["structural.word_range"]
    with pytest.raises(ReviewError, match="errors must be fixed"):
        session.approve(0)
    session.set_metadata(wps=512)
    assert item.state == STATE_VALIDATED and item.validation_issues == []
    session.approve(0)
    assert item.state == STATE_APPROVED


def test_metadata_change_unapproves_rows_that_break():
    session = build_session([raw("FAR", word="132")], wps=256)
    [item] = session.items
    assert item.state == STATE_APPROVED
    session.set_metadata(wps=64)
    assert item.state == STATE_REVIEW_REQUIRED
    assert "after approval" in item.history[-1].note
    session.set_metadata(wps=256)
    assert item.state == STATE_VALIDATED  # re-checked, but not silently re-approved
    with pytest.raises(ReviewError):
        session.set_metadata(wps=0)


def test_manual_edit_resolves_review():
    session = build_session(rows())
    broken = session.items[2]
    edited = ParameterDefinition(
        id="ignored",
        mnemonic="BROKEN FIXED",
        source_parameter_type="Signed Analog",
        parameter_type="analog_signed",
        conversion=ConversionRule(resolution=0.176),
        occurrences=[
            ParameterOccurrence(
                index=1,
                segments=[ParameterSegment(sequence=1, subframes=(1, 2, 3, 4), word=20, lsb=1, msb=12)],
            )
        ],
    )
    session.update_candidate(2, edited)
    assert broken.state == STATE_APPROVED
    assert broken.candidate.id == "pdf-0003"
    assert broken.normalization_issues == []
    assert any(e.note.startswith("resolved by edit") for e in broken.history)

    bad = ParameterDefinition(
        id="x",
        mnemonic="STILL BAD",
        parameter_type="analog_signed",
        occurrences=[
            ParameterOccurrence(
                index=1,
                segments=[ParameterSegment(sequence=1, subframes=(1,), word=999, lsb=1, msb=12)],
            )
        ],
    )
    session.update_candidate(2, bad)
    assert broken.state == STATE_REVIEW_REQUIRED
    assert broken.error_count == 1


def test_exclude_and_include():
    session = build_session(rows())
    session.exclude(0)
    assert session.items[0].excluded
    assert [p.mnemonic for p in session.preview_dataframe().parameters] == ["SPARE"]
    with pytest.raises(ReviewError):
        session.approve(0)
    session.include(0)
    assert not session.items[0].excluded and session.items[0].state == STATE_APPROVED


def test_validation_warnings_block_auto_approval_only():
    # Same word, same subframe: one overlap warning, attributed to both rows.
    session = build_session(
        [
            raw("A", word="4", subframe="1", frequency="0.25"),
            raw("B", word="4", subframe="1", frequency="0.25"),
        ]
    )
    assert [i.state for i in session.items] == [STATE_VALIDATED, STATE_VALIDATED]
    assert [i.warning_count for i in session.items] == [1, 1]
    assert session.items[1].validation_issues[0].rule_name == "semantic.overlap"
    assert session.approve_all_validated() == 2
    assert [i.state for i in session.items] == [STATE_APPROVED, STATE_APPROVED]
    assert session.pending_items() == []


def test_session_level_validation_issues():
    session = build_session([raw("A")], sync_words=[583, 1464])
    assert [i.rule_name for i in session.session_validation_issues] == ["structural.sync_words"]
    assert session.items[0].state == STATE_APPROVED  # not attributed to the row


def test_renormalize_keeps_exclusions_and_applies_conventions():
    from arinc717_reader.dataframe.pdf_importer import ImportProfile, renormalize_session

    session = build_session([raw("A", resolution="-40, 0.0195"), raw("B", word="5")])
    assert session.items[0].state == STATE_REVIEW_REQUIRED  # pair order not declared
    session.exclude(1)
    fresh = renormalize_session(session, ImportProfile(resolution_pair="offset_resolution"))
    assert fresh is not session
    assert fresh.items[0].state == STATE_APPROVED
    assert fresh.items[0].candidate.conversion.offset == -40
    assert fresh.items[1].excluded
