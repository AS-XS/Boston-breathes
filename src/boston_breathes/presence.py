"""Student Presence Index and effective population, by week.

Students in session
    For each institution, in-person enrollment (IPEDS, students not enrolled
    only online) times the share of the week its calendar is in session.
    Institutions without a collected calendar, and weeks a collected calendar
    leaves unknown, use the enrollment-weighted average of the known
    calendars that week. Weeks outside the published falls use the nearest
    published fall (carried back to spring 2015, forward after the latest)
    and are flagged as enrollment_imputed.

Student Presence Index
    Students in session divided by the normal in-person enrollment: 0 when
    every institution is on break, 1 when all are in session. In fall 2020
    (REMOTE_FALL) many students studied only online, so the normal
    enrollment uses each institution's fall 2019 count of online-only
    students instead; the extra online-only students count as away.

Effective population
    The Census counts college students where they live during the school
    year, so the resident population already includes students living in the
    study area all year round. The effective population removes the resident
    students who are away when not in session:

        effective = Census population
                    - (1 - presence index) * (UNDERGRAD_AWAY * resident undergraduates
                                              + GRAD_AWAY * resident graduate students)

    Resident students come from the ACS 5-year estimate ending in the year of
    the fall term (the latest available estimate for later years).

COVID-19
    From COVID_START to COVID_END students are treated as away, although the
    calendars still show the spring 2020 term in session; from fall 2020 the
    in-person enrollment already reflects remote study.

Outputs in data/processed/:
  * student_presence_weekly.csv
  * student_presence_by_institution.csv
"""

from datetime import date

import polars as pl

from boston_breathes.paths import PROCESSED
from boston_breathes.weeks import build_weeks

# Share of resident students away from the area when not in session.
UNDERGRAD_AWAY = 1.0
GRAD_AWAY = 0.0

COVID_START = date(2020, 3, 16)  # most universities sent students home that week
COVID_END = date(2020, 8, 15)
REMOTE_FALL = 2020  # fall term taught mostly online


def fall_year(week_start: pl.Expr) -> pl.Expr:
    """Fall term a week belongs to: August-December -> that year, else the year before."""
    thursday = week_start + pl.duration(days=3)
    return pl.when(thursday.dt.month() >= 8).then(thursday.dt.year()).otherwise(thursday.dt.year() - 1)


def enrollment_by_week(weeks: pl.DataFrame, enrollment: pl.DataFrame) -> pl.DataFrame:
    """In-person and normal enrollment of each institution for each week (fall-term value)."""
    first, latest = enrollment["fall_year"].min(), enrollment["fall_year"].max()
    before = enrollment.filter(pl.col("fall_year") == REMOTE_FALL - 1).select(
        "unitid", pl.col("distance_only").fill_null(0).alias("_usual_online"))
    per_year = (
        enrollment.join(before, on="unitid", how="left")
        .with_columns(
            pl.when((pl.col("fall_year") == REMOTE_FALL) & pl.col("_usual_online").is_not_null())
            .then((pl.col("total") - pl.col("_usual_online")).clip(pl.col("in_person"), pl.col("total")))
            .otherwise(pl.col("in_person")).alias("normal_in_person")
        )
        .select("unitid", "name", "fall_year", "in_person", "normal_in_person")
    )
    w = weeks.with_columns(fall_year(pl.col("week_start")).alias("fall_year")).with_columns(
        pl.col("fall_year").clip(first, latest).alias("enrollment_year"),
        ~pl.col("fall_year").is_between(first, latest).alias("enrollment_imputed"),
    )
    return w.join(per_year.rename({"fall_year": "enrollment_year"}), on="enrollment_year", how="inner")


def session_share(enrolled: pl.DataFrame, academic_weekly: pl.DataFrame) -> pl.DataFrame:
    """Add each institution's share of the week in session and where it came from."""
    own = academic_weekly.select("week_start", "unitid", (pl.col("days_in_session") / 7).alias("own_share"))
    df = enrolled.join(own, on=["week_start", "unitid"], how="left")
    known = df.filter(pl.col("own_share").is_not_null())
    typical = known.group_by("week_start").agg(
        ((pl.col("own_share") * pl.col("in_person")).sum() / pl.col("in_person").sum()).alias("typical_share")
    )
    return df.join(typical, on="week_start", how="left").with_columns(
        pl.coalesce("own_share", "typical_share").alias("session_share"),
        pl.when(pl.col("own_share").is_not_null()).then(pl.lit("own calendar"))
        .otherwise(pl.lit("typical calendar")).alias("calendar_source"),
    ).drop("own_share", "typical_share")


def apply_covid(df: pl.DataFrame) -> pl.DataFrame:
    covid = (pl.col("week_start") + pl.duration(days=3)).is_between(COVID_START, COVID_END)
    return df.with_columns(
        covid.alias("covid_away"),
        pl.when(covid).then(0.0).otherwise(pl.col("session_share")).alias("session_share"),
    )


def resident_students(weeks: pl.DataFrame, residents: pl.DataFrame) -> pl.DataFrame:
    """Study-area resident undergraduate and graduate students for each week."""
    totals = residents.group_by("acs_year").agg(
        pl.col("undergrad").sum().alias("resident_undergrads"),
        pl.col("graduate").sum().alias("resident_grads"),
    )
    first, latest = totals["acs_year"].min(), totals["acs_year"].max()
    return weeks.with_columns(
        fall_year(pl.col("week_start")).clip(first, latest).alias("acs_year")
    ).join(totals, on="acs_year", how="left").drop("acs_year")


def build(
    weeks: pl.DataFrame,
    enrollment: pl.DataFrame,
    academic_weekly: pl.DataFrame,
    residents: pl.DataFrame,
    population: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    per_inst = apply_covid(session_share(enrollment_by_week(weeks, enrollment), academic_weekly)).with_columns(
        (pl.col("in_person") * pl.col("session_share")).alias("students_in_session")
    )
    weekly = (
        per_inst.group_by("week_start").agg(
            pl.col("in_person").sum().alias("in_person_enrollment"),
            pl.col("normal_in_person").sum().alias("normal_enrollment"),
            pl.col("students_in_session").sum().round(0),
            (pl.col("in_person").filter(pl.col("calendar_source") == "own calendar").sum()
             / pl.col("in_person").sum()).round(3).alias("own_calendar_share"),
            pl.col("enrollment_imputed").any(),
            pl.col("covid_away").any(),
        )
        .with_columns((pl.col("students_in_session") / pl.col("normal_enrollment")).round(4).alias("presence_index"))
        .join(resident_students(weeks, residents), on="week_start", how="left")
        .join(population.select("week_start", pl.col("study_area").alias("census_population"),
                                pl.col("extrapolated").alias("population_extrapolated")), on="week_start", how="left")
        .with_columns(
            (-(1 - pl.col("presence_index"))
             * (UNDERGRAD_AWAY * pl.col("resident_undergrads") + GRAD_AWAY * pl.col("resident_grads")))
            .round(0).alias("student_change")
        )
        .with_columns((pl.col("census_population") + pl.col("student_change")).alias("effective_population"))
        .sort("week_start")
        .select(
            "week_start", "presence_index", "students_in_session", "in_person_enrollment", "normal_enrollment",
            "own_calendar_share",
            "resident_undergrads", "resident_grads", "census_population", "student_change", "effective_population",
            "enrollment_imputed", "population_extrapolated", "covid_away",
        )
    )
    by_inst = per_inst.select(
        "week_start", "unitid", "name", "in_person", "session_share", "calendar_source",
        pl.col("students_in_session").round(1), "enrollment_imputed", "covid_away",
    ).sort("week_start", "unitid")
    return weekly, by_inst


def main() -> None:
    weeks = build_weeks().select("week_start")
    enrollment = pl.read_csv(PROCESSED / "enrollment_annual.csv", schema_overrides={"unitid": pl.Utf8})
    academic = pl.read_csv(PROCESSED / "academic_weekly.csv", schema_overrides={"unitid": pl.Utf8}, try_parse_dates=True)
    residents = pl.read_csv(PROCESSED / "student_residents_annual.csv")
    population = pl.read_csv(PROCESSED / "population_weekly.csv", try_parse_dates=True)
    weekly, by_inst = build(weeks, enrollment, academic, residents, population)
    weekly.write_csv(PROCESSED / "student_presence_weekly.csv")
    by_inst.write_csv(PROCESSED / "student_presence_by_institution.csv")

    y = weekly.filter(pl.col("week_start").dt.year() == 2019)
    print(f"Student presence: {len(weekly)} weeks; in 2019 the index ranged {y['presence_index'].min():.2f}-"
          f"{y['presence_index'].max():.2f} and the effective population "
          f"{y['effective_population'].min():,.0f}-{y['effective_population'].max():,.0f}")
    print(f"Share of enrollment with its own calendar: median {weekly['own_calendar_share'].median():.0%}")


if __name__ == "__main__":
    main()
