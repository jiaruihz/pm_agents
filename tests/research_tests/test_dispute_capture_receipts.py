from __future__ import annotations

import json
import pytest

from src.platform.market_data.capture_demand import CaptureDemand
from src.platform.market_data.capture_receipt import CaptureReceipt
from src.strategies.rule_lawyer.capture_receipts import sync_capture_receipts


def test_capture_receipt_links_demand_to_single_owner_epoch(tmp_path) -> None:
    output = tmp_path / "forward"
    epochs = tmp_path / "epochs"
    output.mkdir()
    epochs.mkdir()
    demand = CaptureDemand.create(
        consumer_id="rule_lawyer_dispute_forward",
        strategy_key="rule_lawyer.dispute_repricing",
        condition_id="condition-1",
        token_id="token-1",
        reason="dispute_first_seen",
        priority="P0",
        requested_at_utc="2026-08-13T00:00:00Z",
        expires_at_utc="2026-08-13T00:10:00Z",
        desired_transport="REST_WS",
        trigger_event_id="trigger-1",
    ).to_dict()
    (output / "capture_demands.jsonl").write_text(json.dumps(demand) + "\n")
    resolved = {**demand, "resolution_status": "resolved_direct_token"}
    epoch = {
        "subscription_epoch_id": "epoch-1",
        "producer": "weather_data_feed_service.market_books_ws",
        "producer_build_id": "sha-1",
        "started_at_utc": "2026-08-13T00:00:01Z",
        "token_ids": ["token-1"],
        "token_rows": {"token-1": {"capture_demand_id": demand["demand_id"]}},
        "capture_demands": [resolved],
    }
    (epochs / "subscription_epochs_2026-08-13.jsonl").write_text(json.dumps(epoch) + "\n")
    first = sync_capture_receipts(output, epochs)
    second = sync_capture_receipts(output, epochs)
    assert first["new_receipts"] == 1
    assert second["new_receipts"] == 0
    assert second["epochs_scanned"] == 0
    receipt = json.loads((output / "capture_receipts.jsonl").read_text())
    assert receipt["demand_id"] == demand["demand_id"]
    assert receipt["subscription_epoch_id"] == "epoch-1"
    assert receipt["resolution_status"] == "resolved_direct_token"
    assert receipt["capture_succeeded"] is True


def test_resolved_receipt_requires_actual_subscription_mapping() -> None:
    demand = {
        "demand_id": "demand-1",
        "token_id": "token-1",
        "resolution_status": "resolved_direct_token",
    }
    epoch = {
        "subscription_epoch_id": "epoch-1",
        "producer": "owner",
        "producer_build_id": "sha",
        "started_at_utc": "2026-08-13T00:00:00Z",
        "token_ids": [],
        "token_rows": {},
    }
    with pytest.raises(ValueError, match="missing subscription token"):
        CaptureReceipt.from_epoch(demand, epoch)


def test_resolved_receipts_support_multiple_demands_for_one_token() -> None:
    epoch = {
        "subscription_epoch_id": "epoch-1",
        "producer": "owner",
        "producer_build_id": "sha",
        "started_at_utc": "2026-08-13T00:00:00Z",
        "token_ids": ["token-1"],
        "token_rows": {
            "token-1": {
                "capture_demand_id": "demand-1",
                "capture_demand_ids": ["demand-1", "demand-2"],
            }
        },
    }
    for demand_id in ("demand-1", "demand-2"):
        receipt = CaptureReceipt.from_epoch(
            {
                "demand_id": demand_id,
                "token_id": "token-1",
                "resolution_status": "resolved_direct_token",
            },
            epoch,
        )
        assert receipt.demand_id == demand_id


def test_rejected_owner_receipt_does_not_count_as_success(tmp_path) -> None:
    output = tmp_path / "forward"
    epochs = tmp_path / "epochs"
    output.mkdir()
    epochs.mkdir()
    demand = CaptureDemand.create(
        consumer_id="rule_lawyer_dispute_forward",
        strategy_key="rule_lawyer.dispute_repricing",
        condition_id="condition-1",
        token_id="token-1",
        reason="dispute_first_seen",
        priority="P0",
        requested_at_utc="2026-08-13T00:00:00Z",
        expires_at_utc="2026-08-13T00:10:00Z",
        desired_transport="REST_WS",
        trigger_event_id="trigger-1",
    ).to_dict()
    (output / "capture_demands.jsonl").write_text(json.dumps(demand) + "\n")
    epoch = {
        "subscription_epoch_id": "epoch-1",
        "producer": "owner",
        "producer_build_id": "sha",
        "started_at_utc": "2026-08-13T00:00:01Z",
        "capture_demands": [
            {**demand, "resolution_status": "global_active_token_budget_exceeded"}
        ],
    }
    (epochs / "subscription_epochs_2026-08-13.jsonl").write_text(json.dumps(epoch) + "\n")
    summary = sync_capture_receipts(output, epochs)
    assert summary["total_receipts"] == 1
    assert summary["successful_demands"] == 0
