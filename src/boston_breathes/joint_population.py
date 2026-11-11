"""One population, several signals: the shares of students who leave, with weather removed by different models.

Each weekly signal of presence_signals.py is modelled as

  signal[week] = k_signal x students missing[week] + g(weather and calendar[week]) + noise

where students missing is, on weekdays, the share of students out of session
(whether they left or stay without classes), and on weekends the share away:
summer share away x share on summer break + break share away x share on
other breaks. The two shares away are the same for every signal; each signal
has its own scale k and its own effect g of weather (that of the same kind of
day) and calendar (holidays, the Christmas-New Year week).

g is removed by partialling out (Robinson's method, as in double machine
learning): the signal and each student input are predicted from weather and
calendar alone by one family of models of travel_models.py, fitted on the
other calendar months (cross-fitting, so that a flexible model cannot absorb
the students' part by overfitting), and the shares are fitted on what is left,
jointly across signals by least squares, each signal weighted by the inverse
of its noise variance. With no weather model, this is population_model.py's
summer-and-breaks version fitted on several signals at once.

Intervals: the last step refitted on runs of BLOCK_MONTHS consecutive months
drawn with replacement (a moving-block bootstrap), and the spread of the
refits around the estimate widened by INFLATION. Both were chosen with the
known-truth test of population_accuracy.py, so that 95% intervals contain
the true shares about 95% of the time: unwidened, they did so only 79-86% of
the time on average, whether the runs were one, three or six months long, so
runs of one month are widened by 1.4.

The subway and Bluebikes together, with no weather model, give the band of
the population estimate (population_estimate.py).

Fitted on 2022-2025 for: each signal alone, subway and bikes together, and
all three signals together; and for the subway and bikes together with every
family of weather model.

Output: data/processed/joint_population_shares.csv
"""

import warnings

import numpy as np
import polars as pl
from scipy.optimize import least_squares
from sklearn.exceptions import ConvergenceWarning
from sklearn.model_selection import GroupKFold, cross_val_predict
from threadpoolctl import threadpool_limits

from boston_breathes import presence_signals as ps
from boston_breathes import travel_models as tm
from boston_breathes.paths import PROCESSED

YEARS = (2022, 2025)
PARTS = {"weekday": ["out"], "weekend": ["summer", "breaks"]}
COMBINATIONS = {"subway": ["subway"], "bikes": ["bikes"], "requests": ["requests"],
                "subway and bikes": ["subway", "bikes"], "all three": ["subway", "bikes", "requests"]}
NO_MODEL = "none"
MAIN_SIGNALS = COMBINATIONS["subway and bikes"]  # the signals of the population estimate's band
BOOTSTRAP = 1000
BLOCK_MONTHS = 1  # consecutive months resampled together } chosen by population_accuracy.py
INFLATION = 1.4  # the refits' spread around the estimate is widened by this factor }


def weather_columns(day: str) -> list[str]:
    return [f"{day}_{w}" for w in ps.WEATHER] + ps.CALENDAR


def partial_out(df: pl.DataFrame, signal: str, day: str, family: str) -> pl.DataFrame:
    """The signal and the student inputs less what weather and calendar predict, cross-fitted by calendar month."""
    columns = [f"{signal}_{day}", *PARTS[day]]
    if family == NO_MODEL:
        return df.select("week_start", *[pl.col(c).alias(f"{c}_left") for c in columns])
    x = df.select(weather_columns(day)).to_numpy()
    months = df["week_start"].dt.strftime("%Y-%m").to_numpy()
    out = {"week_start": df["week_start"]}
    for c in columns:
        y = df[c].to_numpy()
        settings = tm.choose(family, x, y, months)
        out[f"{c}_left"] = y - cross_val_predict(tm.model(family, settings), x, y, groups=months, cv=GroupKFold(tm.FOLDS))
    return pl.DataFrame(out)


def stacked(table: pl.DataFrame, signals: list[str], family: str) -> list[dict]:
    """For each signal and kind of day, what is left of the signal and of the student inputs."""
    pieces = []
    for signal in signals:
        for day in ps.DAYS:
            df = tm.target_data(table, f"{signal}_{day}", weather_columns(day) + PARTS[day])
            df = df.filter(pl.col("week_start").dt.year().is_between(*YEARS))
            left = partial_out(df, signal, day, family)
            pieces.append({"signal": signal, "day": day, "weeks": left["week_start"],
                           "y": left[f"{signal}_{day}_left"].to_numpy(),
                           "x": left.select([f"{p}_left" for p in PARTS[day]]).to_numpy()})
    return pieces


def residuals(params: np.ndarray, pieces: list[dict], signals: list[str], weights: dict) -> np.ndarray:
    """params: the share away in summer, the share away on breaks, then k of each signal."""
    shares, k = params[:2], dict(zip(signals, params[2:]))
    out = []
    for p in pieces:
        pred = k[p["signal"]] * (p["x"][:, 0] if p["day"] == "weekday" else p["x"] @ shares)
        out.append((p["y"] - pred) * weights[(p["signal"], p["day"])])
    return np.concatenate(out)


def fit(pieces: list[dict], signals: list[str]) -> np.ndarray:
    """Shares and scales by least squares, each signal and kind of day weighted by its noise (two steps)."""
    weights = {(p["signal"], p["day"]): 1.0 for p in pieces}
    start = np.r_[0.8, 0.8, [np.polyfit(p["x"][:, 0], p["y"], 1)[0] for p in pieces if p["day"] == "weekday"]]
    for _ in range(2):
        params = least_squares(residuals, start, args=(pieces, signals, weights)).x
        for p in pieces:
            r = residuals(params, [p], signals, {(p["signal"], p["day"]): 1.0})
            weights[(p["signal"], p["day"])] = 1 / r.std()
        start = params
    return params


def bootstrap(pieces: list[dict], signals: list[str], rng: np.random.Generator, block_months: int = BLOCK_MONTHS,
              draws: int = BOOTSTRAP) -> np.ndarray:
    """The last step refitted on runs of block_months consecutive calendar months drawn with replacement, as many
    runs as it takes to cover the months of the data."""
    months = sorted({w.strftime("%Y-%m") for p in pieces for w in p["weeks"]})
    index = [{m: np.flatnonzero(p["weeks"].dt.strftime("%Y-%m").to_numpy() == m) for m in months} for p in pieces]
    runs = -(-len(months) // block_months)
    out = []
    for _ in range(draws):
        starts = rng.integers(0, len(months) - block_months + 1, runs)
        picked = [months[s + i] for s in starts for i in range(block_months)]
        resampled = []
        for p, idx in zip(pieces, index):
            rows = np.concatenate([idx[m] for m in picked])
            resampled.append({**p, "y": p["y"][rows], "x": p["x"][rows], "weeks": p["weeks"].gather(rows)})
        out.append(fit(resampled, signals))
    return np.array(out)


def widen(params: np.ndarray, draws: np.ndarray, inflation: float = INFLATION) -> np.ndarray:
    """Refits moved away from the estimate by the inflation factor."""
    return params + inflation * (draws - params)


def interval(params: np.ndarray, draws: np.ndarray, inflation: float = INFLATION) -> tuple[np.ndarray, np.ndarray]:
    low, high = np.percentile(widen(params, draws, inflation), [2.5, 97.5], axis=0)
    return low, high


def main_shares(table: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """The shares away fitted on the subway and Bluebikes, and their widened refits (for the population band)."""
    pieces = stacked(table, MAIN_SIGNALS, NO_MODEL)
    params = fit(pieces, MAIN_SIGNALS)
    return params[:2], widen(params, bootstrap(pieces, MAIN_SIGNALS, np.random.default_rng(0)))[:, :2]


def rows_for(label: str, family: str, signals: list[str], pieces: list[dict], rng: np.random.Generator) -> list[dict]:
    params, draws = fit(pieces, signals), bootstrap(pieces, signals, rng)
    names = ["share_summer", "share_breaks"] + [f"k_{s}" for s in signals]
    low, high = interval(params, draws)
    weeks = sum(len(p["y"]) for p in pieces)
    return [{"signals": label, "weather_model": family, "parameter": n, "estimate": float(v), "low": float(lo),
             "high": float(hi), "signal_weeks": weeks} for n, v, lo, hi in zip(names, params, low, high)]


def build(table: pl.DataFrame) -> pl.DataFrame:
    """Each fit's refits start from the same seed, so those of the subway and Bluebikes without a weather model
    are the ones of the population band (main_shares)."""
    rows = []
    for label, signals in COMBINATIONS.items():
        rows += rows_for(label, NO_MODEL, signals, stacked(table, signals, NO_MODEL), np.random.default_rng(0))
    both = COMBINATIONS["subway and bikes"]
    for family in tm.FAMILIES:
        rows += rows_for("subway and bikes", family, both, stacked(table, both, family), np.random.default_rng(0))
    return pl.DataFrame(rows)


def main() -> None:
    table = pl.read_csv(PROCESSED / "presence_signals_weekly.csv", try_parse_dates=True)
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    with threadpool_limits(limits=1):
        out = build(table)
    out.with_columns(pl.col("estimate", "low", "high").round(4)).write_csv(PROCESSED / "joint_population_shares.csv")
    shares = out.filter(pl.col("parameter").str.starts_with("share_")).with_columns(
        pl.format("{} ({}-{})", *[pl.col(c).round(2) for c in ("estimate", "low", "high")]).alias("value"))
    with pl.Config(tbl_rows=40, tbl_width_chars=200, fmt_str_lengths=40):
        print(shares.pivot(on="parameter", index=["signals", "weather_model"], values="value"))


if __name__ == "__main__":
    main()
