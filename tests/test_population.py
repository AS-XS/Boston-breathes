from datetime import date

import polars as pl
import pytest

from boston_breathes import population as pop

HEADER = "SUMLEV,STATE,COUNTY,PLACE,COUSUB,NAME,POPESTIMATE2019,POPESTIMATE2020,POPESTIMATE042020,CENSUS2020POP"
ROWS = [
    "061,25,025,00000,07000,Boston city,600,610,605,606",
    "061,25,017,00000,11000,Cambridge city,100,110,105,106",
    "061,25,017,00000,62535,Somerville city,80,81,80,80",
    "061,25,021,00000,09175,Brookline town,60,61,60,60",
    # Same city at the place summary level; must not be double counted.
    "162,25,000,07000,00000,Boston city,600,610,605,606",
    # Another state's town with a matching code.
    "061,33,001,00000,07000,Elsewhere town,5,5,5,5",
]


def csv(rows: list[str]) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def test_municipal_estimates_uses_county_subdivisions_only():
    df = pop.municipal_estimates(csv(ROWS), "test").sort("municipality", "year")
    assert df.columns == ["municipality", "year", "population", "source"]
    assert df.filter(pl.col("municipality") == "Boston")["population"].to_list() == [600, 610]
    # Only POPESTIMATEyyyy columns are kept, not CENSUS2020POP or other variants.
    assert sorted(df["year"].unique().to_list()) == [2019, 2020]
    assert len(df) == 8


def test_municipal_estimates_requires_every_municipality():
    with pytest.raises(ValueError, match="Brookline"):
        pop.municipal_estimates(csv(ROWS[:3]), "test")


def test_municipal_estimates_rejects_duplicates():
    with pytest.raises(ValueError, match="more than one row"):
        pop.municipal_estimates(csv([*ROWS, ROWS[0]]), "test")


def test_combine_series_switches_at_2020():
    def series(source: str, years: list[int]) -> pl.DataFrame:
        return pl.DataFrame({
            "municipality": ["Boston"] * len(years), "year": years,
            "population": [1] * len(years), "source": [source] * len(years),
        })
    combined = pop.combine_series(series("intercensal", [2013, 2014, 2019, 2020]), series("vintage", [2020, 2021]))
    assert combined["year"].to_list() == [2014, 2019, 2020, 2021]
    assert combined["source"].to_list() == ["intercensal", "intercensal", "vintage", "vintage"]


def test_weekly_interpolates_between_july_estimates():
    rows = []
    for municipality, base in [("Boston", 1000), ("Cambridge", 100), ("Somerville", 80), ("Brookline", 60)]:
        rows += [(municipality, 2015, base), (municipality, 2016, base + 365 + 1)]  # 366 days apart
    annual = pl.DataFrame(rows, schema=["municipality", "year", "population"], orient="row")
    w = pop.weekly(annual)

    # The week starting 2015-06-29 has its Thursday on July 2: one day after the anchor.
    row = w.filter(pl.col("week_start") == date(2015, 6, 29)).row(0, named=True)
    assert row["Boston"] == 1001
    assert row["study_area"] == 1001 + 101 + 81 + 61
    assert row["extrapolated"] is False

    # Weeks before the first estimate take its value; weeks after the last are flagged.
    assert w.filter(pl.col("week_start") == date(2014, 12, 29))["Boston"][0] == 1000
    late = w.filter(pl.col("week_start") == date(2016, 12, 26)).row(0, named=True)
    assert late["Boston"] == 1366 and late["extrapolated"] is True
    assert w.null_count().sum_horizontal()[0] == 0
