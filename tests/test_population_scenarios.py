from datetime import date, timedelta

import polars as pl
import pytest

from boston_breathes import population_scenarios as ps
from boston_breathes.paths import PROCESSED


def test_scaling_students_leaves_those_living_with_parents_alone():
    arrangements = pl.DataFrame({"municipality": ["A"], "acs_year": [2023], "college_students": [1000],
                                 "in_university_housing": [300], "with_parents": [200], "elsewhere": [500]})
    out = ps.scale_students(arrangements, -0.10).row(0, named=True)
    assert (out["in_university_housing"], out["with_parents"], out["elsewhere"]) == (270, 200, 450)
    assert out["college_students"] == 920


def spring_weeks(off: dict[str, dict[date, float]], in_person: dict[str, int]) -> pl.DataFrame:
    weeks = [date(2026, 2, 2) + timedelta(weeks=i) for i in range(13)]
    return pl.DataFrame([{"week_start": w, "unitid": u, "in_person": in_person[u], "summer_share": 0.0,
                          "session_share": 1 - off[u].get(w, 0.0)} for u in off for w in weeks])


def test_aligning_spring_breaks_moves_each_break_to_the_week_most_students_have_theirs():
    mar2, mar9, mar16, mar23, feb16 = (date(2026, 3, 2), date(2026, 3, 9), date(2026, 3, 16), date(2026, 3, 23),
                                       date(2026, 2, 16))
    off = {"a": {mar9: 1.0}, "b": {mar16: 1.0, mar23: 2 / 7}, "c": {mar2: 3 / 7, mar9: 1.0, feb16: 1 / 7}}
    out = ps.align_spring_breaks(spring_weeks(off, {"a": 1000, "b": 500, "c": 200}))
    got = {(r["unitid"], r["week_start"]): 1 - r["session_share"] for r in out.iter_rows(named=True)}
    # Most students (a and c, 1,200) have their fullest week on March 9: b's break moves one week earlier.
    assert got[("b", mar9)] == pytest.approx(1.0) and got[("b", mar16)] == pytest.approx(2 / 7)
    assert got[("b", mar23)] == pytest.approx(0.0)
    assert got[("a", mar9)] == pytest.approx(1.0) and got[("c", mar2)] == pytest.approx(3 / 7)
    # A day off apart from the break stays where it was.
    assert got[("c", feb16)] == pytest.approx(1 / 7)
    for u in off:
        assert sum(v for (unit, _), v in got.items() if unit == u) == pytest.approx(sum(off[u].values()))


def test_a_remote_term_takes_only_the_remote_institutions_out_of_session_in_that_fall():
    weeks = [date(2025, 9, 15), date(2026, 2, 9), date(2024, 9, 16)]
    per_inst = pl.DataFrame([{"week_start": w, "unitid": u, "session_share": 1.0} for u in ("r", "h") for w in weeks])
    policy = pl.DataFrame({"unitid": ["r", "h", "r"], "term": ["fall_2020", "fall_2020", "spring_2021"],
                           "instruction": ["remote", "hybrid", "remote"]})
    out = {(r["unitid"], r["week_start"]): r["session_share"] for r in ps.remote_term(per_inst, policy).iter_rows(named=True)}
    assert out[("r", weeks[0])] == 0.0
    assert out[("r", weeks[1])] == out[("r", weeks[2])] == 1.0  # spring, and another year
    assert {out[("h", w)] for w in weeks} == {1.0}


def test_processed_scenarios_keep_the_baseline_and_change_what_they_say():
    s = pl.read_csv(PROCESSED / "population_scenarios.csv", try_parse_dates=True)
    e = pl.read_csv(PROCESSED / "population_estimate.csv", try_parse_dates=True, infer_schema_length=None)
    base = s.filter(pl.col("scenario") == ps.BASELINE).select(ps.COLUMNS)
    assert base.equals(e.join(base.select("week_start"), on="week_start").select(ps.COLUMNS))
    total = lambda name, months: s.filter((pl.col("scenario") == name)  # noqa: E731
                                          & ps.thursday_month().is_between(*months))["students_away"].sum()
    # Aligning breaks moves the time away, it does not add any; where breaks bunch, the most that could be away
    # (students_away.py) can cap a week a little.
    assert total(ps.ALIGNED, ps.SPRING_MONTHS) == pytest.approx(total(ps.BASELINE, ps.SPRING_MONTHS), rel=0.005)
    assert total(ps.ALIGNED, ps.SPRING_MONTHS) <= total(ps.BASELINE, ps.SPRING_MONTHS) + 50
    summary = {r["scenario"]: r for r in pl.read_csv(PROCESSED / "population_scenarios_summary.csv").iter_rows(named=True)}
    fewer, more = (summary[n]["average_change_from_baseline"] for n in ps.ENROLLMENT)
    assert fewer < 0 < more and fewer == pytest.approx(-more, abs=2)
    assert summary[ps.REMOTE]["largest_rise_from_baseline"] == 0 < -summary[ps.REMOTE]["largest_fall_from_baseline"]
