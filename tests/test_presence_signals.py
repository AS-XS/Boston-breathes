from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from boston_breathes import boston_311 as b311
from boston_breathes import joint_population as jp
from boston_breathes import population_accuracy as pa
from boston_breathes import presence_signals as ps
from boston_breathes import travel_models as tm
from boston_breathes.paths import PROCESSED


def test_weather_is_split_into_weekdays_and_weekend_days():
    days = [date(2023, 6, 5) + timedelta(days=i) for i in range(7)]  # Monday to Sunday
    daily = pl.DataFrame({"date": days, "tmean_c": [20.0] * 5 + [10.0, 12.0], "prcp_mm": [0, 0, 5, 0, 0, 12, 0],
                          "snow_mm": [0] * 7, "tmax_c": [25] * 4 + [31, 15, 15]})
    out = ps.weather_by_day_type(daily).row(0, named=True)
    assert out["weekday_tmean_c"] == 20 and out["weekend_tmean_c"] == 11
    assert out["weekday_rain"] == pytest.approx(0.2) and out["weekend_rain"] == 0.5
    assert out["weekend_heavy_rain"] == 0.5 and out["weekday_hot"] == pytest.approx(0.2)


def test_calendar_marks_the_weeks_from_december_24_to_january_1():
    weeks = pl.DataFrame({"week_start": [date(2023, 12, 18), date(2023, 12, 25), date(2024, 1, 1), date(2024, 1, 8)],
                          "n_holidays": [0, 1, 1, 0]})
    assert ps.calendar_features(weeks)["year_end_week"].to_list() == [1.0, 1.0, 1.0, 0.0]


def test_requests_signal_is_the_log_ratio_near_campuses_over_away():
    days = [date(2023, 6, 5) + timedelta(days=i) for i in range(7)]
    daily = pl.DataFrame([{"date": d, "campus_zone": z, "requests": n}
                          for d in days for z, n in (("campus", 2), ("away", 8), ("near", 50))])
    out = ps.requests_signal(daily).row(0, named=True)
    assert out["requests_weekday"] == pytest.approx(np.log(0.25)) and out["requests_weekend"] == pytest.approx(np.log(0.25))


def test_311_requests_are_counted_by_day_and_campus_zone():
    campuses = pl.DataFrame({"unitid": ["1"], "institution": ["A"], "typical_in_person": [5000.0], "lat": [42.35],
                             "lng": [-71.10], "major": [True]})
    requests = pl.DataFrame({"date": [date(2023, 6, 5)] * 4, "lat": [42.3501, 42.3501, 42.358, 42.40],
                             "lng": [-71.1001, -71.1001, -71.10, -71.10], "source": ["Constituent Call"] * 4})
    out = dict(b311.daily_by_zone(requests, campuses).select("campus_zone", "requests").rows())
    assert out == {"campus": 2, "near": 1, "away": 1}


def test_each_model_family_learns_a_straight_line():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(120, 2))
    y = 2 * x[:, 0] - x[:, 1] + rng.normal(0, 0.1, 120)
    months = np.repeat(np.arange(12), 10)
    for family in tm.FAMILIES:
        pred, half = tm.fit_predict(family, x[:100], y[:100], months[:100], x[100:])
        assert tm.scores(y[100:], pred, half)["r2"] > 0.7, family


def test_splits_grow_forward_in_time():
    weeks = [date(2022, 1, 3) + timedelta(weeks=i) for i in range(52 * 5)]
    df = pl.DataFrame({"week_start": weeks})
    years = [(sorted(tr["week_start"].dt.year().unique()), te["week_start"].dt.year().unique().to_list())
             for _, tr, te in tm.splits(df)]
    assert years == [([2022], [2023]), ([2022, 2023], [2024]), ([2022, 2023, 2024], [2025]),
                     ([2022, 2023, 2024, 2025], [2026])]


def pieces(shares=(0.8, 0.6), k=(-6.0, -0.5), noise=1e-4, n=150, seed=0) -> list[dict]:
    rng = np.random.default_rng(seed)
    weeks = pl.Series([date(2022, 1, 3) + timedelta(weeks=i) for i in range(n)])
    out_ = rng.random(n)
    summer, breaks = rng.random(n), rng.random(n)
    result = []
    for signal, scale in zip(["subway", "bikes"], k):
        result.append({"signal": signal, "day": "weekday", "weeks": weeks, "x": out_[:, None],
                       "y": scale * out_ + rng.normal(0, noise, n)})
        x = np.column_stack([summer, breaks])
        result.append({"signal": signal, "day": "weekend", "weeks": weeks, "x": x,
                       "y": scale * x @ np.array(shares) + rng.normal(0, noise, n)})
    return result


def test_joint_fit_recovers_shared_shares_and_each_signals_scale():
    params = jp.fit(pieces(), ["subway", "bikes"])
    assert params == pytest.approx([0.8, 0.6, -6.0, -0.5], abs=1e-3)


def test_simulated_signals_without_noise_are_the_truth():
    p = pieces()
    rng = np.random.default_rng(1)
    tiny = [rng.normal(0, 1e-4, len(q["y"])) for q in p]  # the fit weights each signal by its noise, so some is needed
    sim = pa.simulate(p, np.array([0.8, 0.6, -6.0, -0.5]), (0.5, 0.4), tiny, np.random.default_rng(0))
    assert jp.fit(sim, pa.SIGNALS)[:2] == pytest.approx([0.5, 0.4], abs=1e-3)
    broken = pa.simulate(p, np.array([0.8, 0.6, -6.0, -0.5]), (0.5, 0.4), tiny, np.random.default_rng(0), 1.2)
    assert jp.fit(broken, pa.SIGNALS)[:2] == pytest.approx([0.6, 0.48], abs=1e-3)  # 1.2 x the truth


def test_processed_model_exploration_and_joint_shares():
    acc = pl.read_csv(PROCESSED / "travel_model_accuracy.csv")
    assert set(acc["family"]) == set(tm.FAMILIES) and set(acc["signal"]) == set(tm.SIGNALS)
    assert acc.group_by("signal", "features", "family", "evaluation", "test_year").len()["len"].max() == 3
    shares = pl.read_csv(PROCESSED / "joint_population_shares.csv")
    assert set(shares["weather_model"]) == {jp.NO_MODEL, *tm.FAMILIES}
    accuracy = pl.read_csv(PROCESSED / "population_accuracy.csv")
    assert accuracy["coverage_summer"].is_between(0, 1).all() and len(accuracy) == len(pa.TRUTHS) + 1
