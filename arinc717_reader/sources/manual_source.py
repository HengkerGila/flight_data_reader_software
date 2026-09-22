"""Manual frame source (design spec §23).

Holds a frame the user edits word by word.  A blank frame defaults to all
words = 0.
"""

from __future__ import annotations

from ..domain.frame import Arinc717Frame


class ManualFrameSource:
    name = "MANUAL"

    def __init__(self, wps: int | None = None, frame: Arinc717Frame | None = None):
        if frame is None:
            if wps is None:
                raise ValueError("either wps or frame is required")
            frame = Arinc717Frame.blank(wps)
        self._frame = frame

    def set_frame(self, frame: Arinc717Frame) -> None:
        self._frame = frame

    def next_frame(self) -> Arinc717Frame:
        return self._frame
