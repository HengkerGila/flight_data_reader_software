"""Canonical ARINC 717 frame model (design spec §7).

The canonical frame holds raw 12-bit word values only.  It never contains
engineering values.  Subframes and word addresses are 1-based at the API
surface; internal storage is plain nested lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SUBFRAME_COUNT = 4
WORD_BITS = 12
WORD_MIN = 0
WORD_MAX = (1 << WORD_BITS) - 1  # 4095


class FrameError(ValueError):
    """Raised when a frame or word constraint is violated."""


def validate_word_value(value: int) -> int:
    """Validate a raw ARINC word value (0..4095) and return it."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise FrameError(f"word value must be an integer, got {value!r}")
    if not WORD_MIN <= value <= WORD_MAX:
        raise FrameError(f"word value {value} outside {WORD_MIN}..{WORD_MAX}")
    return value


@dataclass
class Arinc717Frame:
    wps: int
    frame_index: int = 0
    subframes: list[list[int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.wps, int) or self.wps <= 0:
            raise FrameError(f"wps must be a positive integer, got {self.wps!r}")
        if not self.subframes:
            self.subframes = [[0] * self.wps for _ in range(SUBFRAME_COUNT)]
        if len(self.subframes) != SUBFRAME_COUNT:
            raise FrameError(
                f"frame must contain exactly {SUBFRAME_COUNT} subframes, "
                f"got {len(self.subframes)}"
            )
        for n, sf in enumerate(self.subframes, start=1):
            if len(sf) != self.wps:
                raise FrameError(
                    f"subframe {n} has {len(sf)} words, expected wps={self.wps}"
                )
            for value in sf:
                validate_word_value(value)

    @classmethod
    def blank(cls, wps: int, frame_index: int = 0) -> "Arinc717Frame":
        return cls(wps=wps, frame_index=frame_index)

    def _check_address(self, subframe: int, word: int) -> None:
        if not 1 <= subframe <= SUBFRAME_COUNT:
            raise FrameError(f"subframe {subframe} outside 1..{SUBFRAME_COUNT}")
        if not 1 <= word <= self.wps:
            raise FrameError(f"word address {word} outside 1..{self.wps}")

    def word(self, subframe: int, word: int) -> int:
        """Return the raw value at 1-based (subframe, word address)."""
        self._check_address(subframe, word)
        return self.subframes[subframe - 1][word - 1]

    def set_word(self, subframe: int, word: int, value: int) -> None:
        """Set the raw value at 1-based (subframe, word address)."""
        self._check_address(subframe, word)
        validate_word_value(value)
        self.subframes[subframe - 1][word - 1] = value

    def copy(self) -> "Arinc717Frame":
        return Arinc717Frame(
            wps=self.wps,
            frame_index=self.frame_index,
            subframes=[list(sf) for sf in self.subframes],
        )
