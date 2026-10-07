PYTHON := .venv/bin/python
RUN := PYTHONPATH=src $(PYTHON) -m

.PHONY: install data weeks bluebikes bluebikes-weekly municipalities weather universities town-gown campus population test clean-interim

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

data: weeks bluebikes municipalities weather universities town-gown campus population

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

# Match stations to nearby university campuses (needs bluebikes and universities).
campus:
	$(RUN) boston_breathes.campus

# Census population estimates for the study-area municipalities.
population:
	$(RUN) boston_breathes.population

test:
	$(PYTHON) -m pytest -q

clean-interim:
	rm -rf data/interim
