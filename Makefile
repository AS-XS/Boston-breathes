PYTHON := .venv/bin/python
RUN := PYTHONPATH=src $(PYTHON) -m

.PHONY: all analysis install data weeks bluebikes bluebikes-weekly municipalities weather universities town-gown boston-housing campus population calendars student-residents living-arrangements presence students-away mbta mbta-survey street-counts modeling-table evaluate models campus-panel population-model population-estimate scenarios forecast checks boston-311 presence-signals travel-models joint-population population-accuracy figures test clean-interim

# Everything, from the downloads to the figures.
all: data analysis figures

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

# Analysis of the processed tables (no downloads): evaluation, travel models, the population model, scenarios and forecast.
analysis: evaluate models campus-panel population-model population-estimate scenarios forecast travel-models joint-population population-accuracy

data: weeks bluebikes municipalities weather universities town-gown boston-housing campus population calendars student-residents living-arrangements presence students-away mbta mbta-survey street-counts modeling-table boston-311 presence-signals

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

# Where resident college students live: university housing, with parents, elsewhere
# (ACS PUMS and the 2020 Census; needs student-residents and municipalities).
living-arrangements:
	$(RUN) boston_breathes.living_arrangements

# Student Presence Index by week (needs universities, population, calendars, student-residents).
presence:
	$(RUN) boston_breathes.presence

# Range of resident students away each week (needs presence, living-arrangements, population).
students-away:
	$(RUN) boston_breathes.students_away

# MBTA gated station entries by station and week (needs campus).
mbta:
	$(RUN) boston_breathes.mbta

# MBTA passenger survey: student riders by station (needs mbta).
mbta-survey:
	$(RUN) boston_breathes.mbta_survey

# Boston street counts of bicycles and motor vehicles (needs campus).
street-counts:
	$(RUN) boston_breathes.traffic_counts

# One row per week for the models: travel records, weather, calendar and students
# (needs weather, campus, mbta, presence, students-away).
modeling-table:
	$(RUN) boston_breathes.modeling_table

# Check the Student Presence Index against Bluebikes, MBTA and street counts (after data).
evaluate:
	$(RUN) boston_breathes.evaluate_presence

# Goal 2: travel models with and without the Student Presence Index (needs modeling-table).
models:
	$(RUN) boston_breathes.model_comparison

# Pooled campus model: travel at each campus against its own university's calendar (after data).
campus-panel:
	$(RUN) boston_breathes.campus_panel

# Population model: the weekly population, with weekday and weekend subway travel as evidence (after data).
population-model:
	$(RUN) boston_breathes.population_model

# The weekly population estimate and its band from the subway and Bluebikes (needs presence-signals).
population-estimate:
	$(RUN) boston_breathes.population_estimate

# Scenarios: the 2025-26 population if one input were different (needs presence-signals).
scenarios:
	$(RUN) boston_breathes.population_scenarios

# Boston 311 requests from residents, by day and campus zone (downloads).
boston-311:
	$(RUN) boston_breathes.boston_311

# Weekly presence signals (subway, Bluebikes, 311) and features (needs bluebikes' station-day files in data/interim).
presence-signals:
	$(RUN) boston_breathes.presence_signals

# Eight model families and six feature sets on each signal, for later years (about 12 minutes).
travel-models:
	$(RUN) boston_breathes.travel_models

# Shares away fitted on several signals at once, weather removed by each family.
joint-population:
	$(RUN) boston_breathes.joint_population

# Accuracy test with a known truth, and the calibration of the intervals (about 14 minutes).
population-accuracy:
	$(RUN) boston_breathes.population_accuracy

# Forecast: the population model on the academic year 2026-27, with the published calendars.
forecast:
	$(RUN) boston_breathes.population_forecast

# Checks of the population model: COVID-19, size, and Bluebikes (needs bluebikes' station-day files in data/interim).
checks:
	$(RUN) boston_breathes.population_checks

# Figures as PNG images and interactive HTML pages in figures/ (after data).
figures:
	$(RUN) boston_breathes.figures

test:
	$(PYTHON) -m pytest -q

clean-interim:
	rm -rf data/interim
