"""Open-Meteo ensemble forecasts (ensemble-api.open-meteo.com).

One request PER ensemble model, deliberately: multi-model ensemble responses
use inconsistent member-key naming (verified live:
temperature_2m_member01_ecmwf_ifs025_ensemble vs temperature_2m_member01_ncep_gefs025),
while single-model responses give clean `<var>` + `<var>_memberNN` keys.

Member counts verified: ecmwf_ifs025 50, gfs025 30, icon_eu/icon_global 39,
ukmo_global_ensemble_20km 17.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from skywatch import console, http
from skywatch.cache import DiskCache
from skywatch.config import Config
from skywatch.models import EnsembleForecast

API_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

# Variables worth ensemble treatment: temperature for spread/frost, precipitation
# and snowfall for probabilities, gusts for wind alerts.
ENSEMBLE_DAILY_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "snowfall_sum",
    "wind_gusts_10m_max",
]

_MEMBER_RE = re.compile(r"^(?P<var>[a-z0-9_]+?)_member(?P<num>\d+)$")


class EnsembleSource:
    name = "openmeteo_ensemble"

    def __init__(self, cfg: Config, cache: DiskCache) -> None:
        self.cfg = cfg
        self.cache = cache

    def fetch(self, *, refresh: bool = False) -> list[EnsembleForecast]:
        out: list[EnsembleForecast] = []
        for model in self.cfg.models.ensemble:
            out.extend(self._fetch_model(model, refresh=refresh))
        return out

    def _fetch_model(self, model: str, *, refresh: bool) -> list[EnsembleForecast]:
        cfg = self.cfg
        key = (
            f"{cfg.location.latitude}_{cfg.location.longitude}_{model}_{cfg.forecast_days}d"
        )
        raw = self.cache.get(self.name, key, refresh=refresh)
        if raw is None:
            raw = http.get_json(
                API_URL,
                params={
                    "latitude": cfg.location.latitude,
                    "longitude": cfg.location.longitude,
                    "daily": ",".join(ENSEMBLE_DAILY_VARIABLES),
                    "models": model,
                    "forecast_days": cfg.forecast_days,
                    "timezone": cfg.location.timezone,
                    "wind_speed_unit": "mph",
                },
                timeout=120.0,  # 50-member responses are large
            )
            self.cache.put(self.name, key, raw)
        return self._parse(model, raw)

    def _parse(self, model: str, raw: dict[str, Any]) -> list[EnsembleForecast]:
        daily = raw["daily"]
        units = raw.get("daily_units", {})
        dates = [date.fromisoformat(d) for d in daily["time"]]

        # Group member series by variable. The unsuffixed key is the control run
        # (member 00 in effect) and is included as a member.
        members_by_var: dict[str, dict[int, list[float | None]]] = {}
        for k, values in daily.items():
            if k == "time":
                continue
            m = _MEMBER_RE.match(k)
            if m and m.group("var") in ENSEMBLE_DAILY_VARIABLES:
                members_by_var.setdefault(m.group("var"), {})[int(m.group("num"))] = values
            elif k in ENSEMBLE_DAILY_VARIABLES:
                members_by_var.setdefault(k, {})[0] = values

        out: list[EnsembleForecast] = []
        for var in ENSEMBLE_DAILY_VARIABLES:
            grouped = members_by_var.get(var)
            if not grouped:
                console.log().warning("%s: %s has no members for %s", self.name, model, var)
                continue
            member_rows = [grouped[i] for i in sorted(grouped)]
            out.append(
                EnsembleForecast(
                    model=model,
                    variable=var,
                    unit=units.get(f"{var}_member01", units.get(var, "")),
                    dates=dates,
                    members=member_rows,
                )
            )
        return out
