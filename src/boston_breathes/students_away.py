"""Resident college students away from the study area: a range for every week.

How many students actually leave when not in session is what the project
measures, from city activity; it is not assumed here. The Census counts
college students where they live during the school year, so the resident
population already includes them. Who could leave depends on where they live
(student_living_arrangements.csv):

  * students living with their parents stay;
  * students in university housing leave when their residence halls close:
    every hall closes for summer (some host summer students and interns),
    and data/manual/residence_hall_breaks.csv records, for the largest
    universities, which close for winter break, Thanksgiving and spring
    break. Halls are treated as closed from 16 March to 15 August 2020;
  * the others (renters, owners, students living with partners) may or may
    not leave.

    max_students_away = in university housing x (1 - housing presence index)
                        + elsewhere x (1 - presence index)
    min_students_away = in university housing x share of beds in closed halls

The share of beds in closed halls weights each institution by its beds
(IPEDS capacity): its summer-break share counts as closed, and its other days
out of session count as closed when its halls close for that break (by month:
November is Thanksgiving, December and January winter, the rest spring).
Institutions without a recorded policy count as open outside summer, so the
minimum stays a minimum. It still assumes students whose halls close leave
the area, which some (staying with friends, in summer housing) do not.

Living arrangements come from ACS 5-year estimates ending in the year of the
fall term (the nearest available for other years), like the resident
students in the presence index.

Output: data/processed/students_away_weekly.csv
"""

import polars as pl

from boston_breathes.paths import DATA, PROCESSED
from boston_breathes.presence import COVID_END, COVID_START, fall_year

HALLS_PATH = DATA / "manual" / "residence_hall_breaks.csv"
BREAKS = ["thanksgiving", "winter", "spring"]
STATUS = {"open", "closed", "unknown"}
CHECKED = {"page", "partial", "search"}


def load_halls(path=HALLS_PATH) -> pl.DataFrame:
    """Read the hand-collected residence hall policies; raise listing all problems."""
    halls = pl.read_csv(path, schema_overrides={"unitid": pl.Utf8})
    problems = []
    for col in BREAKS:
        for value in set(halls[col]) - STATUS:
            problems.append(f"{col}: unknown value {value!r}")
    for value in set(halls["checked"]) - CHECKED:
        problems.append(f"checked: unknown value {value!r}")
    for r in halls.filter(~pl.col("source_url").fill_null("").str.starts_with("http")).iter_rows(named=True):
        problems.append(f"{r['institution']}: missing source URL")
    for r in halls.filter(pl.col("unitid").is_duplicated()).unique("unitid").iter_rows(named=True):
        problems.append(f"{r['institution']}: duplicate row")
    if problems:
        raise ValueError("Residence hall problems:\n  " + "\n  ".join(problems))
    return halls.select("unitid", *BREAKS)


def break_kind(week_start: pl.Expr) -> pl.Expr:
    """Which break a non-summer week out of session belongs to, by the month of its Thursday."""
    month = (week_start + pl.duration(days=3)).dt.month()
    return pl.when(month == 11).then(pl.lit("thanksgiving")).when(month.is_in([12, 1])).then(pl.lit("winter")) \
        .otherwise(pl.lit("spring"))


def closed_bed_share(by_inst: pl.DataFrame, halls: pl.DataFrame) -> pl.DataFrame:
    """Share of the week's beds (IPEDS capacity) in closed residence halls."""
    thursday = pl.col("week_start") + pl.duration(days=3)
    policy = pl.coalesce(
        *[pl.when(pl.col("_kind") == b).then(pl.col(b)) for b in BREAKS]
    ).fill_null("unknown")
    closed = (
        pl.when(thursday.is_between(COVID_START, COVID_END)).then(1.0)
        .otherwise(pl.col("summer_share")
                   + (1 - pl.col("session_share") - pl.col("summer_share")).clip(lower_bound=0)
                   * (policy == "closed").cast(pl.Float64))
    )
    return (
        by_inst.join(halls, on="unitid", how="left")
        .with_columns(break_kind(pl.col("week_start")).alias("_kind"))
        .group_by("week_start")
        .agg(((pl.col("dorm_capacity") * closed).sum() / pl.col("dorm_capacity").sum()).alias("closed_bed_share"))
    )


def arrangements_by_week(weeks: pl.DataFrame, arrangements: pl.DataFrame) -> pl.DataFrame:
    """Study-area resident students by living arrangement for each week (fall-term ACS year)."""
    totals = arrangements.group_by("acs_year").agg(
        pl.col("college_students").sum().alias("resident_students"),
        pl.col("in_university_housing", "with_parents", "elsewhere").sum(),
    )
    first, latest = totals["acs_year"].min(), totals["acs_year"].max()
    return weeks.with_columns(fall_year(pl.col("week_start")).clip(first, latest).alias("acs_year")) \
        .join(totals, on="acs_year", how="left").drop("acs_year")


def build(presence: pl.DataFrame, by_inst: pl.DataFrame, arrangements: pl.DataFrame,
          population: pl.DataFrame, halls: pl.DataFrame) -> pl.DataFrame:
    weeks = presence.select("week_start", "presence_index", "housing_presence_index", "covid_period")
    return (
        weeks.join(arrangements_by_week(weeks.select("week_start"), arrangements), on="week_start", how="left")
        .join(closed_bed_share(by_inst, halls), on="week_start", how="left")
        .join(population.select("week_start", pl.col("study_area").alias("census_population"),
                                pl.col("extrapolated").alias("population_extrapolated")), on="week_start", how="left")
        .with_columns(
            (pl.col("in_university_housing") * (1 - pl.col("housing_presence_index"))
             + pl.col("elsewhere") * (1 - pl.col("presence_index"))).round(0).cast(pl.Int64).alias("max_students_away"),
            (pl.col("in_university_housing") * pl.col("closed_bed_share")).round(0).cast(pl.Int64)
            .alias("min_students_away"),
            pl.col("closed_bed_share").round(4),
        )
        .with_columns(
            (pl.col("census_population") - pl.col("max_students_away")).alias("min_effective_population"),
            (pl.col("census_population") - pl.col("min_students_away")).alias("max_effective_population"),
        )
        .select(
            "week_start", "presence_index", "housing_presence_index", "closed_bed_share",
            "resident_students", "in_university_housing", "with_parents", "elsewhere",
            "min_students_away", "max_students_away", "census_population",
            "min_effective_population", "max_effective_population", "population_extrapolated", "covid_period",
        )
        .sort("week_start")
    )


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    presence = read("student_presence_weekly.csv", infer_schema_length=None)
    by_inst = read("student_presence_by_institution.csv", schema_overrides={"unitid": pl.Utf8})
    arrangements = read("student_living_arrangements.csv")
    population = read("population_weekly.csv")
    out = build(presence, by_inst, arrangements, population, load_halls())
    out.write_csv(PROCESSED / "students_away_weekly.csv")

    y = out.filter(pl.col("week_start").dt.year() == 2019)
    print(f"Students away, 2019: between {y['min_students_away'].max():,} and {y['max_students_away'].max():,} "
          f"in the emptiest week, of {y['resident_students'][0]:,} resident students "
          f"({y['with_parents'][0]:,} living with their parents)")


if __name__ == "__main__":
    main()
