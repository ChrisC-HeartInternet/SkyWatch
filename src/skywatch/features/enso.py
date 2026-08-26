"""ENSO status: classification and recent trend from the weekly Nino 3.4 series."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from skywatch.models import EnsoWeek

# Weekly data arrives ~weekly; older than this means the feed has a problem.
_STALE_DAYS = 21
_TREND_EPSILON = 0.15  # degrees C over 4 weeks


class EnsoStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week: date
    sst: float
    anomaly: float
    classification: str
    four_week_change: float | None = None
    trend: str = "insufficient history"    # warming | cooling | steady
    recent_weeks: list[float] = []          # anomalies, oldest first
    stale: bool = False


def classify_enso(anomaly: float) -> str:
    """Standard ONI-style bins applied to the weekly anomaly."""
    mag = abs(anomaly)
    if mag < 0.5:
        return "neutral"
    phase = "El Nino" if anomaly > 0 else "La Nina"
    if mag < 1.0:
        strength = "weak"
    elif mag < 1.5:
        strength = "moderate"
    elif mag < 2.0:
        strength = "strong"
    else:
        strength = "very strong"
    return f"{strength} {phase}"


def enso_status(weeks: list[EnsoWeek], *, today: date) -> EnsoStatus:
    """Reduce the weekly series to current state + 4-week trend."""
    if not weeks:
        raise ValueError("no ENSO weeks supplied")
    ordered = sorted(weeks, key=lambda w: w.week)
    latest = ordered[-1]
    status = EnsoStatus(
        week=latest.week,
        sst=latest.sst,
        anomaly=latest.anomaly,
        classification=classify_enso(latest.anomaly),
        recent_weeks=[w.anomaly for w in ordered[-8:]],
        stale=(today - latest.week).days > _STALE_DAYS,
    )
    if len(ordered) >= 5:
        change = round(latest.anomaly - ordered[-5].anomaly, 2)
        status.four_week_change = change
        if abs(change) < _TREND_EPSILON:
            status.trend = "steady"
        else:
            status.trend = "warming" if change > 0 else "cooling"
    return status
