"""Word Inspector (design spec §9).

Diagnostic panel exposing the full decoding chain of a clicked cell: raw
word in all representations, and for every mapped parameter the bit range,
extracted bits, encoding, decoded decimal, conversion, and engineering
value.  If several parameters use the same word, all are shown.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from ...domain.engineering import format_engineering_value
from ..common import monospace_font
from ..representation import all_representations

RULE = "─" * 34


class WordInspectorWidget(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._current: tuple[int, int] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("WORD INSPECTOR")
        layout.addWidget(title)
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(monospace_font())
        layout.addWidget(self._text)
        self._render()

        ctx.frame_store.subscribe(lambda event: self._render())
        ctx.engineering_store.subscribe(lambda event: self._render())

    def show_word(self, subframe: int, word: int) -> None:
        self._current = (subframe, word)
        self._render()

    def _render(self) -> None:
        frame = self._ctx.frame_store.frame
        if self._current is None:
            self._text.setPlainText(
                "Click a Frame View cell, or move to one with the arrow keys, to inspect it."
            )
            return
        if frame is None:
            self._text.setPlainText("No frame loaded.")
            return
        subframe, word = self._current
        if not (1 <= subframe <= 4 and 1 <= word <= frame.wps):
            self._text.setPlainText("Selected cell is outside the current frame.")
            return
        value = frame.word(subframe, word)
        reps = all_representations(value)
        lines = [
            RULE,
            f"Subframe       : {subframe}",
            f"Word Address   : {word:03d}",
            "",
            "Raw Word",
            f"BIN            : {reps['BIN']}",
            f"OCT            : {reps['OCT']}",
            f"HEX            : {reps['HEX']}",
            f"DEC            : {reps['DEC']}",
            "",
            "Mapped Parameters",
            RULE,
        ]
        lines.extend(self._parameter_lines(subframe, word))
        self._text.setPlainText("\n".join(lines))

    def _parameter_lines(self, subframe: int, word: int) -> list[str]:
        dataframe = self._ctx.dataframe_store.dataframe
        if dataframe is None:
            return ["No dataframe loaded."]
        mappings = dataframe.parameters_at(subframe, word)
        if not mappings:
            return ["No parameters mapped to this word."]
        lines: list[str] = []
        for parameter, occurrence, segment in mappings:
            sample = self._find_sample(parameter.id, occurrence.index, subframe)
            lines.append(f"{parameter.mnemonic}  (occurrence {occurrence.index})")
            lines.append("")
            lines.append(f"Bit Range      : {segment.msb}-{segment.lsb}")
            if sample is not None:
                seg_trace = next(
                    (
                        s
                        for s in sample.trace.segments
                        if s.word == word and s.subframe == subframe
                    ),
                    None,
                )
                if seg_trace and seg_trace.extracted_bits is not None:
                    lines.append(f"Extracted Bits : {seg_trace.extracted_bits}")
                lines.append("")
                lines.append(f"Encoding       : {parameter.parameter_type}")
                lines.append(f"Bit Width      : {sample.trace.bit_width}")
                lines.append("")
                lines.append(f"Decoded Decimal: {sample.decoded_decimal}")
                if sample.trace.resolution is not None:
                    lines.append(f"Resolution     : {sample.trace.resolution:g}")
                    lines.append(f"Offset         : {sample.trace.offset:g}")
                engineering = format_engineering_value(sample.engineering_value)
                unit = f" {sample.unit}" if sample.unit else ""
                lines.append("")
                lines.append(f"Engineering    : {engineering}{unit}")
                lines.append(f"Status         : {sample.status}")
                if sample.trace.message:
                    lines.append(f"Message        : {sample.trace.message}")
            else:
                lines.append(f"Encoding       : {parameter.parameter_type}")
                lines.append("(not decoded — no engineering values yet)")
            lines.append(RULE)
        return lines

    def _find_sample(self, parameter_id: str, occurrence_index: int, subframe: int):
        for value in self._ctx.engineering_store.values:
            if (
                value.parameter_id == parameter_id
                and value.occurrence_index == occurrence_index
                and value.subframe == subframe
            ):
                return value
        return None
