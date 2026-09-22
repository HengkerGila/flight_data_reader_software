# 10 — Troubleshooting

## Messages from the services

Service operations fail with `STATE: message`. The state names the
situation; the message gives the detail.

| State | Meaning and what to do |
| --- | --- |
| `ADB_PARSE_ERROR` | The `.adb` file is not readable or a record does not match the layout; the message names the record and field. See [06](06-adb-format.md). |
| `ADB_WRITE_ERROR`, `FRAME_SAVE_ERROR` | The file could not be written (permissions, path). |
| `MISSING_DATAFRAME` | The operation needs a loaded dataframe (scenarios, edits). |
| `MISSING_FRAME` | Nothing to save; generate or load a frame first. |
| `INVALID_WPS` | WPS must be a positive integer. |
| `INVALID_FIELD` | A metadata field that cannot be edited was given. |
| `DUPLICATE_ID`, `UNKNOWN_PARAMETER` | Parameter id clash or a stale reference during editing. |
| `FRAME_LOAD_ERROR` | The frame file is not an `arinc717-frame` JSON file or contains an invalid word. |
| `DATAFRAME_WPS_MISMATCH` | The frame file's WPS differs from the loaded dataframe. Load a matching dataframe first, or generate a new frame. |
| `INVALID_WORD` | A word outside 0..4095 or an address outside the frame. |
| `EMPTY_SCENARIO`, `INVALID_SCENARIO_INPUT` | No target values, or a target that is not a number. |
| `ENCODE_ERROR` | A requested value does not fit the parameter's mapping (out of signed range, BCD digit overflow, unknown discrete label, word outside the frame). |
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
- **Applying a scenario discarded my manual edits.** A scenario writes into a
  fresh blank frame unless *Start from current frame* is checked.
