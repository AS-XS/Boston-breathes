"""Cambridge Town Gown higher-education statistics.

Source: City of Cambridge open data, "Annual Town Gown Report Higher
Education Statistics 2000 - Present" (dataset 46sm-9zs4). Cambridge's
universities report enrollment and student housing to the city each year.

A report year describes the previous fall: MIT's reported student counts
match IPEDS fall enrollment one year earlier to within about 1%. The reports
are published sooner than IPEDS, so they extend enrollment for Cambridge
institutions by a year. Their counting rules differ somewhat from IPEDS
(e.g. Lesley's counts are much higher), so they are best used for year-to-year
change rather than mixed with IPEDS levels directly.

Output: data/processed/cambridge_town_gown.csv, one row per report year.
"""

import io

import polars as pl

from boston_breathes import http
from boston_breathes.paths import PROCESSED, RAW

DATASET_URL = "https://data.cambridgema.gov/api/views/46sm-9zs4/rows.csv?accessType=DOWNLOAD"
RAW_PATH = RAW / "cambridge" / "town_gown.csv"

COLUMNS = {
    "Reporting Year": "report_year",
    "Harvard Students": "harvard_students",
    "MIT Students": "mit_students",
    "Lesley Students": "lesley_students",
    "Hult Students": "hult_students",
    "Cambridge College Students": "cambridge_college_students",
    "Degree Students": "degree_students",
    "Non-Degree Students": "non_degree_students",
    "Undergraduate Students": "undergrad_students",
    "Graduate Students": "graduate_students",
    "Post-Doctoral Fellows": "postdocs",
    "Student Residents - Dormitory": "students_in_dorms",
    "Student Residents - Affiliate Housing": "students_in_affiliate_housing",
    "Student Residents - Off Campus": "students_off_campus",
}


def download() -> str:
    resp = http.get(DATASET_URL, timeout=120)
    resp.raise_for_status()
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(resp.text)
    return resp.text


def parse(csv_text: str) -> pl.DataFrame:
    raw = pl.read_csv(io.StringIO(csv_text), infer_schema=False)
    missing = set(COLUMNS) - set(raw.columns)
    if missing:
        raise ValueError(f"Town Gown data is missing columns: {sorted(missing)}")
    numbers = [
        pl.col(src).str.replace_all(",", "").str.strip_chars().cast(pl.Int64, strict=False).alias(dst)
        for src, dst in COLUMNS.items()
    ]
    df = raw.select(numbers).sort("report_year")
    if df["report_year"].is_duplicated().any():
        raise ValueError("Duplicate report years in Town Gown data")
    return df.with_columns((pl.col("report_year") - 1).alias("fall_year")).select(
        "report_year", "fall_year", *[c for c in COLUMNS.values() if c != "report_year"]
    )


def main() -> None:
    df = parse(download())
    PROCESSED.mkdir(parents=True, exist_ok=True)
    df.write_csv(PROCESSED / "cambridge_town_gown.csv")
    print(f"Town Gown: report years {df['report_year'].min()}-{df['report_year'].max()} "
          f"(falls {df['fall_year'].min()}-{df['fall_year'].max()})")


if __name__ == "__main__":
    main()
