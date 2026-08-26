"""Digest assembly: the compact, pre-computed summary that is the LLM's ONLY input.

Every number in here was computed in Python. The LLM is instructed to never
invent numbers not present in this digest, so what goes in defines what the
briefing can honestly say. Kept to roughly a page of JSON.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from skywatch.config import Config
from skywatch.features.anomaly import DayAnomaly
from skywatch.features.disagreement import DisagreementResult
from skywatch.features.enso import EnsoStatus
from skywatch.features.spread import SpreadStats
from skywatch.features.thresholds import ThresholdEvent
from skywatch.features.trends import TrendDelta
from skywatch.features.vortex import VortexStatus
from skywatch.models import ModelForecast


def build_digest(
    cfg: Config,
    run_at: datetime,
    forecasts: list[ModelForecast],
    spreads: list[SpreadStats],
    disagreement: DisagreementResult,
    anomalies: list[DayAnomaly],
    enso: EnsoStatus,
    vortex: VortexStatus,
    events: list[ThresholdEvent],
    trends: list[TrendDelta],
    map_facts: list[str] | None = None,
    skill: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the run digest. Plain dict so it serialises 1:1 to digest.json."""
    days = _daily_rows(cfg, forecasts, spreads, disagreement, anomalies)
    return {
        "meta": {
            "location": cfg.location.name,
            "latitude": cfg.location.latitude,
            "longitude": cfg.location.longitude,
            "run_at": run_at.isoformat(),
            "briefing_days": cfg.briefing_days,
            "deterministic_models": cfg.models.deterministic,
            "ensemble_models": cfg.models.ensemble,
            "units": {
                "temperature": "°C", "precipitation": "mm",
                "snowfall": "cm", "wind_gusts": "mph", "vortex_wind": "m/s",
            },
        },
        "days": days,
        "uncertainty": _uncertainty_notes(cfg, forecasts, spreads, disagreement),
        "global_drivers": {
            "enso": enso.model_dump(mode="json"),
            "stratospheric_vortex": vortex.model_dump(mode="json"),
        },
        "regional_map_facts": map_facts or [],
        "model_skill": skill or {},
        "threshold_events": [e.model_dump(mode="json") for e in events],
        "forecast_trends": [
            t.model_dump(mode="json") for t in trends
            if t.delta is not None and abs(t.delta) >= 1.0
        ],
    }


def _spread_for(
    spreads: list[SpreadStats], variable: str
) -> SpreadStats | None:
    """Prefer the largest ensemble for the headline spread numbers."""
    candidates = [s for s in spreads if s.variable == variable]
    if not candidates:
        return None
    return max(candidates, key=lambda s: max((d.n_members for d in s.days), default=0))


def _daily_rows(
    cfg: Config,
    forecasts: list[ModelForecast],
    spreads: list[SpreadStats],
    disagreement: DisagreementResult,
    anomalies: list[DayAnomaly],
) -> list[dict[str, Any]]:
    dates: list[date] = forecasts[0].dates if forecasts else []
    anom_by_date = {a.date: a for a in anomalies}
    tmax_spread = _spread_for(spreads, "temperature_2m_max")
    tmin_spread = _spread_for(spreads, "temperature_2m_min")

    rows: list[dict[str, Any]] = []
    for i, d in enumerate(dates[: cfg.briefing_days]):
        row: dict[str, Any] = {"date": d.isoformat(), "weekday": d.strftime("%A")}

        if i < len(disagreement.days):
            dd = disagreement.days[i].variables
            for var, short in [
                ("temperature_2m_max", "tmax"),
                ("precipitation_sum", "precip"),
                ("wind_gusts_10m_max", "gusts"),
                ("snowfall_sum", "snow"),
            ]:
                vd = dd.get(var)
                if vd is None or vd.n_models == 0:
                    continue
                row[short] = {
                    "by_model": vd.values,
                    "n_models": vd.n_models,
                    "range": vd.range,
                    "divergence_flagged": vd.flagged,
                }

        for spread, key in [(tmax_spread, "tmax_ensemble"), (tmin_spread, "tmin_ensemble")]:
            if spread and i < len(spread.days) and spread.days[i].n_members > 0:
                ds = spread.days[i]
                row[key] = {
                    "median": ds.median, "p10": ds.p10, "p90": ds.p90,
                    "std": ds.std, "n_members": ds.n_members,
                    "spread_jump": ds.spread_jump,
                }

        a = anom_by_date.get(d)
        if a and a.tmax_anomaly is not None:
            row["vs_normal"] = {
                "tmax_anomaly_c": a.tmax_anomaly,
                "tmax_sigma": a.tmax_sigma,
                "normal_tmax": a.normal_tmax,
            }
        rows.append(row)
    return rows


def _uncertainty_notes(
    cfg: Config,
    forecasts: list[ModelForecast],
    spreads: list[SpreadStats],
    disagreement: DisagreementResult,
) -> dict[str, Any]:
    """Panel sizes, horizons, spread jumps — the honesty section."""
    horizons = {fc.model: fc.horizon_days() for fc in forecasts}
    jump_days: list[str] = []
    dates = forecasts[0].dates if forecasts else []
    for s in spreads:
        for i, day in enumerate(s.days):
            if day.spread_jump and i < len(dates):
                jump_days.append(f"{dates[i].isoformat()} ({s.variable}, {s.model})")

    panel_change_dates = [
        dates[i].isoformat() for i in disagreement.panel_changes if i < len(dates)
    ]
    return {
        "model_horizons_days": horizons,
        "panel_changes": panel_change_dates,
        "panel_change_note": (
            "Model count per day varies; a smaller panel agreeing is weaker evidence "
            "than the full panel agreeing."
        ),
        "ensemble_spread_jumps": sorted(set(jump_days)),
    }
