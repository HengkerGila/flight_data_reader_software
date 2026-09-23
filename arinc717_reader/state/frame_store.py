"""Holds the current canonical frame (raw words only)."""

from __future__ import annotations

from ..domain.frame import Arinc717Frame, FrameError
from .base import Observable


class FrameStore(Observable):
    def __init__(self) -> None:
        super().__init__()
        self.frame: Arinc717Frame | None = None
        self.source_name: str = "—"
        # Live streaming only (spec v2 §26T): per-subframe receive state and
        # the (subframe, word) cells flagged invalid.  None when the frame is
        # a complete, static frame.
        self.subframe_states: list[str] | None = None
        self.invalid_words: frozenset[tuple[int, int]] = frozenset()
        self.blank_subframes: frozenset[int] = frozenset()  # never received any data

    @property
    def live(self) -> bool:
        return self.subframe_states is not None

    def set_frame(
        self,
        frame: Arinc717Frame | None,
        source_name: str | None = None,
        subframe_states: list[str] | None = None,
        invalid_words=(),
        blank_subframes=(),
    ) -> None:
        self.frame = frame
        if source_name is not None:
            self.source_name = source_name
        self.subframe_states = list(subframe_states) if subframe_states is not None else None
        self.invalid_words = frozenset(invalid_words)
        self.blank_subframes = frozenset(blank_subframes) if self.live else frozenset()
        self._notify({"type": "frame", "live": self.live})

    def set_word(self, subframe: int, word: int, value: int) -> None:
        """Edit a single word; notifies with cell granularity (spec §53.1)."""
        if self.frame is None:
            raise FrameError("no frame loaded")
        if self.live:
            raise FrameError("the frame is being received live; stop the stream to edit")
        self.frame.set_word(subframe, word, value)
        self._notify({"type": "word", "subframe": subframe, "word": word})

    def clear(self) -> None:
        self.frame = None
        self.source_name = "—"
        self.subframe_states = None
        self.invalid_words = frozenset()
        self.blank_subframes = frozenset()
        self._notify({"type": "frame", "live": False})
