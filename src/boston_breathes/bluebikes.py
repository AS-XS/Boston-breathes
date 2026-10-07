"""Download, clean and summarize Bluebikes trip data.

Each monthly file is processed on its own and reduced to:
  * station-day trip counts  -> data/interim/bluebikes/station_day/YYYYMM.parquet
  * station locations        -> data/interim/bluebikes/stations/YYYYMM.parquet
  * a quality report         -> data/interim/bluebikes/qa/YYYYMM.json
  * trips starting outside the file's month, and the IDs of trips starting
    near the month's edges       -> data/interim/bluebikes/{spill,edge_keys}/
Raw downloads are deleted afterwards unless --keep-raw is given.

Since mid-2024 some monthly files also contain trips that started in the
previous month; some of these repeat trips already in that month's file and
some do not. Out-of-month trips are therefore set aside and only added back,
when the months are combined, if their trip ID is not already counted.

The monthly results are then combined into small tables in data/processed/.

Bluebikes has published two file layouts:
  * "legacy" (2015 to early 2023): tripduration, starttime, "start station id",
    usertype (Subscriber/Customer), birth year, gender, ...
  * "modern" (2023 onward): ride_id, rideable_type, started_at, member_casual, ...
Station IDs also changed format between them (e.g. "115" -> "A32042"), so
stations are only comparable across the switch by location, not by ID.
"""

import argparse
import io
import json
import re
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

import polars as pl
import requests

from boston_breathes.paths import INTERIM, PROCESSED, RAW
from boston_breathes.weeks import build_weeks

BUCKET_URL = "https://s3.amazonaws.com/hubway-data/"
MONTH_KEY = re.compile(r"^(\d{6})-(?:hubway|bluebikes)-tripdata(?:\.csv)?\.zip$")

RAW_DIR = RAW / "bluebikes"
INTERIM_DIR = INTERIM / "bluebikes"

MIN_DURATION_S = 60
MAX_DURATION_S = 24 * 3600
# Stations used for maintenance or testing rather than by riders, including
# the system vendor's (8D) operations and quality-control stations.
NON_PUBLIC_STATION = r"(?i)\b(test|warehouse|repair|depot)\b|^8D (OPS|QC)\b|^MTL-"

STANDARD_COLUMNS = [
    "started_at",
    "ended_at",
    "start_station_id",
    "start_station_name",
    "start_lat",
    "start_lng",
    "end_station_id",
    "end_station_name",
    "rider_type",
    "bike_type",
]

LEGACY_RENAME = {
    "starttime": "started_at",
    "stoptime": "ended_at",
    "start station id": "start_station_id",
    "start station name": "start_station_name",
    "start station latitude": "start_lat",
    "start station longitude": "start_lng",
    "end station id": "end_station_id",
    "end station name": "end_station_name",
    "usertype": "rider_type",
}
LEGACY_RIDER = {"Subscriber": "member", "Customer": "casual"}
MODERN_BIKE = {"classic_bike": "classic", "docked_bike": "classic", "electric_bike": "electric"}


# ---------------------------------------------------------------- download


def list_month_files() -> dict[str, str]:
    """Map YYYYMM -> S3 object key for every monthly trip file in the bucket."""
    resp = requests.get(BUCKET_URL, timeout=60)
    resp.raise_for_status()
    keys = re.findall(r"<Key>([^<]+)</Key>", resp.text)
    if "<IsTruncated>true</IsTruncated>" in resp.text:
        raise RuntimeError("Bucket listing is truncated; pagination is needed.")
    months = {}
    for key in keys:
        m = MONTH_KEY.match(key)
        if m:
            months[m.group(1)] = key
    return dict(sorted(months.items()))


def download(key: str, dest: Path, retries: int = 4) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(retries + 1):
        try:
            with requests.get(BUCKET_URL + key, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            tmp.rename(dest)
            return dest
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(2 ** (attempt + 1))
    return dest


# ------------------------------------------------------------ read & clean


def read_zip_csv(path: Path) -> pl.DataFrame:
    """Read the trip CSV inside a monthly zip, every column as text."""
    with zipfile.ZipFile(path) as zf:
        members = [
            n for n in zf.namelist()
            if n.lower().endswith(".csv") and not n.startswith("__MACOSX/")
        ]
        if len(members) != 1:
            raise ValueError(f"{path.name}: expected one CSV, found {members}")
        data = zf.read(members[0])
    return pl.read_csv(io.BytesIO(data), infer_schema=False)


def detect_layout(columns: list[str]) -> str:
    cols = set(columns)
    if {"ride_id", "started_at", "member_casual"} <= cols:
        return "modern"
    if {"tripduration", "starttime", "usertype"} <= cols:
        return "legacy"
    raise ValueError(f"Unrecognized Bluebikes columns: {columns}")


def _parse_time(col: str) -> pl.Expr:
    # Timestamps come with 0, 3 or 4 fractional digits; seconds are enough.
    return pl.col(col).str.slice(0, 19).str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S", strict=False)


def standardize(raw: pl.DataFrame) -> pl.DataFrame:
    """Convert either file layout to STANDARD_COLUMNS with parsed types."""
    layout = detect_layout(raw.columns)
    if layout == "legacy":
        df = raw.rename(LEGACY_RENAME).with_columns(
            pl.col("rider_type").replace_strict(LEGACY_RIDER, default="other"),
            pl.lit("classic").alias("bike_type"),
            # Legacy files have no trip ID; start time + bike + station is unique.
            pl.concat_str(["started_at", "bikeid", "start_station_id"], separator="|").alias("trip_key"),
        )
    else:
        df = raw.with_columns(
            pl.col("member_casual").alias("rider_type"),
            pl.col("rideable_type").replace_strict(MODERN_BIKE, default="other").alias("bike_type"),
            pl.col("ride_id").alias("trip_key"),
        )

    df = df.select(["trip_key", *STANDARD_COLUMNS]).with_columns(
        _parse_time("started_at"),
        _parse_time("ended_at"),
        pl.col("start_lat").cast(pl.Float64, strict=False),
        pl.col("start_lng").cast(pl.Float64, strict=False),
        # Empty IDs/names (dockless e-bike trips) become nulls.
        *[
            pl.col(c).str.strip_chars().replace("", None).alias(c)
            for c in ["start_station_id", "start_station_name", "end_station_id", "end_station_name"]
        ],
        pl.col("rider_type").str.to_lowercase(),
    )
    # Times are local clock times, so trips spanning the November fall-back
    # (first Sunday, 2am -> 1am) look up to an hour shorter than they were.
    raw_s = (pl.col("ended_at") - pl.col("started_at")).dt.total_seconds()
    fall_back = (
        (raw_s < 0) & (raw_s >= -3600)
        & (pl.col("started_at").dt.month() == 11)
        & (pl.col("started_at").dt.weekday() == 7)
        & (pl.col("started_at").dt.day() <= 7)
    )
    df = df.with_columns(pl.when(fall_back).then(raw_s + 3600).otherwise(raw_s).alias("duration_s"))
    return df.with_columns(pl.lit(layout).alias("layout"))


def clean(df: pl.DataFrame) -> tuple[pl.DataFrame, dict]:
    """Drop unusable trips and report how many were removed for each reason.

    Reasons are checked in order, so each dropped trip is counted once.
    """
    qa = {"rows_raw": len(df)}

    bad_time = pl.col("started_at").is_null() | pl.col("ended_at").is_null() | (pl.col("duration_s") < 0)
    too_short = pl.col("duration_s") < MIN_DURATION_S
    too_long = pl.col("duration_s") > MAX_DURATION_S
    non_public = (
        pl.col("start_station_name").fill_null("").str.contains(NON_PUBLIC_STATION)
        | pl.col("end_station_name").fill_null("").str.contains(NON_PUBLIC_STATION)
    )

    for name, cond in [
        ("rows_bad_time", bad_time),
        ("rows_too_short", too_short),
        ("rows_too_long", too_long),
        ("rows_non_public_station", non_public),
    ]:
        mask = df.select(cond.fill_null(False)).to_series()
        qa[name] = int(mask.sum())
        df = df.filter(~mask)

    qa["rows_kept"] = len(df)
    qa["rows_no_start_station"] = int(df["start_station_id"].is_null().sum())
    return df, qa


# --------------------------------------------------------------- summarize


def station_day(df: pl.DataFrame) -> pl.DataFrame:
    """Trips started per station per day, split by rider and bike type.

    Trips without a start station (dockless) are kept under a null station ID
    so that daily totals stay complete.
    """
    return (
        df.with_columns(pl.col("started_at").dt.date().alias("date"))
        .group_by("date", "start_station_id")
        .agg(
            pl.len().alias("trips"),
            (pl.col("rider_type") == "member").sum().alias("member_trips"),
            (pl.col("rider_type") == "casual").sum().alias("casual_trips"),
            (pl.col("bike_type") == "classic").sum().alias("classic_trips"),
            (pl.col("bike_type") == "electric").sum().alias("electric_trips"),
        )
        .rename({"start_station_id": "station_id"})
        .with_columns(pl.col(c).cast(pl.Int64) for c in [
            "trips", "member_trips", "casual_trips", "classic_trips", "electric_trips"
        ])
        .sort("date", "station_id")
    )


def stations(df: pl.DataFrame) -> pl.DataFrame:
    """Location and activity span of each start station seen in the month."""
    return (
        df.filter(pl.col("start_station_id").is_not_null())
        .group_by("start_station_id")
        .agg(
            pl.col("start_station_name").mode().first().alias("station_name"),
            pl.col("start_lat").median().alias("lat"),
            pl.col("start_lng").median().alias("lng"),
            pl.len().alias("trips"),
            pl.col("started_at").min().dt.date().alias("first_date"),
            pl.col("started_at").max().dt.date().alias("last_date"),
        )
        .rename({"start_station_id": "station_id"})
        .sort("station_id")
    )


# ---------------------------------------------------------- month edges

SPILL_COLUMNS = ["trip_key", "started_at", "start_station_id", "rider_type", "bike_type"]
EDGE_HOURS = 48


def month_bounds(month: str) -> tuple[datetime, datetime]:
    start = datetime(int(month[:4]), int(month[4:]), 1)
    end = datetime(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start, end


def split_month(df: pl.DataFrame, month: str) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Split trips into (started in `month`, started outside it, edge trip keys).

    Edge keys identify trips starting within EDGE_HOURS of the month's
    boundaries; they are what neighbouring files' out-of-month trips can repeat.
    """
    start, end = month_bounds(month)
    started = pl.col("started_at")
    inside = (started >= start) & (started < end)
    in_month = df.filter(inside)
    spill = df.filter(~inside)
    near_edge = (started < start + pl.duration(hours=EDGE_HOURS)) | (started >= end - pl.duration(hours=EDGE_HOURS))
    edge_keys = in_month.filter(near_edge).select("trip_key")
    return in_month, spill, edge_keys


def unseen_spill(spill: pl.DataFrame, edge_keys: pl.DataFrame) -> pl.DataFrame:
    """Out-of-month trips that no monthly file counts as its own."""
    return spill.unique("trip_key").join(edge_keys, on="trip_key", how="anti")


# ------------------------------------------------------------------ steps


def process_month(month: str, key: str, keep_raw: bool = False) -> dict:
    """Process one monthly file into interim outputs; skip if already done."""
    qa_path = INTERIM_DIR / "qa" / f"{month}.json"
    if qa_path.exists():
        return json.loads(qa_path.read_text())

    zip_path = download(key, RAW_DIR / key)
    df = standardize(read_zip_csv(zip_path))
    layout = df["layout"][0]
    df, qa = clean(df)
    first_start, last_start = df["started_at"].min(), df["started_at"].max()

    in_month, spill, edge_keys = split_month(df, month)
    outputs = {
        "station_day": station_day(in_month),
        "stations": stations(in_month),
        "spill": spill.select(SPILL_COLUMNS),
        "edge_keys": edge_keys,
    }
    for name, table in outputs.items():
        out = INTERIM_DIR / name / f"{month}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        table.write_parquet(out)

    qa = {
        "month": month,
        "layout": layout,
        **qa,
        "rows_out_of_month": len(spill),
        "first_start": str(first_start),
        "last_start": str(last_start),
        "n_stations": int(in_month["start_station_id"].n_unique()),
    }
    qa_path.parent.mkdir(parents=True, exist_ok=True)
    qa_path.write_text(json.dumps(qa, indent=2))

    if not keep_raw:
        zip_path.unlink()
    return qa


def build_weekly(
    station_day_all: pl.DataFrame,
    coverage_start: date,
    coverage_end: date,
    by: tuple[str, ...] = (),
) -> pl.DataFrame:
    """Weekly Bluebikes activity on the shared Monday-to-Sunday timeline.

    `by` adds grouping columns present in `station_day_all` (e.g. municipality).

    Network size is the average number of stations with at least one trip per
    day. Counting distinct station IDs per week would double count the week
    in which Bluebikes changed its station ID format.
    `complete_week` is False for weeks only partly inside the data coverage.
    """
    weeks = build_weeks().select("week_start", "week_end", "iso_year", "iso_week")
    week_start = (pl.col("date") - pl.duration(days=pl.col("date").dt.weekday() - 1)).alias("week_start")
    daily = station_day_all.group_by("date", *by).agg(
        pl.col("trips").sum(),
        pl.col("member_trips").sum(),
        pl.col("casual_trips").sum(),
        pl.col("classic_trips").sum(),
        pl.col("electric_trips").sum(),
        pl.col("trips").filter(pl.col("station_id").is_null()).sum().alias("no_station_trips"),
        pl.col("station_id").drop_nulls().n_unique().alias("active_stations"),
    ).with_columns(week_start)
    weekly = daily.group_by("week_start", *by).agg(
        pl.len().alias("days_with_data"),
        pl.col("trips", "member_trips", "casual_trips", "classic_trips", "electric_trips", "no_station_trips").sum(),
        pl.col("active_stations").mean().round(1).alias("avg_daily_active_stations"),
    )
    return (
        weeks.join(weekly, on="week_start", how="inner")
        .with_columns(
            ((pl.col("week_start") >= coverage_start) & (pl.col("week_end") <= coverage_end)).alias("complete_week"),
            (pl.col("trips") - pl.col("no_station_trips")).truediv(pl.col("avg_daily_active_stations"))
            .round(2).alias("trips_per_active_station"),
        )
        .drop("week_end")
        .sort("week_start", *by)
    )


def combine_stations(frames: list[pl.DataFrame]) -> pl.DataFrame:
    """One row per station ID across all months, located by its busiest month."""
    allst = pl.concat(frames)
    spans = allst.group_by("station_id").agg(
        pl.col("trips").sum().alias("total_trips"),
        pl.col("first_date").min(),
        pl.col("last_date").max(),
    )
    busiest = (
        allst.sort("trips", descending=True)
        .group_by("station_id", maintain_order=True)
        .first()
        .select("station_id", "station_name", "lat", "lng")
    )
    return busiest.join(spans, on="station_id").sort("station_id")


def read_interim(name: str) -> pl.DataFrame:
    return pl.concat([pl.read_parquet(f) for f in sorted((INTERIM_DIR / name).glob("*.parquet"))])


def load_station_day() -> tuple[pl.DataFrame, int, int]:
    """All station-day counts, with unseen out-of-month trips added back.

    Returns the table plus how many out-of-month trips were added and found.
    """
    spill = read_interim("spill")
    extra = unseen_spill(spill, read_interim("edge_keys"))
    return pl.concat([read_interim("station_day"), station_day(extra)]), len(extra), len(spill)


def coverage() -> tuple[date, date]:
    """First and last day covered by the processed monthly files."""
    months = sorted(f.stem for f in (INTERIM_DIR / "station_day").glob("*.parquet"))
    start = month_bounds(months[0])[0].date()
    end = date.fromordinal(month_bounds(months[-1])[1].toordinal() - 1)
    return start, end


def combine() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    sd_files = sorted((INTERIM_DIR / "station_day").glob("*.parquet"))
    if not sd_files:
        raise FileNotFoundError("No processed months found; run the download step first.")

    station_day_all, extra_trips, spill_trips = load_station_day()
    print(f"Out-of-month trips: {spill_trips:,}; {spill_trips - extra_trips:,} were duplicates, {extra_trips:,} added")
    coverage_start, coverage_end = coverage()
    build_weekly(station_day_all, coverage_start, coverage_end).write_csv(PROCESSED / "bluebikes_weekly.csv")

    st_files = sorted((INTERIM_DIR / "stations").glob("*.parquet"))
    combine_stations([pl.read_parquet(f) for f in st_files]).write_csv(
        PROCESSED / "bluebikes_stations.csv"
    )

    qa_files = sorted((INTERIM_DIR / "qa").glob("*.json"))
    pl.DataFrame([json.loads(f.read_text()) for f in qa_files]).write_csv(
        PROCESSED / "bluebikes_monthly_qa.csv"
    )
    print(f"Combined {len(sd_files)} months into data/processed/bluebikes_*.csv")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", default="201501", help="first month, YYYYMM")
    parser.add_argument("--end", default="999912", help="last month, YYYYMM")
    parser.add_argument("--keep-raw", action="store_true", help="keep downloaded zips")
    parser.add_argument("--weekly-only", action="store_true", help="only rebuild processed tables")
    args = parser.parse_args()

    if not args.weekly_only:
        months = {m: k for m, k in list_month_files().items() if args.start <= m <= args.end}
        for i, (month, key) in enumerate(months.items(), 1):
            t0 = time.time()
            qa = process_month(month, key, keep_raw=args.keep_raw)
            print(
                f"[{i}/{len(months)}] {month} {qa['layout']:>6}: "
                f"{qa['rows_kept']:>9,} kept of {qa['rows_raw']:>9,} ({time.time() - t0:.1f}s)"
            )
    combine()


if __name__ == "__main__":
    main()
