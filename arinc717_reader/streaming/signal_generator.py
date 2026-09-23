"""Engineering signal generators for the HIL simulator (spec v2 §26H).

Each generator answers "what is this parameter's engineering value at time
t?"  The PC encodes those values through the normal parameter encoder into
frames that are uploaded to the stream device, so simulated values stay
coherent with the dataframe ("Engineering Random").
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field

from ..domain.parameter import (
    TYPE_ANALOG_SIGNED,
    TYPE_BCD,
    TYPE_DISCRETE,
    TYPE_RAW,
    ParameterDefinition,
)
from ..encoder.parameter_encoder import is_encodable

MODE_FIXED = "Fixed"
MODE_UNIFORM = "Uniform Random"
MODE_RANDOM_WALK = "Random Walk"
MODE_SINE = "Sine"
MODE_RAMP = "Ramp"
MODE_STEP = "Step"
MODE_SCRIPTED = "Scripted"
SIGNAL_MODES = (
    MODE_FIXED,
    MODE_UNIFORM,
    MODE_RANDOM_WALK,
    MODE_SINE,
    MODE_RAMP,
    MODE_STEP,
    MODE_SCRIPTED,
)
DEFAULT_MODE = MODE_RANDOM_WALK


@dataclass
class SignalConfig:
    """Per-parameter signal settings; ``low``/``high`` bound every mode."""

    mode: str = DEFAULT_MODE
    low: float = 0.0
    high: float = 1.0
    value: float = 0.0          # Fixed / initial Random Walk / Step start
    period: float = 30.0        # seconds: Sine period, Ramp duration, Step interval
    step_size: float | None = None  # Random Walk max change per second (default 2 % of span)
    script: list[tuple[float, float]] = field(default_factory=list)  # (t, value) points
    enabled: bool = True

    @property
    def span(self) -> float:
        return self.high - self.low

    def clamp(self, value: float) -> float:
        lo, hi = min(self.low, self.high), max(self.low, self.high)
        return max(lo, min(hi, value))


def parse_script(text: str) -> list[tuple[float, float]]:
    """Parse scripted-scenario points written as ``time:value`` pairs.

    ``"0:0, 10:20, 20:0"`` → ``[(0.0, 0.0), (10.0, 20.0), (20.0, 0.0)]``.
    Pairs are separated by commas, semicolons or whitespace; the result is
    sorted by time.  Raises ``ValueError`` for a malformed pair.
    """
    points: list[tuple[float, float]] = []
    for token in re.split(r"[,;\s]+", text.strip()):
        if not token:
            continue
        if ":" not in token:
            raise ValueError(f"script point {token!r} must be written as time:value")
        t_text, v_text = token.split(":", 1)
        try:
            points.append((float(t_text), float(v_text)))
        except ValueError as exc:
            raise ValueError(f"script point {token!r}: {exc}") from exc
    return sorted(points)


def format_script(points) -> str:
    """Inverse of :func:`parse_script`."""
    return ", ".join(f"{t:g}:{v:g}" for t, v in sorted(points))


# Engineering values this close to the field's last raw step still round to
# it; anything beyond would round past the field and fail to encode.
_HALF_STEP = 0.499


def _bcd_max_decimal(width: int) -> int:
    """Largest decimal a BCD field of ``width`` bits holds (partial leading digit allowed)."""
    value, place, remaining = 0, 1, width
    while remaining > 0:
        slot = min(4, remaining)
        value += min(9, (1 << slot) - 1) * place
        place *= 10
        remaining -= slot
    return value


def representable_range(parameter: ParameterDefinition) -> tuple[float, float] | None:
    """Engineering range the parameter's mapping can actually encode.

    Derived from the total bit width of the first occurrence and the type's
    raw range, pushed through the linear conversion (spec §14–§17).  ``None``
    when the parameter has no mapping.  Declared min/max in a dataframe are
    often wider than the field (the demo's FLAP POS declares −2..45 deg but
    its 12-bit unsigned field with offset −1.6 holds −1.6..23.8), and a
    generated value outside this range cannot be encoded.
    """
    if not parameter.occurrences or not parameter.occurrences[0].segments:
        return None
    width = sum(s.width for s in parameter.occurrences[0].segments)
    if width <= 0:
        return None
    ptype = parameter.parameter_type
    if ptype == TYPE_ANALOG_SIGNED:
        raw_lo, raw_hi = -(1 << (width - 1)), (1 << (width - 1)) - 1
    elif ptype == TYPE_BCD:
        raw_lo, raw_hi = 0, _bcd_max_decimal(width)
    else:
        raw_lo, raw_hi = 0, (1 << width) - 1
    if ptype == TYPE_RAW:
        return float(raw_lo), float(raw_hi)
    resolution = parameter.conversion.resolution or 1.0
    offset = parameter.conversion.offset
    a = offset + (raw_lo - _HALF_STEP) * resolution
    b = offset + (raw_hi + _HALF_STEP) * resolution
    return min(a, b), max(a, b)


def default_config(parameter: ParameterDefinition) -> SignalConfig:
    """Default range: the declared limits, clipped to what the mapping can encode."""
    if parameter.parameter_type == TYPE_DISCRETE:
        return SignalConfig(mode=MODE_STEP, low=0.0, high=1.0, value=0.0, period=5.0)
    low = parameter.minimum
    high = parameter.maximum
    representable = representable_range(parameter)
    if low is None or high is None or high <= low:
        low, high = representable if representable is not None else (0.0, 1.0)
    elif representable is not None:
        low, high = max(low, representable[0]), min(high, representable[1])
        if high <= low:  # declared limits entirely outside the field
            low, high = representable
    mid = (low + high) / 2
    return SignalConfig(mode=DEFAULT_MODE, low=low, high=high, value=mid, period=30.0)


class SignalGenerator:
    """Stateful generator for one parameter."""

    def __init__(self, parameter: ParameterDefinition, config: SignalConfig, seed: int | None = None):
        self.parameter = parameter
        self.config = config
        self._rng = random.Random(seed)
        self._walk = config.clamp(config.value)
        self._last_t: float | None = None
        self._step_state = 0

    def reset(self) -> None:
        self._walk = self.config.clamp(self.config.value)
        self._last_t = None
        self._step_state = 0

    def value_at(self, t: float) -> float | str:
        cfg = self.config
        mode = cfg.mode
        if mode == MODE_FIXED:
            value = cfg.value
        elif mode == MODE_UNIFORM:
            value = self._rng.uniform(cfg.low, cfg.high)
        elif mode == MODE_RANDOM_WALK:
            value = self._random_walk(t)
        elif mode == MODE_SINE:
            period = cfg.period if cfg.period > 0 else 1.0
            mid = (cfg.low + cfg.high) / 2
            value = mid + cfg.span / 2 * math.sin(2 * math.pi * t / period)
        elif mode == MODE_RAMP:
            period = cfg.period if cfg.period > 0 else 1.0
            value = cfg.low + cfg.span * ((t % period) / period)
        elif mode == MODE_STEP:
            period = cfg.period if cfg.period > 0 else 1.0
            value = cfg.high if int(t // period) % 2 else cfg.low
        elif mode == MODE_SCRIPTED:
            value = self._scripted(t)
        else:
            raise ValueError(f"unknown signal mode {mode!r}")
        value = cfg.clamp(value)
        if self.parameter.parameter_type == TYPE_DISCRETE:
            return self._discrete_state(value)
        return value

    def _random_walk(self, t: float) -> float:
        cfg = self.config
        if self._last_t is None:
            self._last_t = t
            return self._walk
        dt = max(0.0, t - self._last_t)
        self._last_t = t
        step = cfg.step_size if cfg.step_size is not None else abs(cfg.span) * 0.02
        self._walk = cfg.clamp(self._walk + self._rng.uniform(-step, step) * max(dt, 0.0))
        return self._walk

    def _scripted(self, t: float) -> float:
        points = sorted(self.config.script)
        if not points:
            return self.config.value
        if t <= points[0][0]:
            return points[0][1]
        if t >= points[-1][0]:
            return points[-1][1]
        for (t0, v0), (t1, v1) in zip(points, points[1:]):
            if t0 <= t <= t1:
                if t1 == t0:
                    return v1
                return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
        return points[-1][1]

    def _discrete_state(self, value: float) -> str:
        parameter = self.parameter
        true_state = parameter.true_state or "TRUE"
        false_state = parameter.false_state or "FALSE"
        return true_state if value >= (self.config.low + self.config.high) / 2 else false_state


class ScenarioSignals:
    """Generators for every encodable parameter of a dataframe."""

    def __init__(self, dataframe, seed: int | None = None):
        self.dataframe = dataframe
        self._rng = random.Random(seed)
        self.generators: dict[str, SignalGenerator] = {}
        for parameter in dataframe.parameters:
            if is_encodable(parameter):
                self.generators[parameter.id] = SignalGenerator(
                    parameter, default_config(parameter), seed=self._rng.randrange(1 << 30)
                )

    def configure(self, parameter_id: str, config: SignalConfig) -> None:
        generator = self.generators.get(parameter_id)
        if generator is None:
            raise KeyError(parameter_id)
        generator.config = config
        generator.reset()

    def config(self, parameter_id: str) -> SignalConfig:
        return self.generators[parameter_id].config

    def values_at(self, t: float) -> dict[str, float | str]:
        return {
            pid: gen.value_at(t)
            for pid, gen in self.generators.items()
            if gen.config.enabled
        }

    def reset(self) -> None:
        for generator in self.generators.values():
            generator.reset()
