PYTHON := .venv/bin/python
RUN := PYTHONPATH=src $(PYTHON) -m

.PHONY: install data weeks bluebikes bluebikes-weekly municipalities weather universities town-gown boston-housing campus population calendars student-residents presence mbta mbta-survey street-counts evaluate figures test clean-interim

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

data: weeks bluebikes municipalities weather universities town-gown boston-housing campus population calendars student-residents presence mbta mbta-survey street-counts

weeks:
	$(RUN) boston_breathes.weeks

# Download, clean and summarize every Bluebikes month (raw files are deleted after processing).
bluebikes:
	$(RUN) boston_breathes.bluebikes

# Rebuild the weekly table from already-processed months without downloading.
bluebikes-weekly:
	$(RUN) boston_breathes.bluebikes --weekly-only

# Assign stations to towns and split weekly activity by town.
municipalities:
	$(RUN) boston_breathes.municipalities

# Daily Boston Logan weather from NOAA, summarized by week.
weather:
	$(RUN) boston_breathes.weather

# Study-area universities and their fall enrollment from IPEDS.
universities:
	$(RUN) boston_breathes.universities

# Cambridge universities' annual Town Gown reports (enrollment and housing).
town-gown:
	$(RUN) boston_breathes.town_gown

# City of Boston student housing reports: students living in Boston by living arrangement.
boston-housing:
	$(RUN) boston_breathes.boston_housing

# Match stations to nearby university campuses (needs bluebikes and universities).
campus:
	$(RUN) boston_breathes.campus

# Census population estimates for the study-area municipalities.
population:
	$(RUN) boston_breathes.population

# Validate hand-collected academic calendars and compute weekly in-session days.
calendars:
	$(RUN) boston_breathes.calendars

# College students living in each study-area municipality (Census ACS).
student-residents:
	$(RUN) boston_breathes.student_residents

# Student Presence Index and effective population by week
# (needs universities, population, calendars, student-residents).
presence:
	$(RUN) boston_breathes.presence

# MBTA gated station entries by station and week (needs campus).
mbta:
	$(RUN) boston_breathes.mbta

# MBTA passenger survey: student riders by station (needs mbta).
mbta-survey:
	$(RUN) boston_breathes.mbta_survey

# Boston street counts of bicycles and motor vehicles (needs campus).
street-counts:
	$(RUN) boston_breathes.traffic_counts

# Check the Student Presence Index against Bluebikes, MBTA and street counts (after data).
evaluate:
	$(RUN) boston_breathes.evaluate_presence

# Figures as PNG images and interactive HTML pages in figures/ (after data).
figures:
	$(RUN) boston_breathes.figures

test:
	$(PYTHON) -m pytest -q

clean-interim:
	rm -rf data/interim
