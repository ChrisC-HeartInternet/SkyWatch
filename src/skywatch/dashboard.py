"""Render dashboard.html: a self-contained page of inline-SVG charts.

Draws ONLY what features/ already computed (via digest.json and state.db) —
no analysis happens here. No external assets: works offline, from file://,
and survives being copied anywhere.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from skywatch import svg
from skywatch.config import Config, project_root
from skywatch.features.gridmap import GridMapSummary
from skywatch.features.skill import (
    LEAD_BUCKETS,
    OverallScore,
    SkillSummary,
    VariableSkill,
)
from skywatch.features.trends import forecast_trends
from skywatch.models import Alert
from skywatch.state import StateDB

# Fixed model->slot assignment (color follows the entity, never rank).
_SERIES_VARS = ["--series-1", "--series-2", "--series-3", "--series-4"]

_MODEL_SHORT = {
    "ecmwf_ifs025": "ECMWF",
    "gfs_seamless": "GFS",
    "icon_seamless": "ICON",
    "ukmo_seamless": "UKMO",
}


def render_dashboard(
    cfg: Config,
    run_dir: Path,
    digest: dict[str, Any],
    briefing_md: str,
    alerts: list[Alert],
    grid: GridMapSummary | None = None,
    skill: SkillSummary | None = None,
    briefing_at: datetime | None = None,
) -> Path:
    env = Environment(
        loader=FileSystemLoader(project_root() / "templates"), autoescape=False
    )
    tpl = env.get_template("dashboard.html.j2")

    days = digest.get("days", [])
    meta = digest.get("meta", {})
    drivers = digest.get("global_drivers", {})
    enso = drivers.get("enso", {})
    vortex = drivers.get("stratospheric_vortex", {})
    models = [m for m in meta.get("deterministic_models", []) if _model_present(days, m)]

    run_at = datetime.fromisoformat(meta["run_at"])

    sev_order = ["severe", "high", "moderate", "low"]
    counts = [(s, sum(1 for a in alerts if a.severity == s)) for s in sev_order]

    html_out = tpl.render(
        location=meta.get("location", cfg.location.name),
        run_date=run_at.strftime("%Y-%m-%d"),
        run_at=run_at.strftime("%Y-%m-%d %H:%M UTC"),
        alert_counts=[(s, n) for s, n in counts if n],
        alerts=[_alert_view(a) for a in alerts],
        enso_chip=f"{enso.get('anomaly', 0):+.1f}°C {enso.get('trend', '')}".strip(),
        vortex_chip=f"{vortex.get('current_u', 0):+.1f} m/s",
        n_days=len(days),
        model_legend=[_MODEL_SHORT.get(m, m) for m in models],
        ens_label=_ens_label(days),
        temp_chart=_temp_chart(days, models),
        temp_table=_temp_table(days, models),
        disagreement_chart=_disagreement_chart(days),
        anomaly_chart=_anomaly_chart(days),
        trend_charts=_trend_charts(cfg, days),
        enso_chart=_enso_chart(enso),
        enso_caption=(
            f"Weekly Niño 3.4 anomaly, most recent {len(enso.get('recent_weeks', []))} weeks. "
            f"Now {enso.get('anomaly', 0):+.1f}°C — {enso.get('classification', '?')}"
            + (", data stale" if enso.get("stale") else "")
            + "."
        ),
        vortex_chart=_vortex_chart(vortex),
        vortex_caption=(
            f"Zonal-mean zonal wind at 10 hPa / 60°N: 16-day forecast vs the NCEP "
            f"daily normal. {vortex.get('state', '?').capitalize()}; below 0 m/s in "
            f"winter = sudden stratospheric warming."
        ),
        briefing_html=_markdown_to_html(briefing_md),
        headline=_extract_headline(briefing_md),
        briefing_stamp=_briefing_stamp(digest, briefing_at),
        **_map_panels(cfg, grid),
        **_skill_panel(cfg, skill),
    )
    out = run_dir / "dashboard.html"
    out.write_text(html_out)
    return out


# --- panels -------------------------------------------------------------------


def _model_present(days: list[dict[str, Any]], model: str) -> bool:
    return any(model in d.get("tmax", {}).get("by_model", {}) for d in days)


def _ens_label(days: list[dict[str, Any]]) -> str:
    n = max((d.get("tmax_ensemble", {}).get("n_members", 0) for d in days), default=0)
    return f"{n} members"


def _day_letter(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%a")


def _temp_chart(days: list[dict[str, Any]], models: list[str]) -> str:
    if not days:
        return ""
    w, h = 1020, 300
    ml, mr, mt, mb = 46, 130, 14, 44
    xs = svg.Scale(0, max(1, len(days) - 1), ml, w - mr)

    vals: list[float] = []
    for d in days:
        for v in d.get("tmax", {}).get("by_model", {}).values():
            vals.append(v)
        ens = d.get("tmax_ensemble", {})
        vals.extend(v for v in (ens.get("p10"), ens.get("p90")) if v is not None)
    if not vals:
        return ""
    ticks = svg.nice_ticks(min(vals) - 1, max(vals) + 1)
    ys = svg.Scale(ticks[0], ticks[-1], h - mb, mt)

    parts = [svg.grid_and_axis(ys, ticks, ml, w - mr, "°")]

    # Ensemble p10-p90 band + median (neutral: the band is context, models are identity).
    bxs, top, bot, med = [], [], [], []
    for i, d in enumerate(days):
        ens = d.get("tmax_ensemble", {})
        if ens.get("p10") is not None and ens.get("p90") is not None:
            bxs.append(xs(i))
            top.append(ys(ens["p90"]))
            bot.append(ys(ens["p10"]))
            med.append((xs(i), ys(ens["median"])))
    if bxs:
        parts.append(svg.band(bxs, top, bot, "var(--neutral-band)"))
        parts.append(svg.polyline(med, "var(--neutral-band)", dash="5 4"))

    # One line per model; direct label at the line's end (selective labeling).
    ends: list[tuple[float, float, str]] = []
    for si, m in enumerate(models):
        color = f"var({_SERIES_VARS[si % len(_SERIES_VARS)]})"
        pts = [
            (xs(i), ys(d["tmax"]["by_model"][m]))
            for i, d in enumerate(days)
            if m in d.get("tmax", {}).get("by_model", {})
        ]
        if not pts:
            continue
        parts.append(svg.polyline(pts, color))
        lx, ly = pts[-1]
        parts.append(svg.dot(lx, ly, color, title=_MODEL_SHORT.get(m, m)))
        ends.append((lx, ly, _MODEL_SHORT.get(m, m)))
    for (lx, _ly, name), label_y in zip(
        ends, _spread_positions([e[1] for e in ends], min_gap=13.0), strict=True
    ):
        parts.append(svg.text(lx + 8, label_y + 4, name, "lbl", "start"))

    # X labels + per-day panel size (the honesty row).
    for i, d in enumerate(days):
        n = d.get("tmax", {}).get("n_models", 0)
        parts.append(svg.text(xs(i), h - mb + 16, _day_letter(d["date"]), "lbl"))
        parts.append(svg.text(xs(i), h - mb + 30, f"{n} mdl", "tick"))

    return _svg_wrap(w, h, parts)


def _spread_positions(ys: list[float], *, min_gap: float) -> list[float]:
    """Nudge overlapping label y-positions apart, preserving vertical order."""
    order = sorted(range(len(ys)), key=lambda i: ys[i])
    adjusted = list(ys)
    prev: float | None = None
    for i in order:
        if prev is not None and adjusted[i] < prev + min_gap:
            adjusted[i] = prev + min_gap
        prev = adjusted[i]
    return adjusted


def _temp_table(days: list[dict[str, Any]], models: list[str]) -> str:
    """The table view: every plotted tmax value, plus ensemble stats."""
    head = "".join(
        f"<th>{date.fromisoformat(d['date']).strftime('%a %d')}</th>" for d in days
    )
    rows = []
    for si, m in enumerate(models):
        cells = "".join(
            "<td>"
            + (svg.fmt(d["tmax"]["by_model"][m])
               if m in d.get("tmax", {}).get("by_model", {}) else "–")
            + "</td>"
            for d in days
        )
        swatch = (f'<span style="display:inline-block;width:10px;height:10px;'
                  f'border-radius:2px;background:var({_SERIES_VARS[si % 4]});'
                  f'margin-right:6px"></span>')
        rows.append(f"<tr><td>{swatch}{_MODEL_SHORT.get(m, m)}</td>{cells}</tr>")
    ens_cells = "".join(
        (lambda e: f"<td>{svg.fmt(e['median']) if e.get('median') is not None else '–'}</td>")(
            d.get("tmax_ensemble", {})
        )
        for d in days
    )
    rows.append(f"<tr><td>Ens. median</td>{ens_cells}</tr>")
    return f"<table><tr><th>Model (°C)</th>{head}</tr>{''.join(rows)}</table>"


def _disagreement_chart(days: list[dict[str, Any]]) -> str:
    if not days:
        return ""
    w, h = 500, 210
    ml, mr, mt, mb = 40, 10, 12, 44
    slot = (w - ml - mr) / max(1, len(days))
    bar_w = min(24.0, slot - 8)
    ranges = [d.get("tmax", {}).get("range") for d in days]
    hi = max((r for r in ranges if r is not None), default=1.0)
    ticks = svg.nice_ticks(0, max(hi, 2.0), 4)
    ys = svg.Scale(ticks[0], ticks[-1], h - mb, mt)
    parts = [svg.grid_and_axis(ys, ticks, ml, w - mr, "°")]
    for i, d in enumerate(days):
        t = d.get("tmax", {})
        r, n = t.get("range"), t.get("n_models", 0)
        cx = ml + slot * (i + 0.5)
        if r is None:
            parts.append(svg.text(cx, ys(0) - 6, "n/a", "tick"))
        else:
            flagged = t.get("divergence_flagged")
            color = "var(--status-serious)" if flagged else "var(--series-1)"
            title = f"{d['date']}: range {svg.fmt(r)}°C across {n} models"
            parts.append(svg.vbar(cx - bar_w / 2, ys(0), ys(r), bar_w, color, title))
            if flagged:
                parts.append(svg.text(cx, ys(r) - 6, "⚠", "lbl-strong"))
        parts.append(svg.text(cx, h - mb + 16, _day_letter(d["date"]), "lbl"))
        parts.append(svg.text(cx, h - mb + 30, f"n={n}", "tick"))
    return _svg_wrap(w, h, parts, label="Cross-model temperature disagreement per day")


def _anomaly_chart(days: list[dict[str, Any]]) -> str:
    if not days:
        return ""
    w, h = 500, 210
    ml, mr, mt, mb = 40, 10, 12, 30
    slot = (w - ml - mr) / max(1, len(days))
    bar_w = min(24.0, slot - 8)
    anoms = [d.get("vs_normal", {}).get("tmax_anomaly_c") for d in days]
    present = [a for a in anoms if a is not None]
    lim = max(3.0, max((abs(a) for a in present), default=3.0) + 0.5)
    ticks = svg.symmetric_ticks(lim, 3)
    ys = svg.Scale(ticks[0], ticks[-1], h - mb, mt)
    parts = [svg.grid_and_axis(ys, ticks, ml, w - mr, "°")]
    parts.append(f'<line x1="{ml}" y1="{ys(0):.1f}" x2="{w - mr}" y2="{ys(0):.1f}" class="zero"/>')
    for i, (d, a) in enumerate(zip(days, anoms, strict=True)):
        cx = ml + slot * (i + 0.5)
        if a is not None:
            color = "var(--diverge-warm)" if a > 0 else "var(--diverge-cool)"
            y0, y1 = ys(0), ys(a)
            if a >= 0:
                parts.append(svg.vbar(cx - bar_w / 2, y0, y1, bar_w, color,
                                      f"{d['date']}: {a:+.1f}°C vs normal"))
            else:
                # Downward bar: flip vertically about the bar's own extent so the
                # rounded data-end lands at the anomaly and the square end at zero.
                parts.append(
                    f'<g transform="translate(0,{y0 + y1:.1f}) scale(1,-1)">'
                    + svg.vbar(cx - bar_w / 2, y1, y0, bar_w, color,
                               f"{d['date']}: {a:+.1f}°C vs normal")
                    + "</g>"
                )
            label_y = (ys(a) - 6) if a >= 0 else min(ys(a) + 14, h - mb - 4)
            parts.append(svg.text(cx, label_y, f"{a:+.1f}", "lbl"))
        parts.append(svg.text(cx, h - mb + 16, _day_letter(d["date"]), "lbl"))
    return _svg_wrap(
        w, h, parts, label="Daily maximum temperature departure from the 1991-2020 normal"
    )


def _trend_charts(cfg: Config, days: list[dict[str, Any]]) -> str:
    """Small multiples: one tile per target date, tmax across recent runs."""
    target_dates = [date.fromisoformat(d["date"]) for d in days]
    db = StateDB(cfg.state_db)
    history = db.history(target_dates)
    trends = {
        (t.target_date, t.variable): t
        for t in forecast_trends(history, max_runs=6)
    }
    tiles = []
    for td in target_dates:
        t = trends.get((td, "temperature_2m_max"))
        if t is None or t.n_runs < 2:
            continue
        tiles.append(_trend_tile(td, t.values, t.delta))
    if not tiles:
        return ""
    return '<div style="display:flex;flex-wrap:wrap;gap:14px">' + "".join(tiles) + "</div>"


def _trend_tile(td: date, values: list[float], delta: float | None) -> str:
    w, h = 128, 96
    ml, mr, mt, mb = 8, 8, 26, 10
    lo, hi = min(values), max(values)
    pad = max(0.5, (hi - lo) * 0.15)
    ys = svg.Scale(lo - pad, hi + pad, h - mb, mt)
    xs = svg.Scale(0, max(1, len(values) - 1), ml, w - mr)
    pts = [(xs(i), ys(v)) for i, v in enumerate(values)]
    d = delta or 0.0
    color = ("var(--neutral-band)" if abs(d) < 0.5
             else "var(--diverge-cool)" if d < 0 else "var(--diverge-warm)")
    parts = [
        svg.text(w / 2, 12, td.strftime("%a %d %b"), "lbl-strong"),
        svg.polyline(pts, color),
        svg.dot(*pts[-1], color, r=4,
                title=f"{td}: {' → '.join(svg.fmt(v) for v in values)} °C"),
        svg.text(w / 2, h - 1,
                 (f"steady over {len(values)} runs" if abs(d) < 0.5
                  else f"{d:+.1f}°C over {len(values)} runs"),
                 "tick"),
    ]
    return _svg_wrap(w, h, parts, inline=True, label=f"Forecast drift for {td.strftime('%d %B')}")


def _enso_chart(enso: dict[str, Any]) -> str:
    weeks: list[float] = enso.get("recent_weeks", [])
    if not weeks:
        return '<p class="note">No ENSO data.</p>'
    w, h = 500, 190
    ml, mr, mt, mb = 42, 60, 12, 26
    lim = max(1.0, max(abs(v) for v in weeks) + 0.4)
    ticks = svg.symmetric_ticks(lim, 3)
    ys = svg.Scale(ticks[0], ticks[-1], h - mb, mt)
    xs = svg.Scale(0, max(1, len(weeks) - 1), ml, w - mr)
    pts = [(xs(i), ys(v)) for i, v in enumerate(weeks)]
    parts = [
        svg.grid_and_axis(ys, ticks, ml, w - mr, "°"),
        f'<line x1="{ml}" y1="{ys(0):.1f}" x2="{w - mr}" y2="{ys(0):.1f}" class="zero"/>',
        svg.polyline(pts, "var(--series-1)"),
        svg.dot(*pts[-1], "var(--series-1)",
                title=f"latest week: {weeks[-1]:+.1f}°C"),
        svg.text(pts[-1][0] + 9, pts[-1][1] + 4, f"{weeks[-1]:+.1f}°C", "lbl-strong", "start"),
        svg.text(ml, h - 6, "oldest", "tick", "start"),
        svg.text(w - mr, h - 6, f"week of {enso.get('week', '?')}", "tick", "end"),
    ]
    return _svg_wrap(w, h, parts, label="Weekly Nino 3.4 anomaly, recent weeks")


def _vortex_chart(vortex: dict[str, Any]) -> str:
    fc: list[float] = vortex.get("forecast_u", [])
    if not fc:
        return '<p class="note">No vortex data.</p>'
    w, h = 500, 190
    ml, mr, mt, mb = 42, 96, 12, 26
    normal = vortex.get("climate_normal")
    vals = fc + ([normal] if normal is not None else [])
    lo, hi = min(min(vals), -2.0), max(max(vals), 2.0)
    ticks = svg.nice_ticks(lo - 1, hi + 1, 4)
    ys = svg.Scale(ticks[0], ticks[-1], h - mb, mt)
    xs = svg.Scale(0, max(1, len(fc) - 1), ml, w - mr)
    pts = [(xs(i), ys(v)) for i, v in enumerate(fc)]
    parts = [
        svg.grid_and_axis(ys, ticks, ml, w - mr, ""),
        f'<line x1="{ml}" y1="{ys(0):.1f}" x2="{w - mr}" y2="{ys(0):.1f}" class="zero"/>',
    ]
    # Reference-line labels share the right margin; in summer the climatological
    # normal sits within a metre per second of zero, so spread them apart.
    ref_labels: list[tuple[float, str]] = [(ys(0), "0 = reversal")]
    if normal is not None:
        parts.append(svg.polyline([(xs(0), ys(normal)), (xs(len(fc) - 1), ys(normal))],
                                  "var(--neutral-band)", dash="5 4"))
        ref_labels.append((ys(normal), "normal"))
    for (_y, label), ly in zip(
        ref_labels, _spread_positions([y for y, _ in ref_labels], min_gap=12.0), strict=True
    ):
        parts.append(svg.text(w - mr + 4, ly + 4, label, "tick", "start"))
    parts.append(svg.polyline(pts, "var(--series-1)"))
    parts.append(svg.dot(*pts[0], "var(--series-1)", title=f"today: {fc[0]:+.1f} m/s"))
    parts.append(svg.text(ml, h - 6, "today", "tick", "start"))
    parts.append(svg.text(w - mr, h - 6, f"+{len(fc) - 1} days", "tick", "end"))
    parts.append(
        svg.text(pts[0][0] + 2, pts[0][1] - 12, f"{fc[0]:+.1f} m/s", "lbl-strong", "start")
    )
    return _svg_wrap(w, h, parts, label="Stratospheric vortex wind, 16-day forecast vs normal")


def _svg_wrap(
    w: int, h: int, parts: list[str], *, inline: bool = False, label: str = ""
) -> str:
    style = "" if not inline else ' style="flex:0 0 auto"'
    aria = f' aria-label="{html.escape(label, quote=True)}"' if label else ""
    return (
        f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}"'
        f' role="img"{aria}{style} xmlns="http://www.w3.org/2000/svg">'
        + "".join(parts) + "</svg>"
    )


# --- model accuracy -------------------------------------------------------------

_SKILL_VAR_LABELS = {
    "temperature_2m_max": ("Max temp", "°C"),
    "temperature_2m_min": ("Min temp", "°C"),
    "precipitation_sum": ("Precip", "mm"),
    "wind_gusts_10m_max": ("Gusts", "mph"),
}


_EMPTY_VS = VariableSkill(buckets={}, overall=OverallScore(n=0, mae=0.0, bias=0.0))


def _skill_color(cfg: Config, model: str) -> str:
    if model in cfg.models.deterministic:
        idx = cfg.models.deterministic.index(model)
        return f"var({_SERIES_VARS[idx % len(_SERIES_VARS)]})"
    return "var(--neutral-band)"   # the panel median baseline


def _skill_panel(cfg: Config, skill: SkillSummary | None) -> dict[str, Any]:
    out: dict[str, Any] = {"skill_chart": "", "skill_table": "", "skill_caption": "",
                           "skill_curve": "", "skill_clim_note": ""}
    if skill is None or not skill.by_model:
        return out
    models = [m for m in [*cfg.models.deterministic, "panel_median"] if m in skill.by_model]

    out["skill_chart"] = _skill_chart(cfg, skill, models)
    out["skill_table"] = _skill_table(cfg, skill, models)
    out["skill_curve"] = _skill_curve_chart(cfg, skill, models)
    out["skill_clim_note"] = _clim_note(skill, models)
    any_prov = any(
        vs.overall.provisional
        for m in models
        for vs in skill.by_model[m].variables.values()
    )
    out["skill_caption"] = (
        f"Every stored forecast scored against ERA5 observations, "
        f"{skill.first_verified} to {skill.last_verified} "
        f"({skill.total_samples} forecast–observation pairs). Lower MAE is better; "
        f"the panel median is the blend each model has to beat."
        + (" Ratings marked * are provisional — not enough distinct days verified yet."
           if any_prov else "")
    )
    return out


def _skill_chart(cfg: Config, skill: SkillSummary, models: list[str]) -> str:
    """Grouped bars: tmax MAE per lead bucket, one bar per model."""
    var = "temperature_2m_max"
    buckets = [
        b for b in LEAD_BUCKETS
        if any(b in skill.by_model[m].variables.get(var, _EMPTY_VS).buckets for m in models)
    ]
    if not buckets:
        return ""
    w, h = 520, 220
    ml, mr, mt, mb = 40, 10, 12, 34
    maes = [
        skill.by_model[m].variables[var].buckets[b].mae
        for m in models for b in buckets
        if var in skill.by_model[m].variables
        and b in skill.by_model[m].variables[var].buckets
    ]
    ticks = svg.nice_ticks(0, max(maes + [1.0]), 4)
    ys = svg.Scale(ticks[0], ticks[-1], h - mb, mt)
    slot = (w - ml - mr) / len(buckets)
    bar_w = min(18.0, (slot - 24) / len(models) - 2)
    parts = [svg.grid_and_axis(ys, ticks, ml, w - mr, "°")]
    for bi, bucket in enumerate(buckets):
        group_w = len(models) * (bar_w + 2)
        x0 = ml + slot * bi + (slot - group_w) / 2
        for mi, m in enumerate(models):
            vs = skill.by_model[m].variables.get(var)
            if vs is None or bucket not in vs.buckets:
                continue
            bs = vs.buckets[bucket]
            x = x0 + mi * (bar_w + 2)
            parts.append(svg.vbar(
                x, ys(0), ys(bs.mae), bar_w, _skill_color(cfg, m),
                title=f"{_MODEL_SHORT.get(m, m)}, lead {bucket} days: "
                      f"MAE {bs.mae}°C, bias {bs.bias:+}°C, n={bs.n}",
            ))
        parts.append(svg.text(ml + slot * (bi + 0.5), h - mb + 16,
                              f"{bucket} days ahead", "lbl"))
    return _svg_wrap(w, h, parts, label="Max-temperature error by model and lead time")


def _skill_curve_chart(cfg: Config, skill: SkillSummary, models: list[str]) -> str:
    """Error-growth curve: tmax MAE vs lead hours, one line per model, plus the
    climatology baseline. The crossing is where a model stops adding information."""
    from skywatch.features.skill import CLIMATOLOGY_NAME

    # A bucket with a handful of pairs is noise, not a curve — and with a very
    # anomalous week the climatology baseline swings wildly per bucket. Plot
    # only buckets with enough samples; the crossing claim is gated separately.
    series = {
        m: [p for p in pts if p.n >= _CURVE_MIN_N]
        for m, pts in skill.curve.items()
        if m in models or (m == CLIMATOLOGY_NAME and not _tmax_provisional(skill, models))
    }
    series = {m: pts for m, pts in series.items() if pts}
    if sum(len(p) for p in series.values()) < 2:
        return ""
    w, h = 520, 220
    ml, mr, mt, mb = 40, 96, 12, 34
    max_h = max(p.lead_hours for pts in series.values() for p in pts) + 12
    max_mae = max(p.mae for pts in series.values() for p in pts)
    ticks = svg.nice_ticks(0, max(max_mae, 0.5), 4)
    ys = svg.Scale(ticks[0], ticks[-1], h - mb, mt)
    xs = svg.Scale(0, max_h, ml, w - mr)
    parts = [svg.grid_and_axis(ys, ticks, ml, w - mr, "°")]
    ends: list[tuple[float, float, str, str]] = []
    for m, pts in series.items():
        if len(pts) < 1:
            continue
        color = "var(--text-muted)" if m == CLIMATOLOGY_NAME else _skill_color(cfg, m)
        coords = [(xs(p.lead_hours + 6), ys(p.mae)) for p in pts]
        if len(coords) >= 2:
            parts.append(svg.polyline(coords, color, dash="6 4" if m == CLIMATOLOGY_NAME else ""))
        for p, (x, y) in zip(pts, coords, strict=True):
            parts.append(svg.dot(x, y, color, r=2.5,
                                 title=f"{_MODEL_SHORT.get(m, m)} at +{p.lead_hours}h: "
                                       f"MAE {p.mae}°C, n={p.n}"))
        label = ("normal for the date" if m == CLIMATOLOGY_NAME
                 else _MODEL_SHORT.get(m, "Panel median"))
        ends.append((coords[-1][0], coords[-1][1], label, color))
    for (x, _y, label, _c), ly in zip(
        ends, _spread_positions([e[1] for e in ends], min_gap=13.0), strict=True
    ):
        parts.append(svg.text(x + 8, ly + 4, label, "lbl", "start"))
    for hrs in range(0, int(max_h) + 1, 48):
        parts.append(svg.text(xs(hrs), h - mb + 16, f"+{hrs // 24}d", "lbl"))
    return _svg_wrap(
        w, h, parts, label="Max-temperature error growth with lead time, vs climatology"
    )


_CURVE_MIN_N = 4


def _tmax_provisional(skill: SkillSummary, models: list[str]) -> bool:
    tmax = "temperature_2m_max"
    return any(
        skill.by_model[m].variables[tmax].overall.provisional
        for m in models if tmax in skill.by_model[m].variables
    )


def _clim_note(skill: SkillSummary, models: list[str]) -> str:
    """The 'beats climatology to ~N days' claim — only once ratings are no longer
    provisional. Before that the baseline is a few days of (possibly anomalous)
    weather and the crossing point is noise, so neither the line nor the claim
    is shown."""
    if _tmax_provisional(skill, models):
        return ("The climatology baseline (dashed) and the crossing point appear once "
                "two weeks of days have verified.")
    bits = []
    for m in models:
        if m not in skill.beats_climatology_until_h:
            continue
        until = skill.beats_climatology_until_h[m]
        name = _MODEL_SHORT.get(m, "Panel median")
        span = "all leads so far" if until is None else f"to ~{until / 24:.1f} days"
        bits.append(f"{name}: {span}")
    if not bits:
        return ""
    return "Beats climatology — " + " · ".join(bits) + "."


def _skill_table(cfg: Config, skill: SkillSummary, models: list[str]) -> str:
    variables = [v for v in _SKILL_VAR_LABELS if any(
        v in skill.by_model[m].variables for m in models)]
    head = "".join(
        f"<th>{_SKILL_VAR_LABELS[v][0]} MAE ({_SKILL_VAR_LABELS[v][1]})</th>"
        for v in variables
    ) + "<th>vs median (tmax)</th>"
    rows = []
    for m in models:
        swatch = (f'<span style="display:inline-block;width:10px;height:10px;'
                  f'border-radius:2px;background:{_skill_color(cfg, m)};'
                  f'margin-right:6px"></span>')
        cells = []
        for v in variables:
            vs = skill.by_model[m].variables.get(v)
            if vs is None:
                cells.append("<td>–</td>")
                continue
            o = vs.overall
            star = "*" if o.provisional else ""
            best = ' style="font-weight:600"' if o.rank == 1 else ""
            cells.append(f"<td{best}>{o.mae}{star}</td>")
        tmax = skill.by_model[m].variables.get("temperature_2m_max")
        vsm = ""
        if tmax and tmax.overall.vs_median_pct is not None and m != "panel_median":
            vsm = f"{tmax.overall.vs_median_pct:+.0f}%"
        rows.append(
            f"<tr><td>{swatch}{_MODEL_SHORT.get(m, 'Panel median')}</td>"
            f"{''.join(cells)}<td>{vsm}</td></tr>"
        )
    return (f"<table><tr><th>Model</th>{head}</tr>{''.join(rows)}</table>")


# --- weather maps ---------------------------------------------------------------

_MAP_RAMPS = {  # variable -> CSS token; each map is a one-hue opacity ramp
    "temperature_2m_max": "var(--ramp-temp)",
    "precipitation_sum": "var(--ramp-precip)",
    "wind_gusts_10m_max": "var(--ramp-gust)",
    "disagreement": "var(--ramp-disagree)",
}
_MAP_UNITS = {
    "temperature_2m_max": "°C",
    "precipitation_sum": "mm",
    "wind_gusts_10m_max": "mph",
    "disagreement": "°C spread",
}


def _load_coastline() -> list[list[list[float]]]:
    path = project_root() / "templates" / "coastline_uk.json"
    chunks: list[list[list[float]]] = json.loads(path.read_text())["chunks"]
    return chunks  # each chunk is a polyline of [lon, lat] points


def _map_svg(
    cfg: Config,
    grid: GridMapSummary,
    values: list[float | None],
    *,
    ramp: str,
    unit: str,
    title: str,
    width: float = 300.0,
    show_legend: bool = True,
) -> str:
    """One map: opacity-ramped grid cells under a coastline, plus home marker."""
    import math

    g = cfg.gridmap
    mid = math.radians((g.lat_min + g.lat_max) / 2)
    lat_span = g.lat_max - g.lat_min + g.step
    lon_span = (g.lon_max - g.lon_min + g.step) * math.cos(mid)
    px = width / lon_span                      # px per degree (lon, cos-scaled)
    w = width
    header = 16.0
    h = lat_span * px + header + (16 if show_legend else 4)

    def xy(lat: float, lon: float) -> tuple[float, float]:
        x = (lon - (g.lon_min - g.step / 2)) * math.cos(mid) * px
        y = header + ((g.lat_max + g.step / 2) - lat) * px
        return x, y

    present = [v for v in values if v is not None]
    if not present:
        return ""
    vmin, vmax = min(present), max(present)
    spread = (vmax - vmin) or 1.0

    cw = g.step * math.cos(mid) * px
    ch = g.step * px
    parts: list[str] = [svg.text(0, 11, title, "lbl-strong", "start")]
    for lat, lon, v in zip(grid.lats, grid.lons, values, strict=True):
        if v is None:
            continue
        x, y = xy(lat, lon)
        t = (v - vmin) / spread
        parts.append(
            f'<rect x="{x - cw / 2:.1f}" y="{y - ch / 2:.1f}" '
            f'width="{cw:.1f}" height="{ch:.1f}" fill="{ramp}" '
            f'fill-opacity="{0.06 + 0.9 * t:.2f}">'
            f"<title>{lat:.1f}, {lon:.1f}: {v:g} {unit}</title></rect>"
        )
    map_top, map_bottom = header, header + lat_span * px
    for chunk in _load_coastline():
        pts = [xy(lat, lon) for lon, lat in chunk]
        pts = [(x, y) for x, y in pts if -2 < x < w + 2 and map_top - 2 < y < map_bottom + 2]
        if len(pts) >= 2:
            parts.append(svg.polyline(pts, "var(--coast)", width=1.0))
    hx, hy = xy(cfg.location.latitude, cfg.location.longitude)
    parts.append(
        f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="3.5" fill="none" '
        f'stroke="var(--text-primary)" stroke-width="1.5">'
        f"<title>{cfg.location.name}</title></circle>"
    )
    if show_legend:
        ly = h - 5
        labels = [(0.06, vmin, unit), (0.51, (vmin + vmax) / 2, ""), (0.96, vmax, unit)]
        for i, (t, v, u) in enumerate(labels):
            lx = 4 + i * (width / 2.9)
            parts.append(
                f'<rect x="{lx:.1f}" y="{ly - 9:.1f}" width="10" height="10" rx="2" '
                f'fill="{ramp}" fill-opacity="{t:.2f}"/>'
            )
            parts.append(svg.text(lx + 14, ly, f"{svg.fmt(v)} {u}".rstrip(), "tick", "start"))
    return _svg_wrap(int(w), int(h), parts, inline=True, label=title)


def _map_panels(cfg: Config, grid: GridMapSummary | None) -> dict[str, Any]:
    """The template variables for the map section; empty strings when no grid."""
    out: dict[str, Any] = {
        "map_disagreement": "", "map_tomorrow": "", "map_week": "", "map_facts": [],
        "map_step": cfg.gridmap.step, "map_points": 0,
        "n_map_models": len(cfg.models.deterministic),
    }
    if grid is None or not grid.days:
        return out
    out["map_points"] = len(grid.lats)

    dd = grid.days[grid.max_disagreement_day]
    out["map_disagreement"] = _map_svg(
        cfg, grid, dd.tmax_range,
        ramp=_MAP_RAMPS["disagreement"], unit=_MAP_UNITS["disagreement"],
        title=f"Where the models disagree — {dd.date.strftime('%a %d %b')}",
        width=380,
    )

    if len(grid.days) > 1:
        d1 = grid.days[1]
        when = d1.date.strftime("%A")
        tiles = []
        for var, name in [
            ("temperature_2m_max", "Max temperature"),
            ("precipitation_sum", "Precipitation"),
            ("wind_gusts_10m_max", "Wind gusts"),
        ]:
            tiles.append(_map_svg(
                cfg, grid, d1.fields[var].values,
                ramp=_MAP_RAMPS[var], unit=_MAP_UNITS[var],
                title=f"{name}, {when}", width=250,
            ))
        out["map_tomorrow"] = "".join(tiles)

    week = []
    for day in grid.days:
        week.append(_map_svg(
            cfg, grid, day.fields["temperature_2m_max"].values,
            ramp=_MAP_RAMPS["temperature_2m_max"], unit="°C",
            title=day.date.strftime("%a %d"), width=118, show_legend=False,
        ))
    out["map_week"] = "".join(week)
    out["map_facts"] = grid.facts
    return out


# --- minimal markdown (headings, bullets, bold, paragraphs) --------------------


def _markdown_to_html(md: str) -> str:
    """Small, dependency-free renderer for the briefing's constrained markdown."""
    out: list[str] = []
    in_list = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        esc = html.escape(line, quote=False)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
        if esc.startswith("## "):
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<h3>{esc[3:]}</h3>")
        elif esc.startswith("# "):
            if in_list:
                out.append("</ul>")
                in_list = False
            # Demoted: the page's h1 is the status bar; the briefing nests under it.
            out.append(f"<h2>{esc[2:]}</h2>")
        elif esc.lstrip().startswith(("- ", "* ")):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{esc.lstrip()[2:]}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{esc}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


_SOURCE_NAMES = {
    "nino34": "Niño 3.4",
    "u60n_10hpa": "stratosphere",
    "ensemble": "ensemble",
    "deterministic": "deterministic models",
    "anomaly": "climatology",
    "blitzortung": "lightning network",
}


def _human_when(a: Alert) -> str:
    if a.valid_from == a.valid_to:
        return a.valid_from.strftime("%a %d %b")
    return f"{a.valid_from.strftime('%a %d')} – {a.valid_to.strftime('%a %d %b')}"


def _alert_view(a: Alert) -> dict[str, str]:
    sources = ", ".join(
        _SOURCE_NAMES.get(src, _MODEL_SHORT.get(src, src)) for src in a.sources
    )
    return {
        "severity": str(a.severity),
        "title": html.escape(a.title, quote=False),
        "detail": html.escape(a.detail, quote=False),
        "when": _human_when(a),
        "confidence_pct": f"{a.confidence:.0%}",
        "sources": html.escape(sources, quote=False),
    }


def _briefing_stamp(digest: dict[str, Any], briefing_at: datetime | None) -> str | None:
    """'briefing 06:30' when the briefing is older than the instruments; None when
    they were written together (the run stamp already says it)."""
    if briefing_at is None:
        return "no briefing yet"
    run_at = datetime.fromisoformat(digest["meta"]["run_at"])
    if abs((run_at - briefing_at).total_seconds()) < 120:
        return None
    same_day = run_at.date() == briefing_at.date()
    fmt = "%H:%M" if same_day else "%d %b %H:%M"
    return f"briefing {briefing_at.strftime(fmt)} UTC"


def _extract_headline(briefing_md: str) -> str | None:
    """The first paragraph under '## Headline' — the page's lede."""
    lines = briefing_md.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines)
                     if ln.strip().lower().startswith("## headline"))
    except StopIteration:
        return None
    para: list[str] = []
    for ln in lines[start + 1:]:
        if ln.strip().startswith("#"):
            break
        if not ln.strip():
            if para:
                break
            continue
        para.append(ln.strip())
    if not para:
        return None
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", " ".join(para))
    return html.escape(text, quote=False)

