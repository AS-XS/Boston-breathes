"""Assign Bluebikes stations to municipalities and split activity by town.

Boundaries are the Census cartographic "county subdivision" file for
Massachusetts: in Massachusetts county subdivisions are the cities and towns
themselves, and the cartographic version is clipped to the shoreline.

Some station IDs moved over the years (a few by more than 1 km), so each
station is assigned per month, using its location in that month.

Outputs in data/processed/:
  * bluebikes_station_municipalities.csv - each station's town (at its busiest
    location), distance to the nearest other town, and whether it ever
    changed town
  * bluebikes_weekly_by_municipality.csv - weekly activity per town
"""

import geopandas as gpd
import polars as pl
import requests

from boston_breathes import bluebikes as bb
from boston_breathes.paths import PROCESSED, RAW, STUDY_AREA

BOUNDARY_URL = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_25_cousub_500k.zip"
BOUNDARY_PATH = RAW / "boundaries" / "cb_2023_25_cousub_500k.zip"

# Massachusetts State Plane (meters), for distances.
MA_CRS = "EPSG:26986"
# Stations on piers or seawalls can fall just outside the shoreline-clipped
# boundaries; they are given the nearest town within this distance.
SNAP_M = 100
# How far to look for a neighbouring town when measuring border distance.
NEIGHBOUR_M = 1000
UNKNOWN = "unknown"


def download_boundaries() -> None:
    if BOUNDARY_PATH.exists():
        return
    BOUNDARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(BOUNDARY_URL, timeout=120)
    resp.raise_for_status()
    BOUNDARY_PATH.write_bytes(resp.content)


def load_towns() -> gpd.GeoDataFrame:
    download_boundaries()
    towns = gpd.read_file(BOUNDARY_PATH)[["NAME", "geometry"]]
    return towns.rename(columns={"NAME": "municipality"})


def assign(points: pl.DataFrame, towns: gpd.GeoDataFrame) -> pl.DataFrame:
    """Add municipality, border_distance_m and nearest_other_municipality.

    `points` needs `lat` and `lng` columns. Missing or (0, 0) coordinates, and
    points more than SNAP_M from any town, get municipality "unknown".
    A point exactly on a border goes to the alphabetically first town.
    """
    towns = towns.to_crs(MA_CRS).sort_values("municipality").reset_index(drop=True)
    pdf = points.with_row_index("_row").to_pandas()
    valid = pdf["lat"].notna() & pdf["lng"].notna() & (pdf["lat"] != 0) & (pdf["lng"] != 0)
    pts = gpd.GeoDataFrame(
        pdf.loc[valid, ["_row"]],
        geometry=gpd.points_from_xy(pdf.loc[valid, "lng"], pdf.loc[valid, "lat"]),
        crs="EPSG:4326",
    ).to_crs(MA_CRS)

    inside = gpd.sjoin(pts, towns, predicate="intersects", how="inner")
    inside = inside.sort_values(["_row", "municipality"]).drop_duplicates("_row")
    rest = pts[~pts["_row"].isin(inside["_row"])]
    snapped = gpd.sjoin_nearest(rest, towns, how="inner", max_distance=SNAP_M)
    snapped = snapped.sort_values(["_row", "municipality"]).drop_duplicates("_row")
    found = dict(zip(inside["_row"], inside["municipality"])) | dict(zip(snapped["_row"], snapped["municipality"]))

    # Distance from each point to the nearest town other than its own.
    near = gpd.sjoin(
        pts.assign(geometry=pts.buffer(NEIGHBOUR_M), point=pts.geometry),
        towns.assign(town_geom=towns.geometry),
        predicate="intersects",
        how="inner",
    )
    near = near[near["municipality"] != near["_row"].map(found)]
    near["dist"] = gpd.GeoSeries(near["point"], crs=MA_CRS).distance(gpd.GeoSeries(near["town_geom"], crs=MA_CRS))
    nearest = near.sort_values(["_row", "dist"]).drop_duplicates("_row").set_index("_row")

    rows = pdf["_row"]
    return points.with_columns(
        pl.Series("municipality", [found.get(r, UNKNOWN) for r in rows], dtype=pl.Utf8),
        pl.Series("border_distance_m", [nearest["dist"].get(r) for r in rows], dtype=pl.Float64).round(0),
        pl.Series("nearest_other_municipality", [nearest["municipality"].get(r) for r in rows], dtype=pl.Utf8),
    )


def station_months() -> pl.DataFrame:
    """Each station's location in each month it was used."""
    return pl.concat([
        pl.read_parquet(f).with_columns(pl.lit(f.stem).alias("month"))
        for f in sorted((bb.INTERIM_DIR / "stations").glob("*.parquet"))
    ])


def tag_station_day(
    station_day_all: pl.DataFrame,
    monthly: pl.DataFrame,
    overall: pl.DataFrame,
    columns: dict[str, object] | None = None,
) -> pl.DataFrame:
    """Add station attributes (by default the municipality) to station-day rows.

    `columns` maps each attribute to its value when nothing is known. Uses the
    station's value in that month (from `monthly`, keyed by station_id and
    month), falling back to its value at its busiest location (`overall`), and
    the default for trips without a station.
    """
    columns = columns or {"municipality": UNKNOWN}
    by_month = monthly.select("station_id", "month", *[pl.col(c).alias(f"_m_{c}") for c in columns])
    by_station = overall.select("station_id", *[pl.col(c).alias(f"_o_{c}") for c in columns])
    tagged = (
        station_day_all.with_columns(pl.col("date").dt.strftime("%Y%m").alias("month"))
        .join(by_month, on=["station_id", "month"], how="left")
        .join(by_station, on="station_id", how="left")
    )
    return tagged.with_columns(
        pl.coalesce(f"_m_{c}", f"_o_{c}", pl.lit(default)).alias(c) for c, default in columns.items()
    ).drop("month", *[f"_m_{c}" for c in columns], *[f"_o_{c}" for c in columns])


def main() -> None:
    towns = load_towns()

    monthly = station_months()
    monthly_towns = assign(monthly, towns)
    stations = bb.combine_stations([monthly.drop("month")])
    station_towns = assign(stations, towns)

    changed = monthly_towns.group_by("station_id").agg(
        (pl.col("municipality").n_unique() > 1).alias("changed_municipality")
    )
    station_towns = (
        station_towns.join(changed, on="station_id", how="left")
        .with_columns(pl.col("municipality").is_in(STUDY_AREA).alias("in_study_area"))
        .sort("station_id")
    )
    station_towns.write_csv(PROCESSED / "bluebikes_station_municipalities.csv")

    station_day_all, _, _ = bb.load_station_day()
    tagged = tag_station_day(station_day_all, monthly_towns, station_towns)
    coverage_start, coverage_end = bb.coverage()
    weekly = bb.build_weekly(tagged, coverage_start, coverage_end, by=("municipality",)).with_columns(
        pl.col("municipality").is_in(STUDY_AREA).alias("in_study_area")
    )
    weekly.write_csv(PROCESSED / "bluebikes_weekly_by_municipality.csv")

    share = (
        tagged.group_by("municipality").agg(pl.col("trips").sum())
        .with_columns((pl.col("trips") / pl.col("trips").sum() * 100).round(2).alias("pct"))
        .sort("trips", descending=True)
    )
    print("Trips by municipality:")
    for m, trips, pct in share.iter_rows():
        print(f"  {m:<20} {trips:>11,} {pct:>6.2f}%")
    print(f"Stations that changed municipality: {station_towns['changed_municipality'].sum()}")


if __name__ == "__main__":
    main()
