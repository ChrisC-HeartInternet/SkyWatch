"""NOAA CPC weekly Nino 3.4 SST index.

Source file: wksst9120.for (1991-2020 base period). NOTE the sibling file
wksst8110.for is FROZEN at Jan 2021 — verified live — so it must not be used.

Format traps, both verified against the live file:
- Nino 3.4 is the THIRD column pair. Header order: Nino1+2, Nino3, Nino34, Nino4.
  Reading the last pair silently gives Nino 4 — a different ENSO answer.
- Negative anomalies concatenate with the SST ("24.8-0.1"), so the line cannot
  be whitespace-split; extract the 8 fixed-format floats by regex instead.

The parser fails loudly if the header's column order ever changes.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from skywatch import http
from skywatch.cache import DiskCache
from skywatch.config import Config
from skywatch.models import EnsoWeek

DATA_URL = "https://www.cpc.ncep.noaa.gov/data/indices/wksst9120.for"

_HEADER_ORDER = ("Nino1+2", "Nino3", "Nino34", "Nino4")
_NINO34_INDEX = _HEADER_ORDER.index("Nino34")  # third pair -> floats 4 and 5

_ROW_RE = re.compile(r"^\s*(\d{2}[A-Z]{3}\d{4})(.*)$")
_FLOAT_RE = re.compile(r"-?\d+\.\d")


class EnsoSource:
    name = "noaa_enso"

    def __init__(self, cfg: Config, cache: DiskCache) -> None:
        self.cfg = cfg
        self.cache = cache

    def fetch(self, *, refresh: bool = False) -> list[EnsoWeek]:
        key = "wksst9120"
        raw = self.cache.get(self.name, key, refresh=refresh)
        if raw is None:
            raw = http.get_text(DATA_URL)
            self.cache.put(self.name, key, raw)
        return parse_wksst(raw)


def parse_wksst(text: str) -> list[EnsoWeek]:
    """Parse the CPC weekly SST file into EnsoWeek rows (Nino 3.4 only)."""
    lines = text.splitlines()

    # Loud column-order guard: find the header and verify it names the four
    # regions in the order this parser assumes.
    header = next((ln for ln in lines if "Nino" in ln and "34" in ln), None)
    if header is None:
        raise ValueError("wksst file has no recognisable Nino header")
    positions = [header.find(name) for name in _HEADER_ORDER]
    if -1 in positions or positions != sorted(positions):
        raise ValueError(
            f"wksst header column order changed; expected {_HEADER_ORDER}, got: {header!r}"
        )

    weeks: list[EnsoWeek] = []
    for ln in lines:
        m = _ROW_RE.match(ln)
        if not m:
            continue
        try:
            week = datetime.strptime(m.group(1), "%d%b%Y").date()
        except ValueError:
            continue
        floats = _FLOAT_RE.findall(m.group(2))
        if len(floats) != 8:
            continue  # malformed row; skip rather than misread
        sst = float(floats[_NINO34_INDEX * 2])
        anom = float(floats[_NINO34_INDEX * 2 + 1])
        weeks.append(EnsoWeek(week=week, sst=sst, anomaly=anom))

    if not weeks:
        raise ValueError("wksst file parsed to zero data rows")
    weeks.sort(key=lambda w: w.week)
    _sanity_check(weeks)
    return weeks


def _sanity_check(weeks: list[EnsoWeek]) -> None:
    """Nino 3.4 SST lives in a tight physical band; catch column mixups loudly."""
    for w in weeks[-52:]:
        if not (20.0 <= w.sst <= 32.0) or not (-4.0 <= w.anomaly <= 4.5):
            raise ValueError(
                f"Nino 3.4 value out of physical range in week {w.week}: "
                f"sst={w.sst} anomaly={w.anomaly} — column order may have changed"
            )


def latest_weeks(weeks: list[EnsoWeek], n: int = 6) -> list[EnsoWeek]:
    return weeks[-n:]


def staleness_days(weeks: list[EnsoWeek], today: date) -> int:
    return (today - weeks[-1].week).days
