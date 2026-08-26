"""Climatology from Open-Meteo's ERA5 archive.

Pulls the full reference period (default 1991-2020) of daily values for the
location in ONE request, then reduces it to per-calendar-date normals using a
+/- window around each date. The reduction lives here rather than in features/
because the result is effectively static reference data: it changes only if the
location or reference period changes, and is cached with a very long TTL.

Known judgement call: the archive snaps to a different grid point than the
forecast API (verified: 52.478,-1.840 vs 52.75,-1.75 for the same query point).
Anomalies therefore mix grid points; acceptable at UK Midlands terrain scales.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from skywatch import console, http
from skywatch.cache import DiskCache
from skywatch.config import Config
from skywatch.models import ClimatologyDay

API_URL = "https://archive-api.open-meteo.com/v1/archive"

_ARCHIVE_VARS = "temperature_2m_max,temperature_2m_min,precipitation_sum"
# The reference period never changes underneath us; cache for ~1 year.
_CLIMO_TTL_MINUTES = 60 * 24 * 365


class ClimatologySource:
    name = "openmeteo_climatology"

    def __init__(self, cfg: Config, cache: DiskCache) -> None:
        self.cfg = cfg
        # Deliberately long TTL regardless of the global setting.
        self.cache = DiskCache(cache.root, _CLIMO_TTL_MINUTES)

    def fetch(self, *, refresh: bool = False) -> dict[tuple[int, int], ClimatologyDay]:
        cfg = self.cfg
        cl = cfg.climatology
        key = (
            f"{cfg.location.latitude}_{cfg.location.longitude}"
            f"_{cl.start_year}-{cl.end_year}"
        )
        raw = self.cache.get(self.name, key, refresh=refresh)
        if raw is None:
            console.log().info(
                "Fetching %d-%d archive for climatology (one-off, ~11k days)",
                cl.start_year, cl.end_year,
            )
            raw = http.get_json(
                API_URL,
                params={
                    "latitude": cfg.location.latitude,
                    "longitude": cfg.location.longitude,
                    "start_date": f"{cl.start_year}-01-01",
                    "end_date": f"{cl.end_year}-12-31",
                    "daily": _ARCHIVE_VARS,
                    "timezone": cfg.location.timezone,
                },
                timeout=180.0,
            )
            self.cache.put(self.name, key, raw)
        return self._reduce(raw)

    def _reduce(self, raw: dict[str, Any]) -> dict[tuple[int, int], ClimatologyDay]:
        """Reduce the day series to per-(month, day) normals with a +/- window."""
        daily = raw["daily"]
        dates = [date.fromisoformat(d) for d in daily["time"]]
        tmax = daily["temperature_2m_max"]
        tmin = daily["temperature_2m_min"]
        precip = daily["precipitation_sum"]

        # Bucket samples by calendar (month, day). Feb 29 samples exist only in
        # leap years; the window fills the rest.
        by_md: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, d in enumerate(dates):
            by_md[(d.month, d.day)].append(i)

        window = self.cfg.climatology.window_days
        out: dict[tuple[int, int], ClimatologyDay] = {}
        # Iterate over every calendar date that occurs (incl. Feb 29).
        for month, day in sorted(by_md):
            idxs: list[int] = []
            # Collect indices for the target date +/- window, by calendar offset.
            # Use a fixed non-leap anchor year for offset arithmetic, except Feb 29
            # which needs a leap anchor.
            anchor_year = 2004 if (month, day) == (2, 29) else 2001
            anchor = date(anchor_year, month, day)
            for off in range(-window, window + 1):
                d = anchor + timedelta(days=off)
                idxs.extend(by_md.get((d.month, d.day), []))
            ts = [tmax[i] for i in idxs if tmax[i] is not None]
            tn = [tmin[i] for i in idxs if tmin[i] is not None]
            pr = sorted(p for i in idxs if (p := precip[i]) is not None)
            out[(month, day)] = ClimatologyDay(
                month=month,
                day=day,
                tmax_mean=round(statistics.fmean(ts), 2) if ts else None,
                tmax_std=round(statistics.stdev(ts), 2) if len(ts) > 1 else None,
                tmin_mean=round(statistics.fmean(tn), 2) if tn else None,
                tmin_std=round(statistics.stdev(tn), 2) if len(tn) > 1 else None,
                precip_mean=round(statistics.fmean(pr), 2) if pr else None,
                precip_p90=round(pr[int(len(pr) * 0.9)], 2) if pr else None,
                n_samples=len(ts),
            )
        return out
