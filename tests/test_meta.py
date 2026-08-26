from datetime import UTC, datetime

from skywatch.sources.openmeteo_meta import ModelRun, new_cycles, parse_meta, real_ids


def test_real_ids_expand_seamless_and_dedupe() -> None:
    ids = real_ids(["ecmwf_ifs025", "gfs_seamless", "ukmo_seamless", "ecmwf_ifs025"])
    assert ids == ["ecmwf_ifs025", "ncep_gfs025",
                   "ukmo_global_deterministic_10km", "ukmo_uk_deterministic_2km"]
    assert real_ids(["unknown_model"]) == ["unknown_model"]


def test_parse_meta_live_shape() -> None:
    raw = {"last_run_initialisation_time": 1787724000, "last_run_availability_time": 1787750021,
           "update_interval_seconds": 21600, "temporal_resolution_seconds": 10800}
    r = parse_meta("ecmwf_ifs025", raw)
    assert r.init_time == datetime(2026, 8, 26, 6, 0, tzinfo=UTC)
    assert r.available_time.hour == 13 and r.interval_hours == 6


def test_new_cycles_detection() -> None:
    t0 = datetime(2026, 8, 26, 6, tzinfo=UTC)
    t1 = datetime(2026, 8, 26, 12, tzinfo=UTC)
    cur = {
        "a": ModelRun(model="a", init_time=t1, available_time=t1, interval_hours=6),
        "b": ModelRun(model="b", init_time=t0, available_time=t0, interval_hours=6),
        "c": ModelRun(model="c", init_time=t0, available_time=t0, interval_hours=6),
    }
    seen = {"a": t0, "b": t0}          # c never seen
    assert new_cycles(cur, seen) == ["a", "c"]
    assert new_cycles(cur, {"a": t1, "b": t0, "c": t0}) == []
