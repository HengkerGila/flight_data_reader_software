"""Minimal observable base for stores.

Deliberately Qt-free: subscribers are plain callables receiving an event
dict, so the domain/service layers never depend on the GUI framework.
"""

from __future__ import annotations

from collections.abc import Callable

Subscriber = Callable[[dict], None]


class Observable:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, callback: Subscriber) -> Subscriber:
        self._subscribers.append(callback)
        return callback

    def unsubscribe(self, callback: Subscriber) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def _notify(self, event: dict) -> None:
        for callback in list(self._subscribers):
            callback(event)
