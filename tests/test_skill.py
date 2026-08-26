"""Forecast verification: MAE/bias per model per lead bucket, ranks, honesty flags."""

from datetime import UTC, date, datetime

from skywatch.features.skill import LEAD_BUCKETS, model_skill

TMAX = "temperature_2m_max"


def _row(run_day: int, model: str, target_day: int, value: float,
         var: str = TMAX) -> tuple[datetime, str, str, date, float]:
    return (
        datetime(2026, 8, run_day, 5, 30, tzinfo=UTC),
        model, var, date(2026, 8, target_day), value,
    )


def _obs(days: dict[int, float], var: str = TMAX) -> dict[date, dict[str, float]]:
    return {date(2026, 8, d): {var: v} for d, v in days.items()}


def test_mae_and_bias_exact() -> None:
    rows = [
        _row(10, "a", 11, 20.0),  # lead 1, obs 18 -> err +2
        _row(10, "a", 12, 16.0),  # lead 2, obs 18 -> err -2
        _row(10, "b", 11, 19.0),  # lead 1, obs 18 -> err +1
        _row(10, "b", 12, 18.0),  # lead 2, obs 18 -> err  0
    ]
    s = model_skill(rows, _obs({11: 18.0, 12: 18.0}), provisional_below_n=1)
    a = s.by_model["a"].variables[TMAX].buckets["0-2"]
    b = s.by_model["b"].variables[TMAX].buckets["0-2"]
    assert a.n == 2 and a.mae == 2.0 and a.bias == 0.0
    assert b.n == 2 and b.mae == 0.5 and b.bias == 0.5


def test_lead_buckets_assignment() -> None:
    rows = [
        _row(10, "a", 10, 20.0),   # lead 0 -> 0-2
        _row(10, "a", 13, 20.0),   # lead 3 -> 3-4
        _row(10, "a", 16, 20.0),   # lead 6 -> 5-7
        _row(10, "a", 19, 20.0),   # lead 9 -> 8+
    ]
    obs = _obs({10: 19.0, 13: 19.0, 16: 19.0, 19: 19.0})
    s = model_skill(rows, obs, provisional_below_n=1)
    buckets = s.by_model["a"].variables[TMAX].buckets
    assert [buckets[b].n for b in LEAD_BUCKETS] == [1, 1, 1, 1]


def test_unobserved_and_unmatched_rows_excluded() -> None:
    rows = [
        _row(10, "a", 11, 20.0),          # observed
        _row(10, "a", 30, 25.0),          # target not yet observed
        _row(10, "a", 11, 5.0, var="snowfall_sum"),  # variable never observed
    ]
    s = model_skill(rows, _obs({11: 18.0}), provisional_below_n=1)
    assert s.by_model["a"].variables[TMAX].buckets["0-2"].n == 1
    assert "snowfall_sum" not in s.by_model["a"].variables
    assert s.total_samples == 1


def test_rank_and_skill_vs_panel_median() -> None:
    rows = []
    for target in (11, 12, 13):        # three verified days, lead 1-3
        rows.append(_row(10, "ecmwf", target, 19.0))         # err 1.0
        rows.append(_row(10, "gfs", target, 22.0))           # err 4.0
        rows.append(_row(10, "panel_median", target, 20.0))  # err 2.0
    s = model_skill(rows, _obs({11: 18.0, 12: 18.0, 13: 18.0}), provisional_below_n=1)
    ov_e = s.by_model["ecmwf"].variables[TMAX].overall
    ov_g = s.by_model["gfs"].variables[TMAX].overall
    assert ov_e.rank == 1 and ov_g.rank == 3
    assert ov_e.vs_median_pct == 50.0    # MAE 1.0 vs median's 2.0 = 50% better
    assert ov_g.vs_median_pct == -100.0
    assert not ov_e.provisional


def test_provisional_flag_small_samples() -> None:
    rows = [_row(10, "a", 11, 20.0)]
    s = model_skill(rows, _obs({11: 18.0}), provisional_below_n=20)
    assert s.by_model["a"].variables[TMAX].overall.provisional


def test_provisional_when_many_samples_but_few_days() -> None:
    # 30 samples all verifying the same two days: enough n, not enough weather.
    rows = [_row(run_day, "a", target, 20.0)
            for run_day in range(1, 16) for target in (16, 17)]
    s = model_skill(rows, _obs({16: 19.0, 17: 19.0}),
                    provisional_below_n=20, provisional_below_days=14)
    ov = s.by_model["a"].variables[TMAX].overall
    assert ov.n == 30 and ov.n_days == 2
    assert ov.provisional


def test_empty_inputs() -> None:
    s = model_skill([], {}, provisional_below_n=20)
    assert s.by_model == {} and s.total_samples == 0


def test_midnight_boundary_lead() -> None:
    # A 23:30 UTC run on the 10th forecasting the 11th is lead 1, not 0.
    rows = [(datetime(2026, 8, 10, 23, 30, tzinfo=UTC), "a", TMAX, date(2026, 8, 11), 20.0)]
    s = model_skill(rows, _obs({11: 20.0}), provisional_below_n=1)
    assert s.by_model["a"].variables[TMAX].buckets["0-2"].n == 1
    assert s.by_model["a"].variables[TMAX].buckets["0-2"].mae == 0.0
