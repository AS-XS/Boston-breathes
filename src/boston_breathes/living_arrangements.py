"""Where resident college students live: university housing, with their parents, or elsewhere.

Sources:
  * ACS 5-year Public Use Microdata Sample (PUMS), Massachusetts person
    records. For every college student (grade attending: undergraduate or
    graduate) it gives the relationship to the household: people in
    non-institutional group quarters, which for college students is college
    housing; children of the householder, i.e. students living with their
    parents; and everyone else (renters, roommates, owners, spouses).
    PUMS areas (PUMAs) hold at least 100,000 people: Boston is split into
    five, and the other towns are grouped with neighbours, some outside the
    study area. Files ending in 2015-2021 use the 2010 PUMAs and later ones
    the 2020 PUMAs; the 2015 and 2022 files mix two PUMA vintages, and only
    the records with the vintage most of them carry are used (2012-2015 and
    2018-2021).
  * 2020 Census redistricting data (table P5): population in college or
    university student housing in every Massachusetts town. It places a
    PUMA's dorm residents in the towns that have the dorms.
  * Resident college students per town (ACS table B14007,
    student_residents_annual.csv), which this step splits.

Method, for each study-area town and ACS 5-year period:

    in university housing = PUMS dorm residents of the town's PUMA group
                            x the town's share of the group's 2020 college housing
                            (at most the town's resident students)
    with parents          = (resident students - in university housing)
                            x the group's PUMS share living with parents
                              among students not in dorms
    elsewhere             = the rest

Each town is assigned to the PUMA that contains it (Boston to its five), and
PUMAs sharing a town form a group.

Outputs in data/processed/:
  * student_living_arrangements.csv  one row per town and ACS period
  * census_college_housing.csv       2020 college housing population per study-area town
"""

import io
import zipfile

import geopandas as gpd
import polars as pl

from boston_breathes import http
from boston_breathes.municipalities import BOUNDARY_PATH, download_boundaries
from boston_breathes.paths import CENSUS_GEOIDS, PROCESSED, RAW

PUMS_URL = "https://www2.census.gov/programs-surveys/acs/data/pums/{year}/5-Year/csv_pma.zip"
PUMA_URLS = {
    2010: "https://www2.census.gov/geo/tiger/TIGER2019/PUMA/tl_2019_25_puma10.zip",
    2020: "https://www2.census.gov/geo/tiger/TIGER2022/PUMA/tl_2022_25_puma20.zip",
}
PL_URL = ("https://www2.census.gov/programs-surveys/decennial/2020/data/"
          "01-Redistricting_File--PL_94-171/Massachusetts/ma2020.pl.zip")
RAW_DIR = RAW / "census"
LAST_2010_PUMA_YEAR = 2021  # 5-year files ending in 2015-2021 use 2010 PUMAs

COLLEGE_GRADES = (15, 16)  # SCHG: undergraduate, graduate or professional school
# Relationship codes: RELSHIPP from 2019 files, RELP before.
DORM = {"RELSHIPP": (38,), "RELP": (17,)}  # non-institutional group quarters
CHILD = {"RELSHIPP": (25, 26, 27), "RELP": (2, 3, 4)}  # biological, adopted or stepchild of the householder


def fetch(url: str, name: str, timeout: int = 900) -> bytes:
    """A zip file, downloaded once into data/raw/census/."""
    path = RAW_DIR / name
    if not path.exists():
        resp = http.get(url, timeout=timeout)
        resp.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(http.require_zip(resp.content, url))
    return path.read_bytes()


# ------------------------------------------------------------ 2020 census college housing


def college_housing_2020(pl_zip: bytes) -> pl.DataFrame:
    """College/university student housing population of every Massachusetts town (P5, item 8)."""
    z = zipfile.ZipFile(io.BytesIO(pl_zip))
    towns = {}
    for line in z.read("mageo2020.pl").decode("latin-1").splitlines():
        f = line.split("|")
        if f[2] == "060":  # county subdivision
            towns[f[7]] = f[8].removeprefix("0600000US")
    rows = []
    for line in z.read("ma000032020.pl").decode("latin-1").splitlines():
        f = line.split("|")
        if f[4] in towns:
            rows.append({"geoid": towns[f[4]], "college_housing_2020": int(f[12])})
    return pl.DataFrame(rows)


# ------------------------------------------------------------ PUMA groups


def puma_towns(pumas: gpd.GeoDataFrame, towns: gpd.GeoDataFrame) -> pl.DataFrame:
    """(puma, geoid) pairs: each town in the PUMA holding its interior point; a PUMA
    holding no town's point (Boston's) gets the town covering most of it."""
    points = towns.copy()
    points["geometry"] = towns.representative_point()
    inside = gpd.sjoin(points[["GEOID", "geometry"]], pumas[["puma", "geometry"]], predicate="within")
    pairs = set(zip(inside["puma"], inside["GEOID"]))
    without = set(pumas["puma"]) - {p for p, _ in pairs}
    if without:
        overlap = gpd.overlay(pumas[pumas["puma"].isin(without)][["puma", "geometry"]],
                              towns[["GEOID", "geometry"]], how="intersection", keep_geom_type=True)
        overlap["area"] = overlap.area
        best = overlap.sort_values("area").groupby("puma").tail(1)
        pairs |= set(zip(best["puma"], best["GEOID"]))
    return pl.DataFrame(sorted(pairs), schema=["puma", "geoid"], orient="row")


def groups(pairs: pl.DataFrame) -> pl.DataFrame:
    """Merge PUMAs that share a town: (puma, geoid, group), group = smallest PUMA code in it."""
    group = {p: p for p in pairs["puma"]}

    def root(p):
        while group[p] != p:
            p = group[p]
        return p

    for _, members in pairs.group_by("geoid"):
        first, *rest = sorted(members["puma"])
        for p in rest:
            a, b = root(first), root(p)
            group[max(a, b)] = min(a, b)
    return pairs.with_columns(pl.col("puma").map_elements(root, return_dtype=pl.Utf8).alias("group"))


# ------------------------------------------------------------ PUMS


def pums_counts(pums_zip: bytes, year: int) -> pl.DataFrame:
    """Weighted college students by PUMA: in dorms, children of the householder, other; and records used."""
    z = zipfile.ZipFile(io.BytesIO(pums_zip))
    name = next(n for n in z.namelist() if n.endswith(".csv"))
    header = z.open(name).readline().decode().strip().split(",")
    rel = "RELSHIPP" if "RELSHIPP" in header else "RELP"
    puma_columns = [c for c in ("PUMA", "PUMA00", "PUMA10", "PUMA20") if c in header]
    df = pl.read_csv(z.read(name), columns=[*puma_columns, "PWGTP", "SCHG", rel], infer_schema_length=0).cast(pl.Int64)
    # Files mixing two PUMA vintages mark the other vintage -9: use the column most records carry.
    puma = max(puma_columns, key=lambda c: (df[c] != -9).sum())
    vintage = {"PUMA00": 2000, "PUMA10": 2010, "PUMA20": 2020}.get(puma, 2010 if year <= LAST_2010_PUMA_YEAR else 2020)
    kept = df.filter(pl.col(puma) != -9)
    # The weights of all five years add up to the population; scale the kept years up to it.
    scale = df["PWGTP"].sum() / kept["PWGTP"].sum()
    return (
        kept.filter(pl.col("SCHG").is_in(COLLEGE_GRADES))
        .with_columns(pl.col(puma).cast(pl.Utf8).str.zfill(5).alias("puma"), pl.col("PWGTP") * scale)
        .group_by("puma")
        .agg(
            pl.col("PWGTP").filter(pl.col(rel).is_in(DORM[rel])).sum().alias("dorm"),
            pl.col("PWGTP").filter(pl.col(rel).is_in(CHILD[rel])).sum().alias("with_parents"),
            pl.col("PWGTP").filter(~pl.col(rel).is_in(DORM[rel] + CHILD[rel])).sum().alias("other"),
            pl.len().alias("records"),
        )
        .with_columns(pl.lit(year).alias("acs_year"), pl.lit(vintage).alias("vintage"))
    )


# ------------------------------------------------------------ towns


def split_towns(residents: pl.DataFrame, counts: pl.DataFrame, membership: pl.DataFrame,
                housing: pl.DataFrame) -> pl.DataFrame:
    """Resident students of each study-area town and period split into the three arrangements.

    residents: municipality, geoid, acs_year, college_students
    counts: puma, acs_year, vintage, dorm, with_parents, other, records
    membership: vintage, puma, geoid, group
    housing: geoid, college_housing_2020 (every town)
    """
    by_group = (
        counts.join(membership.select("vintage", "puma", "group").unique(), on=["vintage", "puma"], how="inner")
        .group_by("acs_year", "vintage", "group")
        .agg(pl.col("dorm", "with_parents", "other", "records").sum())
    )
    group_housing = (
        membership.select("vintage", "group", "geoid").unique()
        .join(housing, on="geoid", how="left")
        .group_by("vintage", "group").agg(pl.col("college_housing_2020").fill_null(0).sum().alias("group_housing"))
    )
    town_group = membership.select("vintage", "geoid", "group").unique()
    vintages = counts.select("acs_year", "vintage").unique()
    return (
        residents.join(vintages, on="acs_year", how="left")
        .join(town_group, on=["vintage", "geoid"], how="left")
        .join(by_group, on=["acs_year", "vintage", "group"], how="left")
        .join(group_housing, on=["vintage", "group"], how="left")
        .join(housing, on="geoid", how="left")
        .with_columns(
            pl.when(pl.col("group_housing") > 0)
            .then(pl.col("dorm") * pl.col("college_housing_2020") / pl.col("group_housing"))
            .otherwise(0.0)
            .clip(upper_bound=pl.col("college_students"))
            .round(0).cast(pl.Int64).alias("in_university_housing"),
            (pl.col("with_parents") / (pl.col("with_parents") + pl.col("other"))).alias("_parents_share"),
        )
        .with_columns(
            ((pl.col("college_students") - pl.col("in_university_housing")) * pl.col("_parents_share"))
            .round(0).cast(pl.Int64).alias("with_parents"),
        )
        .with_columns(
            (pl.col("college_students") - pl.col("in_university_housing") - pl.col("with_parents")).alias("elsewhere"),
        )
        .select(
            "municipality", "acs_year", "college_students", "in_university_housing", "with_parents", "elsewhere",
            pl.col("group").alias("puma_group"), pl.col("records").alias("pums_records"),
        )
        .sort("municipality", "acs_year")
    )


def main() -> None:
    download_boundaries()
    towns = gpd.read_file(BOUNDARY_PATH)[["GEOID", "geometry"]].to_crs(26986)
    housing = college_housing_2020(fetch(PL_URL, "ma2020.pl.zip"))
    membership = []
    for vintage, url in PUMA_URLS.items():
        pumas = gpd.read_file(io.BytesIO(fetch(url, url.rsplit("/", 1)[-1]))).to_crs(26986)
        pumas["puma"] = pumas[f"PUMACE{vintage % 100:02d}"]
        membership.append(groups(puma_towns(pumas, towns)).with_columns(pl.lit(vintage).alias("vintage")))
    membership = pl.concat(membership)

    residents = pl.read_csv(PROCESSED / "student_residents_annual.csv")
    names = pl.DataFrame({"geoid": list(CENSUS_GEOIDS), "municipality": list(CENSUS_GEOIDS.values())})
    town_geoids = names.filter(pl.col("geoid").is_in(list(towns["GEOID"])))
    residents = residents.join(town_geoids, on="municipality", how="left")
    counts = pl.concat([pums_counts(fetch(PUMS_URL.format(year=y), f"pums_{y}_csv_pma.zip"), y)
                        for y in sorted(residents["acs_year"].unique())])

    out = split_towns(residents.select("municipality", "geoid", "acs_year", "college_students"), counts,
                      membership, housing)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    out.write_csv(PROCESSED / "student_living_arrangements.csv")
    housing.join(town_geoids, on="geoid", how="inner").select("municipality", "college_housing_2020").sort(
        "municipality").write_csv(PROCESSED / "census_college_housing.csv")

    print(f"Student living arrangements: {out['municipality'].n_unique()} towns, ACS {out['acs_year'].min()}-"
          f"{out['acs_year'].max()}")
    print(out.group_by("acs_year").agg(pl.col("college_students", "in_university_housing", "with_parents",
                                               "elsewhere").sum()).sort("acs_year"))


if __name__ == "__main__":
    main()
