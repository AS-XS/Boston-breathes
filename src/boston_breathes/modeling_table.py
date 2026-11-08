"""One row per week with what the models will use: travel records, weather, calendar and students.

Nothing is fitted here. The weekly tables are joined on week_start (Monday),
so every model reads the same rows and columns.

Columns, by source:
  * calendar (weeks.csv): ISO year and week, month (of the week's Thursday),
    fall_year (the academic year a week belongs to, by its Thursday), the
    number of holidays, and whether the week has a day from December 24 to
    January 1, when the whole city slows down (year_end_week);
  * weather (weather_weekly.csv): temperature, rain, snow, ice and hot days,
    wind;
  * Bluebikes (bluebikes_weekly_by_campus_zone.csv, study-area stations):
    trips in total and by campus zone, casual and electric trips, the share
    of trips by riders aged 18-24 (while birth years were published, until
    April 2020), and network size (average stations in use per day) in
    total and by zone;
  * MBTA (mbta_weekly_by_campus_zone.csv and mbta_weekly_by_station.csv,
    study-area gated stations): entries in total and by campus zone, and the
    number of stations with entries that week, since stations close for
    repairs and the Green Line Extension opened in 2022;
  * students (student_presence_weekly.csv): the Student Presence Index and
    the housing-weighted index, the summer-break share, students in
    session, beds out of session, flagged enrollment and the COVID-19
    period;
  * population (students_away_weekly.csv): the Census resident population,
    resident college students by where they live, and the weekly range of
    students away.

A week without data from a source keeps nulls; the *_complete flags mark
weeks with all seven days of that source. Street counts are single days
rather than a weekly series, so they are left out.

Output: data/processed/modeling_weekly.csv
"""

import polars as pl

from boston_breathes.paths import PROCESSED
from boston_breathes.presence import fall_year

ZONES = ["campus", "near", "away"]
WEATHER = ["tmean_c", "tmax_mean_c", "tmin_mean_c", "prcp_total_mm", "rain_days", "heavy_rain_days",
           "snow_total_mm", "snow_days", "ice_days", "hot_days", "wind_mean_ms"]
STUDENTS = ["presence_index", "housing_presence_index", "summer_break_share", "students_in_session",
            "normal_enrollment", "beds_out_of_session", "enrollment_imputed", "covid_period", "covid_remote_share"]
POPULATION = ["census_population", "population_extrapolated", "resident_students", "in_university_housing",
              "with_parents", "elsewhere", "closed_bed_share", "min_students_away", "max_students_away"]


def year_end_week() -> pl.Expr:
    days = [pl.col("week_start") + pl.duration(days=i) for i in range(7)]
    return pl.any_horizontal(
        [((d.dt.month() == 12) & (d.dt.day() >= 24)) | ((d.dt.month() == 1) & (d.dt.day() == 1)) for d in days]
    ).alias("year_end_week")


def calendar(weeks: pl.DataFrame) -> pl.DataFrame:
    return weeks.select("week_start", "iso_year", "iso_week", "month",
                        fall_year(pl.col("week_start")).alias("fall_year"), "n_holidays", year_end_week())


def weather(weekly: pl.DataFrame) -> pl.DataFrame:
    return weekly.select("week_start", *WEATHER, pl.col("complete_week").alias("weather_complete"))


def by_zone(weekly: pl.DataFrame, prefix: str, totals: dict[str, str], per_zone: dict[str, str]) -> pl.DataFrame:
    """Study-area totals and per-zone columns of a weekly table by campus zone, named prefix_name(_zone)."""
    zone = pl.col("campus_zone")
    return weekly.group_by("week_start").agg(
        *[pl.col(col).sum().alias(f"{prefix}_{name}") for col, name in totals.items()],
        *[pl.col(col).filter(zone == z).sum().alias(f"{prefix}_{name}_{z}")
          for col, name in per_zone.items() for z in ZONES],
        pl.col("complete_week").all().alias(f"{prefix}_complete"),
    )


def bluebikes(weekly_by_zone: pl.DataFrame) -> pl.DataFrame:
    stations = {"avg_daily_active_stations": "stations"}
    out = by_zone(weekly_by_zone, "bluebikes",
                  {"trips": "trips", "casual_trips": "casual_trips", "electric_trips": "electric_trips",
                   "age_known_trips": "age_known_trips", "college_age_trips": "college_age_trips", **stations},
                  {"trips": "trips", **stations})
    known = pl.col("bluebikes_age_known_trips")
    return out.with_columns(
        pl.when(known > 0).then(pl.col("bluebikes_college_age_trips") / known).round(4)
        .alias("bluebikes_college_age_share"),
        pl.col("^bluebikes_stations.*$").round(1),
    ).drop("bluebikes_age_known_trips", "bluebikes_college_age_trips")


def mbta(weekly_by_zone: pl.DataFrame, weekly_by_station: pl.DataFrame, stations: pl.DataFrame) -> pl.DataFrame:
    study_area = stations.filter(pl.col("in_study_area")).select("stop_id")
    open_stations = (
        weekly_by_station.join(study_area, on="stop_id").filter(pl.col("entries") > 0)
        .group_by("week_start").agg(pl.len().cast(pl.Int64).alias("mbta_stations"))
    )
    return by_zone(weekly_by_zone, "mbta", {"entries": "entries"}, {"entries": "entries"}) \
        .join(open_stations, on="week_start", how="left")


def build(weeks: pl.DataFrame, weather_weekly: pl.DataFrame, bike_zone: pl.DataFrame, mbta_zone: pl.DataFrame,
          mbta_station: pl.DataFrame, mbta_stations: pl.DataFrame, presence: pl.DataFrame,
          away: pl.DataFrame) -> pl.DataFrame:
    tables = [weather(weather_weekly), bluebikes(bike_zone), mbta(mbta_zone, mbta_station, mbta_stations),
              presence.select("week_start", *STUDENTS), away.select("week_start", *POPULATION)]
    out = calendar(weeks)
    for table in tables:
        out = out.join(table, on="week_start", how="left")
    return out.sort("week_start")


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    out = build(
        read("weeks.csv"), read("weather_weekly.csv"), read("bluebikes_weekly_by_campus_zone.csv"),
        read("mbta_weekly_by_campus_zone.csv"), read("mbta_weekly_by_station.csv"), read("mbta_stations.csv"),
        read("student_presence_weekly.csv", infer_schema_length=None), read("students_away_weekly.csv"),
    )
    out.write_csv(PROCESSED / "modeling_weekly.csv")
    usable = out.filter(pl.col("weather_complete") & pl.col("bluebikes_complete") & pl.col("mbta_complete"))
    print(f"Modeling table: {len(out)} weeks ({out['week_start'].min()} to {out['week_start'].max()}), "
          f"{len(out.columns)} columns; {len(usable)} weeks with complete weather, Bluebikes and MBTA data")


if __name__ == "__main__":
    main()
