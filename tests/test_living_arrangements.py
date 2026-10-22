import io
import zipfile

import polars as pl
import pytest

from boston_breathes import living_arrangements as la


def zipped(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def test_college_housing_2020_reads_town_records():
    geo = ("PLST|MA|040|00|00|000|00|0000001|0400000US25|x\n"
           "PLST|MA|060|00|00|000|00|0000002|0600000US2501711000|x\n")
    seg3 = ("PLST|MA|000|03|0000001|10|1|1|1|1|1|9|8|1|0\n"
            "PLST|MA|000|03|0000002|20|2|1|1|0|0|18|15|0|3\n")
    out = la.college_housing_2020(zipped({"mageo2020.pl": geo, "ma000032020.pl": seg3}))
    assert out.rows() == [("2501711000", 15)]


def test_groups_merge_pumas_sharing_a_town():
    pairs = pl.DataFrame({"puma": ["03301", "03302", "00506"], "geoid": ["boston", "boston", "cambridge"]})
    out = dict(la.groups(pairs).select("puma", "group").rows())
    assert out == {"03301": "03301", "03302": "03301", "00506": "00506"}


def test_pums_counts_keeps_the_main_vintage_and_rescales():
    csv = ("SERIALNO,PUMA00,PUMA10,PWGTP,SCHG,RELP\n"
           "2011A,00100,-9,40,15,17\n"     # 2011 record with the old vintage: dropped
           "2012A,-9,00506,10,15,17\n"     # dorm
           "2012B,-9,00506,20,16,2\n"      # child of the householder
           "2012C,-9,00506,30,15,0\n"      # householder
           "2012D,-9,00506,60,1,2\n")      # not in college
    out = la.pums_counts(zipped({"ss15pma.csv": csv}), 2015)
    row = out.row(0, named=True)
    # Kept weights 120 of 160, so each is scaled by 4/3.
    assert (row["puma"], row["vintage"], row["records"]) == ("00506", 2010, 3)
    assert (row["dorm"], row["with_parents"], row["other"]) == pytest.approx((40 / 3, 80 / 3, 40))
    assert len(out) == 1


def test_pums_counts_reads_the_newer_relationship_codes():
    csv = ("SERIALNO,PUMA,PWGTP,SCHG,RELSHIPP\n"
           "2023GQ1,00801,5,15,38\n"
           "2023A,00801,7,16,25\n"
           "2023B,00801,9,15,34\n")
    row = la.pums_counts(zipped({"psam_p25.csv": csv}), 2024).row(0, named=True)
    assert (row["vintage"], row["dorm"], row["with_parents"], row["other"]) == (2020, 5, 7, 9)


def test_split_towns_places_dorms_by_census_housing_and_keeps_totals():
    residents = pl.DataFrame({"municipality": ["Newton", "Brookline"], "geoid": ["n", "b"],
                              "acs_year": [2019, 2019], "college_students": [1000, 500]})
    counts = pl.DataFrame({"puma": ["03400"], "acs_year": [2019], "vintage": [2010],
                           "dorm": [800.0], "with_parents": [100.0], "other": [300.0], "records": [90]})
    membership = pl.DataFrame({"vintage": [2010, 2010], "puma": ["03400", "03400"], "geoid": ["n", "b"],
                               "group": ["03400", "03400"]})
    housing = pl.DataFrame({"geoid": ["n", "b"], "college_housing_2020": [900, 100]})
    out = {r["municipality"]: r for r in la.split_towns(residents, counts, membership, housing).iter_rows(named=True)}
    assert out["Newton"]["in_university_housing"] == 720  # 800 x 900 / 1000
    assert out["Brookline"]["in_university_housing"] == 80
    # A quarter of the group's students outside dorms live with their parents.
    assert out["Newton"]["with_parents"] == 70  # (1000 - 720) / 4
    for r in out.values():
        assert r["in_university_housing"] + r["with_parents"] + r["elsewhere"] == r["college_students"]
