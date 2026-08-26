"""Anomalies: forecast values vs the climatological normal for each date.

Sigma (anomaly / climatological std) is included because "+4C" means something
different in changeable January than in settled July.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict

from skywatch.models import ClimatologyDay, ModelForecast


class DayAnomaly(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    tmax_anomaly: float | None = None   # degrees C vs normal
    tmax_sigma: float | None = None     # anomaly in climatological std devs
    tmin_anomaly: float | None = None
    tmin_sigma: float | None = None
    precip_vs_mean_mm: float | None = None
    normal_tmax: float | None = None
    normal_tmin: float | None = None


def daily_anomalies(
    forecast: ModelForecast,
    climatology: dict[tuple[int, int], ClimatologyDay],
) -> list[DayAnomaly]:
    """Anomaly per day for a forecast series (typically the cross-model median)."""
    tmax = forecast.series.get("temperature_2m_max")
    tmin = forecast.series.get("temperature_2m_min")
    precip = forecast.series.get("precipitation_sum")

    out: list[DayAnomaly] = []
    for i, d in enumerate(forecast.dates):
        climo = climatology.get((d.month, d.day))
        a = DayAnomaly(date=d)
        if climo is not None:
            a.normal_tmax = climo.tmax_mean
            a.normal_tmin = climo.tmin_mean
            fv = tmax.values[i] if tmax and i < len(tmax.values) else None
            if fv is not None and climo.tmax_mean is not None:
                a.tmax_anomaly = round(fv - climo.tmax_mean, 1)
                if climo.tmax_std:
                    a.tmax_sigma = round(a.tmax_anomaly / climo.tmax_std, 2)
            fv = tmin.values[i] if tmin and i < len(tmin.values) else None
            if fv is not None and climo.tmin_mean is not None:
                a.tmin_anomaly = round(fv - climo.tmin_mean, 1)
                if climo.tmin_std:
                    a.tmin_sigma = round(a.tmin_anomaly / climo.tmin_std, 2)
            fv = precip.values[i] if precip and i < len(precip.values) else None
            if fv is not None and climo.precip_mean is not None:
                a.precip_vs_mean_mm = round(fv - climo.precip_mean, 1)
        out.append(a)
    return out
