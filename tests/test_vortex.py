"""Vortex analysis: SSW flags with a seasonality gate, trend over the forecast."""

from datetime import UTC, date, datetime

from skywatch.config import VortexConfig
from skywatch.features.vortex import analyse_vortex
from skywatch.models import VortexSample
from skywatch.sources.vortex import zonal_component


def _samples(us: list[float], start: tuple[int, int, int] = (2026, 1, 5)) -> list[VortexSample]:
    from datetime import timedelta
    t0 = datetime(*start, 12, 0, tzinfo=UTC)
    return [
        VortexSample(time=t0 + timedelta(days=i), u_ms=u, n_points=12)
        for i, u in enumerate(us)
    ]


_CLIMO = {i: 35.0 for i in range(1, 366)}   # constant winter-ish normal
_CFG = VortexConfig()


def test_zonal_component_signs() -> None:
    # Westerly (from 270 deg) -> positive u; easterly (from 90) -> negative.
    assert zonal_component(10.0, 270.0) > 9.99
    assert zonal_component(10.0, 90.0) < -9.99
    assert abs(zonal_component(10.0, 0.0)) < 1e-9      # northerly: no zonal part
    assert abs(zonal_component(10.0, 360.0)) < 1e-6    # direction wrap


def test_healthy_winter_vortex() -> None:
    v = analyse_vortex(_samples([40.0] * 16), _CLIMO, _CFG, today=date(2026, 1, 5))
    assert v.in_season
    assert v.state == "normal"
    assert not v.reversal_forecast
    assert v.current_u == 40.0


def test_weak_vortex_flagged() -> None:
    v = analyse_vortex(_samples([12.0] * 16), _CLIMO, _CFG, today=date(2026, 1, 5))
    assert v.state == "weak"


def test_reversal_now_is_ssw() -> None:
    v = analyse_vortex(_samples([-5.0] * 16), _CLIMO, _CFG, today=date(2026, 1, 5))
    assert v.state == "reversed (SSW underway)"


def test_forecast_reversal_flagged_with_date() -> None:
    us = [30.0, 25.0, 20.0, 14.0, 8.0, 2.0, -3.0, -6.0] + [-5.0] * 8
    v = analyse_vortex(_samples(us), _CLIMO, _CFG, today=date(2026, 1, 5))
    assert v.reversal_forecast
    assert v.reversal_date == date(2026, 1, 11)   # first day below 0
    assert v.trend == "weakening rapidly"


def test_out_of_season_never_alarms() -> None:
    # August easterlies are climatologically normal; must not read as SSW.
    v = analyse_vortex(
        _samples([-2.0] * 16, start=(2026, 8, 21)),
        {i: -1.7 for i in range(1, 366)},
        _CFG,
        today=date(2026, 8, 21),
    )
    assert not v.in_season
    assert v.state == "out of season (summer easterlies are normal)"
    assert not v.reversal_forecast


def test_november_is_in_season() -> None:
    v = analyse_vortex(_samples([25.0] * 16, start=(2026, 11, 10)), _CLIMO, _CFG,
                       today=date(2026, 11, 10))
    assert v.in_season


def test_anomaly_vs_climatology() -> None:
    v = analyse_vortex(_samples([20.0] * 16), _CLIMO, _CFG, today=date(2026, 1, 5))
    assert v.climate_anomaly == -15.0


def test_partial_longitude_coverage_noted() -> None:
    samples = _samples([30.0] * 16)
    samples[0] = VortexSample(time=samples[0].time, u_ms=30.0, n_points=5)
    v = analyse_vortex(samples, _CLIMO, _CFG, today=date(2026, 1, 5))
    assert v.partial_coverage
