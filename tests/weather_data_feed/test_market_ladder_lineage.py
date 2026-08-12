from __future__ import annotations

import pytest

from weather_data_feed.market_ladder_lineage import annotate_market_ladder_snapshot


def _row(bracket: str, question: str, probability: float) -> dict:
    return {
        "city": "Testville",
        "event_date": "2026-08-06",
        "event_slug": "test-weather",
        "bracket": bracket,
        "question": question,
        "condition_id": f"condition-{bracket}",
        "market_id": f"market-{bracket}",
        "yes_token_id": f"yes-{bracket}",
        "no_token_id": f"no-{bracket}",
        "market_yes_price": probability,
    }


def test_full_ladder_manifest_has_stable_ids_and_normalized_distribution() -> None:
    payload = {
        "ts_utc": "2026-08-05T00:00:00Z",
        "collection_started_at_utc": "2026-08-05T00:00:00Z",
        "available_at_utc": "2026-08-05T00:01:00Z",
        "records": [
            _row("72", "Will Tmax be 72 or below?", 0.2),
            _row("73", "Will Tmax be 73?", 0.5),
            _row("74", "Will Tmax be 74 or higher?", 0.4),
        ],
    }
    manifests = annotate_market_ladder_snapshot(
        payload, producer="collector", producer_build_id="sha"
    )

    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest["native_lattice_complete"] is True
    assert manifest["market_distribution_complete"] is True
    assert manifest["checkpoint_status"] == "scorable_probability"
    assert manifest["two_sided_book_distribution_complete"] is False
    assert manifest["strict_book_checkpoint"]["evidence_status"] == "blocked"
    assert sum(manifest["normalized_market_probability"]) == pytest.approx(1.0)
    assert len(manifest["book_snapshot_id"]) == 64
    assert {row["book_snapshot_id"] for row in payload["records"]} == {manifest["book_snapshot_id"]}
    assert len(manifest["generic_market_group_snapshot"]["expressions"]) == 6
    assert manifest["generic_market_group_snapshot"]["batch_complete"] is False
    assert {row["market_group_snapshot_id"] for row in payload["records"]} == {
        manifest["market_group_snapshot_id"]
    }


def test_missing_rung_is_a_blocker_not_a_silent_drop() -> None:
    payload = {
        "ts_utc": "2026-08-05T00:00:00Z",
        "available_at_utc": "2026-08-05T00:01:00Z",
        "records": [
            _row("72", "Will Tmax be 72 or below?", 0.2),
            _row("74", "Will Tmax be 74 or higher?", 0.4),
        ],
    }
    manifest = annotate_market_ladder_snapshot(
        payload, producer="collector", producer_build_id="sha"
    )[0]
    assert manifest["native_lattice_complete"] is False
    assert manifest["checkpoint_status"] == "blocked"
    assert "non_contiguous_native_lattice" in manifest["checkpoint_blockers"]
