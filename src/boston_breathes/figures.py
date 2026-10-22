"""Figures of the processed data, as PNG images and an interactive HTML page.

Each chart is defined once (Vega-Lite, through Altair) and written twice:
  * figures/<name>.png               - static image (light theme) for reports
  * figures/<section>_<title>.html   - interactive page: hover tooltips, light
                                       and dark themes, and a table under each
                                       chart with the values it shows

Section A, "What data we have":
  A1 coverage of every dataset over the study period
  A2 weekly Bluebikes trips and network size
  A3 academic calendars of the universities with collected calendars
  A4 fall enrollment of the largest institutions, in person and online only

Section B, "The Student Presence Index":
  B5 the index by week, with summer and the COVID-19 years marked
  B6 the academic year, one line per year
  B7 resident population and the most it could fall when students leave

Section C, "Does city activity follow the students?":
  C8, C9 campus activity per away activity against the index (Bluebikes, MBTA)
  C10 correlation between the two, year by year
  C11 campus activity in break weeks
  C12 street counts, September against June

Section D, "Where campuses and stations are":
  D13, D14 campus zones and the Bluebikes and MBTA stations of the study area

Colors follow the project's chart palette (validated for color-vision
deficiency); identity is never carried by color alone.

The pages are self-contained: the Vega libraries are downloaded once from the
npm registry (checked against the registry's published sha512), cached in
data/raw/vendor/, and embedded, so a page works offline.
"""

import base64
import functools
import hashlib
import html
import io
import json
import math
import tarfile
import textwrap
from datetime import date, timedelta

import altair as alt
import polars as pl
import vl_convert as vlc

from boston_breathes import evaluate_presence as ev
from boston_breathes import http
from boston_breathes.campus import CAMPUS_M, MAJOR_MIN_STUDENTS, NEAR_M
from boston_breathes.municipalities import MA_CRS, load_towns
from boston_breathes.paths import PROCESSED, RAW, ROOT, STUDY_AREA
from boston_breathes.presence import COVID_END, COVID_START, COVID_YEAR_END, fall_year

FIGURES = ROOT / "figures"
VENDOR_DIR = RAW / "vendor"
NPM_REGISTRY = "https://registry.npmjs.org"
# Library versions matching the Vega-Lite schema Altair writes.
VENDOR = [("vega", "5.30.0", "build/vega.min.js"), ("vega-lite", "5.20.1", "build/vega-lite.min.js"),
          ("vega-embed", "6.26.0", "build/vega-embed.min.js")]
alt.data_transformers.disable_max_rows()

THEMES = {
    "light": {
        "surface": "#fcfcfb", "page": "#f9f9f7", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781",
        "grid": "#e1e0d9", "axis": "#c3c2b7", "border": "rgba(11,11,11,0.10)",
        "series1": "#2a78d6", "series2": "#eb6834", "series3": "#1baf7a",
        # Ordinal blue ramp, least to most; validated light end >= 2:1 on the surface.
        "ordinal": ["#86b6ef", "#3987e5", "#184f95"],
        "neutral": "#e1e0d9",
    },
    "dark": {
        "surface": "#1a1a19", "page": "#0d0d0d", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "axis": "#383835", "border": "rgba(255,255,255,0.10)",
        "series1": "#3987e5", "series2": "#d95926", "series3": "#199e70",
        "ordinal": ["#184f95", "#3987e5", "#86b6ef"],
        "neutral": "#383835",
    },
}
FONT_HTML = 'system-ui, -apple-system, "Segoe UI", sans-serif'
FONT_PNG = "Liberation Sans"  # the system sans available to the PNG renderer
WIDTH = 760


def vega_config(t: dict, font: str) -> dict:
    """Chart chrome: recessive hairline grid and axes, text in ink tokens."""
    return {
        "background": t["surface"],
        "font": font,
        "padding": 12,
        "view": {"stroke": None},
        "axis": {
            "domainColor": t["axis"], "tickColor": t["axis"], "gridColor": t["grid"], "gridWidth": 1,
            "labelColor": t["muted"], "titleColor": t["ink2"], "labelFontSize": 11, "titleFontSize": 11,
            "titleFontWeight": "normal",
        },
        "legend": {
            "labelColor": t["ink2"], "titleColor": t["ink2"], "labelFontSize": 11, "titleFontSize": 11,
            "titleFontWeight": "normal", "orient": "top", "direction": "horizontal", "labelLimit": 420,
        },
        "header": {
            "labelColor": t["ink"], "labelFontSize": 12, "labelFontWeight": 600,
            "labelAnchor": "start", "labelAlign": "left", "titleColor": t["ink2"],
        },
        "title": {
            "color": t["ink"], "subtitleColor": t["ink2"], "anchor": "start", "fontSize": 15,
            "fontWeight": 600, "subtitleFontSize": 12, "subtitlePadding": 6, "offset": 14,
        },
        "text": {"color": t["ink2"], "font": font},
    }


def read(name: str, **kwargs) -> pl.DataFrame:
    return pl.read_csv(PROCESSED / name, try_parse_dates=True, **kwargs)


def iso(df: pl.DataFrame, *cols: str) -> pl.DataFrame:
    """Dates as ISO text, so the chart data is plain JSON."""
    return df.with_columns(pl.col(c).dt.strftime("%Y-%m-%d") for c in cols)


def runs(df: pl.DataFrame, flag: str, start: str = "week_start", days: int = 7) -> list[tuple[date, date, bool]]:
    """Contiguous runs of a boolean flag over consecutive periods: (first day, last day, flag)."""
    out = []
    for value, group in df.sort(start).with_columns(
        (pl.col(flag) != pl.col(flag).shift()).fill_null(True).cum_sum().alias("_run")
    ).group_by("_run", maintain_order=True):
        out.append((group[start].min(), group[start].max() + timedelta(days=days - 1), bool(group[flag][0])))
    return out


def academic_year_columns() -> list[pl.Expr]:
    """Academic year of each week (by its Thursday, August to July) and its place on a reference year.

    The reference year runs from August 2003 to July 2004 (a leap year), so weeks of different academic
    years line up on one month axis.
    """
    thursday = pl.col("week_start") + pl.duration(days=3)
    ref_thursday = pl.date(pl.when(thursday.dt.month() >= 8).then(2003).otherwise(2004),
                           thursday.dt.month(), thursday.dt.day())
    fy = fall_year(pl.col("week_start"))
    return [
        fy.alias("fy"),
        pl.format("{}-{}", fy, (fy + 1).mod(100).cast(pl.Utf8).str.zfill(2)).alias("academic_year"),
        (ref_thursday - pl.duration(days=3)).alias("ref_start"),
        (ref_thursday + pl.duration(days=4)).alias("ref_end"),
    ]


def month_axis(**kwargs) -> alt.Axis:
    return alt.Axis(format="%b", tickCount="month", grid=False, domain=False, labelAlign="left", **kwargs)


REFERENCE_YEAR = ["2003-08-01", "2004-08-01"]


# ------------------------------------------------------------------ A1 coverage

OBSERVED, FILLED, COUNT_DAY = "Observed", "Filled in (carried forward, held flat or estimated)", "Count day"
GROUPS = ["City activity", "Students and universities", "Resident population"]


def coverage_rows() -> pl.DataFrame:
    """Time spans covered by each dataset, from the processed files themselves."""
    rows: list[dict] = []

    def add(group, dataset, start, end, kind, detail):
        rows.append({"group": group, "dataset": dataset, "start": start, "end": end, "kind": kind, "detail": detail})

    qa = read("bluebikes_monthly_qa.csv")
    first, last = str(qa["month"].min()), str(qa["month"].max())
    bb_start = date(int(first[:4]), int(first[4:]), 1)
    bb_end = (date(int(last[:4]) + int(last[4:]) // 12, int(last[4:]) % 12 + 1, 1) - timedelta(days=1))
    add(GROUPS[0], "Bluebikes trips", bb_start, bb_end, OBSERVED, f"{len(qa)} monthly trip files")
    weekly = read("bluebikes_weekly.csv")
    age_end = weekly.filter(pl.col("age_known_trips") > 0)["week_start"].max() + timedelta(days=6)
    add(GROUPS[0], "Bluebikes rider age", bb_start, age_end, OBSERVED, "birth year published until April 2020")
    stations = read("mbta_stations.csv")
    add(GROUPS[0], "MBTA gated entries", stations["first_date"].min(), stations["last_date"].max(), OBSERVED,
        f"{len(stations)} gated stations")
    for d in read("boston_street_counts.csv")["date"].unique().sort():
        add(GROUPS[0], "Boston street counts", d, d, COUNT_DAY, "24-hour bicycle and motor-vehicle counts")
    weather = read("weather_daily.csv")
    add(GROUPS[0], "Weather (Logan Airport)", weather["date"].min(), weather["date"].max(), OBSERVED, "NOAA daily")

    presence = read("student_presence_weekly.csv")
    for s, e, imputed in runs(presence, "enrollment_imputed"):
        add(GROUPS[1], "IPEDS fall enrollment", s, e, FILLED if imputed else OBSERVED,
            "nearest published fall used" if imputed else "falls 2015 onward as published")
    tg = read("cambridge_town_gown.csv").filter(pl.col("fall_year") >= 2014)
    add(GROUPS[1], "Cambridge Town Gown reports", date(tg["fall_year"].min(), 8, 1),
        date(tg["fall_year"].max() + 1, 7, 31), OBSERVED, "Cambridge universities only, one report a year")
    calendar = read("academic_calendar.csv")
    for (year,), g in calendar.group_by("academic_year", maintain_order=True):
        start_year = int(year[:4])
        estimated = g.filter(pl.col("status") == "estimated")
        add(GROUPS[1], f"Academic calendars ({calendar['unitid'].n_unique()} universities)",
            date(start_year, 8, 1), date(start_year + 1, 7, 31),
            OBSERVED, f"{len(estimated)} of {len(g)} dates estimated" if len(estimated) else "all dates verified")
    add(GROUPS[1], "COVID-19 university policies", COVID_END + timedelta(days=1), COVID_YEAR_END, OBSERVED,
        "teaching mode and housing, fall 2020 and spring 2021")
    acs = read("student_residents_annual.csv")
    first_acs, last_acs = acs["acs_year"].min(), acs["acs_year"].max()
    acs_weeks = presence.select("week_start").with_columns(
        ~fall_year(pl.col("week_start")).is_between(first_acs, last_acs).alias("outside")
    )
    for s, e, outside in runs(acs_weeks, "outside"):
        add(GROUPS[1], "ACS resident students", s, e, FILLED if outside else OBSERVED,
            "nearest 5-year estimate used" if outside else f"5-year estimates {first_acs - 4}-{first_acs} to {last_acs - 4}-{last_acs}")

    population = read("population_weekly.csv")
    for s, e, extrapolated in runs(population, "extrapolated"):
        add(GROUPS[2], "Census population estimates", s, e, FILLED if extrapolated else OBSERVED,
            "held at the latest July 1 estimate" if extrapolated else "interpolated between July 1 estimates")

    lo, hi = date(2015, 1, 1), date(2026, 12, 31)
    return (
        pl.DataFrame(rows)
        .with_columns(pl.col("start").clip(lo, hi), pl.col("end").clip(lo, hi))
        .filter(pl.col("start") <= pl.col("end"))
    )


def coverage_chart(t: dict) -> alt.TopLevelMixin:
    df = iso(coverage_rows(), "start", "end")
    order = list(dict.fromkeys(df["dataset"].to_list()))
    base = alt.Chart(df).encode(
        y=alt.Y("dataset:N", sort=order, title=None, axis=alt.Axis(labelColor=t["ink2"], labelLimit=240, ticks=False, domain=False)),
        tooltip=[alt.Tooltip("dataset:N", title="Dataset"), alt.Tooltip("kind:N", title="Coverage"),
                 alt.Tooltip("start:T", title="From", format="%b %d, %Y"),
                 alt.Tooltip("end:T", title="To", format="%b %d, %Y"), alt.Tooltip("detail:N", title="Note")],
    )
    color = alt.Color("kind:N", title=None, scale=alt.Scale(domain=[OBSERVED, FILLED, COUNT_DAY],
                                                            range=[t["series1"], t["ordinal"][0], t["series1"]]),
                      legend=alt.Legend(values=[OBSERVED, FILLED], labelLimit=420))
    x = alt.X("start:T", title=None, scale=alt.Scale(domain=["2015-01-01", "2026-12-31"]),
              axis=alt.Axis(format="%Y", tickCount="year", grid=True, labelAlign="left", labelOffset=4))
    bars = base.transform_filter(alt.datum.kind != COUNT_DAY).mark_bar(
        height=12, cornerRadius=4, stroke=t["surface"], strokeWidth=2
    ).encode(x=x, x2="end:T", color=color)
    ticks = base.transform_filter(alt.datum.kind == COUNT_DAY).mark_tick(
        thickness=2, size=14, color=t["series1"]
    ).encode(x=x)
    return (
        alt.layer(bars, ticks)
        .properties(width=WIDTH - 260, height=alt.Step(24))
        .facet(row=alt.Row("group:N", sort=GROUPS, title=None,
                           header=alt.Header(labelOrient="top", labelPadding=6)))
        .resolve_scale(y="independent")
        .properties(spacing=10)
    )


def coverage_table() -> pl.DataFrame:
    df = coverage_rows()
    days = df.filter(pl.col("kind") == COUNT_DAY)
    spans = df.filter(pl.col("kind") != COUNT_DAY).select("group", "dataset", "kind", "start", "end", "detail")
    count_row = pl.DataFrame({
        "group": [GROUPS[0]], "dataset": ["Boston street counts"], "kind": [f"{len(days)} count days"],
        "start": [days["start"].min()], "end": [days["end"].max()], "detail": ["about 90 locations, a few days a year"],
    })
    return pl.concat([spans, count_row]).rename(
        {"group": "Group", "dataset": "Dataset", "kind": "Coverage", "start": "From", "end": "To", "detail": "Note"}
    )


# ------------------------------------------------------------------ A2 Bluebikes


COMPACT = "datum.value == 0 ? '0' : replace(format(datum.value, '~s'), 'k', 'K')"


YEAR_AXIS = alt.Axis(format="%Y", tickCount="year")
STUDY_X = alt.Scale(domain=["2014-12-29", "2026-12-31"])  # the weekly timeline, without a stray 2027 tick
WEEK_TOOLTIP = alt.Tooltip("week_start:T", title="Week of", format="%b %d, %Y")


def crosshair(df: pl.DataFrame, name: str, tooltip: list, t: dict, height: int, scale: alt.Scale | None = None):
    """Hovering anywhere in a week's column selects the week: an invisible column per week carries the
    tooltip, and a hairline marks the week. Returns (columns, rule, selection)."""
    hover = alt.selection_point(name=name, on="pointerover", nearest=False, fields=["week_start"],
                                empty=False, clear="pointerout")
    columns = alt.Chart(df).mark_bar(opacity=0, clip=True).encode(
        x=alt.X("week_start:T", title=None, axis=YEAR_AXIS, scale=scale or alt.Undefined), x2="week_end:T",
        y=alt.value(0), y2=alt.value(height), tooltip=tooltip,
    ).add_params(hover)
    rule = alt.Chart(df).mark_rule(color=t["muted"], strokeWidth=1).encode(
        x="week_start:T", opacity=alt.condition(hover, alt.value(1), alt.value(0))
    )
    return columns, rule, hover


def week_hover(df: pl.DataFrame, value: str, label: str, fmt: str, t: dict, color: str, height: int):
    """Line with a crosshair: hovering anywhere in a week's column shows its value."""
    base = alt.Chart(df).encode(x=alt.X("week_start:T", title=None, axis=YEAR_AXIS))
    line = base.mark_line(strokeWidth=2, strokeJoin="round", strokeCap="round", color=color).encode(
        y=alt.Y(f"{value}:Q", title=label, axis=alt.Axis(labelExpr=fmt, tickCount=4, domain=False, ticks=False,
                                                        minExtent=40))
    )
    columns, rule, hover = crosshair(
        df, f"hover_{value}", [WEEK_TOOLTIP, alt.Tooltip(f"{value}:Q", title=label, format=",.0f")], t, height)
    dot = base.mark_circle(size=70, color=color, stroke=t["surface"], strokeWidth=2, opacity=1).encode(
        y=f"{value}:Q"
    ).transform_filter(hover)
    last = df.tail(1)
    end_label = alt.Chart(last).mark_text(align="left", dx=6, fontSize=11, color=t["ink2"]).encode(
        x="week_start:T", y=f"{value}:Q", text=alt.Text(f"{value}:Q", format=",.0f")
    )
    return alt.layer(columns, line, rule, dot, end_label).properties(width=WIDTH - 120, height=height)


def lockdown_band(t: dict, height: int, label: bool = True):
    band = pl.DataFrame({"start": [COVID_START.isoformat()], "end": [COVID_END.isoformat()], "label": ["COVID-19 lockdown"]})
    rect = alt.Chart(band).mark_rect(color=t["neutral"], opacity=0.6).encode(
        x="start:T", x2="end:T", y=alt.value(0), y2=alt.value(height)
    )
    if not label:
        return rect
    text = alt.Chart(band).mark_text(align="left", baseline="top", dx=4, dy=4, fontSize=10, color=t["muted"]).encode(
        x="start:T", y=alt.value(0), text="label:N"
    )
    return rect + text


def bluebikes_data() -> pl.DataFrame:
    df = read("bluebikes_weekly.csv").filter(pl.col("complete_week")).select(
        "week_start", "trips", "avg_daily_active_stations"
    ).with_columns((pl.col("week_start") + timedelta(days=7)).alias("week_end"))
    return iso(df, "week_start", "week_end")


def bluebikes_chart(t: dict) -> alt.TopLevelMixin:
    df = bluebikes_data()
    trips = alt.layer(lockdown_band(t, 220), week_hover(df, "trips", "Trips per week", COMPACT, t, t["series1"], 220))
    stations = alt.layer(lockdown_band(t, 120, label=False), week_hover(
        df, "avg_daily_active_stations", "Stations in use per day", COMPACT, t, t["series1"], 120))
    return alt.vconcat(
        trips.properties(title=alt.Title("Weekly trips", fontSize=12, fontWeight=600, color=t["ink"])),
        stations.properties(title=alt.Title("Network size: average stations in use per day", fontSize=12,
                                            fontWeight=600, color=t["ink"])),
        spacing=18,
    ).resolve_scale(x="shared")


def bluebikes_table() -> pl.DataFrame:
    df = read("bluebikes_weekly.csv").filter(pl.col("complete_week"))
    return (
        df.group_by(pl.col("week_start").dt.year().alias("Year"))
        .agg(pl.len().alias("Complete weeks"), pl.col("trips").sum().alias("Trips"),
             pl.col("trips").max().alias("Busiest week"),
             pl.col("avg_daily_active_stations").mean().round(0).alias("Stations in use per day (average)"))
        .sort("Year")
    )


# ------------------------------------------------------------------ A3 calendars

IN_SESSION, PARTIAL, BREAK, SUMMER = "In session all week", "In session part of the week", "Break", "Summer break"
def calendar_order() -> list[str]:
    """Universities with collected calendars, largest (in-person students in the latest fall) first."""
    collected = read("academic_calendar.csv", schema_overrides={"unitid": pl.Utf8}).select(
        "unitid", "institution").unique()
    e = read("enrollment_annual.csv", schema_overrides={"unitid": pl.Utf8})
    latest = e.filter(pl.col("fall_year") == e["fall_year"].max()).select("unitid", "in_person")
    return (collected.join(latest, on="unitid", how="left")
            .sort("in_person", "institution", descending=[True, False], nulls_last=True)["institution"].to_list())


def calendar_coverage_note() -> str:
    e = read("enrollment_annual.csv", schema_overrides={"unitid": pl.Utf8})
    year = e["fall_year"].max()
    latest = e.filter(pl.col("fall_year") == year)
    collected = set(read("academic_calendar.csv", schema_overrides={"unitid": pl.Utf8})["unitid"])
    share = latest.filter(pl.col("unitid").is_in(list(collected)))["in_person"].sum() / latest["in_person"].sum()
    return (f"{len(collected)} universities, with {share:.0%} of fall {year} in-person students, have collected "
            "calendars; the other institutions follow their enrollment-weighted average. Largest first.")


def calendar_data() -> pl.DataFrame:
    weekly = read("academic_weekly.csv", schema_overrides={"unitid": pl.Utf8})
    calendar = read("academic_calendar.csv", schema_overrides={"unitid": pl.Utf8})
    estimated = (
        calendar.filter(pl.col("status") == "estimated")
        .group_by("unitid", "academic_year")
        .agg(pl.col("event").str.replace_all("_", " ").sort().str.join(", ").alias("estimated_events"))
    )
    return (
        weekly.with_columns(academic_year_columns())
        .join(estimated, on=["unitid", "academic_year"], how="left")
        .with_columns(
            pl.when(pl.col("days_in_session") == 7).then(pl.lit(IN_SESSION))
            .when(pl.col("days_in_session") > 0).then(pl.lit(PARTIAL))
            .when(pl.col("summer_break_days") > 0).then(pl.lit(SUMMER))
            .otherwise(pl.lit(BREAK)).alias("state"),
            (pl.col("week_start") + pl.duration(days=6)).alias("week_end"),
            (pl.col("academic_year").str.replace("-", "–")
             + pl.when(pl.col("estimated_events").is_not_null()).then(pl.lit(" *")).otherwise(pl.lit("")))
            .alias("year_label"),
            pl.col("estimated_events").fill_null("none (all verified)"),
        )
    )


def calendar_chart(t: dict) -> alt.TopLevelMixin:
    df = iso(calendar_data(), "week_start", "ref_start", "ref_end")
    panels = []
    for name in calendar_order():
        sub = df.filter(pl.col("institution") == name)
        labels = sub.select("academic_year", "year_label").unique().sort("academic_year")["year_label"].to_list()
        # The estimated dates are listed once per year and looked up, not repeated on every week.
        years = sub.select("year_label", "estimated_events").unique().sort("year_label")
        weeks = sub.select("year_label", "week_start", "ref_start", "ref_end", "state", "days_in_session")
        panels.append(
            alt.Chart(weeks).transform_lookup(
                lookup="year_label", from_=alt.LookupData(years, "year_label", ["estimated_events"])
            ).transform_calculate(institution=json.dumps(name))
            .mark_rect(stroke=t["surface"], strokeWidth=1).encode(
                x=alt.X("ref_start:T", title=None, scale=alt.Scale(domain=REFERENCE_YEAR), axis=month_axis()),
                x2="ref_end:T",
                y=alt.Y("year_label:N", sort=labels, title=None,
                        axis=alt.Axis(ticks=False, domain=False, labelColor=t["ink2"], labelFontSize=10)),
                color=alt.Color("state:N", title=None,
                                scale=alt.Scale(domain=[IN_SESSION, PARTIAL, BREAK, SUMMER],
                                                range=[t["ordinal"][2], t["ordinal"][1], t["ordinal"][0], t["neutral"]])),
                tooltip=[alt.Tooltip("institution:N", title="University"),
                         alt.Tooltip("week_start:T", title="Week of", format="%b %d, %Y"),
                         alt.Tooltip("state:N", title="Status"),
                         alt.Tooltip("days_in_session:Q", title="Days in session"),
                         alt.Tooltip("estimated_events:N", title="Estimated dates this year")],
            ).properties(width=330, height=alt.Step(11), title=alt.Title(name, fontSize=12, fontWeight=600,
                                                                          color=t["ink"]))
        )
    rows = [alt.hconcat(*panels[i:i + 2], spacing=24) for i in range(0, len(panels), 2)]
    return alt.vconcat(*rows, spacing=18).resolve_scale(color="shared")


def calendar_table() -> pl.DataFrame:
    df = calendar_data()
    return (
        df.group_by("institution", "academic_year")
        .agg((pl.col("state") == IN_SESSION).sum().alias("Weeks fully in session"),
             (pl.col("state") == PARTIAL).sum().alias("Weeks partly in session"),
             (pl.col("state") == BREAK).sum().alias("Break weeks"),
             (pl.col("state") == SUMMER).sum().alias("Summer weeks"),
             pl.col("estimated_events").first().alias("Estimated dates"))
        .sort("institution", "academic_year")
        .rename({"institution": "University", "academic_year": "Academic year"})
    )


# ------------------------------------------------------------------ A4 enrollment

TOP_N = 8
SHORT_NAMES = {"University of Massachusetts-Boston": "UMass Boston", "Massachusetts Institute of Technology": "MIT"}
OTHER = "All other institutions"
IN_PERSON, ONLINE = "In person", "Online only"


def enrollment_data() -> pl.DataFrame:
    e = read("enrollment_annual.csv", schema_overrides={"unitid": pl.Utf8}).with_columns(
        pl.when(pl.col("unitid").is_in(["167358", "482705"])).then(pl.lit("Northeastern (2 IPEDS records)"))
        .otherwise(pl.col("name").replace(SHORT_NAMES)).alias("institution"),
        pl.col("distance_only").fill_null(0),
    )
    size = e.filter(pl.col("fall_year") == 2019).group_by("institution").agg(pl.col("total").sum()).sort(
        "total", descending=True)
    top = size["institution"].head(TOP_N).to_list()
    n_other = e.filter(~pl.col("institution").is_in(top))["institution"].n_unique()
    grouped = e.with_columns(
        pl.when(pl.col("institution").is_in(top)).then(pl.col("institution"))
        .otherwise(pl.lit(f"{OTHER} ({n_other})")).alias("institution")
    ).group_by("institution", "fall_year").agg(
        pl.col("in_person").sum(), pl.col("distance_only").sum().alias("online_only"), pl.col("total").sum()
    )
    order = top + [f"{OTHER} ({n_other})"]
    return grouped.with_columns(
        pl.col("institution").replace_strict({n: i for i, n in enumerate(order)}).alias("rank")
    ).sort("rank", "fall_year")


def enrollment_chart(t: dict) -> alt.TopLevelMixin:
    df = enrollment_data()
    order = df.sort("rank")["institution"].unique(maintain_order=True).to_list()
    tooltip = [alt.Tooltip("institution:N", title="Institution"), alt.Tooltip("fall_year:O", title="Fall"),
               alt.Tooltip("in_person:Q", title=IN_PERSON, format=","),
               alt.Tooltip("online_only:Q", title=ONLINE, format=","), alt.Tooltip("total:Q", title="Total", format=",")]
    legend_scale = alt.Scale(domain=[IN_PERSON, ONLINE], range=[t["series1"], t["series2"]])
    panels = []
    for name in order:
        sub = df.filter(pl.col("institution") == name).with_columns(
            pl.lit(IN_PERSON).alias("k1"), pl.lit(ONLINE).alias("k2"))
        base = alt.Chart(sub).encode(
            x=alt.X("fall_year:O", title=None, axis=alt.Axis(labelAngle=0, labelExpr="'’' + slice(datum.label, 2)",
                                                            ticks=False, domainColor=t["axis"])),
            tooltip=tooltip,
        )
        y = alt.Y("in_person:Q", title=None, axis=alt.Axis(labelExpr=COMPACT, tickCount=3, domain=False, ticks=False,
                                                          minExtent=34, labelFlush=True))
        inner = base.mark_bar(size=14, stroke=t["surface"], strokeWidth=2).encode(
            y=y, color=alt.Color("k1:N", title=None, scale=legend_scale))
        outer = base.mark_bar(size=14, stroke=t["surface"], strokeWidth=2, cornerRadiusTopLeft=4,
                              cornerRadiusTopRight=4).encode(
            y="in_person:Q", y2="total:Q", color=alt.Color("k2:N", title=None, scale=legend_scale))
        panels.append(alt.layer(inner, outer).properties(
            width=205, height=110, title=alt.Title(name, fontSize=11, fontWeight=600, color=t["ink"], limit=215)))
    rows = [alt.hconcat(*panels[i:i + 3], spacing=22) for i in range(0, len(panels), 3)]
    return alt.vconcat(*rows, spacing=16).resolve_scale(color="shared")


def enrollment_table() -> pl.DataFrame:
    df = enrollment_data()
    return df.select(
        pl.col("institution").alias("Institution"), pl.col("fall_year").alias("Fall"),
        pl.col("in_person").alias(IN_PERSON), pl.col("online_only").alias(ONLINE), pl.col("total").alias("Total"),
    )


# ------------------------------------------------------------------ B presence

INDEX, SUMMER_SHARE = "Student Presence Index", "Summer break (share of students)"
CENSUS, FLOOR = "Census resident population", "Lowest possible: every resident student away"
COVID_LABELS = {"normal": "Normal", "lockdown": "COVID-19 lockdown: students away",
                "remote_year": "COVID-19, 2020–21: remote or hybrid teaching"}


def presence_data() -> pl.DataFrame:
    return read("student_presence_weekly.csv").with_columns(
        (pl.col("week_start") + pl.duration(days=7)).alias("week_end"),
        pl.col("covid_period").replace_strict(COVID_LABELS).alias("covid_label"),
    )


def covid_bands(t: dict, height: int):
    """The lockdown and the 2020-21 academic year as labelled gray bands."""
    bands = pl.DataFrame({
        "start": [COVID_START.isoformat(), (COVID_END + timedelta(days=1)).isoformat()],
        "end": [COVID_END.isoformat(), COVID_YEAR_END.isoformat()],
        "label": ["COVID-19: lockdown, then the 2020–21 academic year", ""],
        "opacity": [0.9, 0.45],
    })
    rect = alt.Chart(bands).mark_rect(color=t["neutral"]).encode(
        x="start:T", x2="end:T", y=alt.value(0), y2=alt.value(height),
        opacity=alt.Opacity("opacity:Q", scale=None, legend=None),
    )
    text = alt.Chart(bands).mark_text(align="left", baseline="top", dx=3, dy=-14, fontSize=10, color=t["muted"]).encode(
        x="start:T", y=alt.value(0), text="label:N"
    )
    return rect + text


def presence_chart(t: dict) -> alt.TopLevelMixin:
    df = iso(presence_data(), "week_start", "week_end")
    height = 240
    identity = alt.Scale(domain=[INDEX, SUMMER_SHARE], range=[t["series1"], t["series2"]])
    summer = alt.Chart(df).transform_calculate(kind=json.dumps(SUMMER_SHARE)).mark_rect(clip=True).encode(
        x="week_start:T", x2="week_end:T", y=alt.value(0), y2=alt.value(height),
        color=alt.Color("kind:N", title=None, scale=identity),
        opacity=alt.Opacity("summer_break_share:Q", scale=alt.Scale(domain=[0, 1], range=[0, 0.22]), legend=None),
    )
    line = alt.Chart(df).transform_calculate(kind=json.dumps(INDEX)).mark_line(
        strokeWidth=2, strokeJoin="round", strokeCap="round", clip=True).encode(
        x=alt.X("week_start:T", title=None, axis=YEAR_AXIS, scale=STUDY_X),
        y=alt.Y("presence_index:Q", title="Share of students in session", scale=alt.Scale(domain=[0, 1]),
                axis=alt.Axis(format=".0%", tickCount=5, domain=False, ticks=False)),
        color=alt.Color("kind:N", title=None, scale=identity),
    )
    columns, rule, hover = crosshair(df, "hover_presence", [
        WEEK_TOOLTIP,
        alt.Tooltip("presence_index:Q", title=INDEX, format=".2f"),
        alt.Tooltip("summer_break_share:Q", title="Share on summer break", format=".0%"),
        alt.Tooltip("students_in_session:Q", title="Students in session", format=",.0f"),
        alt.Tooltip("covid_label:N", title="Period"),
    ], t, height, STUDY_X)
    dot = alt.Chart(df).mark_circle(size=70, color=t["series1"], stroke=t["surface"], strokeWidth=2, opacity=1).encode(
        x="week_start:T", y="presence_index:Q").transform_filter(hover)
    return alt.layer(summer, covid_bands(t, height), columns, line, rule, dot).properties(
        width=WIDTH - 110, height=height)


def presence_table() -> pl.DataFrame:
    return presence_data().select(
        pl.col("week_start").alias("Week of"), pl.col("presence_index").alias(INDEX),
        pl.col("summer_break_share").alias("Share on summer break"),
        pl.col("students_in_session").cast(pl.Int64).alias("Students in session"),
        pl.col("covid_label").alias("Period"),
    )


EXCLUDED_YEARS = {"2014-15": "starts in late December", "2019-20": "COVID-19 lockdown",
                  "2020-21": "remote or hybrid teaching", "2026-27": "not over yet"}


def typical_year_data() -> pl.DataFrame:
    df = presence_data().with_columns(academic_year_columns())
    shown = df.filter(~pl.col("academic_year").is_in(list(EXCLUDED_YEARS)))
    highlight = shown["academic_year"].max()
    return shown.with_columns(
        (pl.col("academic_year") == highlight).alias("highlight"),
        pl.col("academic_year").str.replace("-", "–").alias("year_label"),
    ).sort("academic_year", "week_start")


def typical_year_chart(t: dict) -> alt.TopLevelMixin:
    df = iso(typical_year_data(), "week_start", "ref_start", "ref_end").select(
        "year_label", "week_start", "ref_start", "presence_index", "highlight")
    latest = df.filter(pl.col("highlight"))["year_label"][0]
    x = alt.X("ref_start:T", title=None, scale=alt.Scale(domain=REFERENCE_YEAR), axis=month_axis())
    y = alt.Y("presence_index:Q", title="Share of students in session", scale=alt.Scale(domain=[0, 1]),
              axis=alt.Axis(format=".0%", tickCount=5, domain=False, ticks=False))
    hover = alt.selection_point(name="hover_year", on="pointerover", nearest=True, fields=["year_label"],
                                empty=False, clear="pointerout")
    others = alt.Chart(df).transform_filter(~alt.datum.highlight).mark_line(
        strokeJoin="round", strokeCap="round").encode(
        x=x, y=y, detail="year_label:N",
        color=alt.condition(hover, alt.value(t["ink2"]), alt.value(t["axis"])),
        strokeWidth=alt.condition(hover, alt.value(2), alt.value(1.25)),
    )
    current = alt.Chart(df).transform_filter(alt.datum.highlight).mark_line(
        strokeWidth=2, strokeJoin="round", strokeCap="round", color=t["series1"]).encode(x=x, y=y)
    points = alt.Chart(df).mark_point(opacity=0, size=200).encode(
        x=x, y=y,
        tooltip=[alt.Tooltip("year_label:N", title="Academic year"), WEEK_TOOLTIP,
                 alt.Tooltip("presence_index:Q", title=INDEX, format=".2f")],
    ).add_params(hover)
    end = df.filter(pl.col("highlight")).tail(1)
    label = alt.Chart(end).mark_text(align="left", dx=6, fontSize=11, color=t["ink2"]).encode(
        x="ref_start:T", y="presence_index:Q", text=alt.value(latest))
    legend_rows = pl.DataFrame({"kind": [latest, "Other years"]})
    legend = alt.Chart(legend_rows).mark_line().encode(
        color=alt.Color("kind:N", title=None, scale=alt.Scale(domain=legend_rows["kind"].to_list(),
                                                              range=[t["series1"], t["axis"]])))
    return alt.layer(others, current, points, label, legend).properties(width=WIDTH - 140, height=240)


def typical_year_table() -> pl.DataFrame:
    df = presence_data().with_columns(academic_year_columns())
    return (
        df.group_by("academic_year")
        .agg(pl.col("presence_index").mean().round(3).alias("Average index"),
             (pl.col("presence_index") >= 0.9).sum().alias("Weeks at 90% or more"),
             (pl.col("presence_index") <= 0.1).sum().alias("Weeks at 10% or less"),
             pl.len().alias("Weeks"))
        .with_columns(pl.col("academic_year").replace_strict(EXCLUDED_YEARS, default="shown").alias("In the chart"))
        .sort("academic_year")
        .rename({"academic_year": "Academic year"})
    )


def population_chart(t: dict) -> alt.TopLevelMixin:
    df = iso(presence_data(), "week_start", "week_end").select(
        "week_start", "week_end", "census_population", "min_effective_population", "max_students_away",
        "presence_index", "population_extrapolated")
    height = 260
    identity = alt.Scale(domain=[CENSUS, FLOOR], range=[t["series1"], t["series2"]])
    y_axis = alt.Axis(labelExpr=COMPACT, tickCount=5, domain=False, ticks=False)
    band = alt.Chart(df).mark_area(color=t["series2"], opacity=0.10, clip=True).encode(
        x=alt.X("week_start:T", title=None, axis=YEAR_AXIS, scale=STUDY_X),
        y=alt.Y("min_effective_population:Q", title="People", scale=alt.Scale(zero=False), axis=y_axis),
        y2="census_population:Q",
    )
    long = df.unpivot(index=["week_start"], on=["census_population", "min_effective_population"],
                      variable_name="series", value_name="population").with_columns(
        pl.col("series").replace_strict({"census_population": CENSUS, "min_effective_population": FLOOR}))
    lines = alt.Chart(long).mark_line(strokeWidth=2, strokeJoin="round", strokeCap="round", clip=True).encode(
        x="week_start:T", y="population:Q", color=alt.Color("series:N", title=None, scale=identity),
        detail="series:N")
    held = df.filter(pl.col("population_extrapolated"))
    latest_estimate = read("population_annual.csv")["year"].max()
    held_band = alt.Chart(pl.DataFrame({"start": [held["week_start"].min()], "end": [held["week_end"].max()],
                                        "label": ["Census held at the latest estimate"]})).mark_rect(
        color=t["neutral"], opacity=0.45, clip=True).encode(
        x="start:T", x2="end:T", y=alt.value(0), y2=alt.value(height))
    held_text = alt.Chart(pl.DataFrame({"end": ["2026-12-31"],
                                        "label": [f"Census held at the July {latest_estimate} estimate"]})).mark_text(
        align="right", baseline="top", dy=-14, fontSize=10, color=t["muted"]).encode(
        x="end:T", y=alt.value(0), text="label:N")
    columns, rule, hover = crosshair(df, "hover_population", [
        WEEK_TOOLTIP,
        alt.Tooltip("census_population:Q", title=CENSUS, format=",.0f"),
        alt.Tooltip("min_effective_population:Q", title="Lowest possible population", format=",.0f"),
        alt.Tooltip("max_students_away:Q", title="Most resident students away", format=",.0f"),
        alt.Tooltip("presence_index:Q", title=INDEX, format=".2f"),
    ], t, height, STUDY_X)
    return alt.layer(held_band, held_text, band, columns, lines, rule).properties(width=WIDTH - 110, height=height)


def population_table() -> pl.DataFrame:
    return (
        presence_data().group_by(pl.col("week_start").dt.year().alias("Year"))
        .agg(pl.col("census_population").mean().round(0).cast(pl.Int64).alias("Census population (average)"),
             pl.col("max_students_away").max().cast(pl.Int64).alias("Most resident students away"),
             pl.col("min_effective_population").min().cast(pl.Int64).alias("Lowest possible population"))
        .sort("Year")
    )


def dorm_bound_note() -> str:
    ev = read("presence_evaluation.csv")
    get = lambda town, metric: ev.filter(  # noqa: E731
        pl.col("subset").str.starts_with(town) & (pl.col("metric") == metric)).row(0, named=True)
    cambridge, boston_min, boston_max = (get("Cambridge", "min_share_away"), get("Boston", "min_share_away"),
                                         get("Boston", "max_share_away"))
    return (f"For a sense of scale ({cambridge['subset'].split(', ')[1].replace('-', '–')}): in Cambridge, "
            f"dormitory residents (who almost all leave in summer) were {cambridge['value']:.0%} of resident college "
            f"students. In Boston, students in university housing were {boston_min['value']:.0%}, and leaving out "
            f"students who live with their parents, at most {boston_max['value']:.0%} could leave.")


def housing_note() -> str:
    ev = read("presence_evaluation.csv")
    beds = ev.filter(pl.col("metric") == "housing_beds")
    share = ev.filter(pl.col("metric") == "housing_beds_share_of_resident_students")
    return (f"Across the study area, universities report {beds['value'][0]:,.0f} beds in university housing "
            f"({beds['subset'][0].split(', ')[1].replace('-', '–')}), {share['value'][0]:.0%} of resident college "
            "students; beds are capacity, not occupancy.")


# ------------------------------------------------------------------ C activity vs the index

RATIO_YEARS = (2019, 2025)  # the last full year before COVID-19 and the latest full year
SOURCES = {"Bluebikes": ("bluebikes_weekly_by_campus_zone.csv", "trips", ("trip", "trips")),
           "MBTA": ("mbta_weekly_by_campus_zone.csv", "entries", ("entry", "entries"))}


def evaluation() -> pl.DataFrame:
    return read("presence_evaluation.csv")


def correlation(source: str, subset: str) -> float | None:
    rows = evaluation().filter((pl.col("check") == "campus_share") & (pl.col("subset") == f"{source}, {subset}")
                               & (pl.col("metric") == "corr_log_ratio_vs_index"))
    return rows["value"][0] if len(rows) else None


def ratio_data(source: str) -> pl.DataFrame:
    """Weekly campus-to-away ratio (the evaluation's own measure) next to the presence index."""
    file, value, _ = SOURCES[source]
    ratio = ev.campus_ratio(read(file), value)
    return (
        ratio.with_columns(pl.col("log_ratio").exp().alias("ratio"))
        .join(presence_data().select("week_start", "week_end", "presence_index", "covid_period"), on="week_start")
        .filter(pl.col("week_start").dt.year().is_in(RATIO_YEARS))
        .with_columns(pl.col("week_start").dt.year().alias("year"))
        .sort("week_start")
    )


def ratio_chart_for(source: str):
    def chart(t: dict) -> alt.TopLevelMixin:
        df = iso(ratio_data(source), "week_start", "week_end")
        _, _, (one, many) = SOURCES[source]
        ratio_label = f"Campus {many} per away {one}"
        columns_by_year = []
        for year in RATIO_YEARS:
            sub = df.filter(pl.col("year") == year)
            r = correlation(source, str(year))
            x = alt.X("week_start:T", title=None, axis=alt.Axis(format="%b", tickCount="month", labelAlign="left"))
            tooltip = [WEEK_TOOLTIP, alt.Tooltip("ratio:Q", title=ratio_label, format=".3f"),
                       alt.Tooltip("presence_index:Q", title=INDEX, format=".2f")]
            panels = []
            for field, label, color, fmt, height in (
                ("ratio", f"Campus {many} ÷ away", t["series1"], ".2f", 150),
                ("presence_index", "In session", t["ink2"], ".0%", 90),
            ):
                cols, rule, hover = crosshair(sub, f"hover_{source}_{year}_{field}", tooltip, t, height)
                scale = alt.Scale(domain=[0, 1]) if field == "presence_index" else alt.Scale(zero=False)
                line = alt.Chart(sub).mark_line(strokeWidth=2, strokeJoin="round", strokeCap="round", color=color).encode(
                    x=x, y=alt.Y(f"{field}:Q", title=label, scale=scale,
                                 axis=alt.Axis(format=fmt, tickCount=3, domain=False, ticks=False, minExtent=36,
                                               titleLimit=height + 20, labelFlush=True)))
                panels.append(alt.layer(cols.encode(x=x), line, rule).properties(width=290, height=height))
            title = f"{year}: correlation {r:.2f}" if r is not None else str(year)
            columns_by_year.append(alt.vconcat(*panels, spacing=8).properties(
                title=alt.Title(title, fontSize=12, fontWeight=600, color=t["ink"])))
        return alt.hconcat(*columns_by_year, spacing=28)
    return chart


def ratio_table_for(source: str):
    def table() -> pl.DataFrame:
        _, _, (one, many) = SOURCES[source]
        return ratio_data(source).select(
            pl.col("week_start").alias("Week of"), pl.col("ratio").round(3).alias(f"Campus {many} per away {one}"),
            pl.col("presence_index").alias(INDEX))
    return table


def correlation_data() -> pl.DataFrame:
    rows = evaluation().filter((pl.col("check") == "campus_share") & (pl.col("metric") == "corr_log_ratio_vs_index"))
    return (
        rows.with_columns(pl.col("subset").str.split(", ").alias("parts"))
        .with_columns(pl.col("parts").list.get(0).alias("source"), pl.col("parts").list.get(1).alias("period"))
        .filter(pl.col("period").str.contains(r"^\d{4}$"))
        .select("source", pl.col("period").cast(pl.Int64).alias("year"), pl.col("value").alias("r"),
                pl.col("n").alias("weeks"))
        .sort("source", "year")
    )


def correlation_chart(t: dict) -> alt.TopLevelMixin:
    df = correlation_data()
    years = list(range(df["year"].min(), df["year"].max() + 1))
    missing = sorted(set(years) - set(df["year"]))
    # Lines break at years without a value, so separate runs of consecutive years are drawn apart.
    df = df.with_columns((pl.col("year").diff().fill_null(1) != 1).cum_sum().over("source").alias("run"))
    identity = alt.Scale(domain=list(SOURCES), range=[t["series1"], t["series2"]])
    x = alt.X("year:O", title=None, scale=alt.Scale(domain=years),
              axis=alt.Axis(labelAngle=0, ticks=False, domainColor=t["axis"]))
    y = alt.Y("r:Q", title="Correlation within the year", scale=alt.Scale(domain=[-0.4, 1]),
              axis=alt.Axis(format=".1f", tickCount=7, domain=False, ticks=False))
    tooltip = [alt.Tooltip("source:N", title="Source"), alt.Tooltip("year:O", title="Year"),
               alt.Tooltip("r:Q", title="Correlation", format=".2f"), alt.Tooltip("weeks:Q", title="Weeks")]
    zero = alt.Chart(pl.DataFrame({"r": [0.0]})).mark_rule(color=t["axis"], strokeWidth=1).encode(y="r:Q")
    lines = alt.Chart(df).mark_line(strokeWidth=2, strokeJoin="round", strokeCap="round").encode(
        x=x, y=y, color=alt.Color("source:N", title=None, scale=identity), detail="run:N")
    gap_text = alt.Chart(pl.DataFrame({"year": missing, "label": ["too few weeks"] * len(missing)})).mark_text(
        angle=270, fontSize=10, color=t["muted"]).encode(x=x, y=alt.value(110), text="label:N")
    dots = alt.Chart(df).mark_circle(size=70, stroke=t["surface"], strokeWidth=2, opacity=1).encode(
        x=x, y=y, color=alt.Color("source:N", title=None, scale=identity), tooltip=tooltip)
    hits = alt.Chart(df).mark_circle(size=600, opacity=0).encode(x=x, y=y, tooltip=tooltip)
    ends = df.group_by("source").agg(pl.all().sort_by("year").last())
    labels = alt.Chart(ends).mark_text(align="left", dx=8, fontSize=11, color=t["ink2"]).encode(
        x=x, y=y, text="source:N")
    return alt.layer(zero, lines, dots, hits, labels, gap_text).properties(width=WIDTH - 160, height=220)


def correlation_table() -> pl.DataFrame:
    return correlation_data().rename({"source": "Source", "year": "Year", "r": "Correlation", "weeks": "Weeks"})


BREAK_ORDER = ["winter", "spring", "thanksgiving"]
BREAK_LABELS = {"winter": "Winter break", "spring": "Spring break", "thanksgiving": "Thanksgiving"}


def dip_data() -> pl.DataFrame:
    dips = ev.break_dips(
        read("bluebikes_weekly_by_institution.csv", schema_overrides={"campus_unitid": pl.Utf8}),
        read("bluebikes_weekly_by_campus_zone.csv"),
        ev.verified_weeks(read("academic_weekly.csv", schema_overrides={"unitid": pl.Utf8}),
                          read("academic_calendar.csv", schema_overrides={"unitid": pl.Utf8})),
        read("student_presence_weekly.csv"),
    )
    return dips.with_columns(
        pl.col("break").replace_strict(BREAK_LABELS).alias("break_label"),
        pl.col("institution").replace(SHORT_NAMES).alias("university"),
    ).sort("institution", "week_start")


def dip_chart(t: dict) -> alt.TopLevelMixin:
    df = iso(dip_data(), "week_start")
    present = set(df["institution"])
    order = [SHORT_NAMES.get(n, n) for n in calendar_order() if n in present]
    # Ten-point steps around the data, always showing -100% to +30%.
    low = min(-100, math.floor(df["change_pct"].min() / 10) * 10)
    high = max(30, math.ceil(df["change_pct"].max() / 10) * 10)
    labels = [BREAK_LABELS[b] for b in BREAK_ORDER]
    identity = alt.Scale(domain=labels, range=[t["series1"], t["series2"], t["series3"]])
    y = alt.Y("university:N", sort=order, title=None,
              axis=alt.Axis(ticks=False, domain=False, labelColor=t["ink2"], labelFontWeight=600, labelPadding=8,
                            labelLimit=240))
    y_offset = alt.YOffset("break_label:N", sort=labels, scale=alt.Scale(paddingOuter=0.3))
    x = alt.X("change_pct:Q", title="Change in the university's campus share of Bluebikes trips (%)",
              scale=alt.Scale(domain=[low, high]),
              axis=alt.Axis(tickCount=7, grid=True, labelExpr="datum.value > 0 ? '+' + datum.value : datum.value"))
    color = alt.Color("break_label:N", title=None, scale=identity, sort=labels, legend=alt.Legend(symbolOpacity=1))
    each = alt.Chart(df).mark_circle(size=36, opacity=0.35).encode(
        x=x, y=y, yOffset=y_offset, color=color,
        tooltip=[alt.Tooltip("university:N", title="University"), alt.Tooltip("break_label:N", title="Break"),
                 alt.Tooltip("week_start:T", title="Week of", format="%b %d, %Y"),
                 alt.Tooltip("change_pct:Q", title="Change in campus share (%)", format="+.0f")])
    median = alt.Chart(df).transform_aggregate(
        median="median(change_pct)", n="count()", groupby=["university", "break_label"]
    ).mark_point(shape="diamond", size=150, filled=True, opacity=1, stroke=t["surface"], strokeWidth=2).encode(
        x="median:Q", y=y, yOffset=y_offset, color=color,
        tooltip=[alt.Tooltip("university:N", title="University"), alt.Tooltip("break_label:N", title="Break"),
                 alt.Tooltip("median:Q", title="Median change (%)", format="+.0f"),
                 alt.Tooltip("n:Q", title="Breaks")])
    zero = alt.Chart(pl.DataFrame({"x": [0]})).mark_rule(color=t["muted"], strokeWidth=1).encode(x="x:Q")
    return alt.layer(zero, each, median).properties(width=WIDTH - 220, height=alt.Step(18))


def dip_table() -> pl.DataFrame:
    return dip_data().select(
        pl.col("university").alias("University"), pl.col("break_label").alias("Break"),
        pl.col("week_start").alias("Week of"), pl.col("change_pct").round(1).alias("Change in campus share (%)"))


ZONE_ORDER = ["campus", "near", "away"]
ZONE_LABELS = {"campus": f"Campus (within {CAMPUS_M} m)", "near": f"Near ({CAMPUS_M} m to {NEAR_M / 1000:g} km)",
               "away": f"Away (over {NEAR_M / 1000:g} km)"}
MODES = {"log_bikes": "Bicycles", "log_vehicles": "Motor vehicles"}


def street_data() -> pl.DataFrame:
    counts = read("boston_street_counts.csv")
    names = counts.group_by("count_id").agg(pl.col("location_name").first())
    pairs = ev.street_count_pairs(counts).join(names, on="count_id", how="left")
    return (
        pairs.unpivot(index=["count_id", "location_name", "campus_zone", "year"], on=list(MODES),
                      variable_name="mode", value_name="log_ratio")
        .filter(pl.col("log_ratio").is_not_null() & pl.col("log_ratio").is_finite())
        .with_columns(pl.col("mode").replace_strict(MODES), pl.col("log_ratio").exp().alias("ratio"),
                      pl.col("campus_zone").replace_strict(ZONE_LABELS).alias("zone"))
        .sort("campus_zone", "count_id", "year", "mode")
    )


RATIO_LABEL = "(datum.value > 1 ? '+' : '') + format((datum.value - 1) * 100, '.0f') + '%'"


def street_chart(t: dict) -> alt.TopLevelMixin:
    df = street_data()
    zones = [ZONE_LABELS[z] for z in ZONE_ORDER]
    modes = list(MODES.values())
    identity = alt.Scale(domain=modes, range=[t["series1"], t["series2"]])
    y = alt.Y("zone:N", sort=zones, title=None,
              axis=alt.Axis(ticks=False, domain=False, labelColor=t["ink2"], labelFontWeight=600, labelPadding=8))
    y_offset = alt.YOffset("mode:N", sort=modes, scale=alt.Scale(paddingOuter=0.4))
    x = alt.X("ratio:Q", title="Change from June to September, same location and year",
              scale=alt.Scale(type="log", domain=[0.2, 8]),
              axis=alt.Axis(values=[0.25, 0.5, 1, 2, 4, 8], labelExpr=RATIO_LABEL, grid=True))
    color = alt.Color("mode:N", title=None, scale=identity, sort=modes, legend=alt.Legend(symbolOpacity=1))
    each = alt.Chart(df).mark_circle(size=36, opacity=0.35).encode(
        x=x, y=y, yOffset=y_offset, color=color,
        tooltip=[alt.Tooltip("location_name:N", title="Location"), alt.Tooltip("year:O", title="Year"),
                 alt.Tooltip("mode:N", title="Count"), alt.Tooltip("ratio:Q", title="September ÷ June", format=".2f")])
    median = alt.Chart(df).transform_aggregate(
        median="median(ratio)", n="count()", groupby=["zone", "mode"]
    ).mark_point(shape="diamond", size=150, filled=True, opacity=1, stroke=t["surface"], strokeWidth=2).encode(
        x="median:Q", y=y, yOffset=y_offset, color=color,
        tooltip=[alt.Tooltip("zone:N", title="Zone"), alt.Tooltip("mode:N", title="Count"),
                 alt.Tooltip("median:Q", title="Median September ÷ June", format=".2f"),
                 alt.Tooltip("n:Q", title="Location-years")])
    one = alt.Chart(pl.DataFrame({"x": [1.0]})).mark_rule(color=t["muted"], strokeWidth=1).encode(x="x:Q")
    return alt.layer(one, each, median).properties(width=WIDTH - 220, height=alt.Step(22))


def street_table() -> pl.DataFrame:
    return street_data().select(
        pl.col("location_name").alias("Location"), pl.col("zone").alias("Zone"), pl.col("year").alias("Year"),
        pl.col("mode").alias("Count"), ((pl.col("ratio") - 1) * 100).round(1).alias("Change, June to September (%)"))


def correlation_note() -> str:
    rows = evaluation().filter((pl.col("check") == "campus_share") & (pl.col("metric") == "median_within_year_corr"))
    values = dict(zip(rows["subset"], rows["value"]))
    b, m = values.get("Bluebikes, normal years"), values.get("MBTA, normal years")
    years = correlation_data()["year"]
    missing = sorted(set(range(years.min(), years.max() + 1)) - set(years))
    gap = f" ({', '.join(map(str, missing))})" if missing else ""
    return (f"Median correlation within a year: Bluebikes {b:.2f}, MBTA {m:.2f}. Years with fewer than 20 "
            f"comparable normal weeks are left out{gap}.")


def dip_note() -> str:
    row = evaluation().filter(pl.col("metric") == "share_of_breaks_with_a_drop")
    return (f"In {row['value'][0]:.0%} of {row['n'][0]} break weeks the university's campus share of study-area "
            "trips fell, compared with full in-session weeks within three weeks of the break. Only academic "
            "years with no estimated term or break date are used.")


# ------------------------------------------------------------------ D map

MAP_WIDTH = WIDTH
FRAME_PAD_M = 500
ZONE_OPACITY = 0.45
# Bluebikes renumbered its stations in 2023, so one dock can have an old and a new ID; records this close
# together are one station on the map.
SAME_STATION_M = 10
# Town names sit inside their town, clear of the campus labels, the campus zones and each other, where they
# cover the fewest stations (Cambridge, Somerville and Everett have no empty spot large enough); they are
# drawn under the stations. (latitude, longitude)
TOWN_LABEL_AT = {
    "Arlington": (42.4193, -71.1771), "Belmont": (42.3964, -71.1787), "Boston": (42.314, -71.0795),
    "Brookline": (42.3231, -71.1461), "Cambridge": (42.3861, -71.1523), "Chelsea": (42.3861, -71.0361),
    "Dedham": (42.2486, -71.1818), "Everett": (42.4159, -71.0377), "Lynn": (42.4811, -70.9649),
    "Malden": (42.4308, -71.064), "Medford": (42.4319, -71.126), "Melrose": (42.4571, -71.0594),
    "Milton": (42.2475, -71.0966), "Needham": (42.2853, -71.236), "Newton": (42.3266, -71.2096),
    "Quincy": (42.2647, -71.0036), "Revere": (42.4205, -71.0098), "Saugus": (42.4685, -71.0206),
    "Somerville": (42.3964, -71.0873), "Waltham": (42.3976, -71.2375), "Watertown Town": (42.3735, -71.1771),
    "Winthrop Town": (42.3747, -70.9897),
}
# Short labels for some of the largest institutions' main campuses (the others are in the tooltips and
# the table): (label, align, dx, dy).
CAMPUS_LABELS = {
    "164988": ("BU", "right", -6, 0), "167358": ("Northeastern", "left", 6, 4),
    "166027": ("Harvard", "right", -6, -2), "166683": ("MIT", "left", 6, 0),
    "166638": ("UMass Boston", "left", 6, 0), "164924": ("Boston College", "left", 6, 8),
    "168148": ("Tufts", "left", 6, 0), "165015": ("Brandeis", "left", 6, 0), "164739": ("Bentley", "left", 6, 0),
    "166391": ("Lasell", "left", 6, 0), "166948": ("Mount Ida", "left", 6, 0), "165529": ("Curry", "left", 6, 0),
    "167525": ("Quincy College", "left", 6, 0),
}


def d3_orient(geometry):
    """Clockwise outer rings, as d3-geo (used by Vega) expects; GeoJSON's counter-clockwise rings would be
    drawn as the whole globe minus the shape."""
    from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
    from shapely.geometry.polygon import orient

    if isinstance(geometry, Polygon):
        return orient(geometry, sign=-1.0)
    if isinstance(geometry, MultiPolygon):
        return MultiPolygon([orient(g, sign=-1.0) for g in geometry.geoms])
    if isinstance(geometry, GeometryCollection):
        polygons = [g for g in geometry.geoms if isinstance(g, (Polygon, MultiPolygon))]
        return d3_orient(MultiPolygon([q for g in polygons for q in getattr(g, "geoms", [g])]))
    return geometry


def rounded(geometry, digits: int = 5):
    """GeoJSON geometry with coordinates rounded (about 1 m), to keep the page small."""
    geometry = d3_orient(geometry)
    def walk(c):
        return [walk(x) for x in c] if isinstance(c, (list, tuple)) and c and isinstance(c[0], (list, tuple)) \
            else [round(c[0], digits), round(c[1], digits)]
    g = geometry.__geo_interface__
    return {"type": g["type"], "coordinates": walk(g["coordinates"])}


@functools.lru_cache(maxsize=1)
def map_layers() -> dict:
    """Town outlines, campus-zone areas, town labels and the map extent (the whole study area), as GeoJSON
    features, with the extent's height-to-width ratio as drawn (computed once)."""
    import geopandas as gpd
    from shapely.geometry import box

    towns = load_towns().to_crs(MA_CRS)
    study = towns[towns["municipality"].isin(STUDY_AREA)]
    campuses = read("campus_points.csv", schema_overrides={"unitid": pl.Utf8})
    major = campuses.filter(pl.col("major"))
    points = gpd.GeoSeries(gpd.points_from_xy(major["lng"].to_list(), major["lat"].to_list()), crs=4326).to_crs(MA_CRS)
    # A latitude-longitude box, which the Mercator projection draws as a rectangle.
    padded = gpd.GeoSeries([box(*study.total_bounds).buffer(FRAME_PAD_M, join_style="mitre")], crs=MA_CRS)
    frame_4326 = box(*padded.to_crs(4326).total_bounds)
    x0, y0, x1, y1 = gpd.GeoSeries([frame_4326], crs=4326).to_crs(3857).total_bounds
    frame = gpd.GeoSeries([frame_4326], crs=4326).to_crs(MA_CRS).iloc[0]
    study_shape = study.union_all()
    campus = points.buffer(CAMPUS_M).union_all().intersection(study_shape)
    near = points.buffer(NEAR_M).union_all().intersection(study_shape).difference(campus)  # a ring, no overlap

    def feature(geom, **props):
        geom = gpd.GeoSeries([geom], crs=MA_CRS).simplify(15).to_crs(4326).iloc[0]
        return {"type": "Feature", "properties": props, "geometry": rounded(geom)}

    town_features = []
    for name, geom in sorted(zip(towns["municipality"], towns.geometry)):
        clipped = geom.intersection(frame)
        if not clipped.is_empty:
            town_features.append(feature(clipped, name=name, study=name in STUDY_AREA))
    return {
        "towns": town_features,
        "zones": [feature(near, zone=ZONE_LABELS["near"]), feature(campus, zone=ZONE_LABELS["campus"])],
        "town_labels": [{"label": n.removesuffix(" Town"), "lat": lat, "lng": lng}
                        for n, (lat, lng) in sorted(TOWN_LABEL_AT.items())],
        "frame": {"type": "Feature", "properties": {}, "geometry": rounded(frame_4326)},
        "aspect": (y1 - y0) / (x1 - x0),
    }


def same_station(st: pl.DataFrame) -> pl.Series:
    """A group number shared by station records within SAME_STATION_M of each other (chains included)."""
    import geopandas as gpd

    points = gpd.GeoSeries(gpd.points_from_xy(st["lng"].to_list(), st["lat"].to_list()), crs=4326).to_crs(MA_CRS)
    merged = points.buffer(SAME_STATION_M / 2).union_all()
    groups = gpd.GeoSeries(list(getattr(merged, "geoms", [merged])), crs=MA_CRS)
    _, group = groups.sindex.query(points, predicate="intersects")
    return pl.Series("group", group)


def station_data(source: str) -> pl.DataFrame:
    """Study-area stations with their zone, nearest major campus and average daily use between their first
    and last recorded day. Records of one station under several IDs are combined, and named and placed by
    the busiest."""
    if source == "Bluebikes":
        st = read("bluebikes_station_campus.csv", schema_overrides={"station_id": pl.Utf8}).join(
            read("bluebikes_stations.csv", schema_overrides={"station_id": pl.Utf8}).select(
                "station_id", "first_date", "last_date"), on="station_id")
        total = "total_trips"
    else:
        st = read("mbta_stations.csv").rename({"stop_id": "station_id"})
        total = "total_entries"
    st = st.filter(pl.col("in_study_area") & pl.col("lat").is_not_null())
    return (
        st.with_columns(same_station(st))
        .sort(total, "station_id", descending=[True, False])
        .group_by("group", maintain_order=True)
        .agg(pl.all().exclude(total, "first_date", "last_date").first(), pl.col(total).sum(),
             pl.col("first_date").min(), pl.col("last_date").max())
        .with_columns(
            (pl.col(total) / ((pl.col("last_date") - pl.col("first_date")).dt.total_days() + 1)).round(1)
            .alias("per_day"),
            pl.col("campus_zone").replace_strict(ZONE_LABELS, default="Unknown").alias("zone"),
            pl.col("campus_institution").replace(SHORT_NAMES).alias("nearest_campus"),
        )
        .select("station_id", "station_name", "lat", "lng", "municipality", "zone", "nearest_campus",
                "campus_distance_m", "per_day")
        .sort("per_day", "station_id", descending=[True, False])  # small stations drawn last, on top
    )


def halo_text(data, t: dict, projection: dict, color: str, **mark) -> list:
    """A label drawn twice: a thick surface-colored outline under it keeps it legible over the map."""
    enc = {"longitude": "lng:Q", "latitude": "lat:Q", "text": "label:N"}
    under = alt.Chart(data).mark_text(stroke=t["surface"], strokeWidth=3, strokeJoin="round", color=t["surface"],
                                      **mark).encode(**enc).project(**projection)
    over = alt.Chart(data).mark_text(color=color, **mark).encode(**enc).project(**projection)
    return [under, over]


def map_panel(t: dict, source: str, unit: str, shape: str, size_range: list[int]) -> alt.LayerChart:
    layers = map_layers()
    projection = {"type": "mercator", "fit": layers["frame"]}
    zones = [ZONE_LABELS["campus"], ZONE_LABELS["near"]]
    towns = alt.Chart(alt.Data(values=layers["towns"])).mark_geoshape(strokeWidth=1).encode(
        fill=alt.condition("datum.properties.study", alt.value(t["surface"]), alt.value(t["page"])),
        stroke=alt.condition("datum.properties.study", alt.value(t["muted"]), alt.value(t["grid"])),
    ).project(**projection)
    zone_areas = alt.Chart(alt.Data(values=layers["zones"])).mark_geoshape(stroke=None).encode(
        fill=alt.Fill("properties.zone:N", title=None, sort=zones,
                      scale=alt.Scale(domain=zones, range=[t["ordinal"][1], t["ordinal"][0]]),
                      legend=alt.Legend(symbolOpacity=ZONE_OPACITY)),
        fillOpacity=alt.value(ZONE_OPACITY),
    ).project(**projection)
    town_rows = pl.DataFrame(layers["town_labels"])
    df = station_data(source)
    stations = alt.Chart(df).mark_point(shape=shape, filled=True, opacity=0.85, stroke=t["surface"],
                                        strokeWidth=1).encode(
        longitude="lng:Q", latitude="lat:Q",
        size=alt.Size("per_day:Q", title=f"Average {unit} per day", scale=alt.Scale(type="sqrt", range=size_range),
                      legend=alt.Legend(orient="bottom", direction="horizontal", symbolFillColor=t["ink2"],
                                        symbolStrokeColor=t["surface"], tickCount=4)),
        color=alt.value(t["ink2"]),
        tooltip=[alt.Tooltip("station_name:N", title="Station"), alt.Tooltip("municipality:N", title="Town"),
                 alt.Tooltip("zone:N", title="Zone"), alt.Tooltip("nearest_campus:N", title="Nearest large campus"),
                 alt.Tooltip("campus_distance_m:Q", title="Distance (m)", format=",.0f"),
                 alt.Tooltip("per_day:Q", title=f"Average {unit} per day", format=",.1~f")],
    ).project(**projection)
    raw_campuses = read("campus_points.csv", schema_overrides={"unitid": pl.Utf8}).filter(pl.col("major"))
    # Each institution's own IPEDS point carries its name (a merged college's campus keeps its own name).
    named = raw_campuses.filter((pl.col("source") == "ipeds") & (pl.col("campus_name") == pl.col("institution")))
    campuses = raw_campuses.with_columns(pl.col("institution").replace(SHORT_NAMES))
    marks = alt.Chart(campuses).mark_point(shape="cross", size=60, filled=True, color=t["series2"], opacity=1).encode(
        longitude="lng:Q", latitude="lat:Q",
        tooltip=[alt.Tooltip("institution:N", title="Campus of"), alt.Tooltip("campus_name:N", title="Site"),
                 alt.Tooltip("typical_in_person:Q", title="Median fall in-person enrollment", format=",.0f")],
    ).project(**projection)
    town_labels = halo_text(town_rows, t, projection, t["muted"], fontSize=11, fontWeight=600)
    label_layers = []
    for unitid, (text, align, dx, dy) in sorted(CAMPUS_LABELS.items()):
        row = named.filter(pl.col("unitid") == unitid).select("lng", "lat").with_columns(
            pl.lit(text).alias("label"))
        label_layers += halo_text(row, t, projection, t["ink"], align=align, dx=dx, dy=dy, fontSize=11,
                                  fontWeight=600)
    return alt.layer(towns, zone_areas, *town_labels, stations, marks, *label_layers).properties(
        width=MAP_WIDTH, height=round(MAP_WIDTH * layers["aspect"]))


MAP_STYLE = {"Bluebikes": ("trips started", "circle", [3, 90]), "MBTA": ("gate entries", "square", [8, 150])}


def map_chart_for(source: str):
    unit, shape, size_range = MAP_STYLE[source]
    return lambda t: map_panel(t, source, unit, shape, size_range)


def map_table_for(source: str):
    unit = MAP_STYLE[source][0]

    def table() -> pl.DataFrame:
        return station_data(source).select(
            pl.col("station_name").alias("Station"), pl.col("municipality").alias("Town"),
            pl.col("zone").alias("Zone"), pl.col("nearest_campus").alias("Nearest large campus"),
            pl.col("campus_distance_m").cast(pl.Int64).alias("Distance (m)"),
            pl.col("per_day").alias(f"Average {unit} per day"),
        ).sort("Zone", "Station")
    return table


def zone_note_for(source: str):
    def note() -> str:
        c = station_data(source).group_by("zone").len()
        n = dict(zip(c["zone"], c["len"]))
        return (f"{n.get(ZONE_LABELS['campus'], 0)} campus, {n.get(ZONE_LABELS['near'], 0)} near and "
                f"{n.get(ZONE_LABELS['away'], 0)} away stations.")
    return note


# ------------------------------------------------------------------ sections

def calendar_note() -> str:
    calendar = read("academic_calendar.csv")
    collected = calendar.filter(pl.col("status").is_in(["verified", "estimated"]))
    n_est = (collected["status"] == "estimated").sum()
    return (f"Academic calendars: {n_est} of {len(collected)} dates are estimated from each university's other "
            "years; the calendar chart marks which years.")


CALENDAR_NOTE = calendar_note


SECTION_A = {
    "slug": "a_data_overview",
    "title": "What data we have",
    "intro": "Section A of the Boston Breathes figures: which datasets cover which years, and what the main "
             "inputs look like before any analysis. All values come from data/processed/.",
    "charts": [
        {
            "name": "a1_coverage",
            "title": "Coverage of every dataset, 2015–2026",
            "question": "Which years does each dataset cover, and where are values filled in rather than observed?",
            "notes": ["Filled in: IPEDS enrollment after fall 2023 and before fall 2015 uses the nearest published "
                      "fall; ACS and Census values beyond their latest release are held at it.",
                      CALENDAR_NOTE,
                      "Street counts are single days (about 90 locations, a few days a year), shown as ticks."],
            "chart": coverage_chart, "table": coverage_table,
        },
        {
            "name": "a2_bluebikes",
            "title": "Bluebikes trips and network size, by week",
            "question": "How did activity grow, how seasonal is it, and what did COVID-19 do?",
            "notes": ["Complete Monday-to-Sunday weeks only. Network size is the average number of stations with at "
                      "least one trip per day, which accounts for winter closures and growth.",
                      "The two measures are on separate charts because their scales differ."],
            "chart": bluebikes_chart, "table": bluebikes_table,
        },
        {
            "name": "a3_calendars",
            "title": "Academic calendars of the universities with collected calendars",
            "question": "When is each university in session, and which years rely on estimated dates?",
            "notes": [calendar_coverage_note,
                      "Each row is an academic year from August to July. * marks a year with at least one "
                      "estimated date (hover a cell to see which).",
                      "Calendars record scheduled dates, so spring 2020 still shows as in session; the move to "
                      "remote learning is handled separately in the Student Presence Index."],
            "chart": calendar_chart, "table": calendar_table,
        },
        {
            "name": "a4_enrollment",
            "title": "Fall enrollment of the largest institutions",
            "question": "Who are the largest institutions, and how many of their students study only online?",
            "notes": ["Fall 2020: universities teaching fully online reported most students as online only (e.g. "
                      "Harvard, UMass Boston); those teaching in hybrid mode reported almost none (e.g. Boston "
                      "University, Northeastern), although some of their students studied from home.",
                      "Northeastern's jump in fall 2021 includes students of its other U.S. campuses, which IPEDS "
                      "does not report separately. Each chart has its own scale."],
            "chart": enrollment_chart, "table": enrollment_table,
        },
    ],
}

SECTION_B = {
    "slug": "b_student_presence",
    "title": "The Student Presence Index",
    "intro": "Section B of the Boston Breathes figures: the weekly share of students in session, how regular "
             "the academic year is, and how much the population could change when students leave. All values "
             "come from data/processed/student_presence_weekly.csv.",
    "charts": [
        {
            "name": "b5_presence_index",
            "title": "Student Presence Index, by week",
            "question": "What share of the area's students is in session each week, 2015–2026?",
            "notes": ["The index is in-person enrollment times the share of each week its university is in "
                      "session, divided by normal enrollment: 100% when every university is in session, 0% when "
                      "all are on break. Universities without a collected calendar follow the average of those with one.",
                      "Orange shading is the share of students on summer break, kept apart from other breaks. "
                      "During the lockdown students count as away; in 2020–21 the index uses in-person enrollment, "
                      "so it stays near 58%.",
                      "Weeks still to come follow the universities' published calendars."],
            "chart": presence_chart, "table": presence_table,
        },
        {
            "name": "b6_typical_year",
            "title": "The academic year, one line per year",
            "question": "How regular is the academic rhythm from year to year?",
            "notes": ["Each line is an academic year from August to July; the most recent complete year is "
                      "highlighted.",
                      "Left out: 2014–15 (starts in late December), 2019–20 and 2020–21 (COVID-19), and 2026–27 "
                      "(not over yet); the table lists every year."],
            "chart": typical_year_chart, "table": typical_year_table,
        },
        {
            "name": "b7_population_bounds",
            "title": "Resident population and the most it could fall when students leave",
            "question": "How large could the seasonal swing be, at most?",
            "notes": ["A range, not an estimate: the blue line is the Census resident population, which already "
                      "includes students living here during the school year. The orange line is the lowest the "
                      "population could be if every resident college student (ACS) were away when not in session. "
                      "How many actually leave will be measured from city activity.",
                      dorm_bound_note,
                      housing_note,
                      "The axis does not start at zero."],
            "chart": population_chart, "table": population_table,
        },
    ],
}

SECTION_C = {
    "slug": "c_activity_vs_presence",
    "title": "Does city activity follow the students?",
    "intro": "Section C of the Boston Breathes figures: whether activity near campuses rises and falls with the "
             "Student Presence Index. These are the checks in data/processed/presence_evaluation.csv; no model "
             "is fitted.",
    "charts": [
        {
            "name": "c8_bluebikes_ratio",
            "title": "Bluebikes: campus trips per away trip, and the index",
            "question": "Does the share of trips near campuses move with the share of students in session?",
            "notes": ["Top: Bluebikes trips starting at campus-zone stations (within 400 m of a large campus) for "
                      "each trip starting more than 1 km from any campus. Weather and citywide events affect both, "
                      "so the ratio mostly removes them. Bottom: the Student Presence Index for the same weeks.",
                      "Correlations use the logarithm of the ratio, within each year."],
            "chart": ratio_chart_for("Bluebikes"), "table": ratio_table_for("Bluebikes"),
        },
        {
            "name": "c9_mbta_ratio",
            "title": "MBTA: campus entries per away entry, and the index",
            "question": "Does the same hold for subway entries?",
            "notes": ["Gated MBTA entries at campus-zone stations per entry at away-zone stations. Campus-zone "
                      "stations also serve commuters and events, and the street-level Green Line stops at Boston "
                      "University and Northeastern have no fare gates, so the student signal is weaker."],
            "chart": ratio_chart_for("MBTA"), "table": ratio_table_for("MBTA"),
        },
        {
            "name": "c10_correlation_by_year",
            "title": "How closely campus activity follows the index, year by year",
            "question": "Is the link stable over time, and for both sources?",
            "notes": [correlation_note,
                      "Bluebikes was weak while the network was small and is strong since stations filled in "
                      "around campuses."],
            "chart": correlation_chart, "table": correlation_table,
        },
        {
            "name": "c11_break_dips",
            "title": "Campus activity in break weeks",
            "question": "Do campuses empty out when their university is on break?",
            "notes": [dip_note,
                      "Faint dots are single breaks; diamonds are medians. Normal years only. Boston College has no "
                      "Bluebikes station within 400 m of its campus, and Tufts' are at its health-sciences campus, "
                      "which follows other calendars, so both are left out."],
            "chart": dip_chart, "table": dip_table,
        },
        {
            "name": "c12_street_counts",
            "title": "Street counts: September against June at the same location",
            "question": "Do bicycles near campuses rise when students return, while cars do not?",
            "notes": ["Boston's 24-hour counts on weekdays, at locations counted in both June and September of the "
                      "same year. Faint dots are location-years; diamonds are medians. The axis is a ratio scale, "
                      "so doubling and halving are the same distance from no change."],
            "chart": street_chart, "table": street_table,
        },
    ],
}

SECTION_D = {
    "slug": "d_map",
    "title": "Where campuses and stations are",
    "intro": "Section D of the Boston Breathes figures: the campus zones used throughout the analysis, and the "
             "Bluebikes and MBTA stations in the study area, the 22 cities and towns of the Metropolitan Area "
             "Planning Council's Inner Core.",
    "charts": [
        {
            "name": "d13_bluebikes_map",
            "title": "Bluebikes stations and campus zones",
            "question": "Which Bluebikes stations count as campus, near or away?",
            "notes": [f"Shaded areas are within {CAMPUS_M} m (campus) and {NEAR_M / 1000:g} km (near) of a campus "
                      f"of a large institution (median fall in-person enrollment of {MAJOR_MIN_STUDENTS:,} or more), "
                      "clipped to the study area, whose towns are outlined; orange crosses are those campus points. "
                      "Stations elsewhere in the study area are away.",
                      zone_note_for("Bluebikes"),
                      f"Bluebikes renumbered its stations in 2023; records within {SAME_STATION_M} m of each other "
                      "(one station under its old and new IDs) are drawn and counted as one station. Stations are "
                      "sized by average trips started per day between their first and last recorded day. Outside "
                      "Boston, Cambridge, Somerville and Brookline, Bluebikes opened in 2019–2022, apart from one "
                      "employer station in Quincy."],
            "chart": map_chart_for("Bluebikes"), "table": map_table_for("Bluebikes"),
        },
        {
            "name": "d14_mbta_map",
            "title": "MBTA gated stations and campus zones",
            "question": "Which MBTA stations count as campus, near or away?",
            "notes": ["The same campus zones as the Bluebikes map.",
                      zone_note_for("MBTA"),
                      "Stations are sized by average gate entries per day between their first and last recorded "
                      "day. Street-level stops without fare gates, such as most of the Green Line, are not included; "
                      "the Mattapan trolley is reported as one line and drawn at Mattapan station."],
            "chart": map_chart_for("MBTA"), "table": map_table_for("MBTA"),
        },
    ],
}

SECTIONS = [SECTION_A, SECTION_B, SECTION_C, SECTION_D]


def resolve(notes: list) -> list[str]:
    """Notes may be functions, so numbers in them come from the current data."""
    return [n() if callable(n) else n for n in notes]


def spec(chart_fn, mode: str, font: str, title: str | None = None, notes: list[str] | None = None) -> dict:
    t = THEMES[mode]
    chart = chart_fn(t)
    if title:
        lines = [line for note in notes or [] for line in textwrap.wrap(note, 120)]
        chart = chart.properties(title=alt.Title(title, subtitle=lines))
    out = chart.to_dict()
    out["config"] = vega_config(t, font)
    return out


YEAR_COLUMNS = {"Year", "Fall"}  # integers shown without a thousands separator


def table_html(df: pl.DataFrame) -> str:
    head = "".join(f"<th scope='col'>{html.escape(c)}</th>" for c in df.columns)
    body = []
    for row in df.iter_rows():
        cells = []
        for column, v in zip(df.columns, row):
            text = "" if v is None else (f"{v:,}" if isinstance(v, int) and column not in YEAR_COLUMNS else str(v))
            cls = " class='num'" if isinstance(v, (int, float)) and not isinstance(v, bool) else ""
            cells.append(f"<td{cls}>{html.escape(text)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
__LIBS__
<style>
:root { color-scheme: light; __LIGHT__ }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) { color-scheme: dark; __DARK__ } }
:root[data-theme="dark"] { color-scheme: dark; __DARK__ }
body { margin: 0; background: var(--page); color: var(--ink); font: 15px/1.5 __FONT__; }
main { max-width: 860px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 24px; margin: 0 0 6px; }
.intro { color: var(--ink2); margin: 0 0 28px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 20px 20px 12px; margin: 0 0 24px; }
.card h2 { font-size: 17px; margin: 0 0 2px; }
.question { color: var(--ink2); margin: 0 0 12px; }
.chart { overflow-x: auto; }
.notes { color: var(--ink2); font-size: 13px; margin: 8px 0; padding-left: 18px; }
details { margin: 8px 0 4px; font-size: 13px; }
summary { cursor: pointer; color: var(--ink2); }
.tablewrap { overflow-x: auto; max-height: 360px; overflow-y: auto; margin-top: 8px; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 4px 10px 4px 0; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th { color: var(--ink2); font-weight: 600; position: sticky; top: 0; background: var(--surface); }
td.num { text-align: right; }
#vg-tooltip-element { background: var(--surface); color: var(--ink); border: 1px solid var(--border);
  border-radius: 6px; box-shadow: 0 2px 8px rgba(0,0,0,0.15); font: 12px/1.4 __FONT__; padding: 6px 8px; }
#vg-tooltip-element table tr td.key { color: var(--ink2); }
#vg-tooltip-element table tr td.value { color: var(--ink); font-weight: 600; }
</style>
</head>
<body>
<main>
<h1>__TITLE__</h1>
<p class="intro">__INTRO__</p>
__CARDS__
</main>
<script type="application/json" id="specs">__SPECS__</script>
<script>
const specs = JSON.parse(document.getElementById("specs").textContent);
function mode() {
  const forced = document.documentElement.getAttribute("data-theme");
  if (forced === "light" || forced === "dark") return forced;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function render() {
  const m = mode();
  for (const [name, byMode] of Object.entries(specs)) {
    const spec = Object.assign({}, byMode[m], {datasets: byMode.datasets});
    vegaEmbed("#" + name, spec, {actions: false, renderer: "svg", tooltip: {theme: "custom"}});
  }
}
render();
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
new MutationObserver(render).observe(document.documentElement, {attributes: true, attributeFilter: ["data-theme"]});
</script>
</body>
</html>
"""


def vendor_js(package: str, version: str, member: str) -> str:
    """A library's minified build from its npm tarball, verified and cached."""
    path = VENDOR_DIR / f"{package}-{version}.min.js"
    if not path.exists():
        resp = http.get(f"{NPM_REGISTRY}/{package}/{version}", timeout=60)
        resp.raise_for_status()
        dist = resp.json()["dist"]
        tarball = http.get(dist["tarball"], timeout=120)
        tarball.raise_for_status()
        algo, expected = dist["integrity"].split("-", 1)
        if algo != "sha512" or base64.b64encode(hashlib.sha512(tarball.content).digest()).decode() != expected:
            raise RuntimeError(f"{package} {version}: tarball does not match the registry's sha512")
        with tarfile.open(fileobj=io.BytesIO(tarball.content), mode="r:gz") as tf:
            code = tf.extractfile(f"package/{member}").read().decode("utf-8")
        VENDOR_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(code)
    code = path.read_text()
    if "</script" in code.lower():
        raise ValueError(f"{package} {version} contains '</script' and cannot be embedded")
    return code


def css_vars(t: dict) -> str:
    keys = ["surface", "page", "ink", "ink2", "muted", "grid", "border"]
    return " ".join(f"--{k}: {t[k]};" for k in keys)


def build_section(section: dict) -> list[str]:
    FIGURES.mkdir(parents=True, exist_ok=True)
    written, cards, specs = [], [], {}
    for c in section["charts"]:
        notes_text = resolve(c["notes"])
        png = vlc.vegalite_to_png(spec(c["chart"], "light", FONT_PNG, c["title"], notes_text), scale=2)
        (FIGURES / f"{c['name']}.png").write_bytes(png)
        written.append(f"figures/{c['name']}.png")
        by_mode = {m: spec(c["chart"], m, FONT_HTML) for m in THEMES}
        # The data is the same in both themes: store it once and attach it in the page.
        datasets = by_mode["light"].pop("datasets", {})
        if by_mode["dark"].pop("datasets", {}) != datasets:
            raise ValueError(f"{c['name']}: light and dark charts use different data")
        specs[c["name"]] = {"datasets": datasets, **by_mode}
        notes = "".join(f"<li>{html.escape(n)}</li>" for n in notes_text)
        cards.append(
            f"<section class='card'><h2>{html.escape(c['title'])}</h2>"
            f"<p class='question'>{html.escape(c['question'])}</p>"
            f"<div class='chart'><div id='{c['name']}' role='img' aria-label='{html.escape(c['title'])}'></div></div>"
            f"<ul class='notes'>{notes}</ul>"
            f"<details><summary>Show the values as a table</summary>"
            f"<div class='tablewrap'>{table_html(c['table']())}</div></details></section>"
        )
    page = (
        PAGE.replace("__TITLE__", html.escape(f"Boston Breathes: {section['title']}"))
        .replace("__INTRO__", html.escape(section["intro"]))
        .replace("__LIGHT__", css_vars(THEMES["light"])).replace("__DARK__", css_vars(THEMES["dark"]))
        .replace("__FONT__", FONT_HTML)
        .replace("__LIBS__", "\n".join(f"<script>/* {p} {v} */\n{vendor_js(p, v, m)}\n</script>" for p, v, m in VENDOR))
        .replace("__CARDS__", "\n".join(cards))
        .replace("__SPECS__", json.dumps(specs, separators=(",", ":")).replace("</", "<\\/"))
    )
    out = FIGURES / f"{section['slug']}.html"
    out.write_text(page)
    written.append(f"figures/{section['slug']}.html")
    return written


def main() -> None:
    for section in SECTIONS:
        for path in build_section(section):
            print(f"Wrote {path}")


if __name__ == "__main__":
    main()
