from datetime import date

import polars as pl
import pytest

from boston_breathes import calendars as cal

HEADER = "unitid,institution,academic_year,event,date,weekday,source_url,note"
SRC = "https://example.edu/calendar.pdf"


def write(tmp_path, rows: list[str]):
    path = tmp_path / "calendar.csv"
    path.write_text("\n".join([HEADER, *rows]) + "\n")
    return path


def full_year(unitid: str, year: str, dates: dict[str, str]) -> list[str]:
    return [f"{unitid},Test U,{year},{event},{d},,{SRC}," for event, d in dates.items()]


YEAR_2018 = {
    "fall_classes_start": "2018-09-04",  # Labor Day 2018-09-03 + 1
    "fall_exams_end": "2018-12-21",
    "spring_classes_start": "2019-01-22",  # MLK Day 2019-01-21 + 1
    "spring_break_start": "2019-03-09",
    "spring_break_end": "2019-03-17",
    "spring_exams_end": "2019-05-11",
    "commencement": "2019-05-19",
}


def test_anchor_holidays():
    assert cal.labor_day(2018) == date(2018, 9, 3)
    assert cal.labor_day(2016) == date(2016, 9, 5)
    assert cal.mlk_day(2019) == date(2019, 1, 21)
    assert cal.mlk_day(2017) == date(2017, 1, 16)


def test_academic_year_labels():
    assert cal.academic_year_label(2019) == "2019-20"
    assert cal.academic_year_label(2099) == "2099-00"
    assert cal.start_year_of("2019-20") == 2019
    with pytest.raises(ValueError):
        cal.start_year_of("2019-21")


def test_load_manual_accepts_valid_rows(tmp_path):
    rows = full_year("1", "2018-19", YEAR_2018)
    rows[0] = rows[0].replace(",,https", ",Tuesday,https")
    df = cal.load_manual(write(tmp_path, rows))
    assert len(df) == 7
    assert set(df["status"]) == {"verified"}


@pytest.mark.parametrize("row, message", [
    (f"1,Test U,2018-19,fall_classes_start,2018-09-04,Monday,{SRC},", "is a Tuesday"),
    (f"1,Test U,2018-19,move_in,2018-09-01,,{SRC},", "unknown event"),
    ("1,Test U,2018-19,fall_classes_start,2018-09-04,,,", "missing source"),
    (f"1,Test U,2018-19,spring_break_start,none,,{SRC},", "needs a note"),
    (f"1,Test U,2018-19,spring_classes_start,2018-01-22,,{SRC},", "outside the 2018-19 spring term"),
    (f"1,Test U,2018-20,fall_classes_start,2018-09-04,,{SRC},", "Bad academic year"),
])
def test_load_manual_rejects_bad_rows(tmp_path, row, message):
    with pytest.raises(ValueError, match=message):
        cal.load_manual(write(tmp_path, [row]))


def test_load_manual_rejects_duplicates_and_reversed_events(tmp_path):
    dup = f"1,Test U,2018-19,fall_classes_start,2018-09-05,,{SRC},"
    with pytest.raises(ValueError, match="duplicate event"):
        cal.load_manual(write(tmp_path, [*full_year("1", "2018-19", YEAR_2018), dup]))
    reversed_rows = full_year("1", "2018-19", YEAR_2018 | {"spring_break_end": "2019-03-08"})
    with pytest.raises(ValueError, match="spring_break_end .* is before spring_break_start"):
        cal.load_manual(write(tmp_path, reversed_rows))


def test_complete_calendar_estimates_from_offsets(tmp_path):
    rows = full_year("1", "2018-19", YEAR_2018)
    rows += [f"1,Test U,2019-20,spring_break_start,none,,{SRC},cancelled"]
    calendar = cal.complete_calendar(cal.load_manual(write(tmp_path, rows)))
    get = lambda year, event: calendar.filter(  # noqa: E731
        (pl.col("academic_year") == year) & (pl.col("event") == event)
    ).row(0, named=True)

    assert get("2018-19", "fall_classes_start")["status"] == "verified"
    # 2019: Labor Day is 2019-09-02, so one day later is a Tuesday again.
    est = get("2019-20", "fall_classes_start")
    assert (est["date"], est["status"]) == (date(2019, 9, 3), "estimated")
    # Spring 2020: MLK Day 2020-01-20; the break starts 47 days later, a Saturday.
    assert get("2020-21", "spring_break_start")["date"] == date(2021, 3, 6)
    assert get("2019-20", "spring_break_start")["status"] == "none"
    years = calendar["academic_year"].unique().sort().to_list()
    assert years[0] == cal.academic_year_label(cal.FIRST_ACADEMIC_YEAR)
    assert years[-1] == cal.academic_year_label(cal.LAST_ACADEMIC_YEAR)


def test_events_without_verified_dates_stay_missing(tmp_path):
    rows = [f"1,Test U,2018-19,fall_classes_start,2018-09-04,,{SRC},"]
    calendar = cal.complete_calendar(cal.load_manual(write(tmp_path, rows)))
    assert set(calendar.filter(pl.col("event") == "commencement")["status"]) == {"missing"}


def test_leave_one_out_errors_are_zero_for_a_regular_pattern(tmp_path):
    rows = full_year("1", "2018-19", YEAR_2018)
    rows += [f"1,Test U,2019-20,fall_classes_start,2019-09-03,,{SRC},"]
    errors = cal.leave_one_out_errors(cal.load_manual(write(tmp_path, rows)))
    assert errors["error_days"].to_list() == [0, 0]


def test_widen_to_weekend():
    assert cal.widen_to_weekend(date(2026, 3, 23), date(2026, 3, 27)) == (date(2026, 3, 21), date(2026, 3, 29))
    assert cal.widen_to_weekend(date(2019, 3, 9), date(2019, 3, 17)) == (date(2019, 3, 9), date(2019, 3, 17))


def weekly_for(calendar: pl.DataFrame) -> pl.DataFrame:
    return cal.weekly_session(cal.session_days(calendar))


def test_weekly_session_days(tmp_path):
    calendar = cal.complete_calendar(cal.load_manual(write(tmp_path, full_year("1", "2018-19", YEAR_2018))))
    w = weekly_for(calendar)
    days = dict(zip(w["week_start"].to_list(), w["days_in_session"].to_list()))
    assert days[date(2018, 9, 3)] == 6  # classes start Tuesday
    assert days[date(2018, 11, 19)] == 2  # Thanksgiving break from Wednesday
    assert days[date(2018, 12, 17)] == 5  # exams end Friday
    assert days[date(2018, 12, 24)] == 0
    assert days[date(2019, 3, 4)] == 5  # break starts Saturday
    assert days[date(2019, 3, 11)] == 0
    assert days[date(2019, 7, 15)] == 0


def test_cancelled_break_and_unknown_break(tmp_path):
    rows = full_year("1", "2018-19", YEAR_2018)
    rows = [r for r in rows if "spring_break" not in r]
    rows += [f"1,Test U,2018-19,spring_break_start,none,,{SRC},cancelled",
             f"1,Test U,2018-19,spring_break_end,none,,{SRC},cancelled"]
    calendar = cal.complete_calendar(cal.load_manual(write(tmp_path, rows)))
    w = weekly_for(calendar.filter(pl.col("academic_year") == "2018-19"))
    assert w.filter(pl.col("week_start") == date(2019, 3, 11))["days_in_session"][0] == 7

    # A break that exists but whose dates are unknown makes those weeks unknown.
    unknown = calendar.with_columns(
        pl.when(pl.col("event").str.starts_with("spring_break")).then(pl.lit("missing")).otherwise(pl.col("status")).alias("status")
    )
    w = weekly_for(unknown.filter(pl.col("academic_year") == "2018-19"))
    assert w.filter(pl.col("week_start") == date(2019, 3, 11))["days_in_session"][0] is None
    assert w.filter(pl.col("week_start") == date(2019, 7, 15))["days_in_session"][0] == 0  # summer is known
