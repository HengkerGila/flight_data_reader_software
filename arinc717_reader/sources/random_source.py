"""Random frame generator (design spec §22).

Every word gets a uniform random 0..4095 value.  This mode does not produce
meaningful engineering values and is labeled RAW RANDOM.
"""

from __future__ import annotations

import random

from ..domain.frame import SUBFRAME_COUNT, WORD_MAX, Arinc717Frame


class RandomFrameSource:
    name = "RAW RANDOM"

    def __init__(self, wps: int, seed: int | None = None):
        self.wps = wps
        self._rng = random.Random(seed)
        self._next_index = 0

    def next_frame(self) -> Arinc717Frame:
        subframes = [
            [self._rng.randint(0, WORD_MAX) for _ in range(self.wps)]
            for _ in range(SUBFRAME_COUNT)
        ]
        frame = Arinc717Frame(
            wps=self.wps, frame_index=self._next_index, subframes=subframes
        )
        self._next_index += 1
        return frame
