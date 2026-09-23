# Boston Breathes: Measuring Boston's Seasonal Population

## Project Description

Boston has an unusually large university population, and the number of people physically present in the city changes noticeably throughout the academic year. Students arrive at the beginning of each semester, leave during winter and summer breaks, and temporarily leave during shorter academic breaks.

The key question here would be:

> **How does Boston's effective population change throughout the year, and how much of that variation can be explained by university students?**

The project will estimate Boston's population over time by combining a relatively stable **resident population baseline** with a **seasonal student population component**.

Rather than simply adding university enrollment to Census population, which would double-count some students already considered Boston residents, the project will estimate how student presence changes relative to its typical annual level.

A simplified version of the model is:

$$
P_{\text{effective}}(t)
=
P_{\text{baseline}}(y)
+
\Delta P_{\text{students}}(t)
$$

where $$(P_{\text{baseline}}(y))$$ is Boston's annual resident population and $$(\Delta P_{\text{students}}(t))$$ represents the seasonal change in student presence during a particular week or month.

The final goal is to create an interactive visualization that allows users to move through a year and watch Boston's estimated population rise and fall with the academic calendar.

---

## Goals

The project has three main goals.

### 1. Estimate Boston's seasonal population

Construct a weekly or monthly estimate of Boston's effective population for approximately the last 10–12 years.

This will combine:

* annual Boston resident population;
* university enrollment;
* the number or proportion of students living in Boston;
* academic calendars and semester breaks.


### 2. Test whether the estimated population reflects real city activity

Bluebikes ridership will be used as the main external activity signal.

The project will test whether student presence helps explain changes in ridership after accounting for factors such as:

* weather;
* season;
* year;
* changes in the size of the Bluebikes network.

A baseline model will be compared with a model that includes the estimated student population.

### 3. Build an interactive "Boston Breathes" visualization

The final visualization will allow users to select a year and move through the weeks or months of that year. Or view the yearly trend

It will show:

* Boston's resident population baseline;
* estimated seasonal student contribution;
* estimated effective population;
* Bluebikes activity;
* major academic periods such as semesters, summer break, winter break, and national holiday.

This will allow the user to visually explore how Boston "breathes" throughout the year.

---

## Data Sources and Collection

### Boston Resident Population

Annual Boston population estimates will be collected from the **U.S. Census Bureau** or **American Community Survey**.

These values will provide the relatively stable population baseline for each year.

### University Enrollment and Housing

Enrollment and student housing data will be collected from sources such as:

* City of Boston Student Housing / University Accountability reports;
* IPEDS enrollment data.

These datasets contain information about university enrollment and, in some cases, where students live.

### Academic Calendars

Historical academic calendars will be collected from major Boston universities.

Important dates include:

* semester start and end dates;
* winter break;
* spring break;
* Thanksgiving break;
* summer period.

The project may initially focus on the largest Boston universities and expand if time permits.

### Bluebikes

Historical Bluebikes trip data will be used to measure observable city activity.

Trips will be aggregated by week, and variables such as total trips, trips per active station, and activity near major university areas may be examined.

### Weather

Historical weather data for Boston will be used to control for variables such as temperature, rain, and snow, which strongly affect bicycle usage.

---

## Data Cleaning and Feature Extraction

The main cleaning challenges will include:

* standardizing university names across datasets and years;
* handling missing enrollment or housing data;
* matching different datasets to a common weekly or monthly timeline;
* accounting for changes in Bluebikes stations over time;
* identifying unusual periods such as COVID-19.

The main derived feature will be a **Student Presence Index**, based on enrollment, student residence, and the academic calendar.

Additional features may include:

* semester-in-session indicators;
* summer and winter break indicators;
* weather variables;
* Bluebikes network size;
* year and season.

---

## Modeling and Evaluation

The main modeling question is:

> **Does information about student presence improve our ability to explain or predict changes in Boston activity?**

An initial regression model will predict weekly Bluebikes activity using weather, seasonal, and time-based variables.

A second model will add the Student Presence Index.

The two models will be compared using metrics such as:

* Mean Absolute Error;
* Root Mean Squared Error;
* \(R^2\).

A second modeling approach, such as a tree-based model, may also be explored.

Because the data are time-based, training and testing will use chronological rather than random splits.

---

## Visualization

The main visualization will be an interactive timeline.

Users will be able to select a year and move through the year to see how estimated population and activity change.

Additional visualizations may include:

* seasonal population curves;
* student presence versus Bluebikes activity;
* comparisons between summer and academic semesters;
* comparisons between normal years and the COVID-19 period;
* maps of Bluebikes activity near university-heavy areas.

---

## Timeline

**Weeks 1–2:** Collect and clean resident population, university enrollment, and student housing data.

**Weeks 3–4:** Collect academic calendars, Bluebikes, and weather data; combine datasets into a common timeline.

**Week 5:** Build and evaluate the Student Presence Index and seasonal population estimate.

**Week 6:** Train and compare predictive models.

**Week 7:** Build the interactive visualization.

**Week 8:** Finalize analysis, documentation, tests, Makefile, GitHub workflow, README, and presentation.

---

## Expected Outcome

The final project will produce:

* a historical estimate of Boston's seasonal population;
* a Student Presence Index;
* an analysis of whether student presence corresponds to observable changes in city activity;
* an interactive visualization showing Boston's annual population "pulse";
* a reproducible GitHub repository containing the full data-processing and analysis pipeline.
