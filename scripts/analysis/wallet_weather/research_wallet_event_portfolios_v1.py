#!/usr/bin/env python3
"""Reconstruct external weather wallets at the city-target-date portfolio grain.

Each temperature event is treated as one mutually exclusive ladder.  The
script combines every traded bracket and both YES/NO tokens before describing
the wallet's economic expression, avoiding single-leg attribution errors.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any

import requests

from research_external_wallet_strategy_v1 import (
    DATA_API,
    _city,
    _event_slug,
    _get,
    _hour_band,
    _is_weather,
    _price_band,
    _target_date,
    CITY_TIMEZONES,
)


GAMMA_API = "https://gamma-api.polymarket.com"


def latest_activity(
    session: requests.Session,
    wallet: str,
    *,
    max_rows: int = 5_500,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, max_rows, 500):
        page = _get(
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
        rows.extend(page)
        if len(page) < 500:
            break
    return rows


def recent_closed_positions(
    wallet: str,
    *,
    max_rows: int = 3_000,
) -> list[dict[str, Any]]:
    """Fetch recent closed token summaries in parallel; never use their PnL."""

    def page(offset: int) -> list[dict[str, Any]]:
        params = {
            "user": wallet,
            "title": "highest temperature",
            "limit": 50,
            "offset": offset,
        }
        for attempt in range(6):
            try:
                response = requests.get(
                    f"{DATA_API}/closed-positions",
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "pm-agent-wallet-event-portfolio/1.0",
                    },
                    timeout=30,
                )
                if response.status_code == 200:
                    payload = response.json()
                    return (
                        [row for row in payload if isinstance(row, dict)]
                        if isinstance(payload, list)
                        else []
                    )
                if response.status_code not in {403, 429, 500, 502, 503, 504}:
                    response.raise_for_status()
            except requests.RequestException:
                pass
            time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"closed-position page exhausted retries: {wallet} offset={offset}")

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(page, offset): offset for offset in range(0, max_rows, 50)
        }
        pages: dict[int, list[dict[str, Any]]] = {}
        for future in as_completed(futures):
            pages[futures[future]] = future.result()
    for offset in sorted(pages):
        rows.extend(pages[offset])
    return [row for row in rows if _is_weather(row)]


def event_metadata(slug: str) -> dict[str, Any] | None:
    for attempt in range(6):
        try:
            response = requests.get(
                f"{GAMMA_API}/events",
                params={"slug": slug},
                headers={
                    "Accept": "application/json",
                    "User-Agent": "pm-agent-wallet-event-portfolio/1.0",
                },
                timeout=30,
            )
            if response.status_code == 200:
                payload = response.json()
                return payload[0] if isinstance(payload, list) and payload else None
            if response.status_code not in {403, 429, 500, 502, 503, 504}:
                response.raise_for_status()
        except requests.RequestException:
            if attempt == 5:
                return None
        time.sleep(0.5 * (attempt + 1))
    return None


def event_market_activity(
    wallet: str,
    metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    conditions = [
        str(row.get("conditionId"))
        for row in metadata.get("markets") or []
        if isinstance(row, dict) and row.get("conditionId")
    ]
    if not conditions:
        return []

    def fetch(condition_batch: list[str]) -> list[dict[str, Any]]:
        session = requests.Session()
        rows: list[dict[str, Any]] = []
        for offset in range(0, 5_001, 500):
            params = {
                    "user": wallet,
                    "market": ",".join(condition_batch),
                    "limit": 500,
                    "offset": offset,
                    "sortDirection": "ASC",
                }
            page: list[dict[str, Any]] | None = None
            for attempt in range(6):
                try:
                    response = session.get(
                        f"{DATA_API}/activity",
                        params=params,
                        headers={
                            "Accept": "application/json",
                            "User-Agent": "pm-agent-wallet-event-portfolio/1.0",
                        },
                        timeout=30,
                    )
                except requests.RequestException:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                if response.status_code == 200:
                    payload = response.json()
                    page = (
                        [row for row in payload if isinstance(row, dict)]
                        if isinstance(payload, list)
                        else []
                    )
                    break
                if response.status_code not in {403, 429, 500, 502, 503, 504}:
                    response.raise_for_status()
                time.sleep(0.5 * (attempt + 1))
            if page is None:
                raise RuntimeError(
                    f"activity request exhausted retries: wallet={wallet} offset={offset}"
                )
            if not page:
                break
            rows.extend(page)
            if len(page) < 500:
                break
        if len(rows) >= 5_500:
            if len(condition_batch) == 1:
                raise RuntimeError(
                    f"single condition exceeds activity offset cap: {condition_batch[0]}"
                )
            midpoint = len(condition_batch) // 2
            return fetch(condition_batch[:midpoint]) + fetch(condition_batch[midpoint:])
        return rows

    rows: list[dict[str, Any]] = []
    for start in range(0, len(conditions), 3):
        rows.extend(fetch(conditions[start : start + 3]))
    return rows


def bracket_label(market: dict[str, Any]) -> str:
    return str(
        market.get("groupItemTitle")
        or market.get("question")
        or market.get("conditionId")
        or ""
    )


def coefficient_of_variation(values: list[float]) -> float | None:
    positive = [value for value in values if value > 1e-8]
    if len(positive) < 2:
        return None
    mean = statistics.fmean(positive)
    return statistics.pstdev(positive) / mean if mean else None


def target_date_near_timestamp(slug: str, timestamp: int):
    target = _target_date(slug, timestamp)
    if not target or not timestamp:
        return target
    observed_date = datetime.fromtimestamp(timestamp, timezone.utc).date()
    delta = (target - observed_date).days
    if delta > 180:
        return target.replace(year=target.year - 1)
    if delta < -300:
        return target.replace(year=target.year + 1)
    return target


def event_portfolio(
    slug: str,
    rows: list[dict[str, Any]],
    metadata: dict[str, Any],
    *,
    sample_oldest_ts: int,
) -> dict[str, Any]:
    markets = [
        row
        for row in metadata.get("markets") or []
        if isinstance(row, dict) and row.get("conditionId")
    ]
    conditions = [str(row["conditionId"]) for row in markets]
    labels = {str(row["conditionId"]): bracket_label(row) for row in markets}
    trades = [row for row in rows if row.get("type") == "TRADE"]
    buys = [row for row in trades if row.get("side") == "BUY"]
    sells = [row for row in trades if row.get("side") == "SELL"]
    timestamps = [int(row.get("timestamp") or 0) for row in trades]

    book: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(
            lambda: {
                "buy_shares": 0.0,
                "sell_shares": 0.0,
                "buy_cash": 0.0,
                "sell_cash": 0.0,
                "buy_rows": 0.0,
                "sell_rows": 0.0,
            }
        )
    )
    price_cost: dict[str, float] = defaultdict(float)
    for row in trades:
        condition = str(row.get("conditionId") or "")
        outcome = str(row.get("outcome") or "")
        if condition not in labels or outcome not in {"Yes", "No"}:
            continue
        side = str(row.get("side") or "")
        shares = float(row.get("size") or 0)
        cash = float(row.get("usdcSize") or 0)
        if side == "BUY":
            book[condition][outcome]["buy_shares"] += shares
            book[condition][outcome]["buy_cash"] += cash
            book[condition][outcome]["buy_rows"] += 1
            price_cost[_price_band(float(row.get("price") or 0))] += cash
        elif side == "SELL":
            book[condition][outcome]["sell_shares"] += shares
            book[condition][outcome]["sell_cash"] += cash
            book[condition][outcome]["sell_rows"] += 1

    net: dict[str, dict[str, float]] = defaultdict(dict)
    negative_net = False
    for condition in conditions:
        for outcome in ("Yes", "No"):
            values = book[condition][outcome]
            value = values["buy_shares"] - values["sell_shares"]
            net[condition][outcome] = value
            negative_net |= value < -1e-6

    buy_cost = sum(float(row.get("usdcSize") or 0) for row in buys)
    sell_proceeds = sum(float(row.get("usdcSize") or 0) for row in sells)
    yes_cost = sum(
        values["buy_cash"]
        for condition in book.values()
        for outcome, values in condition.items()
        if outcome == "Yes"
    )
    no_cost = sum(
        values["buy_cash"]
        for condition in book.values()
        for outcome, values in condition.items()
        if outcome == "No"
    )
    traded_conditions = {
        condition
        for condition in conditions
        if any(book[condition][outcome]["buy_rows"] or book[condition][outcome]["sell_rows"] for outcome in ("Yes", "No"))
    }
    paired_conditions = sum(
        book[condition]["Yes"]["buy_rows"] > 0 and book[condition]["No"]["buy_rows"] > 0
        for condition in conditions
    )

    payoff: dict[str, float] = {}
    if not negative_net:
        total_no = sum(max(net[condition]["No"], 0.0) for condition in conditions)
        for winner in conditions:
            payoff[labels[winner]] = (
                max(net[winner]["Yes"], 0.0)
                + total_no
                - max(net[winner]["No"], 0.0)
            )
    payoff_values = list(payoff.values())
    payoff_mean = statistics.fmean(payoff_values) if payoff_values else None
    payoff_range = max(payoff_values) - min(payoff_values) if payoff_values else None
    payoff_range_over_mean = (
        payoff_range / payoff_mean if payoff_mean is not None and payoff_mean > 1e-8 else None
    )

    yes_share = yes_cost / buy_cost if buy_cost else 0.0
    no_share = no_cost / buy_cost if buy_cost else 0.0
    condition_n = len(traded_conditions)
    if paired_conditions and paired_conditions >= max(1, condition_n // 2):
        base_shape = "paired_yes_no_inventory"
    elif condition_n <= 1:
        base_shape = "single_bracket"
    elif yes_share >= 0.90:
        base_shape = "yes_distribution_strip"
    elif no_share >= 0.90:
        base_shape = "no_complement_basket"
    else:
        base_shape = "mixed_ladder"
    active_rebalance = sell_proceeds / buy_cost >= 0.20 if buy_cost else False

    target = target_date_near_timestamp(slug, max(timestamps) if timestamps else 0)
    city = _city(slug)
    target_day_cost = 0.0
    local_hour_cost: dict[str, float] = defaultdict(float)
    pre_target_cost = 0.0
    if target:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(CITY_TIMEZONES.get(city, "UTC"))
        for row in buys:
            local = datetime.fromtimestamp(int(row.get("timestamp") or 0), timezone.utc).astimezone(zone)
            cost = float(row.get("usdcSize") or 0)
            if local.date() == target:
                target_day_cost += cost
                local_hour_cost[_hour_band(local.hour)] += cost
            elif local.date() < target:
                pre_target_cost += cost

    return {
        "event_slug": slug,
        "city": city,
        "target_date": target.isoformat() if target else "",
        "event_title": metadata.get("title") or "",
        "sample_complete": bool(timestamps) and min(timestamps) > sample_oldest_ts,
        "negative_net_inventory": negative_net,
        "trade_rows": len(trades),
        "buy_rows": len(buys),
        "sell_rows": len(sells),
        "buy_cost": round(buy_cost, 6),
        "sell_proceeds": round(sell_proceeds, 6),
        "sell_proceeds_over_buy_cost": round(sell_proceeds / buy_cost, 6) if buy_cost else None,
        "yes_buy_cost_share": round(yes_share, 6) if buy_cost else None,
        "no_buy_cost_share": round(no_share, 6) if buy_cost else None,
        "total_brackets": len(conditions),
        "traded_conditions": condition_n,
        "ladder_coverage": round(condition_n / len(conditions), 6) if conditions else None,
        "paired_conditions": paired_conditions,
        "base_shape": base_shape,
        "active_rebalance": active_rebalance,
        "expression": f"{base_shape}{'+active_rebalance' if active_rebalance else ''}",
        "target_day_buy_cost_share": round(target_day_cost / buy_cost, 6) if buy_cost else None,
        "pre_target_buy_cost_share": round(pre_target_cost / buy_cost, 6) if buy_cost else None,
        "target_day_local_hour_cost_share": {
            key: round(value / target_day_cost, 6) if target_day_cost else None
            for key, value in sorted(local_hour_cost.items())
        },
        "buy_cost_share_by_price": {
            key: round(value / buy_cost, 6) if buy_cost else None
            for key, value in sorted(price_cost.items())
        },
        "net_yes_share_cv": coefficient_of_variation(
            [max(net[condition]["Yes"], 0.0) for condition in conditions]
        ),
        "net_no_share_cv": coefficient_of_variation(
            [max(net[condition]["No"], 0.0) for condition in conditions]
        ),
        "payoff": {
            "net_cash_cost_before_redemption": round(buy_cost - sell_proceeds, 6),
            "min": round(min(payoff_values), 6) if payoff_values else None,
            "max": round(max(payoff_values), 6) if payoff_values else None,
            "mean": round(payoff_mean, 6) if payoff_mean is not None else None,
            "range_over_mean": (
                round(payoff_range_over_mean, 6)
                if payoff_range_over_mean is not None and math.isfinite(payoff_range_over_mean)
                else None
            ),
            "by_winning_bracket": {
                key: round(value, 6) for key, value in payoff.items()
            },
        },
        "condition_inventory": {
            labels[condition]: {
                outcome.lower(): {
                    "net_shares": round(net[condition][outcome], 6),
                    "buy_cash": round(book[condition][outcome]["buy_cash"], 6),
                    "sell_cash": round(book[condition][outcome]["sell_cash"], 6),
                }
                for outcome in ("Yes", "No")
            }
            for condition in conditions
            if condition in traded_conditions
        },
    }


def weighted_share(events: list[dict[str, Any]], field: str) -> float | None:
    denominator = sum(event["buy_cost"] for event in events)
    if not denominator:
        return None
    return sum(event["buy_cost"] * float(event.get(field) or 0) for event in events) / denominator


def historical_gross_structure(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine closed token summaries into event-level gross bought expressions."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        slug = _event_slug(row)
        if slug:
            grouped[slug].append(row)
    events = []
    for slug, event_rows in grouped.items():
        condition_outcomes: dict[str, set[str]] = defaultdict(set)
        yes_cost = 0.0
        no_cost = 0.0
        yes_shares: list[float] = []
        no_shares: list[float] = []
        for row in event_rows:
            condition = str(row.get("conditionId") or "")
            outcome = str(row.get("outcome") or "")
            shares = float(row.get("totalBought") or 0)
            cost = shares * float(row.get("avgPrice") or 0)
            if condition and outcome:
                condition_outcomes[condition].add(outcome)
            if outcome == "Yes":
                yes_cost += cost
                yes_shares.append(shares)
            elif outcome == "No":
                no_cost += cost
                no_shares.append(shares)
        total_cost = yes_cost + no_cost
        conditions = len(condition_outcomes)
        paired = sum({"Yes", "No"} <= outcomes for outcomes in condition_outcomes.values())
        if paired and paired >= max(1, conditions // 2):
            expression = "paired_yes_no_inventory"
        elif conditions <= 1:
            expression = "single_bracket"
        elif yes_cost / total_cost >= 0.90 if total_cost else False:
            expression = "yes_distribution_strip"
        elif no_cost / total_cost >= 0.90 if total_cost else False:
            expression = "no_complement_basket"
        else:
            expression = "mixed_ladder"
        timestamp = max(int(row.get("timestamp") or 0) for row in event_rows)
        target = target_date_near_timestamp(slug, timestamp)
        events.append(
            {
                "event_slug": slug,
                "city": _city(slug),
                "target_date": target.isoformat() if target else "",
                "token_rows": len(event_rows),
                "traded_conditions": conditions,
                "paired_conditions": paired,
                "gross_bought_cost": total_cost,
                "yes_cost_share": yes_cost / total_cost if total_cost else None,
                "no_cost_share": no_cost / total_cost if total_cost else None,
                "yes_share_cv": coefficient_of_variation(yes_shares),
                "no_share_cv": coefficient_of_variation(no_shares),
                "expression": expression,
            }
        )
    total_cost = sum(event["gross_bought_cost"] for event in events)
    expression_counts = Counter(event["expression"] for event in events)
    expression_cost = defaultdict(float)
    for event in events:
        expression_cost[event["expression"]] += event["gross_bought_cost"]
    target_dates = {event["target_date"] for event in events if event["target_date"]}
    cities = Counter(event["city"] for event in events)
    yes_cost = sum(
        event["gross_bought_cost"] * float(event["yes_cost_share"] or 0) for event in events
    )
    return {
        "status": "discovery_only_do_not_infer_strategy_or_pnl",
        "closed_token_rows": len(rows),
        "discovered_events": len(events),
        "independent_target_dates": len(target_dates),
        "sample_target_date_min": min(target_dates) if target_dates else None,
        "sample_target_date_max": max(target_dates) if target_dates else None,
        "caveat": (
            "closed positions are survivor/remaining-position biased and are used only "
            "to discover historical event slugs for exact activity refetch; all "
            "expression statistics and realizedPnl from this layer are intentionally omitted"
        ),
    }


def stratified_event_slugs(
    closed_rows: list[dict[str, Any]],
    *,
    max_events: int,
) -> list[str]:
    """Choose at most one event per evenly spaced target date first."""
    by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in closed_rows:
        if _event_slug(row):
            by_slug[_event_slug(row)].append(row)
    by_date: dict[str, list[str]] = defaultdict(list)
    for slug, rows in by_slug.items():
        timestamp = max(int(row.get("timestamp") or 0) for row in rows)
        target = target_date_near_timestamp(slug, timestamp)
        if target:
            by_date[target.isoformat()].append(slug)
    dates = sorted(by_date)
    if not dates:
        return []
    date_budget = min(max_events, len(dates))
    if date_budget == 1:
        selected_dates = [dates[-1]]
    else:
        selected_dates = [
            dates[round(index * (len(dates) - 1) / (date_budget - 1))]
            for index in range(date_budget)
        ]
    chosen: list[str] = []
    for index, target_date in enumerate(selected_dates):
        slugs = sorted(set(by_date[target_date]))
        chosen.append(slugs[index % len(slugs)])
    if len(chosen) < max_events:
        for target_date in reversed(dates):
            for slug in sorted(set(by_date[target_date])):
                if slug not in chosen:
                    chosen.append(slug)
                    if len(chosen) >= max_events:
                        return chosen
    return chosen


def historical_exact_events(
    wallet: str,
    closed_rows: list[dict[str, Any]],
    *,
    max_events: int,
) -> list[dict[str, Any]]:
    slugs = stratified_event_slugs(closed_rows, max_events=max_events)
    metadata: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(event_metadata, slug): slug for slug in slugs}
        for future in as_completed(futures):
            slug = futures[future]
            value = future.result()
            if value:
                metadata[slug] = value

    def reconstruct(slug: str) -> dict[str, Any] | None:
        value = metadata.get(slug)
        if not value:
            return None
        rows = event_market_activity(wallet, value)
        weather_rows = [row for row in rows if _is_weather(row) and _event_slug(row) == slug]
        return event_portfolio(
            slug,
            weather_rows,
            value,
            sample_oldest_ts=0,
        )

    events: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(reconstruct, slug): slug for slug in slugs}
        for future in as_completed(futures):
            value = future.result()
            if value:
                events.append(value)
    return sorted(events, key=lambda row: (row["target_date"], row["event_slug"]))


def aggregate(
    wallet: str,
    raw_rows: list[dict[str, Any]],
    closed_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    usable = [
        event
        for event in events
        if event["sample_complete"] and not event["negative_net_inventory"] and event["buy_cost"] > 0
    ]
    total_cost = sum(event["buy_cost"] for event in usable)
    expressions = Counter(event["expression"] for event in usable)
    expression_cost = defaultdict(float)
    for event in usable:
        expression_cost[event["expression"]] += event["buy_cost"]
    range_values = [
        event["payoff"]["range_over_mean"]
        for event in usable
        if event["payoff"]["range_over_mean"] is not None
    ]
    target_dates = {event["target_date"] for event in usable if event["target_date"]}
    cities = Counter(event["city"] for event in usable)
    return {
        "wallet": wallet,
        "coverage": {
            "latest_wallet_activity_rows": len(raw_rows),
            "weather_rows": sum(_is_weather(row) for row in raw_rows),
            "events_with_metadata": len(events),
            "usable_complete_events": len(usable),
            "independent_target_dates": len(target_dates),
            "sample_target_date_min": min(target_dates) if target_dates else None,
            "sample_target_date_max": max(target_dates) if target_dates else None,
        },
        "portfolio_structure": {
            "multi_condition_event_share": (
                round(sum(event["traded_conditions"] >= 2 for event in usable) / len(usable), 6)
                if usable
                else None
            ),
            "mean_traded_conditions": (
                round(statistics.fmean(event["traded_conditions"] for event in usable), 6)
                if usable
                else None
            ),
            "mean_ladder_coverage": (
                round(statistics.fmean(event["ladder_coverage"] for event in usable), 6)
                if usable
                else None
            ),
            "yes_buy_cost_share": round(weighted_share(usable, "yes_buy_cost_share") or 0, 6),
            "no_buy_cost_share": round(weighted_share(usable, "no_buy_cost_share") or 0, 6),
            "active_rebalance_event_share": (
                round(sum(event["active_rebalance"] for event in usable) / len(usable), 6)
                if usable
                else None
            ),
            "paired_condition_event_share": (
                round(sum(event["paired_conditions"] > 0 for event in usable) / len(usable), 6)
                if usable
                else None
            ),
            "flat_payoff_event_share": (
                round(sum(value <= 0.20 for value in range_values) / len(range_values), 6)
                if range_values
                else None
            ),
            "median_payoff_range_over_mean": (
                round(statistics.median(range_values), 6) if range_values else None
            ),
            "target_day_buy_cost_share": round(
                weighted_share(usable, "target_day_buy_cost_share") or 0, 6
            ),
            "pre_target_buy_cost_share": round(
                weighted_share(usable, "pre_target_buy_cost_share") or 0, 6
            ),
            "expression_event_counts": dict(expressions),
            "expression_buy_cost_share": {
                key: round(value / total_cost, 6) if total_cost else None
                for key, value in sorted(expression_cost.items())
            },
            "top_cities": cities.most_common(12),
        },
        "historical_gross_structure": historical_gross_structure(closed_rows),
        "events": usable,
    }


def scan_wallet(wallet: str, max_events: int) -> dict[str, Any]:
    session = requests.Session()
    raw_rows = latest_activity(session, wallet)
    closed_rows = recent_closed_positions(wallet)
    weather_trades = [
        row
        for row in raw_rows
        if _is_weather(row) and row.get("type") == "TRADE" and _event_slug(row)
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in weather_trades:
        grouped[_event_slug(row)].append(row)
    selected = sorted(
        grouped,
        key=lambda slug: max(int(row.get("timestamp") or 0) for row in grouped[slug]),
        reverse=True,
    )[:max_events]
    metadata: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(event_metadata, slug): slug for slug in selected}
        for future in as_completed(futures):
            slug = futures[future]
            value = future.result()
            if value:
                metadata[slug] = value
    oldest_ts = min((int(row.get("timestamp") or 0) for row in raw_rows), default=0)
    events = [
        event_portfolio(
            slug,
            grouped[slug],
            metadata[slug],
            sample_oldest_ts=oldest_ts,
        )
        for slug in selected
        if slug in metadata
    ]
    result = aggregate(wallet, raw_rows, closed_rows, events)
    historical_events = historical_exact_events(
        wallet,
        closed_rows,
        max_events=min(30, max_events),
    )
    historical_summary = aggregate(wallet, [], [], historical_events)
    result["historical_exact_sample"] = {
        "coverage": historical_summary["coverage"],
        "portfolio_structure": historical_summary["portfolio_structure"],
        "events": historical_summary["events"],
        "sampling": (
            "one event from each of up to 30 evenly spaced target dates discovered "
            "through closed positions; every bracket is then refetched from exact "
            "condition-filtered activity"
        ),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", action="append", required=True)
    parser.add_argument("--max-events", type=int, default=40)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--append-existing", action="store_true")
    args = parser.parse_args()

    results = []
    if args.append_existing and args.output.exists():
        prior = json.loads(args.output.read_text(encoding="utf-8"))
        requested = {wallet.lower() for wallet in args.wallet}
        results = [
            row
            for row in prior.get("wallets") or []
            if str(row.get("wallet") or "").lower() not in requested
        ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for wallet in args.wallet:
        result = scan_wallet(wallet.lower(), args.max_events)
        results.append(result)
        print(
            wallet,
            result["coverage"],
            result["portfolio_structure"]["expression_event_counts"],
            flush=True,
        )
        payload = {
            "snapshot_utc": datetime.now(timezone.utc).isoformat(),
            "grain": "one mutually-exclusive temperature ladder per city x target_date event",
            "wallets": results,
        }
        args.output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
