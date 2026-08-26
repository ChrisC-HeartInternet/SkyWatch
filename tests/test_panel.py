from datetime import date

from skywatch.features.panel import panel_median
from skywatch.models import DailySeries, ModelForecast


def _mf(model: str, tmax: list[float | None]) -> ModelForecast:
    return ModelForecast(
        model=model,
        dates=[date(2026, 1, i + 1) for i in range(len(tmax))],
        series={"temperature_2m_max": DailySeries(
            variable="temperature_2m_max", unit="°C", values=tmax)},
    )


def test_median_across_models() -> None:
    p = panel_median([_mf("a", [10.0, 8.0]), _mf("b", [12.0, 9.0]), _mf("c", [11.0, None])])
    vals = p.series["temperature_2m_max"].values
    assert vals[0] == 11.0
    assert vals[1] == 8.5     # median of two remaining models
    assert p.model == "panel_median"


def test_ragged_horizons_use_longest_dates() -> None:
    p = panel_median([_mf("a", [10.0, 11.0, 12.0]), _mf("b", [9.0])])
    assert len(p.dates) == 3
    vals = p.series["temperature_2m_max"].values
    assert vals[0] == 9.5
    assert vals[2] == 12.0    # only model a reaches day 3


def test_all_null_day_stays_none() -> None:
    p = panel_median([_mf("a", [None]), _mf("b", [None])])
    assert p.series["temperature_2m_max"].values == [None]
