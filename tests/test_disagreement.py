"""Inter-model disagreement: scored divergence that never mistakes a shrinking
model panel for convergence."""

from datetime import date

from skywatch.features.disagreement import model_disagreement
from skywatch.models import DailySeries, ModelForecast


def _mf(model: str, tmax: list[float | None]) -> ModelForecast:
    n = len(tmax)
    return ModelForecast(
        model=model,
        dates=[date(2026, 1, i + 1) for i in range(n)],
        series={
            "temperature_2m_max": DailySeries(
                variable="temperature_2m_max", unit="°C", values=tmax
            )
        },
    )


def test_agreeing_models_score_low() -> None:
    fcs = [_mf("a", [10.0, 11.0]), _mf("b", [10.2, 11.1]), _mf("c", [9.8, 10.9])]
    result = model_disagreement(fcs, divergence_thresholds={"temperature_2m_max": 4.0})
    d = result.days[0].variables["temperature_2m_max"]
    assert d.n_models == 3
    assert d.range < 1.0
    assert not d.flagged


def test_diverging_models_flagged() -> None:
    fcs = [_mf("a", [10.0]), _mf("b", [15.0]), _mf("c", [10.5])]
    result = model_disagreement(fcs, divergence_thresholds={"temperature_2m_max": 4.0})
    d = result.days[0].variables["temperature_2m_max"]
    assert d.range == 5.0
    assert d.flagged


def test_shrinking_panel_is_reported_not_hidden() -> None:
    # 4 models for 2 days, then only 2 — must NOT read as convergence.
    fcs = [
        _mf("ecmwf", [10.0, 11.0, 12.0, 13.0]),
        _mf("gfs",   [14.0, 15.0, 12.5, 13.5]),
        _mf("icon",  [11.0, 12.0, None, None]),
        _mf("ukmo",  [12.0, 13.0, None, None]),
    ]
    result = model_disagreement(fcs, divergence_thresholds={"temperature_2m_max": 4.0})
    assert result.days[1].variables["temperature_2m_max"].n_models == 4
    assert result.days[2].variables["temperature_2m_max"].n_models == 2
    assert 2 in result.panel_changes  # day index where the panel shrank
    assert result.days[2].variables["temperature_2m_max"].models_present == ["ecmwf", "gfs"]


def test_single_model_is_undefined_not_zero() -> None:
    fcs = [_mf("gfs", [10.0]), _mf("icon", [None])]
    result = model_disagreement(fcs, divergence_thresholds={"temperature_2m_max": 4.0})
    d = result.days[0].variables["temperature_2m_max"]
    assert d.n_models == 1
    assert d.range is None
    assert not d.flagged


def test_ragged_lengths_tolerated() -> None:
    # Models legitimately return different series lengths; missing tail = absent.
    fcs = [_mf("a", [10.0, 11.0, 12.0]), _mf("b", [10.5])]
    result = model_disagreement(fcs, divergence_thresholds={"temperature_2m_max": 4.0})
    assert len(result.days) == 3
    assert result.days[0].variables["temperature_2m_max"].n_models == 2
    assert result.days[2].variables["temperature_2m_max"].n_models == 1
