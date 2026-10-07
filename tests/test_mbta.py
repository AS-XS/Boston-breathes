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
