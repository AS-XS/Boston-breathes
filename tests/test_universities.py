import io
import zipfile

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import box

from boston_breathes import municipalities as mun
from boston_breathes import universities as uni

EF_CSV = """\
UNITID,EFALEVEL,LINE,SECTION,LSTUDY,XEFTOTLT,EFTOTLT
100,1,29,1,999,R,1000
100,2,1,1,1,R,600
100,12,1,1,1,R,400
100,21,1,1,1,R,800
100,22,1,1,1,R,550
100,32,1,1,1,R,250
100,41,1,1,1,R,200
200,1,29,1,999,R,300
200,2,1,1,1,R,300
200,21,1,1,1,R,300
"""

DIST_CSV = """\
UNITID,EFDELEV,XEFDETOT,EFDETOT,XEFDEEXC,EFDEEXC
100, 1,R,1000,R,150
100, 2,R,600,R,50
100,12,R,400,R,100
"""


def frame(csv: str) -> pl.DataFrame:
    return pl.read_csv(io.StringIO(csv), infer_schema=False).with_columns(pl.col(pl.Utf8).str.strip_chars())


def test_enrollment_pivots_levels_and_fills_missing_with_zero():
    ef = uni.enrollment(frame(EF_CSV), 2023).sort("unitid")
    first, second = ef.row(0, named=True), ef.row(1, named=True)
    assert (first["total"], first["undergrad"], first["graduate"]) == (1000, 600, 400)
    assert (first["full_time"], first["part_time"]) == (800, 200)
    assert first["fall_year"] == 2023
    assert (second["graduate"], second["part_time"]) == (0, 0)  # level not reported


def test_distance_only_and_in_person():
    ef = uni.enrollment(frame(EF_CSV), 2023)
    dist = uni.distance_only(frame(DIST_CSV))
    joined = ef.join(dist, on="unitid", how="left").with_columns(
        pl.col(*uni.DIST_LEVELS.values()).fill_null(0)
    )
    out = uni.with_in_person(joined).sort("unitid")
    assert out["in_person"].to_list() == [850, 300]
    assert out["in_person_undergrad"].to_list() == [550, 300]
    assert out["in_person_graduate"].to_list() == [300, 0]


def test_read_ipeds_csv_prefers_revised_file_and_normalizes_names():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ef2015a.csv", "UNITID,EFTOTLT\n1,10\n")
        zf.writestr("ef2015a_rv.csv", "﻿UNITID,EFTOTLT ,Name\n1,12,Caf\xe9\n".encode("cp1252", errors="ignore"))
    df = uni.read_ipeds_csv(buf.getvalue())
    assert df.columns == ["UNITID", "EFTOTLT", "NAME"]
    assert df["EFTOTLT"][0] == "12"


def test_latest_directory_keeps_newest_entry_and_span():
    dirs = pl.DataFrame({
        "unitid": ["1", "1", "2"],
        "name": ["Old Name", "New Name", "Closed College"],
        "fall_year": [2015, 2020, 2016],
    })
    latest = uni.latest_directory(dirs).sort("unitid")
    assert latest["name"].to_list() == ["New Name", "Closed College"]
    assert latest["first_fall"].to_list() == [2015, 2016]
    assert latest["last_fall"].to_list() == [2020, 2016]


@pytest.fixture
def towns() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"municipality": ["Boston", "Newton"]},
        geometry=[box(235000, 900000, 236000, 901000), box(233000, 900000, 235000, 901000)],
        crs=mun.MA_CRS,
    )


def point(x: float, y: float) -> tuple[float, float]:
    pt = gpd.GeoSeries.from_xy([x], [y], crs=mun.MA_CRS).to_crs("EPSG:4326")[0]
    return pt.y, pt.x


def test_select_institutions(towns, monkeypatch):
    monkeypatch.setattr(uni, "NEARBY_INSTITUTIONS", {"3": "Nearby University"})
    boston, newton = point(235500, 900500), point(234000, 900500)
    latest = pl.DataFrame({
        "unitid": ["1", "2", "3", "4", "5"],
        "name": ["City University", "Admin Office", "Nearby University", "Suburban College", "Barber School"],
        "city": ["Boston"] * 5,
        "sector": [2, 0, 2, 2, 9],
        "degree_granting": [1, 2, 1, 1, 2],
        "lat": [boston[0], boston[0], newton[0], newton[0], boston[0]],
        "lng": [boston[1], boston[1], newton[1], newton[1], boston[1]],
        "first_fall": [2015] * 5,
        "last_fall": [2023] * 5,
    })
    selected = uni.select_institutions(latest, towns)
    assert selected["unitid"].to_list() == ["1", "3"]
    assert selected["in_study_area"].to_list() == [True, False]
    assert selected["municipality"].to_list() == ["Boston", "Newton"]


def test_select_institutions_requires_nearby_ones(towns, monkeypatch):
    monkeypatch.setattr(uni, "NEARBY_INSTITUTIONS", {"999": "Missing University"})
    latest = pl.DataFrame({
        "unitid": ["1"], "name": ["City University"], "city": ["Boston"], "sector": [2],
        "degree_granting": [1], "lat": [point(235500, 900500)[0]], "lng": [point(235500, 900500)[1]],
        "first_fall": [2015], "last_fall": [2023],
    })
    with pytest.raises(ValueError, match="Missing University"):
        uni.select_institutions(latest, towns)
