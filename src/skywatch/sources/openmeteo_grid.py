"""Gridded multi-model forecast over the UK box, for the dashboard maps.

One multi-location request covers the whole grid (verified live: 546 points,
4 models, 3 daily variables, 7 days, ~0.5 s, ~900 KB). Same suffixed-variable
response shape as the point forecast source.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from skywatch import console, http
from skywatch.cache import DiskCache
from skywatch.config import Config
from skywatch.models import GridForecast

API_URL = "https://api.open-meteo.com/v1/forecast"

GRID_VARIABLES = ["temperature_2m_max", "precipitation_sum", "wind_gusts_10m_max"]


def grid_points(cfg: Config) -> tuple[list[float], list[float]]:
    g = cfg.gridmap
    lats: list[float] = []
    lons: list[float] = []
    la = g.lat_min
    while la <= g.lat_max + 1e-9:
        lo = g.lon_min
        while lo <= g.lon_max + 1e-9:
            lats.append(round(la, 3))
            lons.append(round(lo, 3))
            lo += g.step
        la += g.step
    return lats, lons


class GridSource:
    name = "openmeteo_grid"

    def __init__(self, cfg: Config, cache: DiskCache) -> None:
        self.cfg = cfg
        self.cache = cache

    def fetch(self, *, refresh: bool = False) -> GridForecast:
        cfg = self.cfg
        g = cfg.gridmap
        lats, lons = grid_points(cfg)
        key = (
            f"{g.lat_min}_{g.lat_max}_{g.lon_min}_{g.lon_max}_{g.step}"
            f"_{'-'.join(cfg.models.deterministic)}_{g.days}d"
        )
        raw = self.cache.get(self.name, key, refresh=refresh)
        if raw is None:
            console.log().info("Fetching %d-point grid…", len(lats))
            raw = http.get_json(
                API_URL,
                params={
                    "latitude": ",".join(map(str, lats)),
                    "longitude": ",".join(map(str, lons)),
                    "daily": ",".join(GRID_VARIABLES),
                    "models": ",".join(cfg.models.deterministic),
                    "forecast_days": g.days,
                    "timezone": "UTC",
                    "wind_speed_unit": "mph",
                },
                timeout=180.0,
            )
            self.cache.put(self.name, key, raw)
        return self._parse(lats, lons, raw)

    def _parse(
        self, lats: list[float], lons: list[float], raw: list[dict[str, Any]]
    ) -> GridForecast:
        if not isinstance(raw, list) or len(raw) != len(lats):
            raise ValueError(
                f"grid: expected {len(lats)} locations, got "
                f"{len(raw) if isinstance(raw, list) else type(raw)}"
            )
        dates = [date.fromisoformat(d) for d in raw[0]["daily"]["time"]]
        values: dict[str, dict[str, list[list[float | None]]]] = {}
        for model in self.cfg.models.deterministic:
            per_var: dict[str, list[list[float | None]]] = {}
            for var in GRID_VARIABLES:
                key = f"{var}_{model}"
                per_var[var] = [
                    list(loc["daily"].get(key) or [None] * len(dates)) for loc in raw
                ]
            values[model] = per_var
        return GridForecast(lats=lats, lons=lons, dates=dates, values=values)
