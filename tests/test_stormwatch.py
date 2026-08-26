"""Storm watch: geometry, buffer, and the alert state machine."""

from skywatch.config import StormwatchConfig
from skywatch.stormwatch import AlertDecider, StrikeBuffer, haversine_miles, lzw_decode

HOME = (54.4287, -2.9613)   # Ambleside, Lake District
CFG = StormwatchConfig()

T0 = 1_000_000_000.0          # arbitrary epoch


def _buffer() -> StrikeBuffer:
    return StrikeBuffer(CFG, *HOME)


def test_haversine_known_distances() -> None:
    # Ambleside -> Windermere town (~4 mi SSE)
    assert 3.0 < haversine_miles(*HOME, 54.3805, -2.9068) < 5.0
    # Ambleside -> Kendal (~13 mi SE)
    assert 11 < haversine_miles(*HOME, 54.3280, -2.7460) < 15
    assert haversine_miles(*HOME, *HOME) == 0.0


def test_buffer_dedup_and_monitor_radius() -> None:
    b = _buffer()
    assert b.add(T0, 54.43, -2.96) is not None      # ~0.1 mi
    assert b.add(T0, 54.43, -2.96) is None          # duplicate
    assert b.add(T0, 56.8, -2.96) is None           # ~165 mi, outside monitor box
    assert len(b.strikes) == 1


def test_buffer_prune() -> None:
    b = _buffer()
    b.add(T0, 54.43, -2.96)
    b.add(T0 + 3600, 54.44, -2.97)
    b.prune(T0 + 3700)   # window 60 min: first strike is 61.7 min old
    assert len(b.strikes) == 1


def test_overhead_alert_and_cooldown() -> None:
    b, d = _buffer(), AlertDecider(CFG)
    b.add(T0, 54.44, -2.94)   # ~1.1 mi
    events = d.evaluate(b, T0 + 10)
    assert len(events) == 1
    assert events[0].kind == "overhead" and events[0].severity == "severe"
    assert events[0].nearest_miles < CFG.alert_radius_miles
    # a second strike inside the cooldown stays silent
    b.add(T0 + 60, 54.43, -2.95)
    assert d.evaluate(b, T0 + 70) == []
    # after the cooldown a new overhead strike fires again
    b.add(T0 + 16 * 60, 54.44, -2.94)
    assert d.evaluate(b, T0 + 16 * 60 + 10)[0].kind == "overhead"


def test_approach_alert_requires_closing_trend() -> None:
    b, d = _buffer(), AlertDecider(CFG)
    # 15 min ago: strike at ~22 mi; now: strike at ~12 mi -> closing by ~10 mi
    b.add(T0 - 15 * 60, 54.11, -2.96)          # ~22 mi S
    b.add(T0 - 60, 54.255, -2.96)              # ~12 mi S
    events = d.evaluate(b, T0)
    assert len(events) == 1
    assert events[0].kind == "approaching" and events[0].severity == "high"


def test_no_approach_alert_when_static_or_receding() -> None:
    b, d = _buffer(), AlertDecider(CFG)
    b.add(T0 - 15 * 60, 54.255, -2.96)         # ~12 mi then
    b.add(T0 - 60, 54.11, -2.96)               # ~22 mi now (receding)
    assert d.evaluate(b, T0) == []


def test_no_approach_alert_outside_radius() -> None:
    b, d = _buffer(), AlertDecider(CFG)
    # closing fast but still ~60 -> ~40 mi out
    b.add(T0 - 15 * 60, 53.56, -2.96)          # ~60 mi S
    b.add(T0 - 60, 53.85, -2.96)               # ~40 mi S
    assert d.evaluate(b, T0) == []


def test_overhead_supersedes_approach() -> None:
    b, d = _buffer(), AlertDecider(CFG)
    b.add(T0 - 15 * 60, 54.255, -2.96)
    b.add(T0 - 30, 54.44, -2.94)               # overhead now
    events = d.evaluate(b, T0)
    assert [e.kind for e in events] == ["overhead"]
    # and the approach alert doesn't then fire right after
    assert d.evaluate(b, T0 + 40) == []


def test_lzw_roundtrip_plain_ascii() -> None:
    # codes < 256 pass through: a plain JSON string survives the decoder
    assert lzw_decode('{"lat": 1}') == '{"lat": 1}'
    assert lzw_decode("") == ""
