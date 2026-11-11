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

Population versions compared, each by the weekend slopes it predicts for
calendar months held out in turn (with the week on either side), after
fitting on the other weeks:
  * no change: nobody leaves;
  * dorm closures only: the upper line of students_away.py;
  * every student out of session not living with parents: the lower line;
  * one share away for every week out of session;
  * summer and breaks: one share for summer, one for other breaks;
  * each kind of break: summer, Thanksgiving (November), winter (December
    and January) and spring (February to April) apart.
Each is fitted with three survey measures of student riders (Semester Pass,
riders aged 18-25, trips between home and school) on 2022-2025, the years
the survey pools, and on 2016-2019 as a check. Intervals for the shares come
from resampling calendar months with replacement (a block bootstrap).

The population estimate uses the summer-and-breaks version with the Semester
Pass measure on 2022-2025, the same shares for every year outside the
COVID-19 period, and a 95% band from the bootstrap. Each week of 2022-2025 is
also read directly off travel: its weekend slope, through the fitted c' and
k, gives the share of students away that week.

Assumptions: students are the same proportion of a station's riders on
weekends as on weekdays (if they are a larger one, since commuters ride less
on weekends, the shares away are overstated); students who stay keep their
weekend travel and those who leave stop it; the survey measure is
proportional to student riders across stations; subway riders stand for all
resident college students; the shares away are the same every year.

Outputs in data/processed/:
  * population_model_comparison.csv  held-out error of each version, and how closely the weekly slopes follow
                                     the calendar, by survey measure and period
  * population_model_shares.csv      k and the shares away of each estimated version, with 95% intervals
  * population_estimate.csv          the weekly population estimate with its 95% band, and the share away read
                                     off each week's travel
"""

import numpy as np
import polars as pl

from boston_breathes import campus_panel as cp
from boston_breathes.paths import PROCESSED

MEASURES = {"semester_pass_share": "riders with a Semester Pass", "age_18_25_share": "riders aged 18-25",
            "school_trip_share": "trips between home and school"}
PERIODS = {"2022-2025": (2022, 2025), "2016-2019": (2016, 2019)}
MAIN_MEASURE, MAIN_PERIOD, MAIN_VERSION = "semester_pass_share", "2022-2025", "summer and breaks"
FIXED = {"no change": None, "dorm closures only": "min_share",
         "every student out of session not living with parents": "max_share"}
ESTIMATED = {"one share away": ["out"], "summer and breaks": ["summer", "breaks"],
             "each kind of break": ["summer", "thanksgiving", "winter", "spring"]}
VERSIONS = [*FIXED, *ESTIMATED]
BREAK_MONTHS = {"thanksgiving": [11], "winter": [12, 1], "spring": [2, 3, 4]}  # students are out of session
# outside summer only from November to April
BUFFER_WEEKS = 1
BOOTSTRAP = 1000
SMOOTH_WEEKS = 5  # centred moving average of the share away read off travel


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
                  years: tuple[int, int]) -> pl.DataFrame:
    """Study-area station-weeks with no closed day and entries on both kinds of day, outside COVID-19."""
    normal = inputs.filter(pl.col("covid_period") == "normal").select("week_start")
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


def design(df: pl.DataFrame, version: str) -> tuple[np.ndarray, np.ndarray]:
    """Weekday slopes stacked over weekend slopes. Columns: the two intercepts, the column of k (weekday: out of
    session; weekend: the fixed share away of a fixed version), then for an estimated version one weekend
    column per part, whose coefficient is k x the share away of that part."""
    n = len(df)
    zero, one = np.zeros(n), np.ones(n)
    weekend_k = zero if version not in FIXED or FIXED[version] is None else df[FIXED[version]].to_numpy()
    cols = [np.r_[one, zero], np.r_[zero, one], np.r_[df["out"].to_numpy(), weekend_k]]
    cols += [np.r_[zero, df[part].to_numpy()] for part in ESTIMATED.get(version, [])]
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


def bootstrap(df: pl.DataFrame, version: str, rng: np.random.Generator, draws: int = BOOTSTRAP) -> np.ndarray:
    """Coefficients refitted on calendar months drawn with replacement."""
    blocks = df["week_start"].dt.strftime("%Y-%m")
    by_block = {b: df.filter(blocks == b) for b in blocks.unique().sort()}
    names = list(by_block)
    return np.array([fit(pl.concat([by_block[names[i]] for i in rng.integers(0, len(names), len(names))]), version)
                     for _ in range(draws)])


def analyse(sw: pl.DataFrame, inputs: pl.DataFrame, measure: str, period: str,
            rng: np.random.Generator) -> tuple[list[dict], list[dict], pl.DataFrame, dict[str, np.ndarray]]:
    df = weekly_slopes(sw, measure).join(inputs, on="week_start")
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
    share_rows, draws = [], {}
    for version, parts in ESTIMATED.items():
        beta = fit(df, version)
        draws[version] = bootstrap(df, version, rng)
        boot_shares = draws[version][:, 3:] / draws[version][:, 2:3]
        for name, value, boot in [("k", beta[2], draws[version][:, 2]),
                                  *zip(parts, shares(beta), boot_shares.T)]:
            low, high = np.percentile(boot, [2.5, 97.5])
            share_rows.append({**base, "version": version, "parameter": name, "estimate": float(value),
                               "low": float(low), "high": float(high)})
    return comparison, share_rows, df, draws


def away(inputs: pl.DataFrame, summer: np.ndarray, breaks: np.ndarray) -> np.ndarray:
    """Students away each week (rows) for each pair of shares (columns), shares kept between 0 and 1 and the
    result inside the range of students_away.py."""
    s, b = np.clip(summer, 0, 1)[None, :], np.clip(breaks, 0, 1)[None, :]
    col = lambda c: inputs[c].to_numpy().astype(float)[:, None]  # noqa: E731
    est = col("resident_students") * (s * col("summer") + b * col("breaks"))
    return np.clip(est, col("min_students_away"), col("max_students_away"))


def estimate(inputs: pl.DataFrame, beta: np.ndarray, draws: np.ndarray, slopes: pl.DataFrame) -> pl.DataFrame:
    """Weekly students away and population: the estimate, its 95% band, and the share away read off travel."""
    point = away(inputs, np.array([shares(beta)[0]]), np.array([shares(beta)[1]]))[:, 0]
    boot = away(inputs, draws[:, 3] / draws[:, 2], draws[:, 4] / draws[:, 2])
    low, high = np.percentile(boot, [2.5, 97.5], axis=1)
    normal = pl.col("covid_period") == "normal"
    rounded = lambda v, name: pl.when(normal).then(pl.Series(name, v).round(0).cast(pl.Int64))  # noqa: E731
    travel = slopes.select("week_start", ((pl.col("weekend") - beta[1]) / beta[2]).alias("travel_share_away"))
    return (
        inputs.with_columns(rounded(point, "a").alias("students_away"), rounded(low, "l").alias("students_away_low"),
                            rounded(high, "h").alias("students_away_high"))
        .with_columns((pl.col("census_population") - pl.col("students_away")).alias("population"),
                      (pl.col("census_population") - pl.col("students_away_high")).alias("population_low"),
                      (pl.col("census_population") - pl.col("students_away_low")).alias("population_high"))
        .join(travel, on="week_start", how="left")
        .with_columns((pl.col("resident_students") * pl.col("travel_share_away")).round(0).cast(pl.Int64)
                      .alias("travel_students_away"))
        .with_columns(pl.col("travel_students_away").cast(pl.Float64)
                      .rolling_mean(SMOOTH_WEEKS, center=True, min_samples=SMOOTH_WEEKS // 2 + 1)
                      .round(0).cast(pl.Int64).alias("travel_students_away_smooth"))
        .select("week_start", "covid_period", "census_population", "resident_students", "presence_index",
                "summer_break_share", "min_students_away", "students_away", "students_away_low",
                "students_away_high", "max_students_away", "population", "population_low", "population_high",
                "population_extrapolated", "travel_students_away", "travel_students_away_smooth")
        .sort("week_start")
    )


def build(by_station: pl.DataFrame, survey: pl.DataFrame, away_weekly: pl.DataFrame,
          presence: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    inputs = weekly_inputs(away_weekly, presence)
    rng = np.random.default_rng(0)
    comparison, share_rows, main = [], [], None
    for period, years in PERIODS.items():
        sw = station_weeks(by_station, survey, inputs, years)
        for measure in MEASURES:
            c, s, slopes, draws = analyse(sw, inputs, measure, period, rng)
            comparison += c
            share_rows += s
            if (measure, period) == (MAIN_MEASURE, MAIN_PERIOD):
                main = (fit(slopes, MAIN_VERSION), draws[MAIN_VERSION], slopes)
    return pl.DataFrame(comparison), pl.DataFrame(share_rows), estimate(inputs, *main)


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    comparison, share_rows, weekly = build(read("mbta_weekly_by_station.csv"), read("mbta_survey_by_station.csv"),
                                           read("students_away_weekly.csv"),
                                           read("student_presence_weekly.csv", infer_schema_length=None))
    comparison.with_columns(pl.col("value").round(4)).write_csv(PROCESSED / "population_model_comparison.csv")
    share_rows.with_columns(pl.col("estimate", "low", "high").round(4)).write_csv(
        PROCESSED / "population_model_shares.csv")
    weekly.write_csv(PROCESSED / "population_estimate.csv")

    for (measure, period), part in comparison.group_by("measure", "period", maintain_order=True):
        print(f"{MEASURES[measure]}, {period}:")
        corr = part.filter(pl.col("version") == "weekly slopes")
        print("  weekly slopes vs out of session: " + ", ".join(
            f"{m.split('_')[1]} {v:+.2f}" for m, v in corr.select("metric", "value").rows()))
        for version in VERSIONS:
            r = part.filter((pl.col("version") == version) & (pl.col("metric") == "rmse_change_pct"))["value"][0]
            line = f"  {version:55s} held-out weekend error {r:+6.1f}%"
            s = share_rows.filter((pl.col("measure") == measure) & (pl.col("period") == period)
                                  & (pl.col("version") == version) & (pl.col("parameter") != "k"))
            line += "".join(f"  {p} {e:.2f} ({lo:.2f}-{hi:.2f})" for p, e, lo, hi in
                            s.select("parameter", "estimate", "low", "high").rows())
            print(line)
    y = weekly.filter(pl.col("week_start").dt.year() == 2019)
    for label, day in (("summer", "2019-07-08"), ("spring break", "2019-03-11"), ("Thanksgiving", "2019-11-25")):
        r = y.filter(pl.col("week_start") == pl.lit(day).str.to_date()).row(0, named=True)
        print(f"2019 {label}: range {r['min_students_away']:,}-{r['max_students_away']:,}, estimate "
              f"{r['students_away']:,} ({r['students_away_low']:,}-{r['students_away_high']:,})")


if __name__ == "__main__":
    main()
