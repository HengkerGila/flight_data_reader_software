"""Lightweight time-series line plot (spec v2 §26O, §26Q).

A QPainter widget with auto-ranging axes, a time window and several
series.  It draws only what ``set_series`` hands it and keeps no history:
the TimeSeriesStore owns the data (spec v2 §54 "graph widgets must not
own time-series state").  The Graphs page refreshes it from a timer, never
per sample, so acquisition and rendering stay decoupled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

SERIES_COLORS = (
    QColor(31, 119, 180),
    QColor(255, 127, 14),
    QColor(44, 160, 44),
    QColor(214, 39, 40),
    QColor(148, 103, 189),
    QColor(140, 86, 75),
    QColor(227, 119, 194),
    QColor(127, 127, 127),
)


@dataclass
class PlotSeries:
    label: str
    times: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    color: QColor | None = None


def _nice_step(span: float, target_ticks: int = 5) -> float:
    if span <= 0 or not math.isfinite(span):
        return 1.0
    raw = span / max(target_ticks, 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 2.5, 5, 10):
        step = factor * magnitude
        if step >= raw:
            return step
    return 10 * magnitude


class TimeSeriesPlot(QWidget):
    MARGIN_LEFT = 64
    MARGIN_RIGHT = 16
    MARGIN_TOP = 12
    MARGIN_BOTTOM = 32

    def __init__(self, parent=None):
        super().__init__(parent)
        self._series: list[PlotSeries] = []
        self._window: float | None = 30.0
        self._t_end: float | None = None
        self._unit: str = ""
        self._x_label: str = "elapsed time (s)"
        self._paused = False
        self._show_legend = False
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # -- data ------------------------------------------------------------

    def set_series(self, series: list[PlotSeries], t_end: float | None, unit: str = "") -> None:
        for i, item in enumerate(series):
            if item.color is None:
                item.color = SERIES_COLORS[i % len(SERIES_COLORS)]
        self._series = series
        self._t_end = t_end
        self._unit = unit
        self.update()

    def set_window(self, seconds: float | None) -> None:
        self._window = seconds
        self.update()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.update()

    def set_show_legend(self, show: bool) -> None:
        """Draw the legend even for a single series (used to show sample status)."""
        self._show_legend = show
        self.update()

    @property
    def window(self) -> float | None:
        return self._window

    # -- painting --------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        rect = QRectF(
            self.MARGIN_LEFT,
            self.MARGIN_TOP,
            max(10.0, self.width() - self.MARGIN_LEFT - self.MARGIN_RIGHT),
            max(10.0, self.height() - self.MARGIN_TOP - self.MARGIN_BOTTOM),
        )
        text_color = palette.text().color()
        grid_color = QColor(text_color)
        grid_color.setAlpha(40)
        axis_pen = QPen(text_color, 1)
        grid_pen = QPen(grid_color, 1, Qt.PenStyle.DashLine)
        metrics = QFontMetrics(painter.font())

        # -- x range
        t_end = self._t_end
        points = [(s.times, s.values) for s in self._series if s.times]
        if t_end is None and points:
            t_end = max(times[-1] for times, _ in points)
        if not points or t_end is None:
            painter.setPen(axis_pen)
            painter.drawRect(rect)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "no samples yet")
            return
        if self._window is not None:
            t_start = t_end - self._window
        else:
            t_start = min(times[0] for times, _ in points)
            if t_end - t_start < 1e-9:
                t_start = t_end - 1.0
        x_span = max(t_end - t_start, 1e-9)

        # -- y range (auto, with 5 % headroom)
        visible: list[float] = []
        for times, values in points:
            for t, v in zip(times, values):
                if t_start <= t <= t_end:
                    visible.append(v)
        if not visible:
            visible = [values[-1] for _, values in points]
        y_min, y_max = min(visible), max(visible)
        if y_max - y_min < 1e-9:
            pad = abs(y_max) * 0.05 or 1.0
            y_min, y_max = y_min - pad, y_max + pad
        else:
            pad = (y_max - y_min) * 0.05
            y_min, y_max = y_min - pad, y_max + pad
        y_span = y_max - y_min

        def sx(t: float) -> float:
            return rect.left() + (t - t_start) / x_span * rect.width()

        def sy(v: float) -> float:
            return rect.bottom() - (v - y_min) / y_span * rect.height()

        # -- grid + y ticks
        painter.setPen(grid_pen)
        y_step = _nice_step(y_span)
        tick = math.ceil(y_min / y_step) * y_step
        while tick <= y_max:
            y = sy(tick)
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(axis_pen)
            label = f"{tick:.6g}"
            painter.drawText(
                QRectF(0, y - metrics.height() / 2, self.MARGIN_LEFT - 6, metrics.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            tick += y_step
        # -- x ticks (elapsed seconds relative to t_end, negative to the left)
        x_step = _nice_step(x_span, 6)
        tick = -math.floor(x_span / x_step) * x_step
        while tick <= 0:
            x = sx(t_end + tick)
            if x >= rect.left() - 0.5:
                painter.setPen(grid_pen)
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                painter.setPen(axis_pen)
                painter.drawText(
                    QRectF(x - 40, rect.bottom() + 4, 80, metrics.height()),
                    Qt.AlignmentFlag.AlignHCenter,
                    f"{tick:.6g}",
                )
            tick += x_step
        painter.setPen(axis_pen)
        painter.drawRect(rect)
        painter.drawText(
            QRectF(rect.left(), rect.bottom() + 4 + metrics.height(), rect.width(), metrics.height()),
            Qt.AlignmentFlag.AlignRight,
            self._x_label,
        )
        if self._unit:
            painter.drawText(QRectF(4, 0, self.MARGIN_LEFT - 8, self.MARGIN_TOP + metrics.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, self._unit)

        # -- series
        painter.setClipRect(rect)
        for series in self._series:
            if not series.times:
                continue
            path = QPainterPath()
            started = False
            last_x = None
            for t, v in zip(series.times, series.values):
                if t < t_start - x_span * 0.02:
                    continue
                x, y = sx(t), sy(v)
                if not started:
                    path.moveTo(x, y)
                    started = True
                else:
                    path.lineTo(x, y)
                last_x = x
            pen = QPen(series.color, 1.6)
            painter.setPen(pen)
            painter.drawPath(path)
            if len(series.times) == 1 or (last_x is not None and len(series.times) < 4):
                painter.setBrush(series.color)
                painter.drawEllipse(QPointF(sx(series.times[-1]), sy(series.values[-1])), 3, 3)
        painter.setClipping(False)

        # -- legend
        if len(self._series) > 1 or self._paused or self._show_legend:
            x = rect.left() + 8
            y = rect.top() + 6
            for series in self._series:
                painter.setPen(QPen(series.color, 3))
                painter.drawLine(QPointF(x, y + metrics.height() / 2), QPointF(x + 18, y + metrics.height() / 2))
                painter.setPen(axis_pen)
                painter.drawText(QPointF(x + 24, y + metrics.ascent()), series.label)
                y += metrics.height() + 2
            if self._paused:
                painter.setPen(axis_pen)
                painter.drawText(QPointF(x, y + metrics.ascent()), "PAUSED (acquisition continues)")
