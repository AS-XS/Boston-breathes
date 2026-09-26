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

# Municipalities whose residents and students define "Greater Boston".
STUDY_AREA = ("Boston", "Cambridge", "Somerville", "Brookline")
