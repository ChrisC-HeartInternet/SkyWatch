"""Cross-model panel median: one synthetic forecast series from the model panel.

Used for anomalies (a single representative series vs climatology) and for
trend persistence (one comparable number per target date per run).
"""

from __future__ import annotations

import statistics

from skywatch.models import DailySeries, ModelForecast

PANEL_MODEL_NAME = "panel_median"


def panel_median(forecasts: list[ModelForecast]) -> ModelForecast:
    if not forecasts:
        raise ValueError("no forecasts to build a panel from")
    longest = max(forecasts, key=lambda fc: len(fc.dates))
    dates = longest.dates
    variables = sorted({v for fc in forecasts for v in fc.series})

    series: dict[str, DailySeries] = {}
    for var in variables:
        unit = next(
            (fc.series[var].unit for fc in forecasts if var in fc.series and fc.series[var].unit),
            "",
        )
        values: list[float | None] = []
        for i in range(len(dates)):
            day_vals = [
                v
                for fc in forecasts
                if (s := fc.series.get(var))
                and i < len(s.values)
                and (v := s.values[i]) is not None
            ]
            values.append(round(statistics.median(day_vals), 2) if day_vals else None)
        series[var] = DailySeries(variable=var, unit=unit, values=values)
    return ModelForecast(model=PANEL_MODEL_NAME, dates=dates, series=series)
