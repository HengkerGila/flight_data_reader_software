# 04 — Data model and validation

All models are plain Python dataclasses in `arinc717_reader/domain/`. They
are deliberately permissive: an importer may hold rows that are not valid
yet. Enforcement lives in the validator and in the decoder, which reports
explicit statuses instead of raising.

## The canonical frame

`domain/frame.py`

```python
@dataclass
class Arinc717Frame:
    wps: int                      # words per subframe, positive
    frame_index: int = 0
    subframes: list[list[int]]    # exactly 4 lists of exactly wps integers, each 0..4095
```

The frame holds raw words only; it never contains engineering values.
`frame.word(subframe, word)` and `frame.set_word(subframe, word, value)` use
1-based subframe (1–4) and word address (1–WPS) and raise `FrameError` for
anything outside those ranges or any value outside 0..4095. A blank frame is
all zeros; sync words are only inserted on request, at word 1 of each
subframe. Constants: `SUBFRAME_COUNT = 4`, `WORD_BITS = 12`, `WORD_MAX = 4095`.

## The canonical dataframe

`domain/dataframe.py`, `domain/parameter.py`

```
DataframeDefinition
├── metadata: DataframeMetadata
└── parameters: list[ParameterDefinition]
      ├── conversion: ConversionRule
      ├── states: list[DiscreteState]
      ├── occurrences: list[ParameterOccurrence]
      │     └── segments: list[ParameterSegment]
      └── provenance: ParameterProvenance | None
```

### DataframeMetadata

| Field | Type | Meaning |
| --- | --- | --- |
| `dataframe_name` | str | Display name; the file stem for imports. |
| `wps` | int | Words per second. |
| `aircraft_type`, `revision`, `issue_date` | str or None | Free text. |
| `superframe_present` | bool or None | Recorded for information; superframes are not decoded. |
| `sync_words` | list[int] | Four sync words, SF1..SF4. |
| `source_type` | str | `adb`, `pdf`, `manual`, `demo`, `repository`. |
| `source_filename`, `source_hash` | str or None | File name and SHA-256 of the imported file. |
| `adb_settings_raw` | tuple[str, ...] or None | The complete raw `Setting` record of an `.adb` import, preserved losslessly. |

### ParameterDefinition

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | str | Unique within the dataframe. Importers assign `adb-0001`, `pdf-0001`; the editor assigns `man-<slug>`; the demo uses `demo-pitch` and similar. |
| `mnemonic` | str | Short name shown everywhere. |
| `description` | str | Long name. |
| `source_parameter_type` | str or None | The type text exactly as the source wrote it (`BNR`, `Signed Analog`, `Discrete`, …). |
| `parameter_type` | str | The canonical type, always derived from the source text (see below). |
| `unit` | str or None | Engineering unit. |
| `minimum`, `maximum` | float or None | Expected engineering range; values outside it decode with status `OUT_OF_RANGE`. |
| `conversion` | ConversionRule | `resolution` (default 1.0), `offset` (default 0.0), `formula_type` (`linear` is the only supported formula). |
| `decimals` | int or None | Display precision as the source records it (AFDA column 8). `None` means "derive from the resolution": `effective_decimals()` returns the explicit value, 0 for a discrete, otherwise what the resolution implies (`resolution_decimals()`: 0.25 → 2, 0.0062 → 4, 1 → 0, capped at 4). Shown in the details panel, editable, written on export; the Parameters page does not round values with it. |
| `true_state`, `false_state` | str or None | The two-state shortcut: the labels of the field values 1 and 0. |
| `states` | list[DiscreteState] | The full state table (`value` → `label`) of a discrete with more than two states, up to the 32 an AFDA record holds. Empty when the two labels say it all. `effective_states()` returns the table, or the one the two labels imply (`0 = false_state`, `1 = true_state`); `state_label(value)` looks one value up. |
| `occurrences` | list | The mapping. |
| `notes` | str or None | Free text. |
| `provenance` | ParameterProvenance or None | Where the definition came from. |

### Canonical parameter types and normalisation

| Canonical type | Decoded as | Typical source text |
| --- | --- | --- |
| `analog_signed` | two's complement of the assembled field, then linear conversion | Signed Analog, Signed, BNR Signed, BNR (signed), two's complement |
| `analog_unsigned` | the assembled field as an integer, then linear conversion | Unsigned Analog, Unsigned, BNR, Analog, Binary |
| `bcd` | 4-bit digits validated 0–9, then linear conversion; when every segment carries a BCD digit weight, the weighted sum of one digit per segment instead | BCD |
| `discrete` | the state table's label for the field value when the parameter has a table; otherwise zero → `false_state`, non-zero → `true_state` | Discrete, Disc, Status, Boolean |
| `raw` | the assembled integer, no conversion | Raw |
| `unknown` | not decoded; status `UNSUPPORTED_TYPE` | anything unrecognised |

`normalize_source_type()` in `domain/parameter.py` is the single function
that performs this mapping. It first looks the trimmed, upper-cased text up
in an exact table; when that fails it classifies by keyword (BCD, discrete
words, UNSIGNED, then signed markers such as SIGNED / 2'S / TWO'S /
COMPLEMENT, then BNR / BINARY / ANALOG, then RAW). Explicit signedness always
wins, a bare `BNR` is unsigned, and anything without a recognised keyword is
`unknown` rather than guessed. The `.adb` parser, the PDF importer and the
parameter editor all call this function, so a parameter's canonical type
never changes between an import and a later edit.

### Occurrences and segments

```python
@dataclass
class ParameterOccurrence:
    index: int                       # 1-based sample number within a subframe
    segments: list[ParameterSegment]

@dataclass
class ParameterSegment:
    sequence: int                    # assembly order; 1 = most significant bits
    subframes: tuple[int, ...]       # every subframe this segment is recorded in
    word: int                        # 1-based word address
    lsb: int                         # bit numbers 12..1, accepted in either order
    msb: int
    source_raw: dict | None = None   # preserved raw fields (ADB selector and word text, legacy flag)
    bcd_weight: float | None = None  # AFDA-style BCD: this segment is one digit with this decimal weight
```

Semantics that the decoder and encoder rely on:

- **Bit numbering** is 12 (most significant) to 1 (least significant).
  `lsb` and `msb` may arrive in either order; `bit_range` normalises them and
  `width` is `high − low + 1`.
- **One occurrence is one sample.** A parameter with two occurrences in every
  subframe yields two samples per subframe.
- **A segment mapped to several subframes** is recorded in each of them, so
  one occurrence produces one decoded sample per listed subframe. All
  segments of an occurrence must agree on the subframe set.
- **Segment sequence defines assembly order**, never the word number. The
  segment with sequence 1 contributes the most significant bits.
- **BCD digit weights.** When every segment of a BCD occurrence carries a
  `bcd_weight` (`10, 1` or `10, 1, 0.1`), each segment is one digit and the
  value is the sum of digit × weight. A `None` weight on any segment means
  the assembled field is read as plain 4-bit nibbles. The `.adb` parser sets
  the weights from the file and the repository stores them. The parameter
  editor does not carry them yet: `build_occurrences()` rebuilds segments
  without a weight, so a weighted BCD parameter that is edited in the
  dialog becomes plain nibble BCD (see [09 — Roadmap](09-architecture-and-development.md#roadmap-and-open-ends)).

Example: pressure altitude as a 21-bit value spread over two words.

```
occurrence 1
  segment 1: subframes (1,2,3,4)  word 154  bits 9-1     ← 9 most significant bits
  segment 2: subframes (1,2,3,4)  word 153  bits 12-1    ← 12 least significant bits
```

### Provenance

```python
@dataclass
class ParameterProvenance:
    source_type: str | None          # "adb", "pdf", "repository", ...
    source_filename: str | None
    record_index: int | None         # record number in the .adb, or row number in a PDF table
    raw_record: tuple[str, ...] | None   # the verbatim source fields / cells
    extra: dict                      # ADB: trailing_fields, adb_warnings, adb_conversion_kind; PDF: page, bbox, raw texts, interpretation, review history
```

Provenance answers "where did this value come from?" (spec §30, §58) and is
shown in the Dataframe page details and the PDF review dialog. For an `.adb`
import `raw_record` is the complete 238-field record and `adb_warnings`
lists what the parser had to guess or ignore. The `.adb` writer writes a
parameter that is still semantically identical to its `raw_record` back as
that record, byte for byte; anything edited or created in the application
is generated from the canonical model. That is what makes the round trip
lossless ([06 — The ADB format](06-adb-format.md#round-trip)).

## Engineering data

`domain/engineering.py`

```python
@dataclass
class EngineeringValue:
    parameter_id: str
    parameter_name: str
    occurrence_index: int
    subframe: int | None             # the subframe this sample was decoded from
    raw_bits: str | None             # assembled bits, most significant first
    raw_integer: int | None
    decoded_decimal: int | float | None
    engineering_value: int | float | str | None
    unit: str | None
    status: str
    trace: DecodeTrace
```

`DecodeTrace` records every `SegmentTrace` (sequence, subframe, word, bit
range, width, the word value, extracted value and bits), the assembled bits
and width, the decoded decimal, resolution, offset, the engineering value,
the status and a message. `format_decode_chain()` renders it for the
Parameters page.

Statuses: `VALID`, `INVALID_MAPPING`, `INVALID_BCD`, `OUT_OF_RANGE`,
`UNSUPPORTED_TYPE`, and `MISSING_DATA` (reserved for sources that cannot
deliver a word; the current decoder never emits it).

## Validation

`arinc717_reader/dataframe/validator/` runs three layers and returns a list
of `ValidationIssue(severity, rule_name, message, parameter_id,
related_parameter_id)`. Errors mean the dataframe cannot decode correctly;
warnings mean something a human should look at. Overlap issues carry the
second parameter in `related_parameter_id`, and `issue.concerns(id)` tests
either side.

| Rule | Severity | Condition |
| --- | --- | --- |
| `structural.wps` | error | WPS is not positive. |
| `structural.sync_words` | warning / error | Not exactly four sync words / a sync word outside 0..4095. |
| `structural.mapping_empty` | error | A parameter has no occurrences, or an occurrence has no segments. |
| `structural.word_range` | error | A segment's word is outside 1..WPS. |
| `structural.bit_range` | error | A segment's LSB or MSB is outside 1..12. |
| `structural.subframe` | error | A segment lists no subframes or one outside 1..4. |
| `structural.resolution` | error | An analog or BCD parameter has resolution 0. |
| `structural.formula` | warning | A conversion formula other than `linear`. |
| `mapping.occurrence_index` | error | The same occurrence index used twice in a parameter. |
| `mapping.segment_sequence` | error | The same segment sequence used twice in an occurrence. |
| `mapping.subframe_consistency` | error | Segments of one occurrence disagree on subframes. |
| `semantic.type` | error / warning | The canonical type is not one of the six / it is `unknown` (the parameter will decode as `UNSUPPORTED_TYPE`). |
| `semantic.discrete_states` | warning | A discrete with neither of the two labels nor a state table. |
| `semantic.range` | error | Minimum greater than maximum. |
| `semantic.overlap` | warning | Two parameters share bits in the same subframe and word; one issue per subframe and word. Overlaps are warnings because real dataframes overlay spares and supersets. |

`validate_dataframe()` is called by every service operation that changes the
dataframe, and the issues are kept in the dataframe store for the GUI.

## The editing helpers

`arinc717_reader/dataframe/editor.py` contains the Qt-free logic behind the
editor dialogs: `new_dataframe()`, `make_parameter_id()` (`man-` plus a slug
of the mnemonic, made unique), `parse_subframes()` (accepts `13`, `1234`,
`0`, `1,3`, `1 3`, `2-4`, `all`), `segment_rows()` and `build_occurrences()`
(flatten a mapping into editable rows and back, preserving raw fields while
the subframe set is unchanged), `candidate_issues()` (validation preview of
an edit including overlaps) and `duplicate_parameter()`.

## Semantic comparison

`arinc717_reader/dataframe/compare.py` implements the round-trip acceptance
test of spec §37: `dataframe_differences(a, b, ignore=())` lists every
semantic difference between two dataframes — WPS, sync words, preserved
settings, parameter count and order, every definition field, conversion,
effective decimals, effective state tables, trailing legacy fields,
occurrences, segments, subframes, words, bits, BCD digit weights and legacy
flags — and returns an empty list when they are equivalent.
`parameter_differences(a, b, ignore=())` does the same for one parameter
(ids excluded). `ignore` names parameter attributes to leave out: the ADB
format has no notes column, so an ADB round trip is compared with
`ignore=("notes",)`. The `.adb` writer uses `parameter_differences` to
decide whether an imported parameter may be written back verbatim.
Byte-for-byte equality of files is deliberately not required here, although
the writer achieves it for unchanged files.
