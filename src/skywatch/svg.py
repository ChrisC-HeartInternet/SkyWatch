"""Tiny inline-SVG chart helpers for the dashboard.

Deliberately minimal: scales, polylines, bands, bars, gridlines. Marks follow
fixed specs (2px lines, 4px rounded data-ends, hairline grids, surface gaps);
identity is carried by a legend + direct labels, values by a table, so color
is never the only channel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Scale:
    """Linear domain -> range mapping."""

    d0: float
    d1: float
    r0: float
    r1: float

    def __call__(self, v: float) -> float:
        if self.d1 == self.d0:
            return (self.r0 + self.r1) / 2
        return self.r0 + (v - self.d0) * (self.r1 - self.r0) / (self.d1 - self.d0)


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    """Clean tick values covering [lo, hi]."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / max(1, n)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if raw <= step:
            break
    # The axis must COVER the data: first tick at or below lo, last tick at or
    # above hi. Stopping at the last tick <= hi built axes too short, and any
    # value between that tick and hi drew above the plot area (off the page).
    start = math.floor(lo / step) * step
    end = math.ceil(hi / step - 1e-9) * step
    ticks = []
    t = start
    while t <= end + step * 0.01:
        ticks.append(round(t, 6))
        t += step
    return ticks


def symmetric_ticks(lim: float, n_half: int = 2) -> list[float]:
    """Ticks symmetric about zero: [-k*step .. 0 .. k*step] covering ±lim."""
    step_ticks = nice_ticks(0, lim, n_half)
    step = step_ticks[1] - step_ticks[0] if len(step_ticks) > 1 else lim
    k = max(1, math.ceil(lim / step))
    return [round(i * step, 6) for i in range(-k, k + 1)]


def fmt(v: float) -> str:
    """Trim trailing zeros: 5.0 -> '5', 5.25 -> '5.3'."""
    r = round(v, 1)
    return str(int(r)) if r == int(r) else f"{r:.1f}"


def polyline(points: list[tuple[float, float]], color: str, *, dash: str = "",
             width: float = 2.0, cls: str = "") -> str:
    if len(points) < 2:
        return dot(points[0][0], points[0][1], color) if points else ""
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    cls_attr = f' class="{cls}"' if cls else ""
    return (
        f'<polyline{cls_attr} points="{pts}" fill="none" stroke="{color}" '
        f'stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"{dash_attr}/>'
    )


def band(xs: list[float], top: list[float], bottom: list[float], fill: str,
         opacity: float = 0.12) -> str:
    """Area between two y-series (e.g. ensemble p10-p90)."""
    fwd = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, top, strict=True))
    rev = " ".join(
        f"{x:.1f},{y:.1f}" for x, y in zip(reversed(xs), list(reversed(bottom)), strict=True)
    )
    return f'<polygon points="{fwd} {rev}" fill="{fill}" opacity="{opacity}"/>'


def dot(x: float, y: float, color: str, r: float = 4.0, title: str = "") -> str:
    t = f"<title>{title}</title>" if title else ""
    return (
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{color}" '
        f'stroke="var(--surface-1)" stroke-width="2">{t}</circle>'
    )


def vbar(x: float, y_base: float, y_top: float, w: float, color: str,
         title: str = "", rx: float = 4.0) -> str:
    """Column growing up from a baseline, rounded at the data end only."""
    h = max(0.0, y_base - y_top)
    if h <= 0.1:
        return ""
    rx = min(rx, w / 2, h / 2)
    t = f"<title>{title}</title>" if title else ""
    # Rounded top corners, square baseline.
    return (
        f'<path d="M{x:.1f},{y_base:.1f} v{-(h - rx):.1f} '
        f'q0,{-rx:.1f} {rx:.1f},{-rx:.1f} h{w - 2 * rx:.1f} '
        f'q{rx:.1f},0 {rx:.1f},{rx:.1f} v{h - rx:.1f} z" fill="{color}">{t}</path>'
    )


def grid_and_axis(scale_y: Scale, ticks: list[float], x0: float, x1: float,
                  unit: str = "") -> str:
    parts = []
    for t in ticks:
        y = scale_y(t)
        parts.append(
            f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" class="grid"/>'
        )
        parts.append(
            f'<text x="{x0 - 6}" y="{y + 3.5:.1f}" class="tick" text-anchor="end">'
            f"{fmt(t)}{unit}</text>"
        )
    return "".join(parts)


def text(x: float, y: float, s: str, cls: str = "lbl", anchor: str = "middle") -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}">{s}</text>'
