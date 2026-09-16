from __future__ import annotations

from scripts.analysis.market_making.research_weather_mm_stage0_truth_audit_v1 import (
    audit_journal_transport_metadata,
    audit_stage0a,
    audit_stage0b,
)


def test_stage0a_reclassifies_old_notional_times_rate_estimate() -> None:
    orders = [
        {
            "execution_id": "e1",
            "order_id": "o1",
            "maker_only": 0,
            "shares": 10,
            "requested_price": 0.94,
            "entry_price": 0.94,
            "status": "submitted",
            "exchange_response": {
                "estimated_fee_usd": "0.94",
                "requested_shares": "10",
                "requested_price": "0.94",
                "collateral_asset": "USDC",
                "fee_schedule_ref": "polymarket-token-fee-bps-v1",
            },
        }
    ]
    summary, detail, _ = audit_stage0a(orders, [])
    assert detail[0]["correct_v2_estimated_fee_usd"] == "0.02820"
    assert summary["estimate_impact"]["decision_changes"] == 0
    assert summary["estimate_impact"]["rows_with_wrong_legacy_estimate"] == 1


def test_journal_transport_metadata_preserves_labels_missing_from_db() -> None:
    rows = [
        {
            "event_type": "outcome",
            "payload": {
                "kind": "submit",
                "status": "submitted",
                "venue_order_id": "o1",
                "estimated_fee_usd": "0.01",
                "realized_maker_rebate_usd": None,
                "collateral_asset": "USDC",
                "fee_schedule_ref": "polymarket-token-fee-bps-v1",
                "protocol_version": "clob-v2",
                "client_version": "py_clob_client_v2",
            },
        },
        {
            "event_type": "outcome",
            "payload": {
                "kind": "submit",
                "status": "unknown",
                "collateral_asset": "USDC",
            },
        },
    ]
    summary = audit_journal_transport_metadata(rows)
    assert summary["submitted_side_effect_rows"] == 1
    assert summary["rows_with_estimated_fee"] == 1
    assert summary["rows_with_realized_maker_rebate"] == 0
    assert summary["collateral_asset_counts"] == {"USDC": 1}
    assert summary["fee_schedule_ref_counts"] == {
        "polymarket-token-fee-bps-v1": 1
    }


def test_stage0b_detects_duplicate_side_effect_and_missing_private_ws() -> None:
    journal = [
        {
            "event_type": "attempt_before_side_effect",
            "writer_id": "owner-a",
            "payload": {"identity_key": "i1", "kind": "submit"},
        },
        {
            "event_type": "outcome",
            "writer_id": "owner-a",
            "payload": {
                "identity_key": "i1",
                "kind": "submit",
                "status": "submitted",
                "venue_order_id": "o1",
            },
        },
        {
            "event_type": "outcome",
            "writer_id": "owner-a",
            "payload": {
                "identity_key": "i1",
                "kind": "submit",
                "status": "submitted",
                "venue_order_id": "o2",
            },
        },
    ]
    summary, _ = audit_stage0b(journal, [], [])
    assert summary["identity"]["duplicate_side_effect_identities"] == ["i1"]
    assert summary["gate"]["duplicate_side_effect_zero"] is False
    assert summary["gate"]["private_user_ws_wired"] is False


def test_stage0b_replays_authenticated_rest_snapshot() -> None:
    journal = [
        {
            "event_type": "attempt_before_side_effect",
            "writer_id": "owner-a",
            "payload": {"identity_key": "i1", "kind": "submit"},
        },
        {
            "event_type": "outcome",
            "writer_id": "owner-a",
            "recorded_at_utc": "2026-08-01T00:00:00Z",
            "payload": {
                "identity_key": "i1",
                "kind": "submit",
                "status": "submitted",
                "venue_order_id": "o1",
                "requested_shares": "5",
                "requested_price": "0.50",
            },
        },
    ]
    live = [
        {
            "created_at_utc": "2026-08-01T00:01:00Z",
            "_line_number": 1,
            "exchange_response": {
                "order": {
                    "id": "o1",
                    "owner": "venue-owner",
                    "original_size": "5",
                    "size_matched": "2",
                    "status": "LIVE",
                }
            },
        }
    ]
    summary, detail = audit_stage0b(journal, live, [])
    assert summary["rest_replay"]["orders_with_clean_rest_replay"] == 1
    assert detail[0]["replay_matched_shares"] == "2"
    assert detail[0]["replay_remaining_shares"] == "3"


def test_stage0b_replays_normalized_authenticated_rest_state() -> None:
    journal = [
        {
            "event_type": "attempt_before_side_effect",
            "writer_id": "owner-a",
            "payload": {"identity_key": "i1", "kind": "submit"},
        },
        {
            "event_type": "outcome",
            "writer_id": "owner-a",
            "recorded_at_utc": "2026-08-01T00:00:00Z",
            "payload": {
                "identity_key": "i1",
                "kind": "submit",
                "status": "submitted",
                "venue_order_id": "o1",
            },
        },
    ]
    live = [
        {
            "created_at_utc": "2026-08-01T00:01:00Z",
            "exchange_response": {
                "authoritative_order_state": {
                    "order_state_provenance": "polymarket_authenticated_order_lookup_v1",
                    "order_id": "o1",
                    "requested_shares": "5",
                    "matched_shares": "5",
                    "raw_venue_status": "MATCHED",
                }
            },
        }
    ]
    summary, detail = audit_stage0b(journal, live, [])
    assert summary["rest_replay"]["orders_with_rest_snapshot"] == 1
    assert detail[0]["replay_status"] == "FILLED"


def test_stage0b_classifies_explicit_403_as_rejected_not_unknown() -> None:
    journal = [
        {
            "event_type": "outcome",
            "writer_id": "owner-a",
            "payload": {
                "identity_key": "i1",
                "kind": "submit",
                "status": "unknown",
                "error": "PolyApiException[status_code=403, error_message=geoblock]",
            },
        }
    ]
    summary, _ = audit_stage0b(journal, [], [])
    assert summary["identity"]["unresolved_unknown_identities"] == []
    assert summary["identity"]["journal_unknown_but_explicit_http_reject_identities"] == ["i1"]
