"""Alert construction and validation.

Two paths produce alerts.json, both schema-valid:
- LLM path: the fast model phrases and groups the threshold facts (validated,
  one retry on invalid output);
- mechanical path: the same facts rendered by code, used whenever the LLM is
  unavailable or returns junk twice.

Either way the FACTS are Python's; alerts are never invented.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict

from skywatch.features.enso import EnsoStatus
from skywatch.features.thresholds import ThresholdEvent
from skywatch.features.vortex import VortexStatus
from skywatch.models import Alert, Severity

# Human phrasing for metric identifiers — raw variable names never reach a
# title or detail; Hermes gets them in `metric`/`sources` fields instead.
_METRIC_PHRASES = {
    "wind_gusts_10m_max": "Gusts to {v} mph",
    "snowfall_sum": "Snow up to {v} cm",
    "precipitation_sum": "Rain up to {v} mm",
    "temperature_2m_max_anomaly": "{v:+} °C against normal",
    "ensemble_frost_probability": "Frost risk {v}%",
    "ensemble_snow_probability": "Snow risk {v}%",
}


def _humanize(metric: str, value: float, unit: str) -> str:
    phrase = _METRIC_PHRASES.get(metric)
    if phrase:
        return phrase.format(v=value)
    return f"{metric.replace('_', ' ')} {value} {unit}".strip()


def _span_text(d0: object, d1: object) -> str:
    from datetime import date as _date

    assert isinstance(d0, _date) and isinstance(d1, _date)
    if d0 == d1:
        return d0.strftime("%a %d %b")
    return f"{d0.strftime('%a %d')}–{d1.strftime('%a %d %b')}"


class AlertList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alerts: list[Alert]


def parse_llm_alerts(text: str) -> list[Alert]:
    """Validate LLM output against the schema. Raises on any deviation."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    payload: Any = json.loads(cleaned)
    if isinstance(payload, list):  # tolerate a bare array
        payload = {"alerts": payload}
    return AlertList.model_validate(payload).alerts


def mechanical_alerts(
    events: list[ThresholdEvent],
    enso: EnsoStatus | None = None,
    vortex: VortexStatus | None = None,
) -> list[Alert]:
    """Deterministic alerts straight from the facts. No LLM involved."""
    alerts: list[Alert] = []

    # Group consecutive days of the same category into one alert.
    by_cat: dict[str, list[ThresholdEvent]] = defaultdict(list)
    for e in sorted(events, key=lambda e: (e.category, e.date)):
        by_cat[e.category].append(e)

    for cat, evs in by_cat.items():
        runs: list[list[ThresholdEvent]] = [[evs[0]]]
        for e in evs[1:]:
            if (e.date - runs[-1][-1].date).days == 1:
                runs[-1].append(e)
            else:
                runs.append([e])
        for run in runs:
            worst = max(run, key=lambda e: abs(e.value))
            severity = (
                Severity.HIGH if any(e.severity == "high" for e in run) else Severity.MODERATE
            )
            span = _span_text(run[0].date, run[-1].date)
            agreement = worst.agreement or "n/a"
            conf = 0.5
            if "/" in agreement:
                num, den = agreement.split("/")
                conf = round(min(0.9, int(num) / int(den)), 2)
            agreeing = (
                f"{agreement} models agree" if "/" in agreement
                else f"from the {worst.source} data"
            )
            alerts.append(
                Alert(
                    severity=severity,
                    category=cat,
                    title=f"{_humanize(worst.metric, worst.value, worst.unit)}, {span}",
                    detail=(
                        f"{_humanize(worst.metric, worst.value, worst.unit)} on {span}, "
                        f"beyond the {worst.threshold} {worst.unit} alert threshold; "
                        f"{agreeing}."
                    ),
                    confidence=conf,
                    valid_from=run[0].date,
                    valid_to=run[-1].date,
                    sources=sorted({m for e in run for m in e.models}) or [worst.source],
                )
            )

    alerts.extend(_driver_alerts(enso, vortex))
    alerts.sort(key=lambda a: (a.valid_from, a.category))
    return alerts


def _driver_alerts(enso: EnsoStatus | None, vortex: VortexStatus | None) -> list[Alert]:
    """Global-driver alerts: only for genuinely notable states."""
    out: list[Alert] = []
    if enso and abs(enso.anomaly) >= 1.5:
        out.append(
            Alert(
                severity=Severity.LOW,
                category="enso",
                title=f"ENSO: {enso.classification}, {enso.anomaly:+.1f} °C",
                detail=(
                    f"Nino 3.4 anomaly {enso.anomaly:+.1f}°C for week {enso.week}, "
                    f"trend {enso.trend}"
                    + (f" ({enso.four_week_change:+.1f}°C over 4 weeks)"
                       if enso.four_week_change is not None else "")
                    + ". Seasonal background signal, not a short-range forecast."
                ),
                confidence=0.9,
                valid_from=enso.week,
                valid_to=enso.week,
                sources=["nino34"],
            )
        )
    if vortex and vortex.in_season:
        notable = vortex.state.startswith(("reversed", "weak")) or vortex.reversal_forecast
        if notable:
            sev = Severity.HIGH if "reversed" in vortex.state else Severity.MODERATE
            title = (
                "SSW underway: stratospheric wind reversed"
                if "reversed" in vortex.state
                else "Stratospheric vortex weak"
            )
            if vortex.reversal_forecast and vortex.reversal_date:
                title = f"SSW watch: reversal forecast {vortex.reversal_date}"
            out.append(
                Alert(
                    severity=sev,
                    category="stratosphere",
                    title=title,
                    detail=(
                        f"u60N@10hPa {vortex.current_u:+.1f} m/s "
                        f"(normal {vortex.climate_normal:+.1f}), trend {vortex.trend}, "
                        f"forecast minimum {vortex.forecast_min_u:+.1f} m/s. SSW events "
                        f"often precede NW-European cold outbreaks by 2-6 weeks."
                    ),
                    confidence=0.7,
                    valid_from=vortex.date,
                    valid_to=vortex.date,
                    sources=["u60n_10hpa"],
                )
            )
    return out
