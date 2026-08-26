"""Has the forecast changed enough to be worth a new briefing?

Pure comparison of the digest at the last briefing against a fresh one.
The LLM is expensive and a reader's attention more so: between the two anchor
briefings, prose is regenerated only when something material moved.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Materiality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material: bool
    reasons: list[str]


def _alert_fingerprints(digest: dict[str, Any]) -> set[str]:
    return {
        f"{e.get('category')}|{e.get('date')}|{e.get('severity')}"
        for e in digest.get("threshold_events", [])
    }


def _tmax_by_date(digest: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in digest.get("days", []):
        med = (d.get("tmax") or {}).get("panel_median")
        if med is not None:
            out[str(d.get("date"))] = float(med)
    return out


def _flagged_days(digest: dict[str, Any]) -> set[str]:
    return {
        str(d.get("date"))
        for d in digest.get("days", [])
        if (d.get("disagreement") or {}).get("flag") or (d.get("spread") or {}).get("flag")
    }


def assess(previous: dict[str, Any] | None, current: dict[str, Any],
           *, drift_c: float = 2.0, window_days: int = 7) -> Materiality:
    """Compare the digest behind the last briefing with a fresh one."""
    if previous is None:
        return Materiality(material=True, reasons=["no previous briefing"])
    reasons: list[str] = []

    added = _alert_fingerprints(current) - _alert_fingerprints(previous)
    removed = _alert_fingerprints(previous) - _alert_fingerprints(current)
    if added:
        reasons.append(f"new threshold events: {', '.join(sorted(added))}")
    if removed:
        reasons.append(f"threshold events cleared: {', '.join(sorted(removed))}")

    prev_t, cur_t = _tmax_by_date(previous), _tmax_by_date(current)
    for date in sorted(cur_t)[:window_days]:
        if date in prev_t and abs(cur_t[date] - prev_t[date]) >= drift_c:
            reasons.append(
                f"{date} max temp moved {cur_t[date] - prev_t[date]:+.1f} °C"
            )

    prev_f, cur_f = _flagged_days(previous), _flagged_days(current)
    if prev_f != cur_f:
        gained, lost = cur_f - prev_f, prev_f - cur_f
        if gained:
            reasons.append(f"uncertainty flags raised: {', '.join(sorted(gained))}")
        if lost:
            reasons.append(f"uncertainty flags cleared: {', '.join(sorted(lost))}")

    return Materiality(material=bool(reasons), reasons=reasons)
