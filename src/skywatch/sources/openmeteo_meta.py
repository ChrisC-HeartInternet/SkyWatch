"""Which model cycle is Open-Meteo currently serving?

Each real model exposes /data/<model>/static/meta.json with its last run's
initialisation and availability times (verified live: all our global models
cycle 6-hourly with 4-7 h publication lag). The watcher polls this — a few
1 KB requests — and only takes a full snapshot when a model has a NEW cycle.

Our configured names are partly "seamless" virtual models; this maps them to
the real datasets that drive them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from skywatch import http

META_URL = "https://api.open-meteo.com/data/{model}/static/meta.json"

# configured model -> real dataset ids whose cycles matter for it
REAL_MODELS: dict[str, list[str]] = {
    "ecmwf_ifs025": ["ecmwf_ifs025"],
    "gfs_seamless": ["ncep_gfs025"],
    "icon_seamless": ["dwd_icon"],
    "ukmo_seamless": ["ukmo_global_deterministic_10km", "ukmo_uk_deterministic_2km"],
    # ensembles
    "gfs025": ["ncep_gefs025"],
    "ecmwf_ifs025_ensemble": ["ecmwf_ifs025_ensemble"],
}


class ModelRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    init_time: datetime
    available_time: datetime
    interval_hours: float


def real_ids(configured: list[str]) -> list[str]:
    out: list[str] = []
    for name in configured:
        for rid in REAL_MODELS.get(name, [name]):
            if rid not in out:
                out.append(rid)
    return out


def parse_meta(model: str, raw: dict[str, Any]) -> ModelRun:
    return ModelRun(
        model=model,
        init_time=datetime.fromtimestamp(raw["last_run_initialisation_time"], UTC),
        available_time=datetime.fromtimestamp(raw["last_run_availability_time"], UTC),
        interval_hours=raw.get("update_interval_seconds", 21600) / 3600,
    )


def fetch_model_runs(models: list[str]) -> dict[str, ModelRun]:
    """Current cycle per real model id. A model whose meta fails is skipped."""
    runs: dict[str, ModelRun] = {}
    for m in models:
        try:
            raw = http.get_json(META_URL.format(model=m), params={}, timeout=20.0)
            runs[m] = parse_meta(m, raw)
        except Exception:  # noqa: BLE001 — one model's meta must not stop the poll
            continue
    return runs


def new_cycles(current: dict[str, ModelRun], seen: dict[str, datetime]) -> list[str]:
    """Models whose current init_time is newer than the one we last snapshotted."""
    return sorted(
        m for m, r in current.items()
        if m not in seen or r.init_time > seen[m]
    )
