from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from boston_breathes import population_model as pm
from boston_breathes.paths import PROCESSED


def test_weekly_slopes_recover_each_weeks_slope_on_the_student_share():
    rng = np.random.default_rng(0)
    weeks = [date(2023, 1, 2) + timedelta(weeks=i) for i in range(20)]
    stations = {f"s{i}": rng.random() * 0.05 for i in range(12)}
    true = {w: -5.0 * (i % 4) for i, w in enumerate(weeks)}  # the slope each week
    rows = []
    for stop, share in stations.items():
        level = rng.normal(8, 1)
        for i, w in enumerate(weeks):
            log_e = level + 0.1 * i + true[w] * share  # station level, week level, the slope
            rows.append({"week_start": w, "stop_id": stop, "year": 2023, "semester_pass_share": share,
                         "weekday_entries": np.exp(log_e), "weekend_entries": np.exp(log_e - 1 + true[w] * share)})
    out = pm.weekly_slopes(pl.DataFrame(rows), "semester_pass_share")
    # Slopes are relative to the station-year levels, so they match the true ones up to a constant.
    got = out["weekday"].to_numpy() - out["weekday"].mean()
    want = np.array([true[w] for w in weeks]) - np.mean(list(true.values()))
    assert got == pytest.approx(want, abs=1e-6)
    assert (out["weekend"] - out["weekend"].mean()).to_numpy() == pytest.approx(2 * want, abs=1e-6)


def synthetic_weeks(k: float = -6.0, summer_share: float = 0.8, break_share: float = 0.4, n: int = 156,
                    noise: float = 0.0) -> pl.DataFrame:
    """Weekly slopes as the measurement model makes them: k x (the calendar input - its mean over the year)."""
    rng = np.random.default_rng(1)
    weeks = [date(2022, 1, 3) + timedelta(weeks=i) for i in range(n)]
    summer = np.array([1.0 if w.month in (6, 7) else 0.0 for w in weeks])
    breaks = np.array([0.5 if w.month in (1, 3) else 0.0 for w in weeks])
    df = pm.deviations(pl.DataFrame({
        "week_start": weeks, "out": summer + breaks, "summer": summer, "breaks": breaks,
        "thanksgiving": 0.0, "winter": np.where([w.month == 1 for w in weeks], breaks, 0.0),
        "spring": np.where([w.month == 3 for w in weeks], breaks, 0.0),
        "min_share": 0.3 * (summer + breaks), "max_share": 0.85 * (summer + breaks)}))
    away = summer_share * df["summer_dev"].to_numpy() + break_share * df["breaks_dev"].to_numpy()
    return df.with_columns(pl.Series("weekday", k * df["out_dev"].to_numpy() + rng.normal(0, noise, n)),
                           pl.Series("weekend", k * away + rng.normal(0, noise, n)))


def test_deviations_are_taken_from_each_years_own_mean():
    df = pl.DataFrame({"week_start": [date(2022, 6, 6), date(2022, 10, 3), date(2026, 3, 2), date(2026, 7, 6)],
                       **{c: [1.0, 0.0, 0.2, 1.0] for c in pm.REGRESSORS}})
    assert pm.deviations(df)["out_dev"].to_list() == pytest.approx([0.5, -0.5, -0.4, 0.4])


def test_fit_recovers_k_and_the_shares_away():
    beta = pm.fit(synthetic_weeks(), "summer and breaks")
    assert beta[2] == pytest.approx(-6.0)
    assert pm.shares(beta) == pytest.approx([0.8, 0.4])
    # A fixed version has only k: the weekend share away is given.
    assert len(pm.fit(synthetic_weeks(), "dorm closures only")) == 3


def test_the_version_that_generated_the_data_predicts_held_out_months_best():
    df = synthetic_weeks(noise=0.3)
    rmse = {v: np.sqrt(((df["weekend"].to_numpy() - pm.held_out_weekend(df, v)) ** 2).mean()) for v in pm.VERSIONS
            if v != "each kind of break"}
    assert min(rmse, key=rmse.get) == "summer and breaks"
    assert rmse["no change"] > rmse["dorm closures only"] > rmse["summer and breaks"]


def test_forward_validation_predicts_each_year_from_the_years_before_it():
    df = synthetic_weeks(n=208)
    splits = pm.validation_splits(df.filter(pl.col("week_start").dt.year() >= 2022))
    years = [(sorted(train["week_start"].dt.year().unique()), test["week_start"].dt.year().unique().to_list())
             for _, train, test in splits]
    assert years == [([2022], [2023]), ([2022, 2023], [2024]), ([2022, 2023, 2024], [2025])]
    _, train, test = splits[0]
    pred, beta = pm.forward(train, test, "summer and breaks")
    assert pred == pytest.approx(test["weekend"].to_numpy()) and pm.shares(beta) == pytest.approx([0.8, 0.4])
    rows = pm.forward_rows(splits, "semester_pass_share")
    change = {(r["test_years"], r["version"]): r["value"] for r in rows if r["metric"] == "rmse_change_pct"}
    assert change[("2023", "no change")] == 0 and change[("2023", "summer and breaks")] < -90


def test_held_out_months_never_see_their_own_or_neighbouring_weeks():
    df = synthetic_weeks()
    spike = 60  # a huge weekend slope in one week and its neighbours
    month = df["week_start"][spike].strftime("%Y-%m")
    weekend = df["weekend"].to_numpy().copy()
    weekend[spike - 1:spike + 2] += 1000
    pred = pm.held_out_weekend(df.with_columns(pl.Series("weekend", weekend)), "one share away")
    in_month = (df["week_start"].dt.strftime("%Y-%m") == month).to_numpy()
    assert np.abs(pred[in_month]).max() < 100


def test_students_away_keeps_shares_between_0_and_1_and_inside_the_range():
    inputs = pl.DataFrame({"resident_students": [1000, 1000, 1000], "summer": [1.0, 0.0, 0.0],
                           "breaks": [0.0, 0.5, 0.0], "min_students_away": [300, 0, 0],
                           "max_students_away": [850, 400, 0]})
    out = pm.away(inputs, np.array([0.5, 1.3, 0.1]), np.array([0.4, -0.2, 0.4]))
    assert out[:, 0].tolist() == [500, 200, 0]  # 0.5 x 1,000; 0.4 x 500
    assert out[:, 1].tolist() == [850, 0, 0]  # share kept at 1, then at the most that could be away; break share at 0
    assert out[:, 2].tolist() == [300, 200, 0]  # at least the students in closed residence halls


def test_estimate_is_the_chosen_version_with_a_band_from_the_fitted_shares():
    weeks = [date(2023, 7, 3), date(2023, 3, 13), date(2020, 4, 6)]
    inputs = pl.DataFrame({
        "week_start": weeks, "covid_period": ["normal", "normal", "lockdown"], "census_population": [10_000] * 3,
        "resident_students": [1000] * 3, "presence_index": [0.0, 0.5, 0.0], "summer_break_share": [1.0, 0.0, 0.0],
        "summer": [1.0, 0.0, 1.0], "breaks": [0.0, 0.5, 0.0], "min_share": [0.3, 0.0, 0.3],
        "max_share": [0.85, 0.4, 0.85], "min_students_away": [300, 0, 300], "max_students_away": [850, 400, 850],
        "population_extrapolated": [False] * 3})
    beta = np.array([0.0, -0.1, -5.0])  # the chosen version's fit: c, c', k
    draws = np.array([[0, 0, -5.0, -5.0 * s, -5.0 * b] for s, b in [(0.5, 0.3), (0.6, 0.4), (0.7, 0.5)] * 40])
    slopes = pl.DataFrame({"week_start": weeks[:2], "max_share": [0.85, 0.4], "weekday": [-5.0, -1.0],
                           "weekend": [-1.0, 1.2]})
    out = {r["week_start"]: r for r in pm.estimate(inputs, beta, draws, slopes).iter_rows(named=True)}
    summer, spring, covid = (out[w] for w in weeks)
    # Every student not living with parents: the lower line of the range.
    assert summer["students_away"] == 850 and spring["students_away"] == 400
    assert summer["population"] == 9_150 and summer["population_low"] == 10_000 - summer["students_away_high"]
    # The band reaches down to what the fitted shares allow: 0.5 to 0.7 of 1,000 in summer.
    assert 500 <= summer["students_away_low"] < 850 == summer["students_away_high"]
    assert covid["students_away"] is None and covid["population_high"] is None
    # Read off travel: (weekend slope - c') / k + the year's mean share, (-1.0 + 0.1) / -5 + 0.625 = 0.805.
    assert summer["travel_students_away"] == 805


def test_processed_outputs_cover_every_version_and_stay_inside_the_range():
    comparison = pl.read_csv(PROCESSED / "population_model_comparison.csv")
    for (measure, period), part in comparison.group_by("measure", "period"):
        assert set(part.filter(pl.col("metric") == "held_out_rmse")["version"]) == set(pm.VERSIONS)
    shares = pl.read_csv(PROCESSED / "population_model_shares.csv")
    band = shares.filter((pl.col("measure") == pm.MAIN_MEASURE) & (pl.col("period") == pm.MAIN_PERIOD)
                         & (pl.col("version") == pm.BAND_VERSION))
    assert set(band["parameter"]) == {"k", "summer", "breaks"}
    assert ((band["low"] <= band["estimate"]) & (band["estimate"] <= band["high"])).all()
    forward = pl.read_csv(PROCESSED / "population_model_forward.csv")
    assert set(forward["evaluation"]) == {"validation", "long horizon", "test"}
    # The chosen version has the lowest error of all versions in every validation year (the documented choice).
    validation = forward.filter((pl.col("measure") == pm.MAIN_MEASURE) & (pl.col("evaluation") == "validation")
                                & (pl.col("metric") == "rmse"))
    best = validation.sort("value").group_by("test_years").first()
    assert set(best["version"]) == {pm.MAIN_VERSION} and best.height == len(pm.VALIDATION_YEARS)
    df = pl.read_csv(PROCESSED / "population_estimate.csv", try_parse_dates=True, infer_schema_length=None)
    normal = df.filter(pl.col("covid_period") == "normal")
    assert (normal["min_students_away"] <= normal["students_away_low"]).all()
    assert (normal["students_away_low"] <= normal["students_away"]).all()
    assert (normal["students_away"] <= normal["students_away_high"]).all()
    assert (normal["students_away_high"] <= normal["max_students_away"]).all()
    assert (normal["students_away"] == normal["max_students_away"]).all()  # the chosen version is the lower line
    assert df.filter(pl.col("covid_period") != "normal")["students_away"].is_null().all()
