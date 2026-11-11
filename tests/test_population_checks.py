from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from boston_breathes import population_checks as pc
from boston_breathes import population_model as pm
from boston_breathes.paths import PROCESSED


def weekly_inputs(weeks: list[date]) -> pl.DataFrame:
    out = np.array([1.0 if w.month in (6, 7) else 0.5 if w.month == 3 else 0.0 for w in weeks])
    return pl.DataFrame({"week_start": weeks, "out": out, "summer": np.where(out == 1.0, 1.0, 0.0),
                         "breaks": np.where(out == 0.5, 0.5, 0.0), "thanksgiving": 0.0, "winter": 0.0,
                         "spring": np.where(out == 0.5, 0.5, 0.0), "min_share": 0.3 * out, "max_share": 0.8 * out})


def test_size_is_one_when_travel_moves_as_much_as_the_model_says_and_smaller_when_it_moves_less():
    weeks = [date(2021, 1, 4) + timedelta(weeks=i) for i in range(52)]
    inputs = weekly_inputs(weeks)
    dev = pm.deviations(inputs)
    beta = np.array([0.1, -0.2, -5.0])  # c, c', k of the chosen version
    rng = np.random.default_rng(0)
    for factor in (1.0, 0.6):
        slopes = pl.DataFrame({"week_start": weeks,
                               "weekday": 0.1 + factor * -5.0 * dev["out_dev"].to_numpy(),
                               "weekend": -0.2 + factor * -5.0 * dev["max_share_dev"].to_numpy()})
        rows = {r["day"]: r for r in pc.size(slopes, inputs, beta, rng)}
        for day in ("weekday", "weekend"):
            assert rows[day]["estimate"] == pytest.approx(factor)
            assert rows[day]["low"] <= factor <= rows[day]["high"]


def test_the_inputs_that_made_the_travel_predict_it_best():
    weeks = [date(2020, 1, 6) + timedelta(weeks=i) for i in range(104)]
    actual = weekly_inputs(weeks)
    normal = actual.with_columns(pl.col("out") * 0.2, pl.col("max_share") * 0.2)
    beta = np.array([0.0, 0.0, -5.0])
    dev = pm.deviations(actual)
    slopes = pl.DataFrame({"week_start": weeks, "weekday": -5.0 * dev["out_dev"].to_numpy(),
                           "weekend": -5.0 * dev["max_share_dev"].to_numpy()})
    rows = pc.covid_rows(slopes, {pc.ACTUAL: actual, pc.NORMAL: normal}, beta)
    err = {(r["year"], r["day"], r["inputs"]): r["value"] for r in rows}
    for year in pc.COVID_YEARS:
        for day in ("weekday", "weekend"):
            assert err[(year, day, pc.ACTUAL)] == pytest.approx(0, abs=1e-9)
            assert err[(year, day, pc.NORMAL)] > 0.1


def test_bluebikes_station_weeks_count_member_trips_at_campus_and_away_stations_outside_covid():
    days = [date(2023, 1, 2) + timedelta(days=i) for i in range(14)] + [date(2020, 4, 6)]
    station_day = pl.DataFrame([{"date": d, "station_id": s, "member_trips": 10, "trips": 12}
                                for d in days for s in ("c", "a", "n", "x")])
    stations = pl.DataFrame({"station_id": ["c", "a", "n", "x"], "in_study_area": [True, True, True, False],
                             "campus_zone": ["campus", "away", "near", "campus"]})
    inputs = pl.DataFrame({"week_start": [date(2023, 1, 2), date(2023, 1, 9), date(2020, 4, 6)],
                           "covid_period": ["normal", "normal", "lockdown"]})
    out = pc.bluebikes_station_weeks(station_day, stations, inputs, (2020, 2023)).sort("stop_id", "week_start")
    assert out["stop_id"].to_list() == ["a", "a", "c", "c"]  # near-zone and outside stations left out; COVID out
    assert out["weekday_entries"].to_list() == [50] * 4 and out["weekend_entries"].to_list() == [20] * 4
    assert out[pc.BLUEBIKES].to_list() == [0.0, 0.0, 1.0, 1.0]


def test_processed_checks():
    covid = pl.read_csv(PROCESSED / "population_checks.csv")
    err = {(r["year"], r["day"], r["inputs"]): r["value"] for r in covid.iter_rows(named=True)}
    for year in pc.COVID_YEARS:
        for day in ("weekday", "weekend"):  # the COVID-19 inputs predict travel better than a normal year's
            assert err[(year, day, pc.ACTUAL)] < err[(year, day, pc.NORMAL)] < err[(year, day, pc.NOBODY)]
    size = pl.read_csv(PROCESSED / "population_checks_size.csv")
    assert set(size["years"]) == set(pc.SIZE_YEARS) and (size["low"] <= size["high"]).all()
    bikes = pl.read_csv(PROCESSED / "population_checks_bluebikes.csv")
    assert {"validation", "long horizon", "test", "fitted shares"} <= set(bikes["evaluation"])
