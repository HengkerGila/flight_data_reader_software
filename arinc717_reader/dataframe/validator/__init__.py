"""Layered dataframe validation (design spec §31)."""

from __future__ import annotations

from dataclasses import dataclass

from ...domain.dataframe import DataframeDefinition

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass
class ValidationIssue:
    severity: str
    rule_name: str
    message: str
    parameter_id: str | None = None
    resolved: bool = False
    # Second parameter involved in a pairwise issue (e.g. an overlap), so a
    # per-parameter view can show the issue on both sides.
    related_parameter_id: str | None = None

    def concerns(self, parameter_id: str) -> bool:
        return parameter_id in (self.parameter_id, self.related_parameter_id)


def validate_dataframe(dataframe: DataframeDefinition) -> list[ValidationIssue]:
    from . import mapping, semantic, structural

    issues: list[ValidationIssue] = []
    issues.extend(structural.validate(dataframe))
    issues.extend(mapping.validate(dataframe))
    issues.extend(semantic.validate(dataframe))
    return issues


def error_count(issues: list[ValidationIssue]) -> int:
    return sum(1 for issue in issues if issue.severity == SEVERITY_ERROR)


def warning_count(issues: list[ValidationIssue]) -> int:
    return sum(1 for issue in issues if issue.severity == SEVERITY_WARNING)
