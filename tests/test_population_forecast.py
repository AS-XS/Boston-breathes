from datetime import date

import polars as pl
import pytest

from boston_breathes import population_forecast as pf
from boston_breathes.paths import PROCESSED


def test_forecast_weeks_cover_the_academic_year_2026_27():
    weeks = pf.forecast_weeks()["week_start"]
    assert (weeks.min(), weeks.max(), len(weeks)) == (date(2026, 8, 3), date(2027, 7, 19), 51)


def test_yearly_changes_leave_out_the_covid_years():
    df = pl.DataFrame({"year": [2018, 2019, 2020, 2021, 2022, 2023], "value": [100.0, 102.0, 60.0, 90.0, 99.0, 98.01]})
    out = pf.change(df, "year", "value", "x")
    # 2019 (+2%) and 2023 (-1%) count; changes into or out of 2020 and 2021 do not.
    assert out["latest_year"] == 2023 and out["years"] == 2
    assert out["largest_yearly_change_pct"] == pytest.approx(2.0) and out["median_yearly_change_pct"] == pytest.approx(1.5)


def test_processed_forecast_matches_the_estimate_where_they_overlap():
    forecast = pl.read_csv(PROCESSED / "population_forecast.csv", try_parse_dates=True)
    estimate = pl.read_csv(PROCESSED / "population_estimate.csv", try_parse_dates=True, infer_schema_length=None)
    overlap = forecast.join(estimate, on="week_start", suffix="_estimate")
    assert len(overlap) == 22  # August to December 2026
    for c in ("students_away", "students_away_low", "students_away_high", "population"):
        assert (overlap[c] == overlap[f"{c}_estimate"]).all()
    assert forecast.null_count().sum_horizontal().item() == 0
    assert ((forecast["students_away_low"] <= forecast["students_away"])
            & (forecast["students_away"] <= forecast["students_away_high"])).all()
