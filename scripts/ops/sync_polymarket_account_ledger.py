#!/usr/bin/env python3
"""Synchronize one registered Polymarket account into the local account ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_dashboard.ingest.polymarket_account_ledger import (  # noqa: E402
    PolymarketAccountSource,
    connect_ledger,
    load_account_registry,
    register_accounts,
    sync_account,
    utc_now,
)


def keychain_secret(account: str, service: str) -> str:
    process = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            account,
            "-s",
            service,
            "-w",
        ],
        text=True,
        capture_output=True,
    )
    secret = process.stdout.strip()
    if process.returncode != 0 or not secret:
        raise RuntimeError(
            f"Keychain item unavailable for account={account!r}, service={service!r}"
        )
    return secret


def default_proxy() -> str | None:
    values = dotenv_values(ROOT / ".env")
    for key in (
        "WEATHER_PREDICT_MARKET_PROXY",
        "WEATHER_DATA_FEED_MARKET_PROXY",
        "HTTPS_PROXY",
    ):
        value = str(values.get(key) or "").strip()
        if value:
            return value
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Persist public Polymarket activity, position snapshots, and "
            "Relayer transaction states for one labeled account."
        )
    )
    parser.add_argument("--account", required=True, help="account_id from the registry")
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config/polymarket_accounts.json",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=ROOT / "runtime/db/polymarket_account_ledger.db",
    )
    parser.add_argument(
        "--full-backfill",
        action="store_true",
        help="start from the earliest public activity even when the account was synced before",
    )
    parser.add_argument("--start-ts", type=int, default=None)
    parser.add_argument(
        "--skip-relayer",
        action="store_true",
        help="skip Relayer GET even if the registry enables it",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="HTTP(S) proxy; defaults to the project market-proxy setting",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    accounts = load_account_registry(args.config)
    if args.account not in accounts:
        raise SystemExit(f"unknown account_id: {args.account}")
    account = accounts[args.account]
    if args.skip_relayer and account.sync_relayer:
        account = type(account)(
            **{**account.__dict__, "sync_relayer": False}
        )

    conn = connect_ledger(args.db_path)
    try:
        register_accounts(conn, accounts.values(), observed_at=utc_now())
        result = sync_account(
            conn=conn,
            account=account,
            source=PolymarketAccountSource(proxy=args.proxy or default_proxy()),
            relayer_key_loader=keychain_secret,
            full_backfill=args.full_backfill,
            requested_start_ts=args.start_ts,
        )
        result["db_path"] = str(args.db_path.resolve())
        result["registered_accounts"] = [
            {
                "account_id": row.account_id,
                "account_kind": row.account_kind,
                "proxy_wallet": row.proxy_wallet,
            }
            for row in accounts.values()
        ]
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
