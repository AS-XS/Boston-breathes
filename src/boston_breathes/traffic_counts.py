"""Boston street counts of bicycles and motor vehicles.

Source: City of Boston open data, "Streets Daily Bike Counts": 24-hour
counts by the Boston Transportation Department at about 90 locations, a few
days per year (mostly June and September, and from 2022 also March and
December). Each record has bicycle and motor-vehicle totals for the day and
for the morning and evening commute windows.

The counts are too sparse for a weekly series, but repeated counts at the
same location in different seasons allow paired comparisons, for example
September (students present) against June (students away) at locations near
campuses and elsewhere. Locations are assigned campus zones with the same
rules as Bluebikes stations.

A day with zero motor vehicles is treated as "not counted" (e.g. an off-road
path), not as no traffic.

Output: data/processed/boston_street_counts.csv, one row per location and day.
"""

import io

import polars as pl

from boston_breathes import http
from boston_breathes.campus import nearest_campus
from boston_breathes.municipalities import assign, load_towns
from boston_breathes.paths import PROCESSED, RAW

DATASET_URL = (
    "https://data.boston.gov/dataset/9344a630-7a6d-4554-b741-915c78a26d19/resource/"
    "9c3c421e-5764-4d8d-8a6c-a4d5fa4a67c9/download/streets_daily_bike_counts.csv"
)
RAW_PATH = RAW / "boston" / "streets_daily_bike_counts.csv"


def download() -> str:
    resp = http.get(DATASET_URL, timeout=120)
    resp.raise_for_status()
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_bytes(resp.content)
    return resp.content.decode("utf-8-sig")


def parse(csv_text: str) -> pl.DataFrame:
    raw = pl.read_csv(io.StringIO(csv_text), infer_schema=False)
    num = lambda c: pl.col(c).cast(pl.Float64, strict=False).round(0).cast(pl.Int64)  # noqa: E731
    df = raw.select(
        pl.col("count_id"),
        pl.col("location_name").str.strip_chars(),
        # e.g. "9/27/2016 4:00:00.000"
        pl.col("date").str.split(" ").list.first().str.to_date("%m/%d/%Y", strict=False).alias("date"),
        pl.col("lat").cast(pl.Float64, strict=False),
        pl.col("long").cast(pl.Float64, strict=False).alias("lng"),
        num("day_bikes").alias("bikes"),
        num("day_veh").alias("vehicles"),
        num("am_bike").alias("bikes_am"),
        num("am_veh").alias("vehicles_am"),
        num("pm_bike").alias("bikes_pm"),
        num("pm_veh").alias("vehicles_pm"),
    ).with_columns(
        pl.when(pl.col("vehicles") > 0).then(pl.col(c)).otherwise(None).alias(c)
        for c in ["vehicles", "vehicles_am", "vehicles_pm"]
    )
    bad = df.filter(pl.col("date").is_null())
    if len(bad):
        raise ValueError(f"{len(bad)} counts have unreadable dates")
    return (
        df.unique(["count_id", "date"], keep="first")
        .with_columns(pl.col("date").dt.strftime("%A").alias("weekday"))
        .sort("date", "count_id")
    )


def main() -> None:
    counts = parse(download())
    campuses = pl.read_csv(PROCESSED / "campus_points.csv", schema_overrides={"unitid": pl.Utf8})
    tagged = nearest_campus(assign(counts, load_towns()), campuses).select(
        "count_id", "location_name", "date", "weekday", "lat", "lng", "municipality",
        "campus_zone", "campus_institution", "campus_distance_m",
        "bikes", "vehicles", "bikes_am", "vehicles_am", "bikes_pm", "vehicles_pm",
    )
    PROCESSED.mkdir(parents=True, exist_ok=True)
    tagged.write_csv(PROCESSED / "boston_street_counts.csv")
    print(f"Street counts: {len(tagged):,} location-days, {tagged['count_id'].n_unique()} locations, "
          f"{tagged['date'].min()} to {tagged['date'].max()}")
    print(tagged.group_by("campus_zone").agg(pl.col("count_id").n_unique().alias("locations")).sort("campus_zone"))


if __name__ == "__main__":
    main()
