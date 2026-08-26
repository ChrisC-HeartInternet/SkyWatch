"""Forecast verification: how right has each model actually been?

Pure functions over (stored forecasts, observed weather). Every forecast row
Skywatch has ever recorded is compared with what ERA5 says happened, giving
per-model MAE and bias by lead-time bucket, a rank, and skill relative to the
panel median (the blend every individual model has to beat).

Honesty rules baked in: sample counts ride every number, ranks are flagged
provisional below a configurable n, and nothing is scored against a day that
has not been observed yet.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from skywatch.features.panel import PANEL_MODEL_NAME

# Lead-time buckets in days: short, medium, long, extended.
LEAD_BUCKETS: dict[str, tuple[int, int]] = {
    "0-2": (0, 2),
    "3-4": (3, 4),
    "5-7": (5, 7),
    "8+": (8, 99),
}


class BucketScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n: int
    mae: float
    bias: float                      # mean(forecast - observed); + = runs warm/wet/windy


class OverallScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n: int
    n_days: int = 0
    mae: float
    bias: float
    rank: int | None = None          # 1 = best MAE for this variable (incl. the median)
    vs_median_pct: float | None = None  # +20 = 20% lower MAE than the panel median
    provisional: bool = True


class VariableSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    buckets: dict[str, BucketScore]
    overall: OverallScore


class ModelSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variables: dict[str, VariableSkill]


class CurvePoint(BaseModel):
    """Error at one lead-time step, for the error-growth curve."""

    model_config = ConfigDict(extra="forbid")

    lead_hours: int          # bucket start, in hours
    n: int
    mae: float


class CycleScore(BaseModel):
    """Skill of one model's cycle hour (00Z/06Z/12Z/18Z)."""

    model_config = ConfigDict(extra="forbid")

    init_hour: int
    n: int
    mae: float


class SkillSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by_model: dict[str, ModelSkill]
    total_samples: int
    first_verified: date | None = None
    last_verified: date | None = None
    # Error-growth curve per model for daily max temperature, in CURVE_STEP_HOURS
    # buckets — the "harder further out" picture. Includes the climatology
    # baseline under CLIMATOLOGY_NAME when normals were supplied.
    curve: dict[str, list[CurvePoint]] = {}
    # Same variable: per model, per cycle hour (only for runs that recorded cycles).
    by_cycle: dict[str, list[CycleScore]] = {}
    # Lead (hours) at which each model's tmax MAE first exceeds climatology's, or
    # None if it never does within the data. "When to stop trusting it."
    beats_climatology_until_h: dict[str, int | None] = {}


CURVE_VARIABLE = "temperature_2m_max"
CURVE_STEP_HOURS = 12
CLIMATOLOGY_NAME = "climatology"


def _bucket_for(lead_days: int) -> str | None:
    for name, (lo, hi) in LEAD_BUCKETS.items():
        if lo <= lead_days <= hi:
            return name
    return None


def lead_hours(run_at: datetime, target: date) -> float:
    """Hours from issue time to the target day's midday (UTC) — a continuous lead."""
    midday = datetime(target.year, target.month, target.day, 12, tzinfo=run_at.tzinfo)
    return (midday - run_at).total_seconds() / 3600


def model_skill(
    rows: list[tuple[datetime, str, str, date, float]],
    observed: dict[date, dict[str, float]],
    *,
    provisional_below_n: int,
    provisional_below_days: int = 1,
    normals: dict[date, float] | None = None,
    cycles: dict[tuple[datetime, str], int] | None = None,
) -> SkillSummary:
    """Score every verifiable forecast row against observations.

    A rating stays provisional until it rests on BOTH enough samples and enough
    distinct verified days — errors within one day are correlated, so sample
    count alone overstates confidence.
    """
    # errors[model][variable][bucket] -> list of (forecast - observed)
    errors: dict[str, dict[str, dict[str, list[float]]]] = {}
    days_seen: dict[tuple[str, str], set[date]] = {}
    verified_dates: set[date] = set()
    # curve[model][lead_bucket_hours] -> abs errors (tmax only)
    curve_err: dict[str, dict[int, list[float]]] = {}
    cycle_err: dict[str, dict[int, list[float]]] = {}

    for run_at, model, variable, target, value in rows:
        truth = observed.get(target, {}).get(variable)
        if truth is None:
            continue
        lead = (target - run_at.date()).days
        bucket = _bucket_for(lead)
        if bucket is None or lead < 0:
            continue
        errors.setdefault(model, {}).setdefault(variable, {}).setdefault(
            bucket, []
        ).append(value - truth)
        days_seen.setdefault((model, variable), set()).add(target)
        verified_dates.add(target)
        if variable == CURVE_VARIABLE:
            lh = max(0.0, lead_hours(run_at, target))
            step = int(lh // CURVE_STEP_HOURS) * CURVE_STEP_HOURS
            curve_err.setdefault(model, {}).setdefault(step, []).append(abs(value - truth))
            if cycles and (hour := cycles.get((run_at, model))) is not None:
                cycle_err.setdefault(model, {}).setdefault(hour, []).append(abs(value - truth))
            if normals and (normal := normals.get(target)) is not None:
                # Climatology "forecast" is the same at every lead; score it on
                # the same (target, lead) pairs so the comparison is like for like.
                curve_err.setdefault(CLIMATOLOGY_NAME, {}).setdefault(step, []).append(
                    abs(normal - truth)
                )

    by_model: dict[str, ModelSkill] = {}
    total = 0
    for model, per_var in errors.items():
        variables: dict[str, VariableSkill] = {}
        for variable, per_bucket in per_var.items():
            buckets = {
                name: BucketScore(
                    n=len(errs),
                    mae=round(statistics.fmean(abs(e) for e in errs), 2),
                    bias=round(statistics.fmean(errs), 2),
                )
                for name, errs in per_bucket.items()
            }
            all_errs = [e for errs in per_bucket.values() for e in errs]
            total += len(all_errs)
            n_days = len(days_seen.get((model, variable), set()))
            variables[variable] = VariableSkill(
                buckets=buckets,
                overall=OverallScore(
                    n=len(all_errs),
                    n_days=n_days,
                    mae=round(statistics.fmean(abs(e) for e in all_errs), 2),
                    bias=round(statistics.fmean(all_errs), 2),
                    provisional=(
                        len(all_errs) < provisional_below_n
                        or n_days < provisional_below_days
                    ),
                ),
            )
        by_model[model] = ModelSkill(variables=variables)

    _rank_and_compare(by_model)
    curve = {
        m: [CurvePoint(lead_hours=h, n=len(e), mae=round(statistics.fmean(e), 2))
            for h, e in sorted(buckets.items())]
        for m, buckets in curve_err.items()
    }
    by_cycle = {
        m: [CycleScore(init_hour=h, n=len(e), mae=round(statistics.fmean(e), 2))
            for h, e in sorted(hours.items())]
        for m, hours in cycle_err.items()
    }
    return SkillSummary(
        by_model=by_model,
        total_samples=total,
        first_verified=min(verified_dates) if verified_dates else None,
        last_verified=max(verified_dates) if verified_dates else None,
        curve=curve,
        by_cycle=by_cycle,
        beats_climatology_until_h=_beats_until(curve),
    )


def _beats_until(curve: dict[str, list[CurvePoint]]) -> dict[str, int | None]:
    """First lead bucket where a model's MAE meets or exceeds climatology's.

    None means the model beat climatology at every lead we have data for; a
    model with no overlapping buckets is omitted.
    """
    clim = {p.lead_hours: p.mae for p in curve.get(CLIMATOLOGY_NAME, [])}
    out: dict[str, int | None] = {}
    for model, pts in curve.items():
        if model == CLIMATOLOGY_NAME:
            continue
        overlap = [p for p in pts if p.lead_hours in clim]
        if not overlap:
            continue
        crossed = next((p.lead_hours for p in overlap if p.mae >= clim[p.lead_hours]), None)
        out[model] = crossed
    return out


def _rank_and_compare(by_model: dict[str, ModelSkill]) -> None:
    """Per variable: rank all scorers by overall MAE; compare each to the median."""
    variables = {v for m in by_model.values() for v in m.variables}
    for variable in variables:
        scored = sorted(
            (
                (model, ms.variables[variable].overall)
                for model, ms in by_model.items()
                if variable in ms.variables
            ),
            key=lambda t: t[1].mae,
        )
        median_mae = next(
            (ov.mae for m, ov in scored if m == PANEL_MODEL_NAME), None
        )
        for rank, (_model, ov) in enumerate(scored, start=1):
            ov.rank = rank
            if median_mae:
                ov.vs_median_pct = round(100.0 * (median_mae - ov.mae) / median_mae, 1)
