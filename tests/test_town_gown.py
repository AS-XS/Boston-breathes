import pytest

from boston_breathes import town_gown

HEADER = ",".join(["Staff", *town_gown.COLUMNS, "Fiscal Year"])


def row(year: int, value: str = "1,234") -> str:
    values = [str(year) if col == "Reporting Year" else f'"{value}"' for col in town_gown.COLUMNS]
    return ",".join(["10", *values, f"FY {year}"])


def test_parse_maps_report_year_to_previous_fall():
    df = town_gown.parse("\n".join([HEADER, row(2025), row(2024)]))
    assert df["report_year"].to_list() == [2024, 2025]
    assert df["fall_year"].to_list() == [2023, 2024]
    assert df["mit_students"].to_list() == [1234, 1234]  # thousands separators removed


def test_parse_keeps_blank_values_as_missing():
    df = town_gown.parse("\n".join([HEADER, row(2025, value="")]))
    assert df["harvard_students"][0] is None


def test_parse_rejects_missing_columns_and_duplicates():
    with pytest.raises(ValueError, match="missing columns"):
        town_gown.parse("Reporting Year,MIT Students\n2025,1\n")
    with pytest.raises(ValueError, match="Duplicate"):
        town_gown.parse("\n".join([HEADER, row(2025), row(2025)]))
