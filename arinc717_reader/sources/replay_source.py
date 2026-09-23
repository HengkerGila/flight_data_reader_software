"""RecordedSessionSource: replays a session file (spec v2 §26R, Phase 12).

Recorded subframes are fed to the normal subframe assembler with their
original relative timing (scaled by ``speed``), so replay exercises exactly
the live decoding and graphing path.  Timestamps are the recorded wall
clock times, which keeps the graph's time axis identical to the original
session.
"""

from __future__ import annotations

import time

from ..recording.session import SessionReader, SubframeRecord
from .serial.subframe_assembler import SF_INVALID
from .stream_base import StreamSourceBase
from .stream_events import EVENT_REPLAY_ERROR, EVENT_REPLAY_FINISHED, EVENT_STREAM_STARTED


class RecordedSessionSource(StreamSourceBase):
    name = "REPLAY"

    def __init__(self, reader: SessionReader, speed: float = 1.0, realtime: bool = True):
        super().__init__(reader.header.wps)
        self.reader = reader
        self.speed = speed if speed > 0 else 1.0
        self.realtime = realtime
        self.position: float = 0.0     # seconds into the session
        self.duration: float = 0.0
        summary = reader.summary()
        if summary.get("first_t") is not None and summary.get("last_t") is not None:
            self.duration = float(summary["last_t"]) - float(summary["first_t"])
        self.paused = False

    def _run(self) -> None:
        self.emit(EVENT_STREAM_STARTED, f"replay {self.reader.path.name} at {self.speed:g}x")
        first_t: float | None = None
        start_mono = time.monotonic()
        try:
            for record in self.reader.subframes():
                if self._stop_event.is_set():
                    return
                if first_t is None:
                    first_t = record.t
                offset = record.t - first_t
                if self.realtime:
                    self._wait_until(start_mono, offset)
                    if self._stop_event.is_set():
                        return
                self.position = offset
                self._replay_record(record)
        except Exception as exc:  # malformed record etc.
            self.emit(EVENT_REPLAY_ERROR, str(exc))
            return
        self.emit(EVENT_REPLAY_FINISHED, "end of session")

    def _wait_until(self, start_mono: float, offset: float) -> None:
        while True:
            if self.paused:
                self._stop_event.wait(0.05)
                start_mono = time.monotonic() - offset / self.speed  # freeze position
                if self._stop_event.is_set():
                    return
                continue
            due = start_mono + offset / self.speed
            remaining = due - time.monotonic()
            if remaining <= 0:
                return
            if self._stop_event.wait(min(remaining, 0.05)):
                return

    def _replay_record(self, record: SubframeRecord) -> None:
        self.diagnostics.add(words_received=len(record.words), bytes_received=2 * len(record.words))
        self.deliver_subframe(
            record.sf,
            record.words,
            record.t,
            valid=record.state != SF_INVALID,
            frame_id=record.frame,
        )

    def replay_all(self) -> None:
        """Synchronous replay without timing (tests, batch analysis)."""
        self.stop()
        self._stop_event.clear()
        self.realtime = False
        self._run()
