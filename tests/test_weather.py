from datetime import date

import polars as pl
import pytest

from boston_breathes import weather

NOAA_CSV = """\
"STATION","DATE","AWND","PRCP","SNOW","TMAX","TMIN"
"USW00014739","2024-01-08","4.0","12.0","0.0","5.0","1.0"
"USW00014739","2024-01-09","6.0","0.5","15.0","-2.0","-8.0"
"USW00014739","2024-01-10","5.0","0.0","0.0","3.0","-3.0"
"USW00014739","2024-01-11","","2.0","0.0","","-1.0"
"USW00014739","2024-01-12","3.0","0.0","0.0","4.0","0.0"
"USW00014739","2024-01-13","2.0","0.0","0.0","6.0","2.0"
"USW00014739","2024-01-14","2.0","0.0","0.0","8.0","2.0"
"USW00014739","2024-01-16","2.0","0.0","0.0","31.0","20.0"
"""


def test_parse_daily():
    daily = weather.parse_daily(NOAA_CSV)
    assert daily.columns == ["date", "tmax_c", "tmin_c", "prcp_mm", "snow_mm", "wind_ms", "tmean_c"]
    assert daily["date"][0] == date(2024, 1, 8)
    assert daily["tmean_c"][0] == 3.0
    assert daily["tmax_c"][3] is None  # blank value, not zero
    assert daily["tmean_c"][3] is None


def test_parse_rejects_other_stations():
    other = NOAA_CSV.replace("USW00014739", "USW00094701", 1)
    with pytest.raises(ValueError):
        weather.parse_daily(other)


def test_missing_dates():
    assert weather.missing_dates(weather.parse_daily(NOAA_CSV)) == [date(2024, 1, 15)]


def test_build_weekly():
    weekly = weather.build_weekly(weather.parse_daily(NOAA_CSV))
    first = weekly.row(0, named=True)
    assert first["week_start"] == date(2024, 1, 8)
    assert first["days_with_data"] == 7 and first["complete_week"] is True
    assert first["prcp_total_mm"] == 14.5
    assert first["rain_days"] == 2
    assert first["heavy_rain_days"] == 1
    assert first["snow_total_mm"] == 15.0 and first["snow_days"] == 1
    assert first["freezing_days"] == 1
    assert first["hot_days"] == 0

    second = weekly.row(1, named=True)
    assert second["week_start"] == date(2024, 1, 15)
    assert second["days_with_data"] == 1 and second["complete_week"] is False
    assert second["hot_days"] == 1
