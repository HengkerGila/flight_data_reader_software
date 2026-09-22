"""Frame lifecycle service: blank / random / load / save / edit."""

from __future__ import annotations

import logging
from pathlib import Path

from ..domain.frame import Arinc717Frame, FrameError
from ..encoder.frame_builder import build_blank_frame
from ..sources.frame_io import FrameIoError, load_frame, save_frame
from ..sources.random_source import RandomFrameSource
from ..state.dataframe_store import DataframeStore
from ..state.frame_store import FrameStore
from . import ServiceError

logger = logging.getLogger(__name__)

DEFAULT_WPS = 256


class FrameService:
    def __init__(self, frame_store: FrameStore, dataframe_store: DataframeStore):
        self._frame_store = frame_store
        self._dataframe_store = dataframe_store
        self._next_index = 0

    def _wps(self) -> int:
        dataframe = self._dataframe_store.dataframe
        return dataframe.metadata.wps if dataframe else DEFAULT_WPS

    def _sync_words(self) -> list[int] | None:
        dataframe = self._dataframe_store.dataframe
        return dataframe.metadata.sync_words if dataframe else None

    def _take_index(self) -> int:
        index = self._next_index
        self._next_index += 1
        return index

    def new_blank(self, with_sync_words: bool = False) -> Arinc717Frame:
        frame = build_blank_frame(
            self._wps(),
            self._take_index(),
            self._sync_words() if with_sync_words else None,
        )
        self._frame_store.set_frame(frame, "MANUAL")
        return frame

    def new_random(self, seed: int | None = None) -> Arinc717Frame:
        source = RandomFrameSource(self._wps(), seed)
        frame = source.next_frame()
        frame.frame_index = self._take_index()
        self._frame_store.set_frame(frame, source.name)
        return frame

    def load(self, path: str | Path) -> Arinc717Frame:
        try:
            frame = load_frame(path)
        except FrameIoError as exc:
            raise ServiceError("FRAME_LOAD_ERROR", str(exc)) from exc
        dataframe = self._dataframe_store.dataframe
        if dataframe and frame.wps != dataframe.metadata.wps:
            raise ServiceError(
                "DATAFRAME_WPS_MISMATCH",
                f"frame has {frame.wps} WPS but loaded dataframe defines "
                f"{dataframe.metadata.wps} WPS",
            )
        self._frame_store.set_frame(frame, f"FILE {Path(path).name}")
        return frame

    def save(self, path: str | Path) -> None:
        if self._frame_store.frame is None:
            raise ServiceError("MISSING_FRAME", "no frame to save")
        try:
            save_frame(path, self._frame_store.frame)
        except OSError as exc:
            raise ServiceError("FRAME_SAVE_ERROR", str(exc)) from exc
        logger.info("event=frame_saved file=%s", path)

    def set_word(self, subframe: int, word: int, value: int) -> None:
        try:
            self._frame_store.set_word(subframe, word, value)
        except FrameError as exc:
            raise ServiceError("INVALID_WORD", str(exc)) from exc
