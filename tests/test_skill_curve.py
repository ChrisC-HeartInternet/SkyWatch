"""Error-growth curve, climatology baseline, and cycle-hour skill."""

from datetime import UTC, date, datetime

from skywatch.features.skill import CLIMATOLOGY_NAME, lead_hours, model_skill

TMAX = "temperature_2m_max"


def _row(run: datetime, model: str, target: date, value: float):  # type: ignore[no-untyped-def]
    return (run, model, TMAX, target, value)


def test_lead_hours_to_midday() -> None:
    run = datetime(2026, 9, 1, 6, tzinfo=UTC)
    assert lead_hours(run, date(2026, 9, 1)) == 6.0
    assert lead_hours(run, date(2026, 9, 3)) == 54.0


def test_curve_buckets_and_climatology_crossing() -> None:
    run = datetime(2026, 9, 1, 0, tzinfo=UTC)
    obs = {date(2026, 9, d): {TMAX: 20.0} for d in (1, 2, 3, 4)}
    normals = {date(2026, 9, d): 18.0 for d in (1, 2, 3, 4)}   # climatology err = 2.0 everywhere
    rows = [
        _row(run, "m", date(2026, 9, 1), 20.5),   # lead 12h  -> err 0.5  (beats clim)
        _row(run, "m", date(2026, 9, 2), 21.0),   # lead 36h  -> err 1.0  (beats)
        _row(run, "m", date(2026, 9, 3), 22.5),   # lead 60h  -> err 2.5  (worse than clim)
        _row(run, "m", date(2026, 9, 4), 24.0),   # lead 84h  -> err 4.0
    ]
    s = model_skill(rows, obs, provisional_below_n=1, normals=normals)
    pts = {p.lead_hours: p.mae for p in s.curve["m"]}
    assert pts == {12: 0.5, 36: 1.0, 60: 2.5, 84: 4.0}
    assert all(p.mae == 2.0 for p in s.curve[CLIMATOLOGY_NAME])
    assert s.beats_climatology_until_h["m"] == 60


def test_never_crossing_is_none_and_no_normals_means_no_baseline() -> None:
    run = datetime(2026, 9, 1, 0, tzinfo=UTC)
    obs = {date(2026, 9, 2): {TMAX: 20.0}}
    s = model_skill([_row(run, "m", date(2026, 9, 2), 20.2)], obs,
                    provisional_below_n=1, normals={date(2026, 9, 2): 15.0})
    assert s.beats_climatology_until_h["m"] is None
    s2 = model_skill([_row(run, "m", date(2026, 9, 2), 20.2)], obs, provisional_below_n=1)
    assert CLIMATOLOGY_NAME not in s2.curve and s2.beats_climatology_until_h == {}


def test_cycle_hour_grouping() -> None:
    r00 = datetime(2026, 9, 1, 3, tzinfo=UTC)    # snapshot carrying the 00Z cycle
    r12 = datetime(2026, 9, 1, 15, tzinfo=UTC)   # snapshot carrying the 12Z cycle
    obs = {date(2026, 9, 2): {TMAX: 20.0}}
    rows = [_row(r00, "m", date(2026, 9, 2), 19.0), _row(r12, "m", date(2026, 9, 2), 20.5)]
    cycles = {(r00, "m"): 0, (r12, "m"): 12}
    s = model_skill(rows, obs, provisional_below_n=1, cycles=cycles)
    by = {c.init_hour: c.mae for c in s.by_cycle["m"]}
    assert by == {0: 1.0, 12: 0.5}
