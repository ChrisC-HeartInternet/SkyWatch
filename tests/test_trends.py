"""Trend deltas: how the forecast for a fixed target date drifted across runs."""

from datetime import UTC, date, datetime

from skywatch.features.trends import HistoryPoint, forecast_trends


def _hp(run_iso: str, value: float) -> HistoryPoint:
    return HistoryPoint(
        run_at=datetime.fromisoformat(run_iso).replace(tzinfo=UTC), value=value
    )


def test_cooling_trend_detected() -> None:
    history = {
        (date(2026, 1, 10), "temperature_2m_max"): [
            _hp("2026-01-06T06:30", 8.0),
            _hp("2026-01-06T16:30", 7.0),
            _hp("2026-01-07T06:30", 6.0),
            _hp("2026-01-07T16:30", 5.0),
        ],
    }
    trends = forecast_trends(history, max_runs=4)
    t = trends[0]
    assert t.target_date == date(2026, 1, 10)
    assert t.variable == "temperature_2m_max"
    assert t.delta == -3.0
    assert t.n_runs == 4
    assert t.direction == "falling"


def test_first_run_no_history() -> None:
    history = {(date(2026, 1, 10), "temperature_2m_max"): [_hp("2026-01-07T06:30", 6.0)]}
    trends = forecast_trends(history, max_runs=4)
    assert trends[0].n_runs == 1
    assert trends[0].delta is None
    assert trends[0].direction == "insufficient history"


def test_gap_in_runs_still_works() -> None:
    history = {
        (date(2026, 1, 10), "precipitation_sum"): [
            _hp("2026-01-04T06:30", 2.0),
            _hp("2026-01-07T16:30", 12.0),  # runs missed in between
        ],
    }
    t = forecast_trends(history, max_runs=4)[0]
    assert t.delta == 10.0
    assert t.direction == "rising"
    assert t.n_runs == 2


def test_window_limits_to_recent_runs() -> None:
    pts = [_hp(f"2026-01-0{i}T06:30", float(i)) for i in range(1, 8)]
    history = {(date(2026, 1, 10), "temperature_2m_max"): pts}
    t = forecast_trends(history, max_runs=4)[0]
    # only last 4 runs count: 4.0 -> 7.0
    assert t.delta == 3.0
    assert t.n_runs == 4


def test_steady_forecast_is_steady() -> None:
    history = {
        (date(2026, 1, 10), "wind_gusts_10m_max"): [
            _hp("2026-01-06T06:30", 30.0),
            _hp("2026-01-07T06:30", 30.4),
        ],
    }
    t = forecast_trends(history, max_runs=4)[0]
    assert t.direction == "steady"


def test_unordered_input_is_sorted() -> None:
    history = {
        (date(2026, 1, 10), "temperature_2m_max"): [
            _hp("2026-01-07T06:30", 5.0),
            _hp("2026-01-05T06:30", 9.0),
        ],
    }
    t = forecast_trends(history, max_runs=4)[0]
    assert t.delta == -4.0
