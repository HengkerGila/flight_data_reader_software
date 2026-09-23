"""SIM-A717 stream parser: bytes → words or packets (spec v2 §26F).

The parser is incremental: feed it whatever the transport returned and it
yields every complete unit found, keeping the remainder for the next call.

Continuous mode recovers byte alignment by itself.  A 16-bit container has
a zero upper nibble, so a mis-aligned pair (low byte read as the high byte)
is invalid most of the time; on an invalid container the parser discards
one byte and retries, converging within a few words.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .sim_a717_protocol import (
    CONTAINER_VALID_MASK,
    MODE_FRAMED,
    MODE_STREAM,
    PACKET_HEADER_BYTES,
    PACKET_MAGIC,
    PacketError,
    SubframePacket,
    container_is_valid,
    decode_packet,
    packet_length,
)


@dataclass
class ParseResult:
    words: list[int] = field(default_factory=list)
    packets: list[SubframePacket] = field(default_factory=list)
    invalid_words: int = 0
    bad_packets: int = 0
    discarded_bytes: int = 0


class StreamParser:
    def __init__(self, mode: str = MODE_STREAM):
        if mode not in (MODE_STREAM, MODE_FRAMED):
            raise ValueError(f"unknown stream mode {mode!r}")
        self.mode = mode
        self._buffer = bytearray()

    def reset(self) -> None:
        self._buffer.clear()

    @property
    def pending_bytes(self) -> int:
        return len(self._buffer)

    def feed(self, data: bytes) -> ParseResult:
        self._buffer.extend(data)
        if self.mode == MODE_STREAM:
            return self._parse_stream()
        return self._parse_framed()

    # -- continuous word stream -----------------------------------------

    def _parse_stream(self) -> ParseResult:
        result = ParseResult()
        buf = self._buffer
        pos = 0
        end = len(buf)
        while end - pos >= 2:
            (container,) = struct.unpack_from("<H", buf, pos)
            if container_is_valid(container):
                result.words.append(container & CONTAINER_VALID_MASK)
                pos += 2
            else:
                # Mis-aligned or corrupted: drop one byte and retry.
                result.invalid_words += 1
                result.discarded_bytes += 1
                pos += 1
        del buf[:pos]
        return result

    # -- framed packets --------------------------------------------------

    def _parse_framed(self) -> ParseResult:
        result = ParseResult()
        buf = self._buffer
        while True:
            start = buf.find(PACKET_MAGIC)
            if start < 0:
                # Keep the last byte: it may be the first byte of a magic.
                keep = 1 if buf and buf[-1:] == PACKET_MAGIC[:1] else 0
                result.discarded_bytes += len(buf) - keep
                del buf[: len(buf) - keep]
                return result
            if start:
                result.discarded_bytes += start
                del buf[:start]
            if len(buf) < PACKET_HEADER_BYTES:
                return result
            try:
                total = packet_length(bytes(buf[:PACKET_HEADER_BYTES]))
            except PacketError:
                result.bad_packets += 1
                result.discarded_bytes += 1
                del buf[:1]
                continue
            if len(buf) < total:
                return result
            try:
                packet = decode_packet(bytes(buf[:total]))
            except PacketError:
                result.bad_packets += 1
                result.discarded_bytes += 1
                del buf[:1]
                continue
            result.packets.append(packet)
            del buf[:total]
