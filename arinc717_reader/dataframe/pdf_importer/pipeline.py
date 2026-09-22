"""End-to-end PDF import pipeline (design spec §28).

    PDF → ingestion → native or scanned extraction → raw rows
        → normalization → validation → review session

The session is handed to the review UI; only ``ImportSession.publish``
produces a canonical dataframe, and only from approved rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .extract import ImportIssue, ProgressCallback, RawParameterRow, extract_tables, rows_from_tables
from .ingest import detect_wps, ingest_pdf
from .normalize import (
    SEVERITY_INFO,
    ImportProfile,
    NormalizationIssue,
    effective_profile,
    normalize_raw_row,
)
from .review import STATE_NORMALIZED, ImportItem, ImportSession

PARAMETER_ID_PREFIX = "pdf"


def import_pdf(
    path: str | Path,
    profile: ImportProfile | None = None,
    progress: ProgressCallback | None = None,
) -> ImportSession:
    profile = profile or ImportProfile()
    if progress is not None:
        progress(0, 0, "opening document")
    document = ingest_pdf(path)
    tables, issues = extract_tables(
        document,
        profile.column_overrides,
        ocr=profile.ocr,
        dpi=profile.ocr_dpi,
        page_range=profile.page_range,
        progress=progress,
    )
    rows = rows_from_tables(tables)
    if not rows and not any(issue.code == "PDF_EXTRACTION_REVIEW_REQUIRED" for issue in issues):
        issues.append(
            ImportIssue(
                "error",
                "PDF_NO_PARAMETER_TABLE",
                "no parameter table with a recognizable header was found",
            )
        )
    scanned = [page.number for page in document.pages if page.is_scanned]
    if scanned:
        issues.insert(
            0,
            ImportIssue(
                "info",
                "PDF_SCANNED",
                f"{len(scanned)} of {document.page_count} page(s) are scans; "
                f"{sum(1 for p in document.pages if p.is_scanned and p.has_text_layer)} "
                "carry an embedded text layer, the rest need OCR",
            ),
        )
    if progress is not None:
        progress(document.page_count, document.page_count, "normalizing rows")
    detected = detect_wps(document.text)
    return build_session(
        rows,
        profile=profile,
        dataframe_name=Path(path).stem,
        wps=detected or profile.default_wps,
        detected_wps=detected,
        source_filename=document.filename,
        source_hash=document.sha256,
        source_path=str(document.path),
        page_count=document.page_count,
        tables_found=len(tables),
        issues=issues,
        aircraft_type=None,
    )


def build_session(
    rows: Iterable[RawParameterRow],
    *,
    profile: ImportProfile | None = None,
    dataframe_name: str = "imported",
    wps: int = 256,
    sync_words: list[int] | None = None,
    detected_wps: int | None = None,
    source_filename: str | None = None,
    source_hash: str | None = None,
    source_path: str | None = None,
    page_count: int = 0,
    tables_found: int = 0,
    issues: list[ImportIssue] | None = None,
    aircraft_type: str | None = None,
    revision: str | None = None,
    issue_date: str | None = None,
) -> ImportSession:
    """Normalize raw rows into a review session (usable without any PDF)."""
    profile = profile or ImportProfile()
    rows = list(rows)
    issues = list(issues or [])
    resolved, notes = effective_profile(rows, profile)
    for code, explanation in notes:
        issues.append(ImportIssue("info", code, explanation))

    items: list[ImportItem] = []
    for index, row in enumerate(rows):
        parameter_id = f"{PARAMETER_ID_PREFIX}-{index + 1:04d}"
        item = ImportItem(index=index, parameter_id=parameter_id, raw=row)
        result = normalize_raw_row(
            row,
            parameter_id,
            profile=resolved,
            source_filename=source_filename,
            wps=wps,
        )
        item.candidate = result.parameter
        item.normalization_issues = result.issues
        item.interpretation = result.interpretation
        item.transition(
            STATE_NORMALIZED,
            "normalized" if result.parameter is not None else "normalization failed",
        )
        items.append(item)
    qualify_duplicate_mnemonics(items)

    session = ImportSession(
        items,
        dataframe_name=dataframe_name,
        wps=wps,
        sync_words=sync_words,
        source_filename=source_filename,
        source_hash=source_hash,
        source_path=source_path,
        page_count=page_count,
        tables_found=tables_found,
        detected_wps=detected_wps,
        issues=issues,
        profile=profile,
        aircraft_type=aircraft_type,
        revision=revision,
        issue_date=issue_date,
    )
    session.effective = resolved
    session.frequency_unit = resolved.frequency_unit
    session.frequency_evidence = "; ".join(explanation for _, explanation in notes)
    session.revalidate()
    session.auto_approve()
    return session


def qualify_duplicate_mnemonics(items: list[ImportItem]) -> None:
    """Documents that stack a group label over the parameter name ("AP Armed
    Mode" / "Alt Mode Armed") repeat the label as the mnemonic; qualify such
    duplicates with the name so every parameter stays identifiable."""
    by_mnemonic: dict[str, list[ImportItem]] = {}
    for item in items:
        if item.candidate is not None:
            by_mnemonic.setdefault(item.candidate.mnemonic.upper(), []).append(item)
    for duplicates in by_mnemonic.values():
        if len(duplicates) < 2:
            continue
        for number, item in enumerate(duplicates, start=1):
            parameter = item.candidate
            original = parameter.mnemonic
            if parameter.description and parameter.description.upper() != original.upper():
                parameter.mnemonic = f"{original}: {parameter.description}"
            else:
                parameter.mnemonic = f"{original} #{number}"
            item.normalization_issues.append(
                NormalizationIssue(
                    SEVERITY_INFO,
                    "normalize.mnemonic_qualified",
                    f"mnemonic {original!r} appears {len(duplicates)} times in the document; "
                    f"qualified as {parameter.mnemonic!r}",
                    "parameter_name",
                )
            )
            item.interpretation["mnemonic"] = f"{original!r} qualified → {parameter.mnemonic!r}"


def renormalize_session(session: ImportSession, profile: ImportProfile) -> ImportSession:
    """Re-run normalization with new conventions; review decisions are reset."""
    keep = {
        issue.code for issue in session.issues
        if issue.code not in ("PDF_FREQUENCY_UNIT", "PDF_BITS_LAYOUT")
    }
    fresh = build_session(
        [item.raw for item in session.items],
        profile=profile,
        dataframe_name=session.dataframe_name,
        wps=session.wps,
        sync_words=session.sync_words,
        detected_wps=session.detected_wps,
        source_filename=session.source_filename,
        source_hash=session.source_hash,
        source_path=session.source_path,
        page_count=session.page_count,
        tables_found=session.tables_found,
        issues=[issue for issue in session.issues if issue.code in keep],
        aircraft_type=session.aircraft_type,
        revision=session.revision,
        issue_date=session.issue_date,
    )
    for old, new in zip(session.items, fresh.items):
        if old.excluded:
            fresh.exclude(new.index, "excluded before re-normalization")
    return fresh
