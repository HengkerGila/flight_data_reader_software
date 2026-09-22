# 07 — PDF import

The PDF importer turns a dataframe layout document into a canonical
dataframe under human review. It implements spec §28–§32 and §46 and lives in
`arinc717_reader/dataframe/pdf_importer/`. It is labelled *Experimental* in
the GUI: extraction from born-digital and scanned documents works, but every
document has its own conventions, and the importer's job is to make its
reading of them explicit and reviewable rather than to be right unattended.

## The pipeline

```
PDF file
  │ ingest.py        open, fingerprint (SHA-256), per page: text layer? image coverage? stated WPS?
  ▼
page classification
  ├─ born-digital page ──► PyMuPDF table finder ("lines", then "text" strategy)
  └─ scanned page ───────► scan.py: render, measure skew, re-render deskewed,
                           detect the ruled grid, drop text boxes into cells
                              text boxes from the page's text layer, if it has one,
                              else from the OCR engine (RapidOCR), plus a
                              recognition-only second pass on empty critical cells
  ▼
extract.py          header recognition (synonyms + fuzzy match), RawTable → RawParameterRow
                    continuation rows merged into the previous row
  ▼
normalize.py        explicit rules → ParameterDefinition candidate + issues + interpretation
                    document-wide conventions resolved from evidence (effective profile)
  ▼
review.py           ImportSession: validation, state machine, approvals, exclusions, edits
  ▼
publish             DataframeDefinition from APPROVED rows only → dataframe store (unexported)
```

`pipeline.import_pdf(path, profile, progress)` runs everything up to the
session. `build_session(rows, …)` runs the second half on raw rows, which is
how most tests work without any PDF. `renormalize_session(session, profile)`
re-runs normalization with different conventions.

## Page classification

`ingest.py` records, for every page, whether it has a text layer and what
fraction of its area is covered by images (measured in both the unrotated
and the displayed coordinate space, so rotated scans are recognised). A page
with image coverage of 80 % or more is a **scan**. A scan with a text layer
(a scanner's or Acrobat's OCR output) is read from that layer; a scan without
one needs the OCR engine; a page that is neither scanned nor has text is
reported as empty. `detect_wps()` returns the words-per-second value stated
in the document text when it is stated exactly once (`256 WPS`, `256 words
per second`); otherwise the review dialog asks the user.

## Born-digital pages

PyMuPDF's `find_tables()` is tried with the ruled-lines strategy first and
the whitespace strategy second. Each table's cells keep their bounding boxes;
cell text keeps line breaks but has runs of spaces collapsed. Native text
carries no confidence value.

## Scanned pages

The image processing summarised here is documented step by step, with the
constants and their tuning, in [11 — Scanned page processing](11-scanned-page-processing.md).

`scan.py` needs only numpy.

1. **Render** the page at `ocr_dpi` (150 by default) in grayscale.
2. **Skew.** Long horizontal ink runs are isolated with a one-dimensional
   morphological opening; each run is fitted with a line and the
   length-weighted median angle is the page skew. Anything beyond 5° is
   ignored. The page is rendered again with the skew removed through the
   render matrix, which also gives exact page-to-pixel transforms.
3. **Grid.** Horizontal rules are ink rows that survive the opening and span
   at least 40 % of the width; vertical rules span at least 20 % of the
   height. Rules that do not intersect the others' extent (signature lines,
   logos) are dropped. The result is a grid of row and column boundaries.
4. **Text boxes.** From the text layer (`page.get_text("words")`, transformed
   into displayed coordinates) or from OCR on the deskewed render. Each box
   lands in the cell containing its centre. Boxes in a cell are grouped into
   lines by vertical overlap, sorted left to right, and joined; lines are
   joined with newlines so stacked content ("AOAL" over "LH Angle Of
   Attack") stays distinguishable.
5. **Second pass.** After the header is mapped, every empty cell in a
   mapping-critical column that still contains ink is cropped, split into
   text lines by its ink profile, and passed to the OCR *recognizer* only.
   Text detectors routinely skip isolated glyphs such as a lone `0`; the
   recognizer reads them.

Every cell records its page, bounding box (displayed page coordinates, the
same space `render_region_png` uses for "Show Source"), text source
(`native`, `text_layer`, `ocr`), OCR confidence, and a note when it came from
the second pass.

### The OCR engine

`RapidOcrEngine` wraps `rapidocr-onnxruntime` (ONNX models bundled with the
package, CPU only, no Tesseract). It is loaded lazily on the first page that
needs it; when the package is missing the page is reported with
`PDF_EXTRACTION_REVIEW_REQUIRED` and skipped, never silently. A page takes
roughly 20 seconds. Known quirks: the recognizer returns a small `0` as the
CJK full stop `。` (repaired, see below), it drops spaces inside some lines
(`PitchAttitude#1`), and its detector downsamples the page, which is why the
second pass exists. `ocr="never"` in the profile disables it.

## Header recognition

`extract.py` maps header cells to raw fields through a synonym table
(normalised to upper case without punctuation) with a fuzzy fallback
(difflib ratio ≥ 0.75, also compared without spaces) for OCR damage such as
`Parameter Tvpe` or `ParameterMnemonic&Name`. Exact matches win; each field
is mapped once. A table is accepted when a name column and a word column are
mapped and at least three columns are recognised; otherwise it is skipped
with a `PDF_TABLE_SKIPPED` issue that quotes the header. A table without a
recognisable header inherits the previous table's header when the column
count matches (page continuation). Repeated header rows inside a table are
dropped.

| Field | Recognised headers (examples) |
| --- | --- |
| `mnemonic_and_name` | Parameter Mnemonic & Name, Mnemonic / Name |
| `parameter_name` | Parameter, Parameter Name, Name, Mnemonic, Signal |
| `description` | Description, Title |
| `parameter_type` | Type, Parameter Type, Data Type, Format, Encoding, Coding |
| `sign` | Sign, Signed, Polarity, S/U |
| `frequency` | Frequency, Freq, Freq (Hz), Rate, Sample Rate, Samples/sec, SPS, Interval |
| `word_location` | Word, Word Location, Word No, Word Address, Slot, A717 Word |
| `subframe` | Subframe, Sub-frame, SF |
| `msb` / `lsb` | MSB, LSB, A717 MSB, A717 LSB, Start Bit / End Bit |
| `bits` | Bits, Bit Range, Bit Position(s) |
| `units` | Units, Unit, EU, Engineering Units |
| `resolution` | Resolution, Res, LSB Value, Scale, Scale Factor, Sensitivity |
| `offset` | Offset, Bias |
| `minimum` / `maximum` | Min, Minimum, Range Min / Max, Maximum, Range Max |
| `true_state` / `false_state` | 1 =, True, True State, State 1, Set / 0 =, False, False State, State 0, Reset |
| `notes` | Notes, Remarks, Comments |

Unmapped columns are kept in `RawParameterRow.extra` keyed by header text.
`ImportProfile.column_overrides` maps a header text to a field explicitly.

## The raw model

`raw.py` defines `RawParameterRow`: one string per raw field exactly as
extracted, `extra` for unmapped columns, per-field `RawFieldProvenance`
(page, bbox, raw text, confidence, source, note), the row's page, table and
row numbers, the header texts and the full list of cells. It never holds
derived values.

**Continuation rows.** A row with no structural cells (word, type, bits,
frequency, resolution) but with text in name, state or notes columns is the
tail of the previous row cut by a page break. It is merged into that row
(text appended with a newline) and the merge is noted, so a ten-line list of
display modes split across pages 12 and 14 becomes one parameter again.

## Normalization

`normalize.py` builds a `ParameterDefinition` from each raw row. Every
derived value comes from an explicit rule, and every rule that had to
*interpret* the text records a `NormalizationIssue(severity, rule, message,
field)`. Errors mean the row is not representable as written; warnings mean
a representation was chosen that a human must confirm. Either sends the row
to review. Info issues document decisions that need no confirmation.

### Values

| Cell | Rule |
| --- | --- |
| Name | `parameter_name`, or the first line of a stacked `mnemonic_and_name` cell (remaining lines become the description when no description column exists). Empty → error. |
| Type | `normalize_source_type` on the type text; a split word (`Discret e`) or a near miss (`BCO`) is repaired to the closest known type with a warning; a sign column (`S`, `U`, `2's`…) can make a bare BNR signed. Unknown → warning. |
| Words | Groups separated by commas, semicolons, slashes or whitespace; `a-b` is a consecutive range in the written order (`154-153` → 154, 153). `14-15, 142-143` is two groups of two words. |
| Subframe | `all`, `1-4`, `1,3`, `1 & 3`, ADB selector digits (`13`), `SF 2`; a parenthetical remark is ignored (`0 (all 4)`); `0` means every subframe (info); blank means every subframe with a warning unless declared. |
| Bits | Either a `bits` column (`12-1`, `12..1`, one range per word or one for all), or `msb` and `lsb` columns holding one number each, one per word, or one *range* each: the MSB range applies to the first word of every pair, the LSB range to the second (the spec §29 layout). Mixed or unmatched counts → error. `9.1` is read as `9-1`. |
| Frequency | A number, with `Hz`, `sps`, `/s` or `s` stripped; `1/4` allowed. Read as Hz or as seconds between samples according to the effective profile. |
| Resolution / offset | Numbers with fractions (`1/16`), powers (`2^-4`), scientific notation and a declared decimal comma. A cell holding two numbers is a pair: offset then resolution by default (warning unless the order is declared), or resolution then offset. A non-zero offset column next to a pair is reported. Missing resolution: error for analog types, info for BCD (digits taken as-is), irrelevant for discretes. |
| Min / max | Optional numbers; unreadable → warning. |
| States | `true_state` / `false_state` cells; dashes and `n/a` mean empty; multi-line labels are joined. For a discrete without labels, `1 = X, 0 = Y` in the description or notes is used with a warning. |
| Units, notes | Verbatim, placeholders removed. |

### Layout of several words

The frequency column, converted to Hz, gives the number of samples per
frame; divided by the number of subframes it gives the **expected number of
occurrences per subframe**, which is used as evidence:

| Situation | Reading |
| --- | --- |
| Any word group has more than one word, or MSB/LSB ranges were used | One occurrence per group, one segment per word in the written order (first = most significant). The rate must match the group count. |
| One word | One occurrence. A rate that implies more is reported. |
| Several single words, rate implies that many occurrences | Repeated occurrences. |
| Several single words, rate implies one occurrence | Segments of one value. |
| Several single words, no usable rate | Provisional reading (occurrences when the bit fields are identical, segments otherwise) with a warning, unless the profile declares the meaning. |

A rate below one sample per frame is a superframe parameter and an error
(not supported). For repeated occurrences the first words should be evenly
spaced (WPS ÷ occurrences); uneven spacing is reported because it usually
means a misread word number.

### OCR corrections

When a numeric cell does not parse, the normalizer tries, in order, a digit
correction (`O`, `o`, `D`, `Q`, `。` → 0; `l`, `I`, `|` → 1; `S`, `s` → 5;
`B` → 8; `Z`, `z` → 2), the removal of stray marks a stamp or rule left next
to the number (`-- 1`, `.. 1`), and both. A correction that makes the cell
parse is applied and reported as a warning that quotes both readings
(`resolution '3.5,0.Dl' read as '3.5,0.01' (OCR digit correction) —
confirm`). Nothing is corrected silently.

Low OCR confidence on a mapping-critical field (below
`ocr_confidence_threshold`, 0.6 by default) is a warning; on units, states
and notes it is only informational.

### Document-wide conventions

`ImportProfile` declares what the document is known to do; a declared
convention is authoritative and raises no review issue. Two conventions are
also **detected** from the document when left on `auto`, and the detection is
reported as a document-level issue and shown in the review dialog:

- **Frequency unit.** For every row with a readable rate, word groups and
  subframes, both readings (Hz, seconds) are checked for consistency with
  the number of groups; the reading that is consistent for more rows wins.
- **MSB/LSB ranges over word pairs.** When at least three rows give MSB and
  LSB as ranges over pairs of words, that layout is applied document-wide.

| Profile field | Default | Meaning |
| --- | --- | --- |
| `page_range` | all pages | `(first, last)` pages to read. |
| `ocr` | `auto` | `never` reports scanned pages without a text layer instead of reading them. |
| `ocr_dpi` | 150 | Render resolution for scanned pages. |
| `ocr_confidence_threshold` | 0.6 | Below this, a critical field is flagged. |
| `frequency_unit` | `auto` | `hz` or `seconds`. |
| `multiword_bits` | `auto` | `per_word` or `range_per_word`. |
| `multiword_meaning` | `auto` | `occurrences` or `segments` for several single words without a usable rate. |
| `resolution_pair` | `auto` | `offset_resolution` or `resolution_offset` for two-number resolution cells. |
| `blank_subframe_means_all` | false | Silence the blank-subframe warning. |
| `zero_subframe_means_all` | true | Accept `0` as every subframe. |
| `decimal_comma` | false | Read `0,0625` as a decimal. |
| `column_overrides` | {} | Header text → field. |
| `default_wps` | 256 | Used when the document does not state a WPS. |

### Duplicate mnemonics

Documents that stack a group label over the parameter name ("AP Armed
Mode" over "Alt Mode Armed") repeat the label as the mnemonic. After
normalization, duplicated mnemonics are qualified with the name ("AP Armed
Mode: Alt Mode Armed", or "#2" when there is no distinct name) and the
qualification is recorded as an info issue.

### Issue rules at a glance

`normalize.name`, `normalize.type`, `normalize.type_repaired`,
`normalize.sign_column`, `normalize.sign_conflict`, `normalize.word`,
`normalize.word_repaired`, `normalize.subframe`, `normalize.subframe_blank`,
`normalize.subframe_zero`, `normalize.subframe_repaired`, `normalize.bits`,
`normalize.bits_missing`, `normalize.bits_precedence`,
`normalize.bits_layout`, `normalize.bits_repaired`, `normalize.bit_order`,
`normalize.frequency`, `normalize.frequency_repaired`,
`normalize.superframe`, `normalize.multiword`, `normalize.spacing`,
`normalize.resolution`, `normalize.resolution_repaired`,
`normalize.resolution_pair`, `normalize.resolution_default`,
`normalize.offset`, `normalize.offset_repaired`, `normalize.offset_conflict`,
`normalize.minimum`, `normalize.maximum`, `normalize.discrete_width`,
`normalize.states_inferred`, `normalize.ocr`, `normalize.ocr_confidence`,
`normalize.mnemonic_qualified`, `extract.continuation`,
`extract.second_pass`. The review dialog's Reason column lists the rules
that apply to a row, and the search box matches them.

Document-level issue codes: `PDF_SCANNED` (info), `PDF_FREQUENCY_UNIT`
(info), `PDF_BITS_LAYOUT` (info), `PDF_EMPTY_PAGE`, `PDF_NO_GRID`,
`PDF_TABLE_SKIPPED` (warnings), `PDF_EXTRACTION_REVIEW_REQUIRED`,
`PDF_NO_PARAMETER_TABLE` (errors).

## Review

`review.py` implements the state machine of spec §32:

```
EXTRACTED → NORMALIZED → VALIDATED → APPROVED → PUBLISHED
                       ↘ REVIEW_REQUIRED ↗
```

with two extra back edges: an edit sends a VALIDATED or APPROVED row back to
NORMALIZED, and an approved row drops to REVIEW_REQUIRED when validation
changes underneath it (for example after the WPS is corrected).

- After normalization the session validates all included candidates
  **together** with the ordinary dataframe validator, so overlaps between
  imported rows are found and shown on both rows.
- A row with any normalization error or warning, or any validation error,
  is REVIEW_REQUIRED. A row with only validation warnings (an overlap) is
  VALIDATED. A row with no issues at all is **approved automatically** (spec
  §32 allows VALIDATED → APPROVED without a human).
- **Approve** is allowed for VALIDATED and REVIEW_REQUIRED rows without
  errors; a row with errors must be edited first.
- **Exclude** removes a row from validation and publishing; **Include**
  brings it back.
- **Edit** replaces the candidate; the row is re-validated and, having been
  looked at, approved when it has no errors.
- **Publish** requires every included row to be APPROVED and produces the
  dataframe with `source_type = "pdf"`, the file name and hash, the confirmed
  WPS and sync words, and each parameter's provenance: page, table, row,
  bounding box, raw cells, raw texts per field, unmapped columns,
  confidence, text source, frequency unit, the interpretation of every
  derived value and the review history.

## The review dialog

Opened automatically after an import, or from the Import page's review
queue.

**Document box.** File, pages, tables, rows, hash, the counts per state, and
the document-level issues including the detected conventions.

**Metadata box.** Dataframe name, WPS (with "stated in document: 256" or
"not stated — verify"), sync words. **Apply Metadata** re-validates every row
against them.

**Document conventions box.** The profile fields listed above. **Re-normalize**
re-reads every row with the chosen conventions; it resets approvals and
manual edits but keeps exclusions, and asks for confirmation.

**Row table.** State (coloured), page, parameter, type, words, bits,
subframes, frequency, unit, resolution, offset, an issue count (`1E 2W`) and
the reasons. The **Show** filter selects all rows, rows needing review,
validated-but-not-approved rows, approved rows or excluded rows; the search
box matches parameter, type, words, reasons and issue text. Selecting
several rows (Ctrl or Shift) enables bulk actions.

**Details panel.** For the selected row: the verbatim cells column by
column, the mapped fields, unmapped columns, bounding box and confidence;
the interpretation (how the type, words, subframes, bits, layout, rate and
conversion were derived); the candidate mapping; the issues; the history.

**Buttons.** Edit… (the parameter editor with a Check preview against the
other imported rows), Approve Selected, Exclude / Include, Show Source…,
Approve All Validated, Publish to Workspace, Cancel.

**Show Source.** Opens a viewer almost as wide as the review dialog with the
page region the row was extracted from. "Row with context" shows the row
with its neighbours above and below and the extracted row outlined in red;
"Whole page" shows the page with the same outline. The render is fitted to
the viewer's width by default (no sideways scrolling); untick "Fit width" to
zoom with the slider.

Rows in the table are tinted by review state (red = needs review, yellow =
validated, green = approved, blue = published) with the text colour chosen
to stay readable on that tint in both light and dark widget themes; excluded
rows are greyed out.

A practical way through a large document: apply the metadata, declare the
conventions the importer detected or flagged and re-normalize, filter
"Needs review", type a reason such as `subframe_repaired` in the search box,
select the visible rows, check a few with Show Source, approve the
selection, and repeat per reason. Rows with errors get Edit; rows that are
not parameters at all (a sync word, a stamp-garbled line) get Exclude.

## The CN235-220 document

`examples/Scanned_from_UK_Lexmark03-12-2025-123425 (1) data frame.pdf` is a
real ARINC 717 dataframe layout document for the CN235-220 by Flight Data
Systems: 21 scanned pages, of which pages 3–13 carry an Acrobat text layer
and 14–21 are image-only and rotated. Its table columns are Parameter
Mnemonic & Name (stacked), Parameter Type, Frequency (the sample **interval
in seconds**), Word Location (`14-15, 142-143`), A717 MSB / A717 LSB (`9-1`
/ `12-9`, the word-pair layout), Subframe (`0 (all 4)`), Units, Resolution
(sometimes `offset, resolution`), True State, False State, Notes. It states
256 WPS.

Importing all pages takes about 2.5 minutes and yields 326 rows, of which
about 175 are approved automatically, both conventions are detected, and the
display-mode row split between pages 12 and 14 is merged. Importing pages
3–13 (`ImportProfile(page_range=(3, 13))`) takes four seconds. The remaining
flagged rows are OCR corrections to confirm, overlap warnings that reveal
misread bit or word numbers, stamp-garbled last rows, and a few enumerated
multi-bit discretes (see limitations).

## Synthetic documents

`synth.py` renders any dataframe as a PDF for closed-loop testing:
`write_dataframe_pdf(dataframe, path, style="generic" | "fds", rows_per_page,
repeat_header, scanned=None | "image" | "text_layer", skew_degrees,
font_size)`. The `fds` style reproduces the CN235 document's conventions; the
scanned variants embed a rotated page image with or without an invisible text
layer. The tests import these and compare the published dataframe with the
original.

## Limitations

- **Enumerated discretes.** A discrete field wider than one bit with a list
  of states (`0 = map display, 1 = weather radar, …`) is flagged; the states
  are kept in the notes because the canonical model only labels zero and
  non-zero.
- **Superframe parameters** are recognised by their rate and rejected.
- **Non-linear conversions** are not represented.
- **OCR quality.** Names may lose spaces or letters; digits are corrected
  only where a correction makes the cell parse, and always with a flag.
  Show Source exists for exactly this.
- **Skew** is corrected up to 5°; strongly warped scans are not.
- The importer reads tables; prose-style parameter descriptions are not
  understood.
