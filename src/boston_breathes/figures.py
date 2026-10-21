"""Figures of the processed data, as PNG images and an interactive HTML page.

Each chart is defined once (Vega-Lite, through Altair) and written twice:
  * figures/<name>.png               - static image (light theme) for reports
  * figures/<section>_<title>.html   - interactive page: hover tooltips, light
                                       and dark themes, and a table under each
                                       chart with the values it shows

Section A, "What data we have":
  A1 coverage of every dataset over the study period
  A2 weekly Bluebikes trips and network size
  A3 academic calendars of the six universities with collected calendars
  A4 fall enrollment of the largest institutions, in person and online only

Colors follow the project's chart palette (validated for color-vision
deficiency); identity is never carried by color alone.

The pages are self-contained: the Vega libraries are downloaded once from the
npm registry (checked against the registry's published sha512), cached in
data/raw/vendor/, and embedded, so a page works offline.
"""

import base64
import hashlib
import html
import io
import json
import tarfile
import textwrap
from datetime import date, timedelta

import altair as alt
import polars as pl
import vl_convert as vlc

from boston_breathes import http
from boston_breathes.paths import PROCESSED, RAW, ROOT
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
        "series1": "#2a78d6", "series2": "#eb6834",
        # Ordinal blue ramp, least to most; validated light end >= 2:1 on the surface.
        "ordinal": ["#86b6ef", "#3987e5", "#184f95"],
        "neutral": "#e1e0d9",
    },
    "dark": {
        "surface": "#1a1a19", "page": "#0d0d0d", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781",
        "grid": "#2c2c2a", "axis": "#383835", "border": "rgba(255,255,255,0.10)",
        "series1": "#3987e5", "series2": "#d95926",
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
            "titleFontWeight": "normal", "orient": "top", "direction": "horizontal",
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
        add(GROUPS[1], "Academic calendars (6 universities)", date(start_year, 8, 1), date(start_year + 1, 7, 31),
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


def week_hover(df: pl.DataFrame, value: str, label: str, fmt: str, t: dict, color: str, height: int):
    """Line with a crosshair: hovering anywhere in a week's column shows its value."""
    base = alt.Chart(df).encode(x=alt.X("week_start:T", title=None, axis=alt.Axis(format="%Y", tickCount="year")))
    hover = alt.selection_point(name=f"hover_{value}", on="pointerover", nearest=False, fields=["week_start"],
                                empty=False, clear="pointerout")
    line = base.mark_line(strokeWidth=2, strokeJoin="round", strokeCap="round", color=color).encode(
        y=alt.Y(f"{value}:Q", title=label, axis=alt.Axis(labelExpr=fmt, tickCount=4, domain=False, ticks=False,
                                                        minExtent=40))
    )
    columns = base.mark_bar(opacity=0).encode(
        x2="week_end:T", y=alt.value(0), y2=alt.value(height),
        tooltip=[alt.Tooltip("week_start:T", title="Week of", format="%b %d, %Y"),
                 alt.Tooltip(f"{value}:Q", title=label, format=",.0f")],
    ).add_params(hover)
    rule = base.mark_rule(color=t["muted"], strokeWidth=1).encode(
        opacity=alt.condition(hover, alt.value(1), alt.value(0))
    )
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
CALENDAR_ORDER = ["Northeastern University", "Boston University", "Harvard University", "Boston College",
                  "Tufts University", "Massachusetts Institute of Technology"]


def calendar_data() -> pl.DataFrame:
    weekly = read("academic_weekly.csv", schema_overrides={"unitid": pl.Utf8})
    calendar = read("academic_calendar.csv", schema_overrides={"unitid": pl.Utf8})
    estimated = (
        calendar.filter(pl.col("status") == "estimated")
        .group_by("unitid", "academic_year")
        .agg(pl.col("event").str.replace_all("_", " ").sort().str.join(", ").alias("estimated_events"))
    )
    thursday = pl.col("week_start") + pl.duration(days=3)
    # Place every week on one reference academic year (fall 2003 - summer 2004, a leap year) for the x axis.
    ref_thursday = pl.date(pl.when(thursday.dt.month() >= 8).then(2003).otherwise(2004),
                           thursday.dt.month(), thursday.dt.day())
    return (
        weekly.with_columns(fall_year(pl.col("week_start")).alias("fy"))
        .with_columns(pl.format("{}-{}", pl.col("fy"), (pl.col("fy") + 1).mod(100).cast(pl.Utf8).str.zfill(2))
                      .alias("academic_year"))
        .join(estimated, on=["unitid", "academic_year"], how="left")
        .with_columns(
            pl.when(pl.col("days_in_session") == 7).then(pl.lit(IN_SESSION))
            .when(pl.col("days_in_session") > 0).then(pl.lit(PARTIAL))
            .when(pl.col("summer_break_days") > 0).then(pl.lit(SUMMER))
            .otherwise(pl.lit(BREAK)).alias("state"),
            (ref_thursday - pl.duration(days=3)).alias("ref_start"),
            (ref_thursday + pl.duration(days=4)).alias("ref_end"),
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
    for name in CALENDAR_ORDER:
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
                x=alt.X("ref_start:T", title=None, scale=alt.Scale(domain=["2003-08-01", "2004-08-01"]),
                        axis=alt.Axis(format="%b", tickCount="month", grid=False, domain=False, labelAlign="left")),
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
            "title": "Academic calendars of the six universities with collected calendars",
            "question": "When is each university in session, and which years rely on estimated dates?",
            "notes": ["Each row is an academic year from August to July. * marks a year with at least one "
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

SECTIONS = [SECTION_A]


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


def table_html(df: pl.DataFrame) -> str:
    head = "".join(f"<th scope='col'>{html.escape(c)}</th>" for c in df.columns)
    body = []
    for row in df.iter_rows():
        cells = []
        for v in row:
            text = "" if v is None else (f"{v:,}" if isinstance(v, int) else str(v))
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
