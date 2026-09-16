"""Backfill settlement data for CLOB fills whose condition_ids have no
settlement record in the DB.

Queries https://clob.polymarket.com/markets/{condition_id} for each
unresolved fill, extracts YES-token final price, and inserts into the
settlements table.

Idempotent — uses INSERT OR IGNORE so re-running is safe.

Run:
    python -m weather_dashboard.ingest.backfill_missing_settlements \
        --db-path runtime/weather.db [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import time
from typing import Any

import requests

CLOB_BASE = "https://clob.polymarket.com"
REQUEST_DELAY = 0.3   # seconds between API calls — be polite


def _make_settlement_id(target_date: str, condition_id: str, bracket: str) -> str:
    raw = f"clob_backfill|{target_date}|{condition_id}|{bracket}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _fetch_clob_market(condition_id: str) -> dict[str, Any] | None:
    """GET /markets/{condition_id} from Polymarket CLOB API."""
    try:
        r = requests.get(
            f"{CLOB_BASE}/markets/{condition_id}",
            timeout=15,
            headers={"Accept": "application/json"},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [warn] API error for {condition_id[:16]}…: {e}")
        return None


def _yes_price(market: dict) -> float | None:
    """Extract YES token final price from CLOB market response."""
    tokens = market.get("tokens") or []
    for tok in tokens:
        if str(tok.get("outcome", "")).lower() == "yes":
            price = tok.get("price")
            if price is not None:
                return float(price)
    return None


def _unresolved_fills(conn: sqlite3.Connection) -> list[dict]:
    """Return CLOB fills with no matching settlement by condition_id."""
    rows = conn.execute("""
        SELECT DISTINCT
            sig.condition_id,
            sig.target_date,
            sig.bracket,
            sig.market_id,
            sig.city
        FROM fills f
        JOIN orders o    ON f.execution_id = o.execution_id
        JOIN plans p     ON o.plan_id      = p.plan_id
        JOIN signals sig ON p.signal_id    = sig.signal_id
        LEFT JOIN settlements s
               ON s.condition_id = sig.condition_id
              AND s.target_date  = sig.target_date
              AND s.bracket      = sig.bracket
        WHERE o.venue    = 'polymarket_clob'
          AND f.status   = 'filled'
          AND sig.condition_id IS NOT NULL
          AND sig.condition_id != ''
          AND s.settlement_id IS NULL
        ORDER BY sig.target_date, sig.city
    """).fetchall()
    return [dict(r) for r in rows]


def backfill(conn: sqlite3.Connection, *, dry_run: bool = False) -> dict:
    conn.row_factory = sqlite3.Row
    unresolved = _unresolved_fills(conn)

    stats = {
        "unresolved": len(unresolved),
        "api_calls": 0,
        "not_closed": 0,
        "inserted": 0,
        "no_data": 0,
        "dry_run": dry_run,
    }

    print(f"Found {len(unresolved)} unresolved CLOB fills. Querying Polymarket API…\n")

    for row in unresolved:
        cid = row["condition_id"]
        date = row["target_date"]
        bracket = row["bracket"]
        city = row["city"]

        print(f"  {date} {city} bracket={bracket} → {cid[:20]}…", end=" ", flush=True)
        market = _fetch_clob_market(cid)
        stats["api_calls"] += 1
        time.sleep(REQUEST_DELAY)

        if market is None:
            print("not found")
            stats["no_data"] += 1
            continue

        if not market.get("closed"):
            print("still open")
            stats["not_closed"] += 1
            continue

        yes_price = _yes_price(market)
        if yes_price is None:
            print("no YES token price")
            stats["no_data"] += 1
            continue

        status = "settled" if yes_price in (0.0, 1.0) else "partial"
        sid = _make_settlement_id(date, cid, bracket)
        market_id = row.get("market_id")

        print(f"final_price={yes_price} ({status})")

        if not dry_run:
            conn.execute(
                """INSERT OR IGNORE INTO settlements
                   (settlement_id, target_date, condition_id, market_id,
                    bracket, token_id, final_price, settlement_status)
                   VALUES (?, ?, ?, ?, ?, NULL, ?, ?)""",
                (sid, date, cid, market_id, bracket, yes_price, status),
            )
            stats["inserted"] += 1

    if not dry_run:
        conn.commit()

    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default="runtime/weather.db")
    ap.add_argument("--dry-run", action="store_true",
                    help="Query API but do not write to DB")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    try:
        import json
        out = backfill(conn, dry_run=args.dry_run)
        print("\n" + json.dumps(out, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
