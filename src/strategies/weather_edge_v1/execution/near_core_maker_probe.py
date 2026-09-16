"""Pure contracts for the additive Core Carry near-Core maker probe.

This module deliberately contains no venue or filesystem side effects.  The
production runner owns persistence and execution; these helpers keep candidate
selection, sleeve identity, exposure accounting, and reporting deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from decimal import Decimal, ROUND_FLOOR
from typing import Any, Iterable, Mapping


SOURCE_SLEEVE = "CORE_CARRY_NEAR_CORE_MAKER_PROBE_5S"
STRATEGY_INSTANCE = "current_yes_core_carry_near_core_maker_5s_v2"
CONFIG_ID = "current_yes_core_carry_near_core_continuous_5s_v2"
EXECUTION_PROFILE = "near_core_continuous_fixed_rest_maker_v2"
EXPERIMENT_ID = "core_carry_near_core_continuous_v2"
CLIENT_ORDER_PREFIX = "pmc_ccnc_"
FIXED_SHARES = 5.0
SOLE_BLOCKER = "non_positive_taker_ev"
LEDGER_SCHEMA_VERSION = "core_carry_near_core_maker_ledger_v2"
MANIFEST_SCHEMA_VERSION = "core_carry_near_core_maker_manifest_v2"
WS_CONTROL_READINESS_SCHEMA_VERSION = "core_carry_near_core_ws_control_readiness_v1"
BUDGET_POLICY = "filled_city_days_plus_pending_reservations_v1"


def daily_budget(
    orders: Iterable[Mapping[str, Any]],
    states: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Account append-only order evidence without treating cancels as fills.

    The budget day is the Beijing day on which an authoritative cumulative fill
    increase was first received (not the canonical economic fill timestamp).
    Pending orders reserve capacity across midnight. A terminal status releases
    the remainder only when its explicit cumulative matched quantity is valid.
    Each physical order must have one stable ``budget_order_id``; duplicates do
    not add cost. A submitted order without final evidence remains reserved.
    """
    bj = timezone(timedelta(hours=8))
    day = now.astimezone(bj).date()

    def clock(value: Any) -> datetime:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("near budget evidence requires timezone-aware clock")
        return result

    by_id: dict[str, Mapping[str, Any]] = {}
    for order in orders:
        if clock(order["created_at_utc"]) > now:
            continue
        key = str(order["budget_order_id"])
        if key in by_id:
            for field in ("city", "target_date", "size", "posted_price"):
                if order[field] != by_id[key][field]:
                    raise ValueError(f"conflicting near budget order {key}: {field}")
        else:
            by_id[key] = order
    events: dict[str, list[Mapping[str, Any]]] = {}
    for state in states:
        if clock(state["received_at_utc"]) <= now:
            events.setdefault(str(state["budget_order_id"]), []).append(state)
    filled_days: set[tuple[str, str]] = set()
    reserved_days: set[tuple[str, str]] = set()
    details = []
    for identity, order in by_id.items():
        quantity, price = float(order["size"]), float(order["posted_price"])
        if not (math.isfinite(quantity) and 0 < quantity <= FIXED_SHARES
                and math.isfinite(price) and 0 < price <= 1):
            raise ValueError(f"invalid near budget size/price for {identity}")
        key = city_day(order)
        if not all(key):
            raise ValueError(f"missing near budget city-day for {identity}")
        matched = today_qty = 0.0
        terminal = False
        for state in sorted(events.get(identity, []), key=lambda x: clock(x["received_at_utc"])):
            try:
                cumulative = float(state["matched_shares"])
            except (KeyError, TypeError, ValueError):
                continue  # Missing quantity is not an authoritative zero.
            if not math.isfinite(cumulative) or not 0 <= cumulative <= quantity:
                raise ValueError(f"invalid near budget matched quantity for {identity}")
            increase = max(0.0, cumulative - matched)
            if clock(state["received_at_utc"]).astimezone(bj).date() == day:
                today_qty += increase
            matched = max(matched, cumulative)
            # Cumulative fill evidence and terminality are monotone: delayed
            # LIVE/zero events must not erase a fill or resurrect a canceled order.
            terminal |= str(state.get("status", "")).lower() in {
                "canceled", "cancelled", "expired", "rejected",
            } and cumulative >= matched
            terminal |= matched >= quantity
        remaining = 0.0 if terminal else quantity - matched
        if today_qty > 0:
            filled_days.add(key)
        if remaining > 0:
            reserved_days.add(key)
        details.append({
            "budget_order_id": identity, "city": key[0], "target_date": key[1],
            "matched_shares": matched, "recognized_today_shares": today_qty,
            "reserved_shares": remaining, "terminal": terminal,
            "recognized_cost_usd": round(today_qty * price, 8),
            "reserved_cost_usd": round(remaining * price, 8),
        })
    return {
        "policy": BUDGET_POLICY, "budget_date_bj": day.isoformat(),
        "used_city_days": len(filled_days | reserved_days),
        "filled_city_days": len(filled_days), "reserved_city_days": len(reserved_days),
        "used_cost_usd": round(sum(x["recognized_cost_usd"] + x["reserved_cost_usd"] for x in details), 8),
        "orders": details,
    }


def city_day(row: Mapping[str, Any]) -> tuple[str, str]:
    return str(row.get("city") or ""), str(row.get("target_date") or "")


def is_candidate(row: Mapping[str, Any]) -> bool:
    """Select a market re-score whose sole blocker is taker EV.

    Structural/model-support gates stay identical to Core Carry.  The near
    sleeve acts only between a non-positive ten-share taker EV and a positive
    retained-edge post-only maker quote.
    """

    return (
        not bool(row.get("eligible"))
        and list(row.get("reasons") or ()) == [SOLE_BLOCKER]
        and str(row.get("rescore_mode") or "") == "continuous_market_reprice"
        and bool(str(row.get("checkpoint_key") or ""))
        and bool(str(row.get("current_yes_token_id") or row.get("token_id") or ""))
        and all(city_day(row))
    )


def matched_shares(row: Mapping[str, Any]) -> float:
    """Read authoritative matched quantity first, then legacy fallbacks."""

    authoritative: list[Any] = [row.get("authoritative_matched_shares")]
    response = row.get("exchange_response")
    if isinstance(response, Mapping):
        state = response.get("authoritative_order_state")
        if isinstance(state, Mapping):
            authoritative.extend(
                (
                    state.get("size_matched"),
                    state.get("sizeMatched"),
                    state.get("matched_shares"),
                )
            )
    fallback = [
        row.get("source_filled_shares"),
        row.get("matched_shares"),
        row.get("size_matched"),
    ]
    for value in (*authoritative, *fallback):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return min(FIXED_SHARES, parsed)
    return 0.0


def consumed_city_days(
    rows: Iterable[Mapping[str, Any]],
) -> set[tuple[str, str]]:
    """One near-Core submit or non-zero fill consumes the city-day sleeve."""

    consumed: set[tuple[str, str]] = set()
    for row in rows:
        if str(row.get("strategy_instance") or "") != STRATEGY_INSTANCE:
            continue
        key = city_day(row)
        if not all(key):
            continue
        if str(row.get("status") or "") == "submitted" or matched_shares(row) > 0:
            consumed.add(key)
    return consumed


def ws_control_readiness_failures(
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
    max_manifest_age_sec: float = 2.0,
) -> list[str]:
    """Validate the E1 smoke seal required before any WS-1 live order."""

    failures: list[str] = []
    if str(payload.get("schema_version") or "") != WS_CONTROL_READINESS_SCHEMA_VERSION:
        failures.append("ws_control_schema_mismatch")
    if str(payload.get("status") or "") != "ready":
        failures.append("ws_control_not_ready")
    generated_at = str(payload.get("generated_at_utc") or "")
    try:
        generated_clock = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age_sec = (current - generated_clock.astimezone(timezone.utc)).total_seconds()
        if age_sec < 0 or age_sec > max_manifest_age_sec:
            failures.append("ws_control_manifest_stale")
    except (TypeError, ValueError):
        failures.append("ws_control_manifest_clock_invalid")
    if str(payload.get("raw_ingestion") or "") != "continuous_event_driven":
        failures.append("raw_ingestion_not_continuous_event_driven")
    try:
        heartbeat_ms = float(payload.get("policy_heartbeat_ms"))
    except (TypeError, ValueError):
        heartbeat_ms = float("inf")
    if heartbeat_ms > 500:
        failures.append("policy_heartbeat_slower_than_500ms")
    try:
        snapshot_ms = float(payload.get("readiness_publish_interval_ms"))
    except (TypeError, ValueError):
        snapshot_ms = float("inf")
    if snapshot_ms > 1000:
        failures.append("readiness_publish_slower_than_1s")
    if str(payload.get("economic_signal_owner") or "") != (
        "current_yes_core_carry_tiny_live_v2"
    ):
        failures.append("economic_signal_owner_mismatch")
    if str(payload.get("economic_signal_rescore_mode") or "") != (
        "continuous_market_reprice_15s"
    ):
        failures.append("economic_signal_rescore_mode_mismatch")
    if not bool(payload.get("private_order_updates")):
        failures.append("private_order_updates_unavailable")
    if not bool(payload.get("private_channel_connected")):
        failures.append("private_channel_disconnected")
    if not bool(payload.get("authenticated_rest_probe")):
        failures.append("authenticated_rest_probe_failed")
    if not bool(payload.get("private_ws_auth_assured")):
        failures.append("private_ws_auth_not_assured")
    if not bool(payload.get("market_ws_connected")):
        failures.append("market_ws_disconnected")
    if str(payload.get("staleness_action") or "") != "safety_cancel":
        failures.append("staleness_action_not_safety_cancel")
    try:
        pre_sec = float(payload.get("window_pre_sec"))
        post_sec = float(payload.get("window_post_sec"))
    except (TypeError, ValueError):
        pre_sec, post_sec = 0.0, 0.0
    if pre_sec < 60 or post_sec < 900:
        failures.append("replay_window_below_60s_pre_900s_post")
    return failures


def filled_exposure_by_city_day(
    rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], float]:
    """Return maximum authoritative fill seen for each near-Core opportunity."""

    exposure: dict[tuple[str, str], float] = {}
    for row in rows:
        if str(row.get("strategy_instance") or "") != STRATEGY_INSTANCE:
            continue
        key = city_day(row)
        if not all(key):
            continue
        exposure[key] = max(exposure.get(key, 0.0), matched_shares(row))
    return exposure


def promotion_gate(ledger_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """WS-1 is evidence collection; it cannot self-promote an economic cancel."""

    rows = [dict(row) for row in ledger_rows]
    submitted = {
        str(row.get("execution_id") or row.get("order_id") or row.get("ledger_id"))
        for row in rows
        if str(row.get("status") or "") == "submitted"
    }
    terminal = {
        str(row.get("execution_id") or row.get("order_id") or row.get("ledger_id"))
        for row in rows
        if str(row.get("status") or "")
        in {"filled", "matched", "cancelled", "canceled", "expired", "settled"}
    }
    return {
        "schema_version": "core_carry_near_core_maker_promotion_gate_v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gate_pass": False,
        "stage": "CONTINUOUS_LIVE_EVIDENCE_COLLECTION",
        "submitted_orders": len(submitted),
        "terminal_orders": len(terminal),
        "fail_reasons": [
            "held_out_near_core_actual_order_evidence_not_complete",
            "minimum_settled_target_dates_not_reached",
        ],
    }


def report(ledger_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(row) for row in ledger_rows]
    by_status: dict[str, int] = {}
    eligible_checkpoints: set[str] = set()
    submitted_orders: set[str] = set()
    matched_by_order: dict[str, float] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        if bool(row.get("eligible_checkpoint")):
            eligible_checkpoints.add(
                str(row.get("checkpoint_key") or row.get("ledger_id") or "")
            )
        order_key = str(
            row.get("execution_id")
            or row.get("order_id")
            or row.get("client_order_id")
            or ""
        )
        if order_key and status == "submitted":
            submitted_orders.add(order_key)
        if order_key:
            matched_by_order[order_key] = max(
                matched_by_order.get(order_key, 0.0), matched_shares(row)
            )
    return {
        "schema_version": "core_carry_near_core_maker_report_v2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_sleeve": SOURCE_SLEEVE,
        "experiment_id": EXPERIMENT_ID,
        "denominator": "all_eligible_near_core_checkpoints_including_unfilled_zero_pnl",
        "eligible_checkpoints": len(eligible_checkpoints),
        "submitted_orders": len(submitted_orders),
        "orders_with_fill": sum(value > 0 for value in matched_by_order.values()),
        "matched_shares": round(sum(matched_by_order.values()), 6),
        "ledger_rows": len(rows),
        "status_counts": dict(sorted(by_status.items())),
        "pnl_pooling_with_existing_core": False,
    }


MID_QUOTE_POLICY = "near_core_midpoint_initial_fixed_rest_v1"

def midpoint_entry_price(*, best_bid: float, best_ask: float, tick_size: float, price_cap: float) -> float:
    """Initial post-only mid quote; round down and preserve the frozen edge cap."""
    bid, ask, tick, cap = map(lambda v: Decimal(str(v)), (best_bid, best_ask, tick_size, price_cap))
    if not all(v.is_finite() for v in (bid, ask, tick, cap)) or not (0 < bid < ask < 1 and tick > 0 and cap > 0):
        return 0.0
    target = min((bid + ask) / 2, cap, ask - tick)
    price = (target / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    return float(price) if 0 < price < ask else 0.0
