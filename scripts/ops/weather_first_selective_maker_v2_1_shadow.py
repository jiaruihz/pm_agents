#!/usr/bin/env python3
"""Weather-first Selective Maker V2.1 zero-notional forward materializer.

This process is deliberately not an execution runner.  It tails the frozen
signal/action and model journals, records a strict as-of book attempt, and
materializes the pure three-arm decision.  It has no venue/client imports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.platform.market_data.capture_demand import CaptureDemand
from src.platform.market_data.capture_receipt import CaptureReceipt
from src.strategies.weather_edge_v1.execution.contracts import BookLevel, MarketBook
from src.strategies.weather_edge_v1.execution.economics import estimate_polymarket_v2_fee
from src.strategies.weather_edge_v1.execution.selective_maker import PITMarketState, RouteInput, route_candidate

FORMAL_FORWARD_START = "2026-08-31T00:00:00Z"
DEFAULT_ACTIONS = Path("/Volumes/jrs/pm_agents/runtime/weather_edge_v1/current_yes_core_carry_market_state_shadow_v3_forward_20260824a/maker_policy_actions.jsonl")
DEFAULT_PACKETS = Path("/Volumes/jrs/pm_agents/runtime/weather_edge_v1/current_yes_core_carry_tiny_live_v2/decision_packets.jsonl")
DEFAULT_BOOKS = Path("/Volumes/jrs/weather_data_feed_service_runtime/market_books/latest.json")
DEFAULT_SUBSCRIPTION_EPOCHS = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/market_books/ws_incremental/subscription_epochs"
)
DEFAULT_EXECUTION_EVIDENCE = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/market_books/ws_incremental/execution_evidence_v1/public_books"
)
DEFAULT_OUTPUT = Path("/Volumes/jrs/pm_agents/runtime/weather_edge_v1/weather_first_selective_maker_v2_1")
SAFETY = {"execution_mode": "zero_notional_shadow", "live_authority": False, "actual_notional": 0,
          "trade_intents": 0, "orders": 0, "fills": 0, "exchange_calls": 0, "venue_call_allowed": False}

VALID_WS_SEQUENCE_STATUSES = {
    "exchange_sequence_verified",
    "exchange_sequence_unavailable",
}
VALID_WS_GAP_STATUSES = {
    "sequence_and_best_quote_parity_checked",
    "best_quote_parity_checked_sequence_unavailable",
}


def utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    answer = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            answer.append(item)
    return answer


def partition_rows(
    root: Path,
    clocks: Iterable[datetime],
    *,
    hourly: bool,
) -> list[dict[str, Any]]:
    """Read only the UTC date/hour partitions that can affect this loop."""

    if root.is_file():
        return rows(root)
    if not root.is_dir():
        return []
    clock_list = list(clocks)
    dates = {clock.astimezone(timezone.utc).strftime("%Y-%m-%d") for clock in clock_list}
    hours = {
        clock.astimezone(timezone.utc).strftime("%Y%m%d_%H") for clock in clock_list
    }
    paths: set[Path] = set()
    for date_text in dates:
        day_root = root / date_text
        if day_root.is_dir():
            if hourly:
                for hour in hours:
                    paths.update(day_root.glob(f"*{hour}*.jsonl"))
            else:
                paths.update(day_root.glob("*.jsonl"))
        # Subscription epochs are date-partitioned by filename, not directory.
        paths.update(root.glob(f"*{date_text}*.jsonl"))
    # Unit fixtures may use an unpartitioned directory.
    if not paths:
        paths.update(root.glob("*.jsonl"))
    answer: list[dict[str, Any]] = []
    for path in sorted(paths):
        answer.extend(rows(path))
    return answer


def _raw_ref_clock(row: Mapping[str, Any]) -> datetime | None:
    ref = row.get("delta_last_raw_frame_ref") or row.get("baseline_raw_frame_ref")
    if not isinstance(ref, Mapping):
        return None
    return utc(ref.get("received_at_utc"))


def _ws_evidence_quality_blockers(row: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if row.get("schema_version") != "weather_public_book_evidence_v1":
        blockers.append("capture_checkpoint_schema_invalid")
    if str(row.get("sequence_status") or "") not in VALID_WS_SEQUENCE_STATUSES:
        blockers.append("capture_checkpoint_sequence_status_invalid")
    if str(row.get("gap_detection_status") or "") not in VALID_WS_GAP_STATUSES:
        blockers.append("capture_checkpoint_gap_or_parity_invalid")
    for field in (
        "subscription_epoch_id",
        "token_map_id",
        "capture_policy_id",
        "subscription_set_id",
        "producer_build_id",
        "selector_version",
        "raw_lineage_id",
        "baseline_raw_frame_ref",
    ):
        if not row.get(field):
            blockers.append(f"capture_checkpoint_{field}_missing")
    if _raw_ref_clock(row) is None:
        blockers.append("capture_checkpoint_raw_clock_missing")
    return blockers


def capture_receipt(
    demand: Mapping[str, Any],
    *,
    token: str,
    route_clock: datetime,
    epochs: Iterable[Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Return the latest exact owner subscription receipt available by route time."""

    candidates: list[tuple[datetime, dict[str, Any]]] = []
    demand_id = str(demand.get("demand_id") or "")
    requested = utc(demand.get("requested_at_utc"))
    expires = utc(demand.get("expires_at_utc"))
    for epoch in epochs:
        accepted = utc(epoch.get("started_at_utc"))
        if (
            epoch.get("schema_version")
            != "weather_market_books_ws_subscription_epoch_v2"
            or epoch.get("producer") != "weather_data_feed_service.market_books_ws"
            or not epoch.get("producer_build_id")
            or not epoch.get("selector_version")
            or not epoch.get("token_map_id")
            or not epoch.get("capture_policy_id")
            or not epoch.get("subscription_set_id")
            or accepted is None
            or requested is None
            or expires is None
            or accepted < requested
            or accepted >= expires
            or accepted > route_clock
        ):
            continue
        resolved = next(
            (
                row
                for row in epoch.get("capture_demands") or ()
                if isinstance(row, Mapping)
                and str(row.get("demand_id") or "") == demand_id
            ),
            None,
        )
        if not isinstance(resolved, Mapping):
            continue
        if (
            resolved.get("consumer_id") != "weather_first_selective_maker_v2_1"
            or resolved.get("strategy_key") != "weather_first_selective_maker_v2_1"
            or token not in {str(value) for value in resolved.get("resolved_token_ids") or ()}
        ):
            continue
        try:
            receipt = CaptureReceipt.from_epoch(resolved, epoch).to_dict()
        except ValueError:
            continue
        if receipt.get("capture_succeeded") is not True or receipt.get("token_id") != token:
            continue
        candidates.append((accepted, receipt))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def checkpoint_receipt(
    demand: Mapping[str, Any],
    *,
    token: str,
    route_clock: datetime,
    subscription_epoch_id: str,
    evidence_rows: Iterable[Mapping[str, Any]],
    grace_seconds: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Verify the exact requested t30 checkpoint was actually materialized."""

    demand_id = str(demand.get("demand_id") or "")
    candidates: list[tuple[datetime, Mapping[str, Any]]] = []
    invalid: list[str] = []
    for row in evidence_rows:
        checkpoint = row.get("checkpoint_ref")
        if not isinstance(checkpoint, Mapping):
            continue
        try:
            offset = int(checkpoint.get("offset_seconds"))
        except (TypeError, ValueError):
            invalid.append("capture_checkpoint_offset_invalid")
            continue
        if (
            row.get("evidence_reason") != "requested_strategy_checkpoint"
            or str(row.get("token_id") or "") != token
            or str(row.get("subscription_epoch_id") or "") != subscription_epoch_id
            or str(checkpoint.get("demand_id") or "") != demand_id
            or offset != 30
        ):
            continue
        requested = utc(checkpoint.get("requested_checkpoint_at_utc"))
        observed = utc(row.get("book_observed_at_utc"))
        if requested != route_clock or observed is None:
            invalid.append("capture_checkpoint_route_clock_mismatch")
            continue
        expected_checkpoint_id = hashlib.sha256(
            canonical(
                {
                    "demand_id": demand_id,
                    "token_id": token,
                    "offset_seconds": 30,
                    "due_at_utc": iso(route_clock),
                }
            ).encode()
        ).hexdigest()
        if checkpoint.get("checkpoint_id") != expected_checkpoint_id:
            invalid.append("capture_checkpoint_identity_invalid")
            continue
        if observed > route_clock + timedelta(seconds=grace_seconds):
            invalid.append("capture_checkpoint_materialization_late")
            continue
        quality = _ws_evidence_quality_blockers(row)
        if quality:
            invalid.extend(quality)
            continue
        candidates.append((observed, row))
    if not candidates:
        return None, sorted(set(invalid))
    observed, row = min(candidates, key=lambda item: item[0])
    checkpoint = row["checkpoint_ref"]
    raw_clock = _raw_ref_clock(row)
    return {
        "schema_version": row.get("schema_version"),
        "evidence_id": row.get("evidence_id"),
        "checkpoint_id": checkpoint.get("checkpoint_id"),
        "demand_id": demand_id,
        "token_id": token,
        "subscription_epoch_id": subscription_epoch_id,
        "requested_checkpoint_at_utc": iso(route_clock),
        "book_observed_at_utc": iso(observed),
        "raw_state_at_utc": None if raw_clock is None else iso(raw_clock),
        "materialization_lag_ms": checkpoint.get("materialization_lag_ms"),
        "sequence_status": row.get("sequence_status"),
        "gap_detection_status": row.get("gap_detection_status"),
        "raw_lineage_id": row.get("raw_lineage_id"),
        "baseline_raw_frame_ref": row.get("baseline_raw_frame_ref"),
        "delta_last_raw_frame_ref": row.get("delta_last_raw_frame_ref"),
    }, []


def action_identity(action: Mapping[str, Any]) -> str:
    return str(action.get("signal_id") or action.get("event_id") or "")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def action_policy(action: Mapping[str, Any]) -> Mapping[str, Any]:
    policy = action.get("policy")
    return policy if isinstance(policy, Mapping) else action


def action_snapshot(items: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    """Return last append revision per identity, fail closed on material conflict."""
    selected: dict[str, dict[str, Any]] = {}
    blocked: set[str] = set()
    for item in items:
        if item.get("event_kind") != "first_positive" or str(item.get("stage") or "") != "t30":
            continue
        ident = action_identity(item)
        if not ident:
            continue
        prior = selected.get(ident)
        if prior:
            for field in (
                "event_id",
                "signal_id",
                "city",
                "target_date",
                "signal_event_at_utc",
                "stage_due_at_utc",
            ):
                if prior.get(field) not in (None, "") and item.get(field) not in (None, "") and prior[field] != item[field]:
                    blocked.add(ident)
            prior_policy = action_policy(prior)
            item_policy = action_policy(item)
            for field in (
                "action",
                "information_horizon_seconds",
                "requested_shares",
                "reason_codes",
            ):
                if prior_policy.get(field) is not None and item_policy.get(field) is not None and prior_policy[field] != item_policy[field]:
                    blocked.add(ident)
        selected[ident] = item
    return list(selected.values()), blocked


def packet_index(items: Iterable[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    index: dict[str, dict[str, Any]] = {}; conflicts: set[str] = set()
    for item in items:
        ident = str(item.get("packet_id") or item.get("event_id") or "")
        if not ident:
            continue
        prior = index.get(ident)
        if prior:
            a = ((prior.get("trigger") or {}).get("payload") or {})
            b = ((item.get("trigger") or {}).get("payload") or {})
            for field in (
                "artifact_hash",
                "model_version",
                "feature_schema_version",
                "strategy_id",
                "created_at_utc",
                "model_probability_hold",
                "current_condition_id",
                "current_yes_token_id",
            ):
                if a.get(field) is not None and b.get(field) is not None and a.get(field) != b.get(field):
                    conflicts.add(ident)
        index[ident] = item
    return index, conflicts


def _book_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list): return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict): return []
    for key in ("records", "books", "data"):
        value = payload.get(key)
        if isinstance(value, list): return [x for x in value if isinstance(x, dict)]
    return [payload]


def _book_file_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        answer: list[dict[str, Any]] = []
        for item in rows(path):
            book = item.get("book")
            if isinstance(book, dict):
                answer.append(book)
        return answer
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return _book_records(payload)


def taker_fee_parameters(
    raw: Mapping[str, Any],
    *,
    fallback_rate: Decimal,
    fallback_exponent: Decimal,
) -> tuple[dict[str, Any], list[str]]:
    """Prefer captured market fee truth; type an absent-market fallback explicitly."""

    containers = [raw]
    for key in ("raw", "fee_metadata", "clob_market_info", "market_info"):
        value = raw.get(key)
        if isinstance(value, Mapping):
            containers.append(value)
    fee_details = next(
        (
            value.get("fd")
            for value in containers
            if isinstance(value.get("fd"), Mapping)
        ),
        None,
    )
    enabled_present = any(
        "feesEnabled" in value or "fees_enabled" in value for value in containers
    )
    enabled = next(
        (
            value.get("feesEnabled", value.get("fees_enabled"))
            for value in containers
            if "feesEnabled" in value or "fees_enabled" in value
        ),
        None,
    )
    if fee_details is not None:
        if any(fee_details.get(key) is None for key in ("r", "e", "to")):
            return {}, ["per_market_fee_metadata_invalid"]
        try:
            rate = Decimal(str(fee_details["r"]))
            exponent = Decimal(str(fee_details["e"]))
        except (KeyError, TypeError, ValueError):
            return {}, ["per_market_fee_metadata_invalid"]
        if not rate.is_finite() or not exponent.is_finite() or rate < 0 or exponent <= 0:
            return {}, ["per_market_fee_metadata_invalid"]
        return {
            "rate": str(rate),
            "exponent": str(exponent),
            "source": "captured_clob_market_info.fd",
            "fees_enabled": enabled,
            "raw_fee_details": dict(fee_details),
            "per_market_metadata_present": True,
            "realized": False,
            "profitability_credit_allowed": False,
        }, []
    if enabled_present:
        if enabled is False:
            return {
                "rate": "0",
                "exponent": "1",
                "source": "captured_market_fees_disabled",
                "fees_enabled": False,
                "raw_fee_details": None,
                "per_market_metadata_present": True,
                "realized": False,
                "profitability_credit_allowed": False,
            }, []
        return {}, ["per_market_fee_metadata_incomplete"]
    return {
        "rate": str(fallback_rate),
        "exponent": str(fallback_exponent),
        "source": "official_weather_category_forecast_fallback",
        "fees_enabled": None,
        "raw_fee_details": None,
        "per_market_metadata_present": False,
        "evidence_limitation": "per_market_fee_metadata_missing",
        "realized": False,
        "profitability_credit_allowed": False,
    }, []


def asof_book(
    path: Path,
    token: str,
    clock: datetime,
    max_age: int,
    *,
    fallback_fee_rate: Decimal,
    fallback_fee_exponent: Decimal,
) -> tuple[MarketBook | None, dict[str, Any], list[str]]:
    blockers: list[str] = []
    records = _book_file_records(path)
    if not records:
        return None, {}, ["market_book_evidence_unreadable_or_empty"]
    usable: list[tuple[datetime, dict[str, Any]]] = []
    for raw in records:
        if str(raw.get("token_id") or raw.get("asset_id") or "") != token: continue
        available = utc(raw.get("available_at_utc") or raw.get("fetched_at_utc"))
        if available and available <= clock: usable.append((available, raw))
    if not usable: return None, {}, ["pit_book_missing_or_future_only"]
    available, raw = max(usable, key=lambda pair: pair[0])
    age = (clock - available).total_seconds()
    book_payload = raw.get("raw") if isinstance(raw.get("raw"), Mapping) else raw
    evidence = {"available_at_utc": iso(available), "route_clock_utc": iso(clock), "book_age_seconds": age,
                "book_capture_id": raw.get("capture_id") or raw.get("book_capture_id"),
                "book_batch_id": raw.get("request_batch_capture_id") or raw.get("batch_id") or raw.get("book_batch_id"),
                "book_producer_identity": raw.get("producer_build_id") or raw.get("producer_identity") or raw.get("producer_id"),
                "raw_payload_hash": raw.get("raw_payload_hash"),
                "exchange_book_hash": raw.get("exchange_book_hash") or book_payload.get("hash")}
    if age > max_age:
        return None, evidence, ["pit_book_stale_over_max_age"]
    schema = str(raw.get("schema_version") or "")
    if schema == "weather_orderbook_capture_v3":
        quality = {
            "kind": "rest_exact_response",
            "status": raw.get("status"),
            "source_lineage_status": raw.get("source_lineage_status"),
            "clock_lineage_status": raw.get("clock_lineage_status"),
        }
        evidence["book_quality"] = quality
        if raw.get("status") != "ok":
            return None, evidence, ["pit_rest_book_status_not_ok"]
        if raw.get("source_lineage_status") != "collector_exact_orderbook_response_v3":
            return None, evidence, ["pit_rest_book_source_lineage_invalid"]
        if raw.get("clock_lineage_status") != "collector_exact_response_clock":
            return None, evidence, ["pit_rest_book_clock_lineage_invalid"]
        if not raw.get("book_capture_id") or not raw.get("raw_payload_hash"):
            return None, evidence, ["pit_rest_book_identity_missing"]
    elif schema == "weather_public_book_evidence_v1":
        quality_blockers = _ws_evidence_quality_blockers(raw)
        evidence["book_quality"] = {
            "kind": "ws_reconstruction",
            "sequence_status": raw.get("sequence_status"),
            "gap_detection_status": raw.get("gap_detection_status"),
            "subscription_epoch_id": raw.get("subscription_epoch_id"),
        }
        if quality_blockers:
            return None, evidence, quality_blockers
    else:
        return None, evidence, ["pit_book_schema_unregistered"]
    fee_parameters, fee_blockers = taker_fee_parameters(
        raw,
        fallback_rate=fallback_fee_rate,
        fallback_exponent=fallback_fee_exponent,
    )
    evidence["taker_fee_parameters"] = fee_parameters
    if fee_blockers:
        return None, evidence, fee_blockers
    bids_raw = book_payload.get("bids") or []; asks_raw = book_payload.get("asks") or []
    def levels(values: list[Any], reverse: bool) -> tuple[BookLevel, ...]:
        parsed = []
        for value in values:
            price = value.get("price") if isinstance(value, Mapping) else value[0] if isinstance(value, (list, tuple)) and len(value) >= 2 else None
            size = (value.get("size") or value.get("quantity")) if isinstance(value, Mapping) else value[1] if isinstance(value, (list, tuple)) and len(value) >= 2 else None
            try:
                if price is not None and size is not None and Decimal(str(price)) > 0 and Decimal(str(price)) < 1 and Decimal(str(size)) > 0:
                    parsed.append(BookLevel(price, size))
            except Exception:
                continue
        return tuple(sorted(parsed, key=lambda x: x.price, reverse=reverse))
    bids = levels(bids_raw, True)
    asks = levels(asks_raw, False)
    if not bids or not asks:
        return None, evidence, ["pit_book_one_sided"]
    tick_size = book_payload.get("tick_size") or raw.get("tick_size")
    minimum_order_shares = book_payload.get("min_order_size") or raw.get("minimum_order_shares")
    if tick_size in (None, "") or minimum_order_shares in (None, ""):
        return None, evidence, ["pit_book_venue_constraints_missing"]
    try:
        book = MarketBook(token_id=token, status=str(raw.get("status") or "ok"), fetched_at_utc=iso(available),
            venue_timestamp_utc=raw.get("exchange_book_ts_utc") or raw.get("venue_timestamp_utc"), book_epoch_ref=str(raw.get("book_capture_id") or raw.get("book_epoch_ref") or book_payload.get("hash") or hashlib.sha256(canonical(raw).encode()).hexdigest()),
            tick_size=str(tick_size), tick_size_source=str(raw.get("tick_size_source") or "market_books_capture"), minimum_order_shares=str(minimum_order_shares), bids=bids, asks=asks)
    except Exception as exc:
        return None, evidence, [f"pit_book_invalid:{type(exc).__name__}"]
    return book, evidence, blockers


def forecast_taker_fee(book: MarketBook, shares: Decimal, rate: Decimal, exponent: Decimal) -> Decimal:
    remaining = shares
    fee = Decimal("0")
    for level in book.asks:
        take = min(remaining, level.size)
        if take > 0:
            fee += estimate_polymarket_v2_fee(
                shares=take,
                rate=rate,
                price=level.price,
                exponent=exponent,
            )
            remaining -= take
        if remaining <= 0:
            break
    return fee


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_once(path: Path, record: Mapping[str, Any], identities: set[str]) -> bool:
    ident = str(record["identity"])
    if ident in identities: return False
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False, default=str) + "\n")
    identities.add(ident); return True


def existing_ids(root: Path, name: str) -> set[str]:
    return {str(x.get("identity") or x.get("demand_id") or "") for x in rows(root / name)}


def packet_payload(packet: Mapping[str, Any]) -> Mapping[str, Any]:
    trigger = packet.get("trigger")
    if not isinstance(trigger, Mapping):
        return {}
    payload = trigger.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def packet_clock(packet: Mapping[str, Any]) -> datetime | None:
    payload = packet_payload(packet)
    return utc(
        payload.get("created_at_utc")
        or payload.get("current_yes_book_available_at_utc")
        or payload.get("current_yes_book_fetched_at_utc")
        or payload.get("snapshot_available_at_utc")
        or payload.get("as_of_ts_utc")
    )


def capture_demand_id(*, condition: str, token: str, event_id: str) -> str:
    return CaptureDemand.identity(
        consumer_id="weather_first_selective_maker_v2_1",
        strategy_key="weather_first_selective_maker_v2_1",
        condition_id=condition,
        token_id=token,
        reason="v2_1_pit_route_clock_capture",
        trigger_event_id=event_id,
    )


def append_capture_demand(
    *,
    output: Path,
    packet: Mapping[str, Any],
    event_id: str,
    route_clock: datetime | None,
    requested_at: datetime,
    existing_demands: list[dict[str, Any]],
    identities: set[str],
    now: datetime,
    ttl_minutes: int,
    max_daily_demands: int,
    max_active_tokens: int,
) -> tuple[bool, str | None]:
    payload = packet_payload(packet)
    token = str(payload.get("current_yes_token_id") or "")
    condition = str(
        payload.get("current_condition_id")
        or payload.get("condition_id")
        or payload.get("market_condition_id")
        or ""
    )
    if not token or not condition or not event_id:
        return False, "capture_identity_missing"
    demand_id = capture_demand_id(condition=condition, token=token, event_id=event_id)
    if demand_id in identities:
        return False, None
    if requested_at > now:
        return False, "capture_packet_clock_in_future"
    if requested_at + timedelta(minutes=ttl_minutes) <= now:
        return False, "capture_window_expired_before_materializer"
    today_demands = [
        item
        for item in existing_demands
        if str(item.get("requested_at_utc") or "")[:10] == iso(requested_at)[:10]
    ]
    active_tokens = {
        str(item.get("token_id") or "")
        for item in existing_demands
        if (utc(item.get("expires_at_utc")) or now) > now
    }
    if len(today_demands) >= max_daily_demands:
        return False, "capture_daily_signal_cap_reached"
    if token not in active_tokens and len(active_tokens) >= max_active_tokens:
        return False, "capture_active_token_cap_reached"
    demand = CaptureDemand.create(
        consumer_id="weather_first_selective_maker_v2_1",
        strategy_key="weather_first_selective_maker_v2_1",
        condition_id=condition,
        token_id=token,
        reason="v2_1_pit_route_clock_capture",
        priority="P1",
        requested_at_utc=iso(requested_at),
        expires_at_utc=iso(requested_at + timedelta(minutes=ttl_minutes)),
        desired_transport="REST_WS",
        requested_checkpoints_seconds=(0, 10, 30, 60, 120, 300),
        trigger_event_id=event_id,
        metadata={
            "route_clock_utc": None if route_clock is None else iso(route_clock),
            "execution_mode": "zero_notional_shadow",
            "source_packet_hash": hashlib.sha256(canonical(packet).encode()).hexdigest(),
        },
    ).to_dict()
    demand["identity"] = demand["demand_id"]
    if not append_once(output / "capture_demands.jsonl", demand, identities):
        return False, None
    existing_demands.append(demand)
    return True, None


def journal_book_snapshots(
    *,
    output: Path,
    books_path: Path,
    tokens: set[str],
    identities: set[str],
    now: datetime,
) -> int:
    written = 0
    for book in _book_file_records(books_path):
        token = str(book.get("token_id") or book.get("asset_id") or "")
        if not token or token not in tokens:
            continue
        available = utc(book.get("available_at_utc") or book.get("fetched_at_utc"))
        if available is None or available > now:
            continue
        capture_ref = str(
            book.get("book_capture_id")
            or book.get("capture_id")
            or book.get("raw_payload_hash")
            or hashlib.sha256(canonical(book).encode()).hexdigest()
        )
        identity = hashlib.sha256(
            canonical({"token_id": token, "capture_ref": capture_ref}).encode()
        ).hexdigest()
        record = {
            **SAFETY,
            "identity": identity,
            "schema_version": "weather_first_selective_maker_v2_1_book_snapshot_v1",
            "token_id": token,
            "available_at_utc": iso(available),
            "book": book,
        }
        if append_once(output / "book_snapshots.jsonl", record, identities):
            written += 1
    return written


def candidate(action: Mapping[str, Any]) -> bool:
    policy = action_policy(action)
    reasons = policy.get("reason_codes") or policy.get("reason") or ()
    if isinstance(reasons, str):
        reasons = (reasons,)
    return (
        policy.get("action") == "place_probe_maker"
        and "post_trigger_acute_window_admitted_probe" in reasons
        and int(policy.get("information_horizon_seconds") or policy.get("horizon_seconds") or -1) == 30
    )


def action_route_clock(action: Mapping[str, Any]) -> datetime | None:
    due = utc(action.get("stage_due_at_utc") or action.get("route_clock_utc"))
    if due is not None:
        return due
    event_at = utc(action.get("signal_event_at_utc"))
    policy = action_policy(action)
    horizon = policy.get("information_horizon_seconds") or policy.get("horizon_seconds")
    try:
        return None if event_at is None or horizon is None else event_at + timedelta(seconds=int(horizon))
    except (TypeError, ValueError):
        return None


def run_once(
    actions_path: Path,
    packets_path: Path,
    books_path: Path,
    output: Path,
    *,
    subscription_epochs_root: Path = DEFAULT_SUBSCRIPTION_EPOCHS,
    execution_evidence_root: Path = DEFAULT_EXECUTION_EVIDENCE,
    now: datetime | None = None,
    maximum_book_age_seconds: int = 5,
    taker_fee_rate: Decimal = Decimal("0.05"),
    taker_fee_exponent: Decimal = Decimal("1"),
    capture_ttl_minutes: int = 10,
    capture_checkpoint_grace_seconds: int = 20,
    max_daily_capture_demands: int = 32,
    max_active_capture_tokens: int = 16,
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc); forward = utc(FORMAL_FORWARD_START); assert forward
    actions, action_conflicts = action_snapshot(rows(actions_path)); packets, packet_conflicts = packet_index(rows(packets_path))
    identities = {
        name.removesuffix(".jsonl"): existing_ids(output, name)
        for name in (
            "decisions.jsonl",
            "route_arms.jsonl",
            "denominator.jsonl",
            "capture_demands.jsonl",
            "book_snapshots.jsonl",
        )
    }
    counts = {
        "warming": 0,
        "denominator": 0,
        "decisions_written": 0,
        "demands_written": 0,
        "book_snapshots_written": 0,
        "capture_receipts": 0,
        "checkpoint_receipts": 0,
        "evidence_pending": 0,
        "future_actions": 0,
    }
    existing_demands = rows(output / "capture_demands.jsonl")
    packet_tokens: set[str] = set()
    capture_blockers: list[dict[str, str]] = []
    # Packets arrive before t30 actions. Demand them immediately; waiting for
    # the action would introduce a future-book look-ahead at the route clock.
    for event_id, packet in sorted(packets.items()):
        observed = packet_clock(packet)
        if observed is None or observed < forward or observed > now:
            continue
        payload = packet_payload(packet)
        token = str(payload.get("current_yes_token_id") or "")
        if token and now < observed + timedelta(minutes=capture_ttl_minutes):
            packet_tokens.add(token)
        wrote, blocker = append_capture_demand(
            output=output,
            packet=packet,
            event_id=event_id,
            route_clock=observed + timedelta(seconds=30),
            requested_at=max(observed, forward),
            existing_demands=existing_demands,
            identities=identities["capture_demands"],
            now=now,
            ttl_minutes=capture_ttl_minutes,
            max_daily_demands=max_daily_capture_demands,
            max_active_tokens=max_active_capture_tokens,
        )
        counts["demands_written"] += int(wrote)
        if blocker:
            capture_blockers.append({"event_id": event_id, "blocker": blocker})
    counts["book_snapshots_written"] = journal_book_snapshots(
        output=output,
        books_path=books_path,
        tokens=packet_tokens,
        identities=identities["book_snapshots"],
        now=now,
    )
    relevant_clocks: list[datetime] = []
    for action in actions:
        event_id = str(action.get("event_id") or "")
        packet = packets.get(event_id)
        source_hash = hashlib.sha256(
            canonical({"action": action, "packet": packet}).encode()
        ).hexdigest()
        revision_identity = f"{action_identity(action)}:{source_hash[:16]}"
        value = action_route_clock(action)
        if (
            revision_identity not in identities["decisions"]
            and value is not None
            and value >= forward
        ):
            relevant_clocks.append(value)
    evidence_partition_clocks = [
        value
        for clock in relevant_clocks
        for value in (
            clock,
            clock + timedelta(seconds=capture_checkpoint_grace_seconds),
        )
    ] or [now]
    subscription_epochs = partition_rows(
        subscription_epochs_root,
        evidence_partition_clocks,
        hourly=False,
    )
    public_book_evidence = partition_rows(
        execution_evidence_root,
        evidence_partition_clocks,
        hourly=True,
    )
    for action in actions:
        ident = action_identity(action)
        clock = action_route_clock(action)
        observed = utc(action.get("observed_at_utc") or action.get("available_at_utc") or action.get("created_at_utc"))
        eligibility_clock = clock or observed
        if eligibility_clock is None or eligibility_clock < forward:
            counts["warming"] += 1; continue
        if (clock is not None and clock > now) or (observed is not None and observed > now):
            counts["future_actions"] += 1
            capture_blockers.append({"event_id": ident, "blocker": "action_clock_in_future"})
            continue
        counts["denominator"] += 1
        event_id = str(action.get("event_id") or "")
        packet = packets.get(event_id); payload = packet_payload(packet or {})
        token = payload.get("current_yes_token_id")
        block = []
        if clock is None: block.append("route_clock_missing")
        if ident in action_conflicts: block.append("action_revision_conflict")
        if str(action.get("event_id") or "") in packet_conflicts: block.append("packet_revision_conflict")
        if not packet or token is None or payload.get("model_probability_hold") is None: block.append("packet_fair_value_or_token_missing")
        state = "transient_dislocation_candidate" if candidate(action) else "unknown"
        source_revision_hash = hashlib.sha256(canonical({"action": action, "packet": packet}).encode()).hexdigest()
        revision_identity = f"{ident}:{source_revision_hash[:16]}"
        if revision_identity in identities["decisions"]:
            continue
        base = {**SAFETY, "identity": revision_identity, "logical_identity": ident, "source_revision_hash": source_revision_hash, "event_id": action.get("event_id"), "signal_id": action.get("signal_id"), "route_clock_utc": None if clock is None else iso(clock), "candidate_state": state, "formal_forward_start_utc": FORMAL_FORWARD_START, "blockers": list(block)}
        if packet:
            wrote, capture_blocker = append_capture_demand(
                output=output,
                packet=packet,
                event_id=event_id,
                route_clock=clock,
                requested_at=max(observed or now, forward),
                existing_demands=existing_demands,
                identities=identities["capture_demands"],
                now=now,
                ttl_minutes=capture_ttl_minutes,
                max_daily_demands=max_daily_capture_demands,
                max_active_tokens=max_active_capture_tokens,
            )
            counts["demands_written"] += int(wrote)
            if capture_blocker:
                block.append(capture_blocker)
        append_once(output / "denominator.jsonl", base, identities["denominator"])
        if clock is not None and now < clock + timedelta(seconds=capture_checkpoint_grace_seconds):
            counts["evidence_pending"] += 1
            continue
        evidence: dict[str, Any] = {}
        owner_receipt: dict[str, Any] | None = None
        completed_checkpoint: dict[str, Any] | None = None
        if packet and token and clock is not None:
            condition = str(
                payload.get("current_condition_id")
                or payload.get("condition_id")
                or payload.get("market_condition_id")
                or ""
            )
            demand_id = capture_demand_id(
                condition=condition,
                token=str(token),
                event_id=event_id,
            ) if condition else ""
            demand = next(
                (
                    item
                    for item in existing_demands
                    if str(item.get("demand_id") or "") == demand_id
                ),
                None,
            )
            if not demand:
                block.append("capture_demand_missing")
            else:
                owner_receipt = capture_receipt(
                    demand,
                    token=str(token),
                    route_clock=clock,
                    epochs=subscription_epochs,
                )
                if owner_receipt is None:
                    block.append("capture_receipt_missing")
                else:
                    counts["capture_receipts"] += 1
                    completed_checkpoint, receipt_blockers = checkpoint_receipt(
                        demand,
                        token=str(token),
                        route_clock=clock,
                        subscription_epoch_id=str(owner_receipt["subscription_epoch_id"]),
                        evidence_rows=public_book_evidence,
                        grace_seconds=capture_checkpoint_grace_seconds,
                    )
                    if completed_checkpoint is None:
                        block.extend(
                            receipt_blockers or ["capture_checkpoint_receipt_missing"]
                        )
                    else:
                        counts["checkpoint_receipts"] += 1
        decision: dict[str, Any] = {
            **base,
            "blockers": list(block),
            "token_id": token,
            "fair_value": payload.get("model_probability_hold"),
            "capture_receipt": owner_receipt,
            "capture_checkpoint_receipt": completed_checkpoint,
            "pit_book": evidence,
            "selected_route": "skip",
        }
        if not block and token and clock is not None:
            book, evidence, book_blockers = asof_book(
                output / "book_snapshots.jsonl",
                str(token),
                clock,
                maximum_book_age_seconds,
                fallback_fee_rate=taker_fee_rate,
                fallback_fee_exponent=taker_fee_exponent,
            )
            decision["pit_book"] = evidence; decision["blockers"].extend(book_blockers)
            if book:
                try:
                    policy_shares = Decimal(str(action_policy(action).get("requested_shares") or "0"))
                    shares = max(book.minimum_order_shares, policy_shares)
                    fee_parameters = evidence["taker_fee_parameters"]
                    fee_rate = Decimal(str(fee_parameters["rate"]))
                    fee_exponent = Decimal(str(fee_parameters["exponent"]))
                    fee = forecast_taker_fee(book, shares, fee_rate, fee_exponent)
                    pit = PITMarketState(candidate_id=revision_identity, feature_epoch_ref=f"{packet.get('packet_id') or action.get('event_id')}:{book.book_epoch_ref}", market_book=book, book_age_sec=Decimal(str(evidence["book_age_seconds"])), max_book_age_sec=maximum_book_age_seconds, coverage_complete=(state != "unknown"), dislocation_observed=(state == "transient_dislocation_candidate"))
                    route = route_candidate(RouteInput(pit=pit, venue_side="BUY", shares=shares, fee_schedule=None, venue_capabilities=None, fair_value=str(payload["model_probability_hold"]), taker_fee_estimate=fee, transition_hazard_estimate=None, passive_fill_estimate=None))
                    decision.update({"selected_route": route.selected_route, "route": route.to_json(), "target_shares": str(shares), "taker_fee_forecast": {**fee_parameters, "amount_usd": str(fee), "status": "forecast_not_realized"}})
                except Exception as exc: decision["blockers"].append(f"route_contract_failed:{type(exc).__name__}")
        # Same identity is intentionally shared by decision and arm journals, each separate namespace.
        if append_once(output / "decisions.jsonl", decision, identities["decisions"]): counts["decisions_written"] += 1
        append_once(output / "route_arms.jsonl", {**SAFETY, "identity": revision_identity, "logical_identity": ident, "source_revision_hash": source_revision_hash, "route_clock_utc": None if clock is None else iso(clock), "maker": (decision.get("route") or {}).get("maker"), "taker": (decision.get("route") or {}).get("taker"), "skip": {"status": "available", "incremental_cost": "0", "incremental_pnl": "0"}, "selected_route": decision["selected_route"], "blockers": decision["blockers"]}, identities["route_arms"])
    missing_inputs = [str(path) for path in (actions_path, packets_path, books_path) if not path.is_file()]
    missing_inputs.extend(
        str(path)
        for path in (subscription_epochs_root, execution_evidence_root)
        if not path.exists()
    )
    status = "blocked" if missing_inputs else "warming" if now < forward else "ok"
    latest = {**SAFETY, "status": status, "schema_version": "weather_first_selective_maker_v2_1_shadow_latest_v1", "updated_at_utc": iso(now), "formal_forward_start_utc": FORMAL_FORWARD_START, "actions_path": str(actions_path), "packets_path": str(packets_path), "books_path": str(books_path), "subscription_epochs_root": str(subscription_epochs_root), "execution_evidence_root": str(execution_evidence_root), "book_snapshot_journal": str(output / "book_snapshots.jsonl"), "missing_inputs": missing_inputs, "action_revision_conflicts": sorted(action_conflicts), "packet_revision_conflicts": sorted(packet_conflicts), "capture_blockers": capture_blockers, "capture_limits": {"ttl_minutes": capture_ttl_minutes, "checkpoint_grace_seconds": capture_checkpoint_grace_seconds, "max_daily_demands": max_daily_capture_demands, "max_active_tokens": max_active_capture_tokens}, "fee_forecast": {"rate": str(taker_fee_rate), "exponent": str(taker_fee_exponent), "source": "official_weather_category_forecast_fallback_when_per_market_metadata_absent", "realized": False}, "counts": counts}
    atomic_json(output / "latest.json", latest); atomic_json(output / "state.json", {"updated_at_utc": iso(now), "processed_identities": sorted(identities["decisions"]), **SAFETY})
    return latest


def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("--once", action="store_true"); p.add_argument("--loop-seconds", type=int, default=15)
    p.add_argument("--actions", type=Path, default=DEFAULT_ACTIONS); p.add_argument("--packets", type=Path, default=DEFAULT_PACKETS); p.add_argument("--market-books", type=Path, default=DEFAULT_BOOKS); p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--subscription-epochs-root", type=Path, default=DEFAULT_SUBSCRIPTION_EPOCHS)
    p.add_argument("--execution-evidence-root", type=Path, default=DEFAULT_EXECUTION_EVIDENCE)
    p.add_argument("--maximum-book-age-seconds", type=int, default=5)
    p.add_argument("--taker-fee-rate", type=Decimal, default=Decimal("0.05")); p.add_argument("--taker-fee-exponent", type=Decimal, default=Decimal("1"))
    p.add_argument("--capture-ttl-minutes", type=int, default=10); p.add_argument("--capture-checkpoint-grace-seconds", type=int, default=20); p.add_argument("--max-daily-capture-demands", type=int, default=32); p.add_argument("--max-active-capture-tokens", type=int, default=16)
    args = p.parse_args()
    while True:
        print(json.dumps(run_once(args.actions, args.packets, args.market_books, args.output_dir, subscription_epochs_root=args.subscription_epochs_root, execution_evidence_root=args.execution_evidence_root, maximum_book_age_seconds=args.maximum_book_age_seconds, taker_fee_rate=args.taker_fee_rate, taker_fee_exponent=args.taker_fee_exponent, capture_ttl_minutes=args.capture_ttl_minutes, capture_checkpoint_grace_seconds=args.capture_checkpoint_grace_seconds, max_daily_capture_demands=args.max_daily_capture_demands, max_active_capture_tokens=args.max_active_capture_tokens), sort_keys=True))
        if args.once: return 0
        time.sleep(args.loop_seconds)

if __name__ == "__main__": raise SystemExit(main())
