"""Application services connecting stores, codec, decoder and encoder."""

from __future__ import annotations


class ServiceError(Exception):
    """User-facing operation failure with an explicit state name (spec §47)."""

    def __init__(self, state: str, message: str):
        self.state = state
        super().__init__(f"{state}: {message}")
