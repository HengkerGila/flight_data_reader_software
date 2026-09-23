"""Parameter Bits panel (Frame View, above the Word Inspector).

Shows the raw words behind one decoded sample chosen on the Parameters page:
one stacked row per segment (subframe, word address, bit range, the twelve
bits of the word with the segment's bits highlighted, the extracted value),
then the assembled bits, the decoded decimal and the engineering value.  A
multi-word parameter therefore shows every word it is built from, in
assembly order (first row = most significant).

The panel reads the stores only (engineering values carry the full decode
trace) and re-renders on their events, so the bits follow a live stream.
Clicking a row hands its word to the Frame View, which selects it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...domain.engineering import STATUS_OUT_OF_RANGE, STATUS_VALID, format_engineering_value
from ...domain.frame import WORD_BITS
from ..common import COLOR_ERROR, COLOR_OK, COLOR_WARNING, monospace_font
from ..parameter_view.engineering_model import SampleKey, sample_key

FIXED_COLUMNS = ("Seg", "SF", "Word", "Bits")
BIT_COLUMNS = tuple(str(bit) for bit in range(WORD_BITS, 0, -1))  # 12 .. 1
COLUMNS = (*FIXED_COLUMNS, *BIT_COLUMNS, "Value")  # Value = the extracted segment value
COL_SEQ, COL_SF, COL_WORD, COL_BITS = range(len(FIXED_COLUMNS))
FIRST_BIT_COLUMN = len(FIXED_COLUMNS)
COL_EXTRACTED = len(COLUMNS) - 1
FIXED_COLUMN_WIDTHS = {COL_SEQ: 34, COL_SF: 30, COL_WORD: 46, COL_BITS: 48}
BIT_COLUMN_WIDTH = 20
ROW_HEIGHT = 22
MAX_VISIBLE_ROWS = 6
SEGMENT_BIT_ALPHA = 90  # tint behind the bits that belong to the segment

HINT = (
    "Select a row on the Parameters tab and press Show in Frame View (or "
    "double-click it) to see the words and bits it is decoded from."
)


def bit_column(bit: int) -> int:
    """Table column of bit number ``bit`` (12 = most significant, leftmost)."""
    return FIRST_BIT_COLUMN + (WORD_BITS - bit)


class ParameterBitsWidget(QWidget):
    word_activated = Signal(int, int)  # (subframe, word) of a clicked segment row
    cells_changed = Signal(object)     # frozenset of (subframe, word) currently shown

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._key: SampleKey | None = None
        self._segments: list = []  # SegmentTrace of the rendered sample, in sequence order
        self._cells: frozenset[tuple[int, int]] = frozenset()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        top.addWidget(QLabel("PARAMETER BITS"))
        top.addStretch(1)
        self._clear = QPushButton("Clear")
        self._clear.setToolTip("Stop following this sample")
        self._clear.clicked.connect(lambda: self.show_sample(None))
        self._clear.hide()
        top.addWidget(self._clear)
        layout.addLayout(top)

        self._summary = QLabel(HINT)
        self._summary.setWordWrap(True)
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._summary)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(list(COLUMNS))
        self._table.setFont(monospace_font())
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(BIT_COLUMN_WIDTH)
        for column, width in FIXED_COLUMN_WIDTHS.items():
            self._table.setColumnWidth(column, width)
        for bit in range(1, WORD_BITS + 1):
            self._table.setColumnWidth(bit_column(bit), BIT_COLUMN_WIDTH)
        self._table.cellClicked.connect(self._row_activated)
        self._table.cellActivated.connect(self._row_activated)
        self._table.hide()
        layout.addWidget(self._table)

        self._assembled = QLabel()
        self._assembled.setFont(monospace_font())
        self._assembled.setWordWrap(True)
        self._assembled.setTextFormat(Qt.TextFormat.RichText)
        self._assembled.hide()
        layout.addWidget(self._assembled)

        ctx.frame_store.subscribe(lambda _event: self._render())
        ctx.engineering_store.subscribe(lambda _event: self._render())
        ctx.dataframe_store.subscribe(lambda _event: self._render())

    # -- API -------------------------------------------------------------------

    @property
    def key(self) -> SampleKey | None:
        return self._key

    def show_sample(self, key: SampleKey | None) -> None:
        """Follow the sample ``key`` (parameter id, occurrence, subframe); None clears."""
        self._key = tuple(key) if key is not None else None
        self._render()

    def cells(self) -> list[tuple[int, int]]:
        """(subframe, word) of every shown segment, in assembly order."""
        return [(segment.subframe, segment.word) for segment in self._segments]

    def segments(self) -> list[tuple[int, int, int, int, int]]:
        """(sequence, subframe, word, msb, lsb) per shown row (tests, scripts)."""
        return [
            (s.sequence, s.subframe, s.word, max(s.msb, s.lsb), min(s.msb, s.lsb))
            for s in self._segments
        ]

    def bit_text(self, row: int) -> str:
        """The twelve bit characters of ``row`` as shown, bit 12 first."""
        return "".join(
            self._table.item(row, bit_column(bit)).text() for bit in range(WORD_BITS, 0, -1)
        )

    def highlighted_bits(self, row: int) -> set[int]:
        """Bit numbers of ``row`` drawn as part of the segment."""
        return {
            bit
            for bit in range(1, WORD_BITS + 1)
            if self._table.item(row, bit_column(bit)).data(Qt.ItemDataRole.UserRole)
        }

    def activate_row(self, row: int) -> None:
        self._row_activated(row, 0)

    # -- rendering -------------------------------------------------------------

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self._render()

    def _row_activated(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._segments):
            segment = self._segments[row]
            self.word_activated.emit(segment.subframe, segment.word)

    def _find_value(self):
        key = self._key
        for value in self._ctx.engineering_store.values:
            if sample_key(value) == key:
                return value
        return None

    def _publish_cells(self, cells: frozenset[tuple[int, int]]) -> None:
        if cells != self._cells:
            self._cells = cells
            self.cells_changed.emit(cells)

    def _render(self) -> None:
        key = self._key
        if key is None:
            self._segments = []
            self._summary.setText(HINT)
            self._table.hide()
            self._assembled.hide()
            self._clear.hide()
            self._publish_cells(frozenset())
            return
        self._clear.show()
        dataframe = self._ctx.dataframe_store.dataframe
        parameter = dataframe.get_parameter(key[0]) if dataframe else None
        value = self._find_value()
        name = parameter.mnemonic if parameter else (value.parameter_name if value else key[0])
        subframe = f"SF{key[2]}" if key[2] is not None else "SF —"
        if value is None:
            self._segments = []
            self._summary.setText(
                f"<b>{name}</b>  occurrence {key[1]}  {subframe} — no decoded sample in the "
                "current frame (the parameter or the frame changed)"
            )
            self._table.hide()
            self._assembled.hide()
            self._publish_cells(frozenset())
            return

        trace = value.trace
        engineering = format_engineering_value(value.engineering_value)
        unit = f" {value.unit}" if value.unit else ""
        status_color = (
            COLOR_OK if value.status == STATUS_VALID
            else COLOR_WARNING if value.status == STATUS_OUT_OF_RANGE
            else COLOR_ERROR
        )
        ptype = parameter.parameter_type if parameter else "?"
        self._summary.setText(
            f"<b>{name}</b>  occurrence {value.occurrence_index}  {subframe}  {ptype}"
            f"  →  <b>{engineering}{unit}</b>  "
            f"<span style='color:{status_color.name()}'><b>{value.status}</b></span>"
            + (f"<br>{trace.message}" if trace.message else "")
        )

        self._segments = list(trace.segments)
        self._fill_table()
        self._table.setVisible(bool(self._segments))

        if trace.assembled_bits is not None:
            parts = [
                f"Assembled: <b>{trace.assembled_bits}</b> (width {trace.bit_width}) = {trace.assembled_value}"
            ]
            if trace.decoded_decimal is not None:
                parts.append(f"decoded {trace.decoded_decimal}")
            if trace.resolution is not None:
                parts.append(f"× {trace.resolution:g} + {trace.offset:g}")
            parts.append(f"= <b>{engineering}{unit}</b>")
            self._assembled.setText("  →  ".join(parts))
            self._assembled.show()
        else:
            self._assembled.hide()
        self._publish_cells(frozenset(self.cells()))

    def _fill_table(self) -> None:
        table = self._table
        palette = table.palette()
        tint = QColor(palette.highlight().color())
        tint.setAlpha(SEGMENT_BIT_ALPHA)
        dim = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        text = palette.color(QPalette.ColorRole.Text)
        bold = QFont(table.font())
        bold.setBold(True)
        plain = QFont(table.font())
        table.blockSignals(True)
        # Same number of rows: update the cells in place so a selected row in
        # this table survives the once-per-subframe refresh of a live stream.
        table.setRowCount(len(self._segments))
        for row, segment in enumerate(self._segments):
            lo, hi = min(segment.lsb, segment.msb), max(segment.lsb, segment.msb)
            self._set(row, COL_SEQ, f"#{segment.sequence}")
            self._set(row, COL_SF, str(segment.subframe))
            self._set(row, COL_WORD, f"{segment.word:03d}")
            self._set(row, COL_BITS, f"{hi}-{lo}")
            bits = format(segment.word_value, f"0{WORD_BITS}b") if segment.word_value is not None else None
            for bit in range(1, WORD_BITS + 1):
                inside = lo <= bit <= hi
                item = self._set(
                    row, bit_column(bit), bits[WORD_BITS - bit] if bits is not None else "·"
                )
                item.setData(Qt.ItemDataRole.UserRole, inside)
                item.setFont(bold if inside else plain)
                item.setForeground(text if inside else dim)
                item.setBackground(tint if inside else QColor(0, 0, 0, 0))
                item.setToolTip(
                    f"bit {bit}: " + ("part of segment" if inside else "not used by this parameter")
                )
            if segment.extracted_bits is not None:
                extracted, detail = (
                    str(segment.extracted_value),
                    f"extracted bits {segment.extracted_bits} = {segment.extracted_value}",
                )
            elif segment.word_value is None:
                extracted, detail = "—", "word not read"
            else:
                extracted, detail = "—", ""
            self._set(row, COL_EXTRACTED, extracted).setToolTip(detail)
        table.blockSignals(False)
        table.setFixedHeight(self._table_height())

    def _set(self, row: int, column: int, value: str) -> QTableWidgetItem:
        item = self._table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, column, item)
        if item.text() != value:
            item.setText(value)
        return item

    def _table_height(self) -> int:
        rows = min(self._table.rowCount(), MAX_VISIBLE_ROWS)
        height = self._table.horizontalHeader().height() + rows * ROW_HEIGHT
        height += 2 * self._table.frameWidth()
        scrollbar = self._table.horizontalScrollBar()
        if scrollbar.isVisible():
            height += scrollbar.height()
        return height

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if self._table.isVisible():
            self._table.setFixedHeight(self._table_height())
