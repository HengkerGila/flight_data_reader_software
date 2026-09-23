"""Session recorder: subscribes to stream arrivals and the sample bus (spec v2 §26R)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ..sources.serial.subframe_assembler import SubframeArrival
from ..streaming.parameter_sample import ParameterSample
from .session import EventRecord, SessionHeader, SessionWriter, SubframeRecord

logger = logging.getLogger(__name__)


class SessionRecorder:
    def __init__(self, path: str | Path, header: SessionHeader):
        if not header.created:
            header.created = time.time()
        self._writer = SessionWriter(path, header)
        self.path = Path(path)
        self.header = header
        self.active = True

    def on_arrival(self, arrival: SubframeArrival) -> None:
        if not self.active or arrival.subframe == 0:
            return
        self._writer.write_subframe(
            SubframeRecord(
                t=arrival.timestamp,
                frame=arrival.frame_index,
                sf=arrival.subframe,
                state=arrival.state,
                words=arrival.words,
            )
        )

    def on_samples(self, samples: list[ParameterSample]) -> None:
        if self.active:
            self._writer.write_samples(samples)

    def on_event(self, timestamp: float, kind: str, message: str) -> None:
        if self.active:
            self._writer.write_event(EventRecord(timestamp, kind, message))

    def flush(self) -> None:
        self._writer.flush()

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        self._writer.close()
        logger.info(
            "event=session_recorded file=%s subframes=%d samples=%d duration=%.1fs",
            self.path,
            self._writer.subframes,
            self._writer.samples,
            self._writer.duration,
        )

    @property
    def subframes(self) -> int:
        return self._writer.subframes

    @property
    def samples(self) -> int:
        return self._writer.samples

    @property
    def duration(self) -> float:
        return self._writer.duration
