#!/usr/bin/env python3
"""Replay Gptball's Chengdu temperature trades against PIT observation state.

The public wallet is reconstructed at the mutually-exclusive city-day ladder
grain.  Individual YES/NO fills are retained for chronology, but strategy
classification and PnL are aggregated at the full event level.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics
from typing import Any
from zoneinfo import ZoneInfo

import requests

from research_external_wallet_strategy_v1 import (
    _activity_key,
    _activity_window,
    _city,
    _event_slug,
    _is_weather,
    _paged,
    _signed_cash,
    _target_date,
)
from research_wallet_event_portfolios_v1 import (
    event_metadata,
    event_portfolio,
)


WALLET = "0xcac70909a505ed6f28b1b59a79bcc99ff22937d8"
ZONE = ZoneInfo("Asia/Shanghai")
OBSERVATION_ROOT = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/observations"
)


def _utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(
        timezone.utc
    )


def _bracket_number(label: str) -> float | None:
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*°?C", label)
    return float(match.group(1)) if match else None


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
            return str(market.get("groupItemTitle") or market.get("question") or "")
    return None


def load_observation_states(target_dates: set[str]) -> dict[str, list[dict[str, Any]]]:
    states: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
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
                if row.get("city") != "Chengdu" or row.get("target_date") != target_date:
                    continue
                if row.get("status") != "ok":
                    continue
                seen = _utc(
                    row.get("observation_cache_generated_at_utc")
                    or row.get("fetched_at_utc")
                )
                if not seen:
                    continue
                states[target_date][seen.isoformat()] = {
                    "seen_ts_utc": seen.isoformat(),
                    "seen_ts_local": seen.astimezone(ZONE).isoformat(),
                    "last_obs_utc": row.get("last_obs_utc"),
                    "age_min": row.get("age_min"),
                    "current_temp_c": row.get("current_temp_c"),
                    "running_max_c": row.get("running_max_c"),
                    "minutes_since_running_max": row.get("minutes_since_running_max"),
                    "d_tmpf_1h": row.get("d_tmpf_1h"),
                    "d_tmpf_3h": row.get("d_tmpf_3h"),
                    "decline_c": row.get("decline_c"),
                    "dewpoint_depression_f": row.get("dewpoint_depression_f"),
                    "wind_dir_deg": row.get("wind_dir_deg"),
                    "wind_speed_kt": row.get("wind_speed_kt"),
                    "sky_cover_code": row.get("sky_cover_code"),
                    "precip_state": row.get("precip_state"),
                    "raw_metar": row.get("raw_metar"),
                    "source": row.get("source"),
                    "station": row.get("station"),
                    "source_path": str(path),
                }
    return {
        target_date: [by_ts[key] for key in sorted(by_ts)]
        for target_date, by_ts in states.items()
    }


def latest_state(
    states: dict[str, list[dict[str, Any]]],
    target_date: str,
    trade_ts: datetime,
) -> dict[str, Any] | None:
    rows = states.get(target_date) or []
    timestamps = [_utc(row["seen_ts_utc"]) for row in rows]
    index = bisect_right(timestamps, trade_ts) - 1
    if index < 0:
        return None
    state = dict(rows[index])
    seen = timestamps[index]
    state["snapshot_age_at_trade_min"] = round(
        (trade_ts - seen).total_seconds() / 60, 6
    )
    return state


def relative_expression(
    *,
    outcome: str,
    bracket: float | None,
    running_max: float | None,
) -> str:
    if bracket is None or running_max is None:
        return "unknown"
    distance = round(bracket - running_max, 6)
    relation = (
        "below_current"
        if distance < 0
        else "current"
        if distance == 0
        else f"d{int(distance)}"
        if float(distance).is_integer()
        else f"above_{distance:+g}"
    )
    return f"{relation}_{outcome.lower()}"


def cluster_episodes(rows: list[dict[str, Any]], gap_minutes: int = 30) -> list[dict[str, Any]]:
    episodes: list[list[dict[str, Any]]] = []
    for row in sorted(rows, key=lambda value: value["timestamp"]):
        if (
            not episodes
            or row["timestamp"] - episodes[-1][-1]["timestamp"] > gap_minutes * 60
        ):
            episodes.append([row])
        else:
            episodes[-1].append(row)
    packed = []
    for index, episode in enumerate(episodes, start=1):
        buys = [row for row in episode if row["side"] == "BUY"]
        sells = [row for row in episode if row["side"] == "SELL"]
        packed.append(
            {
                "episode": index,
                "start_local": episode[0]["ts_local"],
                "end_local": episode[-1]["ts_local"],
                "trade_rows": len(episode),
                "buy_cost": round(sum(row["usdc_size"] for row in buys), 6),
                "sell_proceeds": round(sum(row["usdc_size"] for row in sells), 6),
                "expressions": dict(
                    Counter(row["relative_expression"] for row in buys)
                ),
                "rows": episode,
            }
        )
    return packed


def analyze(
    *,
    wallet: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    session = requests.Session()
    raw_activity = _activity_window(
        session, wallet, int(start.timestamp()), int(end.timestamp()) + 1
    )
    activity = list(
        {
            _activity_key(row): row
            for row in raw_activity
            if _is_weather(row)
        }.values()
    )
    positions = [
        row
        for row in _paged(
            session,
            "/positions",
            wallet,
            limit=500,
            max_offset=10_000,
            extra={
                "sizeThreshold": 0,
                "sortBy": "TOKENS",
                "sortDirection": "DESC",
            },
        )
        if _is_weather(row)
    ]
    chengdu = [
        row
        for row in activity
        if _city(_event_slug(row)) == "chengdu"
    ]
    target_dates = {
        target.isoformat()
        for row in chengdu
        if (
            target := _target_date(
                _event_slug(row), int(row.get("timestamp") or 0)
            )
        )
    }
    observations = load_observation_states(target_dates)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in chengdu:
        grouped[_event_slug(row)].append(row)
    position_value: dict[str, float] = defaultdict(float)
    for row in positions:
        position_value[_event_slug(row)] += float(row.get("currentValue") or 0)

    event_rows = []
    aligned_buys = []
    transaction_timestamps: list[int] = []
    for slug, rows in sorted(grouped.items()):
        metadata = event_metadata(slug)
        if not metadata:
            continue
        labels = _market_labels(metadata)
        trades = [row for row in rows if row.get("type") == "TRADE"]
        timestamps = [int(row.get("timestamp") or 0) for row in trades]
        target = _target_date(slug, max(timestamps) if timestamps else 0)
        if not target:
            continue
        timeline = []
        for row in sorted(trades, key=lambda value: int(value.get("timestamp") or 0)):
            timestamp = int(row.get("timestamp") or 0)
            trade_ts = datetime.fromtimestamp(timestamp, timezone.utc)
            outcome = str(row.get("outcome") or "")
            label = labels.get(str(row.get("conditionId") or ""), str(row.get("title") or ""))
            state = latest_state(observations, target.isoformat(), trade_ts)
            running_max = (
                float(state["running_max_c"])
                if state and state.get("running_max_c") is not None
                else None
            )
            expression = relative_expression(
                outcome=outcome,
                bracket=_bracket_number(label),
                running_max=running_max,
            )
            packed = {
                "timestamp": timestamp,
                "ts_utc": trade_ts.isoformat(),
                "ts_local": trade_ts.astimezone(ZONE).isoformat(),
                "transaction_hash": row.get("transactionHash"),
                "side": row.get("side"),
                "outcome": outcome,
                "bracket": label,
                "price": float(row.get("price") or 0),
                "shares": float(row.get("size") or 0),
                "usdc_size": float(row.get("usdcSize") or 0),
                "relative_expression": expression,
                "pit_observation": state,
            }
            timeline.append(packed)
            if row.get("side") == "BUY":
                aligned_buys.append(packed)
            if row.get("transactionHash"):
                transaction_timestamps.append(timestamp)

        exact = event_portfolio(
            slug,
            rows,
            metadata,
            sample_oldest_ts=0,
        )
        cash_pnl = sum(_signed_cash(row) for row in rows) + position_value.get(slug, 0)
        exact.update(
            {
                "winner": _winner(metadata),
                "event_closed": bool(metadata.get("closed")),
                "public_cashflow_plus_inventory_pnl": round(cash_pnl, 6),
                "current_position_value": round(position_value.get(slug, 0), 6),
                "episodes": cluster_episodes(timeline),
                "timeline": timeline,
            }
        )
        event_rows.append(exact)

    covered = [row for row in aligned_buys if row["pit_observation"]]
    expression_cost: dict[str, float] = defaultdict(float)
    total_covered_cost = 0.0
    for row in covered:
        expression_cost[row["relative_expression"]] += row["usdc_size"]
        total_covered_cost += row["usdc_size"]

    unique_tx = sorted(set(transaction_timestamps))
    gaps = [
        current - previous
        for previous, current in zip(unique_tx, unique_tx[1:])
        if current >= previous
    ]
    minute_counts = Counter(timestamp // 60 for timestamp in unique_tx)
    source_age = [
        float(row["pit_observation"]["age_min"])
        + float(row["pit_observation"]["snapshot_age_at_trade_min"])
        for row in covered
        if row["pit_observation"].get("age_min") is not None
    ]
    all_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in activity:
        if _event_slug(row):
            all_grouped[_event_slug(row)].append(row)
    all_event_pnl = []
    for slug, rows in sorted(all_grouped.items()):
        metadata = event_metadata(slug)
        if not metadata:
            continue
        trades = [row for row in rows if row.get("type") == "TRADE"]
        timestamps = [int(row.get("timestamp") or 0) for row in trades]
        target = _target_date(slug, max(timestamps) if timestamps else 0)
        buys = [row for row in trades if row.get("side") == "BUY"]
        buy_cost = sum(float(row.get("usdcSize") or 0) for row in buys)
        yes_cost = sum(
            float(row.get("usdcSize") or 0)
            for row in buys
            if row.get("outcome") == "Yes"
        )
        no_cost = sum(
            float(row.get("usdcSize") or 0)
            for row in buys
            if row.get("outcome") == "No"
        )
        cash_pnl = sum(_signed_cash(row) for row in rows) + position_value.get(slug, 0)
        all_event_pnl.append(
            {
                "event_slug": slug,
                "city": _city(slug),
                "target_date": target.isoformat() if target else "",
                "winner": _winner(metadata),
                "buy_cost": round(buy_cost, 6),
                "yes_cost_share": round(yes_cost / buy_cost, 6)
                if buy_cost
                else None,
                "no_cost_share": round(no_cost / buy_cost, 6)
                if buy_cost
                else None,
                "pnl": round(cash_pnl, 6),
                "current_position_value": round(position_value.get(slug, 0), 6),
            }
        )
    settled_all = [row for row in all_event_pnl if row["winner"] and row["buy_cost"] > 0]
    settled_chengdu = [
        row for row in settled_all if row["city"] == "chengdu"
    ]

    def performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
        cost = sum(row["buy_cost"] for row in rows)
        pnl = sum(row["pnl"] for row in rows)
        by_date: dict[str, dict[str, float]] = defaultdict(
            lambda: {"cost": 0.0, "pnl": 0.0}
        )
        for row in rows:
            by_date[row["target_date"]]["cost"] += row["buy_cost"]
            by_date[row["target_date"]]["pnl"] += row["pnl"]
        blocks = list(by_date.values())
        bootstrap_roi: list[float] = []
        if blocks:
            import random

            rng = random.Random(20260729)
            for _ in range(10_000):
                sample = [rng.choice(blocks) for _ in blocks]
                sample_cost = sum(row["cost"] for row in sample)
                if sample_cost:
                    bootstrap_roi.append(
                        sum(row["pnl"] for row in sample) / sample_cost
                    )
            bootstrap_roi.sort()
        return {
            "events": len(rows),
            "target_dates": len(by_date),
            "buy_cost": round(cost, 6),
            "pnl": round(pnl, 6),
            "roi": round(pnl / cost, 6) if cost else None,
            "profitable_event_share": round(
                sum(row["pnl"] > 0 for row in rows) / len(rows), 6
            )
            if rows
            else None,
            "target_date_block_bootstrap_roi_ci95": (
                [
                    round(bootstrap_roi[int(0.025 * len(bootstrap_roi))], 6),
                    round(bootstrap_roi[int(0.975 * len(bootstrap_roi)) - 1], 6),
                ]
                if bootstrap_roi
                else None
            ),
        }

    return {
        "wallet": wallet,
        "snapshot_utc": end.isoformat(),
        "data_sources": {
            "wallet": "Polymarket Data API activity/positions",
            "event_ladders": "Gamma API complete event markets",
            "pit_weather": str(OBSERVATION_ROOT / "<target_date>/observations.jsonl"),
        },
        "coverage": {
            "weather_activity_rows": len(activity),
            "chengdu_activity_rows": len(chengdu),
            "chengdu_events": len(event_rows),
            "chengdu_target_dates": len(
                {row["target_date"] for row in event_rows if row["target_date"]}
            ),
            "observation_dates_available": sorted(observations),
            "buy_rows_with_pit_observation": len(covered),
            "buy_rows_total": sum(
                row.get("type") == "TRADE" and row.get("side") == "BUY"
                for row in chengdu
            ),
        },
        "pit_expression": {
            "covered_buy_cost": round(total_covered_cost, 6),
            "cost_by_relative_expression": {
                key: round(value, 6)
                for key, value in sorted(
                    expression_cost.items(), key=lambda item: item[1], reverse=True
                )
            },
            "cost_share_by_relative_expression": {
                key: round(value / total_covered_cost, 6)
                if total_covered_cost
                else None
                for key, value in sorted(
                    expression_cost.items(), key=lambda item: item[1], reverse=True
                )
            },
            "source_age_min": {
                "n": len(source_age),
                "median": round(statistics.median(source_age), 6)
                if source_age
                else None,
                "share_le_10m": round(
                    sum(value <= 10 for value in source_age) / len(source_age), 6
                )
                if source_age
                else None,
                "share_le_30m": round(
                    sum(value <= 30 for value in source_age) / len(source_age), 6
                )
                if source_age
                else None,
            },
        },
        "automation": {
            "unique_transactions": len(unique_tx),
            "median_inter_tx_seconds": round(statistics.median(gaps), 6)
            if gaps
            else None,
            "share_inter_tx_le_2s": round(
                sum(value <= 2 for value in gaps) / len(gaps), 6
            )
            if gaps
            else None,
            "share_inter_tx_le_10s": round(
                sum(value <= 10 for value in gaps) / len(gaps), 6
            )
            if gaps
            else None,
            "max_transactions_per_minute": max(minute_counts.values(), default=0),
        },
        "event_pnl": {
            "all_weather_settled": performance(settled_all),
            "chengdu_settled": performance(settled_chengdu),
            "settled_event_pnl": round(
                sum(
                    row["public_cashflow_plus_inventory_pnl"]
                    for row in event_rows
                    if row["winner"]
                ),
                6,
            ),
            "unsettled_or_unresolved_event_count": sum(
                not bool(row["winner"]) for row in event_rows
            ),
            "events": [
                {
                    "target_date": row["target_date"],
                    "winner": row["winner"],
                    "buy_cost": row["buy_cost"],
                    "pnl": row["public_cashflow_plus_inventory_pnl"],
                    "expression": row["expression"],
                }
                for row in event_rows
            ],
            "all_weather_events": all_event_pnl,
        },
        "events": sorted(
            event_rows, key=lambda row: (row["target_date"], row["event_slug"])
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wallet", default=WALLET)
    parser.add_argument("--start", default="2026-07-01")
    parser.add_argument("--end", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = (
        datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
        if args.end
        else datetime.now(timezone.utc)
    )
    payload = analyze(wallet=args.wallet.lower(), start=start, end=end)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "coverage": payload["coverage"],
                "pit_expression": payload["pit_expression"],
                "automation": payload["automation"],
                "event_pnl": payload["event_pnl"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
