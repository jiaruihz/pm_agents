import argparse

from scripts.analysis.market_structure_edge.research_all_yes_underround_live_prep_v0 import group_baskets


def _row(idx: int, *, bracket=None, condition_id=None):
    price = [0.10, 0.15, 0.20, 0.22, 0.28][idx]
    return {
        "outcome": "yes",
        "event_date": "2026-06-14",
        "city": "Seattle",
        "event_slug": "highest-temperature-in-seattle-on-june-14-2026",
        "condition_id": condition_id or f"condition-{idx}",
        "bracket": bracket if bracket is not None else str(20 + idx),
        "snapshot_ts_utc": "2026-06-13T18:00:00Z",
        "fetched_at_utc": "2026-06-13T18:00:01Z",
        "status": "ok",
        "summary": {
            "best_ask": price,
            "best_bid": max(price - 0.01, 0.001),
            "ask_size": 10.0,
        },
    }


def _args():
    return argparse.Namespace(
        min_underround=0.02,
        min_leg_count=5,
        min_shares=5.0,
        max_spread=0.05,
    )


def test_scanner_rejects_duplicate_bracket_as_non_shadow_candidate():
    rows = [_row(idx) for idx in range(5)]
    rows[4]["bracket"] = rows[0]["bracket"]

    baskets = group_baskets(rows, _args())

    assert len(baskets) == 1
    assert baskets[0]["paper_shadow_candidate"] is False
    assert "duplicate_bracket" in baskets[0]["blockers"]


def test_scanner_rejects_duplicate_condition_id_as_non_shadow_candidate():
    rows = [_row(idx) for idx in range(5)]
    rows[4]["condition_id"] = rows[0]["condition_id"]

    baskets = group_baskets(rows, _args())

    assert len(baskets) == 1
    assert baskets[0]["paper_shadow_candidate"] is False
    assert "duplicate_condition_id" in baskets[0]["blockers"]
