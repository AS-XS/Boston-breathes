from datetime import date

import numpy as np
import polars as pl
import pytest

from boston_breathes import students_away_estimate as sae
from boston_breathes.paths import PROCESSED


def test_slope_recovers_a_line():
    x = np.linspace(0, 1, 50)
    coef, se = sae.slope(3 - 0.8 * x, x)
    assert coef == pytest.approx(-0.8) and se == pytest.approx(0, abs=1e-9)


def test_share_away_is_weekend_over_weekday_kept_between_0_and_1():
    parts = pl.DataFrame({"period": ["summer"] * 4, "method": ["a", "a", "b", "b"],
                          "day": ["weekday", "weekend"] * 2, "coef": [-0.8, -0.4, -0.5, -0.6]})
    out = dict(sae.shares(parts).select("method", "share_away").rows())
    assert out == {"a": pytest.approx(0.5), "b": 1.0}


def test_weekly_estimate_applies_each_share_to_its_part_and_stays_in_the_range():
    weeks = [date(2019, 7, 8), date(2019, 3, 11), date(2019, 10, 7), date(2020, 4, 6)]
    away = pl.DataFrame({
        "week_start": weeks, "covid_period": ["normal", "normal", "normal", "lockdown"],
        "resident_students": [1000] * 4, "presence_index": [0.0, 0.5, 1.0, 0.0],
        "min_students_away": [300, 0, 0, 300], "max_students_away": [800, 400, 0, 800],
        "census_population": [10_000] * 4, "population_extrapolated": [False] * 4,
    })
    presence = pl.DataFrame({"week_start": weeks, "summer_break_share": [1.0, 0.0, 0.0, 0.0]})
    share = pl.DataFrame({"period": ["summer", "summer", "breaks", "breaks"], "share_away": [0.4, 1.0, 0.2, 0.6]})
    out = {r["week_start"]: r for r in sae.weekly(away, presence, share).iter_rows(named=True)}
    summer, spring, autumn, lockdown = (out[w] for w in weeks)
    # Summer: 400 to 1,000, the high end kept at the most that could be away.
    assert (summer["est_students_away_low"], summer["est_students_away_high"]) == (400, 800)
    # Half the students out of session on a break: 0.2 x 500 to 0.6 x 500.
    assert (spring["est_students_away_low"], spring["est_students_away_high"]) == (100, 300)
    assert (autumn["est_students_away_low"], autumn["est_students_away_high"]) == (0, 0)
    assert lockdown["est_students_away_low"] is None
    assert (summer["est_population_low"], summer["est_population_high"]) == (9_200, 9_600)


def test_processed_estimate_stays_inside_the_range():
    df = pl.read_csv(PROCESSED / "students_away_estimate.csv", try_parse_dates=True)
    normal = df.filter(pl.col("covid_period") == "normal")
    assert (normal["min_students_away"] <= normal["est_students_away_low"]).all()
    assert (normal["est_students_away_low"] <= normal["est_students_away_high"]).all()
    assert (normal["est_students_away_high"] <= normal["max_students_away"]).all()
    assert df.filter(pl.col("covid_period") != "normal")["est_students_away_low"].is_null().all()
    shares = pl.read_csv(PROCESSED / "students_away_shares.csv")
    assert set(shares["period"]) == {"summer", "breaks"} and shares["share_away"].is_between(0, 1).all()
