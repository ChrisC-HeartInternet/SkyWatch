"""Inter-model disagreement scoring.

The trap this module exists to avoid: model horizons differ (GFS 16 days,
ECMWF 15, ICON/UKMO 7 — verified), so a naive range/std across models APPEARS
to collapse after day 7 purely because the panel shrank. Every figure here
carries its panel size and composition, and panel changes are surfaced
explicitly so the digest can say "2 of 4 models cover this day" instead of
"models agree".
"""

from __future__ import annotations

import statistics

from pydantic import BaseModel, ConfigDict

from skywatch.models import ModelForecast


class VariableDisagreement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_models: int
    models_present: list[str]
    values: dict[str, float]          # model -> value that day
    range: float | None = None        # max - min; None when < 2 models
    std: float | None = None
    flagged: bool = False             # range beyond the configured threshold


class DayDisagreement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variables: dict[str, VariableDisagreement]


class DisagreementResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[DayDisagreement]
    # Day indices where the panel of reporting models changed vs the previous
    # day (usually models dropping out at their horizon).
    panel_changes: list[int]


def model_disagreement(
    forecasts: list[ModelForecast],
    *,
    divergence_thresholds: dict[str, float],
) -> DisagreementResult:
    """Score cross-model divergence per day for every variable present."""
    if not forecasts:
        return DisagreementResult(days=[], panel_changes=[])

    n_days = max(len(fc.dates) for fc in forecasts)
    variables = sorted({v for fc in forecasts for v in fc.series})

    days: list[DayDisagreement] = []
    panel_changes: list[int] = []
    prev_panel: frozenset[str] | None = None

    for day in range(n_days):
        per_var: dict[str, VariableDisagreement] = {}
        day_panel: set[str] = set()
        for var in variables:
            values: dict[str, float] = {}
            for fc in forecasts:
                s = fc.series.get(var)
                if s and day < len(s.values) and (v := s.values[day]) is not None:
                    values[fc.model] = v
            day_panel.update(values)
            vs = list(values.values())
            rng = round(max(vs) - min(vs), 2) if len(vs) >= 2 else None
            threshold = divergence_thresholds.get(var)
            per_var[var] = VariableDisagreement(
                n_models=len(vs),
                models_present=sorted(values),
                values={m: round(v, 2) for m, v in values.items()},
                range=rng,
                std=round(statistics.stdev(vs), 2) if len(vs) >= 2 else None,
                flagged=bool(rng is not None and threshold is not None and rng > threshold),
            )
        days.append(DayDisagreement(variables=per_var))
        panel = frozenset(day_panel)
        if prev_panel is not None and panel != prev_panel:
            panel_changes.append(day)
        prev_panel = panel

    return DisagreementResult(days=days, panel_changes=panel_changes)
