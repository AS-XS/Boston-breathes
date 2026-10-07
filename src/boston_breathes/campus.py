"""Match Bluebikes stations to nearby university campuses.

Campus locations come from two sources:
  * IPEDS: the main-campus coordinates of each study-area institution
    (data/processed/universities.csv);
  * City of Boston open data, "Colleges and Universities": campus locations in
    Boston, including secondary campuses that IPEDS does not list, such as
    Harvard's business and medical schools and Boston University's medical
    campus. Points without an IPEDS ID are linked to an institution through
    SECONDARY_CAMPUSES.

Only "major" institutions (typically at least MAJOR_MIN_STUDENTS students
present in person) define campus zones, so a station's zone reflects
proximity to a large student population:
  * campus - within CAMPUS_M of a major campus point
  * near   - within NEAR_M
  * away   - farther than NEAR_M
As with municipalities, stations are located month by month.

Outputs in data/processed/:
  * campus_points.csv                     every campus point used
  * bluebikes_station_campus.csv          each station's nearest major campus and zone
  * bluebikes_weekly_by_campus_zone.csv   weekly activity by zone, study-area stations
  * bluebikes_weekly_by_institution.csv   weekly activity of campus-zone stations,
                                          by nearest institution
"""

import io

import geopandas as gpd
import polars as pl

from boston_breathes import bluebikes as bb
from boston_breathes import http
from boston_breathes.municipalities import MA_CRS, UNKNOWN, assign, load_towns, station_months, tag_station_day
from boston_breathes.paths import PROCESSED, RAW, STUDY_AREA

BOSTON_CAMPUSES_URL = (
    "https://data.boston.gov/dataset/7624a2d6-ca74-4fc1-9bba-d6e89d4ca9c7/resource/"
    "f59456d7-4b3d-407d-9fcd-4a6ae473afcb/download/colleges_and_universities.csv"
)
BOSTON_CAMPUSES_PATH = RAW / "boston" / "colleges_and_universities.csv"

# Boston campus points that carry no IPEDS ID, linked to their institution.
# Administrative offices and athletic facilities are left out.
SECONDARY_CAMPUSES = {
    "Boston University Public Health": "164988",
    "Boston University School of Medicine": "164988",
    "Boston University School of Law": "164988",
    "Boston University Sargent College": "164988",
    "Boston University School of Management": "164988",
    "Boston University Research": "164988",
    "Harvard Business School": "166027",
    "Harvard Medical School": "166027",
    "Harvard University of Public Health": "166027",
    "Tufts University School of Medicine": "168148",
    "Simmons College": "167783",
}

MAJOR_MIN_STUDENTS = 1000
CAMPUS_M = 400  # about a five-minute walk
NEAR_M = 1000

ZONE_COLUMNS = {
    "campus_zone": UNKNOWN,
    "campus_unitid": None,
    "campus_institution": None,
    "campus_distance_m": None,
}


# ------------------------------------------------------------ campus points


def download_boston_campuses() -> str:
    resp = http.get(BOSTON_CAMPUSES_URL, timeout=120)
    resp.raise_for_status()
    BOSTON_CAMPUSES_PATH.parent.mkdir(parents=True, exist_ok=True)
    BOSTON_CAMPUSES_PATH.write_text(resp.text)
    return resp.text


def boston_campus_points(csv_text: str, unitids: set[str]) -> pl.DataFrame:
    """Boston campus points belonging to the selected institutions."""
    raw = pl.read_csv(io.StringIO(csv_text), infer_schema=False)
    df = raw.select(
        pl.col("SchoolId").str.strip_chars().alias("school_id"),
        pl.col("Name").str.strip_chars().alias("campus_name"),
        pl.col("POINT_Y").cast(pl.Float64, strict=False).alias("lat"),
        pl.col("POINT_X").cast(pl.Float64, strict=False).alias("lng"),
    ).with_columns(
        pl.when(pl.col("school_id").is_in(list(unitids)))
        .then(pl.col("school_id"))
        .otherwise(pl.col("campus_name").replace_strict(SECONDARY_CAMPUSES, default=None))
        .alias("unitid")
    )
    missing = set(SECONDARY_CAMPUSES) - set(df["campus_name"])
    if missing:
        raise ValueError(f"Secondary campuses not found in Boston data: {sorted(missing)}")
    return (
        df.filter(pl.col("unitid").is_not_null() & pl.col("lat").is_not_null() & (pl.col("lat") != 0))
        .select("unitid", "campus_name", "lat", "lng")
        .with_columns(pl.lit("boston_open_data").alias("source"))
    )


def typical_in_person(enrollment: pl.DataFrame) -> pl.DataFrame:
    """Median in-person enrollment of each institution across falls."""
    return enrollment.group_by("unitid").agg(pl.col("in_person").median().alias("typical_in_person"))


def campus_points(universities: pl.DataFrame, enrollment: pl.DataFrame, boston: pl.DataFrame) -> pl.DataFrame:
    """IPEDS main campuses plus Boston campus points, with institution size."""
    ipeds = universities.select(
        "unitid", pl.col("name").alias("campus_name"), "lat", "lng", pl.lit("ipeds").alias("source")
    )
    names = universities.select("unitid", pl.col("name").alias("institution"))
    return (
        pl.concat([ipeds, boston])
        .join(names, on="unitid", how="inner")
        .join(typical_in_person(enrollment), on="unitid", how="left")
        .with_columns((pl.col("typical_in_person") >= MAJOR_MIN_STUDENTS).fill_null(False).alias("major"))
        .select("unitid", "institution", "campus_name", "source", "lat", "lng", "typical_in_person", "major")
        .sort("unitid", "source", "campus_name")
    )


# ---------------------------------------------------------- station matching


def to_points(df: pl.DataFrame) -> gpd.GeoDataFrame:
    pdf = df.to_pandas()
    return gpd.GeoDataFrame(
        pdf, geometry=gpd.points_from_xy(pdf["lng"], pdf["lat"]), crs="EPSG:4326"
    ).to_crs(MA_CRS)


def zone_of(distance: pl.Expr) -> pl.Expr:
    return (
        pl.when(distance.is_null()).then(pl.lit(UNKNOWN))
        .when(distance <= CAMPUS_M).then(pl.lit("campus"))
        .when(distance <= NEAR_M).then(pl.lit("near"))
        .otherwise(pl.lit("away"))
    )


def nearest_campus(points: pl.DataFrame, campuses: pl.DataFrame) -> pl.DataFrame:
    """Add the nearest major campus, its distance and the resulting zone.

    `points` needs `lat` and `lng`; rows with missing or (0, 0) coordinates
    get zone "unknown".
    """
    major = campuses.filter(pl.col("major")).select(
        pl.col("unitid").alias("campus_unitid"), pl.col("institution").alias("campus_institution"), "lat", "lng"
    )
    indexed = points.with_row_index("_row")
    valid = indexed.filter(
        pl.col("lat").is_not_null() & pl.col("lng").is_not_null() & (pl.col("lat") != 0) & (pl.col("lng") != 0)
    )
    joined = gpd.sjoin_nearest(
        to_points(valid.select("_row", "lat", "lng")),
        to_points(major).drop(columns=["lat", "lng"]),
        how="left",
        distance_col="campus_distance_m",
    )
    # Ties (equidistant campus points) keep the first match.
    nearest = pl.from_pandas(
        joined[["_row", "campus_unitid", "campus_institution", "campus_distance_m"]].drop_duplicates("_row")
    ).with_columns(pl.col("_row").cast(pl.UInt32), pl.col("campus_distance_m").round(0))
    return (
        indexed.join(nearest, on="_row", how="left")
        .with_columns(zone_of(pl.col("campus_distance_m")).alias("campus_zone"))
        .drop("_row")
    )


# ------------------------------------------------------------------- main


def main() -> None:
    universities = pl.read_csv(PROCESSED / "universities.csv", schema_overrides={"unitid": pl.Utf8})
    enrollment = pl.read_csv(PROCESSED / "enrollment_annual.csv", schema_overrides={"unitid": pl.Utf8})
    boston = boston_campus_points(download_boston_campuses(), set(universities["unitid"]))
    campuses = campus_points(universities, enrollment, boston)
    campuses.write_csv(PROCESSED / "campus_points.csv")

    towns = load_towns()
    monthly = station_months()
    monthly_tags = nearest_campus(assign(monthly, towns), campuses)
    stations = nearest_campus(assign(bb.combine_stations([monthly.drop("month")]), towns), campuses)
    stations = stations.with_columns(pl.col("municipality").is_in(STUDY_AREA).alias("in_study_area"))
    stations.select(
        "station_id", "station_name", "lat", "lng", "municipality", "in_study_area",
        "campus_zone", "campus_institution", "campus_unitid", "campus_distance_m", "total_trips",
    ).sort("station_id").write_csv(PROCESSED / "bluebikes_station_campus.csv")

    station_day_all, _, _ = bb.load_station_day()
    tagged = tag_station_day(
        station_day_all, monthly_tags, stations, columns={"municipality": UNKNOWN, **ZONE_COLUMNS}
    ).filter(pl.col("municipality").is_in(STUDY_AREA))
    coverage_start, coverage_end = bb.coverage()

    by_zone = bb.build_weekly(tagged, coverage_start, coverage_end, by=("campus_zone",))
    by_zone.write_csv(PROCESSED / "bluebikes_weekly_by_campus_zone.csv")

    by_institution = bb.build_weekly(
        tagged.filter(pl.col("campus_zone") == "campus"),
        coverage_start, coverage_end, by=("campus_unitid", "campus_institution"),
    )
    by_institution.write_csv(PROCESSED / "bluebikes_weekly_by_institution.csv")

    print(f"Campus points: {len(campuses)} ({campuses['major'].sum()} at major institutions)")
    summary = (
        tagged.group_by("campus_zone").agg(pl.col("trips").sum())
        .join(stations.filter(pl.col("in_study_area")).group_by("campus_zone").agg(pl.len().alias("stations")),
              on="campus_zone", how="left")
        .with_columns((pl.col("trips") / pl.col("trips").sum() * 100).round(1).alias("pct_trips"))
        .sort("campus_zone")
    )
    print("Study-area trips by campus zone:")
    for zone, trips, n_stations, pct in summary.select("campus_zone", "trips", "stations", "pct_trips").iter_rows():
        print(f"  {zone:<8} {trips:>11,} trips ({pct:>5.1f}%), {n_stations or 0:>4} stations")


if __name__ == "__main__":
    main()
