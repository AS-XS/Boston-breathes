"""Forecast: the weekly population of the academic year 2026-27.

The population model is run on the weeks of the academic year 2026-27 (the
weeks of August 3, 2026 to July 19, 2027), past the end of the study's
weekly timeline (December 2026), with the inputs known in advance:
  * the universities' published 2026-27 calendars (academic_calendar.csv);
  * enrollment held at the latest IPEDS fall, resident students and where
    they live at the latest ACS 5-year estimate, and the Census population
    at its latest July estimate, as in the estimate for weeks after them;
  * the population model as fitted, with its 95% band (population_estimate.py).
The steps are the same as for the estimate (presence.py, students_away.py,
population_estimate.py), so the weeks it shares with population_estimate.csv
(August to December 2026) have the same numbers.

The band covers the shares away, not the held inputs, which add their own
uncertainty: `input_changes` gives how much each moved from one year to the
next before, outside the COVID-19 years.

Outputs in data/processed/:
  * population_forecast.csv          the weekly forecast with its 95% band
  * population_forecast_inputs.csv   the inputs' latest year, and their largest and typical yearly change
"""

from datetime import date

import polars as pl

from boston_breathes import calendars
from boston_breathes import population as census
from boston_breathes import population_estimate as pe
from boston_breathes import population_model as pm
from boston_breathes import presence
from boston_breathes import students_away as sa
from boston_breathes.paths import PROCESSED
from boston_breathes.weeks import build_weeks

YEAR = 2026  # the academic year 2026-27
FIRST, LAST = date(2026, 8, 1), date(2027, 7, 25)  # the calendars' days run to July 31, 2027
COVID_YEARS = {2020, 2021}  # yearly changes into or out of these years are left out
COLUMNS = ["week_start", "presence_index", "summer_break_share", "census_population", "min_students_away",
           "students_away", "students_away_low", "students_away_high", "max_students_away", "population",
           "population_low", "population_high", "population_extrapolated"]


def forecast_weeks() -> pl.DataFrame:
    weeks = build_weeks(FIRST, LAST)
    return weeks.filter(presence.fall_year(pl.col("week_start")) == YEAR)


def forecast(weeks: pl.DataFrame, calendar: pl.DataFrame, enrollment: pl.DataFrame, policy: pl.DataFrame,
             arrangements: pl.DataFrame, annual: pl.DataFrame, halls: pl.DataFrame, model: tuple) -> pl.DataFrame:
    academic = calendars.weekly_session(calendars.session_days(calendar), weeks)
    weekly, by_inst = presence.aggregate(presence.per_institution(weeks.select("week_start"), enrollment, academic,
                                                                  policy))
    away = sa.build(weekly, by_inst, arrangements, census.weekly(annual, weeks), halls)
    inputs = pm.weekly_inputs(away, weekly)
    return pe.estimate(inputs, *model).join(inputs.select("week_start", "summer_break_share"), on="week_start",
                                            suffix="_input").select(COLUMNS).sort("week_start")


def change(df: pl.DataFrame, year: str, value: str, name: str) -> dict:
    """The latest year of a yearly input, and its largest and median absolute change from one year to the next,
    leaving out changes into or out of the COVID-19 years."""
    s = df.sort(year).with_columns((pl.col(value).pct_change() * 100).alias("pct"), pl.col(year).shift().alias("prev"))
    s = s.filter(pl.col("pct").is_not_null() & ~pl.col(year).is_in(COVID_YEARS) & ~pl.col("prev").is_in(COVID_YEARS))
    return {"input": name, "latest_year": int(df[year].max()), "largest_yearly_change_pct": round(s["pct"].abs().max(), 2),
            "median_yearly_change_pct": round(s["pct"].abs().median(), 2), "years": len(s)}


def input_changes(enrollment: pl.DataFrame, arrangements: pl.DataFrame, annual: pl.DataFrame) -> pl.DataFrame:
    return pl.DataFrame([
        change(annual.group_by("year").agg(pl.col("population").sum()), "year", "population",
               "Census resident population (July)"),
        change(enrollment.group_by("fall_year").agg(pl.col("in_person").sum()), "fall_year", "in_person",
               "IPEDS in-person enrollment (fall)"),
        change(arrangements.group_by("acs_year").agg((pl.col("in_university_housing") + pl.col("elsewhere")).sum()
                                                     .alias("students")), "acs_year", "students",
               "ACS resident students not living with their parents"),
    ])


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    ids = {"unitid": pl.Utf8}
    enrollment, arrangements = read("enrollment_annual.csv", schema_overrides=ids), read("student_living_arrangements.csv")
    annual = read("population_annual.csv")
    model = pe.chosen_model(pm.weekly_inputs(read("students_away_weekly.csv"),
                                             read("student_presence_weekly.csv", infer_schema_length=None)),
                            read("mbta_weekly_by_station.csv"), read("mbta_survey_by_station.csv"),
                            read("presence_signals_weekly.csv"))
    out = forecast(forecast_weeks(), read("academic_calendar.csv", schema_overrides=ids), enrollment,
                   presence.load_covid_policy(), arrangements, annual, sa.load_halls(), model)
    out.write_csv(PROCESSED / "population_forecast.csv")
    changes = input_changes(enrollment, arrangements, annual)
    changes.write_csv(PROCESSED / "population_forecast_inputs.csv")

    print(f"Forecast {YEAR}-{(YEAR + 1) % 100:02d}: {len(out)} weeks, {out['week_start'].min()} to {out['week_start'].max()}")
    peak = out.sort("students_away", descending=True).row(0, named=True)
    print(f"  most students away: {peak['students_away']:,} ({peak['students_away_low']:,}-{peak['students_away_high']:,})"
          f" in the week of {peak['week_start']}; population {peak['population']:,}")
    print(f"  average population {out['population'].mean():,.0f}; lowest {out['population'].min():,}")
    with pl.Config(tbl_width_chars=200, fmt_str_lengths=60):
        print(changes)


if __name__ == "__main__":
    main()
