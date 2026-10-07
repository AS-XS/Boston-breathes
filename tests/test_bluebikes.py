import io
import zipfile
from datetime import date

import polars as pl
import pytest

from boston_breathes import bluebikes as bb

LEGACY_CSV = """\
"tripduration","starttime","stoptime","start station id","start station name","start station latitude","start station longitude","end station id","end station name","end station latitude","end station longitude","bikeid","usertype","birth year","gender"
241,"2019-06-03 08:00:20.0010","2019-06-03 08:04:21.1670",81,"Chinatown T Stop",42.352409,-71.062679,48,"Post Office Square",42.355854,-71.054597,2861,"Subscriber",1988,1
600,"2019-06-03 09:00:00.0000","2019-06-03 09:10:00.0000",81,"Chinatown T Stop",42.352409,-71.062679,48,"Post Office Square",42.355854,-71.054597,2862,"Customer",1990,2
30,"2019-06-04 10:00:00.0000","2019-06-04 10:00:30.0000",48,"Post Office Square",42.355854,-71.054597,81,"Chinatown T Stop",42.352409,-71.062679,2863,"Subscriber",1970,1
300,"2019-06-09 10:00:00.0000","2019-06-09 10:05:00.0000",99,"18 Dorrance Warehouse",42.387151,-71.075,81,"Chinatown T Stop",42.352409,-71.062679,2864,"Subscriber",1970,1
"""

MODERN_CSV = """\
"ride_id","rideable_type","started_at","ended_at","start_station_name","start_station_id","end_station_name","end_station_id","start_lat","start_lng","end_lat","end_lng","member_casual"
"A","electric_bike","2026-08-10 19:52:17.929","2026-08-10 20:12:27.991","Surface Rd at Summer St","A32042","Commonwealth Ave at Agganis Way","A32002",42.3529,-71.0565,42.3516,-71.1190,"member"
"B","classic_bike","2026-08-11 07:00:00","2026-08-11 07:15:00","Surface Rd at Summer St","A32042","Commonwealth Ave at Agganis Way","A32002",42.3531,-71.0567,42.3516,-71.1190,"casual"
"C","electric_bike","2026-08-11 08:00:00","2026-08-11 08:20:00","","","Commonwealth Ave at Agganis Way","A32002",42.3600,-71.0600,42.3516,-71.1190,"member"
"D","docked_bike","2026-08-12 08:00:00","2026-08-13 09:00:00","Surface Rd at Summer St","A32042","Commonwealth Ave at Agganis Way","A32002",42.3529,-71.0565,42.3516,-71.1190,"member"
"""


def read(csv: str) -> pl.DataFrame:
    return pl.read_csv(io.StringIO(csv), infer_schema=False)


def test_detect_layout():
    assert bb.detect_layout(read(LEGACY_CSV).columns) == "legacy"
    assert bb.detect_layout(read(MODERN_CSV).columns) == "modern"
    with pytest.raises(ValueError):
        bb.detect_layout(["a", "b"])


def test_standardize_legacy():
    df = bb.standardize(read(LEGACY_CSV))
    assert df.columns[: len(bb.STANDARD_COLUMNS) + 1] == ["trip_key", *bb.STANDARD_COLUMNS]
    assert df["rider_type"].to_list() == ["member", "casual", "member", "member"]
    assert set(df["bike_type"]) == {"classic"}
    assert df["started_at"].null_count() == 0
    assert df["duration_s"][0] == 241  # fractional seconds are dropped


def test_standardize_modern():
    df = bb.standardize(read(MODERN_CSV))
    assert df["bike_type"].to_list() == ["electric", "classic", "electric", "classic"]
    assert df["start_station_id"][2] is None  # dockless trip
    assert df["started_at"].null_count() == 0


def test_clean_counts_each_drop_once():
    df, qa = bb.clean(bb.standardize(read(LEGACY_CSV)))
    assert qa["rows_raw"] == 4
    assert qa["rows_too_short"] == 1
    assert qa["rows_non_public_station"] == 1
    assert qa["rows_kept"] == len(df) == 2

    df, qa = bb.clean(bb.standardize(read(MODERN_CSV)))
    assert qa["rows_too_long"] == 1
    assert qa["rows_kept"] == 3
    assert qa["rows_no_start_station"] == 1


def test_station_day_keeps_dockless_trips():
    df, _ = bb.clean(bb.standardize(read(MODERN_CSV)))
    sd = bb.station_day(df)
    assert sd["trips"].sum() == 3
    day11 = sd.filter(pl.col("date") == date(2026, 8, 11))
    assert day11["trips"].sum() == 2
    assert day11["station_id"].null_count() == 1


def test_build_weekly_counts_active_stations():
    df, _ = bb.clean(bb.standardize(read(MODERN_CSV)))
    weekly = bb.build_weekly(bb.station_day(df), date(2026, 8, 1), date(2026, 8, 31))
    assert len(weekly) == 1
    row = weekly.row(0, named=True)
    assert row["week_start"] == date(2026, 8, 10)
    assert row["trips"] == 3
    assert row["no_station_trips"] == 1
    assert row["days_with_data"] == 2
    assert row["avg_daily_active_stations"] == 1.0
    assert row["trips_per_active_station"] == 2.0
    assert row["electric_trips"] == 2
    assert row["complete_week"] is True


def test_station_id_change_is_not_double_counted():
    # Same physical station under an old ID on Friday and a new ID on Saturday.
    sd = pl.DataFrame({
        "date": [date(2023, 3, 31), date(2023, 4, 1)],
        "station_id": ["81", "A32042"],
        **{c: [1, 1] for c in ["trips", "member_trips", "casual_trips", "classic_trips", "electric_trips"]},
    })
    row = bb.build_weekly(sd, date(2023, 3, 1), date(2023, 4, 30)).row(0, named=True)
    assert row["avg_daily_active_stations"] == 1.0


def test_partial_week_is_flagged():
    df, _ = bb.clean(bb.standardize(read(MODERN_CSV)))
    weekly = bb.build_weekly(bb.station_day(df), date(2026, 8, 1), date(2026, 8, 12))
    assert weekly["complete_week"].to_list() == [False]


def test_trip_keys():
    legacy = bb.standardize(read(LEGACY_CSV))
    assert legacy["trip_key"][0] == "2019-06-03 08:00:20.0010|2861|81"
    assert legacy["trip_key"].n_unique() == len(legacy)
    assert bb.standardize(read(MODERN_CSV))["trip_key"].to_list() == ["A", "B", "C", "D"]


def test_fall_back_duration_is_corrected():
    csv = MODERN_CSV.splitlines()[0] + "\n" + (
        '"E","classic_bike","2024-11-03 01:50:00","2024-11-03 01:05:00","Surface Rd at Summer St","A32042",'
        '"Commonwealth Ave at Agganis Way","A32002",42.35,-71.05,42.35,-71.11,"member"\n'
    )
    df = bb.standardize(read(csv))
    assert df["duration_s"][0] == 15 * 60


def test_month_bounds_december():
    start, end = bb.month_bounds("202412")
    assert (start.year, start.month, end.year, end.month) == (2024, 12, 2025, 1)


def test_out_of_month_trips_are_deduplicated():
    header = MODERN_CSV.splitlines()[0]
    row = ('"{id}","classic_bike","{start}","{end}","Surface Rd at Summer St","A32042",'
           '"Commonwealth Ave at Agganis Way","A32002",42.35,-71.05,42.35,-71.11,"member"')
    may = read("\n".join([header,
        row.format(id="dup", start="2024-05-31 23:50:00", end="2024-06-01 00:10:00"),
        row.format(id="may", start="2024-05-15 12:00:00", end="2024-05-15 12:10:00"),
    ]))
    june = read("\n".join([header,
        row.format(id="dup", start="2024-05-31 23:50:00", end="2024-06-01 00:10:00"),
        row.format(id="only_in_june_file", start="2024-05-31 22:00:00", end="2024-06-01 00:05:00"),
        row.format(id="june", start="2024-06-02 09:00:00", end="2024-06-02 09:20:00"),
    ]))
    may_in, may_spill, may_edges = bb.split_month(bb.standardize(may), "202405")
    jun_in, jun_spill, jun_edges = bb.split_month(bb.standardize(june), "202406")
    assert len(may_in) == 2 and len(may_spill) == 0
    assert may_edges["trip_key"].to_list() == ["dup"]  # May 15 is not near an edge
    assert len(jun_in) == 1 and len(jun_spill) == 2

    extra = bb.unseen_spill(pl.concat([may_spill, jun_spill]), pl.concat([may_edges, jun_edges]))
    assert extra["trip_key"].to_list() == ["only_in_june_file"]


def test_read_zip_csv_skips_macos_metadata(tmp_path):
    path = tmp_path / "201906-bluebikes-tripdata.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("201906-bluebikes-tripdata.csv", LEGACY_CSV)
        zf.writestr("__MACOSX/._201906-bluebikes-tripdata.csv", "junk")
    assert len(bb.read_zip_csv(path)) == 4


def test_month_key_pattern():
    assert bb.MONTH_KEY.match("201501-hubway-tripdata.zip").group(1) == "201501"
    assert bb.MONTH_KEY.match("202511-bluebikes-tripdata.csv.zip").group(1) == "202511"
    assert bb.MONTH_KEY.match("hubway_Trips_2014_1.csv") is None


@pytest.mark.parametrize("name, expected", [
    ("18 Dorrance Warehouse", True),
    ("440 Rutherford Ave Depot", True),
    ("8D OPS 01", True),
    ("8D QC Station 02", True),
    ("MTL-ECO4-01", True),
    ("Post Office Square", False),
    ("Innovation Lab - 125 Western Ave at Batten Way", False),
])
def test_non_public_station_pattern(name, expected):
    assert bool(pl.Series([name]).str.contains(bb.NON_PUBLIC_STATION)[0]) is expected
