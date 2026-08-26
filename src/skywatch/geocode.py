"""Turn a human location string into coordinates.

Accepts three forms, tried in order:
- "lat,lon"            -> used directly
- a UK postcode        -> postcodes.io (free, keyless, returns the parish/ward)
- anything else        -> Open-Meteo geocoding (free, keyless, global, gives tz)

Results are cached on disk so restarts never depend on either service.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from skywatch import http

POSTCODES_URL = "https://api.postcodes.io/postcodes/{postcode}"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

_UK_POSTCODE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.IGNORECASE)
_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")


class Place(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float
    longitude: float
    name: str
    timezone: str | None = None
    source: str


def parse_latlon(text: str) -> tuple[float, float] | None:
    m = _LATLON.match(text)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"coordinates out of range: {text!r}")
    return lat, lon


def is_uk_postcode(text: str) -> bool:
    return bool(_UK_POSTCODE.match(text.strip()))


def resolve(query: str, cache_file: Path | None = None) -> Place:
    """Resolve a location string; cached per query when cache_file is given."""
    query = query.strip()
    if (ll := parse_latlon(query)) is not None:
        return Place(latitude=ll[0], longitude=ll[1], name=f"{ll[0]:.3f}, {ll[1]:.3f}",
                     source="coordinates")

    cache: dict[str, dict[str, object]] = {}
    if cache_file and cache_file.exists():
        cache = json.loads(cache_file.read_text())
        if query in cache:
            return Place.model_validate(cache[query])

    place = _postcode(query) if is_uk_postcode(query) else _place_name(query)

    if cache_file:
        cache[query] = place.model_dump()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, indent=1))
    return place


def _postcode(postcode: str) -> Place:
    data = http.get_json(POSTCODES_URL.format(postcode=postcode.replace(" ", "%20")),
                         params={}, timeout=20.0)
    result = data.get("result") if isinstance(data, dict) else None
    if not result:
        raise ValueError(f"postcode not found: {postcode!r}")
    name = result.get("parish") or result.get("admin_ward") or result.get("admin_district")
    return Place(
        latitude=result["latitude"], longitude=result["longitude"],
        name=str(name or postcode).replace(", unparished area", ""),
        timezone="Europe/London", source="postcodes.io",
    )


def _place_name(name: str) -> Place:
    data = http.get_json(GEOCODE_URL, params={"name": name, "count": 1, "language": "en",
                                              "format": "json"}, timeout=20.0)
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        raise ValueError(
            f"place not found: {name!r}. Try a UK postcode, a larger town, or 'lat,lon'."
        )
    r = results[0]
    return Place(latitude=r["latitude"], longitude=r["longitude"], name=r["name"],
                 timezone=r.get("timezone"), source="open-meteo geocoding")
