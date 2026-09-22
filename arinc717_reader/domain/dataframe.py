"""Canonical dataframe model (design spec §6.1, §6.2)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .parameter import ParameterDefinition, ParameterOccurrence, ParameterSegment


@dataclass
class DataframeMetadata:
    dataframe_name: str
    wps: int

    aircraft_type: str | None = None
    revision: str | None = None
    issue_date: str | None = None

    superframe_present: bool | None = None

    sync_words: list[int] = field(default_factory=list)

    source_type: str = "manual"
    source_filename: str | None = None
    source_hash: str | None = None

    # Full raw ADB "Setting" record, preserved losslessly (design spec §34).
    # Fields with unknown semantics must survive a round-trip unchanged.
    adb_settings_raw: tuple[str, ...] | None = None


@dataclass
class DataframeDefinition:
    metadata: DataframeMetadata
    parameters: list[ParameterDefinition] = field(default_factory=list)

    def get_parameter(self, parameter_id: str) -> ParameterDefinition | None:
        for parameter in self.parameters:
            if parameter.id == parameter_id:
                return parameter
        return None

    def parameters_at(
        self, subframe: int, word: int
    ) -> list[tuple[ParameterDefinition, ParameterOccurrence, ParameterSegment]]:
        """All (parameter, occurrence, segment) triples mapped to a cell.

        Used by the Word Inspector (design spec §9): if several parameters use
        the same word, all must be shown.
        """
        hits: list[tuple[ParameterDefinition, ParameterOccurrence, ParameterSegment]] = []
        for parameter in self.parameters:
            for occurrence in parameter.occurrences:
                for segment in occurrence.segments:
                    if segment.word == word and subframe in segment.subframes:
                        hits.append((parameter, occurrence, segment))
        return hits
