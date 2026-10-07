from datetime import date, timedelta

import polars as pl
import pytest

from boston_breathes import presence

MON = [date(2019, 9, 2) + timedelta(weeks=i) for i in range(3)]  # fall 2019


def weeks(starts):
    return pl.DataFrame({"week_start": starts})


def enrollment(rows):
    cols = ["unitid", "name", "fall_year", "total", "distance_only", "in_person"]
    return pl.DataFrame([dict(zip(cols, r)) for r in rows])


def test_fall_year_follows_the_thursday():
    df = weeks([date(2019, 7, 29), date(2019, 12, 30), date(2020, 1, 6)])
    # Thursday of the week of Mon 29 Jul 2019 is 1 Aug -> fall 2019.
    assert df.select(presence.fall_year(pl.col("week_start")))["week_start"].to_list() == [2019, 2019, 2019]


def test_enrollment_is_carried_to_weeks_outside_published_falls():
    e = enrollment([("1", "A", 2016, 100, 0, 100), ("1", "A", 2017, 120, 0, 120)])
    out = presence.enrollment_by_week(weeks([date(2015, 3, 2), date(2016, 9, 5), date(2019, 9, 2)]), e)
    assert out["in_person"].to_list() == [100, 100, 120]
    assert out["enrollment_imputed"].to_list() == [True, False, True]


def test_remote_fall_uses_the_usual_online_count():
    e = enrollment([("1", "A", 2019, 100, 10, 90), ("1", "A", 2020, 100, 60, 40)])
    out = presence.enrollment_by_week(weeks([date(2019, 9, 2), date(2020, 9, 7)]), e)
    assert out["in_person"].to_list() == [90, 40]
    assert out["normal_in_person"].to_list() == [90, 90]


def test_session_share_falls_back_to_the_typical_calendar():
    enrolled = pl.DataFrame({
        "week_start": [MON[0]] * 3, "unitid": ["1", "2", "3"], "in_person": [100, 300, 50],
    })
    academic = pl.DataFrame({"week_start": [MON[0]] * 2, "unitid": ["1", "2"],
                             "days_in_session": [7, 0], "summer_break_days": [0, 7]})
    out = presence.session_share(enrolled, academic).sort("unitid")
    assert out["session_share"].to_list() == pytest.approx([1.0, 0.0, 0.25])  # 100 / (100 + 300)
    assert out["calendar_source"].to_list() == ["own calendar", "own calendar", "typical calendar"]
    assert out["summer_share"].to_list() == pytest.approx([0.0, 1.0, 0.75])  # 300 / (100 + 300)


def test_covid_weeks_are_away():
    df = pl.DataFrame({"week_start": [date(2020, 3, 9), date(2020, 3, 16), date(2020, 8, 17)],
                       "session_share": [1.0, 1.0, 1.0]})
    out = presence.apply_covid(df)
    assert out["session_share"].to_list() == [1.0, 0.0, 1.0]
    assert out["covid_away"].to_list() == [False, True, False]


def test_build_effective_population():
    e = enrollment([("1", "A", 2019, 1000, 0, 1000), ("2", "B", 2019, 1000, 0, 1000)])
    academic = pl.DataFrame({
        "week_start": MON * 2,
        "unitid": ["1"] * 3 + ["2"] * 3,
        "days_in_session": [7, 7, 0, 7, 0, 0],
        "summer_break_days": [0, 0, 7, 0, 0, 0],
    })
    residents = pl.DataFrame({
        "acs_year": [2019, 2019], "undergrad": [300, 100], "graduate": [50, 50],
    })
    population = pl.DataFrame({"week_start": MON, "study_area": [10_000] * 3, "extrapolated": [False] * 3})
    weekly, by_inst = presence.build(weeks(MON), e, academic, residents, population)
    assert weekly["presence_index"].to_list() == [1.0, 0.5, 0.0]
    assert weekly["summer_break_share"].to_list() == [0.0, 0.0, 0.5]
    # Undergraduates (400) leave when out of session; graduate students stay.
    assert weekly["student_change"].to_list() == [0, -200, -400]
    assert weekly["effective_population"].to_list() == [10_000, 9_800, 9_600]
    assert len(by_inst) == 6
