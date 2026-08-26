"""Real-time lightning watcher.

A long-running daemon (launchd KeepAlive) that listens to the Blitzortung
community lightning network's live feed, keeps a rolling buffer of strikes
around home, and:

- pushes an URGENT ntfy alert when lightning strikes within the alert radius,
- pushes a HIGH alert when strikes inside the approach radius are closing
  across successive time windows,
- writes an auto-refreshing local strike map (output/stormwatch.html) and a
  machine-readable state file (output/stormwatch.json) for Hermes.

Detection maths and the alert state machine are pure and unit-tested; the
websocket client is a thin shell around them, verified live.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from skywatch import console
from skywatch.config import Config, StormwatchConfig

# --- feed decoding -------------------------------------------------------------


def lzw_decode(b: str) -> str:
    """Blitzortung's LZW variant (mirrors their web client's decoder)."""
    d = list(b)
    if not d:
        return ""
    e: dict[int, str] = {}
    f = d[0]
    result = [f]
    o = 256
    for q in range(1, len(d)):
        code = ord(d[q])
        entry = d[q] if code < 256 else e.get(code, f + f[0])
        result.append(entry)
        e[o] = f + entry[0]
        o += 1
        f = entry
    return "".join(result)


# --- geometry & state ------------------------------------------------------------

_EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return _EARTH_RADIUS_MILES * 2 * math.asin(math.sqrt(a))


class Strike(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: float          # unix seconds
    lat: float
    lon: float
    miles: float      # distance from home


class StrikeBuffer:
    """Rolling window of strikes within the monitor radius, deduplicated."""

    def __init__(self, cfg: StormwatchConfig, home_lat: float, home_lon: float) -> None:
        self.cfg = cfg
        self.home = (home_lat, home_lon)
        self.strikes: list[Strike] = []
        self._seen: set[tuple[int, int, int]] = set()

    def add(self, t: float, lat: float, lon: float) -> Strike | None:
        """Add a strike if it's inside the monitor radius and not a duplicate."""
        key = (int(t * 10), int(lat * 1000), int(lon * 1000))
        if key in self._seen:
            return None
        miles = haversine_miles(self.home[0], self.home[1], lat, lon)
        if miles > self.cfg.monitor_radius_miles:
            return None
        self._seen.add(key)
        strike = Strike(t=t, lat=lat, lon=lon, miles=round(miles, 2))
        self.strikes.append(strike)
        return strike

    def prune(self, now: float) -> None:
        horizon = now - self.cfg.window_minutes * 60
        self.strikes = [s for s in self.strikes if s.t >= horizon]
        if len(self._seen) > 50_000:
            self._seen = {k for k in self._seen if k[0] >= horizon * 10}

    def min_distance(
        self, now: float, *, since_minutes: float, until_minutes: float = 0.0
    ) -> float | None:
        """Nearest strike distance in the window [now-since, now-until]."""
        lo, hi = now - since_minutes * 60, now - until_minutes * 60
        d = [s.miles for s in self.strikes if lo <= s.t <= hi]
        return min(d) if d else None


class AlertEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str            # overhead | approaching
    severity: str        # severe | high
    title: str
    detail: str
    nearest_miles: float
    at: float


class AlertDecider:
    """Pure alert state machine over the strike buffer. Owns the cooldowns."""

    def __init__(self, cfg: StormwatchConfig) -> None:
        self.cfg = cfg
        self._last_fired: dict[str, float] = {}

    def _cooled(self, kind: str, now: float, minutes: int) -> bool:
        last = self._last_fired.get(kind)
        return last is None or (now - last) >= minutes * 60

    def evaluate(self, buffer: StrikeBuffer, now: float) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        cfg = self.cfg

        recent = buffer.min_distance(now, since_minutes=cfg.evaluate_every_seconds / 60 + 1)
        if (
            recent is not None
            and recent <= cfg.alert_radius_miles
            and self._cooled("overhead", now, cfg.overhead_cooldown_minutes)
        ):
            self._last_fired["overhead"] = now
            # An overhead alert supersedes any pending approach alert.
            self._last_fired["approaching"] = now
            events.append(AlertEvent(
                kind="overhead", severity="severe",
                title=f"Lightning within {cfg.alert_radius_miles:g} miles",
                detail=(
                    f"Strike detected {recent:.1f} miles from home in the last "
                    f"minute. Storm is overhead or immediately adjacent."
                ),
                nearest_miles=recent, at=now,
            ))
            return events

        # Approaching: nearest distance in the last 10 min is meaningfully closer
        # than in the 10-20 min window before it, and already inside the radius.
        near_now = buffer.min_distance(now, since_minutes=10)
        near_before = buffer.min_distance(now, since_minutes=20, until_minutes=10)
        if (
            near_now is not None
            and near_before is not None
            and near_now <= cfg.approach_radius_miles
            and near_before - near_now >= 3.0
            and self._cooled("approaching", now, cfg.approach_cooldown_minutes)
        ):
            self._last_fired["approaching"] = now
            events.append(AlertEvent(
                kind="approaching", severity="high",
                title=f"Lightning approaching — {near_now:.0f} miles and closing",
                detail=(
                    f"Nearest strike {near_now:.1f} miles (was {near_before:.1f} miles "
                    f"10 minutes ago). Track on the storm map."
                ),
                nearest_miles=near_now, at=now,
            ))
        return events


# --- outputs ---------------------------------------------------------------------


def write_state(path: Path, buffer: StrikeBuffer, events: list[AlertEvent], now: float) -> None:
    nearest = buffer.min_distance(now, since_minutes=buffer.cfg.window_minutes)
    payload = {
        "updated_at": datetime.fromtimestamp(now, UTC).isoformat(),
        "monitor_radius_miles": buffer.cfg.monitor_radius_miles,
        "strikes_in_window": len(buffer.strikes),
        "nearest_miles": nearest,
        "active_alerts": [e.model_dump(mode="json") for e in events],
        "strikes": [s.model_dump(mode="json") for s in buffer.strikes[-500:]],
    }
    path.write_text(json.dumps(payload, indent=1))


def render_map(
    cfg: Config, buffer: StrikeBuffer, now: float, recent_events: list[AlertEvent] | None = None
) -> str:
    """Self-contained auto-refreshing strike map: range rings + age-faded dots.

    Designed to be embedded in the dashboard via an iframe as well as viewed
    standalone; recent alert events (last 30 min) render as a banner.
    """
    sw = cfg.stormwatch
    quiet = not buffer.strikes and not (recent_events or [])
    if quiet:
        updated = datetime.fromtimestamp(now, UTC).strftime("%H:%M UTC")
        return _QUIET_PAGE.format(
            location=cfg.location.name, updated=updated, window=sw.window_minutes,
            monitor=f"{sw.monitor_radius_miles:g}",
        )
    home_lat, home_lon = cfg.location.latitude, cfg.location.longitude
    view_miles = sw.approach_radius_miles * 1.8
    size = 520.0
    px_per_mile = (size / 2) / view_miles
    cx = cy = size / 2
    coslat = math.cos(math.radians(home_lat))

    def xy(lat: float, lon: float) -> tuple[float, float]:
        x = cx + (lon - home_lon) * 69.17 * coslat * px_per_mile
        y = cy - (lat - home_lat) * 69.17 * px_per_mile
        return x, y

    parts: list[str] = []
    # Compass: the map is north-up (latitude up, longitude right). A faint
    # crosshair through home plus the four cardinal letters just outside the
    # outer ring make a strike's bearing readable without thinking.
    outer = sw.approach_radius_miles * px_per_mile
    parts.append(
        f'<line x1="{cx}" y1="0" x2="{cx}" y2="{size}" class="crosshair"/>'
        f'<line x1="0" y1="{cy}" x2="{size}" y2="{cy}" class="crosshair"/>'
    )
    for label, x, y, anchor in (
        ("N", cx, cy - outer - 10, "middle"),
        ("S", cx, cy + outer + 20, "middle"),
        ("E", cx + outer + 12, cy + 5, "start"),
        ("W", cx - outer - 12, cy + 5, "end"),
    ):
        parts.append(
            f'<text x="{x:.0f}" y="{y:.0f}" class="compass" text-anchor="{anchor}">{label}</text>'
        )
    for r_mi in (sw.alert_radius_miles, 10.0, sw.approach_radius_miles):
        r = r_mi * px_per_mile
        parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r:.0f}" fill="none" '
            f'stroke="var(--grid)" stroke-width="1"/>'
            f'<text x="{cx + 4:.0f}" y="{cy - r + 14:.0f}" class="tick">{r_mi:g} mi</text>'
        )
    for s in buffer.strikes:
        age_min = (now - s.t) / 60
        if age_min > sw.window_minutes:
            continue
        x, y = xy(s.lat, s.lon)
        if not (0 <= x <= size and 0 <= y <= size):
            continue
        freshness = max(0.15, 1 - age_min / sw.window_minutes)
        color = "var(--strike-fresh)" if age_min <= 10 else "var(--strike-old)"
        parts.append(
            f'<circle cx="{x:.0f}" cy="{y:.0f}" r="3.5" fill="{color}" '
            f'fill-opacity="{freshness:.2f}"><title>{s.miles:.1f} mi, '
            f"{age_min:.0f} min ago</title></circle>"
        )
    parts.append(
        f'<circle cx="{cx}" cy="{cy}" r="5" fill="none" '
        f'stroke="var(--text-primary)" stroke-width="2"/>'
    )
    nearest = buffer.min_distance(now, since_minutes=10)
    status = (
        f"nearest strike {nearest:.1f} mi (last 10 min)" if nearest is not None
        else "no strikes within range in the last 10 minutes"
    )
    banner = ""
    for ev in recent_events or []:
        age_min = (now - ev.at) / 60
        banner += (
            f'<div class="banner {ev.severity}">&#9889; {ev.title} '
            f'<span class="when">({age_min:.0f} min ago)</span></div>'
        )
    updated = datetime.fromtimestamp(now, UTC).strftime("%H:%M:%S UTC")
    return _MAP_PAGE.format(
        size=int(size), svg="".join(parts), status=status, updated=updated,
        n=len(buffer.strikes), window=sw.window_minutes,
        location=cfg.location.name, banner=banner,
    )


_QUIET_PAGE = """<!doctype html>
<html lang="en-GB"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<link rel="icon" href="data:,">
<title>Storm watch — {location}</title>
<style>
:root {{ color-scheme: light; --surface-1:#fcfcfb; --text-secondary:#52514e;
  --text-muted:#7d7c77; --good:#0ca30c; }}
@media (prefers-color-scheme: dark) {{ :root {{ color-scheme: dark;
  --surface-1:#1a1a19; --text-secondary:#c3c2b7; --text-muted:#8f8e86; }} }}
body {{ margin:0; padding:10px 12px; background:var(--surface-1);
  font:14px/1.5 -apple-system,"Helvetica Neue",Arial,sans-serif;
  color:var(--text-secondary); display:flex; align-items:baseline; gap:8px;
  flex-wrap:wrap; }}
.dot {{ color:var(--good); }}
.meta {{ color:var(--text-muted); font-size:12px; }}
</style></head><body>
<span class="dot">●</span>
<span>Quiet — no lightning within {monitor} miles in the last {window} min.</span>
<span class="meta">The radar view appears here when strikes are detected ·
checked {updated} · Blitzortung.org</span>
</body></html>
"""

_MAP_PAGE = """<!doctype html>
<html lang="en-GB"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<link rel="icon" href="data:,">
<title>Storm watch — {location}</title>
<style>
:root {{ color-scheme: light; --surface-1:#fcfcfb; --text-primary:#0b0b0b;
  --text-secondary:#52514e; --text-muted:#7d7c77; --grid:#e4e3df;
  --strike-fresh:#d03b3b; --strike-old:#eda100;
  --banner-severe:#d03b3b; --banner-high:#ec835a; }}
@media (prefers-color-scheme: dark) {{ :root {{ color-scheme: dark;
  --surface-1:#1a1a19; --text-primary:#fff; --text-secondary:#c3c2b7;
  --text-muted:#8f8e86; --grid:#383835; --strike-fresh:#e66767; --strike-old:#c98500; }} }}
body {{ margin:0; padding:8px 12px; background:var(--surface-1); color:var(--text-primary);
  font:14px/1.5 -apple-system,"Helvetica Neue",Arial,sans-serif;
  display:flex; flex-direction:column; align-items:center; gap:6px; }}
.status {{ color:var(--text-secondary); }}
.banner {{ width:100%; max-width:{size}px; padding:8px 12px; border-radius:8px;
  font-weight:600; color:#fff; }}
.banner.severe {{ background:var(--banner-severe); }}
.banner.high {{ background:var(--banner-high); color:#0b0b0b; }}
.banner .when {{ font-weight:400; opacity:0.85; }}
.meta {{ color:var(--text-muted); font-size:12px; text-align:center; }}
svg {{ max-width:100%; height:auto; }}
svg .tick {{ fill:var(--text-muted); font-size:11px; }}
svg .compass {{ fill:var(--text-secondary); font-size:13px; font-weight:600;
  letter-spacing:0.04em; }}
svg .crosshair {{ stroke:var(--grid); stroke-width:1; stroke-dasharray:2 5; opacity:0.8; }}
</style></head><body>
{banner}
<div class="status">{status}</div>
<svg viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img"
     aria-label="Lightning strikes around home with range rings">{svg}</svg>
<div class="meta">{n} strikes in the last {window} min &middot; updated {updated}
&middot; refreshes every minute &middot; data: Blitzortung.org community network</div>
</body></html>
"""


# --- the daemon ------------------------------------------------------------------


async def _consume(cfg: Config, buffer: StrikeBuffer, url: str, decider: AlertDecider) -> None:
    import websockets

    from skywatch.models import Alert, Severity
    from skywatch.notify import push_alerts

    state_path = cfg.output_dir / "stormwatch.json"
    map_path = cfg.output_dir / "stormwatch.html"
    last_eval = 0.0
    recent_events: list[AlertEvent] = []

    async with websockets.connect(url, open_timeout=15, ping_interval=30) as ws:
        await ws.send('{"a": 111}')
        console.log().info("stormwatch: connected to %s", url)
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=120)
            try:
                data = json.loads(lzw_decode(raw if isinstance(raw, str) else raw.decode()))
            except (ValueError, UnicodeDecodeError):
                continue
            if "lat" not in data or "lon" not in data:
                continue
            now = time.time()
            strike = buffer.add(data["time"] / 1e9, data["lat"], data["lon"])
            if strike:
                console.log().info("strike: %.1f mi (%.2f, %.2f)",
                                   strike.miles, strike.lat, strike.lon)
            if now - last_eval < cfg.stormwatch.evaluate_every_seconds:
                continue
            last_eval = now
            buffer.prune(now)
            events = decider.evaluate(buffer, now)
            recent_events = [
                e for e in [*recent_events, *events] if now - e.at <= 30 * 60
            ]
            write_state(state_path, buffer, events, now)
            map_path.write_text(render_map(cfg, buffer, now, recent_events))
            for ev in events:
                console.log().warning("ALERT %s: %s", ev.kind, ev.title)
                today = datetime.fromtimestamp(now, UTC).date()
                push_alerts(cfg.ntfy, [Alert(
                    severity=Severity(ev.severity), category="lightning",
                    title=ev.title, detail=ev.detail, confidence=0.9,
                    valid_from=today, valid_to=today,
                    sources=["blitzortung"],
                )])


async def run_stormwatch(cfg: Config) -> None:
    """Connect, consume, reconnect forever with backoff across servers."""
    buffer = StrikeBuffer(cfg.stormwatch, cfg.location.latitude, cfg.location.longitude)
    decider = AlertDecider(cfg.stormwatch)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    # An initial empty state so the map URL works before the first strike.
    write_state(cfg.output_dir / "stormwatch.json", buffer, [], time.time())
    (cfg.output_dir / "stormwatch.html").write_text(render_map(cfg, buffer, time.time()))

    backoff = 5.0
    while True:
        for url in cfg.stormwatch.servers:
            try:
                await _consume(cfg, buffer, url, decider)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                console.log().warning("stormwatch: %s dropped (%s); next server", url, exc)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300.0)


def main(cfg: Config) -> None:
    try:
        asyncio.run(run_stormwatch(cfg))
    except KeyboardInterrupt:
        console.err().print("\n[yellow]Storm watch stopped.[/yellow]")
