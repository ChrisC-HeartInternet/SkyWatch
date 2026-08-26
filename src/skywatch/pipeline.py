"""Run orchestration: fetch -> features -> digest -> LLM -> outputs -> state."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from rich.table import Table

from skywatch import console, output
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
from skywatch.models import Alert
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


def run_cycle(
    cfg: Config, *, use_llm: bool = True, refresh: bool = False, json_out: bool = False
) -> None:
    """The full one-shot cycle."""
    run_at = datetime.now(UTC)
    data = _fetch_all(cfg, refresh=refresh)
    features = _compute_features(cfg, data, run_at)
    digest = features["digest"]

    run_dir = output.make_run_dir(cfg.output_dir, run_at)
    output.write_digest(run_dir, digest)

    # LLM layer — never fatal.
    if use_llm:
        from skywatch import llm

        briefing_md, llm_ok = llm.write_briefing(cfg, digest)
        alerts, alert_mode = llm.write_alerts(
            cfg, digest, features["events"], features["enso"], features["vortex"]
        )
    else:
        from skywatch.alerts import mechanical_alerts

        briefing_md = "# Briefing skipped (--no-llm)\n\nSee digest.json.\n"
        llm_ok = False
        alerts = mechanical_alerts(features["events"], features["enso"], features["vortex"])
        alert_mode = "mechanical"

    output.write_briefing(run_dir, briefing_md)
    output.write_alerts(run_dir, alerts, mode=alert_mode, run_at=run_at)

    _render_dashboard(cfg, run_dir, digest, briefing_md, alerts,
                      grid=features["grid_summary"], skill=features["skill_summary"])
    output.update_latest(cfg.output_dir, run_dir)

    # Persist AFTER outputs so a crash never loses the run's files.
    db = StateDB(cfg.state_db)
    db.record_run(run_at, cfg.location.name, data["forecasts"], features["panel"])

    push_alerts(cfg.ntfy, alerts)

    if json_out:
        console.emit_json(
            {
                "run_dir": str(run_dir),
                "llm_ok": llm_ok,
                "alert_mode": alert_mode,
                "n_alerts": len(alerts),
                "alerts": [a.model_dump(mode="json") for a in alerts],
            }
        )
    else:
        console.err().print(f"[green]Run complete:[/green] {run_dir}")
        console.err().print(
            f"  briefing: {'LLM' if llm_ok else 'unavailable-note'}, "
            f"alerts: {len(alerts)} ({alert_mode})"
        )


def brief_only(cfg: Config, *, run_dir: Path | None = None, json_out: bool = False) -> None:
    """Re-run the LLM over an existing digest (e.g. after the model comes back)."""
    from skywatch import llm

    target = run_dir or output.latest_run_dir(cfg.output_dir)
    if target is None or not (target / "digest.json").exists():
        raise FileNotFoundError("No digest.json found; run `skywatch run` first.")
    digest = json.loads((target / "digest.json").read_text())

    briefing_md, llm_ok = llm.write_briefing(cfg, digest)
    output.write_briefing(target, briefing_md)

    # Rebuild the dashboard so it embeds the new briefing. The grid comes
    # from cache (or a fresh fetch if the TTL lapsed); failure just skips maps.
    alerts = _load_alerts(target)
    grid_sum = None
    if cfg.gridmap.enabled:
        try:
            from skywatch.sources import build_sources
            from skywatch.sources.openmeteo_grid import GRID_VARIABLES

            grid_sum = grid_summary(
                build_sources(cfg)["openmeteo_grid"].fetch(), variables=GRID_VARIABLES
            )
        except Exception as exc:
            console.log().warning("Grid unavailable for re-brief; maps skipped: %s", exc)
    skill_sum = None
    if cfg.skill.enabled:
        try:
            from datetime import timedelta

            from skywatch.sources import build_sources

            today = datetime.now(UTC).date()
            observed = build_sources(cfg)["openmeteo_observed"].fetch()
            rows = StateDB(cfg.state_db).forecast_history(
                since=today - timedelta(days=cfg.skill.window_days),
                until=today - timedelta(days=1),
            )
            skill_sum = model_skill(
                rows, observed,
                provisional_below_n=cfg.skill.provisional_below_n,
                provisional_below_days=cfg.skill.provisional_below_days,
            )
        except Exception as exc:
            console.log().warning("Verification unavailable for re-brief: %s", exc)
    _render_dashboard(cfg, target, digest, briefing_md, alerts, grid=grid_sum,
                      skill=skill_sum)

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
    trends = forecast_trends(db.history(list(target_dates)), max_runs=6)

    skill_sum: SkillSummary | None = None
    if data.get("observed"):
        from datetime import timedelta

        rows = db.forecast_history(
            since=today - timedelta(days=cfg.skill.window_days),
            until=today - timedelta(days=1),
        )
        skill_sum = model_skill(
            rows, data["observed"],
            provisional_below_n=cfg.skill.provisional_below_n,
            provisional_below_days=cfg.skill.provisional_below_days,
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
) -> None:
    from skywatch.dashboard import render_dashboard

    render_dashboard(cfg, run_dir, digest, briefing_md, alerts, grid=grid, skill=skill)


def _load_alerts(run_dir: Path) -> list[Alert]:
    p = run_dir / "alerts.json"
    if not p.exists():
        return []
    payload = json.loads(p.read_text())
    return [Alert.model_validate(a) for a in payload.get("alerts", [])]
