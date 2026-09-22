"""Holds the currently loaded canonical dataframe and its validation state."""

from __future__ import annotations

from ..dataframe.validator import ValidationIssue
from ..domain.dataframe import DataframeDefinition
from .base import Observable


class DataframeStore(Observable):
    def __init__(self) -> None:
        super().__init__()
        self.dataframe: DataframeDefinition | None = None
        self.issues: list[ValidationIssue] = []
        # True when the in-memory dataframe has edits not yet exported.
        self.dirty: bool = False

    def set_dataframe(
        self,
        dataframe: DataframeDefinition | None,
        issues: list[ValidationIssue] | None = None,
        dirty: bool = False,
    ) -> None:
        self.dataframe = dataframe
        self.issues = list(issues or [])
        self.dirty = dirty
        self._notify({"type": "dataframe"})

    def mark_clean(self) -> None:
        if self.dirty:
            self.dirty = False
            self._notify({"type": "dirty"})

    def set_issues(self, issues: list[ValidationIssue]) -> None:
        self.issues = list(issues)
        self._notify({"type": "validation"})

    def clear(self) -> None:
        self.set_dataframe(None, [])
