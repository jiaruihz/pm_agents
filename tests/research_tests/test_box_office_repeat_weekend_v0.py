from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts/analysis/box_office/research_repeat_weekend_v0.py"
)
SPEC = spec_from_file_location("box_office_repeat_v0", SCRIPT)
MODULE = module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_parse_repeat_event_supports_ordinal_words():
    event = {
        "id": "1",
        "title": '"GOAT" Second Weekend Box Office',
        "slug": "goat-second",
        "startDate": "2026-02-18T12:00:00Z",
        "endDate": "2026-02-23T12:00:00Z",
        "markets": [],
    }
    parsed = MODULE.parse_repeat_event(event)
    assert parsed["movie"] == "GOAT"
    assert parsed["week"] == 2


def test_parse_daily_rows_extracts_first_money_cell():
    page = """
    <table><tr>
      <td><a href="/date/2026-07-22/?ref_=x">Jul 22</a></td>
      <td>Wednesday</td><td>1</td>
      <td class="a-text-right mojo-field-type-money mojo-estimatable">$18,067,805</td>
      <td class="a-text-right mojo-field-type-money mojo-estimatable">$4,610</td>
    </tr></table>
    """
    assert MODULE.parse_daily_rows(page) == {date(2026, 7, 22): 18_067_805.0}


def test_bracket_partition_and_boundary_rule():
    intervals = [
        MODULE.parse_bracket("<47m"),
        MODULE.parse_bracket("47-52m"),
        MODULE.parse_bracket("52m+"),
    ]
    assert MODULE.exhaustive(intervals)
    assert not MODULE.bracket_contains(intervals[0], 47.0)
    assert MODULE.bracket_contains(intervals[1], 47.0)
    assert MODULE.parse_bracket(">10m") == (10.0, float("inf"))


def test_feature_row_uses_only_monday_through_wednesday_for_signal():
    daily = {}
    opening = date(2026, 7, 17)
    for offset in range(24):
        daily[opening.fromordinal(opening.toordinal() + offset)] = 1_000_000.0
    info = {"daily": daily, "release_id": "rl1", "release_url": "https://example.test"}
    row = MODULE.feature_row(info, "Example", 3)
    assert row["target_friday"] == "2026-07-31"
    assert row["target_mw_m"] == 3.0
    assert row["actual_gross_m"] == 3.0
    assert row["weekday_hold"] == 1.0
    assert row["weekend_hold"] == 1.0
