#!/usr/bin/env python3
"""Profile peer weather wallets with leaderboard PnL and recent public activity."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import requests

from research_external_wallet_strategy_v1 import DATA_API, _is_weather, _price_band


def get_rows(
    session: requests.Session,
    path: str,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    response = session.get(
        f"{DATA_API}{path}",
        params=params,
        headers={"Accept": "application/json", "User-Agent": "pm-agent-weather-peer-scan/1.0"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def leaderboard_row(
    session: requests.Session,
    wallet: str,
    period: str,
) -> dict[str, Any] | None:
    rows = get_rows(
        session,
        "/v1/leaderboard",
        {
            "category": "WEATHER",
            "timePeriod": period,
            "orderBy": "PNL",
            "user": wallet,
            "limit": 1,
        },
    )
    if not rows:
        return None
    row = rows[0]
    volume = float(row.get("vol") or 0)
    pnl = float(row.get("pnl") or 0)
    return {
        "rank": int(row["rank"]) if str(row.get("rank") or "").isdigit() else row.get("rank"),
        "name": row.get("userName") or "",
        "volume": round(volume, 6),
        "pnl": round(pnl, 6),
        "pnl_over_volume": round(pnl / volume, 6) if volume else None,
    }


def recent_activity(
    session: requests.Session,
    wallet: str,
    *,
    max_rows: int = 5_500,
) -> tuple[list[dict[str, Any]], int]:
    raw: list[dict[str, Any]] = []
    for offset in range(0, max_rows, 500):
        page = get_rows(
            session,
            "/activity",
            {
                "user": wallet,
                "limit": 500,
                "offset": offset,
                "sortDirection": "DESC",
            },
        )
        if not page:
            break
        raw.extend(page)
        if len(page) < 500:
            break
    return [row for row in raw if _is_weather(row)], len(raw)


def activity_profile(rows: list[dict[str, Any]], raw_rows: int) -> dict[str, Any]:
    trades = [row for row in rows if row.get("type") == "TRADE"]
    buys = [row for row in trades if row.get("side") == "BUY"]
    cost = sum(float(row.get("usdcSize") or 0) for row in buys)
    bands: dict[str, float] = defaultdict(float)
    outcomes: dict[str, float] = defaultdict(float)
    for row in buys:
        value = float(row.get("usdcSize") or 0)
        bands[_price_band(float(row.get("price") or 0))] += value
        outcomes[str(row.get("outcome") or "unknown")] += value
    events = {
        str(row.get("eventSlug") or row.get("slug") or "")
        for row in rows
        if row.get("eventSlug") or row.get("slug")
    }
    return {
        "raw_wallet_rows_examined": raw_rows,
        "weather_rows": len(rows),
        "weather_trade_rows": len(trades),
        "weather_events": len(events),
        "buy_cost_in_sample": round(cost, 6),
        "sell_trade_share": (
            round(sum(row.get("side") == "SELL" for row in trades) / len(trades), 6)
            if trades
            else None
        ),
        "buy_cost_share_by_price": {
            key: round(value / cost, 6) if cost else None
            for key, value in sorted(bands.items())
        },
        "buy_cost_share_by_outcome": {
            key: round(value / cost, 6) if cost else None
            for key, value in sorted(outcomes.items())
        },
        "caveat": (
            "latest-wallet-activity sample is truncated at 5,500 rows and filtered "
            "client-side because the API title parameter is not reliable"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    session = requests.Session()
    wallets = []
    for wallet_raw in args.wallet:
        wallet = wallet_raw.lower()
        weather_rows, raw_rows = recent_activity(session, wallet)
        wallets.append(
            {
                "wallet": wallet,
                "leaderboard": {
                    period.lower(): leaderboard_row(session, wallet, period)
                    for period in ("ALL", "MONTH", "WEEK")
                },
                "recent_activity": activity_profile(weather_rows, raw_rows),
            }
        )
        print(wallet, wallets[-1]["leaderboard"], flush=True)
    payload = {
        "snapshot_utc": datetime.now(timezone.utc).isoformat(),
        "wallets": wallets,
        "notes": [
            "Leaderboard PnL and volume are Polymarket WEATHER-category fields.",
            "PnL/volume is a comparison ratio, not return on capital.",
            "Recent activity is used only to classify execution style and 99-cent dependence.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
