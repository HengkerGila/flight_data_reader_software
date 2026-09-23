"""Keeps the engineering store in sync with frame + dataframe (spec §40).

Reads FrameStore + DataframeStore, writes EngineeringStore.  Never reads
GUI widgets.
"""

from __future__ import annotations

import logging

from ..decoder.parameter_decoder import ParameterDecoder
from ..domain.engineering import STATUS_VALID
from ..state.dataframe_store import DataframeStore
from ..state.engineering_store import EngineeringStore
from ..state.frame_store import FrameStore

logger = logging.getLogger(__name__)


class DecodingService:
    def __init__(
        self,
        dataframe_store: DataframeStore,
        frame_store: FrameStore,
        engineering_store: EngineeringStore,
        decoder: ParameterDecoder | None = None,
    ):
        self._dataframe_store = dataframe_store
        self._frame_store = frame_store
        self._engineering_store = engineering_store
        self._decoder = decoder or ParameterDecoder()
        dataframe_store.subscribe(self._on_store_event)
        frame_store.subscribe(self._on_store_event)

    def _on_store_event(self, event: dict) -> None:
        if event.get("live"):
            # A frame under live reception is decoded per subframe by the
            # streaming service (spec v2 §26G); a whole-frame decode here
            # would publish values from subframes that have not arrived.
            return
        if event.get("type") in ("dataframe", "frame", "word"):
            if event.get("type") == "dataframe" and self._frame_store.live:
                return
            self.redecode()

    def redecode(self) -> None:
        dataframe = self._dataframe_store.dataframe
        frame = self._frame_store.frame
        if dataframe is None or frame is None:
            self._engineering_store.set_values([])
            return
        values = self._decoder.decode_frame(frame, dataframe)
        self._engineering_store.set_values(values)
        failures = sum(1 for v in values if v.status != STATUS_VALID)
        logger.debug(
            "event=decoded frame_index=%d values=%d failures=%d",
            frame.frame_index,
            len(values),
            failures,
        )
