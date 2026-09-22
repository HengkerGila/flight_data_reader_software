# 08 — File formats and storage

## Where data lives

| Data | Representation | Persistence |
| --- | --- | --- |
| Dataframe | `DataframeDefinition` in the dataframe store | `.adb` export ([06](06-adb-format.md)); optionally the SQLite repository (API only) |
| Frame | `Arinc717Frame` in the frame store | Frame JSON file |
| Engineering values | `EngineeringValue` list in the engineering store | Not persisted; recomputed from frame + dataframe |
| PDF import session | `ImportSession` held by the Import page until published | Not persisted; publish to make it a dataframe |

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
| `parameters` | id, document_id, param_key (the canonical id), mnemonic, description, source_parameter_type, parameter_type, unit, minimum, maximum, resolution, conversion_offset, formula_type, true_state, false_state, notes, status |
| `parameter_occurrences` | id, parameter_id, occurrence_index |
| `parameter_segments` | id, occurrence_id, sequence, subframe_selector_raw, word, lsb, msb, legacy_flag |
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

## PDF documents

Inputs to the importer are ordinary PDF files. Nothing is written back to
them; the importer only reads and renders. See [07 — PDF import](07-pdf-import.md).
