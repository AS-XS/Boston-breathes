import io
import zipfile

import pytest

from boston_breathes import student_residents as sr

GEOS = list(sr.GEOIDS)


def table_based_text(geoids: list[str]) -> str:
    cols = [f"B14007_{k}{n:03d}" for n in range(1, 20) for k in ("E", "M")]
    lines = ["|".join(["GEO_ID", *cols])]
    lines.append("|".join(["0100000US", *["9"] * len(cols)]))  # the nation, ignored
    for i, geo in enumerate(geoids):
        vals = []
        for n in range(1, 20):
            vals += [str(1000 * (i + 1) + n), str(n)]
        lines.append("|".join([f"0600000US{geo}", *vals]))
    return "\n".join(lines) + "\n"


def test_parse_table_based():
    df = sr.parse_table_based(table_based_text(GEOS), 2023).sort("municipality")
    boston = df.filter(df["municipality"] == "Boston").row(0, named=True)
    # Boston is the first GEOID: undergrad line 17 = 1017, graduate line 18 = 1018.
    assert (boston["undergrad"], boston["graduate"], boston["college_students"]) == (1017, 1018, 2035)
    assert (boston["undergrad_moe"], boston["graduate_moe"]) == (17, 18)
    assert boston["population_3plus"] == 1001
    assert boston["period"] == "2019-2023"
    assert len(df) == 4


def test_missing_municipality_is_an_error():
    with pytest.raises(ValueError, match="Brookline"):
        sr.parse_table_based(table_based_text(GEOS[:3]), 2023)


LOOKUP = """\
File ID,Table ID,Sequence Number,Line Number,Start Position,Total Cells in Table,Total Cells in Sequence,Table Title,Subject Area
ACSSF,B01001,0001,,7,49 CELLS,,SEX BY AGE,Age-Sex
ACSSF,B14007,0040,,8,19 CELLS,,SCHOOL ENROLLMENT,School Enrollment
ACSSF,B14007,0040,1,,,,Total:,
"""


def sequence_zip(year: int) -> bytes:
    geo_rows, est_rows, moe_rows = [], [], []
    for i, geo in enumerate(GEOS):
        rec = f"{i + 1:07d}"
        geo_rows.append(f"ACSSF,MA,060,00,{rec},,,06000US{geo},\"Town {i}\"")
        # Fields: FILEID,FILETYPE,STUSAB,CHARITER,SEQUENCE,LOGRECNO, one other cell, then B14007 (start 8).
        cells = [str(100 * (i + 1) + n) for n in range(1, 20)]
        est_rows.append(",".join(["ACSSF", f"{year}e5", "ma", "000", "0040", rec, "5", *cells]))
        moe_rows.append(",".join(["ACSSF", f"{year}m5", "ma", "000", "0040", rec, "1", *[str(n) for n in range(1, 20)]]))
    geo_rows.append("ACSSF,MA,040,00,0000001,,,04000US25,Massachusetts")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"g{year}5ma.csv", "\n".join(geo_rows))
        zf.writestr(f"e{year}5ma0040000.txt", "\n".join(est_rows))
        zf.writestr(f"m{year}5ma0040000.txt", "\n".join(moe_rows))
    return buf.getvalue()


def test_table_position():
    assert sr.table_position(LOOKUP) == ("0040", 8)


def test_parse_sequence_based():
    df = sr.parse_sequence_based(sequence_zip(2019), LOOKUP, 2019)
    boston = df.filter(df["municipality"] == "Boston").row(0, named=True)
    assert (boston["undergrad"], boston["graduate"]) == (117, 118)
    assert (boston["undergrad_moe"], boston["graduate_moe"]) == (17, 18)
    assert boston["population_3plus"] == 101
    assert boston["period"] == "2015-2019"
