"""Grid-map reductions: medians, disagreement day, region naming, facts."""

from datetime import date

from skywatch.features.gridmap import grid_summary, region_name
from skywatch.models import GridForecast

VARS = ["temperature_2m_max", "precipitation_sum", "wind_gusts_10m_max"]


def _grid() -> GridForecast:
    # 3 points: the Midlands, NW Scotland, open Atlantic (no region)
    lats = [52.5, 57.0, 49.6]
    lons = [-1.5, -5.5, -9.0]
    dates = [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]

    def series(vals_by_point: list[list[float | None]]) -> list[list[float | None]]:
        return vals_by_point

    return GridForecast(
        lats=lats, lons=lons, dates=dates,
        values={
            "m1": {
                "temperature_2m_max": series(
                    [[10.0, 8.0, 5.0], [4.0, 2.0, 1.0], [11.0, 10.0, 9.0]]),
                "precipitation_sum": series(
                    [[0.0, 2.0, 1.0], [5.0, 9.0, 3.0], [1.0, 1.0, 1.0]]),
                "wind_gusts_10m_max": series(
                    [[20.0, 30.0, 25.0], [40.0, 55.0, 45.0], [35.0, 50.0, 44.0]]),
            },
            "m2": {
                "temperature_2m_max": series(
                    [[12.0, 9.0, None], [6.0, 3.0, None], [11.5, 10.5, None]]),
                "precipitation_sum": series(
                    [[0.2, 2.4, None], [6.0, 8.0, None], [1.2, 0.8, None]]),
                "wind_gusts_10m_max": series(
                    [[22.0, 33.0, None], [42.0, 51.0, None], [36.0, 52.0, None]]),
            },
        },
    )


def test_median_and_range() -> None:
    s = grid_summary(_grid(), variables=VARS)
    d0 = s.days[0]
    assert d0.fields["temperature_2m_max"].values[0] == 11.0   # median of 10, 12
    assert d0.tmax_range[0] == 2.0
    assert d0.fields["temperature_2m_max"].n_models_min == 2


def test_horizon_dropout_day3() -> None:
    s = grid_summary(_grid(), variables=VARS)
    d2 = s.days[2]
    # m2 has no day-3 data: median falls back to m1 alone; range undefined
    assert d2.fields["temperature_2m_max"].values[0] == 5.0
    assert d2.tmax_range[0] is None
    assert d2.fields["temperature_2m_max"].n_models_min == 1


def test_disagreement_day_argmax() -> None:
    g = _grid()
    # widen model disagreement on day 1 (index 1)
    g.values["m2"]["temperature_2m_max"][1][1] = 12.0   # NW Scotland day1: 2 vs 12
    s = grid_summary(g, variables=VARS)
    assert s.max_disagreement_day == 1


def test_region_names() -> None:
    assert region_name(52.63, -1.13) == "the Midlands"           # Leicester
    assert region_name(56.8, -5.1) == "NW Scotland"              # Fort William
    assert region_name(52.6, 1.3) == "East Anglia"               # Norwich
    assert region_name(54.6, -5.9) == "Northern Ireland"         # Belfast
    assert region_name(49.6, -9.0) is None                       # open sea


def test_facts_name_regions_not_sea() -> None:
    s = grid_summary(_grid(), variables=VARS)
    joined = " | ".join(s.facts)
    # warmest tomorrow (day 1) among REGIONAL points is median(8,9)=8.5, Midlands;
    # the warmer 10.25 Atlantic point has no region and must not be named
    assert "Warmest on Friday: 8.5 °C around the Midlands" in joined
    assert "Wettest on Friday" in joined and "NW Scotland" in joined
    assert "Largest model disagreement" in joined


def test_empty_day_everywhere() -> None:
    g = _grid()
    for m in g.values.values():
        for var in m.values():
            for pt in var:
                pt[0] = None
    s = grid_summary(g, variables=VARS)
    assert s.days[0].fields["temperature_2m_max"].values == [None, None, None]
    assert s.days[0].fields["temperature_2m_max"].n_models_min == 0
