"""The user guide shown on the Help tab.

Plain HTML in the subset QTextBrowser renders.  Every page, button, menu
entry and dialog of the application is described here, followed by the
step-by-step workflows, the messages the application can show, and a
glossary.  Keep this in step with the widgets: the GUI smoke test checks
that the guide mentions every tab and toolbar button.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

FIGURE_WIDTH = 900  # display width of a screenshot in the guide (pixels)


@dataclass(frozen=True)
class HelpSection:
    anchor: str
    title: str
    level: int
    html: str


@dataclass(frozen=True)
class Figure:
    file: str  # relative to the image directory, e.g. "mockups/graphs.png"
    caption: str


def help_images_dir() -> Path:
    """Where the screenshots live: ``img/`` next to the source tree, or inside a
    PyInstaller bundle (the packaging spec copies the folder)."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root) / "img"
    return Path(__file__).resolve().parents[3] / "img"


# Screenshots shown under a section's heading, when the files are available.
# All were taken with the FDS81 dataframe (imported from the reference PDF)
# during a live stream from the virtual device.
FIGURES: dict[str, list[Figure]] = {
    "overview": [
        Figure(
            "mockups/frame_view.png",
            "The main window during a live stream: toolbar with the stream state, header line, tabs, "
            "the Frame View with the Parameter Bits panel and the Word Inspector, and the status bar.",
        )
    ],
    "getting-started": [
        Figure(
            "mockups/import.png",
            "The Import tab: Load Demo Dataframe (or Import ADB… / Import PDF…) is the first step; "
            "here after publishing a PDF import, with the validation results of the loaded dataframe.",
        )
    ],
    "frame-view": [
        Figure(
            "mockups/frame_view.png",
            "Frame View during a stream: SF1 and SF2 of the current frame received, SF3 and SF4 pending "
            "(still showing the previous frame's words), HEX representation.",
        )
    ],
    "parameter-bits": [
        Figure(
            "mockups/frame_view.png",
            "The Parameter Bits panel above the Word Inspector: the word behind the 28VDC Power Input "
            "sample, its twelve bits with the parameter's bits highlighted, and the assembled value.",
        )
    ],
    "parameters": [
        Figure(
            "mockups/parameters.png",
            "Parameters during a stream: one row per decoded sample refreshed in place, the selected "
            "row's decode trace underneath, and Show in Frame View at the right of the filter row.",
        )
    ],
    "graphs": [
        Figure(
            "mockups/graphs.png",
            "Graphs: a 30-second window of the AOAR parameter with the statistics box and the same-unit overlay list.",
        )
    ],
    "dataframe": [
        Figure(
            "mockups/dataframe.png",
            "Dataframe: metadata, the parameter tree, the details pane with mapping and provenance, and the validation issues.",
        )
    ],
    "hardware": [
        Figure(
            "mockups/hardware.png",
            "Hardware page connected to the virtual device and streaming: stream progress, the "
            "simulated-signal table, diagnostics, fault injection and the event log with the handshake replies.",
        ),
    ],
    "import": [
        Figure(
            "mockups/import.png",
            "Import after publishing a PDF import: the review queue line and the validation results of the loaded dataframe.",
        )
    ],
    "pdf-review": [
        Figure(
            "mockups/pdf_import_dialogue.png",
            "The PDF review dialog on the scanned 21-page document: document summary, metadata to confirm, "
            "conventions, the rows held for review with their reasons, and the verbatim cells of the selected row.",
        )
    ],
}


def available_figures(anchor: str, root: Path | None = None) -> list[Figure]:
    root = help_images_dir() if root is None else root
    return [figure for figure in FIGURES.get(anchor, []) if (root / figure.file).is_file()]


def figure_html(figure: Figure) -> str:
    return (
        f'<p><img src="{figure.file}" width="{FIGURE_WIDTH}"><br>'
        f"<i>{figure.caption}</i></p>"
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<table border="1" cellspacing="0" cellpadding="4"><tr>{head}</tr>{body}</table>'


HELP_SECTIONS: list[HelpSection] = [
    HelpSection(
        "overview",
        "Overview",
        1,
        """
<p>ARINC 717 Data Reader decodes flight-data-recorder frames with a
<b>dataframe</b> (the document that says which bits of which words hold which
parameter), shows the raw words and the engineering values side by side, streams
frames live from an STM32 hardware-in-the-loop board or from a built-in virtual
device, graphs parameters over time, records and replays sessions, and imports
dataframes from <code>.adb</code> files or from PDF dataframe documents.</p>

<p>Three kinds of data never mix:</p>
<ul>
<li><b>Dataframe</b>: the interpretation rules (parameters, mappings, conversions). Loaded, edited and exported on the Dataframe and Import pages.</li>
<li><b>Frame</b>: the raw 12-bit words, four subframes of WPS words each. Shown on the Frame View page; produced by the live stream, a replay, a random generator or a saved file.</li>
<li><b>Engineering values</b>: what the decoder derives from the two above. Shown on the Parameters page as a table and on the Graphs page over time.</li>
</ul>

<p>The tabs, left to right:</p>
"""
        + _table(
            ["Tab", "What it is for"],
            [
                ["Frame View", "The raw frame, word by word, with the Word Inspector and word editing."],
                ["Parameters", "Every decoded sample of the current frame with filters and the decode trace."],
                ["Graphs", "Live time-series plots of decoded parameters, with statistics and same-unit overlays."],
                ["Dataframe", "Browse, create, edit, validate and export the dataframe."],
                ["Hardware", "Serial connection, stream control, simulated signals, diagnostics and fault injection."],
                ["Import", "Load ADB files or the demo dataframe, import PDF documents, review queue, validation results."],
                ["Help", "This guide. Press F1 from anywhere."],
            ],
        ),
    ),
    HelpSection(
        "getting-started",
        "Getting started in two minutes",
        1,
        """
<p>Nothing needs to be plugged in: a virtual STM32 device runs inside the
application.</p>
<ol>
<li>Open the <b>Import</b> tab and press <b>Load Demo Dataframe</b> (or File → Open ADB… for your own file). The header now shows the dataframe name and its WPS.</li>
<li>Press <b>▶ Start stream</b> in the toolbar. With nothing connected yet this connects to the port selected on the Hardware page (the virtual device by default) and starts the stream.</li>
<li>Watch the <b>Frame View</b>: one subframe column refreshes every second, the header says which columns are current, and the header line reads <i>Source: SERIAL (live)</i>.</li>
<li>Open <b>Parameters</b> to see the decoded values update, and <b>Graphs</b> to plot one of them. Use the parameter box and the window box; tick other parameters with the same unit to overlay them.</li>
<li>Press <b>❚❚ Pause stream</b> to freeze the frame. Now the Frame View is editable again (double-click a cell). <b>▶ Start stream</b> resumes.</li>
<li>To record, use File → Record Session…, later File → Replay Session… plays it back through the same decoder and graphs.</li>
</ol>
<p>For a real board see <a href="#stm32">Connecting the STM32 board</a>.</p>
""",
    ),
    HelpSection(
        "window",
        "The main window",
        1,
        """
<h3>Header line</h3>
<p><b>Dataframe</b> (file name or name; a trailing <code>*</code> means edits or imported
content not yet exported), <b>WPS</b> of the dataframe, <b>Source</b> of the current frame
(<code>SERIAL</code>, <code>REPLAY</code>, <code>RAW RANDOM</code>, <code>MANUAL</code>, <code>FILE name.json</code>;
<i>(live)</i> is appended while a stream is being received) and the <b>Frame</b> index.</p>

<h3>Toolbar</h3>
"""
        + _table(
            ["Button", "What it does"],
            [
                [
                    "▶ Start stream",
                    "Starts the live stream. If nothing is connected it first connects with the port, baud and protocol chosen on the Hardware page. During a paused replay it resumes the replay. Start after a pause restarts the frame counter at 0.",
                ],
                [
                    "❚❚ Pause stream",
                    "Sends STOP to the device: the incoming stream stops, the last frame becomes a static frame that can be inspected and edited. During a replay it pauses the replay. Recording is not affected; it simply records nothing while paused.",
                ],
                ["state text", "LIVE port · frame · subframe while streaming, PAUSED while connected but stopped, REPLAY with position, or a hint when nothing is connected."],
            ],
        )
        + """
<h3>Status bar</h3>
<p><b>Source</b>, <b>DF</b> (VALID or the number of validation errors, plus warnings),
<b>WPS</b> of the frame (it can differ from the dataframe until a new frame arrives),
<b>Parameters</b> count, <b>Decode OK / FAIL</b> (samples with status VALID versus any
other status), <b>Stream</b> state and port, and <b>● REC</b> / <b>▶ REPLAY</b> while a
recording or replay is running.</p>
""",
    ),
    HelpSection(
        "menus",
        "Menus",
        2,
        _table(
            ["Menu entry", "What it does"],
            [
                ["File → New Dataframe…", "Creates an empty dataframe (name, WPS, sync words) to author by hand on the Dataframe page."],
                ["File → Open ADB…", "Loads a <code>.adb</code> dataframe file. Parse errors are shown with the record-level explanation."],
                ["File → Import PDF…", "Runs the PDF dataframe importer with a progress dialog, then opens the review dialog."],
                ["File → Export ADB…", "Writes the current dataframe as <code>.adb</code> in the AFDA layout and clears the unexported marker. Notes have no column in that format and are not exported."],
                ["File → Load Frame… / Save Frame…", "Reads or writes the current frame as a JSON file (the last frame of a paused stream can be saved)."],
                ["File → Record Session…", "Starts writing a <code>.a717session</code> file with every received subframe, every decoded sample and every stream event. Needs a connected device or a running replay."],
                ["File → Stop Recording", "Closes the session file; the status bar names it."],
                ["File → Replay Session…", "Plays a session file through the normal assembler, decoder and graphs, at the speed chosen in Replay Speed."],
                ["File → Pause Replay / Stop Replay", "Pause or end the replay. The toolbar Pause / Start buttons do the same while a replay runs."],
                ["File → Replay Speed", "0.25× to 10× real time for the next replay."],
                ["File → Quit", "Stops any stream, recording or replay, and exits."],
                ["Stream → Connect / Disconnect", "Same as the Hardware page buttons."],
                ["Stream → Start Stream / Pause Stream", "Same as the toolbar buttons."],
                ["Settings → Theme", "System default (the platform's own colours); light themes Light, Solarized Light, Sepia; dark themes Dark, Solarized Dark, Nord, Midnight Blue, Cockpit (black panel with green readouts and amber highlights), High Contrast. Applies at once to every page and is remembered for the next start."],
                ["Help → User Guide (F1)", "Opens this tab."],
                ["Help → About", "Version information."],
            ],
        ),
    ),
    HelpSection(
        "help-tab",
        "The Help tab",
        2,
        """
<p>This guide. The list on the left jumps to a section, the search box finds text
(<b>Enter</b> or <b>Find next</b> moves to the next match and wraps around), and
links inside the text jump to other sections. <b>F1</b> or Help → User Guide opens
it from any page. Screenshots appear under the section headings when the
application's <code>img</code> folder is present.</p>
""",
    ),
    HelpSection(
        "frame-view",
        "Frame View",
        1,
        """
<p>The canonical frame as a table: one row per word address (001 … WPS), one column per
subframe (SF1 … SF4). The value in every cell is a 12-bit word shown in the chosen
representation.</p>
"""
        + _table(
            ["Control", "What it does"],
            [
                ["Generate Frame (random)", "Replaces the frame with random words (source RAW RANDOM). Useful to exercise the decoder without a stream."],
                ["Clear Frame", "Replaces the frame with all-zero words (source MANUAL). Enter sync words and values by editing cells."],
                ["Load Frame… / Save Frame…", "Frame JSON files, the same as the File menu entries."],
                ["Representation", "BIN (12 digits), OCT (4 digits), DEC, HEX (3 digits). Changing it never changes the data."],
                ["Click a cell / arrow keys", "Shows the word in the Word Inspector on the right. The inspector follows the current cell, so after a click the arrow keys walk through the words and the inspector keeps up; the selected cell survives a new frame. The selected cell is highlighted and its whole row is tinted, so the same word address is easy to compare across SF1–SF4."],
                ["Double-click a cell", "Opens Edit Word. Refused while the stream is live: press ❚❚ Pause stream first."],
            ],
        )
        + """
<h3>While a stream is running</h3>
<p>Frames arrive one subframe per second. The column of the subframe that just arrived
is refreshed; the other columns keep showing the <b>value they had before</b>, so every
word always shows its last known value and one column changes per second. Column
headers say what each column holds:</p>
"""
        + _table(
            ["Header", "Meaning", "Cells"],
            [
                ["SF1 · received", "This subframe of the current frame has arrived.", "Current values, normal background."],
                ["SF2 · pending", "Not yet received in the current frame.", "Previous frame's values, normal background. <code>----</code> only before any data has ever arrived."],
                ["SF3 · missing", "The device skipped this subframe (a later one arrived first).", "Previous frame's values, orange tint."],
                ["SF3 · late", "Arrived after it had been marked missing.", "Current values, blue tint."],
                ["SF4 · invalid", "Received, but the sync word or the length was wrong.", "The received words, red tint; individual out-of-range words red."],
            ],
        )
        + """
<p>Hover a cell for the state. Press ❚❚ Pause stream to hold the frame; it then becomes
an ordinary static frame (editable, saveable). The Word Inspector works at any time.</p>
""",
    ),
    HelpSection(
        "word-inspector",
        "Word Inspector and Edit Word",
        2,
        """
<p><b>Word Inspector</b> (right side of the Frame View) shows the clicked word: subframe,
word address, the value in BIN / OCT / HEX / DEC, and every parameter mapped to that word
with its bit range, the extracted bits, the raw integer, the decoded decimal and the
engineering value with its status. It is the fastest way to see why a value decodes the
way it does.</p>

<p><b>Edit Word</b> (double-click a cell) shows the subframe and address, the current value,
and lets you type a new value in any representation (choose it in the box next to the
input). Out-of-range or malformed input keeps the dialog open with the reason. A committed
edit changes exactly that cell and re-decodes the frame immediately.</p>
""",
    ),
    HelpSection(
        "parameter-bits",
        "Parameter Bits panel",
        2,
        """
<p>Above the Word Inspector. It shows the raw words behind one decoded <b>sample</b>
chosen on the Parameters page (<b>Show in Frame View</b>, or double-click the row):</p>
<ul>
<li>A summary line: parameter, occurrence, subframe, type, the engineering value and the status.</li>
<li>One row per <b>segment</b>, in assembly order (the first row holds the most significant bits): subframe, word address, bit range, then the twelve bits of that word with the bits that belong to the parameter highlighted and the others dimmed, and the extracted bits with their value. A parameter built from several words (a coarse/fine altitude, a word pair) therefore shows all of its words stacked.</li>
<li>The assembled bits, the decoded decimal, the conversion and the engineering value.</li>
</ul>
<p>The words are also outlined in the frame table and the first one is selected, so the
Word Inspector shows it. Clicking a segment row selects that word instead. The panel follows
the frame: during a stream the bits change as the sample's subframe arrives, and after an
edit they update at once. <b>Clear</b> stops following the sample.</p>
""",
    ),
    HelpSection(
        "parameters",
        "Parameters",
        1,
        """
<p>One row per decoded <b>sample</b> of the current frame: a parameter mapped to all four
subframes has four rows, one mapped to SF2 only has one. Columns: mnemonic, description,
type, occurrence, subframe, raw integer, decoded decimal, resolution, offset, engineering
value, unit, source and status.</p>
"""
        + _table(
            ["Control", "What it does"],
            [
                ["Search", "Filters rows by mnemonic or description."],
                ["Type", "Show one canonical type only: analog signed / unsigned, BCD, discrete, raw, unknown."],
                ["Status", "Show one decode status only (VALID, INVALID_MAPPING, UNSUPPORTED_TYPE, …)."],
                ["Subframe", "Show samples of one subframe only."],
                ["Select a row", "The trace pane below shows the full decode trace: segments, bits, assembled raw value, sign handling, conversion, and the reason for a non-VALID status."],
                ["Show in Frame View / double-click a row", "Switches to the Frame View with the sample's words in the <a href=\"#parameter-bits\">Parameter Bits</a> panel: one row per word (segment) with its twelve bits and the segment's bits highlighted, the words outlined in the frame table, the first one selected."],
                ["Column widths", "Fixed until you drag a header edge; Description takes the remaining width. They do not change when values change."],
            ],
        )
        + """
<p>During a live stream the rows update as each subframe arrives: a value is published as
soon as its subframe is in, never before. During a replay the same happens with the
recorded subframes. The values change in place: the selected row, its trace and the
scroll position stay where they are, so a parameter can be watched with the arrow keys
while the stream runs. When the set of rows itself changes (another dataframe, a
parameter added or removed) the same sample is selected again.</p>
""",
    ),
    HelpSection(
        "graphs",
        "Graphs",
        1,
        """
<p>Time-series plots of decoded parameters. The page reads the bounded history that the
live decoder fills (10 minutes or 10,000 samples per parameter, whichever comes first)
and never decodes anything itself.</p>
"""
        + _table(
            ["Control", "What it does"],
            [
                ["Parameter", "Which parameter to plot; every numeric parameter of the dataframe is offered, discretes plot as 0 / 1."],
                ["Window", "10 seconds, 30 seconds, 1 minute, 5 minutes, 10 minutes or All buffered. The x axis is elapsed time relative to the newest sample (0 on the right)."],
                ["Pause graph / Resume graph", "Freezes the picture only. Acquisition, decoding and recording continue; resuming shows what happened meanwhile."],
                ["Clear history", "Empties the history of every parameter."],
                ["Selected parameter box", "Current value with unit, raw value, min / max / average over the window, the number of samples in the window, and the status of the latest sample."],
                ["Overlay (same unit only)", "Tick other parameters with the same engineering unit to draw them on the same axes, each in its own colour. Parameters with another unit are not offered."],
                ["Show sample status in legend", "Adds the latest status to each legend entry (<code>IAS · VALID</code>) and keeps the legend visible for a single series."],
            ],
        )
        + """
<p>Samples are only created when a subframe carrying the parameter arrives: a 0.25 Hz
parameter gets one point every four seconds and nothing is interpolated in between.
Timestamps are the moment the word was recorded (subframe start plus the word's position
in the second), so the plot reads in real time.</p>
""",
    ),
    HelpSection(
        "dataframe",
        "Dataframe",
        1,
        """
<p>The loaded dataframe as a tree: one row per parameter with type, unit, resolution,
offset and a summary of its mapping; the details pane shows the full mapping (occurrences,
segments, subframes, word, bits) and the provenance (where the definition came from: ADB
record, PDF page and cell, or manual entry).</p>
"""
        + _table(
            ["Control", "What it does"],
            [
                ["New Dataframe…", "Starts an empty dataframe; opens the metadata dialog for name, WPS and sync words."],
                ["Edit Metadata…", "Changes name, WPS, sync words and the superframe flag of the current dataframe."],
                ["Add Parameter… / Edit Parameter…", "Opens the parameter editor (double-click a row also edits)."],
                ["Duplicate", "Copies the selected parameter as a starting point for a similar one."],
                ["Remove", "Deletes the selected parameter after confirmation."],
                ["▲ / ▼", "Moves the selected parameter in the list (the order is kept in the export)."],
                ["Search", "Filters the tree by mnemonic or description."],
                ["Validate Dataframe", "Runs the structural, mapping and semantic checks and shows the issues (also listed on the Import page)."],
                ["Export ADB…", "Writes the dataframe as <code>.adb</code> in the AFDA layout (frame-absolute word numbers, state tables, BCD digit weights). A parameter that is unchanged since import is written back exactly as it was read; unknown fields are preserved."],
            ],
        )
        + """
<p>Every committed edit re-decodes the current frame at once, so the Parameters page shows
the effect immediately. Unexported changes are marked with <code>*</code> in the header and
the window title.</p>
""",
    ),
    HelpSection(
        "parameter-editor",
        "Metadata and parameter editor dialogs",
        2,
        """
<p><b>Metadata</b>: dataframe name, WPS (64, 128, 256, 512, 1024 or typed), the four sync
words (decimal or <code>0x</code> hexadecimal; they are what the stream synchroniser looks
for), and whether a superframe is present (recognised, not decoded).</p>

<p><b>Parameter editor</b>: mnemonic and description; the source type as written in the
document (it is turned into the canonical type exactly as the importer does); unit,
resolution and offset of the linear conversion; declared minimum and maximum; the true and
false state names for discretes. The mapping table has one row per <b>segment</b>:
occurrence, sequence within the occurrence, subframes (<code>1,3</code> or <code>1-4</code>),
word address, LSB and MSB (bits numbered 12 = most significant down to 1). <b>Add
Segment</b> adds a row to the current occurrence, <b>Add Occurrence</b> starts a new
independent sample of the same parameter, <b>Remove Row</b> deletes a row. <b>Check</b> runs
the validator and shows the verdict without saving; OK commits only when there is no
error.</p>
""",
    ),
    HelpSection(
        "hardware",
        "Hardware",
        1,
        """
<p>Everything about the serial link to the stream device: the real STM32 board through an
FTDI USB-to-UART cable, or the in-process virtual device.</p>
<h3>Connection</h3>
"""
        + _table(
            ["Control", "What it does"],
            [
                ["Port", "<code>VIRTUAL</code> (the built-in device) or any serial port pyserial found (COM7, /dev/ttyUSB0 …). Refresh rescans. You can also type a port name."],
                ["Baud", "9600 … 921600; the firmware uses 115200, 8 data bits, no parity, 1 stop bit."],
                ["Protocol", "SIM-A717 v1 continuous word stream (normal), or framed packets with sequence numbers and CRC for diagnostics."],
                ["Virtual speed", "Time factor for the virtual device only: 50× makes a 4-second frame arrive in 80 ms. Ignored for a real port."],
                ["Connect", "Opens the port and runs the handshake (STOP, PING, SET_WPS, SET_SYNC, SET_PROTOCOL, SET_MODE, INFO). WPS and sync words come from the loaded dataframe, so load one first. The device's INFO line is shown under the status."],
                ["Disconnect", "Stops the stream if needed and closes the port."],
                ["Start stream / Stop stream", "Same as the toolbar ▶ Start stream / ❚❚ Pause stream."],
                ["Reset device", "Sends RESET and re-applies WPS, sync words, protocol and signal mode; clears the counters."],
                ["Status", "DISCONNECTED, CONNECTING, CONNECTED, STREAMING, RECONNECTING (after a lost link the application retries every half second and resumes the stream) or REPLAYING."],
            ],
        )
        + """
<h3>Stream</h3>
<p>WPS in use, the frame index being assembled, the subframe being received with the
state of all four slots, the word count within that subframe, the measured word rate
(with the virtual device this is the true rate times the speed factor) and the
synchroniser state (SEARCHING or LOCKED; if the sync words repeat at another standard
spacing the detected WPS is shown).</p>
""",
    ),
    HelpSection(
        "signals",
        "Simulated signals",
        2,
        """
<p>The <b>Source</b> box decides what the device streams:</p>
<ul>
<li><b>PC: engineering signals</b> (default). The application generates an engineering value per parameter and second, encodes it through the loaded dataframe and uploads the frame to the device, which streams it. Values are therefore coherent with the dataframe and the graphs show meaningful curves.</li>
<li><b>Device: raw random walk / raw random / fixed frame</b>. The device produces words on its own. Raw words decode to arbitrary engineering values; these modes exist to exercise the parser and the Frame View.</li>
</ul>
<p>The table lists every parameter that can be encoded, one row each:</p>
"""
        + _table(
            ["Column", "Meaning"],
            [
                ["Mode", "Fixed, Uniform Random, Random Walk (default), Sine, Ramp, Step, Scripted."],
                ["Low / High", "Bounds for every mode. Defaults are the declared limits clipped to what the bit field can encode."],
                ["Value", "The Fixed value, the Random Walk start, the Step start."],
                ["Period (s)", "Sine period, Ramp duration, Step interval, in seconds of simulated time."],
                ["Script (t:v, …)", "Points for the Scripted mode, e.g. <code>0:0, 10:20, 20:0</code> (seconds:value, linear interpolation). A malformed script turns red with a tooltip and the previous points are kept."],
            ],
        )
        + """
<p>Edits apply to the next uploaded frame. A value the mapping cannot encode is reported
once per stream as <code>ENCODE_ERROR</code> in the Stream events.</p>
""",
    ),
    HelpSection(
        "faults",
        "Diagnostics, fault injection and stream events",
        2,
        """
<p><b>Diagnostics</b>: bytes, words, subframes and frames received, sync losses, invalid
words and subframes, dropped and out-of-order subframes, bad packets (framed mode) and
discarded bytes. Reset device clears them.</p>

<p><b>Fault injection</b> asks the device to misbehave a given number of times (0 = off,
65535 = continuously) so that the error handling can be seen:</p>
"""
        + _table(
            ["Fault", "Effect on the receiving side"],
            [
                ["DROP_WORD", "One word missing: the following sync word is off by one → STREAM_SYNC_LOST, the subframe is INVALID, re-lock within two subframes."],
                ["CORRUPT_WORD", "One word with flipped bits; if it is a sync word, a sync loss."],
                ["DROP_SUBFRAME", "A whole subframe missing → sync loss, the slot shows as invalid or missing."],
                ["BAD_CRC", "Framed mode only: a packet with a wrong CRC is discarded → bad packet, the slot is missing."],
                ["DELAY_SUBFRAME", "A 0.5 s gap before a subframe; the rate dips."],
                ["OUT_OF_ORDER_SUBFRAME", "SF3 is sent before SF2 → SF2 is marked missing, then late."],
                ["WRONG_WPS", "One subframe at another WPS → sync loss and STREAM_RATE_MISMATCH."],
                ["PAUSE_STREAM", "Two seconds of silence; the rate goes to zero, nothing is lost."],
                ["DISCONNECT_RECONNECT", "Virtual device only: the link drops, the application reconnects and restarts the stream."],
            ],
        )
        + """
<p><b>Stream events</b> is the log of the last 200 events with time and message: handshake
replies, stream started / stopped, sync locked / lost, rate mismatch, incomplete subframes
or frames, faults injected, device reset, reconnects, encode errors, replay start and end.
Errors are red, warnings orange.</p>
""",
    ),
    HelpSection(
        "import",
        "Import",
        1,
        _table(
            ["Group", "Controls"],
            [
                ["ADB dataframes", "<b>Import ADB…</b> loads a file; <b>Export ADB…</b> writes the current dataframe; <b>Load Demo Dataframe</b> loads the bundled 256 WPS example; <b>New Dataframe…</b> starts an empty one."],
                ["PDF dataframes — Experimental", "<b>Import PDF…</b> extracts the parameter table of a dataframe document: born-digital pages through the PDF text, scanned pages through grid detection on the page image with the embedded text layer or OCR (if the OCR package is installed). Runs in the background with a progress dialog and then opens the review dialog."],
                ["Review queue", "State of an import that has not been published yet, with <b>Open Review…</b> to reopen its dialog."],
                ["Validation results", "Summary and issues of the current dataframe."],
            ],
        ),
    ),
    HelpSection(
        "pdf-review",
        "PDF review dialog",
        2,
        """
<p>Nothing extracted from a PDF reaches the workspace without review. The dialog shows:</p>
<ul>
<li><b>Document</b>: file, pages, tables found, how many rows are approved, need review or are excluded.</li>
<li><b>Dataframe metadata</b>: WPS and sync words as detected; confirm or change them and press <b>Apply Metadata</b> (rows are re-validated against the WPS).</li>
<li><b>Document conventions</b>: how the document writes frequencies (Hz or seconds), the bit layout of MSB/LSB columns, word pairs, the order of an "offset, resolution" pair, blank or zero subframe meaning every subframe, decimal comma. Change them and press <b>Re-normalize</b> to reinterpret every row (approvals are reset).</li>
<li>The <b>row table</b> with state and reason. <b>Show</b> filters (all, needs review, approved, excluded …) and the search box matches reasons and issue text. Selecting a row shows its verbatim cells, the interpretation, the validation issues and the review history below.</li>
</ul>
"""
        + _table(
            ["Button", "What it does"],
            [
                ["Edit…", "Opens the parameter editor on the row; the corrected definition replaces the extraction."],
                ["Approve Selected", "Approves the selected rows (multi-select works: filter, then Ctrl+A or Shift+click). Only rows the filter shows are affected, never hidden ones; the button says how many. Rows with validation errors cannot be approved until edited."],
                ["Exclude", "Leaves the selected rows out of the published dataframe (toggle; again only the visible rows, the button says how many)."],
                ["Show Source…", "Renders the page region the row came from, with the row outlined; row-with-context or whole page, fit to width or zoom."],
                ["Approve All Validated", "Approves every row that normalised and validated without any issue."],
                ["Publish", "Loads the approved rows as the working dataframe (marked unexported until Export ADB). Refused while any row blocks publishing; the reason is shown."],
            ],
        ),
    ),
    HelpSection(
        "recording",
        "Recording and replay",
        1,
        """
<p>A session file (<code>.a717session</code>) keeps the dataframe identity, WPS, sync words,
port and protocol settings, the simulator configuration, and then every received subframe
with its timestamp and state, every decoded sample, and every stream event. It is written
line by line, so a file cut short by a crash still replays up to its last complete line.</p>
<ol>
<li>Connect (and usually start the stream). File → <b>Record Session…</b>, choose a file. The status bar shows <b>● REC</b>.</li>
<li>File → <b>Stop Recording</b>. The status bar names the file.</li>
<li>File → <b>Replay Session…</b> (choose <b>Replay Speed</b> first if you want). The recorded subframes go through the same synchroniser-free assembler, decoder, Parameters page and Graphs as live data; the header shows <i>Source: REPLAY</i>, the toolbar shows the position.</li>
<li><b>❚❚ Pause stream</b> / <b>▶ Start stream</b> (or File → Pause Replay) pause and resume; File → <b>Stop Replay</b> ends it. Replay stops by itself at the end of the file.</li>
</ol>
<p>Replay refuses a file recorded at a different WPS than the loaded dataframe, and logs
a warning when the dataframe name differs. Replay cannot run while recording, and a
device cannot be connected while a replay runs.</p>
""",
    ),
    HelpSection(
        "stm32",
        "Connecting the STM32 board",
        1,
        """
<p>The board is a stream <i>emulator</i>: it produces the same 12-bit words the software
expects from a recorder, over a plain UART. It is not an ARINC 717 electrical interface.</p>
<ol>
<li><b>Flash the firmware</b> from <code>firmware/stm32f103_sim_a717</code> (<code>make</code>, then <code>make flash</code> with an ST-Link or <code>make flash-serial PORT=…</code> with the ROM bootloader). The README in that folder has the details.</li>
<li><b>Wire</b> STM32 PA9 (TX) → FTDI RXD, PA10 (RX) ← FTDI TXD, GND ↔ GND. 3.3 V logic. Never RS-232 levels.</li>
<li>On the <b>Hardware</b> page press <b>Refresh</b>, pick the FTDI port, keep 115200 baud and the continuous word stream protocol, and press <b>Connect</b>. The Stream events log shows the handshake replies (<code>+OK STOPPED</code>, <code>+OK PONG</code>, …) and the status turns CONNECTED with the board's INFO line.</li>
<li>Press <b>▶ Start stream</b>. The board's LED lights while it streams; the Frame View fills one column per second.</li>
</ol>
<p>If Connect fails with <i>no reply to STOP within 1.5 s</i>: the port opened but nothing
answered. Check that the firmware is flashed and running, that TX and RX are crossed and
GND is shared, that the baud rate is 115200, and that the right port is selected. A
terminal program sending <code>PING</code> and a line end must get <code>+OK PONG</code>
back from a working board.</p>
""",
    ),
    HelpSection(
        "troubleshooting",
        "Messages and what to do",
        1,
        _table(
            ["Message / state", "Meaning", "What to do"],
            [
                ["MISSING_DATAFRAME", "A stream or a decode needs the dataframe (WPS, sync words, parameters).", "Load a dataframe (Import → Load Demo Dataframe or File → Open ADB…)."],
                ["SERIAL_PORT_ERROR: cannot open …", "The port does not exist, is in use, or lacks permission.", "Refresh the port list, close other programs using it; on Linux add yourself to the dialout group."],
                ["no reply to STOP within 1.5 s", "The port opened but no SIM-A717 device answered.", "See Connecting the STM32 board: firmware, wiring, baud, port."],
                ["INVALID_SIM_PROTOCOL", "The device answered, but not as SIM-A717 v1, or rejected a setting.", "Check the firmware version and the WPS (the firmware supports up to 512)."],
                ["SERIAL_DISCONNECTED / RECONNECTING", "The link dropped during the stream.", "Nothing: the application reconnects every half second and resumes. Disconnect to stop trying."],
                ["STREAM_SYNC_LOST", "A sync word was not where the last lock predicted it.", "One-off after a fault is normal. Continuous: wrong sync words in the dataframe, noise, or the wrong WPS."],
                ["STREAM_RATE_MISMATCH", "Sync words repeat at another standard WPS spacing.", "The device and the dataframe disagree on WPS; fix one of them and Reset device."],
                ["SUBFRAME_INCOMPLETE / FRAME_INCOMPLETE", "A subframe was invalid, or a frame closed with missing subframes.", "Look at the Frame View tints and the diagnostics; inject faults deliberately to learn the patterns."],
                ["ENCODE_ERROR", "A simulated signal value does not fit the parameter's bit field.", "Lower High / raise Low for that parameter in Simulated signals."],
                ["REPLAY_ERROR", "The session file is unreadable or was recorded at another WPS.", "Load the dataframe the session was recorded with."],
                ["INVALID_WORD: the frame is being received live", "Editing a cell while the stream runs.", "Press ❚❚ Pause stream, then edit."],
                ["Decode status other than VALID", "The sample could not be decoded (bad mapping, unsupported type, out-of-range BCD digit …).", "Select the row on the Parameters page and read the trace; fix the parameter on the Dataframe page."],
                ["DF: n ERRORS", "The dataframe has validation errors (overlapping bits, words beyond WPS, missing mapping …).", "Dataframe → Validate Dataframe lists them; edit the parameters."],
            ],
        ),
    ),
    HelpSection(
        "glossary",
        "Glossary",
        1,
        _table(
            ["Term", "Meaning"],
            [
                ["Word", "One 12-bit ARINC 717 data word, value 0 … 4095."],
                ["WPS", "Words per second, also the number of words in each subframe (64, 128, 256, 512, 1024)."],
                ["Subframe", "One second of words. Four subframes (SF1 … SF4) make a frame of four seconds."],
                ["Sync word", "The fixed value at word address 1 of each subframe; the four values rotate SF1 … SF4 and are how the stream is aligned."],
                ["Word address", "Position of a word within its subframe, 1 … WPS."],
                ["Bit numbering", "12 is the most significant bit, 1 the least significant. LSB and MSB in a mapping name a bit range."],
                ["Parameter", "A measured quantity (IAS, pitch …) with a type, a conversion and a mapping."],
                ["Occurrence", "One independent sample position of a parameter (a parameter recorded twice per second has two occurrences)."],
                ["Segment", "One contiguous bit range in one word; an occurrence made of several segments is assembled in sequence order."],
                ["Resolution / offset", "engineering = raw × resolution + offset for analog and BCD parameters."],
                ["Signed / unsigned analog, BCD, discrete, raw", "The canonical parameter types: two's-complement, plain binary, binary-coded decimal, on/off with named states, and untouched bits."],
                ["Dataframe", "The complete set of parameter definitions plus WPS and sync words; stored as <code>.adb</code> or imported from a PDF."],
                ["Engineering value", "The decoded, converted value in its unit, with a status telling whether the decode was clean."],
                ["SIM-A717 v1", "The project's own serial protocol between the PC and the stream emulator: 12-bit words in 16-bit little-endian containers plus ASCII commands."],
                ["Session", "A recorded stream: subframes, samples and events in a <code>.a717session</code> file."],
            ],
        ),
    ),
]


def render_html(images_dir: Path | None = None) -> str:
    parts = ["<h1>ARINC 717 Data Reader — User Guide</h1>", "<p>Contents:</p><ul>"]
    for section in HELP_SECTIONS:
        indent = "&nbsp;&nbsp;&nbsp;&nbsp;" if section.level > 1 else ""
        parts.append(f'<li>{indent}<a href="#{section.anchor}">{section.title}</a></li>')
    parts.append("</ul>")
    for section in HELP_SECTIONS:
        tag = "h2" if section.level == 1 else "h3"
        parts.append(f'<a name="{section.anchor}"></a><{tag}>{section.title}</{tag}>')
        for figure in available_figures(section.anchor, images_dir):
            parts.append(figure_html(figure))
        parts.append(section.html)
    return "\n".join(parts)
