"""Access helpers for preserved unknown ADB fields (design spec §34, §35).

Header fields whose semantics are unknown (indexes 1, 3 and 6, plus anything
beyond the sync words) are exposed as ``legacy_setting_N`` names.  No
semantic meaning is invented here.
"""

from __future__ import annotations

from ...domain.dataframe import DataframeMetadata
from ...domain.parameter import ParameterDefinition
from .mappings import SETTING_MIN_FIELDS, SETTING_UNKNOWN_INDEXES


def legacy_setting_fields(metadata: DataframeMetadata) -> dict[str, str]:
    """Unknown-semantics settings fields keyed ``legacy_setting_<index>``."""
    if not metadata.adb_settings_raw:
        return {}
    return {
        f"legacy_setting_{index}": value
        for index, value in enumerate(metadata.adb_settings_raw)
        if index in SETTING_UNKNOWN_INDEXES or index >= SETTING_MIN_FIELDS
    }


def trailing_legacy_fields(parameter: ParameterDefinition) -> list[str]:
    """Fields beyond the 238-field record, preserved verbatim."""
    if not parameter.provenance:
        return []
    return list(parameter.provenance.extra.get("trailing_fields", []))


def segment_legacy_flag(source_raw: dict | None) -> str:
    """The spare fifth field of a location group (blank in every file seen)."""
    return (source_raw or {}).get("legacy_flag", "")


def adb_warnings(parameter: ParameterDefinition) -> list[str]:
    """What the parser had to guess or ignore for this record."""
    if not parameter.provenance:
        return []
    return list(parameter.provenance.extra.get("adb_warnings", []))
