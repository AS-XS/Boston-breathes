# Boston Breathes: Technical Notes

The [README](../README.md) summarizes the project, its models and results. This file keeps the details: how each source was processed, the decisions taken on the way, and each model and check in full. Each module in `src/boston_breathes/` also describes its method in its docstring. Numbers are those of the committed tables in `data/processed/`.

**Study area.** MAPC's subregion pages list the 22 Inner Core members (checked October 2026); its older work plans and agendas list 21, without Dedham, which has no university, Bluebikes station, or gated MBTA station. Bluebikes has served Boston, Cambridge, Somerville and Brookline since 2015, and the other Inner Core towns it reaches only since 2019–2022.

## Data Cleaning and Feature Extraction

The main cleaning steps (details under [Processing decisions](#processing-decisions)): standardizing university names across datasets and years; filling missing enrollment and housing years; putting every source on one Monday-to-Sunday weekly timeline; following Bluebikes stations as the network grew and assigning them to municipalities and campus zones; finding MBTA closure days; and flagging the COVID-19 period.

The main derived feature is the **Student Presence Index**, from enrollment and the academic calendars. Other features: the shares of students on summer break and otherwise out of session, the range of students away (from where students live and residence hall closures), weather, holidays, network size, campus zones, the share of Bluebikes trips by riders of college age (2015 – April 2020), year and season.

---

## Data Processing

Every source is processed onto a common **Monday-to-Sunday weekly timeline** covering 2015–2026. Raw downloads are not stored in the repository; each step rebuilds them from the original source, and only small processed tables are kept in `data/processed/`.

| Step | Source | Status | Processed output |
|---|---|---|---|
| 1. Weekly timeline | Calendar with U.S. federal and Massachusetts holidays | Done | `weeks.csv` |
| 2. Bluebikes trips | Bluebikes monthly trip files, January 2015 onward | Done | `bluebikes_weekly.csv`, `bluebikes_stations.csv`, `bluebikes_monthly_qa.csv` |
| 3. Station municipalities | U.S. Census town boundaries (county subdivisions) | Done | `bluebikes_station_municipalities.csv`, `bluebikes_weekly_by_municipality.csv` |
| 4. Weather | NOAA daily observations, Boston Logan Airport | Done | `weather_daily.csv`, `weather_weekly.csv` |
| 5. Universities and campus locations | IPEDS institution directory | Done | `universities.csv` |
| 6. Enrollment | IPEDS fall enrollment, distance education, and university housing capacity; City of Cambridge Town Gown reports | Done | `enrollment_annual.csv`, `cambridge_town_gown.csv` |
| 7. Bluebikes rider age | Bluebikes trip files, January 2015 – April 2020 | Done | `college_age_trips` and `college_age_share` in the weekly Bluebikes tables |
| 8. Campus-area activity | Bluebikes stations matched to campus locations from IPEDS and City of Boston open data | Done | `campus_points.csv`, `bluebikes_station_campus.csv`, `bluebikes_weekly_by_campus_zone.csv`, `bluebikes_weekly_by_institution.csv` |
| 9. Resident population | U.S. Census population estimates (2010–2020 intercensal and latest vintage) | Done | `population_annual.csv`, `population_weekly.csv` |
| 10. Academic calendars | Official university calendars, collected by hand (`data/manual/academic_calendar.csv`) | Done | `academic_calendar.csv`, `academic_weekly.csv` |
| 11. Student housing | Census American Community Survey (college enrollment by place of residence) and its microdata (PUMS: where students live); 2020 Census (college housing by town); City of Cambridge Town Gown reports; City of Boston student housing reports; residence hall break policies, collected by hand (`data/manual/residence_hall_breaks.csv`) | Done | `student_residents_annual.csv`, `student_living_arrangements.csv`, `census_college_housing.csv`, `cambridge_town_gown.csv`, `boston_student_housing.csv` |
| 12. MBTA ridership | MBTA / MassDOT gated station entries (2014 onward); station locations from the MBTA API; MBTA 2025 System-Wide Passenger Survey (student riders by station) | Done | `mbta_stations.csv`, `mbta_weekly_by_station.csv`, `mbta_weekly_by_campus_zone.csv`, `mbta_survey_groups.csv`, `mbta_survey_by_station.csv` |
| 13. Street counts | City of Boston street counts of bicycles and motor vehicles (rideshare and statewide traffic counts not used: neither is published weekly or monthly for the study area) | Done | `boston_street_counts.csv` |
| 14. Student Presence Index | Combination of steps 5–11 | Done | `student_presence_weekly.csv`, `student_presence_by_institution.csv`, `presence_evaluation.csv` |
| 15. Students away: weekly range | Steps 11 and 14 | Done | `students_away_weekly.csv` |
| 16. Figures and year explorer | Steps 1–15 | Done | `figures/*.png`, `figures/*.html` (see [Figures](../README.md#figures)) |
| 17. Modeling table | Weekly tables of steps 1–15 | Done | `modeling_weekly.csv`: one row per week with travel records, weather, calendar, and student columns |
| 18. Model comparison (goal 2) | Step 17 | Done | `model_comparison.csv`: held-out error of three model families with and without the index, and the index's effect; `model_coefficients.csv` (with the combined effect of warming from 0 to 20 °C), `model_feature_importance.csv`: what each input does (see [Travel Models in Detail](#travel-models-in-detail)) |
| 19. Pooled campus model | Steps 8, 10, 12, and 14 | Done | `campus_panel.csv`: travel at each campus against its own university's calendar (all universities with assumed calendars, and verified calendars only), by university, with a placebo |
| 20. Population model | Steps 12, 14, and 15 | Done | `population_model_forward.csv`, `population_model_comparison.csv`, `population_model_shares.csv`: versions of who leaves scored on later years (validation 2023–2025, test 2026) and on months within a period, and the fitted shares of students out of session who leave |
| 21. Scenarios | Step 29 | Done | `population_scenarios.csv`, `population_scenarios_summary.csv`: the 2025–26 population with 10% fewer or more students, aligned spring breaks, or a remote fall term |
| 22. Checks | Steps 2, 20 | Done | `population_checks.csv`, `population_checks_size.csv`, `population_checks_bluebikes.csv`: the COVID-19 natural experiment, the size of the absence in years outside the fit, and the model fitted on Bluebikes |
| 23. Forecast | Steps 10, 14, 15, and 29 | Done | `population_forecast.csv`, `population_forecast_inputs.csv`: the weekly population of the academic year 2026–27 from the published calendars, with its 95% band, and how much the held inputs used to change each year |
| 24. Boston 311 requests | City of Boston 311 service requests (2015 onward) | Done | `boston_311_daily.csv`: resident requests per day and campus zone |
| 25. Presence signals | Steps 2, 4, 12, 14, 15, and 24 | Done | `presence_signals_weekly.csv`: the weekly subway, Bluebikes and 311 signals on weekdays and weekends, with student, weather and calendar features |
| 26. Model families | Step 25 | Done | `travel_model_accuracy.csv`: eight families of models and six feature sets predicting each signal for later years |
| 27. Joint population model | Step 25 | Done | `joint_population_shares.csv`: the shares away fitted on several signals at once, with weather removed by each family |
| 28. Accuracy test | Steps 25 and 27 | Done | `population_accuracy.csv`, `population_accuracy_calibration.csv`: the method's error in students away, and its intervals' coverage, on signals made from a known truth; the run length and widening that make the 95% intervals contain the truth 95% of the time |
| 29. Population estimate | Steps 20, 27, 28, and 30 | Done | `population_estimate.csv`: the weekly population from the subway and Bluebikes shares of step 27, corrected with the weekend factor of step 30, with a 95% band calibrated in step 28 |
| 30. Weekend riders | Bluebikes trip files (2016–2019), steps 14, 25, and 27 | Done | `bluebikes_member_ages_weekly.csv`, `weekend_riders_shares.csv`, `weekend_riders.csv`, `weekend_factor_draws.csv`: member trips by age band, the share of trips by riders aged 18–24 on weekdays and weekends by zone, how much larger students' share of weekend riders is (with its refits), and the shares away corrected for it |

### Processing decisions

#### Bluebikes and weather

* **Bluebikes file formats.** The trip files changed layout and station ID format in 2023; both layouts are converted to the same columns, and stations are matched across the change by location.
* **Trip cleaning.** Trips shorter than 60 seconds or longer than 24 hours, trips with invalid times, and trips at warehouse, depot, and test stations are removed, using the same rules for every year.
* **Month boundaries.** Some monthly files repeat trips from the previous month; repeated trips are counted once.
* **Network size.** Measured as the average number of stations with at least one trip per day, which accounts for winter closures and network growth.
* **Electric bikes.** Trips are split by bike type, since electric bikes (introduced in 2023) changed ridership.
* **Municipalities.** Stations are assigned to a town month by month, because some stations moved over time.
* **Rider age.** Riders aged 18–24 are counted as college age. Birth year 1969 is the system default for riders who gave no birth year, so it is treated as unknown, as are ages below 16 or above 90.
* **Weather.** Logan Airport does not report daily average temperature, so it is taken as the midpoint of the daily maximum and minimum. Weekly "ice days" are days that stay below freezing all day.

#### MBTA, street counts, and campus zones

* **MBTA entries.** Gated entries count fare-gate taps at subway, Silver Line, and light-rail stations; they miss fare evasion and free riders, so they track change rather than total ridership.
  * Street-level Green Line stops, including those at Boston University and Northeastern, have no fare gates and are not included.
  * Missing station IDs (newer Green Line Extension stations, and Charles/MGH in 2014–2019) are filled from the same station name.
  * Some campus-zone stations are also major downtown or event stations (e.g. Kenmore near Fenway Park), so station-level series are kept alongside the zone totals.
  * The weekly tables also split entries into weekdays (Monday to Friday) and weekends, by service date; the population model compares the two.
  * *Closures.* The MBTA shuts stations for repairs, mostly on weekends and sometimes for weeks (the Orange Line for 30 days in August–September 2022), with shuttle buses instead of gated entries. A station-day counts as closed when its entries are below half of its typical level for that kind of day (weekday, Saturday or Sunday: the median of the surrounding nine weeks), after scaling that level by how the whole system did that day, so holidays and storms are not closures; days with no entries at all count as closed too. `mbta_weekly_by_station.csv` gives each station-week's closed days. 2.3% of study-area station-days are closed (4.6% of weekend days, 1.3% of weekdays), and 8.6% of station-weeks have at least one closed day, rising to 13–15% in 2024–2025.
  * Comparisons between stations (the passenger-survey check, the pooled campus model, the population model) leave out station-weeks with a closed day. The zone totals are not adjusted: closures cost a zone 1–2% of its weekly entries on average, and more than 5% in 6–9% of zone-weeks.
* **MBTA passenger survey.** The MBTA's 2025 System-Wide Passenger Survey (the Rider Census) pools 2022–2025 responses and reports, for each station or small group of stops, the weighted share of riders in each answer with its number of respondents.
  * Four answers describe students: riders using a Semester Pass (sold through colleges and universities, so it marks college students but misses those who pay per ride or use passes their university provides), riders with middle- and high-school student fares, trips between home and school (college and school students alike), and riders aged 18–25. An answer nobody at a station chose has no row and counts as 0.
  * Gated stations are matched to the survey's groups by name; two stations sharing a group get its values, and stations on two lines (e.g. Park Street) combine their lines' groups weighted by respondents. 69 of the 72 gated stations match: the Silver Line stations are reported by route, not station, and Beachmont has no group.
  * The highest semester-pass shares are at Community College (6.6%), Roxbury Crossing, Kendall/MIT, Ruggles, and JFK/UMass (about 3%); across the system it is 0.9%.
* **Street counts.** Boston's 24-hour bicycle and motor-vehicle counts cover about 90 locations on a few days per year, too few for a weekly series. They are used for paired comparisons of the same location in different seasons; in a first check on weekdays, September bicycle counts were about 18% above June near campuses and unchanged elsewhere, while motor-vehicle counts barely changed in either. Days with zero motor vehicles are treated as not counted.
* **Rideshare not used.** Massachusetts publishes rideshare trips only as yearly totals per municipality, which cannot show weekly or seasonal changes, so they are not part of the analysis. Statewide MassDOT traffic counts are likewise mostly published as yearly averages.
* **Campus zones.** Campus locations combine each institution's main campus from IPEDS with campus locations from City of Boston open data, which adds secondary campuses such as Harvard's business and medical schools and Boston University's medical campus.
  * Only institutions with typically at least 1,000 students present in person define zones: stations within 400 m of such a campus are "campus", within 1 km "near", and otherwise "away". Comparisons use study-area stations only.
  * Bluebikes reached the other study-area towns between 2019 and 2022 (Everett in 2019; Watertown, Arlington, Newton, and Revere in 2020; Chelsea in 2021; Medford and Malden in 2022; Quincy has only an employer's station at a Blue Cross Blue Shield office, with about 300 trips in 2018–2023), so the away zone grows over those years, although these towns carry only about 1.5% of trips; no station in them is within 400 m of a campus.
  * Wheelock College's campus counts as Boston University's, since it became BU's Wheelock College of Education when the two merged in 2018.

#### Universities and enrollment

* **Universities.** Institutions are selected by the location of their main campus, using the same town boundaries as Bluebikes stations: every degree-granting institution located in a study-area municipality, 59 in 2015–2023.
  * Each fall uses that fall's location, so an institution that moved away counts only while it was in the area (Andover Newton Theological School until 2017 and the New England School of Acupuncture in 2015, both in Newton).
  * Institutions that closed or merged during the period are kept for the years they reported; in their final year IPEDS lists them without enrollment, because their students were already reported by the institution they merged into or they had closed.
  * Non-degree schools (e.g. beauty, trade and photography schools) are left out. The City of Boston's list of colleges and universities was used as a cross-check: every degree-granting school on it that reported to IPEDS in 2015–2023 is included.
* **Students present in person.** Students enrolled only in online programs are subtracted from enrollment, since they are not physically in the area.
  * In fall 2020 this count reflects how courses were taught rather than where students lived, and universities reported it differently: those teaching fully online reported most students as online-only (Harvard 92%, MIT 66%, UMass Boston 99%) although some still lived locally (Harvard planned for about 1,600 undergraduates to live on campus), while those teaching in hybrid mode reported almost none (Boston University 9%, Northeastern 3%) although some of their students studied from home.
  * Student presence in fall 2020 is therefore approximate.
* **Enrollment coverage.** IPEDS fall enrollment is currently published through 2023.
  * Cambridge's Town Gown reports, which are published sooner, add fall 2024 for Harvard, MIT, Lesley, and Hult; each report year describes the previous fall. Other institutions and later years will need to be carried forward or estimated.
  * IPEDS gives one location per institution, so universities with several campuses are placed at their main campus.
  * IPEDS has no separate records for Northeastern's other U.S. campuses (Seattle, Charlotte, Silicon Valley, Portland, Arlington; only Oakland has its own), so their students are counted in Northeastern's two Boston records; its two records together grew by about 900 students a year in 2015–2019 but by 4,870 in fall 2021, roughly 3,000–4,000 more than the trend, which matches regional-campus students being folded in (Seattle alone reports almost 1,500 students).
  * Northeastern's own enrollment reports and Common Data Set do not separate campuses, so this is not corrected: regional-campus students are probably 3,000–6,000 of Northeastern's count from 2021, 10–15% of its in-person enrollment but about 2% of the study area's.
* **COVID-19 policies.** `data/manual/covid_policy.csv` records, for fall 2020 and spring 2021, whether each larger university taught remotely or in hybrid mode and whether its housing was open, limited or closed, with a source; it covers universities with about 85% of in-person enrollment.
  * Each row notes whether its source page was read directly and confirms it (21 rows), confirms only the teaching mode or the housing (4), or was only seen through search results (9: Bunker Hill, Emerson, Emmanuel, Lesley, Simmons, and UMass Boston's spring, whose pages are blocked or no longer online).
  * The weekly presence table flags the period (`covid_period`: normal, lockdown from 16 March to 15 August 2020, and remote_year for 2020–21) and gives the share of normal enrollment at universities teaching remotely: 34.5% in fall 2020 and 29.5% in spring 2021, the largest being Harvard, UMass Boston, MIT, and Bunker Hill Community College.
  * The presence index itself is not adjusted, so models can use the flag to treat 2020–21 separately or leave it out.

#### Where students live

* **Resident population.** Annual July 1 estimates come from the Census 2010–2020 intercensal series before 2020 and the latest postcensal vintage from 2020; both are based on the 2020 Census, so they join without a break.
  * Weekly values are interpolated between July 1 estimates and held at the latest estimate afterwards.
  * The Census counts college students where they live during the school year, so these totals already include students living in the area.
* **Student residents.** The number of college students living in each municipality comes from the American Community Survey 5-year estimates (table B14007), which count residents enrolled in college, including those in dormitories, for every study-area municipality in the same way. Each value averages five years of surveys.
  * Watertown's Census code changed in 2024 data (Watertown Town city became Watertown city), so both codes are matched to it.
  * Cambridge's Town Gown reports add dormitory and off-campus counts for its universities.
  * The City of Boston's annual student housing reports, compiled from the University Accountability Reports that Boston's institutions file each fall, give the students of Boston institutions who live in Boston by degree level and living arrangement: in university housing, off campus living with their parents, or off campus not at home. They are transcribed by hand with their source table (`data/manual/boston_student_housing.csv`, falls 2018–2024; earlier reports do not split the students living in Boston this way, and the 2018 report's stated undergraduate total is one more than its categories). They do not count Boston residents who attend institutions elsewhere, such as in Cambridge.
* **Where resident students live.** The ACS microdata (PUMS, 5-year files) record, for every college student, whether they live in group quarters (for students, college housing), as a child of the householder (with their parents), or otherwise (renting, owning, with a partner).
  * The microdata areas (PUMAs) split Boston into five and group the other towns with neighbours, some outside the study area, so each group's dorm residents are placed in the towns that have dorms by the 2020 Census count of college housing in each town (`census_college_housing.csv`), and the group's share living with parents is applied to each town's other students. The town totals stay those of table B14007.
  * Files ending in 2015 and 2022 mix two PUMA vintages; only the records with the vintage most of them carry are used, with their weights scaled up to the whole file.
  * For the ACS period ending in 2019, 32% of the study area's 228,080 resident college students lived in university housing, 17% with their parents and 51% elsewhere.
  * Checks against the cities' own counts: Cambridge 15,261 in university housing (Town Gown: 14,322 in dormitories and 2,028 in affiliated housing; 2020 Census: 15,437); Boston 35,657 (2020 Census: 35,750; the City's report counts 40,933 because it includes apartments the universities lease, which the Census counts as ordinary households) and 13,796 living with their parents (the City counts 9,197, but only for students of Boston institutions).
* **Residence halls during breaks.** `data/manual/residence_hall_breaks.csv` records whether the halls of the largest universities close for Thanksgiving, winter break and spring break, with a source: Bentley, Boston University, Brandeis, Emerson, Harvard, Lasell, Northeastern, and Tufts, with 62% of the beds.
  * Most close for winter break (Boston University's apartment-style buildings and Brandeis's apartments with kitchens stay open), but Northeastern lets students with year-long contracts stay through it.
  * Bentley, Boston University (since 2015–16), Brandeis, Harvard (spring) and Tufts stay open over Thanksgiving and spring break; Emerson closes for both.
  * Harvard's policy is known only from search results, because its site denies automated access; MIT's housing agreement needs a login, and no Boston College page was found, so neither is recorded.
  * Where a policy is not recorded, halls count as open outside summer.

#### Academic calendars

* **Sources.** Dates are collected by hand from official university calendars, each with its source and, where stated, its weekday, which is checked automatically.
  * Boston University, Northeastern, and Tufts are complete for 2014–15 to 2026–27 (Tufts from its registrar's published calendar feed, which keeps past years; four dates in 2019–20 and 2020–21 are missing from it), MIT from 2015–16 (from its catalog archive), and Boston College from 2015–16 (from its official academic calendar, with 2015–16 to 2018–19 class, break, and commencement dates from its School of Theology and Ministry calendar, which matches the university calendar where both exist).
  * Harvard's own calendar sites block automated access, so its dates come from the Harvard College calendar of events and the FAS faculty handbook (2020–21 onward), the provost's coordinated calendar (2014–16), and, for 2016–17 to 2019–20, class start dates reported by the Harvard Crimson; estimated Harvard dates are the end of exams before 2020–21, spring break and commencement for 2016–17 to 2018–19, commencement in 2019–20 and 2020–21, and the start of spring 2023 classes.
  * BU, Northeastern, Boston College, MIT, and Harvard all cancelled spring break in 2021 (Harvard replaced it with single "wellness days", which are not counted as breaks), and Tufts shortened it to a long weekend.
* **Sixteen more institutions** were added after the study area grew, in order of size:
  * UMass Boston (2020–21 to 2026–27, from the registrar's archived calendars; the archived Fall 2022 – Summer 2023 PDF repeats the fall 2023 dates under a fall 2022 heading, so its fall 2022 dates are estimated);
  * Suffolk (2019–20 to 2026–27, from the 25Live archive of its arts and sciences and business school calendar, which starts in January 2020);
  * Berklee (2025–26 and 2026–27);
  * Bunker Hill Community College (2014–15 to 2017–18, 2019–20 and 2021–22 from the academic calendars in its college catalogs, 2025–26 and 2026–27 from its website, and the 2022–2024 commencements from its ceremony pages);
  * MCPHS (2019–20 to 2023–24, 2025–26, and 2026–27, from its master academic calendars);
  * Bentley (2014–15 to 2026–27 except 2021–22, from the registrar's archived calendars; 2020–21 was a trimester year, and its spring 2021 dates come from the August 2020 version of the calendar);
  * Brandeis (2014–15 to 2026–27, from the registrar's term pages);
  * Emerson (2026–27);
  * Northeastern University Professional Programs (2014–15 to 2026–27, from the College of Professional Studies calendars);
  * Wentworth (2019–20 to 2026–27, from the academic calendars in its catalogs);
  * Simmons (2026–27), Lesley (2026–27), MassArt (2026–27), Emmanuel (2026–27), Curry (2025–26 and 2026–27), and Lasell (2025–26 and 2026–27).
* **Older calendars.** Where only one or two recent years are available, the other years are estimated from them and cannot be checked against held-out dates. Older calendars could not be collected for UMass Boston before 2020–21 (its catalog archive is behind a bot challenge and the registrar's archive starts in fall 2020), Emerson (its calendar archive is behind a bot challenge), Berklee (its site keeps only the previous year), Wentworth before 2019–20 (its older catalogs have no calendar), Simmons and Lesley (no archive found), and Lasell (its older catalogs have no calendar); the Internet Archive cuts its connections.
* **Special cases.** Three calendars need special handling:
  * The Professional Programs mostly teach graduate students in quarters: the fall quarter is recorded as the fall term, the start of the winter quarter as the start of spring classes, the week between the winter and spring quarters as spring break, and the end of the spring quarter (late June) as the end of spring exams, while the summer quarter counts as summer like every other summer term; quarters end with 2025–26 (2026–27 follows Northeastern's semester calendar), and these calendars give no commencement.
  * Brandeis has a week-long February break (the week of Presidents' Day, not held in spring 2021) as well as its April (Passover) spring recess; both are recorded, the February week as a separate `february_break` event that only universities with such a break have.
  * Wentworth runs three full semesters, with a spring semester from January to April; through 2025–26 it held commencements in April and August, and the April one is recorded, while its 2026–27 calendar lists only an August commencement.
  * In addition, Emerson's calendar lists no last day of classes or exams, so the day its residence halls close is recorded as the end of each term. Where a calendar lists exam make-up days, the last regular exam day is recorded.
* **Estimated dates.** Missing dates are estimated from the same university's verified years, keeping their typical offset from Labor Day (fall) or Martin Luther King Jr. Day (spring), and are marked as estimated.
  * Checked against verified dates held out one at a time, the median error is 0 days and 94% are within a week.
  * The largest errors are for Wentworth's 2026–27 commencement, which moved from April to August (111 days), the Professional Programs' switch from quarters to semesters in 2026–27 (up to 62 days), Brandeis's spring recess, which moves with Passover (up to 27 days), Bentley, which moved its fall start from late August to after Labor Day in 2022 and used trimesters in 2020–21 (up to 19 days), and spring 2021 terms that started late because of COVID-19 (up to two weeks).
* **Scheduled, not actual.** Calendars record scheduled dates, so the spring 2020 move to remote learning is not reflected in them, and MIT's January term is counted as out of session.

#### Student presence and students away

* **Student presence.** Each week, each institution's in-person enrollment is multiplied by the share of the week its calendar is in session. Institutions without a collected calendar (about 7% of in-person enrollment in fall 2023, mostly smaller colleges) follow the enrollment-weighted average of the collected calendars that week.
  * The Student Presence Index divides the students in session by the normal in-person enrollment, so it is 0 when every institution is on break and 1 when all are in session.
  * Students are treated as away from 16 March to 15 August 2020. In fall 2020 the normal enrollment keeps each institution's fall 2019 number of online-only students, so the extra students who studied remotely count as away (the index peaks at about 0.59 that year).
  * Enrollment for spring 2015 and for weeks after fall 2023 uses the nearest published fall and is flagged.
  * Summer terms are smaller and optional, so summer is not counted as in session; a separate summer-break share (the share of students whose university is between spring exams and fall classes) tells summer apart from other breaks.
  * A second index weighs each institution by its beds in university housing (IPEDS capacity) instead of its enrollment, because students in university housing are the most likely to leave on breaks while commuter colleges have few or no beds; it comes with the number of beds whose institution is out of session that week (in 2019, up to 79,191 in summer and about 26,000–28,000 in the March spring-break weeks). The two indices move almost together (correlation 0.98); they differ most around spring breaks.
* **How many students leave is measured, not assumed.** The Census already counts college students where they live during the school year, so the resident population includes the students living in the area; how many of them actually leave when not in session is what the project measures from travel records (goal 2). Step 15 therefore only bounds it each week.
  * At most, every resident college student not living with their parents is away when their school is out of session (`max_students_away`: students in university housing weighted by the housing presence index, the others by the presence index).
  * At least, the students in university housing whose residence halls are closed are away (`min_students_away`): every hall closes in summer, those recorded as closing close for their breaks, all are treated as closed during the spring 2020 lockdown, and halls without a recorded policy count as open outside summer. This assumes students whose halls close leave the area, which some do not (summer housing, friends).
  * The two give the lowest and highest possible population (`min_effective_population`, `max_effective_population`).
  * Resident students come from the ACS 5-year estimate ending in the year of the fall term, which lags recent changes by about two years; resident students changed by about 3,400 a year on average and by up to 12,800 over two years, so the lag can move the bound by up to about 0.7% of the population.

#### Modeling table

* **One row per week, nothing fitted.** `modeling_weekly.csv` joins the weekly tables on the week's Monday: the calendar (ISO week, month, academic year, holidays, and the weeks from December 24 to January 1), weather, Bluebikes and MBTA in total and by campus zone, the two student indices with the summer-break share, and the Census population with the range of students away. Every model reads the same 627 weeks (2014-12-29 to 2026-12-28) and 55 columns.
  * Network size is a column for both sources: the average number of Bluebikes stations in use per day, and the number of study-area gated MBTA stations with entries that week (60–71; stations close for repairs, and the Green Line Extension opened in 2022).
  * A week without data from a source keeps nulls, and `*_complete` flags mark weeks with all seven days of that source; 608 weeks have complete weather, Bluebikes and MBTA data. The share of trips by college-age riders is null after April 2020, when birth years stop.
  * Street counts are single days rather than a weekly series, so they stay out of the table.

#### Model comparison (goal 2)

* **Three model families, each with and without the index**: linear with a smooth seasonal curve, linear with a level for each week of the year, and gradient-boosted trees, for the campus ratio (campus-zone over away-zone activity) and the study-area total of Bluebikes trips and MBTA entries (inputs and settings under [Travel Models in Detail](#travel-models-in-detail); testing under [How performance was tested](../README.md#how-performance-was-tested)). The smooth curve cannot copy the calendar's sharp changes, so it leaves them to the index; the week-of-year levels learn the typical calendar, students included, so the index can only add what differs from year to year.
  * *Christmas and New Year.* The whole city slows down in the weeks from December 24 to January 1, which fall in the universities' winter break. Without a flag for those weeks, the index took credit for that slowdown: its effect on total Bluebikes trips came out at +47% instead of +29%, and on total MBTA entries at +23% instead of +16%. Flagging the weeks and dropping them give almost the same result. The campus ratio moves much less (Bluebikes +22% to +18%), since the slowdown hits both zones.

The change in held-out error when the index is added is in figure F17 ([Performance](../README.md#performance)). The lowest error for each target comes from gradient boosting for both campus ratios and from the week-of-year family for both totals.

Effect of the index (from no university in session to all in session), with a 95% interval, in the two linear families:

| Target | Linear, smooth season | Linear, week of year |
|---|---|---|
| Bluebikes campus ratio | +18% (+14% to +23%) | +15% (+4% to +27%) |
| MBTA campus ratio | +5% (+3% to +7%) | −3% (−10% to +5%) |
| Bluebikes total | +29% (+22% to +36%) | +32% (+18% to +47%) |
| MBTA total | +16% (+11% to +20%) | +18% (+9% to +27%) |

* **For every target, the best model includes the index.** Without it, the best family's held-out error is 1.6% (MBTA campus ratio) to 4.1% (Bluebikes campus ratio) higher.
* **Most of the index's gain is the regular academic calendar.** Against the smooth cycle the index lowers error by 6.7–7.9% for the Bluebikes targets and the MBTA total, mostly from the calendar's sharp edges, which repeat every year. Against a baseline that already knows the typical week of the year, what remains is the year-to-year shifts: 0.7–1.9%.
* **The effect sizes hold, except for the MBTA campus ratio.** For Bluebikes and the MBTA total, the two linear families agree on the effect (Bluebikes campus ratio +18% and +15%); the week-of-year intervals are wider because fewer weeks inform them. The MBTA campus ratio's +5% does not survive the week-of-year baseline (−3%, interval −10% to +5%). Its signal is weak in every family (held-out R² of about 0.25–0.3), as in the earlier checks: campus-zone gated stations also serve commuters and events, and the street-level Green Line stops at Boston University and Northeastern have no gates.
* **Trees fit the campus ratios best and the totals worst.** Gradient boosting has the lowest error for both campus ratios (Bluebikes 0.061, against 0.089 for the best linear model), finding interactions such as weather by season. It is the worst family for the totals, probably because trees cannot carry the growth of the networks into a held-out month the way a linear term in network size can.
* **The trees lean on the index once they have it.** Shuffling the index across weeks raises their held-out error by 66% for the Bluebikes campus ratio, 18% and 32% for the totals, and 4% for the MBTA campus ratio. That is far more than the index adds (1.6–4.5%), because with the index the trees stop learning the calendar from the date: the comparison with and without the index is the measure of what it adds.
* **The summer-break share adds nothing** once the index is in (smooth-season family: held-out error changes by less than 1%).
* **Reading the effects.** The effect is the change from every university out of session to every one in session, with standard errors that allow for neighbouring weeks being alike (Newey-West, 4 weeks). It covers everything that comes with term time: students, but also staff and campus events, and in summer partly school children, whose vacation overlaps. For the totals it is therefore an upper bound for students alone. The campus ratio is the cleaner measure, because it compares campus areas with the rest of the study area in the same week.

#### Pooled campus model

* **Campuses compared within the same week.** In the study-area models, students leave at the same time of year every year, so the index cannot be fully told apart from everything else that happens then. Universities do not break at the same time, though: spring breaks fall in different weeks, terms start before or after Labor Day, and some have a February break or a January term. `campus_panel.csv` pools the campuses and compares them week by week: the logarithm of activity at each campus is fitted with a level for every week, shared by all campuses (which removes weather, season, holidays, visitors and school vacations), a level for each campus in each year (size and new stations), and the share of the week its own university is in session.
  * *Units.* Bluebikes trips at the stations of each campus, and entries at each gated station in a campus zone, linked to the nearest campus's university.
  * *All universities, assumed calendars.* Every campus with travel data (19 Bluebikes campuses, 8,988 campus-weeks; 15 subway stations near 9 universities, 7,047 station-weeks), each with the calendar the Student Presence Index assumes for its university: its own collected calendar, in verified years and in years estimated from its other years (keeping their offset from Labor Day or Martin Luther King Jr. Day), or, without a collected calendar, the typical calendar, the enrollment-weighted average of the collected ones. Tufts' Bluebikes stations and the Tufts Medical Center station are at its health-sciences campus, whose medical and dental schools follow other calendars than the main-campus calendar collected, so they are given the typical calendar and reported apart from Medford/Tufts.
  * *Verified calendars only.* Universities with their own calendar, in academic years without estimated term or break dates, with at least a year of such weeks; Bluebikes campuses with a median of at least 100 trips a week; without Tufts' health-sciences campus (8 Bluebikes campuses, 2,480 campus-weeks; 9 subway stations of 5 universities, 2,107 station-weeks).
  * *Closures.* MBTA station-weeks with a closed day ([MBTA entries](#mbta-street-counts-and-campus-zones)) are left out, since the fall in entries is the closure, not students: 8% of the station-weeks in the all-universities version. This also drops Longwood, gated in 2026, whose few recorded entries (about one a day) are not real traffic, and Hynes Convention Center (Berklee) from the verified version, which no longer has a year of verified weeks.
  * *Uncertainty.* Standard errors are clustered by calendar month, so neighbouring weeks and nearby campuses count as alike.
  * *Placebo.* Each campus is also given another calendar, for every rotation of the list of calendars (the typical calendar counts as one). If activity follows each university's own students, the wrong calendars should give smaller effects.

| Source | Version | Effect of the own calendar (0 → 1), 95% interval | With a seasonal cycle for each campus | Other calendars (placebo) |
|---|---|---|---|---|
| Bluebikes | all universities, assumed calendars | +14% (+10% to +18%) | +16% (+13% to +20%) | median 0%, at most +2% (14 rotations) |
| Bluebikes | verified calendars only | +21% (+14% to +28%) | +26% (+21% to +33%) | median −4%, at most +6% (7 rotations) |
| MBTA | all universities, assumed calendars | +7% (+5% to +10%) | +6% (+4% to +8%) | median 0%, at most +1% (7 rotations) |
| MBTA | verified calendars only | +11% (+3% to +19%) | +9% (+2% to +15%) | median +1%, at most +6% (4 rotations) |

By university, all universities with assumed calendars (95% intervals; "verified" is the share of the university's weeks with verified dates):

| University | Calendar | Bluebikes | Bluebikes, own seasonal cycle | MBTA |
|---|---|---|---|---|
| Northeastern University | own, 100% verified | +46% (+37% to +55%) | +56% (+39% to +75%) |  |
| Lesley University | own, 1% verified | +42% (+34% to +50%) | +27% (+19% to +35%) |  |
| Massachusetts Institute of Technology | own, 85% verified | +34% (+25% to +43%) | +23% (+17% to +30%) |  |
| Wentworth Institute of Technology | own, 62% verified | +23% (+14% to +32%) | +47% (+34% to +62%) |  |
| Massachusetts College of Art and Design | own, 1% verified | +21% (+14% to +27%) | +24% (+15% to +35%) |  |
| Boston University | own, 100% verified | +19% (+15% to +24%) | +26% (+20% to +32%) | −4% (−9% to +1%) |
| Roxbury Community College | typical (assumed) | +16% (+9% to +23%) | +19% (+6% to +35%) | +18% (+13% to +22%) |
| Harvard University | own, 39% verified | +13% (+8% to +18%) | +13% (+8% to +18%) | +5% (+2% to +9%) |
| University of Massachusetts-Boston | own, 44% verified | +11% (−1% to +25%) | +37% (+16% to +62%) |  |
| Emmanuel College | own, 1% verified | +4% (−2% to +10%) | +10% (+1% to +20%) |  |
| Berklee College of Music | own, 13% verified | 0% (−5% to +6%) | −3% (−10% to +5%) | +15% (+12% to +18%) |
| Bunker Hill Community College | own, 62% verified | −1% (−6% to +4%) | −6% (−11% to −1%) | +47% (+42% to +52%) |
| Simmons University | own, 1% verified | −3% (−7% to +2%) | −6% (−11% to +1%) |  |
| Tufts University, health-sciences campus | typical (assumed) | −5% (−10% to 0%) | −16% (−21% to −10%) | +7% (+4% to +11%) |
| Hult International Business School | typical (assumed) | −6% (−11% to −1%) | 0% (−7% to +7%) |  |
| Suffolk University | own, 42% verified | −8% (−12% to −5%) | −10% (−15% to −6%) | −1% (−3% to +2%) |
| Emerson College | own, 1% verified | −11% (−15% to −7%) | −7% (−13% to −1%) | +11% (+8% to +15%) |
| Cambridge College | typical (assumed) | −13% (−18% to −7%) | −14% (−23% to −5%) |  |
| MGH Institute of Health Professions | typical (assumed) | −14% (−20% to −7%) | −16% (−27% to −5%) |  |
| Quincy College | typical (assumed) |  |  | +2% (−2% to +6%) |
| Tufts University | own, 100% verified |  |  | −11% (−28% to +10%) |

* **Activity follows each university's own calendar.** Every campus gets the same weather, season and holidays in a given week, yet trips at a campus are higher when its own university is in session, and no other calendar gives a comparable effect. With every campus and assumed calendars the effect is smaller (+14% for Bluebikes, +7% for the subway) than with verified calendars only (+21%, +11%): assumed dates blur the timing, and the added campuses include several where few riders are students. The placebo is clearer with more campuses: no other calendar reaches +2%.
* **Where it is strong.** At residential campuses whose students ride near their stations: Northeastern, MIT, Wentworth, Boston University and Harvard for Bluebikes, and the Community College station for the subway. Some colleges known only from estimated calendars show it too (Lesley +42%, MassArt +21%), so their estimated calendars fit; Roxbury Community College shows it on the typical calendar (+16%, and +18% at Roxbury Crossing), although its stations may also serve Northeastern and Wentworth students. A campus's stations can serve neighbouring universities' students, so a university's effect is not only its own students.
* **Where it is absent or negative.** At downtown campuses whose stations mostly serve commuters and visitors (Suffolk, Emerson for Bluebikes, Suffolk's five subway stations, Boston University's Kenmore and Longwood) and at graduate, health-professions and adult-learning institutions on the assumed typical calendar (MGH Institute of Health Professions, Cambridge College, Hult, Tufts' health-sciences campus), whose programmes often run through the summer, so the typical calendar probably does not fit them. Summer seasonality does not explain the negative effects: with a seasonal cycle for each campus, most stay negative.
* **The pooled effects are steadier than the per-university ones.** With each campus's own seasonal cycle, some universities move a lot (Wentworth +23% to +47%, MIT +34% to +23%, UMass Boston +11% to +37%), while the pooled effects move by 1–6 points.
* **Precise, but a small part of the noise.** After the week and campus-year levels, the own calendar explains 1–7% of the remaining week-to-week variation at a Bluebikes campus and about 0.5% at a subway station: single stations vary a lot from week to week (events, partial closures, local weather). The effect is precise because there are thousands of campus-weeks, not because it explains much of that noise.

#### Population model

* **What it predicts.** The weekly resident population of the study area (`population_model.py`): the Census resident population minus resident college students away. Students away = resident college students × (the summer share away × the share of students on summer break + the break share away × the share otherwise out of session), kept inside the range of step 15. The two shares away (of the students out of session, how many leave the area) are the unknowns; the calendars, enrollment, housing and the Census give the rest.
* **How travel reflects it.** Gated subway stations differ in how many of their riders are college students: the passenger survey's share of riders with a Semester Pass, which is sold only through universities, runs from 0 to 6.6% across the study area's stations. When students are missing, stations with more student riders lose more entries. Each week, the slope of station entries (logarithm, after a level for each station in each year and for each week) on that share measures how many student riders are missing that week:
  * on weekdays, every student out of session, whether they left or stay without classes: weekday slope = c + k × share of students out of session;
  * on weekends, when there are no classes, only those who left: weekend slope = c′ + k × share of students away;
  * with the same k on both kinds of day, which assumes students are the same proportion of a station's riders on weekends as on weekdays. The weekday slopes fix k from the calendar; the weekend slopes then say how many students left.
  * The data are the 67 study-area gated stations in the survey, without station-weeks with a closed day, in 2022–2025 (the years the survey pools; 209 weeks), with 2016–2019 as a check.
* **The weekly signal is clear.** With the Semester Pass measure in 2022–2025, the weekday slope correlates −0.71 with the share of students out of session, and the weekend slope −0.59.
* **Which version of who leaves: chosen on years the model did not see.** A model of the population has to hold in years it was not fitted on, so each version is fitted on earlier years and scored by the weekend slopes it predicts for later ones (change in error against nobody leaving, `population_model_forward.csv`):
  * *validation*, forward in time: 2023 from 2022, 2024 from 2022–2023, 2025 from 2022–2024, and, across the COVID-19 gap, 2022–2025 from 2016–2019;
  * *test*: January to September 2026 (38 weeks: 17 in summer, 20 with a break, 12 fully in session) from 2022–2025, scored once, after the measure and version were chosen on the validation years. Neither the model nor the exploration that led to it had used 2026.
  * Weekly calendar inputs enter as deviations from their mean over the same year, as travel does (it is measured against each station's level for the year), so a part of a year such as 2026 is scored on the same footing.
  * The results are in the table and figure under [Performance](../README.md#performance).
  * The validation years chose the measure and the version. With the Semester Pass measure, every student out of session not living with their parents leaving had the lowest error in each validation year and across the COVID-19 gap, and the Semester Pass measure gave the largest gain of the three (20% on average, against 14% for riders aged 18–25 and 11% for trips between home and school).
  * The fitted shares move with the years they learn from (summer 0.59 from 2022, 0.59 from 2022–2023, 0.76 from 2022–2024, 0.90 from 2022–2025), so with a few years of data they predict later years less well than the version with nothing to fit.
  * With the other two measures 2026 is weaker: with riders aged 18–25 every version gains only 4–6%, and with trips between home and school the chosen version does worse than nobody leaving (+9.0%), as a measure that includes school children would. The result rests on the Semester Pass measure.
  * Within a period, predicting each calendar month from the other weeks gives the same ranking (`population_model_comparison.csv`): every student not living with parents is best with the Semester Pass measure in 2022–2025 (−18.9%) and 2016–2019 (−7.3%), and only closed residence halls is worse than it with every measure in both periods.
* **The fitted shares** (summer and breaks; 95% intervals from refitting on calendar months drawn with replacement). The [known-truth test](#exploring-models-and-signals) found intervals made this way too narrow; widened as calibrated there, the Semester Pass shares of 2022–2025 are 0.64–1.20 and 0.27–1.30 (`joint_population_shares.csv`, subway alone):

| Student measure | Years | Summer | Breaks |
|---|---|---|---|
| Semester Pass | 2022–2025 | 0.90 (0.71–1.11) | 0.74 (0.42–1.13) |
| Riders aged 18–25 | 2022–2025 | 0.86 (0.65–1.05) | 1.31 (0.94–1.79) |
| Trips between home and school | 2022–2025 | 0.49 (0.35–0.64) | 0.87 (0.57–1.23) |
| Semester Pass | 2016–2019 | 0.73 (0.47–0.97) | 0.69 (0.21–1.10) |
| Riders aged 18–25 | 2016–2019 | 1.22 (1.05–1.42) | 1.22 (0.90–1.56) |
| Trips between home and school | 2016–2019 | 0.67 (0.47–0.84) | 0.82 (0.51–1.15) |

  * The shares are of all resident students out of session. The 17% who live with their parents stay, so a share of about 0.83 is the chosen version, and the Semester Pass intervals include it for summer and for breaks.
  * Trips between home and school include school children, who stop school trips in summer without leaving, which pulls the summer share down. Riders aged 18–25 include many non-students, and in 2016–2019 give shares above 1, which is impossible: the assumption of equal proportions can fail.
* **The estimate** (`population_estimate.py`, `population_estimate.csv`). The comparison above assumes students are the same share of weekend riders as of weekday riders. Near campuses they are a larger one ([Checks against other evidence](#checks-against-other-evidence)), which makes every version predict best at shares too large by that factor. The estimate therefore takes the shares fitted on the subway and Bluebikes together (summer 0.83, breaks 0.91, close to the chosen version) and divides them by the weekend factor measured on Bluebikes, 1.26: about 66% of students out of session leave in summer and 72% on other breaks. The 95% band pairs each refit of the shares (widened as calibrated in [Exploring models and signals](#exploring-models-and-signals)) with a refit of the factor, so it covers both: summer 0.52–0.82, breaks 0.41–1.07 (a share above 1 counts as 1). Every student not living with their parents leaving, the lower line of the range, is the most there could be.

| Week of 2019 | Possible range of students away (step 15) | Students away, estimate (95% interval) | Population, estimate |
|---|---|---|---|
| Mid-summer (July 8) | 73,420–190,056 | 150,656 (119,876–188,428) | 1,694,078 |
| Spring breaks (March 11) | 1,826–72,254 | 61,398 (34,995–72,254) | 1,782,221 |
| Thanksgiving (November 25) | 1,815–135,180 | 117,055 (66,718–135,180) | 1,723,652 |
| After Christmas (December 30) | 36,168–189,248 | 163,874 (93,403–189,248) | 1,675,826 |

  * In July of each year outside the COVID-19 period, 144,000–156,000 students are away (7.8–8.6% of residents), with 95% intervals from about 112,000–124,000 to 180,000–195,000, against 181,000–196,000 at the most.
  * The week after Christmas, when nearly every student is out of session and the higher break share applies, has more students away than mid-summer, and is the year's low point of the population.
  * Averaged over the weeks of 2019, 67,000 resident college students were away (3.6% of residents), with the band from 48,000 to 81,000 on average: 33,000 students wide, against 56,500 for the possible range.
* **What the 95% band covers, and what it does not.**
  * It covers the statistical uncertainty of the shares fitted on the subway and Bluebikes (refits on calendar months drawn with replacement, widened by 1.4 so that, on signals simulated from a known truth, the intervals contained it about 95% of the time; nobody counts the population each week, so they cannot be checked against observed counts), and the sampling uncertainty of the weekend factor (stations and months resampled).
  * It does not cover: whether the subway's weekend factor is the same as Bluebikes' (it could not be tested; with a factor of 1 for the subway, about 164,000 students rather than 151,000 were away in mid-July 2019); whether riders aged 18–24 stand for students; whether the factor measured in 2016–2019 still holds in 2022–2025; errors in the Census, ACS and IPEDS inputs; and the model's form (one summer share and one break share for every student and year).
* **Read off travel week by week.** Each week of 2022–2025 can also be read directly: (weekend slope − c′) / (k × the weekend factor) gives that week's share of students away relative to the year's mean, to which the estimate's mean for the year is added (`travel_students_away`, and a five-week average). It follows the estimate (correlation 0.59, 0.61 averaged), but single weeks miss it by about ±89,000 students (±66,000 averaged). Travel pins down the shares over many weeks, not the population of any one week.
* **Assumptions.**
  * Students are the same proportion of a station's riders on weekends as on weekdays. They are a larger one near campuses (commuters ride less on weekends): Bluebikes riders' ages put the factor at about 1.26, which the estimate corrects for ([Checks against other evidence](#checks-against-other-evidence)); if students who stay also stop weekend trips to campus (libraries, events), the shares away are still overstated.
  * Students who stay keep their weekend travel, and those who leave stop it; subway riders stand for all resident college students; the shares are the same every year.
  * Only resident college students are counted; visitors, commuters and people who arrive for the summer, such as interns subletting student apartments, are not.

#### Forecast

* **What it is.** The population model run on the weeks of the academic year 2026–27 (August 3, 2026 to July 19, 2027), beyond the study's weekly timeline, with the inputs known in advance (`population_forecast.py`, `population_forecast.csv`): the published 2026–27 calendars of all 22 universities with collected calendars, enrollment held at fall 2023 (IPEDS), resident students and where they live at the ACS 5-year estimate ending 2024, the Census population at July 2025, and the population model as fitted, with its 95% band. It runs through the same steps as the estimate, and the 22 weeks it shares with `population_estimate.csv` (August to December 2026) have the same numbers.

| Week (2026–27) | Students away, forecast (95% interval) | Population, forecast |
|---|---|---|
| Thanksgiving (November 23, 2026) | 112,759 (64,269–132,916) | 1,747,640 |
| Christmas to New Year (December 28, 2026) | 157,859 (89,975–186,079) | 1,702,540 |
| Busiest spring-break week (March 8, 2027) | 83,681 (47,696–98,946) | 1,776,718 |
| Summer (from May 24, 2027) | 144,791 (115,209–181,092) | 1,715,608 |

* **What it says.** Spring breaks bunch more in 2027 than in 2026: 83,681 students away in the busiest week, against 57,619 in 2026. Otherwise the year looks like 2025–26 (average population 1,796,465 against 1,795,933).
* **What the band does not cover.** The band covers the shares away and the weekend factor, not the held inputs. Outside the COVID-19 years, the study area's Census population changed by 0.8% a year (median; at most 1.1%), IPEDS in-person enrollment by 0.7% (at most 2.0%), and ACS resident students not living with their parents by 1.1% (at most 3.5%) (`population_forecast_inputs.csv`). Held for two to three years, that adds roughly ±1.5% (about ±28,000 people) to the population level and a few percent to the students away, about as much as the band (on average 32,000 students wide). The summer level repeats 2025's because the Census is held at July 2025, not because the population stops changing, and published calendar dates can still change.

#### Checks against other evidence

`make checks` (`population_checks.py`) tests the population model against evidence it was not built from.

* **COVID-19 as a natural experiment.** Students were sent home in March 2020, 41% of normal in-person enrollment studied remotely in 2020–21, and students came back in fall 2021. The model's inputs record this, but the model never saw those weeks. Its measurement part, fitted on 2022–2025, predicts the weekly station slopes of 2020 and 2021 with three sets of inputs (error; lower is better):

| Year | Day | COVID-19 inputs | Normal-year calendar | Nobody away |
|---|---|---|---|---|
| 2020 | Weekdays | **5.51** | 6.45 | 7.02 |
| 2020 | Weekends | **5.97** | 6.19 | 6.72 |
| 2021 | Weekdays | **2.54** | 3.57 | 3.84 |
| 2021 | Weekends | **2.75** | 3.17 | 3.51 |

  * The inputs with the absences predict travel best in both years and on both kinds of day: travel saw the students leave and come back. Office commuters also vanished in 2020 and returned gradually, which affects stations differently, so the check is indicative; the 2020 errors are about twice those of 2021.
* **Size.** Better is not the same as the right size. For years outside the fit, the observed slopes are regressed on the part the model predicts from its inputs; 1 means the model's size of the absence matches travel (95% intervals from resampling calendar months):

| Years | Weekdays | Weekends |
|---|---|---|
| 2016–2019 | 1.00 (0.91–1.10) | 0.88 (0.52–1.18) |
| 2021 | 1.10 (0.69–1.38) | 0.95 (0.47–1.39) |
| 2026 (test) | 1.01 (0.61–1.40) | 0.93 (0.62–1.21) |
| 2020 | 2.23 (1.00–3.02) | 1.54 (0.74–2.20) |

  * Outside 2020, weekday travel moves as much as the model says, and weekend travel 88–95% as much: consistent with the model, with a hint that it overstates leaving a little. Every interval includes 1, but one year cannot pin the size closer than about ±30–50%.
  * In 2020, travel at student-heavy stations fell about twice as much as the students' absence explains: the lockdown emptied commuter traffic too.
* **Bluebikes, a second measurement.** The same measurement model, fitted on Bluebikes instead of the subway: member trips (residents; casual riders are often visitors) started at study-area stations in campus zones against away zones, on weekdays and on weekends, at stations with a median of at least 20 member trips a week. Its weekly slopes follow the share of students out of session at least as closely as the subway's (correlation −0.78 on weekdays, −0.65 on weekends, 2022–2025). Change in error against nobody leaving:

| Who leaves (Bluebikes) | 2023 from 2022 | 2024 from 2022–23 | 2025 from 2022–24 | 2022–25 from 2016–19 | Test: 2026 (January–August) from 2022–25 |
|---|---|---|---|---|---|
| Only students in closed residence halls | −8.9% | −9.1% | −18.0% | −6.8% | −16.9% |
| Every student not living with parents | −19.1% | −19.5% | **−36.8%** | −17.3% | −20.7% |
| The same fraction leaves on every break, estimated from travel | −18.9% | −19.6% | −35.9% | **−17.4%** | **−21.1%** |
| One fraction for summer and another for other breaks, estimated | −21.2% | −21.4% | −32.5% | −14.1% | −20.5% |
| A separate fraction for each kind of break (summer, Thanksgiving, winter, spring), estimated | **−23.6%** | **−22.3%** | −33.2% | −14.5% | −17.3% |

  * Bluebikes also prefers the versions in which most students leave to only closed residence halls in every column (by 4 points on the 2026 test, 11–19 points elsewhere), but it does not single out one of them: the finer fitted versions win 2023 and 2024, and every student not living with parents wins 2025.
  * Its fitted shares, summer 0.79 (0.67–0.90) and breaks 1.02 (0.59–1.39) (intervals not widened), overlap the subway's (0.90 and 0.74). The summer share is a little below the 0.83 of every student not living with their parents, as in the size check: about 95% of them leave rather than all.
  * On 2016–2019 data Bluebikes gives a break share above 2, which is impossible: the network was too thin around campuses then ([Presence evaluation](#presence-evaluation)), so those years only serve as the long-horizon training data above.

* **Are students the same share of weekend riders as of weekday riders?** (`bluebikes_ages.py`, `weekend_riders.py`). The estimate rests on this assumption: if students are a larger share of weekend riders, weekend travel moves more for each student who leaves, and the shares away come out too large by that ratio. The MBTA passenger survey does not split weekdays from weekends, but Bluebikes published riders' birth years until April 2020. In members' trips at 378 study-area stations in 2016–2019, riders aged 18–24 stand in for students. Share of member trips by riders aged 18–24, in weeks when every university is in session and in summer weeks (`weekend_riders_shares.csv`):

| Stations | In session, weekdays | In session, weekends | Summer, weekdays | Summer, weekends |
|---|---|---|---|---|
| Away zone (206 stations) | 15.7% | 20.8% | 16.8% | 20.7% |
| Campus zone (60) | 24.0% | 33.6% | 21.7% | 28.2% |
| Campus zone of the seven universities where young riders are clearly students (22) | 34.6% | 44.7% | 28.7% | 34.8% |

  * The Bluebikes signal compares campus-zone with away-zone stations, so what matters is how much larger the students' share is at campus-zone stations, on weekends against weekdays (95% intervals with stations and months resampled, `weekend_riders.csv`). Across the whole campus zone that excess is 1.54 times as large on weekends (1.23–2.38), but part of it is not students: at downtown campus-zone stations (Suffolk, Emerson, Tufts' health-sciences campus) young riders are fewer than in the away zone on weekdays, when commuters ride, and catch up on weekends.
  * At the campus-zone stations of the seven universities where riders aged 18–24 are at least 10 points more common than in the away zone on weekdays (Berklee, Boston University, MassArt, MIT, Northeastern, Roxbury Community College, Wentworth), the excess is **1.26 times as large on weekends (1.11–1.48)**, and 1.22–1.33 in each year. It varies by campus: no increase at Northeastern (0.97), 1.24 at MIT, 1.48 at Boston University.
  * **The assumption fails for Bluebikes, in the direction that overstates leaving**, probably because weekday riders near campuses include commuters (to Kendall Square, the Longwood medical area, offices) who are absent on weekends.
  * About 60% of the young excess at those campuses is still there in summer, so many young riders near campuses are not students who leave (students who stay, summer students, interns, young residents); the factor is that of this mix, not of students alone.
  * **What it means.** The joint model refitted with the weekend inputs multiplied by the factor (ranges from the factor's interval, without the shares' own uncertainty):

| Corrected with the factor 1.26 (1.11–1.48) | Summer share away | Break share away | Students away, mid-July 2019 |
|---|---|---|---|
| Not corrected (subway and Bluebikes) | 0.83 | 0.91 | 189,887 |
| Bluebikes alone (as fitted 0.79 and 1.02) | 0.62 (0.53–0.71) | 0.81 (0.69–0.93) | 142,615 (121,292–162,668) |
| Subway and Bluebikes, Bluebikes corrected | 0.72 (0.64–0.78) | 0.79 (0.72–0.86) | 163,921 (146,932–178,459) |
| Subway and Bluebikes, both corrected | 0.66 (0.56–0.75) | 0.72 (0.61–0.82) | 150,654 (128,129–171,837) |

  * The subway could not be tested. Its weekday riders at student-heavy stations also include many commuters, so a factor above 1 is likely there too, but its size is unknown. The estimate applies Bluebikes' factor to both signals ([Population model](#population-model)): about two thirds of students out of session (80% of those not living with their parents) leave in summer, not all of them. The forward validation cannot tell these apart: it scores each version on weekend travel under the same assumption.
  * If the subway's factor were 1, the joint shares corrected for Bluebikes only would put 163,921 students away in mid-July 2019, and uncorrected 189,887, at the top of the estimate's band (188,428).

#### Exploring models and signals

* **Why.** The population model reads the population from subway travel with one simple form: a straight line in the share of students away. This step tests that choice against other kinds of models, other features (weather, holidays) and other signals, alone and together (`presence_signals.py`, `travel_models.py`, `joint_population.py`, `population_accuracy.py`).
* **Signals** (`presence_signals_weekly.csv`), each week, for weekdays and weekends apart: the subway (the Semester Pass slope of the population model), Bluebikes (member trips at campus-zone against away-zone stations), and Boston 311 service requests from residents near campuses against those away from them (`boston_311.py`), which show people at home however they travel. **Features**: the student inputs; weather on weekdays and on weekend days apart (mean temperature, and the shares of days with rain, heavy rain, snow and heat); holidays and the Christmas–New Year weeks; all as deviations from their mean over the same year, like the signals. No weekly public data exist for driving or ride-hailing in the study area (see [Traffic and Rideshare](../README.md#data-sources)).
* **Eight model families and six feature sets** predict each signal for years they were not fitted on: 2023 from 2022, 2024 from 2022–2023 and 2025 from 2022–2024 (validation), and January–September 2026 from 2022–2025 (test) (`travel_model_accuracy.csv`). The support vector machine, nearest neighbours and the neural network (a multi-layer perceptron with hidden layers of 32 and 16 units) have their settings chosen by cross-validation on the training years. Share of the week-to-week variance explained (R²), averaged over the four travel signals:

| Family | Weather and calendar only | Students: only closed halls leave | Students: everyone not living with parents | Students and weather | Students and weather, 2026 test |
|---|---|---|---|---|---|
| Linear regression | 0.21 | 0.43 | **0.47** | 0.46 | 0.46 |
| Ridge | 0.21 | 0.43 | **0.47** | 0.45 | 0.47 |
| Lasso | 0.21 | 0.42 | 0.46 | **0.47** | 0.47 |
| Neural network | 0.22 | 0.43 | 0.46 | 0.43 | **0.50** |
| Support vector machine | 0.19 | 0.43 | 0.44 | 0.37 | 0.43 |
| k nearest neighbours | 0.17 | 0.39 | 0.41 | 0.37 | 0.46 |
| Random forest | 0.15 | 0.40 | 0.41 | 0.45 | 0.48 |
| Gradient boosting | −0.13 | 0.37 | 0.36 | 0.35 | 0.37 |

  * **Simple models do as well as any.** On the validation years, linear regression, ridge and lasso match or beat the neural network and the support vector machine, and random forest, gradient boosting and nearest neighbours do worse: with 50 to 150 weeks to learn from and a nearly straight-line relationship, flexibility mostly adds noise. On the 2026 test the flexible models with weather do slightly better (neural network 0.50, random forest 0.48, against 0.46–0.47), within the spread between years.
  * **The students carry the signal.** Weather and calendar alone explain about a fifth of the variance (partly because summer is warm), the student inputs 0.36–0.47, and every student not living with their parents leaving beats only closed residence halls in every family but gradient boosting (0.36 against 0.37).
  * **Weather matters for bikes, not for the subway.** With the best family for each, the subway's weekday signal is explained 0.48 by the students alone and 0.43 with weather added, its weekend signal 0.34 and 0.27; Bluebikes' weekday signal 0.62 and 0.70, its weekend signal 0.43 and 0.47. Comparing stations within the same week already removes the weather all stations share; what is left matters for bikes, whose riders near campuses respond to the weather differently from others.
  * **311 requests carry no student signal**: R² about 0 with every family and feature set (mean −0.02). Requests near campuses follow other things, such as moves, snow and street cleaning.
  * The 95% intervals (each prediction plus or minus the 95th percentile of cross-validated errors on the training years) contained 90% of the validation weeks and 97% of the test weeks.
* **One population, several signals** (`joint_population_shares.csv`): the shares away fitted jointly on several signals, each with its own scale, after each family of models has removed what weather and calendar predict (fitted on the other months of the data, so that it cannot absorb the students' part):

| Signals | Weather removed by | Summer share away (95% interval) | Break share away (95% interval) |
|---|---|---|---|
| Subway alone | nothing | 0.90 (0.64–1.20) | 0.74 (0.27–1.30) |
| Bluebikes alone | nothing | 0.79 (0.63–0.95) | 1.02 (0.47–1.52) |
| **Subway and Bluebikes** | nothing | **0.83 (0.69–0.99)** | **0.91 (0.52–1.28)** |
| Subway, Bluebikes and 311 | nothing | 0.83 (0.69–0.99) | 0.90 (0.52–1.28) |
| Subway and Bluebikes | linear regression, ridge, lasso, neural network | 0.83–0.85 | 0.93–0.95 |
| Subway and Bluebikes | nearest neighbours, random forest | 0.80, 0.76 | 1.01, 1.04 |
| Subway and Bluebikes | support vector machine, gradient boosting | 0.73, 0.70 | 0.89, 0.92 |

  * The intervals are the calibrated ones of the test below: refits on calendar months drawn with replacement, their spread widened by 1.4.
  * Together, the subway and Bluebikes narrow the shares (summer 0.69–0.99, breaks 0.52–1.28, against 0.64–1.20 and 0.27–1.30 for the subway alone), and give the band of the population estimate. The joint summer share, 0.83, is that of every student not living with their parents leaving, the version the population model chose on its own.
  * Every way of removing the weather gives a summer share inside the others' intervals, but removing it widens them (summer 0.44–0.97 to 0.62–1.10, against 0.69–0.99 without). The tree models and the support vector machine give lower shares (0.70–0.76), probably because they let temperature take part of the summer's timing.
* **Accuracy in people: a test with a known truth** (`population_accuracy.py`). Nobody counts the population each week, so the method is tested on signals made up from shares chosen in advance, with the fitted scales and the noise the real fits leave (shifted in time, which keeps its week-to-week pattern). The joint model is fitted on them as on the real ones, and its shares are turned into students away in the weeks of 2019.
* **Calibrating the intervals** (`population_accuracy_calibration.csv`). For 60 made-up data sets of each truth, the shares are refitted on calendar months drawn with replacement, in runs of one, three or six consecutive months, and the refits' spread around the estimate is widened by 1.0 to 2.0. How often the 95% interval contains the true share (summer, breaks), on average over the four truths below:

| Months drawn together | Not widened | Widened by 1.2 | Widened by 1.4 | Widened by 1.6 |
|---|---|---|---|---|
| **1** | 86%, 81% | 94%, 88% | **97%, 96%** | 98%, 98% |
| 3 | 84%, 82% | 92%, 89% | 97%, 96% | 98%, 98% |
| 6 | 83%, 79% | 90%, 86% | 93%, 90% | 96%, 94% |

  * Not widened, the intervals are too narrow, and drawing longer runs of months does not help, so the week-to-week dependence of the noise is not the main reason. The rule chosen in advance takes the shortest run, then the smallest widening, that reaches 95% for both shares: one month, widened by 1.4. The joint model and the band of the population estimate use it.
* **Accuracy, on new made-up data sets** (so that coverage is not measured on the data the widening was chosen on): errors from 200 data sets for each truth, coverage from 60 of them.

| True shares away (summer, breaks) | Estimated on average | Typical error, mid-summer week (students) | 95% of errors within | Typical error, Thanksgiving week | 95% interval contains the true share (summer, breaks) | Band contains the true students away (mid-summer, Thanksgiving) |
|---|---|---|---|---|---|---|
| 0.50, 0.50 | 0.49, 0.50 | 13,892 | −34,630 to +28,712 | 17,622 | 100%, 97% | 100%, 97% |
| 0.70, 0.60 | 0.70, 0.59 | 12,248 | −26,717 to +29,479 | 18,409 | 95%, 93% | 95%, 93% |
| 0.90, 0.74 | 0.90, 0.74 | 1,013 | −11,176 to 0 | 13,545 | 90%, 92% | 100%, 92% |
| 1.00, 1.00 | 1.01, 1.01 | 0 | 0 | 1,033 | 95%, 95% | 100%, 100% |
| 0.70, 0.60, with students 20% more of weekend riders than the model assumes | 0.85, 0.73 | 25,241 | +5,163 to +30,030 | 22,026 | 52%, 80% | 52%, 80% |

  * **When its assumptions hold, the method is unbiased**: on average it finds the true shares. A single summer or break week is typically off by 12,000–18,000 students (out of about 1.84 million residents); near the upper end of the range the error is smaller, because no estimate can exceed every student not living with their parents. Averaged over all the weeks of 2019, most of which have nobody out of session, the typical error is at most about 7,400 students.
  * **The calibrated intervals hold on new data**: they contain the true share 95% of the time for summer and 94% for breaks on average (90–100% for single truths, each from 60 data sets and so uncertain by about ±6 points). The band of students away, built as the estimate's is (with the weekend factor known here), contains the truth in 96–100% of the weeks of 2019 and 92–100% of the mid-summer and Thanksgiving weeks.
  * **If the weekend factor is wrong, the estimate is off and the intervals miss.** If students make up a 20% larger share of weekend riders than the model allows for (than the weekend factor it corrects with), the method finds 0.85 and 0.73 instead of 0.70 and 0.60, about 25,000 more students away in mid-summer than the truth, and the summer interval contains the truth only 52% of the time: a widening calibrated on a correct model cannot make up for a wrong one. The weekend factor's own interval (1.11–1.48) is part of the estimate's band for this reason.

#### Scenarios

* **What a scenario is.** A forecast is the best guess for a year with the inputs we expect; a scenario is a "what if". One input of the population model is changed, the fitted model is kept, and the weekly population is recomputed through the same steps as the estimate: the Student Presence Index, the range of students away, and the population model's estimate and band (`population_scenarios.py`). It shows how much the population would change if that input changed, not that it will, and it assumes students behave as in the years the model was fitted on. All scenarios use the academic year 2025–26 (the weeks of August 4, 2025 to July 27, 2026); the baseline reproduces `population_estimate.csv` for those weeks exactly.
* **The scenarios.**
  * *10% fewer or 10% more students*: resident college students not living with their parents (for example, international students) scaled by 0.9 or 1.1. The Census population moves with them, since it counts students where they live during the school year.
  * *Spring breaks aligned*: each institution's spring break (its run of weeks out of session from February to April, around its fullest week) moved to the week in which the most students have theirs, the week of March 2, 2026. Other days off stay where they are.
  * *Remote fall term*: the eight institutions that taught fall 2020 remotely (Berklee, Bunker Hill Community College, Emmanuel, Harvard, Lesley, MIT, Simmons and UMass Boston; 68,243 in-person students, 32%) teach fall 2025 remotely, so their students are out of session from the start of term to December.

| Scenario (2025–26) | Average population | Most students away in a week | Most away, February–April | Average away, September–December | Average change from baseline | Largest weekly change |
|---|---|---|---|---|---|---|
| Baseline | 1,795,933 | 157,859 | 57,619 | 23,466 | | |
| 10% fewer students | 1,782,786 | 144,490 | 52,739 | 21,475 | −13,147 | −18,608 |
| 10% more students | 1,809,081 | 171,228 | 62,498 | 25,456 | +13,148 | +18,608 |
| Spring breaks aligned | 1,795,940 | 157,859 | 148,514 | 23,466 | +7 | −90,895 and +50,434 |
| Remote fall term | 1,781,645 | 157,859 | 57,619 | 67,026 | −14,289 | −50,751 |

* **Enrollment moves the term more than the summer.** With 10% fewer students the population is 18,608 lower in every week in session and 13,147 lower on average, but only 6,346 lower in summer and 5,239 lower in the week after Christmas: the Census counts those students where they live during the school year, and most of them are away in those weeks anyway.
* **Aligned spring breaks make one deep dip instead of three shallow ones.** In the week of March 2, 148,514 students would be away (95% interval 84,648–176,400), more than in a summer week (144,791) and nearly as many as in the week after Christmas (157,859), and the population would be 90,895 lower than it is that week; in the weeks the breaks move from, it is up to 50,434 higher. The time away over the spring hardly changes: where the breaks bunch, the most students who could be away caps one week a little.
* **A remote fall term empties part of every fall week.** 50,594 students (95% interval 28,837–60,409), most of the remote institutions' students not living with their parents, would be away in each regular fall week, lowering the year's average population by 14,289.
* **Limits.** Each scenario changes one input and keeps everything else, including how students behave: if every university broke the same week or a term went remote, more or fewer students might stay than in the years the model learned from. In a single long break or a remote term the band's lower end matters more, since it uses the share that leaves on breaks (as few as 41%). Only resident college students are modeled.

#### Presence evaluation

`make evaluate` checks the index against observed activity without fitting a model (`presence_evaluation.csv`):

* **Campus share of Bluebikes trips.** The weekly ratio of trips at campus-zone to away-zone stations moves with the index: the median correlation within a year is 0.85.
  * It is weak while the network was small (−0.17 in 2015, −0.13 in 2016, 0.25–0.45 in 2017–2019) and strong from 2021 (0.85–0.91), as stations filled in around campuses.
  * The housing-weighted index fits equally well (0.85), so weighting by beds does not change the fit; its use is that it counts beds.
* **MBTA.** The same ratio for gated MBTA entries correlates only weakly (median 0.30 within a year): campus-zone gated stations also serve commuters and events, and the street-level Green Line stops at Boston University and Northeastern have no gates.
* **Student riders at MBTA stations.** Across 67 study-area gated stations, entries fell a median 5.8% from full in-session weeks to full summer weeks in 2022–2025 (station-weeks with a closed day left out), and they fell more where the passenger survey finds more student riders.
  * The correlation with home-to-school trips is −0.50, with the station's semester-pass share −0.48 (−0.44 by rank, −0.40 without the three highest), and with riders aged 18–25 −0.42. JFK/UMass, Community College, and Ruggles lose 28–31% of entries in summer.
  * Middle- and high-school fares correlate too (−0.35), since school children are also away in summer. Some stations gain in summer for other reasons (e.g. Kenmore with Fenway Park, Aquarium with visitors).
* **Breaks.** In 76% of 293 break weeks, a university's campus share of study-area Bluebikes trips fell compared with full in-session weeks around it.
  * Only academic years with no estimated term or break date are used, since an estimated break week would test the estimate rather than the index; this leaves out the universities with only a 2026–27 calendar, whose breaks are still ahead.
  * Median drops: Northeastern −54% (winter) and −40% (spring); Wentworth −47% and −34%; Boston University −27% and −15%; UMass Boston −22% and −19%; Harvard −18% and −20%; MIT −12% and −21%; Berklee about −10% (few verified weeks); Thanksgiving dips are smaller.
  * Two colleges show no winter drop: Bunker Hill Community College (+9%), a commuter college whose nearby Charlestown stations mostly serve other riders, and Suffolk (+15%), whose campus is downtown next to Government Center and Boston Common, where stations also serve commuters and visitors.
  * Tufts is left out of this check: its campus stations inside the study area are at its Chinatown health-sciences campus, which follows other calendars than the main-campus calendar collected (with it included, its share rose during breaks).
* **Street counts.** At locations counted on weekdays in both June and September of the same year, bicycle counts rose a median 17.5% at campus locations, 11% nearby and 1% elsewhere, while motor-vehicle counts barely changed (+3%, +1%, −1%).
* **Summer.** Adding the summer-break share to the index (with a constant per year) raises the share of weekly variation explained from 0.78 to 0.81 for Bluebikes and from 0.38 to 0.43 for MBTA; campus areas stay busier in summer than in winter breaks with the same index.
* **College-age riders.** From 2015 to April 2020 the trip files give each rider's birth year.
  * Across the study area, the share of trips by riders aged 18–24 is almost the same in full in-session weeks and in summer (18.5% and 18.1%; median correlation with the index within a year 0.10).
  * College-age riders ride less near campuses in summer (24.1% of campus-zone trips in session, 21.7% in summer) and more elsewhere (15.1% and 16.1% away from campuses), so rider age shows where students ride rather than how many are here.
  * Many 18–24-year-olds are not students, and the age is known for 99% of members' trips but only about half of casual riders' (June 2019).
* **Bounds on students away.**
  * *Study area.* In summer 2019 between about 73,000 (4.0% of residents, students in university housing) and 190,000 (10.3%) resident students could be away; in the week after Christmas 2019 between 36,000 and 189,000; over Thanksgiving 2019 between 1,800 and 135,000, and in the week of 11 March 2019, when many universities had spring break, between 1,800 and 72,000, because most residence halls stay open over those breaks. The 117,000 students living elsewhere, mostly renting, make most of the difference.
  * *Cambridge.* The students living in dormitories in fall 2019 (14,322, who almost all leave in summer) were 49% of the 29,279 resident college students counted by the ACS, so between 49% and 100% of Cambridge's resident students leave for the summer.
  * *Boston.* In fall 2019, 40,933 students lived in university housing and 9,197 lived with their parents (City of Boston student housing report), against 105,609 resident college students counted by the ACS: between 39% and 91% of Boston's resident students could leave, the upper end assuming everyone not living with their parents leaves. The 36,288 students renting off campus, a third of Boston's resident students, make most of the difference.
  * *Beds.* Across the whole study area, IPEDS lists 79,191 beds in university housing in fall 2019 (every institution reporting), 35% of the resident college students. Dormitory residents are the students most likely to leave in summer, but beds are capacity rather than occupancy, some dormitories host summer programs, and IPEDS counts an institution's beds wherever its housing is (Harvard's housing in Boston counts with Harvard), so this describes the study area as a whole rather than any one town.

#### Downloads

* Downloads come from s3.amazonaws.com (Bluebikes), www.arcgis.com and services1.arcgis.com (MBTA entries and passenger survey), api-v3.mbta.com (station locations), www2.census.gov (population, ACS, microdata, boundaries), nces.ed.gov (IPEDS), www.ncei.noaa.gov (weather), data.boston.gov and data.cambridgema.gov (city data), and registry.npmjs.org (the chart libraries embedded in the figure pages).

* Every download retries dropped connections and temporary server errors, and files kept for later runs (IPEDS, Census population, ACS, town boundaries, MBTA and Bluebikes) are checked before they are saved: some sites answer with a web page when they are down (NCES shows a maintenance page, with a success status, for every IPEDS file), so a file that is not the expected zip or CSV stops the step with an error instead of being cached.
* Files downloaded again on every run fail to parse instead. The MBTA historical file is downloaded again whenever MBTA publishes a newer version.

---

## Travel Models in Detail

The methods, tests and results of all the models are summarized under [Modeling Methods and Results](../README.md#modeling-methods-and-evaluation), and the population model, joint model, simulation and checks are detailed under [Processing decisions](#processing-decisions). This section details the travel models.

All models work on the logarithm of weekly activity, so effects are percentages. The four targets are the campus ratio (campus-zone over away-zone activity) and the study-area total, for Bluebikes trips and for MBTA gated entries; each target has its own fit. The model comparison is in `src/boston_breathes/model_comparison.py`, the pooled campus model in `campus_panel.py`.

| | Linear, smooth season | Linear, week of year | Gradient boosting | Pooled campus model |
|---|---|---|---|---|
| Method | Least squares | Least squares | Gradient-boosted regression trees (scikit-learn `HistGradientBoostingRegressor`) | Least squares with a level for every week (shared by all campuses) and for every campus in each year |
| Predicts | One of the four targets | One of the four targets | One of the four targets | Activity at one campus (Bluebikes) or campus station (MBTA) |
| Named inputs | 16: 8 weather, 2 holiday, network size, 4 season terms, the index | 12: 8 weather, 2 holiday, network size, the index | 13: 7 weather, 2 holiday, network size, day of the year, year, the index | 1 (the share of the week the campus's own university is in session), plus stations in use for Bluebikes |
| Also estimated | A level for each of the 12 years: 28 coefficients | 12 year levels and 52 week-of-year levels: 76 columns, 75 of them independent | 300 trees of at most 15 leaves, each leaf at least 10 weeks; learning rate 0.05, L2 penalty 1.0; fixed settings, not tuned | A level for each week and each campus-year |
| Data | 534 weeks (Bluebikes), 538 (MBTA), outside the COVID-19 period | Same | Same | 8,988 campus-weeks (Bluebikes), 7,047 station-weeks (MBTA); 2,480 and 2,107 with verified calendars only |
| Tested | Each month held out in turn, with the week on either side | Same | Same | Standard errors clustered by month; other universities' calendars as a placebo |

A third smooth-season model also adds the summer-break share (17 named inputs); it does not improve on the model with the index.

**The inputs.**
* *Weather (8)*: weekly mean temperature and its square (so the effect of warmth can level off), days with rain (1 mm or more), days with heavy rain (10 mm or more), days with snow (1 cm or more), days below freezing all day, hot days (a maximum of 30 °C or more), and mean wind speed. The trees use temperature without its square.
* *Holidays (2)*: the number of federal and Massachusetts holidays in the week, and whether the week has a day from December 24 to January 1.
* *Network size (1)*: the logarithm of Bluebikes stations in use per day (for the campus ratio, campus-zone over away-zone stations), or of MBTA gated stations with entries that week.
* *Season*: two pairs of sine and cosine terms of the day of the year (a smooth annual cycle), or a level for each week of the year; the trees get the day of the year as a number.
* *Year*: a level for each calendar year in the linear models (network growth, COVID-19 recovery); a number for the trees.
* *Students (1)*: the Student Presence Index.

**How accurate they are.** R² and typical error (the RMSE of the logarithm as a percentage) on held-out months, all models with the index:

| Target | Linear, smooth season | Linear, week of year | Gradient boosting |
|---|---|---|---|
| Bluebikes campus ratio | 0.80, 9.3% | 0.79, 9.6% | 0.91, 6.3% |
| MBTA campus ratio | 0.27, 6.6% | 0.26, 6.6% | 0.32, 6.3% |
| Bluebikes total | 0.98, 13.6% | 0.98, 13.1% | 0.97, 18.3% |
| MBTA total | 0.94, 8.2% | 0.95, 7.6% | 0.94, 8.4% |

By kind of week (typical error; 231 fully in-session weeks, 164 summer weeks and 139 break weeks for Bluebikes):

| Target | Model | Fully in session | Summer | Breaks |
|---|---|---|---|---|
| Bluebikes campus ratio | linear, smooth season | 9.2% | 8.4% | 10.4% |
| Bluebikes campus ratio | linear, week of year | 9.5% | 8.6% | 10.7% |
| Bluebikes campus ratio | gradient boosting | 5.7% | 4.3% | 8.7% |
| MBTA campus ratio | linear, smooth season | 6.5% | 6.3% | 7.0% |
| MBTA campus ratio | linear, week of year | 6.5% | 6.3% | 7.1% |
| MBTA campus ratio | gradient boosting | 6.5% | 6.1% | 6.4% |
| Bluebikes total | linear, smooth season | 13.2% | 11.6% | 16.4% |
| Bluebikes total | linear, week of year | 13.3% | 11.4% | 14.5% |
| Bluebikes total | gradient boosting | 21.3% | 8.5% | 21.9% |
| MBTA total | linear, smooth season | 7.6% | 7.7% | 9.5% |
| MBTA total | linear, week of year | 7.3% | 7.4% | 8.3% |
| MBTA total | gradient boosting | 7.8% | 8.1% | 9.5% |

**What the inputs do** (linear, smooth season, with the index; the change in the target for each input, in bold where the 95% interval excludes zero; the temperature row combines temperature and its square, and its interval allows for the two being estimated together; network size as an elasticity, the percentage change in activity for a 1% change in stations):

| Input | Bluebikes campus ratio | MBTA campus ratio | Bluebikes total | MBTA total |
|---|---|---|---|---|
| Mean temperature 0 → 20 °C | **+11.5%** | −2.0% | **+88.6%** | +5.0% |
| Each rain day | −0.3% | −0.0% | **−3.5%** | −0.3% |
| Each heavy-rain day (on top) | +0.2% | −0.7% | **−2.2%** | −0.6% |
| Each snow day | −1.3% | +0.8% | −4.0% | −1.5% |
| Each holiday | +0.2% | **+1.5%** | **−5.4%** | **−8.0%** |
| Christmas–New Year week | **−10.1%** | −0.9% | **−34.3%** | **−18.5%** |
| Student Presence Index 0 → 1 | **+18.1%** | **+5.3%** | **+28.7%** | **+15.7%** |
| Network size (elasticity) | **0.61** | −0.23 | **1.08** | **1.62** |

* *Bluebikes total* behaves as a bike-share system should: going from 0 °C to 20 °C almost doubles trips (+89%), with no further gain above about 24 °C; each rain day costs 3.5%, each holiday 5%, and the Christmas–New Year weeks 34%. Trips grow slightly faster than the network (elasticity 1.08).
* *MBTA total* barely reacts to weather (0 °C to 20 °C: +5%, interval −2% to +12%): holidays (−8% each) and the Christmas–New Year weeks (−18.5%) matter most, and closed stations cost more than their share of the network (elasticity 1.6), probably because closures come with line shutdowns.
* *The campus ratios* cancel most of the weather, as intended. The Bluebikes campus share rises a little in warm weather (+12% from 0 °C to 20 °C) and falls in the Christmas–New Year weeks (−10%); after the year levels, the index is its strongest input. Little explains the MBTA campus share apart from the index and holidays, hence its low R².

**What the trees rely on** (permutation importance on held-out months: the increase in error when one input is shuffled across weeks):

| Target | Inputs whose shuffling raises held-out error most |
|---|---|
| Bluebikes campus ratio | year (+262%), presence_index (+66%), tmean_c (+41%), network (+36%), day_of_year (+25%) |
| MBTA campus ratio | year (+38%), network (+11%), day_of_year (+7%), tmean_c (+5%), presence_index (+4%) |
| Bluebikes total | network (+556%), tmean_c (+222%), presence_index (+18%), day_of_year (+15%), rain_days (+4%) |
| MBTA total | year (+465%), presence_index (+32%), network (+30%), n_holidays (+28%), tmean_c (+18%) |

The year and network size come first because they set the level of activity, which changes far more between years than within them. After them, temperature and the index matter most for the Bluebikes targets, and the index and holidays for the MBTA total. Once the trees have the index they lean on it instead of learning the calendar from the date, so shuffling it costs more than the index adds (1.6–4.5% lower error than the trees without it).

**Where they go wrong.**
* Breaks are the hardest weeks for every target: for the Bluebikes campus share, the typical error is 10.4% in breaks against 8.4% in summer with the smooth-season model, and 8.7% against 4.3% with the trees. Breaks are short, often start or end in the middle of a week, and differ between universities, which the study-area index averages.
* The trees fit the campus ratios best, especially in summer, but the Bluebikes total worst (21% typical error in session weeks): trees cannot carry the network's growth into a held-out month the way a linear term in network size can.
* No model explains more than about a third of the MBTA campus share.

### Combined models

`ensembles.py` asks whether combining models predicts travel better than the best single model, on the same held-out data as the comparisons it extends. The README summarizes it under [Performance](../README.md#performance).

**Travel targets** (each calendar month held out in turn, with the week on either side; every model with the index). Single models: the week-of-year linear model and gradient boosting above, and a random forest on the trees' inputs (300 trees, at least 5 weeks a leaf). Combinations:
* *average*: the mean of the linear model and the trees;
* *stacked*: their weighted mean, with non-negative weights fitted on predictions for the training weeks made without those weeks (5 folds of calendar months, inside the training weeks only), scaled to add up to one; and the same with the random forest;
* *hybrid*: the linear model, plus gradient boosting fitted to its residuals on the training weeks;
* *chosen by cross-validation*: whichever of the linear model and the trees has the smaller error on those out-of-fold predictions.

Typical error on held-out months; in brackets, the change in RMSE against the better of the linear model and the trees for that target (in bold), with its 95% interval from resampling months (`ensemble_targets_accuracy.csv`). The better model is picked after seeing the errors, which favours it; the combinations never see the held-out month.

| Model | Bluebikes campus ratio | MBTA campus ratio | Bluebikes total | MBTA total |
|---|---|---|---|---|
| Linear, week of year | 9.6% (+50.5; +32.4 to +70.9) | 6.6% (+3.7; −5.7 to +14.9) | **13.1%** | **7.6%** |
| Gradient boosting | **6.3%** | **6.3%** | 18.3% (+36.7; +17.6 to +52.9) | 8.3% (+9.0; −0.0 to +18.7) |
| Random forest | 7.3% (+15.6; +9.0 to +22.9) | 6.0% (−4.9; −9.4 to −1.1) | 23.9% (+74.3; +51.7 to +96.0) | 9.5% (+22.9; +11.2 to +36.2) |
| Average | 6.7% (+7.1; −0.9 to +15.2) | 6.2% (−2.4; −7.6 to +3.0) | 13.6% (+3.7; −8.6 to +15.4) | 7.4% (−2.9; −8.4 to +1.8) |
| Stacked | 6.1% (−2.3; −6.3 to +1.4) | 6.3% (−1.2; −5.6 to +4.0) | 12.7% (−2.6; −8.8 to +3.2) | 7.4% (−2.8; −7.4 to +1.2) |
| Stacked, with the forest | 6.2% (−1.5; −5.6 to +2.5) | 6.1% (−4.2; −9.2 to +0.4) | 12.7% (−2.6; −8.8 to +3.3) | 7.4% (−2.7; −7.4 to +1.3) |
| Hybrid | 6.3% (+1.1; −9.4 to +13.7) | 6.7% (+6.0; +0.8 to +12.9) | 12.1% (−7.0; −13.5 to −1.5) | 7.8% (+2.5; −1.0 to +6.1) |
| Chosen by cross-validation | 6.3% (+0.0; +0.0 to +0.0) | 6.7% (+4.7; +0.0 to +14.0) | 13.1% (+0.0; +0.0 to +0.0) | 7.9% (+3.4; +0.1 to +9.7) |

* **Stacking learns which model to trust for each target**: on average 0.78 on the trees for the Bluebikes campus ratio and 0.76 on the linear model for the Bluebikes total, and, with the forest available, 0.73 on the forest for the MBTA campus ratio (`ensemble_targets_weights.csv`). It beat the better single model on all four targets, by 1–3%, but no interval excludes zero.
* **The plain average needs both models to be good**: it is 7% worse than the trees on the Bluebikes campus ratio, where the linear model is far behind.
* **The hybrid is the one clear gain**, on the Bluebikes total (13.1% to 12.1%; −7.0%, −13.5% to −1.5%; in weeks not fully in session 12.9% to 11.1%): the linear model carries the network's growth and the year levels, and the trees add what the season and weather do beyond them. On the MBTA campus ratio it is worse (+6.0%).
* **The random forest alone** is the best model for the MBTA campus ratio (6.0%), and the worst for both totals, for the same reason as gradient boosting.
* **Choosing by cross-validation** picked the better model in every month for the Bluebikes targets, and too often the wrong one for the MBTA targets (+4.7% and +3.4%): when two models are close, the choice is noisy.

**Weekly signals** of the population model (subway and Bluebikes, weekdays and weekends; each validation year predicted from the years before it, and January–September 2026 from 2022–2025; the four feature sets with student inputs). Three more families: additive splines (cubic splines of each input, then ridge), extra trees (300 trees, at least 5 weeks a leaf) and a Gaussian process (a radial kernel plus white noise). Combinations, over the eight families of [Exploring models and signals](#exploring-models-and-signals) and over all eleven: the mean, a stack fitted on the training years' out-of-fold predictions (5 folds of calendar months), and the family with the smallest out-of-fold error. R² on the validation years (mean) and on the 2026 test, each with the feature set (and, for the best single model, the family) that did best on the validation years (`ensemble_signals_accuracy.csv`):

| Signal | Best single model | Mean of all eleven | Stacked, all eleven | Chosen by cross-validation |
|---|---|---|---|---|
| Subway, weekdays | 0.48, 0.55 (linear regression) | 0.46, 0.52 | 0.44, 0.51 | 0.45, 0.54 |
| Subway, weekends | 0.34, 0.35 (linear regression) | 0.32, 0.35 | 0.33, 0.33 | 0.32, 0.35 |
| Bluebikes, weekdays | 0.70, 0.62 (ridge) | 0.69, 0.69 | 0.69, 0.72 | 0.69, 0.69 |
| Bluebikes, weekends | 0.48, 0.34 (lasso) | 0.49, 0.43 | 0.44, 0.35 | 0.43, 0.37 |

* **The subway's signals are still best read by plain linear regression** on the population model's own input: the combinations, and the best of the new families (additive splines, 0.48 on weekdays; the Gaussian process, 0.32 on weekends), at best match it. The relationship is a straight line, as the population model assumes.
* **For Bluebikes, the mean of all eleven held up best on the 2026 test** (0.69 and 0.43, against 0.62 and 0.34 for the family best on the validation years), with about the same validation score. Averaging many families protects against picking one that happened to do well on three years.
* **Stacking and the cross-validated choice were less steady**: the stack was best on the Bluebikes weekday test (0.72) but among the worst on weekends (0.35). With 50 to 200 training weeks, learned weights and choices are noisy, and a plain mean is the safer combination.
* The mean of ridge and the random forest, the two best families for Bluebikes weekdays in the comparison above, did as well as the mean of all eleven (validation 0.71 and 0.49, test 0.70 and 0.42), but it was chosen after seeing those results.

**What it changes.** Nothing in the population estimate, which reads the shares away from the subway and Bluebikes slopes with a straight line, as these results support for the subway. Better predictions of travel cannot remove what limits the estimate: the weekend factor and the assumption about students who do not ride ([Limits](../README.md#limits)).
