"""Project paths and study period shared by every pipeline step."""

from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
INTERIM = DATA / "interim"
PROCESSED = DATA / "processed"

# Bluebikes monthly trip files are available from January 2015 onward.
STUDY_START = date(2015, 1, 1)
STUDY_END = date(2026, 12, 31)

# Municipalities whose residents and students define "Greater Boston": the Inner
# Core subregion of the Metropolitan Area Planning Council (MAPC), the region's
# planning agency. Keyed by Census county subdivision GEOID (state, county and
# subdivision codes), with names as in the Census town boundaries. The first
# four were the original study area.
STUDY_AREA_GEOIDS = {
    "2502507000": "Boston",
    "2501711000": "Cambridge",
    "2501762535": "Somerville",
    "2502109175": "Brookline",
    "2501701605": "Arlington",
    "2501705070": "Belmont",
    "2502513205": "Chelsea",
    "2502116495": "Dedham",
    "2501721990": "Everett",
    "2500937490": "Lynn",
    "2501737875": "Malden",
    "2501739835": "Medford",
    "2501740115": "Melrose",
    "2502141690": "Milton",
    "2502144105": "Needham",
    "2501745560": "Newton",
    "2502155745": "Quincy",
    "2502556585": "Revere",
    "2500960015": "Saugus",
    "2501772600": "Waltham",
    "2501773440": "Watertown Town",
    "2502581005": "Winthrop Town",
}
STUDY_AREA = tuple(STUDY_AREA_GEOIDS.values())
# Every Census GEOID of a study-area town: Watertown Town city became Watertown
# city, with a new code, in Census data from 2024 on (the 2020-2025 population
# estimates and the 2020-2024 ACS).
CENSUS_GEOIDS = {**STUDY_AREA_GEOIDS, "2501773405": "Watertown Town"}
