"""Goal 2: does the Student Presence Index improve models of weekly travel records?

For each target, three linear models (least squares) are compared on held-out
weeks:
  * baseline: weather (mean temperature and its square, rain days, heavy-rain
    days, snow days, ice days, hot days, wind), holidays, the weeks from
    December 24 to January 1 (the whole city slows down then, students or
    not), a smooth annual cycle (two pairs of sine and cosine terms of the
    day of the year), network size, and a separate level for each calendar
    year;
  * index: the baseline plus the Student Presence Index;
  * index and summer: also the summer-break share, since campus areas stay
    busier in summer than in winter breaks with the same index.

Targets, as logarithms so that effects are proportional:
  * campus ratio: campus-zone over away-zone Bluebikes trips, and MBTA
    entries. Weather and citywide changes affect both zones, so the ratio
    isolates where students are;
  * total: all study-area Bluebikes trips, and MBTA entries.
Network size is, for Bluebikes, the ratio of campus- to away-zone stations in
use (campus ratio) or all stations in use (total); for MBTA, the number of
gated stations with entries.

Weeks: complete weather and travel data, outside the COVID-19 period (the
spring 2020 lockdown and the 2020-21 academic year).

Testing: travel levels change far more between years than within them (the
Bluebikes network grew fivefold, and subway entries are still about a third
below 2019), so a model fitted on earlier years cannot predict the level of
later ones. Instead each calendar month is held out in turn and predicted from
a model fitted on the other weeks, leaving out the week on either side of the
month as well, because neighbouring weeks are alike. Blocks are contiguous
months, not random weeks. The held-out predictions give MAE, RMSE and R² of
the logarithm, for all weeks and for weeks not fully in session (index below
0.9), where the students' absence should show.

Effect size: the coefficient of the index in the model fitted on all weeks,
with Newey-West standard errors (autocorrelation up to 4 weeks), given as the
percentage change of the target from all students away (index 0) to all in
session (index 1), with a 95% interval.

Output: data/processed/model_comparison.csv, one row per target, model,
subset and metric.
"""

import math

import numpy as np
import polars as pl

from boston_breathes.paths import PROCESSED

WEATHER = ["tmean_c", "tmean_sq", "rain_days", "heavy_rain_days", "snow_days", "ice_days", "hot_days",
           "wind_mean_ms"]
HARMONICS = 2
SEASON = [f"{f}{k}" for k in range(1, HARMONICS + 1) for f in ("sin", "cos")]
BASE = [*WEATHER, "n_holidays", "year_end_week", "network", *SEASON]
MODELS = {"baseline": [], "index": ["presence_index"], "index and summer": ["presence_index", "summer_break_share"]}
BUFFER_WEEKS = 1
HAC_LAGS = 4
IN_SESSION = 0.9  # weeks below this index are "not fully in session"


def log_ratio(a: str, b: str) -> pl.Expr:
    return (pl.col(a) / pl.col(b)).log()


TARGETS = {  # name: (target, network size, completeness flag)
    "Bluebikes campus ratio": (log_ratio("bluebikes_trips_campus", "bluebikes_trips_away"),
                               log_ratio("bluebikes_stations_campus", "bluebikes_stations_away"),
                               "bluebikes_complete"),
    "MBTA campus ratio": (log_ratio("mbta_entries_campus", "mbta_entries_away"), pl.col("mbta_stations").log(),
                          "mbta_complete"),
    "Bluebikes total": (pl.col("bluebikes_trips").log(), pl.col("bluebikes_stations").log(), "bluebikes_complete"),
    "MBTA total": (pl.col("mbta_entries").log(), pl.col("mbta_stations").log(), "mbta_complete"),
}


def prepare(table: pl.DataFrame, target: str) -> pl.DataFrame:
    """The weeks used for one target, with its y, network size, squared temperature, season terms and year."""
    y, network, complete = TARGETS[target]
    thursday = pl.col("week_start") + pl.duration(days=3)
    angle = (thursday.dt.ordinal_day() - 1) * (2 * math.pi / 365.25)
    return (
        table.filter((pl.col("covid_period") == "normal") & pl.col("weather_complete") & pl.col(complete))
        .with_columns(
            y.alias("y"), network.alias("network"), (pl.col("tmean_c") ** 2).alias("tmean_sq"),
            thursday.dt.year().alias("year"), thursday.dt.strftime("%Y-%m").alias("block"),
            *[(k * angle).sin().alias(f"sin{k}") for k in range(1, HARMONICS + 1)],
            *[(k * angle).cos().alias(f"cos{k}") for k in range(1, HARMONICS + 1)],
        )
        .sort("week_start")
    )


def design(df: pl.DataFrame, extra: list[str]) -> np.ndarray:
    """Baseline columns, the extra columns, then one column per calendar year (the year's level)."""
    columns = [df[c].cast(pl.Float64).to_numpy() for c in [*BASE, *extra]]
    years = [(df["year"] == y).cast(pl.Float64).to_numpy() for y in sorted(df["year"].unique())]
    return np.column_stack(columns + years)


def held_out_predictions(df: pl.DataFrame, x: np.ndarray) -> np.ndarray:
    """Predict each month from a fit on the other weeks, without the week on either side of the month."""
    y = df["y"].to_numpy()
    weeks = df["week_start"].to_numpy()
    blocks = df["block"].to_numpy()
    buffer = np.timedelta64(7 * BUFFER_WEEKS, "D")
    pred = np.empty(len(y))
    for block in np.unique(blocks):
        test = blocks == block
        near = (weeks >= weeks[test].min() - buffer) & (weeks <= weeks[test].max() + buffer)
        beta = np.linalg.lstsq(x[~near], y[~near], rcond=None)[0]
        pred[test] = x[test] @ beta
    return pred


def scores(y: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    err = y - pred
    return {"mae": float(np.abs(err).mean()), "rmse": float(np.sqrt((err ** 2).mean())),
            "r2": float(1 - (err ** 2).sum() / ((y - y.mean()) ** 2).sum())}


def newey_west(x: np.ndarray, resid: np.ndarray, lags: int = HAC_LAGS) -> np.ndarray:
    """Standard errors of least-squares coefficients allowing autocorrelation up to `lags` rows apart."""
    xe = x * resid[:, None]
    meat = xe.T @ xe
    for lag in range(1, lags + 1):
        g = xe[lag:].T @ xe[:-lag]
        meat += (1 - lag / (lags + 1)) * (g + g.T)
    bread = np.linalg.pinv(x.T @ x)
    return np.sqrt(np.diag(bread @ meat @ bread))


def row(target: str, model: str, subset: str, metric: str, value: float, n: int) -> dict:
    return {"target": target, "model": model, "subset": subset, "metric": metric, "value": round(value, 4), "n": n}


def compare(table: pl.DataFrame, target: str) -> list[dict]:
    df = prepare(table, target)
    y = df["y"].to_numpy()
    partial = (df["presence_index"] < IN_SESSION).to_numpy()
    subsets = {"all weeks": np.ones(len(df), dtype=bool), "not fully in session": partial}
    rows, baseline_rmse = [], {}
    for model, extra in MODELS.items():
        x = design(df, extra)
        pred = held_out_predictions(df, x)
        for subset, mask in subsets.items():
            s = scores(y[mask], pred[mask])
            rows += [row(target, model, subset, metric, value, int(mask.sum())) for metric, value in s.items()]
            if model == "baseline":
                baseline_rmse[subset] = s["rmse"]
            else:
                rows.append(row(target, model, subset, "rmse_change_pct",
                                (s["rmse"] / baseline_rmse[subset] - 1) * 100, int(mask.sum())))
        beta = np.linalg.lstsq(x, y, rcond=None)[0]
        se = newey_west(x, y - x @ beta)
        for i, name in enumerate(extra, start=len(BASE)):
            effect = lambda b: (math.exp(b) - 1) * 100  # noqa: E731
            rows += [
                row(target, model, "all weeks", f"{name}_coef", beta[i], len(df)),
                row(target, model, "all weeks", f"{name}_se", se[i], len(df)),
                row(target, model, "all weeks", f"{name}_effect_pct", effect(beta[i]), len(df)),
                row(target, model, "all weeks", f"{name}_effect_pct_low", effect(beta[i] - 1.96 * se[i]), len(df)),
                row(target, model, "all weeks", f"{name}_effect_pct_high", effect(beta[i] + 1.96 * se[i]), len(df)),
            ]
    return rows


def build(table: pl.DataFrame) -> pl.DataFrame:
    return pl.DataFrame([r for target in TARGETS for r in compare(table, target)])


def main() -> None:
    table = pl.read_csv(PROCESSED / "modeling_weekly.csv", try_parse_dates=True, infer_schema_length=None)
    out = build(table)
    out.write_csv(PROCESSED / "model_comparison.csv")
    get = lambda t, m, s, k: out.filter(  # noqa: E731
        (pl.col("target") == t) & (pl.col("model") == m) & (pl.col("subset") == s) & (pl.col("metric") == k)
    )["value"][0]
    for target in TARGETS:
        n = int(out.filter((pl.col("target") == target) & (pl.col("subset") == "all weeks"))["n"][0])
        print(f"{target} ({n} weeks)")
        for subset in ("all weeks", "not fully in session"):
            rmse = " -> ".join(f"{get(target, m, subset, 'rmse'):.4f}" for m in MODELS)
            print(f"  held-out RMSE, {subset}: {rmse} (baseline -> index -> index and summer)")
        effect = [get(target, "index", "all weeks", f"presence_index_effect_pct{s}") for s in ("", "_low", "_high")]
        print(f"  index effect, all away to all in session: {effect[0]:+.1f}% ({effect[1]:+.1f}% to {effect[2]:+.1f}%)")


if __name__ == "__main__":
    main()
