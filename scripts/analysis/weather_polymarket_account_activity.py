#!/usr/bin/env python3
"""Summarize public Polymarket account activity for cashflow reconciliation."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA_API_BASE = "https://data-api.polymarket.com"
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


def _ts_to_utc(row: dict[str, Any]) -> dt.datetime | None:
    value = row.get("timestamp") or row.get("createdAt") or row.get("created_at")
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value = value / 1000.0
        return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _money(row: dict[str, Any]) -> float:
    for key in ["usdcSize", "amount", "value", "cashAmount", "tradeAmount"]:
        try:
            value = float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value:
            return value
    try:
        return float(row.get("size") or 0.0) * float(row.get("price") or row.get("avgPrice") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _signed_cash(row: dict[str, Any]) -> float:
    event = str(row.get("type") or row.get("eventType") or "").upper()
    side = str(row.get("side") or "").upper()
    value = _money(row)
    if event == "TRADE" and side == "BUY":
        return -value
    if event == "TRADE" and side == "SELL":
        return value
    if event in {"REDEEM", "MAKER_REBATE"}:
        return value
    return 0.0


def _market_date(row: dict[str, Any]) -> str:
    text = " ".join(
        [
            str(row.get("slug") or ""),
            str(row.get("eventSlug") or ""),
            str(row.get("title") or ""),
        ]
    ).lower()
    match = re.search(
        r"(january|february|march|april|may|june|july|august|september|october|november|december)[- ](\d{1,2})[-, ]+(\d{4})",
        text,
    )
    if not match:
        return ""
    return f"{match.group(3)}-{MONTHS[match.group(1)]}-{int(match.group(2)):02d}"


def iter_activity(wallet: str, *, max_rows: int) -> list[dict[str, Any]]:
    session = requests.Session()
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < max_rows:
        limit = min(500, max_rows - len(rows))
        resp = session.get(
            f"{DATA_API_BASE}/activity",
            params={"user": wallet, "limit": limit, "offset": offset},
            headers={"Accept": "application/json", "User-Agent": "pm-agent-research/1.0"},
            timeout=30,
        )
        resp.raise_for_status()
        page = resp.json()
        if not isinstance(page, list) or not page:
            break
        rows.extend([row for row in page if isinstance(row, dict)])
        if len(page) < limit:
            break
        offset += len(page)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="inclusive UTC date, e.g. 2026-05-31")
    parser.add_argument("--end", default=None, help="exclusive UTC date; defaults to now")
    parser.add_argument("--max-rows", type=int, default=5000)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    wallet = os.getenv("PM_ADDRESS", "").strip()
    if not wallet:
        raise SystemExit("missing PM_ADDRESS")

    start = dt.datetime.fromisoformat(args.start).replace(tzinfo=dt.timezone.utc)
    end = (
        dt.datetime.fromisoformat(args.end).replace(tzinfo=dt.timezone.utc)
        if args.end
        else dt.datetime.now(dt.timezone.utc)
    )
    all_rows = iter_activity(wallet, max_rows=args.max_rows)
    recent = []
    for row in all_rows:
        ts = _ts_to_utc(row)
        if ts is not None and start <= ts < end:
            item = dict(row)
            item["_ts_utc"] = ts.isoformat()
            item["_money"] = round(_money(row), 6)
            item["_signed_cash"] = round(_signed_cash(row), 6)
            recent.append(item)

    by_type_side: dict[str, dict[str, Any]] = defaultdict(lambda: {"rows": 0, "money": 0.0, "signed_cash": 0.0})
    by_date: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_market_date: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in recent:
        key = f"{str(row.get('type') or row.get('eventType') or '').upper()}:{str(row.get('side') or '').upper()}"
        by_type_side[key]["rows"] += 1
        by_type_side[key]["money"] += float(row["_money"])
        by_type_side[key]["signed_cash"] += float(row["_signed_cash"])
        day = str(row["_ts_utc"])[:10]
        by_date[day][key] += float(row["_money"])
        by_date[day]["signed_cash"] += float(row["_signed_cash"])
        market_date = _market_date(row)
        if market_date:
            by_market_date[market_date][key] += float(row["_money"])
            by_market_date[market_date]["signed_cash"] += float(row["_signed_cash"])

    payload = {
        "window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        "rows_all_fetched": len(all_rows),
        "rows_in_window": len(recent),
        "by_type_side": {
            key: {
                "rows": value["rows"],
                "money": round(value["money"], 6),
                "signed_cash": round(value["signed_cash"], 6),
            }
            for key, value in sorted(by_type_side.items())
        },
        "signed_cash_delta_visible": round(sum(float(r["_signed_cash"]) for r in recent), 6),
        "by_date": {
            day: {key: round(value, 6) for key, value in sorted(items.items())}
            for day, items in sorted(by_date.items())
        },
        "by_market_date": {
            day: {key: round(value, 6) for key, value in sorted(items.items())}
            for day, items in sorted(by_market_date.items())
        },
        "sample_keys": sorted(all_rows[0].keys()) if all_rows else [],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
