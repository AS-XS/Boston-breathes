from datetime import date

import pytest

from boston_breathes import traffic_counts as tc

HEADER = "count_id,location_name,month,season,year,date,weather,lat,long,day_bikes,day_veh,day_total,day_share,am_bike,am_veh,am_share,pm_bike,pm_veh,pm_share,month_id,notes,shape_wkt,POINT_X,POINT_Y"


def row(cid, name, d, bikes, veh):
    return f'{cid},{name},June,Summer,2021,{d},Sunny,42.35,-71.07,{bikes},{veh},0,0,10,{veh // 10 if veh else 0},0,12,{veh // 10 if veh else 0},0,6,,POINT (-71.07 42.35),-71.07,42.35'


def test_parse_dates_and_columns():
    text = "\n".join([HEADER, row(25, "Comm Ave", "9/27/2016 4:00:00.000", 583, 12904), row(6, "Beacon St", "6/9/2021 4:00:00.000", 292, 14474)])
    df = tc.parse(text)
    assert df["date"].to_list() == [date(2016, 9, 27), date(2021, 6, 9)]
    assert df["weekday"].to_list() == ["Tuesday", "Wednesday"]
    assert df.row(0, named=True)["bikes"] == 583 and df.row(0, named=True)["vehicles"] == 12904


def test_zero_vehicles_means_not_counted():
    df = tc.parse("\n".join([HEADER, row(7, "River path", "6/9/2021 4:00:00.000", 900, 0)]))
    r = df.row(0, named=True)
    assert r["bikes"] == 900
    assert r["vehicles"] is None and r["vehicles_am"] is None


def test_duplicates_and_bad_dates():
    dup = row(25, "Comm Ave", "9/27/2016 4:00:00.000", 583, 12904)
    assert len(tc.parse("\n".join([HEADER, dup, dup]))) == 1
    with pytest.raises(ValueError, match="unreadable dates"):
        tc.parse("\n".join([HEADER, row(1, "X", "not a date", 1, 1)]))
