import numpy as np
import polars as pl
import pytest

from boston_breathes import model_comparison as mc
from boston_breathes import model_search as ms
from boston_breathes import travel_models as tm
from boston_breathes.paths import PROCESSED


def test_extra_inputs_split_the_breaks_and_follow_the_index():
    table = ms.weekly_table()
    other = (1 - table["presence_index"] - table["summer_break_share"]).clip(lower_bound=0)
    by_kind = table["thanksgiving_share"] + table["winter_share"] + table["spring_share"]
    months = table["week_start"].dt.month()
    assert (by_kind - other).filter(months.is_in([11, 12, 1, 2, 3, 4])).abs().max() < 1e-12
    assert (by_kind.filter(~months.is_in([11, 12, 1, 2, 3, 4])) == 0).all()
    assert table["index_change"][1:].to_list() == pytest.approx(table["presence_index"].diff()[1:].to_list())
    assert len(ms.university_columns(table)) == ms.TOP_UNIVERSITIES


def test_the_comparisons_inputs_are_model_comparisons_designs():
    df = mc.prepare(ms.weekly_table(), "MBTA total")
    base, more = ms.designs(df, ms.INPUTS[0]), ms.designs(df, ms.INPUTS[1])
    assert np.array_equal(base["linear"], mc.design(df, mc.WEEKS, ["presence_index"]))
    assert np.array_equal(base["trees"], mc.design(df, mc.TREES, ["presence_index"]))
    added = len(ms.EXTRA) + ms.TOP_UNIVERSITIES
    assert {k: more[k].shape[1] - base[k].shape[1] for k in base} == {"linear": added, "trees": added, "smooth": added}
    assert not any(np.isnan(m).any() for m in more.values())



def test_processed_search_reproduces_the_models_it_extends():
    out = pl.read_csv(PROCESSED / "model_search_targets.csv")
    comparison = pl.read_csv(PROCESSED / "model_comparison.csv").filter(
        (pl.col("model") == "index") & (pl.col("subset") == "all weeks") & (pl.col("metric") == "rmse"))
    base = out.filter(pl.col("inputs") == ms.INPUTS[0])
    for ours, theirs in (("linear", mc.WEEKS), ("gradient boosting", mc.TREES)):
        a = base.filter(pl.col("model") == ours).sort("target")["rmse"].to_list()
        b = comparison.filter(pl.col("family") == theirs).sort("target")["value"].to_list()
        assert a == pytest.approx(b, abs=1e-4)
    reference = base.filter(pl.col("model") == pl.col("reference"))
    assert len(reference) == len(mc.TARGETS) and (reference["rmse_change_pct"] == 0).all()
    assert len(out) == len(mc.TARGETS) * len(ms.INPUTS) * (len(ms.SINGLES) + len(ms.COMBINED))
    weights = pl.read_csv(PROCESSED / "model_search_weights.csv")
    assert weights.group_by("target", "inputs").agg(pl.col("weight").sum())["weight"].to_list() == pytest.approx(
        [1.0] * len(mc.TARGETS) * len(ms.INPUTS), abs=1e-3)
    signals = pl.read_csv(PROCESSED / "model_search_signals.csv")
    keys = ["signal", "features", "model", "test_year"]
    linear = signals.filter((pl.col("inputs") == "students, everyone") & (pl.col("model") == "linear regression"))
    joined = linear.rename({"inputs": "features"}).join(
        pl.read_csv(PROCESSED / "travel_model_accuracy.csv").filter(pl.col("metric") == "r2")
        .rename({"family": "model"}).select(*keys, "value"), on=keys)
    assert len(joined) == len(ms.SIGNALS) * 4 and (joined["r2"] - joined["value"]).abs().max() < 1e-3
    assert set(signals["model"]) == {*ms.NETWORKS, "linear regression", "ridge", "lasso", "random forest",
                                     "gradient boosting", "Gaussian process", "ridge, then neural network",
                                     "ridge, then trees", "stacked, all", "mean of all"}
