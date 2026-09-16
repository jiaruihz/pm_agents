"""Reconcile capture demand declarations with canonical-owner subscription epochs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from src.platform.market_data.identity import canonical_json_hash


@dataclass(frozen=True)
class CaptureReceipt:
    receipt_id: str
    demand_id: str
    token_id: str
    resolution_status: str
    subscription_epoch_id: str
    owner_producer: str
    owner_build_id: str
    accepted_at_utc: str
    schema_version: str = "polymarket_capture_receipt_v1"

    @classmethod
    def from_epoch(
        cls, demand: Mapping[str, Any], epoch: Mapping[str, Any]
    ) -> "CaptureReceipt":
        demand_id = str(demand.get("demand_id") or demand.get("capture_request_id") or "")
        status = str(demand.get("resolution_status") or "")
        epoch_id = str(epoch.get("subscription_epoch_id") or "")
        if not demand_id or not status or not epoch_id:
            raise ValueError("capture receipt requires demand, resolution and epoch identities")
        values = {
            "demand_id": demand_id,
            "token_id": str(demand.get("token_id") or ""),
            "resolution_status": status,
            "subscription_epoch_id": epoch_id,
            "owner_producer": str(epoch.get("producer") or ""),
            "owner_build_id": str(epoch.get("producer_build_id") or ""),
            "accepted_at_utc": str(epoch.get("started_at_utc") or ""),
        }
        if not values["token_id"] or not values["owner_producer"] or not values["accepted_at_utc"]:
            raise ValueError("capture receipt owner and token mapping are required")
        if status == "resolved_direct_token":
            token_ids = {str(value) for value in epoch.get("token_ids") or ()}
            token_row = (epoch.get("token_rows") or {}).get(values["token_id"]) or {}
            mapped_demand_ids = {
                str(value)
                for value in token_row.get("capture_demand_ids") or ()
                if value
            }
            if token_row.get("capture_demand_id"):
                mapped_demand_ids.add(str(token_row["capture_demand_id"]))
            if values["token_id"] not in token_ids:
                raise ValueError("resolved direct-token receipt missing subscription token")
            if demand_id not in mapped_demand_ids:
                raise ValueError("resolved direct-token receipt has mismatched token mapping")
        return cls(
            receipt_id=canonical_json_hash(
                {"schema_version": "polymarket_capture_receipt_v1", **values}
            ),
            **values,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "capture_succeeded": self.resolution_status == "resolved_direct_token",
        }
