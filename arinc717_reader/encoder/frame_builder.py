"""Frame construction helpers (design spec §23, §24).

A blank frame defaults to all words = 0; sync words (from dataframe metadata,
word address 1 of each subframe) are inserted only when requested.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..domain.dataframe import DataframeDefinition
from ..domain.frame import Arinc717Frame
from .parameter_encoder import EncodeError, ParameterEncoder


def build_blank_frame(
    wps: int,
    frame_index: int = 0,
    sync_words: Sequence[int] | None = None,
) -> Arinc717Frame:
    frame = Arinc717Frame.blank(wps, frame_index)
    if sync_words:
        for i, sync in enumerate(sync_words[:4]):
            frame.set_word(i + 1, 1, sync)
    return frame


def build_scenario_frame(
    dataframe: DataframeDefinition,
    scenario: Mapping[str, int | float | str | bool],
    frame_index: int = 0,
    encoder: ParameterEncoder | None = None,
    base_frame: Arinc717Frame | None = None,
) -> tuple[Arinc717Frame, dict[str, int]]:
    """Encode a scenario (parameter id → engineering value) into a frame.

    Returns the frame and the raw bit pattern written per parameter.
    """
    encoder = encoder or ParameterEncoder()
    if base_frame is not None:
        frame = base_frame.copy()
        frame.frame_index = frame_index
    else:
        frame = build_blank_frame(
            dataframe.metadata.wps, frame_index, dataframe.metadata.sync_words
        )
    raw_patterns: dict[str, int] = {}
    for parameter_id, value in scenario.items():
        parameter = dataframe.get_parameter(parameter_id)
        if parameter is None:
            raise EncodeError(f"unknown parameter id {parameter_id!r}")
        raw_patterns[parameter_id] = encoder.encode_into_frame(frame, parameter, value)
    return frame, raw_patterns
