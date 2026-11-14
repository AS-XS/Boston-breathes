"""Are students the same share of weekend riders as of weekday riders? A test with Bluebikes riders' ages.

The population model reads how many students left from weekend travel,
through a scale k fitted on weekday travel. That assumes students make up the
same share of a station's riders on weekends as on weekdays. If they make up
a larger one, weekend travel moves more for each student who leaves, and the
shares away come out too large by that ratio (population_accuracy.py: a ratio
of 1.2 puts about 25,000 too many students away in mid-summer).

The MBTA passenger survey does not split weekdays and weekends, but Bluebikes
published riders' birth years until April 2020. Members' trips (residents
rather than visitors, as in the Bluebikes signal) by riders aged 18-24 stand
in for students' trips, in 2016-2019.

The Bluebikes signal compares member trips at campus-zone stations with those
at away-zone stations, so its k is proportional to how much larger students'
share of trips is at campus-zone stations. The weekend factor is
  c = campus-zone minus away-zone share of member trips by riders aged 18-24 on weekends
      / the same on weekdays,
in weeks when every university is in session (Student Presence Index at least
0.9).

Riders aged 18-24 also include young people who are not students, whose
share of trips also rises on weekends, more at some places than others. At
downtown campus-zone stations (Suffolk, Emerson, Tufts' health-sciences
campus) young riders are a smaller share than in the away zone on weekdays,
when commuters ride, and catch up on weekends: a shift among non-students
that the zone-wide factor takes for students. A second estimate therefore
keeps only the campus-zone stations of universities where riders aged 18-24
are clearly more common than in the away zone on weekdays in session (by at
least STUDENT_EXCESS, 10 percentage points), where the young excess is
mostly students.

How much of the excess disappears in summer (summer break for at least 90%
of students) shows how much of it is students. The shares of the other age
bands are reported but are no check: the shares add up to one, so a larger
young excess on weekends means larger shortfalls of other ages.

95% intervals: stations and calendar months resampled with replacement (each
station with all its months and each month with all its stations, by
multiplying their counts).

What it would mean: weekend signals c times larger than the model expects.
The joint model (joint_population.py, no weather model) is refitted with the
weekend inputs of Bluebikes multiplied by c, and, as a what-if, those of the
subway too; the shares give the students away in weeks of 2019
(population_model.away). The factor is measured in 2016-2019 and applied to
the fit on 2022-2025.

Downloads the 2016-2019 trip files again (about 230 MB) and deletes them after
counting.

Outputs:
  * data/interim/bluebikes/member_ages/YYYYMM.parquet  member trips by station, day and age band
  * data/processed/bluebikes_member_ages_weekly.csv    the same by station-week and kind of day
  * data/processed/weekend_riders_shares.csv           share of member trips by each age band, by zone, kind
                                                       of day and time of year
  * data/processed/weekend_riders.csv                  weekend factors, and the shares and students away with
                                                       the assumption corrected
"""

import numpy as np
import polars as pl

from boston_breathes import bluebikes as bb
from boston_breathes import joint_population as jp
from boston_breathes import population_model as pm
from boston_breathes.paths import INTERIM, PROCESSED

MONTHS = ("201601", "201912")
AGE_BANDS = {"18_24": (18, 24), "25_29": (25, 29), "30_plus": (30, bb.MAX_RIDER_AGE)}
STUDENT_BAND = "18_24"
IN_SESSION, SUMMER = 0.9, 0.9  # presence index, and share of students on summer break
STUDENT_EXCESS = 0.10  # a university's campus-zone stations count as student-dominated above this weekday excess
BOOTSTRAP = 1000
WEEKS = {"mid-summer": "2019-07-08", "spring breaks": "2019-03-11", "Thanksgiving": "2019-11-25"}
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


def labelled(counts: pl.DataFrame, stations: pl.DataFrame, inputs: pl.DataFrame) -> pl.DataFrame:
    """Station-weeks of the study area with their zone, and whether the week is in session or in summer."""
    period = (pl.when(pl.col("presence_index") >= IN_SESSION).then(pl.lit("in session"))
              .when(pl.col("summer_break_share") >= SUMMER).then(pl.lit("summer")).otherwise(None))
    return (
        counts.join(stations.filter(pl.col("in_study_area")).select(
            "station_id", pl.col("campus_zone").alias("zone"), pl.col("campus_institution").alias("institution")),
            on="station_id")
        .join(inputs.filter(pl.col("covid_period") == "normal").select("week_start", "presence_index",
                                                                        "summer_break_share"), on="week_start")
        .with_columns(period.alias("period")).drop_nulls("period")
    )


def shares(df: pl.DataFrame, bands=tuple(AGE_BANDS)) -> pl.DataFrame:
    """Share of member trips with a known age made by each age band, by zone, period and kind of day."""
    return (df.group_by("zone", "period", "day").agg(pl.col("known", *bands).sum())
            .with_columns(*[(pl.col(b) / pl.col("known")).alias(f"share_{b}") for b in bands])
            .sort("zone", "period", "day"))


def excess(s: pl.DataFrame, band: str, period: str, day: str) -> float:
    """How much larger the band's share is at campus-zone stations than at away-zone ones."""
    get = lambda zone: s.filter((pl.col("zone") == zone) & (pl.col("period") == period)  # noqa: E731
                                & (pl.col("day") == day))[f"share_{band}"][0]
    return get("campus") - get("away")


def student_campuses(df: pl.DataFrame) -> list[str]:
    """Universities whose campus-zone stations have a share of trips by riders aged 18-24 at least STUDENT_EXCESS
    above the away zone's on weekdays in session."""
    weekday = df.filter((pl.col("period") == "in session") & (pl.col("day") == "weekday"))
    away = weekday.filter(pl.col("zone") == "away").select(pl.col(STUDENT_BAND).sum() / pl.col("known").sum()).item()
    by_campus = (weekday.filter(pl.col("zone") == "campus").group_by("institution")
                 .agg((pl.col(STUDENT_BAND).sum() / pl.col("known").sum() - away).alias("excess")))
    return sorted(by_campus.filter(pl.col("excess") >= STUDENT_EXCESS)["institution"])


def factors(df: pl.DataFrame, campuses: list[str]) -> dict[str, float]:
    """The weekend factor of riders aged 18-24 at every campus-zone station and at those of the given
    universities, and how much of the excess is left in summer."""
    s = shares(df, (STUDENT_BAND,))
    e = {(period, day): excess(s, STUDENT_BAND, period, day)
         for period in ("in session", "summer") for day in ("weekday", "weekend")}
    student = shares(df.filter((pl.col("zone") == "away") | pl.col("institution").is_in(campuses)), (STUDENT_BAND,))
    return {"weekend_factor": e[("in session", "weekend")] / e[("in session", "weekday")],
            "weekend_factor_student_campuses": (excess(student, STUDENT_BAND, "in session", "weekend")
                                                / excess(student, STUDENT_BAND, "in session", "weekday")),
            "summer_excess_left_weekday": e[("summer", "weekday")] / e[("in session", "weekday")],
            "summer_excess_left_weekend": e[("summer", "weekend")] / e[("in session", "weekend")]}


def bootstrap(df: pl.DataFrame, campuses: list[str], rng: np.random.Generator,
              draws: int = BOOTSTRAP) -> dict[str, np.ndarray]:
    """The factors recomputed with stations and calendar months drawn with replacement: each station-month's
    counts are multiplied by the number of times its station and its month were drawn."""
    cells = (df.with_columns(pl.col("week_start").dt.strftime("%Y-%m").alias("month"))
             .group_by("station_id", "month", "zone", "institution", "period", "day")
             .agg(pl.col("known", STUDENT_BAND).sum()))
    stations, months = cells["station_id"].unique().sort(), cells["month"].unique().sort()
    s_idx = cells["station_id"].replace_strict(stations, range(len(stations))).to_numpy()
    m_idx = cells["month"].replace_strict(months, range(len(months))).to_numpy()
    out = []
    for _ in range(draws):
        weight = (np.bincount(rng.integers(0, len(stations), len(stations)), minlength=len(stations))[s_idx]
                  * np.bincount(rng.integers(0, len(months), len(months)), minlength=len(months))[m_idx])
        out.append(factors(cells.with_columns(pl.col("known", STUDENT_BAND) * pl.Series(weight)), campuses))
    return {k: np.array([d[k] for d in out]) for k in out[0]}


def corrected_shares(pieces: list[dict], signals: list[str], scaled: list[str], c: float) -> np.ndarray:
    """The joint fit with the weekend inputs of the scaled signals multiplied by c."""
    adjusted = [{**p, "x": p["x"] * c} if p["day"] == "weekend" and p["signal"] in scaled else p for p in pieces]
    return jp.fit(adjusted, signals)[:2]


def implications(table: pl.DataFrame, inputs_2019: pl.DataFrame, c: dict[str, float]) -> list[dict]:
    """Shares away and students away in weeks of 2019 as fitted, and with the weekend factor applied."""
    weeks = inputs_2019["week_start"].to_list()
    rows = []
    cases = [("Bluebikes alone", ["bikes"], ["bikes"]), ("subway and Bluebikes, Bluebikes corrected", jp.MAIN_SIGNALS,
             ["bikes"]), ("subway and Bluebikes, both corrected", jp.MAIN_SIGNALS, jp.MAIN_SIGNALS)]
    for label, signals, scaled in cases:
        pieces = jp.stacked(table, signals, jp.NO_MODEL)
        for name, value in {"as fitted": 1.0, **c}.items():
            summer, breaks = corrected_shares(pieces, signals, scaled, value)
            away = pm.away(inputs_2019, np.array([summer]), np.array([breaks]))[:, 0]
            row = {"case": label, "weekend_factor": name, "factor": value, "share_summer": float(summer),
                   "share_breaks": float(breaks), "mean_students_away_2019": float(away.mean())}
            for week, day in WEEKS.items():
                row[f"students_away_{week}"] = int(away[weeks.index(pl.Series([day]).str.to_date()[0])])
            rows.append(row)
    return rows


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    files = {m: k for m, k in bb.list_month_files().items() if MONTHS[0] <= m <= MONTHS[1]}
    counts = weekly(pl.concat([count_month(m, k) for m, k in files.items()]))
    counts.write_csv(PROCESSED / "bluebikes_member_ages_weekly.csv")
    inputs = pm.weekly_inputs(read("students_away_weekly.csv"), read("student_presence_weekly.csv",
                                                                      infer_schema_length=None))
    df = labelled(counts, read("bluebikes_station_campus.csv", schema_overrides={"station_id": pl.Utf8}), inputs)
    s = shares(df)
    s.with_columns(pl.selectors.starts_with("share_").round(4)).write_csv(PROCESSED / "weekend_riders_shares.csv")

    campuses = student_campuses(df)
    point, draws = factors(df, campuses), bootstrap(df, campuses, np.random.default_rng(0))
    rows = [{"what": name, "estimate": value, "low": float(np.percentile(draws[name], 2.5)),
             "high": float(np.percentile(draws[name], 97.5))} for name, value in point.items()]
    year = pl.col("week_start").dt.year()
    for y in range(2016, 2020):
        by_year = factors(df.filter(year == y), campuses)
        rows += [{"what": f"{name}_{y}", "estimate": by_year[name], "low": None, "high": None}
                 for name in ("weekend_factor", "weekend_factor_student_campuses")]
    get = lambda name: next(r for r in rows if r["what"] == name)  # noqa: E731
    zone, student = get("weekend_factor"), get("weekend_factor_student_campuses")
    c = {"every campus-zone station": zone["estimate"], "student-dominated campuses": student["estimate"],
         "student-dominated campuses, low end": student["low"], "student-dominated campuses, high end": student["high"]}
    table = read("presence_signals_weekly.csv")
    effects = implications(table, inputs.filter(pl.col("week_start").dt.year() == 2019), c)
    out = pl.concat([pl.DataFrame(rows).with_columns(pl.lit("factor").alias("table")),
                     pl.DataFrame(effects).with_columns(pl.lit("implication").alias("table"))], how="diagonal")
    out.with_columns(pl.selectors.float().round(4)).write_csv(PROCESSED / "weekend_riders.csv")
    with pl.Config(tbl_rows=40, tbl_cols=-1, tbl_width_chars=250, fmt_str_lengths=50):
        print(s.select("zone", "period", "day", "known", *[pl.col(f"share_{b}").round(3) for b in AGE_BANDS]))
        print("Student-dominated campuses:", ", ".join(campuses))
        print(pl.DataFrame(rows).with_columns(pl.selectors.float().round(3)))
        print(pl.DataFrame(effects).with_columns(pl.selectors.float().round(3)))


if __name__ == "__main__":
    main()
