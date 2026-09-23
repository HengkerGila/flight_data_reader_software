"""Bounded time-series history (spec v2 §26N, §53.9)."""

from __future__ import annotations

from arinc717_reader.streaming.parameter_sample import ParameterSample
from arinc717_reader.streaming.timeseries_store import TimeSeriesStore


def sample(pid: str, t: float, value, unit="kt") -> ParameterSample:
    return ParameterSample(pid, pid.upper(), t, 0, 1, 1, 0, value, value, unit, "VALID")


def test_ring_buffer_bounded_by_count():
    store = TimeSeriesStore(max_samples=5, max_seconds=1e9)
    store.append([sample("a", float(t), t) for t in range(10)])
    times, values = store.window("a")
    assert times == [5.0, 6.0, 7.0, 8.0, 9.0]
    assert values == [5.0, 6.0, 7.0, 8.0, 9.0]
    assert store.info("a").count == 5


def test_ring_buffer_bounded_by_duration():
    store = TimeSeriesStore(max_samples=1000, max_seconds=3.0)
    store.append([sample("a", float(t), t) for t in range(10)])
    times, _ = store.window("a")
    assert times == [6.0, 7.0, 8.0, 9.0]


def test_window_and_stats():
    store = TimeSeriesStore()
    store.append([sample("a", float(t), t * 10) for t in range(10)])
    times, values = store.window("a", seconds=2.0)
    assert times == [7.0, 8.0, 9.0]
    stats = store.stats("a", seconds=2.0)
    assert (stats.count, stats.current, stats.minimum, stats.maximum) == (3, 90.0, 70.0, 90.0)
    assert abs(stats.average - 80.0) < 1e-9
    assert store.stats("missing").count == 0


def test_non_numeric_samples_are_not_stored_but_latest_is_kept():
    store = TimeSeriesStore()
    text = ParameterSample("d", "D", 1.0, 0, 1, 1, None, None, None, None, "INVALID_MAPPING")
    store.append([text])
    assert store.window("d") == ([], [])
    assert store.info("d").latest is text


def test_reconfigure_and_clear_notify():
    store = TimeSeriesStore(max_samples=10)
    events = []
    store.subscribe(events.append)
    store.append([sample("a", float(t), t) for t in range(10)])
    store.configure(max_samples=3)
    assert store.window("a")[0] == [7.0, 8.0, 9.0]
    store.clear("a")
    assert store.keys() == []
    assert [e["reason"] for e in events] == ["append", "configure", "clear"]
