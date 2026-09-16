from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import replay_tokyo_overshoot_intraday_v2 as replay


def _book(timestamp: str, bracket: str, bid: float | None, ask: float | None):
    return {
        "book_fetched_at_utc": timestamp,
        "bracket": bracket,
        "question": f"Will the highest temperature in Tokyo be {bracket}°C?",
        "summary": {"best_bid": bid, "best_ask": ask},
    }


def test_select_first_pit_current_book_prefers_first_two_sided(monkeypatch):
    official = [
        {
            "running_max_c": 35.0,
            "fetched_at_utc": "2026-08-01T06:00:00+00:00",
            "last_obs_utc": "2026-08-01T06:00:00+00:00",
        }
    ]
    monkeypatch.setattr(replay, "_official_history", lambda *_args: official)
    candidates = [
        _book("2026-08-01T06:01:00+00:00", "35", None, 0.2),
        _book("2026-08-01T06:01:01+00:00", "36", 0.7, 0.8),
        _book("2026-08-01T06:02:00+00:00", "35", 0.1, 0.2),
        _book("2026-08-01T06:03:00+00:00", "35", 0.2, 0.3),
    ]

    selected = replay.select_first_pit_current_book(
        candidates, Path("unused"), "2026-08-01"
    )

    assert selected is not None
    assert selected.official_anchor == 35
    assert selected.book["book_fetched_at_utc"] == "2026-08-01T06:02:00+00:00"


def test_select_first_pit_current_book_retains_one_sided_fallback(monkeypatch):
    official = [{"running_max_c": 35.0}]
    monkeypatch.setattr(replay, "_official_history", lambda *_args: official)
    candidates = [
        _book("2026-08-01T06:01:00+00:00", "35", None, 0.2),
        _book("2026-08-01T06:02:00+00:00", "36", 0.7, 0.8),
    ]

    selected = replay.select_first_pit_current_book(
        candidates, Path("unused"), "2026-08-01"
    )

    assert selected is not None
    assert selected.book["bracket"] == "35"
    assert replay._market_prices(selected.book)["no_mid"] is None


def test_select_first_pit_current_book_prefers_captured_official_anchor(monkeypatch):
    monkeypatch.setattr(
        replay,
        "_official_history",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not backfill PIT")),
    )
    current = _book("2026-08-08T01:07:14+00:00", "31", 0.6, 0.7)
    current["capture_anchor_values"] = {"official": 31, "source": 31}

    selected = replay.select_first_pit_current_book(
        [current], Path("unused"), "2026-08-08"
    )

    assert selected is not None
    assert selected.official == []
    assert selected.official_anchor == 31
    assert selected.official_anchor_provenance == "book_capture_anchor_values"


def test_pm_history_settlement_is_detached_post_event_label(tmp_path):
    (tmp_path / "Tokyo_2026-08-08.json").write_text(
        '{"brackets": [{"label": "33", "final_price": 0.0}, '
        '{"label": "34", "final_price": 1.0}]}'
    )

    bracket, source, ref = replay._pm_history_final_bracket(
        tmp_path, "2026-08-08"
    )

    assert bracket == 34
    assert source == "pm_history_near_binary"
    assert ref.endswith("Tokyo_2026-08-08.json")


def test_fee_is_zero_at_binary_boundaries_and_positive_inside():
    assert replay.official_fee_per_share(0.0) == 0.0
    assert replay.official_fee_per_share(1.0) == 0.0
    assert replay.official_fee_per_share(0.5) == 0.0125


def test_fee_adjusted_binary_pnl_uses_selected_side_ask():
    winner_cost, winner_pnl = replay.fee_adjusted_binary_pnl(1, 0.6)
    loser_cost, loser_pnl = replay.fee_adjusted_binary_pnl(0, 0.6)

    assert winner_cost == loser_cost == 0.612
    assert winner_pnl == 0.388
    assert loser_pnl == -0.612
    assert replay.REPLAY_SHARES == 5.0


def test_timely_books_rejects_stale_source_capture():
    first_seen = datetime(2026, 8, 2, 2, 57, tzinfo=timezone.utc)
    candidates = [
        _book("2026-08-02T02:56:59+00:00", "32", 0.9, 0.99),
        _book("2026-08-02T02:57:30+00:00", "32", 0.9, 0.99),
        _book("2026-08-02T07:08:00+00:00", "33", 0.1, 0.2),
    ]

    selected = replay.timely_books_after_first_seen(candidates, first_seen, 900)

    assert [row["book_fetched_at_utc"] for row in selected] == [
        "2026-08-02T02:57:30+00:00"
    ]
