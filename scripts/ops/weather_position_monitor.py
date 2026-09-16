#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, AssetType, BalanceAllowanceParams, OpenOrderParams
from py_clob_client.constants import POLYGON

from src.platform.notification.telegram import PMMTelegramNotifier
from src.platform.strategy_runtime.store import StrategyRuntimeStore
from src.strategies.pmm.config import PMMConfig
from src.platform.clients.polymarket_data import PolymarketDataClient
from src.strategies.registry import load_strategy_rows
from src.strategies.weather_edge_v1.tools.airport_weather_tool import (
    AirportWeatherTool,
    load_watch_baseline,
    upsert_watch_entry,
)
from src.strategies.weather_edge_v1.tools.codex_weather_advisor import run_codex_analysis
from src.strategies.weather_edge_v1.tools.decision_journal import WeatherDecisionJournal, compute_state_hash


load_dotenv()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clob_client() -> ClobClient:
    host = (
        os.getenv("CLOB_BASE_URL", "").strip()
        or os.getenv("PM_API_BASE_URL", "").strip()
        or "https://clob.polymarket.com"
    )
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip() or os.getenv("PM", "").strip()
    if not private_key:
        raise RuntimeError("missing POLYGON_WALLET_PRIVATE_KEY or PM")

    signer_addr = ""
    try:
        signer_addr = ClobClient(host, chain_id=POLYGON, key=private_key).get_address()
    except Exception:
        signer_addr = ""

    funder = os.getenv("PM_ADDRESS", "").strip()
    sig_type_raw = int(os.getenv("CLOB_SIGNATURE_TYPE", "-1"))
    if sig_type_raw < 0:
        sig_type = 1 if funder and signer_addr and funder.lower() != signer_addr.lower() else 0
    else:
        sig_type = sig_type_raw

    api_key = os.getenv("CLOB_API_KEY", "").strip()
    api_secret = os.getenv("CLOB_SECRET", "").strip()
    api_pass = os.getenv("CLOB_PASS_PHRASE", "").strip()
    if api_key and api_secret and api_pass:
        creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass)
        return ClobClient(
            host,
            chain_id=POLYGON,
            key=private_key,
            creds=creds,
            signature_type=sig_type,
            funder=funder or None,
        )

    client = ClobClient(
        host,
        chain_id=POLYGON,
        key=private_key,
        signature_type=sig_type,
        funder=funder or None,
    )
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


def _configure_http_proxy(data_client: PolymarketDataClient, proxy_url: str) -> None:
    proxy = proxy_url.strip()
    if not proxy:
        return
    data_client.session.proxies.update({"http": proxy, "https": proxy})
    data_client.session.trust_env = False


def _asset_id(row: Dict[str, Any]) -> str:
    return str(row.get("asset") or row.get("assetId") or row.get("token_id") or "").strip()


def _is_weather_position(
    row: Dict[str, Any],
    include_tokens: Set[str],
    title_patterns: List[str],
) -> bool:
    asset = _asset_id(row)
    if include_tokens and asset in include_tokens:
        return True
    text = " ".join(
        [
            str(row.get("title") or ""),
            str(row.get("slug") or ""),
        ]
    ).lower()
    return any(pattern in text for pattern in title_patterns)


def _best_bid_ask_from_book(book: Any) -> tuple[float, float]:
    if hasattr(book, "bids") and hasattr(book, "asks"):
        bids = getattr(book, "bids") or []
        asks = getattr(book, "asks") or []
        bid_prices = [_to_float(getattr(item, "price", 0.0), 0.0) for item in bids]
        ask_prices = [_to_float(getattr(item, "price", 0.0), 0.0) for item in asks]
        bid = max((price for price in bid_prices if price > 0), default=0.0)
        ask = min((price for price in ask_prices if price > 0), default=0.0)
        return bid, ask
    text = str(book)
    # Lightweight parse from OrderBookSummary repr.
    bid = 0.0
    ask = 0.0
    bid_idx = text.find("bids=[OrderSummary(price='")
    if bid_idx >= 0:
        start = bid_idx + len("bids=[OrderSummary(price='")
        end = text.find("'", start)
        if end > start:
            bid = _to_float(text[start:end], 0.0)
    ask_idx = text.find("asks=[OrderSummary(price='")
    if ask_idx >= 0:
        start = ask_idx + len("asks=[OrderSummary(price='")
        end = text.find("'", start)
        if end > start:
            ask = _to_float(text[start:end], 0.0)
    return bid, ask


def _short_order_levels(orders: List[Dict[str, Any]]) -> List[str]:
    levels: List[str] = []
    for order in orders:
        price = _to_float(order.get("price"), 0.0)
        size = _to_float(order.get("original_size"), 0.0)
        if price > 0 and size > 0:
            levels.append(f"{size:g}@{price:.2f}")
    return levels


def _watch_entries_by_token(path: str) -> Dict[str, Dict[str, Any]]:
    raw_path = path.strip()
    if not raw_path:
        return {}
    payload = load_watch_baseline(Path(raw_path))
    out: Dict[str, Dict[str, Any]] = {}
    for item in payload.get("entries", []):
        if not isinstance(item, dict):
            continue
        token_id = str(item.get("token_id") or "").strip()
        if token_id:
            out[token_id] = item
    return out


def _suggestion_urgency(action: str, drift_triggered: bool) -> str:
    normalized = str(action or "").strip()
    if drift_triggered:
        return "high"
    if normalized in {"avoid_new_entry", "tighten_exit_or_avoid_new_entry", "size_down_and_review"}:
        return "medium"
    if normalized in {"size_down", "wait_for_tighter_book"}:
        return "low"
    return "low"


def _maybe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _bucket_bounds(market_meta: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    bucket_min = market_meta.get("bucket_min")
    bucket_max = market_meta.get("bucket_max")
    bucket_value = market_meta.get("bucket_value")
    if bucket_min is None and bucket_value is not None:
        bucket_min = bucket_value
    if bucket_max is None and bucket_value is not None:
        bucket_max = bucket_value
    lo = _maybe_float(bucket_min if bucket_min is not None else bucket_max)
    hi = _maybe_float(bucket_max if bucket_max is not None else bucket_min)
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _range_contains_target(
    *,
    range_low: Any,
    range_high: Any,
    target_low: Optional[float],
    target_high: Optional[float],
) -> bool:
    lo = _maybe_float(range_low)
    hi = _maybe_float(range_high)
    if lo is None or hi is None or target_low is None or target_high is None:
        return False
    return not (hi < target_low or lo > target_high)


def _derive_metrics(
    *,
    market_meta: Dict[str, Any],
    baseline_forecast: Dict[str, Any],
    latest_forecast: Dict[str, Any],
) -> Dict[str, Any]:
    target_low, target_high = _bucket_bounds(market_meta)
    baseline_distance = _maybe_float(baseline_forecast.get("edge_to_bucket"))
    latest_distance = _maybe_float(latest_forecast.get("edge_to_bucket"))
    baseline_contains = _range_contains_target(
        range_low=baseline_forecast.get("main_range_low"),
        range_high=baseline_forecast.get("main_range_high"),
        target_low=target_low,
        target_high=target_high,
    )
    latest_contains = _range_contains_target(
        range_low=latest_forecast.get("main_range_low"),
        range_high=latest_forecast.get("main_range_high"),
        target_low=target_low,
        target_high=target_high,
    )
    baseline_center = None
    latest_center = None
    base_lo = _maybe_float(baseline_forecast.get("main_range_low"))
    base_hi = _maybe_float(baseline_forecast.get("main_range_high"))
    latest_lo = _maybe_float(latest_forecast.get("main_range_low"))
    latest_hi = _maybe_float(latest_forecast.get("main_range_high"))
    if base_lo is not None and base_hi is not None:
        baseline_center = (base_lo + base_hi) / 2.0
    if latest_lo is not None and latest_hi is not None:
        latest_center = (latest_lo + latest_hi) / 2.0
    main_range_right_shift_bins = 0
    if baseline_center is not None and latest_center is not None:
        main_range_right_shift_bins = max(0, int(round(latest_center - baseline_center)))
    forecast_shift = _maybe_float(latest_forecast.get("forecast_shift_vs_baseline"))
    bucket_width = None
    if target_low is not None and target_high is not None:
        bucket_width = max(1.0, abs(target_high - target_low))
    moving_toward_target = False
    if baseline_distance is not None and latest_distance is not None:
        threshold = max(0.5, (bucket_width or 1.0) * 0.5)
        moving_toward_target = latest_distance <= (baseline_distance - threshold)
    return {
        "target_bucket_low": target_low,
        "target_bucket_high": target_high,
        "target_bucket_unit": str(latest_forecast.get("market_unit") or ""),
        "baseline_distance_to_target": baseline_distance,
        "latest_distance_to_target": latest_distance,
        "baseline_range_contains_target": baseline_contains,
        "latest_range_contains_target": latest_contains,
        "main_range_right_shift_bins": main_range_right_shift_bins,
        "peak_hour_shift_minutes": int(_to_float(latest_forecast.get("peak_hour_shift_minutes"), 0.0)),
        "forecast_shift": forecast_shift,
        "forecast_shift_unit": str(latest_forecast.get("market_unit") or ""),
        "moving_toward_target": moving_toward_target,
    }


def _build_decision_guardrails(
    *,
    market_meta: Dict[str, Any],
    station_code: str,
    latest_forecast: Dict[str, Any],
    baseline_forecast: Dict[str, Any],
    drift: Dict[str, Any],
    action_suggestion: Dict[str, Any],
    position_qty: float,
) -> Dict[str, Any]:
    unit = str(latest_forecast.get("market_unit") or baseline_forecast.get("market_unit") or market_meta.get("unit") or "").upper()
    target_low, target_high = _bucket_bounds(market_meta)
    integrity_errors: List[str] = []
    if unit not in {"C", "F"}:
        integrity_errors.append("market_unit_invalid")
    if target_low is None or target_high is None:
        integrity_errors.append("target_bucket_missing")
    for prefix, payload in (("baseline", baseline_forecast), ("latest", latest_forecast)):
        lo = _maybe_float(payload.get("main_range_low"))
        hi = _maybe_float(payload.get("main_range_high"))
        if lo is not None and hi is not None and lo > hi:
            integrity_errors.append(f"{prefix}_range_invalid")
    if not str(station_code or "").strip():
        integrity_errors.append("station_code_missing")

    derived = _derive_metrics(
        market_meta=market_meta,
        baseline_forecast=baseline_forecast,
        latest_forecast=latest_forecast,
    )
    latest_distance = _maybe_float(derived.get("latest_distance_to_target"))
    target_width = None
    if target_low is not None and target_high is not None:
        target_width = max(1.0, abs(target_high - target_low))
    near_target = latest_distance is not None and latest_distance <= max(0.75, target_width or 1.0)
    latest_contains = bool(derived.get("latest_range_contains_target"))
    moving_toward_target = bool(derived.get("moving_toward_target"))
    allow_stop_loss_review = bool(latest_contains or (near_target and moving_toward_target))
    suggestion_action = str(action_suggestion.get("action") or "").strip()
    max_allowed_urgency = "high" if allow_stop_loss_review else "medium" if (drift.get("triggered") or suggestion_action in {"avoid_new_entry", "tighten_exit_or_avoid_new_entry", "size_down_and_review"}) else "low"
    if not integrity_errors:
        if position_qty > 0:
            preferred_action_set = ["hold", "avoid_new_entry", "no_add", "partial_take_profit"]
            if allow_stop_loss_review:
                preferred_action_set.append("stop_loss_review")
        else:
            preferred_action_set = ["hold", "avoid_new_entry", "no_add"]
    else:
        preferred_action_set = ["manual_check"]

    return {
        "integrity_ok": not integrity_errors,
        "integrity_errors": integrity_errors,
        "allow_stop_loss_review": allow_stop_loss_review and not integrity_errors,
        "max_allowed_urgency": "high" if integrity_errors else max_allowed_urgency,
        "preferred_action_set": preferred_action_set,
        "derived_metrics": derived,
    }


def _analysis_action_text(analysis: Dict[str, Any], fallback: str) -> str:
    action = str(analysis.get("action") or "").strip()
    judgment = str(analysis.get("judgment") or "").strip()
    combined = " | ".join(part for part in [action, judgment] if part)
    return combined or fallback


def _fmt_num(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _yes_no(value: bool) -> str:
    return "是" if bool(value) else "否"


async def _run(args: argparse.Namespace) -> int:
    instance_id = args.instance_id
    wallet = args.wallet.strip() or os.getenv("PM_ADDRESS", "").strip()
    if not wallet:
        raise RuntimeError("missing wallet: pass --wallet or set PM_ADDRESS")

    cfg = PMMConfig.from_env()
    cfg.telegram_enabled = bool(os.getenv("PMM_TELEGRAM_ENABLED", "1") == "1")
    cfg.telegram_report_interval_sec = int(os.getenv("PMM_TELEGRAM_REPORT_INTERVAL_SEC", "3600"))
    cfg.telegram_send_startup = True
    cfg.market.symbol = args.symbol

    notifier = PMMTelegramNotifier(
        config=cfg,
        execution_mode="live",
        strategy_key="weather_edge_v1",
    )
    await notifier.start()

    store = StrategyRuntimeStore(args.db_path)
    store.ensure_builtin_strategies(load_strategy_rows())

    imported_tokens: Set[str] = set()
    data_client = PolymarketDataClient()
    _configure_http_proxy(data_client, args.proxy_url)
    clob = _clob_client()
    stop = False
    include_tokens = {x.strip() for x in args.token_ids.split(",") if x.strip()}
    title_patterns = [x.strip().lower() for x in args.title_patterns.split(",") if x.strip()]
    watch_entries = _watch_entries_by_token(args.weather_baseline_file)
    weather_tool = AirportWeatherTool(proxy_url=args.proxy_url) if args.weather_baseline_file.strip() else None
    last_weather_eval_ts = 0.0
    last_codex_analysis_by_token: Dict[str, float] = {}
    decision_journal = WeatherDecisionJournal(db_path=args.decision_journal_db)

    def _handle_stop(signum, frame) -> None:  # type: ignore[override]
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    try:
        tick = 0
        while not stop:
            rows = data_client.get_user_positions(user=wallet, limit=200)
            weather_rows = [row for row in rows if _is_weather_position(row, include_tokens, title_patterns)]
            position_rows = {_asset_id(row): row for row in weather_rows if _asset_id(row)}
            all_orders = clob.get_orders() or []
            orders_by_asset: Dict[str, List[Dict[str, Any]]] = {}
            for order in all_orders:
                if not isinstance(order, dict):
                    continue
                asset_id = str(order.get("asset_id") or order.get("assetId") or "").strip()
                if not asset_id:
                    continue
                orders_by_asset.setdefault(asset_id, []).append(order)
            watch_token_ids = sorted(set(include_tokens) | set(position_rows) | set(orders_by_asset))

            store.upsert_start(
                instance_id=instance_id,
                strategy_key="weather_edge_v1",
                label=args.label,
                execution_mode="live",
                market_data_source="external_api",
                account_id=wallet,
                wallet_address=wallet,
                token_ids=watch_token_ids,
                max_position=float(args.max_position),
                telegram_enabled=cfg.telegram_enabled,
                pid=os.getpid(),
                log_file=args.log_file,
                metrics_path="",
                cwd=os.getcwd(),
                run_params={
                    "mode": "portfolio_monitor",
                    "title_patterns": title_patterns,
                },
                runtime_paths={"db_path": args.db_path},
                notes="monitoring existing live weather strategy positions",
            )

            bal = clob.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            wallet_usdc_balance = _to_float((bal or {}).get("balance"), 0.0) / 1_000_000.0
            open_orders_count = len(all_orders)
            mids: Dict[str, float] = {}
            positions_map: Dict[str, float] = {}
            books_map: Dict[str, Dict[str, float]] = {}
            weather_items: List[Dict[str, Any]] = []
            total_current_value = 0.0
            total_cash_pnl = 0.0
            total_position_notional = 0.0

            pending_items: List[Dict[str, Any]] = []
            pending_notional = 0.0
            weather_watch_items: List[Dict[str, Any]] = []

            for token_id in watch_token_ids:
                row = position_rows.get(token_id, {})
                orders = orders_by_asset.get(token_id, [])
                token_open_orders = len(orders)
                book = clob.get_order_book(token_id)
                best_bid, best_ask = _best_bid_ask_from_book(book)
                qty = _to_float(row.get("size"), 0.0)
                avg_price = _to_float(row.get("avgPrice"), 0.0)
                cur_price = _to_float(row.get("curPrice"), qty and _to_float(row.get("currentValue"), 0.0) / qty)
                current_value = _to_float(row.get("currentValue"), qty * cur_price)
                cash_pnl = _to_float(row.get("cashPnl"), current_value - qty * avg_price)
                watch_meta = watch_entries.get(token_id, {})
                title = str(row.get("title") or watch_meta.get("market_title") or "")
                slug = str(row.get("slug") or "")
                condition_id = str(row.get("conditionId") or "")

                for order in orders:
                    order_id = str(order.get("id") or "").strip()
                    if not order_id:
                        continue
                    order_side = str(order.get("side") or "").strip().upper() or "BUY"
                    order_price = _to_float(order.get("price"), 0.0)
                    order_size = _to_float(order.get("original_size"), 0.0)
                    order_filled = _to_float(order.get("size_matched"), 0.0)
                    order_status = str(order.get("status") or "LIVE").strip().upper()
                    store.upsert_trade_order(
                        order_id=order_id,
                        instance_id=instance_id,
                        strategy_key="weather_edge_v1",
                        token_id=token_id,
                        side=order_side,
                        size=order_size,
                        price=order_price,
                        status=order_status,
                        order_type=str(order.get("order_type") or "LIMIT").strip().upper() or "LIMIT",
                        filled_size=order_filled,
                        average_price=order_price if order_filled > 0 else 0.0,
                        fee_paid=0.0,
                        metadata={
                            "source": "clob_open_orders",
                            "title": title,
                            "slug": slug,
                            "condition_id": condition_id,
                            "outcome": str(order.get("outcome") or ""),
                            "market": str(order.get("market") or ""),
                        },
                    )

                if token_id not in imported_tokens and qty > 0:
                    imported_tokens.add(token_id)
                    store.upsert_trade_order(
                        order_id=f"manual_import::{token_id}",
                        instance_id=instance_id,
                        strategy_key="weather_edge_v1",
                        token_id=token_id,
                        side="BUY",
                        size=qty,
                        price=avg_price,
                        status="FILLED",
                        order_type="LIMIT",
                        filled_size=qty,
                        average_price=avg_price,
                        fee_paid=0.0,
                        metadata={
                            "imported": True,
                            "source": "polymarket_data_api",
                            "title": title,
                            "slug": slug,
                            "condition_id": condition_id,
                        },
                    )

                books_map[token_id] = {"best_bid": best_bid, "best_ask": best_ask}
                if qty > 0:
                    mids[token_id] = cur_price
                    positions_map[token_id] = qty
                    total_current_value += current_value
                    total_cash_pnl += cash_pnl
                    total_position_notional += qty * avg_price
                    weather_items.append(
                        {
                            "token_id": token_id,
                            "title": title,
                            "slug": slug,
                            "condition_id": condition_id,
                            "outcome": "No",
                            "qty": qty,
                            "avg_price": avg_price,
                            "cur_price": cur_price,
                            "current_value": current_value,
                            "cash_pnl": cash_pnl,
                            "best_bid": best_bid,
                            "best_ask": best_ask,
                            "open_orders": token_open_orders,
                        }
                    )
                elif token_open_orders > 0:
                    pending_qty = 0.0
                    for order in orders:
                        original_size = _to_float(order.get("original_size"), 0.0)
                        size_matched = _to_float(order.get("size_matched"), 0.0)
                        pending_qty += max(0.0, original_size - size_matched)
                        pending_notional += max(0.0, original_size - size_matched) * _to_float(order.get("price"), 0.0)
                    pending_items.append(
                        {
                            "token_id": token_id,
                            "title": title,
                            "slug": slug,
                            "outcome": str(watch_meta.get("outcome") or "No"),
                            "best_bid": best_bid,
                            "best_ask": best_ask,
                            "open_orders": token_open_orders,
                            "pending_qty": pending_qty,
                            "levels": _short_order_levels(orders),
                        }
                    )
                if watch_meta:
                    weather_watch_items.append(
                        {
                            "token_id": token_id,
                            "city_key": str(watch_meta.get("city_key") or ""),
                            "local_date": str(watch_meta.get("local_date") or ""),
                            "market_title": str(watch_meta.get("market_title") or title or token_id),
                            "best_bid": best_bid,
                            "best_ask": best_ask,
                        }
                    )

            now_ts = asyncio.get_running_loop().time()  # monotonic for throttle
            if weather_tool is not None and (tick == 0 or (now_ts - last_weather_eval_ts) >= max(60, int(args.weather_poll_sec))):
                last_weather_eval_ts = now_ts
                refreshed_watch_entries = _watch_entries_by_token(args.weather_baseline_file)
                watch_entries.update(refreshed_watch_entries)
                new_weather_watch_items: List[Dict[str, Any]] = []
                for item in weather_watch_items:
                    token_id = str(item.get("token_id") or "")
                    watch_meta = watch_entries.get(token_id, {})
                    if not watch_meta:
                        continue
                    try:
                        snapshot = weather_tool.build_snapshot(
                            city_key=str(watch_meta.get("city_key") or ""),
                            local_date=str(watch_meta.get("local_date") or ""),
                            market_meta=dict(watch_meta.get("market_meta") or {}),
                            latest_orderbook={
                                "best_bid": item.get("best_bid", 0.0),
                                "best_ask": item.get("best_ask", 0.0),
                            },
                            baseline_forecast=dict(watch_meta.get("baseline_forecast") or {}),
                        )
                        updated_watch_meta = dict(watch_meta)
                        updated_watch_meta["latest_snapshot"] = snapshot
                        updated_watch_meta["baseline_forecast"] = dict(watch_meta.get("baseline_forecast") or snapshot.get("baseline_forecast") or {})
                        if watch_meta.get("city_key") and watch_meta.get("local_date"):
                            upsert_watch_entry(Path(args.weather_baseline_file), updated_watch_meta)
                        drift = dict(snapshot.get("drift_alert") or {})
                        thresholds = dict(snapshot.get("drift_thresholds") or {})
                        new_weather_watch_items.append(
                            {
                                "token_id": token_id,
                                "city_key": watch_meta.get("city_key"),
                                "local_date": watch_meta.get("local_date"),
                                "market_title": watch_meta.get("market_title"),
                                "latest_forecast": snapshot.get("latest_forecast", {}),
                                "latest_observation": snapshot.get("latest_observation", {}),
                                "action_suggestion": snapshot.get("action_suggestion", {}),
                                "drift_thresholds": thresholds,
                                "drift_alert": drift,
                            }
                        )
                        latest_forecast = dict(snapshot.get("latest_forecast") or {})
                        baseline_forecast = dict(snapshot.get("baseline_forecast") or {})
                        market_meta = dict(watch_meta.get("market_meta") or {})
                        position_qty = positions_map.get(token_id, 0.0)
                        guardrails = _build_decision_guardrails(
                            market_meta=market_meta,
                            station_code=str((snapshot.get("station") or {}).get("icao_id") or (snapshot.get("station") or {}).get("station_code") or ""),
                            latest_forecast=latest_forecast,
                            baseline_forecast=baseline_forecast,
                            drift=drift,
                            action_suggestion=dict(snapshot.get("action_suggestion") or {}),
                            position_qty=position_qty,
                        )
                        derived_metrics = dict(guardrails.get("derived_metrics") or {})
                        target_low, target_high = _bucket_bounds(market_meta)
                        analysis_payload: Dict[str, Any] = {
                            "market": {
                                "token_id": token_id,
                                "title": watch_meta.get("market_title"),
                                "outcome": watch_meta.get("outcome") or "No",
                                "city_key": watch_meta.get("city_key"),
                                "local_date": watch_meta.get("local_date"),
                                "target_bucket_low": target_low,
                                "target_bucket_high": target_high,
                                "target_bucket_unit": str(latest_forecast.get("market_unit") or market_meta.get("unit") or ""),
                                "resolution_station_code": str((snapshot.get("station") or {}).get("icao_id") or (snapshot.get("station") or {}).get("station_code") or ""),
                            },
                            "baseline_forecast": baseline_forecast,
                            "latest_forecast": latest_forecast,
                            "latest_observation": snapshot.get("latest_observation", {}),
                            "latest_orderbook": snapshot.get("latest_orderbook", {}),
                            "position": {
                                "qty": position_qty,
                                "weather_item": next((x for x in weather_items if x.get("token_id") == token_id), {}),
                                "pending_item": next((x for x in pending_items if x.get("token_id") == token_id), {}),
                            },
                            "action_suggestion": snapshot.get("action_suggestion", {}),
                            "derived_metrics": derived_metrics,
                            "decision_guardrails": {
                                "integrity_ok": guardrails.get("integrity_ok"),
                                "integrity_errors": guardrails.get("integrity_errors"),
                                "allow_stop_loss_review": guardrails.get("allow_stop_loss_review"),
                                "max_allowed_urgency": guardrails.get("max_allowed_urgency"),
                                "preferred_action_set": guardrails.get("preferred_action_set"),
                            },
                            "drift_thresholds": thresholds,
                            "drift_alert": drift,
                        }
                        suggestion_action = str((snapshot.get("action_suggestion") or {}).get("action") or "")
                        suggestion_event_type = "weather_action_suggestion"
                        suggestion_state_hash = compute_state_hash(
                            event_type=suggestion_event_type,
                            input_payload=analysis_payload,
                            action_suggestion=dict(snapshot.get("action_suggestion") or {}),
                        )
                        latest_suggestion = decision_journal.latest_decision(
                            token_id=token_id,
                            event_type=suggestion_event_type,
                        )
                        suggestion_changed = (
                            latest_suggestion is None
                            or str(latest_suggestion.get("state_hash") or "") != suggestion_state_hash
                        )

                        analysis: Dict[str, Any] = {}
                        codex_detail = ""
                        if args.codex_analysis_enabled and suggestion_changed:
                            try:
                                analysis = run_codex_analysis(
                                    payload=analysis_payload,
                                    project_root=Path.cwd(),
                                    model=args.codex_analysis_model,
                                    timeout_sec=args.codex_analysis_timeout_sec,
                                )
                                codex_detail = (
                                    f"\n模型判断\n"
                                    f"- 标题：{analysis.get('title')}\n"
                                    f"- 数据变化：{analysis.get('summary')}\n"
                                    f"- 判断：{analysis.get('judgment')}\n"
                                    f"- 结论：{analysis.get('action')}\n"
                                    f"- 紧急程度：{analysis.get('urgency')}"
                                )
                                last_codex_analysis_by_token[token_id] = time.time()
                            except Exception as exc:
                                codex_detail = f"\n模型判断\n- 调用失败：{exc}"
                        reasons = ", ".join(str(x) for x in (drift.get("reasons") or []))
                        suggestion_detail = (
                            f"市场：{watch_meta.get('market_title') or token_id}\n"
                            f"方向：{watch_meta.get('outcome') or 'No'} | 城市：{watch_meta.get('city_key')} | 日期：{watch_meta.get('local_date')}\n"
                            f"基准天气：最高温 {_fmt_num(baseline_forecast.get('max_temp_market_unit'))}{latest_forecast.get('market_unit')}，"
                            f"主区间 {baseline_forecast.get('main_range_low')}..{baseline_forecast.get('main_range_high')}{latest_forecast.get('market_unit')}\n"
                            f"最新天气：最高温 {_fmt_num(latest_forecast.get('max_temp_market_unit'))}{latest_forecast.get('market_unit')}，"
                            f"主区间 {latest_forecast.get('main_range_low')}..{latest_forecast.get('main_range_high')}{latest_forecast.get('market_unit')}，"
                            f"方向 {latest_forecast.get('forecast_direction')}\n"
                            f"目标桶关系：距离目标桶 {_fmt_num(derived_metrics.get('latest_distance_to_target'))}{derived_metrics.get('target_bucket_unit')}，"
                            f"主区间覆盖目标桶：{_yes_no(bool(derived_metrics.get('latest_range_contains_target')))}\n"
                            f"规则建议：{suggestion_action or '-'}\n"
                            f"原因：{str((snapshot.get('action_suggestion') or {}).get('reason') or '-')}\n"
                            f"是否已触发漂移：{_yes_no(bool(drift.get('triggered')))}"
                            f"{codex_detail}"
                        )
                        if suggestion_changed:
                            decision_journal.record_decision_if_changed(
                                instance_id=instance_id,
                                token_id=token_id,
                                city_key=str(watch_meta.get("city_key") or ""),
                                local_date=str(watch_meta.get("local_date") or ""),
                                market_title=str(watch_meta.get("market_title") or token_id),
                                outcome=str(watch_meta.get("outcome") or "No"),
                                event_type=suggestion_event_type,
                                urgency=str(analysis.get("urgency") or guardrails.get("max_allowed_urgency") or _suggestion_urgency(suggestion_action, False)),
                                action_text=_analysis_action_text(
                                    analysis,
                                    str((snapshot.get("action_suggestion") or {}).get("reason") or suggestion_action or "hold"),
                                ),
                                prompt_version="codex_weather_advisor_v1",
                                baseline_file=args.weather_baseline_file,
                                input_payload=analysis_payload,
                                codex_output=analysis,
                                action_suggestion=dict(snapshot.get("action_suggestion") or {}),
                                alert_message=suggestion_detail,
                            )
                        if drift.get("triggered"):
                            drift_event_type = "weather_main_range_drift"
                            drift_state_hash = compute_state_hash(
                                event_type=drift_event_type,
                                input_payload=analysis_payload,
                                action_suggestion=dict(snapshot.get("action_suggestion") or {}),
                            )
                            latest_drift = decision_journal.latest_decision(
                                token_id=token_id,
                                event_type=drift_event_type,
                            )
                            drift_changed = (
                                latest_drift is None
                                or str(latest_drift.get("state_hash") or "") != drift_state_hash
                            )
                            if args.codex_analysis_enabled and drift_changed and not analysis:
                                now_wall_ts = time.time()
                                last_analysis_ts = last_codex_analysis_by_token.get(token_id, 0.0)
                                if (now_wall_ts - last_analysis_ts) >= max(60, int(args.codex_analysis_min_interval_sec)):
                                    try:
                                        analysis = run_codex_analysis(
                                            payload=analysis_payload,
                                            project_root=Path.cwd(),
                                            model=args.codex_analysis_model,
                                            timeout_sec=args.codex_analysis_timeout_sec,
                                        )
                                        codex_detail = (
                                            f"\n模型判断\n"
                                            f"- 标题：{analysis.get('title')}\n"
                                            f"- 数据变化：{analysis.get('summary')}\n"
                                            f"- 判断：{analysis.get('judgment')}\n"
                                            f"- 结论：{analysis.get('action')}\n"
                                            f"- 紧急程度：{analysis.get('urgency')}"
                                        )
                                        last_codex_analysis_by_token[token_id] = now_wall_ts
                                    except Exception as exc:
                                        codex_detail = f"\n模型判断\n- 调用失败：{exc}"
                            detail = (
                                f"市场：{watch_meta.get('market_title') or token_id}\n"
                                f"方向：{watch_meta.get('outcome') or 'No'} | 城市：{watch_meta.get('city_key')} | 日期：{watch_meta.get('local_date')}\n"
                                f"\n"
                                f"数据变化\n"
                                f"- 基准最高温：{_fmt_num(baseline_forecast.get('max_temp_market_unit'))}{latest_forecast.get('market_unit')}\n"
                                f"- 基准主区间：{baseline_forecast.get('main_range_low')}..{baseline_forecast.get('main_range_high')}{latest_forecast.get('market_unit')}\n"
                                f"- 最新最高温：{_fmt_num(latest_forecast.get('max_temp_market_unit'))}{latest_forecast.get('market_unit')}\n"
                                f"- 最新主区间：{latest_forecast.get('main_range_low')}..{latest_forecast.get('main_range_high')}{latest_forecast.get('market_unit')}\n"
                                f"- 变化方向：{latest_forecast.get('forecast_direction')}\n"
                                f"- 距离目标桶：{_fmt_num(derived_metrics.get('latest_distance_to_target'))}{derived_metrics.get('target_bucket_unit')}\n"
                                f"- 主区间覆盖目标桶：{_yes_no(bool(derived_metrics.get('latest_range_contains_target')))}\n"
                                f"\n"
                                f"触发原因\n"
                                f"- {reasons or '达到天气漂移阈值'}\n"
                                f"- 规则阈值：主区间偏移>={thresholds.get('main_range_shift_bins')} 档，"
                                f"温度偏移>={thresholds.get('forecast_shift')}{thresholds.get('forecast_shift_unit')}，"
                                f"峰值时间偏移>={thresholds.get('peak_hour_shift_minutes')} 分钟\n"
                                f"\n"
                                f"处理结论\n"
                                f"- 请手动检查并考虑止损或撤单"
                                f"{codex_detail}"
                            )
                            decision_journal.record_decision_if_changed(
                                instance_id=instance_id,
                                token_id=token_id,
                                city_key=str(watch_meta.get("city_key") or ""),
                                local_date=str(watch_meta.get("local_date") or ""),
                                market_title=str(watch_meta.get("market_title") or token_id),
                                outcome=str(watch_meta.get("outcome") or "No"),
                                event_type=drift_event_type,
                                urgency=str(analysis.get("urgency") or guardrails.get("max_allowed_urgency") or _suggestion_urgency(suggestion_action, True)),
                                action_text=_analysis_action_text(analysis, "请手动检查并考虑止损/撤单"),
                                prompt_version="codex_weather_advisor_v1",
                                baseline_file=args.weather_baseline_file,
                                input_payload=analysis_payload,
                                codex_output=analysis,
                                action_suggestion=dict(snapshot.get("action_suggestion") or {}),
                                alert_message=detail,
                            )
                            await notifier.send_alert(
                                alert_key=f"weather_drift::{token_id}",
                                event="weather_main_range_drift",
                                detail=detail,
                                tick=tick,
                                pnl=total_cash_pnl,
                            )
                    except Exception as exc:
                        new_weather_watch_items.append(
                            {
                                "token_id": token_id,
                                "market_title": watch_meta.get("market_title") or token_id,
                                "error": str(exc),
                            }
                        )
                weather_watch_items = new_weather_watch_items

            state = {
                "positions": positions_map,
                "weather_items": weather_items,
                "position_count": len(weather_items),
                "position_notional": total_position_notional,
                "current_value": total_current_value,
                "cash_pnl": total_cash_pnl,
                "wallet_usdc_balance": wallet_usdc_balance,
                "books": books_map,
                "pending_items": pending_items,
                "pending_notional": pending_notional,
                "weather_watch_items": weather_watch_items,
                "watched_token_ids": watch_token_ids,
            }
            # For position-monitor instances, equity should reflect the tracked
            # strategy allocation, not the entire wallet's idle USDC.
            strategy_usdc_balance = 0.0
            equity = total_current_value + strategy_usdc_balance
            store.heartbeat(
                instance_id=instance_id,
                tick=tick,
                pnl=total_cash_pnl,
                equity=equity,
                usdc_balance=strategy_usdc_balance,
                open_orders=open_orders_count,
                fills_total=len(imported_tokens),
                placed_total=len(imported_tokens) + len(all_orders),
                canceled_total=0,
                errors_total=0,
                state=state,
                snapshot_interval_sec=max(30, int(args.snapshot_interval_sec)),
            )
            await notifier.maybe_send_periodic_report(
                tick=tick,
                pnl=total_cash_pnl,
                equity=wallet_usdc_balance + total_current_value,
                usdc_balance=wallet_usdc_balance,
                positions=positions_map,
                mids=mids,
                open_orders_count=open_orders_count,
                fills_total=len(imported_tokens),
                placed_total=len(imported_tokens) + len(all_orders),
                canceled_total=0,
                pending_items=pending_items,
                position_items=weather_items,
                weather_watch_items=weather_watch_items,
                force=(tick == 0),
            )

            if args.once:
                break
            tick += 1
            await asyncio.sleep(max(5, int(args.poll_sec)))
    finally:
        try:
            if hasattr(clob, "shutdown"):
                clob.shutdown()
        except Exception:
            pass
        decision_journal.close()
        store.mark_stopped(instance_id, status="stopped" if stop else "stopped")
        store.close()
        await notifier.aclose()
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Monitor live weather-strategy positions and publish them as one strategy instance.")
    p.add_argument("--instance-id", default="weather_strategy_live_001")
    p.add_argument("--label", default="Weather strategy live")
    p.add_argument("--symbol", default="Weather Strat")
    p.add_argument("--token-ids", default="")
    p.add_argument("--wallet", default=os.getenv("PM_ADDRESS", "").strip())
    p.add_argument("--title-patterns", default="highest temperature in,temperature in")
    p.add_argument(
        "--proxy-url",
        default=(
            os.getenv("POLYMARKET_PROXY_URL", "").strip()
            or os.getenv("HTTPS_PROXY", "").strip()
            or os.getenv("HTTP_PROXY", "").strip()
            or os.getenv("https_proxy", "").strip()
            or os.getenv("http_proxy", "").strip()
        ),
    )
    p.add_argument("--db-path", default="runtime/strategy_runtime.db")
    p.add_argument("--log-file", default="runtime/logs/weather_strategy_live_001.log")
    p.add_argument("--poll-sec", type=int, default=30)
    p.add_argument("--snapshot-interval-sec", type=int, default=60)
    p.add_argument("--max-position", type=float, default=100.0)
    p.add_argument(
        "--weather-baseline-file",
        default="src/strategies/weather_edge_v1/plan/watch",
    )
    p.add_argument("--weather-poll-sec", type=int, default=300)
    p.add_argument("--codex-analysis-enabled", action="store_true")
    p.add_argument("--codex-analysis-timeout-sec", type=int, default=90)
    p.add_argument("--codex-analysis-min-interval-sec", type=int, default=1800)
    p.add_argument("--codex-analysis-model", default="")
    p.add_argument("--decision-journal-db", default="runtime/weather_decision_journal.db")
    p.add_argument("--once", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
