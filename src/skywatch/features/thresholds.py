"""Threshold events: deterministic alert facts.

Python decides WHAT is alertable; the LLM only phrases it. Each event carries
which models crossed the threshold and how many of the panel agreed, so the
briefing can distinguish "all models show a gale" from "GFS alone shows one".
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from skywatch.config import Thresholds
from skywatch.models import ModelForecast

if TYPE_CHECKING:
    from skywatch.features.anomaly import DayAnomaly


class ThresholdEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    category: str            # wind | snow | rain | heat | cold | frost_risk | snow_risk
    severity: str            # moderate | high
    metric: str
    value: float             # worst offending value (or probability %)
    threshold: float
    unit: str
    models: list[str] = []   # deterministic models that crossed it
    agreement: str = ""      # e.g. "2/3" of models reporting that day
    source: str = "deterministic"


def threshold_events(
    forecasts: list[ModelForecast],
    ensemble_probs: dict[tuple[str, date], float | None],
    th: Thresholds,
) -> list[ThresholdEvent]:
    """All threshold crossings from deterministic panels + ensemble probabilities."""
    events: list[ThresholdEvent] = []
    events.extend(_deterministic_events(forecasts, th))
    events.extend(_probability_events(ensemble_probs, th))
    events.sort(key=lambda e: (e.date, e.category))
    return events


def _panel_values(
    forecasts: list[ModelForecast], var: str
) -> dict[date, dict[str, float]]:
    """date -> {model: value} for every model reporting that variable that day."""
    out: dict[date, dict[str, float]] = {}
    for fc in forecasts:
        s = fc.series.get(var)
        if s is None:
            continue
        for i, d in enumerate(fc.dates):
            if i < len(s.values) and (v := s.values[i]) is not None:
                out.setdefault(d, {})[fc.model] = v
    return out


def _crossings(
    forecasts: list[ModelForecast],
    var: str,
    limit: float,
    *,
    category: str,
    severity: str,
    unit: str,
    below: bool = False,
) -> list[ThresholdEvent]:
    events: list[ThresholdEvent] = []
    for d, by_model in sorted(_panel_values(forecasts, var).items()):
        hits = {
            m: v for m, v in by_model.items() if (v < limit if below else v > limit)
        }
        if not hits:
            continue
        worst = min(hits.values()) if below else max(hits.values())
        events.append(
            ThresholdEvent(
                date=d,
                category=category,
                severity=severity,
                metric=var,
                value=round(worst, 1),
                threshold=limit,
                unit=unit,
                models=sorted(hits),
                agreement=f"{len(hits)}/{len(by_model)}",
            )
        )
    return events


def _deterministic_events(
    forecasts: list[ModelForecast], th: Thresholds
) -> list[ThresholdEvent]:
    events: list[ThresholdEvent] = []

    # Wind: high tier wins where both cross.
    high = _crossings(forecasts, "wind_gusts_10m_max", th.gust_mph_high,
                      category="wind", severity="high", unit="mph")
    high_dates = {e.date for e in high}
    moderate = [
        e for e in _crossings(forecasts, "wind_gusts_10m_max", th.gust_mph_moderate,
                              category="wind", severity="moderate", unit="mph")
        if e.date not in high_dates
    ]
    events.extend(high)
    events.extend(moderate)

    events.extend(_crossings(forecasts, "snowfall_sum", th.snowfall_cm,
                             category="snow", severity="moderate", unit="cm"))
    events.extend(_crossings(forecasts, "precipitation_sum", th.precip_mm_daily,
                             category="rain", severity="moderate", unit="mm"))
    return events


def _probability_events(
    probs: dict[tuple[str, date], float | None], th: Thresholds
) -> list[ThresholdEvent]:
    """Ensemble-probability events. Keys are ('frost'|'snow', date) -> percent."""
    limits = {"frost": th.frost_probability_pct, "snow": th.snow_probability_pct}
    events: list[ThresholdEvent] = []
    for (kind, d), p in probs.items():
        limit = limits.get(kind)
        if p is None or limit is None or p < limit:
            continue
        events.append(
            ThresholdEvent(
                date=d,
                category=f"{kind}_risk",
                severity="high" if p >= 2 * limit else "moderate",
                metric=f"ensemble_{kind}_probability",
                value=p,
                threshold=limit,
                unit="%",
                source="ensemble",
            )
        )
    return events


def anomaly_events(
    anomalies: list[DayAnomaly],
    th: Thresholds,
) -> list[ThresholdEvent]:
    """Heat/cold events where tmax departs from normal beyond the configured band."""
    events: list[ThresholdEvent] = []
    for a in anomalies:
        v = a.tmax_anomaly
        if v is None or abs(v) <= th.temp_anomaly_c:
            continue
        events.append(
            ThresholdEvent(
                date=a.date,
                category="heat" if v > 0 else "cold",
                severity="high" if abs(v) > 1.5 * th.temp_anomaly_c else "moderate",
                metric="temperature_2m_max_anomaly",
                value=v,
                threshold=th.temp_anomaly_c,
                unit="°C vs normal",
                source="anomaly",
            )
        )
    return events
