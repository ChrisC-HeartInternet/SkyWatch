"""Domain types shared across the pipeline.

Conventions, fixed at the fetch boundary so everything downstream is uniform:
temperatures degrees C, precipitation mm, snowfall cm, wind gusts mph,
pressure hPa, stratospheric winds m/s. Missing data is None, never a sentinel.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

# Daily variables pulled from every deterministic model, in Open-Meteo naming.
DAILY_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_gusts_10m_max",
    "snowfall_sum",
    "pressure_msl_mean",
]


class DailySeries(BaseModel):
    """One variable's daily values from one model. None = model has no data that day."""

    model_config = ConfigDict(extra="forbid")

    variable: str
    unit: str
    values: list[float | None]


class ModelForecast(BaseModel):
    """One deterministic model's full daily forecast."""

    model_config = ConfigDict(extra="forbid")

    model: str
    dates: list[date]
    series: dict[str, DailySeries]  # keyed by variable name

    def horizon_days(self) -> int:
        """Days with at least one non-null value — models differ (GFS 16, UKMO 7)."""
        best = 0
        for s in self.series.values():
            n = len(s.values)
            while n > 0 and s.values[n - 1] is None:
                n -= 1
            best = max(best, n)
        return best


class EnsembleForecast(BaseModel):
    """One ensemble system's members for one variable, daily."""

    model_config = ConfigDict(extra="forbid")

    model: str
    variable: str
    unit: str
    dates: list[date]
    # members[i][d] = member i's value on day d; None where a member has no data
    members: list[list[float | None]]


class ClimatologyDay(BaseModel):
    """What's normal for one calendar date, from the reference-period archive."""

    model_config = ConfigDict(extra="forbid")

    month: int
    day: int
    tmax_mean: float | None = None
    tmax_std: float | None = None
    tmin_mean: float | None = None
    tmin_std: float | None = None
    precip_mean: float | None = None
    precip_p90: float | None = None
    n_samples: int = 0


class GridForecast(BaseModel):
    """Gridded multi-model daily forecast for the map panels.

    values[model][variable][point_index][day_index]; None past a model's horizon.
    """

    model_config = ConfigDict(extra="forbid")

    lats: list[float]
    lons: list[float]
    dates: list[date]
    values: dict[str, dict[str, list[list[float | None]]]]


class EnsoWeek(BaseModel):
    """One week of the NOAA CPC Nino 3.4 index."""

    model_config = ConfigDict(extra="forbid")

    week: date
    sst: float
    anomaly: float


class VortexSample(BaseModel):
    """Zonal-mean zonal wind at 10 hPa / 60N for one time."""

    model_config = ConfigDict(extra="forbid")

    time: datetime
    u_ms: float
    n_points: int  # longitudes that contributed; < configured n means partial data


class Severity(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    SEVERE = "severe"


class Alert(BaseModel):
    """The machine-readable alert contract consumed by Hermes."""

    model_config = ConfigDict(extra="forbid")

    severity: Severity
    category: str
    title: str
    detail: str
    confidence: float  # 0..1
    valid_from: date
    valid_to: date
    sources: list[str]
