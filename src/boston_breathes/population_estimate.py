"""The weekly population estimate, with its 95% band and the students away read off travel each week.

  * Shares away: the shares of students out of session who leave in summer
    and on other breaks, fitted on the subway and Bluebikes together
    (joint_population.py, no weather model), divided by the weekend factor
    of weekend_riders.py. Students make up a larger share of weekend riders
    than of weekday riders near campuses (Bluebikes members aged 18-24 at
    the student-dominated campuses, 2016-2019), so weekend travel moves more
    for each student who leaves than the measurement model assumes; the
    factor is applied to both signals, as the subway could not be tested.
  * Estimate: resident college students x (summer share x share on summer
    break + break share x share otherwise out of session), inside the range
    of students_away.py (population_model.away).
  * 95% band: each refit of the shares (on calendar months drawn with
    replacement, widened as population_accuracy.py calibrated it) divided by
    one refit of the weekend factor (stations and months drawn with
    replacement); the band runs from the 2.5th to the 97.5th percentile of
    the students away they give, so it covers both.
  * Read off travel, for 2022-2025: each week's subway weekend slope, through
    the c' and k of population_model.py's chosen version and the weekend
    factor, gives that week's share of students away relative to the year's
    mean; the estimate gives the mean.
The COVID-19 period is not estimated.

Output: data/processed/population_estimate.csv
"""

import numpy as np
import polars as pl

from boston_breathes import joint_population as jp
from boston_breathes import population_model as pm
from boston_breathes import weekend_riders as wr
from boston_breathes.paths import PROCESSED

SMOOTH_WEEKS = 5  # centred moving average of the share away read off travel


def estimate(inputs: pl.DataFrame, beta: np.ndarray, shares: np.ndarray, draws: np.ndarray, slopes: pl.DataFrame,
             factor: float) -> pl.DataFrame:
    """Weekly students away and population. beta is the chosen version's fit on the subway (for the read-off);
    shares are the shares away (summer, breaks) and draws their refits, one pair per row, both already divided
    by the weekend factor."""
    point = pm.away(inputs, shares[:1], shares[1:])[:, 0]
    low, high = np.percentile(pm.away(inputs, draws[:, 0], draws[:, 1]), [2.5, 97.5], axis=1)
    low, high = np.minimum(low, point), np.maximum(high, point)  # the band always contains the estimate
    normal = pl.col("covid_period") == "normal"
    rounded = lambda v, name: pl.when(normal).then(pl.Series(name, v).round(0).cast(pl.Int64))  # noqa: E731
    # The weekend slope gives the share away relative to its mean over the year; the estimate gives that mean.
    level = (pl.col("point") / pl.col("resident_students")).mean().over(pl.col("week_start").dt.year())
    travel = (inputs.select("week_start", "resident_students").with_columns(pl.Series("point", point))
              .join(slopes.select("week_start", "weekend"), on="week_start")
              .select("week_start", ((pl.col("weekend") - beta[1]) / (beta[2] * factor) + level)
                      .alias("travel_share_away")))
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


def chosen_model(inputs: pl.DataFrame, by_station: pl.DataFrame, survey: pl.DataFrame,
                 signals: pl.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, pl.DataFrame, float]:
    """The chosen version's fit on the subway, the corrected shares away and their refits, the weekly subway
    slopes, and the weekend factor (also used by population_scenarios.py and population_forecast.py)."""
    sw = pm.station_weeks(by_station, survey, inputs, pm.PERIODS[pm.MAIN_PERIOD])
    slopes = pm.deviations(pm.weekly_slopes(sw, pm.MAIN_MEASURE).join(inputs, on="week_start"))
    shares, draws = jp.main_shares(signals)
    factor, factor_draws = wr.weekend_factor()
    return pm.fit(slopes, pm.MAIN_VERSION), shares / factor, draws / factor_draws[:, None], slopes, factor


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    inputs = pm.weekly_inputs(read("students_away_weekly.csv"),
                              read("student_presence_weekly.csv", infer_schema_length=None))
    model = chosen_model(inputs, read("mbta_weekly_by_station.csv"), read("mbta_survey_by_station.csv"),
                         read("presence_signals_weekly.csv"))
    weekly = estimate(inputs, *model)
    weekly.write_csv(PROCESSED / "population_estimate.csv")
    shares, draws, factor = model[1], model[2], model[4]
    low, high = np.percentile(draws, [2.5, 97.5], axis=0)
    print(f"Shares away on the subway and Bluebikes, divided by the weekend factor {factor}: summer {shares[0]:.3f} "
          f"({low[0]:.3f}-{high[0]:.3f}), breaks {shares[1]:.3f} ({low[1]:.3f}-{high[1]:.3f})")
    y = weekly.filter(pl.col("week_start").dt.year() == 2019)
    for label, day in (("summer", "2019-07-08"), ("spring break", "2019-03-11"), ("Thanksgiving", "2019-11-25"),
                       ("after Christmas", "2019-12-30")):
        r = y.filter(pl.col("week_start") == pl.lit(day).str.to_date()).row(0, named=True)
        print(f"2019 {label}: range {r['min_students_away']:,}-{r['max_students_away']:,}, estimate "
              f"{r['students_away']:,} ({r['students_away_low']:,}-{r['students_away_high']:,}), "
              f"population {r['population']:,}")


if __name__ == "__main__":
    main()
