"""Holds the current canonical frame (raw words only)."""

from __future__ import annotations

from ..domain.frame import Arinc717Frame, FrameError
from .base import Observable


class FrameStore(Observable):
    def __init__(self) -> None:
        super().__init__()
        self.frame: Arinc717Frame | None = None
        self.source_name: str = "—"

    def set_frame(self, frame: Arinc717Frame | None, source_name: str | None = None) -> None:
        self.frame = frame
        if source_name is not None:
            self.source_name = source_name
        self._notify({"type": "frame"})

    def set_word(self, subframe: int, word: int, value: int) -> None:
        """Edit a single word; notifies with cell granularity (spec §53.1)."""
        if self.frame is None:
            raise FrameError("no frame loaded")
        self.frame.set_word(subframe, word, value)
        self._notify({"type": "word", "subframe": subframe, "word": word})

    def clear(self) -> None:
        self.frame = None
        self.source_name = "—"
        self._notify({"type": "frame"})
