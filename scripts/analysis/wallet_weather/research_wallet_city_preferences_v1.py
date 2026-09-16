#!/usr/bin/env python3
"""Measure external weather-wallet city concentration from public activity."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from research_external_wallet_strategy_v1 import (
    CITY_TIMEZONES,
    _city,
    _event_slug,
    _hour_band,
    _is_weather,
    _price_band,
    _target_date,
)
from research_wallet_event_portfolios_v1 import event_metadata, latest_activity


REGION = {
    **{
        city: "china_mainland"
        for city in (
            "beijing",
            "chengdu",
            "chongqing",
            "guangzhou",
            "qingdao",
            "shanghai",
            "shenzhen",
            "wuhan",
        )
    },
    "hong-kong": "hong_kong",
    "seoul": "korea",
    "busan": "korea",
    **{
        city: "united_states"
        for city in (
            "atlanta",
            "austin",
            "chicago",
            "dallas",
            "denver",
            "houston",
            "los-angeles",
            "miami",
            "nyc",
            "san-francisco",
            "seattle",
        )
    },
}


def ratio_map(values: dict[str, float]) -> dict[str, float]:
    total = sum(values.values())
    return {
        key: round(value / total, 6) if total else 0.0
        for key, value in sorted(values.items(), key=lambda item: item[1], reverse=True)
    }


def scan_wallet(wallet: str) -> dict[str, Any]:
    session = requests.Session()
    raw = latest_activity(session, wallet)
    weather = [row for row in raw if _is_weather(row)]
    trades = [row for row in weather if row.get("type") == "TRADE"]
    buys = [row for row in trades if row.get("side") == "BUY"]

    city_cost: dict[str, float] = defaultdict(float)
    city_rows: dict[str, int] = defaultdict(int)
    city_events: dict[str, set[str]] = defaultdict(set)
    city_dates: dict[str, set[str]] = defaultdict(set)
    city_latest_slug: dict[str, tuple[int, str]] = {}
    region_cost: dict[str, float] = defaultdict(float)
    price_cost: dict[str, float] = defaultdict(float)
    outcome_cost: dict[str, float] = defaultdict(float)
    relation_cost: dict[str, float] = defaultdict(float)
    target_hour_cost: dict[str, float] = defaultdict(float)

    for row in buys:
        slug = _event_slug(row)
        timestamp = int(row.get("timestamp") or 0)
        city = _city(slug)
        target = _target_date(slug, timestamp)
        value = float(row.get("usdcSize") or 0)
        city_cost[city] += value
        city_rows[city] += 1
        city_events[city].add(slug)
        if target:
            city_dates[city].add(target.isoformat())
        if slug and timestamp > city_latest_slug.get(city, (0, ""))[0]:
            city_latest_slug[city] = (timestamp, slug)
        region_cost[REGION.get(city, "other")] += value
        price_cost[_price_band(float(row.get("price") or 0))] += value
        outcome_cost[str(row.get("outcome") or "unknown")] += value

        if target:
            local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(
                ZoneInfo(CITY_TIMEZONES.get(city, "UTC"))
            )
            if local.date() < target:
                relation = "pre_target"
            elif local.date() == target:
                relation = "target_day"
                target_hour_cost[_hour_band(local.hour)] += value
            else:
                relation = "post_target"
            relation_cost[relation] += value

    shares = list(ratio_map(city_cost).values())
    hhi = sum(share * share for share in shares)
    city_rank = sorted(city_cost, key=city_cost.get, reverse=True)
    source_examples = []
    for city in city_rank[:5]:
        slug = city_latest_slug.get(city, (0, ""))[1]
        metadata = event_metadata(slug) if slug else None
        source_examples.append(
            {
                "city": city,
                "event_slug": slug,
                "resolution_source": metadata.get("resolutionSource") if metadata else None,
                "description": metadata.get("description") if metadata else None,
            }
        )

    city_rows_out = []
    total_cost = sum(city_cost.values())
    for city in city_rank:
        city_rows_out.append(
            {
                "city": city,
                "buy_cost": round(city_cost[city], 6),
                "buy_cost_share": round(city_cost[city] / total_cost, 6) if total_cost else 0,
                "buy_rows": city_rows[city],
                "events": len(city_events[city]),
                "target_dates": len(city_dates[city]),
            }
        )
    return {
        "wallet": wallet,
        "coverage": {
            "raw_wallet_rows": len(raw),
            "weather_rows": len(weather),
            "weather_trade_rows": len(trades),
            "weather_buy_rows": len(buys),
            "events": len({_event_slug(row) for row in weather if _event_slug(row)}),
            "target_dates": len(
                {
                    target.isoformat()
                    for row in weather
                    if (target := _target_date(_event_slug(row), int(row.get("timestamp") or 0)))
                }
            ),
            "sample_truncated": len(raw) >= 5_500,
        },
        "concentration": {
            "city_hhi": round(hhi, 6),
            "effective_city_count": round(1 / hhi, 6) if hhi else None,
            "top_1_city_cost_share": round(sum(shares[:1]), 6),
            "top_3_city_cost_share": round(sum(shares[:3]), 6),
            "region_cost_share": ratio_map(region_cost),
            "cities": city_rows_out,
        },
        "market_preference": {
            "price_cost_share": ratio_map(price_cost),
            "outcome_cost_share": ratio_map(outcome_cost),
            "target_relation_cost_share": ratio_map(relation_cost),
            "target_day_local_hour_cost_share": ratio_map(target_hour_cost),
            "sell_trade_share": (
                round(sum(row.get("side") == "SELL" for row in trades) / len(trades), 6)
                if trades
                else None
            ),
        },
        "source_examples": source_examples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    wallets = []
    for wallet in args.wallet:
        result = scan_wallet(wallet.lower())
        wallets.append(result)
        print(
            wallet,
            result["concentration"]["top_1_city_cost_share"],
            result["concentration"]["top_3_city_cost_share"],
            result["concentration"]["effective_city_count"],
            flush=True,
        )
    payload = {
        "snapshot_utc": datetime.now(timezone.utc).isoformat(),
        "grain": "latest public wallet activity, temperature BUY usdcSize weighted by city",
        "wallets": wallets,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
