"""Threshold alerts: deterministic facts computed from forecasts + ensemble stats."""

from datetime import date

from skywatch.config import Thresholds
from skywatch.features.thresholds import threshold_events
from skywatch.models import DailySeries, ModelForecast


def _mf(model: str, **series: list[float | None]) -> ModelForecast:
    n = max(len(v) for v in series.values())
    return ModelForecast(
        model=model,
        dates=[date(2026, 1, i + 1) for i in range(n)],
        series={
            var: DailySeries(variable=var, unit="", values=vals)
            for var, vals in series.items()
        },
    )


TH = Thresholds()


def test_gust_alert_two_tiers() -> None:
    fc = _mf("gfs", wind_gusts_10m_max=[30.0, 50.0, 70.0])
    events = threshold_events([fc], {}, TH)
    gusts = [e for e in events if e.category == "wind"]
    assert len(gusts) == 2
    assert gusts[0].date == date(2026, 1, 2) and gusts[0].severity == "moderate"
    assert gusts[1].date == date(2026, 1, 3) and gusts[1].severity == "high"
    assert "gfs" in gusts[1].models


def test_multi_model_agreement_raises_confidence() -> None:
    a = _mf("ecmwf", wind_gusts_10m_max=[62.0])
    b = _mf("gfs", wind_gusts_10m_max=[65.0])
    c = _mf("icon", wind_gusts_10m_max=[40.0])
    events = threshold_events([a, b, c], {}, TH)
    e = next(e for e in events if e.category == "wind")
    assert set(e.models) == {"ecmwf", "gfs"}
    assert e.agreement == "2/3"


def test_snowfall_and_null_days() -> None:
    fc = _mf("ecmwf", snowfall_sum=[0.0, 5.0, None])
    events = threshold_events([fc], {}, TH)
    snow = [e for e in events if e.category == "snow"]
    assert len(snow) == 1 and snow[0].date == date(2026, 1, 2)


def test_missing_variable_is_fine() -> None:
    fc = _mf("ukmo", temperature_2m_max=[5.0])
    assert threshold_events([fc], {}, TH) == []


def test_ensemble_probability_events() -> None:
    probs = {
        ("frost", date(2026, 1, 2)): 45.0,
        ("frost", date(2026, 1, 3)): 10.0,   # below threshold: no event
        ("snow", date(2026, 1, 2)): 25.0,
    }
    events = threshold_events([], probs, TH)
    cats = {(e.category, e.date) for e in events}
    assert ("frost_risk", date(2026, 1, 2)) in cats
    assert ("snow_risk", date(2026, 1, 2)) in cats
    assert ("frost_risk", date(2026, 1, 3)) not in cats


def test_temperature_anomaly_events() -> None:
    from skywatch.features.anomaly import DayAnomaly
    from skywatch.features.thresholds import anomaly_events

    days = [
        DayAnomaly(date=date(2026, 1, 1), tmax_anomaly=7.5, tmax_sigma=2.5),
        DayAnomaly(date=date(2026, 1, 2), tmax_anomaly=-8.0, tmax_sigma=-2.7),
        DayAnomaly(date=date(2026, 1, 3), tmax_anomaly=2.0, tmax_sigma=0.7),
        DayAnomaly(date=date(2026, 1, 4)),  # no data
    ]
    events = anomaly_events(days, TH)
    assert len(events) == 2
    heat, cold = events[0], events[1]
    assert heat.category == "heat" and heat.date == date(2026, 1, 1) and heat.value == 7.5
    assert cold.category == "cold" and cold.date == date(2026, 1, 2) and cold.value == -8.0
