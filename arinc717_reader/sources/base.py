"""Common source interface (design spec §21)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.frame import Arinc717Frame


@runtime_checkable
class FrameSource(Protocol):
    name: str

    def next_frame(self) -> Arinc717Frame: ...
