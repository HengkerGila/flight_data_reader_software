"""Raw row → canonical parameter normalization (design spec §28, §29, §31.5).

Every derived value comes from an explicit rule listed here, and every rule
that had to *interpret* the source text records an issue: errors when the
row cannot be represented, warnings when a representation was chosen that a
human must confirm.  Any error or warning routes the row to manual review
(spec §32); ambiguous extraction is never silently repaired (spec §54).

Conventions a document is known to follow can be declared up front in an
``ImportProfile``; a declared convention is authoritative and raises no
review issue, while the same reading applied heuristically does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

from ...domain.frame import SUBFRAME_COUNT
from ...domain.parameter import (
    TYPE_ANALOG_SIGNED,
    TYPE_ANALOG_UNSIGNED,
    TYPE_BCD,
    TYPE_DISCRETE,
    TYPE_UNKNOWN,
    ConversionRule,
    ParameterDefinition,
    ParameterOccurrence,
    ParameterProvenance,
    ParameterSegment,
    normalize_source_type,
)
from ..editor import ALL_SUBFRAMES, parse_subframes
from .raw import (
    CRITICAL_FIELDS,
    DEFAULT_OCR_RECOGNIZER,
    SOURCE_NATIVE,
    SOURCE_OCR,
    SOURCE_TEXT_LAYER,
    RawParameterRow,
)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

MULTIWORD_AUTO = "auto"
MULTIWORD_SEGMENTS = "segments"
MULTIWORD_OCCURRENCES = "occurrences"
MULTIWORD_GROUPED = "grouped"  # one occurrence per listed word group
BITS_AUTO = "auto"
BITS_PER_WORD = "per_word"
BITS_RANGE_PER_WORD = "range_per_word"
FREQUENCY_AUTO = "auto"
FREQUENCY_HZ = "hz"
FREQUENCY_SECONDS = "seconds"
PAIR_AUTO = "auto"
PAIR_OFFSET_RESOLUTION = "offset_resolution"
PAIR_RESOLUTION_OFFSET = "resolution_offset"
OCR_AUTO = "auto"
OCR_NEVER = "never"

FRAME_SECONDS = float(SUBFRAME_COUNT)  # ARINC 717: one subframe per second


@dataclass
class ImportProfile:
    """Document conventions declared by the user (authoritative when set)."""

    name: str = "default"
    default_wps: int = 256
    page_range: tuple[int, int] | None = None
    ocr: str = OCR_AUTO
    # Render resolution for scanned pages.  200 dpi reads about two points
    # more cells than 150 on the benchmark at no extra time; 300 gains
    # nothing worth its time (tools/ocr_bench.py).
    ocr_dpi: int = 200
    # Recognizer model: "ch" (RapidOCR's own), "en" (bundled English) or a
    # model file path; ``ocr_keys_path`` names its character list when the
    # model file does not embed one.
    ocr_recognizer: str = DEFAULT_OCR_RECOGNIZER
    ocr_keys_path: str | None = None
    # RapidOCR's 180° line classifier; off because deskewed tables have no
    # upside-down lines and it flips short crops ("ON" → "NO", "9" → "6").
    ocr_angle_classifier: bool = False
    # Recognize each grid cell on its own crop (True) rather than detecting
    # text lines on the whole page first: short strings such as a lone "0"
    # are read like any other cell and text cannot land in a neighbour.
    ocr_cells: bool = True
    ocr_confidence_threshold: float = 0.6
    blank_subframe_means_all: bool = False
    zero_subframe_means_all: bool = True
    multiword_bits: str = BITS_AUTO
    multiword_meaning: str = MULTIWORD_AUTO
    frequency_unit: str = FREQUENCY_AUTO
    resolution_pair: str = PAIR_AUTO
    decimal_comma: bool = False
    column_overrides: dict[str, str] = field(default_factory=dict)


DEFAULT_PROFILE = ImportProfile()


@dataclass
class NormalizationIssue:
    severity: str
    rule: str
    message: str
    field: str | None = None

    @property
    def needs_review(self) -> bool:
        return self.severity in (SEVERITY_ERROR, SEVERITY_WARNING)


@dataclass
class NormalizationResult:
    parameter: ParameterDefinition | None
    issues: list[NormalizationIssue]
    interpretation: dict[str, str]

    @property
    def needs_review(self) -> bool:
        return self.parameter is None or any(i.needs_review for i in self.issues)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == SEVERITY_ERROR for i in self.issues)


class _RowError(ValueError):
    """Internal: a field that cannot be interpreted at all."""

    def __init__(self, rule: str, message: str, field: str | None = None):
        super().__init__(message)
        self.rule = rule
        self.field = field


# --------------------------------------------------------------------------
# Text parsing helpers
# --------------------------------------------------------------------------

_RANGE_OPERATORS = re.compile(r"(?<=\d)\s*(?:-|–|—|·|•|\.\.+|:)\s*(?=\d)")
_RANGE_WORDS = re.compile(r"(?<=\d)\s+(?:to|thru|through)\s+(?=\d)", re.IGNORECASE)
_LIST_SEPARATORS = re.compile(r"\s*(?:[,;/&+|]|\band\b)\s*|\s+", re.IGNORECASE)
_MINUS_SIGNS = str.maketrans({"−": "-", "–": "-", "—": "-"})
_PARENTHETICAL = re.compile(r"\([^)]*\)?|\[[^\]]*\]?")

# Characters OCR confuses with digits, tried only when a numeric field does
# not parse as written; every such correction is reported for confirmation.
_OCR_DIGITS = str.maketrans(
    {"O": "0", "o": "0", "D": "0", "Q": "0", "。": "0", "○": "0", "〇": "0", "º": "0", "°": "0",
     "l": "1", "I": "1", "|": "1", "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "g": "9"}
)
# A lone dot between digits in a bit or word cell is a misread dash ("9.1").
_DOT_RANGE = re.compile(r"(?<=\d)\.(?=\d)")


def ocr_digit_repair(text: str) -> str:
    return text.translate(_OCR_DIGITS)


# Stamp or rule fragments that OCR turns into stray punctuation next to a
# number ("-- 1", ".. 1", "~ 0.5").
_JUNK_TOKEN = re.compile(r"(?:^|\s)[-–—.~_'`,·•|]{1,4}(?=\s|$)")


def ocr_junk_strip(text: str) -> str:
    return " ".join(_JUNK_TOKEN.sub(" ", text).split())


# Cells that mean "nothing here" in dataframe documents.
_PLACEHOLDERS = {"-", "–", "—", ".", "..", "n/a", "na", "none", "nil", "/", "--"}


def cell_value(text: str) -> str:
    """One-line cell text with placeholder dashes treated as empty."""
    value = " ".join(text.split())
    return "" if value.lower() in _PLACEHOLDERS else value


def _tokens(text: str) -> list[str]:
    """Split a list-like cell into tokens; ``a-b`` ranges stay one token."""
    cleaned = _RANGE_WORDS.sub("-", text.strip().translate(_MINUS_SIGNS))
    cleaned = _RANGE_OPERATORS.sub("-", cleaned)
    return [t for t in _LIST_SEPARATORS.split(cleaned) if t]


def parse_word_groups(text: str) -> list[list[int]]:
    """Word location as groups: ``'247'`` → [[247]]; ``'14-15, 142-143'`` →
    [[14, 15], [142, 143]]; ``'7, 71, 135, 199'`` → four single-word groups;
    a range keeps its written order (``'154-153'`` → [[154, 153]])."""
    groups: list[list[int]] = []
    for token in _tokens(_DOT_RANGE.sub("-", text)):
        match = re.fullmatch(r"(\d+)-(\d+)", token)
        if match:
            lo, hi = int(match.group(1)), int(match.group(2))
            step = 1 if hi >= lo else -1
            groups.append(list(range(lo, hi + step, step)))
        elif token.isdigit():
            groups.append([int(token)])
        else:
            raise ValueError(f"unreadable number list {text!r}")
    if not groups:
        raise ValueError("empty number list")
    return groups


def parse_int_list(text: str) -> list[int]:
    """All words of a word-location cell, flattened (written order)."""
    return [word for group in parse_word_groups(text) for word in group]


BitToken = int | tuple[int, int]


def parse_bit_tokens(text: str) -> list[BitToken]:
    """Bit cell tokens: ints or ``(high, low)`` ranges, in written order."""
    tokens: list[BitToken] = []
    for token in _tokens(_DOT_RANGE.sub("-", text)):
        match = re.fullmatch(r"(\d+)-(\d+)", token)
        if match:
            a, b = int(match.group(1)), int(match.group(2))
            tokens.append((max(a, b), min(a, b)))
        elif token.isdigit():
            tokens.append(int(token))
        else:
            raise ValueError(f"unreadable bit specification {text!r}")
    if not tokens:
        raise ValueError("empty bit specification")
    return tokens


def parse_number(text: str, decimal_comma: bool = False) -> float:
    """Decimal, ``1/16`` fraction, ``2^-4`` power or scientific notation."""
    cleaned = text.strip().translate(_MINUS_SIGNS)
    if not cleaned:
        raise ValueError("empty number")
    if decimal_comma:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        if "." not in cleaned and re.fullmatch(r"-?\d+,\d+", cleaned):
            raise ValueError(
                f"{text!r} looks like a decimal comma; enable the decimal-comma "
                "profile option if the document uses it"
            )
        cleaned = cleaned.replace(",", "")  # thousands separator
    match = re.fullmatch(r"(-?\d+)\s*/\s*(\d+)", cleaned)
    if match:
        return float(Fraction(int(match.group(1)), int(match.group(2))))
    match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*\^\s*\(?(-?\d+)\)?", cleaned)
    if match:
        return float(match.group(1)) ** int(match.group(2))
    try:
        return float(cleaned)
    except ValueError as exc:
        raise ValueError(f"unreadable number {text!r}") from exc


_NUMBER = r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?"
_NUMBER_PAIR = re.compile(rf"^\s*({_NUMBER})\s*(?:[,;]\s*|\s+)({_NUMBER})\s*$")


def split_number_pair(text: str) -> tuple[str, str] | None:
    """``'-40, 0.0195'`` → ('-40', '0.0195'); None when not two numbers."""
    match = _NUMBER_PAIR.match(text.strip().translate(_MINUS_SIGNS).replace("\n", " "))
    return (match.group(1), match.group(2)) if match else None


_FREQUENCY_NOISE = re.compile(
    r"\b(?:hz|sps|s/s|sec|s|samples?(?:\s*/\s*|\s+per\s+)(?:s|sec|second)|per\s+second|/s)\b",
    re.IGNORECASE,
)


def parse_frequency(text: str) -> float:
    """The number in a frequency / interval cell: ``'1'``, ``'4 Hz'``, ``'1/4'``, ``'0.5 s'``."""
    cleaned = _FREQUENCY_NOISE.sub("", text.strip()).strip()
    return parse_number(cleaned)


_SUBFRAME_ALIASES = {"all": ALL_SUBFRAMES, "*": ALL_SUBFRAMES, "1-4": ALL_SUBFRAMES}


def parse_subframe_field(
    text: str, profile: ImportProfile
) -> tuple[tuple[int, ...], NormalizationIssue | None]:
    cleaned = _PARENTHETICAL.sub("", text).strip().lower()
    cleaned = re.sub(r"\bsf\s*", "", cleaned)
    if not cleaned:
        if profile.blank_subframe_means_all:
            return ALL_SUBFRAMES, None
        return ALL_SUBFRAMES, NormalizationIssue(
            SEVERITY_WARNING,
            "normalize.subframe_blank",
            "subframe not stated; assumed recorded in every subframe — confirm",
            "subframe",
        )
    if cleaned in _SUBFRAME_ALIASES:
        return _SUBFRAME_ALIASES[cleaned], None
    if cleaned == "0":
        if profile.zero_subframe_means_all:
            return ALL_SUBFRAMES, NormalizationIssue(
                SEVERITY_INFO,
                "normalize.subframe_zero",
                "subframe '0' read as every subframe (ADB convention)",
                "subframe",
            )
        raise _RowError("normalize.subframe", "subframe '0' is not allowed by the profile", "subframe")
    cleaned = re.sub(r"\s*(?:[/&+|.]|\band\b)\s*", ",", cleaned)  # "1.3" is an OCR "1,3"
    try:
        return parse_subframes(cleaned), None
    except ValueError as exc:
        raise _RowError("normalize.subframe", f"subframe {text!r}: {exc}", "subframe") from exc


# --------------------------------------------------------------------------
# Type / sign
# --------------------------------------------------------------------------

_SIGNED_HINTS = {"S", "SIGNED", "Y", "YES", "2'S", "TWO'S", "2S", "+/-", "±", "SGN", "TRUE"}
_UNSIGNED_HINTS = {"U", "UNSIGNED", "N", "NO", "+", "UNS", "FALSE", "-"}
_TYPE_WORDS = ("DISCRETE", "BNR", "BCD", "SIGNED", "UNSIGNED", "ANALOG", "STATUS", "RAW")


def resolve_type(
    type_text: str, sign_text: str
) -> tuple[str | None, str, list[NormalizationIssue]]:
    """(source type text, canonical type, issues)."""
    issues: list[NormalizationIssue] = []
    source_type = " ".join(type_text.split()) or None
    canonical = normalize_source_type(source_type)
    if canonical == TYPE_UNKNOWN and source_type:
        # OCR noise: "Discret e" (split word) or "BCO" (misread letter).
        joined = source_type.replace(" ", "")
        if normalize_source_type(joined) != TYPE_UNKNOWN:
            canonical = normalize_source_type(joined)
            issues.append(
                NormalizationIssue(
                    SEVERITY_WARNING,
                    "normalize.type_repaired",
                    f"source type {type_text!r} read as {joined!r} (OCR split word) — confirm",
                    "parameter_type",
                )
            )
        else:
            import difflib

            key = joined.upper()
            best = max(_TYPE_WORDS, key=lambda w: difflib.SequenceMatcher(None, key, w).ratio())
            if len(key) >= 3 and difflib.SequenceMatcher(None, key, best).ratio() >= 0.66:
                canonical = normalize_source_type(best)
                issues.append(
                    NormalizationIssue(
                        SEVERITY_WARNING,
                        "normalize.type_repaired",
                        f"source type {type_text!r} read as {best!r} (closest known type) — confirm",
                        "parameter_type",
                    )
                )
    sign = " ".join(sign_text.split()).upper()
    if sign:
        if sign in _SIGNED_HINTS:
            if canonical == TYPE_ANALOG_UNSIGNED:
                canonical = TYPE_ANALOG_SIGNED
                source_type = f"{source_type or 'BNR'} (signed)"
                issues.append(
                    NormalizationIssue(
                        SEVERITY_INFO,
                        "normalize.sign_column",
                        f"sign column {sign_text!r} makes the parameter signed",
                        "sign",
                    )
                )
        elif sign in _UNSIGNED_HINTS:
            if canonical == TYPE_ANALOG_SIGNED:
                issues.append(
                    NormalizationIssue(
                        SEVERITY_WARNING,
                        "normalize.sign_conflict",
                        f"type {type_text!r} is signed but sign column says {sign_text!r}",
                        "sign",
                    )
                )
        else:
            issues.append(
                NormalizationIssue(
                    SEVERITY_WARNING,
                    "normalize.sign_column",
                    f"sign column {sign_text!r} not understood",
                    "sign",
                )
            )
    if canonical == TYPE_UNKNOWN:
        issues.append(
            NormalizationIssue(
                SEVERITY_WARNING,
                "normalize.type",
                f"source type {type_text!r} not recognized; will decode as UNSUPPORTED_TYPE "
                "unless corrected",
                "parameter_type",
            )
        )
    return source_type, canonical, issues


# --------------------------------------------------------------------------
# Mapping
# --------------------------------------------------------------------------


def resolve_bit_specs(
    msb_text: str,
    lsb_text: str,
    bits_text: str,
    groups: list[list[int]],
    profile: ImportProfile,
) -> tuple[list[tuple[int, int]], list[NormalizationIssue], list[list[int]], bool, str]:
    """Per-word ``(msb, lsb)`` pairs aligned with the flattened word list.

    Returns ``(specs, issues, groups, forced_grouped, explanation)``; the
    groups come back regrouped when the bit layout itself implies pairs of
    words (MSB-word range / LSB-word range).
    """
    issues: list[NormalizationIssue] = []
    words = [word for group in groups for word in group]
    if bits_text.strip():
        try:
            tokens = parse_bit_tokens(bits_text)
        except ValueError as exc:
            raise _RowError("normalize.bits", str(exc), "bits") from exc
        ranges = [(t, t) if isinstance(t, int) else t for t in tokens]
        specs, note = _spread(ranges, len(words), "bits")
        if msb_text.strip() or lsb_text.strip():
            issues.append(
                NormalizationIssue(
                    SEVERITY_INFO,
                    "normalize.bits_precedence",
                    "bit range column used; MSB/LSB columns ignored",
                    "bits",
                )
            )
        return specs, issues, groups, False, note

    if not (msb_text.strip() and lsb_text.strip()):
        raise _RowError(
            "normalize.bits_missing",
            "no bit positions: MSB and LSB (or a bit range) are required",
            "msb" if not msb_text.strip() else "lsb",
        )
    try:
        msb_tokens = parse_bit_tokens(msb_text)
        lsb_tokens = parse_bit_tokens(lsb_text)
    except ValueError as exc:
        raise _RowError("normalize.bits", str(exc), "msb") from exc

    if all(isinstance(t, int) for t in msb_tokens + lsb_tokens):
        if len(msb_tokens) != len(lsb_tokens):
            raise _RowError(
                "normalize.bits",
                f"MSB lists {len(msb_tokens)} value(s) but LSB lists {len(lsb_tokens)}",
                "lsb",
            )
        pairs = [(int(m), int(l)) for m, l in zip(msb_tokens, lsb_tokens)]
        specs, note = _spread(pairs, len(words), "MSB/LSB")
        return specs, issues, groups, False, note

    # Ranges inside the MSB / LSB columns (design spec §29 example: word
    # "121-122", MSB "9-1", LSB "12-7"): the MSB column describes the bits of
    # the most significant word of each pair and the LSB column those of the
    # least significant word.
    if (
        len(msb_tokens) == 1
        and len(lsb_tokens) == 1
        and isinstance(msb_tokens[0], tuple)
        and isinstance(lsb_tokens[0], tuple)
    ):
        if all(len(group) == 1 for group in groups) and len(words) == 2:
            groups = [list(words)]  # "154, 153" written as two single words
        if not all(len(group) == 2 for group in groups):
            raise _RowError(
                "normalize.bits",
                f"MSB {msb_text.strip()!r} / LSB {lsb_text.strip()!r} ranges need pairs "
                f"of words, got groups {groups}",
                "msb",
            )
        specs = [msb_tokens[0], lsb_tokens[0]] * len(groups)
        note = (
            f"MSB column {msb_text.strip()!r} read as bits of the first word of each pair, "
            f"LSB column {lsb_text.strip()!r} as bits of the second word"
        )
        if profile.multiword_bits == BITS_RANGE_PER_WORD:
            issues.append(NormalizationIssue(SEVERITY_INFO, "normalize.bits_layout", note, "msb"))
        else:
            issues.append(
                NormalizationIssue(
                    SEVERITY_WARNING,
                    "normalize.bits_layout",
                    note + " — confirm this reading (or declare it in the conventions)",
                    "msb",
                )
            )
        return specs, issues, groups, True, note
    raise _RowError(
        "normalize.bits",
        f"MSB {msb_text.strip()!r} / LSB {lsb_text.strip()!r} layout not recognized "
        f"for {len(words)} word(s)",
        "msb",
    )


def _spread(pairs: list[tuple[int, int]], word_count: int, label: str):
    if len(pairs) == word_count:
        return list(pairs), f"{label}: one bit field per listed word"
    if len(pairs) == 1:
        return [pairs[0]] * word_count, f"{label}: the same bit field applies to every listed word"
    raise _RowError(
        "normalize.bits",
        f"{label} lists {len(pairs)} bit field(s) for {word_count} word(s)",
        "bits" if label == "bits" else "msb",
    )


def frequency_to_hz(value: float, unit: str) -> float | None:
    if unit == FREQUENCY_SECONDS:
        return 1.0 / value if value > 0 else None
    return value


def expected_occurrences(frequency_hz: float | None, subframes: tuple[int, ...]) -> float | None:
    """Occurrences per subframe the document's rate implies, or None."""
    if frequency_hz is None:
        return None
    samples_per_frame = frequency_hz * FRAME_SECONDS
    if samples_per_frame < 1 or not subframes:
        return None
    return samples_per_frame / len(subframes)


def resolve_layout(
    groups: list[list[int]],
    specs: list[tuple[int, int]],
    frequency_hz: float | None,
    subframes: tuple[int, ...],
    profile: ImportProfile,
    forced_grouped: bool,
) -> tuple[str, list[NormalizationIssue], str]:
    """Decide whether several words are occurrences or segments of one value.

    ``frequency × frame seconds / subframes`` is the number of occurrences the
    document itself implies; it is used as evidence, never as a guess.
    """
    issues: list[NormalizationIssue] = []
    words = [word for group in groups for word in group]
    word_count, group_count = len(words), len(groups)
    expected: float | None = None
    if frequency_hz is not None:
        if frequency_hz * FRAME_SECONDS < 1:
            issues.append(
                NormalizationIssue(
                    SEVERITY_ERROR,
                    "normalize.superframe",
                    f"rate {frequency_hz:g} Hz is below one sample per frame "
                    "(superframe parameter); superframes are not supported yet",
                    "frequency",
                )
            )
        else:
            expected = expected_occurrences(frequency_hz, subframes)
            if expected is not None and abs(expected - round(expected)) > 1e-9:
                issues.append(
                    NormalizationIssue(
                        SEVERITY_WARNING,
                        "normalize.frequency",
                        f"rate {frequency_hz:g} Hz in subframes {list(subframes)} "
                        f"implies {expected:g} occurrences per subframe set",
                        "frequency",
                    )
                )
                expected = None
            elif expected is not None:
                expected = round(expected)

    def rate_check(actual: int, what: str) -> None:
        if expected is not None and expected != actual:
            issues.append(
                NormalizationIssue(
                    SEVERITY_WARNING,
                    "normalize.frequency",
                    f"rate {frequency_hz:g} Hz implies {expected} occurrence(s) but "
                    f"{what}",
                    "frequency",
                )
            )

    if forced_grouped or any(len(group) > 1 for group in groups):
        rate_check(group_count, f"{group_count} word group(s) are listed")
        explanation = (
            f"{group_count} word group(s): one occurrence per group, one segment per "
            "word (listed order, first = most significant)"
        )
        return MULTIWORD_GROUPED, issues, explanation

    if word_count == 1:
        rate_check(1, "only one word is listed")
        return MULTIWORD_OCCURRENCES, issues, "single word: one occurrence, one segment"

    identical = len(set(specs)) == 1
    if profile.multiword_meaning != MULTIWORD_AUTO:
        meaning = profile.multiword_meaning
        explanation = f"profile declares multi-word rows as {meaning}"
    elif expected == word_count:
        meaning = MULTIWORD_OCCURRENCES
        explanation = (
            f"rate {frequency_hz:g} Hz × {FRAME_SECONDS:g} s / {len(subframes)} "
            f"subframe(s) = {word_count} occurrences: one per listed word"
        )
    elif expected == 1:
        meaning = MULTIWORD_SEGMENTS
        explanation = (
            f"rate {frequency_hz:g} Hz × {FRAME_SECONDS:g} s / {len(subframes)} "
            f"subframe(s) = 1 occurrence: the {word_count} words are segments "
            "(listed order, first = most significant)"
        )
    else:
        meaning = MULTIWORD_OCCURRENCES if identical else MULTIWORD_SEGMENTS
        reason = "no usable rate" if expected is None else f"rate implies {expected} occurrence(s)"
        explanation = (
            f"{word_count} words with {reason}: provisionally read as {meaning} "
            f"({'identical' if identical else 'differing'} bit fields)"
        )
        issues.append(
            NormalizationIssue(
                SEVERITY_WARNING,
                "normalize.multiword",
                f"{word_count} words listed but {reason}; read as {meaning} — confirm",
                "word_location",
            )
        )
    if meaning == MULTIWORD_SEGMENTS:
        rate_check(1, "the words were read as segments of one value")
    else:
        rate_check(word_count, f"{word_count} words are listed")
    return meaning, issues, explanation


def build_occurrences(
    groups: list[list[int]],
    specs: list[tuple[int, int]],
    subframes: tuple[int, ...],
    meaning: str,
) -> list[ParameterOccurrence]:
    words = [word for group in groups for word in group]
    spec_of = dict(zip(range(len(words)), specs))

    def segment(sequence: int, position: int) -> ParameterSegment:
        msb, lsb = spec_of[position]
        return ParameterSegment(
            sequence=sequence, subframes=subframes, word=words[position], lsb=lsb, msb=msb
        )

    if meaning == MULTIWORD_SEGMENTS:
        return [
            ParameterOccurrence(
                index=1,
                segments=[segment(k + 1, k) for k in range(len(words))],
            )
        ]
    if meaning == MULTIWORD_GROUPED:
        occurrences: list[ParameterOccurrence] = []
        position = 0
        for index, group in enumerate(groups, start=1):
            segments = [segment(k + 1, position + k) for k in range(len(group))]
            position += len(group)
            occurrences.append(ParameterOccurrence(index=index, segments=segments))
        return occurrences
    return [
        ParameterOccurrence(index=k + 1, segments=[segment(1, k)]) for k in range(len(words))
    ]


def spacing_issue(occurrences: list[ParameterOccurrence], wps: int | None) -> NormalizationIssue | None:
    """Samples of one parameter are normally spread evenly across a subframe;
    uneven spacing usually means a misread word number."""
    if wps is None or len(occurrences) < 2:
        return None
    firsts = [o.segments[0].word for o in occurrences if o.segments]
    if len(firsts) != len(occurrences):
        return None
    expected = wps / len(occurrences)
    gaps = [b - a for a, b in zip(firsts, firsts[1:])]
    if any(abs(gap - expected) > 1e-9 for gap in gaps):
        return NormalizationIssue(
            SEVERITY_WARNING,
            "normalize.spacing",
            f"occurrence words {firsts} are not evenly spaced (expected every "
            f"{expected:g} words for {len(occurrences)} samples per subframe in "
            f"{wps} WPS) — check the word numbers",
            "word_location",
        )
    return None


# --------------------------------------------------------------------------
# States
# --------------------------------------------------------------------------

_STATE_ONE = re.compile(r"(?:^|[\s,;(\[])1\s*[=:]\s*([^,;/)\]]+)")
_STATE_ZERO = re.compile(r"(?:^|[\s,;(\[])0\s*[=:]\s*([^,;/)\]]+)")


def infer_states(text: str) -> tuple[str | None, str | None]:
    one = _STATE_ONE.search(text)
    zero = _STATE_ZERO.search(text)
    return (
        one.group(1).strip() if one else None,
        zero.group(1).strip() if zero else None,
    )


# --------------------------------------------------------------------------
# Row normalization
# --------------------------------------------------------------------------


def normalize_raw_row(
    row: RawParameterRow,
    parameter_id: str,
    profile: ImportProfile | None = None,
    source_filename: str | None = None,
    *,
    wps: int | None = None,
) -> NormalizationResult:
    """Build a parameter from a raw row.

    ``profile`` should be the *effective* profile: "auto" settings resolved
    from document evidence by the caller (``effective_profile``); an
    unresolved "auto" frequency unit is read as Hz.
    """
    profile = profile or DEFAULT_PROFILE
    unit = profile.frequency_unit if profile.frequency_unit != FREQUENCY_AUTO else FREQUENCY_HZ
    issues: list[NormalizationIssue] = []
    interpretation: dict[str, str] = {}
    try:
        parameter = _normalize(
            row, parameter_id, profile, source_filename, issues, interpretation, unit, wps
        )
    except _RowError as exc:
        issues.append(NormalizationIssue(SEVERITY_ERROR, exc.rule, str(exc), exc.field))
        parameter = None
    return NormalizationResult(parameter=parameter, issues=issues, interpretation=interpretation)


def _oneline(text: str) -> str:
    return " ".join(text.split())


def _parse_numeric(row, fld, parser, rule, issues, label=None):
    """Parse a numeric cell, retrying with OCR digit corrections (reported)."""
    text = row.raw(fld)
    label = label or fld
    try:
        return parser(text)
    except ValueError as first:
        for repaired, how in _repairs(text):
            try:
                value = parser(repaired)
            except ValueError:
                continue
            issues.append(
                NormalizationIssue(
                    SEVERITY_WARNING,
                    f"{rule}_repaired",
                    f"{label} {text.strip()!r} read as {repaired.strip()!r} ({how}) — confirm",
                    fld,
                )
            )
            return value
        raise _RowError(rule, f"{label} {text.strip()!r}: {first}", fld) from first


def _repairs(text: str) -> list[tuple[str, str]]:
    """Candidate OCR corrections for a cell, mildest first."""
    candidates: list[tuple[str, str]] = []
    digits = ocr_digit_repair(text)
    junk = ocr_junk_strip(text)
    both = ocr_junk_strip(digits)
    for candidate, how in (
        (digits, "OCR digit correction"),
        (junk, "stray marks removed"),
        (both, "OCR digit correction, stray marks removed"),
    ):
        if candidate != text and all(candidate != c for c, _ in candidates):
            candidates.append((candidate, how))
    return candidates


def _normalize(row, parameter_id, profile, source_filename, issues, interpretation, unit, wps):
    mnemonic = _oneline(row.raw("parameter_name"))
    description = _oneline(row.raw("description"))
    stacked = row.raw("mnemonic_and_name")
    if stacked.strip():
        lines = [line.strip() for line in stacked.splitlines() if line.strip()]
        if not mnemonic:
            mnemonic = lines[0]
        if not description and len(lines) > 1:
            description = " ".join(lines[1:])
        interpretation["name"] = (
            f"mnemonic {lines[0]!r}" + (f", name {' '.join(lines[1:])!r}" if len(lines) > 1 else "")
            + " (stacked cell: first line = mnemonic)"
        )
    if not mnemonic:
        raise _RowError("normalize.name", "parameter name is empty", "parameter_name")

    if row.text_source == SOURCE_OCR:
        issues.append(
            NormalizationIssue(SEVERITY_INFO, "normalize.ocr", "text recognized by OCR from the page image")
        )
    elif row.text_source == SOURCE_TEXT_LAYER:
        issues.append(
            NormalizationIssue(
                SEVERITY_INFO, "normalize.ocr", "text from the document's embedded OCR layer"
            )
        )
    for fld in CRITICAL_FIELDS:
        confidence = row.field_confidence(fld)
        if confidence is not None and confidence < profile.ocr_confidence_threshold and row.raw(fld).strip():
            issues.append(
                NormalizationIssue(
                    SEVERITY_WARNING,
                    "normalize.ocr_confidence",
                    f"{fld} {row.raw(fld).strip()!r}: OCR confidence {confidence:.2f} — "
                    "verify against the source image",
                    fld,
                )
            )
    for note in row.extraction_notes:
        issues.append(NormalizationIssue(SEVERITY_INFO, "extract.continuation", note))
    for fld, provenance in row.provenance.items():
        if provenance.note and row.raw(fld).strip():
            issues.append(
                NormalizationIssue(
                    SEVERITY_INFO, "extract.second_pass",
                    f"{fld} {row.raw(fld).strip()!r}: {provenance.note}", fld,
                )
            )

    source_type, canonical, type_issues = resolve_type(row.raw("parameter_type"), row.raw("sign"))
    issues.extend(type_issues)
    interpretation["type"] = f"{row.raw('parameter_type')!r} → {canonical}"

    groups = _parse_numeric(row, "word_location", parse_word_groups, "normalize.word", issues, "word location")
    interpretation["words"] = f"{_oneline(row.raw('word_location'))!r} → {groups}"

    subframes, subframe_issue = _subframes(row, profile, issues)
    if subframe_issue:
        issues.append(subframe_issue)
    interpretation["subframes"] = f"{row.raw('subframe')!r} → {list(subframes)}"

    specs, bit_issues, groups, forced_grouped, bit_note = _bit_specs(row, groups, profile, issues)
    issues.extend(bit_issues)
    interpretation["bits"] = bit_note + ": " + ", ".join(f"{m}-{l}" for m, l in specs)
    for msb, lsb in specs:
        if msb < lsb:
            issues.append(
                NormalizationIssue(
                    SEVERITY_INFO,
                    "normalize.bit_order",
                    f"bit field given low-to-high ({msb}-{lsb}); normalized",
                    "msb",
                )
            )
            break

    frequency_hz: float | None = None
    frequency_text = row.raw("frequency")
    if frequency_text.strip():
        try:
            value = _parse_numeric(row, "frequency", parse_frequency, "normalize.frequency", issues)
        except _RowError as exc:
            issues.append(
                NormalizationIssue(
                    SEVERITY_WARNING,
                    "normalize.frequency",
                    f"{exc}; rate consistency not checked",
                    "frequency",
                )
            )
        else:
            frequency_hz = frequency_to_hz(value, unit)
            if frequency_hz is None:
                issues.append(
                    NormalizationIssue(
                        SEVERITY_WARNING, "normalize.frequency",
                        f"frequency {frequency_text.strip()!r} is not a usable interval", "frequency",
                    )
                )
            elif unit == FREQUENCY_SECONDS:
                interpretation["frequency"] = f"{value:g} s interval → {frequency_hz:g} Hz"
            else:
                interpretation["frequency"] = f"{frequency_hz:g} Hz"
    meaning, layout_issues, explanation = resolve_layout(
        groups, specs, frequency_hz, subframes, profile, forced_grouped
    )
    issues.extend(layout_issues)
    interpretation["layout"] = explanation
    occurrences = build_occurrences(groups, specs, subframes, meaning)
    spacing = spacing_issue(occurrences, wps)
    if spacing is not None:
        issues.append(spacing)

    conversion = ConversionRule()
    resolution_text = cell_value(row.raw("resolution"))
    offset_text = cell_value(row.raw("offset"))
    pair = split_number_pair(resolution_text) if resolution_text else None
    if pair is None and resolution_text:
        for repaired, how in _repairs(resolution_text):
            pair = split_number_pair(repaired)
            if pair is not None:
                issues.append(
                    NormalizationIssue(
                        SEVERITY_WARNING,
                        "normalize.resolution_repaired",
                        f"resolution {resolution_text!r} read as {repaired!r} ({how}) — confirm",
                        "resolution",
                    )
                )
                break
    if pair is not None:
        first, second = pair
        if profile.resolution_pair == PAIR_RESOLUTION_OFFSET:
            resolution_str, offset_str = first, second
            order = "resolution, offset"
            severity = SEVERITY_INFO
        else:
            offset_str, resolution_str = first, second
            order = "offset, resolution"
            severity = SEVERITY_INFO if profile.resolution_pair == PAIR_OFFSET_RESOLUTION else SEVERITY_WARNING
        conversion.resolution = parse_number(resolution_str, profile.decimal_comma)
        conversion.offset = parse_number(offset_str, profile.decimal_comma)
        issues.append(
            NormalizationIssue(
                severity,
                "normalize.resolution_pair",
                f"resolution cell {_oneline(resolution_text)!r} read as {order}: "
                f"offset {conversion.offset:g}, resolution {conversion.resolution:g}"
                + ("" if severity == SEVERITY_INFO else " — confirm (or declare the order in the conventions)"),
                "resolution",
            )
        )
        interpretation["resolution"] = f"{_oneline(resolution_text)!r} → {order}"
        if offset_text:
            try:
                column_offset = parse_number(offset_text, profile.decimal_comma)
            except ValueError:
                column_offset = None
            if column_offset != 0:  # a plain "0" is just the column's default
                issues.append(
                    NormalizationIssue(
                        SEVERITY_WARNING,
                        "normalize.offset_conflict",
                        f"offset column {offset_text.strip()!r} ignored: the resolution cell "
                        "already holds an offset",
                        "offset",
                    )
                )
    else:
        # Conversion problems are reported but never drop the row: the mapping
        # is still worth reviewing, and approval stays blocked by the error.
        if resolution_text:
            try:
                conversion.resolution = _parse_numeric(
                    row, "resolution", lambda t: parse_number(t, profile.decimal_comma),
                    "normalize.resolution", issues,
                )
                interpretation["resolution"] = f"{resolution_text!r} → {conversion.resolution:g}"
            except _RowError as exc:
                issues.append(NormalizationIssue(SEVERITY_ERROR, exc.rule, f"{exc} — edit the row", exc.field))
        elif canonical in (TYPE_ANALOG_SIGNED, TYPE_ANALOG_UNSIGNED):
            issues.append(
                NormalizationIssue(
                    SEVERITY_ERROR,
                    "normalize.resolution",
                    f"resolution is required for {canonical} — edit the row",
                    "resolution",
                )
            )
        elif canonical == TYPE_BCD:
            issues.append(
                NormalizationIssue(
                    SEVERITY_INFO,
                    "normalize.resolution_default",
                    "no resolution given for a BCD field: digits are taken as-is (resolution 1)",
                    "resolution",
                )
            )
        if offset_text:
            try:
                conversion.offset = _parse_numeric(
                    row, "offset", lambda t: parse_number(t, profile.decimal_comma),
                    "normalize.offset", issues,
                )
                interpretation["offset"] = f"{offset_text!r} → {conversion.offset:g}"
            except _RowError as exc:
                issues.append(NormalizationIssue(SEVERITY_ERROR, exc.rule, f"{exc} — edit the row", exc.field))

    minimum = _optional_number(row, "minimum", profile, issues)
    maximum = _optional_number(row, "maximum", profile, issues)

    true_state = cell_value(row.raw("true_state")) or None
    false_state = cell_value(row.raw("false_state")) or None
    notes = cell_value(row.raw("notes")) or None
    if canonical == TYPE_DISCRETE:
        width = max(m - l + 1 for m, l in specs) if specs else 1
        if width > 1:
            state_lines = [line.strip() for line in row.raw("true_state").splitlines() if line.strip()]
            listing = "; ".join(state_lines) if state_lines else "(none listed)"
            issues.append(
                NormalizationIssue(
                    SEVERITY_WARNING,
                    "normalize.discrete_width",
                    f"discrete field is {width} bits wide with states {listing!r}; the "
                    "canonical model labels only zero / non-zero — states kept in notes",
                    "true_state",
                )
            )
            if len(state_lines) > 1:
                notes = f"{notes + ' ' if notes else ''}States: {listing}"
                true_state = None
        if not (true_state or false_state):
            inferred_true, inferred_false = infer_states(
                " ".join([row.raw("description"), row.raw("notes"), stacked])
            )
            if inferred_true or inferred_false:
                true_state, false_state = inferred_true, inferred_false
                issues.append(
                    NormalizationIssue(
                        SEVERITY_WARNING,
                        "normalize.states_inferred",
                        f"discrete states read from free text: 1={true_state!r} 0={false_state!r} — confirm",
                        "true_state",
                    )
                )

    provenance = ParameterProvenance(
        source_type="pdf",
        source_filename=source_filename,
        record_index=row.row_index,
        raw_record=tuple(row.cells),
        extra={
            "page": row.page_number,
            "table": row.table_index,
            "row": row.row_index,
            "bbox": list(row.bbox) if row.bbox else None,
            "columns": list(row.columns),
            "raw_fields": {fld: p.raw_text for fld, p in row.provenance.items()},
            "unmapped": dict(row.extra),
            "confidence": row.confidence,
            "text_source": row.text_source,
            "frequency_unit": unit,
            "interpretation": dict(interpretation),
        },
    )
    return ParameterDefinition(
        id=parameter_id,
        mnemonic=mnemonic,
        description=description,
        source_parameter_type=source_type,
        parameter_type=canonical,
        unit=cell_value(row.raw("units")) or None,
        minimum=minimum,
        maximum=maximum,
        conversion=conversion,
        true_state=true_state,
        false_state=false_state,
        occurrences=occurrences,
        notes=notes,
        provenance=provenance,
    )


def _subframes(row, profile, issues):
    text = row.raw("subframe")
    try:
        return parse_subframe_field(text, profile)
    except _RowError as first:
        repaired = ocr_digit_repair(text)
        if repaired != text:
            try:
                result = parse_subframe_field(repaired, profile)
            except _RowError:
                pass
            else:
                issues.append(
                    NormalizationIssue(
                        SEVERITY_WARNING,
                        "normalize.subframe_repaired",
                        f"subframe {text.strip()!r} read as {repaired.strip()!r} "
                        "(OCR digit correction) — confirm",
                        "subframe",
                    )
                )
                return result
        raise first


def _bit_specs(row, groups, profile, issues):
    fields = (row.raw("msb"), row.raw("lsb"), row.raw("bits"))
    try:
        return resolve_bit_specs(*fields, groups, profile)
    except _RowError as first:
        repaired = tuple(ocr_digit_repair(text) for text in fields)
        if repaired != fields:
            try:
                result = resolve_bit_specs(*repaired, groups, profile)
            except _RowError:
                pass
            else:
                issues.append(
                    NormalizationIssue(
                        SEVERITY_WARNING,
                        "normalize.bits_repaired",
                        f"bit positions {[f.strip() for f in fields if f.strip()]} read as "
                        f"{[f.strip() for f in repaired if f.strip()]} (OCR digit correction) — confirm",
                        "msb",
                    )
                )
                return result
        raise first


def _optional_number(row, fld, profile, issues) -> float | None:
    text = row.raw(fld)
    if not text.strip():
        return None
    try:
        return parse_number(text, profile.decimal_comma)
    except ValueError as exc:
        issues.append(
            NormalizationIssue(SEVERITY_WARNING, f"normalize.{fld}", f"{fld}: {exc}", fld)
        )
        return None


# --------------------------------------------------------------------------
# Document-level evidence
# --------------------------------------------------------------------------


def frequency_unit_evidence(rows: list[RawParameterRow], profile: ImportProfile) -> tuple[int, int, int]:
    """How many rows are rate-consistent reading the frequency column as
    Hz vs as an interval in seconds: ``(hz_consistent, seconds_consistent, usable)``."""
    hz_ok = seconds_ok = usable = 0
    for row in rows:
        try:
            value = parse_frequency(ocr_digit_repair(row.raw("frequency")))
            groups = parse_word_groups(ocr_digit_repair(row.raw("word_location")))
            subframes, _ = parse_subframe_field(row.raw("subframe"), profile)
        except (ValueError, _RowError):
            continue
        if value <= 0:
            continue
        usable += 1
        actual = len(groups) if any(len(g) > 1 for g in groups) or len(groups) == 1 else None
        for unit, counter in ((FREQUENCY_HZ, "hz"), (FREQUENCY_SECONDS, "seconds")):
            expected = expected_occurrences(frequency_to_hz(value, unit), subframes)
            if expected is None or abs(expected - round(expected)) > 1e-9:
                continue
            expected = round(expected)
            consistent = expected == actual if actual is not None else expected in (1, len(groups))
            if consistent:
                if counter == "hz":
                    hz_ok += 1
                else:
                    seconds_ok += 1
    return hz_ok, seconds_ok, usable


def bits_layout_evidence(rows: list[RawParameterRow]) -> int:
    """Rows whose MSB/LSB cells are single ranges over pairs of words — the
    layout of the design spec §29 example."""
    count = 0
    for row in rows:
        try:
            msb = parse_bit_tokens(ocr_digit_repair(row.raw("msb")))
            lsb = parse_bit_tokens(ocr_digit_repair(row.raw("lsb")))
            groups = parse_word_groups(ocr_digit_repair(row.raw("word_location")))
        except ValueError:
            continue
        if (
            len(msb) == 1 and len(lsb) == 1
            and isinstance(msb[0], tuple) and isinstance(lsb[0], tuple)
            and groups and all(len(group) == 2 for group in groups)
        ):
            count += 1
    return count


MIN_LAYOUT_EVIDENCE = 3


def resolve_bits_layout(rows: list[RawParameterRow], profile: ImportProfile) -> tuple[str, str | None]:
    """(multiword_bits setting, explanation when auto-detected)."""
    if profile.multiword_bits != BITS_AUTO:
        return profile.multiword_bits, None
    count = bits_layout_evidence(rows)
    if count >= MIN_LAYOUT_EVIDENCE:
        return BITS_RANGE_PER_WORD, (
            f"{count} rows give MSB and LSB as bit ranges over word pairs: read as bits "
            "of the first / second word of each pair (document-wide layout)"
        )
    return BITS_AUTO, None


def effective_profile(
    rows: list[RawParameterRow], profile: ImportProfile
) -> tuple[ImportProfile, list[tuple[str, str]]]:
    """Resolve the profile's "auto" conventions from document evidence.

    Returns the resolved profile and ``(code, explanation)`` notes for every
    convention that was detected rather than declared.
    """
    import dataclasses

    notes: list[tuple[str, str]] = []
    unit, evidence = resolve_frequency_unit(rows, profile)
    if profile.frequency_unit == FREQUENCY_AUTO and rows:
        notes.append(("PDF_FREQUENCY_UNIT", f"frequency column {evidence}"))
    layout, layout_evidence = resolve_bits_layout(rows, profile)
    if layout_evidence:
        notes.append(("PDF_BITS_LAYOUT", layout_evidence))
    return dataclasses.replace(profile, frequency_unit=unit, multiword_bits=layout), notes


def resolve_frequency_unit(rows: list[RawParameterRow], profile: ImportProfile) -> tuple[str, str]:
    """(unit, explanation) — the profile's declaration, else document evidence."""
    if profile.frequency_unit != FREQUENCY_AUTO:
        return profile.frequency_unit, f"declared by the import profile: {profile.frequency_unit}"
    hz_ok, seconds_ok, usable = frequency_unit_evidence(rows, profile)
    if seconds_ok > hz_ok:
        unit = FREQUENCY_SECONDS
    else:
        unit = FREQUENCY_HZ
    return unit, (
        f"read as {'sample interval in seconds' if unit == FREQUENCY_SECONDS else 'Hz'}: "
        f"{seconds_ok if unit == FREQUENCY_SECONDS else hz_ok} of {usable} rows with a "
        f"usable rate are consistent ({hz_ok} as Hz, {seconds_ok} as seconds)"
    )
