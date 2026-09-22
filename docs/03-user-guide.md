# 03 — User guide

## The main window

```
┌────────────────────────────────────────────────────────────────────────┐
│ File                                                                   │
│ Dataframe: demo_256wps.adb*   WPS: 256   Source: SCENARIO   Frame: 000003 │
├────────────────────────────────────────────────────────────────────────┤
│ [Frame View] [Parameters] [Dataframe] [Simulator] [Import]             │
│                                                                        │
│                          current page                                  │
│                                                                        │
├────────────────────────────────────────────────────────────────────────┤
│ Source: SCENARIO  DF: VALID, 1 warn  WPS: 256  Parameters: 12  Decode OK: 33  FAIL: 1 │
└────────────────────────────────────────────────────────────────────────┘
```

**Header line.** The loaded dataframe (its file name, or its name when it was
not loaded from a file), its WPS, the name of the source that produced the
current frame (`RAW RANDOM`, `MANUAL`, `SCENARIO`, `FILE name.json`), and the
frame index. A `*` after the dataframe name means it has edits or imported
content that has not been exported yet; the window title shows the same
marker.

**Status bar.** The frame source, the dataframe validity (`VALID`, or the
number of validation errors, plus the number of warnings), the WPS of the
*frame* (which can differ from the dataframe's WPS until a new frame is
generated), the parameter count, and how many decoded samples are `VALID`
versus any other status.

**File menu.** New Dataframe…, Open ADB…, Import PDF…, Export ADB…, Load
Frame…, Save Frame…, Quit.

## Frame View

The Frame View shows the canonical frame as a table with one row per word
address (001 to WPS) and one column per subframe (SF1 to SF4). Cells are
centred, monospaced, and formatted in the selected representation.

**Controls.**

| Control | Effect |
| --- | --- |
| Generate Frame (random) | Replaces the frame with uniform random words (0 to 4095). Labelled RAW RANDOM because engineering values are meaningless. |
| Clear Frame | Replaces the frame with all zeros, without sync words. |
| Load Frame… / Save Frame… | Frame JSON files, see [08 — File formats](08-file-formats-and-storage.md#frame-files). Loading refuses a file whose WPS differs from the loaded dataframe. |
| Representation | BIN (12 zero-padded bits), OCT (4 digits), DEC, HEX (3 digits). Switching never changes the data. |

The frame's WPS follows the loaded dataframe; with no dataframe it is 256.

**Single click** on a cell opens it in the **Word Inspector** on the right.
**Double click** opens the **edit dialog**.

### Word Inspector

The inspector is a diagnostic panel that exposes the full decoding chain of
the clicked word. It shows the raw word in all four representations and then,
for every parameter mapped to that word in that subframe, a block like:

```
PITCH ATT #1  (occurrence 1)

Bit Range      : 12-3
Extracted Bits : 1101010101

Encoding       : analog_signed
Bit Width      : 10

Decoded Decimal: -171
Resolution     : 0.176
Offset         : 0

Engineering    : -30.096 deg
Status         : VALID
```

If several parameters share the word, every one is listed. For multi-segment
parameters the *extracted bits* line shows only this word's segment; the
*decoded decimal* and *engineering* lines refer to the whole assembled value.
The panel updates whenever the frame or the decoded values change.

### Editing a word

The edit dialog shows the subframe, the word address, the current value in
all representations, a representation selector and an input field. Enter the
new value in the chosen representation and press OK. Anything that is not a
valid number in that base, or that exceeds 12 bits (4095), is rejected with a
message and the dialog stays open. A successful edit changes exactly that
canonical cell and immediately re-decodes every parameter that uses it.

## Parameters

The Parameters page lists every decoded **sample**: one row per parameter,
occurrence and subframe. A parameter recorded in all four subframes with two
occurrences therefore appears eight times.

| Column | Meaning |
| --- | --- |
| Mnemonic, Description, Type | From the dataframe. Type is the canonical type (`analog_signed`, `analog_unsigned`, `bcd`, `discrete`, `raw`, `unknown`). |
| Occ, SF | Occurrence index and the subframe this sample came from. |
| Raw | The assembled raw bits of all segments, most significant first. |
| Decimal | The decoded decimal: two's-complement value for signed parameters, the integer for unsigned, the digit value for BCD, the field value for discretes. |
| Resolution, Offset | The linear conversion; shown only for analog and BCD types. |
| Engineering | The converted value, or the state label for discretes. |
| Unit | From the dataframe. |
| Source | Where the parameter definition came from (`adb`, `pdf`, `demo`, `manual`, `repository`). |
| Status | `VALID`, or a diagnostic status; non-valid statuses are coloured. |

Filters: free-text search over mnemonic and description, and drop-downs for
type, status and subframe. Selecting a row shows its complete **decode
trace** below the table: every segment with its subframe, word, bit range,
the word value and the extracted bits, then the assembled bits, decoded
decimal, resolution, offset and engineering value. See
[05 — Decoding](05-decoding-and-encoding.md) for the meaning of each status.

## Dataframe

The Dataframe page is where a dataframe is browsed, inspected and edited.

**Metadata line.** Name, aircraft type, revision, issue date, WPS, sync
words, superframe flag, source type and file, the first characters of the
source file's SHA-256, and the parameter count. "(modified, not exported)"
appears while there are unexported edits.

**Toolbar.**

| Button | Effect |
| --- | --- |
| New Dataframe… | Creates an empty dataframe from a metadata dialog (name, aircraft, revision, date, WPS, superframe flag, four sync words). Asks first if the current dataframe has unexported edits. |
| Edit Metadata… | The same dialog for the loaded dataframe. Changing the WPS re-validates all mappings but does not rebuild the current frame. |
| Add Parameter… / Edit Parameter… | The parameter editor, described below. Double-clicking a parameter also opens it. |
| Duplicate | Copies the selected parameter with the mnemonic suffixed " COPY" and a fresh id; provenance is dropped because the copy was made in the application. |
| Remove | Deletes the selected parameter after confirmation. |
| ▲ ▼ | Move the parameter up or down. Parameter order is the export order. |
| Search | Filters the tree by mnemonic, description, unit or type. |

**Tree.** One top-level row per parameter (mnemonic, type, unit, resolution,
offset, occurrence count), expandable into occurrences and segments
(`SF 1,2,3,4  word 004  bits 12-3`).

**Details panel.** For the selected parameter: identity, source and canonical
type, unit, conversion, range, discrete states, notes, the full mapping with
any preserved legacy flags, and the **provenance**: the source type, the
record index and the verbatim source record for `.adb` imports, trailing
unknown fields, and for PDF imports the page, table, row and raw cells. Below
that, the preserved unknown settings fields of an `.adb` header are listed.

**Validate Dataframe** re-runs validation; the issues table lists every issue
with its severity, rule name, parameter and message. **Export ADB…** writes
the dataframe and clears the modified marker.

### The parameter editor

The editor dialog has three parts.

1. **Definition form.** Mnemonic (required), description, source type,
   unit, resolution and offset, minimum and maximum, the discrete state
   labels ("1 =" and "0 ="), and notes. The **source type** is an editable
   combo box (Signed Analog, Unsigned Analog, BNR, BCD, Discrete, Raw, or any
   text); the canonical type shown next to it is *derived* from that text by
   the same normalisation every importer uses, so it can never disagree with
   an import. Unrecognised text yields `unknown` and a warning.
2. **Mapping table.** One row per segment: occurrence index, segment
   sequence, subframes, word, LSB, MSB. Subframes accept `1,3`, `2-4`, `all`,
   or ADB selector digits such as `13`. Segment sequence 1 holds the most
   significant bits of a multi-segment value. **Add Segment** adds a segment
   to the current occurrence, **Add Occurrence** starts a new occurrence,
   **Remove Row** deletes a row. Nothing is renumbered silently; duplicate
   indexes are reported by the validator.
3. **Check** previews the validation issues the edit would cause, including
   overlaps with other parameters, without committing. **OK** commits through
   the dataframe service even if issues remain (the domain is permissive; the
   issues stay visible on the page). Invalid *input*, such as a non-numeric
   word, keeps the dialog open with a message.

Every committed edit re-decodes the current frame immediately. Edited
parameters keep their preserved raw fields, so an edited `.adb` import still
round-trips.

## Simulator

Three modes are available from the mode selector.

**Random.** Generates a RAW RANDOM frame; an optional seed makes the frame
reproducible.

**Manual.** Creates a blank frame, optionally with the dataframe's sync words
in word 1 of each subframe, for editing word by word in the Frame View.

**Scenario.** Lists every parameter that can be safely encoded (a known type
with a mapping and, for analog and BCD types, a linear conversion with a
non-zero resolution). Enter a target engineering value per parameter, or pick
a state for discretes; blank entries are left alone. **Apply Scenario**
encodes the requested values into a fresh blank frame with sync words, or
into a copy of the current frame when *Start from current frame* is checked,
and writes every occurrence in every subframe. The results table then shows,
per parameter, the requested value, the raw bit pattern written, the value
the decoder read back, the difference, the quantization tolerance (half a
resolution step for analog and BCD, exact for discretes and raw), and
PASS/FAIL. This is the closed-loop check of spec §26: a value that cannot
survive encode → decode within one quantization step indicates a mapping or
conversion problem.

## Import

**ADB dataframes.** Import ADB…, Export ADB…, Load Demo Dataframe, New
Dataframe…. Import errors are shown in a message box with the parser's
record-level explanation.

**PDF dataframes (Experimental).** Import PDF… runs the pipeline of
[07 — PDF import](07-pdf-import.md) in the background with a progress dialog
and opens the review dialog when it finishes. The button is disabled with an
explanatory tooltip when PyMuPDF is not installed.

**Review queue.** Shows the state of an import that has not been published
yet ("12 rows — 11 approved, 1 need review, 0 excluded. 1 row(s) block
publishing.") and reopens its review dialog with **Open Review…**. After
publishing, the line reports what was loaded.

**Validation results.** The current dataframe's summary and issues.

## Step-by-step workflows

### Inspect a dataframe and a frame

1. File → Open ADB… and choose the file (or Import page → Load Demo
   Dataframe).
2. Dataframe tab: expand a parameter to see its mapping; read the details
   panel for the source record.
3. Simulator tab → Manual → Create Blank Frame (sync words inserted).
4. Frame View tab: double-click SF1 word 004, enter `D54` (HEX), OK. The
   inspector shows `PITCH ATT #1` decoding to −30.096 deg.
5. Parameters tab: filter Status = `VALID`, select the pitch row, read the
   trace.
6. Frame View → Save Frame… to keep the frame as JSON.

### Author a dataframe by hand

1. File → New Dataframe…; set the name, WPS and sync words.
2. Dataframe tab → Add Parameter…; fill the definition, add mapping rows,
   press Check, then OK.
3. Repeat; use Duplicate for similar parameters.
4. Export ADB… when done. The `*` marker disappears.

### Verify a mapping with a scenario

1. Load the dataframe, then Simulator → Scenario.
2. Enter target values (for example pitch −20, airspeed 150, gear DOWN).
3. Apply Scenario; every row should show PASS with a difference below the
   tolerance. A FAIL means the encoder and decoder disagree about the
   mapping, which points at overlapping fields, a wrong sign convention, or a
   segment order problem.

### Import a dataframe document (PDF)

1. File → Import PDF…, choose the document, wait for the progress dialog.
2. In the review dialog confirm WPS and sync words (Apply Metadata), then
   declare any conventions the importer flagged (Re-normalize).
3. Filter "Needs review", work through rows with Show Source…, Edit…, Approve
   Selected or Exclude.
4. Publish to Workspace. The dataframe is now loaded and marked unexported;
   Export ADB… writes it.

The review dialog is described in detail in
[07 — PDF import](07-pdf-import.md#the-review-dialog).
