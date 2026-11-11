"""Checks of the population model against evidence it was not built from.

COVID-19 natural experiment. Students were sent home in March 2020 (the
lockdown, to 15 August 2020), and in 2020-21 41% of normal in-person
enrollment studied remotely; they came back in fall 2021. The population
model's inputs record this; the model itself never saw those weeks
(population_model.py uses weeks outside COVID-19). Its measurement part,
fitted on 2022-2025 (the chosen version: weekday slope = c + k x share out
of session, weekend slope = c' + k x share of students not living with their
parents who are out of session), predicts the weekly station slopes of 2020
and of 2021 with three sets of inputs:
  * the COVID-19 inputs, as the model has them;
  * a normal-year calendar for the same weeks, as if COVID-19 had not
    happened (each institution's calendar and usual in-person enrollment);
  * nobody away.
If travel reflects the population, the COVID-19 inputs predict it best. Office
commuters also vanished in 2020 and came back gradually, which affects
stations differently, so the check is indicative, not clean.

Size. Better is not the same as the right size: for years outside the fit
(2016-2019, 2020, 2021, and the 2026 test), the observed weekday and weekend
slopes are regressed on the part the model predicts from its inputs (without
the intercepts). A coefficient of 1 means the model's size of the absence
matches travel; below 1, fewer students were missing than the model says.
95% intervals from resampling calendar months.

Bluebikes, a second measurement. The same measurement model is fitted on
Bluebikes instead of the subway: member trips (residents; casual riders are
often visitors) started at each study-area station in a campus zone (within
400 m of a large campus) or an away zone (over 1 km from any), on weekdays
and on weekends, with the campus zone as the station's student measure. Each
week's slope is then the difference between campus and away stations (after
a level for each station in each year and for each week). Stations need a
median of at least MIN_WEEKLY_TRIPS member trips a week in the year. If
travel reflects the population, Bluebikes should choose the same version of
who leaves and give shares away close to the subway's. Trips come from the
monthly files in data/interim/bluebikes/station_day/ (bluebikes.py).

Outputs in data/processed/:
  * population_checks.csv       the COVID-19 check: errors of each set of inputs, by year and kind of day
  * population_checks_size.csv  the size check: coefficients with 95% intervals, by years and kind of day
  * population_checks_bluebikes.csv  the Bluebikes check: each version's error on later years, and the
                                     fitted shares with 95% intervals
"""

import numpy as np
import polars as pl

from boston_breathes import population_model as pm
from boston_breathes import presence
from boston_breathes import students_away as sa
from boston_breathes.paths import INTERIM, PROCESSED
from boston_breathes.weeks import build_weeks

COVID_YEARS = [2020, 2021]
SIZE_YEARS = {"2016-2019": (2016, 2019), "2020": (2020, 2020), "2021": (2021, 2021), "2026": (2026, 2026)}
ALL_PERIODS = ("normal", "lockdown", "remote_year")
BOOTSTRAP = 1000
BLUEBIKES = "bluebikes_campus_zone"  # the student measure: 1 at campus-zone stations, 0 at away-zone stations
MIN_WEEKLY_TRIPS = 20  # median member trips a week a station needs in a year
BLUEBIKES_YEARS = {"2022-2025": (2022, 2025), "2016-2019": (2016, 2019), "test": (2026, 2026)}
ACTUAL, NORMAL, NOBODY = "COVID-19 inputs", "normal-year calendar", "nobody away"


def normal_calendar(weeks: pl.DataFrame, enrollment: pl.DataFrame, academic: pl.DataFrame,
                    policy: pl.DataFrame) -> pl.DataFrame:
    """Each institution's week as its calendar has it, with its usual in-person enrollment: no lockdown and no
    remote year."""
    per_inst = presence.add_covid_policy(presence.session_share(presence.enrollment_by_week(weeks, enrollment),
                                                                academic), policy)
    return per_inst.with_columns(pl.col("normal_in_person").alias("in_person"), pl.lit(False).alias("covid_away"))


def rmse(observed: pl.Series, predicted) -> float:
    return float(((observed.to_numpy() - predicted) ** 2).mean() ** 0.5)


def covid_rows(slopes: pl.DataFrame, inputs: dict[str, pl.DataFrame], beta) -> list[dict]:
    """Weekday and weekend slopes of each year predicted with each set of inputs, through the fitted model."""
    rows = []
    for year in COVID_YEARS:
        observed = slopes.filter(pl.col("week_start").dt.year() == year)
        for name, df in inputs.items():
            x = pm.deviations(observed.join(df, on="week_start"))
            pred, _ = pm.design(x, pm.MAIN_VERSION)
            pred = pred @ beta
            n = len(x)
            rows += [{"check": "covid-19", "year": year, "inputs": name, "day": day, "metric": "rmse",
                      "value": rmse(x[day], pred[i * n:(i + 1) * n]), "weeks": n}
                     for i, day in enumerate(("weekday", "weekend"))]
    return rows


def size(slopes: pl.DataFrame, inputs: pl.DataFrame, beta: np.ndarray, rng: np.random.Generator) -> list[dict]:
    """Slope of the observed weekday and weekend slopes on the model's predicted change, with a month-block
    bootstrap interval."""
    x = pm.deviations(slopes.join(inputs, on="week_start"))
    design, _ = pm.design(x, pm.MAIN_VERSION)
    n = len(x)
    blocks = x["week_start"].dt.strftime("%Y-%m").to_numpy()
    names = np.unique(blocks)
    rows = []
    for i, day in enumerate(("weekday", "weekend")):
        predicted = design[i * n:(i + 1) * n, 2:] @ beta[2:]
        observed = x[day].to_numpy() - beta[i]
        coef = lambda m: float(predicted[m] @ observed[m] / (predicted[m] @ predicted[m]))  # noqa: E731
        draws = [coef(np.concatenate([np.flatnonzero(blocks == b) for b in rng.choice(names, len(names))]))
                 for _ in range(BOOTSTRAP)]
        low, high = np.percentile(draws, [2.5, 97.5])
        rows.append({"day": day, "estimate": coef(np.arange(n)), "low": float(low), "high": float(high), "weeks": n})
    return rows


def bluebikes_station_weeks(station_day: pl.DataFrame, stations: pl.DataFrame, inputs: pl.DataFrame,
                            years: tuple[int, int]) -> pl.DataFrame:
    """Member trips started at study-area campus- and away-zone stations, on weekdays and weekends, each week,
    in the same layout as the subway station-weeks."""
    zones = stations.filter(pl.col("in_study_area") & pl.col("campus_zone").is_in(["campus", "away"])).select(
        pl.col("station_id").cast(pl.Utf8), (pl.col("campus_zone") == "campus").cast(pl.Float64).alias(BLUEBIKES))
    weekend = pl.col("date").dt.weekday() >= 6
    normal = inputs.filter(pl.col("covid_period") == "normal").select("week_start")
    weekly = (
        station_day.with_columns(pl.col("station_id").cast(pl.Utf8))
        .join(zones, on="station_id")
        .with_columns((pl.col("date") - pl.duration(days=pl.col("date").dt.weekday() - 1)).alias("week_start"))
        .group_by("week_start", "station_id", BLUEBIKES)
        .agg(pl.col("member_trips").filter(~weekend).sum().alias("weekday_entries"),
             pl.col("member_trips").filter(weekend).sum().alias("weekend_entries"))
        .join(normal, on="week_start")
        .with_columns(pl.col("week_start").dt.year().alias("year"), pl.col("station_id").alias("stop_id"))
        .filter(pl.col("year").is_between(*years))
    )
    busy = (pl.col("weekday_entries") + pl.col("weekend_entries")).median().over("stop_id", "year") >= MIN_WEEKLY_TRIPS
    return weekly.filter(busy & (pl.col("weekday_entries") > 0) & (pl.col("weekend_entries") > 0))


def bluebikes_rows(station_day: pl.DataFrame, stations: pl.DataFrame, inputs: pl.DataFrame,
                   rng: np.random.Generator) -> list[dict]:
    slopes = {}
    for label, years in BLUEBIKES_YEARS.items():
        sw = bluebikes_station_weeks(station_day, stations, inputs, years)
        slopes[label] = pm.deviations(pm.weekly_slopes(sw, BLUEBIKES).join(inputs, on="week_start"))
    recent = slopes["2022-2025"]
    rows = [{"evaluation": "weekly slopes", "train_years": "", "test_years": "2022-2025", "version": "",
             "metric": f"corr_{day}_slope_vs_out_of_session", "value": float(np.corrcoef(recent[day], recent["out"])[0, 1]),
             "weeks": len(recent)} for day in ("weekday", "weekend")]
    rows += [{k: v for k, v in r.items() if k != "measure"} for r in pm.forward_rows(
        [*pm.validation_splits(recent), ("long horizon", slopes["2016-2019"], recent), ("test", recent, slopes["test"])],
        BLUEBIKES)]
    for label in ("2022-2025", "2016-2019"):
        df = slopes[label]
        beta, draws = pm.fit(df, pm.BAND_VERSION), pm.bootstrap(df, pm.BAND_VERSION, rng)
        boot = draws[:, 3:] / draws[:, 2:3]
        for i, part in enumerate(pm.ESTIMATED[pm.BAND_VERSION]):
            low, high = np.percentile(boot[:, i], [2.5, 97.5])
            rows += [{"evaluation": "fitted shares", "train_years": label, "test_years": "", "version": pm.BAND_VERSION,
                      "metric": f"share_{part}{end}", "value": float(v), "weeks": len(df)}
                     for end, v in (("", pm.shares(beta)[i]), ("_low", low), ("_high", high))]
    return rows


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    ids = {"unitid": pl.Utf8}
    presence_weekly = read("student_presence_weekly.csv", infer_schema_length=None)
    away = read("students_away_weekly.csv")
    by_station, survey = read("mbta_weekly_by_station.csv"), read("mbta_survey_by_station.csv")
    actual = pm.weekly_inputs(away, presence_weekly)

    # The measurement part, fitted on 2022-2025 as in population_model.py.
    sw = pm.station_weeks(by_station, survey, actual, pm.PERIODS[pm.MAIN_PERIOD])
    beta = pm.fit(pm.deviations(pm.weekly_slopes(sw, pm.MAIN_MEASURE).join(actual, on="week_start")),
                  pm.MAIN_VERSION)

    weekly, by_inst = presence.aggregate(normal_calendar(
        build_weeks().select("week_start"), read("enrollment_annual.csv", schema_overrides=ids),
        read("academic_weekly.csv", schema_overrides=ids), presence.load_covid_policy()))
    normal = pm.weekly_inputs(sa.build(weekly, by_inst, read("student_living_arrangements.csv"),
                                       read("population_weekly.csv"), sa.load_halls()), weekly)
    nobody = actual.with_columns(*[pl.lit(0.0).alias(c) for c in pm.REGRESSORS])
    covid_sw = pm.station_weeks(by_station, survey, actual, (min(COVID_YEARS), max(COVID_YEARS)),
                                periods=("normal", "lockdown", "remote_year"))
    slopes = pm.weekly_slopes(covid_sw, pm.MAIN_MEASURE)
    rows = covid_rows(slopes, {ACTUAL: actual, NORMAL: normal, NOBODY: nobody}, beta)
    out = pl.DataFrame(rows).with_columns(pl.col("value").round(4))
    out.write_csv(PROCESSED / "population_checks.csv")
    print(out.pivot(on="inputs", index=["year", "day"], values="value"))

    rng = np.random.default_rng(0)
    size_rows = []
    for label, years in SIZE_YEARS.items():
        sw_years = pm.station_weeks(by_station, survey, actual, years, periods=ALL_PERIODS)
        size_rows += [{"check": "size", "years": label, **r}
                      for r in size(pm.weekly_slopes(sw_years, pm.MAIN_MEASURE), actual, beta, rng)]
    sizes = pl.DataFrame(size_rows).with_columns(pl.col("estimate", "low", "high").round(3))
    sizes.write_csv(PROCESSED / "population_checks_size.csv")
    print(sizes)

    station_day = pl.concat([pl.read_parquet(f) for f in sorted((INTERIM / "bluebikes" / "station_day").glob("*.parquet"))],
                            how="diagonal_relaxed")
    bikes = pl.DataFrame(bluebikes_rows(station_day, read("bluebikes_station_campus.csv", schema_overrides={"station_id": pl.Utf8}),
                                        actual, rng))
    bikes.with_columns(pl.col("value").round(4)).write_csv(PROCESSED / "population_checks_bluebikes.csv")
    change = bikes.filter(pl.col("metric") == "rmse_change_pct")
    with pl.Config(tbl_rows=40, tbl_width_chars=200, fmt_str_lengths=55):
        print(bikes.filter(pl.col("evaluation") == "weekly slopes").select("metric", "value"))
        print(change.with_columns((pl.col("evaluation") + " " + pl.col("test_years")).alias("split"))
              .pivot(on="split", index="version", values="value").with_columns(pl.selectors.numeric().round(1)))
        print(bikes.filter(pl.col("evaluation") == "fitted shares").select("train_years", "metric", pl.col("value").round(2)))


if __name__ == "__main__":
    main()
