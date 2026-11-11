"""Goal 2: does the Student Presence Index improve models of weekly travel records?

Three families of models are compared on held-out weeks, each without the
index (baseline) and with it:
  * linear, smooth season (least squares): weather (mean temperature and its
    square, rain days, heavy-rain days, snow days, ice days, hot days, wind),
    holidays, the weeks from December 24 to January 1 (the whole city slows
    down then, students or not), a smooth annual cycle (two pairs of sine and
    cosine terms of the day of the year), network size, and a separate level
    for each calendar year. A third model also adds the summer-break share,
    since campus areas stay busier in summer than in winter breaks with the
    same index. The smooth cycle cannot copy the sharp changes of the
    academic calendar, so they are left for the index to explain;
  * linear, week of year: the same, with a separate level for each week of
    the year instead of the smooth cycle. It learns the typical calendar,
    students included, so the index can only add what differs from year to
    year: when Labor Day falls, how spring breaks and commencements move;
  * gradient boosting (scikit-learn): the same weather, holidays and network
    size, with the day of the year and the year as plain inputs, so the trees
    find season, year levels and interactions themselves. Their reliance on
    the index is measured by permutation: the increase in held-out error when
    the index is shuffled across weeks.

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
0.9), where the students' absence should show. Every family uses the same
weeks and the same held-out months.

Effect size (linear families): the coefficient of the index in the model
fitted on all weeks, with Newey-West standard errors (autocorrelation up to 4
weeks), given as the percentage change of the target from all students away
(index 0) to all in session (index 1), with a 95% interval.

Held-out scores are also given for fully in-session weeks, summer weeks
(at least half the students on summer break) and other weeks not fully in
session (breaks and the edges of terms).

Outputs in data/processed/:
  * model_comparison.csv          held-out scores and the index's effect, one row per target, family,
                                  model, subset and metric
  * model_coefficients.csv        every coefficient (except the year and week levels) of the linear models
                                  with the index, with its Newey-West standard error, and the combined
                                  effect of warming from 0 to 20 °C through both temperature terms
  * model_feature_importance.csv  for the trees with the index, the increase in held-out error when each
                                  input is shuffled across weeks
"""

import math

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingRegressor
from threadpoolctl import threadpool_limits

from boston_breathes.paths import PROCESSED

SMOOTH, WEEKS, TREES = "linear, smooth season", "linear, week of year", "gradient boosting"
WEATHER = ["tmean_c", "tmean_sq", "rain_days", "heavy_rain_days", "snow_days", "ice_days", "hot_days",
           "wind_mean_ms"]
CALENDAR = ["n_holidays", "year_end_week"]
HARMONICS = 2
SEASON = [f"{f}{k}" for k in range(1, HARMONICS + 1) for f in ("sin", "cos")]
FEATURES = {  # columns before the extra ones; the linear families add a level per year after them
    SMOOTH: [*WEATHER, *CALENDAR, "network", *SEASON],
    WEEKS: [*WEATHER, *CALENDAR, "network"],
    TREES: [*[c for c in WEATHER if c != "tmean_sq"], *CALENDAR, "network", "day_of_year", "year"],
}
EXTRA = {"baseline": [], "index": ["presence_index"], "index and summer": ["presence_index", "summer_break_share"]}
MODELS = {SMOOTH: ["baseline", "index", "index and summer"], WEEKS: ["baseline", "index"],
          TREES: ["baseline", "index"]}
TREE_SETTINGS = {"max_iter": 300, "learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 10,
                 "l2_regularization": 1.0, "early_stopping": False, "random_state": 0}
BUFFER_WEEKS = 1
HAC_LAGS = 4
IN_SESSION = 0.9  # weeks below this index are "not fully in session"
WARMING = (0, 20)  # °C: a change in temperature reported as one effect, through both temperature terms


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
    """The weeks used for one target, with its y, network size, squared temperature, season terms, day and
    week of the year (of the week's Thursday) and year."""
    y, network, complete = TARGETS[target]
    thursday = pl.col("week_start") + pl.duration(days=3)
    day = thursday.dt.ordinal_day()
    angle = (day - 1) * (2 * math.pi / 365.25)
    return (
        table.filter((pl.col("covid_period") == "normal") & pl.col("weather_complete") & pl.col(complete))
        .with_columns(
            y.alias("y"), network.alias("network"), (pl.col("tmean_c") ** 2).alias("tmean_sq"),
            thursday.dt.year().alias("year"), thursday.dt.strftime("%Y-%m").alias("block"),
            day.alias("day_of_year"),
            # The rare 53rd week (a Thursday on December 30 or 31) joins the 52nd.
            ((day - 1) // 7).clip(upper_bound=51).alias("week_of_year"),
            *[(k * angle).sin().alias(f"sin{k}") for k in range(1, HARMONICS + 1)],
            *[(k * angle).cos().alias(f"cos{k}") for k in range(1, HARMONICS + 1)],
        )
        .sort("week_start")
    )


def levels(df: pl.DataFrame, col: str) -> list[np.ndarray]:
    return [(df[col] == v).cast(pl.Float64).to_numpy() for v in sorted(df[col].unique())]


def design(df: pl.DataFrame, family: str, extra: list[str]) -> np.ndarray:
    """The family's columns, then the extra ones; for the linear families, a level per calendar year and,
    for the week-of-year family, a level per week of the year."""
    columns = [df[c].cast(pl.Float64).to_numpy() for c in [*FEATURES[family], *extra]]
    if family != TREES:
        columns += levels(df, "year")
    if family == WEEKS:
        columns += levels(df, "week_of_year")
    return np.column_stack(columns)


def fit_predict(family: str, x_train: np.ndarray, y_train: np.ndarray, x_tests: list[np.ndarray]) -> list[np.ndarray]:
    if family == TREES:
        model = HistGradientBoostingRegressor(**TREE_SETTINGS).fit(x_train, y_train)
        return [model.predict(x) for x in x_tests]
    beta = np.linalg.lstsq(x_train, y_train, rcond=None)[0]
    return [x @ beta for x in x_tests]


def held_out_predictions(df: pl.DataFrame, x: np.ndarray, family: str = SMOOTH,
                         shuffled: list[int] = ()) -> tuple[np.ndarray, dict[int, np.ndarray]]:
    """Predict each month from a fit on the other weeks, without the week on either side of the month.
    For each column in `shuffled`, also predict with that column permuted across all weeks (permutation
    importance): the same fit, with that one input scrambled."""
    y = df["y"].to_numpy()
    weeks = df["week_start"].to_numpy()
    blocks = df["block"].to_numpy()
    buffer = np.timedelta64(7 * BUFFER_WEEKS, "D")
    order = np.random.default_rng(0).permutation(len(y))
    scrambled = {}
    for col in shuffled:
        scrambled[col] = x.copy()
        scrambled[col][:, col] = x[order, col]
    pred = np.empty(len(y))
    pred_shuffled = {col: np.empty(len(y)) for col in shuffled}
    for block in np.unique(blocks):
        test = blocks == block
        near = (weeks >= weeks[test].min() - buffer) & (weeks <= weeks[test].max() + buffer)
        out = fit_predict(family, x[~near], y[~near], [x[test], *[scrambled[col][test] for col in shuffled]])
        pred[test] = out[0]
        for col, p in zip(shuffled, out[1:]):
            pred_shuffled[col][test] = p
    return pred, pred_shuffled


def scores(y: np.ndarray, pred: np.ndarray) -> dict[str, float]:
    err = y - pred
    return {"mae": float(np.abs(err).mean()), "rmse": float(np.sqrt((err ** 2).mean())),
            "r2": float(1 - (err ** 2).sum() / ((y - y.mean()) ** 2).sum())}


def newey_west_cov(x: np.ndarray, resid: np.ndarray, lags: int = HAC_LAGS) -> np.ndarray:
    """Covariance of least-squares coefficients allowing autocorrelation up to `lags` rows apart."""
    xe = x * resid[:, None]
    meat = xe.T @ xe
    for lag in range(1, lags + 1):
        g = xe[lag:].T @ xe[:-lag]
        meat += (1 - lag / (lags + 1)) * (g + g.T)
    bread = np.linalg.pinv(x.T @ x)
    return bread @ meat @ bread


def newey_west(x: np.ndarray, resid: np.ndarray, lags: int = HAC_LAGS) -> np.ndarray:
    """Standard errors of least-squares coefficients allowing autocorrelation up to `lags` rows apart."""
    return np.sqrt(np.diag(newey_west_cov(x, resid, lags)))


def warming_effect(names: list[str], beta: np.ndarray, cov: np.ndarray) -> tuple[float, float]:
    """Change in the target from WARMING[0] to WARMING[1] °C, through both temperature terms, and its
    standard error."""
    low, high = WARMING
    w = np.zeros(len(beta))
    w[names.index("tmean_c")] = high - low
    w[names.index("tmean_sq")] = high ** 2 - low ** 2
    return float(w @ beta), float(np.sqrt(w @ cov @ w))


def row(target: str, family: str, model: str, subset: str, metric: str, value: float, n: int) -> dict:
    return {"target": target, "family": family, "model": model, "subset": subset, "metric": metric,
            "value": round(value, 4), "n": n}


def effect_rows(target: str, family: str, model: str, df: pl.DataFrame, x: np.ndarray, extra: list[str]) -> list[dict]:
    """Coefficients of the extra columns in the linear model fitted on all weeks, as percentage effects."""
    y = df["y"].to_numpy()
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    se = newey_west(x, y - x @ beta)
    effect = lambda b: (math.exp(b) - 1) * 100  # noqa: E731
    rows = []
    for i, name in enumerate(extra, start=len(FEATURES[family])):
        r = lambda metric, value: row(target, family, model, "all weeks", metric, value, len(df))  # noqa: E731
        rows += [r(f"{name}_coef", beta[i]), r(f"{name}_se", se[i]), r(f"{name}_effect_pct", effect(beta[i])),
                 r(f"{name}_effect_pct_low", effect(beta[i] - 1.96 * se[i])),
                 r(f"{name}_effect_pct_high", effect(beta[i] + 1.96 * se[i]))]
    return rows


def compare(table: pl.DataFrame, target: str,
            families: dict[str, list[str]] = MODELS) -> tuple[list[dict], list[dict], list[dict]]:
    """Held-out scores of every model, the coefficients of the linear models with the index, and the
    permutation importance of every input of the trees with the index."""
    df = prepare(table, target)
    y = df["y"].to_numpy()
    in_session = (df["presence_index"] >= IN_SESSION).to_numpy()
    summer = (df["summer_break_share"] >= 0.5).to_numpy()
    subsets = {"all weeks": np.ones(len(df), dtype=bool), "not fully in session": ~in_session,
               "fully in session": in_session, "summer": summer, "breaks": ~in_session & ~summer}
    rows, coefficients, importance = [], [], []
    for family, models in families.items():
        baseline_rmse = {}
        for model in models:
            extra = EXTRA[model]
            x = design(df, family, extra)
            names = [*FEATURES[family], *extra]
            shuffled = list(range(len(names))) if family == TREES and extra else []
            pred, pred_shuffled = held_out_predictions(df, x, family, shuffled)
            index_col = len(FEATURES[family]) if shuffled else None
            for subset, mask in subsets.items():
                n = int(mask.sum())
                s = scores(y[mask], pred[mask])
                rows += [row(target, family, model, subset, metric, value, n) for metric, value in s.items()]
                if model == "baseline":
                    baseline_rmse[subset] = s["rmse"]
                else:
                    rows.append(row(target, family, model, subset, "rmse_change_pct",
                                    (s["rmse"] / baseline_rmse[subset] - 1) * 100, n))
                if index_col is not None:
                    scrambled = scores(y[mask], pred_shuffled[index_col][mask])["rmse"]
                    rows.append(row(target, family, model, subset, "presence_index_permutation_rmse_increase_pct",
                                    (scrambled / s["rmse"] - 1) * 100, n))
            if shuffled:
                rmse = scores(y, pred)["rmse"]
                importance += [{"target": target, "family": family, "model": model, "feature": names[col],
                                "rmse_increase_pct": round((scores(y, pred_shuffled[col])["rmse"] / rmse - 1) * 100, 4),
                                "n": len(df)} for col in shuffled]
            if family != TREES and extra:
                rows += effect_rows(target, family, model, df, x, extra)
            if family != TREES and model == "index":
                beta = np.linalg.lstsq(x, y, rcond=None)[0]
                cov = newey_west_cov(x, y - x @ beta)
                se = np.sqrt(np.diag(cov))
                coef = lambda feature, b, s: {"target": target, "family": family, "model": model,  # noqa: E731
                                              "feature": feature, "coef": round(b, 6), "se": round(s, 6),
                                              "n": len(df)}
                coefficients += [coef(name, float(beta[i]), float(se[i])) for i, name in enumerate(names)]
                coefficients.append(coef(f"tmean_{WARMING[0]}_to_{WARMING[1]}c", *warming_effect(names, beta, cov)))
    return rows, coefficients, importance


def build(table: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    results = [compare(table, target) for target in TARGETS]
    return tuple(pl.DataFrame([r for result in results for r in result[i]]) for i in range(3))


def main() -> None:
    table = pl.read_csv(PROCESSED / "modeling_weekly.csv", try_parse_dates=True, infer_schema_length=None)
    with threadpool_limits(limits=1):  # the data are small: one thread is faster and gives the same result
        out, coefficients, importance = build(table)
    out.write_csv(PROCESSED / "model_comparison.csv")
    coefficients.write_csv(PROCESSED / "model_coefficients.csv")
    importance.write_csv(PROCESSED / "model_feature_importance.csv")

    def get(target, family, model, metric, subset="all weeks"):
        return out.filter((pl.col("target") == target) & (pl.col("family") == family) & (pl.col("model") == model)
                          & (pl.col("subset") == subset) & (pl.col("metric") == metric))["value"][0]

    for target in TARGETS:
        n = int(out.filter((pl.col("target") == target) & (pl.col("subset") == "all weeks"))["n"][0])
        print(f"{target} ({n} weeks)")
        for family in MODELS:
            change = get(target, family, "index", "rmse_change_pct")
            line = (f"  {family:22s} held-out RMSE {get(target, family, 'baseline', 'rmse'):.4f} -> "
                    f"{get(target, family, 'index', 'rmse'):.4f} ({change:+.1f}%)")
            if family == TREES:
                shuffled = get(target, family, "index", "presence_index_permutation_rmse_increase_pct")
                line += f"; shuffling the index raises it by {shuffled:.1f}%"
            else:
                effect = [get(target, family, "index", f"presence_index_effect_pct{s}") for s in ("", "_low", "_high")]
                line += f"; index effect {effect[0]:+.1f}% ({effect[1]:+.1f}% to {effect[2]:+.1f}%)"
            print(line)


if __name__ == "__main__":
    main()
