"""Run-to-run state in SQLite.

Stores every run's per-(target_date, model, variable) forecast values so the
trends feature can say how a target date's forecast has drifted. The panel
median (model='panel_median') is what trend deltas are computed from; per-model
rows are kept for deeper digging.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
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
CREATE TABLE IF NOT EXISTS model_cycles (
    model TEXT PRIMARY KEY,
    init_time TEXT NOT NULL,
    seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_pushes (
    fingerprint TEXT PRIMARY KEY,
    severity_rank INTEGER NOT NULL,
    first_pushed_at TEXT NOT NULL,
    last_pushed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# runs.model_inits was added after the first release; older databases lack it.
_MIGRATIONS = [
    "ALTER TABLE runs ADD COLUMN model_inits TEXT",
]

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
            for stmt in _MIGRATIONS:
                with contextlib.suppress(sqlite3.OperationalError):  # already applied
                    conn.execute(stmt)

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
        model_inits: dict[str, datetime] | None = None,
    ) -> int:
        """Persist one run's forecasts (and which model cycles fed it). Returns the run id."""
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
            inits_json = json.dumps(
                {m: t.astimezone(UTC).isoformat() for m, t in (model_inits or {}).items()}
            ) if model_inits else None
            cur = conn.execute(
                "INSERT INTO runs (run_at, location, model_inits) VALUES (?, ?, ?)",
                (run_at.astimezone(UTC).isoformat(), location, inits_json),
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
        window_hours: float | None = None,
        now: datetime | None = None,
    ) -> dict[tuple[date, str], list[HistoryPoint]]:
        """Forecast history for the trends feature, oldest run first per key.

        window_hours limits to recent runs (so 4 snapshots/day don't compress
        the drift view into 36 hours); max_runs still caps the count.
        """
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
        if window_hours is not None:
            cutoff = (now or datetime.now(UTC)) - timedelta(hours=window_hours)
            out = {k: [p for p in v if p.run_at >= cutoff] for k, v in out.items()}
        return {k: v[-max_runs:] for k, v in out.items() if v}

    def forecast_cycles(
        self, *, since: date, until: date
    ) -> dict[tuple[datetime, str], int]:
        """(run_at, model) -> the model's cycle init hour (UTC) for runs that
        recorded it. Lets verification compare 00Z vs 06Z vs 12Z vs 18Z skill."""
        query = """
            SELECT DISTINCT r.run_at, r.model_inits
            FROM runs r JOIN forecasts f ON f.run_id = r.id
            WHERE f.target_date >= ? AND f.target_date <= ? AND r.model_inits IS NOT NULL
        """
        out: dict[tuple[datetime, str], int] = {}
        with self._conn() as conn:
            for run_at, inits in conn.execute(query, (since.isoformat(), until.isoformat())):
                run_dt = datetime.fromisoformat(run_at)
                for model, init in json.loads(inits).items():
                    out[(run_dt, model)] = datetime.fromisoformat(init).hour
        return out

    # ---- watcher state ------------------------------------------------------

    def seen_cycles(self) -> dict[str, datetime]:
        with self._conn() as conn:
            return {
                m: datetime.fromisoformat(t)
                for m, t in conn.execute("SELECT model, init_time FROM model_cycles")
            }

    def mark_cycles(self, inits: dict[str, datetime], seen_at: datetime) -> None:
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO model_cycles VALUES (?, ?, ?)",
                [(m, t.astimezone(UTC).isoformat(), seen_at.astimezone(UTC).isoformat())
                 for m, t in inits.items()],
            )

    def get_meta(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return str(row[0]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))

    # ---- alert push memory ----------------------------------------------------

    def pushed_alerts(self) -> dict[str, int]:
        """fingerprint -> severity rank already pushed."""
        with self._conn() as conn:
            return {
                fp: int(rank)
                for fp, rank in conn.execute("SELECT fingerprint, severity_rank FROM alert_pushes")
            }

    def record_pushes(self, pushes: dict[str, int], at: datetime) -> None:
        ts = at.astimezone(UTC).isoformat()
        with self._conn() as conn:
            for fp, rank in pushes.items():
                conn.execute(
                    """INSERT INTO alert_pushes VALUES (?, ?, ?, ?)
                       ON CONFLICT(fingerprint) DO UPDATE SET
                         severity_rank = excluded.severity_rank,
                         last_pushed_at = excluded.last_pushed_at""",
                    (fp, rank, ts, ts),
                )

    def forget_pushes_before(self, cutoff: date) -> None:
        """Drop memory of alerts whose validity ended before cutoff (keeps the table small)."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM alert_pushes WHERE substr(fingerprint, -10) < ?",
                (cutoff.isoformat(),),
            )

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
