from __future__ import annotations

import argparse
import json
import os
from typing import Any, Optional

from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    AssetType,
    BalanceAllowanceParams,
    OpenOrderParams,
    OrderArgs,
)
from py_clob_client.constants import POLYGON


load_dotenv()


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


def _print_json(label: str, payload: Any) -> None:
    print(f"[OK] {label}")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _print_err(label: str, exc: Exception) -> None:
    print(f"[ERR] {label}: {type(exc).__name__}: {exc}")


def _build_client(host: str, chain_id: int, private_key: str, use_existing_creds: bool) -> ClobClient:
    if use_existing_creds:
        api_key = os.getenv("CLOB_API_KEY", "").strip()
        api_secret = os.getenv("CLOB_SECRET", "").strip()
        api_pass = os.getenv("CLOB_PASS_PHRASE", "").strip()
        if not (api_key and api_secret and api_pass):
            raise RuntimeError("missing CLOB_API_KEY/CLOB_SECRET/CLOB_PASS_PHRASE for --use-existing-creds")
        creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass)
        return ClobClient(host, chain_id=chain_id, key=private_key, creds=creds)
    client = ClobClient(host, chain_id=chain_id, key=private_key)
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pmm_live_order_test",
        description="Official Polymarket CLOB test: balance -> orders -> place -> cancel.",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("CLOB_BASE_URL", "").strip()
        or os.getenv("PM_API_BASE_URL", "").strip()
        or "https://clob.polymarket.com",
        help="CLOB host URL",
    )
    parser.add_argument("--chain-id", type=int, default=POLYGON, help="Chain id, mainnet=137")
    parser.add_argument(
        "--signature-type",
        type=int,
        default=int(os.getenv("CLOB_SIGNATURE_TYPE", "-1")),
        help="CLOB signature type: 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE. -1 means auto (prefer 1 when funder!=signer).",
    )
    parser.add_argument(
        "--funder",
        default=os.getenv("PM_ADDRESS", "").strip(),
        help="Funding/proxy wallet address (usually the wallet shown on polymarket.com).",
    )
    parser.add_argument(
        "--private-key",
        default=os.getenv("POLYGON_WALLET_PRIVATE_KEY", "").strip() or os.getenv("PM", "").strip(),
        help="EOA private key (default: POLYGON_WALLET_PRIVATE_KEY -> PM)",
    )
    parser.add_argument("--token-id", default="", help="CLOB token id")
    parser.add_argument("--side", default="BUY", choices=["BUY", "SELL"], help="Order side")
    parser.add_argument("--price", type=float, default=0.10, help="Limit order price")
    parser.add_argument("--size", type=float, default=1.0, help="Limit order size")
    parser.add_argument("--place-live", action="store_true", help="Actually place one live limit order")
    parser.add_argument("--confirm-live", action="store_true", help="Required with --place-live")
    parser.add_argument("--cancel-after", action="store_true", help="Cancel created order after placement")
    parser.add_argument("--use-existing-creds", action="store_true", help="Use CLOB_API_KEY/CLOB_SECRET/CLOB_PASS_PHRASE")
    return parser


def _run(args: argparse.Namespace) -> int:
    print(f"[INFO] host={args.host}")
    if not args.private_key:
        print("[FATAL] missing private key: set POLYGON_WALLET_PRIVATE_KEY or PM")
        return 2
    if args.place_live and not args.confirm_live:
        print("[BLOCKED] --place-live requires --confirm-live")
        return 2
    if args.place_live and not args.token_id:
        print("[BLOCKED] --token-id is required when --place-live is set")
        return 2

    try:
        signer_addr = ClobClient(args.host, chain_id=int(args.chain_id), key=args.private_key).get_address()
    except Exception:
        signer_addr = ""
    sig_type = int(args.signature_type)
    funder = str(args.funder or "").strip()
    if sig_type < 0:
        if funder and signer_addr and funder.lower() != signer_addr.lower():
            sig_type = 1
        else:
            sig_type = 0
    print(f"[INFO] signer={signer_addr or 'unknown'}")
    print(f"[INFO] funder={funder or '(none)'} signature_type={sig_type}")

    try:
        client = _build_client(
            host=args.host,
            chain_id=int(args.chain_id),
            private_key=args.private_key,
            use_existing_creds=bool(args.use_existing_creds),
        )
        # Rebuild client with explicit signature_type/funder to match official docs.
        if bool(args.use_existing_creds):
            api_key = os.getenv("CLOB_API_KEY", "").strip()
            api_secret = os.getenv("CLOB_SECRET", "").strip()
            api_pass = os.getenv("CLOB_PASS_PHRASE", "").strip()
            creds = ApiCreds(api_key=api_key, api_secret=api_secret, api_passphrase=api_pass)
            client = ClobClient(
                args.host,
                chain_id=int(args.chain_id),
                key=args.private_key,
                creds=creds,
                signature_type=sig_type,
                funder=funder or None,
            )
        else:
            client = ClobClient(
                args.host,
                chain_id=int(args.chain_id),
                key=args.private_key,
                signature_type=sig_type,
                funder=funder or None,
            )
            client.set_api_creds(client.create_or_derive_api_creds())
    except Exception as exc:
        _print_err("init_client", exc)
        return 1

    try:
        try:
            balance = client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=sig_type)
            )
            _print_json("get_balance_allowance(collateral)", balance)
        except Exception as exc:
            _print_err("get_balance_allowance(collateral)", exc)

        if args.token_id:
            try:
                orders = client.get_orders(OpenOrderParams(asset_id=args.token_id))
                _print_json(f"get_orders(asset_id={args.token_id})", orders)
            except Exception as exc:
                _print_err("get_orders", exc)
            try:
                orderbook = client.get_order_book(args.token_id)
                _print_json(f"get_order_book(token_id={args.token_id})", orderbook)
            except Exception as exc:
                _print_err("get_order_book", exc)
        else:
            try:
                orders = client.get_orders()
                _print_json("get_orders(all)", orders)
            except Exception as exc:
                _print_err("get_orders(all)", exc)

        if not args.place_live:
            print("[SAFE] read-only test finished. no order placed.")
            return 0

        try:
            place_resp = client.create_and_post_order(
                OrderArgs(
                    token_id=args.token_id,
                    price=float(args.price),
                    size=float(args.size),
                    side=args.side,
                )
            )
            _print_json("create_and_post_order", place_resp)
        except Exception as exc:
            _print_err("create_and_post_order", exc)
            return 1

        order_id = _extract_order_id(place_resp)
        if not order_id:
            print("[WARN] cannot find order id in placement response.")
            return 1

        if args.cancel_after:
            try:
                cancel_resp = client.cancel(order_id)
                _print_json(f"cancel({order_id})", cancel_resp)
            except Exception as exc:
                _print_err(f"cancel({order_id})", exc)
                return 1
        return 0
    finally:
        try:
            if hasattr(client, "shutdown"):
                client.shutdown()
        except Exception:
            pass


def main() -> int:
    args = _build_parser().parse_args()
    try:
        return _run(args)
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
