# 06 — The ADB format

`.adb` files are the dataframe databases of the AFDA flight-data software.
The application reads and writes them through
`arinc717_reader/dataframe/adb_codec/`.

The layout below was verified on 2026-09-24 against two real AFDA files
supplied by the user: a vendor-made NC212i dataframe (442 parameters) and a
hand-made one (37 parameters). They share one identical header and one
record shape. The earlier "provisional" layout of this project was a guess
and has been replaced; files written with it are rejected with a clear
error and must be re-exported.

## What an ADB file looks like

- Plain text in the Windows code page (cp1252; the vendor file contains a
  right single quote), CRLF line endings, no trailing blank line.
- CSV: fields containing a line break are quoted; the vendor file has
  descriptions with embedded line breaks.
- The first record begins with `Setting`; every following record is one
  parameter and has **exactly 238 fields**, most of them empty.

The parser uses Python's `csv` module (spec §33 forbids splitting on commas
by hand), reads bytes to compute the SHA-256, decodes UTF-8 when the bytes
are valid UTF-8 (plain ASCII included) and cp1252 otherwise. The writer
always emits cp1252.

```
Setting,1,64,1,12,256,0,583,1464,2631,3512
ACC LATERAL,LATERAL ACCELEROMETER,Unsigned Analog,G,-1,1,0.002,-1.08,0,-,,,…,4,1,1234,4,1,12,,1234,68,1,12,,1234,132,1,12,,1234,196,1,12,,…
GEAR LH,LH WOW,Discrete,Status,0,1,1,0,0,Discrete,0,1,,,…,ON AIR,ON GROUND,,,…,1,1,1234,248,3,3,,…
```

## The settings record

| Index | Example | Meaning |
| --- | --- | --- |
| 0 | `Setting` | Record tag. |
| 1 | `1` | Unknown; preserved as `legacy_setting_1`. |
| 2 | `64` | **Words per subframe = WPS.** |
| 3 | `1` | Unknown; preserved as `legacy_setting_3`. |
| 4 | `12` | Bits per word. |
| 5 | `256` | Words per frame (4 × WPS). |
| 6 | `0` | Unknown; preserved as `legacy_setting_6`. |
| 7–10 | `583`, `1464`, `2631`, `3512` | **Sync words** SF1..SF4 in decimal (octal 1107, 2670, 5107, 6670). |
| 11+ | | Any further fields are preserved too. |

The whole record is stored in `DataframeMetadata.adb_settings_raw`; the
writer re-emits it with WPS, words per frame and sync words taken from the
canonical metadata (words per frame only when the source file showed it to
be 4 × WPS). A dataframe that never came from an ADB (manual, PDF, demo) is
written with `Setting,1,<wps>,1,12,<4×wps>,0,<sync…>`, the unknown values
copied from the observed header rather than invented.

## Parameter records

| Index | Field | Notes |
| --- | --- | --- |
| 0 | name | Empty names become `PARAM_<record number>`. |
| 1 | description | May contain line breaks (then quoted). |
| 2 | type | `Signed Analog`, `Unsigned Analog`, `Discrete`, `BCD`. Kept verbatim; canonical type via `normalize_source_type`. |
| 3 | unit | Often empty. |
| 4 | minimum | |
| 5 | maximum | |
| 6 | scale | Resolution, engineering units per count. Default 1 when empty. |
| 7 | offset | Default 0 when empty. |
| 8 | decimals | Display precision. |
| 9 | conversion kind | `-` for analog and BCD, `Discrete` when a state table follows, empty otherwise. |
| 10–41 | state values / BCD weights | 32 slots. Discrete: the raw value of each state. BCD: one decimal weight per part (`1,10` or `0.01,0.1,1,10`). |
| 42–73 | state labels | 32 slots, parallel to the values (`ON AIR`, `ON GROUND`). |
| 74 | samples per frame | Number of samples = locations ÷ parts. |
| 75 | parts per sample | 1, or the number of word parts of a concatenated value. |
| 76–235 | 32 location slots | Each slot is five fields: **subframe selector, word, lsb, msb, spare** (the spare has been blank in every file seen; it is kept as the segment's `legacy_flag`). |
| 236–237 | | Always blank. |
| 238+ | trailing fields | Anything beyond 238 is preserved as `provenance.extra["trailing_fields"]`. |

Bit numbers are 1..12 with bit 1 the LSB, as in the rest of the
application.

### Locations

Every sample of a parameter is listed explicitly: a 4 Hz parameter in a
64 wps frame has four locations (words 4, 68, 132, 196), an 8 Hz one eight.
Word numbers are **frame-absolute**, 1..4×WPS, and such locations carry the
selector `1234`; the subframe follows from the word (`248` = SF4 word 56).
The vendor's own editor writes nothing else, and the user's hand-made file
uses only this form.

The parser folds locations into the canonical model: samples of the same
word bits in different subframes become one segment with a multi-subframe
tuple; different words become separate occurrences; the parts of a
concatenated value become the segments of one occurrence. The writer does
the reverse, always in the `1234` + absolute form.

A minority of vendor-file locations use a single subframe digit or a pair
(`4,19` for the year, `13,204` for TAT). The parser reads a word inside one
subframe (≤ WPS) as relative to the subframes the selector names, and a
larger word as frame-absolute with the selector ignored and a note in
`provenance.extra["adb_warnings"]`. See "Open questions".

### Concatenated values and BCD

Column 75 > 1 means each sample spans that many word parts, listed one
after the other. For BCD the vendor file lists the digit weights least
significant first (`1,10`; `0.01,0.1,1,10`), so the codec assumes parts
are listed least significant first for analog values too
(`mappings.PARTS_ORDER`). Sequence 1 of a canonical occurrence is the most
significant part (spec §13), so the parser reverses the file order and the
writer restores it.

BCD digit weights are stored per segment (`ParameterSegment.bcd_weight`).
The decoder then computes Σ digit × weight instead of treating the
assembled field as plain nibbles, and the encoder splits a value into
digits by the same weights.

### Discrete states

`true_state` / `false_state` remain the two-state shortcut (labels of values
1 and 0). The full table lives in `ParameterDefinition.states`, so a
multi-bit discrete with values 0..3 keeps its four labels and decodes to
them. The parser collapses a table whose values are only 0 and 1 into the
two labels, so a plain on/off discrete never carries a table; a state slot
with a label but no value, or a non-integer value, gets the slot index as
its value and an import note. The writer emits the table when present and
otherwise derives `0=false_state, 1=true_state`.

### What the format cannot hold

- **Notes** have no column and are not exported.
- At most **32 locations** per parameter (8 Hz at 4 subframes); more
  raises `AdbWriteError`.
- At most 32 discrete states.
- Occurrences of different part counts within one parameter cannot share a
  parts count; the writer then lists every segment as a separate sample.

## Round trip

An imported parameter that is still semantically identical to its raw
record is written back as that record, byte for byte, so
`write(parse(file)) == file` for both real samples. Anything edited or
created in the application is generated from the canonical model with
plain decimal numbers (`0.00017166137`, never `1.7166137E-4`), AFDA's type
vocabulary (`BNR` becomes `Unsigned Analog`), `1234` + absolute words, the
state table and BCD weights. The semantic round trip
`dataframe_differences(a, parse(write(a)), ignore=("notes",)) == []` holds
for the demo dataframe and for the real files with provenance stripped.

## Open questions (to settle in AFDA itself)

`tools/make_afda_probe.py` writes `examples/afda_probe/afda_probe_64wps.adb`,
a small database in this layout with parameters named after what they test.
Opening it in AFDA answers:

1. **Selector semantics.** Does AFDA place `2,6` at SF2 word 6 (relative),
   `3,70` at SF2 word 6 (absolute, selector ignored) or somewhere else, and
   does `13,6` yield two samples? The parser's rule above is the working
   assumption.
2. **Part order.** Is the first listed part of a concatenated value the
   least or the most significant? Flip `PARTS_ORDER` if AFDA shows MS-first.
3. **Decimals and states.** Whether column 8 drives the display precision
   and whether a four-state table is accepted.
4. **Tolerance.** Whether empty min/max or an unknown type text is accepted
   (the probe avoids both).

`examples/afda_probe/README.md` is the checklist. If AFDA can save the
database, the saved file shows how AFDA itself writes those rows, which is
the best evidence of all.

## Legacy field helpers

`adb_codec/legacy.py` exposes preserved unknown fields without inventing
meanings: `legacy_setting_fields(metadata)` for the unknown header fields,
`trailing_legacy_fields(parameter)` for fields beyond 238,
`segment_legacy_flag(source_raw)` for a location's spare field and
`adb_warnings(parameter)` for what the parser guessed or ignored: a
selector other than `1234` on a frame-absolute word (ignored), a declared
samples × parts count that does not match the locations present, locations
that do not divide into whole parts, a non-numeric state value or BCD digit
weight, or a number of weights that differs from the parts count (the
weights are then dropped). The Dataframe page shows all of them in the
details panel, the warnings as *import note* lines. A segment's
`source_raw` also keeps the selector and word text exactly as the file
wrote them (`adb_selector`, `adb_word`).
