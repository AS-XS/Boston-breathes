import numpy as np
import polars as pl
import pytest

from boston_breathes import ensembles as en
from boston_breathes import travel_models as tm
from boston_breathes.paths import PROCESSED


def test_stack_weights_recover_the_mix_and_drop_a_model_that_only_adds_noise():
    rng = np.random.default_rng(0)
    a, b, noise = rng.normal(size=200), rng.normal(size=200), rng.normal(size=200)
    y = 0.3 * a + 0.7 * b
    assert en.stack_weights(np.column_stack([a, b]), y) == pytest.approx([0.3, 0.7], abs=1e-9)
    w = en.stack_weights(np.column_stack([a, b, -y + noise]), y)  # the third moves against y
    assert w[2] == pytest.approx(0, abs=1e-9) and w.sum() == pytest.approx(1) and (w >= 0).all()


def test_new_families_learn_a_straight_line():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(120, 2))
    y = 2 * x[:, 0] - x[:, 1] + rng.normal(0, 0.1, 120)
    for family in en.NEW_FAMILIES:
        pred = tm.model(family, {}, en.FAMILIES).fit(x[:100], y[:100]).predict(x[100:])
        assert tm.scores(y[100:], pred, np.inf)["r2"] > 0.7, family


def test_combinations_take_weights_and_choices_from_the_training_years_only():
    rng = np.random.default_rng(0)
    y = rng.normal(size=60)
    oof = {f: y + rng.normal(0, 1, 60) for f in en.FAMILIES}
    oof["ridge"] = y.copy()  # perfect out of fold on the training years
    pred = {f: np.full(5, i, dtype=float) for i, f in enumerate(en.FAMILIES)}
    out = en.combinations(pred, oof, y)
    ridge = list(en.FAMILIES).index("ridge")
    assert (out["chosen by cross-validation, the eight"] == ridge).all()
    assert out["stacked, all eleven"] == pytest.approx(np.full(5, ridge))
    assert out["mean of all eleven"] == pytest.approx(np.full(5, np.mean(range(len(en.FAMILIES)))))


def test_processed_single_models_are_those_of_the_comparisons_they_extend():
    targets = pl.read_csv(PROCESSED / "ensemble_targets_accuracy.csv").filter(pl.col("subset") == "all weeks")
    comparison = pl.read_csv(PROCESSED / "model_comparison.csv").filter(
        (pl.col("model") == "index") & (pl.col("subset") == "all weeks") & (pl.col("metric") == "rmse"))
    for family in (en.LINEAR, en.TREES):
        ours = targets.filter(pl.col("model") == family).sort("target")["rmse"]
        theirs = comparison.filter(pl.col("family") == family).sort("target")["value"]
        assert ours.to_list() == pytest.approx(theirs.to_list(), abs=1e-4)
    # Against the best single model: zero for it, and the same draws of months for every model.
    best = targets.filter(pl.col("model") == pl.col("best_single"))
    assert (best["rmse_change_pct"] == 0).all() and (best["rmse_change_pct_high"] == 0).all()
    signals = pl.read_csv(PROCESSED / "ensemble_signals_accuracy.csv")
    keys = ["signal", "features", "model", "test_year"]
    joined = signals.join(pl.read_csv(PROCESSED / "travel_model_accuracy.csv").filter(pl.col("metric") == "r2")
                          .rename({"family": "model"}).select(*keys, "value"), on=keys)
    assert len(joined) == len(en.SIGNALS) * len(en.FEATURE_SETS) * 4 * len(tm.FAMILIES)
    assert (joined["r2"] - joined["value"]).abs().max() < 1e-3
