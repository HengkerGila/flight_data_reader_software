"""Subframe assembler: progressive frames and explicit slot states (spec v2 §26T)."""

from __future__ import annotations

from arinc717_reader.sources.serial.subframe_assembler import (
    SF_INVALID,
    SF_LATE,
    SF_MISSING,
    SF_PENDING,
    SF_RECEIVED,
    SubframeAssembler,
)

WPS = 64


def words(sf: int) -> list[int]:
    return [sf * 100 + i for i in range(WPS)]


def test_progressive_states_and_frame_completion():
    asm = SubframeAssembler(WPS)
    a1 = asm.take(1, words(1), 1.0)[0]
    assert a1.subframe_states == [SF_RECEIVED, SF_PENDING, SF_PENDING, SF_PENDING]
    assert a1.frame.subframes[0] == words(1)
    assert a1.frame.subframes[1] == [0] * WPS
    assert not a1.frame_completed
    asm.take(2, words(2), 2.0)
    asm.take(3, words(3), 3.0)
    a4 = asm.take(4, words(4), 4.0)[0]
    assert a4.frame_completed
    assert a4.subframe_states == [SF_RECEIVED] * 4
    assert a4.frame.frame_index == 0
    assert asm.frame_index == 1
    assert asm.stats.frames == 1


def test_skipped_subframe_is_marked_missing():
    asm = SubframeAssembler(WPS)
    asm.take(1, words(1), 1.0)
    a3 = asm.take(3, words(3), 3.0)[0]
    assert a3.subframe_states == [SF_RECEIVED, SF_MISSING, SF_RECEIVED, SF_PENDING]
    assert asm.stats.missing_subframes == 1


def test_new_frame_closes_incomplete_frame():
    asm = SubframeAssembler(WPS)
    asm.take(1, words(1), 1.0)
    asm.take(2, words(2), 2.0)
    arrivals = asm.take(1, words(1), 5.0)  # SF1 again: device restarted a frame
    assert len(arrivals) == 2
    closed, fresh = arrivals
    assert closed.frame_completed and closed.subframe == 0
    assert closed.subframe_states == [SF_RECEIVED, SF_RECEIVED, SF_MISSING, SF_MISSING]
    assert fresh.frame_index == 1 and fresh.subframe_states[0] == SF_RECEIVED
    assert asm.stats.incomplete_frames == 1


def test_late_subframe_fills_missing_slot():
    asm = SubframeAssembler(WPS)
    asm.take(1, words(1), 1.0)
    asm.take(3, words(3), 3.0)  # SF2 missing
    a2 = asm.take(2, words(2), 3.5)[0]
    assert a2.state == SF_LATE
    assert a2.subframe_states == [SF_RECEIVED, SF_LATE, SF_RECEIVED, SF_PENDING]
    assert asm.stats.out_of_order == 1
    assert asm.stats.missing_subframes == 0


def test_invalid_length_and_out_of_range_words():
    asm = SubframeAssembler(WPS)
    short = asm.take(1, words(1)[:-3], 1.0)[0]
    assert short.state == SF_INVALID
    bad = words(2)
    bad[5] = 5000
    a2 = asm.take(2, bad, 2.0)[0]
    assert a2.state == SF_INVALID
    assert (2, 6) in a2.invalid_words
    assert a2.frame.word(2, 6) == 5000 & 0xFFF


def test_framed_mode_frame_id_drives_frame_index():
    asm = SubframeAssembler(WPS)
    asm.take(1, words(1), 1.0, frame_id=41)
    assert asm.frame_index == 41
    arrivals = asm.take(1, words(1), 5.0, frame_id=42)
    assert arrivals[0].frame_completed and arrivals[0].frame_index == 41
    assert arrivals[1].frame_index == 42


def test_pending_slots_keep_previous_frame_words():
    """A live view shows each word's last known value; blanks only before any data."""
    asm = SubframeAssembler(WPS)
    first = asm.take(1, words(1), 1.0)[0]
    assert first.blank_subframes == frozenset({2, 3, 4})
    assert first.frame.subframes[1] == [0] * WPS
    for sf in (2, 3, 4):
        asm.take(sf, words(sf), float(sf))
    second = asm.take(1, words(11), 5.0)[0]  # frame 1, only SF1 so far
    assert second.blank_subframes == frozenset()
    assert second.frame.subframes[0] == words(11)
    assert second.frame.subframes[1] == words(2)  # carried from frame 0
    assert second.subframe_states == [SF_RECEIVED, SF_PENDING, SF_PENDING, SF_PENDING]
    asm.reset(0)  # a stop / start keeps the last words as the baseline
    assert asm.blank_subframes == frozenset()
    asm.reset(0, keep_words=False)
    fresh = asm.take(1, words(1), 9.0)[0]
    assert fresh.blank_subframes == frozenset({2, 3, 4})
