"""SQLite schema (design spec §39).

`.adb` is never used as the internal database; this schema is.  ``offset`` is
stored as ``conversion_offset`` to stay clear of the SQL keyword.
"""

from __future__ import annotations

import sqlite3

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS dataframe_documents (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        aircraft_type TEXT,
        revision TEXT,
        issue_date TEXT,
        wps INTEGER NOT NULL,
        superframe_present INTEGER,
        sync_words TEXT NOT NULL,
        source_type TEXT,
        source_filename TEXT,
        source_hash TEXT,
        status TEXT NOT NULL DEFAULT 'DRAFT'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS parameters (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES dataframe_documents(id)
            ON DELETE CASCADE,
        param_key TEXT NOT NULL,
        mnemonic TEXT NOT NULL,
        description TEXT,
        source_parameter_type TEXT,
        parameter_type TEXT NOT NULL,
        unit TEXT,
        minimum REAL,
        maximum REAL,
        resolution REAL NOT NULL,
        conversion_offset REAL NOT NULL,
        formula_type TEXT NOT NULL DEFAULT 'linear',
        true_state TEXT,
        false_state TEXT,
        notes TEXT,
        status TEXT NOT NULL DEFAULT 'IMPORTED'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS parameter_occurrences (
        id INTEGER PRIMARY KEY,
        parameter_id INTEGER NOT NULL REFERENCES parameters(id)
            ON DELETE CASCADE,
        occurrence_index INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS parameter_segments (
        id INTEGER PRIMARY KEY,
        occurrence_id INTEGER NOT NULL REFERENCES parameter_occurrences(id)
            ON DELETE CASCADE,
        sequence INTEGER NOT NULL,
        subframe_selector_raw TEXT NOT NULL,
        word INTEGER NOT NULL,
        lsb INTEGER NOT NULL,
        msb INTEGER NOT NULL,
        legacy_flag TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS source_fields (
        id INTEGER PRIMARY KEY,
        parameter_id INTEGER NOT NULL REFERENCES parameters(id)
            ON DELETE CASCADE,
        field_name TEXT NOT NULL,
        raw_text TEXT,
        normalized_value TEXT,
        page_number INTEGER,
        bbox TEXT,
        confidence REAL,
        reviewed INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS validation_issues (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES dataframe_documents(id)
            ON DELETE CASCADE,
        parameter_id INTEGER REFERENCES parameters(id) ON DELETE CASCADE,
        severity TEXT NOT NULL,
        rule_name TEXT NOT NULL,
        message TEXT NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS review_history (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES dataframe_documents(id)
            ON DELETE CASCADE,
        parameter_id INTEGER REFERENCES parameters(id) ON DELETE CASCADE,
        from_state TEXT,
        to_state TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        note TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS adb_legacy_fields (
        id INTEGER PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES dataframe_documents(id)
            ON DELETE CASCADE,
        parameter_id INTEGER REFERENCES parameters(id) ON DELETE CASCADE,
        scope TEXT NOT NULL,
        position INTEGER NOT NULL,
        value TEXT NOT NULL
    )
    """,
)


def init_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.commit()
