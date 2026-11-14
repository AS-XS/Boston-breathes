from datetime import date

import numpy as np
import polars as pl

from boston_breathes import joint_population as jp
from boston_breathes import population_estimate as pe
from boston_breathes import weekend_riders as wr
from boston_breathes.paths import PROCESSED


def test_estimate_comes_from_the_corrected_shares_with_a_band_from_their_refits():
    weeks = [date(2023, 7, 3), date(2023, 3, 13), date(2020, 4, 6)]
    inputs = pl.DataFrame({
        "week_start": weeks, "covid_period": ["normal", "normal", "lockdown"], "census_population": [10_000] * 3,
        "resident_students": [1000] * 3, "presence_index": [0.0, 0.5, 0.0], "summer_break_share": [1.0, 0.0, 0.0],
        "summer": [1.0, 0.0, 1.0], "breaks": [0.0, 0.5, 0.0], "min_share": [0.3, 0.0, 0.3],
        "max_share": [0.85, 0.4, 0.85], "min_students_away": [300, 0, 300], "max_students_away": [850, 400, 850],
        "population_extrapolated": [False] * 3})
    beta = np.array([0.0, -0.1, -5.0])  # the chosen version's fit on the subway: c, c', k
    shares = np.array([0.6, 0.4])  # summer, breaks, already divided by the weekend factor
    draws = np.array([(0.5, 0.3), (0.6, 0.4), (0.7, 0.5)] * 40)
    slopes = pl.DataFrame({"week_start": weeks[:2], "weekday": [-5.0, -1.0], "weekend": [-1.0, 1.2]})
    out = {r["week_start"]: r for r in pe.estimate(inputs, beta, shares, draws, slopes, 1.25).iter_rows(named=True)}
    summer, spring, covid = (out[w] for w in weeks)
    assert summer["students_away"] == 600 and spring["students_away"] == 200  # 0.6 x 1,000; 0.4 x 500
    assert summer["population"] == 9_400 and summer["population_low"] == 10_000 - summer["students_away_high"]
    # The band: what the refitted shares give, 0.5 to 0.7 of 1,000 in summer.
    assert (summer["students_away_low"], summer["students_away_high"]) == (500, 700)
    assert covid["students_away"] is None and covid["population_high"] is None
    # Read off travel: (weekend slope - c') / (k x factor) + the year's mean share,
    # (-1.0 + 0.1) / (-5 x 1.25) + (0.6 + 0.2) / 2 = 0.544.
    assert summer["travel_students_away"] == 544


def test_processed_estimate_stays_inside_the_range_with_the_corrected_joint_shares():
    df = pl.read_csv(PROCESSED / "population_estimate.csv", try_parse_dates=True, infer_schema_length=None)
    normal = df.filter(pl.col("covid_period") == "normal")
    assert (normal["min_students_away"] <= normal["students_away_low"]).all()
    assert (normal["students_away_low"] <= normal["students_away"]).all()
    assert (normal["students_away"] <= normal["students_away_high"]).all()
    assert (normal["students_away_high"] <= normal["max_students_away"]).all()
    assert df.filter(pl.col("covid_period") != "normal")["students_away"].is_null().all()
    # In a week when every student is on summer break, the estimate is the joint summer share divided by the weekend
    # factor (the share is rounded to 4 decimals in the table).
    summer = normal.filter((pl.col("summer_break_share") == 1) & (pl.col("presence_index") == 0)
                           & (pl.col("students_away") > pl.col("min_students_away"))).row(0, named=True)
    share = pl.read_csv(PROCESSED / "joint_population_shares.csv").filter(
        (pl.col("signals") == "subway and bikes") & (pl.col("weather_model") == jp.NO_MODEL)
        & (pl.col("parameter") == "share_summer"))["estimate"].item()
    expected = share / wr.weekend_factor()[0] * summer["resident_students"]
    assert abs(summer["students_away"] - expected) <= 1e-4 * summer["resident_students"] + 1
