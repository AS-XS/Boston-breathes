from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from boston_breathes import joint_population as jp
from boston_breathes import weekend_riders as wr
from boston_breathes.paths import PROCESSED


def test_member_trips_are_counted_by_age_band_without_casual_riders_or_the_placeholder_year():
    trips = pl.DataFrame({
        "started_at": [datetime(2018, 5, 1, 9)] * 5,
        "start_station_id": ["10"] * 5,
        "rider_type": ["member", "member", "member", "member", "casual"],
        "birth_year": [1998, 1990, 1969, 1960, 1998],  # 20, 28, placeholder, 58; a casual 20-year-old
    })
    out = wr.member_ages(trips).row(0, named=True)
    assert (out["known"], out["18_24"], out["25_29"], out["30_plus"]) == (3, 1, 1, 1)


def labelled(young: dict[tuple[str, str, str], float], trips: int = 1000) -> pl.DataFrame:
    """Station-weeks with the given share of trips by riders aged 18-24 for each (zone or university, period, day)."""
    rows = []
    for (where, period, day), share in young.items():
        zone, institution = ("away", None) if where == "away" else ("campus", where)
        rows.append({"week_start": date(2018, 10, 1) if period == "in session" else date(2018, 7, 2),
                     "station_id": where, "zone": zone, "institution": institution, "period": period, "day": day,
                     "known": trips, "18_24": round(share * trips)})
    return pl.DataFrame(rows)


def test_weekend_factor_is_the_ratio_of_the_campus_excess_on_weekends_to_weekdays():
    young = {}
    for period, scale in (("in session", 1.0), ("summer", 0.5)):
        young[("away", period, "weekday")], young[("away", period, "weekend")] = 0.10, 0.15
        young[("A", period, "weekday")], young[("A", period, "weekend")] = 0.10 + 0.20 * scale, 0.15 + 0.30 * scale
        young[("B", period, "weekday")], young[("B", period, "weekend")] = 0.05, 0.20  # a downtown campus
    df = labelled(young)
    assert wr.student_campuses(df) == ["A"]
    f = wr.factors(df, ["A"])
    assert f["weekend_factor_student_campuses"] == pytest.approx(1.5)  # 0.30 / 0.20
    assert f["weekend_factor"] > f["weekend_factor_student_campuses"]  # B's non-student shift adds to it
    # Weekdays: campus excess 0.075 in session ((0.30 + 0.05) / 2 - 0.10), 0.025 in summer.
    assert f["summer_excess_left_weekday"] == pytest.approx(1 / 3)


def test_weekend_inputs_multiplied_by_c_divide_the_fitted_shares_by_c():
    rng = np.random.default_rng(0)
    n = 150
    weeks = pl.Series([date(2022, 1, 3) + timedelta(weeks=i) for i in range(n)])
    out_, x = rng.random(n), rng.random((n, 2))
    pieces = []
    for signal, k in (("subway", -6.0), ("bikes", -0.5)):
        pieces.append({"signal": signal, "day": "weekday", "weeks": weeks, "x": out_[:, None],
                       "y": k * out_ + rng.normal(0, 1e-4, n)})
        pieces.append({"signal": signal, "day": "weekend", "weeks": weeks, "x": x,
                       "y": 1.5 * k * x @ np.array([0.6, 0.4]) + rng.normal(0, 1e-4, n)})  # weekend signals 1.5x
    assert jp.fit(pieces, ["subway", "bikes"])[:2] == pytest.approx([0.9, 0.6], abs=1e-3)
    assert wr.corrected_shares(pieces, ["subway", "bikes"], ["subway", "bikes"], 1.5) == pytest.approx([0.6, 0.4],
                                                                                                        abs=1e-3)


def test_processed_weekend_riders():
    out = pl.read_csv(PROCESSED / "weekend_riders.csv")
    factor = out.filter(pl.col("what") == "weekend_factor_student_campuses").row(0, named=True)
    assert factor["low"] > 1  # the documented finding: students are a larger share of weekend riders
    effects = out.filter(pl.col("table") == "implication")
    assert set(effects["case"]) == {"Bluebikes alone", "subway and Bluebikes, Bluebikes corrected",
                                    "subway and Bluebikes, both corrected"}
    fitted = effects.filter(pl.col("weekend_factor") == "as fitted")
    assert (fitted["share_summer"] > effects.filter(pl.col("weekend_factor") == "student-dominated campuses")[
        "share_summer"]).all()
