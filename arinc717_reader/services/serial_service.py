"""Serial / HIL service: owns the stream source and pumps its events (spec v2 §26F–§26J, §26S).

Responsibilities:

* open the transport (a COM port, or the in-process virtual device), run
  the SIM-A717 handshake and configure the device;
* start / stop the continuous stream; inject faults; choose the device
  signal mode or drive it with PC-generated engineering signals;
* ``pump()``: drain the source's event queue on the GUI thread, update the
  frame / stream stores and hand arrivals to the listeners (live decoding,
  recording);
* reconnect automatically after a disconnect.

Everything the source produces is a canonical subframe or frame; nothing
here knows about parameters except the optional engineering-signal upload,
which goes through the normal parameter encoder.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from ..domain.frame import SUBFRAME_COUNT
from ..encoder.frame_builder import build_blank_frame
from ..encoder.parameter_encoder import EncodeError, ParameterEncoder
from ..sources.serial import sim_a717_protocol as proto
from ..sources.serial.serial_source import SerialHardwareSource
from ..sources.serial.subframe_assembler import SubframeArrival
from ..sources.serial.transport import (
    VIRTUAL_PORT,
    PySerialTransport,
    Transport,
    TransportError,
    list_serial_ports,
)
from ..sources.serial.virtual_device import VirtualSimDevice
from ..sources.stream_base import StreamSourceBase
from ..sources.stream_events import (
    EVENT_DISCONNECTED,
    EVENT_FRAME,
    EVENT_FRAME_INCOMPLETE,
    EVENT_RATE_MISMATCH,
    EVENT_REPLAY_ERROR,
    EVENT_REPLAY_FINISHED,
    EVENT_SUBFRAME,
    EVENT_SUBFRAME_INCOMPLETE,
    EVENT_SYNC_LOST,
    StreamEvent,
)
from ..state.dataframe_store import DataframeStore
from ..state.frame_store import FrameStore
from ..state.stream_store import (
    CONN_CONNECTED,
    CONN_CONNECTING,
    CONN_DISCONNECTED,
    CONN_RECONNECTING,
    CONN_REPLAYING,
    CONN_STREAMING,
    StreamStore,
)
from ..streaming.signal_generator import ScenarioSignals, SignalConfig
from . import ServiceError

logger = logging.getLogger(__name__)

SIGNAL_SOURCE_DEVICE = "device"
SIGNAL_SOURCE_ENGINEERING = "engineering"
RECONNECT_INTERVAL_S = 0.5
HANDSHAKE_TIMEOUT_S = 1.5

ArrivalListener = Callable[[SubframeArrival], None]
EventListener = Callable[[StreamEvent], None]


class SerialService:
    def __init__(
        self,
        dataframe_store: DataframeStore,
        frame_store: FrameStore,
        stream_store: StreamStore,
        encoder: ParameterEncoder | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._dataframe_store = dataframe_store
        self._frame_store = frame_store
        self._stream_store = stream_store
        self._encoder = encoder or ParameterEncoder()
        self._clock = clock
        self.source: StreamSourceBase | None = None
        self.device: VirtualSimDevice | None = None
        self.signals: ScenarioSignals | None = None
        # Default: PC-generated engineering signals (random walk per
        # parameter, spec v2 §26H), so the live values and graphs are coherent
        # with the dataframe from the first frame; raw device modes are for
        # parser / stress testing.
        self.device_mode = proto.DEVICE_MODE_WALK
        self.signal_source = SIGNAL_SOURCE_ENGINEERING
        stream_store.signal_source = self.signal_source
        self.arrival_listeners: list[ArrivalListener] = []
        self.event_listeners: list[EventListener] = []
        self._want_connected = False
        self._want_streaming = False
        self._reconnect_due: float = 0.0
        self._stream_started_at: float | None = None
        self._last_upload_frame: int | None = None
        self._virtual_speed = 1.0
        # Parameters whose signal could not be encoded, reported once per
        # stream in the event log instead of once per frame.
        self._reported_encode_errors: set[str] = set()
        dataframe_store.subscribe(self._on_dataframe_event)

    # -- helpers ---------------------------------------------------------

    def _dataframe(self):
        dataframe = self._dataframe_store.dataframe
        if dataframe is None:
            raise ServiceError("MISSING_DATAFRAME", "load a dataframe first (WPS and sync words come from it)")
        if len(dataframe.metadata.sync_words) != SUBFRAME_COUNT:
            raise ServiceError("MISSING_DATAFRAME", "the dataframe must define four sync words")
        return dataframe

    def _on_dataframe_event(self, event: dict) -> None:
        if event.get("type") == "dataframe":
            self.signals = None
            self._last_upload_frame = None
            self._reported_encode_errors.clear()

    @staticmethod
    def available_ports() -> list[tuple[str, str]]:
        return [(VIRTUAL_PORT, "Virtual STM32 SIM-A717 device (in-process)")] + list_serial_ports()

    @property
    def connected(self) -> bool:
        return self._stream_store.connection in (CONN_CONNECTED, CONN_STREAMING)

    @property
    def streaming(self) -> bool:
        return self._stream_store.connection == CONN_STREAMING

    @property
    def replaying(self) -> bool:
        return self._stream_store.connection == CONN_REPLAYING

    # -- connection ------------------------------------------------------

    def connect(
        self,
        port: str,
        baudrate: int = 115200,
        protocol_mode: str = proto.MODE_STREAM,
        virtual_speed: float = 1.0,
    ) -> str:
        """Open the port, handshake with the device and configure it.

        Returns the device's INFO line.  Raises ``ServiceError`` with state
        ``SERIAL_PORT_ERROR`` when the port cannot be opened or no SIM-A717
        device answers.
        """
        if self.replaying:
            raise ServiceError("REPLAY_ACTIVE", "stop the replay before connecting")
        if self.source is not None:
            self.disconnect()
        dataframe = self._dataframe()
        store = self._stream_store
        store.port = port
        store.baudrate = baudrate
        store.protocol_mode = protocol_mode
        store.source_kind = "virtual" if port == VIRTUAL_PORT else "serial"
        store.wps = dataframe.metadata.wps
        self._virtual_speed = virtual_speed
        store.set_connection(CONN_CONNECTING)
        try:
            info = self._open_and_handshake()
        except ServiceError:
            store.set_connection(CONN_DISCONNECTED, "")
            self._teardown_source()
            raise
        self._want_connected = True
        store.device_info = info
        store.set_connection(CONN_CONNECTED)
        logger.info("event=serial_connected port=%s info=%s", port, info)
        return info

    def _open_transport(self) -> Transport:
        store = self._stream_store
        if store.port == VIRTUAL_PORT:
            if self.device is None:
                dataframe = self._dataframe()
                self.device = VirtualSimDevice(
                    wps=dataframe.metadata.wps,
                    sync_words=dataframe.metadata.sync_words,
                    speed=self._virtual_speed,
                )
                self.device.start_thread()
            try:
                return self.device.connect()
            except TransportError as exc:
                raise ServiceError("SERIAL_PORT_ERROR", str(exc)) from exc
        try:
            return PySerialTransport(store.port, store.baudrate)
        except TransportError as exc:
            raise ServiceError("SERIAL_PORT_ERROR", str(exc)) from exc

    def _open_and_handshake(self) -> str:
        dataframe = self._dataframe()
        transport = self._open_transport()
        source = SerialHardwareSource(
            transport,
            dataframe.metadata.wps,
            dataframe.metadata.sync_words,
            protocol_mode=self._stream_store.protocol_mode,
            clock=self._clock,
        )
        self.source = source
        source.start()
        try:
            self._request(proto.CMD_STOP)
            ok, reply = self._request(proto.CMD_PING)
            if not ok or reply != "PONG":
                raise ServiceError("INVALID_SIM_PROTOCOL", f"unexpected PING reply {reply!r}")
            self._request(proto.CMD_SET_WPS, dataframe.metadata.wps, must_succeed=True)
            self._request(proto.CMD_SET_SYNC, *dataframe.metadata.sync_words, must_succeed=True)
            self._request(proto.CMD_SET_PROTOCOL, self._stream_store.protocol_mode, must_succeed=True)
            self._apply_signal_source(initial=True)
            _ok, info = self._request(proto.CMD_INFO)
        except ServiceError:
            raise
        except (TimeoutError, TransportError) as exc:
            raise ServiceError(
                "SERIAL_PORT_ERROR", f"no SIM-A717 device answered on {transport.name}: {exc}"
            ) from exc
        return info

    def _request(self, command: str, *args, must_succeed: bool = False) -> tuple[bool, str]:
        source = self.source
        if not isinstance(source, SerialHardwareSource):
            raise ServiceError("SERIAL_DISCONNECTED", "not connected")
        try:
            ok, reply = source.request(command, *args, timeout=HANDSHAKE_TIMEOUT_S)
        except (TimeoutError, TransportError) as exc:
            raise ServiceError("SERIAL_DISCONNECTED", f"{command}: {exc}") from exc
        if must_succeed and not ok:
            raise ServiceError("INVALID_SIM_PROTOCOL", f"{command} rejected: {reply}")
        return ok, reply

    def _send(self, command: str, *args) -> None:
        """Fire-and-forget command (the device does not reply while streaming)."""
        source = self.source
        if not isinstance(source, SerialHardwareSource):
            raise ServiceError("SERIAL_DISCONNECTED", "not connected")
        try:
            source.send(proto.command_line(command, *args))
        except TransportError as exc:
            raise ServiceError("SERIAL_DISCONNECTED", str(exc)) from exc

    def send_raw(self, data: bytes) -> None:
        source = self.source
        if not isinstance(source, SerialHardwareSource):
            raise ServiceError("SERIAL_DISCONNECTED", "not connected")
        try:
            source.send(data)
        except TransportError as exc:
            raise ServiceError("SERIAL_DISCONNECTED", str(exc)) from exc

    def disconnect(self) -> None:
        self._want_connected = False
        self._want_streaming = False
        was_open = self.source is not None or self.device is not None
        if isinstance(self.source, SerialHardwareSource) and self.source.streaming:
            try:
                self.source.stop_stream()
            except TransportError:
                pass
        self._finish_live_frame()
        self._teardown_source()
        if self.device is not None:
            self.device.stop_thread()
            self.device.disconnect()
            self.device = None
        self._stream_store.set_connection(CONN_DISCONNECTED)
        if was_open:
            logger.info("event=serial_disconnected")

    def _teardown_source(self) -> None:
        source = self.source
        self.source = None
        if source is None:
            return
        source.stop()
        if isinstance(source, SerialHardwareSource):
            source.close()
        elif self.device is not None:
            self.device.disconnect()

    # -- streaming -------------------------------------------------------

    def start_stream(self) -> None:
        if not isinstance(self.source, SerialHardwareSource) or not self.connected:
            raise ServiceError("SERIAL_DISCONNECTED", "connect to a device first")
        if self.signal_source == SIGNAL_SOURCE_ENGINEERING:
            # The device is stopped, so it answers: use requests and drain the
            # replies before the parser switches to word mode.
            self._upload_engineering_frame(0, commit_now=True, use_request=True)
        self._stream_started_at = self._clock()
        self._last_upload_frame = None
        self._reported_encode_errors.clear()
        self.source.assembler.reset(0)
        try:
            self.source.start_stream()
        except TransportError as exc:
            raise ServiceError("SERIAL_DISCONNECTED", str(exc)) from exc
        self._want_streaming = True
        self._stream_store.set_connection(CONN_STREAMING)
        logger.info("event=stream_started")

    def stop_stream(self) -> None:
        self._want_streaming = False
        if isinstance(self.source, SerialHardwareSource) and self.source.streaming:
            try:
                self.source.stop_stream()
            except TransportError as exc:
                raise ServiceError("SERIAL_DISCONNECTED", str(exc)) from exc
        self._finish_live_frame()
        if self.connected or self._stream_store.connection == CONN_STREAMING:
            self._stream_store.set_connection(CONN_CONNECTED)
        logger.info("event=stream_stopped")

    def reset_device(self) -> None:
        if not self.connected:
            raise ServiceError("SERIAL_DISCONNECTED", "connect to a device first")
        was_streaming = self.streaming
        if was_streaming:
            self.stop_stream()
        self._request(proto.CMD_RESET, must_succeed=True)
        dataframe = self._dataframe()
        self._request(proto.CMD_SET_WPS, dataframe.metadata.wps, must_succeed=True)
        self._request(proto.CMD_SET_SYNC, *dataframe.metadata.sync_words, must_succeed=True)
        self._request(proto.CMD_SET_PROTOCOL, self._stream_store.protocol_mode, must_succeed=True)
        self._apply_signal_source(initial=True)
        if self.source is not None:
            self.source.assembler.reset(0)
            self.source.diagnostics.reset()
        self._stream_store.set_progress(None, None, 0, None)
        self._stream_store.add_event(self._clock(), "DEVICE_RESET", "device reset")

    def _finish_live_frame(self) -> None:
        """Turn the last live frame into a static one so it can be edited/decoded."""
        frame = self._frame_store.frame
        if frame is not None and self._frame_store.live:
            self._frame_store.set_frame(frame.copy(), self._frame_store.source_name)

    # -- device configuration --------------------------------------------

    def set_device_mode(self, mode: str) -> None:
        if mode not in proto.DEVICE_MODES:
            raise ServiceError("INVALID_SIM_PROTOCOL", f"unknown device mode {mode}")
        self.device_mode = mode
        if self.connected:
            self._send(proto.CMD_SET_MODE, mode)
        self._stream_store.touch()

    def set_signal_source(self, source: str) -> None:
        if source not in (SIGNAL_SOURCE_DEVICE, SIGNAL_SOURCE_ENGINEERING):
            raise ServiceError("INVALID_SIM_PROTOCOL", f"unknown signal source {source}")
        self.signal_source = source
        self._stream_store.signal_source = source
        if self.connected:
            self._apply_signal_source(initial=not self.streaming)
        self._stream_store.touch()

    def _apply_signal_source(self, initial: bool) -> None:
        if self.signal_source == SIGNAL_SOURCE_ENGINEERING:
            self._ensure_signals()
            if initial:
                self._request(proto.CMD_SET_MODE, proto.DEVICE_MODE_UPLOAD, must_succeed=True)
                self._upload_engineering_frame(0, commit_now=True, use_request=True)
            else:
                self._send(proto.CMD_SET_MODE, proto.DEVICE_MODE_UPLOAD)
        else:
            if initial:
                self._request(proto.CMD_SET_MODE, self.device_mode, must_succeed=True)
            else:
                self._send(proto.CMD_SET_MODE, self.device_mode)

    def _ensure_signals(self) -> ScenarioSignals:
        if self.signals is None:
            self.signals = ScenarioSignals(self._dataframe())
        return self.signals

    def signal_configs(self) -> dict[str, SignalConfig]:
        return {pid: gen.config for pid, gen in self._ensure_signals().generators.items()}

    def configure_signal(self, parameter_id: str, config: SignalConfig) -> None:
        try:
            self._ensure_signals().configure(parameter_id, config)
        except KeyError as exc:
            raise ServiceError("ENCODE_ERROR", f"{parameter_id} is not an encodable parameter") from exc

    def inject_fault(self, name: str, count: int = 1) -> None:
        if name not in proto.FAULTS:
            raise ServiceError("INVALID_SIM_PROTOCOL", f"unknown fault {name}")
        if not self.connected:
            raise ServiceError("SERIAL_DISCONNECTED", "connect to a device first")
        if self.streaming:
            self._send(proto.CMD_FAULT, name, count)
        else:
            self._request(proto.CMD_FAULT, name, count, must_succeed=True)
        self._stream_store.add_event(self._clock(), "FAULT_INJECTED", f"{name} x{count}")
        logger.info("event=fault_injected fault=%s count=%d", name, count)

    def set_word_on_device(self, subframe: int, word: int, value: int) -> None:
        if not self.connected:
            raise ServiceError("SERIAL_DISCONNECTED", "connect to a device first")
        if self.streaming:
            self._send(proto.CMD_SET_WORD, subframe, word, value)
        else:
            self._request(proto.CMD_SET_WORD, subframe, word, value, must_succeed=True)

    # -- engineering signal upload ---------------------------------------

    def build_engineering_frame(self, frame_index: int) -> tuple:
        """Frame for ``frame_index`` with per-subframe generator values.

        Subframe k of frame n covers simulation time 4n + (k-1) .. 4n + k.
        """
        dataframe = self._dataframe()
        signals = self._ensure_signals()
        frame = build_blank_frame(dataframe.metadata.wps, frame_index, dataframe.metadata.sync_words)
        errors: list[tuple[str, str]] = []  # (parameter id, message)
        base_t = frame_index * SUBFRAME_COUNT
        for subframe in range(1, SUBFRAME_COUNT + 1):
            values = signals.values_at(base_t + subframe - 1)
            for parameter_id, value in values.items():
                parameter = dataframe.get_parameter(parameter_id)
                if parameter is None:
                    continue
                try:
                    self._encoder.encode_into_frame(
                        frame, parameter, value, only_subframes={subframe}
                    )
                except EncodeError as exc:
                    errors.append((parameter_id, str(exc)))
        return frame, errors

    def _upload_engineering_frame(self, frame_index: int, commit_now: bool, use_request: bool = False) -> None:
        frame, errors = self.build_engineering_frame(frame_index)
        for parameter_id, message in errors:
            if parameter_id in self._reported_encode_errors:
                continue
            # Once per parameter per stream: the word keeps its previous /
            # blank value, which is visible in the decoded samples anyway.
            self._reported_encode_errors.add(parameter_id)
            logger.warning("event=signal_encode_error parameter=%s error=%s", parameter_id, message)
            self._stream_store.add_event(
                self._clock(), "ENCODE_ERROR", f"{message} (signal range outside the mapping; adjust Low/High)"
            )
        for subframe in range(1, SUBFRAME_COUNT + 1):
            words = frame.subframes[subframe - 1]
            if use_request:
                hexwords = "".join(f"{w:03X}" for w in words)
                self._request(proto.CMD_LOAD_SF, subframe, hexwords, must_succeed=True)
            else:
                self.send_raw(proto.load_subframe_line(subframe, words))
        if use_request:
            self._request(proto.CMD_COMMIT, must_succeed=True)
        else:
            self._send(proto.CMD_COMMIT)
        self._last_upload_frame = frame_index

    # -- replay attachment -----------------------------------------------

    def attach_source(self, source: StreamSourceBase, kind: str) -> None:
        """Use a non-serial source (replay) as the live stream."""
        if self.source is not None:
            self.disconnect()
        self.source = source
        store = self._stream_store
        store.source_kind = kind
        store.wps = source.wps
        store.port = ""
        store.set_connection(CONN_REPLAYING)
        source.start()

    def detach_source(self) -> None:
        self._finish_live_frame()
        self._teardown_source()
        self._stream_store.set_replay_position(None, None)
        self._stream_store.set_connection(CONN_DISCONNECTED)

    # -- pump ------------------------------------------------------------

    def pump(self) -> int:
        """Drain source events onto the stores; call from the GUI thread."""
        source = self.source
        handled = 0
        if source is None:
            self._maybe_reconnect()
            return 0
        while True:
            try:
                event = source.events.get_nowait()
            except Exception:
                break
            handled += 1
            self._handle_event(event)
        if self.source is source:
            self._update_progress(source)
        else:
            self._maybe_reconnect()
        return handled

    def _handle_event(self, event: StreamEvent) -> None:
        store = self._stream_store
        source_name = "REPLAY" if store.source_kind == "replay" else "SERIAL"
        if event.kind in (EVENT_SUBFRAME, EVENT_SUBFRAME_INCOMPLETE, EVENT_FRAME_INCOMPLETE):
            arrival = event.arrival
            if arrival is None:
                return
            self._frame_store.set_frame(
                arrival.frame,
                source_name,
                subframe_states=arrival.subframe_states,
                invalid_words=arrival.invalid_words,
                blank_subframes=arrival.blank_subframes,
            )
            if event.kind != EVENT_SUBFRAME:
                store.add_event(event.timestamp, event.kind, f"frame {arrival.frame_index} SF{arrival.subframe or '-'}: {arrival.state}")
            for listener in list(self.arrival_listeners):
                listener(arrival)
            if not self._want_streaming and not self.replaying:
                # A subframe already in flight when STOP was sent arrives
                # after the stream stopped: keep the data, but leave the
                # frame static so it stays editable.
                self._finish_live_frame()
            if (
                arrival.subframe == 1
                and self.signal_source == SIGNAL_SOURCE_ENGINEERING
                and self.streaming
                and self._last_upload_frame != arrival.frame_index + 1
            ):
                try:
                    self._upload_engineering_frame(arrival.frame_index + 1, commit_now=False)
                except ServiceError as exc:
                    store.add_event(event.timestamp, exc.state, str(exc))
            return
        if event.kind == EVENT_FRAME:
            return
        # State events
        store.add_event(event.timestamp, event.kind, event.message)
        for listener in list(self.event_listeners):
            listener(event)
        if event.kind == EVENT_DISCONNECTED:
            self._on_disconnected(event)
        elif event.kind in (EVENT_REPLAY_FINISHED, EVENT_REPLAY_ERROR):
            self.detach_source()
        elif event.kind in (EVENT_SYNC_LOST, EVENT_RATE_MISMATCH):
            logger.warning("event=%s message=%s", event.kind.lower(), event.message)

    def _on_disconnected(self, event: StreamEvent) -> None:
        self._finish_live_frame()
        self._teardown_source()
        if self._want_connected:
            self._stream_store.set_connection(CONN_RECONNECTING, event.message)
            self._reconnect_due = time.monotonic() + RECONNECT_INTERVAL_S
        else:
            self._stream_store.set_connection(CONN_DISCONNECTED, event.message)

    def _maybe_reconnect(self) -> None:
        if not self._want_connected or self.source is not None:
            return
        if self._stream_store.connection != CONN_RECONNECTING:
            return
        if time.monotonic() < self._reconnect_due:
            return
        self._reconnect_due = time.monotonic() + RECONNECT_INTERVAL_S
        try:
            info = self._open_and_handshake()
        except ServiceError as exc:
            self._teardown_source()
            self._stream_store.last_error = str(exc)
            return
        self._stream_store.device_info = info
        self._stream_store.set_connection(CONN_CONNECTED)
        self._stream_store.add_event(self._clock(), "RECONNECTED", info)
        logger.info("event=serial_reconnected")
        if self._want_streaming:
            try:
                self.start_stream()
            except ServiceError as exc:
                self._stream_store.add_event(self._clock(), exc.state, str(exc))

    def _update_progress(self, source: StreamSourceBase) -> None:
        store = self._stream_store
        assembler = source.assembler
        words = 0
        subframe: int | None = None
        if isinstance(source, SerialHardwareSource):
            sync = source._sync
            words = sync.words_in_subframe
            subframe = sync.expected_subframe if sync.state == "LOCKED" else None
        store.set_diagnostics(source.diagnostics.snapshot())
        store.set_progress(assembler.frame_index, subframe, words, assembler.subframe_states)
        if store.source_kind == "replay" and hasattr(source, "position"):
            store.set_replay_position(getattr(source, "position"), getattr(source, "duration", None))
