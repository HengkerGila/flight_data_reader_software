"""Structural validation (design spec §31.1)."""

from __future__ import annotations

from ...domain.dataframe import DataframeDefinition
from ...domain.frame import SUBFRAME_COUNT, WORD_BITS, WORD_MAX
from ...domain.parameter import TYPE_ANALOG_SIGNED, TYPE_ANALOG_UNSIGNED, TYPE_BCD
from . import SEVERITY_ERROR, SEVERITY_WARNING, ValidationIssue


def validate(dataframe: DataframeDefinition) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    wps = dataframe.metadata.wps

    if wps <= 0:
        issues.append(
            ValidationIssue(SEVERITY_ERROR, "structural.wps", f"invalid WPS {wps}")
        )
    if len(dataframe.metadata.sync_words) != SUBFRAME_COUNT:
        issues.append(
            ValidationIssue(
                SEVERITY_WARNING,
                "structural.sync_words",
                f"{len(dataframe.metadata.sync_words)} sync words defined, "
                f"expected {SUBFRAME_COUNT}",
            )
        )
    for sync in dataframe.metadata.sync_words:
        if not 0 <= sync <= WORD_MAX:
            issues.append(
                ValidationIssue(
                    SEVERITY_ERROR,
                    "structural.sync_words",
                    f"sync word {sync} outside 0..{WORD_MAX}",
                )
            )

    for parameter in dataframe.parameters:
        if not parameter.occurrences:
            issues.append(
                ValidationIssue(
                    SEVERITY_ERROR,
                    "structural.mapping_empty",
                    f"{parameter.mnemonic}: parameter has no mapping",
                    parameter.id,
                )
            )
        for occurrence in parameter.occurrences:
            if not occurrence.segments:
                issues.append(
                    ValidationIssue(
                        SEVERITY_ERROR,
                        "structural.mapping_empty",
                        f"{parameter.mnemonic}: occurrence {occurrence.index} "
                        "has no segments",
                        parameter.id,
                    )
                )
            for segment in occurrence.segments:
                where = (
                    f"{parameter.mnemonic} occ {occurrence.index} "
                    f"seg {segment.sequence}"
                )
                if not 1 <= segment.word <= wps:
                    issues.append(
                        ValidationIssue(
                            SEVERITY_ERROR,
                            "structural.word_range",
                            f"{where}: word {segment.word} outside 1..{wps}",
                            parameter.id,
                        )
                    )
                for name, bit in (("lsb", segment.lsb), ("msb", segment.msb)):
                    if not 1 <= bit <= WORD_BITS:
                        issues.append(
                            ValidationIssue(
                                SEVERITY_ERROR,
                                "structural.bit_range",
                                f"{where}: {name} {bit} outside 1..{WORD_BITS}",
                                parameter.id,
                            )
                        )
                bad = [sf for sf in segment.subframes if not 1 <= sf <= SUBFRAME_COUNT]
                if bad or not segment.subframes:
                    issues.append(
                        ValidationIssue(
                            SEVERITY_ERROR,
                            "structural.subframe",
                            f"{where}: invalid subframes {segment.subframes}",
                            parameter.id,
                        )
                    )

        if parameter.parameter_type in (
            TYPE_ANALOG_SIGNED,
            TYPE_ANALOG_UNSIGNED,
            TYPE_BCD,
        ):
            if parameter.conversion.resolution == 0:
                issues.append(
                    ValidationIssue(
                        SEVERITY_ERROR,
                        "structural.resolution",
                        f"{parameter.mnemonic}: resolution must be non-zero",
                        parameter.id,
                    )
                )
            if parameter.conversion.formula_type != "linear":
                issues.append(
                    ValidationIssue(
                        SEVERITY_WARNING,
                        "structural.formula",
                        f"{parameter.mnemonic}: unsupported formula_type "
                        f"{parameter.conversion.formula_type!r}",
                        parameter.id,
                    )
                )
    return issues
