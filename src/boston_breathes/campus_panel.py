"""Pooled campus model: does travel at each campus follow its own university's calendar?

Universities do not break at the same time: spring breaks fall in different
weeks, terms start before or after Labor Day, and some universities have a
February break or a January term. So in the same week some campuses are in
session and others are not. Comparing campuses within the same week removes
everything the week shares (weather, season, holidays, visitors, school
vacations), which the study-area models (model_comparison.py) could not tell
apart from the students, since students leave at the same time of year
every year.

Units:
  * Bluebikes: trips started at the stations of each campus
    (bluebikes_weekly_by_institution.csv);
  * MBTA: entries at each gated station in a campus zone, linked to the
    university of the nearest campus.
Two versions:
  * all universities, assumed calendars: every campus with travel data, each
    with the calendar the Student Presence Index assumes for its university:
    its own collected calendar, in verified years and in years whose dates
    are estimated from its other years (keeping their offset from Labor Day
    or Martin Luther King Jr. Day), or, without a collected calendar, the
    typical calendar (the enrollment-weighted average of the collected ones).
    Tufts' Bluebikes stations and the Tufts Medical Center station are at its
    Boston health-sciences campus, whose medical and dental schools follow
    other calendars than the main-campus calendar collected, so they are
    given the typical calendar and reported apart;
  * verified calendars only: universities with their own calendar, academic
    years with no estimated date that sets the weeks in session (as in the
    break check of evaluate_presence.py), universities with at least 52 such
    weeks (one year), Bluebikes campuses with a median of at least 100 trips
    a week, and without Tufts' health-sciences campus.
Both use weeks outside the COVID-19 period and complete weeks with activity.
MBTA station-weeks with a closed day (mbta.py: the station was shut for
repairs, often on weekends and in summer, or has no entries that day) are
left out, since the fall in entries is the closure, not students.

Model, by least squares on the logarithm of activity:

  log activity[unit, week] = level[week] + level[unit, year]
                             + b x session_share[university, week]
                             (+ d x log stations in use, Bluebikes)

The week level is shared by all campuses; the unit-year level absorbs each
campus's size and new stations. session_share is the share of the week the
university is in session (student_presence_by_institution.csv). The levels
are removed by alternating projections, then the rest is fitted by least
squares. Standard errors are clustered by calendar month: all units in a
month form one cluster, which allows for neighbouring weeks and nearby
campuses being alike. The effect is given as the percentage change of
activity from out of session (0) to in session (1).

Also estimated:
  * the effect for each university, also with each unit's own seasonal cycle;
  * a variant with a smooth annual cycle for each unit (two pairs of sine and
    cosine terms), since campus areas can differ in how busy they are in
    summer for reasons other than students;
  * placebo: each unit given another calendar instead of its own, for every
    rotation of the list of calendars (the typical calendar counts as one).
    If activity follows each university's own students, the wrong calendars
    should give smaller effects; not zero, since calendars largely agree.

Output: data/processed/campus_panel.csv, one row per source, model, term and
metric.
"""

import math

import numpy as np
import polars as pl

from boston_breathes.evaluate_presence import verified_weeks
from boston_breathes.paths import PROCESSED

MIN_MEDIAN_TRIPS = 100
MIN_UNIVERSITY_WEEKS = 52
OTHER_CAMPUS = {  # at Tufts' Boston health-sciences campus, which follows other calendars than its main campus
    ("Bluebikes", "168148"): "Tufts University, health-sciences campus",
    ("MBTA", "place-tumnl"): "Tufts University, health-sciences campus",
}
VERIFIED, ESTIMATED, TYPICAL = "verified", "estimated", "typical"
ALL, VERIFIED_ONLY = "all universities, assumed calendars", "verified calendars only"
HARMONICS = 2
SEASON = [f"{f}{k}" for k in range(1, HARMONICS + 1) for f in ("sin", "cos")]


def calendars(presence_by_inst: pl.DataFrame, academic_weekly: pl.DataFrame, calendar: pl.DataFrame,
              presence: pl.DataFrame) -> pl.DataFrame:
    """Session share of every university in normal weeks, with the kind of calendar behind it (verified,
    estimated or typical) and a key naming the calendar: the university's own, or the typical one."""
    verified = verified_weeks(academic_weekly, calendar).select("unitid", "week_start").with_columns(
        pl.lit(True).alias("verified"))
    normal = presence.filter(pl.col("covid_period") == "normal").select("week_start")
    own = pl.col("calendar_source") == "own calendar"
    return (
        presence_by_inst.join(normal, on="week_start").join(verified, on=["unitid", "week_start"], how="left")
        .select("week_start", "unitid", pl.col("name").alias("university"), "session_share",
                pl.when(~own).then(pl.lit(TYPICAL)).when(pl.col("verified")).then(pl.lit(VERIFIED))
                .otherwise(pl.lit(ESTIMATED)).alias("calendar"),
                pl.when(own).then(pl.col("unitid")).otherwise(pl.lit(TYPICAL)).alias("calendar_key"))
    )


def bluebikes_units(by_institution: pl.DataFrame) -> pl.DataFrame:
    return by_institution.filter(pl.col("complete_week") & (pl.col("trips") > 0)).select(
        "week_start", pl.col("campus_unitid").alias("unitid"), pl.col("campus_unitid").alias("unit"),
        pl.col("trips").alias("activity"), pl.col("avg_daily_active_stations").alias("stations"))


def mbta_units(by_station: pl.DataFrame, stations: pl.DataFrame, universities: pl.DataFrame) -> pl.DataFrame:
    names = universities.select(pl.col("name").alias("campus_institution"), "unitid").unique("campus_institution")
    campus = (stations.filter(pl.col("in_study_area") & (pl.col("campus_zone") == "campus"))
              .join(names, on="campus_institution").select("stop_id", "unitid"))
    return (by_station.filter(pl.col("complete_week") & (pl.col("closed_days") == 0) & (pl.col("entries") > 0))
            .join(campus, on="stop_id")
            .select("week_start", "unitid", pl.col("stop_id").alias("unit"), pl.col("entries").alias("activity")))


def panel(units: pl.DataFrame, cal: pl.DataFrame, source: str, verified_only: bool = False) -> pl.DataFrame:
    """Units with their calendar's session share; the year and season of each week (by its Thursday)."""
    other = {unit: label for (s, unit), label in OTHER_CAMPUS.items() if s == source}
    thursday = pl.col("week_start") + pl.duration(days=3)
    angle = (thursday.dt.ordinal_day() - 1) * (2 * math.pi / 365.25)
    df = units.join(cal, on=["unitid", "week_start"])
    if verified_only:
        df = df.filter(~pl.col("unit").is_in(list(other)) & (pl.col("calendar") == VERIFIED))
        if source == "Bluebikes":
            busy = units.group_by("unit").agg(pl.col("activity").median()).filter(
                pl.col("activity") >= MIN_MEDIAN_TRIPS)
            df = df.join(busy.select("unit"), on="unit")
        enough = df.group_by("unitid").agg(pl.col("week_start").n_unique().alias("weeks")).filter(
            pl.col("weeks") >= MIN_UNIVERSITY_WEEKS)
        df = df.join(enough.select("unitid"), on="unitid")
    else:
        typical = cal.filter(pl.col("calendar_key") == TYPICAL).group_by("week_start").agg(
            pl.col("session_share").first().alias("typical_share"))
        moved = pl.col("unit").is_in(list(other))
        df = df.join(typical, on="week_start", how="left").with_columns(
            pl.when(moved).then(pl.col("typical_share")).otherwise(pl.col("session_share")).alias("session_share"),
            pl.when(moved).then(pl.lit(TYPICAL)).otherwise(pl.col("calendar")).alias("calendar"),
            pl.when(moved).then(pl.lit(TYPICAL)).otherwise(pl.col("calendar_key")).alias("calendar_key"),
            pl.when(moved).then(pl.col("unit").replace_strict(other, default=None))
            .otherwise(pl.col("university")).alias("university"),
        ).drop("typical_share").drop_nulls("session_share")
    return (
        df
        .with_columns(
            pl.col("activity").log().alias("y"), thursday.dt.year().alias("year"),
            thursday.dt.strftime("%Y-%m").alias("month"),
            *[(k * angle).sin().alias(f"sin{k}") for k in range(1, HARMONICS + 1)],
            *[(k * angle).cos().alias(f"cos{k}") for k in range(1, HARMONICS + 1)],
        )
        .sort("unit", "week_start")
    )


def codes(df: pl.DataFrame, *cols: str) -> np.ndarray:
    """Integer codes 0..k-1 of the combinations of columns."""
    return df.select(pl.struct(*cols).rank("dense").cast(pl.Int64) - 1).to_series().to_numpy()


def demean(values: np.ndarray, groups: list[np.ndarray], tol: float = 1e-10, max_iter: int = 10_000) -> np.ndarray:
    """Remove the level of every group of every grouping (alternating projections), column by column."""
    out = values.astype(float).copy()
    for _ in range(max_iter):
        before = out.copy()
        for g in groups:
            sums = np.zeros((g.max() + 1, out.shape[1]))
            np.add.at(sums, g, out)
            out -= (sums / np.bincount(g)[:, None])[g]
        if np.abs(out - before).max() < tol:
            return out
    raise RuntimeError("Alternating projections did not converge")


def clustered_fit(y: np.ndarray, x: np.ndarray, clusters: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Least squares with standard errors clustered by `clusters`. Returns coefficients, standard errors, residuals."""
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    resid = y - x @ beta
    bread = np.linalg.pinv(x.T @ x)
    scores = np.zeros((clusters.max() + 1, x.shape[1]))
    np.add.at(scores, clusters, x * resid[:, None])
    g = len(np.unique(clusters))
    meat = scores.T @ scores * g / (g - 1)
    return beta, np.sqrt(np.diag(bread @ meat @ bread)), resid


def regressors(df: pl.DataFrame, source: str, terms: list[str], seasons: bool) -> tuple[list[str], np.ndarray]:
    """Columns: the session-share terms, log stations (Bluebikes), and each unit's season terms."""
    names = list(terms)
    cols = [df[t].to_numpy() for t in terms]
    if source == "Bluebikes":
        names.append("log_stations")
        cols.append(df["stations"].log().to_numpy())
    if seasons:
        for unit in sorted(df["unit"].unique()):
            is_unit = (df["unit"] == unit).cast(pl.Float64).to_numpy()
            for s in SEASON:
                names.append(f"{s}_{unit}")
                cols.append(is_unit * df[s].to_numpy())
    return names, np.column_stack(cols)


def fit(df: pl.DataFrame, source: str, terms: list[str], seasons: bool = False) -> dict[str, tuple[float, float]]:
    """Coefficient and clustered standard error of each session-share term, with the week and unit-year levels
    removed; also the share of the remaining variation the terms explain (within R²)."""
    groups = [codes(df, "week_start"), codes(df, "unit", "year")]
    names, x = regressors(df, source, terms, seasons)
    both = demean(np.column_stack([df["y"].to_numpy(), x]), groups)
    y, x = both[:, 0], both[:, 1:]
    beta, se, resid = clustered_fit(y, x, codes(df, "month"))
    controls = x[:, len(terms):]
    rest = y - controls @ np.linalg.lstsq(controls, y, rcond=None)[0] if controls.shape[1] else y
    out = {name: (float(beta[i]), float(se[i])) for i, name in enumerate(names[:len(terms)])}
    out["within_r2"] = (float(1 - (resid ** 2).sum() / (rest ** 2).sum()), float("nan"))
    return out


def effect_rows(source: str, model: str, term: str, coef: float, se: float, n: int) -> list[dict]:
    pct = lambda b: (math.exp(b) - 1) * 100  # noqa: E731
    values = {"coef": coef, "se": se, "effect_pct": pct(coef), "effect_pct_low": pct(coef - 1.96 * se),
              "effect_pct_high": pct(coef + 1.96 * se)}
    return [{"source": source, "model": model, "term": term, "metric": k, "value": round(v, 4), "n": n}
            for k, v in values.items()]


def by_university(df: pl.DataFrame) -> tuple[pl.DataFrame, list[str]]:
    """One session-share column per university (zero for the others' units)."""
    names = sorted(df["university"].unique())
    return df.with_columns(
        pl.when(pl.col("university") == u).then(pl.col("session_share")).otherwise(0.0).alias(u) for u in names
    ), names


def placebo(df: pl.DataFrame, cal: pl.DataFrame, source: str) -> list[tuple[int, float]]:
    """Session-share effect when each unit is given the k-th next calendar of the list, for every k."""
    keys = sorted(df["calendar_key"].unique())
    shares = cal.select(pl.col("calendar_key").alias("placebo_key"), "week_start", "session_share").unique(
        ["placebo_key", "week_start"])
    out = []
    for k in range(1, len(keys)):
        swap = pl.DataFrame({"calendar_key": keys, "placebo_key": keys[k:] + keys[:k]})
        moved = (df.drop("session_share").join(swap, on="calendar_key")
                 .join(shares, on=["placebo_key", "week_start"]))
        out.append((len(moved), fit(moved, source, ["session_share"])["session_share"][0]))
    return out


def analyse(df: pl.DataFrame, cal: pl.DataFrame, source: str, spec: str) -> list[dict]:
    n = len(df)
    info = {"source": source, "n": n}
    rows = [{**info, "model": spec, "term": "units", "metric": "count", "value": float(df["unit"].n_unique())},
            {**info, "model": spec, "term": "universities", "metric": "count",
             "value": float(df["university"].n_unique())}]
    for model, seasons in ((spec, False), (f"{spec}, unit seasons", True)):
        result = fit(df, source, ["session_share"], seasons)
        rows += effect_rows(source, model, "session_share", *result["session_share"], n)
        rows.append({**info, "model": model, "term": "session_share", "metric": "within_r2",
                     "value": round(result["within_r2"][0], 4)})
    wide, names = by_university(df)
    result = fit(wide, source, names)
    with_seasons = fit(wide, source, names, seasons=True)
    for u in names:
        weeks = df.filter(pl.col("university") == u)
        rows += effect_rows(source, f"{spec}, by university", u, *result[u], len(weeks))
        rows += effect_rows(source, f"{spec}, by university, unit seasons", u, *with_seasons[u], len(weeks))
        for kind in (VERIFIED, ESTIMATED, TYPICAL):
            rows.append({"source": source, "model": f"{spec}, by university", "term": u,
                         "metric": f"{kind}_week_share", "value": round(float((weeks["calendar"] == kind).mean()), 4),
                         "n": len(weeks)})
    placebos = placebo(df, cal, source)
    own = fit(df, source, ["session_share"])["session_share"][0]
    pct = lambda b: (math.exp(b) - 1) * 100  # noqa: E731
    effects = [pct(b) for _, b in placebos]
    for metric, value in (("effect_pct_median", float(np.median(effects))), ("effect_pct_max", max(effects)),
                          ("rotations", float(len(effects))),
                          ("rotations_at_least_own", float(sum(b >= own for _, b in placebos)))):
        rows.append({**info, "model": f"{spec}, placebo", "term": "session_share", "metric": metric,
                     "value": round(value, 4)})
    return rows


def build(presence_by_inst, academic_weekly, calendar, presence, bike_inst, mbta_station, mbta_stations,
          universities) -> pl.DataFrame:
    cal = calendars(presence_by_inst, academic_weekly, calendar, presence)
    rows = []
    for source, units in (("Bluebikes", bluebikes_units(bike_inst)),
                          ("MBTA", mbta_units(mbta_station, mbta_stations, universities))):
        rows += analyse(panel(units, cal, source), cal, source, ALL)
        rows += analyse(panel(units, cal, source, verified_only=True), cal.filter(pl.col("calendar") == VERIFIED),
                        source, VERIFIED_ONLY)
    return pl.DataFrame(rows).select("source", "model", "term", "metric", "value", "n")


def main() -> None:
    ids = {"unitid": pl.Utf8}
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    out = build(
        read("student_presence_by_institution.csv", schema_overrides=ids),
        read("academic_weekly.csv", schema_overrides=ids), read("academic_calendar.csv", schema_overrides=ids),
        read("student_presence_weekly.csv", infer_schema_length=None),
        read("bluebikes_weekly_by_institution.csv", schema_overrides={"campus_unitid": pl.Utf8}),
        read("mbta_weekly_by_station.csv"), read("mbta_stations.csv"), read("universities.csv", schema_overrides=ids),
    )
    out.write_csv(PROCESSED / "campus_panel.csv")

    def get(source, model, term, metric):
        return out.filter((pl.col("source") == source) & (pl.col("model") == model) & (pl.col("term") == term)
                          & (pl.col("metric") == metric))["value"][0]

    for source in ("Bluebikes", "MBTA"):
        for spec in (ALL, VERIFIED_ONLY):
            units, unis = (int(get(source, spec, t, "count")) for t in ("units", "universities"))
            n = int(out.filter((pl.col("source") == source) & (pl.col("model") == spec))["n"][0])
            print(f"{source}, {spec}: {units} units of {unis} universities, {n:,} unit-weeks")
            for model in (spec, f"{spec}, unit seasons"):
                e = [get(source, model, "session_share", f"effect_pct{s}") for s in ("", "_low", "_high")]
                print(f"  {model}: {e[0]:+.1f}% ({e[1]:+.1f}% to {e[2]:+.1f}%), "
                      f"within R² {get(source, model, 'session_share', 'within_r2'):.3f}")
            p = f"{spec}, placebo"
            print(f"  placebo calendars: median {get(source, p, 'session_share', 'effect_pct_median'):+.1f}%, "
                  f"max {get(source, p, 'session_share', 'effect_pct_max'):+.1f}%; "
                  f"{int(get(source, p, 'session_share', 'rotations_at_least_own'))} of "
                  f"{int(get(source, p, 'session_share', 'rotations'))} at least the own-calendar effect")


if __name__ == "__main__":
    main()
