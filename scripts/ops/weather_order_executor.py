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
    execute_trade_plans,
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
        if best_bid <= 0:
            return 0.0
        if requested_price > best_bid:
            return requested_price
        return best_ask if best_ask > best_bid else 0.0
    return 0.0


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
        headline = f"已提交 {live_written} 笔 maker-only 实盘订单。"
    elif live_errors > 0:
        headline = f"本次执行有 {live_errors} 笔失败；未发送 taker 单。"
    else:
        headline = "本次没有提交实盘订单。"

    wallet = os.getenv("PM_ADDRESS", "").strip()
    lines = [
        "【Weather 实盘下单回报】",
        headline,
        "",
        f"计划读取：{int(result.get('plans_read', 0) or 0)} 条",
        f"paper 记录：{paper_written} 条",
        f"实盘提交：{live_written} 条成功，{live_errors} 条失败",
        f"未启用实盘而跳过：{skipped_disabled} 条",
        f"实盘记录文件：{_short_path(live_out)}",
        "",
        "当前 Weather 相关持仓：",
        *_build_position_lines(wallet=wallet),
    ]
    if live_errors > 0:
        lines.extend(["", "需要处理：优先检查余额、allowance、CLOB 鉴权和盘口是否仍有可挂 maker 价格。"])
    try:
        send_telegram_message_sync("\n".join(lines))
    except Exception as exc:
        print(f"[WARN] telegram send failed: {type(exc).__name__}: {exc}")


def _build_live_place_fn(*, cancel_after: bool, maker_only: bool):
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
        requested_price = float(plan["limit_price"])
        order_price = requested_price
        best_bid = 0.0
        best_ask = 0.0
        if maker_only:
            book = client.get_order_book(str(plan["token_id"]))
            best_bid, best_ask = _best_bid_ask_from_book(book)
            order_price = _maker_only_price(
                side=side,
                requested_price=requested_price,
                best_bid=best_bid,
                best_ask=best_ask,
            )
            if order_price <= 0:
                raise RuntimeError(
                    "maker_only_no_resting_price "
                    f"side={side} requested={requested_price:.6f} "
                    f"best_bid={best_bid:.6f} best_ask={best_ask:.6f}"
                )
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
        result: Dict[str, Any] = {"place": response}
        result["clob_client"] = "py_clob_client_v2" if clob_v2 else "py_clob_client"
        result["maker_only"] = bool(maker_only)
        result["requested_price"] = requested_price
        result["posted_price"] = order_price
        result["best_bid"] = best_bid
        result["best_ask"] = best_ask
        if cancel_after:
            order_id = _extract_order_id(response)
            if order_id:
                if clob_v2:
                    result["cancel"] = client.cancel_order(OrderPayload(orderID=order_id))
                else:
                    result["cancel"] = client.cancel(order_id)
            else:
                result["cancel_error"] = "missing order id"
        return result

    return place


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute weather_edge_v1 trade plans to paper and optionally live CLOB.")
    parser.add_argument("--plans", default=str(DEFAULT_RUNTIME_ROOT / "plans" / "trade_plans.jsonl"))
    parser.add_argument("--paper-out", default=str(DEFAULT_RUNTIME_ROOT / "paper" / "orders.jsonl"))
    parser.add_argument("--live-out", default=str(DEFAULT_RUNTIME_ROOT / "live" / "orders.jsonl"))
    parser.add_argument("--live", action="store_true", help="Submit live orders for plans marked live_enabled=true.")
    parser.add_argument("--confirm-live", action="store_true", help="Required with --live.")
    parser.add_argument("--cancel-after", action="store_true", help="Cancel live orders immediately after placement.")
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
    live_place_fn = (
        _build_live_place_fn(cancel_after=bool(args.cancel_after), maker_only=not bool(args.allow_taker))
        if args.live
        else None
    )
    result = execute_trade_plans(
        plan_path=Path(args.plans),
        paper_out=Path(args.paper_out),
        live_out=Path(args.live_out),
        config=ExecutorConfig(
            live=bool(args.live),
            confirm_live=bool(args.confirm_live),
            cancel_after=bool(args.cancel_after),
        ),
        live_place_fn=live_place_fn,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if args.live and not args.no_telegram:
        _send_execution_telegram(result, live_out=Path(args.live_out))
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
