from datetime import date

import geopandas as gpd
import polars as pl
import pytest
from shapely.geometry import box

from boston_breathes import bluebikes as bb
from boston_breathes import municipalities as mun

# Two adjacent 1 km squares in Massachusetts State Plane meters:
# "Alpha" to the west, "Beta" to the east, sharing the line x = 236000.
ALPHA = box(235000, 900000, 236000, 901000)
BETA = box(236000, 900000, 237000, 901000)


@pytest.fixture
def towns() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"municipality": ["Beta", "Alpha"]}, geometry=[BETA, ALPHA], crs=mun.MA_CRS)


def lat_lng(x: float, y: float) -> tuple[float, float]:
    pt = gpd.GeoSeries.from_xy([x], [y], crs=mun.MA_CRS).to_crs("EPSG:4326")[0]
    return pt.y, pt.x


def points(*xy: tuple[float, float]) -> pl.DataFrame:
    coords = [lat_lng(x, y) if (x, y) != (0, 0) else (0.0, 0.0) for x, y in xy]
    return pl.DataFrame({"lat": [c[0] for c in coords], "lng": [c[1] for c in coords]})


def test_assign_inside_each_town(towns):
    out = mun.assign(points((235500, 900500), (236800, 900500)), towns)
    assert out["municipality"].to_list() == ["Alpha", "Beta"]
    assert out["border_distance_m"].to_list() == [500.0, 800.0]
    assert out["nearest_other_municipality"].to_list() == ["Beta", "Alpha"]


def test_point_on_border_goes_to_first_town_alphabetically(towns):
    out = mun.assign(points((236000, 900500)), towns)
    assert out["municipality"][0] == "Alpha"
    assert out["border_distance_m"][0] == 0.0


def test_point_just_outside_is_snapped(towns):
    out = mun.assign(points((234950, 900500)), towns)  # 50 m west of Alpha
    assert out["municipality"][0] == "Alpha"


def test_far_and_missing_points_are_unknown(towns):
    out = mun.assign(points((230000, 900500), (0, 0)), towns)
    assert out["municipality"].to_list() == [mun.UNKNOWN, mun.UNKNOWN]
    assert out["border_distance_m"].null_count() == 2


def test_no_neighbour_within_search_radius(towns):
    only_alpha = towns[towns["municipality"] == "Alpha"]
    out = mun.assign(points((235500, 900500)), only_alpha)
    assert out["municipality"][0] == "Alpha"
    assert out["nearest_other_municipality"][0] is None


def test_tag_station_day_uses_monthly_town_then_fallback():
    counts = {c: [1, 1, 1, 1] for c in bb.COUNT_COLUMNS}
    sd = pl.DataFrame({
        "date": [date(2019, 1, 5), date(2019, 2, 5), date(2019, 3, 5), date(2019, 3, 5)],
        "station_id": ["9", "9", "9", None],
        **counts,
    })
    monthly = pl.DataFrame({
        "station_id": ["9", "9"],
        "month": ["201901", "201902"],
        "municipality": ["Brookline", "Boston"],
    })
    overall = pl.DataFrame({"station_id": ["9"], "municipality": ["Boston"]})
    tagged = mun.tag_station_day(sd, monthly, overall)
    assert tagged["municipality"].to_list() == ["Brookline", "Boston", "Boston", mun.UNKNOWN]
