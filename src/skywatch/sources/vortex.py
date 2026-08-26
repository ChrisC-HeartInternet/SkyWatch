"""Stratospheric polar vortex: zonal-mean zonal wind at 10 hPa / 60N (u60N).

This is the sudden-stratospheric-warming (SSW) early-warning signal: a collapse
or reversal of the winter westerlies here often precedes NW-European cold-air
outbreaks by 2-6 weeks.

NOAA CPC publishes this quantity only as images (verified), so we compute it:

1. Live + forecast: sample the 60N latitude circle at N longitudes from
   Open-Meteo's forecast API (wind_speed_10hPa / wind_direction_10hPa, GFS),
   convert each sample to its zonal component u = -speed*sin(dir), and average.
   Gives today's value plus a 16-day forecast. Verified against climatology:
   late Aug computed -0.3 m/s vs -1.7 normal; deep winter normal is +35 m/s.
2. Recent history: the same variables from Open-Meteo's previous-runs archive
   are NOT available (nulls, verified), so history accumulates from our own
   cached fetches over time (cache history files).
3. Climatology: NOAA PSL's NCEP/NCAR daily long-term mean via OPeNDAP .ascii
   (no netCDF dependency), exact grid point level=10 hPa, lat=60N, all 144
   longitudes, cached ~forever.

Known bias, accepted: live values are GFS, climatology is NCEP-R1.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from skywatch import http
from skywatch.cache import DiskCache
from skywatch.config import Config
from skywatch.models import VortexSample

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# NCEP/NCAR reanalysis daily long-term mean (1981-2010), 17 levels, 2.5 deg grid.
PSL_LTM_URL = (
    "https://psl.noaa.gov/thredds/dodsC/Datasets/ncep.reanalysis.derived/"
    "pressure/uwnd.day.1981-2010.ltm.nc.ascii"
)
_PSL_LEVEL_INDEX = 16  # 10 hPa   (level order verified: ... 30, 20, 10)
_PSL_LAT_INDEX = 12    # 60N      (90 - 12*2.5)
_PSL_TTL_MINUTES = 60 * 24 * 365


class VortexData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    samples: list[VortexSample]          # daily u60N, today .. +16 days (forecast)
    climatology: dict[int, float]        # day-of-year (1..365) -> normal u60N m/s


def zonal_component(speed_ms: float, direction_deg: float) -> float:
    """Zonal (west->east) wind component from meteorological speed/direction.

    Direction is where the wind comes FROM, so a 270 deg (westerly) wind has
    positive u: u = -speed * sin(direction).
    """
    return -speed_ms * math.sin(math.radians(direction_deg))


class VortexSource:
    name = "vortex"

    def __init__(self, cfg: Config, cache: DiskCache) -> None:
        self.cfg = cfg
        self.cache = cache
        self._climo_cache = DiskCache(cache.root, _PSL_TTL_MINUTES)

    def fetch(self, *, refresh: bool = False) -> VortexData:
        return VortexData(
            samples=self._fetch_forecast(refresh=refresh),
            climatology=self._fetch_climatology(refresh=refresh),
        )

    # -- live + forecast ---------------------------------------------------

    def _fetch_forecast(self, *, refresh: bool) -> list[VortexSample]:
        v = self.cfg.vortex
        n = v.n_longitudes
        lons = [round(-180 + i * (360 / n), 2) for i in range(n)]  # API needs -180..180
        key = f"u{v.pressure_hpa}hpa_{v.latitude}N_{n}lon"
        raw = self.cache.get(self.name, key, refresh=refresh)
        if raw is None:
            raw = http.get_json(
                FORECAST_URL,
                params={
                    "latitude": ",".join(str(v.latitude) for _ in lons),
                    "longitude": ",".join(str(lo) for lo in lons),
                    "hourly": f"wind_speed_{v.pressure_hpa}hPa,wind_direction_{v.pressure_hpa}hPa",
                    "models": "gfs_seamless",
                    "forecast_days": 16,
                    "wind_speed_unit": "ms",
                    "timezone": "UTC",
                },
                timeout=120.0,
            )
            self.cache.put(self.name, key, raw)
        return self._parse_forecast(raw)

    def _parse_forecast(self, raw: list[dict[str, Any]]) -> list[VortexSample]:
        v = self.cfg.vortex
        spd_key = f"wind_speed_{v.pressure_hpa}hPa"
        dir_key = f"wind_direction_{v.pressure_hpa}hPa"
        if not isinstance(raw, list) or not raw:
            raise ValueError("vortex: expected a multi-location response list")

        times = raw[0]["hourly"]["time"]
        # Daily cadence at 12z is plenty for a signal that evolves over weeks.
        idxs = [i for i, t in enumerate(times) if t.endswith("T12:00")]
        samples: list[VortexSample] = []
        for i in idxs:
            us: list[float] = []
            for loc in raw:
                s = loc["hourly"][spd_key][i]
                d = loc["hourly"][dir_key][i]
                if s is None or d is None:
                    continue
                us.append(zonal_component(s, d))
            if not us:
                continue
            samples.append(
                VortexSample(
                    time=datetime.fromisoformat(times[i]),
                    u_ms=round(sum(us) / len(us), 2),
                    n_points=len(us),
                )
            )
        if not samples:
            raise ValueError("vortex: no valid 12z samples in forecast response")
        return samples

    # -- climatology ---------------------------------------------------------

    def _fetch_climatology(self, *, refresh: bool) -> dict[int, float]:
        """Daily-normal u60N for all 365 climatology days, one OPeNDAP request."""
        key = "psl_u60n_10hpa_ltm"
        raw = self._climo_cache.get(self.name, key, refresh=refresh)
        if raw is None:
            url = (
                f"{PSL_LTM_URL}?uwnd"
                f"%5B0:364%5D%5B{_PSL_LEVEL_INDEX}%5D%5B{_PSL_LAT_INDEX}%5D%5B0:143%5D"
            )
            raw = http.get_text(url, timeout=300.0)
            self.cache.put(self.name, key, raw)   # keep in main cache history too
            self._climo_cache.put(self.name, key, raw)
        return parse_psl_ltm(raw)


def parse_psl_ltm(text: str) -> dict[int, float]:
    """Parse the OPeNDAP .ascii response into day-of-year -> zonal-mean u60N.

    Rows look like: [day][0][0], v0, v1, ..., v143  (values often scaled shorts
    on some mirrors; this endpoint returns plain floats — sanity-checked below).
    """
    row_re = re.compile(r"^\[(\d+)\]\[0\]\[0\](?:,\s*|\s+)(.+)$")
    out: dict[int, float] = {}
    for line in text.splitlines():
        m = row_re.match(line.strip())
        if not m:
            continue
        day_idx = int(m.group(1))
        vals = [float(x) for x in m.group(2).split(",") if x.strip()]
        if len(vals) < 100:  # expect all 144 longitudes
            continue
        out[day_idx + 1] = round(sum(vals) / len(vals), 2)
    if len(out) < 360:
        raise ValueError(f"PSL climatology parsed only {len(out)} days")
    # Physical sanity: deep-winter vortex should be strongly westerly.
    jan5 = out.get(5)
    if jan5 is None or not (10.0 <= jan5 <= 60.0):
        raise ValueError(f"PSL climatology looks wrong: u60N Jan5 = {jan5}")
    return out


def climatology_for(climo: dict[int, float], d: date) -> float | None:
    """Normal u60N for a calendar date (Feb 29 borrows Feb 28; PSL LTM has 365 days)."""
    doy = d.timetuple().tm_yday
    if d.month > 2 and d.year % 4 == 0 and (d.year % 100 != 0 or d.year % 400 == 0):
        doy -= 1  # collapse leap-year offset onto the 365-day climatology
    return climo.get(min(doy, 365))
