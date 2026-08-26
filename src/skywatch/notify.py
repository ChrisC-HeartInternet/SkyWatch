"""Optional ntfy push for high-severity alerts.

Off by default. Hermes consumes alerts.json regardless; this is only a nudge.
Failures are logged and swallowed — notification must never fail a run.
"""

from __future__ import annotations

import httpx

from skywatch import console
from skywatch.config import NtfyConfig
from skywatch.models import Alert, Severity

_RANK = {Severity.LOW: 0, Severity.MODERATE: 1, Severity.HIGH: 2, Severity.SEVERE: 3}
_NTFY_PRIORITY = {Severity.HIGH: "high", Severity.SEVERE: "urgent"}


def push_alerts(cfg: NtfyConfig, alerts: list[Alert]) -> int:
    """Push qualifying alerts. Returns how many were sent."""
    if not cfg.enabled or not cfg.topic:
        return 0
    try:
        min_rank = _RANK[Severity(cfg.min_severity)]
    except ValueError:
        console.log().warning("ntfy.min_severity %r invalid; using 'high'", cfg.min_severity)
        min_rank = _RANK[Severity.HIGH]

    sent = 0
    for alert in alerts:
        if _RANK[alert.severity] < min_rank:
            continue
        try:
            httpx.post(
                f"{cfg.server.rstrip('/')}/{cfg.topic}",
                content=f"{alert.detail}\n({alert.valid_from} to {alert.valid_to}, "
                        f"confidence {alert.confidence:.0%})",
                headers={
                    "Title": alert.title,
                    "Priority": _NTFY_PRIORITY.get(alert.severity, "default"),
                    "Tags": f"warning,{alert.category}",
                },
                timeout=15.0,
            ).raise_for_status()
            sent += 1
        except httpx.HTTPError as exc:
            console.log().warning("ntfy push failed for %r: %s", alert.title, exc)
    if sent:
        console.log().info("Pushed %d alert(s) to ntfy topic %s", sent, cfg.topic)
    return sent
