"""Data source registry.

To add a new source: create a module here with a class implementing the Source
protocol (see base.py), then register it in build_sources(). Everything else —
caching, history, the digest — picks it up from there.
"""

from __future__ import annotations

from skywatch.cache import DiskCache
from skywatch.config import Config
from skywatch.sources.base import Source
from skywatch.sources.noaa_enso import EnsoSource
from skywatch.sources.openmeteo_climatology import ClimatologySource
from skywatch.sources.openmeteo_ensemble import EnsembleSource
from skywatch.sources.openmeteo_forecast import ForecastSource
from skywatch.sources.openmeteo_grid import GridSource
from skywatch.sources.openmeteo_observed import ObservedSource
from skywatch.sources.vortex import VortexSource

__all__ = [
    "ClimatologySource",
    "EnsembleSource",
    "EnsoSource",
    "ForecastSource",
    "GridSource",
    "ObservedSource",
    "Source",
    "VortexSource",
    "build_sources",
]


def build_sources(cfg: Config) -> dict[str, Source]:
    cache = DiskCache(cfg.cache_dir, cfg.cache_ttl_minutes)
    sources: list[Source] = [
        ForecastSource(cfg, cache),
        EnsembleSource(cfg, cache),
        ClimatologySource(cfg, cache),
        EnsoSource(cfg, cache),
        VortexSource(cfg, cache),
        GridSource(cfg, cache),
        ObservedSource(cfg, cache),
    ]
    return {s.name: s for s in sources}
