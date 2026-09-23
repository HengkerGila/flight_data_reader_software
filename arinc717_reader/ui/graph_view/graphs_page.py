"""Graphs page (spec v2 §26O, §26P, §46A).

Reads windows from the TimeSeriesStore on a refresh timer (10 Hz) and
never decodes anything itself.  Pausing freezes the view only: samples
keep arriving in the store.  Overlays are restricted to parameters that
share the selected parameter's engineering unit.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...domain.engineering import format_engineering_value
from ..common import monospace_font
from .plot_widget import PlotSeries, TimeSeriesPlot

WINDOWS = (
    ("10 seconds", 10.0),
    ("30 seconds", 30.0),
    ("1 minute", 60.0),
    ("5 minutes", 300.0),
    ("10 minutes", 600.0),
    ("All buffered", None),
)
REFRESH_MS = 100


class GraphsPage(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._paused = False
        self._frozen_t_end: float | None = None
        self._dirty = True

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Parameter:"))
        self._parameter_combo = QComboBox()
        self._parameter_combo.setMinimumWidth(260)
        self._parameter_combo.currentIndexChanged.connect(self._parameter_changed)
        controls.addWidget(self._parameter_combo)
        controls.addWidget(QLabel("Window:"))
        self._window_combo = QComboBox()
        for label, _seconds in WINDOWS:
            self._window_combo.addItem(label)
        self._window_combo.setCurrentIndex(1)
        self._window_combo.currentIndexChanged.connect(self._window_changed)
        controls.addWidget(self._window_combo)
        self._pause_button = QPushButton("Pause graph")
        self._pause_button.setCheckable(True)
        self._pause_button.toggled.connect(self._toggle_pause)
        controls.addWidget(self._pause_button)
        clear = QPushButton("Clear history")
        clear.clicked.connect(self._clear_history)
        controls.addWidget(clear)
        controls.addStretch(1)
        layout.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._plot = TimeSeriesPlot()
        self._plot.set_window(30.0)
        splitter.addWidget(self._plot)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        stats_box = QGroupBox("Selected parameter")
        form = QFormLayout(stats_box)
        self._current = QLabel("—")
        self._raw = QLabel("—")
        self._min = QLabel("—")
        self._max = QLabel("—")
        self._avg = QLabel("—")
        self._count = QLabel("—")
        self._status = QLabel("—")
        for label in (self._current, self._raw, self._min, self._max, self._avg, self._count, self._status):
            label.setFont(monospace_font())
        form.addRow("Current:", self._current)
        form.addRow("Raw:", self._raw)
        form.addRow("Min:", self._min)
        form.addRow("Max:", self._max)
        form.addRow("Average:", self._avg)
        form.addRow("Samples:", self._count)
        form.addRow("Status:", self._status)
        side_layout.addWidget(stats_box)

        overlay_box = QGroupBox("Overlay (same unit only)")
        overlay_layout = QVBoxLayout(overlay_box)
        self._overlay_list = QListWidget()
        self._overlay_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._overlay_list.itemChanged.connect(lambda _item: self._mark_dirty())
        overlay_layout.addWidget(self._overlay_list)
        side_layout.addWidget(overlay_box, 1)

        self._show_status = QCheckBox("Show sample status in legend")
        self._show_status.setChecked(True)
        self._show_status.toggled.connect(lambda _v: self._mark_dirty())
        side_layout.addWidget(self._show_status)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        ctx.timeseries_store.subscribe(self._on_timeseries_event)
        ctx.dataframe_store.subscribe(self._on_dataframe_event)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._rebuild_parameters()

    # -- helpers ---------------------------------------------------------

    @property
    def selected_parameter_id(self) -> str | None:
        return self._parameter_combo.currentData()

    def select_parameter(self, parameter_id: str) -> None:
        index = self._parameter_combo.findData(parameter_id)
        if index >= 0:
            self._parameter_combo.setCurrentIndex(index)

    def overlay_ids(self) -> list[str]:
        ids = []
        for row in range(self._overlay_list.count()):
            item = self._overlay_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                ids.append(item.data(Qt.ItemDataRole.UserRole))
        return ids

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _on_timeseries_event(self, event: dict) -> None:
        if event.get("reason") in ("clear", "configure") or self._parameter_combo.count() == 0:
            self._rebuild_parameters()
        elif event.get("keys") and any(
            self._parameter_combo.findData(k) < 0 for k in event["keys"]
        ):
            self._rebuild_parameters()
        self._dirty = True

    def _on_dataframe_event(self, event: dict) -> None:
        if event.get("type") == "dataframe":
            self._rebuild_parameters()

    def _numeric_parameters(self) -> list[tuple[str, str, str | None]]:
        """(id, label, unit) for every parameter that can produce numeric samples."""
        dataframe = self._ctx.dataframe_store.dataframe
        store = self._ctx.timeseries_store
        seen: dict[str, tuple[str, str | None]] = {}
        if dataframe is not None:
            for parameter in dataframe.parameters:
                if parameter.parameter_type == "unknown":
                    continue
                seen[parameter.id] = (parameter.mnemonic, parameter.unit)
        for key in store.keys():
            info = store.info(key)
            if info and key not in seen:
                seen[key] = (info.parameter_name, info.unit)
        return [(pid, name, unit) for pid, (name, unit) in seen.items()]

    def _rebuild_parameters(self) -> None:
        current = self.selected_parameter_id
        self._parameter_combo.blockSignals(True)
        self._parameter_combo.clear()
        for pid, name, unit in self._numeric_parameters():
            label = f"{name} [{unit}]" if unit else name
            self._parameter_combo.addItem(label, pid)
        self._parameter_combo.blockSignals(False)
        if current is not None:
            self.select_parameter(current)
        self._rebuild_overlays()
        self._dirty = True

    def _rebuild_overlays(self) -> None:
        selected = self.selected_parameter_id
        unit = None
        if selected is not None:
            for pid, _name, punit in self._numeric_parameters():
                if pid == selected:
                    unit = punit
        checked = set(self.overlay_ids())
        self._overlay_list.blockSignals(True)
        self._overlay_list.clear()
        for pid, name, punit in self._numeric_parameters():
            if pid == selected or punit != unit:
                continue
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if pid in checked else Qt.CheckState.Unchecked)
            self._overlay_list.addItem(item)
        self._overlay_list.blockSignals(False)

    # -- control handlers ------------------------------------------------

    def _parameter_changed(self, _index: int) -> None:
        self._rebuild_overlays()
        self._dirty = True
        self._refresh(force=True)

    def _window_changed(self, index: int) -> None:
        self._plot.set_window(WINDOWS[index][1])
        self._dirty = True
        self._refresh(force=True)

    def _toggle_pause(self, paused: bool) -> None:
        self._paused = paused
        self._pause_button.setText("Resume graph" if paused else "Pause graph")
        self._plot.set_paused(paused)
        if paused:
            self._frozen_t_end = self._latest_time()
        else:
            self._frozen_t_end = None
            self._dirty = True
            self._refresh(force=True)

    def _clear_history(self) -> None:
        self._ctx.timeseries_store.clear()
        self._dirty = True
        self._refresh(force=True)

    def _latest_time(self) -> float | None:
        store = self._ctx.timeseries_store
        latest = None
        for pid in [self.selected_parameter_id, *self.overlay_ids()]:
            if pid is None:
                continue
            times, _values = store.window(pid, None)
            if times:
                latest = times[-1] if latest is None else max(latest, times[-1])
        return latest

    # -- refresh ---------------------------------------------------------

    def _refresh(self, force: bool = False) -> None:
        if self._paused and not force:
            return
        if not self._dirty and not force:
            return
        self._dirty = False
        store = self._ctx.timeseries_store
        selected = self.selected_parameter_id
        window = self._plot.window
        t_end = self._frozen_t_end if self._paused else self._latest_time()
        series: list[PlotSeries] = []
        unit = ""
        show_status = self._show_status.isChecked()

        def legend_label(pid: str) -> str:
            info = store.info(pid)
            label = info.parameter_name if info else pid
            if show_status and info and info.latest is not None:
                label += f" · {info.latest.status}"
            return label

        if selected is not None:
            times, values = store.window(selected, window, now=t_end)
            info = store.info(selected)
            series.append(PlotSeries(label=legend_label(selected), times=times, values=values))
            unit = (info.unit if info and info.unit else "") or ""
            for pid in self.overlay_ids():
                o_times, o_values = store.window(pid, window, now=t_end)
                series.append(PlotSeries(label=legend_label(pid), times=o_times, values=o_values))
            stats = store.stats(selected, window, now=t_end)
            latest = info.latest if info else None
            self._current.setText(
                f"{format_engineering_value(latest.engineering_value)} {latest.unit or ''}".strip()
                if latest
                else "—"
            )
            self._raw.setText(
                "—" if latest is None or latest.raw_value is None else f"{latest.raw_value} (0x{latest.raw_value:03X})"
            )
            self._min.setText("—" if stats.minimum is None else f"{stats.minimum:.6g}")
            self._max.setText("—" if stats.maximum is None else f"{stats.maximum:.6g}")
            self._avg.setText("—" if stats.average is None else f"{stats.average:.6g}")
            self._count.setText(str(stats.count))
            self._status.setText(latest.status if latest else "—")
        else:
            for label in (self._current, self._raw, self._min, self._max, self._avg, self._count, self._status):
                label.setText("—")
        self._plot.set_show_legend(show_status)
        self._plot.set_series(series, t_end, unit)
