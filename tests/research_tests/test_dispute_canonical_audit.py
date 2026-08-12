from __future__ import annotations

import json

from src.platform.market_data.capture_demand import CaptureDemand
from src.strategies.rule_lawyer.canonical import materialize
from src.strategies.rule_lawyer.canonical_audit import audit_canonical


def test_audit_detects_unmaterialized_append_without_corrupting_db(tmp_path) -> None:
    source = tmp_path / "forward"
    source.mkdir()
    events = source / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "first_observed_at_utc": "2026-08-12T00:00:00Z",
                "raw_request": {"id": "request-1"},
            }
        )
        + "\n"
    )
    db = tmp_path / "dispute.db"
    materialize(source, db)
    first = audit_canonical(db)
    assert first["status"] == "healthy_with_warnings"
    assert not first["errors"]

    with events.open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "case_id": "case-2",
                    "first_observed_at_utc": "2026-08-12T00:01:00Z",
                    "raw_request": {"id": "request-2"},
                }
            )
            + "\n"
        )
    stale = audit_canonical(db)
    assert any("unmaterialized_source_bytes" in row for row in stale["warnings"])
    materialize(source, db)
    current = audit_canonical(db)
    assert not any("unmaterialized_source_bytes" in row for row in current["warnings"])


def test_audit_separates_pre_owner_gap_from_operational_miss(tmp_path) -> None:
    source = tmp_path / "forward"
    source.mkdir()

    def demand(trigger: str, requested: str, expires: str) -> dict:
        return CaptureDemand.create(
            consumer_id="rule_lawyer_dispute_forward",
            strategy_key="rule_lawyer.dispute_repricing",
            condition_id="condition-1",
            token_id=f"token-{trigger}",
            reason="dispute_first_seen",
            priority="P0",
            requested_at_utc=requested,
            expires_at_utc=expires,
            desired_transport="REST_WS",
            trigger_event_id=trigger,
        ).to_dict()

    before = demand("before", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z")
    owner = demand("owner", "2026-01-01T00:02:00Z", "2026-01-01T00:03:00Z")
    after = demand("after", "2026-01-01T00:04:00Z", "2026-01-01T00:05:00Z")
    (source / "capture_demands.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in (before, owner, after))
    )
    (source / "capture_receipts.jsonl").write_text(
        json.dumps(
            {
                "receipt_id": "receipt-owner",
                "demand_id": owner["demand_id"],
                "token_id": owner["token_id"],
                "resolution_status": "resolved_direct_token",
                "subscription_epoch_id": "epoch-1",
                "owner_producer": "owner",
                "owner_build_id": "sha",
                "accepted_at_utc": "2026-01-01T00:02:30Z",
            }
        )
        + "\n"
    )
    db = tmp_path / "dispute.db"
    materialize(source, db)
    health = audit_canonical(db)
    assert health["status"] == "error"
    assert health["metrics"]["pre_owner_expired_capture_gaps"] == 1
    assert health["metrics"]["operational_expired_capture_gaps"] == 1
    assert "pre_owner_capture_demands_without_receipt:1" in health["warnings"]
    assert (
        "expired_capture_demands_after_owner_without_successful_receipt:1"
        in health["errors"]
    )
