"""Access helpers for preserved unknown ADB fields (design spec §34, §35).

Header fields whose semantics are currently unknown are exposed as
``legacy_setting_N`` names.  No semantic meaning is invented here.
"""

from __future__ import annotations

from ...domain.dataframe import DataframeMetadata
from ...domain.parameter import ParameterDefinition
from .mappings import SETTING_SYNC_WORD_INDEXES, SETTING_WPS_INDEX


def legacy_setting_fields(metadata: DataframeMetadata) -> dict[str, str]:
    """Unknown-semantics settings fields keyed ``legacy_setting_<index>``."""
    if not metadata.adb_settings_raw:
        return {}
    known = {0, SETTING_WPS_INDEX, *SETTING_SYNC_WORD_INDEXES}
    return {
        f"legacy_setting_{index}": value
        for index, value in enumerate(metadata.adb_settings_raw)
        if index not in known
    }


def trailing_legacy_fields(parameter: ParameterDefinition) -> list[str]:
    """Trailing unrecognized fields preserved from the parameter record."""
    if not parameter.provenance:
        return []
    return list(parameter.provenance.extra.get("trailing_fields", []))


def segment_legacy_flag(source_raw: dict | None) -> str:
    return (source_raw or {}).get("legacy_flag", "")
