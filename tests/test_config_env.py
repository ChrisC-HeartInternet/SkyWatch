"""Env-over-YAML config layer and the location parser (no network)."""

from pathlib import Path

import pytest

from skywatch.config import apply_env_overrides, load_config
from skywatch.geocode import is_uk_postcode, parse_latlon


def test_parse_latlon_forms() -> None:
    assert parse_latlon("54.43, -2.96") == (54.43, -2.96)
    assert parse_latlon("54.43,-2.96") == (54.43, -2.96)
    assert parse_latlon("Ambleside") is None
    with pytest.raises(ValueError):
        parse_latlon("95, 0")


def test_uk_postcode_detection() -> None:
    assert is_uk_postcode("LA22 9BU")
    assert is_uk_postcode("la229bu")
    assert is_uk_postcode("SW1A 1AA")
    assert not is_uk_postcode("Ambleside")
    assert not is_uk_postcode("12345")


def test_env_overrides_scalars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SKYWATCH_LLM_BASE_URL", "http://ollama:11434/v1")
    monkeypatch.setenv("SKYWATCH_LLM_MODEL", "big")
    monkeypatch.setenv("SKYWATCH_NTFY_TOPIC", "my-secret-topic")
    monkeypatch.setenv("SKYWATCH_SERVE_PORT", "9000")
    monkeypatch.setenv("SKYWATCH_DATA_DIR", "/data")
    raw = apply_env_overrides({"ntfy": {"enabled": False}}, cache_dir=tmp_path)
    assert raw["llm"] == {"base_url": "http://ollama:11434/v1", "model": "big"}
    assert raw["ntfy"]["topic"] == "my-secret-topic"
    assert raw["ntfy"]["enabled"] is True        # naming a topic switches pushes on
    assert raw["serve"]["port"] == 9000
    assert raw["output_dir"] == "/data/output" and raw["state_db"] == "/data/state.db"


def test_ntfy_enabled_false_wins_over_topic(monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path) -> None:
    monkeypatch.setenv("SKYWATCH_NTFY_TOPIC", "t")
    monkeypatch.setenv("SKYWATCH_NTFY_ENABLED", "false")
    raw = apply_env_overrides({}, cache_dir=tmp_path)
    assert raw["ntfy"]["enabled"] is False


def test_location_from_coordinates_env(monkeypatch: pytest.MonkeyPatch,
                                       tmp_path: Path) -> None:
    monkeypatch.setenv("SKYWATCH_LOCATION", "54.43, -2.96")
    monkeypatch.setenv("SKYWATCH_LOCATION_NAME", "Ambleside")
    raw = apply_env_overrides({}, cache_dir=tmp_path)
    assert raw["location"]["latitude"] == 54.43
    assert raw["location"]["name"] == "Ambleside"


def test_load_config_env_only_no_yaml_location(monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("forecast_days: 10\n")
    monkeypatch.setenv("SKYWATCH_LOCATION", "54.43,-2.96")
    monkeypatch.setenv("SKYWATCH_LOCATION_NAME", "Test")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.location.name == "Test" and cfg.forecast_days == 10
    assert cfg.cache_dir == (tmp_path / "cache").resolve()


def test_load_config_without_any_location_fails(monkeypatch: pytest.MonkeyPatch,
                                                tmp_path: Path) -> None:
    for var in ("SKYWATCH_LOCATION", "SKYWATCH_LOCATION_NAME"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "config.yaml").write_text("forecast_days: 7\n")
    with pytest.raises(ValueError, match="No location configured"):
        load_config(tmp_path / "config.yaml")


def test_dotenv_file_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SKYWATCH_LOCATION", raising=False)
    (tmp_path / "config.yaml").write_text("")
    (tmp_path / ".env").write_text("SKYWATCH_LOCATION=54.43,-2.96\nSKYWATCH_LOCATION_NAME=Env\n")
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.location.name == "Env"
