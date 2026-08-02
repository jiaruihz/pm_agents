from __future__ import annotations

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
