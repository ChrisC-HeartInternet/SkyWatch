"""Open-Meteo multi-model deterministic forecasts.

One request pulls the same daily variables from every configured model. The API
returns variables suffixed with the model name (e.g. temperature_2m_max_ukmo_seamless)
when several models are requested — verified live.

Model horizons differ (verified: gfs_seamless 16 days, ecmwf_ifs025 15,
icon_seamless 7, ukmo_seamless 7); days beyond a model's horizon come back null
and are kept as None so downstream code can track panel size per day.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from skywatch import console, http
from skywatch.cache import DiskCache
from skywatch.config import Config
from skywatch.models import DAILY_VARIABLES, DailySeries, ModelForecast

API_URL = "https://api.open-meteo.com/v1/forecast"


class ForecastSource:
    name = "openmeteo_forecast"

    def __init__(self, cfg: Config, cache: DiskCache) -> None:
        self.cfg = cfg
        self.cache = cache

    def fetch(self, *, refresh: bool = False) -> list[ModelForecast]:
        cfg = self.cfg
        key = (
            f"{cfg.location.latitude}_{cfg.location.longitude}"
            f"_{'-'.join(cfg.models.deterministic)}_{cfg.forecast_days}d"
        )
        raw = self.cache.get(self.name, key, refresh=refresh)
        if raw is None:
            raw = http.get_json(
                API_URL,
                params={
                    "latitude": cfg.location.latitude,
                    "longitude": cfg.location.longitude,
                    "daily": ",".join(DAILY_VARIABLES),
                    "models": ",".join(cfg.models.deterministic),
                    "forecast_days": cfg.forecast_days,
                    "timezone": cfg.location.timezone,
                    "wind_speed_unit": "mph",  # gusts arrive in mph, the alert unit
                },
            )
            self.cache.put(self.name, key, raw)
        return self._parse(raw)

    def _parse(self, raw: dict[str, Any]) -> list[ModelForecast]:
        daily = raw["daily"]
        units = raw.get("daily_units", {})
        dates = [date.fromisoformat(d) for d in daily["time"]]
        n = len(dates)

        forecasts: list[ModelForecast] = []
        for model in self.cfg.models.deterministic:
            series: dict[str, DailySeries] = {}
            for var in DAILY_VARIABLES:
                # Multi-model responses suffix keys with the model name; if only
                # one model were configured the key would be bare.
                key = f"{var}_{model}" if f"{var}_{model}" in daily else var
                values = daily.get(key)
                if values is None:
                    console.log().warning("%s: %s missing for %s", self.name, var, model)
                    values = [None] * n
                series[var] = DailySeries(
                    variable=var, unit=units.get(key, ""), values=list(values)
                )
            fc = ModelForecast(model=model, dates=dates, series=series)
            if fc.horizon_days() == 0:
                console.log().warning("%s: model %s returned no data at all", self.name, model)
            forecasts.append(fc)
        return forecasts
