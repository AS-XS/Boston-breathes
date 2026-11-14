"""How accurate is the population estimate, in people? A test with a known truth.

Nobody counts the population each week, so the estimate cannot be compared
with the truth directly. Instead, weekly signals are made up from a truth that
is known: the real calendar inputs of 2022-2025, chosen shares away, the
scales k fitted on the subway and Bluebikes (joint_population.py, no weather
model), and the noise those fits leave, shifted in time by a random number of
weeks for each signal (which keeps its week-to-week pattern). The joint model
is then fitted on the made-up signals as on the real ones, and its shares are
turned into students away for the weeks of 2019 (population_model.away).

Truths: shares away of 0.5 and 0.5, 0.7 and 0.6, 0.9 and 0.74 (about the
fitted ones), and 1.0 and 1.0 (summer, breaks). A last run breaks the
model's key assumption: students make up a 20% larger share of weekend riders
than of weekday riders, so weekend signals are 1.2 times larger than the
model expects.

Calibration of the intervals: for each made-up data set, the refits of
joint_population.bootstrap with runs of 1, 3 and 6 consecutive months, their
spread widened by 1.0 to 2.0, give 95% intervals for the two shares. The
rule: the shortest run, then the smallest widening, for which the intervals
of both shares contain the truth at least 95% of the time on average over
the four truths. joint_population.BLOCK_MONTHS and INFLATION are set to what
the rule chose (a test checks that they still are).

Accuracy, on new made-up data sets (so that the coverage is not that of the
data sets the intervals were chosen on), with the chosen intervals: the mean
of the absolute errors in students away over the weeks of 2019, the error in
the mid-summer and Thanksgiving weeks, and how often the intervals of the
shares and the band of students away (as population_estimate.py builds it)
contain the truth.

Outputs in data/processed/:
  * population_accuracy.csv              errors and coverage for each truth, with the chosen intervals
  * population_accuracy_calibration.csv  coverage for each run length and widening
"""

import numpy as np
import polars as pl

from boston_breathes import joint_population as jp
from boston_breathes import population_model as pm
from boston_breathes.paths import PROCESSED

SIGNALS = jp.MAIN_SIGNALS
TRUTHS = [(0.5, 0.5), (0.7, 0.6), (0.9, 0.74), (1.0, 1.0)]
WEEKEND_SCALE = 1.2  # the broken assumption: weekend signals this much larger than the model expects
REPLICATIONS = 200  # made-up data sets for each truth, for the errors
WITH_INTERVALS = 60  # of which this many also get intervals (each refits INTERVAL_DRAWS times per run length)
INTERVAL_DRAWS = 200
BLOCKS = [1, 3, 6]
INFLATIONS = [round(1 + 0.1 * i, 1) for i in range(11)]
TARGET = 0.95
WEEKS = {"mid-summer": "2019-07-08", "Thanksgiving": "2019-11-25"}


def simulate(pieces: list[dict], params: np.ndarray, truth: tuple[float, float], residuals: list[np.ndarray],
             rng: np.random.Generator, weekend_scale: float = 1.0) -> list[dict]:
    """Signals made from the true shares, the fitted scales, and the fitted noise shifted in time."""
    k = dict(zip(SIGNALS, params[2:]))
    out = []
    shift = {s: int(rng.integers(0, 10_000)) for s in SIGNALS}
    for p, r in zip(pieces, residuals):
        if p["day"] == "weekday":
            truth_part = k[p["signal"]] * p["x"][:, 0]
        else:
            truth_part = weekend_scale * k[p["signal"]] * (p["x"] @ np.array(truth))
        out.append({**p, "y": truth_part + np.roll(r, shift[p["signal"]] % len(r))})
    return out


def students_away(inputs: pl.DataFrame, summer, breaks) -> np.ndarray:
    """Students away each week (rows) for each pair of shares (columns)."""
    return pm.away(inputs, np.atleast_1d(summer), np.atleast_1d(breaks))


def covered(truth: np.ndarray, estimate: np.ndarray, draws: np.ndarray, inflation: float) -> np.ndarray:
    low, high = jp.interval(estimate, draws, inflation)
    return (low <= truth) & (truth <= high)


def replications(pieces: list[dict], params: np.ndarray, residuals: list[np.ndarray], inputs: pl.DataFrame,
                 truth: tuple[float, float], rng: np.random.Generator, blocks: list[int],
                 weekend_scale: float = 1.0) -> dict:
    """The fitted shares and the errors in students away of each made-up data set, and for the first
    WITH_INTERVALS of them the refits with runs of each length of months."""
    true_away = students_away(inputs, *truth)[:, 0]
    shares, errors, refits = [], [], []
    for i in range(REPLICATIONS):
        sim = simulate(pieces, params, truth, residuals, rng, weekend_scale)
        est = jp.fit(sim, SIGNALS)
        shares.append(est)
        errors.append(students_away(inputs, *est[:2])[:, 0] - true_away)
        if i < WITH_INTERVALS:
            refits.append({b: jp.bootstrap(sim, SIGNALS, rng, b, INTERVAL_DRAWS) for b in blocks})
    return {"truth": truth, "weekend_scale": weekend_scale, "true_away": true_away, "shares": np.array(shares),
            "errors": np.array(errors), "refits": refits}


def grid_rows(rep: dict) -> list[dict]:
    """Whether the intervals of each run length and widening contain the true shares, for each made-up data set."""
    rows = []
    for est, refits in zip(rep["shares"], rep["refits"]):
        for block, draws in refits.items():
            for inflation in INFLATIONS:
                ok = covered(np.array(rep["truth"]), est[:2], draws[:, :2], inflation)
                rows.append({"true_summer": rep["truth"][0], "true_breaks": rep["truth"][1], "block_months": block,
                             "inflation": inflation, "summer": ok[0], "breaks": ok[1]})
    return rows


def calibration(grid: pl.DataFrame) -> pl.DataFrame:
    """Coverage of each run length and widening: the mean over truths, and the lowest."""
    per_truth = grid.group_by("block_months", "inflation", "true_summer", "true_breaks").agg(
        pl.col("summer").mean(), pl.col("breaks").mean())
    return (per_truth.group_by("block_months", "inflation")
            .agg(pl.col("summer").mean().alias("coverage_summer"), pl.col("breaks").mean().alias("coverage_breaks"),
                 pl.col("summer").min().alias("lowest_coverage_summer"),
                 pl.col("breaks").min().alias("lowest_coverage_breaks"))
            .sort("block_months", "inflation"))


def rule(table: pl.DataFrame) -> tuple[int, float]:
    """The shortest run, then the smallest widening, whose intervals contain both true shares at least TARGET of the
    time on average over the truths."""
    ok = table.filter((pl.col("coverage_summer") >= TARGET) & (pl.col("coverage_breaks") >= TARGET))
    first = ok.sort("block_months", "inflation").row(0, named=True)
    return first["block_months"], first["inflation"]


def accuracy(rep: dict, inputs: pl.DataFrame, block: int, inflation: float) -> dict:
    """Errors in students away, and how often the chosen intervals of the shares and the band of students away
    contain the truth. The band is built as population_estimate.py builds it, from the 2.5th to the 97.5th
    percentile of the students away that the widened refits give (with the weekend factor known here)."""
    truth, true_away, errors = np.array(rep["truth"]), rep["true_away"], rep["errors"]
    shares, band = [], []
    for est, refits in zip(rep["shares"], rep["refits"]):
        shares.append(covered(truth, est[:2], refits[block][:, :2], inflation))
        widened = jp.widen(est, refits[block], inflation)
        low, high = np.percentile(students_away(inputs, widened[:, 0], widened[:, 1]), [2.5, 97.5], axis=1)
        band.append((low <= true_away) & (true_away <= high))
    shares, band = np.array(shares), np.array(band)
    weeks = inputs["week_start"].to_list()
    row = {"true_summer": truth[0], "true_breaks": truth[1], "weekend_scale": rep["weekend_scale"],
           "block_months": block, "inflation": inflation,
           "mean_summer": float(rep["shares"][:, 0].mean()), "mean_breaks": float(rep["shares"][:, 1].mean()),
           "mean_abs_error_students": float(np.abs(errors).mean()),
           "coverage_summer": float(shares[:, 0].mean()), "coverage_breaks": float(shares[:, 1].mean()),
           "band_coverage_weeks": float(band.mean())}
    for name, day in WEEKS.items():
        i = weeks.index(pl.Series([day]).str.to_date()[0])
        row[f"true_{name}"] = int(true_away[i])
        row[f"abs_error_{name}"] = float(np.abs(errors[:, i]).mean())
        row[f"error_{name}_low"], row[f"error_{name}_high"] = map(float, np.percentile(errors[:, i], [2.5, 97.5]))
        row[f"band_coverage_{name}"] = float(band[:, i].mean())
    return row


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    pieces = jp.stacked(read("presence_signals_weekly.csv"), SIGNALS, jp.NO_MODEL)
    params = jp.fit(pieces, SIGNALS)
    k = dict(zip(SIGNALS, params[2:]))
    residuals = [p["y"] - k[p["signal"]] * (p["x"][:, 0] if p["day"] == "weekday" else p["x"] @ params[:2])
                 for p in pieces]
    inputs = pm.weekly_inputs(read("students_away_weekly.csv"), read("student_presence_weekly.csv",
                                                                      infer_schema_length=None))
    inputs = inputs.filter(pl.col("week_start").dt.year() == 2019)
    rng = np.random.default_rng(0)
    chosen_on = [replications(pieces, params, residuals, inputs, truth, rng, BLOCKS) for truth in TRUTHS]
    table = calibration(pl.DataFrame([row for rep in chosen_on for row in grid_rows(rep)]))
    block, inflation = rule(table)
    checked_on = [replications(pieces, params, residuals, inputs, truth, rng, [block]) for truth in TRUTHS]
    checked_on.append(replications(pieces, params, residuals, inputs, (0.7, 0.6), rng, [block], WEEKEND_SCALE))
    out = pl.DataFrame([accuracy(rep, inputs, block, inflation) for rep in checked_on])
    out.write_csv(PROCESSED / "population_accuracy.csv")
    table.with_columns(pl.selectors.float().round(3)).write_csv(PROCESSED / "population_accuracy_calibration.csv")
    with pl.Config(tbl_cols=-1, tbl_rows=40, tbl_width_chars=250, fmt_str_lengths=60):
        print(table.with_columns(pl.selectors.float().round(3)))
        print(out.with_columns(pl.selectors.float().round(3)))
    print(f"Rule: runs of {block} months, spread widened by {inflation}; "
          f"joint_population uses {jp.BLOCK_MONTHS} and {jp.INFLATION}")


if __name__ == "__main__":
    main()
