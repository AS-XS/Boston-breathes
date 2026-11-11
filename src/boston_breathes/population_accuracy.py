"""How accurate is the population estimate, in people? A test with a known truth.

Nobody counts the population each week, so the estimate cannot be compared
with the truth directly. Instead, weekly signals are made up from a truth that
is known: the real calendar inputs of 2022-2025, chosen shares away, the
scales k fitted on the subway and bikes (joint_population.py, no weather
model), and the noise those fits leave, shifted in time by a random number of
weeks for each signal (which keeps its week-to-week pattern). The joint model
is then fitted on the made-up signals as on the real ones, and its shares are
turned into students away for the weeks of 2019 (population_model.away). The
estimate is compared with the truth, in students away:
  * error: the mean of the absolute errors over the weeks of 2019, and the
    error in the mid-summer and Thanksgiving weeks;
  * coverage: how often the 95% interval (refitting on calendar months drawn
    with replacement) contains the true summer and break shares.

Truths: shares away of 0.5 and 0.5, 0.7 and 0.6, 0.9 and 0.74 (about the
fitted ones), and 1.0 and 1.0 (summer, breaks). A last run breaks the
model's key assumption: students make up a 20% larger share of weekend riders
than of weekday riders, so weekend signals are 1.2 times larger than the
model expects.

Output: data/processed/population_accuracy.csv
"""

import numpy as np
import polars as pl

from boston_breathes import joint_population as jp
from boston_breathes import population_model as pm
from boston_breathes.paths import PROCESSED

SIGNALS = ["subway", "bikes"]
TRUTHS = [(0.5, 0.5), (0.7, 0.6), (0.9, 0.74), (1.0, 1.0)]
WEEKEND_SCALE = 1.2  # the broken assumption: weekend signals this much larger than the model expects
REPLICATIONS = 200
WITH_INTERVALS = 40  # replications whose 95% interval is computed (each refits 200 times)
INTERVAL_DRAWS = 200
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


def students_away(inputs: pl.DataFrame, summer: float, breaks: float) -> np.ndarray:
    return pm.away(inputs, np.array([summer]), np.array([breaks]))[:, 0]


def interval(pieces: list[dict], rng: np.random.Generator) -> np.ndarray:
    months = sorted({w.strftime("%Y-%m") for p in pieces for w in p["weeks"]})
    index = [{m: np.flatnonzero(p["weeks"].dt.strftime("%Y-%m").to_numpy() == m) for m in months} for p in pieces]
    draws = []
    for _ in range(INTERVAL_DRAWS):
        picked = rng.choice(months, len(months))
        resampled = []
        for p, idx in zip(pieces, index):
            rows = np.concatenate([idx[m] for m in picked])
            resampled.append({**p, "y": p["y"][rows], "x": p["x"][rows], "weeks": p["weeks"].gather(rows)})
        draws.append(jp.fit(resampled, SIGNALS)[:2])
    return np.percentile(np.array(draws), [2.5, 97.5], axis=0)


def run(pieces: list[dict], params: np.ndarray, residuals: list[np.ndarray], inputs: pl.DataFrame,
        truth: tuple[float, float], label: str, rng: np.random.Generator, weekend_scale: float = 1.0) -> dict:
    true_away = students_away(inputs, *truth)
    weeks = inputs["week_start"].to_list()
    errors, shares, covered = [], [], []
    for i in range(REPLICATIONS):
        sim = simulate(pieces, params, truth, residuals, rng, weekend_scale)
        est = jp.fit(sim, SIGNALS)[:2]
        shares.append(est)
        errors.append(students_away(inputs, *est) - true_away)
        if i < WITH_INTERVALS:
            lo, hi = interval(sim, rng)
            covered.append((lo <= np.array(truth)) & (np.array(truth) <= hi))
    errors, shares, covered = np.array(errors), np.array(shares), np.array(covered)
    row = {"truth": label, "true_summer": truth[0], "true_breaks": truth[1],
           "mean_summer": float(shares[:, 0].mean()), "mean_breaks": float(shares[:, 1].mean()),
           "mean_abs_error_students": float(np.abs(errors).mean()),
           "coverage_summer": float(covered[:, 0].mean()), "coverage_breaks": float(covered[:, 1].mean())}
    for name, day in WEEKS.items():
        i = weeks.index(pl.Series([day]).str.to_date()[0])
        row[f"true_{name}"] = int(true_away[i])
        row[f"abs_error_{name}"] = float(np.abs(errors[:, i]).mean())
        row[f"error_{name}_low"], row[f"error_{name}_high"] = map(float, np.percentile(errors[:, i], [2.5, 97.5]))
    return row


def main() -> None:
    read = lambda name, **kw: pl.read_csv(PROCESSED / name, try_parse_dates=True, **kw)  # noqa: E731
    table = read("presence_signals_weekly.csv")
    pieces = jp.stacked(table, SIGNALS, jp.NO_MODEL)
    params = jp.fit(pieces, SIGNALS)
    k = dict(zip(SIGNALS, params[2:]))
    residuals = [p["y"] - k[p["signal"]] * (p["x"][:, 0] if p["day"] == "weekday" else p["x"] @ params[:2])
                 for p in pieces]
    inputs = pm.weekly_inputs(read("students_away_weekly.csv"), read("student_presence_weekly.csv",
                                                                      infer_schema_length=None))
    inputs = inputs.filter(pl.col("week_start").dt.year() == 2019)
    rng = np.random.default_rng(0)
    rows = [run(pieces, params, residuals, inputs, t, f"shares {t[0]:.2f} and {t[1]:.2f}", rng) for t in TRUTHS]
    rows.append(run(pieces, params, residuals, inputs, (0.7, 0.6), "shares 0.70 and 0.60, weekend riders 20% more "
                    "student than the model assumes", rng, WEEKEND_SCALE))
    out = pl.DataFrame(rows)
    out.write_csv(PROCESSED / "population_accuracy.csv")
    with pl.Config(tbl_cols=-1, tbl_width_chars=250, fmt_str_lengths=60):
        print(out.with_columns(pl.selectors.float().round(2)))


if __name__ == "__main__":
    main()
