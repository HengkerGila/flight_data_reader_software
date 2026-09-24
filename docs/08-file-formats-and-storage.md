# 08 — File formats and storage

## Where data lives

| Data | Representation | Persistence |
| --- | --- | --- |
| Dataframe | `DataframeDefinition` in the dataframe store | `.adb` export ([06](06-adb-format.md)); optionally the SQLite repository (API only) |
| Frame | `Arinc717Frame` in the frame store | Frame JSON file |
| Engineering values | `EngineeringValue` list in the engineering store | Not persisted; recomputed from frame + dataframe |
| PDF import session | `ImportSession` held by the Import page until published | Not persisted; publish to make it a dataframe |
| Stream state | `StreamStore`: connection, progress, diagnostics snapshot, last 200 events | Not persisted |
| Parameter samples | `TimeSeriesStore` ring buffers, at most 10,000 samples or 10 minutes per parameter | Not persisted as such; written as `sample` records while a session is recorded |
| Recorded session | `.a717session` file written line by line while recording | The file itself; replay reads it back |

## Frame files

Frames are saved as JSON by `arinc717_reader/sources/frame_io.py`:

```json
{
  "format": "arinc717-frame",
  "version": 1,
  "wps": 256,
  "frame_index": 3,
  "subframes": [
    [583, 0, 0, 3412, ...],
    [1464, 0, 0, 0, ...],
    [2631, 0, 0, 0, ...],
    [3512, 0, 0, 0, ...]
  ]
}
```

`subframes` holds exactly four lists of exactly `wps` integers in 0..4095.
Loading validates the `format` tag and every value and raises
`FrameIoError` (surfaced as `FRAME_LOAD_ERROR`) on anything malformed. The
frame service additionally refuses a frame whose WPS differs from the loaded
dataframe (`DATAFRAME_WPS_MISMATCH`), because word addresses would not line
up with the mapping.

## Session files

A recording of a live stream (or of a replay) is a `.a717session` file
written by `arinc717_reader/recording/session.py` in **JSON Lines**: one
JSON object per line, appended as data arrives, so nothing is held in memory
and a file cut short by a crash is still readable up to its last complete
line.

| Line | Content |
| --- | --- |
| 1 | The header: `format` (`arinc717-session`), `version` (1), `created`, `dataframe_name`, `dataframe_source`, `dataframe_hash`, `wps`, `sync_words`, `source_type` (`serial`, `virtual`, `replay`), `port`, `baudrate`, `protocol_mode`, `simulator` (signal source, device mode, the device's `INFO` line), `notes` |
| `{"kind": "subframe", …}` | One per received subframe: `t` (arrival time, seconds since the epoch), `frame`, `sf`, `state` (`RECEIVED`, `INVALID`, `LATE`), `words` (every word of the subframe as 3 hexadecimal characters, `247` `000` `D54` …) |
| `{"kind": "sample", …}` | One per decoded sample: `t`, `id`, `name`, `frame`, `sf`, `occ`, `raw`, `dec`, `eng`, `unit`, `status` |
| `{"kind": "event", …}` | One per stream event: `t`, `event`, `message` |
| `{"kind": "end", …}` | Written once when the recording is stopped: `subframes`, `samples`, `events`, `first_t`, `last_t` |

Replay uses the header and the `subframe` records only and re-decodes them
through the normal pipeline; the `sample` records document what was decoded
at recording time. `SessionReader` rejects a file without the format tag or
with another version (`REPLAY_ERROR`), and the recording service refuses to
replay a session whose `wps` differs from the loaded dataframe. Word strings
that are not a multiple of three characters are a `REPLAY_ERROR` as well.
Worked examples and the replay timing are in
[13 — Live streaming, graphs and recording](13-live-streaming-graphs-recording.md#recording).

## The SQLite repository

`arinc717_reader/dataframe/repository/` implements the internal database of
spec §38–§39. It is complete and tested but not yet reachable from the GUI;
it is used from Python:

```python
from arinc717_reader.dataframe.repository import DataframeRepository

repo = DataframeRepository("dataframes.sqlite")     # or ":memory:"
document_id = repo.save_dataframe(dataframe, status="PUBLISHED")
repo.save_validation_issues(document_id, validate_dataframe(dataframe))
for row in repo.list_documents():
    print(row["id"], row["name"], row["wps"], row["status"])
loaded = repo.load_dataframe(document_id)
repo.close()
```

### Schema

| Table | Columns |
| --- | --- |
| `dataframe_documents` | id, name, aircraft_type, revision, issue_date, wps, superframe_present, sync_words (JSON list), source_type, source_filename, source_hash, status |
| `parameters` | id, document_id, param_key (the canonical id), mnemonic, description, source_parameter_type, parameter_type, unit, minimum, maximum, resolution, conversion_offset, formula_type, true_state, false_state, notes, decimals, states (JSON list of `[value, label]` pairs, NULL without a table), status |
| `parameter_occurrences` | id, parameter_id, occurrence_index |
| `parameter_segments` | id, occurrence_id, sequence, subframe_selector_raw, word, lsb, msb, legacy_flag, bcd_weight |
| `source_fields` | id, parameter_id, field_name, raw_text, normalized_value, page_number, bbox, confidence, reviewed — reserved for per-field PDF provenance |
| `validation_issues` | id, document_id, parameter_id, severity, rule_name, message, resolved |
| `review_history` | id, document_id, parameter_id, from_state, to_state, timestamp, note |
| `adb_legacy_fields` | id, document_id, parameter_id, scope, position, value |

`offset` is stored as `conversion_offset` to avoid the SQL keyword. Raw ADB
records are stored field by field in `adb_legacy_fields` under the scopes
`settings`, `parameter_record` and `parameter_trailing`, and reattached on
load, so a save/load cycle followed by an export loses nothing. Foreign keys
cascade on delete. Loaded parameters carry `provenance.source_type =
"repository"`.

The columns `decimals`, `states` and `bcd_weight` were added on 2026-09-24
with the AFDA record layout. `init_schema()` adds any of them that is
missing (`ALTER TABLE … ADD COLUMN`) when an older database file is opened,
so existing files keep working; parameters saved before the change load
with no decimals, no state table and no digit weights.

## PDF documents

Inputs to the importer are ordinary PDF files. Nothing is written back to
them; the importer only reads and renders. See [07 — PDF import](07-pdf-import.md).
