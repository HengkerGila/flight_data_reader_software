"""Stream Start / Pause toolbar.

Two buttons that control the incoming stream from any page:

* **Start stream** starts the live stream, connecting first (with the port,
  baud and protocol chosen on the Hardware page) when nothing is connected;
  during a paused replay it resumes the replay.
* **Pause stream** sends STOP to the device, so the Frame View holds still
  and becomes editable again; during a replay it pauses the replay.

Recording is not affected by either button: it records whatever arrives.
Start after a pause restarts the frame counter at 0.
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QPushButton, QWidget

from ..services import ServiceError
from ..state.stream_store import (
    CONN_CONNECTED,
    CONN_CONNECTING,
    CONN_DISCONNECTED,
    CONN_RECONNECTING,
    CONN_STREAMING,
)
from .common import monospace_font


class StreamControls(QWidget):
    def __init__(self, ctx, hardware_page, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._hardware_page = hardware_page
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._start = QPushButton("▶ Start stream")
        self._start.setToolTip(
            "Start the live stream. Connects first, with the port selected on the "
            "Hardware page, when nothing is connected yet. Resumes a paused replay."
        )
        self._start.clicked.connect(self.start)
        self._pause = QPushButton("❚❚ Pause stream")
        self._pause.setToolTip(
            "Stop the incoming stream so the Frame View holds still (and can be edited). "
            "Start stream resumes it. Pauses a running replay."
        )
        self._pause.clicked.connect(self.pause)
        self._state = QLabel("")
        self._state.setFont(monospace_font())
        layout.addWidget(self._start)
        layout.addWidget(self._pause)
        layout.addSpacing(12)
        layout.addWidget(self._state)
        layout.addStretch(1)
        ctx.stream_store.subscribe(lambda _event: self._refresh())
        self._refresh()

    # -- actions ---------------------------------------------------------

    def start(self) -> bool:
        """Start (or resume) the stream; returns True when it is running."""
        service = self._ctx.serial_service
        recording = self._ctx.recording_service
        if recording.replaying:
            recording.pause_replay(False)
            return True
        if not service.connected:
            self._hardware_page.connect_device()  # reports its own errors
            if not service.connected:
                return False
        if service.streaming:
            return True
        try:
            service.start_stream()
        except ServiceError as exc:
            QMessageBox.critical(self, "Start stream", str(exc))
            return False
        return True

    def pause(self) -> None:
        service = self._ctx.serial_service
        recording = self._ctx.recording_service
        if recording.replaying:
            recording.pause_replay(True)
            return
        if service.streaming:
            try:
                service.stop_stream()
            except ServiceError as exc:
                QMessageBox.critical(self, "Pause stream", str(exc))

    # -- rendering -------------------------------------------------------

    def _refresh(self) -> None:
        store = self._ctx.stream_store
        recording = self._ctx.recording_service
        state = store.connection
        replaying = recording.replaying
        replay_paused = replaying and recording.replay is not None and recording.replay.paused
        self._start.setEnabled(state in (CONN_DISCONNECTED, CONN_CONNECTED) or replay_paused)
        self._pause.setEnabled(state == CONN_STREAMING or (replaying and not replay_paused))
        if state == CONN_STREAMING:
            where = store.port or store.source_kind
            frame = f"{store.frame_index:06d}" if store.frame_index is not None else "—"
            subframe = f" SF{store.subframe}" if store.subframe else ""
            text = f"LIVE {where} · frame {frame}{subframe}"
        elif state == CONN_CONNECTED:
            text = f"PAUSED · connected to {store.port or store.source_kind}"
        elif state in (CONN_CONNECTING, CONN_RECONNECTING):
            text = state
        elif replaying:
            position = f"{store.replay_position:.1f}" if store.replay_position is not None else "0.0"
            total = f" / {store.replay_duration:.1f} s" if store.replay_duration else " s"
            text = f"REPLAY {'paused ' if replay_paused else ''}{position}{total}"
        else:
            text = "no stream · Start connects to the port chosen on the Hardware page"
        self._state.setText(text)
