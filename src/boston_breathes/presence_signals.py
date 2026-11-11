"""Weekly signals of student presence, and the features that may explain them.

Signals, each week, for weekdays and for weekends apart: the slope across
stations of travel on the station's student measure, after a level for each
station in each year and for each week (so weather and events that hit every
station alike cancel):
  * subway: MBTA gated entries on the passenger survey's Semester Pass share
    (population_model.py), station-weeks with a closed day left out;
  * bikes: Bluebikes member trips at campus-zone against away-zone stations
    (population_checks.py);
  * requests: Boston 311 service requests from residents near campuses
    against those away from them (boston_311.py): the logarithm of their
    ratio, as a deviation from its mean over the same year. They show people
    at home, however they travel. 2026 straddles the City's switch to a new
    311 system.

Features, as deviations from their mean over the same calendar year, like the
signals:
  * students: the population model's inputs: the share of students out of
    session, on summer break and on other breaks, and the shares of resident
    students away if only students in closed residence halls leave (min) or
    every student out of session not living with their parents (max);
  * weather, on the weekdays and on the weekend days of the week apart
    (Logan Airport): mean temperature, days with rain (1 mm or more), heavy
    rain (10 mm or more), snow (1 cm or more), and hot days (30 C or more),
    as shares of those days;
  * calendar: holidays in the week, and the weeks from December 24 to January 1.

Weeks outside COVID-19 in 2016-2019 and 2022 to the latest data; Bluebikes
needs the station-day files that bluebikes.py keeps in data/interim.

Output: data/processed/presence_signals_weekly.csv
"""

import polars as pl

from boston_breathes import population_checks as pc
from boston_breathes import population_model as pm
from boston_breathes import weather as wx
from boston_breathes.paths import INTERIM, PROCESSED
from boston_breathes.weeks import build_weeks

YEARS = [(2016, 2019), (2022, 2026)]
SIGNALS = {"subway": pm.MAIN_MEASURE, "bikes": pc.BLUEBIKES}  # station-based signals and their student measure
NAMES = [*SIGNALS, "requests"]
DAYS = ("weekday", "weekend")
STUDENTS = ["out", "summer", "breaks", "min_share", "max_share"]
WEATHER = ["tmean_c", "rain", "heavy_rain", "snow", "hot"]
CALENDAR = ["holidays", "year_end_week"]


def weather_by_day_type(daily: pl.DataFrame) -> pl.DataFrame:
    """Each week's weather on weekdays and on weekend days: mean temperature and shares of days with rain, heavy
    rain, snow and heat."""
    weekend = pl.col("date").dt.weekday() >= 6
    week_start = (pl.col("date") - pl.duration(days=pl.col("date").dt.weekday() - 1)).alias("week_start")
    share = lambda condition: condition.cast(pl.Float64).mean()  # noqa: E731
    out = []
    for day, mask in (("weekday", ~weekend), ("weekend", weekend)):
        out.append(
            daily.filter(mask).with_columns(week_start).group_by("week_start").agg(
                pl.col("tmean_c").mean().alias(f"{day}_tmean_c"),
                share(pl.col("prcp_mm") >= wx.RAIN_DAY_MM).alias(f"{day}_rain"),
                share(pl.col("prcp_mm") >= wx.HEAVY_RAIN_MM).alias(f"{day}_heavy_rain"),
                share(pl.col("snow_mm") >= wx.SNOW_DAY_MM).alias(f"{day}_snow"),
                share(pl.col("tmax_c") >= wx.HOT_C).alias(f"{day}_hot"))
        )
    return out[0].join(out[1], on="week_start")


def calendar_features(weeks: pl.DataFrame) -> pl.DataFrame:
    """Holidays in the week, and whether the week has a day from December 24 to January 1."""
    days = [pl.col("week_start") + pl.duration(days=i) for i in range(7)]
    year_end = pl.any_horizontal(*[((d.dt.month() == 12) & (d.dt.day() >= 24)) | ((d.dt.month() == 1) & (d.dt.day() == 1))
                                   for d in days])
    return weeks.select("week_start", pl.col("n_holidays").cast(pl.Float64).alias("holidays"),
                        year_end.cast(pl.Float64).alias("year_end_week"))


def signals(by_station: pl.DataFrame, survey: pl.DataFrame, station_day: pl.DataFrame, bike_stations: pl.DataFrame,
            inputs: pl.DataFrame) -> pl.DataFrame:
    """Weekly weekday and weekend slopes of each signal."""
    frames = []
    for years in YEARS:
        subway = pm.weekly_slopes(pm.station_weeks(by_station, survey, inputs, years), SIGNALS["subway"])
        bikes = pm.weekly_slopes(pc.bluebikes_station_weeks(station_day, bike_stations, inputs, years),
                                 SIGNALS["bikes"])
        frames.append(subway.rename({d: f"subway_{d}" for d in DAYS}).join(
            bikes.rename({d: f"bikes_{d}" for d in DAYS}), on="week_start", how="full", coalesce=True))
    return pl.concat(frames).sort("week_start")


def requests_signal(daily_311: pl.DataFrame) -> pl.DataFrame:
    """The logarithm of resident 311 requests near campuses over those away from them, each week, on weekdays and
    on weekends."""
    weekend = pl.col("date").dt.weekday() >= 6
    week_start = (pl.col("date") - pl.duration(days=pl.col("date").dt.weekday() - 1)).alias("week_start")
    counts = (daily_311.filter(pl.col("campus_zone").is_in(["campus", "away"]))
              .with_columns(week_start, pl.when(weekend).then(pl.lit("weekend")).otherwise(pl.lit("weekday")).alias("day"))
              .group_by("week_start", "day", "campus_zone").agg(pl.col("requests").sum())
              .pivot(on="campus_zone", index=["week_start", "day"], values="requests"))
    return (counts.with_columns((pl.col("campus") / pl.col("away")).log().alias("ratio"))
            .pivot(on="day", index="week_start", values="ratio")
            .select("week_start", *[pl.col(d).alias(f"requests_{d}") for d in DAYS]))


def build(by_station: pl.DataFrame, survey: pl.DataFrame, station_day: pl.DataFrame, bike_stations: pl.DataFrame,
          inputs: pl.DataFrame, daily_weather: pl.DataFrame, weeks: pl.DataFrame, daily_311: pl.DataFrame) -> pl.DataFrame:
    features = (inputs.select("week_start", *STUDENTS)
                .join(weather_by_day_type(daily_weather), on="week_start")
                .join(calendar_features(weeks), on="week_start"))
    columns = [c for c in features.columns if c != "week_start"] + [f"requests_{d}" for d in DAYS]
    year = pl.col("week_start").dt.year()
    return (
        signals(by_station, survey, station_day, bike_stations, inputs)
        .join(requests_signal(daily_311), on="week_start", how="left")
        .join(features, on="week_start")
        .with_columns(*[(pl.col(c) - pl.col(c).mean().over(year)).alias(c) for c in columns])
        .sort("week_start")
    )


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    inputs = pm.weekly_inputs(read("students_away_weekly.csv"), read("student_presence_weekly.csv",
                                                                     infer_schema_length=None))
    station_day = pl.concat([pl.read_parquet(f) for f in sorted((INTERIM / "bluebikes" / "station_day").glob("*.parquet"))],
                            how="diagonal_relaxed")
    out = build(read("mbta_weekly_by_station.csv"), read("mbta_survey_by_station.csv"), station_day,
                read("bluebikes_station_campus.csv", schema_overrides={"station_id": pl.Utf8}), inputs,
                read("weather_daily.csv"), build_weeks(), read("boston_311_daily.csv"))
    out.write_csv(PROCESSED / "presence_signals_weekly.csv")
    print(f"{len(out)} weeks, {out['week_start'].min()} to {out['week_start'].max()}; "
          f"signals: {[c for c in out.columns if c.endswith(DAYS)]}")
    print(out.null_count().transpose(include_header=True).filter(pl.col("column_0") > 0))


if __name__ == "__main__":
    main()
