# 13 — Live streaming, graphs and recording

Spec v2 adds a live path to the application: a continuous word stream from
the hardware-in-the-loop simulator is synchronised into subframes, decoded
as each subframe arrives, plotted, and recorded for replay. This page
explains how that path is built and why. The user-facing controls are in
[03 — User guide](03-user-guide.md#graphs); the wire protocol is in
[12 — The SIM-A717 v1 protocol](12-sim-a717-protocol.md).

## Four levels of data

The runtime keeps four levels apart (spec v2 §3A), each with its own models
and modules:

| Level | Holds | Modules |
| --- | --- | --- |
| 1 Transport / word stream | UART bytes, 12-bit words | `sources/serial/transport.py`, `sim_a717_protocol.py`, `stream_parser.py`, `synchronizer.py` |
| 2 ARINC frame domain | Subframes, the progressive canonical frame | `sources/serial/subframe_assembler.py`, `state/frame_store.py`, `state/stream_store.py` |
| 3 Engineering sample domain | Timestamped decoded samples | `services/streaming_service.py`, `streaming/parameter_sample.py`, `streaming/sample_bus.py` |
| 4 Time series / visualisation | Bounded history, graphs, sessions | `streaming/timeseries_store.py`, `ui/graph_view/`, `recording/`, `sources/replay_source.py` |

```
 STM32 + FTDI ──┐
 VIRTUAL device ┼─► transport ─► SIM-A717 parser ─► synchronizer ─► subframe assembler ─┬─► FrameStore ─► Frame View
 session file ──┘  (replay skips the first three: its subframes are already cut)        │
                                                                                          └─► StreamingService
                                                                                                │  decode_subframe
                                                                                                ▼
                                                                          ParameterSampleBus ──┬─► EngineeringStore ─► Parameters
                                                                                               ├─► TimeSeriesStore ─► Graphs
                                                                                               └─► SessionRecorder ─► .a717session
```

After the assembler nothing knows where the words came from (spec v2 §26F,
§26R): the firmware, the virtual device and a replayed session produce the
same `SubframeArrival` objects and travel the same decoder and graph path.

## Threads and the pump

Acquisition must never block on the GUI, and plotting must never block
acquisition (spec v2 §26Q, §54).

- **Source thread.** Every streaming source extends `StreamSourceBase` and
  runs its own daemon thread. `SerialHardwareSource` reads the transport in
  50 ms slices, feeds the parser, the synchronizer and the assembler, and
  pushes `StreamEvent` objects onto a thread-safe queue. `RecordedSessionSource`
  does the same from a file, pacing itself on the recorded timestamps.
  Diagnostics counters are updated under a lock and read as immutable
  snapshots.
- **Virtual device thread.** `VirtualSimDevice` runs its stepping engine on
  a second thread when the port is `VIRTUAL`.
- **GUI thread.** `MainWindow` runs a 50 ms `QTimer` that calls
  `AppContext.pump()` → `SerialService.pump()`. The pump drains the event
  queue, updates `FrameStore` and `StreamStore`, hands arrivals to the
  listeners (live decoding, recording), refreshes the progress and
  diagnostics snapshot, and drives the automatic reconnect. Because
  everything downstream of the queue runs on the GUI thread, the stores, the
  sample bus and the time-series store need no locking. The tests call
  `ctx.pump()` in a loop instead of running Qt.

The event kinds double as the explicit stream states of spec v2 §47:

| Event | Emitted when |
| --- | --- |
| `STREAM_STARTED`, `STREAM_STOPPED` | `START` / `STOP` sent (or a replay started) |
| `STREAM_SYNC_LOCKED`, `STREAM_SYNC_LOST` | The synchronizer locks / sees a wrong sync word |
| `STREAM_RATE_MISMATCH` | Sync words repeat at another standard WPS spacing, or a framed packet declares another WPS |
| `SUBFRAME` | A subframe was assembled (RECEIVED, LATE) |
| `SUBFRAME_INCOMPLETE` | A subframe was assembled but is INVALID |
| `FRAME_INCOMPLETE` | A frame was closed with MISSING slots because the next frame began |
| `FRAME` | A frame completed |
| `INVALID_SIM_PROTOCOL` | Bad framed packets |
| `SERIAL_DISCONNECTED` | The transport failed |
| `REPLY` | A `+OK` / `+ERR` line arrived |
| `REPLAY_FINISHED`, `REPLAY_ERROR` | End of a session file, or a malformed record |

The serial service adds its own records to the stream log: `CONNECTED`
(connection state), `FAULT_INJECTED`, `DEVICE_RESET`, `RECONNECTED`,
`REPLAY_STARTED`, `ENCODE_ERROR` (an engineering signal the mapping cannot
hold), and any `ServiceError` raised inside the pump. The `StreamStore`
keeps the last 200.

## From bytes to subframes

**Parser** (`StreamParser`). Incremental: it takes whatever the transport
returned and yields every complete word (continuous mode) or packet (framed
mode), keeping the remainder. Alignment recovery and CRC checking are
described in [12](12-sim-a717-protocol.md).

**Synchronizer** (`StreamSynchronizer`). Word stream → subframes. It searches
for a sync word followed by the *next* sync word exactly `WPS` words later,
locks there (`STREAM_SYNC_LOCKED … locked at SFn`), and then cuts one
subframe per `WPS` words while verifying word 1 of each. A mismatch is not
repaired: the subframe is delivered as INVALID with `sync_valid=False`,
`STREAM_SYNC_LOST` is emitted, and the search restarts from the very next
word so nothing is skipped. While searching it also looks for sync pairs at
the other standard spacings (64, 128, 256, 512, 1024) and reports
`STREAM_RATE_MISMATCH` with the spacing it found, so a device streaming at
the wrong WPS is a diagnosis instead of an endless "no lock". The
Hardware page shows the words received so far in the current subframe from
the synchronizer's buffer.

**Assembler** (`SubframeAssembler`). Subframes → a progressive canonical
frame (spec v2 §26T). It owns the frame under construction and gives every
slot an explicit state:

| State | Meaning | Frame View |
| --- | --- | --- |
| `PENDING` | Not received yet in this frame | previous frame's words, normal cells (`----` only while the slot has never held data) |
| `RECEIVED` | Received in order and valid | normal |
| `INVALID` | Received, but the sync word, length or a word value was wrong | red; individual out-of-range words carry a stronger red |
| `MISSING` | A later subframe arrived without it | previous frame's words on orange |
| `LATE` | Arrived out of order after being marked missing | blue |

Every subframe taken produces a `SubframeArrival` carrying the words, the
state, the timestamp, a snapshot of the partial frame and the four slot
states. Slots not yet received in the current frame carry the **previous
frame's words**, so a live view shows the last known value of every word and
one column changes per second; only a slot that has never held any data is
zero-filled, and those are listed in `blank_subframes` (the Frame View shows
`----` for them). `reset(frame_index)` (a stop / start) starts a fresh frame
but keeps the last words as the baseline; a new source object starts blank. A subframe lower than the expected one
closes the current frame with its pending slots marked MISSING (an
`FRAME_INCOMPLETE` arrival with `subframe = 0`) and starts the next; in
framed mode the packet's frame id drives the frame index. Words outside
0..4095 are masked to 12 bits and flagged as invalid cells rather than
dropped.

`SerialService.pump()` writes each arrival's snapshot into `FrameStore`
together with the slot states, which puts the store into *live* mode:
headers read `SF1 · received`, editing a word is refused
(`INVALID_WORD: the frame is being received live; stop the stream to edit`),
and the whole-frame `DecodingService` stays out of the way because values are
published per subframe instead. Stopping the stream (or losing the
connection) copies the last frame back as a static frame, every slot holding
its last known words, so it can be inspected and edited like any other.

## Live decoding and samples

![Live samples on the Parameters page](../img/mockups/parameters.png)

*Live samples on the Parameters page: four rows per parameter mapped to all four subframes, refreshed in place as each subframe arrives.*


`StreamingService.on_arrival` decodes each RECEIVED or LATE subframe as soon
as it arrives (spec v2 §26G); INVALID and MISSING slots yield no samples, and
a dataframe whose WPS differs from the frame is ignored. It calls
`ParameterDecoder.decode_subframe`, which decodes only the occurrences
recorded in that subframe (an occurrence's segments always share one
subframe set, so a subframe is self-contained; mapping errors are reported
once, with subframe 1, so a broken parameter still shows up).

Each `EngineeringValue` becomes a `ParameterSample` (spec v2 §26K):
parameter id and name, timestamp, frame index, subframe, occurrence, raw
value, decoded decimal, engineering value, unit, status.

**Timestamps.** A subframe spans one second at any WPS (spec v2 §26B), and
the arrival timestamp is taken when its last word is in, so a sample recorded
at word *w* of that subframe is stamped

```
t = subframe_end − 1 s + (w − 1) / WPS
```

with *w* the lowest word address of the occurrence's segments. Samples
therefore carry recording time, not GUI time.

**Frequency.** Parameter frequency follows the mapping and nothing else
(spec v2 §26M): a parameter mapped to all four subframes yields four samples
per frame (1 Hz), one mapped to subframes 2 and 4 yields two (0.5 Hz), one
mapped to subframe 2 only yields one (0.25 Hz). No sample is ever
interpolated or repeated to smooth a graph (spec v2 §54).

**Fan-out.** The samples of one subframe are published as one batch on the
`ParameterSampleBus` (spec v2 §26L). Subscribers: `TimeSeriesStore.append`,
the session recorder, and, through the streaming service itself, the
`EngineeringStore`, which is rewritten with the latest value per (parameter,
occurrence, subframe) so the Parameters page shows the live state of the
whole frame.

## Bounded history: `TimeSeriesStore`

One ring buffer per parameter id (spec v2 §26N): a `deque` bounded by
`max_samples` (default 10,000) and trimmed to `max_seconds` (default 600 s)
behind the newest sample; when full, the oldest sample goes first. Only
numeric samples are stored: engineering values, or the decoded decimal when
the engineering value is a state label, which is how discretes plot as 0/1.
A non-numeric or non-finite sample still updates the series' *latest*
sample, so the Graphs page can show a status such as `INVALID_MAPPING`
without plotting it. `window(id, seconds, now)` returns the last *seconds*
of (times, values); `stats` derives current, min, max, average and count
over the same window; `configure` changes the bounds at run time; `clear`
drops one series or all. The store is `Observable` and emits `timeseries`
events with the reason (`append`, `configure`, `clear`).

The store is the *only* owner of history (spec v2 §54): the plot widget
draws whatever it is handed and keeps nothing.

## The Graphs page

![Graphs page](../img/mockups/graphs.png)

*The Graphs page reading a 30-second window from the TimeSeriesStore.*


`GraphsPage` (`ui/graph_view/graphs_page.py`) consumes the time-series store
and never decodes (spec v2 §46A). A 100 ms timer redraws only when the store
reported new data since the last redraw (a dirty flag), never per sample
(spec v2 §26Q). Each refresh reads the selected window for the selected
parameter and the checked overlays, computes the statistics, and hands
`PlotSeries` lists to `TimeSeriesPlot`, a QPainter widget with auto-ranging
y axis, an x axis in seconds before the newest sample, nice tick steps, a
legend and the unit.

Windows: 10 s, 30 s, 1 min, 5 min, 10 min, all buffered. **Pause** freezes
the view's end time and stops refreshing; samples keep flowing into the store
(the legend says so), so resuming shows what happened meanwhile (spec v2
§26O). **Clear history** empties the store. Overlays are offered only for
parameters with the same unit as the selected one, so one y axis never mixes
units (spec v2 §26P); an overlaid parameter is drawn in its own colour with
the legend.

The parameter list is every dataframe parameter whose type is not `unknown`,
plus any series the store already holds (after a replay with a different
dataframe, for instance).

## Engineering signals: the upload cycle

Random raw words make meaningless engineering values, so by default the PC
generates the stream content (spec v2 §26H "Engineering Random", §26I: the
PC is the scenario authority, the device the timing authority):

1. `ScenarioSignals` builds one `SignalGenerator` per *encodable* parameter
   of the dataframe (`is_encodable`: a known type with a mapping and, for
   analog and BCD types, a non-zero resolution).
   `default_config` gives each the **Random Walk** mode (2 % of the span per
   second) over its declared minimum/maximum *clipped to what the mapping can
   encode* (`representable_range`: the field's raw range pushed through the
   linear conversion; a declared range is often wider than the field, the
   demo's `FLAP POS` declares −2..45 deg but its field holds −1.6..23.8).
   Without declared limits the representable range is used. Discretes
   default to a **Step** between their two states every 5 s.
2. Modes (spec v2 §26H): Fixed, Uniform Random, Random Walk, Sine, Ramp,
   Step, Scripted. `low`/`high` bound every mode; `value` is the Fixed value
   and the Random Walk / Step start; `period` is the Sine period, the Ramp
   duration and the Step interval; `script` is a list of (time, value)
   points interpolated linearly, held at the ends.
3. Simulated time is frame-based: subframe *k* of frame *n* covers
   `4n + (k − 1)` seconds. `SerialService.build_engineering_frame(n)` asks
   every generator for its value at that time and encodes it with the normal
   `ParameterEncoder`, restricted to that one subframe (`only_subframes`),
   so each subframe carries its own sample and the decoded stream moves
   within a frame.
4. The frame goes to the device as `LOAD_SF` × 4 + `COMMIT`: frame 0 during
   connect and again at *Start stream*; frame *n + 1* whenever subframe 1 of
   frame *n* arrives. The device swaps buffers at the frame boundary.

A value the encoder refuses (a `Low`/`High` edited beyond the mapping, for
instance) leaves that parameter's words untouched in the uploaded frame and
is reported once per parameter per stream: an `ENCODE_ERROR` line in the
stream event log ("… signal range outside the mapping; adjust Low/High") and
a warning in the application log. The device's own modes (WALK, RANDOM,
FIXED) remain available on the Hardware page for parser and stress testing.

## Recording

`RecordingService.start_recording(path)` needs a running source (stream or
replay) and writes a `.a717session` file through `SessionRecorder`, which
listens to the serial service's arrivals and events and to the sample bus.
The format is JSON Lines so the recorder appends without holding the session
in memory and a file cut short by a crash still replays up to its last
complete line.

**Line 1, the header** (`SessionHeader`):

```json
{"format": "arinc717-session", "version": 1, "created": 1790000000.0,
 "dataframe_name": "DEMO_256WPS", "dataframe_source": null, "dataframe_hash": null,
 "wps": 256, "sync_words": [583, 1464, 2631, 3512],
 "source_type": "virtual", "port": "VIRTUAL", "baudrate": 115200, "protocol_mode": "STREAM",
 "simulator": {"signal_source": "engineering", "device_mode": "WALK",
               "device_info": "SIM-A717 v1 wps=256 mode=UPLOAD proto=STREAM running=0 virtual=1"},
 "notes": ""}
```

`dataframe_source` and `dataframe_hash` are the dataframe's source file and
SHA-256 when it came from a file; `source_type` is `serial`, `virtual` or
`replay` (when re-recording a replay).

**Following lines**, one record each, distinguished by `kind`:

| Kind | Fields | Written |
| --- | --- | --- |
| `subframe` | `t` (arrival time, seconds since the epoch), `frame`, `sf`, `state` (`RECEIVED`, `INVALID`, `LATE`), `words` (all `WPS` words as 3 hex characters each) | For every assembled subframe; this is the raw stream data replay is built from |
| `sample` | `t`, `id`, `name`, `frame`, `sf`, `occ`, `raw`, `dec`, `eng`, `unit`, `status` | For every published `ParameterSample` |
| `event` | `t`, `event` (the kind), `message` | For every stream state event |
| `end` | `subframes`, `samples`, `events`, `first_t`, `last_t` | Once, when the recording is stopped cleanly |

```json
{"kind": "subframe", "t": 1790000001.0, "frame": 0, "sf": 1, "state": "RECEIVED", "words": "247000000D54000…"}
{"kind": "sample", "t": 1790000000.01171875, "id": "demo-pitch", "name": "PITCH ATT #1", "frame": 0, "sf": 1, "occ": 1, "raw": 853, "dec": -171, "eng": -30.096, "unit": "deg", "status": "VALID"}
{"kind": "event", "t": 1790000009.0, "event": "STREAM_SYNC_LOST", "message": "sync word mismatch"}
{"kind": "end", "subframes": 12, "samples": 468, "events": 3, "first_t": 1790000000.01171875, "last_t": 1790000012.0}
```

Recording is started and stopped from the File menu (**Record Session…**,
**Stop Recording**); the status bar shows `● REC` while recording and names
the file for a few seconds when it stops.

## Replay

`RecordingService.start_replay(path, speed)` opens the file with
`SessionReader` (which validates the format tag and version and reads the
header), refuses a session whose WPS differs from the loaded dataframe
(`REPLAY_ERROR`), logs a warning when the dataframe *name* differs, and
attaches a `RecordedSessionSource` to the serial service in place of a
serial source (`CONN_REPLAYING`; the Hardware page shows `REPLAYING` and the
header `Source: REPLAY (live)`).

The replay thread reads the `subframe` records and feeds each one to the
normal subframe assembler with its recorded words, state and timestamp, at
the recorded relative timing divided by the speed factor (spec v2 §53.10);
File → **Pause Replay**, or the toolbar's **❚❚ Pause stream**, holds it in
place and **▶ Start stream** resumes it. Timestamps are the recorded wall-clock
times, so the graph's time axis matches the original session. Everything
after the assembler is the live path: the streaming service re-decodes the
subframes, the bus publishes fresh samples, the time-series store fills, the
Frame View updates progressively. The `sample` records in the file are not
replayed; they document what was decoded at recording time and let a script
compare (the tests do). At the end `REPLAY_FINISHED` detaches the source and
the connection returns to `DISCONNECTED`; a replay can itself be recorded.

## Acceptance (spec v2 §53.7–§53.10)

| Criterion | Where it is met | Test |
| --- | --- | --- |
| 53.7 connect to a COM port, receive 256 WPS, stay aligned, recognise subframes, assemble SF1–SF4, progressive Frame View, disconnect/reconnect without restart | `PySerialTransport`, parser, synchronizer, assembler, `FrameStore` live states, `SerialService._maybe_reconnect` | `tests/test_sim_a717_stream.py`, `test_subframe_assembler.py`, `test_virtual_device.py` (incl. the `DISCONNECT_RECONNECT` fault) |
| 53.8 samples only when the subframe exists, timestamps, frequency respected, no fabricated samples | `StreamingService`, `decode_subframe`, `sample_time` | `tests/test_parameter_samples.py` |
| 53.9 any numeric parameter selectable, live updates, current value and unit, window change, pause without stopping acquisition, bounded history, same-unit overlays | `GraphsPage`, `TimeSeriesStore` | `tests/test_timeseries_store.py`, `tests/test_ui_stream_smoke.py` (offscreen: virtual device, overlay, pause, fault, record, replay) |
| 53.10 record a HIL session, replay within timing tolerance through the same decoder and graph pipeline | `SessionRecorder`, `RecordedSessionSource`, `RecordingService` | `tests/test_record_replay.py` (live and replayed series are identical) |

## Design constraints (spec v2 §54)

| Must not | How the code complies |
| --- | --- |
| Treat the STM32 UART as ARINC 717 electrical signalling | The protocol is named `SIM-A717`, the source `SERIAL`; the real hardware source stays a stub behind the same subframe boundary. |
| Make graph widgets own time-series state | `TimeSeriesPlot` keeps only the series it was handed for the current paint; history lives in `TimeSeriesStore`. |
| Block serial acquisition while redrawing | Acquisition runs on its own thread and only touches a queue and locked counters; the GUI drains it on a timer and repaints at 10 Hz at most. |
| Fabricate samples to smooth graphs | Samples come from decoded subframes only; a missing or invalid subframe leaves a gap. |
| Hard-code WPS = 256 | WPS comes from the dataframe and is sent to the device; the synchronizer, assembler and firmware take it as a parameter (the firmware's ceiling is 512 for RAM reasons). |
| Require real hardware for development | The virtual device runs the whole path in-process; `--selftest` exercises it. |
