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


def test_college_age_share_pools_zones_and_skips_weeks_without_ages():
    df = pl.DataFrame({
        "week_start": [WEEKS[0], WEEKS[0], WEEKS[1], WEEKS[2], WEEKS[2]],
        "campus_zone": ["campus", "away", "campus", "campus", "near"],
        "college_age_trips": [30, 10, 0, 50, 5],
        "age_known_trips": [100, 100, 0, 100, 50],
        "complete_week": [True, True, True, False, True],
    })
    out = ev.college_age_share(df, ["campus", "away"])
    # Week 1 has no known ages; week 2 is incomplete and its near-zone row is not asked for.
    assert out["week_start"].to_list() == [WEEKS[0]]
    assert out["share"][0] == pytest.approx(0.2)


def test_verified_weeks_drop_academic_years_with_an_estimated_date():
    weeks = [date(2019, 12, 2), date(2020, 3, 2), date(2020, 9, 7)]
    academic = pl.DataFrame({"week_start": weeks * 2, "unitid": ["1"] * 3 + ["2"] * 3, "days_in_session": [7] * 6})
    calendar = pl.DataFrame({"unitid": ["1", "1", "2"], "academic_year": ["2019-20", "2020-21", "2019-20"],
                             "event": ["spring_break_start", "fall_exams_end", "commencement"],
                             "status": ["estimated", "verified", "estimated"]})
    out = ev.verified_weeks(academic, calendar)
    # University 1 loses both weeks of 2019-20; an estimated commencement does not change the weeks in session.
    assert sorted(out.select("unitid", "week_start").rows()) == [
        ("1", weeks[2]), ("2", weeks[0]), ("2", weeks[1]), ("2", weeks[2])]


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


def test_bounds_on_students_away():
    weeks = [date(2019, 1, 7), date(2019, 7, 8)]
    away = pl.DataFrame({"week_start": weeks, "max_students_away": [0, 100], "min_students_away": [0, 40],
                         "census_population": [1000, 1000]})
    residents = pl.DataFrame({"municipality": ["Cambridge", "Boston"], "acs_year": [2019, 2019],
                              "undergrad": [80, 150], "graduate": [20, 50]})
    town_gown = pl.DataFrame({"fall_year": [2019], "students_in_dorms": [40]})
    boston_housing = pl.DataFrame({"fall_year": [2018, 2019], "on_campus": [1, 70], "off_campus_at_home": [1, 30]})
    enrollment = pl.DataFrame({"fall_year": [2019, 2019, 2020], "dorm_capacity": [30, 0, 99]})
    arrangements = pl.DataFrame({"municipality": ["Cambridge", "Boston"], "acs_year": [2019, 2019],
                                 "college_students": [100, 200], "in_university_housing": [45, 60],
                                 "with_parents": [5, 40], "elsewhere": [50, 100]})
    rows = {(r["subset"], r["metric"]): r["value"]
            for r in ev.bound_rows(away, residents, town_gown, boston_housing, enrollment, arrangements)}
    assert rows[("study area, 2019", "max_students_away")] == 100
    assert rows[("study area, 2019", "max_students_away_pct_of_census")] == 10
    assert rows[("study area, 2019", "min_students_away")] == 40
    assert rows[("study area, ACS 2019", "with_parents")] == 45
    assert rows[("Boston, ACS 2019", "in_university_housing")] == 60
    assert rows[("Cambridge, 2019-20", "min_students_away_dorm_residents")] == 40
    assert rows[("Cambridge, 2019-20", "min_share_away")] == 0.4
    assert rows[("Boston, 2019-20", "min_students_away_dorm_residents")] == 70
    assert rows[("Boston, 2019-20", "min_share_away")] == 0.35
    assert rows[("Boston, 2019-20", "max_share_away")] == 0.85  # students living with their parents stay
    assert rows[("study area, 2019-20", "housing_beds")] == 30
    assert rows[("study area, 2019-20", "housing_beds_share_of_resident_students")] == 0.1


def test_break_test_leaves_out_universities_with_mismatched_campuses(monkeypatch):
    monkeypatch.setattr(ev, "BREAK_TEST_EXCLUDED", {"1": "test"})
    by_zone = pl.DataFrame({"week_start": WEEKS, "campus_zone": ["away"] * 6,
                            "trips": [1000] * 6, "complete_week": [True] * 6})
    by_inst = pl.DataFrame({"week_start": WEEKS, "campus_unitid": ["1"] * 6,
                            "trips": [100, 100, 60, 100, 100, 100], "complete_week": [True] * 6})
    academic = pl.DataFrame({"week_start": WEEKS, "unitid": ["1"] * 6, "institution": ["U"] * 6,
                             "days_in_session": [7, 7, 0, 7, 7, 7], "summer_break_days": [0] * 6})
    presence = pl.DataFrame({"week_start": WEEKS, "covid_period": ["normal"] * 6})
    assert ev.break_dips(by_inst, by_zone, academic, presence).is_empty()


def test_within_year_corr_uses_normal_years_with_enough_weeks():
    weeks = [date(2019, 1, 7) + timedelta(weeks=i) for i in range(25)] + [date(2020, 1, 6) + timedelta(weeks=i) for i in range(5)]
    df = pl.DataFrame({"week_start": weeks, "log_ratio": [float(i % 5) for i in range(30)],
                       "idx": [float(i % 5) for i in range(30)], "covid_period": ["normal"] * 30})
    out = ev.within_year_corr(df, "idx")
    assert out["year"].to_list() == [2019]  # 2020 has only 5 weeks
    assert out["r"][0] == pytest.approx(1.0)


def test_station_summer_change_and_survey_correlation():
    session = [date(2023, 10, 2) + timedelta(weeks=i) for i in range(4)]
    summer = [date(2023, 7, 3) + timedelta(weeks=i) for i in range(4)]
    presence = pl.DataFrame({"week_start": session + summer, "presence_index": [1.0] * 4 + [0.0] * 4,
                             "summer_break_share": [0.0] * 4 + [1.0] * 4, "covid_period": ["normal"] * 8})
    entries = {"a": (100, 50), "b": (100, 90), "c": (100, 100)}  # (in session, summer)
    weekly = pl.DataFrame([{"week_start": w, "stop_id": s, "entries": e[0] if w in session else e[1],
                            "complete_week": True} for s, e in entries.items() for w in session + summer])
    change = ev.station_summer_change(weekly, presence)
    assert dict(change.sort("stop_id").rows()) == pytest.approx({"a": -0.6931, "b": -0.1054, "c": 0.0}, abs=1e-4)
    survey = pl.DataFrame({"stop_id": ["a", "b", "c"], "in_study_area": [True] * 3,
                           **{s: [0.3, 0.1, 0.0] for s in ev.SURVEY_SHARES}})
    rows = {r["metric"]: r["value"] for r in ev.survey_rows(change, survey)}
    assert rows["corr_summer_change_vs_semester_pass_share"] < -0.9
    assert rows["median_summer_change_pct"] == pytest.approx(-10.0)
