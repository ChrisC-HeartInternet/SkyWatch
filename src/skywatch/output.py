"""Run output directory management.

output/YYYY-MM-DD_HHMM/{briefing.md, alerts.json, digest.json, dashboard.html}
output/latest -> symlink to the most recent run directory.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from skywatch.models import Alert


def make_run_dir(output_root: Path, run_at: datetime) -> Path:
    run_dir = output_root / run_at.strftime("%Y-%m-%d_%H%M")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def update_latest(output_root: Path, run_dir: Path) -> None:
    latest = output_root / "latest"
    if latest.is_symlink() or latest.exists():
        if latest.is_dir() and not latest.is_symlink():
            # Someone made a real directory; leave it alone rather than delete data.
            return
        latest.unlink()
    latest.symlink_to(run_dir.name)  # relative, so the tree can be moved


def write_digest(run_dir: Path, digest: dict[str, Any]) -> Path:
    p = run_dir / "digest.json"
    p.write_text(json.dumps(digest, indent=2, ensure_ascii=False))
    return p


def write_briefing(run_dir: Path, markdown: str) -> Path:
    p = run_dir / "briefing.md"
    p.write_text(markdown)
    return p


def write_alerts(run_dir: Path, alerts: list[Alert], *, mode: str, run_at: datetime) -> Path:
    payload = {
        "generated_at": run_at.isoformat(),
        "generator": f"skywatch ({mode})",
        "alerts": [a.model_dump(mode="json") for a in alerts],
    }
    p = run_dir / "alerts.json"
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return p


def latest_run_dir(output_root: Path) -> Path | None:
    latest = output_root / "latest"
    if latest.exists():
        return latest.resolve()
    # Fall back to newest timestamped directory.
    runs = sorted(
        (d for d in output_root.glob("????-??-??_????") if d.is_dir()),
        reverse=True,
    )
    return runs[0] if runs else None
