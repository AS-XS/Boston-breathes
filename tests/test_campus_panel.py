from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from boston_breathes import campus_panel as cp


def test_demean_matches_least_squares_with_dummies():
    rng = np.random.default_rng(0)
    a, b = rng.integers(0, 5, 200), rng.integers(0, 7, 200)
    v = rng.normal(size=(200, 2))
    dummies = np.column_stack([(a == i) for i in range(5)] + [(b == j) for j in range(1, 7)]).astype(float)
    expected = v - dummies @ np.linalg.lstsq(dummies, v, rcond=None)[0]
    assert cp.demean(v, [a, b]) == pytest.approx(expected, abs=1e-8)


def test_clustered_errors_with_one_row_per_cluster_are_the_robust_errors():
    rng = np.random.default_rng(1)
    x = np.column_stack([np.ones(100), rng.normal(size=100)])
    y = x @ [1.0, 2.0] + rng.normal(size=100)
    beta, se, resid = cp.clustered_fit(y, x, np.arange(100))
    bread = np.linalg.inv(x.T @ x)
    robust = np.sqrt(np.diag(bread @ (x.T * resid ** 2) @ x @ bread) * 100 / 99)
    assert se == pytest.approx(robust)


def synthetic(effect: float = 0.2, seed: int = 0) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Four campuses with calendars that break in different weeks; a large shock every week that all share."""
    rng = np.random.default_rng(seed)
    weeks = [date(2016, 1, 4) + timedelta(weeks=i) for i in range(156)]
    rows, cal = [], []
    shocks = rng.normal(0, 0.5, len(weeks))
    for u in range(4):
        share = np.ones(len(weeks))
        for start in range(u * 3, len(weeks), 13):  # a two-week break every quarter, at a different time per campus
            share[start:start + 2] = 0.0
        for i, w in enumerate(weeks):
            cal.append({"week_start": w, "unitid": f"u{u}", "university": f"University {u}",
                        "session_share": share[i]})
            rows.append({"week_start": w, "unitid": f"u{u}", "unit": f"u{u}", "stations": 2.0,
                         "activity": float(np.exp(5 + u + shocks[i] + effect * share[i] + rng.normal(0, 0.02)))})
    return pl.DataFrame(rows), pl.DataFrame(cal)


def test_own_calendar_effect_is_recovered_and_other_calendars_give_less():
    units, cal = synthetic()
    df = cp.panel(units, cal, "Bluebikes")
    coef, se = cp.fit(df, "Bluebikes", ["session_share"])["session_share"]
    assert coef == pytest.approx(0.2, abs=0.01) and se < 0.01
    assert all(abs(b) < 0.1 for _, b in cp.placebo(df, cal, "Bluebikes"))
    wide, names = cp.by_university(df)
    by_u = cp.fit(wide, "Bluebikes", names)
    assert all(by_u[u][0] == pytest.approx(0.2, abs=0.02) for u in names)


def test_universities_with_less_than_a_year_of_weeks_are_left_out():
    units, cal = synthetic()
    cal = cal.filter((pl.col("unitid") != "u3") | (pl.col("week_start") < date(2016, 6, 1)))
    assert set(cp.panel(units, cal, "Bluebikes")["unitid"]) == {"u0", "u1", "u2"}


def test_mbta_closures_are_left_out_but_low_holiday_weeks_stay():
    weeks = [date(2019, 1, 7) + timedelta(weeks=i) for i in range(10)]
    entries = [1000] * 8 + [500, 100]  # a holiday week at half the median, and a closure
    by_station = pl.DataFrame({"week_start": weeks, "stop_id": ["s"] * 10, "entries": entries,
                               "complete_week": [True] * 10})
    stations = pl.DataFrame({"stop_id": ["s"], "in_study_area": [True], "campus_zone": ["campus"],
                             "campus_institution": ["A College"]})
    universities = pl.DataFrame({"name": ["A College"], "unitid": ["1"]})
    out = cp.mbta_units(by_station, stations, universities)
    assert out["activity"].to_list() == [1000] * 8 + [500]
