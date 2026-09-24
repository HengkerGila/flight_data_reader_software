# ARINC 717 Reader — Documentation

This folder documents the ARINC 717 Reader: a simulation-first desktop
application that loads a flight-data-recorder dataframe, shows raw ARINC 717
frames, decodes them into engineering values, simulates frames, imports
dataframe definitions from `.adb` files or from dataframe layout documents
in PDF form (born-digital or scanned), receives a live word stream from an
STM32 hardware-in-the-loop simulator (or an in-process virtual device),
decodes it subframe by subframe, graphs parameters, and records and replays
sessions.

![Main window](../img/mockups/frame_view.png)

*The application with the FDS81 dataframe (imported from a PDF) during a live stream from the virtual device: Frame View, Parameter Bits panel and Word Inspector.*


The implementation specification the software follows is the project's
internal *ARINC 717 Reader System Design*, revision 2, which is not part of
the repository: it keeps the v1 sections unchanged and adds the live
streaming, graphing and recording sections (§3A, §26A–§26T, §46A–§46C,
Phases 9–14). Section numbers quoted as "spec §14" refer to sections common
to both revisions; "spec v2 §26G" marks a revision 2 addition. Where the
code has since departed from the spec (the `.adb` record layout, replaced on
2026-09-24 by the layout verified against real AFDA files), these pages
describe the code.

## Contents

| Page | What it covers |
| --- | --- |
| [01 — Overview](01-overview.md) | What the software does, an ARINC 717 primer, the three data planes, the main workflow, package map |
| [02 — Installation and running](02-installation-and-running.md) | Requirements, environment setup, starting the application, running the tests, regenerating the examples |
| [03 — User guide](03-user-guide.md) | Every page of the GUI, dialog by dialog, plus step-by-step workflows |
| [04 — Data model and validation](04-data-model.md) | Canonical dataframe, frame and engineering models, parameter types, bit numbering, occurrences and segments, BCD digit weights, state tables and decimals, validation rules, semantic comparison |
| [05 — Decoding and encoding](05-decoding-and-encoding.md) | The decoder pipeline, per-type interpretation, statuses and traces, the scenario encoder and closed-loop validation, worked examples |
| [06 — The ADB format](06-adb-format.md) | The `.adb` codec for the AFDA layout verified against real files: settings record, 238-field parameter records, frame-absolute locations, concatenated parts, BCD digit weights, state tables, what the format cannot hold, the byte-for-byte round trip, open questions and the AFDA probe |
| [07 — PDF import](07-pdf-import.md) | The dataframe-document importer: extraction of born-digital and scanned pages, OCR, normalization rules, conventions, review, publishing |
| [08 — File formats and storage](08-file-formats-and-storage.md) | Frame JSON files, the SQLite repository schema, where each kind of data lives |
| [09 — Architecture and development](09-architecture-and-development.md) | Layering rules, stores and services, event flow, spec traceability, testing strategy, extension points, roadmap |
| [10 — Troubleshooting](10-troubleshooting.md) | Error states, decode statuses, OCR and PDF problems, environment quirks |
| [11 — Scanned page processing](11-scanned-page-processing.md) | The image pipeline for scanned documents: scan detection, deskew, ruling-line grid detection, text layer vs OCR, cell assignment, second-pass recognition, tuning, limitations |
| [12 — The SIM-A717 v1 protocol](12-sim-a717-protocol.md) | The project-owned serial protocol of the hardware-in-the-loop simulator: word container, framed packets, commands and replies, device modes, fault injection, what the PC sends, the virtual device |
| [13 — Live streaming, graphs and recording](13-live-streaming-graphs-recording.md) | The live path: threads and the pump, parser, synchronizer and assembler, progressive frames, per-subframe decoding into timestamped samples, the bounded time-series store, the Graphs page, engineering-signal upload, session files, replay, acceptance and constraints |

The firmware for the STM32F103 stream emulator is kept outside this
repository together with its own README (wiring, build, flash, limits);
[12](12-sim-a717-protocol.md) documents the protocol it speaks and the
virtual device that stands in for it.

## Quick start

```bash
cd ~/projects/flight_data_reader_software
.venv/bin/arinc717-reader examples/demo_256wps.adb
```

Then press **▶ Start stream** in the toolbar. With nothing connected yet this
connects to the in-process virtual STM32 device (the port selected on the
**Hardware** tab by default) and starts a 256 WPS stream whose values are
encoded through the dataframe on the PC and decoded back on arrival. Watch
the **Frame View** update one subframe column per second, the **Parameters**
tab follow, and pick a parameter on the **Graphs** tab. **❚❚ Pause stream**
holds the frame so it can be inspected and edited; the **Help** tab (F1) is
the in-application user guide.

## Conventions used in these pages

- **Word** means one 12-bit ARINC 717 word; **WPS** means words per second,
  which is also the number of words in each subframe.
- Bit numbers run **12 (most significant) down to 1 (least significant)**.
- Subframes are numbered **1 to 4**; word addresses are **1-based**.
- Stream, connection and slot states are written in capitals as the
  application shows them (`STREAM_SYNC_LOST`, `RECONNECTING`, `MISSING`).
- Code paths are given relative to the package, for example
  `arinc717_reader/decoder/parameter_decoder.py`.
