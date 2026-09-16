"""Reusable one-shot event-triggered stop-loss orchestration primitives.

This module deliberately knows nothing about Amsterdam, METAR, or a specific
market.  Source adapters supply an ordered event history; the engine selects
the first event after a durable baseline, evaluates one condition, and expands
configured close/open actions into canonical weather trade plans.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from weather_clock_contract import parse_utc


def _parse_utc(value: Any) -> datetime:
    parsed = parse_utc(value, field="stop_loss_event_clock")
    assert parsed is not None
    return parsed


def _stable_id(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class TriggerCondition:
    field: str
    operator: str
    value: Any


@dataclass(frozen=True)
class TriggerDecision:
    status: str
    event: dict[str, Any] | None = None
    reason: str = ""

    @property
    def triggered(self) -> bool:
        return self.status == "triggered"


@dataclass(frozen=True)
class TradeAction:
    action_id: str
    token_id: str
    signal_side: str
    order_side: str
    size_mode: str
    shares: float = 0.0
    limit_price: float = 0.0


def _compare(actual: Any, condition: TriggerCondition) -> bool:
    op = condition.operator.strip().lower()
    expected = condition.value
    if op in {"eq", "=="}:
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            return math.isclose(float(actual), float(expected), abs_tol=1e-9)
        return actual == expected
    if op in {"ne", "!="}:
        return not _compare(actual, TriggerCondition(condition.field, "eq", expected))
    if op in {"gt", ">"}:
        return float(actual) > float(expected)
    if op in {"gte", ">="}:
        return float(actual) >= float(expected)
    if op in {"lt", "<"}:
        return float(actual) < float(expected)
    if op in {"lte", "<="}:
        return float(actual) <= float(expected)
    if op == "in":
        return actual in expected
    raise ValueError(f"unsupported trigger operator: {condition.operator}")


def evaluate_first_new_event(
    events: Iterable[Mapping[str, Any]],
    *,
    baseline_sequence: str,
    sequence_field: str,
    condition: TriggerCondition,
) -> TriggerDecision:
    """Evaluate only the earliest event after baseline, never the latest row.

    Requiring a history-bearing source adapter prevents a delayed cache from
    skipping one report and accidentally firing on a later report.
    """

    baseline = _parse_utc(baseline_sequence)
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    for raw in events:
        event = dict(raw)
        sequence = event.get(sequence_field)
        if not sequence:
            continue
        parsed = _parse_utc(sequence)
        if parsed > baseline:
            candidates.append((parsed, event))
    if not candidates:
        return TriggerDecision(status="waiting", reason="no_event_after_baseline")
    _, first = min(candidates, key=lambda item: item[0])
    if condition.field not in first:
        return TriggerDecision(status="invalid_event", event=first, reason=f"missing_condition_field:{condition.field}")
    if _compare(first[condition.field], condition):
        return TriggerDecision(status="triggered", event=first, reason="condition_matched")
    return TriggerDecision(status="no_trigger", event=first, reason="first_new_event_condition_not_matched")


def resolve_action_shares(action: TradeAction, *, wallet_positions: Mapping[str, float]) -> float:
    mode = action.size_mode.strip().lower()
    if mode == "fixed_shares":
        shares = float(action.shares)
    elif mode == "wallet_position_all":
        if action.token_id not in wallet_positions:
            raise RuntimeError(f"wallet position unavailable for close-all token {action.token_id}")
        shares = float(wallet_positions[action.token_id])
    else:
        raise ValueError(f"unsupported action size_mode: {action.size_mode}")
    if not math.isfinite(shares) or shares <= 0:
        raise RuntimeError(f"resolved action shares must be positive: {shares}")
    return shares


def build_action_plan(
    *,
    rule_id: str,
    action: TradeAction,
    event_sequence: str,
    wallet_positions: Mapping[str, float],
    market: Mapping[str, Any],
    source_strategy_instance: str = "",
) -> dict[str, Any]:
    shares = resolve_action_shares(action, wallet_positions=wallet_positions)
    identity = {
        "rule_id": rule_id,
        "action_id": action.action_id,
        "event_sequence": event_sequence,
        "token_id": action.token_id,
        "signal_side": action.signal_side,
        "order_side": action.order_side,
    }
    limit_price = float(action.limit_price)
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": "plan-" + _stable_id(identity),
        "signal_id": "signal-" + _stable_id(identity),
        "created_at_utc": _utc_now(),
        "status": "accepted",
        "risk_status": "passed",
        "strategy": "weather_edge_v1",
        "strategy_id": rule_id,
        "strategy_instance": rule_id,
        "source_strategy_instance": source_strategy_instance,
        "strategy_family": "conditional_stop_loss",
        "profile": rule_id,
        "combo": action.action_id,
        **dict(market),
        "token_id": action.token_id,
        "signal_side": action.signal_side,
        "order_side": action.order_side,
        "market_price": limit_price,
        "limit_price": limit_price,
        "quote_status": "accepted",
        "quote_reason": "conditional_stop_loss_triggered",
        "quote_edge": 0.0,
        "required_quote_edge": 0.0,
        "model_token_probability": 0.0,
        "quote_tick_size": 0.001,
        "quote_mode": "conditional_stop_loss_taker_limit",
        "child_order_role": action.action_id,
        "maker_only": False,
        "execution_policy": "operator_taker_limit_v1",
        "sizing_mode": action.size_mode,
        "fixed_order_shares": shares if action.size_mode == "fixed_shares" else 0.0,
        "max_order_shares": shares,
        "size": shares,
        "notional": round(shares * limit_price, 6),
        "order_notional_cap": round(shares * limit_price, 6),
        "paper_enabled": True,
        "live_enabled": True,
        "source_event_sequence": event_sequence,
    }
