# 06 — The ADB format

`.adb` files are the dataframe databases used by the Aering/AFDA family of
flight-data tools. The application reads and writes them through
`arinc717_reader/dataframe/adb_codec/`.

## What an ADB file looks like

- Plain ASCII text, CSV-like positional fields, CRLF line endings.
- Quoted fields may contain commas and embedded line breaks.
- Many fields are intentionally empty.
- The first record begins with `Setting`; every following record is one
  parameter.

The parser uses Python's `csv` module (spec §33 forbids splitting on commas
by hand), reads the file as bytes to compute its SHA-256, and decodes it as
UTF-8 with replacement so a stray byte never aborts an import.

```
Setting,1,57,9,12,256,0,583,1464,2631,3512
PITCH ATT #1,"Pitch attitude, captain side",Signed Analog,deg,0.176,0,-90,90,,,,2,1,1234,4,3,12,,1,1234,132,3,12,
LDG GEAR DOWN,Landing gear lever position,Discrete,,1,0,,,DOWN,UP,,1,1,1234,13,1,1,
```

## The settings record

| Index | Example | Meaning |
| --- | --- | --- |
| 0 | `Setting` | Record tag. |
| 1–4 | `1`, `57`, `9`, `12` | Unknown semantics; preserved verbatim as `legacy_setting_1` … `legacy_setting_4`. |
| 5 | `256` | **WPS.** |
| 6 | `0` | Unknown; preserved as `legacy_setting_6`. |
| 7–10 | `583`, `1464`, `2631`, `3512` | **Sync words** SF1..SF4. |
| 11+ | | Any further fields are preserved too. |

The whole record is stored in `DataframeMetadata.adb_settings_raw`; the
writer re-emits it with WPS and sync words overridden from the canonical
metadata. A dataframe that never came from an ADB (manual, PDF, demo) is
written with a template header (`Setting,1,57,9,12,<wps>,0,<sync…>`), the
template values being copied from the observed example rather than invented.

## Parameter records — provisional layout

> The parameter record layout below is this project's serialisation of what
> reverse engineering of the Aering/AFDA files indicates. Real supplied files
> were not available when it was written. When one is, the constants in
> `adb_codec/mappings.py` are the only place to adjust; nothing else depends
> on field positions, and unknown fields are preserved either way.

| Index | Field | Notes |
| --- | --- | --- |
| 0 | mnemonic | Empty mnemonics become `PARAM_<record number>`. |
| 1 | description | |
| 2 | source type | Kept verbatim; canonical type derived by `normalize_source_type`. |
| 3 | unit | |
| 4 | resolution | Default 1 when empty. |
| 5 | offset | Default 0 when empty. |
| 6 | minimum | Optional. |
| 7 | maximum | Optional. |
| 8 | true state | |
| 9 | false state | |
| 10 | notes | |
| 11 | occurrence count | |
| 12… | occurrence groups | For each occurrence: a **segment count**, then per segment five fields: **subframe selector, word, LSB, MSB, legacy flag**. |
| after the groups | trailing fields | Anything left is preserved as `provenance.extra["trailing_fields"]`. |

Each segment keeps its raw selector text and its legacy flag in
`segment.source_raw` (`subframe_selector_raw`, `legacy_flag`). A parse error
names the record and the problem (`record 3: PITCH: invalid word 'x'`).

## The subframe selector

Selectors are strings of subframe digits (spec §36), decoded and encoded by
one codec in `adb_codec/mappings.py`:

| Selector | Subframes |
| --- | --- |
| `1` | (1,) |
| `13` | (1, 3) |
| `24` | (2, 4) |
| `1234` | (1, 2, 3, 4) |
| `0` | (1, 2, 3, 4) — the common "every subframe" convention, provisional until verified |

Digits outside 1–4 or non-digit text raise `SubframeSelectorError`. The
writer reuses the original selector text of an unchanged segment (so `0`
stays `0`) and regenerates it from the subframe tuple only after an edit that
changed the subframes.

## Losslessness and the round trip

The acceptance target of spec §37 is that decoding an exported file gives the
same dataframe as decoding the original, at the semantic level:

```
existing.adb → parse → DataframeDefinition → write → generated.adb → parse
dataframe_differences(first, second) == []
```

What survives: settings (including unknown header fields), sync words, every
parameter's name, description, source type, unit, conversion, range, states
and notes, the occurrence and segment structure, subframes, words, bits,
segment legacy flags and trailing legacy fields. Byte-for-byte equality is
not promised (field formatting may differ), and it is not needed.

The writer emits CRLF-terminated CSV and formats numbers with Python's `g`
format, so `0.176` stays `0.176` and `1.0` becomes `1`.

## Legacy field helpers

`adb_codec/legacy.py` exposes the preserved unknown fields without inventing
meanings: `legacy_setting_fields(metadata)` returns the unknown header fields
keyed `legacy_setting_<index>`, `trailing_legacy_fields(parameter)` the
trailing record fields, and `segment_legacy_flag(source_raw)` the per-segment
flag. The Dataframe page shows them in the details panel.

## Adapting to a real file

1. Parse the file with `parse_adb_file`; a clear `AdbParseError` tells you
   which record and field disagree with the layout.
2. Adjust the index constants and, if the group structure differs, the
   segment group size in `adb_codec/mappings.py`.
3. Run `tests/test_adb_roundtrip.py` against the real file and add it as a
   fixture: the semantic round trip must stay empty.
4. Keep preserving anything you do not understand; `legacy_*` names exist for
   exactly that.
