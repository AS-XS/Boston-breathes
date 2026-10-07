"""Resident population of the study-area municipalities (U.S. Census Bureau).

Annual July 1 estimates for each municipality come from two Census
Population Estimates Program series:
  * 2010-2020 intercensal estimates, revised to agree with the 2020 Census
    (the original estimates had drifted, e.g. by about 14,000 for Boston in
    2019), used for years before 2020;
  * the latest postcensal vintage (2020 onward), which starts from the 2020
    Census, used from 2020.
Both are based on the 2020 Census, so they join without a break.

Municipalities are Census county subdivisions (summary level 061): in
Massachusetts these are the cities and towns, which matters for Brookline,
a town rather than an incorporated place.

The Census counts college students where they live during the school year,
so these totals already include students living in the area.

Outputs in data/processed/:
  * population_annual.csv - one row per municipality and year (July 1)
  * population_weekly.csv - linear interpolation between July 1 estimates for
    each week, held at the latest estimate after it (flagged `extrapolated`)
"""

import io
import re
from datetime import date

import polars as pl

from boston_breathes import http
from boston_breathes.paths import PROCESSED, RAW, STUDY_AREA, STUDY_START
from boston_breathes.weeks import build_weeks

BASE_URL = "https://www2.census.gov/programs-surveys/popest/datasets/"
INTERCENSAL_URL = BASE_URL + "2010-2020/intercensal/cities/sub-est2020int.csv"
RAW_DIR = RAW / "census"

STATE_FIPS = "25"  # Massachusetts
COUNTY_SUBDIVISION = "061"
COUSUB_CODES = {"07000": "Boston", "11000": "Cambridge", "62535": "Somerville", "09175": "Brookline"}
FIRST_YEAR = STUDY_START.year - 1  # one year earlier, so weekly interpolation covers the first weeks


def fetch(url: str, name: str) -> str | None:
    """Download a Census CSV into data/raw/census/, or None if it does not exist."""
    path = RAW_DIR / name
    if path.exists():
        return path.read_text(encoding="latin-1")
    resp = http.get(url, timeout=300)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return resp.content.decode("latin-1")


def latest_vintage(first: int = 2024) -> tuple[int, str]:
    """Most recent postcensal vintage with Massachusetts city and town estimates."""
    for vintage in range(date.today().year, first - 1, -1):
        url = f"{BASE_URL}2020-{vintage}/cities/totals/sub-est{vintage}_{STATE_FIPS}.csv"
        text = fetch(url, f"sub-est{vintage}_{STATE_FIPS}.csv")
        if text is not None:
            return vintage, text
    raise FileNotFoundError("No postcensal vintage found")


def municipal_estimates(csv_text: str, source: str) -> pl.DataFrame:
    """July 1 estimates of the study-area municipalities, one row per year."""
    raw = pl.read_csv(io.StringIO(csv_text), infer_schema=False)
    rows = raw.filter(
        (pl.col("SUMLEV") == COUNTY_SUBDIVISION)
        & (pl.col("STATE") == STATE_FIPS)
        & pl.col("COUSUB").is_in(list(COUSUB_CODES))
    )
    found = set(rows["COUSUB"])
    if found != set(COUSUB_CODES):
        missing = [COUSUB_CODES[c] for c in set(COUSUB_CODES) - found]
        raise ValueError(f"{source}: municipalities not found: {missing}")
    if rows["COUSUB"].is_duplicated().any():
        raise ValueError(f"{source}: more than one row per municipality")
    # July 1 estimates only (e.g. POPESTIMATE2019), not variants such as the
    # April 1, 2020 estimate (POPESTIMATE042020) in some vintages.
    estimates = [c for c in rows.columns if re.fullmatch(r"POPESTIMATE\d{4}", c)]
    return (
        rows.select(pl.col("COUSUB").replace_strict(COUSUB_CODES).alias("municipality"), *estimates)
        .unpivot(index="municipality", variable_name="column", value_name="population")
        .select(
            "municipality",
            pl.col("column").str.slice(-4).cast(pl.Int64).alias("year"),
            pl.col("population").cast(pl.Int64),
            pl.lit(source).alias("source"),
        )
    )


def combine_series(intercensal: pl.DataFrame, postcensal: pl.DataFrame) -> pl.DataFrame:
    """Intercensal estimates before 2020, postcensal estimates from 2020."""
    return (
        pl.concat([intercensal.filter(pl.col("year") < 2020), postcensal.filter(pl.col("year") >= 2020)])
        .filter(pl.col("year") >= FIRST_YEAR)
        .sort("municipality", "year")
    )


def weekly(annual: pl.DataFrame) -> pl.DataFrame:
    """Population for each week, interpolated between July 1 estimates.

    Each week is placed at its Thursday. Weeks after the latest July 1
    estimate keep that estimate and are flagged `extrapolated`.
    """
    weeks = build_weeks().select("week_start").with_columns(
        (pl.col("week_start") + pl.duration(days=3)).alias("mid")
    )
    anchors = annual.with_columns(pl.date(pl.col("year"), 7, 1).alias("mid"))
    last_anchor = anchors["mid"].max()
    out = weeks
    for municipality in STUDY_AREA:
        series = anchors.filter(pl.col("municipality") == municipality).select(
            "mid", pl.col("population").cast(pl.Float64).alias(municipality)
        )
        out = (
            out.join(series, on="mid", how="full", coalesce=True)
            .sort("mid")
            .with_columns(pl.col(municipality).interpolate_by("mid").forward_fill().backward_fill())
            .filter(pl.col("week_start").is_not_null())
        )
    return (
        out.with_columns(
            pl.col(*STUDY_AREA).round(0).cast(pl.Int64),
            (pl.col("mid") > last_anchor).alias("extrapolated"),
        )
        .with_columns(pl.sum_horizontal(*STUDY_AREA).alias("study_area"))
        .select("week_start", *STUDY_AREA, "study_area", "extrapolated")
        .sort("week_start")
    )


def main() -> None:
    intercensal = municipal_estimates(fetch(INTERCENSAL_URL, "sub-est2020int.csv"), "intercensal 2010-2020")
    vintage, text = latest_vintage()
    postcensal = municipal_estimates(text, f"vintage {vintage}")
    annual = combine_series(intercensal, postcensal)

    PROCESSED.mkdir(parents=True, exist_ok=True)
    annual.write_csv(PROCESSED / "population_annual.csv")
    weekly(annual).write_csv(PROCESSED / "population_weekly.csv")

    totals = annual.group_by("year").agg(pl.col("population").sum()).sort("year")
    print(f"Population: intercensal before 2020, vintage {vintage} from 2020")
    for year, population in totals.iter_rows():
        print(f"  {year}: {population:>9,} (study area, July 1)")


if __name__ == "__main__":
    main()
