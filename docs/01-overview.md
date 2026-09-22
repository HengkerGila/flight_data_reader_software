# 01 — Overview

## Purpose

Flight data recorders on aircraft such as the CN235 record an **ARINC 717**
data stream: a fixed pattern of 12-bit words whose meaning is defined by a
**dataframe** (also called a dataframe layout, interface control document, or
FDR parameter library). Without the dataframe the stream is just numbers;
with it, each group of bits becomes a named parameter with an engineering
value such as *pitch attitude = −30.096 deg*.

The ARINC 717 Reader is a desktop application that:

1. loads a dataframe from an existing `.adb` file, from a dataframe document
   in PDF form, or from manual entry;
2. holds ARINC 717 data as a canonical frame of four subframes with a
   configurable number of words per second (256 is the primary target);
3. shows the raw frame in a **Frame View** with binary, octal, decimal and
   hexadecimal representations and lets the user edit individual words;
4. decodes every mapped parameter into raw bits, decoded decimal and
   engineering value, with a complete trace of how each value was derived;
5. simulates frames without aircraft hardware: random words, hand-edited
   words, or a **scenario** in which requested engineering values are encoded
   into the frame and decoded back for closed-loop verification;
6. exports dataframes back to `.adb`.

Real hardware acquisition (ABUS 717 / FTDI) is deliberately deferred; the
application is designed so a hardware source can be added later behind the
same frame-source interface without touching the rest.

## ARINC 717 in five minutes

An ARINC 717 recording is organised as follows.

| Term | Meaning |
| --- | --- |
| **Word** | A 12-bit value, 0 to 4095. Bits are numbered 12 (most significant) down to 1 (least significant). |
| **Subframe** | A sequence of *WPS* words transmitted in one second. There are always four subframes, numbered 1 to 4. |
| **Frame** | Four consecutive subframes, four seconds of data. |
| **WPS** | Words per second: the length of a subframe. Common values are 64, 128, 256, 512 and 1024; the primary target here is 256. |
| **Sync word** | Word 1 of each subframe holds a fixed synchronisation pattern that identifies the subframe. The usual pattern is 583, 1464, 2631, 3512 (octal 1107, 2670, 5107, 6670). |
| **Parameter** | A named quantity recorded in the frame, such as pitch attitude, airspeed or a landing-gear discrete. |
| **Mapping** | Where a parameter's bits live: which subframes, which word address, which bit range. |
| **Occurrence** | One sample of a parameter within a subframe. A parameter sampled twice per second has two occurrences in different words of every subframe. |
| **Segment** | One contiguous bit field inside one word. A value wider than one field spans several segments, assembled in a defined order. |
| **Superframe** | A slower cycle spanning many frames; parameters recorded once per superframe are recognised but not supported yet. |

Sample rate follows from the mapping: a parameter recorded in all four
subframes with one occurrence is sampled once per second (1 Hz); two
occurrences per subframe give 2 Hz; one occurrence in only subframes 1 and 3
gives 0.5 Hz.

## Three separate domains

The design keeps three things apart, and the code never mixes them (spec §4):

| Domain | Holds | Lives in |
| --- | --- | --- |
| **Canonical dataframe** | *How* bits are interpreted: parameters, types, mappings, conversions, discrete state labels, provenance | `arinc717_reader/domain/dataframe.py`, `domain/parameter.py` |
| **Canonical frame** | *Only* raw 12-bit words: four subframes of WPS integers | `arinc717_reader/domain/frame.py` |
| **Engineering data** | Decoded values derived from frame + dataframe, each with a trace | `arinc717_reader/domain/engineering.py` |

They meet in the **parameter decoder**, which reads a frame and a dataframe
and produces engineering values. The GUI observes these three kinds of data
through stores and never becomes a source of truth itself.

## The workflow at a glance

```
 .adb file ──► ADB parser ─────┐
 PDF document ─► PDF importer ─┤──► Canonical dataframe ──► Validator
 manual entry ─► Editor ───────┘            │
                                            ▼
 Random / Manual / Scenario ──► Canonical frame ──► Parameter decoder ──► Engineering values
                                     ▲                                          │
                                     └──────── Parameter encoder (scenario) ◄───┘  (closed loop)
```

A typical session: load a dataframe, generate or load a frame, inspect words
and decoded parameters, edit words or apply a scenario, save the frame, and
export the dataframe if it was edited or imported.

## Package map

```
arinc717_reader/
├── app.py                 application assembly (stores, services, main window)
├── demo.py                bundled 256 WPS demo dataframe
├── domain/                canonical models: frame, dataframe, parameter, engineering
├── decoder/               bit extraction, segment assembly, signed/unsigned/BCD/discrete, conversion
├── encoder/               inverse path: parameter encoder and frame builders (scenario simulation)
├── dataframe/
│   ├── adb_codec/         .adb parser, writer, record layout, subframe selector, legacy fields
│   ├── validator/         structural, mapping and semantic validation
│   ├── repository/        SQLite persistence
│   ├── pdf_importer/      PDF pipeline: ingest, scan, extract, normalize, review, pipeline, synth
│   ├── editor.py          Qt-free helpers for the dataframe editor
│   └── compare.py         semantic dataframe comparison (round-trip acceptance)
├── sources/               frame sources: random, manual, scenario, frame JSON I/O, hardware stub
├── state/                 observable stores: dataframe, frame, engineering
├── services/              dataframe, frame, decoding and simulation services
└── ui/                    PySide6 GUI: main window and the five pages with their dialogs
```

Everything below `ui/` is importable and testable without a Qt application.
