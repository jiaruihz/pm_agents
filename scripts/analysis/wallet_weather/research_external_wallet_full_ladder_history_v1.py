#!/usr/bin/env python3
"""Replay a complete external weather wallet at city x target_date ladder grain.

Every mutually exclusive temperature bracket is combined before strategy
statistics are calculated.  Public activity timestamps are fill timestamps,
not private signal or original order-post timestamps.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timezone
import gzip
import json
import math
from pathlib import Path
import random
import statistics
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from research_external_wallet_strategy_v1 import (
    CITY_TIMEZONES,
    _city,
    _hour_band,
)
from research_wallet_event_portfolios_v1 import (
    bracket_label,
    target_date_near_timestamp,
)


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise RuntimeError(f"refusing empty CSV: {path}")
    fields = sorted({key for row in rows for key in row})
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        json.dumps(value, ensure_ascii=False, sort_keys=True)
                        if isinstance(value, (dict, list))
                        else value
                    )
                    for key, value in row.items()
                }
            )
    temporary.replace(path)


def percentile(values: Iterable[float], probability: float) -> float | None:
    ordered = sorted(float(value) for value in values if value is not None)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def distribution(values: Iterable[float]) -> dict[str, float | int | None]:
    materialized = [float(value) for value in values if value is not None]
    return {
        "n": len(materialized),
        "p10": percentile(materialized, 0.10),
        "p25": percentile(materialized, 0.25),
        "median": percentile(materialized, 0.50),
        "p75": percentile(materialized, 0.75),
        "p90": percentile(materialized, 0.90),
        "mean": statistics.fmean(materialized) if materialized else None,
    }


def weighted_percentile(
    values_and_weights: Iterable[tuple[float, float]],
    probability: float,
) -> float | None:
    pairs = sorted(
        (float(value), float(weight))
        for value, weight in values_and_weights
        if value is not None and weight is not None and float(weight) > 0
    )
    total = sum(weight for _, weight in pairs)
    if total <= 0:
        return None
    threshold = probability * total
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return pairs[-1][0]


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def parse_jsonish(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def winner_condition(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    winners: list[str] = []
    for market in metadata.get("markets") or []:
        if not isinstance(market, dict) or not market.get("conditionId"):
            continue
        outcomes = [str(value).lower() for value in parse_jsonish(market.get("outcomes"))]
        prices = parse_jsonish(market.get("outcomePrices"))
        if not outcomes or len(prices) != len(outcomes):
            continue
        try:
            yes_index = outcomes.index("yes")
            yes_price = float(prices[yes_index])
        except (ValueError, TypeError):
            continue
        if yes_price >= 0.999:
            winners.append(str(market["conditionId"]))
    return winners[0] if len(winners) == 1 else None


def event_target_date(event_slug: str, timestamps: list[int]) -> date | None:
    timestamp = min((value for value in timestamps if value > 0), default=0)
    return target_date_near_timestamp(event_slug, timestamp)


def group_identity(event_slug: str, timestamps: list[int]) -> tuple[str, str]:
    target = event_target_date(event_slug, timestamps)
    return _city(event_slug), target.isoformat() if target else "unknown"


def cluster_count(timestamps: Iterable[int], gap_seconds: int) -> int:
    ordered = sorted(set(int(value) for value in timestamps if value))
    if not ordered:
        return 0
    return 1 + sum(
        current - previous > gap_seconds
        for previous, current in zip(ordered, ordered[1:])
    )


def local_datetime(timestamp: int, city: str) -> datetime | None:
    timezone_name = CITY_TIMEZONES.get(city)
    if not timezone_name or timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(ZoneInfo(timezone_name))


def local_hour(timestamp: int, city: str) -> float | None:
    value = local_datetime(timestamp, city)
    if not value:
        return None
    return value.hour + value.minute / 60 + value.second / 3600


def metadata_markets(
    event_slugs: list[str],
    metadata_by_slug: dict[str, dict[str, Any] | None],
) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event_slug in event_slugs:
        metadata = metadata_by_slug.get(event_slug)
        if not metadata:
            continue
        for index, market in enumerate(metadata.get("markets") or []):
            if not isinstance(market, dict) or not market.get("conditionId"):
                continue
            condition = str(market["conditionId"])
            if condition in seen:
                continue
            seen.add(condition)
            markets.append(
                {
                    **market,
                    "_event_slug": event_slug,
                    "_source_index": index,
                }
            )
    return markets


def event_portfolio(
    city: str,
    target_date: str,
    event_slugs: list[str],
    rows: list[dict[str, Any]],
    position_rows: list[dict[str, Any]],
    metadata_by_slug: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    rows = sorted(rows, key=lambda row: int(row.get("timestamp") or 0))
    trades = [row for row in rows if str(row.get("type") or "").upper() == "TRADE"]
    buys = [row for row in trades if str(row.get("side") or "").upper() == "BUY"]
    sells = [row for row in trades if str(row.get("side") or "").upper() == "SELL"]
    redeems = [row for row in rows if str(row.get("type") or "").upper() == "REDEEM"]
    merges = [row for row in rows if str(row.get("type") or "").upper() == "MERGE"]
    splits = [row for row in rows if str(row.get("type") or "").upper() == "SPLIT"]
    conversions = [
        row for row in rows if str(row.get("type") or "").upper() == "CONVERSION"
    ]

    buy_cost = sum(float(row.get("usdcSize") or 0) for row in buys)
    sell_proceeds = sum(float(row.get("usdcSize") or 0) for row in sells)
    redeem_cash = sum(float(row.get("usdcSize") or 0) for row in redeems)
    merge_cash = sum(float(row.get("usdcSize") or 0) for row in merges)
    split_cash = sum(float(row.get("usdcSize") or 0) for row in splits)
    conversion_cash = sum(float(row.get("usdcSize") or 0) for row in conversions)
    public_cashflow = (
        sell_proceeds
        + redeem_cash
        + merge_cash
        + conversion_cash
        - buy_cost
        - split_cash
    )
    position_current_value = sum(
        float(row.get("currentValue") or 0) for row in position_rows
    )

    first_buy_ts = min((int(row.get("timestamp") or 0) for row in buys), default=0)
    last_buy_ts = max((int(row.get("timestamp") or 0) for row in buys), default=0)
    first_sell_ts = min((int(row.get("timestamp") or 0) for row in sells), default=0)
    last_sell_ts = max((int(row.get("timestamp") or 0) for row in sells), default=0)
    first_redeem_ts = min((int(row.get("timestamp") or 0) for row in redeems), default=0)
    last_redeem_ts = max((int(row.get("timestamp") or 0) for row in redeems), default=0)

    buy_transactions: dict[str, int] = {}
    for row in buys:
        transaction = str(row.get("transactionHash") or "")
        timestamp = int(row.get("timestamp") or 0)
        if transaction:
            buy_transactions[transaction] = min(
                timestamp,
                buy_transactions.get(transaction, timestamp),
            )

    yes_buys = [row for row in buys if str(row.get("outcome") or "").lower() == "yes"]
    no_buys = [row for row in buys if str(row.get("outcome") or "").lower() == "no"]
    yes_buy_cost = sum(float(row.get("usdcSize") or 0) for row in yes_buys)
    no_buy_cost = sum(float(row.get("usdcSize") or 0) for row in no_buys)

    net_yes: dict[str, float] = defaultdict(float)
    net_no: dict[str, float] = defaultdict(float)
    for row in trades:
        condition = str(row.get("conditionId") or "")
        outcome = str(row.get("outcome") or "").lower()
        direction = 1.0 if str(row.get("side") or "").upper() == "BUY" else -1.0
        size = float(row.get("size") or 0) * direction
        if outcome == "yes":
            net_yes[condition] += size
        elif outcome == "no":
            net_no[condition] += size
    for row in splits:
        condition = str(row.get("conditionId") or "")
        size = float(row.get("size") or 0)
        net_yes[condition] += size
        net_no[condition] += size
    for row in merges:
        condition = str(row.get("conditionId") or "")
        size = float(row.get("size") or 0)
        net_yes[condition] -= size
        net_no[condition] -= size

    markets = metadata_markets(event_slugs, metadata_by_slug)
    for row in conversions:
        size = float(row.get("size") or 0)
        for market in markets:
            net_no[str(market["conditionId"])] -= size
    condition_index = {
        str(market["conditionId"]): index for index, market in enumerate(markets)
    }
    condition_labels = {
        str(market["conditionId"]): bracket_label(market) for market in markets
    }
    yes_positive = {
        condition: shares
        for condition, shares in net_yes.items()
        if shares > 1e-8
    }
    yes_indices = sorted(
        condition_index[condition]
        for condition in yes_positive
        if condition in condition_index
    )
    yes_span = (
        yes_indices[-1] - yes_indices[0] + 1 if yes_indices else None
    )
    yes_contiguous = (
        bool(yes_indices)
        and len(yes_indices) == yes_span
        and len(yes_indices) == len(yes_positive)
    )
    yes_shares = list(yes_positive.values())
    total_yes_shares = sum(yes_shares)
    minimum_yes_shares = min(yes_shares) if yes_shares else None
    mean_yes_shares = statistics.fmean(yes_shares) if yes_shares else None
    yes_share_cv = (
        statistics.pstdev(yes_shares) / mean_yes_shares
        if len(yes_shares) >= 2 and mean_yes_shares
        else 0.0 if len(yes_shares) == 1 else None
    )
    base_share_fraction = (
        minimum_yes_shares * len(yes_shares) / total_yes_shares
        if minimum_yes_shares is not None and total_yes_shares
        else None
    )
    top_share_fraction = (
        max(yes_shares) / total_yes_shares if total_yes_shares else None
    )
    yes_share_hhi = (
        sum((value / total_yes_shares) ** 2 for value in yes_shares)
        if total_yes_shares
        else None
    )
    weighted_index = (
        sum(condition_index[condition] * shares for condition, shares in yes_positive.items())
        / total_yes_shares
        if total_yes_shares
        and all(condition in condition_index for condition in yes_positive)
        else None
    )
    modal_condition = (
        max(yes_positive, key=yes_positive.get) if yes_positive else None
    )
    modal_index = condition_index.get(modal_condition) if modal_condition else None
    normalized_center = (
        weighted_index / (len(markets) - 1)
        if weighted_index is not None and len(markets) > 1
        else None
    )

    metadata_complete = all(metadata_by_slug.get(slug) for slug in event_slugs)
    winners = {
        condition
        for slug in event_slugs
        if (condition := winner_condition(metadata_by_slug.get(slug)))
    }
    winner = next(iter(winners)) if len(winners) == 1 else None
    resolved = winner is not None
    cashflow_complete = resolved and position_current_value < 0.01
    token_payout = None
    economic_pnl = None
    if winner:
        token_payout = net_yes.get(winner, 0.0) + sum(
            shares for condition, shares in net_no.items() if condition != winner
        )
        economic_pnl = (
            sell_proceeds
            + merge_cash
            + conversion_cash
            - buy_cost
            - split_cash
            + token_payout
        )

    first_local = local_datetime(first_buy_ts, city)
    last_local = local_datetime(last_buy_ts, city)
    weighted_local_hour = (
        sum(
            (local_hour(int(row.get("timestamp") or 0), city) or 0.0)
            * float(row.get("usdcSize") or 0)
            for row in buys
            if local_hour(int(row.get("timestamp") or 0), city) is not None
        )
        / sum(
            float(row.get("usdcSize") or 0)
            for row in buys
            if local_hour(int(row.get("timestamp") or 0), city) is not None
        )
        if any(local_hour(int(row.get("timestamp") or 0), city) is not None for row in buys)
        else None
    )
    target = date.fromisoformat(target_date) if target_date != "unknown" else None
    first_entry_day_offset = (
        (first_local.date() - target).days if first_local and target else None
    )
    redeem_local = local_datetime(first_redeem_ts, city)
    redeem_target_hours = (
        (
            redeem_local
            - datetime.combine(target, datetime.min.time(), tzinfo=redeem_local.tzinfo)
        ).total_seconds()
        / 3600
        if redeem_local and target
        else None
    )

    if sells and (redeems or resolved):
        exit_style = "active_sell_then_settlement"
    elif sells:
        exit_style = "active_sell_only"
    elif redeems:
        exit_style = "redeem_without_sell"
    elif merges:
        exit_style = "merge_without_sell"
    elif resolved:
        exit_style = "resolved_without_public_cash_exit"
    else:
        exit_style = "unsettled_or_no_public_exit"

    if yes_buy_cost and no_buy_cost:
        expression = "mixed_yes_no"
    elif yes_buy_cost:
        expression = "yes_strip" if len(yes_positive) > 1 else "single_yes"
    elif no_buy_cost:
        expression = "no_only"
    else:
        expression = "conversion_only"

    return {
        "city": city,
        "target_date": target_date,
        "event_slugs": event_slugs,
        "event_slug_count": len(event_slugs),
        "metadata_complete": metadata_complete,
        "ladder_brackets": len(markets) if metadata_complete else None,
        "activity_rows": len(rows),
        "trade_rows": len(trades),
        "buy_rows": len(buys),
        "sell_rows": len(sells),
        "buy_cost": buy_cost,
        "yes_buy_cost": yes_buy_cost,
        "no_buy_cost": no_buy_cost,
        "yes_buy_cost_share": ratio(yes_buy_cost, buy_cost),
        "sell_proceeds": sell_proceeds,
        "redeem_cash": redeem_cash,
        "merge_cash": merge_cash,
        "split_cash": split_cash,
        "conversion_cash": conversion_cash,
        "public_cashflow": public_cashflow,
        "position_current_value_at_snapshot": position_current_value,
        "cashflow_complete": cashflow_complete,
        "resolved": resolved,
        "winner_condition": winner,
        "winner_label": condition_labels.get(winner) if winner else None,
        "token_payout_reconstructed": token_payout,
        "economic_pnl_reconstructed": economic_pnl,
        "turnover_roi_reconstructed": ratio(economic_pnl, buy_cost)
        if economic_pnl is not None
        else None,
        "winner_in_positive_yes_strip": winner in yes_positive if winner else None,
        "expression": expression,
        "first_buy_ts": first_buy_ts or None,
        "last_buy_ts": last_buy_ts or None,
        "first_sell_ts": first_sell_ts or None,
        "last_sell_ts": last_sell_ts or None,
        "first_redeem_ts": first_redeem_ts or None,
        "last_redeem_ts": last_redeem_ts or None,
        "first_buy_local": first_local.isoformat() if first_local else None,
        "last_buy_local": last_local.isoformat() if last_local else None,
        "first_buy_local_hour": local_hour(first_buy_ts, city),
        "cost_weighted_buy_local_hour": weighted_local_hour,
        "last_buy_local_hour": local_hour(last_buy_ts, city),
        "first_entry_day_offset": first_entry_day_offset,
        "buy_span_minutes": (
            (last_buy_ts - first_buy_ts) / 60 if first_buy_ts and last_buy_ts else None
        ),
        "first_buy_to_first_sell_hours": (
            (first_sell_ts - first_buy_ts) / 3600
            if first_buy_ts and first_sell_ts
            else None
        ),
        "first_buy_to_redeem_hours": (
            (first_redeem_ts - first_buy_ts) / 3600
            if first_buy_ts and first_redeem_ts
            else None
        ),
        "last_buy_to_redeem_hours": (
            (first_redeem_ts - last_buy_ts) / 3600
            if last_buy_ts and first_redeem_ts
            else None
        ),
        "redeem_target_day_hour": redeem_target_hours,
        "unique_buy_transactions": len(buy_transactions),
        "buy_bursts_gap_gt_60s": cluster_count(buy_transactions.values(), 60),
        "buy_sessions_gap_gt_5m": cluster_count(buy_transactions.values(), 300),
        "sell_transactions": len(
            {
                str(row.get("transactionHash"))
                for row in sells
                if row.get("transactionHash")
            }
        ),
        "sell_proceeds_over_buy_cost": ratio(sell_proceeds, buy_cost),
        "sell_price_cost_weighted": (
            sum(float(row.get("price") or 0) * float(row.get("usdcSize") or 0) for row in sells)
            / sell_proceeds
            if sell_proceeds
            else None
        ),
        "sell_proceeds_share_ge_95c": ratio(
            sum(
                float(row.get("usdcSize") or 0)
                for row in sells
                if float(row.get("price") or 0) >= 0.95
            ),
            sell_proceeds,
        ),
        "sell_proceeds_share_ge_99c": ratio(
            sum(
                float(row.get("usdcSize") or 0)
                for row in sells
                if float(row.get("price") or 0) >= 0.99
            ),
            sell_proceeds,
        ),
        "exit_style": exit_style,
        "yes_positive_brackets": len(yes_positive),
        "yes_positive_span_brackets": yes_span,
        "yes_strip_contiguous": yes_contiguous,
        "yes_ladder_coverage": ratio(len(yes_positive), len(markets))
        if metadata_complete
        else None,
        "yes_share_cv": yes_share_cv,
        "yes_base_share_fraction": base_share_fraction,
        "yes_modal_overweight_fraction": (
            1 - base_share_fraction if base_share_fraction is not None else None
        ),
        "yes_top_share_fraction": top_share_fraction,
        "yes_share_hhi": yes_share_hhi,
        "yes_weighted_ladder_center": normalized_center,
        "yes_modal_ladder_index": modal_index,
        "yes_modal_label": condition_labels.get(modal_condition)
        if modal_condition
        else None,
        "yes_bracket_shares": {
            condition_labels.get(condition, condition): shares
            for condition, shares in sorted(
                yes_positive.items(),
                key=lambda item: condition_index.get(item[0], 10**9),
            )
        },
        "has_sell": bool(sells),
        "has_redeem": bool(redeems),
        "has_merge": bool(merges),
        "has_split": bool(splits),
        "has_conversion": bool(conversions),
    }


def weighted_hour_profile(
    activity: list[dict[str, Any]],
) -> dict[str, Any]:
    bands: dict[str, float] = defaultdict(float)
    observations: list[tuple[float, float]] = []
    day_offsets: dict[str, float] = defaultdict(float)
    covered_cost = 0.0
    total_cost = 0.0
    for row in activity:
        if (
            str(row.get("type") or "").upper() != "TRADE"
            or str(row.get("side") or "").upper() != "BUY"
        ):
            continue
        cost = float(row.get("usdcSize") or 0)
        total_cost += cost
        slug = str(row.get("eventSlug") or "")
        city = _city(slug)
        timestamp = int(row.get("timestamp") or 0)
        observed = local_datetime(timestamp, city)
        target = target_date_near_timestamp(slug, timestamp)
        if not observed or not target:
            continue
        hour = observed.hour + observed.minute / 60 + observed.second / 3600
        observations.append((hour, cost))
        bands[_hour_band(observed.hour)] += cost
        day_offsets[str((observed.date() - target).days)] += cost
        covered_cost += cost
    return {
        "total_buy_cost": total_cost,
        "timezone_covered_buy_cost": covered_cost,
        "timezone_coverage": ratio(covered_cost, total_cost),
        "cost_weighted_hour": {
            "p10": weighted_percentile(observations, 0.10),
            "p25": weighted_percentile(observations, 0.25),
            "median": weighted_percentile(observations, 0.50),
            "p75": weighted_percentile(observations, 0.75),
            "p90": weighted_percentile(observations, 0.90),
        },
        "buy_cost_share_by_local_hour_band": {
            key: ratio(value, covered_cost) for key, value in sorted(bands.items())
        },
        "buy_cost_share_by_target_day_offset": {
            key: ratio(value, covered_cost)
            for key, value in sorted(day_offsets.items(), key=lambda item: int(item[0]))
        },
    }


def summarize_group(
    rows: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    buy_cost = sum(float(row["buy_cost"]) for row in rows)
    resolved = [row for row in rows if row["cashflow_complete"]]
    pnl = sum(float(row["public_cashflow"]) for row in resolved)
    return {
        "label": label,
        "events": len(rows),
        "independent_target_dates": len({row["target_date"] for row in rows}),
        "cities": len({row["city"] for row in rows}),
        "buy_cost": buy_cost,
        "resolved_events": len(resolved),
        "cashflow_complete_pnl": pnl,
        "turnover_roi": ratio(pnl, sum(float(row["buy_cost"]) for row in resolved)),
        "yes_buy_cost_share": ratio(
            sum(float(row["yes_buy_cost"]) for row in rows),
            buy_cost,
        ),
        "sell_event_share": ratio(sum(bool(row["has_sell"]) for row in rows), len(rows)),
        "redeem_event_share": ratio(sum(bool(row["has_redeem"]) for row in rows), len(rows)),
        "merge_event_share": ratio(sum(bool(row["has_merge"]) for row in rows), len(rows)),
        "settlement_without_sell_share": ratio(
            sum(
                not row["has_sell"]
                and (
                    row["has_redeem"]
                    or row["has_merge"]
                    or row["resolved"]
                )
                for row in rows
            ),
            len(rows),
        ),
        "first_buy_local_hour": distribution(
            row["first_buy_local_hour"] for row in rows
        ),
        "cost_weighted_buy_local_hour": distribution(
            row["cost_weighted_buy_local_hour"] for row in rows
        ),
        "buy_span_minutes": distribution(row["buy_span_minutes"] for row in rows),
        "unique_buy_transactions": distribution(
            row["unique_buy_transactions"] for row in rows
        ),
        "buy_bursts_gap_gt_60s": distribution(
            row["buy_bursts_gap_gt_60s"] for row in rows
        ),
        "buy_sessions_gap_gt_5m": distribution(
            row["buy_sessions_gap_gt_5m"] for row in rows
        ),
        "first_buy_to_redeem_hours": distribution(
            row["first_buy_to_redeem_hours"] for row in rows
        ),
        "last_buy_to_redeem_hours": distribution(
            row["last_buy_to_redeem_hours"] for row in rows
        ),
        "yes_positive_brackets": distribution(
            row["yes_positive_brackets"] for row in rows
        ),
        "yes_positive_span_brackets": distribution(
            row["yes_positive_span_brackets"] for row in rows
        ),
        "yes_ladder_coverage": distribution(
            row["yes_ladder_coverage"] for row in rows
        ),
        "yes_share_cv": distribution(row["yes_share_cv"] for row in rows),
        "yes_base_share_fraction": distribution(
            row["yes_base_share_fraction"] for row in rows
        ),
        "yes_modal_overweight_fraction": distribution(
            row["yes_modal_overweight_fraction"] for row in rows
        ),
        "yes_top_share_fraction": distribution(
            row["yes_top_share_fraction"] for row in rows
        ),
        "yes_weighted_ladder_center": distribution(
            row["yes_weighted_ladder_center"] for row in rows
        ),
        "contiguous_yes_strip_share": ratio(
            sum(bool(row["yes_strip_contiguous"]) for row in rows),
            len(rows),
        ),
        "winner_in_positive_yes_strip_share": ratio(
            sum(bool(row["winner_in_positive_yes_strip"]) for row in resolved),
            len(resolved),
        ),
    }


def flat_summary_row(summary: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, value in summary.items():
        if isinstance(value, dict):
            for subkey, subvalue in value.items():
                row[f"{key}_{subkey}"] = subvalue
        else:
            row[key] = value
    return row


def target_date_block_bootstrap(
    portfolios: list[dict[str, Any]],
    *,
    samples: int = 5_000,
    seed: int = 20260729,
) -> dict[str, Any]:
    by_date: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for row in portfolios:
        if not row["cashflow_complete"]:
            continue
        bucket = by_date[row["target_date"]]
        bucket[0] += float(row["public_cashflow"])
        bucket[1] += float(row["buy_cost"])
    dates = sorted(by_date)
    point_pnl = sum(by_date[value][0] for value in dates)
    point_cost = sum(by_date[value][1] for value in dates)
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(samples):
        selected = [dates[rng.randrange(len(dates))] for _ in dates]
        pnl = sum(by_date[value][0] for value in selected)
        cost = sum(by_date[value][1] for value in selected)
        if cost:
            draws.append(pnl / cost)
    return {
        "block": "target_date",
        "independent_target_dates": len(dates),
        "samples": samples,
        "seed": seed,
        "point_turnover_roi": ratio(point_pnl, point_cost),
        "ci95": [
            percentile(draws, 0.025),
            percentile(draws, 0.975),
        ],
        "positive_target_date_share": ratio(
            sum(by_date[value][0] > 0 for value in dates),
            len(dates),
        ),
    }


def analyze(
    source: Path,
    *,
    wallet: str,
    snapshot_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    activity = read_jsonl_gz(source / "weather_activity.jsonl.gz")
    positions = read_jsonl_gz(source / "weather_open_positions.jsonl.gz")
    metadata_wrappers = read_jsonl_gz(source / "event_metadata.jsonl.gz")
    metadata_by_slug = {
        str(row.get("event_slug") or ""): (
            row.get("metadata") if isinstance(row.get("metadata"), dict) else None
        )
        for row in metadata_wrappers
    }

    rows_by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in activity:
        slug = str(row.get("eventSlug") or "")
        if slug:
            rows_by_slug[slug].append(row)
    positions_by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in positions:
        slug = str(row.get("eventSlug") or "")
        if slug:
            positions_by_slug[slug].append(row)

    slugs_by_group: dict[tuple[str, str], list[str]] = defaultdict(list)
    for event_slug, rows in rows_by_slug.items():
        timestamps = [int(row.get("timestamp") or 0) for row in rows]
        slugs_by_group[group_identity(event_slug, timestamps)].append(event_slug)

    portfolios: list[dict[str, Any]] = []
    for (city, target_date), event_slugs in sorted(slugs_by_group.items()):
        event_rows = [
            row
            for event_slug in event_slugs
            for row in rows_by_slug[event_slug]
        ]
        event_positions = [
            row
            for event_slug in event_slugs
            for row in positions_by_slug[event_slug]
        ]
        portfolios.append(
            event_portfolio(
                city,
                target_date,
                sorted(event_slugs),
                event_rows,
                event_positions,
                metadata_by_slug,
            )
        )

    monthly_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    city_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in portfolios:
        monthly_groups[row["target_date"][:7]].append(row)
        city_groups[row["city"]].append(row)
    monthly = [
        flat_summary_row(summarize_group(rows, label=month))
        for month, rows in sorted(monthly_groups.items())
    ]
    cities = [
        flat_summary_row(summarize_group(rows, label=city))
        for city, rows in sorted(
            city_groups.items(),
            key=lambda item: -sum(float(row["buy_cost"]) for row in item[1]),
        )
    ]

    all_summary = summarize_group(portfolios, label="all")
    expression_counts = Counter(row["expression"] for row in portfolios)
    exit_counts = Counter(row["exit_style"] for row in portfolios)
    total_sell_proceeds = sum(float(row["sell_proceeds"]) for row in portfolios)
    sell_behavior = {
        "sell_events": sum(bool(row["has_sell"]) for row in portfolios),
        "sell_event_share": ratio(
            sum(bool(row["has_sell"]) for row in portfolios),
            len(portfolios),
        ),
        "sell_rows": sum(int(row["sell_rows"]) for row in portfolios),
        "sell_transactions": sum(int(row["sell_transactions"]) for row in portfolios),
        "sell_proceeds": total_sell_proceeds,
        "sell_proceeds_over_buy_cost": ratio(
            total_sell_proceeds,
            sum(float(row["buy_cost"]) for row in portfolios),
        ),
        "sell_price_cost_weighted": ratio(
            sum(
                float(row["sell_price_cost_weighted"] or 0)
                * float(row["sell_proceeds"])
                for row in portfolios
            ),
            total_sell_proceeds,
        ),
        "sell_proceeds_share_ge_95c": ratio(
            sum(
                float(row["sell_proceeds_share_ge_95c"] or 0)
                * float(row["sell_proceeds"])
                for row in portfolios
            ),
            total_sell_proceeds,
        ),
        "sell_proceeds_share_ge_99c": ratio(
            sum(
                float(row["sell_proceeds_share_ge_99c"] or 0)
                * float(row["sell_proceeds"])
                for row in portfolios
            ),
            total_sell_proceeds,
        ),
        "first_buy_to_first_sell_hours": distribution(
            row["first_buy_to_first_sell_hours"] for row in portfolios
        ),
    }
    settlement_behavior = {
        "gamma_resolved_events": sum(bool(row["resolved"]) for row in portfolios),
        "redeem_events": sum(bool(row["has_redeem"]) for row in portfolios),
        "merge_events": sum(bool(row["has_merge"]) for row in portfolios),
        "split_events": sum(bool(row["has_split"]) for row in portfolios),
        "conversion_events": sum(
            bool(row["has_conversion"]) for row in portfolios
        ),
        "conversion_cash": sum(
            float(row["conversion_cash"]) for row in portfolios
        ),
        "settlement_without_active_sell_events": sum(
            not row["has_sell"]
            and (row["has_redeem"] or row["has_merge"] or row["resolved"])
            for row in portfolios
        ),
        "settlement_without_active_sell_share": ratio(
            sum(
                not row["has_sell"]
                and (row["has_redeem"] or row["has_merge"] or row["resolved"])
                for row in portfolios
            ),
            len(portfolios),
        ),
        "first_buy_to_redeem_hours": distribution(
            row["first_buy_to_redeem_hours"] for row in portfolios
        ),
        "last_buy_to_redeem_hours": distribution(
            row["last_buy_to_redeem_hours"] for row in portfolios
        ),
        "redeem_target_day_hour": distribution(
            row["redeem_target_day_hour"] for row in portfolios
        ),
        "cashflow_complete_resolved_events": sum(
            bool(row["cashflow_complete"]) for row in portfolios
        ),
        "cashflow_complete_resolved_pnl": sum(
            float(row["public_cashflow"])
            for row in portfolios
            if row["cashflow_complete"]
        ),
        "cashflow_complete_resolved_buy_cost": sum(
            float(row["buy_cost"])
            for row in portfolios
            if row["cashflow_complete"]
        ),
        "resolved_current_value_at_snapshot": sum(
            float(row["position_current_value_at_snapshot"])
            for row in portfolios
            if row["resolved"]
        ),
    }
    settlement_behavior["cashflow_complete_turnover_roi"] = ratio(
        settlement_behavior["cashflow_complete_resolved_pnl"],
        settlement_behavior["cashflow_complete_resolved_buy_cost"],
    )
    settlement_behavior["target_date_block_bootstrap"] = target_date_block_bootstrap(
        portfolios
    )
    summary = {
        "schema_version": "external_wallet_full_ladder_history_v1",
        "wallet": wallet,
        "snapshot_id": snapshot_id,
        "source": str(source),
        "grain": "city_x_target_date_complete_mutually_exclusive_ladder",
        "coverage": {
            "activity_rows": len(activity),
            "event_slugs": len(rows_by_slug),
            "city_target_date_portfolios": len(portfolios),
            "independent_target_dates": len(
                {row["target_date"] for row in portfolios}
            ),
            "cities": len({row["city"] for row in portfolios}),
            "metadata_complete_portfolios": sum(
                bool(row["metadata_complete"]) for row in portfolios
            ),
            "metadata_incomplete_portfolios": sum(
                not row["metadata_complete"] for row in portfolios
            ),
            "multi_event_slug_city_dates": sum(
                row["event_slug_count"] > 1 for row in portfolios
            ),
            "timezone_missing_portfolios": sum(
                row["first_buy_local_hour"] is None for row in portfolios
            ),
            "timezone_missing_cities": sorted(
                {
                    row["city"]
                    for row in portfolios
                    if row["first_buy_local_hour"] is None
                }
            ),
        },
        "entry_fill_timing": weighted_hour_profile(activity),
        "portfolio_summary": all_summary,
        "sell_behavior": sell_behavior,
        "settlement_behavior": settlement_behavior,
        "expression_counts": dict(sorted(expression_counts.items())),
        "exit_style_counts": dict(sorted(exit_counts.items())),
        "methodology": {
            "entry_timestamp": "public fill timestamp; original order-post time unavailable",
            "batch_primary": "unique BUY transactionHash per city-target_date",
            "burst_definition": "new burst after >60 seconds between unique BUY transactions",
            "session_definition": "new session after >5 minutes between unique BUY transactions",
            "holding_proxy": "first BUY fill to first public REDEEM; active SELL reported separately",
            "width": (
                "positive net YES brackets after BUY/SELL/SPLIT/MERGE/"
                "NegRisk CONVERSION, ordered on complete Gamma ladder"
            ),
            "center_weighting": (
                "base_share_fraction = min positive YES shares * bracket_count / "
                "total positive YES shares; modal_overweight = 1-base_share_fraction"
            ),
            "settled_pnl": (
                "actual public activity cashflow on Gamma-resolved portfolios whose "
                "positions snapshot has <1c currentValue; CONVERSION usdcSize is "
                "realized full-set NO collateral and is included as an inflow; "
                "winner-token reconstruction is diagnostic only because NegRisk "
                "conversion changes redeemable shares"
            ),
            "public_data_limit": (
                "unfilled/cancelled orders, private signals, maker intent and original "
                "order-post timestamps are unavailable"
            ),
        },
    }
    return summary, portfolios, monthly, cities


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--snapshot-id", required=True)
    args = parser.parse_args()

    summary, portfolios, monthly, cities = analyze(
        args.source.resolve(),
        wallet=args.wallet.lower(),
        snapshot_id=args.snapshot_id,
    )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output / "summary.json", summary)
    write_csv(output / "event_portfolios.csv", portfolios)
    write_csv(output / "monthly_summary.csv", monthly)
    write_csv(output / "city_summary.csv", cities)
    atomic_write_json(
        output / "methodology.json",
        {
            "schema_version": summary["schema_version"],
            "grain": summary["grain"],
            "methodology": summary["methodology"],
        },
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "wallet": args.wallet.lower(),
                "snapshot_id": args.snapshot_id,
                "portfolios": len(portfolios),
                "independent_target_dates": summary["coverage"][
                    "independent_target_dates"
                ],
                "output": str(output),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
