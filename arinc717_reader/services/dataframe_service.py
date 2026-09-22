"""Dataframe load / create / edit / validate / export service.

Every edit replaces the store's dataframe through ``set_dataframe`` so the
"dataframe" event fires: the decoding service re-decodes the current frame
and every page re-renders.  Edits are committed on already-validated copies;
a failed edit leaves the store untouched.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path

from ..dataframe.adb_codec import AdbParseError, parse_adb_file, write_adb_file
from ..dataframe.editor import duplicate_parameter, make_parameter_id, new_dataframe
from ..dataframe.pdf_importer import (
    ImportProfile,
    ImportSession,
    PdfImportNotAvailable,
    PdfIngestError,
    ReviewError,
    import_pdf,
)
from ..dataframe.validator import ValidationIssue, error_count, validate_dataframe
from ..domain.dataframe import DataframeDefinition
from ..domain.parameter import ParameterDefinition
from ..state.dataframe_store import DataframeStore
from . import ServiceError

logger = logging.getLogger(__name__)

EDITABLE_METADATA_FIELDS = (
    "dataframe_name",
    "wps",
    "aircraft_type",
    "revision",
    "issue_date",
    "superframe_present",
    "sync_words",
)


class DataframeService:
    def __init__(self, dataframe_store: DataframeStore):
        self._store = dataframe_store

    # -- load / create ------------------------------------------------------

    def load_adb(self, path: str | Path) -> tuple[DataframeDefinition, list[ValidationIssue]]:
        try:
            dataframe = parse_adb_file(path)
        except (OSError, AdbParseError) as exc:
            raise ServiceError("ADB_PARSE_ERROR", str(exc)) from exc
        issues = validate_dataframe(dataframe)
        self._store.set_dataframe(dataframe, issues)
        logger.info(
            "event=adb_loaded file=%s wps=%d parameters=%d errors=%d",
            path,
            dataframe.metadata.wps,
            len(dataframe.parameters),
            error_count(issues),
        )
        return dataframe, issues

    def import_pdf(
        self,
        path: str | Path,
        profile: ImportProfile | None = None,
        progress=None,
    ) -> ImportSession:
        """Run the PDF pipeline up to the review session (spec §28).

        Nothing reaches the store here: only ``publish_import`` does, and only
        with approved rows.  ``progress(done, total, message)`` is called per
        page when given.
        """
        try:
            session = import_pdf(path, profile, progress=progress)
        except PdfImportNotAvailable as exc:
            raise ServiceError("PDF_IMPORT_UNAVAILABLE", str(exc)) from exc
        except (OSError, PdfIngestError) as exc:
            raise ServiceError("PDF_INGEST_ERROR", str(exc)) from exc
        counts = session.state_counts()
        logger.info(
            "event=pdf_imported file=%s pages=%d tables=%d rows=%d approved=%d review=%d",
            path,
            session.page_count,
            session.tables_found,
            len(session.items),
            counts["APPROVED"],
            counts["REVIEW_REQUIRED"],
        )
        return session

    def publish_import(
        self, session: ImportSession
    ) -> tuple[DataframeDefinition, list[ValidationIssue]]:
        """Publish an import session's approved rows as the loaded dataframe."""
        try:
            dataframe = session.publish()
        except ReviewError as exc:
            raise ServiceError("PDF_EXTRACTION_REVIEW_REQUIRED", str(exc)) from exc
        issues = self._commit(
            dataframe,
            "pdf_published",
            file=session.source_filename,
            parameters=len(dataframe.parameters),
        )
        return dataframe, issues

    def set_dataframe(self, dataframe: DataframeDefinition) -> list[ValidationIssue]:
        issues = validate_dataframe(dataframe)
        self._store.set_dataframe(dataframe, issues)
        return issues

    def new_dataframe(self, name: str, wps: int, **metadata) -> DataframeDefinition:
        """Start an empty manual dataframe (spec §1.1 "manually created")."""
        if int(wps) <= 0:
            raise ServiceError("INVALID_WPS", f"WPS must be positive, got {wps}")
        dataframe = new_dataframe(name, wps, **metadata)
        issues = validate_dataframe(dataframe)
        self._store.set_dataframe(dataframe, issues, dirty=True)
        logger.info("event=dataframe_created name=%s wps=%d", name, int(wps))
        return dataframe

    # -- edit ----------------------------------------------------------------

    def _require(self) -> DataframeDefinition:
        if self._store.dataframe is None:
            raise ServiceError("MISSING_DATAFRAME", "no dataframe loaded")
        return self._store.dataframe

    def _commit(self, dataframe: DataframeDefinition, event: str, **fields) -> list[ValidationIssue]:
        issues = validate_dataframe(dataframe)
        self._store.set_dataframe(dataframe, issues, dirty=True)
        logger.info(
            "event=%s parameters=%d errors=%d %s",
            event,
            len(dataframe.parameters),
            error_count(issues),
            " ".join(f"{k}={v}" for k, v in fields.items()),
        )
        return issues

    def update_metadata(self, **fields) -> list[ValidationIssue]:
        current = self._require()
        unknown = sorted(set(fields) - set(EDITABLE_METADATA_FIELDS))
        if unknown:
            raise ServiceError("INVALID_FIELD", f"not editable: {unknown}")
        if "wps" in fields and int(fields["wps"]) <= 0:
            raise ServiceError("INVALID_WPS", f"WPS must be positive, got {fields['wps']}")
        dataframe = copy.copy(current)
        dataframe.metadata = copy.copy(current.metadata)
        for key, value in fields.items():
            if key == "sync_words":
                value = [int(v) for v in value]
            elif key == "wps":
                value = int(value)
            setattr(dataframe.metadata, key, value)
        return self._commit(dataframe, "dataframe_metadata_updated", **fields)

    def add_parameter(self, parameter: ParameterDefinition) -> ParameterDefinition:
        current = self._require()
        existing = [p.id for p in current.parameters]
        if not parameter.id:
            parameter.id = make_parameter_id(parameter.mnemonic, existing)
        elif parameter.id in existing:
            raise ServiceError("DUPLICATE_ID", f"parameter id {parameter.id!r} already exists")
        dataframe = copy.copy(current)
        dataframe.parameters = [*current.parameters, parameter]
        self._commit(dataframe, "parameter_added", id=parameter.id)
        return parameter

    def update_parameter(self, parameter: ParameterDefinition) -> list[ValidationIssue]:
        current = self._require()
        if current.get_parameter(parameter.id) is None:
            raise ServiceError("UNKNOWN_PARAMETER", f"no parameter with id {parameter.id!r}")
        dataframe = copy.copy(current)
        dataframe.parameters = [
            parameter if p.id == parameter.id else p for p in current.parameters
        ]
        return self._commit(dataframe, "parameter_updated", id=parameter.id)

    def remove_parameter(self, parameter_id: str) -> list[ValidationIssue]:
        current = self._require()
        if current.get_parameter(parameter_id) is None:
            raise ServiceError("UNKNOWN_PARAMETER", f"no parameter with id {parameter_id!r}")
        dataframe = copy.copy(current)
        dataframe.parameters = [p for p in current.parameters if p.id != parameter_id]
        return self._commit(dataframe, "parameter_removed", id=parameter_id)

    def duplicate_parameter(self, parameter_id: str) -> ParameterDefinition:
        current = self._require()
        source = current.get_parameter(parameter_id)
        if source is None:
            raise ServiceError("UNKNOWN_PARAMETER", f"no parameter with id {parameter_id!r}")
        clone = duplicate_parameter(source, (p.id for p in current.parameters))
        dataframe = copy.copy(current)
        position = current.parameters.index(source) + 1
        dataframe.parameters = [
            *current.parameters[:position],
            clone,
            *current.parameters[position:],
        ]
        self._commit(dataframe, "parameter_duplicated", id=clone.id, source=parameter_id)
        return clone

    def move_parameter(self, parameter_id: str, delta: int) -> None:
        """Reorder a parameter (record order is the ADB export order)."""
        current = self._require()
        parameters = list(current.parameters)
        ids = [p.id for p in parameters]
        if parameter_id not in ids:
            raise ServiceError("UNKNOWN_PARAMETER", f"no parameter with id {parameter_id!r}")
        index = ids.index(parameter_id)
        target = max(0, min(len(parameters) - 1, index + delta))
        if target == index:
            return
        parameters.insert(target, parameters.pop(index))
        dataframe = copy.copy(current)
        dataframe.parameters = parameters
        self._commit(dataframe, "parameter_moved", id=parameter_id, position=target)

    # -- validate / export --------------------------------------------------

    def revalidate(self) -> list[ValidationIssue]:
        issues = validate_dataframe(self._require())
        self._store.set_issues(issues)
        return issues

    def export_adb(self, path: str | Path) -> None:
        dataframe = self._require()
        try:
            write_adb_file(path, dataframe)
        except OSError as exc:
            raise ServiceError("ADB_WRITE_ERROR", str(exc)) from exc
        self._store.mark_clean()
        logger.info("event=adb_exported file=%s", path)
