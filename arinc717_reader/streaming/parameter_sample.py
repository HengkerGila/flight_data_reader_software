"""ParameterSample model (spec v2 §26K).

Samples carry the decode outcome plus timing.  ``timestamp`` is the wall
clock time (seconds since the epoch) the parameter was *recorded* at:
the subframe's start time plus the word's position in the subframe, since
one subframe always spans one second (§26B).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.engineering import EngineeringValue


@dataclass
class ParameterSample:
    parameter_id: str
    parameter_name: str
    timestamp: float
    frame_index: int
    subframe: int
    occurrence_index: int
    raw_value: int | None
    decoded_decimal: float | int | None
    engineering_value: float | int | str | None
    unit: str | None
    status: str

    @property
    def numeric_value(self) -> float | None:
        """The value to plot: engineering, or decoded decimal for discretes."""
        value = self.engineering_value
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(self.decoded_decimal, (int, float)):
            return float(self.decoded_decimal)
        return None


def sample_from_value(
    value: EngineeringValue,
    timestamp: float,
    frame_index: int,
) -> ParameterSample:
    return ParameterSample(
        parameter_id=value.parameter_id,
        parameter_name=value.parameter_name,
        timestamp=timestamp,
        frame_index=frame_index,
        subframe=value.subframe or 0,
        occurrence_index=value.occurrence_index,
        raw_value=value.raw_integer,
        decoded_decimal=value.decoded_decimal,
        engineering_value=value.engineering_value,
        unit=value.unit,
        status=value.status,
    )


def sample_time(subframe_end: float, first_word: int, wps: int) -> float:
    """Timestamp of a sample recorded at ``first_word`` (1-based) of a subframe.

    ``subframe_end`` is when the subframe's last word arrived; a subframe is
    one second long, so its first word was recorded one second earlier.
    """
    return subframe_end - 1.0 + (first_word - 1) / wps
