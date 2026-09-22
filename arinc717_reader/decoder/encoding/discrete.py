"""Discrete / status interpretation (design spec §18).

The dataframe controls the meaning: ``true_state`` labels a non-zero field,
``false_state`` labels zero.  Nothing here assumes 1 = active — an active-low
signal simply assigns its labels the other way around.
"""

from __future__ import annotations

DEFAULT_TRUE_STATE = "TRUE"
DEFAULT_FALSE_STATE = "FALSE"


class DiscreteError(ValueError):
    """Raised when a discrete state cannot be encoded."""


def decode_discrete(
    value: int, true_state: str | None = None, false_state: str | None = None
) -> str:
    if value:
        return true_state if true_state is not None else DEFAULT_TRUE_STATE
    return false_state if false_state is not None else DEFAULT_FALSE_STATE


def encode_discrete(
    state: str | bool | int,
    true_state: str | None = None,
    false_state: str | None = None,
) -> int:
    """Map a logical state (label string, bool, or 0/1) to a raw bit value."""
    if isinstance(state, bool):
        return 1 if state else 0
    if isinstance(state, int):
        if state in (0, 1):
            return state
        raise DiscreteError(f"integer discrete state must be 0 or 1, got {state}")
    text = state.strip()
    t = true_state if true_state is not None else DEFAULT_TRUE_STATE
    f = false_state if false_state is not None else DEFAULT_FALSE_STATE
    if text.upper() == t.strip().upper():
        return 1
    if text.upper() == f.strip().upper():
        return 0
    raise DiscreteError(f"state {state!r} matches neither {t!r} nor {f!r}")
