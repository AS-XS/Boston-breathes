"""Daily and weekly weather at Boston Logan Airport (NOAA GHCN-Daily).

Source: NOAA NCEI Access Data Service, station USW00014739, metric units.
Logan does not report daily average temperature (TAVG) or snow depth (SNWD),
so the daily mean temperature is taken as the midpoint of TMAX and TMIN.

Outputs in data/processed/:
  * weather_daily.csv  - one row per day
  * weather_weekly.csv - one row per Monday-to-Sunday week
"""

from datetime import date, timedelta
import io

import polars as pl

from boston_breathes import http
from boston_breathes.paths import PROCESSED, RAW, STUDY_END, STUDY_START
from boston_breathes.weeks import build_weeks

STATION = "USW00014739"  # Boston Logan International Airport
API_URL = "https://www.ncei.noaa.gov/access/services/data/v1"
DATA_TYPES = ["TMAX", "TMIN", "PRCP", "SNOW", "AWND"]
RAW_PATH = RAW / "weather" / f"noaa_daily_{STATION}.csv"

RAIN_DAY_MM = 1.0  # a "wet day" in climatology
HEAVY_RAIN_MM = 10.0
SNOW_DAY_MM = 10.0  # 1 cm of new snow
FREEZING_C = 0.0
HOT_C = 30.0


def download(start: date = STUDY_START, end: date | None = None) -> str:
    end = min(end or date.today(), STUDY_END)
    params = {
        "dataset": "daily-summaries",
        "stations": STATION,
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dataTypes": ",".join(DATA_TYPES),
        "units": "metric",
        "format": "csv",
        "includeAttributes": "false",
    }
    resp = http.get(API_URL, params=params, timeout=120)
    resp.raise_for_status()
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(resp.text)
    return resp.text


def parse_daily(csv_text: str) -> pl.DataFrame:
    """NOAA CSV -> one row per day with readable column names."""
    raw = pl.read_csv(io.StringIO(csv_text), infer_schema=False)
    stations = raw["STATION"].unique().to_list()
    if stations != [STATION]:
        raise ValueError(f"Expected only station {STATION}, got {stations}")
    num = lambda c: pl.col(c).cast(pl.Float64, strict=False)  # noqa: E731
    daily = raw.select(
        pl.col("DATE").str.to_date("%Y-%m-%d").alias("date"),
        num("TMAX").alias("tmax_c"),
        num("TMIN").alias("tmin_c"),
        num("PRCP").alias("prcp_mm"),
        num("SNOW").alias("snow_mm"),
        num("AWND").alias("wind_ms"),
    ).with_columns(((pl.col("tmax_c") + pl.col("tmin_c")) / 2).round(2).alias("tmean_c"))
    if daily["date"].is_duplicated().any():
        raise ValueError("Duplicate dates in NOAA data")
    return daily.sort("date")


def missing_dates(daily: pl.DataFrame) -> list[date]:
    first, last = daily["date"].min(), daily["date"].max()
    expected = {first + timedelta(days=i) for i in range((last - first).days + 1)}
    return sorted(expected - set(daily["date"].to_list()))


def build_weekly(daily: pl.DataFrame) -> pl.DataFrame:
    """Weekly weather on the shared Monday-to-Sunday timeline.

    Precipitation and snowfall are weekly totals, so weeks with missing days
    (`days_with_data` < 7) understate them.
    """
    weeks = build_weeks().select("week_start", "iso_year", "iso_week")
    week_start = (pl.col("date") - pl.duration(days=pl.col("date").dt.weekday() - 1)).alias("week_start")
    weekly = daily.with_columns(week_start).group_by("week_start").agg(
        pl.len().alias("days_with_data"),
        pl.col("tmean_c").mean().round(2),
        pl.col("tmax_c").mean().round(2).alias("tmax_mean_c"),
        pl.col("tmin_c").mean().round(2).alias("tmin_mean_c"),
        pl.col("prcp_mm").sum().round(1).alias("prcp_total_mm"),
        (pl.col("prcp_mm") >= RAIN_DAY_MM).sum().alias("rain_days"),
        (pl.col("prcp_mm") >= HEAVY_RAIN_MM).sum().alias("heavy_rain_days"),
        pl.col("snow_mm").sum().round(1).alias("snow_total_mm"),
        (pl.col("snow_mm") >= SNOW_DAY_MM).sum().alias("snow_days"),
        (pl.col("tmax_c") < FREEZING_C).sum().alias("freezing_days"),
        (pl.col("tmax_c") >= HOT_C).sum().alias("hot_days"),
        pl.col("wind_ms").mean().round(2).alias("wind_mean_ms"),
    )
    return (
        weeks.join(weekly, on="week_start", how="inner")
        .with_columns((pl.col("days_with_data") == 7).alias("complete_week"))
        .sort("week_start")
    )


def main() -> None:
    daily = parse_daily(download())
    gaps = missing_dates(daily)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    daily.write_csv(PROCESSED / "weather_daily.csv")
    weekly = build_weekly(daily)
    weekly.write_csv(PROCESSED / "weather_weekly.csv")
    print(
        f"Weather: {len(daily):,} days ({daily['date'].min()} to {daily['date'].max()}), "
        f"{len(gaps)} missing days, {len(weekly)} weeks"
    )
    for col in ["tmax_c", "tmin_c", "prcp_mm", "snow_mm", "wind_ms"]:
        n = daily[col].null_count()
        if n:
            print(f"  {col}: {n} days without a value")


if __name__ == "__main__":
    main()
