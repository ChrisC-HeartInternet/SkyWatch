"""ENSO status: classification and 4-week trend, plus the parser regression guards."""

from datetime import date, timedelta
from pathlib import Path

import pytest

from skywatch.features.enso import classify_enso, enso_status
from skywatch.models import EnsoWeek
from skywatch.sources.noaa_enso import parse_wksst

FIXTURE = Path(__file__).parent / "fixtures" / "wksst9120_sample.for"


def _weeks(anoms: list[float]) -> list[EnsoWeek]:
    start = date(2026, 1, 7)
    return [
        EnsoWeek(week=start + timedelta(weeks=i), sst=27.0, anomaly=a)
        for i, a in enumerate(anoms)
    ]


def test_classification_bins() -> None:
    assert classify_enso(0.2) == "neutral"
    assert classify_enso(-0.3) == "neutral"
    assert classify_enso(0.7) == "weak El Nino"
    assert classify_enso(-1.2) == "moderate La Nina"
    assert classify_enso(1.7) == "strong El Nino"
    assert classify_enso(2.7) == "very strong El Nino"
    assert classify_enso(-2.1) == "very strong La Nina"


def test_status_trend() -> None:
    s = enso_status(_weeks([2.0, 2.1, 2.2, 2.3, 2.6, 2.7]), today=date(2026, 2, 20))
    assert s.anomaly == 2.7
    assert s.classification == "very strong El Nino"
    assert s.four_week_change == 0.6   # 2.7 - 2.1 (4 weeks back)
    assert s.trend == "warming"


def test_status_cooling_and_steady() -> None:
    cooling = enso_status(_weeks([1.0, 0.8, 0.6, 0.5, 0.3]), today=date(2026, 2, 20))
    assert cooling.trend == "cooling"
    steady = enso_status(_weeks([1.0, 1.0, 1.05, 1.0, 1.0]), today=date(2026, 2, 20))
    assert steady.trend == "steady"


def test_short_history() -> None:
    s = enso_status(_weeks([1.5, 1.6]), today=date(2026, 2, 20))
    assert s.four_week_change is None
    assert s.trend == "insufficient history"


def test_staleness_flag() -> None:
    s = enso_status(_weeks([1.0] * 6), today=date(2026, 6, 1))
    assert s.stale  # last fixture week is 2026-02-11, months before "today"


# --- parser regression guards against the live-file traps -------------------

def test_parser_reads_nino34_not_nino4() -> None:
    weeks = parse_wksst(FIXTURE.read_text())
    latest = weeks[-1]
    assert latest.week == date(2026, 8, 12)
    assert latest.anomaly == 2.7   # Nino 3.4; the Nino 4 column says +1.0
    assert latest.sst == 29.6


def test_parser_handles_concatenated_negatives() -> None:
    weeks = parse_wksst(FIXTURE.read_text())
    w = next(w for w in weeks if w.week == date(2021, 1, 13))
    assert w.anomaly == -1.0       # appears as "25.5-1.0" in the file


def test_parser_rejects_reordered_header() -> None:
    mutated = FIXTURE.read_text().replace(
        "Nino1+2      Nino3        Nino34        Nino4",
        "Nino34       Nino3        Nino1+2       Nino4",
    )
    with pytest.raises(ValueError, match="column order"):
        parse_wksst(mutated)


def test_parser_rejects_empty() -> None:
    with pytest.raises(ValueError):
        parse_wksst("no data here\n")
