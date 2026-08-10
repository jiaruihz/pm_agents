#!/usr/bin/env python3
"""20c take-profit exit monitor for low-price YES tiny-live.

This is an exit overlay, not an entry selector.  It only considers tokens bought
by low_price_yes_lottery_tiny_live_v1 and only sizes exits from the wallet's
actual Polymarket position.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.clients.polymarket_data import PolymarketDataClient
from scripts.ops.weather_market_proxy import market_proxy_url as shared_market_proxy_url
from src.strategies.weather_edge_v1.tools.execution_pipeline import cancel_log_path_for_live_out

RUNTIME_DIR = ROOT / "runtime/weather_edge_v1/low_price_yes_take_profit_exit_v1"
LIVE_DIR = ROOT / "runtime/weather_edge_v1/live"
ENTRY_LIVE_OUT = LIVE_DIR / "low_price_yes_lottery_tiny_live_v1_orders.jsonl"
LIVE_OUT = LIVE_DIR / "low_price_yes_take_profit_exit_v1_orders.jsonl"
PLAN_OUT = RUNTIME_DIR / "trade_plans.jsonl"
PAPER_OUT = RUNTIME_DIR / "paper_orders.jsonl"
DECISIONS_OUT = RUNTIME_DIR / "exit_decisions.jsonl"
LATEST_SUMMARY = RUNTIME_DIR / "latest_summary.json"
HISTORY_OUT = RUNTIME_DIR / "summary_history.jsonl"

STRATEGY_INSTANCE = "low_price_yes_take_profit_exit_v1"
STRATEGY_ID = "low_price_yes_take_profit_exit_v1"
STRATEGY_FAMILY = "forecast_quality.low_price_yes_lottery"
PROFILE = "tp20_full_sell_maker_first_fallback_v1"
COMBO = "low_price_yes_tp20_full_sell_v1"
CLOB_BASE_URL = os.getenv("CLOB_BASE_URL", "").strip() or os.getenv("PM_API_BASE_URL", "").strip() or "https://clob.polymarket.com"


def now_utc_dt() -> datetime:
    return datetime.now(timezone.utc)


def now_utc() -> str:
    return now_utc_dt().isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def json_ready(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    return value


def stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def parse_utc(value: Any) -> datetime | None:
    text = safe_str(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_date(value: Any) -> datetime.date | None:
    text = safe_str(value)
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(json_ready(row), ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(json_ready(row), ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def market_proxy_url() -> str:
    explicit = os.getenv("LOW_PRICE_YES_LOTTERY_MARKET_PROXY")
    return shared_market_proxy_url(explicit if explicit is not None else None)


def fetch_book(token_id: str, *, timeout_sec: float) -> dict[str, Any]:
    proxy = market_proxy_url()
    url = f"{CLOB_BASE_URL.rstrip('/')}/book"
    with httpx.Client(proxy=proxy or None, timeout=timeout_sec, trust_env=False) as client:
        response = client.get(url, params={"token_id": token_id}, headers={"Accept": "application/json"})
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def fetch_book_with_retry(
    token_id: str,
    *,
    timeout_sec: float,
    retries: int,
    retry_sleep_sec: float,
) -> dict[str, Any]:
    attempts = max(1, int(retries) + 1)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fetch_book(token_id, timeout_sec=timeout_sec)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_exc = exc
            if attempt < attempts and retry_sleep_sec > 0:
                time.sleep(float(retry_sleep_sec))
        except Exception:
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("book_fetch_failed_without_exception")


def book_levels(book: dict[str, Any], side: str) -> list[tuple[float, float]]:
    rows = book.get(f"{side}s") if isinstance(book, dict) else []
    out: list[tuple[float, float]] = []
    for item in rows or []:
        if isinstance(item, dict):
            price = item.get("price") or item.get("p")
            size = item.get("size") or item.get("q") or item.get("quantity")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            price, size = item[0], item[1]
        else:
            continue
        price_f = to_float(price, 0.0)
        size_f = to_float(size, 0.0)
        if price_f > 0 and size_f > 0:
            out.append((price_f, size_f))
    return sorted(out, key=lambda x: x[0], reverse=(side == "bid"))


def ceil_to_tick(value: float, tick: float) -> float:
    tick = tick if tick > 0 else 0.001
    return round(math.ceil((value - 1e-12) / tick) * tick, 6)


def floor_shares(value: float) -> float:
    return round(math.floor(max(0.0, value) * 100.0) / 100.0, 2)


def extract_order_id(row: dict[str, Any]) -> str:
    payload = row.get("exchange_response")
    if not isinstance(payload, dict):
        payload = {}
    place = payload.get("place")
    if not isinstance(place, dict):
        place = {}
    for obj in (place, payload):
        for key in ("orderID", "order_id", "id"):
            value = safe_str(obj.get(key))
            if value:
                return value
    return ""


def place_status(row: dict[str, Any]) -> str:
    payload = row.get("exchange_response")
    place = payload.get("place") if isinstance(payload, dict) else None
    if isinstance(place, dict):
        return safe_str(place.get("status")).lower()
    return ""


def load_entry_orders(path: Path = ENTRY_LIVE_OUT) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(path):
        if safe_str(row.get("record_type")) != "weather_edge_live_order":
            continue
        if safe_str(row.get("status")) != "submitted":
            continue
        if safe_str(row.get("order_side")).upper() != "BUY":
            continue
        if safe_str(row.get("signal_side")).upper() != "BUY_YES":
            continue
        token_id = safe_str(row.get("token_id"))
        if not token_id:
            continue
        current = out.get(token_id)
        if current is None or safe_str(row.get("created_at_utc")) > safe_str(current.get("created_at_utc")):
            out[token_id] = row
    return out


def load_positions(wallet: str, *, max_rows: int) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    client = PolymarketDataClient()
    proxy = market_proxy_url()
    if proxy:
        client.session.trust_env = False
        client.session.proxies.update({"http": proxy, "https": proxy})
    for page in client.iter_user_positions(user=wallet, page_size=200, max_rows=max_rows):
        rows.extend(page)
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        asset = safe_str(row.get("asset") or row.get("token_id") or row.get("tokenId"))
        if not asset:
            continue
        size = to_float(row.get("size"), 0.0)
        current = out.get(asset)
        if current is None or size > to_float(current.get("size"), 0.0):
            out[asset] = row
    return out


def load_cancel_index(cancel_path: Path) -> dict[str, set[str]]:
    order_ids: set[str] = set()
    execution_ids: set[str] = set()
    for row in read_jsonl(cancel_path):
        if safe_str(row.get("record_type")) != "weather_edge_live_order_cancel":
            continue
        if safe_str(row.get("status")) != "cancel_submitted":
            continue
        order_id = safe_str(row.get("order_id"))
        execution_id = safe_str(row.get("source_execution_id"))
        if order_id:
            order_ids.add(order_id)
        if execution_id:
            execution_ids.add(execution_id)
    return {"order_ids": order_ids, "execution_ids": execution_ids}


def exit_state_for_token(
    token_id: str,
    *,
    now_dt: datetime,
    exit_rows: list[dict[str, Any]],
    cancel_index: dict[str, set[str]],
) -> dict[str, Any]:
    rows = [
        row
        for row in exit_rows
        if safe_str(row.get("token_id")) == token_id
        and safe_str(row.get("record_type")) == "weather_edge_live_order"
        and safe_str(row.get("status")) == "submitted"
        and safe_str(row.get("order_side")).upper() == "SELL"
    ]
    rows.sort(key=lambda r: safe_str(r.get("created_at_utc")), reverse=True)
    if not rows:
        return {"state": "none"}

    latest = rows[0]
    execution_id = safe_str(latest.get("execution_id"))
    order_id = extract_order_id(latest)
    canceled = bool(order_id and order_id in cancel_index["order_ids"]) or bool(
        execution_id and execution_id in cancel_index["execution_ids"]
    )
    status = place_status(latest)
    expires_at = parse_utc(latest.get("expires_at_utc"))
    if canceled:
        return {
            "state": "canceled_ready_for_taker",
            "source_execution_id": execution_id,
            "order_id": order_id,
            "latest_exit_created_at_utc": safe_str(latest.get("created_at_utc")),
        }
    if status == "matched" or not bool(latest.get("maker_only", False)):
        return {
            "state": "already_exit_submitted",
            "source_execution_id": execution_id,
            "order_id": order_id,
            "latest_exit_created_at_utc": safe_str(latest.get("created_at_utc")),
            "place_status": status,
        }
    if status == "live":
        if expires_at is not None and expires_at <= now_dt:
            return {
                "state": "expired_pending_cancel",
                "source_execution_id": execution_id,
                "order_id": order_id,
                "expires_at_utc": expires_at.isoformat(),
                "latest_exit_created_at_utc": safe_str(latest.get("created_at_utc")),
            }
        return {
            "state": "active_maker",
            "source_execution_id": execution_id,
            "order_id": order_id,
            "expires_at_utc": expires_at.isoformat() if expires_at else "",
            "latest_exit_created_at_utc": safe_str(latest.get("created_at_utc")),
        }
    return {
        "state": "already_exit_submitted",
        "source_execution_id": execution_id,
        "order_id": order_id,
        "latest_exit_created_at_utc": safe_str(latest.get("created_at_utc")),
        "place_status": status,
    }


def maker_sell_price(*, best_bid: float, best_ask: float, threshold: float, tick: float) -> float:
    if best_bid <= 0:
        return 0.0
    price = ceil_to_tick(max(threshold, best_bid + tick), tick)
    if best_ask > 0 and price >= best_ask - 1e-12:
        return 0.0
    return round(price, 6)


def build_plan(
    decision: dict[str, Any],
    *,
    live_enabled: bool,
    role: str,
    expires_at_utc: str,
) -> dict[str, Any]:
    price = to_float(decision.get("limit_price"), 0.0)
    shares = to_float(decision.get("planned_shares"), 0.0)
    signal_id = stable_hash(
        {
            "strategy_instance": STRATEGY_INSTANCE,
            "token_id": safe_str(decision.get("token_id")),
            "role": role,
            "threshold": to_float(decision.get("take_profit_bid"), 0.0),
            "refresh_after": safe_str(decision.get("prior_exit_execution_id")) if safe_str(decision.get("prior_exit_state")) == "canceled_ready_for_taker" else "",
        }
    )
    maker_role = role != "taker_fallback"
    if role == "taker_fallback":
        execution_mode = "tp20_taker_fallback"
    elif role.startswith("resting"):
        execution_mode = "tp20_resting_maker"
    else:
        execution_mode = "tp20_maker_first"
    base = {
        "strategy": "weather_edge_v1",
        "strategy_instance": STRATEGY_INSTANCE,
        "source_strategy_instance": safe_str(decision.get("entry_strategy_instance")),
        "strategy_id": STRATEGY_ID,
        "strategy_family": STRATEGY_FAMILY,
        "probability_source": "exit_overlay_real_position_live_book",
        "decision_mode": "forecast_tail_take_profit_exit",
        "execution_mode": execution_mode,
        "profile": PROFILE,
        "combo": COMBO,
        "signal_id": signal_id,
        "city": safe_str(decision.get("city")),
        "city_pool": safe_str(decision.get("city_pool")),
        "target_date": safe_str(decision.get("target_date")),
        "market_id": safe_str(decision.get("market_id")),
        "market_slug": safe_str(decision.get("market_slug")),
        "event_slug": safe_str(decision.get("market_slug")),
        "event_id": safe_str(decision.get("event_id")),
        "question": safe_str(decision.get("question")),
        "bracket": safe_str(decision.get("bracket")),
        "token_id": safe_str(decision.get("token_id")),
        "signal_side": "SELL_YES",
        "order_side": "SELL",
        "market_price": round(price, 6),
        "best_bid": round(to_float(decision.get("fresh_best_bid"), 0.0), 6),
        "best_ask": round(to_float(decision.get("fresh_best_ask"), 0.0), 6),
        "spread": round(to_float(decision.get("fresh_spread"), 0.0), 6),
        "limit_price": round(price, 6),
        "quote_status": "accepted",
        "quote_reason": safe_str(decision.get("quote_reason")),
        "quote_edge": round(price - to_float(decision.get("position_avg_price"), 0.0), 6),
        "required_quote_edge": 0.0,
        "model_token_probability": 0.0,
        "quote_best_bid": round(to_float(decision.get("fresh_best_bid"), 0.0), 6),
        "quote_best_ask": round(to_float(decision.get("fresh_best_ask"), 0.0), 6),
        "quote_spread": round(to_float(decision.get("fresh_spread"), 0.0), 6),
        "quote_tick_size": round(to_float(decision.get("tick_size"), 0.001), 6),
        "quote_mode": "fresh_book_tp20_exit",
        "child_order_role": role,
        "maker_only": maker_role,
        "notional_fraction": 1.0,
        "size_multiplier": 1.0,
        "order_notional_cap": round(shares * price, 6),
        "size": round(shares, 6),
        "notional": round(shares * price, 6),
        "execution_policy": "low_price_yes_take_profit_exit_v1",
        "tick_size": round(to_float(decision.get("tick_size"), 0.001), 6),
        "entry_price_window": "0.05-0.20",
        "sizing_mode": "position_full_sell",
        "fixed_order_shares": 0.0,
        "max_order_shares": round(shares, 6),
        "edge": round(price - to_float(decision.get("position_avg_price"), 0.0), 6),
        "min_edge": 0.0,
        "market_implied_p_yes": round(price, 6),
        "shadow_decision": "low_price_yes_take_profit_exit_v1",
        "shadow_reason": "user_approved_tp20_maker_first_exit_overlay",
        "obs_source": "not_used_exit_overlay",
        "model_version": safe_str(decision.get("entry_model_version")),
        "paper_enabled": True,
        "live_enabled": bool(live_enabled),
        "expires_at_utc": expires_at_utc if maker_role else "",
        "source_snapshot_path": rel(ENTRY_LIVE_OUT),
        "entry_execution_id": safe_str(decision.get("entry_execution_id")),
        "entry_signal_id": safe_str(decision.get("entry_signal_id")),
        "entry_created_at_utc": safe_str(decision.get("entry_created_at_utc")),
        "position_avg_price": round(to_float(decision.get("position_avg_price"), 0.0), 6),
        "take_profit_bid": round(to_float(decision.get("take_profit_bid"), 0.0), 6),
    }
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": stable_hash(base),
        "created_at_utc": now_utc(),
        "status": "accepted",
        "risk_status": "passed",
        "risk_reason": "",
        **base,
    }


def evaluate_token(
    *,
    token_id: str,
    entry: dict[str, Any],
    position: dict[str, Any] | None,
    exit_state: dict[str, Any],
    args: argparse.Namespace,
    now_dt: datetime,
) -> dict[str, Any]:
    base = {
        "record_type": "low_price_yes_take_profit_exit_decision",
        "created_at_utc": now_utc(),
        "strategy_instance": STRATEGY_INSTANCE,
        "source_strategy_instance": "low_price_yes_lottery_tiny_live_v1",
        "token_id": token_id,
        "city": safe_str(entry.get("city")),
        "city_pool": safe_str(entry.get("city_pool")),
        "target_date": safe_str(entry.get("target_date")),
        "bracket": safe_str(entry.get("bracket")),
        "market_id": safe_str(entry.get("market_id")),
        "market_slug": safe_str(entry.get("market_slug")),
        "event_id": safe_str(entry.get("event_id")),
        "question": safe_str(entry.get("question")),
        "entry_strategy_instance": safe_str(entry.get("strategy_instance")),
        "entry_execution_id": safe_str(entry.get("execution_id")),
        "entry_signal_id": safe_str(entry.get("signal_id")),
        "entry_created_at_utc": safe_str(entry.get("created_at_utc")),
        "entry_order_side": safe_str(entry.get("order_side")),
        "entry_place_status": place_status(entry),
        "entry_posted_price": to_float(entry.get("posted_price"), to_float(entry.get("limit_price"), 0.0)),
        "entry_size": to_float(entry.get("size"), 0.0),
        "entry_model_version": safe_str(entry.get("model_version")),
        "take_profit_bid": float(args.take_profit_bid),
        "prior_exit_state": safe_str(exit_state.get("state")),
        "prior_exit_order_id": safe_str(exit_state.get("order_id")),
        "prior_exit_execution_id": safe_str(exit_state.get("source_execution_id")),
    }
    target_date = parse_date(entry.get("target_date"))
    if target_date and target_date < now_dt.date() and not args.allow_past_target_date:
        return {**base, "decision_status": "skipped", "blocker": "past_target_date"}
    if position is None:
        return {**base, "decision_status": "skipped", "blocker": "no_wallet_position"}

    position_size = to_float(position.get("size"), 0.0)
    avg_price = to_float(position.get("avgPrice") or position.get("avg_price"), 0.0)
    cur_price = to_float(position.get("curPrice") or position.get("cur_price"), 0.0)
    current_value = to_float(position.get("currentValue") or position.get("current_value"), position_size * cur_price)
    cash_pnl = to_float(position.get("cashPnl") or position.get("cash_pnl"), 0.0)
    position_fields = {
        "position_size": round(position_size, 6),
        "position_avg_price": round(avg_price, 6),
        "position_cur_price": round(cur_price, 6),
        "position_current_value": round(current_value, 6),
        "position_cash_pnl": round(cash_pnl, 6),
        "position_outcome": safe_str(position.get("outcome")),
        "position_title": safe_str(position.get("title")),
    }
    if position_size < float(args.min_position_shares):
        return {**base, **position_fields, "decision_status": "skipped", "blocker": "position_below_min_shares"}

    try:
        book = fetch_book_with_retry(
            token_id,
            timeout_sec=float(args.book_timeout_sec),
            retries=int(args.book_retries),
            retry_sleep_sec=float(args.book_retry_sleep_sec),
        )
    except Exception as exc:  # noqa: BLE001
        return {
            **base,
            **position_fields,
            "decision_status": "blocked",
            "blocker": "book_fetch_failed",
            "book_error": f"{type(exc).__name__}: {exc}",
        }
    bids = book_levels(book, "bid")
    asks = book_levels(book, "ask")
    best_bid = bids[0][0] if bids else 0.0
    best_bid_size = bids[0][1] if bids else 0.0
    best_ask = asks[0][0] if asks else 0.0
    best_ask_size = asks[0][1] if asks else 0.0
    spread = max(0.0, best_ask - best_bid) if best_bid > 0 and best_ask > 0 else 0.0
    book_fields = {
        "fresh_best_bid": round(best_bid, 6),
        "fresh_best_bid_size": round(best_bid_size, 6),
        "fresh_best_ask": round(best_ask, 6),
        "fresh_best_ask_size": round(best_ask_size, 6),
        "fresh_spread": round(spread, 6),
        "tick_size": float(args.tick_size),
        "gross_exit_value_at_bid": round(position_size * best_bid, 6),
        "gross_exit_pnl_at_bid": round(position_size * (best_bid - avg_price), 6),
        "gross_exit_roi_at_bid": round((best_bid - avg_price) / avg_price, 6) if avg_price > 0 else 0.0,
    }
    state = safe_str(exit_state.get("state"))
    if state in {"active_maker", "expired_pending_cancel", "already_exit_submitted"}:
        return {**base, **position_fields, **book_fields, "decision_status": "blocked", "blocker": state}

    shares = floor_shares(position_size)
    if args.max_exit_shares > 0:
        shares = min(shares, floor_shares(float(args.max_exit_shares)))
    if shares < float(args.min_position_shares):
        return {**base, **position_fields, **book_fields, "decision_status": "skipped", "blocker": "planned_shares_below_min"}

    threshold = float(args.take_profit_bid)
    triggered = best_bid >= threshold
    if not triggered:
        if best_bid <= 0:
            return {
                **base,
                **position_fields,
                **book_fields,
                "decision_status": "blocked",
                "blocker": "no_bid_for_resting_maker_sell",
            }
        role = "resting_maker_refresh" if state == "canceled_ready_for_taker" else "resting_maker"
        limit_price = round(threshold, 6)
        quote_reason = "low_price_yes_tp20_resting_maker"
    elif state == "canceled_ready_for_taker":
        if not args.enable_taker_fallback:
            return {**base, **position_fields, **book_fields, "decision_status": "blocked", "blocker": "taker_fallback_disabled"}
        role = "taker_fallback"
        limit_price = round(best_bid, 6)
        quote_reason = "low_price_yes_tp20_taker_after_maker_cancel"
    else:
        role = "maker_first"
        limit_price = maker_sell_price(
            best_bid=best_bid,
            best_ask=best_ask,
            threshold=threshold,
            tick=float(args.tick_size),
        )
        quote_reason = "low_price_yes_tp20_maker_first"
        if limit_price <= 0:
            if args.allow_taker_when_no_maker_price and args.enable_taker_fallback:
                role = "taker_fallback"
                limit_price = round(best_bid, 6)
                quote_reason = "low_price_yes_tp20_taker_no_resting_maker_price"
            else:
                return {
                    **base,
                    **position_fields,
                    **book_fields,
                    "decision_status": "blocked",
                    "blocker": "no_resting_maker_sell_price",
                }

    return {
        **base,
        **position_fields,
        **book_fields,
        "decision_status": "planned",
        "blocker": "",
        "planned_role": role,
        "planned_shares": shares,
        "limit_price": round(limit_price, 6),
        "planned_gross_proceeds": round(shares * limit_price, 6),
        "planned_gross_pnl_vs_avg": round(shares * (limit_price - avg_price), 6),
        "quote_reason": quote_reason,
    }


def run_executor(args: argparse.Namespace, *, plans: list[dict[str, Any]], has_taker: bool) -> dict[str, Any] | None:
    if not args.live:
        return None
    if not args.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    cmd = [
        sys.executable,
        "scripts/ops/weather_order_executor.py",
        "--plans",
        str(PLAN_OUT),
        "--paper-out",
        str(PAPER_OUT),
        "--live-out",
        str(LIVE_OUT),
        "--live",
        "--confirm-live",
        "--cancel-expired",
        "--no-telegram",
    ]
    if has_taker:
        cmd.append("--allow-taker")
    env = os.environ.copy()
    proxy = market_proxy_url()
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        env["ALL_PROXY"] = proxy
    else:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(key, None)
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=args.executor_timeout_sec,
    )
    payload: dict[str, Any] = {
        "executor_cmd": cmd,
        "executor_returncode": proc.returncode,
        "executor_output": proc.stdout[-8000:],
    }
    try:
        start = proc.stdout.find("{")
        end = proc.stdout.rfind("}")
        payload["executor_result"] = json.loads(proc.stdout[start : end + 1]) if start >= 0 and end > start else None
    except Exception:
        payload["executor_result"] = None
    if proc.returncode != 0:
        payload["executor_error"] = "nonzero_returncode"
    return payload


def send_telegram_summary(summary: dict[str, Any], *, args: argparse.Namespace) -> None:
    if args.no_telegram:
        return
    planned = int(summary.get("planned_count") or 0)
    executor_result = summary.get("executor_result") if isinstance(summary.get("executor_result"), dict) else {}
    live_written = int(executor_result.get("live_written") or 0)
    live_errors = int(executor_result.get("live_errors") or 0)
    cancel_attempted = int(executor_result.get("cancel_attempted") or 0)
    if planned <= 0 and live_errors <= 0 and cancel_attempted <= 0:
        return
    try:
        from src.platform.notification.telegram import send_telegram_message_sync
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] telegram helper unavailable: {type(exc).__name__}: {exc}")
        return
    lines = [
        "[Low-price YES TP20 exit]",
        f"planned={planned} live_written={live_written} live_errors={live_errors} cancel_attempted={cancel_attempted}",
        f"threshold_bid={args.take_profit_bid:.3f} ttl={int(args.maker_ttl_seconds)}s taker_fallback={bool(args.enable_taker_fallback)}",
    ]
    for row in summary.get("planned_preview", [])[:8]:
        lines.append(
            "- "
            f"{safe_str(row.get('city'))} {safe_str(row.get('target_date'))} {safe_str(row.get('bracket'))} "
            f"role={safe_str(row.get('planned_role'))} size={to_float(row.get('planned_shares'), 0.0):.2f} "
            f"bid={to_float(row.get('fresh_best_bid'), 0.0):.3f} px={to_float(row.get('limit_price'), 0.0):.3f}"
        )
    try:
        send_telegram_message_sync("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] telegram send failed: {type(exc).__name__}: {exc}")


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    generated_at = now_utc()
    now_dt = now_utc_dt()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LIVE_DIR.mkdir(parents=True, exist_ok=True)

    wallet = os.getenv("PM_ADDRESS", "").strip()
    if not wallet:
        raise RuntimeError("missing PM_ADDRESS")

    entry_orders = load_entry_orders(ENTRY_LIVE_OUT)
    positions = load_positions(wallet, max_rows=int(args.max_position_rows))
    exit_rows = read_jsonl(LIVE_OUT)
    cancel_path = Path(args.cancel_log) if args.cancel_log else cancel_log_path_for_live_out(LIVE_OUT)
    cancel_index = load_cancel_index(cancel_path)

    decisions: list[dict[str, Any]] = []
    for token_id, entry in sorted(entry_orders.items(), key=lambda kv: safe_str(kv[1].get("created_at_utc"))):
        if len(decisions) >= int(args.max_tokens_per_run):
            break
        exit_state = exit_state_for_token(
            token_id,
            now_dt=now_dt,
            exit_rows=exit_rows,
            cancel_index=cancel_index,
        )
        decision = evaluate_token(
            token_id=token_id,
            entry=entry,
            position=positions.get(token_id),
            exit_state=exit_state,
            args=args,
            now_dt=now_dt,
        )
        decisions.append(decision)

    planned_decisions = [row for row in decisions if safe_str(row.get("decision_status")) == "planned"]
    planned_decisions = planned_decisions[: int(args.max_exits_per_run)]
    maker_expires_at = (now_dt + timedelta(seconds=float(args.maker_ttl_seconds))).isoformat(timespec="seconds")
    live_enabled = bool(args.live and args.confirm_live)
    plans = [
        build_plan(
            decision,
            live_enabled=live_enabled,
            role=safe_str(decision.get("planned_role")),
            expires_at_utc=maker_expires_at,
        )
        for decision in planned_decisions
    ]
    has_taker = any(safe_str(plan.get("child_order_role")) == "taker_fallback" for plan in plans)

    write_jsonl(PLAN_OUT, plans)
    for row in decisions:
        append_jsonl(DECISIONS_OUT, row)

    executor_payload = run_executor(args, plans=plans, has_taker=has_taker)

    status_counts: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    for row in decisions:
        status = safe_str(row.get("decision_status")) or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        blocker = safe_str(row.get("blocker"))
        if blocker:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
        role = safe_str(row.get("planned_role"))
        if role:
            role_counts[role] = role_counts.get(role, 0) + 1

    summary: dict[str, Any] = {
        "generated_at_utc": generated_at,
        "status": "planned",
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "strategy_family": STRATEGY_FAMILY,
        "profile": PROFILE,
        "entry_live_orders_file": rel(ENTRY_LIVE_OUT),
        "entry_submitted_buy_tokens": len(entry_orders),
        "wallet": wallet,
        "position_rows_matched_to_entry_tokens": sum(1 for token in entry_orders if token in positions),
        "decision_count": len(decisions),
        "planned_count": len(planned_decisions),
        "plans": len(plans),
        "live_requested": bool(args.live),
        "live_enabled": live_enabled,
        "take_profit_bid": float(args.take_profit_bid),
        "maker_ttl_seconds": float(args.maker_ttl_seconds),
        "enable_taker_fallback": bool(args.enable_taker_fallback),
        "allow_taker_when_no_maker_price": bool(args.allow_taker_when_no_maker_price),
        "status_counts": status_counts,
        "blocker_counts": blocker_counts,
        "role_counts": role_counts,
        "planned_preview": [
            {
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "bracket": row.get("bracket"),
                "planned_role": row.get("planned_role"),
                "planned_shares": row.get("planned_shares"),
                "position_avg_price": row.get("position_avg_price"),
                "fresh_best_bid": row.get("fresh_best_bid"),
                "fresh_best_ask": row.get("fresh_best_ask"),
                "limit_price": row.get("limit_price"),
                "planned_gross_proceeds": row.get("planned_gross_proceeds"),
                "planned_gross_pnl_vs_avg": row.get("planned_gross_pnl_vs_avg"),
            }
            for row in planned_decisions[:20]
        ],
        "files": {
            "summary": rel(LATEST_SUMMARY),
            "summary_history": rel(HISTORY_OUT),
            "decisions": rel(DECISIONS_OUT),
            "trade_plans": rel(PLAN_OUT),
            "paper_orders": rel(PAPER_OUT),
            "live_orders": rel(LIVE_OUT),
            "cancel_log": rel(cancel_path),
        },
    }
    if executor_payload:
        summary.update(executor_payload)
        if isinstance(executor_payload.get("executor_result"), dict):
            summary["executor_result"] = executor_payload["executor_result"]

    write_json(LATEST_SUMMARY, summary)
    append_jsonl(HISTORY_OUT, summary)
    send_telegram_summary(summary, args=args)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "loop"], nargs="?", default="run")
    parser.add_argument("--take-profit-bid", type=float, default=0.20)
    parser.add_argument("--tick-size", type=float, default=0.001)
    parser.add_argument("--maker-ttl-seconds", type=float, default=900.0)
    parser.add_argument("--enable-taker-fallback", action="store_true")
    parser.add_argument("--allow-taker-when-no-maker-price", action="store_true")
    parser.add_argument("--min-position-shares", type=float, default=5.0)
    parser.add_argument("--max-exit-shares", type=float, default=0.0)
    parser.add_argument("--max-exits-per-run", type=int, default=4)
    parser.add_argument("--max-tokens-per-run", type=int, default=200)
    parser.add_argument("--max-position-rows", type=int, default=5000)
    parser.add_argument("--book-timeout-sec", type=float, default=10.0)
    parser.add_argument("--book-retries", type=int, default=1)
    parser.add_argument("--book-retry-sleep-sec", type=float, default=0.4)
    parser.add_argument("--executor-timeout-sec", type=float, default=180.0)
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    parser.add_argument("--cancel-log", default="")
    parser.add_argument("--allow-past-target-date", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ModuleNotFoundError:
        pass
    os.chdir(ROOT)
    args = parse_args()
    if args.live and not args.confirm_live:
        raise RuntimeError("--live requires --confirm-live")
    if args.take_profit_bid < 0.05:
        raise RuntimeError("--take-profit-bid below 0.05 is not allowed for this exit overlay")
    if args.command == "run":
        summary = run_once(args)
        print(json.dumps(json_ready(summary), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    while True:
        print(f"[low_price_yes_take_profit] cycle_start_utc={now_utc()}", flush=True)
        try:
            summary = run_once(args)
            print(
                "[low_price_yes_take_profit] "
                f"planned={summary.get('planned_count')} status_counts={summary.get('status_counts')} "
                f"live={summary.get('live_enabled')}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[low_price_yes_take_profit] runner_failed {type(exc).__name__}: {exc}", flush=True)
        time.sleep(max(5.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
