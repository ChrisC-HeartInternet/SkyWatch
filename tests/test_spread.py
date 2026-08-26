"""Ensemble spread: std dev / percentiles per day, uncertainty-explosion flags,
and member-based event probabilities."""

from datetime import date

from skywatch.features.spread import ensemble_stats, event_probability
from skywatch.models import EnsembleForecast


def _ef(members: list[list[float | None]], variable: str = "temperature_2m_max",
        n_days: int | None = None) -> EnsembleForecast:
    n = n_days or len(members[0])
    return EnsembleForecast(
        model="test_ens", variable=variable, unit="°C",
        dates=[date(2026, 1, i + 1) for i in range(n)],
        members=members,
    )


def test_basic_stats() -> None:
    ef = _ef([[10.0, 12.0], [12.0, 16.0], [14.0, 20.0]])
    stats = ensemble_stats(ef, spread_jump_factor=2.0)
    d0 = stats.days[0]
    assert d0.n_members == 3
    assert d0.median == 12.0
    assert abs(d0.std - 2.0) < 1e-9
    assert d0.p10 <= d0.median <= d0.p90


def test_missing_members_are_excluded_per_day() -> None:
    ef = _ef([[10.0, None], [12.0, 14.0], [None, 18.0]])
    stats = ensemble_stats(ef, spread_jump_factor=2.0)
    assert stats.days[0].n_members == 2
    assert stats.days[1].n_members == 2


def test_identical_members_zero_spread() -> None:
    ef = _ef([[5.0, 5.0], [5.0, 5.0], [5.0, 5.0]])
    stats = ensemble_stats(ef, spread_jump_factor=2.0)
    assert stats.days[0].std == 0.0
    assert stats.days[0].p10 == stats.days[0].p90 == 5.0
    assert not stats.days[0].spread_jump


def test_single_member_has_no_std() -> None:
    ef = _ef([[7.0], [None]], n_days=1)
    stats = ensemble_stats(ef, spread_jump_factor=2.0)
    assert stats.days[0].n_members == 1
    assert stats.days[0].std is None
    assert stats.days[0].median == 7.0


def test_fully_null_day() -> None:
    ef = _ef([[10.0, None], [11.0, None]])
    stats = ensemble_stats(ef, spread_jump_factor=2.0)
    assert stats.days[1].n_members == 0
    assert stats.days[1].median is None
    assert stats.days[1].std is None
    assert not stats.days[1].spread_jump


def test_uncertainty_explosion_flag() -> None:
    # Day 0 tight (std=1), day 1 wide (std well over 2x) -> flag on day 1.
    tight_then_wide = [[10.0, 4.0], [11.0, 10.0], [12.0, 16.0], [11.0, 22.0]]
    stats = ensemble_stats(_ef(tight_then_wide), spread_jump_factor=2.0)
    assert not stats.days[0].spread_jump
    assert stats.days[1].spread_jump


def test_no_flag_when_growth_from_zero_but_tiny() -> None:
    # zero -> 0.2 std is growth by an infinite factor but meteorologically nothing.
    ef = _ef([[5.0, 5.1], [5.0, 4.9], [5.0, 5.2]])
    stats = ensemble_stats(ef, spread_jump_factor=2.0)
    assert not stats.days[1].spread_jump


def test_event_probability_frost() -> None:
    ef = _ef([[-1.0, 2.0], [-0.5, -3.0], [1.0, 0.5], [2.0, -1.5]],
             variable="temperature_2m_min")
    p = event_probability(ef, lambda v: v < 0.0)
    assert p[0] == 50.0   # 2 of 4 below zero
    assert p[1] == 50.0
    # nulls excluded from the denominator
    ef2 = _ef([[None, -1.0], [1.0, -2.0]], variable="temperature_2m_min")
    p2 = event_probability(ef2, lambda v: v < 0.0)
    assert p2[0] == 0.0
    assert p2[1] == 100.0


def test_event_probability_fully_null_day_is_none() -> None:
    ef = _ef([[None], [None]], n_days=1)
    p = event_probability(ef, lambda v: v < 0.0)
    assert p[0] is None


def test_negative_snowfall_clamped() -> None:
    # GFS emits tiny negative snowfall values (verified live); prob of "snow > 0.5"
    # must not be poisoned and stats must clamp to 0.
    ef = _ef([[-0.1, 0.0], [0.0, 0.0], [-0.05, 1.0]], variable="snowfall_sum")
    stats = ensemble_stats(ef, spread_jump_factor=2.0)
    assert stats.days[0].median == 0.0
    p = event_probability(ef, lambda v: v > 0.5)
    assert p[0] == 0.0
    assert abs(p[1] - 33.3) < 0.1
