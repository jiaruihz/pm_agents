from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Optional


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.platform.market_data.http_client import ToolServiceClient


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pmm_live_order_test",
        description="Test PMM tool-service connectivity and optionally place one live limit order.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("PM_API_BASE_URL", "http://localhost:8000"),
        help="Tool service base URL (default from PM_API_BASE_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("PM_API_KEY", ""),
        help="Tool service API key (default from PM_API_KEY)",
    )
    parser.add_argument("--token-id", default="", help="CLOB token id for orderbook/position and live order")
    parser.add_argument("--side", default="BUY", choices=["BUY", "SELL"], help="Order side")
    parser.add_argument("--price", type=float, default=0.10, help="Limit order price")
    parser.add_argument("--size", type=float, default=1.0, help="Limit order size")
    parser.add_argument(
        "--place-live",
        action="store_true",
        help="Actually place one live limit order. Default mode is read-only checks.",
    )
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required together with --place-live as a safety confirmation.",
    )
    parser.add_argument(
        "--cancel-after",
        action="store_true",
        help="Attempt to cancel the created order right after placement.",
    )
    parser.add_argument(
        "--wait-before-cancel",
        type=float,
        default=0.2,
        help="Seconds to wait before cancel when --cancel-after is set",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    print(f"[INFO] base_url={args.base_url}")
    print("[INFO] starting connectivity checks...")
    async with ToolServiceClient(args.base_url, args.api_key) as client:
        balance = await client.retry(client.get_balance)
        orders = await client.retry(client.get_orders)
        print("[OK] get_balance")
        print(json.dumps(balance, ensure_ascii=False, indent=2))
        print("[OK] get_orders")
        print(json.dumps(orders, ensure_ascii=False, indent=2))

        if args.token_id:
            positions = await client.retry(client.get_positions, [args.token_id])
            orderbook = await client.retry(client.get_orderbook, args.token_id)
            print(f"[OK] get_positions token_id={args.token_id}")
            print(json.dumps(positions, ensure_ascii=False, indent=2))
            print(f"[OK] get_orderbook token_id={args.token_id}")
            print(json.dumps(orderbook, ensure_ascii=False, indent=2))

        if not args.place_live:
            print("[SAFE] read-only test finished. no order placed.")
            return 0

        if not args.confirm_live:
            print("[BLOCKED] --place-live requires --confirm-live")
            return 2
        if not args.token_id:
            print("[BLOCKED] --token-id is required when --place-live is set")
            return 2

        print(
            "[LIVE] placing order: "
            f"token_id={args.token_id} side={args.side} price={args.price} size={args.size}"
        )
        place_resp = await client.retry(
            client.place_limit_order,
            args.token_id,
            float(args.price),
            float(args.size),
            args.side,
        )
        print("[LIVE] place_limit_order response:")
        print(json.dumps(place_resp, ensure_ascii=False, indent=2))

        order_id = _extract_order_id(place_resp)
        if args.cancel_after and order_id:
            if args.wait_before_cancel > 0:
                await asyncio.sleep(args.wait_before_cancel)
            cancel_resp = await client.retry(client.cancel_order, order_id)
            print(f"[LIVE] cancel_order response order_id={order_id}:")
            print(json.dumps(cancel_resp, ensure_ascii=False, indent=2))
        elif args.cancel_after:
            print("[WARN] --cancel-after enabled but order id not found in response.")
        return 0


def main() -> int:
    args = _build_parser().parse_args()
    try:
        return asyncio.run(_run(args))
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
