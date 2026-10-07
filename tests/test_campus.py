from datetime import date

import geopandas as gpd
import polars as pl
import pytest

from boston_breathes import bluebikes as bb
from boston_breathes import campus
from boston_breathes import municipalities as mun

BOSTON_CSV = """\
SchoolId,Name,POINT_X,POINT_Y,NumStudents13
164988,Boston University,-71.0997,42.3496,32411
0,Boston University School of Medicine,-71.0710,42.3364,0
0,Boston University Rental Office,-71.0970,42.3495,0
999999,Beauty School,-71.0900,42.3500,50
167358,Northeastern University,0,0,8479
"""


def test_boston_campus_points(monkeypatch):
    monkeypatch.setattr(campus, "SECONDARY_CAMPUSES", {"Boston University School of Medicine": "164988"})
    points = campus.boston_campus_points(BOSTON_CSV, {"164988", "167358"})
    # Unlisted offices, unselected schools and missing coordinates are dropped.
    assert points["campus_name"].to_list() == ["Boston University", "Boston University School of Medicine"]
    assert points["unitid"].to_list() == ["164988", "164988"]


def test_boston_campus_points_requires_secondary_campuses(monkeypatch):
    monkeypatch.setattr(campus, "SECONDARY_CAMPUSES", {"Harvard Medical School": "166027"})
    with pytest.raises(ValueError, match="Harvard Medical School"):
        campus.boston_campus_points(BOSTON_CSV, {"164988"})


def test_campus_points_flags_major_institutions():
    universities = pl.DataFrame({
        "unitid": ["1", "2"], "name": ["Big U", "Small College"], "lat": [42.35, 42.34], "lng": [-71.1, -71.09],
    })
    enrollment = pl.DataFrame({"unitid": ["1", "1", "1", "2"], "in_person": [30000, 500, 31000, 400]})
    boston = pl.DataFrame({
        "unitid": ["1", "3"], "campus_name": ["Big U Medical", "Unselected"], "lat": [42.33, 42.3],
        "lng": [-71.07, -71.0], "source": ["boston_open_data", "boston_open_data"],
    })
    points = campus.campus_points(universities, enrollment, boston)
    assert points["campus_name"].to_list() == ["Big U Medical", "Big U", "Small College"]
    assert points["major"].to_list() == [True, True, False]  # median, not the COVID low
    assert points["typical_in_person"].to_list() == [30000, 30000, 400]


def lat_lng(x: float, y: float) -> tuple[float, float]:
    pt = gpd.GeoSeries.from_xy([x], [y], crs=mun.MA_CRS).to_crs("EPSG:4326")[0]
    return pt.y, pt.x


def test_nearest_campus_zones():
    big, small = lat_lng(236000, 900000), lat_lng(236000, 900600)
    campuses = pl.DataFrame({
        "unitid": ["1", "2"], "institution": ["Big U", "Small College"],
        "lat": [big[0], small[0]], "lng": [big[1], small[1]], "major": [True, False],
    })
    stations = [lat_lng(236100, 900000), lat_lng(236000, 900700), lat_lng(238000, 900000), (0.0, 0.0)]
    points = pl.DataFrame({"lat": [s[0] for s in stations], "lng": [s[1] for s in stations]})
    out = campus.nearest_campus(points, campuses)
    assert out["campus_zone"].to_list() == ["campus", "near", "away", mun.UNKNOWN]
    # The small college is closer to the second station but is not a major campus.
    assert out["campus_institution"].to_list() == ["Big U", "Big U", "Big U", None]
    assert out["campus_distance_m"].to_list()[:3] == [100.0, 700.0, 2000.0]


def test_zone_thresholds():
    df = pl.DataFrame({"d": [0.0, 400.0, 400.5, 1000.0, 1000.5, None]})
    zones = df.select(campus.zone_of(pl.col("d")).alias("zone"))["zone"].to_list()
    assert zones == ["campus", "campus", "near", "near", "away", mun.UNKNOWN]


def test_tag_station_day_with_several_columns():
    counts = {c: [1, 1] for c in bb.COUNT_COLUMNS}
    sd = pl.DataFrame({"date": [date(2019, 1, 5), date(2019, 1, 5)], "station_id": ["9", None], **counts})
    monthly = pl.DataFrame({
        "station_id": ["9"], "month": ["201901"], "municipality": ["Boston"],
        "campus_zone": ["campus"], "campus_distance_m": [120.0],
    })
    overall = monthly.drop("month")
    tagged = mun.tag_station_day(
        sd, monthly, overall, columns={"municipality": mun.UNKNOWN, "campus_zone": mun.UNKNOWN, "campus_distance_m": None}
    )
    assert tagged["campus_zone"].to_list() == ["campus", mun.UNKNOWN]
    assert tagged["campus_distance_m"].to_list() == [120.0, None]
