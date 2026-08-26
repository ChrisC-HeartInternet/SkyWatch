"""Run-to-run state in SQLite.

Stores every run's per-(target_date, model, variable) forecast values so the
trends feature can say how a target date's forecast has drifted. The panel
median (model='panel_median') is what trend deltas are computed from; per-model
rows are kept for deeper digging.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from skywatch.features.trends import HistoryPoint
from skywatch.models import ModelForecast

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    run_at TEXT NOT NULL UNIQUE,
    location TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS forecasts (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    target_date TEXT NOT NULL,
    model TEXT NOT NULL,
    variable TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (run_id, target_date, model, variable)
);
CREATE INDEX IF NOT EXISTS ix_forecasts_lookup
    ON forecasts (target_date, model, variable);
"""

# Variables worth trending.
TREND_VARIABLES = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_gusts_10m_max",
    "snowfall_sum",
]


class StateDB:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def record_run(
        self,
        run_at: datetime,
        location: str,
        forecasts: list[ModelForecast],
        panel_median: ModelForecast,
    ) -> int:
        """Persist one run's forecasts. Returns the run id."""
        rows: list[tuple[str, str, str, float]] = []
        for fc in [*forecasts, panel_median]:
            for var in TREND_VARIABLES:
                s = fc.series.get(var)
                if s is None:
                    continue
                for i, d in enumerate(fc.dates):
                    if i < len(s.values) and (v := s.values[i]) is not None:
                        rows.append((d.isoformat(), fc.model, var, float(v)))
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO runs (run_at, location) VALUES (?, ?)",
                (run_at.astimezone(UTC).isoformat(), location),
            )
            run_id = cur.lastrowid
            assert run_id is not None
            conn.executemany(
                "INSERT OR REPLACE INTO forecasts VALUES (?, ?, ?, ?, ?)",
                [(run_id, *row) for row in rows],
            )
        return run_id

    def history(
        self,
        target_dates: list[date],
        *,
        model: str = "panel_median",
        max_runs: int = 8,
    ) -> dict[tuple[date, str], list[HistoryPoint]]:
        """Forecast history for the trends feature, oldest run first per key."""
        if not target_dates:
            return {}
        placeholders = ",".join("?" for _ in target_dates)
        query = f"""
            SELECT f.target_date, f.variable, r.run_at, f.value
            FROM forecasts f JOIN runs r ON r.id = f.run_id
            WHERE f.model = ? AND f.target_date IN ({placeholders})
            ORDER BY r.run_at
        """
        out: dict[tuple[date, str], list[HistoryPoint]] = {}
        with self._conn() as conn:
            for td, var, run_at, value in conn.execute(
                query, [model, *[d.isoformat() for d in target_dates]]
            ):
                key = (date.fromisoformat(td), var)
                out.setdefault(key, []).append(
                    HistoryPoint(run_at=datetime.fromisoformat(run_at), value=value)
                )
        return {k: v[-max_runs:] for k, v in out.items()}

    def forecast_history(
        self, *, since: date, until: date
    ) -> list[tuple[datetime, str, str, date, float]]:
        """Every stored forecast row (run_at, model, variable, target_date, value)
        with a target date inside [since, until] — the verification feed."""
        query = """
            SELECT r.run_at, f.model, f.variable, f.target_date, f.value
            FROM forecasts f JOIN runs r ON r.id = f.run_id
            WHERE f.target_date >= ? AND f.target_date <= ?
            ORDER BY r.run_at
        """
        with self._conn() as conn:
            return [
                (
                    datetime.fromisoformat(run_at),
                    model,
                    variable,
                    date.fromisoformat(td),
                    value,
                )
                for run_at, model, variable, td, value in conn.execute(
                    query, (since.isoformat(), until.isoformat())
                )
            ]

    def run_count(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
