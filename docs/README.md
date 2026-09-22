# ARINC 717 Reader — Documentation

This folder documents the ARINC 717 Reader: a simulation-first desktop
application that loads a flight-data-recorder dataframe, shows raw ARINC 717
frames, decodes them into engineering values, simulates frames, and imports
dataframe definitions from `.adb` files or from dataframe layout documents
in PDF form (born-digital or scanned).

The implementation specification the software follows is
`../ARINC717_READER_SYSTEM_DESIGN.md`. Section numbers quoted in these pages
(for example "spec §14") refer to that document.

## Contents

| Page | What it covers |
| --- | --- |
| [01 — Overview](01-overview.md) | What the software does, an ARINC 717 primer, the three data planes, the main workflow, package map |
| [02 — Installation and running](02-installation-and-running.md) | Requirements, environment setup, starting the application, running the tests, regenerating the examples |
| [03 — User guide](03-user-guide.md) | Every page of the GUI, dialog by dialog, plus step-by-step workflows |
| [04 — Data model and validation](04-data-model.md) | Canonical dataframe, frame and engineering models, parameter types, bit numbering, occurrences and segments, validation rules |
| [05 — Decoding and encoding](05-decoding-and-encoding.md) | The decoder pipeline, per-type interpretation, statuses and traces, the scenario encoder and closed-loop validation, worked examples |
| [06 — The ADB format](06-adb-format.md) | The `.adb` codec: settings record, parameter records, subframe selector, preservation of unknown fields, round trip |
| [07 — PDF import](07-pdf-import.md) | The dataframe-document importer: extraction of born-digital and scanned pages, OCR, normalization rules, conventions, review, publishing |
| [08 — File formats and storage](08-file-formats-and-storage.md) | Frame JSON files, the SQLite repository schema, where each kind of data lives |
| [09 — Architecture and development](09-architecture-and-development.md) | Layering rules, stores and services, event flow, spec traceability, testing strategy, extension points, roadmap |
| [10 — Troubleshooting](10-troubleshooting.md) | Error states, decode statuses, OCR and PDF problems, environment quirks |
| [11 — Scanned page processing](11-scanned-page-processing.md) | The image pipeline for scanned documents: scan detection, deskew, ruling-line grid detection, text layer vs OCR, cell assignment, second-pass recognition, tuning, limitations |

## Quick start

```bash
cd ~/projects/flight_data_reader_software
.venv/bin/arinc717-reader examples/demo_256wps.adb
```

Then open the **Simulator** tab, choose **Scenario**, type `-20` for
`PITCH ATT #1`, press **Apply Scenario**, and look at the **Frame View** and
**Parameters** tabs. The frame now contains the encoded word pattern, and the
decoder reads it back as −20.064 degrees: the closed loop that the whole
design is built around.

## Conventions used in these pages

- **Word** means one 12-bit ARINC 717 word; **WPS** means words per second,
  which is also the number of words in each subframe.
- Bit numbers run **12 (most significant) down to 1 (least significant)**.
- Subframes are numbered **1 to 4**; word addresses are **1-based**.
- Code paths are given relative to the package, for example
  `arinc717_reader/decoder/parameter_decoder.py`.
