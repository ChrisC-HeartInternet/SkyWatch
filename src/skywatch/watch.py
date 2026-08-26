"""The watcher: snapshot when models publish, brief when it matters.

Loop every `schedule.poll_minutes`:
1. Ask Open-Meteo which cycle each real model is serving (tiny metadata calls).
2. If any model has a NEW cycle since our last snapshot -> take a snapshot
   (fetch with cache bypass, features, dashboard, state.db). No LLM.
3. Decide whether to write a briefing: always at an anchor time not yet
   fired today; otherwise only when the fresh digest differs materially from
   the digest behind the last briefing, and the minimum gap has passed.

Anchor logic is pure (`anchors_due`) and tested; the loop is a thin shell.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from skywatch import console
from skywatch.config import Config
from skywatch.features.materiality import assess
from skywatch.pipeline import (
    last_briefing_digest,
    take_snapshot,
    write_briefing_for,
)
from skywatch.sources.openmeteo_meta import fetch_model_runs, new_cycles, real_ids
from skywatch.state import StateDB


def anchors_due(anchors: list[str], now_local: datetime, fired: dict[str, str]) -> list[str]:
    """Anchor times (HH:MM) that have passed today and haven't fired today.

    `fired` maps anchor -> ISO date it last fired. Pure, so it's testable.
    """
    today = now_local.date().isoformat()
    due: list[str] = []
    for a in anchors:
        hh, mm = (int(x) for x in a.split(":"))
        at = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now_local >= at and fired.get(a) != today:
            due.append(a)
    return due


def _cycle_key(cfg: Config) -> list[str]:
    return real_ids([*cfg.models.deterministic, *cfg.models.ensemble])


def tick(cfg: Config, db: StateDB, now: datetime) -> dict[str, object]:
    """One watcher iteration. Returns a small summary for logging/tests."""
    log = console.log()
    tz = ZoneInfo(cfg.location.timezone)
    now_local = now.astimezone(tz)

    current = fetch_model_runs(_cycle_key(cfg))
    fresh = new_cycles(current, db.seen_cycles())
    fired = {
        a: db.get_meta(f"anchor_fired:{a}") or "" for a in cfg.schedule.anchors
    }
    due = anchors_due(cfg.schedule.anchors, now_local, fired)
    first_ever = db.run_count() == 0

    summary: dict[str, object] = {"fresh_cycles": fresh, "anchors_due": due,
                                  "snapshot": False, "briefing": None}
    if not (fresh or due or first_ever):
        return summary

    inits = {m: r.init_time for m, r in current.items()}
    reason = (
        f"new cycles: {', '.join(fresh)}" if fresh
        else f"anchor {', '.join(due)}" if due else "first run"
    )
    log.info("watch: snapshot (%s)", reason)
    snap = take_snapshot(cfg, refresh=bool(fresh), model_inits=inits, run_at=now)
    db.mark_cycles(inits, now)
    summary["snapshot"] = True

    previous = last_briefing_digest(cfg, db)
    mat = assess(previous, snap.digest, drift_c=cfg.schedule.materiality_drift_c,
                 window_days=cfg.briefing_days)
    last_at = db.get_meta("last_briefing_at")
    gap_ok = (
        last_at is None
        or now - datetime.fromisoformat(last_at)
        >= timedelta(minutes=cfg.schedule.min_brief_gap_minutes)
    )
    if due or first_ever or (mat.material and gap_ok):
        why = [f"anchor {a}" for a in due] + mat.reasons
        log.info("watch: briefing (%s)", "; ".join(why) or "first run")
        write_briefing_for(cfg, snap, trigger=why)
        for a in due:
            db.set_meta(f"anchor_fired:{a}", now_local.date().isoformat())
        summary["briefing"] = why
    elif mat.material:
        log.info("watch: material change but inside min gap; briefing deferred: %s",
                 "; ".join(mat.reasons))
    else:
        log.info("watch: instruments refreshed; briefing unchanged")
    return summary


def main(cfg: Config) -> None:
    db = StateDB(cfg.state_db)
    poll = cfg.schedule.poll_minutes * 60
    console.err().print(
        f"[green]Watching[/green] {len(_cycle_key(cfg))} model feeds every "
        f"{cfg.schedule.poll_minutes} min; anchors {', '.join(cfg.schedule.anchors)} "
        f"({cfg.location.timezone})"
    )
    try:
        while True:
            try:
                tick(cfg, db, datetime.now(UTC))
            except Exception as exc:  # noqa: BLE001 — the daemon must outlive one bad tick
                console.log().exception("watch: tick failed: %s", exc)
            time.sleep(poll)
    except KeyboardInterrupt:
        console.err().print("\n[yellow]Watcher stopped.[/yellow]")
