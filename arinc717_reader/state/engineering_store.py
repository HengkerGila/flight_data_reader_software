"""Holds decoded engineering values (decoder output only)."""

from __future__ import annotations

from ..domain.engineering import EngineeringValue
from .base import Observable


class EngineeringStore(Observable):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[EngineeringValue] = []

    def set_values(self, values: list[EngineeringValue]) -> None:
        self.values = list(values)
        self._notify({"type": "engineering"})

    def values_for_parameter(self, parameter_id: str) -> list[EngineeringValue]:
        return [v for v in self.values if v.parameter_id == parameter_id]

    def values_at_word(self, subframe: int, word: int) -> list[EngineeringValue]:
        """Values whose decode chain touched (subframe, word) — inspector use."""
        hits = []
        for value in self.values:
            for segment in value.trace.segments:
                if segment.subframe == subframe and segment.word == word:
                    hits.append(value)
                    break
        return hits

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for value in self.values:
            counts[value.status] = counts.get(value.status, 0) + 1
        return counts
