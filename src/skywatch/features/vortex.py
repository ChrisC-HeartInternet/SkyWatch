"""Polar vortex analysis: SSW watch with an explicit seasonality gate.

A reversal (u60N@10hPa below 0 m/s) in winter is an SSW — often followed by
NW-European cold outbreaks 2-6 weeks later. In summer the stratosphere is
climatologically easterly, so the same number means nothing; the gate keeps
August from screaming SSW.
"""

from __future__ import annotations

import datetime as dt
from datetime import date

from pydantic import BaseModel, ConfigDict

from skywatch.config import VortexConfig
from skywatch.models import VortexSample
from skywatch.sources.vortex import climatology_for

_RAPID_WEAKENING_MS = 15.0  # drop over the forecast window that counts as rapid


class VortexStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date
    current_u: float
    climate_normal: float | None = None
    climate_anomaly: float | None = None
    in_season: bool
    state: str                    # normal | weak | reversed (SSW underway) | out of season...
    trend: str                    # strengthening | steady | weakening | weakening rapidly
    forecast_min_u: float | None = None
    reversal_forecast: bool = False
    reversal_date: dt.date | None = None
    forecast_u: list[float] = []  # daily u over the forecast window
    partial_coverage: bool = False


def _in_season(d: date, cfg: VortexConfig) -> bool:
    m, start, end = d.month, cfg.season_start_month, cfg.season_end_month
    return (m >= start or m <= end) if start > end else (start <= m <= end)


def analyse_vortex(
    samples: list[VortexSample],
    climatology: dict[int, float],
    cfg: VortexConfig,
    *,
    today: date,
) -> VortexStatus:
    if not samples:
        raise ValueError("no vortex samples")
    ordered = sorted(samples, key=lambda s: s.time)
    current = ordered[0]
    future = ordered[1:]

    normal = climatology_for(climatology, today)
    in_season = _in_season(today, cfg)

    status = VortexStatus(
        date=today,
        current_u=current.u_ms,
        climate_normal=normal,
        climate_anomaly=round(current.u_ms - normal, 1) if normal is not None else None,
        in_season=in_season,
        state="",
        trend="steady",
        forecast_u=[s.u_ms for s in ordered],
        forecast_min_u=round(min(s.u_ms for s in ordered), 2),
        partial_coverage=any(s.n_points < cfg.n_longitudes for s in ordered),
    )

    if not in_season:
        status.state = "out of season (summer easterlies are normal)"
        return status

    # State from the current value.
    if current.u_ms < cfg.reversal_ms:
        status.state = "reversed (SSW underway)"
    elif current.u_ms < cfg.weak_ms:
        status.state = "weak"
    else:
        status.state = "normal"

    # Trend over the forecast window.
    if future:
        drop = current.u_ms - min(s.u_ms for s in future)
        rise = max(s.u_ms for s in future) - current.u_ms
        if drop >= _RAPID_WEAKENING_MS:
            status.trend = "weakening rapidly"
        elif drop >= 5.0:
            status.trend = "weakening"
        elif rise >= 5.0:
            status.trend = "strengthening"

        reversal = next((s for s in future if s.u_ms < cfg.reversal_ms), None)
        if reversal is not None and status.state != "reversed (SSW underway)":
            status.reversal_forecast = True
            status.reversal_date = reversal.time.date()

    return status
