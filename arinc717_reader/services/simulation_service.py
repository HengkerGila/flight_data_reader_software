"""Scenario simulation with closed-loop validation (design spec §24–§26)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..domain.engineering import STATUS_OUT_OF_RANGE, STATUS_VALID
from ..domain.parameter import ParameterDefinition
from ..encoder.frame_builder import build_blank_frame, build_scenario_frame
from ..encoder.parameter_encoder import (
    EncodeError,
    ParameterEncoder,
    is_encodable,
    quantization_tolerance,
)
from ..state.dataframe_store import DataframeStore
from ..state.engineering_store import EngineeringStore
from ..state.frame_store import FrameStore
from . import ServiceError

logger = logging.getLogger(__name__)


@dataclass
class ClosedLoopEntry:
    parameter: ParameterDefinition
    requested: int | float | str | bool
    raw_pattern: int
    decoded: int | float | str | None
    delta: float | None
    tolerance: float
    passed: bool


class SimulationService:
    def __init__(
        self,
        dataframe_store: DataframeStore,
        frame_store: FrameStore,
        engineering_store: EngineeringStore,
        encoder: ParameterEncoder | None = None,
    ):
        self._dataframe_store = dataframe_store
        self._frame_store = frame_store
        self._engineering_store = engineering_store
        self._encoder = encoder or ParameterEncoder()
        self._next_index = 0

    def encodable_parameters(self) -> list[ParameterDefinition]:
        dataframe = self._dataframe_store.dataframe
        if dataframe is None:
            return []
        return [p for p in dataframe.parameters if is_encodable(p)]

    def apply_scenario(
        self,
        scenario: dict[str, int | float | str | bool],
        start_from_current: bool = False,
    ) -> dict[str, int]:
        dataframe = self._dataframe_store.dataframe
        if dataframe is None:
            raise ServiceError("MISSING_DATAFRAME", "load a dataframe first")
        if not scenario:
            raise ServiceError("EMPTY_SCENARIO", "no scenario values given")
        base = None
        if start_from_current and self._frame_store.frame is not None:
            if self._frame_store.frame.wps == dataframe.metadata.wps:
                base = self._frame_store.frame
        try:
            frame, raw_patterns = build_scenario_frame(
                dataframe,
                scenario,
                frame_index=self._next_index,
                encoder=self._encoder,
                base_frame=base,
            )
        except EncodeError as exc:
            raise ServiceError("ENCODE_ERROR", str(exc)) from exc
        self._next_index += 1
        self._frame_store.set_frame(frame, "SCENARIO")
        logger.info(
            "event=scenario_applied parameters=%d frame_index=%d",
            len(scenario),
            frame.frame_index,
        )
        return raw_patterns

    def closed_loop_report(
        self,
        scenario: dict[str, int | float | str | bool],
        raw_patterns: dict[str, int],
    ) -> list[ClosedLoopEntry]:
        """Compare requested vs decoded values (quantization-aware, §26)."""
        dataframe = self._dataframe_store.dataframe
        if dataframe is None:
            return []
        report: list[ClosedLoopEntry] = []
        for parameter_id, requested in scenario.items():
            parameter = dataframe.get_parameter(parameter_id)
            if parameter is None:
                continue
            samples = [
                v
                for v in self._engineering_store.values_for_parameter(parameter_id)
                if v.status in (STATUS_VALID, STATUS_OUT_OF_RANGE)
            ]
            decoded = samples[0].engineering_value if samples else None
            tolerance = quantization_tolerance(parameter)
            delta: float | None = None
            if isinstance(requested, (int, float)) and not isinstance(requested, bool):
                if isinstance(decoded, (int, float)):
                    delta = abs(float(decoded) - float(requested))
                    passed = delta <= tolerance
                else:
                    passed = False
            else:
                passed = decoded == requested or (
                    isinstance(requested, str)
                    and isinstance(decoded, str)
                    and requested.strip().upper() == decoded.strip().upper()
                )
            report.append(
                ClosedLoopEntry(
                    parameter=parameter,
                    requested=requested,
                    raw_pattern=raw_patterns.get(parameter_id, 0),
                    decoded=decoded,
                    delta=delta,
                    tolerance=tolerance,
                    passed=passed,
                )
            )
        return report

    def blank_frame(self, with_sync_words: bool = True) -> None:
        dataframe = self._dataframe_store.dataframe
        if dataframe is None:
            raise ServiceError("MISSING_DATAFRAME", "load a dataframe first")
        frame = build_blank_frame(
            dataframe.metadata.wps,
            self._next_index,
            dataframe.metadata.sync_words if with_sync_words else None,
        )
        self._next_index += 1
        self._frame_store.set_frame(frame, "MANUAL")
