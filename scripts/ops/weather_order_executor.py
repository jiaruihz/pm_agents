#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

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


def _build_live_place_fn(*, cancel_after: bool):
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds, OrderArgs
    from py_clob_client.constants import POLYGON

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
        client.set_api_creds(client.create_or_derive_api_creds())

    def place(plan: Dict[str, Any]) -> Dict[str, Any]:
        response = client.create_and_post_order(
            OrderArgs(
                token_id=str(plan["token_id"]),
                price=float(plan["limit_price"]),
                size=float(plan["size"]),
                side=str(plan.get("order_side") or "BUY"),
            )
        )
        result: Dict[str, Any] = {"place": response}
        if cancel_after:
            order_id = _extract_order_id(response)
            if order_id:
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
    return parser


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ModuleNotFoundError:
        pass
    args = _parser().parse_args()
    live_place_fn = _build_live_place_fn(cancel_after=bool(args.cancel_after)) if args.live else None
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
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
