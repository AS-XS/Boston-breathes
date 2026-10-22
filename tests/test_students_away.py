from datetime import date

import polars as pl
import pytest

from boston_breathes import students_away as sa

HALLS = pl.DataFrame({"unitid": ["1"], "thanksgiving": ["open"], "winter": ["closed"], "spring": ["closed"]})


def by_inst(week, session, summer):
    """Institution 1 (300 beds, policy recorded) and 2 (100 beds, no policy), same calendar."""
    return pl.DataFrame({"week_start": [week] * 2, "unitid": ["1", "2"], "dorm_capacity": [300, 100],
                         "session_share": [session] * 2, "summer_share": [summer] * 2})


@pytest.mark.parametrize("week, session, summer, expected", [
    (date(2019, 7, 8), 0.0, 1.0, 1.0),        # summer: every hall closes
    (date(2019, 12, 23), 0.0, 0.0, 0.75),     # winter: only institution 1's halls close
    (date(2019, 11, 25), 4 / 7, 0.0, 0.0),    # Thanksgiving: institution 1 stays open
    (date(2019, 3, 11), 2 / 7, 0.0, 0.75 * 5 / 7),
    (date(2020, 4, 6), 1.0, 0.0, 1.0),        # lockdown: halls closed
])
def test_closed_bed_share(week, session, summer, expected):
    out = sa.closed_bed_share(by_inst(week, session, summer), HALLS)
    assert out["closed_bed_share"][0] == pytest.approx(expected)


def test_build_range_leaves_students_living_with_parents_out():
    week = date(2019, 7, 8)
    presence = pl.DataFrame({"week_start": [week], "presence_index": [0.2], "housing_presence_index": [0.0],
                             "covid_period": ["normal"]})
    arrangements = pl.DataFrame({"acs_year": [2018, 2019, 2019], "college_students": [1, 600, 400],
                                 "in_university_housing": [1, 200, 100], "with_parents": [0, 100, 100],
                                 "elsewhere": [0, 300, 200]})
    population = pl.DataFrame({"week_start": [week], "study_area": [10_000], "extrapolated": [False]})
    out = sa.build(presence, by_inst(week, 0.0, 1.0), arrangements, population, HALLS).row(0, named=True)
    # Summer 2019 belongs to fall 2018, whose ACS year is the nearest available here (2018).
    assert out["resident_students"] == 1
    week = date(2019, 9, 9)
    presence = presence.with_columns(pl.lit(week).alias("week_start"))
    population = population.with_columns(pl.lit(week).alias("week_start"))
    out = sa.build(presence, by_inst(week, 0.0, 1.0), arrangements, population, HALLS).row(0, named=True)
    assert (out["resident_students"], out["with_parents"]) == (1000, 200)
    # At most: all 300 in university housing (housing index 0) and 80% of the 500 elsewhere.
    assert out["max_students_away"] == 700
    assert out["min_students_away"] == 300
    assert (out["min_effective_population"], out["max_effective_population"]) == (9_300, 9_700)


def test_load_halls_rejects_unknown_values(tmp_path):
    path = tmp_path / "halls.csv"
    path.write_text("unitid,institution,thanksgiving,winter,spring,checked,source_url,note\n"
                    "1,A,open,shut,open,page,https://example.org,\n"
                    "1,A,open,closed,open,rumour,,\n")
    with pytest.raises(ValueError, match="(?s)'shut'.*'rumour'.*missing source.*duplicate"):
        sa.load_halls(path)


def test_hand_collected_halls_load():
    halls = sa.load_halls()
    assert halls["unitid"].n_unique() == len(halls) > 0
