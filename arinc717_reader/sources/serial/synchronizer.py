"""Stream synchronizer: word stream → subframe boundaries (spec v2 §26F).

ARINC 717 places a sync word at word address 1 of every subframe, and the
four sync words rotate SF1..SF4.  The synchronizer searches for two sync
words the right distance apart (WPS words), locks, then verifies every
subsequent sync position.  A mismatch is reported as a sync loss and the
synchronizer returns to searching; nothing is silently repaired.

While searching it also looks for sync pairs at the other standard WPS
distances so a device streaming at the wrong rate is reported as
``STREAM_RATE_MISMATCH`` instead of an endless "no lock".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...domain.frame import SUBFRAME_COUNT

STATE_SEARCHING = "SEARCHING"
STATE_LOCKED = "LOCKED"

STANDARD_WPS = (64, 128, 256, 512, 1024)


@dataclass
class RawSubframe:
    """One subframe's worth of words as delivered by the stream."""

    subframe: int
    words: list[int]
    sync_valid: bool = True


@dataclass
class SyncResult:
    subframes: list[RawSubframe] = field(default_factory=list)
    locked_now: bool = False
    lost_now: bool = False
    detected_wps: int | None = None  # set when sync pairs match another WPS


class StreamSynchronizer:
    def __init__(self, wps: int, sync_words):
        if len(sync_words) != SUBFRAME_COUNT:
            raise ValueError("four sync words are required")
        self.wps = wps
        self.sync_words = [int(s) for s in sync_words]
        self._sync_index = {s: i + 1 for i, s in enumerate(self.sync_words)}
        self.state = STATE_SEARCHING
        self._buffer: list[int] = []
        self._expected_subframe = 1
        self.words_in_subframe = 0  # progress indicator while locked

    def reset(self) -> None:
        self.state = STATE_SEARCHING
        self._buffer.clear()
        self._expected_subframe = 1
        self.words_in_subframe = 0

    @property
    def expected_subframe(self) -> int:
        return self._expected_subframe

    def feed(self, words) -> SyncResult:
        result = SyncResult()
        self._buffer.extend(words)
        while True:
            if self.state == STATE_SEARCHING:
                if not self._try_lock(result):
                    break
            else:
                if not self._take_locked(result):
                    break
        return result

    # -- searching -------------------------------------------------------

    def _try_lock(self, result: SyncResult) -> bool:
        buf = self._buffer
        wps = self.wps
        for pos in range(len(buf) - wps):
            index = self._sync_index.get(buf[pos])
            if index is None:
                continue
            following = self._sync_index.get(buf[pos + wps])
            if following == index % SUBFRAME_COUNT + 1:
                self._buffer = buf[pos:]
                self._expected_subframe = index
                self.state = STATE_LOCKED
                result.locked_now = True
                return True
        # No lock: report a rate mismatch if syncs pair up at another WPS.
        detected = self._detect_other_wps()
        if detected is not None:
            result.detected_wps = detected
        # Keep enough history to find a pair spanning the largest WPS.
        limit = max(self.wps, max(STANDARD_WPS)) + 1
        if len(buf) > limit:
            del buf[: len(buf) - limit]
        return False

    def _detect_other_wps(self) -> int | None:
        buf = self._buffer
        for wps in STANDARD_WPS:
            if wps == self.wps or len(buf) <= wps:
                continue
            for pos in range(len(buf) - wps):
                index = self._sync_index.get(buf[pos])
                if index is None:
                    continue
                if self._sync_index.get(buf[pos + wps]) == index % SUBFRAME_COUNT + 1:
                    return wps
        return None

    # -- locked ----------------------------------------------------------

    def _take_locked(self, result: SyncResult) -> bool:
        buf = self._buffer
        self.words_in_subframe = min(len(buf), self.wps)
        if len(buf) < self.wps:
            return False
        expected = self._expected_subframe
        sync_ok = buf[0] == self.sync_words[expected - 1]
        if not sync_ok:
            # Sync loss.  Report the subframe as invalid, then search again
            # from the very next word so nothing is skipped.
            self.state = STATE_SEARCHING
            result.lost_now = True
            result.subframes.append(
                RawSubframe(subframe=expected, words=buf[: self.wps], sync_valid=False)
            )
            del buf[:1]
            self.words_in_subframe = 0
            return True
        result.subframes.append(RawSubframe(subframe=expected, words=buf[: self.wps]))
        del buf[: self.wps]
        self._expected_subframe = expected % SUBFRAME_COUNT + 1
        self.words_in_subframe = 0
        return True
