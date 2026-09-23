"""Holds the live stream state shown on the Hardware page (spec v2 §26S).

Connection, protocol mode, device configuration, progress through the
current frame, diagnostics snapshot and the last stream events.  Written
only by the serial service (on the GUI thread); read by the UI.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..sources.serial.diagnostics import DiagnosticsSnapshot
from .base import Observable

CONN_DISCONNECTED = "DISCONNECTED"
CONN_CONNECTING = "CONNECTING"
CONN_CONNECTED = "CONNECTED"
CONN_STREAMING = "STREAMING"
CONN_RECONNECTING = "RECONNECTING"
CONN_REPLAYING = "REPLAYING"


@dataclass
class StreamEventRecord:
    timestamp: float
    kind: str
    message: str


class StreamStore(Observable):
    MAX_EVENTS = 200

    def __init__(self) -> None:
        super().__init__()
        self.connection = CONN_DISCONNECTED
        self.port: str = ""
        self.baudrate: int = 115200
        self.protocol_mode: str = "STREAM"
        self.device_info: str = ""
        self.wps: int | None = None
        self.frame_index: int | None = None
        self.subframe: int | None = None       # subframe currently being received
        self.word_progress: int = 0            # words received in that subframe
        self.subframe_states: list[str] | None = None
        self.diagnostics = DiagnosticsSnapshot()
        self.events: deque[StreamEventRecord] = deque(maxlen=self.MAX_EVENTS)
        self.last_error: str = ""
        self.source_kind: str = ""             # "serial" / "virtual" / "replay"
        self.signal_source: str = "engineering"  # "device" or "engineering"; set by the serial service
        self.replay_position: float | None = None
        self.replay_duration: float | None = None
        self.recording_path: str | None = None

    def set_connection(self, state: str, message: str = "") -> None:
        self.connection = state
        if message:
            self.last_error = message if state in (CONN_DISCONNECTED, CONN_RECONNECTING) else ""
        self._notify({"type": "stream", "field": "connection"})

    def set_progress(
        self,
        frame_index: int | None,
        subframe: int | None,
        word_progress: int,
        subframe_states: list[str] | None,
    ) -> None:
        self.frame_index = frame_index
        self.subframe = subframe
        self.word_progress = word_progress
        self.subframe_states = subframe_states
        self._notify({"type": "stream", "field": "progress"})

    def set_diagnostics(self, snapshot: DiagnosticsSnapshot) -> None:
        self.diagnostics = snapshot
        self._notify({"type": "stream", "field": "diagnostics"})

    def add_event(self, timestamp: float, kind: str, message: str) -> None:
        self.events.append(StreamEventRecord(timestamp, kind, message))
        self._notify({"type": "stream", "field": "event", "kind": kind})

    def set_replay_position(self, position: float | None, duration: float | None) -> None:
        self.replay_position = position
        self.replay_duration = duration
        self._notify({"type": "stream", "field": "replay"})

    def touch(self) -> None:
        self._notify({"type": "stream", "field": "config"})
