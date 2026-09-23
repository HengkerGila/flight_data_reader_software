# 09 — Architecture and development

## Layering rules

The code base follows a few rules without exception; new code should too.

1. **The GUI never holds truth.** Widgets render stores and call services.
   No decoding, encoding, parsing or validation happens in `ui/`.
2. **The core is Qt-free.** Everything under `domain/`, `decoder/`,
   `encoder/`, `dataframe/`, `sources/`, `state/` and `services/` imports no
   Qt and runs headless, which is how most tests work.
3. **Three domains stay apart.** Dataframe (interpretation), frame (raw
   words) and engineering data (derived values) are separate models; only
   the decoder combines them.
4. **Explicit states, no silent fallbacks.** Decode problems are statuses,
   service problems are `ServiceError(state, message)`, import
   interpretations are issues. Unknown input is preserved, not repaired.
5. **Extraction stays away from decoding.** Nothing in the runtime decoder
   imports the PDF importer or OCR.

## Stores, services and events

```
                 ┌──────────────────────────────────── AppContext ─────────────────────────────────────┐
                 │                                                                                      │
   DataframeService ──► DataframeStore ──┐                                                              │
                                          ├──► DecodingService ──► EngineeringStore ──► Parameters page  │
   FrameService ──────► FrameStore ───────┘   (static frames)  ▲                                        │
   SimulationService ─► FrameStore (scenario; API and tests)   │ ParameterDecoder                       │
                                                               │                                        │
   SerialService ─► source thread ──queue──► pump (50 ms) ─────┼─► FrameStore (live, per subframe) ─► Frame View
        │  (serial / virtual / replay source)                  │   StreamStore ─────────────────────► Hardware page, status bar
        │ arrivals                                             │
        └─► StreamingService ── decode_subframe ───────────────┘─► ParameterSampleBus ─┬─► EngineeringStore
                                                                                       ├─► TimeSeriesStore ─► Graphs page
   RecordingService ◄── arrivals, events, samples ─────────────────────────────────────┴─► SessionRecorder ─► .a717session
        └─► RecordedSessionSource ─► SerialService.attach_source   (replay re-enters the same path)
                 └──────────────────────────────────────────────────────────────────────────────────────┘
```

- **Stores** (`state/`, plus `streaming/timeseries_store.py`) are
  `Observable`: subscribers are plain callables receiving an event dict.
  `DataframeStore` emits `dataframe`, `validation` and `dirty` events and
  keeps the current issues and the unexported flag; `FrameStore` emits
  `frame` (with a `live` flag) and per-cell `word` events, remembers the
  source name and, while live, the per-subframe slot states and invalid
  cells; `EngineeringStore` emits `engineering`; `StreamStore` emits
  `stream` events with a `field` (`connection`, `progress`, `diagnostics`,
  `event`, `replay`, `config`) and holds connection state, device
  configuration, progress through the current frame, the diagnostics
  snapshot and the last 200 stream events; `TimeSeriesStore` emits
  `timeseries` events with a `reason` (`append`, `configure`, `clear`).
- **Services** (`services/`) are the only writers. `DataframeService`
  loads, creates, edits, validates, exports, imports PDFs and publishes
  import sessions; every change goes through `set_dataframe`, which
  re-validates and notifies. `FrameService` and `SimulationService` write
  static frames (the latter has no page since the Simulator tab was
  removed; the tests and the selftest use it). `DecodingService` subscribes to the dataframe and frame
  stores and keeps the engineering store consistent for static frames; it
  stands back while the frame is live. `SerialService` owns the streaming
  source (a `SerialHardwareSource` over pyserial or the virtual device, or an
  attached replay source), runs the SIM-A717 dialogue, uploads engineering
  frames, and in `pump()` drains the source's event queue onto `FrameStore`
  and `StreamStore` and calls its arrival and event listeners.
  `StreamingService` is such a listener: it decodes every received subframe,
  publishes `ParameterSample` batches on the `ParameterSampleBus` and
  rewrites the engineering store with the latest samples. `RecordingService`
  listens to arrivals, events and the bus to write session files, and
  attaches a `RecordedSessionSource` for replay.
- **Threads.** Streaming sources and the virtual device run daemon threads
  that only touch a queue and locked diagnostics; everything from `pump()`
  on runs on the GUI thread (a 50 ms `QTimer` in `MainWindow`; the tests call
  `ctx.pump()` themselves). See [13](13-live-streaming-graphs-recording.md#threads-and-the-pump).
- **`app.build_context()`** wires it all and returns an `AppContext` the GUI
  and the tests share; `ctx.shutdown()` stops recording, replay and the
  connection.

The GUI pages subscribe to the stores and re-render on events; dialogs call
services and show `ServiceError` messages inline. The Graphs page is the one
page driven by its own timer rather than by events: it redraws at most every
100 ms, and only when the time-series store reported new data.

## Spec traceability

| Spec section | Implementation |
| --- | --- |
| §6 canonical models | `domain/dataframe.py`, `domain/parameter.py` |
| §7 canonical frame | `domain/frame.py` |
| §8–§9 Frame View, inspector | `ui/frame_view/`, `ui/representation.py` |
| §10–§18 decoder | `decoder/` |
| §19–§20 engineering data and view | `domain/engineering.py`, `ui/parameter_view/` |
| §21–§23 sources | `sources/` |
| §24–§26 scenario, encoder, closed loop | `encoder/`, `services/simulation_service.py`; exercised by `tests/test_scenario_roundtrip.py`, the GUI smoke test and `--selftest` (no page, see §45) |
| §27–§32 dataframe subsystem, PDF import, review | `dataframe/pdf_importer/`, `ui/importer/` |
| §31 validator | `dataframe/validator/` |
| §33–§37 ADB codec, round trip | `dataframe/adb_codec/`, `dataframe/compare.py` |
| §38–§39 repository | `dataframe/repository/` |
| §40 application state | `state/`, `services/`, `app.py` |
| §41–§46 UI structure | `ui/main_window.py` and the pages: Frame View, Parameters, Graphs, Dataframe, Hardware, Import, plus a Help tab with the in-app user guide (`ui/help/`, not in the spec) and a stream Start / Pause toolbar (`ui/stream_controls.py`) |
| §45 Simulator page | Removed at the user's request (2026-09-23). Random and blank frames come from the Frame View buttons; scenario encoding remains in `encoder/` and `services/simulation_service.py`, exercised by tests |
| §44 dataframe editing | `dataframe/editor.py`, `ui/dataframe_view/` |
| §47–§48 error handling, logging | `ServiceError` states, decode statuses, stream event kinds, `logging` events |
| §52–§53 MVP and acceptance | `tests/` |
| §56–§57 hardware | `sources/hardware_source.py` (stub for the real ABUS 717 source, spec v2 Phase 14) |
| v2 §3A runtime levels | `sources/serial/`, `state/`, `streaming/`, `recording/` (see [13](13-live-streaming-graphs-recording.md)) |
| v2 §26A–§26F HIL simulator, timing, UART, protocol, source chain | `firmware/stm32f103_sim_a717/`, `sources/serial/transport.py`, `sim_a717_protocol.py`, `stream_parser.py`, `synchronizer.py`, `subframe_assembler.py`, `serial_source.py`, `virtual_device.py` |
| v2 §26G, §26K–§26M live decoding, samples, bus, frequency | `decoder/parameter_decoder.py` (`decode_subframe`), `services/streaming_service.py`, `streaming/parameter_sample.py`, `streaming/sample_bus.py` |
| v2 §26H signal modes | `streaming/signal_generator.py`, `encoder/parameter_encoder.py` (`only_subframes`) |
| v2 §26I–§26J commands, fault injection | `services/serial_service.py`, `sources/serial/sim_a717_protocol.py`, firmware `command_receiver.c`, `stream_scheduler.c` |
| v2 §26N time series | `streaming/timeseries_store.py` |
| v2 §26O–§26Q, §46A Graph View | `ui/graph_view/` |
| v2 §26R, §46C recording and replay | `recording/`, `sources/replay_source.py`, `services/recording_service.py`; controls in the File menu of `ui/main_window.py` plus the status bar (the toolbar carries stream Start / Pause instead, which also pause and resume a replay) |
| v2 §26S, §46B Hardware page, diagnostics | `state/stream_store.py`, `sources/serial/diagnostics.py`, `ui/hardware/` |
| v2 §26T progressive Frame View | `sources/serial/subframe_assembler.py` (slot states; slots not yet received carry the previous frame's words, `blank_subframes` names the ones that never held data), `state/frame_store.py`, `ui/frame_view/frame_table_model.py` (previous values shown, `----` only for blank slots) |
| v2 §53.7–§53.10 acceptance | `tests/test_sim_a717_stream.py`, `test_subframe_assembler.py`, `test_virtual_device.py`, `test_parameter_samples.py`, `test_timeseries_store.py`, `test_record_replay.py`, `test_ui_stream_smoke.py` |

## Testing strategy

`tests/` holds more than 200 tests that run in under a minute:

| Area | Tests |
| --- | --- |
| Bits, signed, BCD, discrete, conversion, frame | Unit tests against the spec's oracle values. |
| Decoder | Demo dataframe and frame fixtures; every status path. |
| Scenario round trip | Encode → decode within quantization tolerance; encodability rules. |
| ADB | Parsing of an embedded sample with quoted fields and unknown columns; semantic round trip; error messages. |
| Validator, editor | Rule coverage; editing helpers; preservation of raw fields through edits. |
| Repository | Save/load round trip in memory and on disk. |
| PDF normalization and review | Pure Python on hand-written raw rows: every rule, the state machine, publishing, re-normalization. |
| PDF extraction | Synthetic documents generated by `synth.py`: born-digital, multi-page with and without repeated headers, scanned with a text layer and skew, scanned image-only through OCR, prose without a table. |
| Real document | The CN235 document's text-layer pages (fast) and the full OCR run (`ARINC717_OCR_TESTS=1`). |
| SIM-A717 protocol | Word container, alignment recovery from a mid-stream connect, partial feeds, framed packets and CRC, command lines and replies, synchronizer lock / loss / re-lock / wrong-WPS detection. |
| Subframe assembler | Progressive slot states, missing and late subframes, frames closed early, invalid lengths and words, framed-mode frame ids. |
| Virtual device | End to end through `build_context()`: command channel, word timing, connect / stream / decode / stop, fault detection, disconnect and automatic reconnect, framed mode with a bad CRC. |
| Live samples | Per-subframe decode equals whole-frame decode; frequency by mapping; timestamps; invalid subframes yield nothing; discretes as 0/1. |
| Signal generators | Every mode within bounds, shapes, scripts, discrete states, per-subframe encoding. |
| Time series | Count and duration bounds, windows and statistics, non-numeric samples, reconfiguration. |
| Record and replay | File round trip, truncated files, a recorded session replayed through a fresh context yields identical series, WPS mismatch refused. |
| GUI | Offscreen smoke tests in a subprocess drive the real widgets: Frame View, inspector, edit dialog, the scenario service, dataframe editor dialogs, the PDF review dialog including re-normalization and bulk approval; and, for the live path, the stream toolbar (start from disconnected, pause, resume), the Hardware page against the virtual device, the rolling Frame View, the Graphs page with an overlay and a pause, fault injection, File-menu recording and replay, and the Help tab. |

The GUI tests show how to drive dialogs without a display: build a context
and a `MainWindow`, then call the same methods the widgets call, avoiding
native file dialogs by using the underlying services. Streaming tests run
the virtual device far faster than real time and poll `ctx.pump()` until a
condition holds instead of sleeping fixed times.

## Extension points

**A new static frame source** implements `sources.base.FrameSource` (`name`
and `next_frame() -> Arinc717Frame`) and hands frames to
`FrameStore.set_frame`.

**A new streaming source** (the real ABUS 717 hardware of spec v2 Phase 14,
another emulator, a network feed) extends `sources.stream_base.StreamSourceBase`:
run its thread in `_run()`, turn the device's bytes into subframes however
its transport requires, and hand each one to
`deliver_subframe(subframe, words, timestamp, valid, frame_id)`. The shared
assembler, the events, the stores, live decoding, graphs and recording then
work unchanged; `SerialService.attach_source` (which replay uses) shows how
a non-serial source is plugged in. A new byte transport for the existing
SIM-A717 chain only needs the `Transport` protocol of
`sources/serial/transport.py` (`read`, `write`, `close`, `is_open`). The
device-specific parsing of spec §56 belongs entirely inside the source;
nothing downstream may learn where the words came from.

**A new parameter type** needs: a canonical constant and synonyms in
`domain/parameter.py`, an interpretation branch in
`decoder/parameter_decoder.py` (and an encoding module under
`decoder/encoding/`), the inverse in `encoder/parameter_encoder.py`,
validator rules if the type has constraints, and tests with oracle values.

**A new document convention** for the PDF importer is a field on
`ImportProfile`, a rule in `normalize.py` that raises an issue when applied
heuristically and stays quiet when declared, a control in the conventions
box of the review dialog, and — when it can be recognised from evidence —
a detector in `effective_profile()`.

**Header synonyms** live in `extract.HEADER_SYNONYMS`; add the exact
normalised text a document uses rather than loosening the fuzzy threshold.

## Conventions

- Python 3.12, type hints, dataclasses for models, `from __future__ import
  annotations`.
- Bit numbers 12..1, subframes 1..4, word addresses 1..WPS everywhere at
  the API surface; internal lists are 0-based.
- Module docstrings cite the spec section they implement and state the
  rule they enforce.
- Logging uses `event=name key=value` messages at INFO for user-level
  operations and DEBUG for per-decode details; no per-word INFO logs.
- Files are UTF-8; ADB output is CRLF CSV.

## Roadmap and open ends

| Item | Status |
| --- | --- |
| Phases 9–12: HIL stream, live decoding, graphs, recording (spec v2) | Implemented with the STM32F103 + FTDI emulator and its in-process virtual twin; the firmware compiles and the whole path is tested against the virtual device. A run against a physical board over an FTDI cable is not recorded in this repository yet. |
| Phase 14: real ABUS 717 acquisition | Blocked on the transport protocol or SDK; `sources/hardware_source.py` stays a stub. It must be a streaming source behind the subframe assembler (see Extension points). |
| UART framing | Only the baud rate is selectable; data bits, parity and stop bits are fixed at 8N1 on both sides. |
| Firmware WPS ceiling | 512 (two frame buffers in 20 KB SRAM); the PC side and the virtual device go to 4096. |
| Simulator page (spec §45) | Removed from the GUI at the user's request; `SimulationService` and the scenario encoder stay as core code for the tests and the selftest. |
| Enumerated multi-bit discretes | Flagged at import, states kept in notes; the model needs a state table and the decoder a lookup. |
| Superframes | Recognised and rejected. |
| Repository in the GUI | Save/load of dataframe documents is API-only. |
| ADB parameter record layout | Provisional until a real Aering/AFDA file is verified. |
| PDF importer | Column conventions verified against one real document; other vendors will need synonyms and possibly conventions. |
| OCR | CPU-only RapidOCR; a page takes about 20 s. |
