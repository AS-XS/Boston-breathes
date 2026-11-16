"""Bluebikes member trips by rider age, 2016-2019 (for weekend_riders.py).

Bluebikes published riders' birth years until April 2020 (bluebikes.py
counts only trips by riders aged 18-24 in its station-day files). This step
reads the trip files of 2016-2019 again and counts member trips with a known
age, and those by riders aged 18-24, 25-29 and 30 or over, by start station
and day; birth years are cleaned as in bluebikes.py (1969, the system's
default, and ages below 16 or above 90 count as unknown).

Downloads the 2016-2019 trip files again (about 230 MB) and deletes them
after counting.

Outputs:
  * data/interim/bluebikes/member_ages/YYYYMM.parquet  member trips by station, day and age band
  * data/processed/bluebikes_member_ages_weekly.csv    the same by station-week and kind of day
"""

import polars as pl

from boston_breathes import bluebikes as bb
from boston_breathes.paths import INTERIM, PROCESSED

MONTHS = ("201601", "201912")
AGE_BANDS = {"18_24": (18, 24), "25_29": (25, 29), "30_plus": (30, bb.MAX_RIDER_AGE)}
OUT_DIR = INTERIM / "bluebikes" / "member_ages"


def member_ages(trips: pl.DataFrame) -> pl.DataFrame:
    """Member trips with a known age, and those in each age band, by start station and day."""
    age = bb.rider_age()
    return (
        trips.filter((pl.col("rider_type") == "member") & pl.col("start_station_id").is_not_null())
        .with_columns(age.alias("age"), pl.col("started_at").dt.date().alias("date"))
        .filter(pl.col("age").is_not_null())
        .group_by("date", pl.col("start_station_id").alias("station_id"))
        .agg(pl.len().alias("known"), *[pl.col("age").is_between(*b).sum().alias(n) for n, b in AGE_BANDS.items()])
        .with_columns(pl.col("known", *AGE_BANDS).cast(pl.Int64))
        .sort("date", "station_id")
    )


def count_month(month: str, key: str) -> pl.DataFrame:
    """One month's counts, from the interim file if it was already made."""
    out = OUT_DIR / f"{month}.parquet"
    if out.exists():
        return pl.read_parquet(out)
    path = bb.download(key, bb.RAW_DIR / key)
    trips, _ = bb.clean(bb.standardize(bb.read_zip_csv(path)))
    start, end = bb.month_bounds(month)
    counts = member_ages(trips.filter(pl.col("started_at").is_between(start, end, closed="left")))
    out.parent.mkdir(parents=True, exist_ok=True)
    counts.write_parquet(out)
    path.unlink()
    return counts


def weekly(daily: pl.DataFrame) -> pl.DataFrame:
    """Counts by station-week, on weekdays and on weekends."""
    weekend = pl.col("date").dt.weekday() >= 6
    return (
        daily.with_columns((pl.col("date") - pl.duration(days=pl.col("date").dt.weekday() - 1)).alias("week_start"),
                           pl.when(weekend).then(pl.lit("weekend")).otherwise(pl.lit("weekday")).alias("day"))
        .group_by("week_start", "station_id", "day").agg(pl.col("known", *AGE_BANDS).sum())
        .sort("week_start", "station_id", "day")
    )


def load() -> pl.DataFrame:
    return pl.read_csv(PROCESSED / "bluebikes_member_ages_weekly.csv", try_parse_dates=True,
                       schema_overrides={"station_id": pl.Utf8})


def main() -> None:
    files = {m: k for m, k in bb.list_month_files().items() if MONTHS[0] <= m <= MONTHS[1]}
    counts = weekly(pl.concat([count_month(m, k) for m, k in files.items()]))
    counts.write_csv(PROCESSED / "bluebikes_member_ages_weekly.csv")
    print(f"{len(counts)} station-weeks and kinds of day, {counts['week_start'].min()} to {counts['week_start'].max()}; "
          f"{counts['known'].sum():,} member trips with a known age")


if __name__ == "__main__":
    main()
