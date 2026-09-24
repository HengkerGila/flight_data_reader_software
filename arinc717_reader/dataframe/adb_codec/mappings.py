"""ADB record layout and the subframe selector codec (design spec §34–§36).

The layout below was verified on 2026-09-24 against two real AFDA files: a
vendor-made 442-parameter NC212i dataframe and a hand-made 37-parameter
one.  Both share one identical header and 238-field parameter records.
`docs/06-adb-format.md` holds the full field table and the evidence.

What the two files could not settle is marked PROVISIONAL and is one
constant to flip after the probe file (`tools/make_afda_probe.py`) has been
opened in AFDA:

* ``PARTS_ORDER`` — the significance order in which the parts of a
  concatenated (multi-word) parameter are listed.  The vendor file lists BCD
  digit weights least significant first, so LS-first is the working
  assumption for analog parts as well.
* the meaning of a subframe selector other than ``1234`` (see the parser).
"""

from __future__ import annotations

from collections.abc import Sequence

from ...domain.frame import SUBFRAME_COUNT
from ...domain.parameter import (
    TYPE_ANALOG_SIGNED,
    TYPE_ANALOG_UNSIGNED,
    TYPE_BCD,
    TYPE_DISCRETE,
)

# Text encoding of the files: the Windows code page (the vendor file carries
# a cp1252 right single quote).  Plain ASCII files are unaffected.
ADB_ENCODING = "cp1252"

# ---------------------------------------------------------------------------
# Settings record:  Setting,1,64,1,12,256,0,583,1464,2631,3512
#   index 2      words per subframe (= words per second, WPS)
#   index 4      bits per word (12)
#   index 5      words per frame (SUBFRAME_COUNT × WPS)
#   index 7..10  sync words SF1..SF4 in decimal (583 = 1107 octal, …)
#   index 1, 3, 6  unknown semantics — preserved verbatim, never invented
# ---------------------------------------------------------------------------
SETTING_TAG = "Setting"
SETTING_WPS_INDEX = 2
SETTING_BITS_INDEX = 4
SETTING_WORDS_PER_FRAME_INDEX = 5
SETTING_SYNC_WORD_INDEXES = (7, 8, 9, 10)
SETTING_MIN_FIELDS = 11
SETTING_UNKNOWN_INDEXES = (1, 3, 6)
# Values of the unknown fields copied from the observed header, used when a
# dataframe that never came from an ADB is written.
SETTING_TEMPLATE_UNKNOWN = {1: "1", 3: "1", 6: "0"}
SETTING_BITS_PER_WORD = "12"

# ---------------------------------------------------------------------------
# Parameter record: exactly 238 positional fields.
# ---------------------------------------------------------------------------
PARAM_NAME = 0
PARAM_DESCRIPTION = 1
PARAM_TYPE = 2  # "Signed Analog" | "Unsigned Analog" | "Discrete" | "BCD"
PARAM_UNIT = 3
PARAM_MINIMUM = 4
PARAM_MAXIMUM = 5
PARAM_SCALE = 6  # resolution, engineering units per count
PARAM_OFFSET = 7
PARAM_DECIMALS = 8  # display precision
PARAM_CONVERSION_KIND = 9  # "-" analog/BCD, "Discrete" = state table follows, "" none
PARAM_STATE_VALUES_START = 10  # 32 slots: discrete state values, or BCD digit weights
PARAM_STATE_LABELS_START = 42  # 32 slots: discrete state labels
STATE_SLOTS = 32
PARAM_SAMPLE_COUNT = 74  # samples per frame (= locations ÷ parts)
PARAM_PART_COUNT = 75  # word parts per sample (concatenation)
PARAM_LOCATIONS_START = 76
LOCATION_SLOTS = 32
LOCATION_FIELD_COUNT = 5  # subframe selector, word, lsb, msb, spare (blank so far)
PARAM_RECORD_LENGTH = (
    PARAM_LOCATIONS_START + LOCATION_SLOTS * LOCATION_FIELD_COUNT + 2
)  # 238: two blank fields close every record

CONVERSION_KIND_ANALOG = "-"
CONVERSION_KIND_DISCRETE = "Discrete"

# A location's word is numbered 1..SUBFRAME_COUNT×WPS over the whole frame
# ("frame-absolute"); the selector of such a location is always this text.
SELECTOR_ALL = "1234"

PARTS_ORDER_LS_FIRST = "ls_first"
PARTS_ORDER_MS_FIRST = "ms_first"
PARTS_ORDER = PARTS_ORDER_LS_FIRST  # PROVISIONAL — see the module docstring

# Type text AFDA understands, keyed by canonical type.
AFDA_TYPE_TEXT = {
    TYPE_ANALOG_SIGNED: "Signed Analog",
    TYPE_ANALOG_UNSIGNED: "Unsigned Analog",
    TYPE_BCD: "BCD",
    TYPE_DISCRETE: "Discrete",
}
_AFDA_TYPE_KEYS = {text.upper() for text in AFDA_TYPE_TEXT.values()}


def afda_type_text(parameter_type: str, source_text: str | None) -> str:
    """Type column text for export.

    The source text when AFDA already knows it (any case/spacing), else
    AFDA's word for the canonical type, else the source text as it is.
    """
    source = (source_text or "").strip()
    if " ".join(source.upper().split()) in _AFDA_TYPE_KEYS:
        return source
    return AFDA_TYPE_TEXT.get(parameter_type, source)


def absolute_word(subframe: int, word: int, wps: int) -> int:
    """(subframe, word in subframe) → frame-absolute word number, 1-based."""
    return (subframe - 1) * wps + word


def split_absolute_word(absolute: int, wps: int) -> tuple[int, int]:
    """Frame-absolute word number → (subframe, word in subframe), 1-based.

    The subframe exceeds ``SUBFRAME_COUNT`` for out-of-frame input; callers
    check.
    """
    return (absolute - 1) // wps + 1, (absolute - 1) % wps + 1


class SubframeSelectorError(ValueError):
    """Raised when a subframe selector cannot be decoded or encoded."""


def decode_subframe_selector(text: str) -> tuple[int, ...]:
    """Decode a subframe selector (design spec §36).

    ``"1"`` → (1,)   ``"13"`` → (1, 3)   ``"1234"`` → (1, 2, 3, 4)

    ``"0"`` is accepted as "recorded in every subframe" → (1, 2, 3, 4), the
    convention of PDF dataframe documents and of the mapping editor.
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
