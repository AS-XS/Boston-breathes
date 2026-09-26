from datetime import date

from boston_breathes.weeks import build_weeks, monday_on_or_before


def test_monday_on_or_before():
    assert monday_on_or_before(date(2015, 1, 1)) == date(2014, 12, 29)  # Thursday
    assert monday_on_or_before(date(2024, 9, 2)) == date(2024, 9, 2)  # Monday


def test_weeks_are_contiguous_mondays():
    weeks = build_weeks(date(2015, 1, 1), date(2016, 12, 31))
    starts = weeks["week_start"].to_list()
    assert all(d.weekday() == 0 for d in starts)
    assert all((b - a).days == 7 for a, b in zip(starts, starts[1:]))
    assert starts[0] == date(2014, 12, 29)
    assert starts[-1] == date(2016, 12, 26)


def test_week_spanning_new_year_uses_iso_year():
    weeks = build_weeks(date(2015, 1, 1), date(2015, 1, 31))
    first = weeks.row(0, named=True)
    assert (first["iso_year"], first["iso_week"], first["month"]) == (2015, 1, 1)


def test_massachusetts_holidays_included():
    weeks = build_weeks(date(2024, 4, 15), date(2024, 4, 15))
    row = weeks.row(0, named=True)
    assert row["n_holidays"] == 1
    assert row["holiday_names"] == "Patriots' Day"
