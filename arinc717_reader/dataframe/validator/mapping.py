"""Mapping validation (design spec §31.3)."""

from __future__ import annotations

from collections import Counter

from ...domain.dataframe import DataframeDefinition
from . import SEVERITY_ERROR, ValidationIssue


def validate(dataframe: DataframeDefinition) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for parameter in dataframe.parameters:
        index_counts = Counter(occ.index for occ in parameter.occurrences)
        for occurrence_index, count in index_counts.items():
            if count > 1:
                issues.append(
                    ValidationIssue(
                        SEVERITY_ERROR,
                        "mapping.occurrence_index",
                        f"{parameter.mnemonic}: occurrence index "
                        f"{occurrence_index} used {count} times",
                        parameter.id,
                    )
                )
        for occurrence in parameter.occurrences:
            sequence_counts = Counter(s.sequence for s in occurrence.segments)
            for sequence, count in sequence_counts.items():
                if count > 1:
                    issues.append(
                        ValidationIssue(
                            SEVERITY_ERROR,
                            "mapping.segment_sequence",
                            f"{parameter.mnemonic} occ {occurrence.index}: "
                            f"segment sequence {sequence} used {count} times",
                            parameter.id,
                        )
                    )
            subframe_sets = {
                tuple(sorted(set(s.subframes))) for s in occurrence.segments
            }
            if len(subframe_sets) > 1:
                issues.append(
                    ValidationIssue(
                        SEVERITY_ERROR,
                        "mapping.subframe_consistency",
                        f"{parameter.mnemonic} occ {occurrence.index}: segments "
                        f"disagree on subframes {sorted(subframe_sets)}",
                        parameter.id,
                    )
                )
    return issues
