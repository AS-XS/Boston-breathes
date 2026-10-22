"""Student riders at MBTA stations, from the MBTA System-Wide Passenger Survey.

Source: MassDOT/MBTA open data, "MBTA 2025 System-Wide Passenger Survey"
(CC0), the Rider Census. The MBTA surveys riders every year and pools the
2022-2025 responses so that results are reliable at the station level. Each
row gives the weighted share of riders in one category of one question, for
one reporting group, with the number of respondents and the margin of error.
Heavy-rail stations are mostly their own reporting group; Green Line surface
stops are grouped.

Four categories describe students:
  * fare product "Semester Pass": the MBTA pass sold through colleges and
    universities, so it marks college students (many others pay per ride or
    with passes their university provides, so it undercounts them);
  * reduced fare "Student (S-Card or M7)": middle- and high-school students;
  * trip purpose "Home-based School": trips between home and school, by
    college and school students alike;
  * age "18 to 25".

Gated stations are matched to reporting groups by name. A few groups cover
two gated stations, and stations served by two lines (e.g. Park Street) have
one group per line, which are combined weighting by respondents. The
Mattapan trolley is reported as one line. Silver Line stations are left out
(the survey reports Silver Line routes, not stations), and Beachmont has no
group.

Outputs in data/processed/:
  * mbta_survey_groups.csv      the four categories for each rapid-transit reporting group
  * mbta_survey_by_station.csv  the same shares for each gated station that has a group
"""

import io

import polars as pl

from boston_breathes import http
from boston_breathes.paths import PROCESSED, RAW

QUERY_URL = ("https://services1.arcgis.com/ceiitspzDAHrdGO1/arcgis/rest/services/"
             "MBTA_2025_System-Wide_Passenger_Survey/FeatureServer/0/query")
RAW_PATH = RAW / "mbta" / "passenger_survey_2025.csv"
PAGE = 1000  # the service's maximum records per request
FIELDS = ["aggregation_level", "service_mode", "reporting_group", "measure_group", "measure", "category",
          "weighted_percent", "n", "margin_of_error"]
SCHEMA = {f: pl.Float64 if f in ("weighted_percent", "margin_of_error") else pl.Int64 if f == "n" else pl.Utf8
          for f in FIELDS}

CATEGORIES = {
    ("Fare Product", "Semester Pass"): "semester_pass",
    ("Reduced Fares", "Student (S-Card or M7)"): "school_student_fare",
    ("Trip Purpose", "Home-based School"): "school_trip",
    ("Age", "18 to 25"): "age_18_25",
}
# Reporting groups whose names differ from the gated stations they cover.
GROUP_STATIONS = {
    "Lechmere and Science Park/West End": ["Lechmere", "Science Park"],
    "East Somerville and Gilman Square": ["East Somerville", "Gilman Square"],
    "Magoun Square and Ball Square": ["Magoun Square", "Ball Square"],
    "Suffolk Downs and Orient Heights": ["Suffolk Downs"],  # Orient Heights has its own group
    "Fenway and Longwood": ["Longwood"],
    "Union": ["Union Square"],
    "State": ["State Street"],
    "All Mattapan Trolley": ["Mattapan Line"],
}
RAPID_TRANSIT = "Rapid Transit or Bus Rapid Transit - "


def download() -> pl.DataFrame:
    """Every row of the survey table, fetched a page at a time."""
    rows, offset = [], 0
    while True:
        resp = http.get(QUERY_URL, params={
            "where": "1=1", "outFields": ",".join(FIELDS), "orderByFields": "ObjectId",
            "resultOffset": offset, "resultRecordCount": PAGE, "f": "json"}, timeout=120)
        resp.raise_for_status()
        page = resp.json()
        if "error" in page:
            raise RuntimeError(f"{QUERY_URL}: {page['error']}")
        rows += [f["attributes"] for f in page["features"]]
        if not page.get("exceededTransferLimit"):
            break
        offset += PAGE
    df = pl.DataFrame(rows, schema=SCHEMA)
    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.write_csv(RAW_PATH)
    return df


def student_groups(survey: pl.DataFrame) -> pl.DataFrame:
    """The student categories for each rapid-transit reporting group (line, group, measure).

    A category no respondent in a group chose has no row; its share is 0 when
    the group has rows for other answers to the same question.
    """
    keys = pl.DataFrame([{"measure": m, "category": c, "measure_key": k} for (m, c), k in CATEGORIES.items()])
    rapid = survey.filter(
        ((pl.col("aggregation_level") == "Reporting Group") | (pl.col("reporting_group") == "All Mattapan Trolley"))
        & pl.col("service_mode").str.starts_with(RAPID_TRANSIT)
        & ~pl.col("service_mode").str.ends_with("Silver Line BRT (includes SL1/SL2/SL3)")
    )
    asked = rapid.group_by("service_mode", "reporting_group", "measure").agg(pl.col("n").first())
    answers = rapid.select("service_mode", "reporting_group", "measure", "category", "weighted_percent", "margin_of_error")
    return (
        asked.join(keys, on="measure", how="inner")
        .join(answers, on=["service_mode", "reporting_group", "measure", "category"], how="left")
        .select(pl.col("service_mode").str.strip_prefix(RAPID_TRANSIT).alias("line"), "reporting_group",
                "measure_key", pl.col("weighted_percent").fill_null(0.0).alias("share"), "n", "margin_of_error")
        .sort("line", "reporting_group", "measure_key")
    )


def by_station(groups: pl.DataFrame, stations: pl.DataFrame) -> pl.DataFrame:
    """Shares for each gated station, combining a station's groups on several lines by respondents."""
    names = set(stations["station_name"])
    mapping = pl.DataFrame(
        [{"reporting_group": g, "station_name": s} for g in groups["reporting_group"].unique()
         for s in GROUP_STATIONS.get(g, [g]) if s in names],
        schema={"reporting_group": pl.Utf8, "station_name": pl.Utf8},
    )
    matched = groups.join(mapping, on="reporting_group", how="inner")
    shares = (
        matched.group_by("station_name", "measure_key")
        .agg(((pl.col("share") * pl.col("n")).sum() / pl.col("n").sum()).round(4).alias("share"),
             pl.col("n").sum().alias("n"))
        .pivot(on="measure_key", index="station_name", values=["share", "n"])
    )
    lines = matched.group_by("station_name").agg(
        pl.concat_str("line", pl.lit(": "), "reporting_group").unique().sort().str.join("; ").alias("survey_groups"))
    return (
        stations.select("stop_id", "station_name", "in_study_area", "campus_zone")
        .join(lines, on="station_name", how="inner")
        .join(shares, on="station_name", how="inner")
        .rename({f"share_{k}": f"{k}_share" for k in CATEGORIES.values()}
                | {f"n_{k}": f"{k}_respondents" for k in CATEGORIES.values()})
        .select("stop_id", "station_name", "in_study_area", "campus_zone", "survey_groups",
                *[f"{k}_share" for k in CATEGORIES.values()], *[f"{k}_respondents" for k in CATEGORIES.values()])
        .sort("station_name")
    )


def main() -> None:
    survey = download() if not RAW_PATH.exists() else pl.read_csv(RAW_PATH, schema=SCHEMA)
    groups = student_groups(survey)
    stations = pl.read_csv(PROCESSED / "mbta_stations.csv")
    out = by_station(groups, stations)
    groups.write_csv(PROCESSED / "mbta_survey_groups.csv")
    out.write_csv(PROCESSED / "mbta_survey_by_station.csv")
    missing = sorted(set(stations["station_name"]) - set(out["station_name"]))
    print(f"MBTA passenger survey: {groups['reporting_group'].n_unique()} rapid-transit groups, "
          f"{len(out)} of {len(stations)} gated stations matched; not matched: {', '.join(missing)}")
    print(out.sort("semester_pass_share", descending=True).select(
        "station_name", "semester_pass_share", "school_trip_share", "age_18_25_share").head(10))


if __name__ == "__main__":
    main()
