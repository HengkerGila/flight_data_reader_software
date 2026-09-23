"""Shared machinery for streaming sources (serial HIL and replay).

A streaming source produces ``StreamEvent`` objects on a thread-safe queue.
Subframes go through one ``SubframeAssembler`` so the serial path and the
replay path publish identical arrivals (spec v2 §26R: "replay must reuse
the normal decoder and graph pipeline").
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

from .serial.diagnostics import StreamDiagnostics
from .serial.subframe_assembler import SF_INVALID, SF_LATE, SF_MISSING, SubframeAssembler
from .stream_events import (
    EVENT_FRAME,
    EVENT_FRAME_INCOMPLETE,
    EVENT_SUBFRAME,
    EVENT_SUBFRAME_INCOMPLETE,
    StreamEvent,
)


class StreamSourceBase:
    name = "STREAM"

    def __init__(
        self,
        wps: int,
        clock: Callable[[], float] = time.time,
        diagnostics: StreamDiagnostics | None = None,
    ):
        self.wps = wps
        self.clock = clock
        self.events: queue.Queue[StreamEvent] = queue.Queue()
        self.diagnostics = diagnostics or StreamDiagnostics()
        self.assembler = SubframeAssembler(wps)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # -- events ----------------------------------------------------------

    def emit(self, kind: str, message: str = "", timestamp: float | None = None, **data) -> None:
        self.events.put(
            StreamEvent(
                kind=kind,
                timestamp=self.clock() if timestamp is None else timestamp,
                message=message,
                data=data,
            )
        )

    def deliver_subframe(
        self,
        subframe: int,
        words,
        timestamp: float,
        valid: bool = True,
        frame_id: int | None = None,
    ) -> None:
        """Push one subframe through the assembler and publish the arrivals."""
        arrivals = self.assembler.take(subframe, words, timestamp, valid=valid, frame_id=frame_id)
        for arrival in arrivals:
            if arrival.subframe == 0:
                kind = EVENT_FRAME_INCOMPLETE
            elif arrival.state in (SF_INVALID,):
                kind = EVENT_SUBFRAME_INCOMPLETE
            else:
                kind = EVENT_SUBFRAME
            self.events.put(
                StreamEvent(kind=kind, timestamp=timestamp, arrival=arrival, message=arrival.state)
            )
            if arrival.subframe != 0:
                self.diagnostics.add(subframes_received=1)
            if arrival.state == SF_INVALID:
                self.diagnostics.add(invalid_subframes=1)
            if arrival.state == SF_LATE:
                self.diagnostics.add(out_of_order_subframes=1)
            if arrival.frame_completed:
                self.diagnostics.add(frames_received=1)
                missing = sum(1 for s in arrival.subframe_states if s == SF_MISSING)
                if missing:
                    self.diagnostics.add(dropped_subframes=missing)
                self.events.put(
                    StreamEvent(kind=EVENT_FRAME, timestamp=timestamp, arrival=arrival)
                )

    # -- thread ----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_safely, name=f"{self.name}-source", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run_safely(self) -> None:
        try:
            self._run()
        finally:
            self._thread = None

    def _run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError
