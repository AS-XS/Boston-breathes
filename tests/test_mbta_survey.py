import polars as pl
import pytest

from boston_breathes import mbta_survey as ms

RT = ms.RAPID_TRANSIT


def survey(rows):
    return pl.DataFrame([dict(zip(ms.FIELDS, r)) for r in rows], schema=ms.SCHEMA)


SURVEY = survey([
    ("Reporting Group", RT + "Red Line", "Park Street", "Fares", "Fare Product", "Semester Pass", 0.02, 100, 0.01),
    ("Reporting Group", RT + "Red Line", "Park Street", "Fares", "Fare Product", "Monthly Pass", 0.50, 100, 0.05),
    ("Reporting Group", RT + "Green Line", "Park Street", "Fares", "Fare Product", "Monthly Pass", 0.40, 300, 0.03),
    ("Reporting Group", RT + "Green Line", "Lechmere and Science Park/West End", "Fares", "Fare Product",
     "Semester Pass", 0.01, 50, 0.01),
    ("Reporting Group", "Bus", "1", "Fares", "Fare Product", "Semester Pass", 0.9, 10, 0.1),
    ("Rapid Transit or Bus Rapid Transit Line", RT + "Red Line", "All Red Line", "Fares", "Fare Product",
     "Semester Pass", 0.9, 10, 0.1),
])
# The real survey asks every question everywhere; give each group one answer to the other three.
OTHER = survey([("Reporting Group", RT + line, group, "x", m, c, 0.1, 10, 0.01)
                for line, group in [("Red Line", "Park Street"), ("Green Line", "Park Street"),
                                    ("Green Line", "Lechmere and Science Park/West End")]
                for (m, c) in ms.CATEGORIES if c != "Semester Pass"])
STATIONS = pl.DataFrame({"stop_id": ["p", "l", "s"], "station_name": ["Park Street", "Lechmere", "Science Park"],
                         "in_study_area": [True] * 3, "campus_zone": ["away"] * 3})


def test_student_groups_fill_unchosen_answers_with_zero():
    g = ms.student_groups(SURVEY).filter(pl.col("measure_key") == "semester_pass")
    # Bus routes and whole-line summaries are left out; Green Line Park Street had no semester-pass answer.
    assert sorted(g.select("line", "reporting_group", "share", "n").rows()) == [
        ("Green Line", "Lechmere and Science Park/West End", 0.01, 50),
        ("Green Line", "Park Street", 0.0, 300),
        ("Red Line", "Park Street", 0.02, 100),
    ]


def test_by_station_combines_lines_by_respondents_and_splits_shared_groups():
    out = ms.by_station(ms.student_groups(pl.concat([SURVEY, OTHER])), STATIONS)
    shares = dict(zip(out["station_name"], out["semester_pass_share"]))
    assert shares["Park Street"] == pytest.approx(0.005)  # (0.02 * 100 + 0 * 300) / 400
    assert shares["Lechmere"] == shares["Science Park"] == 0.01
    park = out.filter(pl.col("station_name") == "Park Street").row(0, named=True)
    assert park["survey_groups"] == "Green Line: Park Street; Red Line: Park Street"
    assert park["semester_pass_respondents"] == 400
