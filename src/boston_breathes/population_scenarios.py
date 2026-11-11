"""Scenarios: the weekly population if one input of the population model were different.

A scenario changes one input for the latest full academic year (2025-26),
keeps the model fitted in population_model.py, and recomputes the weekly
population through the same steps as the estimate: the Student Presence
Index (presence.py), the range of students away (students_away.py), and the
population model's estimate and band. It says how much the population would
change if that input changed, not that it will, and it assumes students
behave as in the years the model was fitted on.

Scenarios:
  * baseline: 2025-26 as it is (the same weeks as population_estimate.csv);
  * 10% fewer / 10% more students: resident college students not living
    with their parents (for example, international students) scaled by 0.9
    or 1.1. The Census population moves with them, since it counts students
    where they live during the school year;
  * spring breaks aligned: each institution's spring break (its run of
    weeks out of session from February to April around the fullest one)
    moved to the week in which the most students have theirs;
  * remote fall term: the institutions that taught fall 2020 remotely
    (data/manual/covid_policy.csv) teach fall 2025 remotely, so their
    students are out of session from the start of term to December. Their
    residence halls count as closed when the halls close for spring break,
    Thanksgiving or winter break (by month), which is approximate; the
    estimate does not depend on it.

Outputs in data/processed/:
  * population_scenarios.csv          the weekly estimate and 95% band under each scenario, 2025-26
  * population_scenarios_summary.csv  each scenario's average and lowest population, students away at
                                      different times of year, and its change from the baseline
"""

import numpy as np
import polars as pl

from boston_breathes import population_model as pm
from boston_breathes import presence
from boston_breathes import students_away as sa
from boston_breathes.paths import PROCESSED
from boston_breathes.weeks import build_weeks

YEAR = 2025  # academic year 2025-26: the latest full year with calendars and travel data
BASELINE, ALIGNED, REMOTE = "baseline", "spring breaks aligned", "remote fall term"
ENROLLMENT = {"10% fewer students": -0.10, "10% more students": 0.10}
SPRING_MONTHS = (2, 4)  # spring breaks fall between February and April
FALL_MONTHS = (8, 12)  # the fall term, from its start to December
REMOTE_TERM = "fall_2020"
COLUMNS = ["week_start", "census_population", "students_away", "students_away_low", "students_away_high",
           "population", "population_low", "population_high"]


def thursday_month() -> pl.Expr:
    return (pl.col("week_start") + pl.duration(days=3)).dt.month()


def in_year(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(presence.fall_year(pl.col("week_start")) == YEAR)


def scale_students(arrangements: pl.DataFrame, change: float) -> pl.DataFrame:
    """Resident students not living with their parents scaled by 1 + change; those with parents unchanged."""
    others = pl.col("in_university_housing") + pl.col("elsewhere")
    return arrangements.with_columns(
        (pl.col("college_students") + change * others).round(0).cast(pl.Int64).alias("college_students"),
        *[(pl.col(c) * (1 + change)).round(0).cast(pl.Int64).alias(c) for c in ("in_university_housing", "elsewhere")])


def align_spring_breaks(per_inst: pl.DataFrame) -> pl.DataFrame:
    """Each institution's spring break moved so that its fullest week falls in the week in which the most
    students (in person) have theirs. The break is the run of weeks with days out of session (not summer)
    around the fullest one; other days off in February to April stay where they are."""
    spring = (presence.fall_year(pl.col("week_start")) == YEAR) & thursday_month().is_between(*SPRING_MONTHS)
    window = per_inst.filter(spring).with_columns(
        (1 - pl.col("session_share") - pl.col("summer_share")).clip(lower_bound=0).alias("_off")).sort("week_start")
    runs, weight = {}, {}
    for (unitid,), g in window.group_by("unitid"):
        off = g["_off"].to_numpy()
        if off.max() <= 0:
            continue
        peak = int(off.argmax())
        lo, hi = peak, peak
        while lo > 0 and off[lo - 1] > 0:
            lo -= 1
        while hi < len(off) - 1 and off[hi + 1] > 0:
            hi += 1
        runs[unitid] = (g["week_start"].to_list(), off, peak, lo, hi, g["summer_share"].to_numpy())
        weight[peak] = weight.get(peak, 0) + g["in_person"][0]
    common = max(weight, key=weight.get)
    moved = []
    for unitid, (weeks, off, peak, lo, hi, summer) in runs.items():
        new = off.copy()
        new[lo:hi + 1] = 0
        for i in range(lo, hi + 1):
            j = i + common - peak
            if 0 <= j < len(off):
                new[j] += off[i]
        new = np.minimum(new, 1 - summer)
        moved += [{"week_start": w, "unitid": unitid, "_session": float(1 - s - n)}
                  for w, s, n in zip(weeks, summer, new)]
    return (per_inst.join(pl.DataFrame(moved), on=["week_start", "unitid"], how="left")
            .with_columns(pl.coalesce("_session", "session_share").alias("session_share")).drop("_session"))


def remote_term(per_inst: pl.DataFrame, policy: pl.DataFrame) -> pl.DataFrame:
    """The institutions that taught fall 2020 remotely out of session from August to December of YEAR."""
    remote = policy.filter((pl.col("term") == REMOTE_TERM) & (pl.col("instruction") == "remote"))["unitid"]
    fall = (presence.fall_year(pl.col("week_start")) == YEAR) & thursday_month().is_between(*FALL_MONTHS)
    return per_inst.with_columns(
        pl.when(pl.col("unitid").is_in(remote.implode()) & fall).then(0.0).otherwise("session_share")
        .alias("session_share"))


def run(per_inst: pl.DataFrame, arrangements: pl.DataFrame, population: pl.DataFrame, halls: pl.DataFrame,
        model: tuple, students_change: float = 0.0) -> pl.DataFrame:
    """The weekly estimate of YEAR, through the same steps as the published one."""
    weekly, by_inst = presence.aggregate(per_inst)
    away = sa.build(weekly, by_inst, scale_students(arrangements, students_change), population, halls)
    # The Census counts students where they live during the school year, so it moves with them.
    away = away.with_columns((pl.col("census_population") + students_change / (1 + students_change)
                              * (pl.col("in_university_housing") + pl.col("elsewhere"))).round(0).cast(pl.Int64))
    return in_year(pm.estimate(pm.weekly_inputs(away, weekly), *model)).select(COLUMNS)


def summary(scenarios: pl.DataFrame) -> pl.DataFrame:
    month = thursday_month()
    base = scenarios.filter(pl.col("scenario") == BASELINE).select("week_start", pl.col("population").alias("_base"))
    return (
        scenarios.join(base, on="week_start").with_columns((pl.col("population") - pl.col("_base")).alias("_diff"))
        .group_by("scenario", maintain_order=True)
        .agg(pl.col("population").mean().round(0).cast(pl.Int64).alias("average_population"),
             pl.col("population").min().alias("lowest_population"),
             pl.col("week_start").sort_by("population").first().alias("lowest_population_week"),
             pl.col("students_away").max().alias("most_students_away"),
             pl.col("students_away").filter(month.is_between(*SPRING_MONTHS)).max().alias("most_away_february_april"),
             pl.col("students_away").filter(month.is_between(9, 12)).mean().round(0).cast(pl.Int64)
             .alias("average_away_september_december"),
             pl.col("_diff").mean().round(0).cast(pl.Int64).alias("average_change_from_baseline"),
             pl.col("_diff").min().alias("largest_fall_from_baseline"),
             pl.col("_diff").max().alias("largest_rise_from_baseline"),
             (pl.col("_diff") != 0).sum().alias("weeks_changed"))
    )


def fitted_model(away: pl.DataFrame, presence_weekly: pl.DataFrame, by_station: pl.DataFrame,
                 survey: pl.DataFrame) -> tuple:
    """The chosen version's fit and the band version's refits, as population_model.py makes them."""
    inputs = pm.weekly_inputs(away, presence_weekly)
    sw = pm.station_weeks(by_station, survey, inputs, pm.PERIODS[pm.MAIN_PERIOD])
    _, _, slopes, draws = pm.analyse(sw, inputs, pm.MAIN_MEASURE, pm.MAIN_PERIOD, np.random.default_rng(0))
    return pm.fit(slopes, pm.MAIN_VERSION), draws[pm.BAND_VERSION], slopes


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    ids = {"unitid": pl.Utf8}
    policy = presence.load_covid_policy()
    per_inst = presence.per_institution(build_weeks().select("week_start"), read("enrollment_annual.csv",
                                        schema_overrides=ids), read("academic_weekly.csv", schema_overrides=ids),
                                        policy)
    arrangements, population, halls = read("student_living_arrangements.csv"), read("population_weekly.csv"), \
        sa.load_halls()
    model = fitted_model(read("students_away_weekly.csv"), read("student_presence_weekly.csv", infer_schema_length=None),
                         read("mbta_weekly_by_station.csv"), read("mbta_survey_by_station.csv"))
    runs = {BASELINE: run(per_inst, arrangements, population, halls, model)}
    for name, change in ENROLLMENT.items():
        runs[name] = run(per_inst, arrangements, population, halls, model, change)
    runs[ALIGNED] = run(align_spring_breaks(per_inst), arrangements, population, halls, model)
    runs[REMOTE] = run(remote_term(per_inst, policy), arrangements, population, halls, model)
    scenarios = pl.concat([df.with_columns(pl.lit(name).alias("scenario")) for name, df in runs.items()]) \
        .select("scenario", *COLUMNS)
    scenarios.write_csv(PROCESSED / "population_scenarios.csv")
    out = summary(scenarios)
    out.write_csv(PROCESSED / "population_scenarios_summary.csv")
    with pl.Config(tbl_cols=-1, tbl_width_chars=220, fmt_str_lengths=30):
        print(out)


if __name__ == "__main__":
    main()
