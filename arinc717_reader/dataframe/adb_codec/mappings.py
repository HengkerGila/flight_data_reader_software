"""ADB record layout constants and the subframe selector codec (§34–§36).

PROVISIONAL LAYOUT — the parameter record layout below is this project's
canonical serialization, designed around what reverse engineering of the
Aering/AFDA-style files indicates (positional fields; mapping information
holding subframe selector, word, lsb, msb and a legacy flag; occurrences with
segments).  When a real supplied `.adb` is fully verified, adjust the
constants and consuming code HERE — nothing else in the codebase depends on
field positions.  Unknown fields are preserved losslessly either way.
"""

from __future__ import annotations

from collections.abc import Sequence

from ...domain.frame import SUBFRAME_COUNT

# ---------------------------------------------------------------------------
# Settings record, e.g.:  Setting,1,57,9,12,256,0,583,1464,2631,3512
# Known / highly-supported fields: index 5 = WPS, indexes 7..10 = sync words.
# The remaining header fields have unknown semantics; they are preserved
# verbatim (never given invented meanings, design spec §34).
# ---------------------------------------------------------------------------
SETTING_TAG = "Setting"
SETTING_WPS_INDEX = 5
SETTING_SYNC_WORD_INDEXES = (7, 8, 9, 10)
SETTING_MIN_FIELDS = 11
# Template mirrors the observed example header; unknown-semantics fields are
# copied verbatim when writing a dataframe that has no preserved raw header.
SETTING_TEMPLATE_UNKNOWN_FIELDS = ("1", "57", "9", "12")  # indexes 1..4
SETTING_TEMPLATE_FIELD_6 = "0"

# ---------------------------------------------------------------------------
# Parameter record: fixed positional head, then occurrence/segment groups,
# then optional trailing fields (preserved verbatim).
#
#   0 mnemonic          6 minimum
#   1 description       7 maximum
#   2 source type       8 true state
#   3 unit              9 false state
#   4 resolution       10 notes
#   5 offset           11 occurrence count
#
#   then per occurrence:  segment count,
#     then per segment:   subframe selector, word, lsb, msb, legacy flag
# ---------------------------------------------------------------------------
PARAM_MNEMONIC = 0
PARAM_DESCRIPTION = 1
PARAM_SOURCE_TYPE = 2
PARAM_UNIT = 3
PARAM_RESOLUTION = 4
PARAM_OFFSET = 5
PARAM_MINIMUM = 6
PARAM_MAXIMUM = 7
PARAM_TRUE_STATE = 8
PARAM_FALSE_STATE = 9
PARAM_NOTES = 10
PARAM_OCCURRENCE_COUNT = 11
PARAM_FIXED_FIELD_COUNT = 12
SEGMENT_FIELD_COUNT = 5  # selector, word, lsb, msb, legacy flag


class SubframeSelectorError(ValueError):
    """Raised when a subframe selector cannot be decoded or encoded."""


def decode_subframe_selector(text: str) -> tuple[int, ...]:
    """Decode an ADB subframe selector (design spec §36).

    ``"1"`` → (1,)   ``"13"`` → (1, 3)   ``"1234"`` → (1, 2, 3, 4)

    ``"0"`` is accepted as "recorded in every subframe" → (1, 2, 3, 4); this
    is the common FDR convention but is provisional until verified against
    real files.
    """
    cleaned = text.strip()
    if cleaned == "0":
        return tuple(range(1, SUBFRAME_COUNT + 1))
    if not cleaned or not cleaned.isdigit():
        raise SubframeSelectorError(f"invalid subframe selector {text!r}")
    subframes: set[int] = set()
    for char in cleaned:
        digit = int(char)
        if not 1 <= digit <= SUBFRAME_COUNT:
            raise SubframeSelectorError(
                f"subframe digit {digit} outside 1..{SUBFRAME_COUNT} "
                f"in selector {text!r}"
            )
        subframes.add(digit)
    return tuple(sorted(subframes))


def encode_subframe_selector(subframes: Sequence[int]) -> str:
    if not subframes:
        raise SubframeSelectorError("cannot encode empty subframe list")
    unique = sorted(set(subframes))
    for sf in unique:
        if not 1 <= sf <= SUBFRAME_COUNT:
            raise SubframeSelectorError(
                f"subframe {sf} outside 1..{SUBFRAME_COUNT}"
            )
    return "".join(str(sf) for sf in unique)
