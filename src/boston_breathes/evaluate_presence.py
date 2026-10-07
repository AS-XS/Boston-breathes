"""Check the Student Presence Index against observed city activity.

No model is fitted to predict activity; these are descriptive checks of
whether the index moves with activity where students should matter most.

1. Campus share of activity: the weekly ratio of Bluebikes trips (and MBTA
   entries) at campus-zone stations to those at away-zone stations should
   rise and fall with the index. Weather, season and citywide events affect
   both zones, so the ratio mostly removes them. Correlations are computed
   within each year, because network growth shifts the ratio between years.
2. Break dips: for each university with its own calendar, the share of
   study-area Bluebikes trips at stations on its campus should drop in break
   weeks compared with nearby full in-session weeks.
3. Street counts: at locations counted in both June and September of the
   same year, bicycle counts near campuses should rise more than elsewhere.
4. Summer: whether the summer-break share adds to the index in explaining
   the campus share (least squares with a constant per year).
5. Bounds on students away: how many students leave is to be measured from
   activity, not assumed. The data bound it: at most every resident college
   student (ACS) is away when out of session, and in Cambridge at least the
   students living in dormitories leave in summer (Town Gown reports).

Output: data/processed/presence_evaluation.csv, one row per check, subset
and metric.
"""

import numpy as np
import polars as pl

from boston_breathes.paths import PROCESSED

CAMPUS, AWAY = "campus", "away"
BREAK_MAX_DAYS = 2  # a break week has at most this many days in session
REFERENCE_WEEKS = 3  # in-session weeks this close to a break are its reference


def row(check: str, subset: str, metric: str, value: float | None, n: int) -> dict:
    return {"check": check, "subset": subset, "metric": metric,
            "value": None if value is None else round(float(value), 4), "n": n}


# ------------------------------------------------------------ campus share


def campus_ratio(weekly_by_zone: pl.DataFrame, value: str) -> pl.DataFrame:
    """Weekly log ratio of campus-zone to away-zone activity, complete weeks only."""
    zones = weekly_by_zone.filter(pl.col("complete_week") & pl.col("campus_zone").is_in([CAMPUS, AWAY]))
    wide = zones.pivot(on="campus_zone", index="week_start", values=value)
    return wide.filter((pl.col(CAMPUS) > 0) & (pl.col(AWAY) > 0)).select(
        "week_start", (pl.col(CAMPUS) / pl.col(AWAY)).log().alias("log_ratio")
    )


def correlations(ratio: pl.DataFrame, presence: pl.DataFrame, source: str) -> list[dict]:
    df = ratio.join(presence.select("week_start", "presence_index", "covid_period"), on="week_start")
    rows = []
    for period in ("normal", "remote_year"):
        sub = df.filter(pl.col("covid_period") == period)
        rows.append(row("campus_share", f"{source}, {period}", "corr_log_ratio_vs_index",
                        sub.select(pl.corr("log_ratio", "presence_index")).item(), len(sub)))
    by_year = (
        df.filter(pl.col("covid_period") == "normal")
        .group_by(pl.col("week_start").dt.year().alias("year"))
        .agg(pl.corr("log_ratio", "presence_index").alias("r"), pl.len().alias("n"))
        .filter(pl.col("n") >= 20)
        .sort("year")
    )
    for year, r, n in by_year.iter_rows():
        rows.append(row("campus_share", f"{source}, {year}", "corr_log_ratio_vs_index", r, n))
    rows.append(row("campus_share", f"{source}, normal years", "median_within_year_corr",
                    by_year["r"].median(), len(by_year)))
    return rows


# ------------------------------------------------------------ break dips


def break_dips(by_institution: pl.DataFrame, by_zone: pl.DataFrame, academic: pl.DataFrame,
               presence: pl.DataFrame) -> pl.DataFrame:
    """Change in each university's campus share of trips in break weeks versus nearby in-session weeks."""
    normal = presence.filter(pl.col("covid_period") == "normal").select("week_start")
    total = by_zone.filter(pl.col("complete_week")).group_by("week_start").agg(pl.col("trips").sum().alias("total"))
    share = (
        by_institution.filter(pl.col("complete_week"))
        .join(total, on="week_start")
        .join(normal, on="week_start")
        .select("week_start", pl.col("campus_unitid").alias("unitid"), (pl.col("trips") / pl.col("total")).alias("share"))
    )
    cal = academic.join(share, on=["week_start", "unitid"]).sort("unitid", "week_start")
    rows = []
    for (unitid, institution), g in cal.group_by("unitid", "institution"):
        g = g.sort("week_start")
        breaks = g.filter((pl.col("days_in_session") <= BREAK_MAX_DAYS) & (pl.col("summer_break_days") == 0))
        in_session = g.filter(pl.col("days_in_session") == 7)
        for wk, brk_share in breaks.select("week_start", "share").iter_rows():
            near = in_session.filter((pl.col("week_start") - wk).dt.total_days().abs() <= 7 * REFERENCE_WEEKS)
            if near.is_empty():
                continue
            kind = {11: "thanksgiving", 12: "winter", 1: "winter"}.get(wk.month, "spring")
            rows.append({"unitid": unitid, "institution": institution, "week_start": wk, "break": kind,
                         "change_pct": (brk_share / near["share"].mean() - 1) * 100})
    return pl.DataFrame(rows, schema={"unitid": pl.Utf8, "institution": pl.Utf8, "week_start": pl.Date,
                                      "break": pl.Utf8, "change_pct": pl.Float64})


def break_rows(dips: pl.DataFrame) -> list[dict]:
    summary = dips.group_by("institution", "break").agg(
        pl.col("change_pct").median().alias("median"), pl.len().alias("n")
    ).sort("institution", "break")
    rows = [row("break_dips", f"{inst}, {brk}", "median_change_in_campus_share_pct", m, n)
            for inst, brk, m, n in summary.iter_rows()]
    rows.append(row("break_dips", "all universities and breaks", "share_of_breaks_with_a_drop",
                    (dips["change_pct"] < 0).mean(), len(dips)))
    return rows


# ------------------------------------------------------------ street counts


def street_count_pairs(counts: pl.DataFrame) -> pl.DataFrame:
    """Log change from June to September at locations counted on weekdays in both months of a year."""
    weekday = counts.filter((pl.col("date").dt.weekday() <= 5) & pl.col("date").dt.month().is_in([6, 9]))
    monthly = weekday.group_by("count_id", "campus_zone", pl.col("date").dt.year().alias("year"),
                               pl.col("date").dt.month().alias("month")).agg(
        pl.col("bikes").mean(), pl.col("vehicles").mean()
    )
    june = monthly.filter(pl.col("month") == 6).drop("month")
    sept = monthly.filter(pl.col("month") == 9).drop("month")
    return june.join(sept, on=["count_id", "campus_zone", "year"], suffix="_sept").select(
        "count_id", "campus_zone", "year",
        (pl.col("bikes_sept") / pl.col("bikes")).log().alias("log_bikes"),
        (pl.col("vehicles_sept") / pl.col("vehicles")).log().alias("log_vehicles"),
    )


def street_rows(pairs: pl.DataFrame) -> list[dict]:
    rows = []
    for zone in (CAMPUS, "near", AWAY):
        sub = pairs.filter(pl.col("campus_zone") == zone)
        for mode in ("bikes", "vehicles"):
            vals = sub[f"log_{mode}"].drop_nulls().drop_nans()
            vals = vals.filter(vals.is_finite())
            rows.append(row("street_counts", f"{zone}, {mode}", "median_sept_vs_june_change_pct",
                            (np.exp(vals.median()) - 1) * 100 if len(vals) else None, len(vals)))
    return rows


# ------------------------------------------------------------ summer


def r_squared(y: np.ndarray, x: np.ndarray) -> tuple[float, np.ndarray]:
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    resid = y - x @ coef
    return 1 - resid.var() / y.var(), coef


def summer_rows(ratio: pl.DataFrame, presence: pl.DataFrame, source: str) -> list[dict]:
    df = (
        ratio.join(presence.select("week_start", "presence_index", "summer_break_share", "covid_period"),
                   on="week_start")
        .filter(pl.col("covid_period") == "normal")
        .with_columns(pl.col("week_start").dt.year().alias("year"))
        .drop_nulls()
    )
    years = df.select("year").to_dummies(columns=["year"]).to_numpy().astype(float)
    y = df["log_ratio"].to_numpy()
    base = np.column_stack([years, df["presence_index"].to_numpy()])
    r2_base, _ = r_squared(y, base)
    r2_full, coef = r_squared(y, np.column_stack([base, df["summer_break_share"].to_numpy()]))
    n = len(df)
    return [
        row("summer", source, "r2_year_and_index", r2_base, n),
        row("summer", source, "r2_year_index_and_summer", r2_full, n),
        row("summer", source, "summer_coefficient_log_ratio", coef[-1], n),
        row("summer", source, "index_coefficient_log_ratio", coef[-2], n),
    ]


# ------------------------------------------------------------ effective population


def bound_rows(presence: pl.DataFrame, residents: pl.DataFrame, town_gown: pl.DataFrame,
               year: int = 2019) -> list[dict]:
    """Upper and lower bounds on resident students away, from data only."""
    y = presence.filter(pl.col("week_start").dt.year() == year)
    most = y["max_students_away"].max()
    rows = [
        row("students_away_bounds", f"study area, {year}", "max_students_away", most, len(y)),
        row("students_away_bounds", f"study area, {year}", "max_students_away_pct_of_census",
            most / y["census_population"].mean() * 100, len(y)),
    ]
    cambridge = residents.filter((pl.col("municipality") == "Cambridge") & (pl.col("acs_year") == year))
    dorms = town_gown.filter(pl.col("fall_year") == year)
    if len(cambridge) and len(dorms):
        in_dorms = dorms["students_in_dorms"][0]
        resident = cambridge["undergrad"][0] + cambridge["graduate"][0]
        rows += [
            row("students_away_bounds", f"Cambridge, {year}-{(year + 1) % 100:02d}", "min_students_away_dorm_residents", in_dorms, 1),
            row("students_away_bounds", f"Cambridge, {year}-{(year + 1) % 100:02d}", "max_students_away_resident_students", resident, 1),
            row("students_away_bounds", f"Cambridge, {year}-{(year + 1) % 100:02d}", "min_share_away", in_dorms / resident, 1),
        ]
    return rows


# ------------------------------------------------------------ main


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    presence = read("student_presence_weekly.csv")
    bike_zone = read("bluebikes_weekly_by_campus_zone.csv")
    mbta_zone = read("mbta_weekly_by_campus_zone.csv")
    bike_inst = read("bluebikes_weekly_by_institution.csv", schema_overrides={"campus_unitid": pl.Utf8})
    academic = read("academic_weekly.csv", schema_overrides={"unitid": pl.Utf8})
    counts = read("boston_street_counts.csv")
    residents = read("student_residents_annual.csv")
    town_gown = read("cambridge_town_gown.csv")

    bike_ratio = campus_ratio(bike_zone, "trips")
    mbta_ratio = campus_ratio(mbta_zone, "entries")
    dips = break_dips(bike_inst, bike_zone, academic, presence)
    rows = (
        correlations(bike_ratio, presence, "Bluebikes")
        + correlations(mbta_ratio, presence, "MBTA")
        + break_rows(dips)
        + street_rows(street_count_pairs(counts))
        + summer_rows(bike_ratio, presence, "Bluebikes")
        + summer_rows(mbta_ratio, presence, "MBTA")
        + bound_rows(presence, residents, town_gown)
    )
    out = pl.DataFrame(rows, schema={"check": pl.Utf8, "subset": pl.Utf8, "metric": pl.Utf8,
                                     "value": pl.Float64, "n": pl.Int64})
    out.write_csv(PROCESSED / "presence_evaluation.csv")
    with pl.Config(tbl_rows=200, fmt_str_lengths=60, tbl_width_chars=160):
        print(out)


if __name__ == "__main__":
    main()
