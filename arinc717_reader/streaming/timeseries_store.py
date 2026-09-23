"""Bounded per-parameter time series (spec v2 §26N).

One ring buffer per parameter id, bounded by sample count and by duration;
when full the oldest sample goes first.  The store is the single owner of
history: graph widgets read windows from it and never keep their own.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from ..state.base import Observable
from .parameter_sample import ParameterSample

DEFAULT_MAX_SAMPLES = 10_000
DEFAULT_MAX_SECONDS = 600.0


@dataclass
class SeriesInfo:
    parameter_id: str
    parameter_name: str
    unit: str | None
    count: int
    latest: ParameterSample | None


@dataclass
class SeriesStats:
    count: int
    current: float | None
    minimum: float | None
    maximum: float | None
    average: float | None


class _Series:
    __slots__ = ("times", "values", "samples", "name", "unit", "latest")

    def __init__(self, maxlen: int, name: str, unit: str | None):
        self.times: deque[float] = deque(maxlen=maxlen)
        self.values: deque[float] = deque(maxlen=maxlen)
        self.samples = 0
        self.name = name
        self.unit = unit
        self.latest: ParameterSample | None = None


class TimeSeriesStore(Observable):
    def __init__(
        self,
        max_samples: int = DEFAULT_MAX_SAMPLES,
        max_seconds: float = DEFAULT_MAX_SECONDS,
    ):
        super().__init__()
        self.max_samples = max_samples
        self.max_seconds = max_seconds
        self._series: dict[str, _Series] = {}

    # -- configuration ---------------------------------------------------

    def configure(self, max_samples: int | None = None, max_seconds: float | None = None) -> None:
        if max_samples is not None and max_samples != self.max_samples:
            self.max_samples = max_samples
            for key, series in list(self._series.items()):
                fresh = _Series(max_samples, series.name, series.unit)
                fresh.times.extend(list(series.times)[-max_samples:])
                fresh.values.extend(list(series.values)[-max_samples:])
                fresh.samples = series.samples
                fresh.latest = series.latest
                self._series[key] = fresh
        if max_seconds is not None:
            self.max_seconds = max_seconds
            for series in self._series.values():
                self._trim(series)
        self._notify({"type": "timeseries", "reason": "configure"})

    # -- input -----------------------------------------------------------

    def append(self, samples: list[ParameterSample]) -> None:
        """Bus subscriber: store every sample with a numeric value."""
        touched: set[str] = set()
        for sample in samples:
            value = sample.numeric_value
            series = self._series.get(sample.parameter_id)
            if series is None:
                series = _Series(self.max_samples, sample.parameter_name, sample.unit)
                self._series[sample.parameter_id] = series
            series.latest = sample
            if value is None or not math.isfinite(value):
                continue
            series.times.append(sample.timestamp)
            series.values.append(value)
            series.samples += 1
            touched.add(sample.parameter_id)
        for key in touched:
            self._trim(self._series[key])
        if touched:
            self._notify({"type": "timeseries", "reason": "append", "keys": touched})

    def _trim(self, series: _Series) -> None:
        if not series.times:
            return
        cutoff = series.times[-1] - self.max_seconds
        while series.times and series.times[0] < cutoff:
            series.times.popleft()
            series.values.popleft()

    def clear(self, parameter_id: str | None = None) -> None:
        if parameter_id is None:
            self._series.clear()
        else:
            self._series.pop(parameter_id, None)
        self._notify({"type": "timeseries", "reason": "clear"})

    # -- output ----------------------------------------------------------

    def keys(self) -> list[str]:
        return list(self._series.keys())

    def info(self, parameter_id: str) -> SeriesInfo | None:
        series = self._series.get(parameter_id)
        if series is None:
            return None
        return SeriesInfo(parameter_id, series.name, series.unit, len(series.times), series.latest)

    def unit(self, parameter_id: str) -> str | None:
        series = self._series.get(parameter_id)
        return series.unit if series else None

    def window(
        self, parameter_id: str, seconds: float | None = None, now: float | None = None
    ) -> tuple[list[float], list[float]]:
        """(times, values) for the last ``seconds`` (None = everything buffered)."""
        series = self._series.get(parameter_id)
        if series is None or not series.times:
            return [], []
        if seconds is None:
            return list(series.times), list(series.values)
        end = series.times[-1] if now is None else now
        cutoff = end - seconds
        times = list(series.times)
        values = list(series.values)
        # Binary search would be faster, but windows are small relative to 10k.
        start = 0
        for i, t in enumerate(times):
            if t >= cutoff:
                start = i
                break
        else:
            return [], []
        return times[start:], values[start:]

    def stats(self, parameter_id: str, seconds: float | None = None, now: float | None = None) -> SeriesStats:
        _times, values = self.window(parameter_id, seconds, now)
        if not values:
            return SeriesStats(0, None, None, None, None)
        return SeriesStats(
            count=len(values),
            current=values[-1],
            minimum=min(values),
            maximum=max(values),
            average=sum(values) / len(values),
        )
