"""Run orchestration: fetch -> features -> digest -> LLM -> outputs -> state."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from rich.table import Table

from skywatch import console, output
from skywatch.alert_state import select_pushes, severity_rank
from skywatch.cache import DiskCache
from skywatch.config import Config
from skywatch.digest import build_digest
from skywatch.features.anomaly import daily_anomalies
from skywatch.features.disagreement import model_disagreement
from skywatch.features.enso import enso_status
from skywatch.features.gridmap import GridMapSummary, grid_summary
from skywatch.features.panel import panel_median
from skywatch.features.skill import SkillSummary, model_skill
from skywatch.features.spread import ensemble_stats, event_probability
from skywatch.features.thresholds import anomaly_events, threshold_events
from skywatch.features.trends import forecast_trends
from skywatch.features.vortex import analyse_vortex
from skywatch.models import Alert, Severity
from skywatch.notify import push_alerts
from skywatch.sources import build_sources
from skywatch.state import StateDB


def fetch_only(cfg: Config, *, refresh: bool = False, json_out: bool = False) -> None:
    """Fetch every source into the cache; report what arrived."""
    results = _fetch_all(cfg, refresh=refresh)
    summary = {
        "forecast_models": [fc.model for fc in results["forecasts"]],
        "ensemble_series": len(results["ensembles"]),
        "climatology_days": len(results["climatology"]),
        "enso_weeks": len(results["enso_weeks"]),
        "vortex_samples": len(results["vortex"].samples),
    }
    if json_out:
        console.emit_json({"fetched": summary})
    else:
        console.err().print("[green]Fetched:[/green]", summary)


class Snapshot:
    """What one snapshot produced; the briefing step builds on it."""

    def __init__(self, run_at: datetime, run_dir: Path, digest: dict[str, Any],
                 features: dict[str, Any]) -> None:
        self.run_at = run_at
        self.run_dir = run_dir
        self.digest = digest
        self.features = features


def take_snapshot(
    cfg: Config,
    *,
    refresh: bool = False,
    model_inits: dict[str, datetime] | None = None,
    run_at: datetime | None = None,
) -> Snapshot:
    """Fetch -> features -> digest -> outputs -> state. No LLM.

    alerts.json holds the mechanical (fact-only) alerts; the dashboard carries
    the most recent briefing forward, stamped with its own time.
    """
    from skywatch.alerts import mechanical_alerts

    run_at = run_at or datetime.now(UTC)
    data = _fetch_all(cfg, refresh=refresh)
    features = _compute_features(cfg, data, run_at)
    digest = features["digest"]

    run_dir = output.make_run_dir(cfg.output_dir, run_at)
    output.write_digest(run_dir, digest)
    alerts = mechanical_alerts(features["events"], features["enso"], features["vortex"])
    output.write_alerts(run_dir, alerts, mode="mechanical", run_at=run_at)

    db = StateDB(cfg.state_db)
    briefing_md, briefing_at = _carried_briefing(cfg, db)
    output.write_briefing(run_dir, briefing_md)
    _render_dashboard(cfg, run_dir, digest, briefing_md, alerts,
                      grid=features["grid_summary"], skill=features["skill_summary"],
                      briefing_at=briefing_at)
    output.update_latest(cfg.output_dir, run_dir)

    # Persist AFTER outputs so a crash never loses the run's files.
    db.record_run(run_at, cfg.location.name, data["forecasts"], features["panel"],
                  model_inits=model_inits)
    _push_new_alerts(cfg, db, alerts, run_at)
    removed = DiskCache(cfg.cache_dir, cfg.cache_ttl_minutes).prune_history(
        keep_days=cfg.schedule.cache_history_days
    )
    if removed:
        console.log().info("pruned %d old cache history files", removed)
    return Snapshot(run_at, run_dir, digest, features)


def write_briefing_for(
    cfg: Config, snap: Snapshot, *, trigger: list[str] | None = None
) -> tuple[str, bool, list[Alert], str]:
    """LLM briefing + LLM-phrased alerts for a snapshot; rewrites its outputs."""
    from skywatch import llm

    digest = dict(snap.digest)
    if trigger:
        digest["briefing_trigger"] = trigger   # why a fresh briefing was warranted
        output.write_digest(snap.run_dir, digest)  # digest.json = exactly what the LLM saw
    briefing_md, llm_ok = llm.write_briefing(cfg, digest)
    alerts, alert_mode = llm.write_alerts(
        cfg, digest, snap.features["events"], snap.features["enso"], snap.features["vortex"]
    )
    output.write_briefing(snap.run_dir, briefing_md)
    output.write_alerts(snap.run_dir, alerts, mode=alert_mode, run_at=snap.run_at)
    _render_dashboard(cfg, snap.run_dir, snap.digest, briefing_md, alerts,
                      grid=snap.features["grid_summary"], skill=snap.features["skill_summary"],
                      briefing_at=snap.run_at)
    db = StateDB(cfg.state_db)
    if llm_ok:
        db.set_meta("last_briefing_run_dir", str(snap.run_dir))
        db.set_meta("last_briefing_at", snap.run_at.astimezone(UTC).isoformat())
    _push_new_alerts(cfg, db, alerts, snap.run_at)
    return briefing_md, llm_ok, alerts, alert_mode


def _carried_briefing(cfg: Config, db: StateDB) -> tuple[str, datetime | None]:
    """The most recent LLM briefing and when it was written, for snapshots.

    Falls back to scanning past run directories (newest first) for one whose
    alerts were LLM-generated — so an upgrade from the run-only era, or a lost
    state.db, still carries the last real briefing forward.
    """
    run_dir = db.get_meta("last_briefing_run_dir")
    at = db.get_meta("last_briefing_at")
    if run_dir and at and (Path(run_dir) / "briefing.md").exists():
        return (Path(run_dir) / "briefing.md").read_text(), datetime.fromisoformat(at)
    for d in sorted(cfg.output_dir.glob("????-??-??_????"), reverse=True):
        alerts_p, brief_p = d / "alerts.json", d / "briefing.md"
        if not (alerts_p.exists() and brief_p.exists()):
            continue
        try:
            meta = json.loads(alerts_p.read_text())
        except json.JSONDecodeError:
            continue
        if meta.get("generator") == "skywatch (llm)":
            when = datetime.fromisoformat(meta["generated_at"])
            db.set_meta("last_briefing_run_dir", str(d))
            db.set_meta("last_briefing_at", when.astimezone(UTC).isoformat())
            return brief_p.read_text(), when
    return ("# No briefing yet\n\nInstruments are live; the first LLM briefing "
            "arrives at the next anchor time or material change.\n"), None


def last_briefing_digest(cfg: Config, db: StateDB) -> dict[str, Any] | None:
    run_dir = db.get_meta("last_briefing_run_dir")
    if run_dir and (Path(run_dir) / "digest.json").exists():
        data: dict[str, Any] = json.loads((Path(run_dir) / "digest.json").read_text())
        return data
    return None


def _push_new_alerts(cfg: Config, db: StateDB, alerts: list[Alert], at: datetime) -> None:
    """Push only alerts that are new or escalated since we last pushed them."""
    if not cfg.ntfy.enabled or not cfg.ntfy.topic:
        return
    min_rank = severity_rank(Alert(
        severity=Severity(cfg.ntfy.min_severity), category="x", title="x", detail="x",
        confidence=0, valid_from=at.date(), valid_to=at.date(), sources=[],
    ))
    to_push, updates = select_pushes(alerts, db.pushed_alerts(), min_rank=min_rank)
    if to_push:
        push_alerts(cfg.ntfy, to_push)
        db.record_pushes(updates, at)
    db.forget_pushes_before(at.date() - timedelta(days=2))


def snapshot_only(cfg: Config, *, refresh: bool = False, json_out: bool = False) -> None:
    snap = take_snapshot(cfg, refresh=refresh)
    if json_out:
        console.emit_json({"run_dir": str(snap.run_dir), "briefing": "carried forward"})
    else:
        console.err().print(f"[green]Snapshot complete:[/green] {snap.run_dir}")


def run_cycle(
    cfg: Config, *, use_llm: bool = True, refresh: bool = False, json_out: bool = False
) -> None:
    """Snapshot now, then (unless --no-llm) write a fresh briefing for it."""
    snap = take_snapshot(cfg, refresh=refresh)
    llm_ok, alert_mode, alerts = False, "mechanical", _load_alerts(snap.run_dir)
    if use_llm:
        _, llm_ok, alerts, alert_mode = write_briefing_for(cfg, snap, trigger=["manual run"])

    if json_out:
        console.emit_json(
            {
                "run_dir": str(snap.run_dir),
                "llm_ok": llm_ok,
                "alert_mode": alert_mode,
                "n_alerts": len(alerts),
                "alerts": [a.model_dump(mode="json") for a in alerts],
            }
        )
    else:
        console.err().print(f"[green]Run complete:[/green] {snap.run_dir}")
        console.err().print(
            f"  briefing: {'LLM' if llm_ok else 'unavailable-note' if use_llm else 'skipped'}, "
            f"alerts: {len(alerts)} ({alert_mode})"
        )


def brief_only(cfg: Config, *, run_dir: Path | None = None, json_out: bool = False) -> None:
    """Re-run the LLM over an existing digest (e.g. after the model comes back)."""
    target = run_dir or output.latest_run_dir(cfg.output_dir)
    if target is None or not (target / "digest.json").exists():
        raise FileNotFoundError("No digest.json found; run `skywatch run` first.")
    digest = json.loads((target / "digest.json").read_text())
    run_at = datetime.fromisoformat(digest["meta"]["run_at"])

    # Rebuild the feature objects the briefing step needs from cached data
    # (a cache hit if within TTL, otherwise a fresh fetch).
    data = _fetch_all(cfg, refresh=False)
    features = _compute_features(cfg, data, run_at)
    snap = Snapshot(run_at, target, digest, features)
    _, llm_ok, _, _ = write_briefing_for(cfg, snap, trigger=["manual re-brief"])

    if json_out:
        console.emit_json({"run_dir": str(target), "llm_ok": llm_ok})
    else:
        console.err().print(f"[green]Briefing {'written' if llm_ok else 'UNAVAILABLE'}:[/green] "
                            f"{target / 'briefing.md'}")


def show_status(cfg: Config, *, json_out: bool = False) -> None:
    """Latest alerts + global drivers, from the most recent run's files."""
    target = output.latest_run_dir(cfg.output_dir)
    if target is None:
        console.err().print("[yellow]No runs yet.[/yellow] Run `skywatch run` first.")
        return
    digest = json.loads((target / "digest.json").read_text())
    alerts = _load_alerts(target)
    drivers = digest.get("global_drivers", {})

    if json_out:
        console.emit_json(
            {
                "run_dir": str(target),
                "run_at": digest.get("meta", {}).get("run_at"),
                "alerts": [a.model_dump(mode="json") for a in alerts],
                "enso": drivers.get("enso"),
                "stratospheric_vortex": drivers.get("stratospheric_vortex"),
            }
        )
        return

    err = console.err()
    err.print(f"[bold]Latest run:[/bold] {target.name}  ({digest['meta']['run_at']})")

    enso = drivers.get("enso", {})
    vortex = drivers.get("stratospheric_vortex", {})
    err.print(
        f"[bold]ENSO:[/bold] {enso.get('classification')} "
        f"({enso.get('anomaly'):+.1f}°C, trend {enso.get('trend')})"
    )
    err.print(
        f"[bold]Vortex:[/bold] u60N@10hPa {vortex.get('current_u'):+.1f} m/s — "
        f"{vortex.get('state')} (trend {vortex.get('trend')})"
    )

    skill = digest.get("model_skill") or {}
    models = skill.get("models") or {}
    if models:
        tmax_rank = sorted(
            ((m, v.get("temperature_2m_max")) for m, v in models.items()),
            key=lambda t: (t[1] or {}).get("mae", 99),
        )
        best, ov = tmax_rank[0]
        prov = " (provisional)" if ov.get("provisional") else ""
        err.print(
            f"[bold]Verified accuracy (tmax):[/bold] best {best} "
            f"MAE {ov.get('mae')}°C over {ov.get('n_days')} days{prov}"
        )

    if alerts:
        table = Table(title=f"Alerts ({len(alerts)})")
        for col in ("Severity", "Category", "Title", "Valid", "Conf"):
            table.add_column(col)
        for a in alerts:
            table.add_row(
                str(a.severity), a.category, a.title,
                f"{a.valid_from} → {a.valid_to}", f"{a.confidence:.0%}",
            )
        err.print(table)
    else:
        err.print("[green]No active alerts.[/green]")


def open_latest_dashboard(cfg: Config) -> None:
    target = output.latest_run_dir(cfg.output_dir)
    if target is None or not (target / "dashboard.html").exists():
        console.err().print("[yellow]No dashboard yet.[/yellow] Run `skywatch run` first.")
        return
    subprocess.run(["open", str(target / "dashboard.html")], check=False)


# --- internals ---------------------------------------------------------------


def _fetch_all(cfg: Config, *, refresh: bool) -> dict[str, Any]:
    sources = build_sources(cfg)
    log = console.log()
    log.info("Fetching deterministic forecasts…")
    forecasts = sources["openmeteo_forecast"].fetch(refresh=refresh)
    log.info("Fetching ensembles…")
    ensembles = sources["openmeteo_ensemble"].fetch(refresh=refresh)
    log.info("Fetching climatology…")
    climatology = sources["openmeteo_climatology"].fetch(refresh=refresh)
    log.info("Fetching ENSO…")
    enso_weeks = sources["noaa_enso"].fetch(refresh=refresh)
    log.info("Fetching stratospheric vortex…")
    vortex = sources["vortex"].fetch(refresh=refresh)
    observed = None
    if cfg.skill.enabled:
        # Verification is an enhancement: failure skips the ratings, not the run.
        try:
            log.info("Fetching observations for verification…")
            observed = sources["openmeteo_observed"].fetch(refresh=refresh)
        except Exception as exc:
            log.warning("Observed fetch failed; skill ratings skipped: %s", exc)
    grid = None
    if cfg.gridmap.enabled:
        # Maps are an enhancement: a grid failure degrades them, never the run.
        try:
            log.info("Fetching map grid…")
            grid = sources["openmeteo_grid"].fetch(refresh=refresh)
        except Exception as exc:
            log.warning("Grid fetch failed; maps skipped this run: %s", exc)
    return {
        "forecasts": forecasts,
        "ensembles": ensembles,
        "climatology": climatology,
        "enso_weeks": enso_weeks,
        "vortex": vortex,
        "grid": grid,
        "observed": observed,
    }


def _compute_features(cfg: Config, data: dict[str, Any], run_at: datetime) -> dict[str, Any]:
    today = run_at.date()
    forecasts = data["forecasts"]

    spreads = [
        ensemble_stats(ef, spread_jump_factor=cfg.thresholds.spread_jump_factor)
        for ef in data["ensembles"]
    ]
    disagreement = model_disagreement(
        forecasts,
        divergence_thresholds={"temperature_2m_max": cfg.thresholds.model_divergence_temp_c},
    )
    panel = panel_median(forecasts)
    anomalies = daily_anomalies(panel, data["climatology"])
    enso = enso_status(data["enso_weeks"], today=today)
    vortex = analyse_vortex(data["vortex"].samples, data["vortex"].climatology,
                            cfg.vortex, today=today)

    probs = _ensemble_probabilities(cfg, data["ensembles"])
    events = threshold_events(forecasts, probs, cfg.thresholds)
    events.extend(anomaly_events(anomalies, cfg.thresholds))

    db = StateDB(cfg.state_db)
    target_dates = panel.dates[: cfg.briefing_days]
    trends = forecast_trends(
        db.history(list(target_dates), max_runs=12, window_hours=72, now=run_at),
        max_runs=12,
    )

    skill_sum: SkillSummary | None = None
    if data.get("observed"):
        rows = db.forecast_history(
            since=today - timedelta(days=cfg.skill.window_days),
            until=today - timedelta(days=1),
        )
        since = today - timedelta(days=cfg.skill.window_days)
        skill_sum = model_skill(
            rows, data["observed"],
            provisional_below_n=cfg.skill.provisional_below_n,
            provisional_below_days=cfg.skill.provisional_below_days,
            normals=_normals_for(data["climatology"], since, today),
            cycles=db.forecast_cycles(since=since, until=today - timedelta(days=1)),
        )

    grid_sum: GridMapSummary | None = None
    if data.get("grid") is not None:
        from skywatch.sources.openmeteo_grid import GRID_VARIABLES

        grid_sum = grid_summary(data["grid"], variables=GRID_VARIABLES)

    digest = build_digest(
        cfg, run_at, forecasts, spreads, disagreement, anomalies,
        enso, vortex, events, trends,
        map_facts=grid_sum.facts if grid_sum else None,
        skill=_skill_digest(skill_sum),
    )
    return {
        "digest": digest, "panel": panel, "events": events,
        "enso": enso, "vortex": vortex, "alerts_probs": probs,
        "grid_summary": grid_sum, "skill_summary": skill_sum,
    }


def _normals_for(climatology: Any, since: date, until: date) -> dict[date, float]:
    """Climatological daily-max normal per date in [since, until], for the
    skill baseline. Tolerates the climatology being keyed by date or (month, day)."""
    out: dict[date, float] = {}
    if not climatology:
        return out
    sample_key = next(iter(climatology))
    d = since
    while d <= until:
        key: Any = d if isinstance(sample_key, date) else (d.month, d.day)
        day = climatology.get(key)
        mean = getattr(day, "tmax_mean", None)
        if mean is not None:
            out[d] = float(mean)
        d += timedelta(days=1)
    return out


def _skill_digest(skill: SkillSummary | None) -> dict[str, Any] | None:
    """Compact verified-accuracy section for the LLM: overall scores only."""
    if skill is None or not skill.by_model:
        return None
    return {
        "note": (
            "Verified accuracy of past forecasts vs ERA5 observations "
            f"({skill.first_verified} to {skill.last_verified}, "
            f"{skill.total_samples} samples). MAE = mean absolute error; "
            "bias + = model runs high. vs_median_pct + = better than the "
            "panel median. Treat 'provisional' ratings as early indications."
        ),
        "models": {
            model: {
                var: vs.overall.model_dump(mode="json")
                for var, vs in ms.variables.items()
            }
            for model, ms in skill.by_model.items()
        },
        "beats_climatology_until_hours": skill.beats_climatology_until_h,
    }


def _ensemble_probabilities(
    cfg: Config, ensembles: list[Any]
) -> dict[tuple[str, date], float | None]:
    """Member-fraction probabilities for frost (tmin < 0) and snow (> 0.5 cm)."""
    probs: dict[tuple[str, date], float | None] = {}
    for ef in ensembles:
        if ef.variable == "temperature_2m_min":
            kind, pred = "frost", (lambda v: v < 0.0)
        elif ef.variable == "snowfall_sum":
            kind, pred = "snow", (lambda v: v > 0.5)
        else:
            continue
        for d, p in zip(ef.dates, event_probability(ef, pred), strict=True):
            key = (kind, d)
            # Multiple ensemble systems: keep the highest probability (worst case).
            if p is not None and (probs.get(key) is None or p > (probs[key] or 0)):
                probs[key] = p
    return probs


def _render_dashboard(
    cfg: Config,
    run_dir: Path,
    digest: dict[str, Any],
    briefing_md: str,
    alerts: list[Alert],
    grid: GridMapSummary | None = None,
    skill: SkillSummary | None = None,
    briefing_at: datetime | None = None,
) -> None:
    from skywatch.dashboard import render_dashboard

    render_dashboard(cfg, run_dir, digest, briefing_md, alerts, grid=grid, skill=skill,
                     briefing_at=briefing_at)


def _load_alerts(run_dir: Path) -> list[Alert]:
    p = run_dir / "alerts.json"
    if not p.exists():
        return []
    payload = json.loads(p.read_text())
    return [Alert.model_validate(a) for a in payload.get("alerts", [])]
