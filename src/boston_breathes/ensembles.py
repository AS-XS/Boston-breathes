"""Do combined models predict travel better than the best single model?

Two comparisons, each on the held-out data of the module it extends, so that the scores line up with it.

Travel targets of model_comparison.py (the four logarithms, with the Student Presence Index; each calendar month
predicted from the other weeks, without the week on either side):
  * single models: linear with a level per week of the year, and gradient boosting, as in model_comparison.py;
    a random forest on the trees' inputs (300 trees, at least 5 weeks a leaf);
  * average: the mean of the linear model and gradient boosting;
  * stacked: their weighted mean. The weights are fitted on predictions for the training weeks made without
    those weeks (5 folds of calendar months, inside the training weeks only, so the held-out month plays no
    part), by non-negative least squares, and scaled to add up to one;
  * stacked, with the forest: the same with all three single models;
  * hybrid: the linear model, plus gradient boosting fitted to what the linear model leaves (its residuals on the
    training weeks), so the trees add interactions without having to carry the trend;
  * chosen by cross-validation: whichever of the linear model and gradient boosting has the smaller error on
    those out-of-fold predictions.
Each is compared with the better of the linear model and gradient boosting for that target, chosen after seeing
the held-out errors (which favours the single model), as the change in RMSE, with a 95% interval from drawing
calendar months with replacement (the same draws for every model).

Weekly presence signals of travel_models.py (subway and Bluebikes, weekdays and weekends; each validation year
predicted from the years before it, and January-September 2026 from 2022-2025), with the four feature sets that
hold student inputs:
  * three more families: additive splines (cubic splines of each input, then ridge), extra trees (300 trees, at
    least 5 weeks a leaf) and a Gaussian process (a radial kernel plus white noise, fitted by maximum likelihood);
  * the mean of the eight families of travel_models.py, and of all eleven; the mean of ridge and the random
    forest (the two best families on Bluebikes weekdays in travel_model_accuracy.csv);
  * stacked, over the eight and over all eleven, on the training years' out-of-fold predictions (5 folds of
    calendar months);
  * chosen by cross-validation, among the eight and among all eleven.
The 311 requests are left out: no family explains them (R² about 0.05).

Outputs in data/processed/:
  * ensemble_targets_accuracy.csv  held-out error of each model for each travel target (all weeks, and weeks not
                                   fully in session), and on all weeks its change in RMSE against the best single
                                   model, with a 95% interval
  * ensemble_targets_weights.csv   the stacking weights fitted for each held-out month
  * ensemble_signals_accuracy.csv  R² and mean absolute error of each model, feature set and split, for each
                                   signal
"""

import os
import warnings
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import polars as pl
from scipy.optimize import nnls
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel
from sklearn.linear_model import RidgeCV
from sklearn.model_selection import GroupKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import SplineTransformer
from threadpoolctl import threadpool_limits

from boston_breathes import model_comparison as mc
from boston_breathes import travel_models as tm
from boston_breathes.paths import PROCESSED

INNER_FOLDS = 5
BOOTSTRAP = 2000
FOREST = {"n_estimators": 300, "min_samples_leaf": 5, "random_state": 0}
LINEAR, TREES, RANDOM_FOREST = "linear, week of year", "gradient boosting", "random forest"
SINGLES = [LINEAR, TREES, RANDOM_FOREST]
COMBINED = ["average", "stacked", "stacked, with the forest", "hybrid", "chosen by cross-validation"]
NEW_FAMILIES = {
    "additive splines": (lambda: make_pipeline(SplineTransformer(n_knots=5, degree=3),
                                               RidgeCV(alphas=np.logspace(-3, 3, 13))), {}),
    "extra trees": (lambda: ExtraTreesRegressor(**FOREST), {}),
    "Gaussian process": (lambda: GaussianProcessRegressor(ConstantKernel() * RBF(3.0) + WhiteKernel(0.5),
                                                          random_state=0), {}),
}
FAMILIES = {**tm.FAMILIES, **NEW_FAMILIES}
SIGNALS = ["subway_weekday", "subway_weekend", "bikes_weekday", "bikes_weekend"]
FEATURE_SETS = ["students, everyone", "students, summer and breaks", "students and weather", "everything"]


def stack_weights(predictions: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Non-negative weights of the columns of `predictions` that fit y best by least squares, scaled to add up
    to one."""
    w = nnls(predictions, y)[0]
    return w / w.sum()


def singles(x_lin: np.ndarray, x_tree: np.ndarray, y: np.ndarray, x_lin_test: np.ndarray,
            x_tree_test: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Predictions of the linear model, gradient boosting and the random forest for the test rows (one column
    each), and the linear model's fit to the training rows."""
    linear, fitted = mc.fit_predict(mc.WEEKS, x_lin, y, [x_lin_test, x_lin])
    trees = mc.fit_predict(mc.TREES, x_tree, y, [x_tree_test])[0]
    forest = RandomForestRegressor(**FOREST).fit(x_tree, y).predict(x_tree_test)
    return np.column_stack([linear, trees, forest]), fitted


def out_of_fold(x_lin: np.ndarray, x_tree: np.ndarray, y: np.ndarray, months: np.ndarray) -> np.ndarray:
    """Predictions of the three single models for the training rows, each fold of calendar months predicted from
    the others."""
    out = np.empty((len(y), len(SINGLES)))
    for fit, held in GroupKFold(INNER_FOLDS).split(x_lin, y, months):
        out[held] = singles(x_lin[fit], x_tree[fit], y[fit], x_lin[held], x_tree[held])[0]
    return out


def target_predictions(df: pl.DataFrame) -> tuple[dict[str, np.ndarray], list[dict]]:
    """Held-out predictions of every model for every week, each month predicted from the other weeks without the
    week on either side, and the stacking weights fitted for each month."""
    y = df["y"].to_numpy()
    x_lin, x_tree = mc.design(df, mc.WEEKS, ["presence_index"]), mc.design(df, mc.TREES, ["presence_index"])
    weeks, blocks = df["week_start"].to_numpy(), df["block"].to_numpy()
    buffer = np.timedelta64(7 * mc.BUFFER_WEEKS, "D")
    pred = {m: np.empty(len(y)) for m in [*SINGLES, *COMBINED]}
    weights = []
    for block in np.unique(blocks):
        test = blocks == block
        train = ~((weeks >= weeks[test].min() - buffer) & (weeks <= weeks[test].max() + buffer))
        p, fitted = singles(x_lin[train], x_tree[train], y[train], x_lin[test], x_tree[test])
        oof = out_of_fold(x_lin[train], x_tree[train], y[train], blocks[train])
        two, three = stack_weights(oof[:, :2], y[train]), stack_weights(oof, y[train])
        residual = mc.fit_predict(mc.TREES, x_tree[train], y[train] - fitted, [x_tree[test]])[0]
        for i, name in enumerate(SINGLES):
            pred[name][test] = p[:, i]
        pred["average"][test] = p[:, :2].mean(axis=1)
        pred["stacked"][test] = p[:, :2] @ two
        pred["stacked, with the forest"][test] = p @ three
        pred["hybrid"][test] = p[:, 0] + residual
        pred["chosen by cross-validation"][test] = p[:, np.argmin(((oof[:, :2] - y[train, None]) ** 2).mean(axis=0))]
        weights.append({"block": block, "stacked_linear": two[0], "stacked_trees": two[1],
                        "with_forest_linear": three[0], "with_forest_trees": three[1], "with_forest_forest": three[2]})
    return pred, weights


def changes(y: np.ndarray, pred: np.ndarray, best: np.ndarray, draws: list[np.ndarray]) -> tuple[float, float, float]:
    """Change in RMSE against the best single model (%), and its 95% interval over the draws of months."""
    se, se_best = (y - pred) ** 2, (y - best) ** 2
    ratio = lambda rows: np.sqrt(se[rows].mean() / se_best[rows].mean())  # noqa: E731
    low, high = np.percentile([ratio(rows) for rows in draws], [2.5, 97.5])
    return 100 * (ratio(slice(None)) - 1), 100 * (low - 1), 100 * (high - 1)


def target_rows(target: str) -> tuple[list[dict], list[dict]]:
    with threadpool_limits(limits=1):
        table = pl.read_csv(PROCESSED / "modeling_weekly.csv", try_parse_dates=True, infer_schema_length=None)
        df = mc.prepare(table, target)
        pred, weights = target_predictions(df)
    y, blocks = df["y"].to_numpy(), df["block"].to_numpy()
    months = [np.flatnonzero(blocks == b) for b in np.unique(blocks)]
    rng = np.random.default_rng(0)
    draws = [np.concatenate([months[i] for i in rng.integers(0, len(months), len(months))]) for _ in range(BOOTSTRAP)]
    best = min([LINEAR, TREES], key=lambda m: ((y - pred[m]) ** 2).mean())
    in_session = (df["presence_index"] >= mc.IN_SESSION).to_numpy()
    rows = []
    for name, p in pred.items():
        change, low, high = changes(y, p, pred[best], draws)
        for subset, mask in {"all weeks": np.ones(len(y), dtype=bool), "not fully in session": ~in_session}.items():
            s = mc.scores(y[mask], p[mask])
            row = {"target": target, "model": name, "kind": "single" if name in SINGLES else "combined",
                   "subset": subset, "weeks": int(mask.sum()), **s,
                   "typical_error_pct": 100 * (np.exp(s["rmse"]) - 1), "best_single": best}
            if subset == "all weeks":
                row |= {"rmse_change_pct": change, "rmse_change_pct_low": low, "rmse_change_pct_high": high}
            rows.append(row)
    return rows, [{"target": target, **w} for w in weights]


def combinations(pred: dict[str, np.ndarray], oof: dict[str, np.ndarray], y: np.ndarray) -> dict[str, np.ndarray]:
    """The combined predictions of the families, with weights and choices from the training years only."""
    out = {}
    for label, members in (("the eight", list(tm.FAMILIES)), ("all eleven", list(FAMILIES))):
        p, o = np.column_stack([pred[f] for f in members]), np.column_stack([oof[f] for f in members])
        out[f"mean of {label}"] = p.mean(axis=1)
        out[f"stacked, {label}"] = p @ stack_weights(o, y)
        out[f"chosen by cross-validation, {label}"] = p[:, np.argmin(((o - y[:, None]) ** 2).mean(axis=0))]
    out["mean of ridge and random forest"] = (pred["ridge"] + pred[RANDOM_FOREST]) / 2
    return out


def signal_rows(signal: str) -> list[dict]:
    warnings.filterwarnings("ignore", category=ConvergenceWarning)  # lbfgs and the kernel's bounds, on tiny data
    table = pl.read_csv(PROCESSED / "presence_signals_weekly.csv", try_parse_dates=True)
    day = signal.split("_")[1]
    rows = []
    with threadpool_limits(limits=1):
        for features in FEATURE_SETS:
            columns = tm.feature_sets(day)[features]
            df = tm.target_data(table, signal, columns)
            for evaluation, train, test in tm.splits(df):
                months = train["week_start"].dt.strftime("%Y-%m").to_numpy()
                x, y = train.select(columns).to_numpy(), train[signal].to_numpy()
                x_test = test.select(columns).to_numpy()
                pred, oof = {}, {}
                for family in FAMILIES:
                    settings = tm.choose(family, x, y, months) if family in tm.FAMILIES else {}
                    pred[family] = tm.model(family, settings, FAMILIES).fit(x, y).predict(x_test)
                    oof[family] = cross_val_predict(tm.model(family, settings, FAMILIES), x, y, groups=months,
                                                    cv=GroupKFold(tm.FOLDS))
                for name, p in {**pred, **combinations(pred, oof, y)}.items():
                    s = tm.scores(test[signal].to_numpy(), p, np.inf)
                    rows.append({"signal": signal, "features": features, "model": name,
                                 "kind": "single" if name in FAMILIES else "combined", "evaluation": evaluation,
                                 "test_year": int(test["week_start"].dt.year()[0]), "weeks": len(test),
                                 "r2": s["r2"], "mae": s["mae"]})
    return rows


def main() -> None:
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
        targets = [pool.submit(target_rows, target) for target in mc.TARGETS]  # the long ones first
        signals = [pool.submit(signal_rows, signal) for signal in SIGNALS]
        target_results = [f.result() for f in targets]
        signal_results = [f.result() for f in signals]
    accuracy = pl.DataFrame([r for rows, _ in target_results for r in rows])
    accuracy.with_columns(pl.selectors.float().round(4)).write_csv(PROCESSED / "ensemble_targets_accuracy.csv")
    pl.DataFrame([w for _, weights in target_results for w in weights]).with_columns(
        pl.selectors.float().round(4)).write_csv(PROCESSED / "ensemble_targets_weights.csv")
    by_signal = pl.DataFrame([r for rows in signal_results for r in rows])
    by_signal.with_columns(pl.selectors.float().round(4)).write_csv(PROCESSED / "ensemble_signals_accuracy.csv")
    with pl.Config(tbl_rows=60, tbl_cols=-1, tbl_width_chars=250, fmt_str_lengths=40):
        print("Travel targets: typical error on held-out months (%), all weeks, and change in RMSE against the best "
              "single model")
        print(accuracy.filter(pl.col("subset") == "all weeks").select(
            "target", "model", "best_single", pl.selectors.matches("typical|change").round(1)))
        validation = (by_signal.filter(pl.col("evaluation") == "validation").group_by("signal", "features", "model")
                      .agg(pl.col("r2").mean().alias("validation_r2")))
        test = by_signal.filter(pl.col("evaluation") == "test").select("signal", "features", "model",
                                                                       pl.col("r2").alias("test_r2"))
        best = (validation.join(test, on=["signal", "features", "model"]).sort("validation_r2", descending=True)
                .group_by("signal", "model", maintain_order=True).head(1))
        print("Weekly signals: R2 of each model with its best feature set (by validation)")
        print(best.sort("signal", "validation_r2", descending=[False, True]).with_columns(
            pl.selectors.float().round(3)))


if __name__ == "__main__":
    main()
