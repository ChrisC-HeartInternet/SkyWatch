"""Grid-map reductions: per-point panel medians, disagreement, and map facts.

Pure functions over GridForecast. The dashboard draws the per-point numbers;
the digest gets only the sentence-sized facts (the LLM never sees the grid).
"""

from __future__ import annotations

import statistics
from datetime import date

from pydantic import BaseModel, ConfigDict

from skywatch.models import GridForecast

# Coarse UK/Ireland region boxes (lat_min, lat_max, lon_min, lon_max, name).
# First match wins; deliberately approximate — these name map areas in prose,
# they are not administrative boundaries.
_REGIONS: list[tuple[float, float, float, float, str]] = [
    (58.7, 61.0, -3.6, 0.0, "the Northern Isles"),
    (56.5, 58.8, -8.0, -4.4, "NW Scotland"),
    (56.5, 58.8, -4.4, -1.0, "NE Scotland"),
    (54.8, 56.5, -6.4, -1.8, "southern Scotland"),
    (54.0, 55.4, -8.2, -5.2, "Northern Ireland"),
    (51.3, 55.4, -10.5, -5.4, "Ireland"),
    (54.4, 55.8, -3.2, -1.0, "the far north of England"),
    (53.3, 54.4, -3.3, -0.2, "northern England"),
    (51.3, 53.5, -5.4, -2.9, "Wales"),
    (52.0, 53.3, -2.9, -0.4, "the Midlands"),
    (51.9, 53.2, -0.4, 1.8, "East Anglia"),
    (49.9, 51.3, -6.5, -2.8, "SW England"),
    (50.5, 52.0, -2.8, 1.6, "southern England"),
]


def region_name(lat: float, lon: float) -> str | None:
    """Coarse region for a grid point; None over open sea / outside the box."""
    for la0, la1, lo0, lo1, name in _REGIONS:
        if la0 <= lat < la1 and lo0 <= lon < lo1:
            return name
    return None


class GridDayField(BaseModel):
    """One variable's per-point panel-median field for one day."""

    model_config = ConfigDict(extra="forbid")

    variable: str
    values: list[float | None]      # per point
    n_models_min: int               # smallest panel behind any point


class GridDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    fields: dict[str, GridDayField]
    tmax_range: list[float | None]  # per-point cross-model max-min


class GridMapSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lats: list[float]
    lons: list[float]
    days: list[GridDay]
    # Index into days of the day the models argue about most (p90 of tmax range).
    max_disagreement_day: int
    facts: list[str]


def _point_day(
    grid: GridForecast, var: str, point: int, day: int
) -> tuple[float | None, float | None, int]:
    """(median, range, n_models) across models for one variable/point/day."""
    vals = [
        v
        for model_vars in grid.values.values()
        if (series := model_vars.get(var)) is not None
        and day < len(series[point])
        and (v := series[point][day]) is not None
    ]
    if not vals:
        return None, None, 0
    rng = round(max(vals) - min(vals), 2) if len(vals) >= 2 else None
    return round(statistics.median(vals), 2), rng, len(vals)


def _p90(vals: list[float]) -> float:
    if not vals:
        return 0.0
    ordered = sorted(vals)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]


def grid_summary(grid: GridForecast, *, variables: list[str]) -> GridMapSummary:
    n_points = len(grid.lats)
    days: list[GridDay] = []
    for di, d in enumerate(grid.dates):
        fields: dict[str, GridDayField] = {}
        tmax_range: list[float | None] = [None] * n_points
        for var in variables:
            values: list[float | None] = []
            n_min = 10**6
            for pi in range(n_points):
                med, rng, n = _point_day(grid, var, pi, di)
                values.append(med)
                if n:
                    n_min = min(n_min, n)
                if var == "temperature_2m_max":
                    tmax_range[pi] = rng
            fields[var] = GridDayField(
                variable=var, values=values, n_models_min=0 if n_min == 10**6 else n_min
            )
        days.append(GridDay(date=d, fields=fields, tmax_range=tmax_range))

    # The day worth mapping the argument for: highest p90 of per-point tmax range.
    scores = [_p90([r for r in day.tmax_range if r is not None]) for day in days]
    max_day = max(range(len(days)), default=0, key=lambda i: scores[i])

    return GridMapSummary(
        lats=grid.lats,
        lons=grid.lons,
        days=days,
        max_disagreement_day=max_day,
        facts=_facts(grid, days, max_day),
    )


def _extreme_fact(
    lats: list[float], lons: list[float], values: list[float | None],
    *, largest: bool, template: str,
) -> str | None:
    """Template a fact about the most extreme REGIONAL value (sea points skipped)."""
    best: tuple[float, str] | None = None
    for lat, lon, v in zip(lats, lons, values, strict=True):
        region = region_name(lat, lon)
        if v is None or region is None:
            continue
        if best is None or (v > best[0] if largest else v < best[0]):
            best = (v, region)
    if best is None:
        return None
    return template.format(value=best[0], region=best[1])


def _facts(grid: GridForecast, days: list[GridDay], max_day: int) -> list[str]:
    """Sentence-sized geography facts for the digest. Python-computed, always."""
    facts: list[str] = []
    if len(days) > 1:
        d1 = days[1]
        when = d1.date.strftime("%A")
        for var, template in [
            ("temperature_2m_max", f"Warmest on {when}: {{value}} °C around {{region}}"),
            ("precipitation_sum", f"Wettest on {when}: {{value}} mm around {{region}}"),
            ("wind_gusts_10m_max", f"Windiest on {when}: gusts {{value}} mph around {{region}}"),
        ]:
            fact = _extreme_fact(
                grid.lats, grid.lons, d1.fields[var].values, largest=True, template=template
            )
            if fact:
                facts.append(fact)
        cold = _extreme_fact(
            grid.lats, grid.lons, d1.fields["temperature_2m_max"].values,
            largest=False, template=f"Coolest on {when}: {{value}} °C around {{region}}",
        )
        if cold:
            facts.append(cold)

    dd = days[max_day]
    ranges: list[float | None] = dd.tmax_range
    arg = _extreme_fact(
        grid.lats, grid.lons, ranges, largest=True,
        template=(
            f"Largest model disagreement on {dd.date.strftime('%A %d %b')}: "
            "{value} °C spread on daily maximum around {region}"
        ),
    )
    if arg:
        facts.append(arg)
    return facts
