"""Events emitted by streaming sources (serial HIL, replay).

Sources run on their own thread and push these onto a queue; the serial
service drains the queue on the GUI thread and updates the stores.  The
event kinds double as the explicit stream states of spec v2 §47.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .serial.subframe_assembler import SubframeArrival

EVENT_CONNECTED = "CONNECTED"
EVENT_DISCONNECTED = "SERIAL_DISCONNECTED"
EVENT_PORT_ERROR = "SERIAL_PORT_ERROR"
EVENT_STREAM_STARTED = "STREAM_STARTED"
EVENT_STREAM_STOPPED = "STREAM_STOPPED"
EVENT_SYNC_LOCKED = "STREAM_SYNC_LOCKED"
EVENT_SYNC_LOST = "STREAM_SYNC_LOST"
EVENT_RATE_MISMATCH = "STREAM_RATE_MISMATCH"
EVENT_SUBFRAME = "SUBFRAME"
EVENT_SUBFRAME_INCOMPLETE = "SUBFRAME_INCOMPLETE"
EVENT_FRAME_INCOMPLETE = "FRAME_INCOMPLETE"
EVENT_FRAME = "FRAME"
EVENT_INVALID_PROTOCOL = "INVALID_SIM_PROTOCOL"
EVENT_REPLY = "REPLY"
EVENT_REPLAY_ERROR = "REPLAY_ERROR"
EVENT_REPLAY_FINISHED = "REPLAY_FINISHED"


@dataclass
class StreamEvent:
    kind: str
    timestamp: float
    message: str = ""
    arrival: SubframeArrival | None = None
    data: dict[str, Any] = field(default_factory=dict)
