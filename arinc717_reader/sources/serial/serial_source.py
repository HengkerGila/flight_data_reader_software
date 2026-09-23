"""SerialHardwareSource: COM port → canonical subframes (spec v2 §26F).

Reads the transport on a background thread, runs the SIM-A717 parser, the
synchronizer and the subframe assembler, and publishes ``StreamEvent``s.
Before the stream is started the link is in *text mode*: bytes are reply
lines to commands, and ``request()`` waits for them.  ``start_stream()``
switches to word parsing; ``stop_stream()`` switches back.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable

from ...domain.frame import SUBFRAME_COUNT
from ..stream_base import StreamSourceBase
from ..stream_events import (
    EVENT_DISCONNECTED,
    EVENT_INVALID_PROTOCOL,
    EVENT_RATE_MISMATCH,
    EVENT_REPLY,
    EVENT_STREAM_STARTED,
    EVENT_STREAM_STOPPED,
    EVENT_SYNC_LOCKED,
    EVENT_SYNC_LOST,
)
from . import sim_a717_protocol as proto
from .diagnostics import StreamDiagnostics
from .stream_parser import StreamParser
from .synchronizer import STATE_LOCKED, STATE_SEARCHING, StreamSynchronizer
from .transport import Transport, TransportError


class SerialHardwareSource(StreamSourceBase):
    name = "SERIAL"

    def __init__(
        self,
        transport: Transport,
        wps: int,
        sync_words,
        protocol_mode: str = proto.MODE_STREAM,
        clock: Callable[[], float] = time.time,
        diagnostics: StreamDiagnostics | None = None,
    ):
        super().__init__(wps, clock=clock, diagnostics=diagnostics)
        self.transport = transport
        self.protocol_mode = protocol_mode
        self.sync_words = list(sync_words)
        self._parser = StreamParser(protocol_mode)
        self._sync = StreamSynchronizer(wps, self.sync_words)
        self._text = bytearray()
        self._text_mode = True
        self._replies: queue.Queue[str] = queue.Queue()
        self._last_sequence: int | None = None
        self._rate_reported = False
        self.streaming = False

    # -- commands --------------------------------------------------------

    def send(self, data: bytes) -> None:
        try:
            self.transport.write(data)
        except TransportError as exc:
            self.emit(EVENT_DISCONNECTED, str(exc))
            raise

    def request(self, command: str, *args, timeout: float = 1.0) -> tuple[bool, str]:
        """Send a command and wait for its ``+OK``/``+ERR`` reply (text mode)."""
        if not self._text_mode:
            raise RuntimeError("replies are only available while the stream is stopped")
        while True:  # discard stale replies
            try:
                self._replies.get_nowait()
            except queue.Empty:
                break
        self.send(proto.command_line(command, *args))
        try:
            line = self._replies.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"no reply to {command} within {timeout:g} s") from exc
        return proto.parse_reply(line)

    def start_stream(self) -> None:
        self._parser.reset()
        self._sync.reset()
        self._text_mode = False
        self._last_sequence = None
        self._rate_reported = False
        self.streaming = True
        self.diagnostics.update(sync_state=STATE_SEARCHING, detected_wps=None)
        self.send(proto.command_line(proto.CMD_START))
        self.emit(EVENT_STREAM_STARTED, "START sent")

    def stop_stream(self) -> None:
        self.streaming = False
        try:
            self.send(proto.command_line(proto.CMD_STOP))
        finally:
            self._text_mode = True
            self._text.clear()
            self.emit(EVENT_STREAM_STOPPED, "STOP sent")

    # -- byte processing (also used synchronously by tests) --------------

    def process_bytes(self, data: bytes, now: float | None = None) -> None:
        if not data:
            return
        now = self.clock() if now is None else now
        self.diagnostics.add(bytes_received=len(data))
        if self._text_mode:
            self._process_text(data)
            return
        result = self._parser.feed(data)
        if result.invalid_words:
            self.diagnostics.add(invalid_words=result.invalid_words)
        if result.bad_packets:
            self.diagnostics.add(bad_packets=result.bad_packets)
            self.emit(EVENT_INVALID_PROTOCOL, f"{result.bad_packets} bad packet(s)", timestamp=now)
        if result.discarded_bytes:
            self.diagnostics.add(discarded_bytes=result.discarded_bytes)
        if result.words:
            self.diagnostics.add(words_received=len(result.words))
            self._process_words(result.words, now)
        for packet in result.packets:
            self._process_packet(packet, now)

    def _process_text(self, data: bytes) -> None:
        self._text.extend(data)
        while True:
            newline = self._text.find(b"\n")
            if newline < 0:
                if len(self._text) > 4096:
                    del self._text[:-256]
                return
            line = self._text[:newline].decode("ascii", errors="replace").strip()
            del self._text[: newline + 1]
            if line.startswith(proto.REPLY_OK) or line.startswith(proto.REPLY_ERR):
                self._replies.put(line)
                self.emit(EVENT_REPLY, line)
            # anything else is residue of a stopped word stream: ignore

    def _process_words(self, words: list[int], now: float) -> None:
        result = self._sync.feed(words)
        if result.locked_now:
            self.diagnostics.update(sync_state=STATE_LOCKED, detected_wps=None)
            self._rate_reported = False
            self.emit(EVENT_SYNC_LOCKED, f"locked at SF{self._sync.expected_subframe}", timestamp=now)
        for raw in result.subframes:
            self.deliver_subframe(raw.subframe, raw.words, now, valid=raw.sync_valid)
        if result.lost_now:
            self.diagnostics.add(sync_losses=1)
            self.diagnostics.update(sync_state=STATE_SEARCHING)
            self.emit(EVENT_SYNC_LOST, "sync word mismatch", timestamp=now)
        if result.detected_wps is not None and not self._rate_reported:
            self._rate_reported = True
            self.diagnostics.update(detected_wps=result.detected_wps)
            self.emit(
                EVENT_RATE_MISMATCH,
                f"sync words repeat every {result.detected_wps} words, expected {self.wps}",
                timestamp=now,
                detected_wps=result.detected_wps,
            )

    def _process_packet(self, packet: proto.SubframePacket, now: float) -> None:
        if packet.message_type != proto.MSG_SUBFRAME:
            return
        self.diagnostics.add(words_received=len(packet.words))
        # Sequence gaps need no accounting here: a skipped packet leaves a
        # MISSING slot in the assembler, which counts it as dropped.
        self._last_sequence = packet.sequence
        valid = (
            packet.wps == self.wps
            and 1 <= packet.subframe <= SUBFRAME_COUNT
            and len(packet.words) == self.wps
            and packet.words[0] == self.sync_words[packet.subframe - 1]
        )
        if packet.wps != self.wps and not self._rate_reported:
            self._rate_reported = True
            self.diagnostics.update(detected_wps=packet.wps)
            self.emit(
                EVENT_RATE_MISMATCH,
                f"packet WPS {packet.wps}, expected {self.wps}",
                timestamp=now,
                detected_wps=packet.wps,
            )
        if not 1 <= packet.subframe <= SUBFRAME_COUNT:
            self.diagnostics.add(invalid_subframes=1)
            return
        if self.diagnostics.snapshot().sync_state != STATE_LOCKED:
            self.diagnostics.update(sync_state=STATE_LOCKED)
            self.emit(EVENT_SYNC_LOCKED, "framed packets", timestamp=now)
        self.deliver_subframe(
            packet.subframe, packet.words, now, valid=valid, frame_id=packet.frame_id
        )

    # -- reader thread ---------------------------------------------------

    def _run(self) -> None:
        last_rate = time.monotonic()
        while not self._stop_event.is_set():
            try:
                data = self.transport.read(4096, timeout=0.05)
            except TransportError as exc:
                self.streaming = False
                self.emit(EVENT_DISCONNECTED, str(exc))
                return
            if data:
                self.process_bytes(data)
            mono = time.monotonic()
            if mono - last_rate >= 0.5:
                last_rate = mono
                self.diagnostics.refresh_rate()

    def close(self) -> None:
        self.stop()
        try:
            self.transport.close()
        except TransportError:
            pass
