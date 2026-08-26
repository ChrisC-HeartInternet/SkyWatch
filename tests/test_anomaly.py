"""Anomalies vs climatology: forecast minus what's normal for that date."""

from datetime import date

from skywatch.features.anomaly import daily_anomalies
from skywatch.models import ClimatologyDay, DailySeries, ModelForecast


def _climo() -> dict[tuple[int, int], ClimatologyDay]:
    out: dict[tuple[int, int], ClimatologyDay] = {}
    for m, d in [(12, 30), (12, 31), (1, 1), (1, 2), (2, 29)]:
        out[(m, d)] = ClimatologyDay(
            month=m, day=d, tmax_mean=7.0, tmax_std=3.0, tmin_mean=2.0, tmin_std=2.5,
            precip_mean=2.0, precip_p90=6.0, n_samples=210,
        )
    return out


def _panel(dates: list[date], tmax: list[float | None], tmin: list[float | None]) -> ModelForecast:
    return ModelForecast(
        model="panel_median", dates=dates,
        series={
            "temperature_2m_max": DailySeries(
                variable="temperature_2m_max", unit="°C", values=tmax),
            "temperature_2m_min": DailySeries(
                variable="temperature_2m_min", unit="°C", values=tmin),
        },
    )


def test_anomaly_and_sigma() -> None:
    fc = _panel([date(2026, 1, 1)], [13.0], [2.0])
    days = daily_anomalies(fc, _climo())
    a = days[0]
    assert a.tmax_anomaly == 6.0
    assert a.tmax_sigma == 2.0        # 6.0 / std 3.0
    assert a.tmin_anomaly == 0.0


def test_year_boundary() -> None:
    dates = [date(2025, 12, 31), date(2026, 1, 1)]
    days = daily_anomalies(_panel(dates, [7.0, 4.0], [None, None]), _climo())
    assert days[0].tmax_anomaly == 0.0
    assert days[1].tmax_anomaly == -3.0


def test_feb29_uses_leap_entry() -> None:
    days = daily_anomalies(_panel([date(2024, 2, 29)], [10.0], [None]), _climo())
    assert days[0].tmax_anomaly == 3.0


def test_missing_forecast_value_gives_none() -> None:
    days = daily_anomalies(_panel([date(2026, 1, 1)], [None], [1.0]), _climo())
    assert days[0].tmax_anomaly is None
    assert days[0].tmin_anomaly == -1.0


def test_missing_climatology_date_gives_none() -> None:
    days = daily_anomalies(_panel([date(2026, 6, 15)], [20.0], [10.0]), _climo())
    assert days[0].tmax_anomaly is None
