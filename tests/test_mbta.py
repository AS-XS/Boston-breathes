from datetime import date, timedelta

import polars as pl

from boston_breathes import mbta

OLD_LAYOUT = """\
"service_date","time_period","stop_id","station_name","route_or_line","gated_entries"
2019-06-03,"(07:00:00)","place-harsq","Harvard","Red Line",100
2019-06-03,"(07:30:00)","place-harsq","Harvard","Red Line",50.4
2019-06-03,"(07:00:00)","place-pktrm","Park Street","Red Line",30
2019-06-03,"(07:00:00)","place-pktrm","Park Street","Green Line",20
2019-06-03,"(07:00:00)","NA","Charles/MGH","Red Line",7
"""

NEW_LAYOUT = """\
"service_date","time_period","station_name","route_or_line","gated_entries","stop_id"
2024-06-03,"(07:00:00)","Charles/MGH","Red Line",5,"place-chmnl"
2024-06-03,"(07:00:00)","Union Square","Green Line",3.5,
2024-06-03,"(07:00:00)","Mattapan Line","Mattapan Line",2,
"""


def combine(*csvs: str) -> pl.DataFrame:
    df = pl.concat([mbta.daily_entries(c.encode()) for c in csvs])
    return (
        mbta.fill_stop_ids(df)
        .group_by("date", "stop_id")
        .agg(pl.col("station_name").first(), pl.col("entries").sum().round(0).cast(pl.Int64))
        .sort("date", "stop_id")
    )


def test_daily_entries_reads_either_column_order():
    old = mbta.daily_entries(OLD_LAYOUT.encode())
    new = mbta.daily_entries(NEW_LAYOUT.encode())
    assert old.columns == new.columns == ["date", "stop_id", "station_name", "entries"]
    assert old["date"][0] == date(2019, 6, 3)
    # The "NA" written for a missing stop ID becomes a real missing value.
    assert old.filter(pl.col("station_name") == "Charles/MGH")["stop_id"][0] is None


def test_lines_are_summed_and_missing_ids_filled_by_name():
    df = combine(OLD_LAYOUT, NEW_LAYOUT)
    by_id = {r["stop_id"]: r for r in df.iter_rows(named=True)}
    assert by_id["place-harsq"]["entries"] == 150  # 100 + 50.4, rounded
    assert by_id["place-pktrm"]["entries"] == 50  # Red + Green Line gates
    # Charles/MGH gets its ID from the year that has it.
    charles = df.filter(pl.col("station_name") == "Charles/MGH")
    assert set(charles["stop_id"]) == {"place-chmnl"}
    # Names never seen with an ID get a stable placeholder.
    assert "name-union-square" in by_id and "name-mattapan-line" in by_id


def test_weekly_flags_partial_weeks():
    daily = pl.DataFrame({
        "date": [date(2019, 6, 3) + timedelta(days=i) for i in range(10)],
        "stop_id": ["place-harsq"] * 10,
        "station_name": ["Harvard"] * 10,
        "entries": [10] * 10,
    })
    w = mbta.weekly(daily, ("stop_id",))
    assert w["week_start"].to_list() == [date(2019, 6, 3), date(2019, 6, 10)]
    assert w["entries"].to_list() == [70, 30]
    assert w["days_with_data"].to_list() == [7, 3]
    assert w["complete_week"].to_list() == [True, False]


def test_closures_are_low_days_of_one_station_not_of_the_whole_system():
    days = [date(2019, 1, 7) + timedelta(days=i) for i in range(18 * 7)]
    weekend_shut = {date(2019, 1, 26), date(2019, 1, 27), date(2019, 2, 9), date(2019, 4, 20)}
    rows = []
    for d in days:
        level = 1000 if d.weekday() < 5 else 400
        holiday = d == date(2019, 2, 18)  # every station is quiet
        for stop in ["a", "b", "c"]:
            shut = stop == "a" and (d in weekend_shut or date(2019, 3, 4) <= d < date(2019, 3, 30))
            entries = 3 if shut else (level // 3 if holiday else level)
            rows.append({"date": d, "stop_id": stop, "station_name": stop, "entries": entries})
    flagged = mbta.closed_days(pl.DataFrame(rows))
    closed = set(flagged.filter(pl.col("closed")).select("stop_id", "date").iter_rows())
    long_shut = {d for d in days if date(2019, 3, 4) <= d < date(2019, 3, 30)}
    # Weekend closures and a closure of almost four weeks are found; the holiday is not a closure.
    assert closed == {("a", d) for d in weekend_shut | long_shut}


def test_weekly_closed_days_count_flagged_and_missing_days():
    days = [date(2019, 6, 3) + timedelta(days=i) for i in range(7)]
    flagged = pl.DataFrame({"date": days[:6], "stop_id": ["a"] * 6, "closed": [False] * 4 + [True, False]})
    out = mbta.weekly_closed_days(flagged)
    assert out.select("week_start", "closed_days").row(0) == (date(2019, 6, 3), 2)  # Friday closed, Sunday missing


def test_placeholder_ids_matched_by_exact_name():
    daily = pl.DataFrame({
        "date": [date(2026, 6, 1)] * 3,
        "stop_id": ["name-longwood", "place-longw", "name-mattapan-line"],
        "station_name": ["Longwood", "Longwood", "Mattapan Line"],
        "entries": [3, 10, 5],
    })
    stations = pl.DataFrame({
        "stop_id": ["place-longw", "place-lngmd", "place-a", "place-b"],
        "api_name": ["Longwood", "Longwood Medical Area", "Twin", "Twin"],
    })
    out = mbta.match_placeholder_ids(daily, stations)
    by_id = dict(zip(out["stop_id"], out["entries"]))
    assert by_id == {"place-longw": 13, "name-mattapan-line": 5}


def test_cached_download_is_refreshed_only_when_a_newer_file_is_published():
    published_ms = 1_792_521_314_000  # 2026-10-20 18:35 UTC
    assert mbta.is_stale(None, published_ms)
    assert mbta.is_stale(published_ms / 1000 - 86400, published_ms)  # cached a day earlier
    assert not mbta.is_stale(published_ms / 1000 + 60, published_ms)
