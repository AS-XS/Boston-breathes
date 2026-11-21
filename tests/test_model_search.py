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

