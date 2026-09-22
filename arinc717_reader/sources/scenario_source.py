"""Scenario frame source (design spec §24).

Encodes user-requested engineering values into frames via the parameter
encoder, inserting the dataframe's sync words at word 1 of each subframe.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..domain.dataframe import DataframeDefinition
from ..domain.frame import Arinc717Frame
from ..encoder.frame_builder import build_scenario_frame
from ..encoder.parameter_encoder import ParameterEncoder


class ScenarioFrameSource:
    name = "SCENARIO"

    def __init__(
        self,
        dataframe: DataframeDefinition,
        scenario: Mapping[str, int | float | str | bool],
        encoder: ParameterEncoder | None = None,
    ):
        self._dataframe = dataframe
        self._scenario = dict(scenario)
        self._encoder = encoder or ParameterEncoder()
        self._next_index = 0
        self.last_raw_patterns: dict[str, int] = {}

    def set_scenario(self, scenario: Mapping[str, int | float | str | bool]) -> None:
        self._scenario = dict(scenario)

    def next_frame(self) -> Arinc717Frame:
        frame, raw = build_scenario_frame(
            self._dataframe,
            self._scenario,
            frame_index=self._next_index,
            encoder=self._encoder,
        )
        self.last_raw_patterns = raw
        self._next_index += 1
        return frame
