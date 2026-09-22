"""Type, range and conflict validation (design spec §31.2, §31.4).

Overlapping parameter assignments are warnings by default, not errors —
legitimate dataframes overlay parameters (e.g. supersets, spares).
"""

from __future__ import annotations

from ...domain.dataframe import DataframeDefinition
from ...domain.frame import SUBFRAME_COUNT, WORD_BITS
from ...domain.parameter import CANONICAL_TYPES, TYPE_DISCRETE, TYPE_UNKNOWN
from . import SEVERITY_ERROR, SEVERITY_WARNING, ValidationIssue


def validate(dataframe: DataframeDefinition) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    for parameter in dataframe.parameters:
        if parameter.parameter_type not in CANONICAL_TYPES:
            issues.append(
                ValidationIssue(
                    SEVERITY_ERROR,
                    "semantic.type",
                    f"{parameter.mnemonic}: unrecognized canonical type "
                    f"{parameter.parameter_type!r}",
                    parameter.id,
                )
            )
        elif parameter.parameter_type == TYPE_UNKNOWN:
            issues.append(
                ValidationIssue(
                    SEVERITY_WARNING,
                    "semantic.type",
                    f"{parameter.mnemonic}: source type "
                    f"{parameter.source_parameter_type!r} did not normalize; "
                    "parameter will decode as UNSUPPORTED_TYPE",
                    parameter.id,
                )
            )
        if parameter.parameter_type == TYPE_DISCRETE and not (
            parameter.true_state or parameter.false_state
        ):
            issues.append(
                ValidationIssue(
                    SEVERITY_WARNING,
                    "semantic.discrete_states",
                    f"{parameter.mnemonic}: discrete without state labels",
                    parameter.id,
                )
            )
        if (
            parameter.minimum is not None
            and parameter.maximum is not None
            and parameter.minimum > parameter.maximum
        ):
            issues.append(
                ValidationIssue(
                    SEVERITY_ERROR,
                    "semantic.range",
                    f"{parameter.mnemonic}: minimum {parameter.minimum:g} > "
                    f"maximum {parameter.maximum:g}",
                    parameter.id,
                )
            )

    issues.extend(_overlap_issues(dataframe))
    return issues


def _overlap_issues(dataframe: DataframeDefinition) -> list[ValidationIssue]:
    # (subframe, word) -> list of (parameter, bitmask)
    cells: dict[tuple[int, int], list[tuple[str, str, int]]] = {}
    for parameter in dataframe.parameters:
        for occurrence in parameter.occurrences:
            for segment in occurrence.segments:
                lo, hi = segment.bit_range
                if not (1 <= lo and hi <= WORD_BITS):
                    continue
                mask = ((1 << (hi - lo + 1)) - 1) << (lo - 1)
                for subframe in segment.subframes:
                    if not 1 <= subframe <= SUBFRAME_COUNT:
                        continue
                    cells.setdefault((subframe, segment.word), []).append(
                        (parameter.id, parameter.mnemonic, mask)
                    )

    issues: list[ValidationIssue] = []
    reported: set[tuple[str, str, int, int]] = set()
    for (subframe, word), entries in cells.items():
        for i, (id_a, name_a, mask_a) in enumerate(entries):
            for id_b, name_b, mask_b in entries[i + 1 :]:
                if id_a == id_b or not (mask_a & mask_b):
                    continue
                key = tuple(sorted((id_a, id_b))) + (subframe, word)
                if key in reported:
                    continue
                reported.add(key)
                issues.append(
                    ValidationIssue(
                        SEVERITY_WARNING,
                        "semantic.overlap",
                        f"{name_a} and {name_b} overlap in SF{subframe} "
                        f"word {word}",
                        id_a,
                        related_parameter_id=id_b,
                    )
                )
    return issues
