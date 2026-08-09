from datetime import date, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts/analysis/box_office"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = spec_from_file_location("box_office_repeat_v1", SCRIPT_DIR / "research_repeat_weekend_v1.py")
MODULE = module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_parse_year_releases_filters_on_opening_gross():
    page = """
    <table><tr><td><a href="/release/rl1/?ref_=x">Large Movie</a></td>
    <td class="mojo-field-type-money">$100,000,000</td>
    <td class="mojo-field-type-money">$20,000,000</td></tr>
    <tr><td><a href="/release/rl2/?ref_=x">Tiny Movie</a></td>
    <td class="mojo-field-type-money">$4,000,000</td>
    <td class="mojo-field-type-money">$1,000,000</td></tr></table>
    """
    rows = MODULE.parse_year_releases(page, 2024, min_opening=5_000_000)
    assert [row["release_id"] for row in rows] == ["rl1"]


def test_parse_rich_daily_extracts_rank_gross_and_theaters():
    page = """
    <table><tr>
      <td><a href="/date/2024-07-22/?ref_=x">Jul 22</a></td><td>Monday</td><td>2</td>
      <td class="mojo-field-type-money">$7,000,000</td><td>-10%</td><td>-20%</td><td>3,500</td>
    </tr></table>
    """
    assert MODULE.parse_rich_daily(page)[date(2024, 7, 22)] == {
        "gross": 7_000_000.0,
        "rank": 2,
        "theaters": 3500,
    }


def test_build_row_feature_cutoff_ignores_target_weekend_values():
    opening = date(2024, 7, 5)
    daily = {}
    for offset in range(35):
        daily[opening + timedelta(days=offset)] = {"gross": 1_000_000.0, "rank": 2, "theaters": 3000}
    release = {"release_id": "rl1", "movie": "Movie", "year": 2024}
    before = MODULE.build_row(daily, release, 3)
    for offset in range(14, 17):
        daily[opening + timedelta(days=offset)]["gross"] = 9_000_000.0
    after = MODULE.build_row(daily, release, 3)
    for feature in MODULE.FEATURES:
        assert before[feature] == after[feature]
    assert before["actual_gross_m"] != after["actual_gross_m"]


def test_holiday_features_identify_july_fourth_week():
    assert MODULE.has_holiday(date(2024, 7, 1), date(2024, 7, 7)) == 1
    assert MODULE.has_holiday(date(2024, 7, 8), date(2024, 7, 14)) == 0


def test_parse_full_repeat_event_accepts_search_title_variants():
    base = {
        "id": "1",
        "slug": "event",
        "startDate": "2025-12-23T22:46:00Z",
        "endDate": "2025-12-28T00:00:00Z",
        "markets": [],
        "volume": "1",
    }
    single_quote = MODULE.parse_full_repeat_event(
        {**base, "title": "'Deadpool & Wolverine' 2nd Weekend Box Office"}
    )
    three_day = MODULE.parse_full_repeat_event(
        {**base, "title": '"Avatar: Fire and Ash" Second 3-Day Weekend Box Office'}
    )
    assert (single_quote["movie"], single_quote["week"]) == ("Deadpool & Wolverine", 2)
    assert (three_day["movie"], three_day["week"]) == ("Avatar: Fire and Ash", 2)
