"""Dataframe persistence over SQLite (design spec §38, §39).

Raw ADB records are preserved through the database: the settings record and
each parameter's raw record are stored field-by-field in ``adb_legacy_fields``
and reattached on load, so exporting after a save/load loses nothing.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ...domain.dataframe import DataframeDefinition, DataframeMetadata
from ...domain.parameter import (
    ConversionRule,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterProvenance,
    ParameterSegment,
)
from ...domain.parameter import DiscreteState
from ..adb_codec.mappings import decode_subframe_selector, encode_subframe_selector
from ..validator import ValidationIssue
from .schema import init_schema

SCOPE_SETTINGS = "settings"
SCOPE_PARAMETER_RECORD = "parameter_record"
SCOPE_PARAMETER_TRAILING = "parameter_trailing"


class DataframeRepository:
    def __init__(self, path: str | Path = ":memory:"):
        self._connection = sqlite3.connect(str(path))
        self._connection.row_factory = sqlite3.Row
        init_schema(self._connection)

    def close(self) -> None:
        self._connection.close()

    # -- save ---------------------------------------------------------------

    def save_dataframe(
        self, dataframe: DataframeDefinition, status: str = "DRAFT"
    ) -> int:
        md = dataframe.metadata
        cursor = self._connection.execute(
            """
            INSERT INTO dataframe_documents
                (name, aircraft_type, revision, issue_date, wps,
                 superframe_present, sync_words, source_type,
                 source_filename, source_hash, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                md.dataframe_name,
                md.aircraft_type,
                md.revision,
                md.issue_date,
                md.wps,
                None if md.superframe_present is None else int(md.superframe_present),
                json.dumps(md.sync_words),
                md.source_type,
                md.source_filename,
                md.source_hash,
                status,
            ),
        )
        document_id = cursor.lastrowid
        if md.adb_settings_raw:
            self._save_legacy_fields(
                document_id, None, SCOPE_SETTINGS, md.adb_settings_raw
            )
        for parameter in dataframe.parameters:
            self._save_parameter(document_id, parameter)
        self._record_review(document_id, None, None, status, "saved")
        self._connection.commit()
        return document_id

    def _save_parameter(self, document_id: int, p: ParameterDefinition) -> int:
        cursor = self._connection.execute(
            """
            INSERT INTO parameters
                (document_id, param_key, mnemonic, description,
                 source_parameter_type, parameter_type, unit, minimum, maximum,
                 resolution, conversion_offset, formula_type,
                 true_state, false_state, notes, decimals, states)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                p.id,
                p.mnemonic,
                p.description,
                p.source_parameter_type,
                p.parameter_type,
                p.unit,
                p.minimum,
                p.maximum,
                p.conversion.resolution,
                p.conversion.offset,
                p.conversion.formula_type,
                p.true_state,
                p.false_state,
                p.notes,
                p.decimals,
                json.dumps([[s.value, s.label] for s in p.states]) if p.states else None,
            ),
        )
        parameter_row_id = cursor.lastrowid
        for occurrence in p.occurrences:
            occ_cursor = self._connection.execute(
                "INSERT INTO parameter_occurrences (parameter_id, occurrence_index)"
                " VALUES (?, ?)",
                (parameter_row_id, occurrence.index),
            )
            occurrence_row_id = occ_cursor.lastrowid
            for segment in occurrence.segments:
                source_raw = segment.source_raw or {}
                selector = source_raw.get(
                    "subframe_selector_raw"
                ) or encode_subframe_selector(segment.subframes)
                self._connection.execute(
                    """
                    INSERT INTO parameter_segments
                        (occurrence_id, sequence, subframe_selector_raw,
                         word, lsb, msb, legacy_flag, bcd_weight)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        occurrence_row_id,
                        segment.sequence,
                        selector,
                        segment.word,
                        segment.lsb,
                        segment.msb,
                        source_raw.get("legacy_flag"),
                        segment.bcd_weight,
                    ),
                )
        if p.provenance:
            if p.provenance.raw_record:
                self._save_legacy_fields(
                    document_id,
                    parameter_row_id,
                    SCOPE_PARAMETER_RECORD,
                    p.provenance.raw_record,
                )
            trailing = p.provenance.extra.get("trailing_fields")
            if trailing:
                self._save_legacy_fields(
                    document_id, parameter_row_id, SCOPE_PARAMETER_TRAILING, trailing
                )
        return parameter_row_id

    def _save_legacy_fields(self, document_id, parameter_id, scope, values) -> None:
        self._connection.executemany(
            "INSERT INTO adb_legacy_fields"
            " (document_id, parameter_id, scope, position, value)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (document_id, parameter_id, scope, position, value)
                for position, value in enumerate(values)
            ],
        )

    def save_validation_issues(
        self, document_id: int, issues: list[ValidationIssue]
    ) -> None:
        self._connection.execute(
            "DELETE FROM validation_issues WHERE document_id = ?", (document_id,)
        )
        self._connection.executemany(
            "INSERT INTO validation_issues"
            " (document_id, parameter_id, severity, rule_name, message, resolved)"
            " VALUES (?, NULL, ?, ?, ?, ?)",
            [
                (document_id, i.severity, i.rule_name, i.message, int(i.resolved))
                for i in issues
            ],
        )
        self._connection.commit()

    def _record_review(self, document_id, parameter_id, from_state, to_state, note):
        self._connection.execute(
            "INSERT INTO review_history"
            " (document_id, parameter_id, from_state, to_state, timestamp, note)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                document_id,
                parameter_id,
                from_state,
                to_state,
                datetime.now(timezone.utc).isoformat(),
                note,
            ),
        )

    # -- load ---------------------------------------------------------------

    def list_documents(self) -> list[sqlite3.Row]:
        return list(
            self._connection.execute(
                "SELECT id, name, aircraft_type, wps, source_type, status"
                " FROM dataframe_documents ORDER BY id"
            )
        )

    def load_dataframe(self, document_id: int) -> DataframeDefinition:
        doc = self._connection.execute(
            "SELECT * FROM dataframe_documents WHERE id = ?", (document_id,)
        ).fetchone()
        if doc is None:
            raise KeyError(f"no dataframe document with id {document_id}")

        settings_raw = self._load_legacy_fields(document_id, None, SCOPE_SETTINGS)
        metadata = DataframeMetadata(
            dataframe_name=doc["name"],
            wps=doc["wps"],
            aircraft_type=doc["aircraft_type"],
            revision=doc["revision"],
            issue_date=doc["issue_date"],
            superframe_present=(
                None
                if doc["superframe_present"] is None
                else bool(doc["superframe_present"])
            ),
            sync_words=json.loads(doc["sync_words"]),
            source_type=doc["source_type"] or "manual",
            source_filename=doc["source_filename"],
            source_hash=doc["source_hash"],
            adb_settings_raw=tuple(settings_raw) if settings_raw else None,
        )

        parameters: list[ParameterDefinition] = []
        for row in self._connection.execute(
            "SELECT * FROM parameters WHERE document_id = ? ORDER BY id",
            (document_id,),
        ):
            parameters.append(self._load_parameter(document_id, row))
        return DataframeDefinition(metadata=metadata, parameters=parameters)

    def _load_parameter(self, document_id: int, row: sqlite3.Row) -> ParameterDefinition:
        occurrences: list[ParameterOccurrence] = []
        for occ_row in self._connection.execute(
            "SELECT * FROM parameter_occurrences WHERE parameter_id = ?"
            " ORDER BY occurrence_index",
            (row["id"],),
        ):
            segments: list[ParameterSegment] = []
            for seg_row in self._connection.execute(
                "SELECT * FROM parameter_segments WHERE occurrence_id = ?"
                " ORDER BY sequence",
                (occ_row["id"],),
            ):
                segments.append(
                    ParameterSegment(
                        sequence=seg_row["sequence"],
                        subframes=decode_subframe_selector(
                            seg_row["subframe_selector_raw"]
                        ),
                        word=seg_row["word"],
                        lsb=seg_row["lsb"],
                        msb=seg_row["msb"],
                        bcd_weight=seg_row["bcd_weight"],
                        source_raw={
                            "subframe_selector_raw": seg_row["subframe_selector_raw"],
                            "legacy_flag": seg_row["legacy_flag"] or "",
                        },
                    )
                )
            occurrences.append(
                ParameterOccurrence(index=occ_row["occurrence_index"], segments=segments)
            )

        raw_record = self._load_legacy_fields(
            document_id, row["id"], SCOPE_PARAMETER_RECORD
        )
        trailing = self._load_legacy_fields(
            document_id, row["id"], SCOPE_PARAMETER_TRAILING
        )
        extra: dict = {}
        if trailing:
            extra["trailing_fields"] = trailing
        return ParameterDefinition(
            id=row["param_key"],
            mnemonic=row["mnemonic"],
            description=row["description"] or "",
            source_parameter_type=row["source_parameter_type"],
            parameter_type=row["parameter_type"],
            unit=row["unit"],
            minimum=row["minimum"],
            maximum=row["maximum"],
            conversion=ConversionRule(
                resolution=row["resolution"],
                offset=row["conversion_offset"],
                formula_type=row["formula_type"],
            ),
            decimals=row["decimals"],
            true_state=row["true_state"],
            false_state=row["false_state"],
            states=[
                DiscreteState(int(value), label)
                for value, label in json.loads(row["states"])
            ]
            if row["states"]
            else [],
            occurrences=occurrences,
            notes=row["notes"],
            provenance=ParameterProvenance(
                source_type="repository",
                record_index=None,
                raw_record=tuple(raw_record) if raw_record else None,
                extra=extra,
            ),
        )

    def _load_legacy_fields(
        self, document_id: int, parameter_id: int | None, scope: str
    ) -> list[str]:
        if parameter_id is None:
            rows = self._connection.execute(
                "SELECT value FROM adb_legacy_fields"
                " WHERE document_id = ? AND parameter_id IS NULL AND scope = ?"
                " ORDER BY position",
                (document_id, scope),
            )
        else:
            rows = self._connection.execute(
                "SELECT value FROM adb_legacy_fields"
                " WHERE document_id = ? AND parameter_id = ? AND scope = ?"
                " ORDER BY position",
                (document_id, parameter_id, scope),
            )
        return [row["value"] for row in rows]
