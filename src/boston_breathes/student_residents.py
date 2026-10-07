"""College students living in each study-area municipality (Census ACS).

Source: American Community Survey 5-year estimates, table B14007 (school
enrollment by detailed level of school), by place of residence. It counts
residents enrolled in college, including those in dormitories, so it measures
how many students live in each municipality whichever school they attend.
Each estimate averages five years of surveys and is labelled by its last year.

The Census API now requires a key, so the bulk summary files are read
instead. They come in two layouts:
  * 2021 onward: one national file per table (GEO_ID | B14007_E001 | ...);
  * up to 2020: per-state "sequence" files, where a lookup table gives the
    sequence number and column position of each table.

Output: data/processed/student_residents_annual.csv, one row per
municipality and ACS 5-year period.
"""

import csv
import io
import zipfile
from datetime import date

import polars as pl

from boston_breathes import http
from boston_breathes.paths import PROCESSED, RAW, STUDY_START

BASE_URL = "https://www2.census.gov/programs-surveys/acs/summary_file/"
RAW_DIR = RAW / "acs"
TABLE = "B14007"
FIRST_TABLE_BASED_YEAR = 2021
FIRST_YEAR = STUDY_START.year

# Census GEOIDs of the study-area municipalities (county subdivisions).
GEOIDS = {
    "2502507000": "Boston",
    "2501711000": "Cambridge",
    "2501762535": "Somerville",
    "2502109175": "Brookline",
}
# Table lines (1-based) used.
LINES = {"population_3plus": 1, "undergrad": 17, "graduate": 18}


def get(url: str, timeout: int = 600) -> bytes | None:
    resp = http.get(url, timeout=timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content


def summarize(values: dict[str, dict[str, int | None]], year: int) -> pl.DataFrame:
    """values: municipality -> {column: value} for estimates (and *_moe)."""
    missing = set(GEOIDS.values()) - set(values)
    if missing:
        raise ValueError(f"ACS {year}: municipalities not found: {sorted(missing)}")
    rows = [{"municipality": m, "acs_year": year, "period": f"{year - 4}-{year}", **v} for m, v in values.items()]
    return pl.DataFrame(rows).with_columns(
        (pl.col("undergrad") + pl.col("graduate")).alias("college_students")
    ).select(
        "municipality", "acs_year", "period", "college_students", "undergrad", "undergrad_moe",
        "graduate", "graduate_moe", "population_3plus",
    )


# ------------------------------------------------------------ 2021 onward


def parse_table_based(text: str, year: int) -> pl.DataFrame:
    """Rows of the national table file for the study-area municipalities."""
    lines = iter(text.splitlines())
    header = next(lines).split("|")
    idx = {name: i for i, name in enumerate(header)}
    values = {}
    for line in lines:
        geo = line.split("|", 1)[0]
        if geo.startswith("0600000US") and geo[9:] in GEOIDS:
            f = line.split("|")
            def cell(kind: str, n: int) -> int | None:
                v = f[idx[f"{TABLE}_{kind}{n:03d}"]]
                return int(v) if v.lstrip("-").isdigit() and int(v) >= 0 else None
            values[GEOIDS[geo[9:]]] = {
                "population_3plus": cell("E", LINES["population_3plus"]),
                "undergrad": cell("E", LINES["undergrad"]),
                "undergrad_moe": cell("M", LINES["undergrad"]),
                "graduate": cell("E", LINES["graduate"]),
                "graduate_moe": cell("M", LINES["graduate"]),
            }
    return summarize(values, year)


def table_based(year: int) -> pl.DataFrame | None:
    url = f"{BASE_URL}{year}/table-based-SF/data/5YRData/acsdt5y{year}-{TABLE.lower()}.dat"
    data = get(url)
    return None if data is None else parse_table_based(data.decode("latin-1"), year)


# ------------------------------------------------------------- up to 2020


def table_position(lookup_csv: str) -> tuple[str, int]:
    """Sequence number and 1-based start field of TABLE in the lookup file."""
    for row in csv.DictReader(io.StringIO(lookup_csv)):
        if row["Table ID"].strip() == TABLE and row["Start Position"].strip():
            return row["Sequence Number"].strip().zfill(4), int(row["Start Position"])
    raise ValueError(f"{TABLE} not found in sequence lookup")


def logical_records(geo_csv: str) -> dict[str, str]:
    """LOGRECNO -> municipality, from the state geography file."""
    out = {}
    for row in csv.reader(io.StringIO(geo_csv)):
        for field in row:
            if field.startswith("06000US") and field[7:] in GEOIDS:
                out[row[4]] = GEOIDS[field[7:]]
    return out


def sequence_values(seq_txt: str, records: dict[str, str], start: int) -> dict[str, dict[str, int | None]]:
    out = {}
    for line in seq_txt.splitlines():
        f = line.split(",")
        if len(f) > 5 and f[5] in records:
            out[records[f[5]]] = {
                name: int(f[start - 1 + n - 1]) if f[start - 1 + n - 1].strip().lstrip("-").isdigit() else None
                for name, n in LINES.items()
            }
    return out


def parse_sequence_based(zip_bytes: bytes, lookup_csv: str, year: int) -> pl.DataFrame:
    seq, start = table_position(lookup_csv)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = {n.lower(): n for n in zf.namelist()}
        geo = zf.read(names[f"g{year}5ma.csv"]).decode("latin-1")
        est = zf.read(names[f"e{year}5ma{seq}000.txt"]).decode("latin-1")
        moe = zf.read(names[f"m{year}5ma{seq}000.txt"]).decode("latin-1")
    records = logical_records(geo)
    estimates, margins = sequence_values(est, records, start), sequence_values(moe, records, start)
    values = {
        m: {**v, "undergrad_moe": margins[m]["undergrad"], "graduate_moe": margins[m]["graduate"]}
        for m, v in estimates.items()
    }
    return summarize(values, year)


def sequence_based(year: int) -> pl.DataFrame | None:
    lookup = get(f"{BASE_URL}{year}/documentation/user_tools/ACS_5yr_Seq_Table_Number_Lookup.txt")
    if lookup is None:
        return None
    path = RAW_DIR / f"ma_5yr_{year}.zip"
    if not path.exists():
        data = get(f"{BASE_URL}{year}/data/5_year_by_state/Massachusetts_All_Geographies_Not_Tracts_Block_Groups.zip")
        if data is None:
            return None
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    df = parse_sequence_based(path.read_bytes(), lookup.decode("latin-1"), year)
    path.unlink()  # about 125 MB per year; the processed table is all that is kept
    return df


def main() -> None:
    frames = []
    for year in range(FIRST_YEAR, date.today().year + 1):
        df = table_based(year) if year >= FIRST_TABLE_BASED_YEAR else sequence_based(year)
        if df is None:
            print(f"  ACS {year} 5-year: not published")
            continue
        frames.append(df)
        print(f"  ACS {year - 4}-{year}: {df['college_students'].sum():,} college students living in the study area")
    out = pl.concat(frames).sort("municipality", "acs_year")
    PROCESSED.mkdir(parents=True, exist_ok=True)
    out.write_csv(PROCESSED / "student_residents_annual.csv")


if __name__ == "__main__":
    main()
