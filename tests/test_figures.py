from datetime import date

import polars as pl

from boston_breathes import figures as fig


def test_runs_split_a_flag_into_contiguous_spans():
    df = pl.DataFrame({
        "week_start": [date(2024, 7, 1), date(2024, 7, 8), date(2024, 7, 15), date(2024, 7, 22)],
        "flag": [False, False, True, True],
    })
    assert fig.runs(df, "flag") == [
        (date(2024, 7, 1), date(2024, 7, 14), False),
        (date(2024, 7, 15), date(2024, 7, 28), True),
    ]


def test_coverage_spans_do_not_overlap_within_a_dataset():
    rows = fig.coverage_rows().filter(pl.col("kind") != fig.COUNT_DAY).sort("dataset", "start")
    for _, g in rows.group_by("dataset"):
        starts, ends = g["start"].to_list(), g["end"].to_list()
        assert all(e < s for e, s in zip(ends, starts[1:]))
    # Enrollment after the latest published fall is marked as filled in.
    enrollment = rows.filter(pl.col("dataset") == "IPEDS fall enrollment").sort("start")
    assert enrollment["kind"][-1] == fig.FILLED


def test_calendar_weeks_have_one_state_and_a_reference_year():
    df = fig.calendar_data()
    assert set(df["state"]) <= {fig.IN_SESSION, fig.PARTIAL, fig.BREAK, fig.SUMMER}
    # Every week maps into the reference academic year August 2003 - July 2004.
    assert df["ref_start"].min() >= date(2003, 7, 28) and df["ref_end"].max() <= date(2004, 8, 4)
    # Years with an estimated date are starred, and only those.
    starred = df.filter(pl.col("year_label").str.ends_with("*"))
    assert (starred["estimated_events"] != "none (all verified)").all()


def test_enrollment_keeps_every_student():
    grouped = fig.enrollment_data()
    raw = pl.read_csv(fig.PROCESSED / "enrollment_annual.csv")
    assert grouped["total"].sum() == raw["total"].sum()
    assert grouped["institution"].n_unique() == fig.TOP_N + 1


def test_table_html_escapes_text():
    out = fig.table_html(pl.DataFrame({"Name": ["<b>A & B</b>"], "Value": [1234]}))
    assert "&lt;b&gt;A &amp; B&lt;/b&gt;" in out and "1,234" in out and "<b>" not in out


def test_academic_year_columns_place_weeks_on_the_reference_year():
    df = pl.DataFrame({"week_start": [date(2019, 7, 29), date(2019, 12, 30), date(2020, 2, 24)]})
    out = df.with_columns(fig.academic_year_columns())
    # Thursday Aug 1 2019 starts 2019-20; Thursday Jan 2 2020 and Feb 27 2020 stay in it.
    assert out["academic_year"].to_list() == ["2019-20"] * 3
    assert out["ref_start"].to_list() == [date(2003, 7, 29), date(2003, 12, 30), date(2004, 2, 24)]


def test_typical_year_leaves_out_partial_and_covid_years_and_highlights_one():
    df = fig.typical_year_data()
    assert not set(df["academic_year"]) & set(fig.EXCLUDED_YEARS)
    highlighted = df.filter(pl.col("highlight"))["academic_year"].unique()
    assert highlighted.to_list() == [df["academic_year"].max()]


def test_population_bounds_stay_below_the_census_line():
    df = fig.presence_data()
    assert (df["min_effective_population"] <= df["census_population"]).all()
    assert set(df["covid_label"]) == set(fig.COVID_LABELS.values())


def test_correlation_lines_break_at_missing_years():
    df = fig.correlation_data()
    assert set(df["source"]) == set(fig.SOURCES)
    assert df["r"].is_between(-1, 1).all()
    runs = df.with_columns((pl.col("year").diff().fill_null(1) != 1).cum_sum().over("source").alias("run"))
    for (_, _), g in runs.group_by("source", "run"):
        assert (g["year"].diff().drop_nulls() == 1).all()


def test_break_dips_leave_out_mismatched_campuses():
    df = fig.dip_data()
    assert "Tufts University" not in set(df["institution"])
    assert set(df["break"]) <= set(fig.BREAK_ORDER)


def test_street_pairs_are_finite_ratios_for_both_modes():
    df = fig.street_data()
    assert (df["ratio"] > 0).all() and df["ratio"].is_finite().all()
    assert set(df["mode"]) == set(fig.MODES.values())
