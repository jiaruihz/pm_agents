#!/usr/bin/env python3
"""Fetch current Polymarket positions and split weather vs non-weather.

Read-only helper for account reconciliation. It uses the public data API with
PM_ADDRESS from .env and does not touch private keys or submit orders.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.clients.polymarket_data import PolymarketDataClient

MONTHS = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}


def _to_float(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except Exception:
        return 0.0


def _asset_id(row: dict[str, Any]) -> str:
    return str(row.get("asset") or row.get("assetId") or row.get("token_id") or "").strip()


def _load_weather_tokens(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT token_id FROM fact_trades "
                "WHERE trade_class='live_real' AND token_id IS NOT NULL"
            )
            if row[0]
        }
    finally:
        conn.close()


def _is_weather(row: dict[str, Any], weather_tokens: set[str]) -> bool:
    token = _asset_id(row)
    if token and token in weather_tokens:
        return True
    text = " ".join([str(row.get("title") or ""), str(row.get("slug") or "")]).lower()
    return "highest temperature" in text or "temperature in" in text


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "positions": len(rows),
        "size": round(sum(_to_float(r, "size") for r in rows), 6),
        "current_value": round(sum(_to_float(r, "currentValue") for r in rows), 6),
        "cash_pnl": round(sum(_to_float(r, "cashPnl") for r in rows), 6),
        "initial_value": round(sum(_to_float(r, "initialValue") for r in rows), 6),
        "realized_pnl": round(sum(_to_float(r, "realizedPnl") for r in rows), 6),
    }


def _position_view(row: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "title",
        "slug",
        "outcome",
        "size",
        "avgPrice",
        "curPrice",
        "currentValue",
        "cashPnl",
        "percentPnl",
        "initialValue",
        "realizedPnl",
        "asset",
    ]
    return {k: row.get(k) for k in keys}


def _market_date(row: dict[str, Any]) -> str:
    text = " ".join([str(row.get("slug") or ""), str(row.get("title") or "")]).lower()
    match = re.search(
        r"(january|february|march|april|may|june|july|august|september|october|november|december)[- ](\d{1,2})[-, ]+(\d{4})",
        text,
    )
    if not match:
        return ""
    month = MONTHS[match.group(1)]
    day = int(match.group(2))
    year = match.group(3)
    return f"{year}-{month}-{day:02d}"


def _group_by_market_date(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_market_date(row) or "unknown", []).append(row)
    out = []
    for market_date, items in grouped.items():
        summary = _summarize(items)
        summary["market_date"] = market_date
        out.append(summary)
    return sorted(out, key=lambda r: str(r["market_date"]))


def _closed_summary(rows: list[dict[str, Any]], weather_tokens: set[str]) -> dict[str, Any]:
    weather = [row for row in rows if _is_weather(row, weather_tokens)]
    other = [row for row in rows if not _is_weather(row, weather_tokens)]
    return {
        "all": _summarize(rows),
        "weather": _summarize(weather),
        "other": _summarize(other),
        "weather_by_market_date": _group_by_market_date(weather),
        "top_weather_by_negative_cash_pnl": [
            _position_view(row) for row in sorted(weather, key=lambda r: _to_float(r, "cashPnl"))[:20]
        ],
    }


def main() -> int:
    load_dotenv(ROOT / ".env")
    wallet = os.getenv("PM_ADDRESS", "").strip()
    if not wallet:
        raise SystemExit("missing PM_ADDRESS")

    weather_tokens = _load_weather_tokens(ROOT / "runtime" / "weather.db")
    client = PolymarketDataClient()
    rows: list[dict[str, Any]] = []
    for page in client.iter_user_positions(user=wallet, page_size=200, max_rows=5000):
        rows.extend(page)
    closed_rows: list[dict[str, Any]] = []
    for page in client.iter_user_closed_positions(
        user=wallet,
        page_size=100,
        max_rows=5000,
        sort_by="endDate",
        sort_direction="desc",
    ):
        closed_rows.extend(page)

    weather = [row for row in rows if _is_weather(row, weather_tokens)]
    other = [row for row in rows if not _is_weather(row, weather_tokens)]
    payload = {
        "wallet": wallet,
        "all": _summarize(rows),
        "weather": _summarize(weather),
        "other": _summarize(other),
        "closed": _closed_summary(closed_rows, weather_tokens),
        "sample_keys": sorted(rows[0].keys()) if rows else [],
        "closed_sample_keys": sorted(closed_rows[0].keys()) if closed_rows else [],
        "weather_by_market_date": _group_by_market_date(weather),
        "top_weather_by_negative_cash_pnl": [
            _position_view(row) for row in sorted(weather, key=lambda r: _to_float(r, "cashPnl"))[:25]
        ],
        "top_other_by_negative_cash_pnl": [
            _position_view(row) for row in sorted(other, key=lambda r: _to_float(r, "cashPnl"))[:10]
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
