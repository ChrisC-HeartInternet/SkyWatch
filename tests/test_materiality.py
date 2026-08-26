from skywatch.features.materiality import assess


def _digest(
    tmax: dict[str, float],
    events: list[tuple[str, str, str]] = (),
    flags: set[str] = (),
) -> dict:
    return {
        "days": [
            {"date": d, "tmax": {"panel_median": v},
             "disagreement": {"flag": d in flags}, "spread": {"flag": False}}
            for d, v in tmax.items()
        ],
        "threshold_events": [
            {"category": c, "date": d, "severity": s} for c, d, s in events
        ],
    }


def test_first_briefing_is_material() -> None:
    m = assess(None, _digest({"2026-09-01": 20.0}))
    assert m.material and m.reasons == ["no previous briefing"]


def test_unchanged_is_not_material() -> None:
    d = _digest({"2026-09-01": 20.0, "2026-09-02": 21.0})
    assert not assess(d, d).material


def test_small_drift_ignored_large_drift_material() -> None:
    a = _digest({"2026-09-01": 20.0})
    assert not assess(a, _digest({"2026-09-01": 21.4})).material
    m = assess(a, _digest({"2026-09-01": 17.5}))
    assert m.material and "moved -2.5" in m.reasons[0]


def test_new_and_cleared_events() -> None:
    a = _digest({"2026-09-01": 20.0}, events=[("wind", "2026-09-03", "moderate")])
    b = _digest({"2026-09-01": 20.0}, events=[("rain", "2026-09-02", "high")])
    m = assess(a, b)
    assert m.material
    assert any("new threshold events: rain|2026-09-02|high" in r for r in m.reasons)
    assert any("cleared: wind|2026-09-03|moderate" in r for r in m.reasons)


def test_flag_flip_material() -> None:
    a = _digest({"2026-09-01": 20.0, "2026-09-02": 20.0})
    b = _digest({"2026-09-01": 20.0, "2026-09-02": 20.0}, flags={"2026-09-02"})
    assert "uncertainty flags raised: 2026-09-02" in assess(a, b).reasons


def test_drift_only_inside_window() -> None:
    dates = {f"2026-09-{d:02d}": 20.0 for d in range(1, 11)}
    later = dict(dates)
    later["2026-09-10"] = 30.0     # day 10, outside a 7-day window
    assert not assess(_digest(dates), _digest(later), window_days=7).material
