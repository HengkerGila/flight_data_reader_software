"""ParameterSample bus (spec v2 §26L).

Publishers hand over batches (one subframe's worth of samples at a time);
every subscriber receives the same list.  The bus is Qt-free and is only
ever driven from the GUI thread by the serial service's pump, so
subscribers need no locking.
"""

from __future__ import annotations

from collections.abc import Callable

from .parameter_sample import ParameterSample

SampleSubscriber = Callable[[list[ParameterSample]], None]


class ParameterSampleBus:
    def __init__(self) -> None:
        self._subscribers: list[SampleSubscriber] = []
        self.published = 0

    def subscribe(self, callback: SampleSubscriber) -> SampleSubscriber:
        self._subscribers.append(callback)
        return callback

    def unsubscribe(self, callback: SampleSubscriber) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def publish(self, samples: list[ParameterSample]) -> None:
        if not samples:
            return
        self.published += len(samples)
        for callback in list(self._subscribers):
            callback(samples)
