"""Qt-free dataframe editing helpers (design spec §27 "Editor", §44).

Everything the editing dialogs need that is not presentation lives here so
it can be unit-tested without a QApplication: blank dataframe construction,
parameter id generation, subframe text parsing and the flattening of a
parameter's occurrence/segment tree into editable rows and back.

Editing never invents semantics for preserved raw fields.  When a segment is
edited, its legacy flag is kept; its raw subframe selector text is kept only
while the subframe set is unchanged, otherwise it is regenerated on export.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..domain.dataframe import DataframeDefinition, DataframeMetadata
from ..domain.frame import SUBFRAME_COUNT
from ..domain.parameter import (
    ParameterDefinition,
    ParameterOccurrence,
    ParameterSegment,
)
from .adb_codec.mappings import SubframeSelectorError, decode_subframe_selector
from .validator import ValidationIssue, validate_dataframe

# Standard ARINC 717 subframe sync pattern (octal 1107, 2670, 5107, 6670).
DEFAULT_SYNC_WORDS = (583, 1464, 2631, 3512)
STANDARD_WPS_VALUES = (64, 128, 256, 512, 1024, 2048)
ALL_SUBFRAMES = tuple(range(1, SUBFRAME_COUNT + 1))

MANUAL_ID_PREFIX = "man"


def new_dataframe(
    name: str,
    wps: int,
    sync_words: Sequence[int] | None = None,
    aircraft_type: str | None = None,
    revision: str | None = None,
    issue_date: str | None = None,
    superframe_present: bool | None = None,
) -> DataframeDefinition:
    """Create an empty, manually authored dataframe."""
    metadata = DataframeMetadata(
        dataframe_name=name.strip() or "untitled",
        wps=int(wps),
        aircraft_type=aircraft_type or None,
        revision=revision or None,
        issue_date=issue_date or None,
        superframe_present=superframe_present,
        sync_words=list(DEFAULT_SYNC_WORDS if sync_words is None else sync_words),
        source_type="manual",
    )
    return DataframeDefinition(metadata=metadata, parameters=[])


def make_parameter_id(mnemonic: str, existing_ids: Iterable[str]) -> str:
    """Stable, human-readable, unique id for a parameter created in-app."""
    slug = re.sub(r"[^a-z0-9]+", "-", mnemonic.lower()).strip("-") or "parameter"
    base = f"{MANUAL_ID_PREFIX}-{slug}"
    taken = set(existing_ids)
    candidate = base
    n = 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def parse_subframes(text: str) -> tuple[int, ...]:
    """Parse user-entered subframes.

    Accepts ADB selector digits (``13``, ``1234``, ``0``), separated lists
    (``1,3`` / ``1 3``), ranges (``2-4``) and ``all``.  Raises ``ValueError``
    on anything else or any subframe outside 1..4.
    """
    cleaned = text.strip().lower()
    if not cleaned:
        raise ValueError("subframes are required (e.g. 1,3 or all)")
    if cleaned in ("all", "*"):
        return ALL_SUBFRAMES
    if cleaned.isdigit():
        try:
            return decode_subframe_selector(cleaned)
        except SubframeSelectorError as exc:
            raise ValueError(str(exc)) from exc
    subframes: set[int] = set()
    for token in re.split(r"[,\s;/]+", cleaned):
        if not token:
            continue
        match = re.fullmatch(r"(\d+)-(\d+)", token)
        if match:
            lo, hi = int(match.group(1)), int(match.group(2))
            if lo > hi:
                lo, hi = hi, lo
            subframes.update(range(lo, hi + 1))
        elif token.isdigit():
            subframes.add(int(token))
        else:
            raise ValueError(f"invalid subframe token {token!r}")
    bad = sorted(sf for sf in subframes if not 1 <= sf <= SUBFRAME_COUNT)
    if bad:
        raise ValueError(f"subframes {bad} outside 1..{SUBFRAME_COUNT}")
    if not subframes:
        raise ValueError("subframes are required (e.g. 1,3 or all)")
    return tuple(sorted(subframes))


def format_subframes(subframes: Sequence[int]) -> str:
    return ",".join(str(sf) for sf in sorted(set(subframes)))


@dataclass
class SegmentRow:
    """One editable mapping row: an occurrence/segment address."""

    occurrence: int
    sequence: int
    subframes: tuple[int, ...]
    word: int
    lsb: int
    msb: int


def segment_rows(parameter: ParameterDefinition) -> list[SegmentRow]:
    rows: list[SegmentRow] = []
    for occurrence in sorted(parameter.occurrences, key=lambda o: o.index):
        for segment in sorted(occurrence.segments, key=lambda s: s.sequence):
            rows.append(
                SegmentRow(
                    occurrence=occurrence.index,
                    sequence=segment.sequence,
                    subframes=tuple(segment.subframes),
                    word=segment.word,
                    lsb=segment.lsb,
                    msb=segment.msb,
                )
            )
    return rows


def build_occurrences(
    rows: Iterable[SegmentRow],
    previous: ParameterDefinition | None = None,
) -> list[ParameterOccurrence]:
    """Rebuild the occurrence tree from rows, preserving raw fields.

    Rows are grouped by occurrence index and ordered by sequence.  Duplicate
    indexes/sequences are kept as-is so the mapping validator can report
    them; the editor does not silently renumber.
    """
    original: dict[tuple[int, int], ParameterSegment] = {}
    if previous is not None:
        for occurrence in previous.occurrences:
            for segment in occurrence.segments:
                original[(occurrence.index, segment.sequence)] = segment

    grouped: dict[int, list[ParameterSegment]] = {}
    for row in rows:
        source_raw = _carry_source_raw(
            original.get((row.occurrence, row.sequence)), row.subframes
        )
        grouped.setdefault(row.occurrence, []).append(
            ParameterSegment(
                sequence=row.sequence,
                subframes=tuple(row.subframes),
                word=row.word,
                lsb=row.lsb,
                msb=row.msb,
                source_raw=source_raw,
            )
        )
    return [
        ParameterOccurrence(
            index=index, segments=sorted(segments, key=lambda s: s.sequence)
        )
        for index, segments in sorted(grouped.items())
    ]


def _carry_source_raw(
    original: ParameterSegment | None, subframes: tuple[int, ...]
) -> dict | None:
    if original is None or not original.source_raw:
        return None
    carried = dict(original.source_raw)
    if tuple(original.subframes) != tuple(subframes):
        # The raw selector text described a different subframe set; the
        # writer regenerates it from the canonical tuple.
        carried.pop("subframe_selector_raw", None)
    return carried or None


def with_parameter(
    dataframe: DataframeDefinition, candidate: ParameterDefinition
) -> DataframeDefinition:
    """Copy of ``dataframe`` with ``candidate`` inserted or substituted by id."""
    clone = copy.copy(dataframe)
    clone.parameters = [
        candidate if p.id == candidate.id else p for p in dataframe.parameters
    ]
    if all(p.id != candidate.id for p in dataframe.parameters):
        clone.parameters.append(candidate)
    return clone


def candidate_issues(
    dataframe: DataframeDefinition, candidate: ParameterDefinition
) -> list[ValidationIssue]:
    """Validation issues an edited parameter would raise, without committing.

    Includes dataframe-level issues that mention the candidate (e.g. bit
    overlaps with other parameters).
    """
    preview = with_parameter(dataframe, candidate)
    return [issue for issue in validate_dataframe(preview) if issue.concerns(candidate.id)]


def duplicate_parameter(
    parameter: ParameterDefinition, existing_ids: Iterable[str]
) -> ParameterDefinition:
    """Deep copy with a fresh id and mnemonic; provenance is dropped because
    the copy was created in the application, not read from a source."""
    clone = copy.deepcopy(parameter)
    clone.mnemonic = f"{parameter.mnemonic} COPY"
    clone.id = make_parameter_id(clone.mnemonic, existing_ids)
    clone.provenance = None
    return clone
