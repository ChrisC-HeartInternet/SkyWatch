"""Axis ticks must cover the data range — the regression behind lines leaving the plot."""

import pytest

from skywatch.svg import nice_ticks, symmetric_ticks


@pytest.mark.parametrize(
    "lo, hi",
    [(0, 4.3), (0, 0.61), (16.4, 26.0), (-3.2, 5.9), (0, 2.0), (12, 12), (-1.7, 0.9)],
)
def test_ticks_cover_range(lo: float, hi: float) -> None:
    ticks = nice_ticks(lo, hi, 4)
    assert ticks[0] <= lo, (lo, hi, ticks)
    assert ticks[-1] >= hi, (lo, hi, ticks)
    assert len(ticks) >= 2


def test_ticks_are_evenly_spaced_and_not_excessive() -> None:
    ticks = nice_ticks(0, 4.3, 4)
    steps = {round(b - a, 6) for a, b in zip(ticks, ticks[1:], strict=False)}
    assert len(steps) == 1
    assert 3 <= len(ticks) <= 8


def test_symmetric_ticks_cover_limit() -> None:
    ticks = symmetric_ticks(6.5)
    assert ticks[0] <= -6.5 and ticks[-1] >= 6.5
