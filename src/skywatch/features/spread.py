"""Ensemble spread statistics.

Pure functions over EnsembleForecast. The LLM never sees raw members — only
these reductions.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from skywatch.models import EnsembleForecast

# Variables where negative values are physically meaningless model noise
# (GFS emits e.g. -0.1 cm snowfall; verified live).
_CLAMP_ZERO_VARS = {"snowfall_sum", "precipitation_sum"}

# Spread below this (in the variable's own unit) never triggers a jump flag:
# growth from 0.05 to 0.2 is a huge factor and meteorologically nothing.
_JUMP_FLOOR = 0.5


class DaySpread(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_members: int
    median: float | None = None
    std: float | None = None
    p10: float | None = None
    p90: float | None = None
    spread_jump: bool = False  # std grew sharply vs the previous day


class SpreadStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    variable: str
    unit: str
    days: list[DaySpread]


def _quantile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated quantile on a pre-sorted list."""
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _day_values(ef: EnsembleForecast, day: int) -> list[float]:
    clamp = ef.variable in _CLAMP_ZERO_VARS
    vals: list[float] = []
    for member in ef.members:
        if day < len(member) and (v := member[day]) is not None:
            vals.append(max(0.0, v) if clamp else v)
    return vals


def ensemble_stats(ef: EnsembleForecast, *, spread_jump_factor: float) -> SpreadStats:
    """Per-day median/std/percentiles plus the uncertainty-explosion flag."""
    days: list[DaySpread] = []
    prev_std: float | None = None
    for day in range(len(ef.dates)):
        vals = sorted(_day_values(ef, day))
        if not vals:
            days.append(DaySpread(n_members=0))
            prev_std = None
            continue
        std = round(statistics.stdev(vals), 3) if len(vals) > 1 else None
        jump = (
            std is not None
            and prev_std is not None
            and std >= _JUMP_FLOOR
            and std > prev_std * spread_jump_factor
        )
        days.append(
            DaySpread(
                n_members=len(vals),
                median=round(_quantile(vals, 0.5), 2),
                std=std,
                p10=round(_quantile(vals, 0.1), 2),
                p90=round(_quantile(vals, 0.9), 2),
                spread_jump=jump,
            )
        )
        prev_std = std
    return SpreadStats(model=ef.model, variable=ef.variable, unit=ef.unit, days=days)


def event_probability(
    ef: EnsembleForecast, predicate: Callable[[float], bool]
) -> list[float | None]:
    """Percent of members meeting the predicate per day; None where no members."""
    out: list[float | None] = []
    for day in range(len(ef.dates)):
        vals = _day_values(ef, day)
        if not vals:
            out.append(None)
            continue
        out.append(round(100.0 * sum(1 for v in vals if predicate(v)) / len(vals), 1))
    return out
