# ARINC 717 Reader

Dataframe-driven decoding of ARINC 717 flight-recorder data, with
hardware-in-the-loop streaming, live graphs, session recording and PDF
dataframe import, in one desktop application.

## Introduction

Flight data recorders on transport aircraft receive their data from a flight
data acquisition unit as an ARINC 717 stream [1]: a continuous series of
12-bit words, grouped into subframes of one second (typically 64 to 1024
words per second) and frames of four subframes, each subframe opening with
one of four fixed synchronisation words [1][2]. The stream itself carries no
labels. Which bits of which word hold the pitch attitude, the airspeed or a
landing-gear discrete, how they are encoded and how they convert to
engineering units is defined by a *dataframe* document that is specific to
the aircraft and its installation, and that regulators require to be kept
current so the recording can be read out [3][4][5][6].

Reading a recording therefore needs two things: the raw frames and a
machine-readable dataframe. In practice the dataframe arrives as a vendor
file (`.adb`) or as a document, often a scanned PDF [7]. ARINC 717 Reader is
built around that problem. It loads or imports a dataframe, shows the raw
frames word by word, decodes every parameter into engineering values with a
complete trace from bits to value, and streams frames live from a
hardware-in-the-loop simulator so the whole path can be exercised without
an aircraft.

Three principles run through the code base:

- **Three data domains stay apart.** The dataframe (interpretation rules),
  the frame (raw words) and the engineering data (derived values) are
  separate models; only the decoder combines them, and the user interface
  never holds data of its own.
- **Explicit states, no silent fallbacks.** A decode problem is a status on
  the value, a service problem is a named error, an ambiguous cell in an
  imported document is a flagged review item. Nothing is guessed quietly.
- **Simulation first.** An in-process virtual device runs the same protocol
  as the STM32 firmware, so every feature works, and is tested, without
  hardware. The serial link is a simulation of a recorder bus, not an
  ARINC 717 electrical interface; real bus acquisition is future work.

The application is written in Python 3.12 with Qt for Python [13], PyMuPDF
[12], NumPy [14], pySerial [15] and, for scanned documents, ONNX Runtime
[11] running the PP-OCRv3 recognizer [8][9] through RapidOCR [10]. The
stream emulator firmware for the STM32F103 [18] is freestanding C.

## Features

**Dataframes**

- Import and export of `.adb` dataframe files with lossless preservation of
  fields whose meaning is unknown, so an imported file round-trips.
- A dataframe editor: metadata, parameters, occurrences and segments, with
  the validator's verdict previewed before a change is committed.
- Layered validation (structural, mapping, semantic) with overlaps,
  out-of-range words and inconsistent subframes reported per parameter.
- A SQLite repository schema for dataframe documents (API level).

**Frame inspection**

- Frame View: one row per word address, one column per subframe, in binary,
  octal, decimal or hexadecimal; word editing with immediate re-decode.
- Word Inspector: the full decode chain of any cell, for every parameter
  mapped to that word.
- Parameter Bits panel: the words behind one decoded sample stacked one row
  per segment, with the parameter's bits highlighted, and those words
  outlined in the grid.

**Decoding**

- Signed and unsigned binary, BCD and discrete parameters with linear
  conversion, multi-segment assembly and per-occurrence, per-subframe
  samples.
- Every value carries a trace: segments, extracted bits, assembled value,
  decoded decimal, conversion, status and message.
- A closed-loop encoder: engineering values are encoded into frames and
  decoded back within quantisation tolerance, which is how the simulator
  and the tests check the decoder.

**Live streaming (hardware in the loop)**

- SIM-A717 v1, a documented serial protocol: 12-bit words in 16-bit
  containers with self-recovering alignment, an optional framed mode with
  CRC-32, and an ASCII command channel.
- An STM32F103 firmware that emits the stream from a hardware timer, and an
  in-process virtual device that behaves identically.
- Synchroniser, subframe assembler and a rolling Frame View with explicit
  received, pending, invalid, missing and late states.
- Per-subframe decoding into timestamped samples, so each parameter is
  published at its own recording rate; nothing is interpolated.
- Engineering-coherent signal generators (random walk, sine, ramp, step,
  scripted) uploaded to the device through the dataframe.
- Fault injection (dropped or corrupted words, dropped or reordered
  subframes, wrong rate, pauses, disconnects) with diagnostics counters and
  automatic reconnection.

**Graphs, recording and replay**

- Time-series plots with windows from 10 seconds to 10 minutes, statistics,
  same-unit overlays and a pause that does not stop acquisition.
- Session files (`.a717session`, JSON Lines) that record every subframe,
  sample and event, and replay through the same decoder and graph pipeline.

**PDF dataframe import**

- Born-digital pages through PyMuPDF's table finder; scanned pages through
  deskew, ruling-line grid detection and either the embedded text layer or
  OCR.
- OCR reads each table cell on its own crop with the bundled English
  PP-OCRv3 recognizer; on the synthetic benchmark 99 % of cells are read
  exactly, and on the reference document's image-only pages the share of
  rows approved automatically rose from a third to four fifths.
- Normalisation by explicit rules that record every interpretation, a
  review dialog with per-row provenance and rendered source regions, and
  publishing of approved rows only.

**Application**

- Ten colour themes, an in-app user guide (F1), and a monitoring-friendly
  interface whose selections and column widths hold still while values
  update.
- Standalone builds with PyInstaller and a Windows MSI installer.
- 219 automated tests, including offscreen tests that drive the real
  widgets, and an OCR benchmark against known ground truth.

## Mockups

All screenshots show the FDS81 dataframe imported from the reference PDF
[7] while a live stream from the virtual device is running.

![Frame View](img/mockups/frame_view.png)

*Frame View during a live stream: subframes 1 and 2 of frame 36 received,
3 and 4 pending. The Parameter Bits panel on the right shows the word behind
the 28VDC Power Input sample with its twelve bits, and the Word Inspector
lists the two discretes mapped to the clicked word.*

![Parameters](img/mockups/parameters.png)

*Parameters: one row per decoded sample, refreshed in place as subframes
arrive; the selected row's decode trace is shown underneath and "Show in
Frame View" opens its words in the Frame View.*

![Graphs](img/mockups/graphs.png)

*Graphs: a 30-second window of the AOAR parameter with its statistics and
the list of same-unit parameters that can be overlaid.*

![Dataframe](img/mockups/dataframe.png)

*Dataframe: metadata, the 311 imported parameters, the selected parameter's
mapping and provenance down to the raw PDF cells, and the validation
issues.*

![Hardware](img/mockups/hardware.png)

*Hardware: connected to the virtual STM32 device and streaming; stream
progress, the simulated-signal table, diagnostics counters, fault injection
and the event log with the handshake replies.*

![Import](img/mockups/import.png)

*Import: ADB and PDF import, the review queue after publishing the FDS81
document, and the validation results of the loaded dataframe.*

![PDF review dialog](img/mockups/pdf_import_dialogue.png)

*The PDF review dialog on the 21-page scanned document: 328 extracted rows,
311 approved automatically, 13 held for review with the reason, the
verbatim cells of the selected row, and the document conventions detected
from the evidence.*

## Getting started

```bash
python3 -m venv .venv
.venv/bin/pip install -e .[dev]
.venv/bin/arinc717-reader examples/demo_256wps.adb   # or: .venv/bin/python -m arinc717_reader
.venv/bin/python -m pytest                            # the test suite
```

Press **▶ Start stream** to stream from the virtual device, or connect an
STM32F103 running the firmware in `firmware/stm32f103_sim_a717/`. The Help
tab (F1) is the user guide; the full documentation (installation, user
guide, data model, decoding, file formats, PDF import, scanned-page
processing, the SIM-A717 protocol, the live path, architecture,
troubleshooting) is in [`docs/`](docs/README.md), and standalone builds are
described in [`packaging/README.md`](packaging/README.md).

Package layout: `arinc717_reader/domain` (models), `decoder` and `encoder`,
`dataframe` (ADB codec, validator, editor, repository, `pdf_importer`),
`sources` (frame and stream sources, `serial/` for SIM-A717), `streaming`
(samples, time series, signal generators), `recording`, `state` and
`services`, `ui` (PySide6 pages), plus `firmware/`, `packaging/`, `tools/`
(OCR benchmark), `tests/`, `docs/` and `img/`.

## References

1. Aeronautical Radio, Inc. *ARINC Characteristic 717: Flight Data
   Acquisition and Recording System*. SAE ITC / ARINC Industry Activities.
2. Aeronautical Radio, Inc. *ARINC Characteristic 573: Aircraft Integrated
   Data System (AIDS) Mark 2*. The predecessor standard from which ARINC 717
   inherits the 12-bit word, the one-second subframe and the four-subframe
   frame with rotating synchronisation words.
3. EUROCAE. *ED-112A: Minimum Operational Performance Specification for
   Crash Protected Airborne Recorder Systems*. 2013.
4. International Civil Aviation Organization. *Annex 6 to the Convention on
   International Civil Aviation, Operation of Aircraft, Part I*, flight
   recorder provisions (including the requirement to maintain documentation
   for converting recorded data to engineering units).
5. Federal Aviation Administration. *Advisory Circular AC 20-141B:
   Airworthiness and Operational Approval of Digital Flight Data Recorder
   Systems*. 2010.
6. United States Code of Federal Regulations. *14 CFR Part 121, §121.344 and
   Appendix M: Digital flight data recorders for transport category
   airplanes*.
7. Flight Data Systems. *CN235-220 ARINC 717 Dataframe Layout Document,
   Mapping (DFDAU FDS81)*. Scanned document, 21 pages, used as the reference
   input for the PDF importer in this project; not redistributed with the
   repository.
8. Du, Y., Li, C., Guo, R., Yin, X., Liu, W., Zhou, J., Bai, Y., Yu, Z.,
   Yang, Y., Dang, Q., Wang, H. *PP-OCR: A Practical Ultra Lightweight OCR
   System*. arXiv:2009.09941, 2020.
9. Li, C., Liu, W., Guo, R., Yin, X., Jiang, K., Du, Y., Du, Y., Zhu, L.,
   Lai, B., Hu, X., Yu, D., Ma, Y. *PP-OCRv3: More Attempts for the
   Improvement of Ultra Lightweight OCR System*. arXiv:2206.03001, 2022.
10. RapidAI. *RapidOCR: a cross-platform OCR toolkit running PaddleOCR
    models on ONNX Runtime* (`rapidocr-onnxruntime`).
    https://github.com/RapidAI/RapidOCR
11. Microsoft. *ONNX Runtime: cross-platform, high performance ML
    inferencing and training accelerator*. https://onnxruntime.ai
12. Artifex Software. *PyMuPDF: Python bindings for MuPDF*.
    https://pymupdf.readthedocs.io
13. The Qt Company. *Qt for Python (PySide6)*.
    https://doc.qt.io/qtforpython-6/
14. Harris, C. R., Millman, K. J., van der Walt, S. J., et al. *Array
    programming with NumPy*. Nature 585, 357–362 (2020).
    https://doi.org/10.1038/s41586-020-2649-2
15. Liechti, C. *pySerial*. https://pyserial.readthedocs.io
16. PyInstaller Development Team. *PyInstaller*. https://pyinstaller.org
17. .NET Foundation / FireGiant. *WiX Toolset*. https://wixtoolset.org
18. STMicroelectronics. *RM0008 Reference manual: STM32F101xx, STM32F102xx,
    STM32F103xx, STM32F105xx and STM32F107xx advanced Arm-based 32-bit
    MCUs*, and *STM32F103x8, STM32F103xB datasheet*.
19. Future Technology Devices International. *FT232R USB UART IC
    datasheet*.
20. Python Software Foundation. *Python 3.12 documentation*.
    https://docs.python.org/3.12/ ; pytest developers. *pytest*.
    https://docs.pytest.org

The implementation follows the project's internal design specification
(*ARINC 717 Reader System Design*, revision 2), which is not part of the
repository. The bundled OCR recognizer is the PP-OCRv3 English model of
PaddleOCR [8][9], converted to ONNX by the RapidOCR project [10]; both are
licensed under the Apache License 2.0 (see
`arinc717_reader/dataframe/pdf_importer/models/README.md`).
