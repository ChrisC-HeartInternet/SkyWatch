"""Run-to-run trend deltas: how the forecast for a fixed target date has moved.

"The weekend has trended 3C colder over the last four runs" is the payoff.
Pure over HistoryPoint lists; state.py supplies them from SQLite.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

# Change smaller than this (in the variable's own unit) counts as steady.
_STEADY_EPSILON = 0.5


class HistoryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_at: datetime
    value: float


class TrendDelta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_date: date
    variable: str
    n_runs: int
    first_value: float | None = None
    latest_value: float | None = None
    delta: float | None = None            # latest - earliest within the window
    direction: str = "insufficient history"  # rising | falling | steady
    values: list[float] = []               # windowed series, oldest first


def forecast_trends(
    history: dict[tuple[date, str], list[HistoryPoint]],
    *,
    max_runs: int = 6,
    since: datetime | None = None,
) -> list[TrendDelta]:
    """Trend per (target_date, variable) over the most recent runs.

    `since` bounds the window in time (several snapshots a day would otherwise
    squash "the last six runs" into a day and a half); max_runs caps the count.
    """
    out: list[TrendDelta] = []
    for (target, variable), points in sorted(history.items()):
        pts = sorted(points, key=lambda p: p.run_at)
        if since is not None:
            pts = [p for p in pts if p.run_at >= since]
        pts = pts[-max_runs:]
        t = TrendDelta(
            target_date=target,
            variable=variable,
            n_runs=len(pts),
            values=[round(p.value, 1) for p in pts],
        )
        if len(pts) >= 2:
            t.first_value = round(pts[0].value, 1)
            t.latest_value = round(pts[-1].value, 1)
            t.delta = round(pts[-1].value - pts[0].value, 1)
            if abs(t.delta) < _STEADY_EPSILON:
                t.direction = "steady"
            else:
                t.direction = "rising" if t.delta > 0 else "falling"
        elif pts:
            t.latest_value = round(pts[-1].value, 1)
        out.append(t)
    return out
