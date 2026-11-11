from datetime import date

import numpy as np
import polars as pl

from boston_breathes import joint_population as jp
from boston_breathes import population_estimate as pe
from boston_breathes.paths import PROCESSED


def test_estimate_is_the_chosen_version_with_a_band_from_the_refitted_shares():
    weeks = [date(2023, 7, 3), date(2023, 3, 13), date(2020, 4, 6)]
    inputs = pl.DataFrame({
        "week_start": weeks, "covid_period": ["normal", "normal", "lockdown"], "census_population": [10_000] * 3,
        "resident_students": [1000] * 3, "presence_index": [0.0, 0.5, 0.0], "summer_break_share": [1.0, 0.0, 0.0],
        "summer": [1.0, 0.0, 1.0], "breaks": [0.0, 0.5, 0.0], "min_share": [0.3, 0.0, 0.3],
        "max_share": [0.85, 0.4, 0.85], "min_students_away": [300, 0, 300], "max_students_away": [850, 400, 850],
        "population_extrapolated": [False] * 3})
    beta = np.array([0.0, -0.1, -5.0])  # the chosen version's fit: c, c', k
    shares = np.array([(0.5, 0.3), (0.6, 0.4), (0.7, 0.5)] * 40)  # refitted shares away: summer, breaks
    slopes = pl.DataFrame({"week_start": weeks[:2], "max_share": [0.85, 0.4], "weekday": [-5.0, -1.0],
                           "weekend": [-1.0, 1.2]})
    out = {r["week_start"]: r for r in pe.estimate(inputs, beta, shares, slopes).iter_rows(named=True)}
    summer, spring, covid = (out[w] for w in weeks)
    # Every student not living with parents: the lower line of the range.
    assert summer["students_away"] == 850 and spring["students_away"] == 400
    assert summer["population"] == 9_150 and summer["population_low"] == 10_000 - summer["students_away_high"]
    # The band reaches down to what the refitted shares allow: 0.5 to 0.7 of 1,000 in summer.
    assert 500 <= summer["students_away_low"] < 850 == summer["students_away_high"]
    assert covid["students_away"] is None and covid["population_high"] is None
    # Read off travel: (weekend slope - c') / k + the year's mean share, (-1.0 + 0.1) / -5 + 0.625 = 0.805.
    assert summer["travel_students_away"] == 805


def test_processed_estimate_stays_inside_the_range_with_the_band_of_the_joint_shares():
    df = pl.read_csv(PROCESSED / "population_estimate.csv", try_parse_dates=True, infer_schema_length=None)
    normal = df.filter(pl.col("covid_period") == "normal")
    assert (normal["min_students_away"] <= normal["students_away_low"]).all()
    assert (normal["students_away_low"] <= normal["students_away"]).all()
    assert (normal["students_away"] <= normal["students_away_high"]).all()
    assert (normal["students_away_high"] <= normal["max_students_away"]).all()
    assert (normal["students_away"] == normal["max_students_away"]).all()  # the chosen version is the lower line
    assert df.filter(pl.col("covid_period") != "normal")["students_away"].is_null().all()
    # In a week when every student is on summer break, the band's low end is the low end of the summer share's
    # interval (rounded to 4 decimals in the table).
    summer = normal.filter((pl.col("summer_break_share") == 1) & (pl.col("presence_index") == 0)
                           & (pl.col("students_away_low") > pl.col("min_students_away"))).row(0, named=True)
    shares = pl.read_csv(PROCESSED / "joint_population_shares.csv").filter(
        (pl.col("signals") == "subway and bikes") & (pl.col("weather_model") == jp.NO_MODEL)
        & (pl.col("parameter") == "share_summer")).row(0, named=True)
    assert abs(summer["students_away_low"] - shares["low"] * summer["resident_students"]) <= 1e-4 * summer[
        "resident_students"] + 1
