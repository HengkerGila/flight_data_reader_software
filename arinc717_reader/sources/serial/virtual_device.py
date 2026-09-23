"""Virtual STM32 SIM-A717 device (spec v2 §26A–§26J, §26I "standalone mode").

An in-process emulation of the firmware in ``firmware/stm32f103_sim_a717``:
same command set, same stream framing, same fault injection.  It lets the
whole acquisition, decoding, graphing and recording path run and be tested
without hardware, and it is what the Hardware page connects to when the
port is ``VIRTUAL``.

The device is a stepping engine — ``step(now)`` emits every word that is
due at ``now`` — so tests can drive it with a manual clock, while the GUI
runs it on a background thread with real time.
"""

from __future__ import annotations

import logging
import random
import threading
import time

from ...domain.frame import SUBFRAME_COUNT, WORD_MAX
from . import sim_a717_protocol as proto
from .transport import PipeTransport, TransportError, pipe_pair

logger = logging.getLogger(__name__)

DEFAULT_SYNC_WORDS = (583, 1464, 2631, 3512)
PAUSE_STREAM_SECONDS = 2.0
DELAY_SUBFRAME_SECONDS = 0.5
DISCONNECT_SECONDS = 1.5


class VirtualSimDevice:
    def __init__(
        self,
        wps: int = 256,
        sync_words=DEFAULT_SYNC_WORDS,
        speed: float = 1.0,
        seed: int | None = None,
    ):
        self.speed = speed
        self._rng = random.Random(seed)
        self._lock = threading.RLock()
        self._host: PipeTransport | None = None
        self._device: PipeTransport | None = None
        self._thread: threading.Thread | None = None
        self._stop_thread = threading.Event()
        self._line = bytearray()
        self.replies: list[str] = []  # every reply line ever sent (tests)
        self.commands: list[str] = []  # every command line received (tests)
        self._sync_words = list(sync_words)
        self._reset_state(wps)

    # -- construction / lifecycle ---------------------------------------

    def _reset_state(self, wps: int) -> None:
        self.wps = wps
        self.running = False
        self.mode = proto.DEVICE_MODE_FIXED
        self.protocol = proto.MODE_STREAM
        self.active = self._blank_frame()
        self.staged = self._blank_frame()
        self.pending_commit = False
        self.faults: dict[str, int] = {name: 0 for name in proto.FAULTS}
        self.word_index = 0  # 0-based within subframe
        self.subframe_index = 0  # 0-based
        self.frame_id = 0
        self.sequence = 0
        self._next_due: float | None = None
        self._resume_at: float | None = None
        self._reconnect_at: float | None = None
        self._order = list(range(SUBFRAME_COUNT))
        self._packet_words: list[int] = []
        self._packet_bad_crc = False
        self._drop_current_subframe = False
        self._wrong_wps_active = False
        self._in_subframe = False

    def _blank_frame(self) -> list[list[int]]:
        frame = [[0] * self.wps for _ in range(SUBFRAME_COUNT)]
        for i, sync in enumerate(self._sync_words[:SUBFRAME_COUNT]):
            frame[i][0] = sync
        return frame

    def connect(self) -> PipeTransport:
        """Open the (virtual) cable; returns the host end."""
        with self._lock:
            if self._reconnect_at is not None:
                raise TransportError("virtual device is disconnected (fault injection)")
            if self._host is not None and self._host.is_open:
                raise TransportError("virtual device already connected")
            self._host, self._device = pipe_pair("virtual")
            self._line.clear()
            return self._host

    def disconnect(self) -> None:
        with self._lock:
            if self._device is not None:
                self._device.close()
            self._host = None
            self._device = None

    @property
    def connected(self) -> bool:
        return self._device is not None and self._device.is_open

    def start_thread(self) -> None:
        if self._thread is not None:
            return
        self._stop_thread.clear()
        self._thread = threading.Thread(target=self._run, name="virtual-sim-device", daemon=True)
        self._thread.start()

    def stop_thread(self) -> None:
        self._stop_thread.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop_thread.is_set():
            try:
                self.step(time.monotonic())
            except Exception:  # keep the emulator alive; log for diagnosis
                logger.exception("virtual device step failed")
            time.sleep(0.002)

    # -- stepping engine -------------------------------------------------

    @property
    def word_period(self) -> float:
        wps = self._effective_wps()
        return 1.0 / (wps * self.speed)

    def _effective_wps(self) -> int:
        if self._wrong_wps_active:
            return 128 if self.wps != 128 else 64
        return self.wps

    def step(self, now: float) -> None:
        """Process commands and emit every word due at ``now``."""
        with self._lock:
            self._maybe_reconnect(now)
            self._read_commands()
            if not self.running or self._device is None:
                return
            if self._resume_at is not None:
                if now < self._resume_at:
                    return
                self._resume_at = None
                self._next_due = now
            if self._next_due is None:
                self._next_due = now
            emitted = 0
            while now >= self._next_due and emitted < 4096:
                self._emit_next_word(now)
                self._next_due += self.word_period
                emitted += 1
                if self._resume_at is not None or not self.running:
                    break

    def _maybe_reconnect(self, now: float) -> None:
        if self._reconnect_at is not None and now >= self._reconnect_at:
            self._reconnect_at = None  # host may connect again

    # -- word generation -------------------------------------------------

    def _current_subframe_slot(self) -> int:
        return self._order[self.subframe_index]

    def _word_value(self, slot: int, index: int) -> int:
        if index == 0:
            return self._sync_words[slot]
        if self.mode == proto.DEVICE_MODE_RANDOM:
            return self._rng.randint(0, WORD_MAX)
        if self.mode == proto.DEVICE_MODE_WALK:
            value = self.active[slot][index] + self._rng.randint(-8, 8)
            value = max(0, min(WORD_MAX, value))
            self.active[slot][index] = value
            return value
        return self.active[slot][index]

    def _emit_next_word(self, now: float) -> None:
        slot = self._current_subframe_slot()
        if not self._in_subframe:
            self._begin_subframe(now)
            if self._resume_at is not None:
                return
        wps = self._effective_wps()
        value = self._word_value(slot, self.word_index) if self.word_index < self.wps else self._rng.randint(0, WORD_MAX)
        drop = False
        if self._take_fault(proto.FAULT_CORRUPT_WORD):
            value ^= self._rng.randint(1, WORD_MAX)
        if self._take_fault(proto.FAULT_DROP_WORD):
            drop = True
        if self._take_fault(proto.FAULT_PAUSE_STREAM):
            self._resume_at = now + PAUSE_STREAM_SECONDS / self.speed
        if self._take_fault(proto.FAULT_DISCONNECT_RECONNECT):
            self._disconnect_for_fault(now)
            return
        if not drop and not self._drop_current_subframe:
            if self.protocol == proto.MODE_STREAM:
                self._write(proto.encode_word(value))
            else:
                self._packet_words.append(value)
        self.word_index += 1
        if self.word_index >= wps:
            self._end_subframe()

    def _begin_subframe(self, now: float) -> None:
        self._in_subframe = True
        self._drop_current_subframe = self._take_fault(proto.FAULT_DROP_SUBFRAME)
        self._packet_bad_crc = self._take_fault(proto.FAULT_BAD_CRC)
        self._wrong_wps_active = self._take_fault(proto.FAULT_WRONG_WPS)
        if self._take_fault(proto.FAULT_DELAY_SUBFRAME):
            self._resume_at = now + DELAY_SUBFRAME_SECONDS / self.speed
        if self.subframe_index == 0 and self._take_fault(proto.FAULT_OUT_OF_ORDER_SUBFRAME):
            self._order = [0, 2, 1, 3]
        self._packet_words = []

    def _end_subframe(self) -> None:
        slot = self._current_subframe_slot()
        if self.protocol == proto.MODE_FRAMED and not self._drop_current_subframe:
            packet = proto.SubframePacket(
                sequence=self.sequence,
                frame_id=self.frame_id,
                subframe=slot + 1,
                wps=self._effective_wps(),
                words=self._packet_words,
            )
            self._write(proto.encode_packet(packet, corrupt_crc=self._packet_bad_crc))
        self.sequence = (self.sequence + 1) & 0xFFFF
        self.word_index = 0
        self._in_subframe = False
        self.subframe_index += 1
        if self.subframe_index >= SUBFRAME_COUNT:
            self.subframe_index = 0
            self.frame_id += 1
            self._order = list(range(SUBFRAME_COUNT))
            if self.pending_commit:
                self.active = [list(sf) for sf in self.staged]
                self.pending_commit = False

    def _take_fault(self, name: str) -> bool:
        count = self.faults.get(name, 0)
        if count <= 0:
            return False
        if count != proto.FAULT_CONTINUOUS:
            self.faults[name] = count - 1
        return True

    def _disconnect_for_fault(self, now: float) -> None:
        logger.info("event=virtual_device_disconnect_fault")
        self.disconnect()
        self._reconnect_at = now + DISCONNECT_SECONDS / self.speed

    def _write(self, data: bytes) -> None:
        if self._device is None:
            return
        try:
            self._device.write(data)
        except TransportError:
            self._device = None
            self._host = None

    # -- commands --------------------------------------------------------

    def _read_commands(self) -> None:
        if self._device is None:
            return
        try:
            data = self._device.read(65536, timeout=0.0)
        except TransportError:
            self._device = None
            self._host = None
            return
        if not data:
            return
        self._line.extend(data)
        while True:
            newline = self._line.find(b"\n")
            if newline < 0:
                if len(self._line) > 8192:
                    self._line.clear()
                break
            line = self._line[:newline].decode("ascii", errors="replace")
            del self._line[: newline + 1]
            if line.strip():
                self.handle_command(line)

    def handle_command(self, line: str) -> str:
        """Execute one command line; returns the reply text (sent only when stopped)."""
        self.commands.append(line.strip())
        try:
            reply = self._execute(line)
            text = f"{proto.REPLY_OK} {reply}".rstrip()
        except (ValueError, IndexError) as exc:
            text = f"{proto.REPLY_ERR} {exc}"
        if not self.running:
            self.replies.append(text)
            self._write((text + proto.LINE_END).encode("ascii"))
        return text

    def _execute(self, line: str) -> str:
        command, args = proto.parse_command_line(line)
        if command == proto.CMD_PING:
            return "PONG"
        if command == proto.CMD_INFO:
            return (
                f"{proto.PROTOCOL_NAME} v{proto.PROTOCOL_VERSION} wps={self.wps} "
                f"mode={self.mode} proto={self.protocol} running={int(self.running)} "
                f"virtual=1"
            )
        if command == proto.CMD_START:
            self.running = True
            self._next_due = None
            return "STARTED"
        if command == proto.CMD_STOP:
            self.running = False
            self._next_due = None
            self._resume_at = None
            return "STOPPED"
        if command == proto.CMD_RESET:
            self._reset_state(self.wps)
            return "RESET"
        if command == proto.CMD_SET_WPS:
            wps = int(args[0])
            if wps <= 0 or wps > 4096:
                raise ValueError("WPS must be 1..4096")
            if self.running:
                raise ValueError("stop the stream before changing WPS")
            self._reset_state(wps)
            return f"WPS {wps}"
        if command == proto.CMD_SET_MODE:
            mode = args[0].upper()
            if mode not in proto.DEVICE_MODES:
                raise ValueError(f"unknown mode {mode}")
            self.mode = mode
            return f"MODE {mode}"
        if command == proto.CMD_SET_PROTOCOL:
            mode = args[0].upper()
            if mode not in proto.STREAM_MODES:
                raise ValueError(f"unknown protocol mode {mode}")
            if self.running:
                raise ValueError("stop the stream before changing protocol")
            self.protocol = mode
            return f"PROTOCOL {mode}"
        if command == proto.CMD_SET_SYNC:
            words = [int(a) for a in args]
            if len(words) != SUBFRAME_COUNT or any(not 0 <= w <= WORD_MAX for w in words):
                raise ValueError("SET_SYNC needs four 12-bit words")
            self._sync_words = words
            for i, sync in enumerate(words):
                self.active[i][0] = sync
                self.staged[i][0] = sync
            return "SYNC " + " ".join(str(w) for w in words)
        if command == proto.CMD_SET_WORD:
            subframe, word, value = (int(a) for a in args[:3])
            self._check_address(subframe, word)
            if not 0 <= value <= WORD_MAX:
                raise ValueError("value outside 0..4095")
            self.active[subframe - 1][word - 1] = value
            return f"WORD {subframe} {word} {value}"
        if command == proto.CMD_LOAD_SF:
            subframe, words = proto.parse_load_subframe(args)
            if not 1 <= subframe <= SUBFRAME_COUNT:
                raise ValueError("subframe outside 1..4")
            if len(words) != self.wps:
                raise ValueError(f"expected {self.wps} words, got {len(words)}")
            self.staged[subframe - 1] = words
            return f"LOADED {subframe}"
        if command == proto.CMD_COMMIT:
            self.pending_commit = True
            if not self.running:
                self.active = [list(sf) for sf in self.staged]
                self.pending_commit = False
            return "COMMIT"
        if command == proto.CMD_FAULT:
            name = args[0].upper()
            if name not in proto.FAULTS:
                raise ValueError(f"unknown fault {name}")
            count = int(args[1]) if len(args) > 1 else 1
            if count < 0 or count > proto.FAULT_CONTINUOUS:
                raise ValueError("count outside 0..65535")
            self.faults[name] = count
            return f"FAULT {name} {count}"
        raise ValueError(f"unknown command {command}")

    def _check_address(self, subframe: int, word: int) -> None:
        if not 1 <= subframe <= SUBFRAME_COUNT:
            raise ValueError("subframe outside 1..4")
        if not 1 <= word <= self.wps:
            raise ValueError(f"word outside 1..{self.wps}")
