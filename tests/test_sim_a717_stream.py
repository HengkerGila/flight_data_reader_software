"""SIM-A717 v1 protocol, stream parser and synchronizer (spec v2 §26E, §26F, §53.7)."""

from __future__ import annotations

import pytest

from arinc717_reader.sources.serial import sim_a717_protocol as proto
from arinc717_reader.sources.serial.stream_parser import StreamParser
from arinc717_reader.sources.serial.synchronizer import (
    STATE_LOCKED,
    STATE_SEARCHING,
    StreamSynchronizer,
)

SYNC = [583, 1464, 2631, 3512]


def make_subframe(index: int, wps: int = 256, fill: int = 0x123) -> list[int]:
    words = [fill] * wps
    words[0] = SYNC[index - 1]
    return words


# -- word container --------------------------------------------------------


def test_word_container_is_16_bit_little_endian():
    assert proto.encode_word(0x0A5C) == b"\x5c\x0a"
    assert proto.encode_word(0) == b"\x00\x00"
    assert proto.encode_word(4095) == b"\xff\x0f"
    with pytest.raises(ValueError):
        proto.encode_word(4096)


def test_parser_recovers_byte_alignment_mid_stream():
    words = make_subframe(1) + make_subframe(2)
    data = proto.encode_words(words)
    parser = StreamParser(proto.MODE_STREAM)
    # Connect one byte late: the first pair is mis-aligned.
    result = parser.feed(data[1:])
    assert result.invalid_words >= 1
    # All following words must come out intact (allow the first to be lost).
    assert result.words[-len(words) + 2 :] == words[2:]


def test_parser_handles_partial_feeds():
    words = make_subframe(3)
    data = proto.encode_words(words)
    parser = StreamParser()
    out: list[int] = []
    for i in range(0, len(data), 7):  # odd chunk size crosses containers
        out.extend(parser.feed(data[i : i + 7]).words)
    assert out == words


# -- framed packets --------------------------------------------------------


def test_packet_round_trip_and_crc():
    packet = proto.SubframePacket(sequence=7, frame_id=3, subframe=2, wps=256, words=make_subframe(2))
    data = proto.encode_packet(packet)
    decoded = proto.decode_packet(data)
    assert decoded.sequence == 7 and decoded.frame_id == 3 and decoded.subframe == 2
    assert decoded.words == packet.words
    with pytest.raises(proto.PacketError):
        proto.decode_packet(proto.encode_packet(packet, corrupt_crc=True))


def test_framed_parser_skips_garbage_and_bad_crc():
    good = proto.encode_packet(proto.SubframePacket(1, 0, 1, 256, make_subframe(1)))
    bad = proto.encode_packet(proto.SubframePacket(2, 0, 2, 256, make_subframe(2)), corrupt_crc=True)
    parser = StreamParser(proto.MODE_FRAMED)
    result = parser.feed(b"junk" + good[:10])
    assert result.packets == []
    result = parser.feed(good[10:] + bad + good)
    assert [p.sequence for p in result.packets] == [1, 1]
    assert result.bad_packets >= 1


# -- command channel -------------------------------------------------------


def test_command_lines_and_replies():
    assert proto.command_line("SET_WPS", 256) == b"SET_WPS 256\n"
    assert proto.parse_reply("+OK PONG") == (True, "PONG")
    assert proto.parse_reply("+ERR unknown command X") == (False, "unknown command X")
    sf, words = proto.parse_load_subframe(["2", "247A5C000"])
    assert sf == 2 and words == [0x247, 0xA5C, 0]
    assert proto.load_subframe_line(1, [0x247, 0xA5C]) == b"LOAD_SF 1 247A5C\n"


# -- synchronizer ----------------------------------------------------------


def test_synchronizer_locks_and_emits_subframes_in_order():
    sync = StreamSynchronizer(256, SYNC)
    stream = []
    for sf in (3, 4, 1, 2, 3):
        stream += make_subframe(sf, fill=sf)
    # Start mid-subframe: 100 junk words before the first sync.
    result = sync.feed([0x555] * 100 + stream)
    assert result.locked_now
    assert sync.state == STATE_LOCKED
    # Every complete subframe is emitted as soon as its WPS words are in.
    assert [s.subframe for s in result.subframes] == [3, 4, 1, 2, 3]
    assert all(s.sync_valid for s in result.subframes)
    assert sync.expected_subframe == 4
    assert sync.words_in_subframe == 0


def test_synchronizer_reports_sync_loss_and_relocks():
    sync = StreamSynchronizer(256, SYNC)
    stream = make_subframe(1) + make_subframe(2)
    broken = make_subframe(3)
    broken[0] = 0  # corrupted sync word
    stream += broken + make_subframe(4) + make_subframe(1) + make_subframe(2)
    result = sync.feed(stream)
    assert result.lost_now
    invalid = [s for s in result.subframes if not s.sync_valid]
    assert [s.subframe for s in invalid] == [3]
    assert sync.state == STATE_LOCKED
    assert [s.subframe for s in result.subframes if s.sync_valid][-2:] == [1, 2]


def test_synchronizer_detects_wrong_wps():
    sync = StreamSynchronizer(256, SYNC)
    stream = []
    for sf in (1, 2, 3, 4, 1, 2):
        stream += make_subframe(sf, wps=128)
    result = sync.feed(stream)
    assert sync.state == STATE_SEARCHING
    assert result.detected_wps == 128
