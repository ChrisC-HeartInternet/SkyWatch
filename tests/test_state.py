"""StateDB round-trip: record runs, read back history for trends."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from skywatch.features.trends import forecast_trends
from skywatch.models import DailySeries, ModelForecast
from skywatch.state import StateDB


def _panel(tmax: list[float], start: date) -> ModelForecast:
    return ModelForecast(
        model="panel_median",
        dates=[start + timedelta(days=i) for i in range(len(tmax))],
        series={"temperature_2m_max": DailySeries(
            variable="temperature_2m_max", unit="°C", values=list(tmax))},
    )


def test_record_and_history_roundtrip(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    start = date(2026, 1, 10)
    t0 = datetime(2026, 1, 8, 6, 30, tzinfo=UTC)
    # three runs, each colder for the target date
    for i, tmax in enumerate([8.0, 6.5, 5.0]):
        db.record_run(t0 + timedelta(hours=12 * i), "Testville",
                      [], _panel([tmax, tmax + 1], start))
    assert db.run_count() == 3

    hist = db.history([start])
    pts = hist[(start, "temperature_2m_max")]
    assert [p.value for p in pts] == [8.0, 6.5, 5.0]

    trends = forecast_trends(hist, max_runs=6)
    t = next(t for t in trends if t.variable == "temperature_2m_max"
             and t.target_date == start)
    assert t.delta == -3.0
    assert t.direction == "falling"


def test_history_empty_on_first_run(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    assert db.history([date(2026, 1, 10)]) == {}
    assert db.run_count() == 0


def test_history_filters_model_and_dates(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    start = date(2026, 1, 10)
    other = _panel([3.0], start)
    other = other.model_copy(update={"model": "gfs_seamless"})
    db.record_run(datetime(2026, 1, 8, 6, 30, tzinfo=UTC), "T",
                  [other], _panel([8.0], start))
    hist = db.history([start])
    assert all(k[0] == start for k in hist)
    # default model filter: only panel_median rows come back
    assert hist[(start, "temperature_2m_max")][0].value == 8.0
    assert db.history([date(2030, 1, 1)]) == {}


def test_max_runs_window(tmp_path: Path) -> None:
    db = StateDB(tmp_path / "state.db")
    start = date(2026, 1, 10)
    t0 = datetime(2026, 1, 1, 6, 30, tzinfo=UTC)
    for i in range(10):
        db.record_run(t0 + timedelta(hours=12 * i), "T", [], _panel([float(i)], start))
    pts = db.history([start], max_runs=4)[(start, "temperature_2m_max")]
    assert [p.value for p in pts] == [6.0, 7.0, 8.0, 9.0]
