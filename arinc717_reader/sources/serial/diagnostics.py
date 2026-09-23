"""Stream diagnostics counters (spec v2 §26S).

Updated from the acquisition thread, read from the GUI thread: every
mutation goes through the lock and readers take an immutable snapshot.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class DiagnosticsSnapshot:
    bytes_received: int = 0
    words_received: int = 0
    subframes_received: int = 0
    frames_received: int = 0
    sync_losses: int = 0
    invalid_words: int = 0
    invalid_subframes: int = 0
    dropped_subframes: int = 0
    out_of_order_subframes: int = 0
    bad_packets: int = 0
    discarded_bytes: int = 0
    word_rate: float = 0.0  # words per second over the last window
    sync_state: str = "SEARCHING"
    detected_wps: int | None = None


class StreamDiagnostics:
    RATE_WINDOW_S = 2.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snap = DiagnosticsSnapshot()
        self._word_times: deque[tuple[float, int]] = deque()

    def reset(self) -> None:
        with self._lock:
            self._snap = DiagnosticsSnapshot()
            self._word_times.clear()

    def update(self, **fields) -> None:
        """Set absolute values (e.g. ``sync_state="LOCKED"``)."""
        with self._lock:
            self._snap = replace(self._snap, **fields)

    def add(self, now: float | None = None, **deltas: int) -> None:
        """Increment counters; ``words_received`` also feeds the rate window."""
        with self._lock:
            snap = self._snap
            values = {k: getattr(snap, k) + v for k, v in deltas.items()}
            self._snap = replace(snap, **values)
            words = deltas.get("words_received", 0)
            if words:
                t = time.monotonic() if now is None else now
                self._word_times.append((t, words))
                self._trim(t)
                self._snap = replace(self._snap, word_rate=self._rate(t))

    def refresh_rate(self, now: float | None = None) -> None:
        """Recompute the rate so it decays to zero when the stream stops."""
        with self._lock:
            t = time.monotonic() if now is None else now
            self._trim(t)
            self._snap = replace(self._snap, word_rate=self._rate(t))

    def _trim(self, now: float) -> None:
        while self._word_times and now - self._word_times[0][0] > self.RATE_WINDOW_S:
            self._word_times.popleft()

    def _rate(self, now: float) -> float:
        if not self._word_times:
            return 0.0
        total = sum(n for _, n in self._word_times)
        span = now - self._word_times[0][0]
        if span < 0.25:
            span = 0.25
        return total / span

    def snapshot(self) -> DiagnosticsSnapshot:
        with self._lock:
            return self._snap
