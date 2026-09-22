"""Manual review state machine and import session (design spec §31.5, §32).

Every extracted row is an ``ImportItem`` that moves through

    EXTRACTED → NORMALIZED → VALIDATED → APPROVED → PUBLISHED
                          ↘ REVIEW_REQUIRED ↗

A row that normalized and validated without any issue may be approved
automatically (spec §32); any normalization error/warning or validation
error sends it to REVIEW_REQUIRED, where a human edits, approves or excludes
it.  Publishing produces the canonical dataframe from APPROVED rows only —
the runtime never decodes with an unreviewed row.

Beyond the spec's transitions, two "back" edges exist so a reviewer can
re-open a row (VALIDATED/APPROVED → NORMALIZED after an edit) and so an
already-approved row drops back to REVIEW_REQUIRED when its validation
changes underneath it (for example after the WPS is corrected).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ...domain.dataframe import DataframeDefinition, DataframeMetadata
from ...domain.parameter import ParameterDefinition
from ..editor import DEFAULT_SYNC_WORDS
from ..validator import SEVERITY_ERROR, ValidationIssue, validate_dataframe
from .normalize import (
    SEVERITY_ERROR as NORMALIZE_ERROR,
    ImportProfile,
    NormalizationIssue,
)
from .raw import ImportIssue, RawParameterRow

STATE_EXTRACTED = "EXTRACTED"
STATE_NORMALIZED = "NORMALIZED"
STATE_VALIDATED = "VALIDATED"
STATE_REVIEW_REQUIRED = "REVIEW_REQUIRED"
STATE_APPROVED = "APPROVED"
STATE_PUBLISHED = "PUBLISHED"

REVIEW_STATES = (
    STATE_EXTRACTED,
    STATE_NORMALIZED,
    STATE_VALIDATED,
    STATE_REVIEW_REQUIRED,
    STATE_APPROVED,
    STATE_PUBLISHED,
)

ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATE_EXTRACTED: (STATE_NORMALIZED,),
    STATE_NORMALIZED: (STATE_VALIDATED, STATE_REVIEW_REQUIRED),
    STATE_VALIDATED: (STATE_APPROVED, STATE_REVIEW_REQUIRED, STATE_NORMALIZED),
    STATE_REVIEW_REQUIRED: (STATE_APPROVED, STATE_NORMALIZED),
    STATE_APPROVED: (STATE_PUBLISHED, STATE_REVIEW_REQUIRED, STATE_NORMALIZED),
    STATE_PUBLISHED: (),
}


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in ALLOWED_TRANSITIONS.get(from_state, ())


class ReviewError(ValueError):
    """A review operation that the state machine does not allow."""


@dataclass
class ReviewEvent:
    from_state: str | None
    to_state: str
    note: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


@dataclass
class ImportItem:
    index: int
    parameter_id: str
    raw: RawParameterRow
    candidate: ParameterDefinition | None = None
    normalization_issues: list[NormalizationIssue] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    interpretation: dict[str, str] = field(default_factory=dict)
    state: str = STATE_EXTRACTED
    excluded: bool = False
    history: list[ReviewEvent] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        if self.candidate is not None:
            return self.candidate.mnemonic
        return self.raw.raw("parameter_name") or f"row {self.index + 1}"

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.normalization_issues if i.severity == NORMALIZE_ERROR) + sum(
            1 for i in self.validation_issues if i.severity == SEVERITY_ERROR
        )

    @property
    def warning_count(self) -> int:
        return sum(
            1 for i in self.normalization_issues if i.severity == "warning"
        ) + sum(1 for i in self.validation_issues if i.severity != SEVERITY_ERROR)

    @property
    def needs_review(self) -> bool:
        """Errors anywhere, or normalization warnings (spec §31.5)."""
        return (
            self.candidate is None
            or any(i.needs_review for i in self.normalization_issues)
            or any(i.severity == SEVERITY_ERROR for i in self.validation_issues)
        )

    @property
    def clean(self) -> bool:
        return (
            self.candidate is not None
            and not any(i.needs_review for i in self.normalization_issues)
            and not self.validation_issues
        )

    @property
    def included(self) -> bool:
        return not self.excluded and self.candidate is not None

    def transition(self, to_state: str, note: str) -> None:
        if not can_transition(self.state, to_state):
            raise ReviewError(
                f"{self.display_name}: cannot move from {self.state} to {to_state}"
            )
        self.history.append(ReviewEvent(self.state, to_state, note))
        self.state = to_state


class ImportSession:
    """One PDF import in progress: rows under review plus document metadata."""

    def __init__(
        self,
        items: list[ImportItem],
        *,
        dataframe_name: str = "imported",
        wps: int = 256,
        sync_words: list[int] | None = None,
        source_filename: str | None = None,
        source_hash: str | None = None,
        source_path: str | None = None,
        page_count: int = 0,
        tables_found: int = 0,
        detected_wps: int | None = None,
        issues: list[ImportIssue] | None = None,
        profile: ImportProfile | None = None,
        aircraft_type: str | None = None,
        revision: str | None = None,
        issue_date: str | None = None,
    ):
        self.items = items
        self.dataframe_name = dataframe_name
        self.wps = int(wps)
        self.sync_words = list(DEFAULT_SYNC_WORDS if sync_words is None else sync_words)
        self.source_filename = source_filename
        self.source_hash = source_hash
        self.source_path = source_path
        self.page_count = page_count
        self.tables_found = tables_found
        self.detected_wps = detected_wps
        self.issues = list(issues or [])  # document-level extraction issues
        self.session_validation_issues: list[ValidationIssue] = []
        self.profile = profile or ImportProfile()
        # The profile with "auto" conventions resolved from document evidence.
        self.effective: ImportProfile = self.profile
        self.aircraft_type = aircraft_type
        self.revision = revision
        self.issue_date = issue_date
        self.published: DataframeDefinition | None = None
        # How the frequency column was read ("hz" / "seconds") and why.
        self.frequency_unit: str = "hz"
        self.frequency_evidence: str = ""

    # -- metadata -------------------------------------------------------------

    def set_metadata(
        self,
        *,
        dataframe_name: str | None = None,
        wps: int | None = None,
        sync_words: list[int] | None = None,
        aircraft_type: str | None = None,
        revision: str | None = None,
        issue_date: str | None = None,
    ) -> None:
        if dataframe_name is not None:
            self.dataframe_name = dataframe_name.strip() or "imported"
        if wps is not None:
            if int(wps) <= 0:
                raise ReviewError(f"WPS must be positive, got {wps}")
            self.wps = int(wps)
        if sync_words is not None:
            self.sync_words = [int(w) for w in sync_words]
        if aircraft_type is not None:
            self.aircraft_type = aircraft_type or None
        if revision is not None:
            self.revision = revision or None
        if issue_date is not None:
            self.issue_date = issue_date or None
        self.revalidate()

    def metadata(self, source_type: str = "pdf") -> DataframeMetadata:
        return DataframeMetadata(
            dataframe_name=self.dataframe_name,
            wps=self.wps,
            aircraft_type=self.aircraft_type,
            revision=self.revision,
            issue_date=self.issue_date,
            sync_words=list(self.sync_words),
            source_type=source_type,
            source_filename=self.source_filename,
            source_hash=self.source_hash,
        )

    # -- queries ---------------------------------------------------------------

    def item(self, index: int) -> ImportItem:
        try:
            return self.items[index]
        except IndexError as exc:
            raise ReviewError(f"no import row {index}") from exc

    def included_items(self) -> list[ImportItem]:
        return [item for item in self.items if item.included]

    def pending_items(self) -> list[ImportItem]:
        """Included rows that are not approved (or published) yet."""
        return [
            item
            for item in self.items
            if not item.excluded and item.state not in (STATE_APPROVED, STATE_PUBLISHED)
        ]

    def state_counts(self) -> dict[str, int]:
        counts = {state: 0 for state in REVIEW_STATES}
        counts["EXCLUDED"] = 0
        for item in self.items:
            if item.excluded:
                counts["EXCLUDED"] += 1
            else:
                counts[item.state] += 1
        return counts

    def preview_dataframe(self, include_excluded: bool = False) -> DataframeDefinition:
        parameters = [
            item.candidate
            for item in self.items
            if item.candidate is not None and (include_excluded or not item.excluded)
        ]
        return DataframeDefinition(metadata=self.metadata(), parameters=parameters)

    # -- state machine ---------------------------------------------------------

    def revalidate(self) -> None:
        """Validate all included candidates together and settle states."""
        preview = self.preview_dataframe()
        by_id: dict[str, list[ValidationIssue]] = {}
        session_issues: list[ValidationIssue] = []
        for issue in validate_dataframe(preview):
            if not issue.parameter_id:
                session_issues.append(issue)
                continue
            by_id.setdefault(issue.parameter_id, []).append(issue)
            if issue.related_parameter_id:  # pairwise issues show on both rows
                by_id.setdefault(issue.related_parameter_id, []).append(issue)
        self.session_validation_issues = session_issues
        for item in self.items:
            if item.excluded:
                continue
            item.validation_issues = by_id.get(item.parameter_id, [])
            self._settle(item)

    def _settle(self, item: ImportItem) -> None:
        if item.state in (STATE_EXTRACTED, STATE_PUBLISHED):
            return
        if item.state == STATE_APPROVED:
            if any(i.severity == SEVERITY_ERROR for i in item.validation_issues):
                item.transition(STATE_REVIEW_REQUIRED, "validation errors appeared after approval")
            return
        target = STATE_REVIEW_REQUIRED if item.needs_review else STATE_VALIDATED
        if item.state == target:
            return
        if item.state == STATE_REVIEW_REQUIRED and target == STATE_VALIDATED:
            item.transition(STATE_NORMALIZED, "re-checked")
        item.transition(target, self._settle_note(item, target))

    @staticmethod
    def _settle_note(item: ImportItem, target: str) -> str:
        if target == STATE_VALIDATED:
            return "validated"
        reasons = [i.message for i in item.normalization_issues if i.needs_review]
        reasons += [i.message for i in item.validation_issues if i.severity == SEVERITY_ERROR]
        if item.candidate is None and not reasons:
            reasons.append("normalization produced no parameter")
        return "review required: " + "; ".join(reasons[:3])

    def auto_approve(self) -> int:
        """VALIDATED rows without any issue at all → APPROVED (spec §32)."""
        count = 0
        for item in self.items:
            if not item.excluded and item.state == STATE_VALIDATED and item.clean:
                item.transition(STATE_APPROVED, "auto-approved: validated with no issues")
                count += 1
        return count

    def approve(self, index: int, note: str = "approved by reviewer") -> ImportItem:
        item = self.item(index)
        if item.excluded:
            raise ReviewError(f"{item.display_name}: excluded rows cannot be approved")
        if item.candidate is None:
            raise ReviewError(
                f"{item.display_name}: no parameter could be built from this row — edit it first"
            )
        if item.error_count:
            raise ReviewError(
                f"{item.display_name}: errors must be fixed (Edit…) before approval"
            )
        if item.state == STATE_APPROVED:
            return item
        item.transition(STATE_APPROVED, note)
        return item

    def approve_all_validated(self) -> int:
        count = 0
        for item in self.items:
            if not item.excluded and item.state == STATE_VALIDATED:
                self.approve(item.index, "approved by reviewer (all validated)")
                count += 1
        return count

    def exclude(self, index: int, note: str = "excluded by reviewer") -> ImportItem:
        item = self.item(index)
        if item.state == STATE_PUBLISHED:
            raise ReviewError(f"{item.display_name}: already published")
        if not item.excluded:
            item.excluded = True
            item.history.append(ReviewEvent(item.state, item.state, note))
            self.revalidate()
        return item

    def include(self, index: int, note: str = "included again by reviewer") -> ImportItem:
        item = self.item(index)
        if item.excluded:
            item.excluded = False
            item.history.append(ReviewEvent(item.state, item.state, note))
            self.revalidate()
        return item

    def update_candidate(
        self, index: int, parameter: ParameterDefinition, note: str = "edited by reviewer"
    ) -> ImportItem:
        """Replace a row's candidate after a manual edit and re-run validation.

        The reviewer has looked at the row, so it is approved when it
        validates without errors; remaining warnings stay visible.
        """
        item = self.item(index)
        if item.state == STATE_PUBLISHED:
            raise ReviewError(f"{item.display_name}: already published")
        parameter.id = item.parameter_id
        if parameter.provenance is None and item.candidate is not None:
            parameter.provenance = item.candidate.provenance
        resolved = [i.message for i in item.normalization_issues if i.needs_review]
        item.candidate = parameter
        item.normalization_issues = []
        if item.state == STATE_EXTRACTED:
            item.transition(STATE_NORMALIZED, note)
        elif item.state != STATE_NORMALIZED:
            item.transition(STATE_NORMALIZED, note)
        if resolved:
            item.history.append(
                ReviewEvent(item.state, item.state, "resolved by edit: " + "; ".join(resolved))
            )
        item.excluded = False
        self.revalidate()
        if item.state == STATE_VALIDATED:
            item.transition(STATE_APPROVED, "approved after manual edit")
        return item

    # -- publish -----------------------------------------------------------------

    def publish(self) -> DataframeDefinition:
        pending = self.pending_items()
        if pending:
            names = ", ".join(item.display_name for item in pending[:5])
            more = f" (+{len(pending) - 5} more)" if len(pending) > 5 else ""
            raise ReviewError(
                f"{len(pending)} row(s) still need review or approval: {names}{more}"
            )
        approved = [
            item for item in self.items if not item.excluded and item.state == STATE_APPROVED
        ]
        if not approved:
            raise ReviewError("nothing to publish: no approved parameters")
        parameters: list[ParameterDefinition] = []
        for item in approved:
            item.transition(STATE_PUBLISHED, "published")
            parameter = item.candidate
            if parameter.provenance is not None:
                parameter.provenance.extra["review_history"] = [
                    f"{event.timestamp} {event.from_state or '-'} → {event.to_state}: {event.note}"
                    for event in item.history
                ]
            parameters.append(parameter)
        self.published = DataframeDefinition(metadata=self.metadata(), parameters=parameters)
        return self.published
