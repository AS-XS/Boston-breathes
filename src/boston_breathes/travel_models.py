"""Which models and features explain the weekly presence signals best?

Each of the six signals of presence_signals.py (subway, bikes and 311
requests; weekdays and weekends) is predicted for years the model was not fitted on, by eight
families of models and six sets of features.

Families (scikit-learn; inputs and target standardized within the training
years):
  * linear regression; ridge and lasso (penalty chosen by cross-validation);
  * support vector machine (RBF kernel), k nearest neighbours: settings
    chosen once by cross-validation on the training years;
  * random forest (300 trees, at least 5 weeks a leaf), gradient boosting
    (the settings of model_comparison.py);
  * neural network: a multi-layer perceptron with two hidden layers (32 and
    16 units), its L2 penalty chosen by cross-validation.
Cross-validation within the training years holds out calendar months.

Feature sets (each as deviations from the year's mean; weather is that of the
same kind of day as the signal):
  * weather and calendar: no student information;
  * students, closed halls: the share out of session on weekdays, the share
    away if only students in closed residence halls leave on weekends;
  * students, everyone: the share away if every student out of session not
    living with their parents leaves, on weekends (the population model's
    choice);
  * students, summer and breaks: the shares on summer break and on other
    breaks, apart;
  * students and weather: the population model's choice, weather and
    calendar;
  * everything: all student inputs, weather and calendar.

Splits: 2023 from 2022, 2024 from 2022-2023, 2025 from 2022-2024
(validation), and January to September 2026 from 2022-2025 (test).

Accuracy, on the weeks predicted: R2 (the share of the week-to-week variance
explained), mean absolute error, and the share of weeks inside a 95%
interval (the prediction plus or minus the 95th percentile of the absolute
errors that cross-validation makes on the training years).

Output: data/processed/travel_model_accuracy.csv
"""

import warnings

import numpy as np
import polars as pl
from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import LassoCV, LinearRegression, RidgeCV
from sklearn.exceptions import ConvergenceWarning
from sklearn.model_selection import GridSearchCV, GroupKFold, cross_val_predict
from sklearn.neighbors import KNeighborsRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR
from threadpoolctl import threadpool_limits

from boston_breathes import model_comparison as mc
from boston_breathes import presence_signals as ps
from boston_breathes.paths import PROCESSED

SIGNALS = [f"{s}_{d}" for s in ps.NAMES for d in ps.DAYS]
VALIDATION_YEARS = [2023, 2024, 2025]
TEST_YEAR = 2026
FIRST_YEAR = 2022
FOLDS = 5
COVERAGE = 0.95


FAMILIES = {  # the model, and the settings tried by cross-validation on the training years
    "linear regression": (LinearRegression, {}),
    "ridge": (lambda: RidgeCV(alphas=np.logspace(-3, 3, 13)), {}),
    "lasso": (lambda: LassoCV(cv=FOLDS, random_state=0), {}),
    "support vector machine": (lambda: SVR(kernel="rbf"), {"C": [0.3, 1, 3, 10], "epsilon": [0.1, 0.3]}),
    "k nearest neighbours": (lambda: KNeighborsRegressor(weights="distance"), {"n_neighbors": [5, 10, 20]}),
    "random forest": (lambda: RandomForestRegressor(n_estimators=300, min_samples_leaf=5, random_state=0), {}),
    "gradient boosting": (lambda: HistGradientBoostingRegressor(**mc.TREE_SETTINGS), {}),
    "neural network": (lambda: MLPRegressor(hidden_layer_sizes=(32, 16), solver="lbfgs", max_iter=2000,
                                            random_state=0), {"alpha": [0.1, 1.0, 10.0]}),
}


def feature_sets(day: str) -> dict[str, list[str]]:
    weather = [f"{day}_{w}" for w in ps.WEATHER] + ps.CALENDAR
    away = (lambda version: ["out"] if day == "weekday" else [version])
    return {
        "weather and calendar": weather,
        "students, closed halls": away("min_share"),
        "students, everyone": away("max_share"),
        "students, summer and breaks": ["summer", "breaks"],
        "students and weather": away("max_share") + weather,
        "everything": ps.STUDENTS + weather,
    }


def model(family: str, settings: dict | None = None) -> TransformedTargetRegressor:
    """The family's model with standardized inputs and target."""
    estimator = FAMILIES[family][0]()
    estimator.set_params(**(settings or {}))
    return TransformedTargetRegressor(regressor=make_pipeline(StandardScaler(), estimator),
                                      transformer=StandardScaler())


def choose(family: str, x: np.ndarray, y: np.ndarray, months: np.ndarray) -> dict:
    """The settings with the lowest cross-validated error on the training weeks (holding out calendar months)."""
    grid = FAMILIES[family][1]
    if not grid:
        return {}
    step = model(family).regressor.steps[-1][0]
    search = GridSearchCV(model(family), {f"regressor__{step}__{k}": v for k, v in grid.items()},
                          cv=GroupKFold(FOLDS), scoring="neg_mean_squared_error")
    search.fit(x, y, groups=months)
    return {k.split("__")[-1]: v for k, v in search.best_params_.items()}


def fit_predict(family: str, x_train: np.ndarray, y_train: np.ndarray, months: np.ndarray, x_test: np.ndarray):
    """Predictions for the test weeks, and the 95th percentile of cross-validated absolute errors on the training
    weeks (holding out calendar months), with the settings chosen once on the training weeks."""
    settings = choose(family, x_train, y_train, months)
    fitted = model(family, settings).fit(x_train, y_train)
    cv = cross_val_predict(model(family, settings), x_train, y_train, groups=months, cv=GroupKFold(FOLDS))
    return fitted.predict(x_test), float(np.quantile(np.abs(y_train - cv), COVERAGE))


def scores(y: np.ndarray, pred: np.ndarray, half_width: float) -> dict[str, float]:
    return {"r2": float(1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()),
            "mae": float(np.abs(y - pred).mean()),
            "coverage": float((np.abs(y - pred) <= half_width).mean())}


def target_data(table: pl.DataFrame, signal: str, columns: list[str]) -> pl.DataFrame:
    """The weeks with this signal, its features again as deviations from their mean over those weeks of the
    year (the signal can miss weeks the features have)."""
    year = pl.col("week_start").dt.year()
    return (table.filter(pl.col(signal).is_not_null() & (year >= FIRST_YEAR))
            .with_columns(*[(pl.col(c) - pl.col(c).mean().over(year)) for c in columns]))


def splits(df: pl.DataFrame) -> list[tuple[str, pl.DataFrame, pl.DataFrame]]:
    year = pl.col("week_start").dt.year()
    out = [("validation", df.filter(year < y), df.filter(year == y)) for y in VALIDATION_YEARS]
    return out + [("test", df.filter(year < TEST_YEAR), df.filter(year == TEST_YEAR))]


def evaluate(table: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for signal in SIGNALS:
        day = signal.split("_")[1]
        for features, columns in feature_sets(day).items():
            df = target_data(table, signal, columns)
            for evaluation, train, test in splits(df):
                months = train["week_start"].dt.strftime("%Y-%m").to_numpy()
                for family in FAMILIES:
                    pred, half = fit_predict(family, train.select(columns).to_numpy(), train[signal].to_numpy(),
                                             months, test.select(columns).to_numpy())
                    base = {"signal": signal, "features": features, "family": family, "evaluation": evaluation,
                            "test_year": int(test["week_start"].dt.year()[0]), "weeks": len(test)}
                    rows += [{**base, "metric": k, "value": v}
                             for k, v in scores(test[signal].to_numpy(), pred, half).items()]
    return pl.DataFrame(rows)


def main() -> None:
    table = pl.read_csv(PROCESSED / "presence_signals_weekly.csv", try_parse_dates=True)
    warnings.filterwarnings("ignore", category=ConvergenceWarning)  # lbfgs stopping at max_iter on tiny data
    with threadpool_limits(limits=1):
        out = evaluate(table)
    out.with_columns(pl.col("value").round(4)).write_csv(PROCESSED / "travel_model_accuracy.csv")
    validation = out.filter((pl.col("evaluation") == "validation") & (pl.col("metric") == "r2"))
    summary = validation.group_by("signal", "features", "family").agg(pl.col("value").mean().alias("r2"))
    with pl.Config(tbl_rows=60, tbl_cols=-1, tbl_width_chars=250, fmt_str_lengths=30):
        for signal in SIGNALS:
            print(signal, "- mean validation R2")
            print(summary.filter(pl.col("signal") == signal).pivot(on="family", index="features", values="r2")
                  .with_columns(pl.selectors.numeric().round(2)))


if __name__ == "__main__":
    main()
