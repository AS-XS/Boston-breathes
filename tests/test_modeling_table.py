from datetime import date

import polars as pl

from boston_breathes import modeling_table as mt
from boston_breathes.paths import PROCESSED

W1, W2, W3 = date(2019, 9, 2), date(2019, 9, 9), date(2019, 9, 16)


def bike_zones(age_known: int = 10) -> pl.DataFrame:
    """Two weeks of trips at campus, near and away stations; the away zone misses a day in the second week."""
    return pl.DataFrame({
        "week_start": [W1] * 3 + [W2] * 3, "campus_zone": mt.ZONES * 2,
        "trips": [10, 20, 30, 1, 2, 3], "casual_trips": [1, 2, 3, 0, 0, 1], "electric_trips": [0] * 6,
        "age_known_trips": [age_known] * 6, "college_age_trips": [5, 2, 1, 0, 0, 0],
        "avg_daily_active_stations": [1.04, 2.0, 3.0, 1.0, 2.0, 3.0],
        "complete_week": [True] * 5 + [False],
    })


def test_bluebikes_adds_zones_and_keeps_each_one():
    out = {r["week_start"]: r for r in mt.bluebikes(bike_zones()).iter_rows(named=True)}
    week = out[W1]
    assert (week["bluebikes_trips"], week["bluebikes_trips_campus"], week["bluebikes_trips_away"]) == (60, 10, 30)
    assert week["bluebikes_stations"] == 6.0 and week["bluebikes_stations_campus"] == 1.0
    assert week["bluebikes_college_age_share"] == round(8 / 30, 4)
    # A week is complete only when every zone has all seven days.
    assert week["bluebikes_complete"] and not out[W2]["bluebikes_complete"]


def test_college_age_share_is_null_once_rider_ages_stop():
    out = mt.bluebikes(bike_zones(age_known=0))
    assert out["bluebikes_college_age_share"].is_null().all()


def test_mbta_counts_study_area_stations_with_entries():
    zones = pl.DataFrame({"week_start": [W1] * 3, "campus_zone": mt.ZONES, "entries": [5, 6, 7],
                          "complete_week": [True] * 3})
    by_station = pl.DataFrame({"week_start": [W1] * 3, "stop_id": ["a", "b", "c"], "entries": [100, 0, 50]})
    stations = pl.DataFrame({"stop_id": ["a", "b", "c"], "in_study_area": [True, True, False]})
    row = mt.mbta(zones, by_station, stations).row(0, named=True)
    # b had no entries (closed for repairs) and c is outside the study area.
    assert (row["mbta_entries"], row["mbta_entries_near"], row["mbta_stations"]) == (18, 6, 1)


def test_build_keeps_every_week_and_leaves_missing_sources_null():
    weeks = pl.DataFrame({"week_start": [W1, W2, W3], "iso_year": [2019] * 3, "iso_week": [36, 37, 38],
                          "month": [9] * 3, "n_holidays": [1, 0, 0]})
    weather = pl.DataFrame({"week_start": [W1, W2], **{c: [1.0, 2.0] for c in mt.WEATHER},
                            "complete_week": [True, True]})
    mbta_zone = bike_zones().select("week_start", "campus_zone", pl.col("trips").alias("entries"), "complete_week")
    by_station = pl.DataFrame({"week_start": [W1], "stop_id": ["a"], "entries": [1]})
    stations = pl.DataFrame({"stop_id": ["a"], "in_study_area": [True]})
    presence = pl.DataFrame({"week_start": [W1, W2, W3], **{c: [0.5] * 3 for c in mt.STUDENTS}})
    away = pl.DataFrame({"week_start": [W1, W2, W3], **{c: [1] * 3 for c in mt.POPULATION}})
    out = mt.build(weeks, weather, bike_zones(), mbta_zone, by_station, stations, presence, away)
    assert out["week_start"].to_list() == [W1, W2, W3]
    last = out.row(2, named=True)
    assert last["tmean_c"] is None and last["bluebikes_trips"] is None and last["presence_index"] == 0.5
    assert out["fall_year"].to_list() == [2019] * 3


def test_processed_table_has_one_row_per_week_and_zones_add_up():
    df = pl.read_csv(PROCESSED / "modeling_weekly.csv", try_parse_dates=True, infer_schema_length=None)
    weeks = pl.read_csv(PROCESSED / "weeks.csv", try_parse_dates=True)
    assert df["week_start"].to_list() == weeks["week_start"].to_list()
    for prefix, name in (("bluebikes", "trips"), ("mbta", "entries")):
        zones = sum(df[f"{prefix}_{name}_{z}"] for z in mt.ZONES)
        assert (zones == df[f"{prefix}_{name}"]).all()


def test_year_end_week_marks_weeks_with_a_day_from_christmas_eve_to_new_year():
    weeks = pl.DataFrame({"week_start": [date(2023, 12, 18), date(2023, 12, 25), date(2024, 1, 1),
                                         date(2024, 1, 8), date(2024, 12, 16)]})
    # Dec 18-24 has Christmas Eve, Jan 1-7 has New Year's Day; Dec 16-22 has neither.
    assert weeks.select(mt.year_end_week())["year_end_week"].to_list() == [True, True, True, False, False]
