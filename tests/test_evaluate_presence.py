from datetime import date, timedelta

import polars as pl
import pytest

from boston_breathes import evaluate_presence as ev

WEEKS = [date(2019, 1, 7) + timedelta(weeks=i) for i in range(6)]


def test_campus_ratio_uses_complete_weeks_with_both_zones():
    df = pl.DataFrame({
        "week_start": [WEEKS[0], WEEKS[0], WEEKS[1], WEEKS[1], WEEKS[2]],
        "campus_zone": ["campus", "away", "campus", "away", "campus"],
        "trips": [200, 100, 50, 100, 10],
        "complete_week": [True, True, True, False, True],
    })
    out = ev.campus_ratio(df, "trips")
    assert out["week_start"].to_list() == [WEEKS[0]]
    assert out["log_ratio"][0] == pytest.approx(0.6931, abs=1e-4)


def test_break_dips_compare_with_nearby_in_session_weeks():
    by_zone = pl.DataFrame({"week_start": WEEKS, "campus_zone": ["away"] * 6,
                            "trips": [1000] * 6, "complete_week": [True] * 6})
    by_inst = pl.DataFrame({"week_start": WEEKS, "campus_unitid": ["1"] * 6,
                            "trips": [100, 100, 60, 100, 100, 100], "complete_week": [True] * 6})
    academic = pl.DataFrame({"week_start": WEEKS, "unitid": ["1"] * 6, "institution": ["U"] * 6,
                             "days_in_session": [7, 7, 0, 7, 7, 7], "summer_break_days": [0] * 6})
    presence = pl.DataFrame({"week_start": WEEKS, "covid_period": ["normal"] * 6})
    dips = ev.break_dips(by_inst, by_zone, academic, presence)
    assert dips.select("week_start", "break").rows() == [(WEEKS[2], "winter")]
    # Campus share 6% in the break week against 10% in the weeks around it.
    assert dips["change_pct"][0] == pytest.approx(-40.0)


def test_street_count_pairs_match_june_and_september_weekdays():
    counts = pl.DataFrame({
        "count_id": ["a", "a", "a", "b"],
        "campus_zone": ["campus"] * 3 + ["away"],
        "date": [date(2019, 6, 12), date(2019, 9, 18), date(2019, 9, 21), date(2019, 6, 12)],  # 9/21 is a Saturday
        "bikes": [100, 150, 999, 50],
        "vehicles": [1000, 1000, 1, 500],
    })
    pairs = ev.street_count_pairs(counts)
    assert pairs["count_id"].to_list() == ["a"]
    assert pairs["log_bikes"][0] == pytest.approx(0.4055, abs=1e-4)  # log(150 / 100)
    assert pairs["log_vehicles"][0] == 0


def test_effective_population_drop_scales_with_share_away():
    weeks = [date(2019, 1, 7), date(2019, 7, 8)]
    presence = pl.DataFrame({"week_start": weeks, "presence_index": [1.0, 0.0],
                             "census_population": [1000, 1000], "resident_undergrads": [100, 100]})
    residents = pl.DataFrame({"municipality": ["Cambridge"], "acs_year": [2019], "undergrad": [80], "graduate": [20]})
    town_gown = pl.DataFrame({"fall_year": [2019], "students_in_dorms": [40]})
    rows = {(r["subset"], r["metric"]): r["value"] for r in ev.effective_rows(presence, residents, town_gown)}
    assert rows[("2019, undergrads away 50%", "seasonal_drop")] == 50
    assert rows[("2019, undergrads away 100%", "seasonal_drop")] == 100
    assert rows[("Cambridge, fall 2019", "dorm_residents_share_of_resident_students")] == 0.4
