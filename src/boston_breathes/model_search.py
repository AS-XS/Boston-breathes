"""How far can more inputs and more flexible models go? A broad search on the travel models.

Travel targets of model_comparison.py (the four logarithms; each calendar month predicted from the other weeks,
without the week on either side), with two sets of inputs:
  * the comparison's: weather, holidays, network size and the Student Presence Index;
  * more inputs: also the summer-break share; the housing presence index (the share of residence-hall beds in
    session); the shares out of session at Thanksgiving, in winter and in spring; the change in the index from the
    week before and to the week after; maximum and minimum temperature, total rain and snow; last week's mean
    temperature, rain days and snow days; and the share in session of each of the ten largest universities (by
    students on campus).
Models, each fitted on the training weeks only, with any setting chosen by 3-fold cross-validation over calendar
months within the training weeks:
  * linear: least squares with a level for each year and each week of the year (model_comparison.py's); ridge, the
    same with a penalty chosen by cross-validation;
  * gradient boosting with model_comparison.py's settings, and tuned (learning rate 0.05 or 0.1; 7 or 31 leaves);
  * random forest and extra trees (300 trees, at least 5 weeks a leaf);
  * neural network: a multi-layer perceptron with hidden layers of 32 and 16 units, its L2 penalty chosen from 0.3,
    1 and 3, the mean of 3 fits from different random starting weights. Its inputs give the season as four pairs of
    sine and cosine terms of the day of the year, and a level for each year;
  * Gaussian process (radial kernel plus white noise) and support vector machine (radial kernel; C and the kernel
    width chosen), on the network's inputs;
  * ridge, then trees, and ridge, then a neural network: the flexible model fitted to what ridge leaves on the
    training weeks;
  * combinations, fixed before the full run: stacked over all eleven models and over ridge, tuned trees and
    the network (non-negative weights fitted on out-of-fold predictions within the training weeks, adding up to one),
    and the mean of the same.
Each is compared with the better of model_comparison.py's linear model and trees for that target, as the change in
RMSE with a 95% interval from drawing calendar months with replacement (the same draws for every model). With 30
candidates per target, the best one's gain is partly luck. The combinations were fixed in advance, but after a trial
run on the two campus ratios, so the two totals are their cleaner test.

Weekly presence signals of travel_models.py (subway and Bluebikes, weekdays and weekends; each validation year
predicted from the years before it, and January-September 2026 from 2022-2025), with three sets of inputs: the
population model's own (every student not living with their parents leaving), everything of travel_models.py, and
everything with the ten universities. Models: linear regression, ridge, lasso, random forest, gradient boosting, a
Gaussian process, three neural networks (hidden layers of 8; 32 and 16; 64, 32 and 16 units; L2 penalty chosen from
0.3 to 10; the mean of 5 fits), ridge then trees, ridge then a neural network, and the stack and mean of all.

Run time: about 3 hours on 4 cores.

Outputs in data/processed/:
  * model_search_targets.csv  typical error of each model and set of inputs for each travel target (all weeks, and
                              weeks not fully in session), and its change in RMSE against the better of the
                              comparison's linear model and trees, with a 95% interval
  * model_search_weights.csv  the mean stacking weight of each model, for each target and set of inputs
  * model_search_signals.csv  R² and mean absolute error of each model, set of inputs and split, for each signal
"""

import os
import warnings
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import polars as pl
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel
from sklearn.linear_model import LassoCV, LinearRegression, RidgeCV
from sklearn.model_selection import GroupKFold
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR
from threadpoolctl import threadpool_limits

from boston_breathes import ensembles as en
from boston_breathes import model_comparison as mc
from boston_breathes import travel_models as tm
from boston_breathes.paths import PROCESSED

TOP_UNIVERSITIES = 10
INNER_FOLDS = 3
BOOTSTRAP = 2000
TREE_GRID = [{"learning_rate": lr, "max_leaf_nodes": leaves} for lr in (0.05, 0.1) for leaves in (7, 31)]
NN_ALPHAS, RESIDUAL_ALPHAS = [0.3, 1.0, 3.0], [1.0, 3.0, 10.0]
SVR_GRID = [{"C": c, "gamma": g} for c in (1, 10, 100) for g in (0.003, 0.01, 0.03)]
TARGET_SEEDS, SIGNAL_SEEDS = 3, 5
FOREST = {"n_estimators": 300, "min_samples_leaf": 5, "random_state": 0}
INPUTS = ["the comparison's", "more inputs"]
EXTRA = ["summer_break_share", "housing_presence_index", "thanksgiving_share", "winter_share", "spring_share",
         "index_change", "index_next_change", "tmax_mean_c", "tmin_mean_c", "prcp_total_mm", "snow_total_mm",
         "tmean_lag", "rain_lag", "snow_lag"]
SINGLES = ["linear", "ridge", "gradient boosting", "gradient boosting, tuned", "random forest", "extra trees",
           "neural network", "Gaussian process", "support vector machine", "ridge, then trees",
           "ridge, then neural network"]
THREE = ["ridge", "gradient boosting, tuned", "neural network"]
COMBINED = ["stacked, all", "stacked: ridge, tuned trees, neural network", "mean of all",
            "mean: ridge, tuned trees, neural network"]
SIGNALS = en.SIGNALS
NETWORKS = {"neural network (8)": (8,), "neural network (32, 16)": (32, 16),
            "neural network (64, 32, 16)": (64, 32, 16)}
SIGNAL_ALPHAS = [0.3, 1.0, 3.0, 10.0]


def university_columns(df: pl.DataFrame) -> list[str]:
    return sorted(c for c in df.columns if c.startswith("university_"))


def weekly_table() -> pl.DataFrame:
    """modeling_weekly.csv with the extra inputs: break shares by kind, changes in the index, last week's weather,
    and the share in session of each of the largest universities."""
    month = pl.col("week_start").dt.month()
    other = (1 - pl.col("presence_index") - pl.col("summer_break_share")).clip(lower_bound=0)
    table = (pl.read_csv(PROCESSED / "modeling_weekly.csv", try_parse_dates=True, infer_schema_length=None)
             .sort("week_start")
             .with_columns((other * month.eq(11)).alias("thanksgiving_share"),
                           (other * month.is_in([12, 1])).alias("winter_share"),
                           (other * month.is_in([2, 3, 4])).alias("spring_share"),
                           pl.col("presence_index").diff().fill_null(0).alias("index_change"),
                           (-pl.col("presence_index").diff(-1)).fill_null(0).alias("index_next_change"),
                           pl.col("tmean_c").shift(1).alias("tmean_lag"),
                           pl.col("rain_days").shift(1).alias("rain_lag"),
                           pl.col("snow_days").shift(1).alias("snow_lag")))
    by_university = pl.read_csv(PROCESSED / "student_presence_by_institution.csv", try_parse_dates=True,
                                infer_schema_length=None)
    largest = (by_university.group_by("unitid").agg(pl.col("in_person").mean())
               .sort("in_person", "unitid", descending=True).head(TOP_UNIVERSITIES)["unitid"])
    wide = (by_university.filter(pl.col("unitid").is_in(largest.to_list()))
            .select("week_start", pl.format("university_{}", "unitid").alias("university"), "session_share")
            .pivot(on="university", index="week_start", values="session_share"))
    return table.join(wide, on="week_start", how="left")


def designs(df: pl.DataFrame, inputs: str) -> dict[str, np.ndarray]:
    """The inputs of each kind of model: linear (model_comparison.py's week-of-year design), trees
    (model_comparison.py's), and smooth (for the network, the Gaussian process and the support vector machine)."""
    extra = [] if inputs == INPUTS[0] else EXTRA + university_columns(df)
    linear = mc.design(df, mc.WEEKS, ["presence_index", *extra])
    trees = mc.design(df, mc.TREES, ["presence_index", *extra])
    x = df.select(*mc.WEATHER, *mc.CALENDAR, "network", "presence_index", *extra).cast(pl.Float64).to_numpy()
    angle = (df["day_of_year"].to_numpy() - 1) * 2 * np.pi / 365.25
    season = np.column_stack([f(k * angle) for k in range(1, 5) for f in (np.sin, np.cos)])
    return {"linear": linear, "trees": trees, "smooth": np.column_stack([x, season, *mc.levels(df, "year")])}


def scaled(estimator) -> TransformedTargetRegressor:
    return TransformedTargetRegressor(regressor=make_pipeline(StandardScaler(), estimator),
                                      transformer=StandardScaler())


def ridge() -> TransformedTargetRegressor:
    return scaled(RidgeCV(alphas=np.logspace(-2, 3, 11)))


def network(alpha: float, layers: tuple = (32, 16), seed: int = 0) -> TransformedTargetRegressor:
    return scaled(MLPRegressor(hidden_layer_sizes=layers, alpha=alpha, solver="lbfgs", max_iter=3000,
                               random_state=seed))


def trees(**settings) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(**{**mc.TREE_SETTINGS, **settings})


def gaussian_process(length: float = 5.0, noise: float = 0.1) -> TransformedTargetRegressor:
    return scaled(GaussianProcessRegressor(ConstantKernel() * RBF(length) + WhiteKernel(noise), random_state=0))


def fit_predict(make, x: np.ndarray, y: np.ndarray, x_test: np.ndarray) -> np.ndarray:
    return make().fit(x, y).predict(x_test)


def choose(makers: list, x: np.ndarray, y: np.ndarray, groups: np.ndarray, folds: int) -> tuple[int, np.ndarray]:
    """The setting with the smallest cross-validated error, and its out-of-fold predictions."""
    oof = np.empty((len(y), len(makers)))
    for fit, held in GroupKFold(folds).split(x, y, groups):
        for j, make in enumerate(makers):
            oof[held, j] = fit_predict(make, x[fit], y[fit], x[held])
    best = int(np.argmin(((oof - y[:, None]) ** 2).mean(axis=0)))
    return best, oof[:, best]


def bagged(alpha: float, layers: tuple, seeds: int, x: np.ndarray, y: np.ndarray, x_test: np.ndarray) -> np.ndarray:
    return np.mean([fit_predict(lambda s=s: network(alpha, layers, s), x, y, x_test) for s in range(seeds)], axis=0)


def target_members(d: dict[str, np.ndarray], y: np.ndarray, months: np.ndarray, train: np.ndarray,
                   test: np.ndarray) -> tuple[dict, dict]:
    """Predictions of every single model for the test weeks, and their out-of-fold predictions for the training
    weeks (3 folds of calendar months), from which the stacks are fitted."""
    lin, tre, smo = d["linear"], d["trees"], d["smooth"]
    tr, te = np.flatnonzero(train), np.flatnonzero(test)
    folds = [(tr[a], tr[b]) for a, b in GroupKFold(INNER_FOLDS).split(tr, y[tr], months[tr])]
    pred, oof = {}, {}

    def both(name, fn):
        pred[name] = fn(tr, te)
        oof[name] = np.empty(len(tr))
        for a, b in folds:
            oof[name][np.searchsorted(tr, b)] = fn(a, b)

    def ridge_then(make, x):
        """Ridge, plus `make` fitted on `x` to what ridge leaves on the fitting weeks."""
        def fn(a, b):
            r = ridge().fit(lin[a], y[a])
            return r.predict(lin[b]) + fit_predict(make, x[a], y[a] - r.predict(lin[a]), x[b])
        return fn

    both("linear", lambda a, b: lin[b] @ np.linalg.lstsq(lin[a], y[a], rcond=None)[0])
    both("ridge", lambda a, b: fit_predict(ridge, lin[a], y[a], lin[b]))
    both("gradient boosting", lambda a, b: fit_predict(trees, tre[a], y[a], tre[b]))
    k, oof["gradient boosting, tuned"] = choose([lambda s=s: trees(**s) for s in TREE_GRID], tre[tr], y[tr], months[tr],
                                                INNER_FOLDS)
    pred["gradient boosting, tuned"] = fit_predict(lambda: trees(**TREE_GRID[k]), tre[tr], y[tr], tre[te])
    both("random forest", lambda a, b: fit_predict(lambda: RandomForestRegressor(**FOREST), tre[a], y[a], tre[b]))
    both("extra trees", lambda a, b: fit_predict(lambda: ExtraTreesRegressor(**FOREST), tre[a], y[a], tre[b]))
    k, oof["neural network"] = choose([lambda a=a: network(a) for a in NN_ALPHAS], smo[tr], y[tr], months[tr],
                                      INNER_FOLDS)
    pred["neural network"] = bagged(NN_ALPHAS[k], (32, 16), TARGET_SEEDS, smo[tr], y[tr], smo[te])
    both("Gaussian process", lambda a, b: fit_predict(gaussian_process, smo[a], y[a], smo[b]))
    k, oof["support vector machine"] = choose([lambda s=s: scaled(SVR(epsilon=0.05, **s)) for s in SVR_GRID], smo[tr],
                                              y[tr], months[tr], INNER_FOLDS)
    pred["support vector machine"] = fit_predict(lambda: scaled(SVR(epsilon=0.05, **SVR_GRID[k])), smo[tr], y[tr],
                                                 smo[te])
    both("ridge, then trees", ridge_then(trees, tre))
    r = ridge().fit(lin[tr], y[tr])
    k, _ = choose([lambda a=a: network(a) for a in RESIDUAL_ALPHAS], smo[tr], y[tr] - r.predict(lin[tr]), months[tr],
                  INNER_FOLDS)
    pred["ridge, then neural network"] = r.predict(lin[te]) + bagged(RESIDUAL_ALPHAS[k], (32, 16), TARGET_SEEDS,
                                                                     smo[tr], y[tr] - r.predict(lin[tr]), smo[te])
    oof["ridge, then neural network"] = np.empty(len(tr))
    for a, b in folds:
        oof["ridge, then neural network"][np.searchsorted(tr, b)] = ridge_then(
            lambda: network(RESIDUAL_ALPHAS[k]), smo)(a, b)
    return pred, oof


def combine(pred: dict, oof: dict, y: np.ndarray) -> tuple[dict, np.ndarray]:
    """The combinations fixed in advance, and the weights of the stack of all eleven."""
    p, o = np.column_stack([pred[n] for n in SINGLES]), np.column_stack([oof[n] for n in SINGLES])
    three = [SINGLES.index(n) for n in THREE]
    w = en.stack_weights(o, y)
    return {"stacked, all": p @ w, "stacked: ridge, tuned trees, neural network":
            p[:, three] @ en.stack_weights(o[:, three], y), "mean of all": p.mean(axis=1),
            "mean: ridge, tuned trees, neural network": p[:, three].mean(axis=1)}, w


def target_predictions(target: str, inputs: str) -> tuple[pl.DataFrame, np.ndarray]:
    """Held-out predictions of every model for every week, and the mean stacking weights."""
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    with threadpool_limits(limits=1):
        df = mc.prepare(weekly_table(), target)
        d = designs(df, inputs)
        y, weeks, months = df["y"].to_numpy(), df["week_start"].to_numpy(), df["block"].to_numpy()
        buffer = np.timedelta64(7 * mc.BUFFER_WEEKS, "D")
        pred = {n: np.empty(len(y)) for n in [*SINGLES, *COMBINED]}
        weights = []
        for month in np.unique(months):
            test = months == month
            train = ~((weeks >= weeks[test].min() - buffer) & (weeks <= weeks[test].max() + buffer))
            p, o = target_members(d, y, months, train, test)
            c, w = combine(p, o, y[train])
            for name, v in {**p, **c}.items():
                pred[name][test] = v
            weights.append(w)
    return (df.select("week_start", "y", "block", (pl.col("presence_index") >= mc.IN_SESSION).alias("in_session"))
            .with_columns(**{n: pl.Series(v) for n, v in pred.items()}), np.mean(weights, axis=0))


def target_rows(target: str, runs: dict[str, pl.DataFrame]) -> list[dict]:
    """Every model's error, and its change against the better of the comparison's linear model and trees."""
    base = runs[INPUTS[0]]
    y, months = base["y"].to_numpy(), base["block"].to_numpy()
    best = min(["linear", "gradient boosting"], key=lambda m: ((y - base[m].to_numpy()) ** 2).mean())
    reference = (y - base[best].to_numpy()) ** 2
    rows_of = [np.flatnonzero(months == m) for m in np.unique(months)]
    rng = np.random.default_rng(0)
    draws = [np.concatenate([rows_of[i] for i in rng.integers(0, len(rows_of), len(rows_of))])
             for _ in range(BOOTSTRAP)]
    out_of_session = ~base["in_session"].to_numpy()
    rows = []
    for inputs, df in runs.items():
        for name in [*SINGLES, *COMBINED]:
            se = (y - df[name].to_numpy()) ** 2
            ratio = lambda r: np.sqrt(se[r].mean() / reference[r].mean())  # noqa: E731
            low, high = np.percentile([ratio(r) for r in draws], [2.5, 97.5])
            rows.append({"target": target, "inputs": inputs, "model": name,
                         "kind": "single" if name in SINGLES else "combined", "reference": best,
                         "rmse": float(np.sqrt(se.mean())), "typical_error_pct": 100 * (np.exp(np.sqrt(se.mean())) - 1),
                         "typical_error_pct_not_in_session": 100 * (np.exp(np.sqrt(se[out_of_session].mean())) - 1),
                         "rmse_change_pct": 100 * (ratio(slice(None)) - 1), "rmse_change_pct_low": 100 * (low - 1),
                         "rmse_change_pct_high": 100 * (high - 1)})
    return rows


def signal_rows(signal: str) -> list[dict]:
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    universities = weekly_table()
    columns = university_columns(universities)
    table = pl.read_csv(PROCESSED / "presence_signals_weekly.csv", try_parse_dates=True).join(
        universities.select("week_start", *columns), on="week_start", how="left")
    day = signal.split("_")[1]
    sets = {"students, everyone": tm.feature_sets(day)["students, everyone"],
            "everything": tm.feature_sets(day)["everything"],
            "everything and universities": tm.feature_sets(day)["everything"] + columns}
    singles = {"linear regression": lambda: scaled(LinearRegression()),
               "ridge": lambda: scaled(RidgeCV(alphas=np.logspace(-3, 3, 13))),
               "lasso": lambda: scaled(LassoCV(cv=tm.FOLDS, random_state=0)),
               "random forest": lambda: scaled(RandomForestRegressor(**FOREST)),
               "gradient boosting": lambda: scaled(trees()),
               "Gaussian process": lambda: gaussian_process(3.0, 0.5)}
    rows = []
    with threadpool_limits(limits=1):
        for inputs, cols in sets.items():
            df = tm.target_data(table, signal, cols)
            for evaluation, train, test in tm.splits(df):
                months = train["week_start"].dt.strftime("%Y-%m").to_numpy()
                x, y = train.select(cols).to_numpy(), train[signal].to_numpy()
                x_test = test.select(cols).to_numpy()
                folds = list(GroupKFold(tm.FOLDS).split(x, y, months))
                pred, oof = {}, {}

                def out_of_fold(fn):
                    out = np.empty(len(y))
                    for a, b in folds:
                        out[b] = fn(a, b)
                    return out
                for name, make in singles.items():
                    pred[name] = fit_predict(make, x, y, x_test)
                    oof[name] = out_of_fold(lambda a, b, make=make: fit_predict(make, x[a], y[a], x[b]))
                for name, layers in NETWORKS.items():
                    k, oof[name] = choose([lambda a=a: network(a, layers) for a in SIGNAL_ALPHAS], x, y, months,
                                          tm.FOLDS)
                    pred[name] = bagged(SIGNAL_ALPHAS[k], layers, SIGNAL_SEEDS, x, y, x_test)
                r = singles["ridge"]().fit(x, y)
                residual = y - r.predict(x)
                k, _ = choose([lambda a=a: network(a) for a in SIGNAL_ALPHAS], x, residual, months, tm.FOLDS)
                pred["ridge, then neural network"] = r.predict(x_test) + bagged(SIGNAL_ALPHAS[k], (32, 16),
                                                                                SIGNAL_SEEDS, x, residual, x_test)
                pred["ridge, then trees"] = r.predict(x_test) + fit_predict(trees, x, residual, x_test)
                for name, make in (("ridge, then neural network", lambda: network(SIGNAL_ALPHAS[k])),
                                   ("ridge, then trees", trees)):
                    def fn(a, b, make=make):
                        ra = singles["ridge"]().fit(x[a], y[a])
                        return ra.predict(x[b]) + fit_predict(make, x[a], y[a] - ra.predict(x[a]), x[b])
                    oof[name] = out_of_fold(fn)
                names = list(pred)
                p, o = np.column_stack([pred[n] for n in names]), np.column_stack([oof[n] for n in names])
                pred["stacked, all"], pred["mean of all"] = p @ en.stack_weights(o, y), p.mean(axis=1)
                for name, v in pred.items():
                    s = tm.scores(test[signal].to_numpy(), v, np.inf)
                    rows.append({"signal": signal, "inputs": inputs, "model": name,
                                 "kind": "combined" if name in ("stacked, all", "mean of all") else "single",
                                 "evaluation": evaluation, "test_year": int(test["week_start"].dt.year()[0]),
                                 "weeks": len(test), "r2": s["r2"], "mae": s["mae"]})
    return rows


def main() -> None:
    jobs = [(target, inputs) for target in mc.TARGETS for inputs in INPUTS]
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as pool:
        runs = {job: pool.submit(target_predictions, *job) for job in jobs}  # the long ones first
        signals = [pool.submit(signal_rows, signal) for signal in SIGNALS]
        runs = {job: f.result() for job, f in runs.items()}
        signal_results = [f.result() for f in signals]
    rows, weights = [], []
    for target in mc.TARGETS:
        rows += target_rows(target, {inputs: runs[(target, inputs)][0] for inputs in INPUTS})
        weights += [{"target": target, "inputs": inputs, "model": name, "weight": float(w)}
                    for inputs in INPUTS for name, w in zip(SINGLES, runs[(target, inputs)][1])]
    out = pl.DataFrame(rows).with_columns(pl.selectors.float().round(4))
    out.write_csv(PROCESSED / "model_search_targets.csv")
    pl.DataFrame(weights).with_columns(pl.col("weight").round(4)).write_csv(PROCESSED / "model_search_weights.csv")
    by_signal = pl.DataFrame([r for rows in signal_results for r in rows])
    by_signal.with_columns(pl.selectors.float().round(4)).write_csv(PROCESSED / "model_search_signals.csv")
    with pl.Config(tbl_rows=150, tbl_cols=-1, tbl_width_chars=250, fmt_str_lengths=45):
        print(out.select("target", "inputs", "model", pl.selectors.matches("typical_error_pct$|change").round(1))
              .sort("target", "rmse_change_pct"))


if __name__ == "__main__":
    main()
