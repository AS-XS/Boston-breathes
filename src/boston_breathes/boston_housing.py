"""City of Boston student housing counts.

Input: data/manual/boston_student_housing.csv, transcribed from the City of
Boston's annual student housing reports. The reports compile the University
Accountability Reports that Boston's colleges and universities file with the
city each fall. The table used counts the students *living in Boston* by
degree level and living arrangement:

  * on campus or in university-provided housing;
  * off campus, living at home (with parents or guardians);
  * off campus, not at home (mostly private rentals);
  * not categorized (reported separately only in some years).

It covers students of Boston-based institutions who live in the city, so
students living in Boston who attend institutions elsewhere (e.g. in
Cambridge) are not included. The reports give this table for falls 2018 to
2024; earlier reports do not split students living in Boston this way.

Output: data/processed/boston_student_housing.csv, one row per fall with
undergraduate, graduate and total counts for each living arrangement.
"""

import polars as pl

from boston_breathes.paths import DATA, PROCESSED

MANUAL_PATH = DATA / "manual" / "boston_student_housing.csv"
ARRANGEMENTS = ["on_campus", "off_campus_at_home", "off_campus_not_at_home", "not_categorized"]
DEGREES = {"undergraduate": "undergrad", "graduate": "graduate"}


def load_manual(path=MANUAL_PATH) -> pl.DataFrame:
    """Read and validate the transcribed counts; raise listing all problems."""
    df = pl.read_csv(path, schema_overrides={a: pl.Int64 for a in ARRANGEMENTS})
    problems = []
    for value in set(df["degree"]) - set(DEGREES):
        problems.append(f"unknown degree {value!r}")
    for r in df.filter(~pl.col("source_url").fill_null("").str.starts_with("http")).iter_rows(named=True):
        problems.append(f"{r['fall_year']} {r['degree']}: missing source URL")
    for r in df.filter(pl.struct("fall_year", "degree").is_duplicated()).unique(["fall_year", "degree"]).iter_rows(named=True):
        problems.append(f"{r['fall_year']} {r['degree']}: duplicate row")
    for a in ARRANGEMENTS:
        for r in df.filter(pl.col(a) < 0).iter_rows(named=True):
            problems.append(f"{r['fall_year']} {r['degree']}: negative {a}")
    if problems:
        raise ValueError("Boston student housing problems:\n  " + "\n  ".join(problems))
    return df


def by_fall(df: pl.DataFrame) -> pl.DataFrame:
    """One row per fall: each arrangement by degree level and in total."""
    wide = df.with_columns(pl.col("degree").replace_strict(DEGREES)).pivot(
        on="degree", index="fall_year", values=ARRANGEMENTS, separator="_"
    )
    totals = [(pl.col(f"{a}_undergrad") + pl.col(f"{a}_graduate")).alias(a) for a in ARRANGEMENTS]
    out = wide.with_columns(totals).with_columns(pl.sum_horizontal(ARRANGEMENTS).alias("students_living_in_boston"))
    columns = ["fall_year", "students_living_in_boston", *ARRANGEMENTS,
               *[f"{a}_{d}" for a in ARRANGEMENTS for d in DEGREES.values()]]
    return out.select(columns).sort("fall_year")


def main() -> None:
    out = by_fall(load_manual())
    PROCESSED.mkdir(parents=True, exist_ok=True)
    out.write_csv(PROCESSED / "boston_student_housing.csv")
    print(f"Boston student housing: falls {out['fall_year'].min()}-{out['fall_year'].max()}")
    print(out.select("fall_year", "students_living_in_boston", *ARRANGEMENTS))


if __name__ == "__main__":
    main()
