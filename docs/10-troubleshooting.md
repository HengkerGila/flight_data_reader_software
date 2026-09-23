# 10 — Troubleshooting

## Messages from the services

Service operations fail with `STATE: message`. The state names the
situation; the message gives the detail.

| State | Meaning and what to do |
| --- | --- |
| `ADB_PARSE_ERROR` | The `.adb` file is not readable or a record does not match the layout; the message names the record and field. See [06](06-adb-format.md). |
| `ADB_WRITE_ERROR`, `FRAME_SAVE_ERROR` | The file could not be written (permissions, path). |
| `MISSING_DATAFRAME` | The operation needs a loaded dataframe (edits, connecting to the stream device, whose WPS and sync words come from it; the dataframe must define four sync words). |
| `MISSING_FRAME` | Nothing to save; generate or load a frame first. |
| `INVALID_WPS` | WPS must be a positive integer. |
| `INVALID_FIELD` | A metadata field that cannot be edited was given. |
| `DUPLICATE_ID`, `UNKNOWN_PARAMETER` | Parameter id clash or a stale reference during editing. |
| `FRAME_LOAD_ERROR` | The frame file is not an `arinc717-frame` JSON file or contains an invalid word. |
| `DATAFRAME_WPS_MISMATCH` | The frame file's WPS differs from the loaded dataframe. Load a matching dataframe first, or generate a new frame. |
| `INVALID_WORD` | A word outside 0..4095 or an address outside the frame; or an edit attempted while the frame is being received live (`the frame is being received live; stop the stream to edit`: press ❚❚ Pause stream first). |
| `EMPTY_SCENARIO`, `INVALID_SCENARIO_INPUT` | No target values, or a target that is not a number (scenario service; reachable from the API and the tests only, the Simulator page was removed). |
| `ENCODE_ERROR` | A requested value does not fit the parameter's mapping (out of signed range, BCD digit overflow, unknown discrete label, word outside the frame). In the stream event log it means a simulated signal's Low/High exceed what the mapping can encode: narrow them on the Hardware page. Also raised when a signal is configured for a parameter that is not encodable. |
| `SERIAL_PORT_ERROR` | The port could not be opened (wrong name, in use by another program, no permission) or no SIM-A717 device answered `STOP`/`PING` within 1.5 s (wiring, baud rate, board not running the firmware). |
| `SERIAL_DISCONNECTED` | The operation needs a connected device, or the port failed while in use. The service reconnects by itself as long as you have not pressed Disconnect. |
| `INVALID_SIM_PROTOCOL` | Something answered but not as a SIM-A717 v1 device (wrong reply to `PING`, a rejected `SET_WPS`/`SET_SYNC`/`SET_PROTOCOL`), an unknown device mode, fault or signal source was requested, or bad framed packets arrived. |
| `REPLAY_ACTIVE`, `RECORDING_ACTIVE` | Stop the replay before connecting; stop the recording before replaying, or vice versa. |
| `RECORDING_ERROR` | The session file could not be created (path, permissions). |
| `REPLAY_ERROR` | The file is not an `arinc717-session` file, has another version, is unreadable, or was recorded at a WPS other than the loaded dataframe's; a malformed record during replay ends it with the same state. |
| `PDF_IMPORT_UNAVAILABLE` | PyMuPDF is not installed: `pip install pymupdf`. |
| `PDF_INGEST_ERROR` | The file is missing, not a PDF, or password protected. |
| `PDF_EXTRACTION_REVIEW_REQUIRED` | Publishing was refused because rows still need review, or a scanned page had no text and no OCR engine. |

## Decode statuses in the Parameters page

| Status | Typical cause | Fix |
| --- | --- | --- |
| `INVALID_MAPPING` | A segment's word is beyond the frame's WPS (dataframe WPS changed, or a frame of a different size is loaded), bits outside 1..12, or segments of one occurrence with different subframes. | Check the mapping in the Dataframe page; generate a new frame after changing WPS. |
| `INVALID_BCD` | A BCD field holds a nibble above 9. Real for random frames; for real data it means the field is not BCD or the mapping is off. | Check the type and bit range. |
| `OUT_OF_RANGE` | The engineering value is outside the dataframe's minimum/maximum. Expected for random frames. | Nothing, unless the range or conversion is wrong. |
| `UNSUPPORTED_TYPE` | Canonical type `unknown` (source type text not recognised) or a non-linear formula. | Edit the parameter's source type; the demo's `SPARE 15` shows this on purpose. |

`Decode OK / FAIL` in the status bar counts these statuses over all samples.

## Validation warnings that are expected

- The demo dataframe reports one warning (`SPARE 15` has an unknown source
  type) and one decode failure. Both are intentional.
- Overlaps (`semantic.overlap`) are warnings because real dataframes overlay
  spares and supersets; they are shown on both parameters.

## Live stream (Hardware page)

The Hardware page's event log and diagnostics name what happened; these are
the events to look for and what they usually mean.

| Event / symptom | Meaning and what to do |
| --- | --- |
| `STREAM_SYNC_LOCKED … locked at SF2` | Normal: the synchronizer found two sync words `WPS` apart and locked at the subframe named. |
| Sync stays `SEARCHING`, words are being received | The board's sync words or WPS do not match the dataframe. Press **Reset device** (re-sends both). If the Stream box says "sync spacing looks like 128 WPS", the device is streaming at that rate while the dataframe declares another; fix the dataframe or the device. |
| `STREAM_RATE_MISMATCH` | Same diagnosis, as an event: sync pairs were found at another standard spacing (64, 128, 256, 512, 1024), or a framed packet declared another WPS. |
| `STREAM_SYNC_LOST` followed by `STREAM_SYNC_LOCKED` | One subframe had a wrong sync word: a dropped or corrupted byte, an injected fault, or the device restarting a frame. The subframe is shown `INVALID`, decoding skips it, and lock is regained within one subframe. Frequent losses without injected faults point at the cable (ground, 5 V vs 3.3 V) or a baud mismatch; `Discarded bytes` and `Invalid words` climb at the same time. |
| `SUBFRAME_INCOMPLETE` | A subframe was assembled but is `INVALID` (sync, length or word range); it produced no samples. |
| `FRAME_INCOMPLETE` | The next frame began before this one had all four subframes; the pending slots are `MISSING`. Expected after a dropped subframe or a device reset. |
| `SERIAL_DISCONNECTED`, status `RECONNECTING — …` | The port failed (cable, board reset, the virtual device's `DISCONNECT_RECONNECT` fault). The service retries every half second and resumes the stream; `RECONNECTED` follows. Press **Disconnect** to stop trying. |
| `INVALID_SIM_PROTOCOL … bad packet(s)` | Framed mode: CRC or header errors. Expected with `BAD_CRC` armed, otherwise a cable problem. |
| `ENCODE_ERROR` | A simulated signal's range exceeds what the mapping can encode; the affected words keep their previous value. Narrow Low/High in the signal table. |
| Rate shows thousands of WPS | The virtual device runs faster than real time (the *Virtual speed* factor); the rate is measured over real seconds. |
| Graph shows nothing | Nothing is streaming, the selected parameter has not produced a numeric sample yet, or its samples are all non-`VALID` (the Status field says which). Check the Parameters page for the parameter's status. |
| The Frame View cannot be edited | The frame is live. Stop the stream; the last frame becomes editable. |

**Serial ports on Windows.** The port list shows `COMn` names from pyserial
with the driver's description ("USB Serial Port"). If the FTDI port is
missing, install the FTDI VCP driver and press **Refresh**; if `Connect`
reports "access denied", another program (a terminal, the flashing tool)
holds the port. Ports above `COM9` work the same way. On Linux the user
needs read/write access to `/dev/ttyUSB0` (the `dialout` group on Debian
and Ubuntu).

**No hardware?** Use the `VIRTUAL` port; every feature of the Hardware page
works against it, including disconnect and reconnect.

The firmware's own symptoms (no reply, LED, flashing) are in the
[firmware README](../firmware/stm32f103_sim_a717/README.md#troubleshooting).

## PDF import

**"PDF import needs the 'pymupdf' package".** Install it into the virtual
environment; the Import PDF button is disabled until then.

**A scanned page was reported, nothing extracted.** The page has no text
layer and the OCR engine is missing (`pip install rapidocr-onnxruntime`), or
`ocr="never"` was set. Pages with an embedded text layer never need OCR.

**No parameter table found.** The header was not recognised. The
`PDF_TABLE_SKIPPED` issue quotes the header cells; add the document's
header names to `ImportProfile.column_overrides` (header text → field) or
to the synonym table in `extract.py`.

**No ruled table found on a scanned page.** The grid detector needs visible
ruling lines spanning most of the table width. Faint or broken rules, very
strong skew, or a table drawn without lines defeat it.

**Everything needs review.** Look at the document-level issues and the
reasons column. If the same reason repeats (blank subframes, a two-number
resolution, MSB/LSB ranges), declare the convention in the review dialog and
press Re-normalize; the rows become clean. Then filter by the remaining
reasons and approve in bulk after spot checks.

**The frequency reading looks wrong.** The dialog shows how it was detected
("frequency read as seconds"). Declare `Hz` or `seconds` explicitly and
re-normalize.

**Digits look right but the row is flagged.** OCR corrections are always
flagged even when correct; that is the point. Use Show Source to confirm,
then approve.

**Import is slow.** OCR costs about 20 seconds per scanned page without a
text layer. Use `page_range` to import the pages you need, or run the import
once and export the published dataframe to `.adb`.

**"Consider using the pymupdf_layout package".** A hint printed by PyMuPDF;
the importer silences it. If it appears, it is harmless.

## GUI and environment

- **Blank window or Qt platform error on a headless machine.** Set
  `QT_QPA_PLATFORM=offscreen` for scripts and tests.
- **"This plugin does not support propagateSizeHints()".** Offscreen noise,
  not an error.
- **Tests fail to start with plugin import errors.** The shell sources ROS
  Jazzy; `pyproject.toml` disables its pytest plugins. Make sure that block
  is intact and run pytest through `.venv/bin/python -m pytest`.
- **Editing the dataframe WPS did not change the Frame View.** The frame
  keeps its size until a new frame is generated; words beyond the new WPS
  decode as `INVALID_MAPPING` meanwhile.
- **Columns that have not arrived yet show values.** By design: during a
  stream a pending column keeps the previous frame's words so every cell
  shows its last known value; the header (`SF2 · pending`) and the cell
  tooltip say so. `----` only appears before a subframe has ever arrived.
- **I want to hold the frame to look at it.** Press ❚❚ Pause stream in the
  toolbar; the frame becomes static and editable, ▶ Start stream resumes.
- **Starting a replay disconnected the device.** By design: a replay takes
  the place of the live source. Reconnect from the Hardware page afterwards.
- **Closing the window while streaming.** The main window stops recording,
  replay and the connection on close (`ctx.shutdown()`); the device keeps
  streaming until it receives `STOP`, which the next connect sends first.
