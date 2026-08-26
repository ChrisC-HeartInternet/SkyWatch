"""Configuration models and loader.

Two layers, one precedence rule:

- config.yaml holds the tunables (thresholds, models, radii). It is generic and
  tracked in git — nothing personal lives in it.
- .env (or real environment variables, SKYWATCH_*) holds the personal and
  deployment values: location, LLM endpoint and models, ntfy topic, bind
  address, data directory. Environment always wins over YAML.

Location can be a UK postcode, a place name, or "lat,lon" — see geocode.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Location(BaseModel):
    """The point forecasts are made for."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Friendly name used in briefings, e.g. 'Ambleside'")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str = Field(default="Europe/London")


class LLMConfig(BaseModel):
    """OpenAI-compatible endpoint (Ollama, LM Studio, ...)."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(
        default="http://localhost:11434/v1",
        description="OpenAI-compatible endpoint, e.g. Ollama at http://host:11434/v1",
    )
    api_key: str | None = Field(
        default=None,
        description="Optional. Ollama ignores it; LM Studio and proxies may require it. "
        "Use ${ENV_VAR} to read from the environment.",
    )
    model: str = Field(default="qwen3:32b", description="Primary model: writes the briefing")
    fast_model: str = Field(default="qwen3:8b", description="Cheap model: formats alerts")
    timeout_seconds: float = Field(default=600.0, gt=0)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=12288, gt=0)
    # Reasoning models (qwen3 family) think before answering, and with a
    # finite max_tokens can burn the whole budget thinking and return empty
    # content (hit live with both qwen3:235b and qwen3.5). False sends
    # Ollama's think:false on every call. If you want thinking, set true AND
    # raise max_tokens substantially.
    thinking: bool = False

    @field_validator("api_key")
    @classmethod
    def _expand_env(cls, v: str | None) -> str | None:
        """Allow ${VAR} indirection so keys never live in the config file."""
        if v and v.startswith("${") and v.endswith("}"):
            return os.environ.get(v[2:-1])
        return v

    @property
    def effective_api_key(self) -> str:
        """The openai client requires a non-empty key; Ollama ignores its value."""
        return self.api_key or "not-required"


class ModelsConfig(BaseModel):
    """Which numerical weather models to pull.

    Horizons differ sharply and this is load-bearing for the disagreement feature:
    at the verified defaults GFS reaches 16 days, ECMWF 15, ICON 7, UKMO 7.
    """

    model_config = ConfigDict(extra="forbid")

    deterministic: list[str] = Field(
        default=["ecmwf_ifs025", "gfs_seamless", "icon_seamless", "ukmo_seamless"]
    )
    ensemble: list[str] = Field(default=["ecmwf_ifs025", "gfs025"])


class Thresholds(BaseModel):
    """Alert trigger levels. All UK-tuned defaults; tune freely.

    Gusts are mph, snow cm, precipitation mm, temperatures degrees C.
    """

    model_config = ConfigDict(extra="forbid")

    gust_mph_moderate: float = 45.0
    gust_mph_high: float = 60.0
    snowfall_cm: float = 2.0
    precip_mm_daily: float = 20.0
    temp_anomaly_c: float = 6.0
    frost_probability_pct: float = 30.0
    snow_probability_pct: float = 20.0
    # Ensemble spread growing by more than this factor between consecutive days
    # is the "uncertainty explosion" signal.
    spread_jump_factor: float = 2.0
    # Cross-model temperature range beyond this is flagged as real divergence.
    model_divergence_temp_c: float = 4.0


class VortexConfig(BaseModel):
    """Stratospheric polar vortex monitoring.

    CPC publishes this only as images, so we compute the zonal-mean zonal wind at
    10 hPa / 60N ourselves from gridded forecast winds sampled around the latitude circle.
    """

    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(default=60.0, description="Latitude circle to average around")
    pressure_hpa: int = Field(default=10, description="Pressure level")
    n_longitudes: int = Field(default=12, ge=4, le=36, description="Sample points around 60N")
    # SSW is a winter phenomenon; summer easterlies are normal, not a collapse.
    season_start_month: int = Field(default=11, ge=1, le=12)
    season_end_month: int = Field(default=3, ge=1, le=12)
    reversal_ms: float = Field(default=0.0, description="u below this = SSW underway")
    weak_ms: float = Field(default=15.0, description="u below this = notably weak vortex")


class ScheduleConfig(BaseModel):
    """The `skywatch watch` daemon: snapshots on new model cycles, briefings at
    anchors or on material change."""

    model_config = ConfigDict(extra="forbid")

    poll_minutes: int = Field(default=30, ge=5, description="How often to check model metadata")
    anchors: list[str] = Field(default=["06:30", "16:30"],
                               description="Local times that always get an LLM briefing")
    materiality_drift_c: float = Field(default=2.0, gt=0)
    min_brief_gap_minutes: int = Field(default=90, ge=0)
    cache_history_days: int = Field(default=30, ge=1, description="Prune raw history older")


class SkillConfig(BaseModel):
    """Forecast verification: score each model against ERA5 observations."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    window_days: int = Field(default=60, ge=7, le=365, description="Verification lookback")
    provisional_below_n: int = Field(default=20, ge=1, description="Mark ranks provisional")
    # Samples from the same day are correlated; require a real span of weather too.
    provisional_below_days: int = Field(default=14, ge=1)


class GridMapConfig(BaseModel):
    """UK grid sampling for the dashboard weather maps.

    One multi-location request per run (verified: 546 points x 4 models x
    3 variables x 7 days in ~0.5 s). Step is degrees; 0.5 ~ 35 km.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    lat_min: float = 49.5
    lat_max: float = 59.5
    lon_min: float = -10.5
    lon_max: float = 2.0
    step: float = Field(default=0.5, ge=0.25, le=2.0)
    days: int = Field(default=7, ge=2, le=16)


class ClimatologyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_year: int = Field(default=1991)
    end_year: int = Field(default=2020)
    window_days: int = Field(default=3, ge=0, le=15, description="+/- days around target date")


class ServeConfig(BaseModel):
    """The skywatch serve HTTP server (dashboard + alerts over Tailscale)."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(
        default="tailscale",
        description="'tailscale' auto-detects this machine's 100.x address; "
        "or give an IP / 0.0.0.0 / hostname explicitly.",
    )
    port: int = Field(default=8092, ge=1, le=65535)


class StormwatchConfig(BaseModel):
    """Real-time lightning watcher (the `skywatch stormwatch` daemon).

    Strikes come from the Blitzortung community network's live feed. Radii in
    miles because the alert reads "within 5 miles of home".
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    alert_radius_miles: float = Field(default=5.0, gt=0)
    approach_radius_miles: float = Field(default=25.0, gt=0)
    monitor_radius_miles: float = Field(default=100.0, gt=0)
    window_minutes: int = Field(default=60, ge=10, description="Strike buffer length")
    overhead_cooldown_minutes: int = Field(default=15, ge=1)
    approach_cooldown_minutes: int = Field(default=30, ge=1)
    evaluate_every_seconds: int = Field(default=30, ge=5)
    servers: list[str] = Field(default=[
        "wss://ws1.blitzortung.org",
        "wss://ws7.blitzortung.org",
        "wss://ws8.blitzortung.org",
    ])


class NtfyConfig(BaseModel):
    """Optional push for high-severity alerts only."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    server: str = "https://ntfy.sh"
    topic: str | None = None
    min_severity: str = Field(default="high")


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: Location
    llm: LLMConfig = Field(default_factory=LLMConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    vortex: VortexConfig = Field(default_factory=VortexConfig)
    climatology: ClimatologyConfig = Field(default_factory=ClimatologyConfig)
    gridmap: GridMapConfig = Field(default_factory=GridMapConfig)
    skill: SkillConfig = Field(default_factory=SkillConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    ntfy: NtfyConfig = Field(default_factory=NtfyConfig)
    serve: ServeConfig = Field(default_factory=ServeConfig)
    stormwatch: StormwatchConfig = Field(default_factory=StormwatchConfig)

    forecast_days: int = Field(default=14, ge=1, le=16)
    briefing_days: int = Field(default=7, ge=1, le=16)

    cache_dir: Path = Field(default=Path("cache"))
    output_dir: Path = Field(default=Path("output"))
    state_db: Path = Field(default=Path("state.db"))
    cache_ttl_minutes: int = Field(default=60, ge=0)

    def resolve_paths(self, root: Path) -> None:
        """Make relative paths relative to the config file's directory, not the CWD.

        Matters because launchd runs the app from an arbitrary working directory.
        """
        for attr in ("cache_dir", "output_dir", "state_db"):
            p: Path = getattr(self, attr)
            if not p.is_absolute():
                object.__setattr__(self, attr, (root / p).resolve())


DEFAULT_CONFIG_NAME = "config.yaml"


def find_config(explicit: Path | None = None) -> Path:
    """Locate the config file: explicit path, then $SKYWATCH_CONFIG, then package root."""
    if explicit is not None:
        return explicit
    env = os.environ.get("SKYWATCH_CONFIG")
    if env:
        return Path(env)
    return project_root() / DEFAULT_CONFIG_NAME


def project_root() -> Path:
    """Repo root, derived from this file's location (src/skywatch/config.py)."""
    return Path(__file__).resolve().parents[2]


def load_dotenv_file(root: Path) -> None:
    """Load <root>/.env into the environment without overriding real env vars."""
    from dotenv import load_dotenv

    load_dotenv(root / ".env", override=False)


def _env(name: str) -> str | None:
    v = os.environ.get(f"SKYWATCH_{name}")
    return v.strip() if v and v.strip() else None


def _set(raw: dict[str, Any], section: str, key: str, value: Any) -> None:
    raw.setdefault(section, {})
    if raw[section] is None:
        raw[section] = {}
    raw[section][key] = value


def apply_env_overrides(raw: dict[str, Any], *, cache_dir: Path) -> dict[str, Any]:
    """Overlay SKYWATCH_* environment values onto the raw YAML mapping.

    Personal/deployment values only; tunables stay in YAML. Location strings
    are resolved via geocode.resolve (cached under cache_dir/geocode.json).
    """
    if loc := _env("LOCATION"):
        from skywatch.geocode import resolve

        place = resolve(loc, cache_file=cache_dir / "geocode.json")
        _set(raw, "location", "latitude", place.latitude)
        _set(raw, "location", "longitude", place.longitude)
        _set(raw, "location", "name", _env("LOCATION_NAME") or place.name)
        if tz := (_env("TIMEZONE") or place.timezone):
            _set(raw, "location", "timezone", tz)
    else:
        if name := _env("LOCATION_NAME"):
            _set(raw, "location", "name", name)
        if tz := _env("TIMEZONE"):
            _set(raw, "location", "timezone", tz)

    for env_key, section, key in [
        ("LLM_BASE_URL", "llm", "base_url"),
        ("LLM_MODEL", "llm", "model"),
        ("LLM_FAST_MODEL", "llm", "fast_model"),
        ("LLM_API_KEY", "llm", "api_key"),
        ("NTFY_TOPIC", "ntfy", "topic"),
        ("NTFY_SERVER", "ntfy", "server"),
        ("NTFY_MIN_SEVERITY", "ntfy", "min_severity"),
        ("SERVE_HOST", "serve", "host"),
    ]:
        if (v := _env(env_key)) is not None:
            _set(raw, section, key, v)
    if (v := _env("SERVE_PORT")) is not None:
        _set(raw, "serve", "port", int(v))
    if (v := _env("NTFY_ENABLED")) is not None:
        _set(raw, "ntfy", "enabled", v.lower() in ("1", "true", "yes", "on"))
    elif _env("NTFY_TOPIC"):
        _set(raw, "ntfy", "enabled", True)   # naming a topic means you want pushes
    if (d := _env("DATA_DIR")) is not None:
        raw["cache_dir"] = str(Path(d) / "cache")
        raw["output_dir"] = str(Path(d) / "output")
        raw["state_db"] = str(Path(d) / "state.db")
    return raw


def load_config(path: Path | None = None) -> Config:
    """Load config.yaml, overlay .env / SKYWATCH_* values, validate."""
    cfg_path = find_config(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"No config at {cfg_path}.")
    load_dotenv_file(cfg_path.parent)
    raw: Any = yaml.safe_load(cfg_path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{cfg_path} must contain a YAML mapping at the top level")

    data_dir = _env("DATA_DIR")
    cache_dir = Path(data_dir) / "cache" if data_dir else Path(raw.get("cache_dir", "cache"))
    if not cache_dir.is_absolute():
        cache_dir = cfg_path.parent / cache_dir
    raw = apply_env_overrides(raw, cache_dir=cache_dir)

    if "location" not in raw or raw["location"] is None:
        raise ValueError(
            "No location configured. Set SKYWATCH_LOCATION in .env "
            "(a UK postcode, a place name, or 'lat,lon') or a location: block in config.yaml."
        )
    cfg = Config.model_validate(raw)
    cfg.resolve_paths(cfg_path.parent)
    return cfg
