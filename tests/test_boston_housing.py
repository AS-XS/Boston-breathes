import polars as pl
import pytest

from boston_breathes import boston_housing as bh

HEADER = "fall_year,degree,on_campus,off_campus_at_home,off_campus_not_at_home,not_categorized,source_url,source_table,note"
SRC = "https://example.gov/report.pdf"


def write(tmp_path, rows: list[str]):
    path = tmp_path / "housing.csv"
    path.write_text("\n".join([HEADER, *rows]) + "\n")
    return path


def test_by_fall_adds_degrees_and_keeps_unreported_counts_empty(tmp_path):
    rows = [f"2019,undergraduate,30,5,15,,{SRC},Table 4,", f"2019,graduate,3,6,11,,{SRC},Table 4,"]
    out = bh.by_fall(bh.load_manual(write(tmp_path, rows)))
    r = out.row(0, named=True)
    assert (r["on_campus"], r["off_campus_at_home"], r["off_campus_not_at_home"]) == (33, 11, 26)
    assert r["not_categorized"] is None
    assert r["students_living_in_boston"] == 70
    assert (r["on_campus_undergrad"], r["on_campus_graduate"]) == (30, 3)


@pytest.mark.parametrize("row, message", [
    (f"2019,doctoral,1,1,1,,{SRC},Table 4,", "unknown degree"),
    ("2019,graduate,1,1,1,,,Table 4,", "missing source"),
    (f"2019,graduate,-1,1,1,,{SRC},Table 4,", "negative on_campus"),
])
def test_load_manual_rejects_bad_rows(tmp_path, row, message):
    with pytest.raises(ValueError, match=message):
        bh.load_manual(write(tmp_path, [row]))


def test_transcribed_totals_match_the_reports():
    out = bh.by_fall(bh.load_manual()).select("fall_year", "students_living_in_boston", "on_campus")
    stated = {2019: (86418, 40933), 2020: (64420, 24057), 2021: (83391, 39146), 2022: (90501, 41204),
              2023: (94100, 41490), 2024: (92146, 42381)}
    got = {r[0]: (r[1], r[2]) for r in out.iter_rows()}
    assert {y: got[y] for y in stated} == stated
    # The 2018 report's stated total is one more than its categories.
    assert got[2018] == (86462, 40092)
    assert out["fall_year"].to_list() == list(range(2018, 2025))
    assert pl.read_csv(bh.MANUAL_PATH)["source_url"].str.starts_with("https://www.boston.gov/").all()
