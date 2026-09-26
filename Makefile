PYTHON := .venv/bin/python
RUN := PYTHONPATH=src $(PYTHON) -m

.PHONY: install data weeks bluebikes bluebikes-weekly municipalities weather test clean-interim

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt

data: weeks bluebikes municipalities weather

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

test:
	$(PYTHON) -m pytest -q

clean-interim:
	rm -rf data/interim
