"""Which alerts deserve a push right now?

With snapshots several times a day, re-pushing every high alert on every run
would send the same gust warning four times. An alert is pushed when it first
appears or when its severity escalates; unchanged alerts stay silent.

Pure functions; StateDB persists the memory.
"""

from __future__ import annotations

from skywatch.models import Alert

_RANK = {"low": 0, "moderate": 1, "high": 2, "severe": 3}


def fingerprint(a: Alert) -> str:
    """Identity of an alert across runs: what + when. Severity is NOT part of it,
    so an escalation updates the same entry. Ends with valid_to for cheap pruning."""
    return f"{a.category}|{a.valid_from.isoformat()}|{a.valid_to.isoformat()}"


def severity_rank(a: Alert) -> int:
    return _RANK.get(str(a.severity), 0)


def select_pushes(
    alerts: list[Alert], already: dict[str, int], *, min_rank: int
) -> tuple[list[Alert], dict[str, int]]:
    """(alerts to push now, memory updates) given what was pushed before."""
    to_push: list[Alert] = []
    updates: dict[str, int] = {}
    for a in alerts:
        rank = severity_rank(a)
        if rank < min_rank:
            continue
        fp = fingerprint(a)
        prev = already.get(fp)
        if prev is None or rank > prev:
            to_push.append(a)
            updates[fp] = rank
    return to_push, updates
