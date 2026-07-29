#!/usr/bin/env python3
"""Replay a weather wallet's complete YES strips against PIT observations.

The analysis grain is one mutually-exclusive city-day ladder.  Individual
fills are retained only to recover chronology and their relation to the
running maximum; strategy classification is performed on the combined event
payoff.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any
from zoneinfo import ZoneInfo

import requests

from research_external_wallet_strategy_v1 import (
    CITY_TIMEZONES,
    _city,
    _event_slug,
    _is_weather,
    _paged,
    _target_date,
    _trade_key,
)
from research_wallet_event_portfolios_v1 import (
    event_metadata,
    event_portfolio,
    latest_activity,
)


OBSERVATION_ROOT = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/observations"
)


def _utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def _normalize_city(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    return {"losangeles": "la"}.get(normalized, normalized)


def _market_labels(metadata: dict[str, Any]) -> dict[str, str]:
    return {
        str(row.get("conditionId") or ""): str(
            row.get("groupItemTitle") or row.get("question") or ""
        )
        for row in metadata.get("markets") or []
        if isinstance(row, dict) and row.get("conditionId")
    }


def _winner(metadata: dict[str, Any]) -> str | None:
    for market in metadata.get("markets") or []:
        if not isinstance(market, dict):
            continue
        try:
            prices = json.loads(market.get("outcomePrices") or "[]")
        except (TypeError, ValueError):
            continue
        if len(prices) >= 2 and float(prices[0]) >= 0.999:
            return str(
                market.get("groupItemTitle") or market.get("question") or ""
            )
    return None


def _bracket_bounds(label: str) -> tuple[float, float, str] | None:
    unit_match = re.search(r"°\s*([CF])", label, re.IGNORECASE)
    if not unit_match:
        return None
    unit = unit_match.group(1).upper()
    normalized_label = re.sub(
        r"(?<=\d)\s*[-–]\s*(?=\d)",
        " to ",
        label,
    )
    values = [
        float(value)
        for value in re.findall(r"-?\d+(?:\.\d+)?", normalized_label)
    ]
    if not values:
        return None
    lowered = label.lower()
    if "or below" in lowered or "or lower" in lowered:
        return -math.inf, values[-1], unit
    if "or higher" in lowered or "or above" in lowered:
        return values[0], math.inf, unit
    if len(values) >= 2:
        return min(values[0], values[1]), max(values[0], values[1]), unit
    return values[0], values[0], unit


def _running_max_native(
    state: dict[str, Any] | None,
    unit: str,
) -> float | None:
    if not state or state.get("running_max_c") is None:
        return None
    value = float(state["running_max_c"])
    return value if unit == "C" else value * 9 / 5 + 32


def _relative_bracket(
    bounds: tuple[float, float, str] | None,
    state: dict[str, Any] | None,
) -> str:
    if not bounds:
        return "unknown"
    lower, upper, unit = bounds
    running = _running_max_native(state, unit)
    if running is None:
        return "unknown"
    if upper < running:
        return "below_running_max"
    if lower <= running <= upper:
        return "contains_running_max"
    return "above_running_max"


def load_observation_states(
    target_dates: set[str],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    states: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for target_date in sorted(target_dates):
        path = OBSERVATION_ROOT / target_date / "observations.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("target_date") != target_date or row.get("status") != "ok":
                    continue
                seen = _utc(
                    row.get("observation_cache_generated_at_utc")
                    or row.get("fetched_at_utc")
                )
                if not seen:
                    continue
                key = (_normalize_city(str(row.get("city") or "")), target_date)
                states[key][seen.isoformat()] = {
                    "seen_ts_utc": seen.isoformat(),
                    "last_obs_utc": row.get("last_obs_utc"),
                    "age_min": row.get("age_min"),
                    "current_temp_c": row.get("current_temp_c"),
                    "running_max_c": row.get("running_max_c"),
                    "minutes_since_running_max": row.get(
                        "minutes_since_running_max"
                    ),
                    "d_tmpf_1h": row.get("d_tmpf_1h"),
                    "d_tmpf_3h": row.get("d_tmpf_3h"),
                    "decline_c": row.get("decline_c"),
                    "dewpoint_depression_f": row.get("dewpoint_depression_f"),
                    "wind_dir_deg": row.get("wind_dir_deg"),
                    "wind_speed_kt": row.get("wind_speed_kt"),
                    "sky_cover_code": row.get("sky_cover_code"),
                    "precip_state": row.get("precip_state"),
                    "source": row.get("source"),
                    "station": row.get("station"),
                    "source_path": str(path),
                }
    return {
        key: [by_time[timestamp] for timestamp in sorted(by_time)]
        for key, by_time in states.items()
    }


def latest_state(
    states: dict[tuple[str, str], list[dict[str, Any]]],
    city: str,
    target_date: str,
    trade_ts: datetime,
) -> dict[str, Any] | None:
    rows = states.get((_normalize_city(city), target_date)) or []
    timestamps = [_utc(row["seen_ts_utc"]) for row in rows]
    index = bisect_right(timestamps, trade_ts) - 1
    if index < 0:
        return None
    state = dict(rows[index])
    snapshot_age = (trade_ts - timestamps[index]).total_seconds() / 60
    state["snapshot_age_at_trade_min"] = round(snapshot_age, 6)
    if state.get("age_min") is not None:
        state["source_age_at_trade_min"] = round(
            float(state["age_min"]) + snapshot_age,
            6,
        )
    return state


def _is_contiguous(metadata: dict[str, Any], traded_conditions: set[str]) -> bool:
    condition_order = [
        str(row.get("conditionId") or "")
        for row in metadata.get("markets") or []
        if isinstance(row, dict) and row.get("conditionId")
    ]
    indices = [
        index
        for index, condition_id in enumerate(condition_order)
        if condition_id in traded_conditions
    ]
    return bool(indices) and max(indices) - min(indices) + 1 == len(indices)


def _performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(float(row["buy_cost"]) for row in rows)
    pnl = sum(float(row["pnl"]) for row in rows)
    by_date: dict[str, dict[str, float]] = defaultdict(
        lambda: {"cost": 0.0, "pnl": 0.0}
    )
    for row in rows:
        by_date[row["target_date"]]["cost"] += float(row["buy_cost"])
        by_date[row["target_date"]]["pnl"] += float(row["pnl"])
    bootstrap: list[float] = []
    blocks = list(by_date.values())
    if blocks:
        import random

        rng = random.Random(20260729)
        for _ in range(10_000):
            sample = [rng.choice(blocks) for _ in blocks]
            sample_cost = sum(row["cost"] for row in sample)
            if sample_cost:
                bootstrap.append(
                    sum(row["pnl"] for row in sample) / sample_cost
                )
        bootstrap.sort()
    return {
        "events": len(rows),
        "target_dates": len(by_date),
        "buy_cost": round(cost, 6),
        "pnl": round(pnl, 6),
        "roi": round(pnl / cost, 6) if cost else None,
        "profitable_event_share": (
            round(sum(row["pnl"] > 0 for row in rows) / len(rows), 6)
            if rows
            else None
        ),
        "target_date_block_bootstrap_roi_ci95": (
            [
                round(bootstrap[int(0.025 * len(bootstrap))], 6),
                round(bootstrap[int(0.975 * len(bootstrap)) - 1], 6),
            ]
            if bootstrap
            else None
        ),
    }


def analyze(wallet: str, max_events: int) -> dict[str, Any]:
    session = requests.Session()
    raw = latest_activity(session, wallet)
    try:
        all_public_trades = _paged(
            session,
            "/trades",
            wallet,
            limit=500,
            max_offset=5_000,
            extra={"takerOnly": "false"},
        )
        taker_public_trades = _paged(
            session,
            "/trades",
            wallet,
            limit=500,
            max_offset=5_000,
            extra={"takerOnly": "true"},
        )
        trade_endpoint_error = None
    except requests.RequestException as exc:
        all_public_trades = []
        taker_public_trades = []
        trade_endpoint_error = f"{type(exc).__name__}: {exc}"
    all_weather_trades = {
        _trade_key(row): row for row in all_public_trades if _is_weather(row)
    }
    taker_weather_trades = {
        _trade_key(row): row for row in taker_public_trades if _is_weather(row)
    }
    all_trade_timestamps = [
        int(row.get("timestamp") or 0) for row in all_weather_trades.values()
    ]
    taker_trade_timestamps = [
        int(row.get("timestamp") or 0) for row in taker_weather_trades.values()
    ]
    if all_trade_timestamps and taker_trade_timestamps:
        comparison_start = max(
            min(all_trade_timestamps),
            min(taker_trade_timestamps),
        )
        comparison_end = min(
            max(all_trade_timestamps),
            max(taker_trade_timestamps),
        )
        comparable_all = {
            key
            for key, row in all_weather_trades.items()
            if comparison_start
            <= int(row.get("timestamp") or 0)
            <= comparison_end
        }
        comparable_taker = {
            key
            for key, row in taker_weather_trades.items()
            if comparison_start
            <= int(row.get("timestamp") or 0)
            <= comparison_end
        }
    else:
        comparison_start = None
        comparison_end = None
        comparable_all = set()
        comparable_taker = set()
    inferred_maker = comparable_all - comparable_taker
    weather = [row for row in raw if _is_weather(row)]
    trades = [
        row
        for row in weather
        if row.get("type") == "TRADE" and _event_slug(row)
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        grouped[_event_slug(row)].append(row)
    selected = sorted(
        grouped,
        key=lambda slug: max(
            int(row.get("timestamp") or 0) for row in grouped[slug]
        ),
        reverse=True,
    )[:max_events]

    metadata: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(event_metadata, slug): slug for slug in selected}
        for future in as_completed(futures):
            value = future.result()
            if value:
                metadata[futures[future]] = value

    target_dates = {
        target.isoformat()
        for slug in selected
        for row in grouped[slug][:1]
        if (
            target := _target_date(
                slug,
                int(row.get("timestamp") or 0),
            )
        )
    }
    observations = load_observation_states(target_dates)
    oldest_ts = min((int(row.get("timestamp") or 0) for row in raw), default=0)
    event_rows: list[dict[str, Any]] = []
    unique_transactions: dict[str, int] = {}
    relative_cost: dict[str, float] = defaultdict(float)
    source_ages: list[float] = []

    for slug in selected:
        event_meta = metadata.get(slug)
        if not event_meta:
            continue
        rows = grouped[slug]
        timestamps = [int(row.get("timestamp") or 0) for row in rows]
        target = _target_date(slug, max(timestamps) if timestamps else 0)
        if not target:
            continue
        city = _city(slug)
        labels = _market_labels(event_meta)
        timeline = []
        traded_yes_conditions: set[str] = set()
        for row in sorted(rows, key=lambda value: int(value.get("timestamp") or 0)):
            timestamp = int(row.get("timestamp") or 0)
            trade_ts = datetime.fromtimestamp(timestamp, timezone.utc)
            local_zone = ZoneInfo(CITY_TIMEZONES.get(city, "UTC"))
            state = latest_state(
                observations,
                city,
                target.isoformat(),
                trade_ts,
            )
            label = labels.get(
                str(row.get("conditionId") or ""),
                str(row.get("title") or ""),
            )
            bounds = _bracket_bounds(label)
            relation = _relative_bracket(bounds, state)
            value = float(row.get("usdcSize") or 0)
            if row.get("side") == "BUY":
                relative_cost[relation] += value
                if (
                    state
                    and state.get("source_age_at_trade_min") is not None
                ):
                    source_ages.append(float(state["source_age_at_trade_min"]))
                if row.get("outcome") == "Yes":
                    traded_yes_conditions.add(str(row.get("conditionId") or ""))
            transaction_hash = str(row.get("transactionHash") or "")
            if transaction_hash:
                unique_transactions[transaction_hash] = timestamp
            timeline.append(
                {
                    "timestamp": timestamp,
                    "ts_utc": trade_ts.isoformat(),
                    "ts_local": trade_ts.astimezone(local_zone).isoformat(),
                    "transaction_hash": transaction_hash,
                    "side": row.get("side"),
                    "outcome": row.get("outcome"),
                    "bracket": label,
                    "price": float(row.get("price") or 0),
                    "shares": float(row.get("size") or 0),
                    "usdc_size": value,
                    "relative_to_running_max": relation,
                    "pit_observation": state,
                }
            )

        portfolio = event_portfolio(
            slug,
            rows,
            event_meta,
            sample_oldest_ts=oldest_ts,
        )
        positive_yes_shares = [
            float(value["yes"]["net_shares"])
            for value in portfolio["condition_inventory"].values()
            if float(value["yes"]["net_shares"]) > 0
        ]
        common_shares = min(positive_yes_shares, default=0.0)
        buy_cost = float(portfolio["buy_cost"])
        winner = _winner(event_meta)
        payout = (
            float(portfolio["payoff"]["by_winning_bracket"].get(winner, 0))
            if winner
            else None
        )
        pnl = (
            payout + float(portfolio["sell_proceeds"]) - buy_cost
            if payout is not None
            else None
        )
        event_rows.append(
            {
                **portfolio,
                "winner": winner,
                "settled_pnl_from_final_inventory": (
                    round(pnl, 6) if pnl is not None else None
                ),
                "common_yes_shares": round(common_shares, 6),
                "basket_cost_per_common_share": (
                    round(buy_cost / common_shares, 6)
                    if common_shares
                    else None
                ),
                "yes_strip_contiguous": _is_contiguous(
                    event_meta,
                    traded_yes_conditions,
                ),
                "first_buy": next(
                    (row for row in timeline if row["side"] == "BUY"),
                    None,
                ),
                "timeline": timeline,
            }
        )

    tx_timestamps = sorted(unique_transactions.values())
    gaps = [
        current - previous
        for previous, current in zip(tx_timestamps, tx_timestamps[1:])
        if current >= previous
    ]
    per_minute = Counter(timestamp // 60 for timestamp in tx_timestamps)
    total_relative_cost = sum(relative_cost.values())
    settled = [
        {
            "target_date": row["target_date"],
            "buy_cost": row["buy_cost"],
            "pnl": row["settled_pnl_from_final_inventory"],
        }
        for row in event_rows
        if row["settled_pnl_from_final_inventory"] is not None
    ]

    return {
        "wallet": wallet,
        "snapshot_utc": datetime.now(timezone.utc).isoformat(),
        "grain": "one complete mutually-exclusive city x target_date ladder",
        "coverage": {
            "latest_wallet_rows": len(raw),
            "weather_rows": len(weather),
            "trade_rows": len(trades),
            "selected_events": len(selected),
            "usable_events": len(event_rows),
            "target_dates": len(
                {row["target_date"] for row in event_rows}
            ),
            "observation_city_dates": len(observations),
            "sample_truncated": len(raw) >= 5_500,
        },
        "structure": {
            "yes_strip_event_share": (
                round(
                    sum(
                        str(row["expression"]).startswith(
                            "yes_distribution_strip"
                        )
                        for row in event_rows
                    )
                    / len(event_rows),
                    6,
                )
                if event_rows
                else None
            ),
            "contiguous_yes_strip_event_share": (
                round(
                    sum(row["yes_strip_contiguous"] for row in event_rows)
                    / len(event_rows),
                    6,
                )
                if event_rows
                else None
            ),
            "mean_traded_conditions": (
                round(
                    statistics.mean(
                        row["traded_conditions"] for row in event_rows
                    ),
                    6,
                )
                if event_rows
                else None
            ),
            "mean_ladder_coverage": (
                round(
                    statistics.mean(row["ladder_coverage"] for row in event_rows),
                    6,
                )
                if event_rows
                else None
            ),
            "active_rebalance_event_share": (
                round(
                    sum(row["active_rebalance"] for row in event_rows)
                    / len(event_rows),
                    6,
                )
                if event_rows
                else None
            ),
        },
        "pit_expression": {
            "buy_cost_share_relative_to_running_max": {
                key: round(value / total_relative_cost, 6)
                if total_relative_cost
                else None
                for key, value in sorted(
                    relative_cost.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
            },
            "source_age_at_trade_min": {
                "n": len(source_ages),
                "median": (
                    round(statistics.median(source_ages), 6)
                    if source_ages
                    else None
                ),
                "share_le_10m": (
                    round(
                        sum(value <= 10 for value in source_ages)
                        / len(source_ages),
                        6,
                    )
                    if source_ages
                    else None
                ),
                "share_le_30m": (
                    round(
                        sum(value <= 30 for value in source_ages)
                        / len(source_ages),
                        6,
                    )
                    if source_ages
                    else None
                ),
            },
        },
        "automation": {
            "unique_transactions": len(tx_timestamps),
            "median_inter_tx_seconds": (
                round(statistics.median(gaps), 6) if gaps else None
            ),
            "share_inter_tx_le_2s": (
                round(sum(value <= 2 for value in gaps) / len(gaps), 6)
                if gaps
                else None
            ),
            "share_inter_tx_le_10s": (
                round(sum(value <= 10 for value in gaps) / len(gaps), 6)
                if gaps
                else None
            ),
            "max_transactions_per_minute": max(
                per_minute.values(),
                default=0,
            ),
            "public_trade_endpoint_rows": len(all_weather_trades),
            "public_taker_trade_endpoint_rows": len(taker_weather_trades),
            "maker_comparison_start_utc": (
                datetime.fromtimestamp(comparison_start, timezone.utc).isoformat()
                if comparison_start
                else None
            ),
            "maker_comparison_end_utc": (
                datetime.fromtimestamp(comparison_end, timezone.utc).isoformat()
                if comparison_end
                else None
            ),
            "maker_comparison_all_trade_rows": len(comparable_all),
            "maker_comparison_taker_trade_rows": len(comparable_taker),
            "maker_inferred_trade_rows": len(inferred_maker),
            "maker_inferred_trade_share": (
                round(len(inferred_maker) / len(comparable_all), 6)
                if comparable_all
                else None
            ),
            "public_trade_endpoint_error": trade_endpoint_error,
            "public_trade_endpoint_comparability": (
                "comparable"
                if comparable_all
                else "unavailable"
            ),
        },
        "sample_performance": _performance(settled),
        "events": sorted(
            event_rows,
            key=lambda row: (row["target_date"], row["city"]),
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--max-events", type=int, default=50)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = analyze(args.wallet.lower(), args.max_events)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "coverage": payload["coverage"],
                "structure": payload["structure"],
                "pit_expression": payload["pit_expression"],
                "automation": payload["automation"],
                "sample_performance": payload["sample_performance"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
