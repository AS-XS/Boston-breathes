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
                        "session_share": share[i], "calendar": cp.VERIFIED, "calendar_key": f"u{u}"})
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
    assert set(cp.panel(units, cal, "Bluebikes", verified_only=True)["unitid"]) == {"u0", "u1", "u2"}
    assert set(cp.panel(units, cal, "Bluebikes")["unitid"]) == {"u0", "u1", "u2", "u3"}


def test_mbta_weeks_with_a_closed_day_are_left_out_but_low_holiday_weeks_stay():
    weeks = [date(2019, 1, 7) + timedelta(weeks=i) for i in range(10)]
    entries = [1000] * 8 + [500, 900]  # a holiday week at half the usual entries, and a week with a closed day
    by_station = pl.DataFrame({"week_start": weeks, "stop_id": ["s"] * 10, "entries": entries,
                               "complete_week": [True] * 10, "closed_days": [0] * 9 + [1]})
    stations = pl.DataFrame({"stop_id": ["s"], "in_study_area": [True], "campus_zone": ["campus"],
                             "campus_institution": ["A College"]})
    universities = pl.DataFrame({"name": ["A College"], "unitid": ["1"]})
    out = cp.mbta_units(by_station, stations, universities)
    assert out["activity"].to_list() == [1000] * 8 + [500]


def test_calendars_tell_verified_estimated_and_typical_weeks_apart():
    weeks = [date(2019, 4, 1), date(2019, 9, 2)]  # academic years 2018-19 and 2019-20
    presence_by_inst = pl.DataFrame({
        "week_start": weeks * 2, "unitid": ["a", "a", "b", "b"], "name": ["A", "A", "B", "B"],
        "session_share": [1.0, 0.5, 0.8, 0.8], "calendar_source": ["own calendar"] * 2 + ["typical calendar"] * 2})
    academic_weekly = pl.DataFrame({"week_start": weeks, "unitid": ["a", "a"]})
    calendar = pl.DataFrame({"unitid": ["a", "a"], "academic_year": ["2018-19", "2019-20"],
                             "event": ["fall_classes_start"] * 2, "status": ["verified", "estimated"]})
    presence = pl.DataFrame({"week_start": weeks, "covid_period": ["normal", "normal"]})
    out = cp.calendars(presence_by_inst, academic_weekly, calendar, presence).sort("unitid", "week_start")
    assert out["calendar"].to_list() == [cp.VERIFIED, cp.ESTIMATED, cp.TYPICAL, cp.TYPICAL]
    assert out["calendar_key"].to_list() == ["a", "a", cp.TYPICAL, cp.TYPICAL]


def test_other_campus_units_get_the_typical_calendar_or_are_left_out():
    units, cal = synthetic()
    tufts = units.filter(pl.col("unit") == "u0").with_columns(pl.lit("168148").alias("unit"),
                                                              pl.lit("168148").alias("unitid"))
    typical = cal.filter(pl.col("unitid") == "u1").with_columns(
        pl.lit("t").alias("unitid"), pl.lit(cp.TYPICAL).alias("calendar"), pl.lit(cp.TYPICAL).alias("calendar_key"))
    own = cal.filter(pl.col("unitid") == "u0").with_columns(pl.lit("168148").alias("unitid"),
                                                            pl.lit("168148").alias("calendar_key"))
    cal = pl.concat([cal, typical, own])
    df = cp.panel(pl.concat([units, tufts]), cal, "Bluebikes").filter(pl.col("unit") == "168148")
    expected = typical.sort("week_start")["session_share"].to_list()
    assert df.sort("week_start")["session_share"].to_list() == expected
    assert set(df["university"]) == {"Tufts University, health-sciences campus"}
    assert "168148" not in cp.panel(pl.concat([units, tufts]), cal, "Bluebikes", verified_only=True)["unit"]
