from datetime import datetime
from zoneinfo import ZoneInfo

from skywatch.watch import anchors_due

LON = ZoneInfo("Europe/London")


def test_anchor_due_once_per_day() -> None:
    now = datetime(2026, 9, 1, 7, 0, tzinfo=LON)
    assert anchors_due(["06:30", "16:30"], now, {}) == ["06:30"]
    fired = {"06:30": "2026-09-01"}
    assert anchors_due(["06:30", "16:30"], now, fired) == []
    later = datetime(2026, 9, 1, 16, 45, tzinfo=LON)
    assert anchors_due(["06:30", "16:30"], later, fired) == ["16:30"]


def test_anchor_fired_yesterday_is_due_again() -> None:
    now = datetime(2026, 9, 2, 6, 31, tzinfo=LON)
    assert anchors_due(["06:30"], now, {"06:30": "2026-09-01"}) == ["06:30"]


def test_before_anchor_not_due() -> None:
    now = datetime(2026, 9, 1, 6, 29, tzinfo=LON)
    assert anchors_due(["06:30"], now, {}) == []
