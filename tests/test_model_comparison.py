from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from boston_breathes import model_comparison as mc


def synthetic(effect: float = 0.2, years: int = 3, seed: int = 0) -> pl.DataFrame:
    """Weekly table with the columns the models read; log campus/away Bluebikes trips rise by `effect`
    when the index goes from 0 to 1, plus weather and noise."""
    rng = np.random.default_rng(seed)
    n = 52 * years
    weeks = [date(2015, 1, 5) + timedelta(weeks=i) for i in range(n)]
    index = np.where(rng.random(n) < 0.6, 1.0, rng.random(n) * 0.5)
    temp = rng.normal(10, 8, n)
    log_ratio = effect * index + 0.01 * temp + rng.normal(0, 0.02, n)
    cols = {c: rng.integers(0, 3, n).astype(float) for c in mc.WEATHER if c not in ("tmean_c", "tmean_sq")}
    return pl.DataFrame({
        "week_start": weeks, "covid_period": ["normal"] * n, "weather_complete": [True] * n,
        "bluebikes_complete": [True] * n, "tmean_c": temp, **cols, "n_holidays": rng.integers(0, 2, n),
        "year_end_week": rng.random(n) < 0.05, "presence_index": index,
        "summer_break_share": rng.random(n) * (1 - index),
        "bluebikes_trips_campus": 1000 * np.exp(log_ratio), "bluebikes_trips_away": [1000.0] * n,
        "bluebikes_stations_campus": [50.0] * n, "bluebikes_stations_away": [100.0] * n,
    })


def results(families: dict) -> callable:
    rows = pl.DataFrame(mc.compare(synthetic(), "Bluebikes campus ratio", families)[0])
    return lambda m, s, k: rows.filter((pl.col("model") == m) & (pl.col("subset") == s)
                                       & (pl.col("metric") == k))["value"][0]


def test_index_model_recovers_a_known_effect_and_beats_the_baseline():
    get = results({mc.SMOOTH: mc.MODELS[mc.SMOOTH]})
    assert get("index", "all weeks", "presence_index_coef") == pytest.approx(0.2, abs=0.01)
    assert get("index", "all weeks", "presence_index_effect_pct") == pytest.approx((np.exp(0.2) - 1) * 100, abs=1)
    assert get("index", "all weeks", "rmse") < get("baseline", "all weeks", "rmse") / 2
    assert get("index", "all weeks", "rmse_change_pct") < -50


def test_held_out_months_never_see_their_own_or_neighbouring_weeks():
    df = mc.prepare(synthetic(effect=0.0), "Bluebikes campus ratio")
    y = df["y"].to_numpy().copy()
    # A huge value in one week: if that week or its neighbours were fitted, its month would be predicted near it.
    spike = 60
    y[spike - 1:spike + 2] += 100
    x = mc.design(df, mc.SMOOTH, [])
    pred, _ = mc.held_out_predictions(df.with_columns(pl.Series("y", y)), x)
    month = df["block"][spike]
    assert np.abs(pred[(df["block"] == month).to_numpy()]).max() < 20


def test_newey_west_without_lags_is_the_robust_standard_error():
    rng = np.random.default_rng(1)
    x = np.column_stack([np.ones(200), rng.normal(size=200)])
    resid = rng.normal(size=200)
    bread = np.linalg.inv(x.T @ x)
    robust = np.sqrt(np.diag(bread @ (x.T * resid ** 2) @ x @ bread))
    assert mc.newey_west(x, resid, lags=0) == pytest.approx(robust)


def test_covid_weeks_and_incomplete_weeks_are_left_out():
    table = synthetic().with_columns(
        pl.when(pl.int_range(pl.len()) < 10).then(pl.lit("lockdown")).otherwise(pl.col("covid_period"))
        .alias("covid_period"),
        (pl.int_range(pl.len()) != 20).alias("weather_complete"))
    assert len(mc.prepare(table, "Bluebikes campus ratio")) == len(table) - 11


def test_week_of_year_design_has_a_level_per_year_and_per_week():
    df = mc.prepare(synthetic(), "Bluebikes campus ratio")
    x = mc.design(df, mc.WEEKS, ["presence_index"])
    assert x.shape[1] == len(mc.FEATURES[mc.WEEKS]) + 1 + df["year"].n_unique() + df["week_of_year"].n_unique()
    # Thursday December 31, 2015 (day 365) joins the 52nd week, numbered 51 from 0.
    assert df.filter(pl.col("week_start") == date(2015, 12, 28))["week_of_year"].to_list() == [51]
    assert df["week_of_year"].max() == 51


def test_trees_use_a_strong_index_and_suffer_when_it_is_shuffled():
    get = results({mc.TREES: mc.MODELS[mc.TREES]})
    assert get("index", "all weeks", "rmse") < get("baseline", "all weeks", "rmse")
    assert get("index", "all weeks", "presence_index_permutation_rmse_increase_pct") > 50


def test_linear_coefficients_and_tree_importance_cover_every_named_input():
    _, coefficients, importance = mc.compare(synthetic(), "Bluebikes campus ratio",
                                             {mc.SMOOTH: ["baseline", "index"], mc.TREES: ["baseline", "index"]})
    coefs = {r["feature"]: r for r in coefficients}
    assert list(coefs) == [*mc.FEATURES[mc.SMOOTH], "presence_index"]
    # The synthetic log ratio rises by 0.2 with the index and by 0.01 per degree.
    assert coefs["presence_index"]["coef"] == pytest.approx(0.2, abs=0.01)
    assert coefs["tmean_c"]["coef"] == pytest.approx(0.01, abs=0.003)
    assert {r["feature"] for r in importance} == {*mc.FEATURES[mc.TREES], "presence_index"}
    assert max(importance, key=lambda r: r["rmse_increase_pct"])["feature"] == "presence_index"
