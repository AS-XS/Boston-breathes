"""University academic calendars and weekly in-session days.

Input: data/manual/academic_calendar.csv, collected by hand from official
university calendars. One row per university, academic year and event, with
the source URL and, where the source states it, the weekday (used to catch
transcription errors). A date of "none" records an event that did not happen,
such as Boston University's spring break cancelled in 2021.

Gaps are estimated from each university's own verified years: fall events
keep their typical offset from Labor Day and spring events their typical
offset from Martin Luther King Jr. Day, which preserves weekdays and follows
how calendars shift from year to year. Events a university has no verified
date for are left missing rather than borrowed from other universities,
whose calendars differ (e.g. Northeastern's spring term ends in April, MIT's
starts in February).

A day is "in session" from the first day of classes to the end of final
exams, excluding Thanksgiving break (Wednesday to Sunday) and spring break.
Breaks reported as Monday-Friday are widened to the surrounding weekend.

Outputs in data/processed/:
  * academic_calendar.csv - every event, marked verified, estimated, none
    (did not happen) or missing
  * academic_weekly.csv   - days in session and summer-break days per
    university and week, null
    where an event needed to tell is missing
"""

import re
import statistics
from datetime import date, timedelta

import holidays
import polars as pl

from boston_breathes.paths import DATA, PROCESSED, STUDY_END, STUDY_START
from boston_breathes.weeks import build_weeks

MANUAL_PATH = DATA / "manual" / "academic_calendar.csv"

FALL_EVENTS = ["fall_classes_start", "fall_exams_end"]
SPRING_EVENTS = ["spring_classes_start", "spring_break_start", "spring_break_end", "spring_exams_end", "commencement"]
EVENTS = FALL_EVENTS + SPRING_EVENTS
NONE = "none"

# Academic years overlapping the study period.
FIRST_ACADEMIC_YEAR = STUDY_START.year - 1
LAST_ACADEMIC_YEAR = STUDY_END.year


def academic_year_label(start_year: int) -> str:
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def start_year_of(label: str) -> int:
    m = re.fullmatch(r"(\d{4})-(\d{2})", label)
    if not m or (int(m.group(1)) + 1) % 100 != int(m.group(2)):
        raise ValueError(f"Bad academic year label: {label!r}")
    return int(m.group(1))


def labor_day(year: int) -> date:
    d = date(year, 9, 1)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def mlk_day(year: int) -> date:
    first_monday = date(year, 1, 1) + timedelta(days=(7 - date(year, 1, 1).weekday()) % 7)
    return first_monday + timedelta(weeks=2)


def anchor(event: str, start_year: int) -> date:
    """Labor Day of the fall for fall events, MLK Day of the spring otherwise."""
    return labor_day(start_year) if event in FALL_EVENTS else mlk_day(start_year + 1)


# ------------------------------------------------------------------- load


def load_manual(path=MANUAL_PATH) -> pl.DataFrame:
    """Read and validate the hand-collected calendar; raise listing all problems."""
    raw = pl.read_csv(path, infer_schema=False)
    problems = []
    rows = []
    for i, r in enumerate(raw.iter_rows(named=True), start=2):  # line 1 is the header
        where = f"line {i} ({r['institution']} {r['academic_year']} {r['event']})"
        if r["event"] not in EVENTS:
            problems.append(f"{where}: unknown event")
            continue
        try:
            start_year = start_year_of(r["academic_year"])
        except ValueError as e:
            problems.append(f"{where}: {e}")
            continue
        if not (r["source_url"] or "").startswith("http"):
            problems.append(f"{where}: missing source URL")
        if r["date"] == NONE:
            if not r["note"]:
                problems.append(f"{where}: 'none' needs a note explaining why")
            rows.append({**r, "start_year": start_year, "date": None, "status": "none"})
            continue
        try:
            d = date.fromisoformat(r["date"])
        except (TypeError, ValueError):
            problems.append(f"{where}: bad date {r['date']!r}")
            continue
        if r["weekday"] and d.strftime("%A") != r["weekday"]:
            problems.append(f"{where}: {d} is a {d.strftime('%A')}, source says {r['weekday']}")
        expected_year = start_year if r["event"] in FALL_EVENTS else start_year + 1
        if d.year != expected_year:
            problems.append(f"{where}: {d} is outside the {r['academic_year']} {r['event'].split('_')[0]} term")
        rows.append({**r, "start_year": start_year, "date": d, "status": "verified"})

    df = pl.DataFrame(rows, schema_overrides={"date": pl.Date}) if rows else pl.DataFrame()
    if not df.is_empty():
        dupes = df.filter(pl.struct("unitid", "academic_year", "event").is_duplicated())
        for r in dupes.unique(["unitid", "academic_year", "event"]).iter_rows(named=True):
            problems.append(f"duplicate event: {r['institution']} {r['academic_year']} {r['event']}")
        problems += order_problems(df)
    if problems:
        raise ValueError("Academic calendar problems:\n  " + "\n  ".join(problems))
    return df.select("unitid", "institution", "academic_year", "start_year", "event", "date", "status", "source_url", "note")


def order_problems(df: pl.DataFrame) -> list[str]:
    """Events within an academic year must not run backwards."""
    problems = []
    rank = {e: i for i, e in enumerate(EVENTS)}
    for (unitid, year), group in df.filter(pl.col("date").is_not_null()).group_by("unitid", "academic_year"):
        events = sorted(group.iter_rows(named=True), key=lambda r: rank[r["event"]])
        for a, b in zip(events, events[1:]):
            if b["date"] < a["date"]:
                problems.append(f"{a['institution']} {year}: {b['event']} ({b['date']}) is before {a['event']} ({a['date']})")
    return problems


# --------------------------------------------------------------- estimate


def offsets(verified: pl.DataFrame) -> dict[tuple[str, str], list[int]]:
    """Days from the anchor holiday to each verified event, by university and event."""
    out: dict[tuple[str, str], list[int]] = {}
    for r in verified.filter(pl.col("status") == "verified").iter_rows(named=True):
        out.setdefault((r["unitid"], r["event"]), []).append((r["date"] - anchor(r["event"], r["start_year"])).days)
    return out


def estimate(offset_values: list[int], event: str, start_year: int) -> date:
    return anchor(event, start_year) + timedelta(days=statistics.median_low(offset_values))


def complete_calendar(manual: pl.DataFrame) -> pl.DataFrame:
    """Every university, academic year and event, filling gaps with estimates."""
    known = {(r["unitid"], r["start_year"], r["event"]): r for r in manual.iter_rows(named=True)}
    offs = offsets(manual)
    names = dict(manual.select("unitid", "institution").unique().iter_rows())
    rows = []
    for unitid, institution in sorted(names.items()):
        for start_year in range(FIRST_ACADEMIC_YEAR, LAST_ACADEMIC_YEAR + 1):
            for event in EVENTS:
                r = known.get((unitid, start_year, event))
                if r is not None:
                    rows.append({k: r[k] for k in ("date", "status", "source_url", "note")}
                                | {"unitid": unitid, "institution": institution, "start_year": start_year, "event": event})
                    continue
                values = offs.get((unitid, event))
                rows.append({
                    "unitid": unitid, "institution": institution, "start_year": start_year, "event": event,
                    "date": estimate(values, event, start_year) if values else None,
                    "status": "estimated" if values else "missing",
                    "source_url": None,
                    "note": f"median offset from {'Labor Day' if event in FALL_EVENTS else 'MLK Day'} over {len(values)} verified year(s)" if values else None,
                })
    return (
        pl.DataFrame(rows, schema_overrides={"date": pl.Date})
        .with_columns(pl.col("start_year").map_elements(academic_year_label, return_dtype=pl.Utf8).alias("academic_year"))
        .select("unitid", "institution", "academic_year", "event", "date", "status", "source_url", "note")
    )


def leave_one_out_errors(manual: pl.DataFrame) -> pl.DataFrame:
    """Absolute error (days) of each estimate rule against verified dates it did not see."""
    rows = []
    for r in manual.filter(pl.col("status") == "verified").iter_rows(named=True):
        others = manual.filter(
            (pl.col("status") == "verified") & (pl.col("unitid") == r["unitid"])
            & (pl.col("event") == r["event"]) & (pl.col("start_year") != r["start_year"])
        )
        values = offsets(others).get((r["unitid"], r["event"]))
        if values:
            rows.append({"institution": r["institution"], "event": r["event"],
                         "error_days": abs((estimate(values, r["event"], r["start_year"]) - r["date"]).days)})
    return pl.DataFrame(rows, schema={"institution": pl.Utf8, "event": pl.Utf8, "error_days": pl.Int64})


# ---------------------------------------------------------------- session


def widen_to_weekend(start: date, end: date) -> tuple[date, date]:
    """Monday-Friday breaks also free the weekends around them."""
    if start.weekday() == 0:
        start -= timedelta(days=2)
    if end.weekday() == 4:
        end += timedelta(days=2)
    return start, end


def is_summer(d: date) -> bool:
    """Mid-June to mid-August: no study-area university is in regular session."""
    return date(d.year, 6, 15) <= d <= date(d.year, 8, 15)


def thanksgiving_break(year: int) -> tuple[date, date]:
    day = next(d for d, name in holidays.US(years=year).items() if name == "Thanksgiving Day")
    return day - timedelta(days=1), day + timedelta(days=3)


def session_days(calendar: pl.DataFrame) -> pl.DataFrame:
    """In-session and summer-break status of every day, per university: True, False or null (unknown).

    Summer break runs from the day after spring exams end to the day before
    fall classes start. Summer terms are smaller and optional, so summer days
    are not counted as in session; they are flagged separately instead.
    """
    rows = []
    for (unitid, institution, year), group in calendar.group_by("unitid", "institution", "academic_year"):
        ev = {r["event"]: (r["date"], r["status"]) for r in group.iter_rows(named=True)}
        start_year = start_year_of(year)
        terms = []
        fall_start, fall_end = ev["fall_classes_start"][0], ev["fall_exams_end"][0]
        terms.append(("fall", date(start_year, 8, 1), date(start_year, 12, 31), fall_start, fall_end,
                      [thanksgiving_break(start_year)]))
        spring_start, spring_end = ev["spring_classes_start"][0], ev["spring_exams_end"][0]
        brk_start, brk_end = ev["spring_break_start"], ev["spring_break_end"]
        if brk_start[1] == "none":
            breaks = []
        elif brk_start[0] and brk_end[0]:
            breaks = [widen_to_weekend(brk_start[0], brk_end[0])]
        else:
            breaks = None  # break exists but its dates are unknown
        terms.append(("spring", date(start_year + 1, 1, 1), date(start_year + 1, 7, 31), spring_start, spring_end, breaks))

        for term, window_start, window_end, start, end, breaks in terms:
            d = window_start
            while d <= window_end:
                if is_summer(d):
                    status = False
                elif start is None or end is None:
                    status = None
                elif d < start or d > end:
                    status = False
                elif breaks is None:
                    status = None
                else:
                    status = not any(b0 <= d <= b1 for b0, b1 in breaks)
                edge = start if term == "fall" else end
                if is_summer(d):
                    summer = True
                elif edge is None:
                    summer = None
                else:
                    summer = d < edge if term == "fall" else d > edge
                rows.append({"unitid": unitid, "institution": institution, "date": d,
                             "in_session": status, "summer_break": summer})
                d += timedelta(days=1)
    return pl.DataFrame(rows, schema={"unitid": pl.Utf8, "institution": pl.Utf8, "date": pl.Date,
                                      "in_session": pl.Boolean, "summer_break": pl.Boolean})


def weekly_session(days: pl.DataFrame) -> pl.DataFrame:
    """Days in session and summer-break days per university and week (null if any day is unknown)."""
    weeks = build_weeks().select("week_start")
    week_start = (pl.col("date") - pl.duration(days=pl.col("date").dt.weekday() - 1)).alias("week_start")
    return (
        days.with_columns(week_start)
        .group_by("week_start", "unitid", "institution")
        .agg(
            pl.when(pl.col("in_session").null_count() == 0)
            .then(pl.col("in_session").sum())
            .alias("days_in_session"),
            pl.when(pl.col("summer_break").null_count() == 0)
            .then(pl.col("summer_break").sum())
            .alias("summer_break_days"),
            pl.len().alias("days_covered"),
        )
        .join(weeks, on="week_start", how="inner")
        .filter(pl.col("days_covered") == 7)
        .drop("days_covered")
        .sort("week_start", "unitid")
    )


def main() -> None:
    manual = load_manual()
    calendar = complete_calendar(manual)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    calendar.write_csv(PROCESSED / "academic_calendar.csv")
    weekly_session(session_days(calendar)).write_csv(PROCESSED / "academic_weekly.csv")

    print(f"Academic calendar: {len(manual)} hand-collected rows, {manual['institution'].n_unique()} universities")
    counts = calendar.group_by("institution", "status").agg(pl.len()).sort("institution", "status")
    for institution in counts["institution"].unique().sort():
        parts = counts.filter(pl.col("institution") == institution)
        print(f"  {institution:<40} " + ", ".join(f"{s} {n}" for _, s, n in parts.iter_rows()))
    errors = leave_one_out_errors(manual)
    if not errors.is_empty():
        print(f"Estimate check against held-out verified dates: median error {errors['error_days'].median():.0f} days, "
              f"{(errors['error_days'] <= 3).mean():.0%} within 3 days, max {errors['error_days'].max()} days "
              f"({len(errors)} dates)")


if __name__ == "__main__":
    main()
