"""The weekly resident population of the study area, with travel records as evidence.

Population model (what is predicted):
  population[week] = Census resident population - students away[week]
  students away    = resident college students x (summer share away x share of students on summer break
                                                  + break share away x share otherwise out of session),
kept inside the range of students_away.py: at least the students whose
residence halls are closed, at most every resident college student not
living with their parents whose school is out of session. The shares away
(of the students out of session, the share who leave the area) are the
unknowns; everything else comes from the calendars, enrollment and the
Census.

Measurement model (how travel reflects the population): MBTA gated stations
differ in how many of their riders are college students (the passenger
survey's share of riders with a Semester Pass, which is sold through
universities). When students are missing, stations with more student riders
lose more entries. Each week, the slope of station entries (logarithm, after
a level for each station in each year and for each week) on the station's
student share measures how many student riders are missing:
  * on weekdays, every student out of session, whether they left or stay
    without classes:   weekday slope[week] = c + k x out of session[week]
  * on weekends, when there are no classes, only those who left:
                       weekend slope[week] = c' + k x share away[week]
with the same k: students are the same proportion of a station's riders on
weekends as on weekdays. The weekday slopes fix k from the calendar; the
weekend slopes then say how many students left. Station-weeks with a closed
day (mbta.py) are left out.

Each calendar input enters as its deviation from its mean over the same
year's weeks, since travel is measured against each station's level for the
year: only changes within a year are seen, and a part of a year needs no
other level.

Population versions compared:
  * no change: nobody leaves;
  * dorm closures only: the upper line of students_away.py;
  * every student out of session not living with parents: the lower line;
  * one share away for every week out of session;
  * summer and breaks: one share for summer, one for other breaks;
  * each kind of break: summer, Thanksgiving (November), winter (December
    and January) and spring (February to April) apart.
Each is scored by the weekend slopes it predicts for weeks it was not fitted
on, against nobody leaving, with three survey measures of student riders
(Semester Pass, riders aged 18-25, trips between home and school):
  * validation, forward in time: 2023 from 2022, 2024 from 2022-2023, 2025
    from 2022-2024 (the years the survey pools), and 2022-2025 from
    2016-2019 across the COVID-19 gap;
  * test: January to September 2026 from 2022-2025, scored once, after the
    measure and version were chosen on the validation years;
  * within a period: each calendar month from the other weeks (with the week
    on either side), for 2022-2025 and 2016-2019.
The validation years chose the Semester Pass measure and the version in which
every student out of session not living with their parents leaves: it had
the lowest error in every validation year, while the fitted shares moved with
the years they were fitted on. All of this assumes students are the same
share of weekend riders as of weekday riders; weekend_riders.py finds them a
larger one near campuses, which makes every version predict best at shares
that large by the same factor. The estimate (population_estimate.py)
therefore takes the shares for summer and breaks fitted on the subway and
Bluebikes together (joint_population.py), which are close to this version,
and divides them by that factor. The intervals of the shares here come from
refits on calendar months drawn with replacement (a block bootstrap); the
known-truth test of population_accuracy.py found such intervals too narrow.

Assumptions: students are the same proportion of a station's riders on
weekends as on weekdays (they are a larger one near campuses, since
commuters ride less on weekends, so the shares away here are overstated;
see weekend_riders.py); students who stay keep their
weekend travel and those who leave stop it; the survey measure is
proportional to student riders across stations; subway riders stand for all
resident college students; the shares away are the same every year.

Outputs in data/processed/:
  * population_model_forward.csv     each version's error on later years (validation, long horizon, test),
                                     and the shares learned from the earlier years
  * population_model_comparison.csv  held-out error of each version on months within a period, and how closely
                                     the weekly slopes follow the calendar, by survey measure and period
  * population_model_shares.csv      k and the shares away of each estimated version, with 95% intervals
"""

import numpy as np
import polars as pl

from boston_breathes import campus_panel as cp
from boston_breathes.paths import PROCESSED

MEASURES = {"semester_pass_share": "riders with a Semester Pass", "age_18_25_share": "riders aged 18-25",
            "school_trip_share": "trips between home and school"}
PERIODS = {"2022-2025": (2022, 2025), "2016-2019": (2016, 2019)}
# Chosen on the validation years (the lowest error in each of them, with the largest gain of the three measures),
# before the test year was scored, under the equal-share assumption; the estimate corrects the fitted summer-and-breaks
# shares for students' larger share of weekend riders (population_estimate.py).
MAIN_MEASURE, MAIN_PERIOD = "semester_pass_share", "2022-2025"
MAIN_VERSION, BAND_VERSION = "every student out of session not living with parents", "summer and breaks"
FIXED = {"no change": None, "dorm closures only": "min_share",
         "every student out of session not living with parents": "max_share"}
ESTIMATED = {"one share away": ["out"], "summer and breaks": ["summer", "breaks"],
             "each kind of break": ["summer", "thanksgiving", "winter", "spring"]}
VERSIONS = [*FIXED, *ESTIMATED]
BREAK_MONTHS = {"thanksgiving": [11], "winter": [12, 1], "spring": [2, 3, 4]}  # students are out of session
# outside summer only from November to April
REGRESSORS = ["out", "summer", "breaks", *BREAK_MONTHS, "min_share", "max_share"]
VALIDATION_YEARS = [2023, 2024, 2025]  # each predicted from the years before it, from 2022 on
TEST_YEARS = (2026, 2026)  # January to September 2026: scored once, after the version was chosen
BUFFER_WEEKS = 1
BOOTSTRAP = 1000


def weekly_inputs(away: pl.DataFrame, presence: pl.DataFrame) -> pl.DataFrame:
    """Each week's share of resident students out of session, split into summer and kinds of break, and the
    range of students away as shares of resident students."""
    out = 1 - pl.col("presence_index")
    month = pl.col("week_start").dt.month()
    return (
        away.join(presence.select("week_start", "summer_break_share"), on="week_start", how="left")
        .with_columns(out.alias("out"), pl.min_horizontal("summer_break_share", out).alias("summer"))
        .with_columns((pl.col("out") - pl.col("summer")).clip(lower_bound=0).alias("breaks"))
        .with_columns(*[pl.when(month.is_in(m)).then("breaks").otherwise(0.0).alias(k) for k, m in BREAK_MONTHS.items()],
                      (pl.col("min_students_away") / pl.col("resident_students")).alias("min_share"),
                      (pl.col("max_students_away") / pl.col("resident_students")).alias("max_share"))
    )


def station_weeks(by_station: pl.DataFrame, survey: pl.DataFrame, inputs: pl.DataFrame,
                  years: tuple[int, int], periods: tuple[str, ...] = ("normal",)) -> pl.DataFrame:
    """Study-area station-weeks with no closed day and entries on both kinds of day, in the given COVID-19
    periods (by default outside COVID-19)."""
    normal = inputs.filter(pl.col("covid_period").is_in(periods)).select("week_start")
    return (
        by_station.filter(pl.col("complete_week") & (pl.col("closed_days") == 0)
                          & (pl.col("weekday_entries") > 0) & (pl.col("weekend_entries") > 0)
                          & pl.col("week_start").dt.year().is_between(*years))
        .join(normal, on="week_start")
        .join(survey.filter(pl.col("in_study_area")).select("stop_id", *MEASURES), on="stop_id")
        .with_columns(pl.col("week_start").dt.year().alias("year"))
    )


def weekly_slopes(sw: pl.DataFrame, measure: str) -> pl.DataFrame:
    """For each week, the slope across stations of log entries (after station-year and week levels) on the
    station's share of student riders, on weekdays and on weekends."""
    groups = [cp.codes(sw, "stop_id", "year"), cp.codes(sw, "week_start")]
    y = cp.demean(np.log(sw.select("weekday_entries", "weekend_entries").to_numpy().astype(float)), groups)
    df = sw.select("week_start", measure).with_columns(pl.Series("weekday", y[:, 0]), pl.Series("weekend", y[:, 1]))
    x = pl.col(measure) - pl.col(measure).mean()
    slope = lambda c: ((x * pl.col(c)).sum() / (x * x).sum()).alias(c)  # noqa: E731
    return df.group_by("week_start").agg(slope("weekday"), slope("weekend")).sort("week_start")


def deviations(df: pl.DataFrame) -> pl.DataFrame:
    """Each calendar input minus its mean over the weeks of the same calendar year in the data. Travel is
    measured against each station's level for the year, so it shows only changes within a year, and the
    inputs are put on the same footing; a part of a year (2026 to September) then needs no other level."""
    year = pl.col("week_start").dt.year()
    return df.with_columns(*[(pl.col(c) - pl.col(c).mean().over(year)).alias(f"{c}_dev") for c in REGRESSORS])


def design(df: pl.DataFrame, version: str) -> tuple[np.ndarray, np.ndarray]:
    """Weekday slopes stacked over weekend slopes. Columns: the two intercepts, the column of k (weekday: out of
    session; weekend: the fixed share away of a fixed version), then for an estimated version one weekend
    column per part, whose coefficient is k x the share away of that part. Inputs are deviations from their
    yearly means."""
    n = len(df)
    zero, one = np.zeros(n), np.ones(n)
    col = lambda c: df[f"{c}_dev"].to_numpy()  # noqa: E731
    weekend_k = zero if version not in FIXED or FIXED[version] is None else col(FIXED[version])
    cols = [np.r_[one, zero], np.r_[zero, one], np.r_[col("out"), weekend_k]]
    cols += [np.r_[zero, col(part)] for part in ESTIMATED.get(version, [])]
    return np.column_stack(cols), np.r_[df["weekday"].to_numpy(), df["weekend"].to_numpy()]


def fit(df: pl.DataFrame, version: str) -> np.ndarray:
    x, y = design(df, version)
    return np.linalg.lstsq(x, y, rcond=None)[0]


def shares(beta: np.ndarray) -> np.ndarray:
    """Share away of each part of an estimated version."""
    return beta[3:] / beta[2]


def held_out_weekend(df: pl.DataFrame, version: str) -> np.ndarray:
    """Weekend slopes of each calendar month predicted from a fit on the other weeks, without the week on
    either side of the month."""
    weeks = df["week_start"].to_numpy()
    blocks = df["week_start"].dt.strftime("%Y-%m").to_numpy()
    buffer = np.timedelta64(7 * BUFFER_WEEKS, "D")
    pred = np.empty(len(df))
    for block in np.unique(blocks):
        test = blocks == block
        near = (weeks >= weeks[test].min() - buffer) & (weeks <= weeks[test].max() + buffer)
        beta = fit(df.filter(pl.Series(~near)), version)
        x, _ = design(df.filter(pl.Series(test)), version)
        pred[test] = x[test.sum():] @ beta
    return pred


def forward(train: pl.DataFrame, test: pl.DataFrame, version: str) -> tuple[np.ndarray, np.ndarray]:
    """Weekend slopes of later weeks predicted from a fit on earlier ones, and the fitted coefficients."""
    beta = fit(train, version)
    x, _ = design(test, version)
    return x[len(test):] @ beta, beta


def bootstrap(df: pl.DataFrame, version: str, rng: np.random.Generator, draws: int = BOOTSTRAP) -> np.ndarray:
    """Coefficients refitted on calendar months drawn with replacement."""
    blocks = df["week_start"].dt.strftime("%Y-%m")
    by_block = {b: df.filter(blocks == b) for b in blocks.unique().sort()}
    names = list(by_block)
    return np.array([fit(pl.concat([by_block[names[i]] for i in rng.integers(0, len(names), len(names))]), version)
                     for _ in range(draws)])


def analyse(sw: pl.DataFrame, inputs: pl.DataFrame, measure: str, period: str,
            rng: np.random.Generator) -> tuple[list[dict], list[dict], pl.DataFrame]:
    df = deviations(weekly_slopes(sw, measure).join(inputs, on="week_start"))
    n = len(df)
    base = {"measure": measure, "period": period, "weeks": n}
    comparison = [{**base, "version": "weekly slopes", "metric": f"corr_{day}_slope_vs_out_of_session",
                   "value": float(np.corrcoef(df[day], df["out"])[0, 1])} for day in ("weekday", "weekend")]
    rmse = {}
    for version in VERSIONS:
        rmse[version] = float(np.sqrt(((df["weekend"].to_numpy() - held_out_weekend(df, version)) ** 2).mean()))
        comparison += [{**base, "version": version, "metric": "held_out_rmse", "value": rmse[version]},
                       {**base, "version": version, "metric": "rmse_change_pct",
                        "value": (rmse[version] / rmse["no change"] - 1) * 100}]
    share_rows = []
    for version, parts in ESTIMATED.items():
        beta = fit(df, version)
        draws = bootstrap(df, version, rng)
        boot_shares = draws[:, 3:] / draws[:, 2:3]
        for name, value, boot in [("k", beta[2], draws[:, 2]),
                                  *zip(parts, shares(beta), boot_shares.T)]:
            low, high = np.percentile(boot, [2.5, 97.5])
            share_rows.append({**base, "version": version, "parameter": name, "estimate": float(value),
                               "low": float(low), "high": float(high)})
    return comparison, share_rows, df


def away(inputs: pl.DataFrame, summer: np.ndarray, breaks: np.ndarray) -> np.ndarray:
    """Students away each week (rows) for each pair of shares (columns), shares kept between 0 and 1 and the
    result inside the range of students_away.py."""
    s, b = np.clip(summer, 0, 1)[None, :], np.clip(breaks, 0, 1)[None, :]
    col = lambda c: inputs[c].to_numpy().astype(float)[:, None]  # noqa: E731
    est = col("resident_students") * (s * col("summer") + b * col("breaks"))
    return np.clip(est, col("min_students_away"), col("max_students_away"))


def version_share(version: str) -> pl.Expr:
    """A fixed version's share of resident students away each week."""
    column = FIXED[version]
    return pl.lit(0.0) if column is None else pl.col(column)


def span(years: list[int]) -> str:
    return f"{min(years)}" if min(years) == max(years) else f"{min(years)}-{max(years)}"


def forward_rows(splits: list[tuple[str, pl.DataFrame, pl.DataFrame]], measure: str) -> list[dict]:
    """Each version fitted on earlier years and scored on the weekend slopes of later ones, against nobody
    leaving; for the estimated versions, also the shares learned from the earlier years."""
    rows = []
    year = lambda df: df["week_start"].dt.year().unique().sort().to_list()  # noqa: E731
    for evaluation, train, test in splits:
        base = {"measure": measure, "evaluation": evaluation, "train_years": span(year(train)),
                "test_years": span(year(test)), "weeks": len(test)}
        rmse = {}
        for version in VERSIONS:
            pred, beta = forward(train, test, version)
            rmse[version] = float(np.sqrt(((test["weekend"].to_numpy() - pred) ** 2).mean()))
            rows += [{**base, "version": version, "metric": "rmse", "value": rmse[version]},
                     {**base, "version": version, "metric": "rmse_change_pct",
                      "value": (rmse[version] / rmse["no change"] - 1) * 100}]
            rows += [{**base, "version": version, "metric": f"share_{part}", "value": float(v)}
                     for part, v in zip(ESTIMATED.get(version, []), shares(beta))]
    return rows


def validation_splits(df: pl.DataFrame) -> list[tuple[str, pl.DataFrame, pl.DataFrame]]:
    """A growing window: each validation year predicted from every year before it in the data."""
    year = pl.col("week_start").dt.year()
    return [("validation", df.filter(year < y), df.filter(year == y)) for y in VALIDATION_YEARS]


def build(by_station: pl.DataFrame, survey: pl.DataFrame, away_weekly: pl.DataFrame,
          presence: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    inputs = weekly_inputs(away_weekly, presence)
    rng = np.random.default_rng(0)
    comparison, share_rows, forward_out, slopes_by = [], [], [], {}
    for period, years in PERIODS.items():
        sw = station_weeks(by_station, survey, inputs, years)
        for measure in MEASURES:
            c, s, slopes = analyse(sw, inputs, measure, period, rng)
            comparison += c
            share_rows += s
            slopes_by[(measure, period)] = slopes
    test_sw = station_weeks(by_station, survey, inputs, TEST_YEARS)
    for measure in MEASURES:
        recent, early = slopes_by[(measure, "2022-2025")], slopes_by[(measure, "2016-2019")]
        test = deviations(weekly_slopes(test_sw, measure).join(inputs, on="week_start"))
        forward_out += forward_rows([*validation_splits(recent), ("long horizon", early, recent),
                                     ("test", recent, test)], measure)
    return pl.DataFrame(comparison), pl.DataFrame(share_rows), pl.DataFrame(forward_out)


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    comparison, share_rows, forward_out = build(
        read("mbta_weekly_by_station.csv"), read("mbta_survey_by_station.csv"), read("students_away_weekly.csv"),
        read("student_presence_weekly.csv", infer_schema_length=None))
    forward_out.with_columns(pl.col("value").round(4)).write_csv(PROCESSED / "population_model_forward.csv")
    comparison.with_columns(pl.col("value").round(4)).write_csv(PROCESSED / "population_model_comparison.csv")
    share_rows.with_columns(pl.col("estimate", "low", "high").round(4)).write_csv(
        PROCESSED / "population_model_shares.csv")

    change = forward_out.filter(pl.col("metric") == "rmse_change_pct").with_columns(
        (pl.col("evaluation") + " " + pl.col("test_years")).alias("split"))
    for measure in MEASURES:
        print(f"{MEASURES[measure]}: change in weekend-slope error against nobody leaving")
        table = change.filter(pl.col("measure") == measure).pivot(on="split", index="version", values="value")
        print("  " + " | ".join(f"{c:>18s}" for c in table.columns[1:]))
        for row in table.iter_rows():
            print(f"  {row[0][:40]:40s} " + " | ".join(f"{v:+17.1f}%" for v in row[1:]))
    band = share_rows.filter((pl.col("measure") == MAIN_MEASURE) & (pl.col("period") == MAIN_PERIOD)
                             & (pl.col("version") == BAND_VERSION) & (pl.col("parameter") != "k"))
    print(f"Chosen: {MAIN_VERSION}; {BAND_VERSION} on the subway alone: " + ", ".join(
        f"{p} {e:.2f} ({lo:.2f}-{hi:.2f})" for p, e, lo, hi in band.select("parameter", "estimate", "low", "high").rows()))


if __name__ == "__main__":
    main()
