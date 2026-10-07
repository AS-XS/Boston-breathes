"""MBTA gated station entries, by station and week.

Source: MassDOT/MBTA open data, "MBTA Gated Station Entries - Historical":
one CSV per calendar year (2014 onward) with entries counted at the fare
gates of subway, Silver Line and light rail stations, per service date
(3:00 AM to 2:59 AM) and 30-minute period. Entries are unscaled: they miss
fare evasion, free riders and times when gates are held open, so they track
change in activity rather than total ridership.

Station locations come from the MBTA V3 API. Stations whose rows never carry
a stop ID (e.g. Longwood, gated in 2026) get the ID of the API station with
exactly the same name. Stations are assigned to a
municipality and a campus zone with the same rules as Bluebikes stations.

Outputs in data/processed/:
  * mbta_stations.csv                  each gated station, its location, town and campus zone
  * mbta_weekly_by_station.csv         weekly entries per station
  * mbta_weekly_by_campus_zone.csv     weekly entries by campus zone, study-area stations
Daily station totals are kept in data/interim/mbta/ (not committed).
"""

import io
import zipfile
from datetime import timedelta

import polars as pl
import requests

from boston_breathes.campus import nearest_campus
from boston_breathes.municipalities import UNKNOWN, assign, load_towns
from boston_breathes.paths import INTERIM, PROCESSED, RAW, STUDY_AREA
from boston_breathes.weeks import build_weeks

HISTORICAL_URL = "https://www.arcgis.com/sharing/rest/content/items/7859894afb5641ce91a2bb03599fdf5b/data"
STOPS_URL = "https://api-v3.mbta.com/stops"
RAW_PATH = RAW / "mbta" / "gated_station_entries_historical.zip"
INTERIM_DIR = INTERIM / "mbta"


def download() -> bytes:
    if not RAW_PATH.exists():
        resp = requests.get(HISTORICAL_URL, timeout=900)
        resp.raise_for_status()
        RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
        RAW_PATH.write_bytes(resp.content)
    return RAW_PATH.read_bytes()


def daily_entries(csv_bytes: bytes) -> pl.DataFrame:
    """Total entries per station and service date from one yearly CSV.

    Column order differs between years, so columns are selected by name.
    Lines at multi-line stations (e.g. Park Street) are summed in read_all,
    after missing stop IDs are filled.
    """
    raw = pl.read_csv(io.BytesIO(csv_bytes), infer_schema=False)
    return (
        raw.select(
            pl.col("service_date").str.slice(0, 10).str.to_date("%Y-%m-%d").alias("date"),
            # Some years write a missing stop ID as the text "NA".
            pl.col("stop_id").str.strip_chars().replace({"NA": None, "": None}),
            pl.col("station_name").str.strip_chars(),
            pl.col("gated_entries").cast(pl.Float64, strict=False),
        )
        .group_by("date", "stop_id", "station_name")
        .agg(pl.col("gated_entries").sum().alias("entries"))
    )


def fill_stop_ids(df: pl.DataFrame) -> pl.DataFrame:
    """Fill missing stop IDs from the same station name in other rows.

    Some years leave stop_id empty for newer stations (the Green Line
    Extension) and for the Mattapan Line, which is reported as a whole line.
    Names never seen with an ID get a placeholder ID derived from the name.
    """
    known = (
        df.filter(pl.col("stop_id").is_not_null() & (pl.col("stop_id") != ""))
        .group_by("station_name").agg(pl.col("stop_id").mode().first().alias("_id"))
    )
    return (
        df.join(known, on="station_name", how="left")
        .with_columns(
            pl.when(pl.col("stop_id").is_null() | (pl.col("stop_id") == ""))
            .then(pl.coalesce("_id", pl.lit("name-") + pl.col("station_name").str.to_lowercase().str.replace_all(r"[^a-z0-9]+", "-")))
            .otherwise(pl.col("stop_id"))
            .alias("stop_id")
        )
        .drop("_id")
    )


def read_all(zip_bytes: bytes) -> pl.DataFrame:
    frames = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in sorted(n for n in zf.namelist() if n.lower().endswith(".csv")):
            frames.append(daily_entries(zf.read(name)))
    df = fill_stop_ids(pl.concat(frames))
    # Sum lines at multi-line stations, and any overlap between yearly files.
    return (
        df.group_by("date", "stop_id")
        .agg(pl.col("station_name").first(), pl.col("entries").sum().round(0).cast(pl.Int64))
        .sort("date", "stop_id")
    )


def all_stations() -> pl.DataFrame:
    """Every parent station in the MBTA V3 API (ID and name)."""
    resp = requests.get(STOPS_URL, params={"filter[location_type]": "1"}, timeout=60)
    resp.raise_for_status()
    rows = [{"stop_id": s["id"], "api_name": s["attributes"]["name"]} for s in resp.json()["data"]]
    return pl.DataFrame(rows, schema={"stop_id": pl.Utf8, "api_name": pl.Utf8})


def match_placeholder_ids(daily: pl.DataFrame, stations: pl.DataFrame) -> pl.DataFrame:
    """Replace placeholder IDs ("name-...") with the API ID of a station with the same name.

    Only names matching exactly one API station are replaced; rows are summed
    again in case the station also appears under its real ID.
    """
    unique = (
        stations.with_columns(pl.col("api_name").str.to_lowercase().alias("_name"))
        .filter(pl.len().over("_name") == 1)
        .select("_name", pl.col("stop_id").alias("_api_id"))
    )
    return (
        daily.with_columns(pl.col("station_name").str.to_lowercase().alias("_name"))
        .join(unique, on="_name", how="left")
        .with_columns(
            pl.when(pl.col("stop_id").str.starts_with("name-") & pl.col("_api_id").is_not_null())
            .then(pl.col("_api_id")).otherwise(pl.col("stop_id")).alias("stop_id")
        )
        .group_by("date", "stop_id")
        .agg(pl.col("station_name").first(), pl.col("entries").sum())
        .sort("date", "stop_id")
    )


def station_locations(stop_ids: list[str]) -> pl.DataFrame:
    """Name and coordinates of each station from the MBTA V3 API."""
    rows = []
    for i in range(0, len(stop_ids), 50):
        resp = requests.get(STOPS_URL, params={"filter[id]": ",".join(stop_ids[i:i + 50])}, timeout=60)
        resp.raise_for_status()
        for s in resp.json()["data"]:
            a = s["attributes"]
            rows.append({"stop_id": s["id"], "api_name": a["name"], "lat": a["latitude"], "lng": a["longitude"]})
    found = pl.DataFrame(rows, schema={"stop_id": pl.Utf8, "api_name": pl.Utf8, "lat": pl.Float64, "lng": pl.Float64})
    return found


def weekly(daily: pl.DataFrame, by: tuple[str, ...]) -> pl.DataFrame:
    """Weekly entries on the shared Monday-to-Sunday timeline."""
    first, last = daily["date"].min(), daily["date"].max()
    weeks = build_weeks().select("week_start", "iso_year", "iso_week")
    week_start = (pl.col("date") - pl.duration(days=pl.col("date").dt.weekday() - 1)).alias("week_start")
    return (
        daily.with_columns(week_start)
        .group_by("week_start", *by)
        .agg(pl.col("entries").sum(), pl.col("date").n_unique().alias("days_with_data"))
        .join(weeks, on="week_start", how="inner")
        .with_columns(
            ((pl.col("week_start") >= first) & (pl.col("week_start") + timedelta(days=6) <= last)).alias("complete_week")
        )
        .select("week_start", "iso_year", "iso_week", *by, "entries", "days_with_data", "complete_week")
        .sort("week_start", *by)
    )


def main() -> None:
    daily = match_placeholder_ids(read_all(download()), all_stations())
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    daily.write_parquet(INTERIM_DIR / "station_daily.parquet")

    names = daily.group_by("stop_id").agg(
        pl.col("station_name").sort_by("date").last().alias("station_name"),
        pl.col("date").min().alias("first_date"),
        pl.col("date").max().alias("last_date"),
        pl.col("entries").sum().alias("total_entries"),
    )
    locations = station_locations(sorted(i for i in names["stop_id"] if i.startswith("place-")))
    locations = names.select("stop_id").join(locations, on="stop_id", how="left")
    missing = locations.filter(pl.col("lat").is_null())["stop_id"].to_list()
    if missing:
        print(f"  No location from the MBTA API for: {missing}")
    campuses = pl.read_csv(PROCESSED / "campus_points.csv", schema_overrides={"unitid": pl.Utf8})
    stations = (
        nearest_campus(assign(names.join(locations, on="stop_id"), load_towns()), campuses)
        .with_columns(pl.col("municipality").is_in(STUDY_AREA).alias("in_study_area"))
        .select("stop_id", "station_name", "lat", "lng", "municipality", "in_study_area", "campus_zone",
                "campus_institution", "campus_distance_m", "first_date", "last_date", "total_entries")
        .sort("stop_id")
    )
    PROCESSED.mkdir(parents=True, exist_ok=True)
    stations.write_csv(PROCESSED / "mbta_stations.csv")

    weekly(daily, ("stop_id",)).write_csv(PROCESSED / "mbta_weekly_by_station.csv")
    tagged = daily.join(stations.select("stop_id", "campus_zone", "in_study_area"), on="stop_id", how="left")
    by_zone = weekly(
        tagged.filter(pl.col("in_study_area")).with_columns(pl.col("campus_zone").fill_null(UNKNOWN)), ("campus_zone",)
    )
    by_zone.write_csv(PROCESSED / "mbta_weekly_by_campus_zone.csv")

    print(f"MBTA: {daily['date'].min()} to {daily['date'].max()}, {len(stations)} stations "
          f"({stations['in_study_area'].sum()} in the study area)")
    for zone, n in stations.filter(pl.col("in_study_area")).group_by("campus_zone").len().sort("campus_zone").iter_rows():
        print(f"  {zone:<8} {n} stations")


if __name__ == "__main__":
    main()
