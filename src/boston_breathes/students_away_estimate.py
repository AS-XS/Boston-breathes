"""Step 3: how many resident college students are actually away each week.

students_away.py bounds the number each week: at least the students whose
residence halls are closed, at most every resident college student not living
with their parents whose school is out of session. This step places the
number inside that range using travel records.

Activity near a campus falls when its university is out of session
(model_comparison.py, campus_panel.py), for two reasons: students who leave
the area, and students who stay but no longer travel to classes. Weekday
entries lose both; weekend entries, when there are no classes, lose mostly
the first. So the weekend loss divided by the weekday loss estimates the
share of students out of session who are away:
  * summer: across the study area's gated stations, the change in entries
    from full in-session weeks to full summer weeks (2022-2025) is regressed
    on the station's share of student riders in the MBTA passenger survey,
    once for weekday and once for weekend entries (as in the survey check of
    evaluate_presence.py). Two survey measures stand for student riders:
    riders aged 18-25, and trips between home and school;
  * breaks: the pooled campus model (campus_panel.py) is fitted on weekday
    and on weekend entries at campus stations, with all universities on
    assumed calendars and with verified calendars only; its effect comes
    from weeks when one university is out of session and others are not,
    mostly breaks and the edges of terms.
For each, the lower and the higher of the two estimates, kept between 0 and
1, give a low and a high share away.

Students away in a week = resident college students x (the summer share
away x the share of them on summer break + the break share away x the share
otherwise out of session), kept inside the range of students_away.py. Only
weeks outside the COVID-19 period are estimated: students were sent home in
the spring 2020 lockdown, and 2020-21 was taught remotely.

Assumptions:
  * students who stay keep their weekend travel, and students who leave stop
    all of it;
  * students are the same proportion of a survey measure's riders on
    weekends as on weekdays. They are probably a larger proportion of weekend
    riders, since commuters ride less on weekends, which makes the ratio
    overstate the share away;
  * trips between home and school include school children, who stop school
    trips in summer without leaving, which makes that ratio understate it;
  * subway riders stand for all resident college students.

Outputs in data/processed/:
  * students_away_shares.csv     the weekday and weekend estimates, and the share away they give
  * students_away_estimate.csv   the weekly low and high estimate of students away and of the population
"""

import numpy as np
import polars as pl

from boston_breathes import campus_panel as cp
from boston_breathes import evaluate_presence as ev
from boston_breathes.paths import PROCESSED

DAYS = {"weekday": "weekday_entries", "weekend": "weekend_entries"}
SUMMER_PROXIES = {"age_18_25_share": "riders aged 18-25", "school_trip_share": "trips between home and school"}
BREAK_SPECS = {cp.ALL: False, cp.VERIFIED_ONLY: True}


def day_entries(by_station: pl.DataFrame, day: str) -> pl.DataFrame:
    """Weekly station entries of one kind of day, under the name the shared functions read."""
    return by_station.select("week_start", "stop_id", pl.col(DAYS[day]).alias("entries"), "complete_week",
                             "closed_days")


def slope(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
    """Least-squares slope of y on x with an intercept, and its robust standard error."""
    design = np.column_stack([np.ones(len(x)), x])
    beta = np.linalg.lstsq(design, y, rcond=None)[0]
    resid = y - design @ beta
    bread = np.linalg.inv(design.T @ design)
    return float(beta[1]), float(np.sqrt((bread @ (design.T * resid ** 2) @ design @ bread)[1, 1]))


def summer_rows(by_station: pl.DataFrame, presence: pl.DataFrame, survey: pl.DataFrame) -> list[dict]:
    rows = []
    for day in DAYS:
        change = ev.station_summer_change(day_entries(by_station, day), presence).join(
            survey.filter(pl.col("in_study_area")), on="stop_id")
        for proxy, label in SUMMER_PROXIES.items():
            coef, se = slope(change["log_summer_change"].to_numpy(), change[proxy].to_numpy())
            rows.append({"period": "summer", "method": f"across stations, {label}", "day": day,
                         "coef": coef, "se": se, "n": len(change)})
    return rows


def break_rows(by_station: pl.DataFrame, stations: pl.DataFrame, universities: pl.DataFrame,
               cal: pl.DataFrame) -> list[dict]:
    rows = []
    for day in DAYS:
        units = cp.mbta_units(day_entries(by_station, day), stations, universities)
        for spec, verified in BREAK_SPECS.items():
            df = cp.panel(units, cal, "MBTA", verified_only=verified)
            coef, se = cp.fit(df, "MBTA", ["session_share"])["session_share"]
            rows.append({"period": "breaks", "method": f"campus stations, {spec}", "day": day,
                         "coef": coef, "se": se, "n": len(df)})
    return rows


def shares(parts: pl.DataFrame) -> pl.DataFrame:
    """Share away = weekend over weekday estimate, kept between 0 and 1, for each period and method."""
    return (parts.pivot(on="day", index=["period", "method"], values="coef")
            .with_columns((pl.col("weekend") / pl.col("weekday")).clip(0, 1).alias("share_away")))


def weekly(away: pl.DataFrame, presence: pl.DataFrame, share: pl.DataFrame) -> pl.DataFrame:
    """Low and high estimate of students away each week, inside the range of students_away.py."""
    level = lambda period, f: f(share.filter(pl.col("period") == period)["share_away"])  # noqa: E731
    out_of_session = 1 - pl.col("presence_index")
    summer = pl.min_horizontal("summer_break_share", out_of_session)
    other = (out_of_session - summer).clip(lower_bound=0)
    def estimate(f) -> pl.Expr:
        away = pl.col("resident_students") * (level("summer", f) * summer + level("breaks", f) * other)
        return away.clip(pl.col("min_students_away"), pl.col("max_students_away")).round(0).cast(pl.Int64)
    normal = pl.col("covid_period") == "normal"
    return (
        away.join(presence.select("week_start", "summer_break_share"), on="week_start", how="left")
        .with_columns(
            pl.when(normal).then(estimate(min)).alias("est_students_away_low"),
            pl.when(normal).then(estimate(max)).alias("est_students_away_high"),
        )
        .with_columns(
            (pl.col("census_population") - pl.col("est_students_away_high")).alias("est_population_low"),
            (pl.col("census_population") - pl.col("est_students_away_low")).alias("est_population_high"),
        )
        .select("week_start", "covid_period", "resident_students", "presence_index", "summer_break_share",
                "min_students_away", "est_students_away_low", "est_students_away_high", "max_students_away",
                "census_population", "est_population_low", "est_population_high", "population_extrapolated")
        .sort("week_start")
    )


def main() -> None:
    ids = {"unitid": pl.Utf8}
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    by_station = read("mbta_weekly_by_station.csv")
    presence = read("student_presence_weekly.csv", infer_schema_length=None)
    cal = cp.calendars(read("student_presence_by_institution.csv", schema_overrides=ids),
                       read("academic_weekly.csv", schema_overrides=ids),
                       read("academic_calendar.csv", schema_overrides=ids), presence)
    parts = pl.DataFrame(
        summer_rows(by_station, presence, read("mbta_survey_by_station.csv"))
        + break_rows(by_station, read("mbta_stations.csv"), read("universities.csv", schema_overrides=ids), cal)
    )
    share = shares(parts)
    share.join(parts.pivot(on="day", index=["period", "method"], values="se"), on=["period", "method"],
               suffix="_se").with_columns(pl.selectors.numeric().round(4)).write_csv(
        PROCESSED / "students_away_shares.csv")
    out = weekly(read("students_away_weekly.csv"), presence, share)
    out.write_csv(PROCESSED / "students_away_estimate.csv")

    for r in share.iter_rows(named=True):
        print(f"{r['period']:7s} {r['method']:60s} weekday {r['weekday']:+.3f}, weekend {r['weekend']:+.3f} "
              f"-> share away {r['share_away']:.2f}")
    y = out.filter(pl.col("week_start").dt.year() == 2019)
    for label, week in (("summer", y.filter(pl.col("summer_break_share") >= 0.9).head(1)),
                        ("Thanksgiving", y.filter(pl.col("week_start") == pl.date(2019, 11, 25)))):
        r = week.row(0, named=True)
        print(f"2019 {label} ({r['week_start']}): range {r['min_students_away']:,}-{r['max_students_away']:,}, "
              f"estimate {r['est_students_away_low']:,}-{r['est_students_away_high']:,}")


if __name__ == "__main__":
    main()
