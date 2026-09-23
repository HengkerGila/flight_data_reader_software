"""Subframe assembler: subframes → progressive canonical frames (spec v2 §26F, §26T).

The assembler owns the frame under construction.  Every subframe it takes
produces a ``SubframeArrival`` (what the live decoder consumes) and updates
the partial frame snapshot (what the Frame View shows).  Each subframe slot
carries an explicit state so the view can distinguish received, pending,
invalid, missing and late data.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...domain.frame import SUBFRAME_COUNT, WORD_MAX, Arinc717Frame

SF_PENDING = "PENDING"      # not yet received in this frame
SF_RECEIVED = "RECEIVED"
SF_INVALID = "INVALID"      # received but sync/length/word-range problem
SF_MISSING = "MISSING"      # a later subframe arrived without it
SF_LATE = "LATE"            # arrived out of order after being marked missing
SUBFRAME_STATES = (SF_PENDING, SF_RECEIVED, SF_INVALID, SF_MISSING, SF_LATE)


@dataclass
class SubframeArrival:
    frame_index: int
    subframe: int
    words: list[int]
    state: str
    timestamp: float
    frame: Arinc717Frame  # snapshot of the partial frame after this arrival
    subframe_states: list[str]
    invalid_words: set[tuple[int, int]] = field(default_factory=set)
    frame_completed: bool = False  # True when this arrival finished a frame
    # Slots that have never held data (no previous frame to carry forward);
    # every other unreceived slot shows the previous frame's words.
    blank_subframes: frozenset[int] = frozenset()


@dataclass
class AssemblerStats:
    subframes: int = 0
    frames: int = 0
    missing_subframes: int = 0
    invalid_subframes: int = 0
    out_of_order: int = 0
    incomplete_frames: int = 0


class SubframeAssembler:
    def __init__(self, wps: int, first_frame_index: int = 0):
        self.wps = wps
        self.stats = AssemblerStats()
        self._frame_index = first_frame_index
        self._new_frame()

    # -- state -----------------------------------------------------------

    def _new_frame(self, keep_words: bool = True) -> None:
        # Slots not yet received in the new frame keep the previous frame's
        # words, so a live view shows the last known value of every word
        # (one column changes per second); the states say which slots are
        # current.  Before any data a slot is None and renders blank.
        previous = getattr(self, "_words", None)
        if keep_words and previous is not None:
            self._words: list[list[int] | None] = [
                list(w) if w is not None else None for w in previous
            ]
        else:
            self._words = [None] * SUBFRAME_COUNT
        self._states = [SF_PENDING] * SUBFRAME_COUNT
        self._invalid: set[tuple[int, int]] = set()
        self._expected = 1

    @property
    def frame_index(self) -> int:
        return self._frame_index

    @property
    def subframe_states(self) -> list[str]:
        return list(self._states)

    @property
    def blank_subframes(self) -> frozenset[int]:
        return frozenset(i + 1 for i, w in enumerate(self._words) if w is None)

    def snapshot(self) -> Arinc717Frame:
        """The partial frame: unreceived subframes carry the previous frame's
        words (zero-filled only before any data has arrived)."""
        subframes = [
            list(w) if w is not None else [0] * self.wps for w in self._words
        ]
        return Arinc717Frame(wps=self.wps, frame_index=self._frame_index, subframes=subframes)

    def reset(self, frame_index: int | None = None, keep_words: bool = True) -> None:
        """Start a fresh frame; the last known words stay as the display
        baseline unless ``keep_words`` is False."""
        if frame_index is not None:
            self._frame_index = frame_index
        self._new_frame(keep_words=keep_words)

    # -- input -----------------------------------------------------------

    def take(
        self,
        subframe: int,
        words,
        timestamp: float,
        valid: bool = True,
        frame_id: int | None = None,
    ) -> list[SubframeArrival]:
        """Take one subframe; returns the arrivals produced (usually one).

        Two arrivals are produced when this subframe implicitly closes the
        previous frame (e.g. SF1 arriving while SF4 was still pending): the
        first carries the closed frame with its missing slots, the second the
        new frame.
        """
        if not 1 <= subframe <= SUBFRAME_COUNT:
            raise ValueError(f"subframe {subframe} outside 1..{SUBFRAME_COUNT}")
        arrivals: list[SubframeArrival] = []
        words = list(words)
        state = SF_RECEIVED if valid else SF_INVALID
        invalid_words: set[tuple[int, int]] = set()
        if len(words) != self.wps:
            state = SF_INVALID
            words = (words + [0] * self.wps)[: self.wps]
        for address, value in enumerate(words, start=1):
            if not 0 <= value <= WORD_MAX:
                invalid_words.add((subframe, address))
                words[address - 1] = value & WORD_MAX
        if invalid_words:
            state = SF_INVALID

        if frame_id is not None and frame_id != self._frame_index and self._expected != 1:
            # Framed mode told us a new frame started: close the current one.
            arrivals.append(self._close_incomplete(timestamp))
            self._frame_index = frame_id
        elif frame_id is not None and self._expected == 1:
            self._frame_index = frame_id

        if subframe < self._expected:
            if self._states[subframe - 1] == SF_MISSING:
                # Late arrival of a slot we already gave up on.
                state = SF_LATE if state == SF_RECEIVED else state
                self.stats.out_of_order += 1
                self.stats.missing_subframes -= 1
                return [self._store(subframe, words, state, invalid_words, timestamp)]
            # A lower subframe than expected: the device started a new frame.
            arrivals.append(self._close_incomplete(timestamp))
        elif subframe > self._expected:
            for missing in range(self._expected, subframe):
                self._states[missing - 1] = SF_MISSING
                self.stats.missing_subframes += 1

        arrivals.append(self._store(subframe, words, state, invalid_words, timestamp))
        return arrivals

    def _store(self, subframe, words, state, invalid_words, timestamp) -> SubframeArrival:
        self._words[subframe - 1] = words
        self._states[subframe - 1] = state
        self._invalid |= invalid_words
        self.stats.subframes += 1
        if state == SF_INVALID:
            self.stats.invalid_subframes += 1
        if subframe >= self._expected:
            self._expected = subframe + 1
        completed = self._expected > SUBFRAME_COUNT
        arrival = SubframeArrival(
            frame_index=self._frame_index,
            subframe=subframe,
            words=list(words),
            state=state,
            timestamp=timestamp,
            frame=self.snapshot(),
            subframe_states=self.subframe_states,
            invalid_words=set(self._invalid),
            frame_completed=completed,
            blank_subframes=self.blank_subframes,
        )
        if completed:
            self.stats.frames += 1
            self._frame_index += 1
            self._new_frame()
        return arrival

    def _close_incomplete(self, timestamp: float) -> SubframeArrival:
        """Close the current frame with its pending slots marked missing."""
        for i, state in enumerate(self._states):
            if state == SF_PENDING:
                self._states[i] = SF_MISSING
                self.stats.missing_subframes += 1
        self.stats.incomplete_frames += 1
        self.stats.frames += 1
        arrival = SubframeArrival(
            frame_index=self._frame_index,
            subframe=0,
            words=[],
            state=SF_MISSING,
            timestamp=timestamp,
            frame=self.snapshot(),
            subframe_states=self.subframe_states,
            invalid_words=set(self._invalid),
            frame_completed=True,
            blank_subframes=self.blank_subframes,
        )
        self._frame_index += 1
        self._new_frame()
        return arrival
