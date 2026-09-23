"""Recording and replay service (spec v2 §26R, §46C).

Recording subscribes to the serial service's arrivals and to the sample
bus and writes a session file.  Replay builds a ``RecordedSessionSource``
and attaches it to the serial service, so the replayed subframes travel
the identical assembler → decoder → graph path as live data.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ..recording.recorder import SessionRecorder
from ..recording.session import SESSION_SUFFIX, SessionError, SessionHeader, SessionReader
from ..sources.replay_source import RecordedSessionSource
from ..state.dataframe_store import DataframeStore
from ..state.stream_store import StreamStore
from ..streaming.sample_bus import ParameterSampleBus
from . import ServiceError
from .serial_service import SerialService

logger = logging.getLogger(__name__)


class RecordingService:
    def __init__(
        self,
        dataframe_store: DataframeStore,
        stream_store: StreamStore,
        serial_service: SerialService,
        sample_bus: ParameterSampleBus,
    ):
        self._dataframe_store = dataframe_store
        self._stream_store = stream_store
        self._serial = serial_service
        self._bus = sample_bus
        self.recorder: SessionRecorder | None = None
        self.replay: RecordedSessionSource | None = None
        self.last_session_path: Path | None = None
        serial_service.arrival_listeners.append(self._on_arrival)
        serial_service.event_listeners.append(self._on_event)
        sample_bus.subscribe(self._on_samples)

    # -- recording -------------------------------------------------------

    @property
    def recording(self) -> bool:
        return self.recorder is not None and self.recorder.active

    def start_recording(self, path: str | Path, notes: str = "") -> SessionRecorder:
        if self.recording:
            raise ServiceError("RECORDING_ACTIVE", "a recording is already running")
        if self._serial.source is None:
            raise ServiceError("SERIAL_DISCONNECTED", "connect and start a stream (or replay) before recording")
        path = Path(path)
        if path.suffix != SESSION_SUFFIX:
            path = path.with_suffix(SESSION_SUFFIX)
        dataframe = self._dataframe_store.dataframe
        store = self._stream_store
        header = SessionHeader(
            created=time.time(),
            dataframe_name=dataframe.metadata.dataframe_name if dataframe else "",
            dataframe_source=dataframe.metadata.source_filename if dataframe else None,
            dataframe_hash=dataframe.metadata.source_hash if dataframe else None,
            wps=store.wps or (dataframe.metadata.wps if dataframe else 256),
            sync_words=list(dataframe.metadata.sync_words) if dataframe else [],
            source_type=store.source_kind,
            port=store.port,
            baudrate=store.baudrate,
            protocol_mode=store.protocol_mode,
            simulator={
                "signal_source": self._serial.signal_source,
                "device_mode": self._serial.device_mode,
                "device_info": store.device_info,
            },
            notes=notes,
        )
        try:
            self.recorder = SessionRecorder(path, header)
        except OSError as exc:
            raise ServiceError("RECORDING_ERROR", f"cannot write {path}: {exc}") from exc
        self.last_session_path = path
        store.recording_path = str(path)
        store.touch()
        logger.info("event=recording_started file=%s", path)
        return self.recorder

    def stop_recording(self) -> Path | None:
        recorder = self.recorder
        if recorder is None:
            return None
        recorder.stop()
        self.recorder = None
        self._stream_store.recording_path = None
        self._stream_store.touch()
        return recorder.path

    def _on_arrival(self, arrival) -> None:
        if self.recorder is not None:
            self.recorder.on_arrival(arrival)

    def _on_samples(self, samples) -> None:
        if self.recorder is not None:
            self.recorder.on_samples(samples)

    def _on_event(self, event) -> None:
        if self.recorder is not None:
            self.recorder.on_event(event.timestamp, event.kind, event.message)

    # -- replay ----------------------------------------------------------

    @property
    def replaying(self) -> bool:
        return self.replay is not None and self._serial.source is self.replay

    def open_session(self, path: str | Path) -> SessionReader:
        try:
            return SessionReader(path)
        except SessionError as exc:
            raise ServiceError("REPLAY_ERROR", str(exc)) from exc

    def start_replay(self, path: str | Path, speed: float = 1.0, realtime: bool = True) -> RecordedSessionSource:
        if self.recording:
            raise ServiceError("RECORDING_ACTIVE", "stop the recording before replaying")
        reader = self.open_session(path)
        dataframe = self._dataframe_store.dataframe
        if dataframe is not None and dataframe.metadata.wps != reader.header.wps:
            raise ServiceError(
                "REPLAY_ERROR",
                f"session was recorded at {reader.header.wps} WPS but the loaded "
                f"dataframe defines {dataframe.metadata.wps} WPS",
            )
        if dataframe is not None and reader.header.dataframe_name and (
            dataframe.metadata.dataframe_name != reader.header.dataframe_name
        ):
            logger.warning(
                "event=replay_dataframe_mismatch session=%s loaded=%s",
                reader.header.dataframe_name,
                dataframe.metadata.dataframe_name,
            )
        source = RecordedSessionSource(reader, speed=speed, realtime=realtime)
        self.replay = source
        self._serial.attach_source(source, "replay")
        self._stream_store.add_event(time.time(), "REPLAY_STARTED", f"{Path(path).name} at {speed:g}x")
        logger.info("event=replay_started file=%s speed=%s", path, speed)
        return source

    def pause_replay(self, paused: bool) -> None:
        if self.replay is not None:
            self.replay.paused = paused
            self._stream_store.touch()

    def stop_replay(self) -> None:
        if not self.replaying:
            return
        self._serial.detach_source()
        self.replay = None
        logger.info("event=replay_stopped")
