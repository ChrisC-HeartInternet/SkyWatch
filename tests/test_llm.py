"""LLM layer tests with the OpenAI client mocked — no network, no model."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

from skywatch.alerts import mechanical_alerts, parse_llm_alerts
from skywatch.config import Config, LLMConfig, Location
from skywatch.features.enso import EnsoStatus
from skywatch.features.thresholds import ThresholdEvent
from skywatch.features.vortex import VortexStatus
from skywatch.llm import write_alerts, write_briefing
from skywatch.models import Severity


def _cfg() -> Config:
    return Config(
        location=Location(name="Testville", latitude=52.0, longitude=-1.0),
        llm=LLMConfig(base_url="http://localhost:1", model="big", fast_model="small"),
    )


def _event(**kw) -> ThresholdEvent:
    base = dict(
        date=date(2026, 1, 2), category="wind", severity="high",
        metric="wind_gusts_10m_max", value=65.0, threshold=60.0, unit="mph",
        models=["ecmwf", "gfs"], agreement="2/4",
    )
    base.update(kw)
    return ThresholdEvent(**base)


def _enso(anom: float = 0.3) -> EnsoStatus:
    return EnsoStatus(week=date(2026, 1, 1), sst=27.0, anomaly=anom,
                      classification="neutral")


def _vortex(u: float = 30.0, state: str = "normal") -> VortexStatus:
    return VortexStatus(
        date=date(2026, 1, 2), current_u=u, in_season=True, state=state,
        trend="steady", climate_normal=35.0, climate_anomaly=u - 35.0,
        forecast_min_u=u,
    )


def _completion(text: str) -> MagicMock:
    resp = MagicMock()
    resp.choices[0].message.content = text
    return resp


VALID_ALERT_JSON = json.dumps({"alerts": [{
    "severity": "high", "category": "wind", "title": "Gale Friday",
    "detail": "Gusts to 65 mph, 2/4 models.", "confidence": 0.6,
    "valid_from": "2026-01-02", "valid_to": "2026-01-02",
    "sources": ["ecmwf", "gfs"],
}]})


def test_briefing_success() -> None:
    with patch("skywatch.llm._client") as client:
        client.return_value.chat.completions.create.return_value = _completion(
            "# Weather briefing\nAll quiet."
        )
        text, ok = write_briefing(_cfg(), {"meta": {}})
    assert ok and text.startswith("# Weather briefing")


def test_briefing_unreachable_degrades() -> None:
    with patch("skywatch.llm._client") as client:
        client.return_value.chat.completions.create.side_effect = ConnectionError("down")
        text, ok = write_briefing(_cfg(), {"meta": {}})
    assert not ok
    assert "Briefing unavailable" in text
    assert "down" in text


def test_briefing_strips_think_blocks() -> None:
    with patch("skywatch.llm._client") as client:
        client.return_value.chat.completions.create.return_value = _completion(
            "<think>hmm let me reason</think># Weather briefing\nText."
        )
        text, ok = write_briefing(_cfg(), {"meta": {}})
    assert ok and text.startswith("# Weather briefing")
    assert "hmm" not in text


def test_alerts_llm_path() -> None:
    with patch("skywatch.llm._client") as client:
        client.return_value.chat.completions.create.return_value = _completion(
            VALID_ALERT_JSON
        )
        alerts, mode = write_alerts(_cfg(), {}, [_event()], _enso(), _vortex())
    assert mode == "llm"
    assert alerts[0].severity == Severity.HIGH
    assert alerts[0].valid_from == date(2026, 1, 2)


def test_alerts_invalid_then_valid_retries_once() -> None:
    with patch("skywatch.llm._client") as client:
        client.return_value.chat.completions.create.side_effect = [
            _completion("not json at all"),
            _completion(VALID_ALERT_JSON),
        ]
        alerts, mode = write_alerts(_cfg(), {}, [_event()], _enso(), _vortex())
    assert mode == "llm"
    assert client.return_value.chat.completions.create.call_count == 2


def test_alerts_twice_invalid_falls_back_to_mechanical() -> None:
    with patch("skywatch.llm._client") as client:
        client.return_value.chat.completions.create.return_value = _completion("junk")
        alerts, mode = write_alerts(_cfg(), {}, [_event()], _enso(), _vortex())
    assert mode == "mechanical"
    assert alerts and alerts[0].category == "wind"


def test_alerts_llm_inventing_category_rejected() -> None:
    invented = json.dumps({"alerts": [json.loads(VALID_ALERT_JSON)["alerts"][0] | {
        "category": "tornado"}]})
    with patch("skywatch.llm._client") as client:
        client.return_value.chat.completions.create.return_value = _completion(invented)
        alerts, mode = write_alerts(_cfg(), {}, [_event()], _enso(), _vortex())
    assert mode == "mechanical"


def test_no_events_no_llm_call() -> None:
    with patch("skywatch.llm._client") as client:
        alerts, mode = write_alerts(_cfg(), {}, [], _enso(0.1), _vortex())
    assert alerts == [] and mode == "mechanical"
    client.assert_not_called()


# --- mechanical path ---------------------------------------------------------

def test_mechanical_merges_consecutive_days() -> None:
    evs = [
        _event(date=date(2026, 1, 2)),
        _event(date=date(2026, 1, 3), value=70.0),
        _event(date=date(2026, 1, 6), severity="moderate", value=50.0),
    ]
    alerts = mechanical_alerts(evs, None, None)
    assert len(alerts) == 2
    first = alerts[0]
    assert first.valid_from == date(2026, 1, 2) and first.valid_to == date(2026, 1, 3)
    assert first.severity == Severity.HIGH
    assert "70.0" in first.detail


def test_mechanical_driver_alerts() -> None:
    alerts = mechanical_alerts([], _enso(2.7), _vortex(-2.0, "reversed (SSW underway)"))
    cats = {a.category for a in alerts}
    assert cats == {"enso", "stratosphere"}
    ssw = next(a for a in alerts if a.category == "stratosphere")
    assert ssw.severity == Severity.HIGH
    assert "2-6 weeks" in ssw.detail


def test_quiet_drivers_no_alerts() -> None:
    assert mechanical_alerts([], _enso(0.3), _vortex(30.0)) == []


def test_parse_tolerates_fences_and_bare_arrays() -> None:
    fenced = f"```json\n{VALID_ALERT_JSON}\n```"
    assert parse_llm_alerts(fenced)[0].category == "wind"
    bare = json.dumps(json.loads(VALID_ALERT_JSON)["alerts"])
    assert parse_llm_alerts(bare)[0].category == "wind"
