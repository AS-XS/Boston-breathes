"""The weekly population estimate, with its 95% band and the students away read off travel each week.

  * Estimate: the version of population_model.py chosen on the validation
    years, in which every resident college student not living with their
    parents whose school is out of session leaves (the lower line of the
    range of students_away.py).
  * 95% band: the shares away in summer and on other breaks fitted on the
    subway and Bluebikes together (joint_population.py, no weather model),
    from the refits on runs of months drawn with replacement, widened as the
    known-truth test of population_accuracy.py calibrated them. Each pair of
    shares gives the students away each week (population_model.away); the
    band runs from the 2.5th to the 97.5th percentile of those, stretched to
    contain the estimate. Since the estimate is the most students who could
    be away, the band shows how far below it the data allow them to be.
  * Read off travel, for 2022-2025: each week's subway weekend slope, through
    the fitted c' and k of the chosen version, gives that week's share of
    students away relative to the year's mean; the chosen version gives the
    mean.
The COVID-19 period is not estimated.

Output: data/processed/population_estimate.csv
"""

import numpy as np
import polars as pl

from boston_breathes import joint_population as jp
from boston_breathes import population_model as pm
from boston_breathes.paths import PROCESSED

SMOOTH_WEEKS = 5  # centred moving average of the share away read off travel


def estimate(inputs: pl.DataFrame, beta: np.ndarray, shares: np.ndarray, slopes: pl.DataFrame) -> pl.DataFrame:
    """Weekly students away and population. beta is the chosen version's fit on the subway; shares are the
    refitted shares away (summer, breaks) of the band, one pair per row."""
    point = (inputs["resident_students"] * inputs.select(pm.version_share(pm.MAIN_VERSION)).to_series()).to_numpy()
    low, high = np.percentile(pm.away(inputs, shares[:, 0], shares[:, 1]), [2.5, 97.5], axis=1)
    low, high = np.minimum(low, point), np.maximum(high, point)  # the band always contains the estimate
    normal = pl.col("covid_period") == "normal"
    rounded = lambda v, name: pl.when(normal).then(pl.Series(name, v).round(0).cast(pl.Int64))  # noqa: E731
    # The weekend slope gives the share away relative to its mean over the year; the model gives that mean.
    level = pm.version_share(pm.MAIN_VERSION).mean().over(pl.col("week_start").dt.year())
    travel = slopes.select("week_start", ((pl.col("weekend") - beta[1]) / beta[2] + level).alias("travel_share_away"))
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
                 signals: pl.DataFrame) -> tuple[np.ndarray, np.ndarray, pl.DataFrame]:
    """The chosen version's fit on the subway, the band's refitted shares, and the weekly subway slopes (also used
    by population_scenarios.py and population_forecast.py)."""
    sw = pm.station_weeks(by_station, survey, inputs, pm.PERIODS[pm.MAIN_PERIOD])
    slopes = pm.deviations(pm.weekly_slopes(sw, pm.MAIN_MEASURE).join(inputs, on="week_start"))
    return pm.fit(slopes, pm.MAIN_VERSION), jp.main_shares(signals)[1], slopes


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    inputs = pm.weekly_inputs(read("students_away_weekly.csv"),
                              read("student_presence_weekly.csv", infer_schema_length=None))
    model = chosen_model(inputs, read("mbta_weekly_by_station.csv"), read("mbta_survey_by_station.csv"),
                         read("presence_signals_weekly.csv"))
    weekly = estimate(inputs, *model)
    weekly.write_csv(PROCESSED / "population_estimate.csv")
    low, high = np.percentile(model[1], [2.5, 97.5], axis=0)
    print(f"Estimate: {pm.MAIN_VERSION}; band from the shares on the subway and Bluebikes: summer {low[0]:.2f}-"
          f"{high[0]:.2f}, breaks {low[1]:.2f}-{high[1]:.2f} (runs of {jp.BLOCK_MONTHS} months, widened by "
          f"{jp.INFLATION})")
    y = weekly.filter(pl.col("week_start").dt.year() == 2019)
    for label, day in (("summer", "2019-07-08"), ("spring break", "2019-03-11"), ("Thanksgiving", "2019-11-25")):
        r = y.filter(pl.col("week_start") == pl.lit(day).str.to_date()).row(0, named=True)
        print(f"2019 {label}: range {r['min_students_away']:,}-{r['max_students_away']:,}, estimate "
              f"{r['students_away']:,} ({r['students_away_low']:,}-{r['students_away_high']:,})")


if __name__ == "__main__":
    main()
