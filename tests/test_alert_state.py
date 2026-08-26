from datetime import date

from skywatch.alert_state import fingerprint, select_pushes
from skywatch.models import Alert, Severity


def _alert(sev: Severity, cat: str = "wind", d: date = date(2026, 9, 3)) -> Alert:
    return Alert(severity=sev, category=cat, title="t", detail="d", confidence=0.5,
                 valid_from=d, valid_to=d, sources=["x"])


def test_fingerprint_ignores_severity_and_ends_with_valid_to() -> None:
    assert fingerprint(_alert(Severity.HIGH)) == fingerprint(_alert(Severity.SEVERE))
    assert fingerprint(_alert(Severity.HIGH)).endswith("2026-09-03")


def test_first_appearance_pushes_then_silence() -> None:
    a = _alert(Severity.HIGH)
    push, mem = select_pushes([a], {}, min_rank=2)
    assert push == [a] and mem == {fingerprint(a): 2}
    push2, mem2 = select_pushes([a], mem, min_rank=2)
    assert push2 == [] and mem2 == {}


def test_escalation_pushes_again_but_deescalation_does_not() -> None:
    high, severe = _alert(Severity.HIGH), _alert(Severity.SEVERE)
    _, mem = select_pushes([high], {}, min_rank=2)
    push, upd = select_pushes([severe], mem, min_rank=2)
    assert push == [severe] and upd[fingerprint(severe)] == 3
    mem.update(upd)
    push, _ = select_pushes([high], mem, min_rank=2)
    assert push == []


def test_below_threshold_never_pushed_or_remembered() -> None:
    push, mem = select_pushes([_alert(Severity.MODERATE)], {}, min_rank=2)
    assert push == [] and mem == {}


def test_distinct_dates_are_distinct_alerts() -> None:
    a, b = _alert(Severity.HIGH, d=date(2026, 9, 3)), _alert(Severity.HIGH, d=date(2026, 9, 4))
    push, _ = select_pushes([a, b], {fingerprint(a): 2}, min_rank=2)
    assert push == [b]
