# Boston Breathes: Measuring Greater Boston's Seasonal Population

## Project Description

Greater Boston has an unusually large university population, and the number of people physically present in the area changes noticeably throughout the academic year. Students arrive at the beginning of each semester, leave during winter and summer breaks, and temporarily leave during shorter academic breaks.

In this project, **Greater Boston** refers to the municipalities served by the Bluebikes bike-share system. The core study area is **Boston, Cambridge, Somerville, and Brookline**, which together contain most of the region's major universities, including Boston University, Northeastern, Harvard, MIT, and Tufts. Other municipalities in the Bluebikes network may be added after evaluating their data coverage.

The key question here would be:

> **How does Greater Boston's effective population change throughout the year, and how much of that variation can be explained by university students?**

The project will estimate Greater Boston's population over time by combining a relatively stable **resident population baseline** with a **seasonal student population component**.

Rather than simply adding university enrollment to Census population, which would double-count some students already counted as residents of the study area, the project will estimate how student presence changes relative to its typical annual level.

A simplified version of the model is:

**P<sub>effective</sub>(t) = P<sub>baseline</sub>(y) + ΔP<sub>students</sub>(t)**

where P<sub>baseline</sub>(y) is the combined annual resident population of the study-area municipalities and ΔP<sub>students</sub>(t) represents the seasonal change in student presence during a particular week or month.


The final goal is to create an interactive visualization that allows users to move through a year and watch Greater Boston's estimated population rise and fall with the academic calendar.

---

## Goals

The project has three main goals.

### 1. Estimate Greater Boston's seasonal population

Construct a weekly or monthly estimate of Greater Boston's effective population for approximately the last 10–12 years.

This will combine:

* annual resident population of each study-area municipality;
* university enrollment;
* the number or proportion of students living in the study area;
* academic calendars and semester breaks.


### 2. Test whether the estimated population reflects real city activity

City activity will be measured with several independent signals rather than Bluebikes alone:

* Bluebikes ridership (main signal);
* MBTA subway station entries;
* road traffic counts and rideshare trips, where data is available at a weekly or monthly level.

The project will test whether student presence helps explain changes in these activity measures after accounting for factors such as:

* weather;
* season;
* year;
* holidays;
* changes in service, such as the size of the Bluebikes network.

None of these sources identify riders as students, so student activity will be separated from resident activity in three ways:

* comparing activity near university campuses with activity elsewhere;
* for 2015–2022 Bluebikes trips, which include rider birth year, comparing riders of typical college age with older riders;
* checking whether changes line up with academic calendar dates, which differ from year to year and from general seasonal patterns.

A baseline model will be compared with a model that includes the estimated student population.

### 3. Build an interactive "Boston Breathes" visualization

The final visualization will allow users to select a year and move through the weeks or months of that year. Or view the yearly trend

It will show:

* Greater Boston's resident population baseline;
* estimated seasonal student contribution;
* estimated effective population;
* city activity measures, such as Bluebikes and MBTA ridership;
* major academic periods such as semesters, summer break, winter break, and national holiday.

This will allow the user to visually explore how Greater Boston "breathes" throughout the year.

---

## Data Sources and Collection

### Resident Population

Annual population estimates for each study-area municipality will be collected from the **U.S. Census Bureau** or **American Community Survey** and combined into a single study-area total.

These values will provide the relatively stable population baseline for each year.

### University Enrollment and Housing

Enrollment and student housing data will be collected from sources such as:

* City of Boston Student Housing / University Accountability reports;
* City of Cambridge Town Gown reports;
* IPEDS enrollment data.

These datasets contain information about university enrollment and, in some cases, where students live.

### Academic Calendars

Historical academic calendars will be collected from major universities in the study area.

Important dates include:

* semester start and end dates;
* winter break;
* spring break;
* Thanksgiving break;
* summer period.

The project may initially focus on the largest universities in the study area and expand if time permits.

### Bluebikes

Historical Bluebikes trip data will be used to measure observable city activity.

Trips will be aggregated by week, and variables such as total trips, trips per active station, and activity near major university areas may be examined.

Each station will be assigned to a municipality based on its location, so activity can be analyzed both for the whole study area and for each municipality.

For 2015–2022, trip records include rider birth year, which allows trips by riders of typical college age to be compared with trips by other riders.

### MBTA Ridership

Gated station entries from the **MBTA / MassDOT open data portal** will be used as a second activity signal, with particular attention to stations serving major universities, such as Kenmore, Harvard, and Kendall/MIT.

### Traffic and Rideshare

Road traffic counts from **MassDOT** and rideshare trip data published by the **Commonwealth of Massachusetts** will be explored as additional signals. They will be used only if they are available at a weekly or monthly level for the study area.

### Weather

Historical weather data for the Boston area will be used to control for variables such as temperature, rain, and snow, which strongly affect bicycle use and travel in general.

---

## Data Cleaning and Feature Extraction

The main cleaning challenges will include:

* standardizing university names across datasets and years;
* handling missing enrollment or housing data;
* matching different datasets to a common weekly or monthly timeline;
* accounting for changes in Bluebikes stations over time;
* assigning Bluebikes stations to municipalities and handling municipalities that joined the network during the study period;
* matching Bluebikes stations and MBTA stations to nearby university campuses;
* identifying unusual periods such as COVID-19.

The main derived feature will be a **Student Presence Index**, based on enrollment, student residence, and the academic calendar.

Additional features may include:

* semester-in-session indicators;
* summer and winter break indicators;
* weather variables;
* Bluebikes network size;
* distance from stations to university campuses;
* share of Bluebikes trips by riders of typical college age (2015–2022);
* year and season.

---

## Modeling and Evaluation

The main modeling question is:

> **Does information about student presence improve our ability to explain or predict changes in Greater Boston activity?**

An initial regression model will predict weekly city activity (Bluebikes trips and MBTA station entries) using weather, seasonal, and time-based variables.

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

* a historical estimate of Greater Boston's seasonal population;
* a Student Presence Index;
* an analysis of whether student presence corresponds to observable changes in city activity;
* an interactive visualization showing Greater Boston's annual population "pulse";
* a reproducible GitHub repository containing the full data-processing and analysis pipeline.
