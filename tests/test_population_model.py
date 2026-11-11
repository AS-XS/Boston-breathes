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
    rng = np.random.default_rng(1)
    weeks = [date(2022, 1, 3) + timedelta(weeks=i) for i in range(n)]
    summer = np.array([1.0 if w.month in (6, 7) else 0.0 for w in weeks])
    breaks = np.array([0.5 if w.month in (1, 3) else 0.0 for w in weeks])
    out = summer + breaks
    return pl.DataFrame({
        "week_start": weeks, "out": out, "summer": summer, "breaks": breaks, "min_share": 0.3 * out,
        "max_share": 0.85 * out,
        "weekday": 0.5 + k * out + rng.normal(0, noise, n),
        "weekend": -0.2 + k * (summer_share * summer + break_share * breaks) + rng.normal(0, noise, n),
    })


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


def test_estimate_gives_a_band_around_the_estimate_and_none_in_covid_weeks():
    weeks = [date(2023, 7, 3), date(2023, 3, 13), date(2020, 4, 6)]
    inputs = pl.DataFrame({
        "week_start": weeks, "covid_period": ["normal", "normal", "lockdown"], "census_population": [10_000] * 3,
        "resident_students": [1000] * 3, "presence_index": [0.0, 0.5, 0.0], "summer_break_share": [1.0, 0.0, 0.0],
        "summer": [1.0, 0.0, 1.0], "breaks": [0.0, 0.5, 0.0], "min_students_away": [300, 0, 300],
        "max_students_away": [850, 400, 850], "population_extrapolated": [False] * 3})
    beta = np.array([0.0, -0.1, -5.0, -5.0 * 0.6, -5.0 * 0.4])  # summer 0.6, breaks 0.4
    draws = np.array([[0, 0, -5.0, -5.0 * s, -5.0 * b] for s, b in [(0.5, 0.3), (0.6, 0.4), (0.7, 0.5)] * 40])
    slopes = pl.DataFrame({"week_start": [weeks[0]], "weekday": [-5.0], "weekend": [-3.6]})
    out = {r["week_start"]: r for r in pm.estimate(inputs, beta, draws, slopes).iter_rows(named=True)}
    summer, spring, covid = (out[w] for w in weeks)
    assert summer["students_away"] == 600 and spring["students_away"] == 200  # 0.6 x 1,000; 0.4 x 0.5 x 1,000
    assert summer["students_away_low"] < 600 < summer["students_away_high"] <= 850
    assert summer["population"] == 9_400 and summer["population_low"] == 10_000 - summer["students_away_high"]
    assert covid["students_away"] is None and covid["population_high"] is None
    # Read off travel: (weekend slope - c') / k = (-3.6 + 0.1) / -5 = 0.7 of resident students.
    assert summer["travel_students_away"] == 700 and spring["travel_students_away"] is None


def test_processed_outputs_cover_every_version_and_stay_inside_the_range():
    comparison = pl.read_csv(PROCESSED / "population_model_comparison.csv")
    for (measure, period), part in comparison.group_by("measure", "period"):
        assert set(part.filter(pl.col("metric") == "held_out_rmse")["version"]) == set(pm.VERSIONS)
    shares = pl.read_csv(PROCESSED / "population_model_shares.csv")
    main = shares.filter((pl.col("measure") == pm.MAIN_MEASURE) & (pl.col("period") == pm.MAIN_PERIOD)
                         & (pl.col("version") == pm.MAIN_VERSION))
    assert set(main["parameter"]) == {"k", "summer", "breaks"}
    assert ((main["low"] <= main["estimate"]) & (main["estimate"] <= main["high"])).all()
    df = pl.read_csv(PROCESSED / "population_estimate.csv", try_parse_dates=True, infer_schema_length=None)
    normal = df.filter(pl.col("covid_period") == "normal")
    assert (normal["min_students_away"] <= normal["students_away_low"]).all()
    assert (normal["students_away_low"] <= normal["students_away"]).all()
    assert (normal["students_away"] <= normal["students_away_high"]).all()
    assert (normal["students_away_high"] <= normal["max_students_away"]).all()
    assert df.filter(pl.col("covid_period") != "normal")["students_away"].is_null().all()
