#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.execution_pipeline import (
    DEFAULT_RUNTIME_ROOT,
    ExecutorConfig,
    cancel_log_path_for_live_out,
    execute_trade_plans,
)
from src.strategies.weather_edge_v1.tools.execution_policy import (
    ExecutionPolicyConfig,
    build_execution_quote,
    build_execution_quotes,
)


def _extract_order_id(payload: Any) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    for key in ("order_id", "orderID", "id"):
        value = payload.get(key)
        if value:
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("order_id", "orderID", "id"):
            value = data.get(key)
            if value:
                return str(value)
    return None


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _best_bid_ask_from_book(book: Any) -> Tuple[float, float]:
    if isinstance(book, dict):
        bids = book.get("bids") or []
        asks = book.get("asks") or []
    else:
        bids = getattr(book, "bids", None) or []
        asks = getattr(book, "asks", None) or []

    def _level_price(item: Any) -> float:
        if isinstance(item, dict):
            return _to_float(item.get("price"), 0.0)
        return _to_float(getattr(item, "price", 0.0), 0.0)

    bid_prices = [_level_price(item) for item in bids]
    ask_prices = [_level_price(item) for item in asks]
    best_bid = max((price for price in bid_prices if price > 0), default=0.0)
    best_ask = min((price for price in ask_prices if price > 0), default=0.0)
    return best_bid, best_ask


def _maker_only_price(
    *,
    side: str,
    requested_price: float,
    best_bid: float,
    best_ask: float,
) -> float:
    side = side.upper().strip()
    if side == "BUY":
        if best_ask <= 0:
            return 0.0
        if requested_price < best_ask:
            return requested_price
        return best_bid if best_bid > 0 and best_bid < best_ask else 0.0
    if side == "SELL":
        if requested_price <= 0:
            return 0.0
        if best_bid <= 0:
            return requested_price
        if requested_price > best_bid:
            return requested_price
        return best_ask if best_ask > best_bid else 0.0
    return 0.0


def _get_tick_size(client: Any, token_id: str, fallback: float) -> float:
    for name in ("get_tick_size", "getTickSize"):
        fn = getattr(client, name, None)
        if not callable(fn):
            continue
        try:
            value = fn(token_id)
            tick = _to_float(value, fallback)
            if tick > 0:
                return tick
        except Exception:
            continue
    return fallback if fallback > 0 else 0.001


class WeatherExecutionError(RuntimeError):
    def __init__(self, message: str, *, response: Dict[str, Any]):
        super().__init__(message)
        self.weather_execution_response = response


def _classify_live_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if "invalid post-only order" in text or "order crosses book" in text:
        return "post_only_crosses_book"
    if "No orderbook exists" in text:
        return "no_orderbook"
    if "service not ready" in text:
        return "service_not_ready"
    if "maker_only_no_resting_price" in text:
        return "maker_only_no_resting_price"
    if "maker_only_price_would_cross" in text:
        return "maker_only_price_would_cross"
    return "live_order_error"


def _build_position_lines(*, wallet: str, limit: int = 8) -> List[str]:
    if not wallet:
        return ["- 持仓读取跳过：缺少 PM_ADDRESS"]
    try:
        from src.platform.clients.polymarket_data import PolymarketDataClient

        rows = PolymarketDataClient().get_user_positions(user=wallet, limit=200)
    except Exception as exc:
        return [f"- 持仓读取失败：{type(exc).__name__}: {exc}"]

    weather_rows: List[Dict[str, Any]] = []
    for row in rows:
        text = " ".join([str(row.get("title") or ""), str(row.get("slug") or "")]).lower()
        if "highest temperature" in text or "temperature in" in text:
            weather_rows.append(row)
    if not weather_rows:
        return ["- 当前未读取到 weather 相关持仓"]

    lines = []
    for row in weather_rows[:limit]:
        title = str(row.get("title") or row.get("slug") or row.get("asset") or "").strip()
        outcome = str(row.get("outcome") or "").strip()
        qty = _to_float(row.get("size"), 0.0)
        cur_price = _to_float(row.get("curPrice"), 0.0)
        current_value = _to_float(row.get("currentValue"), qty * cur_price)
        label = title if len(title) <= 80 else f"{title[:77]}..."
        lines.append(f"- {label} [{outcome}]：{qty:g} 股，现价 {cur_price:.3f}，市值 {current_value:.2f}")
    if len(weather_rows) > limit:
        lines.append(f"- 另有 {len(weather_rows) - limit} 条 weather 持仓未展开")
    return lines


def _short_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except Exception:
        return str(path)


def _send_execution_telegram(result: Dict[str, Any], *, live_out: Path) -> None:
    if not bool(result.get("live_requested")):
        return
    try:
        from src.platform.notification.telegram import send_telegram_message_sync
    except Exception as exc:
        print(f"[WARN] telegram helper unavailable: {type(exc).__name__}: {exc}")
        return

    live_orders = int(result.get("live_orders", 0) or 0)
    live_written = int(result.get("live_written", 0) or 0)
    live_errors = int(result.get("live_errors", 0) or 0)
    skipped_disabled = int(result.get("live_skipped_disabled", 0) or 0)
    paper_written = int(result.get("paper_written", 0) or 0)
    if live_orders > 0 and live_errors == 0:
        headline = f"已提交 {live_written} 笔真实挂单。"
    elif live_errors > 0:
        headline = f"本次执行有 {live_errors} 笔失败；没有主动吃单。"
    else:
        headline = "本次没有提交真实订单。"

    wallet = os.getenv("PM_ADDRESS", "").strip()
    lines = [
        "【天气策略下单回报】",
        headline,
        "",
        f"读取计划：{int(result.get('plans_read', 0) or 0)} 条。",
        f"下单结果：成功 {live_written} 笔，失败 {live_errors} 笔。",
    ]
    if paper_written:
        lines.append(f"模拟记录：写入 {paper_written} 条。")
    if skipped_disabled:
        lines.append(f"未启用实盘而跳过：{skipped_disabled} 条。")
    lines.extend(
        [
            "",
            f"记录文件：{_short_path(live_out)}",
            "",
            "当前天气市场相关持仓：",
            *_build_position_lines(wallet=wallet),
        ]
    )
    if live_errors > 0:
        lines.extend(
            [
                "",
                "需要处理：优先检查余额、授权、交易所 API 鉴权，以及盘口是否还有可挂价格。",
            ]
        )
    try:
        send_telegram_message_sync("\n".join(lines))
    except Exception as exc:
        print(f"[WARN] telegram send failed: {type(exc).__name__}: {exc}")


def _cancel_order(client: Any, *, clob_v2: bool, order_payload_cls: Any, order_id: str) -> Any:
    if clob_v2:
        return client.cancel_order(order_payload_cls(orderID=order_id))
    return client.cancel(order_id)


def _build_live_cancel_fn():
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds, OrderPayload
        from py_clob_client_v2.constants import POLYGON

        clob_v2 = True
    except ModuleNotFoundError:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
        from py_clob_client.constants import POLYGON

        OrderPayload = None  # type: ignore[assignment]
        clob_v2 = False

    host = os.getenv("CLOB_BASE_URL", "").strip() or os.getenv("PM_API_BASE_URL", "").strip() or "https://clob.polymarket.com"
    chain_id = int(os.getenv("CLOB_CHAIN_ID", str(POLYGON)))
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip() or os.getenv("PM", "").strip()
    if not private_key:
        raise RuntimeError("missing POLYGON_WALLET_PRIVATE_KEY or PM")

    signature_type_raw = int(os.getenv("CLOB_SIGNATURE_TYPE", "-1"))
    funder = os.getenv("PM_ADDRESS", "").strip()
    try:
        signer_addr = ClobClient(host, chain_id=chain_id, key=private_key).get_address()
    except Exception:
        signer_addr = ""
    signature_type = signature_type_raw
    if signature_type < 0:
        signature_type = 1 if funder and signer_addr and funder.lower() != signer_addr.lower() else 0

    api_key = os.getenv("CLOB_API_KEY", "").strip()
    api_secret = os.getenv("CLOB_SECRET", "").strip()
    api_pass = os.getenv("CLOB_PASS_PHRASE", "").strip()
    creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass) if api_key and api_secret and api_pass else None
    client = ClobClient(
        host,
        chain_id=chain_id,
        key=private_key,
        creds=creds,
        signature_type=signature_type,
        funder=funder or None,
    )
    if creds is None:
        if clob_v2:
            client.set_api_creds(client.derive_api_key())
        else:
            client.set_api_creds(client.create_or_derive_api_creds())

    def cancel(order_id: str) -> Dict[str, Any]:
        return {"cancel": _cancel_order(client, clob_v2=clob_v2, order_payload_cls=OrderPayload, order_id=order_id)}

    return cancel


def _build_lazy_live_cancel_fn():
    live_cancel_fn = None

    def cancel(order_id: str) -> Dict[str, Any]:
        nonlocal live_cancel_fn
        if live_cancel_fn is None:
            live_cancel_fn = _build_live_cancel_fn()
        return live_cancel_fn(order_id)

    return cancel


def _build_live_place_fn(*, cancel_after: bool, default_maker_only: bool):
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds, OrderArgsV2, OrderPayload, OrderType
        from py_clob_client_v2.constants import POLYGON

        clob_v2 = True
    except ModuleNotFoundError:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
        from py_clob_client.constants import POLYGON

        OrderArgsV2 = OrderArgs  # type: ignore[assignment]
        OrderPayload = None  # type: ignore[assignment]
        clob_v2 = False

    host = os.getenv("CLOB_BASE_URL", "").strip() or os.getenv("PM_API_BASE_URL", "").strip() or "https://clob.polymarket.com"
    chain_id = int(os.getenv("CLOB_CHAIN_ID", str(POLYGON)))
    private_key = os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip() or os.getenv("PM", "").strip()
    if not private_key:
        raise RuntimeError("missing POLYGON_WALLET_PRIVATE_KEY or PM")

    signature_type_raw = int(os.getenv("CLOB_SIGNATURE_TYPE", "-1"))
    funder = os.getenv("PM_ADDRESS", "").strip()
    try:
        signer_addr = ClobClient(host, chain_id=chain_id, key=private_key).get_address()
    except Exception:
        signer_addr = ""
    signature_type = signature_type_raw
    if signature_type < 0:
        signature_type = 1 if funder and signer_addr and funder.lower() != signer_addr.lower() else 0

    api_key = os.getenv("CLOB_API_KEY", "").strip()
    api_secret = os.getenv("CLOB_SECRET", "").strip()
    api_pass = os.getenv("CLOB_PASS_PHRASE", "").strip()
    creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass) if api_key and api_secret and api_pass else None
    client = ClobClient(
        host,
        chain_id=chain_id,
        key=private_key,
        creds=creds,
        signature_type=signature_type,
        funder=funder or None,
    )
    if creds is None:
        if clob_v2:
            client.set_api_creds(client.derive_api_key())
        else:
            client.set_api_creds(client.create_or_derive_api_creds())

    def place(plan: Dict[str, Any]) -> Dict[str, Any]:
        side = str(plan.get("order_side") or "BUY").upper().strip()
        execution_policy = str(plan.get("execution_policy") or "").strip()
        child_order_role = str(plan.get("child_order_role") or "single").strip() or "single"
        maker_only = bool(plan.get("maker_only", default_maker_only))
        requested_price = float(plan["limit_price"])
        order_price = requested_price
        best_bid = 0.0
        best_ask = 0.0
        tick_size = _to_float(plan.get("quote_tick_size"), 0.01)
        quote: Dict[str, Any] = {
            "quote_status": "",
            "quote_reason": "",
            "quote_edge": _to_float(plan.get("quote_edge"), 0.0),
            "required_quote_edge": _to_float(plan.get("required_quote_edge"), 0.0),
            "model_token_probability": _to_float(plan.get("model_token_probability"), 0.0),
            "quote_best_bid": _to_float(plan.get("quote_best_bid"), 0.0),
            "quote_best_ask": _to_float(plan.get("quote_best_ask"), 0.0),
            "quote_spread": _to_float(plan.get("quote_spread"), 0.0),
            "quote_tick_size": tick_size,
            "quote_mode": "not_evaluated",
        }

        def _diagnostics(*, classification: str, reason: str = "") -> Dict[str, Any]:
            return {
                "error_classification": classification,
                "error_reason": reason,
                "maker_only": bool(maker_only),
                "cancel_after": bool(cancel_after),
                "side": side,
                "requested_price": requested_price,
                "attempted_price": order_price,
                "posted_price": 0.0,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": max(0.0, best_ask - best_bid) if best_bid > 0 and best_ask > 0 else 0.0,
                "tick_size": tick_size,
                "token_id": str(plan.get("token_id") or ""),
                "market_id": str(plan.get("market_id") or ""),
                "city": str(plan.get("city") or ""),
                "target_date": str(plan.get("target_date") or ""),
                "bracket": str(plan.get("bracket") or ""),
                "signal_side": str(plan.get("signal_side") or ""),
                "execution_policy": str(plan.get("execution_policy") or ""),
                "child_order_role": str(plan.get("child_order_role") or "single"),
                "diagnostic_key": "|".join(
                    [
                        str(plan.get("target_date") or ""),
                        str(plan.get("city") or ""),
                        str(plan.get("bracket") or ""),
                        str(plan.get("signal_side") or ""),
                        str(plan.get("token_id") or ""),
                    ]
                ),
                **(quote if isinstance(quote, dict) else {}),
            }

        needs_live_policy_quote = execution_policy == "mid_price_core_v2"
        if maker_only or needs_live_policy_quote:
            try:
                book = client.get_order_book(str(plan["token_id"]))
            except Exception as exc:
                classification = _classify_live_error(exc)
                raise WeatherExecutionError(
                    f"{classification}: {exc}",
                    response=_diagnostics(classification=classification, reason="get_order_book_failed"),
                ) from exc
            best_bid, best_ask = _best_bid_ask_from_book(book)
            tick_size = _get_tick_size(client, str(plan["token_id"]), _to_float(plan.get("quote_tick_size"), 0.01))
            if execution_policy == "mid_price_core_v2":
                quotes = build_execution_quotes(
                    plan,
                    ExecutionPolicyConfig(
                        policy_name="mid_price_core_v2",
                        price_floor=0.01,
                        price_ceiling=0.99,
                        tick_size=tick_size,
                        min_quote_edge=_to_float(plan.get("min_quote_edge"), 0.03),
                        max_quote_spread=_to_float(plan.get("max_quote_spread"), 0.12),
                        max_mid_drift=_to_float(plan.get("max_mid_drift"), 0.10),
                        quote_improvement_ticks=int(_to_float(plan.get("quote_improvement_ticks"), 1.0)),
                        wide_spread_shade_ticks=int(_to_float(plan.get("wide_spread_shade_ticks"), 1.0)),
                        narrow_spread=_to_float(plan.get("narrow_quote_spread"), 0.03),
                        adverse_selection_spread_fraction=_to_float(
                            plan.get("adverse_selection_spread_fraction"),
                            0.50,
                        ),
                        low_band_ceiling=_to_float(plan.get("low_band_ceiling"), 0.40),
                        high_band_floor=_to_float(plan.get("high_band_floor"), 0.55),
                        split_enabled=bool(plan.get("split_enabled", True)),
                        taker_fraction=_to_float(plan.get("taker_fraction"), 0.50),
                        split_min_edge=_to_float(plan.get("split_min_edge"), 0.10),
                        high_band_shade_narrow=int(_to_float(plan.get("high_band_shade_narrow"), 1.0)),
                        high_band_shade_wide=int(_to_float(plan.get("high_band_shade_wide"), 2.0)),
                        high_band_min_edge=_to_float(plan.get("high_band_min_edge"), 0.15),
                        high_band_size_mult=_to_float(plan.get("high_band_size_mult"), 0.60),
                    ),
                    best_bid=best_bid,
                    best_ask=best_ask,
                    tick_size=tick_size,
                )
                quote = next(
                    (item for item in quotes if str(item.get("child_order_role") or "single") == child_order_role),
                    None,
                )
                if quote is None:
                    # The role may not match because build_execution_quotes emitted a
                    # signal-level reject (e.g. mid_drift_too_large) with role="single".
                    # Surface that real reason instead of the misleading missing_child_quote.
                    signal_reject = next(
                        (q for q in quotes if isinstance(q, dict) and q.get("quote_status") == "rejected"),
                        None,
                    )
                    reason = (
                        str(signal_reject.get("quote_reason") or f"missing_child_quote:{child_order_role}")
                        if signal_reject is not None
                        else f"missing_child_quote:{child_order_role}"
                    )
                    raise WeatherExecutionError(
                        "mid_price_core_v2_quote_rejected "
                        f"reason={reason} best_bid={best_bid:.6f} best_ask={best_ask:.6f}",
                        response=_diagnostics(classification="mid_price_core_v2_quote_rejected", reason=reason),
                    )
                order_price = _to_float(quote.get("limit_price"), 0.0)
                maker_only = bool(quote.get("maker_only", maker_only))
                if quote.get("quote_status") != "accepted" or order_price <= 0:
                    reason = str(quote.get("quote_reason") or "mid_price_core_v2_quote_rejected")
                    raise WeatherExecutionError(
                        "mid_price_core_v2_quote_rejected "
                        f"reason={reason} "
                        f"best_bid={best_bid:.6f} best_ask={best_ask:.6f} "
                        f"quote_edge={_to_float(quote.get('quote_edge'), 0.0):.6f} "
                        f"required={_to_float(quote.get('required_quote_edge'), 0.0):.6f}",
                        response=_diagnostics(classification="mid_price_core_v2_quote_rejected", reason=reason),
                    )
            elif execution_policy == "maker_queue_v1":
                raise WeatherExecutionError(
                    "maker_queue_v1_retired",
                    response=_diagnostics(
                        classification="maker_queue_v1_retired",
                        reason="execution_policy_removed_from_active_strategies",
                    ),
                )
            elif execution_policy == "maker_queue_v2":
                quote = build_execution_quote(
                    plan,
                    ExecutionPolicyConfig(
                        policy_name="maker_queue_v2",
                        price_floor=0.01,
                        price_ceiling=0.99,
                        tick_size=tick_size,
                        min_quote_edge=_to_float(plan.get("min_quote_edge"), 0.03),
                        max_quote_spread=_to_float(plan.get("max_quote_spread"), 0.12),
                        max_mid_drift=_to_float(plan.get("max_mid_drift"), 0.10),
                        quote_improvement_ticks=int(_to_float(plan.get("quote_improvement_ticks"), 1.0)),
                        wide_spread_shade_ticks=int(_to_float(plan.get("wide_spread_shade_ticks"), 1.0)),
                        narrow_spread=_to_float(plan.get("narrow_quote_spread"), 0.03),
                        adverse_selection_spread_fraction=_to_float(
                            plan.get("adverse_selection_spread_fraction"),
                            0.50,
                        ),
                    ),
                    best_bid=best_bid,
                    best_ask=best_ask,
                    tick_size=tick_size,
                )
                order_price = _to_float(quote.get("limit_price"), 0.0)
                if quote.get("quote_status") != "accepted" or order_price <= 0:
                    reason = str(quote.get("quote_reason") or "maker_queue_quote_rejected")
                    raise WeatherExecutionError(
                        "maker_queue_quote_rejected "
                        f"reason={reason} "
                        f"best_bid={best_bid:.6f} best_ask={best_ask:.6f} "
                        f"quote_edge={_to_float(quote.get('quote_edge'), 0.0):.6f} "
                        f"required={_to_float(quote.get('required_quote_edge'), 0.0):.6f}",
                        response=_diagnostics(classification="maker_queue_quote_rejected", reason=reason),
                    )
            else:
                order_price = _maker_only_price(
                    side=side,
                    requested_price=requested_price,
                    best_bid=best_bid,
                    best_ask=best_ask,
                )
                quote = {
                    "quote_status": "accepted" if order_price > 0 else "rejected",
                    "quote_reason": "" if order_price > 0 else "maker_only_no_resting_price",
                    "quote_edge": _to_float(plan.get("quote_edge"), 0.0),
                    "required_quote_edge": _to_float(plan.get("required_quote_edge"), 0.0),
                    "model_token_probability": _to_float(plan.get("model_token_probability"), 0.0),
                    "quote_best_bid": best_bid,
                    "quote_best_ask": best_ask,
                    "quote_spread": max(0.0, best_ask - best_bid) if best_bid > 0 and best_ask > 0 else 0.0,
                    "quote_tick_size": tick_size,
                    "quote_mode": "executor_clamp",
                }
            if maker_only and side == "BUY" and order_price >= best_ask and best_ask > 0:
                raise WeatherExecutionError(
                    "maker_only_price_would_cross "
                    f"price={order_price:.6f} best_ask={best_ask:.6f}",
                    response=_diagnostics(
                        classification="maker_only_price_would_cross",
                        reason="computed_price_crosses_best_ask",
                    ),
                )
            if maker_only and side == "SELL" and order_price <= best_bid and best_bid > 0:
                raise WeatherExecutionError(
                    "maker_only_price_would_cross "
                    f"price={order_price:.6f} best_bid={best_bid:.6f}",
                    response=_diagnostics(
                        classification="maker_only_price_would_cross",
                        reason="computed_price_crosses_best_bid",
                    ),
                )
            if maker_only and order_price <= 0:
                raise WeatherExecutionError(
                    "maker_only_no_resting_price "
                    f"side={side} requested={requested_price:.6f} "
                    f"best_bid={best_bid:.6f} best_ask={best_ask:.6f}",
                    response=_diagnostics(
                        classification="maker_only_no_resting_price",
                        reason="missing_valid_resting_price",
                    ),
                )
        else:
            quote = {
                "quote_status": "accepted",
                "quote_reason": "",
                "quote_edge": _to_float(plan.get("quote_edge"), 0.0),
                "required_quote_edge": _to_float(plan.get("required_quote_edge"), 0.0),
                "model_token_probability": _to_float(plan.get("model_token_probability"), 0.0),
                "quote_best_bid": best_bid,
                "quote_best_ask": best_ask,
                "quote_spread": max(0.0, best_ask - best_bid) if best_bid > 0 and best_ask > 0 else 0.0,
                "quote_tick_size": _to_float(plan.get("quote_tick_size"), 0.01),
                "quote_mode": "taker_allowed",
            }
        try:
            signed_order = client.create_order(
                OrderArgsV2(
                    token_id=str(plan["token_id"]),
                    price=float(order_price),
                    size=float(plan["size"]),
                    side=side,
                )
            )
            if clob_v2:
                response = client.post_order(
                    signed_order,
                    order_type=OrderType.GTC,
                    post_only=bool(maker_only),
                )
            else:
                response = client.post_order(
                    signed_order,
                    orderType=OrderType.GTC,
                    post_only=bool(maker_only),
                )
        except Exception as exc:
            classification = _classify_live_error(exc)
            raise WeatherExecutionError(
                f"{classification}: {exc}",
                response=_diagnostics(classification=classification, reason="post_order_failed"),
            ) from exc
        result: Dict[str, Any] = {"place": response}
        result["clob_client"] = "py_clob_client_v2" if clob_v2 else "py_clob_client"
        result["maker_only"] = bool(maker_only)
        result["requested_price"] = requested_price
        result["posted_price"] = order_price
        result["best_bid"] = best_bid
        result["best_ask"] = best_ask
        result.update(quote)
        if cancel_after:
            order_id = _extract_order_id(response)
            if order_id:
                result["cancel"] = _cancel_order(client, clob_v2=clob_v2, order_payload_cls=OrderPayload, order_id=order_id)
            else:
                result["cancel_error"] = "missing order id"
        return result

    return place


def _plans_require_live_place(plan_path: Path) -> bool:
    if not plan_path.exists():
        return False
    for line in plan_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("record_type") == "weather_edge_trade_plan" and bool(row.get("live_enabled", False)):
            return True
    return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute weather_edge_v1 trade plans to paper and optionally live CLOB.")
    parser.add_argument("--plans", default=str(DEFAULT_RUNTIME_ROOT / "plans" / "trade_plans.jsonl"))
    parser.add_argument("--paper-out", default=str(DEFAULT_RUNTIME_ROOT / "paper" / "orders.jsonl"))
    parser.add_argument("--live-out", default=str(DEFAULT_RUNTIME_ROOT / "live" / "orders.jsonl"))
    parser.add_argument("--live", action="store_true", help="Submit live orders for plans marked live_enabled=true.")
    parser.add_argument("--confirm-live", action="store_true", help="Required with --live.")
    parser.add_argument("--cancel-after", action="store_true", help="Cancel live orders immediately after placement.")
    parser.add_argument("--cancel-expired", action="store_true", help="Cancel previously submitted live orders whose expires_at_utc has passed.")
    parser.add_argument("--cancel-log", default="", help="JSONL path for expired-order cancel records. Defaults next to --live-out.")
    parser.add_argument(
        "--allow-taker",
        action="store_true",
        help="Disable maker-only protection. Do not use for weather strategy rollout.",
    )
    parser.add_argument("--no-telegram", action="store_true", help="Do not send Telegram execution summary after live run.")
    return parser


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ModuleNotFoundError:
        pass
    args = _parser().parse_args()
    plan_path = Path(args.plans)
    live_place_fn = (
        _build_live_place_fn(cancel_after=bool(args.cancel_after), default_maker_only=not bool(args.allow_taker))
        if args.live and _plans_require_live_place(plan_path)
        else None
    )
    live_cancel_fn = _build_lazy_live_cancel_fn() if args.live and args.cancel_expired else None
    live_out = Path(args.live_out)
    result = execute_trade_plans(
        plan_path=plan_path,
        paper_out=Path(args.paper_out),
        live_out=live_out,
        config=ExecutorConfig(
            live=bool(args.live),
            confirm_live=bool(args.confirm_live),
            cancel_after=bool(args.cancel_after),
            cancel_expired=bool(args.cancel_expired),
            cancel_log_path=Path(args.cancel_log) if args.cancel_log else cancel_log_path_for_live_out(live_out),
        ),
        live_place_fn=live_place_fn,
        live_cancel_fn=live_cancel_fn,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.live and not args.no_telegram:
        _send_execution_telegram(result, live_out=Path(args.live_out))
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
