"""Universities in the study area and their fall enrollment (IPEDS).

Sources (NCES IPEDS complete data files, one per fall):
  * HDyyyy        institution directory: name, sector, campus coordinates
  * EFyyyyA       fall enrollment by level and attendance status
  * EFyyyyA_DIST  fall enrollment in distance education

Institutions are selected by their campus coordinates (assigned to a town
with the Census boundaries used for Bluebikes stations), not by the city
named in their address, since IPEDS addresses use neighbourhood names such as
"Chestnut Hill" or "Jamaica Plain". A few large universities just outside the
study area whose students largely live or travel in it are added explicitly.

Students enrolled exclusively in distance education are reported separately,
because they are not physically present.

IPEDS leaves out levels that do not apply to an institution (e.g. graduate
students at a community college, or distance education at a school without
online programs), so for an institution that reported in a given fall, a
missing level is counted as zero.

Outputs in data/processed/:
  * universities.csv       one row per selected institution
  * enrollment_annual.csv  one row per institution and fall
"""

import io
import zipfile
from datetime import date

import polars as pl

from boston_breathes import http
from boston_breathes.municipalities import assign, load_towns
from boston_breathes.paths import PROCESSED, RAW, STUDY_AREA, STUDY_START

BASE_URL = "https://nces.ed.gov/ipeds/datacenter/data/"
RAW_DIR = RAW / "ipeds"

FIRST_FALL = STUDY_START.year

# Universities outside the four study-area towns, included because their
# students are a visible part of study-area activity. Keyed by IPEDS UNITID.
NEARBY_INSTITUTIONS = {
    "164924": "Boston College",  # Chestnut Hill (Newton), next to Brighton
    "168148": "Tufts University",  # Medford/Somerville line
}

# Degree-granting public, private nonprofit and private for-profit
# institutions (2- and 4-year). Excludes administrative units (0) and
# less-than-2-year schools (7-9).
KEPT_SECTORS = {1, 2, 3, 4, 5, 6}

# EFALEVEL / EFDELEV codes used below.
LEVELS = {
    1: "total",
    2: "undergrad",
    12: "graduate",
    21: "full_time",
    22: "full_time_undergrad",
    32: "full_time_graduate",
    41: "part_time",
}
DIST_LEVELS = {1: "distance_only", 2: "distance_only_undergrad", 12: "distance_only_graduate"}


# ---------------------------------------------------------------- download


def download(name: str) -> bytes | None:
    """Return the zip for an IPEDS file, or None if it is not published."""
    path = RAW_DIR / f"{name}.zip"
    if path.exists():
        return path.read_bytes()
    resp = http.get(BASE_URL + f"{name}.zip", timeout=120)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return resp.content


def read_ipeds_csv(zip_bytes: bytes) -> pl.DataFrame:
    """Read the CSV in an IPEDS zip, preferring the revised (_rv) version.

    Every column is read as text; names are stripped and upper-cased.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        revised = [n for n in csvs if n.lower().endswith("_rv.csv")]
        name = (revised or csvs)[0]
        data = zf.read(name)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    df = pl.read_csv(io.StringIO(text), infer_schema=False)
    return df.rename({c: c.strip().upper() for c in df.columns}).with_columns(
        pl.col(pl.Utf8).str.strip_chars()
    )


def available_falls() -> list[int]:
    """Falls from FIRST_FALL onward for which the enrollment file exists."""
    falls = []
    for year in range(FIRST_FALL, date.today().year + 1):
        if download(f"EF{year}A") is None:
            break
        falls.append(year)
    return falls


# --------------------------------------------------------------- transform


def directory(hd: pl.DataFrame, year: int) -> pl.DataFrame:
    return hd.select(
        pl.col("UNITID").alias("unitid"),
        pl.col("INSTNM").alias("name"),
        pl.col("CITY").alias("city"),
        pl.col("SECTOR").cast(pl.Int64, strict=False).alias("sector"),
        pl.col("DEGGRANT").cast(pl.Int64, strict=False).alias("degree_granting"),
        pl.col("LATITUDE").cast(pl.Float64, strict=False).alias("lat"),
        pl.col("LONGITUD").cast(pl.Float64, strict=False).alias("lng"),
        pl.lit(year).alias("fall_year"),
    )


def latest_directory(directories: pl.DataFrame) -> pl.DataFrame:
    """Most recent directory entry for each institution, with its year span."""
    span = directories.group_by("unitid").agg(
        pl.col("fall_year").min().alias("first_fall"),
        pl.col("fall_year").max().alias("last_fall"),
    )
    latest = directories.sort("fall_year", descending=True).group_by("unitid", maintain_order=True).first()
    return latest.drop("fall_year").join(span, on="unitid")


def select_institutions(latest: pl.DataFrame, towns) -> pl.DataFrame:
    """Degree-granting institutions located in the study area, plus nearby ones."""
    located = assign(latest, towns)
    nearby = pl.col("unitid").is_in(list(NEARBY_INSTITUTIONS))
    eligible = (pl.col("degree_granting") == 1) & pl.col("sector").is_in(list(KEPT_SECTORS))
    selected = located.filter(eligible & (pl.col("municipality").is_in(STUDY_AREA) | nearby))

    missing = set(NEARBY_INSTITUTIONS) - set(selected["unitid"])
    if missing:
        raise ValueError(f"Nearby institutions not found in IPEDS: {[NEARBY_INSTITUTIONS[m] for m in missing]}")
    return (
        selected.with_columns(pl.col("municipality").is_in(STUDY_AREA).alias("in_study_area"))
        .select("unitid", "name", "city", "municipality", "in_study_area", "sector", "lat", "lng", "first_fall", "last_fall")
        .sort("unitid")
    )


def enrollment(ef: pl.DataFrame, year: int) -> pl.DataFrame:
    """One row per institution: headcount by level and attendance status."""
    long = ef.select(
        pl.col("UNITID").alias("unitid"),
        pl.col("EFALEVEL").cast(pl.Int64).alias("level"),
        pl.col("EFTOTLT").cast(pl.Int64, strict=False).alias("students"),
    ).filter(pl.col("level").is_in(list(LEVELS)))
    wide = long.with_columns(pl.col("level").replace_strict(LEVELS).alias("level")).pivot(
        on="level", index="unitid", values="students"
    )
    for col in LEVELS.values():
        if col not in wide.columns:
            wide = wide.with_columns(pl.lit(None, dtype=pl.Int64).alias(col))
    return wide.select("unitid", *LEVELS.values()).with_columns(
        pl.col(*LEVELS.values()).fill_null(0),
        pl.lit(year).alias("fall_year"),
    )


def distance_only(dist: pl.DataFrame) -> pl.DataFrame:
    """Students enrolled exclusively in distance education, by level."""
    long = dist.select(
        pl.col("UNITID").alias("unitid"),
        pl.col("EFDELEV").cast(pl.Int64).alias("level"),
        pl.col("EFDEEXC").cast(pl.Int64, strict=False).alias("students"),
    ).filter(pl.col("level").is_in(list(DIST_LEVELS)))
    wide = long.with_columns(pl.col("level").replace_strict(DIST_LEVELS).alias("level")).pivot(
        on="level", index="unitid", values="students"
    )
    for col in DIST_LEVELS.values():
        if col not in wide.columns:
            wide = wide.with_columns(pl.lit(None, dtype=pl.Int64).alias(col))
    return wide.select("unitid", *DIST_LEVELS.values())


def with_in_person(df: pl.DataFrame) -> pl.DataFrame:
    """Students not enrolled exclusively online, i.e. expected to be present."""
    return df.with_columns(
        (pl.col("total") - pl.col("distance_only").fill_null(0)).alias("in_person"),
        (pl.col("undergrad") - pl.col("distance_only_undergrad").fill_null(0)).alias("in_person_undergrad"),
        (pl.col("graduate") - pl.col("distance_only_graduate").fill_null(0)).alias("in_person_graduate"),
    )


# ------------------------------------------------------------------- main


def main() -> None:
    falls = available_falls()
    print(f"IPEDS falls available: {falls[0]}-{falls[-1]}")

    directories, enrollments = [], []
    for year in falls:
        hd = download(f"HD{year}")
        if hd is None:
            raise FileNotFoundError(f"HD{year} is missing although EF{year}A exists")
        directories.append(directory(read_ipeds_csv(hd), year))

        ef = enrollment(read_ipeds_csv(download(f"EF{year}A")), year)
        dist_zip = download(f"EF{year}A_DIST")
        if dist_zip is not None:
            ef = ef.join(distance_only(read_ipeds_csv(dist_zip)), on="unitid", how="left").with_columns(
                pl.col(*DIST_LEVELS.values()).fill_null(0)
            )
        else:
            ef = ef.with_columns(pl.lit(None, dtype=pl.Int64).alias(c) for c in DIST_LEVELS.values())
        enrollments.append(ef)

    institutions = select_institutions(latest_directory(pl.concat(directories)), load_towns())
    names = institutions.select("unitid", "name", "municipality", "in_study_area")
    annual = (
        with_in_person(pl.concat(enrollments))
        .join(names, on="unitid", how="inner")
        .select("unitid", "name", "municipality", "in_study_area", "fall_year", *LEVELS.values(),
                *DIST_LEVELS.values(), "in_person", "in_person_undergrad", "in_person_graduate")
        .sort("unitid", "fall_year")
    )

    PROCESSED.mkdir(parents=True, exist_ok=True)
    institutions.write_csv(PROCESSED / "universities.csv")
    annual.write_csv(PROCESSED / "enrollment_annual.csv")

    totals = (
        annual.group_by("fall_year", "in_study_area")
        .agg(pl.col("total").sum(), pl.col("in_person").sum(), pl.len().alias("institutions"))
        .sort("fall_year", "in_study_area")
    )
    print(f"{len(institutions)} institutions ({institutions['in_study_area'].sum()} in the study area)")
    for row in totals.iter_rows(named=True):
        where = "study area" if row["in_study_area"] else "nearby    "
        print(f"  fall {row['fall_year']} {where}: {row['total']:>7,} enrolled, "
              f"{row['in_person']:>7,} not online-only, {row['institutions']} institutions")


if __name__ == "__main__":
    main()
