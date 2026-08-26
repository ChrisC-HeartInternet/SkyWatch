"""Observed daily weather (ERA5 archive) for forecast verification.

The archive runs ~1 day behind real time (verified live), so each date can be
scored the morning after. Grid-point caveat: the archive snaps to a different
cell than the forecast API; the offset inflates absolute errors equally for
every model, so cross-model comparison stays fair.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from skywatch import http
from skywatch.cache import DiskCache
from skywatch.config import Config

API_URL = "https://archive-api.open-meteo.com/v1/archive"

OBSERVED_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_gusts_10m_max",
]

# The archive updates daily; refetch a few times a day at most.
_OBS_TTL_MINUTES = 6 * 60


class ObservedSource:
    name = "openmeteo_observed"

    def __init__(self, cfg: Config, cache: DiskCache) -> None:
        self.cfg = cfg
        self.cache = DiskCache(cache.root, _OBS_TTL_MINUTES)

    def fetch(
        self, *, refresh: bool = False, today: date | None = None
    ) -> dict[date, dict[str, float]]:
        cfg = self.cfg
        today = today or date.today()
        start = today - timedelta(days=cfg.skill.window_days)
        end = today - timedelta(days=1)
        key = f"{cfg.location.latitude}_{cfg.location.longitude}_{start}_{end}"
        raw = self.cache.get(self.name, key, refresh=refresh)
        if raw is None:
            raw = http.get_json(
                API_URL,
                params={
                    "latitude": cfg.location.latitude,
                    "longitude": cfg.location.longitude,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "daily": ",".join(OBSERVED_VARIABLES),
                    "timezone": "UTC",
                    "wind_speed_unit": "mph",
                },
                timeout=120.0,
            )
            self.cache.put(self.name, key, raw)
        return _parse(raw)


def _parse(raw: dict[str, Any]) -> dict[date, dict[str, float]]:
    daily = raw["daily"]
    out: dict[date, dict[str, float]] = {}
    for i, d in enumerate(daily["time"]):
        row = {
            var: v
            for var in OBSERVED_VARIABLES
            if (vals := daily.get(var)) is not None and (v := vals[i]) is not None
        }
        if row:
            out[date.fromisoformat(d)] = row
    return out
