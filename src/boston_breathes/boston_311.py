"""Boston 311 service requests from residents, by day and campus zone.

A 311 request records where and when someone asked the City of Boston for a
service: missed trash, a noise complaint, a parking problem, a pothole.
Requests that residents make (by phone, with the app, by self-service or on
Twitter; not those logged by City staff) show that people are there, however
they get around, so they give a signal of presence that does not depend on
travel. 311 covers the City of Boston only.

Each request is placed in a campus zone with the same rules as stations
(campus.py): campus (within 400 m of a large campus), near (to 1 km) or away.

Source: data.boston.gov, "311 Service Requests", one table per calendar year,
read through the portal's SQL interface (date, location and source only), in
pages, and cached in data/raw/boston_311/ (not committed). In 2026 the City
moved to a new 311 system, published as a separate table from July 2026 with
other fields; it is not used, and self-service requests stop in May 2026, so
2026 is not comparable with earlier years for this signal.

Output: data/processed/boston_311_daily.csv  resident requests per day and campus zone
"""

import polars as pl

from boston_breathes import http
from boston_breathes.campus import nearest_campus
from boston_breathes.paths import PROCESSED, RAW

API = "https://data.boston.gov/api/3/action/"
PACKAGE = "311-service-requests"
RESIDENT_SOURCES = ["Constituent Call", "Citizens Connect App", "Self Service", "Twitter"]
YEARS = range(2015, 2027)
PAGE = 32000  # the portal's largest page
RAW_DIR = RAW / "boston_311"


def year_tables() -> dict[int, str]:
    """The table (resource) ID of each year's requests."""
    resp = http.get(API + "package_show", params={"id": PACKAGE}, timeout=60)
    resp.raise_for_status()
    out = {}
    for r in resp.json()["result"]["resources"]:
        name = (r.get("name") or "").upper()
        if r.get("datastore_active") and name.startswith("311 SERVICE REQUESTS - ") and name[-4:].isdigit():
            out[int(name[-4:])] = r["id"]
    return out


def download_year(year: int, table: str) -> pl.DataFrame:
    """Date, location and source of every resident request in a year, cached."""
    path = RAW_DIR / f"{year}.parquet"
    if path.exists():
        return pl.read_parquet(path)
    sources = ", ".join(f"'{s}'" for s in RESIDENT_SOURCES)
    frames, offset = [], 0
    while True:
        sql = (f'SELECT _id, open_dt, latitude, longitude, source FROM "{table}" WHERE source IN ({sources}) '
               f"ORDER BY _id LIMIT {PAGE} OFFSET {offset}")
        resp = http.get(API + "datastore_search_sql", params={"sql": sql}, timeout=300)
        resp.raise_for_status()
        records = resp.json()["result"]["records"]
        if not records:
            break
        frames.append(pl.DataFrame(records, schema={"_id": pl.Int64, "open_dt": pl.Utf8, "latitude": pl.Utf8,
                                                    "longitude": pl.Utf8, "source": pl.Utf8}))
        offset += PAGE
    df = pl.concat(frames).select(
        pl.col("open_dt").str.slice(0, 10).str.to_date("%Y-%m-%d").alias("date"),
        pl.col("latitude").cast(pl.Float64, strict=False).alias("lat"),
        pl.col("longitude").cast(pl.Float64, strict=False).alias("lng"), "source")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    return df


def daily_by_zone(requests: pl.DataFrame, campuses: pl.DataFrame) -> pl.DataFrame:
    """Requests per day and campus zone; each distinct location is placed once."""
    places = requests.select("lat", "lng").unique().drop_nulls()
    zones = nearest_campus(places, campuses).select("lat", "lng", "campus_zone")
    return (requests.join(zones, on=["lat", "lng"], how="left")
            .with_columns(pl.col("campus_zone").fill_null("unknown"))
            .group_by("date", "campus_zone").agg(pl.len().alias("requests"))
            .sort("date", "campus_zone"))


def main() -> None:
    tables = year_tables()
    requests = pl.concat([download_year(year, tables[year]) for year in YEARS])
    campuses = pl.read_csv(PROCESSED / "campus_points.csv", schema_overrides={"unitid": pl.Utf8})
    out = daily_by_zone(requests, campuses)
    out.write_csv(PROCESSED / "boston_311_daily.csv")
    totals = out.group_by(pl.col("date").dt.year().alias("year"), "campus_zone").agg(pl.col("requests").sum())
    print(totals.pivot(on="campus_zone", index="year", values="requests").sort("year"))


if __name__ == "__main__":
    main()
