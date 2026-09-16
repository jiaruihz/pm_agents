#!/usr/bin/env python3
"""Infer an external Polymarket wallet's weather trading style from public data.

This is a descriptive wallet audit, not a reconstruction of the wallet's
unobserved signal universe or PIT weather model. Cash PnL uses public activity
cashflows plus current inventory value, so taker fees reflected in ``usdcSize``
remain in the result.
"""
from __future__ import annotations

import argparse
import calendar
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

import requests


DATA_API = "https://data-api.polymarket.com"
WEATHER_TITLE = "highest temperature"
MONTHS = {name.lower(): idx for idx, name in enumerate(calendar.month_name) if name}
CITY_TIMEZONES = {
    "amsterdam": "Europe/Amsterdam",
    "ankara": "Europe/Istanbul",
    "atlanta": "America/New_York",
    "austin": "America/Chicago",
    "beijing": "Asia/Shanghai",
    "buenos-aires": "America/Argentina/Buenos_Aires",
    "busan": "Asia/Seoul",
    "cape-town": "Africa/Johannesburg",
    "chengdu": "Asia/Shanghai",
    "chicago": "America/Chicago",
    "chongqing": "Asia/Shanghai",
    "dallas": "America/Chicago",
    "denver": "America/Denver",
    "guangzhou": "Asia/Shanghai",
    "helsinki": "Europe/Helsinki",
    "hong-kong": "Asia/Hong_Kong",
    "houston": "America/Chicago",
    "istanbul": "Europe/Istanbul",
    "jakarta": "Asia/Jakarta",
    "jeddah": "Asia/Riyadh",
    "karachi": "Asia/Karachi",
    "kuala-lumpur": "Asia/Kuala_Lumpur",
    "la": "America/Los_Angeles",
    "lagos": "Africa/Lagos",
    "london": "Europe/London",
    "los-angeles": "America/Los_Angeles",
    "lucknow": "Asia/Kolkata",
    "madrid": "Europe/Madrid",
    "manila": "Asia/Manila",
    "mexico-city": "America/Mexico_City",
    "miami": "America/New_York",
    "milan": "Europe/Rome",
    "moscow": "Europe/Moscow",
    "munich": "Europe/Berlin",
    "nyc": "America/New_York",
    "panama-city": "America/Panama",
    "paris": "Europe/Paris",
    "qingdao": "Asia/Shanghai",
    "san-francisco": "America/Los_Angeles",
    "sao-paulo": "America/Sao_Paulo",
    "seattle": "America/Los_Angeles",
    "seoul": "Asia/Seoul",
    "shanghai": "Asia/Shanghai",
    "shenzhen": "Asia/Shanghai",
    "singapore": "Asia/Singapore",
    "taipei": "Asia/Taipei",
    "tel-aviv": "Asia/Jerusalem",
    "tokyo": "Asia/Tokyo",
    "toronto": "America/Toronto",
    "warsaw": "Europe/Warsaw",
    "wellington": "Pacific/Auckland",
    "wuhan": "Asia/Shanghai",
}


def _get(session: requests.Session, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    response = session.get(
        f"{DATA_API}{path}",
        params=params,
        headers={"Accept": "application/json", "User-Agent": "pm-agent-wallet-weather-research/1.0"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def _activity_window(
    session: requests.Session,
    wallet: str,
    start_ts: int,
    end_ts: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, 5_001, 500):
        page = _get(
            session,
            "/activity",
            {
                "user": wallet,
                "start": start_ts,
                "end": end_ts,
                "limit": 500,
                "offset": offset,
                "sortDirection": "ASC",
            },
        )
        if not page:
            break
        rows.extend(page)
        if len(page) < 500:
            break
    if len(rows) >= 5_500:
        if end_ts - start_ts <= 1:
            raise RuntimeError("activity exceeds the API offset budget inside one second")
        midpoint = (start_ts + end_ts) // 2
        return _activity_window(session, wallet, start_ts, midpoint) + _activity_window(
            session, wallet, midpoint, end_ts
        )
    return rows


def _paged(
    session: requests.Session,
    path: str,
    wallet: str,
    *,
    limit: int,
    max_offset: int,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, max_offset + 1, limit):
        page = _get(session, path, {"user": wallet, "limit": limit, "offset": offset, **(extra or {})})
        if not page:
            break
        rows.extend(page)
        if len(page) < limit:
            break
    return rows


def _activity_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("transactionHash"),
        row.get("type"),
        row.get("asset"),
        row.get("conditionId"),
        row.get("side"),
        row.get("size"),
        row.get("usdcSize"),
        row.get("timestamp"),
    )


def _trade_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("transactionHash"),
        row.get("asset"),
        row.get("side"),
        round(float(row.get("size") or 0), 8),
        round(float(row.get("price") or 0), 10),
        int(row.get("timestamp") or 0),
    )


def _is_weather(row: dict[str, Any]) -> bool:
    return WEATHER_TITLE in str(row.get("title") or "").lower()


def _event_slug(row: dict[str, Any]) -> str:
    return str(row.get("eventSlug") or row.get("slug") or "")


def _city(slug: str) -> str:
    match = re.match(r"highest-temperature-in-(.+?)-on-", slug.lower())
    return match.group(1) if match else "unknown"


def _target_date(slug: str, timestamp: int) -> date | None:
    match = re.search(r"-on-([a-z]+)-(\d{1,2})(?:-(\d{4}))?", slug.lower())
    if not match or match.group(1) not in MONTHS:
        return None
    year = int(match.group(3) or datetime.fromtimestamp(timestamp, timezone.utc).year)
    return date(year, MONTHS[match.group(1)], int(match.group(2)))


def _signed_cash(row: dict[str, Any]) -> float:
    activity_type = str(row.get("type") or "").upper()
    side = str(row.get("side") or "").upper()
    value = float(row.get("usdcSize") or 0)
    if activity_type == "TRADE":
        return -value if side == "BUY" else value if side == "SELL" else 0.0
    if activity_type in {
        "REDEEM",
        "MERGE",
        "MAKER_REBATE",
        "TAKER_REBATE",
        "REWARD",
        "REFERRAL_REWARD",
        "YIELD",
    }:
        return value
    if activity_type == "SPLIT":
        return -value
    return 0.0


def _price_band(price: float) -> str:
    if price <= 0.01:
        return "<=1c"
    if price <= 0.05:
        return "1-5c"
    if price <= 0.20:
        return "5-20c"
    if price < 0.80:
        return "20-80c"
    if price < 0.95:
        return "80-95c"
    return ">=95c"


def _hour_band(hour: int) -> str:
    if hour < 6:
        return "00-06"
    if hour < 10:
        return "06-10"
    if hour < 14:
        return "10-14"
    if hour < 18:
        return "14-18"
    return "18-24"


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def analyze(
    *,
    wallet: str,
    activity: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    trades_all: list[dict[str, Any]],
    trades_taker: list[dict[str, Any]],
    snapshot_utc: str,
) -> dict[str, Any]:
    activity = list({_activity_key(row): row for row in activity if _is_weather(row)}.values())
    positions = [row for row in positions if _is_weather(row)]
    trades_all = [row for row in trades_all if _is_weather(row)]
    trades_taker = [row for row in trades_taker if _is_weather(row)]

    events: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "cash_pnl": 0.0,
            "buy_cost": 0.0,
            "sell_proceeds": 0.0,
            "redeem_proceeds": 0.0,
            "city": "unknown",
            "target_date": "",
            "conditions": set(),
            "buy_outcomes": set(),
            "traded_outcomes": set(),
            "condition_outcomes": defaultdict(set),
            "has_sell": False,
            "buy_rows": 0,
            "sell_rows": 0,
            "buy_cost_by_day_relation": defaultdict(float),
        }
    )
    timing_rows = Counter()
    timing_cost = Counter()
    target_day_hour_rows = Counter()
    target_day_hour_cost = Counter()
    price_rows = Counter()
    price_cost = Counter()
    outcome_price_rows = Counter()
    outcome_price_cost = Counter()
    monthly = defaultdict(lambda: {"pnl": 0.0, "cost": 0.0, "events": 0})

    for row in activity:
        slug = _event_slug(row)
        event = events[slug]
        timestamp = int(row.get("timestamp") or 0)
        target = _target_date(slug, timestamp)
        city = _city(slug)
        event["city"] = city
        event["target_date"] = target.isoformat() if target else ""
        event["cash_pnl"] += _signed_cash(row)
        condition = str(row.get("conditionId") or "")
        if condition:
            event["conditions"].add(condition)
        outcome = str(row.get("outcome") or "")
        if outcome:
            event["traded_outcomes"].add(outcome)
            event["condition_outcomes"][condition].add(outcome)

        if row.get("type") == "TRADE" and row.get("side") == "BUY":
            value = float(row.get("usdcSize") or 0)
            event["buy_cost"] += value
            event["buy_rows"] += 1
            if outcome:
                event["buy_outcomes"].add(outcome)
            price = float(row.get("price") or 0)
            price_rows[_price_band(price)] += 1
            price_cost[_price_band(price)] += value
            outcome_price_rows[f"{outcome}|{_price_band(price)}"] += 1
            outcome_price_cost[f"{outcome}|{_price_band(price)}"] += value
            if target:
                timezone_name = CITY_TIMEZONES.get(city, "UTC")
                local = datetime.fromtimestamp(timestamp, timezone.utc).astimezone(ZoneInfo(timezone_name))
                day_delta = (local.date() - target).days
                relation = "pre_target" if day_delta < 0 else "target_day" if day_delta == 0 else "post_target"
                timing_rows[relation] += 1
                timing_cost[relation] += value
                event["buy_cost_by_day_relation"][relation] += value
                if relation == "target_day":
                    target_day_hour_rows[_hour_band(local.hour)] += 1
                    target_day_hour_cost[_hour_band(local.hour)] += value
        elif row.get("type") == "TRADE" and row.get("side") == "SELL":
            event["sell_rows"] += 1
            event["has_sell"] = True
            event["sell_proceeds"] += float(row.get("usdcSize") or 0)
        elif row.get("type") == "REDEEM":
            event["redeem_proceeds"] += float(row.get("usdcSize") or 0)
        elif row.get("type") == "SPLIT":
            event["buy_cost"] += float(row.get("usdcSize") or 0)

    for row in positions:
        slug = _event_slug(row)
        event = events[slug]
        event["cash_pnl"] += float(row.get("currentValue") or 0)
        event["city"] = _city(slug)
        target = _target_date(slug, int(datetime.now(timezone.utc).timestamp()))
        event["target_date"] = target.isoformat() if target else event["target_date"]

    position_units = defaultdict(
        lambda: {"cash_pnl": 0.0, "buy_cost": 0.0, "buy_shares": 0.0}
    )
    for row in activity:
        outcome = str(row.get("outcome") or "")
        if not outcome:
            continue
        unit = position_units[(str(row.get("conditionId") or ""), outcome)]
        unit["cash_pnl"] += _signed_cash(row)
        if row.get("type") == "TRADE" and row.get("side") == "BUY":
            unit["buy_cost"] += float(row.get("usdcSize") or 0)
            unit["buy_shares"] += float(row.get("size") or 0)
    for row in positions:
        outcome = str(row.get("outcome") or "")
        if outcome:
            position_units[(str(row.get("conditionId") or ""), outcome)][
                "cash_pnl"
            ] += float(row.get("currentValue") or 0)
    position_band = defaultdict(
        lambda: {"positions": 0, "wins": 0, "cost": 0.0, "pnl": 0.0}
    )
    for unit in position_units.values():
        if not unit["buy_cost"] or not unit["buy_shares"]:
            continue
        band = _price_band(unit["buy_cost"] / unit["buy_shares"])
        row = position_band[band]
        row["positions"] += 1
        row["wins"] += int(unit["cash_pnl"] > 0)
        row["cost"] += unit["buy_cost"]
        row["pnl"] += unit["cash_pnl"]

    mechanism = defaultdict(lambda: {"events": 0, "cost": 0.0, "pnl": 0.0})
    for event in events.values():
        buy_outcomes = event["buy_outcomes"]
        paired_condition = any(len(outcomes) >= 2 for outcomes in event["condition_outcomes"].values())
        outcome_tag = (
            "buy_yes_and_no"
            if {"Yes", "No"} <= buy_outcomes
            else "buy_yes_only"
            if buy_outcomes == {"Yes"}
            else "buy_no_only"
            if buy_outcomes == {"No"}
            else "no_classified_buys"
        )
        tags = [
            outcome_tag,
            "multi_bracket" if len(event["conditions"]) >= 3 else "one_or_two_brackets",
            "active_exit" if event["has_sell"] else "hold_or_redeem_only",
            "paired_condition_both_tokens" if paired_condition else "no_paired_condition",
        ]
        dominant_relation = max(event["buy_cost_by_day_relation"], key=event["buy_cost_by_day_relation"].get, default="unknown")
        tags.append(f"timing_{dominant_relation}")
        for tag in tags:
            mechanism[tag]["events"] += 1
            mechanism[tag]["cost"] += event["buy_cost"]
            mechanism[tag]["pnl"] += event["cash_pnl"]
        month = str(event["target_date"])[:7] or "unknown"
        monthly[month]["events"] += 1
        monthly[month]["cost"] += event["buy_cost"]
        monthly[month]["pnl"] += event["cash_pnl"]

    taker_counts = Counter(_trade_key(row) for row in trades_taker)
    taker_n = 0
    for row in trades_all:
        key = _trade_key(row)
        if taker_counts[key] > 0:
            taker_n += 1
            taker_counts[key] -= 1

    def packed(counter: Counter, costs: Counter | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {"rows": dict(counter)}
        if costs is not None:
            total = sum(costs.values())
            result["cost"] = {key: round(value, 6) for key, value in costs.items()}
            result["cost_share"] = {
                key: round(value / total, 6) if total else 0.0 for key, value in costs.items()
            }
        return result

    mechanism_rows = {}
    for key, value in sorted(mechanism.items()):
        mechanism_rows[key] = {
            "events": value["events"],
            "cost": round(value["cost"], 6),
            "pnl": round(value["pnl"], 6),
            "roi": round(_ratio(value["pnl"], value["cost"]) or 0.0, 6),
        }

    return {
        "wallet": wallet,
        "snapshot_utc": snapshot_utc,
        "coverage": {
            "activity_rows": len(activity),
            "trade_rows": len(trades_all),
            "position_rows": len(positions),
            "events": len(events),
            "target_dates": len({event["target_date"] for event in events.values() if event["target_date"]}),
        },
        "cashflow": {
            "buy_cost": round(sum(event["buy_cost"] for event in events.values()), 6),
            "sell_proceeds": round(sum(event["sell_proceeds"] for event in events.values()), 6),
            "redeem_proceeds": round(sum(event["redeem_proceeds"] for event in events.values()), 6),
            "inventory_value": round(sum(float(row.get("currentValue") or 0) for row in positions), 6),
            "pnl": round(sum(event["cash_pnl"] for event in events.values()), 6),
        },
        "execution": {
            "all_trade_rows": len(trades_all),
            "taker_trade_rows": taker_n,
            "maker_inferred_trade_rows": len(trades_all) - taker_n,
            "maker_inferred_share": round(_ratio(len(trades_all) - taker_n, len(trades_all)) or 0.0, 6),
            "activity_type_side": {
                str(key): value
                for key, value in Counter(
                    (str(row.get("type") or ""), str(row.get("side") or "")) for row in activity
                ).items()
            },
        },
        "entry_price": packed(price_rows, price_cost),
        "entry_price_by_outcome": packed(outcome_price_rows, outcome_price_cost),
        "position_avg_entry_band": {
            band: {
                "positions": value["positions"],
                "cost": round(value["cost"], 6),
                "pnl": round(value["pnl"], 6),
                "roi": round(_ratio(value["pnl"], value["cost"]) or 0.0, 6),
                "win_rate": round(_ratio(value["wins"], value["positions"]) or 0.0, 6),
            }
            for band, value in sorted(position_band.items())
        },
        "entry_timing": {
            "target_relation": packed(timing_rows, timing_cost),
            "target_day_local_hour": packed(target_day_hour_rows, target_day_hour_cost),
        },
        "mechanism_slices": mechanism_rows,
        "monthly": {
            month: {
                "events": value["events"],
                "cost": round(value["cost"], 6),
                "pnl": round(value["pnl"], 6),
                "roi": round(_ratio(value["pnl"], value["cost"]) or 0.0, 6),
            }
            for month, value in sorted(monthly.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--start", default="2025-11-01", help="inclusive UTC date")
    parser.add_argument("--end", default="", help="exclusive UTC date; defaults to now")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = (
        datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
        if args.end
        else datetime.now(timezone.utc)
    )
    session = requests.Session()
    activity = _activity_window(session, args.wallet.lower(), int(start.timestamp()), int(end.timestamp()) + 1)
    positions = _paged(
        session,
        "/positions",
        args.wallet.lower(),
        limit=500,
        max_offset=10_000,
        extra={"sizeThreshold": 0, "sortBy": "TOKENS", "sortDirection": "DESC"},
    )
    trades_all = _paged(
        session,
        "/trades",
        args.wallet.lower(),
        limit=10_000,
        max_offset=10_000,
        extra={"takerOnly": "false"},
    )
    trades_taker = _paged(
        session,
        "/trades",
        args.wallet.lower(),
        limit=10_000,
        max_offset=10_000,
        extra={"takerOnly": "true"},
    )
    payload = analyze(
        wallet=args.wallet.lower(),
        activity=activity,
        positions=positions,
        trades_all=trades_all,
        trades_taker=trades_taker,
        snapshot_utc=end.isoformat(),
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
