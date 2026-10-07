"""Weekly timeline that every data source is joined onto.

Weeks run Monday to Sunday. Each week is labelled by its Monday (`week_start`)
and by the ISO year and week number, so weeks spanning New Year stay whole.
"""

from datetime import date, timedelta

import holidays
import polars as pl

from boston_breathes.paths import PROCESSED, STUDY_END, STUDY_START


def monday_on_or_before(d: date) -> date:
    return d - timedelta(days=d.weekday())


def build_weeks(start: date = STUDY_START, end: date = STUDY_END) -> pl.DataFrame:
    """One row per Monday-to-Sunday week covering [start, end].

    Holidays are US federal plus Massachusetts state holidays (Patriots' Day).
    """
    first = monday_on_or_before(start)
    last = monday_on_or_before(end)
    n_weeks = (last - first).days // 7 + 1
    week_starts = [first + timedelta(weeks=i) for i in range(n_weeks)]

    ma_holidays = holidays.US(subdiv="MA", years=range(first.year, last.year + 2))

    rows = []
    for ws in week_starts:
        days = [ws + timedelta(days=i) for i in range(7)]
        names = [ma_holidays[d] for d in days if d in ma_holidays]
        iso_year, iso_week, _ = ws.isocalendar()
        rows.append(
            {
                "week_start": ws,
                "week_end": days[-1],
                "iso_year": iso_year,
                "iso_week": iso_week,
                # The Thursday decides which month a week mostly falls in.
                "month": days[3].month,
                "n_holidays": len(names),
                "holiday_names": "; ".join(names),
            }
        )
    return pl.DataFrame(rows)


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    weeks = build_weeks()
    out = PROCESSED / "weeks.csv"
    weeks.write_csv(out)
    print(f"Wrote {len(weeks)} weeks to {out.relative_to(PROCESSED.parent.parent)}")


if __name__ == "__main__":
    main()
