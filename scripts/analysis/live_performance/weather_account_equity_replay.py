#!/usr/bin/env python3
"""Compare local weather live fills with Polymarket public account activity.

This is a read-only account-level reconciliation helper.  It does not submit
orders and does not use private CLOB credentials.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.weather_polymarket_account_activity import iter_activity  # noqa: E402


def parse_utc_date(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    parsed = parsed.astimezone(dt.timezone.utc)
    return parsed


def parse_ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def activity_ts(row: dict[str, Any]) -> dt.datetime | None:
    value = row.get("timestamp") or row.get("createdAt") or row.get("created_at")
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value = value / 1000.0
        return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
    return parse_ts(str(value))


def money(row: dict[str, Any]) -> float:
    for key in ("usdcSize", "amount", "value", "cashAmount", "tradeAmount"):
        try:
            val = float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            val = 0.0
        if val:
            return val
    try:
        return float(row.get("size") or 0.0) * float(row.get("price") or row.get("avgPrice") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def signed_cash(row: dict[str, Any]) -> float:
    typ = str(row.get("type") or row.get("eventType") or "").upper()
    side = str(row.get("side") or "").upper()
    value = money(row)
    if typ == "TRADE" and side == "BUY":
        return -value
    if typ == "TRADE" and side == "SELL":
        return value
    if typ in {"REDEEM", "MAKER_REBATE"}:
        return value
    return 0.0


def fetch_local_fills(db_path: Path, start: dt.datetime, end: dt.datetime) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = []
    for row in conn.execute(
        """
        SELECT
          fill_id, execution_id, order_id, token_id, condition_id, market_id,
          city, target_date, bracket, side, fill_ts_utc, fill_price, fill_qty,
          cost_usd, settlement_status, pnl_usd_at_fill, final_yes,
          strategy_id, execution_policy, entry_price_window
        FROM fact_trades
        WHERE trade_class='live_real'
          AND fill_ts_utc >= ?
          AND fill_ts_utc < ?
        ORDER BY fill_ts_utc
        """,
        (start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")),
    ):
        item = dict(row)
        item["_ts"] = parse_ts(item.get("fill_ts_utc"))
        item["_cost"] = float(item.get("cost_usd") or 0.0)
        rows.append(item)
    conn.close()
    return rows


def fetch_local_target_trades(db_path: Path, start_date: str, end_date: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = []
    for row in conn.execute(
        """
        SELECT
          fill_id, execution_id, order_id, token_id, condition_id, market_id,
          city, target_date, bracket, side, fill_ts_utc, fill_price, fill_qty,
          cost_usd, settlement_status, pnl_usd_at_fill, final_yes,
          strategy_id, execution_policy, entry_price_window
        FROM fact_trades
        WHERE trade_class='live_real'
          AND target_date >= ?
          AND target_date <= ?
        ORDER BY target_date, fill_ts_utc
        """,
        (start_date, end_date),
    ):
        item = dict(row)
        item["_cost"] = float(item.get("cost_usd") or 0.0)
        rows.append(item)
    conn.close()
    return rows


def fetch_all_local_tokens(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT token_id FROM fact_trades WHERE trade_class='live_real' AND token_id IS NOT NULL"
            )
            if row[0]
        }
    finally:
        conn.close()


def filter_activity(rows: list[dict[str, Any]], start: dt.datetime, end: dt.datetime) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        ts = activity_ts(row)
        if ts is None or not (start <= ts < end):
            continue
        item = dict(row)
        item["_ts"] = ts
        item["_money"] = money(row)
        item["_signed_cash"] = signed_cash(row)
        out.append(item)
    return out


def summarize_activity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, float]] = defaultdict(lambda: {"rows": 0, "money": 0.0, "signed_cash": 0.0})
    by_day: dict[str, float] = defaultdict(float)
    for row in rows:
        key = f"{str(row.get('type') or '').upper()}:{str(row.get('side') or '').upper()}"
        grouped[key]["rows"] += 1
        grouped[key]["money"] += float(row.get("_money") or 0.0)
        grouped[key]["signed_cash"] += float(row.get("_signed_cash") or 0.0)
        by_day[row["_ts"].date().isoformat()] += float(row.get("_signed_cash") or 0.0)
    return {
        "by_type_side": {
            key: {
                "rows": int(val["rows"]),
                "money": round(val["money"], 6),
                "signed_cash": round(val["signed_cash"], 6),
            }
            for key, val in sorted(grouped.items())
        },
        "signed_cash_delta": round(sum(float(row.get("_signed_cash") or 0.0) for row in rows), 6),
        "by_day_signed_cash": {key: round(val, 6) for key, val in sorted(by_day.items())},
    }


def summarize_local(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if row.get("settlement_status") == "settled"]
    by_fill_day: dict[str, float] = defaultdict(float)
    by_target_date: dict[str, float] = defaultdict(float)
    by_strategy: dict[str, dict[str, float]] = defaultdict(lambda: {"fills": 0, "cost_usd": 0.0, "settled_pnl_usd": 0.0})
    for row in rows:
        ts = parse_ts(row.get("fill_ts_utc"))
        if ts is not None:
            by_fill_day[ts.date().isoformat()] += float(row.get("_cost") or 0.0)
        target_date = str(row.get("target_date") or "")
        if target_date:
            by_target_date[target_date] += float(row.get("_cost") or 0.0)
        strategy = str(row.get("strategy_id") or row.get("execution_policy") or "unknown")
        by_strategy[strategy]["fills"] += 1
        by_strategy[strategy]["cost_usd"] += float(row.get("_cost") or 0.0)
        if row.get("settlement_status") == "settled":
            by_strategy[strategy]["settled_pnl_usd"] += float(row.get("pnl_usd_at_fill") or 0.0)
    return {
        "fills": len(rows),
        "distinct_fill_ids": len({row.get("fill_id") for row in rows if row.get("fill_id")}),
        "distinct_token_ids": len({row.get("token_id") for row in rows if row.get("token_id")}),
        "cost_usd": round(sum(float(row.get("_cost") or 0.0) for row in rows), 6),
        "settled_fills": len(settled),
        "settled_cost_usd": round(sum(float(row.get("_cost") or 0.0) for row in settled), 6),
        "settled_pnl_usd": round(sum(float(row.get("pnl_usd_at_fill") or 0.0) for row in settled), 6),
        "by_fill_day_cost": {key: round(val, 6) for key, val in sorted(by_fill_day.items())},
        "by_target_date_cost": {key: round(val, 6) for key, val in sorted(by_target_date.items())},
        "by_strategy": {
            key: {
                "fills": int(val["fills"]),
                "cost_usd": round(val["cost_usd"], 6),
                "settled_pnl_usd": round(val["settled_pnl_usd"], 6),
            }
            for key, val in sorted(by_strategy.items())
        },
    }


def match_local_to_activity(
    local_rows: list[dict[str, Any]],
    activity_rows: list[dict[str, Any]],
    *,
    seconds_tolerance: int,
    money_tolerance: float,
    all_local_tokens: set[str] | None = None,
) -> dict[str, Any]:
    trade_buys = [
        row
        for row in activity_rows
        if str(row.get("type") or "").upper() == "TRADE" and str(row.get("side") or "").upper() == "BUY"
    ]
    used: set[int] = set()
    matches = []
    unmatched_local = []
    for local in local_rows:
        token = str(local.get("token_id") or "")
        lts = local.get("_ts")
        if not token or lts is None:
            unmatched_local.append(local)
            continue
        candidates = []
        for idx, row in enumerate(trade_buys):
            if idx in used:
                continue
            if str(row.get("asset") or "") != token:
                continue
            dt_sec = abs((row["_ts"] - lts).total_seconds())
            if dt_sec > seconds_tolerance:
                continue
            money_diff = abs(float(row.get("_money") or 0.0) - float(local.get("_cost") or 0.0))
            if money_diff > money_tolerance:
                continue
            candidates.append((dt_sec, money_diff, idx, row))
        if not candidates:
            unmatched_local.append(local)
            continue
        _, _, idx, match = sorted(candidates, key=lambda x: (x[0], x[1]))[0]
        used.add(idx)
        matches.append(
            {
                "fill_id": local.get("fill_id"),
                "token_id": token,
                "local_ts": local.get("fill_ts_utc"),
                "poly_ts": match["_ts"].isoformat(),
                "local_cost": round(float(local.get("_cost") or 0.0), 6),
                "poly_money": round(float(match.get("_money") or 0.0), 6),
                "city": local.get("city"),
                "target_date": local.get("target_date"),
                "bracket": local.get("bracket"),
                "side": local.get("side"),
                "transactionHash": match.get("transactionHash"),
            }
        )
    unmatched_poly = [row for idx, row in enumerate(trade_buys) if idx not in used]
    unmatched_poly_by_day: dict[str, float] = defaultdict(float)
    unmatched_poly_weather = 0.0
    unmatched_poly_other = 0.0
    unmatched_poly_known_local_token = 0.0
    unmatched_poly_unknown_local_token = 0.0
    unmatched_poly_titles: dict[str, dict[str, Any]] = defaultdict(lambda: {"rows": 0, "money": 0.0})
    all_local_tokens = all_local_tokens or set()
    for row in unmatched_poly:
        text = " ".join([str(row.get("title") or ""), str(row.get("slug") or ""), str(row.get("eventSlug") or "")]).lower()
        is_weather = "temperature" in text or "weather" in text
        value = float(row.get("_money") or 0.0)
        unmatched_poly_by_day[row["_ts"].date().isoformat()] += value
        if is_weather:
            unmatched_poly_weather += value
        else:
            unmatched_poly_other += value
        if str(row.get("asset") or "") in all_local_tokens:
            unmatched_poly_known_local_token += value
        else:
            unmatched_poly_unknown_local_token += value
        title = str(row.get("title") or row.get("slug") or "unknown")
        unmatched_poly_titles[title]["rows"] += 1
        unmatched_poly_titles[title]["money"] += value
    top_unmatched_titles = sorted(
        (
            {"title": key, "rows": int(val["rows"]), "money": round(float(val["money"]), 6)}
            for key, val in unmatched_poly_titles.items()
        ),
        key=lambda row: float(row["money"]),
        reverse=True,
    )[:25]
    return {
        "matched": len(matches),
        "matched_local_cost": round(sum(float(row["local_cost"]) for row in matches), 6),
        "matched_poly_money": round(sum(float(row["poly_money"]) for row in matches), 6),
        "unmatched_local": len(unmatched_local),
        "unmatched_local_cost": round(sum(float(row.get("_cost") or 0.0) for row in unmatched_local), 6),
        "unmatched_poly_buys": len(unmatched_poly),
        "unmatched_poly_buy_money": round(sum(float(row.get("_money") or 0.0) for row in unmatched_poly), 6),
        "unmatched_poly_buy_weather_like_money": round(unmatched_poly_weather, 6),
        "unmatched_poly_buy_other_money": round(unmatched_poly_other, 6),
        "unmatched_poly_buy_known_local_token_money": round(unmatched_poly_known_local_token, 6),
        "unmatched_poly_buy_unknown_local_token_money": round(unmatched_poly_unknown_local_token, 6),
        "unmatched_poly_buy_by_day": {key: round(val, 6) for key, val in sorted(unmatched_poly_by_day.items())},
        "top_unmatched_poly_titles": top_unmatched_titles,
        "unmatched_local_sample": [
            {
                "fill_id": row.get("fill_id"),
                "token_id": row.get("token_id"),
                "fill_ts_utc": row.get("fill_ts_utc"),
                "cost_usd": round(float(row.get("_cost") or 0.0), 6),
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "side": row.get("side"),
            }
            for row in unmatched_local[:25]
        ],
        "unmatched_poly_sample": [
            {
                "asset": row.get("asset"),
                "ts_utc": row["_ts"].isoformat(),
                "money": round(float(row.get("_money") or 0.0), 6),
                "price": row.get("price"),
                "size": row.get("size"),
                "title": row.get("title"),
                "outcome": row.get("outcome"),
            }
            for row in unmatched_poly[:25]
        ],
    }


def asset_day_aggregate_gap(
    local_rows: list[dict[str, Any]],
    activity_rows: list[dict[str, Any]],
    *,
    all_local_tokens: set[str] | None = None,
) -> dict[str, Any]:
    """Compare local fills and public BUY activity at asset + UTC-day grain.

    This is a diagnostic for strict one-to-one match failures. It does not prove
    exact order-level reconciliation, but it shows whether the same token/date
    roughly balances after partial fills are aggregated.
    """
    all_local_tokens = all_local_tokens or set()
    local_by_key: dict[tuple[str, str], float] = defaultdict(float)
    poly_by_key: dict[tuple[str, str], float] = defaultdict(float)
    local_count_by_key: dict[tuple[str, str], int] = defaultdict(int)
    poly_count_by_key: dict[tuple[str, str], int] = defaultdict(int)
    local_meta_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    poly_meta_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in local_rows:
        token = str(row.get("token_id") or "")
        ts = row.get("_ts")
        if not token or ts is None:
            continue
        key = (token, ts.date().isoformat())
        local_by_key[key] += float(row.get("_cost") or 0.0)
        local_count_by_key[key] += 1
        local_meta_by_key.setdefault(
            key,
            {
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "bracket": row.get("bracket"),
                "side": row.get("side"),
            },
        )
    for row in activity_rows:
        if str(row.get("type") or "").upper() != "TRADE" or str(row.get("side") or "").upper() != "BUY":
            continue
        token = str(row.get("asset") or "")
        ts = row.get("_ts")
        if not token or ts is None:
            continue
        key = (token, ts.date().isoformat())
        poly_by_key[key] += float(row.get("_money") or 0.0)
        poly_count_by_key[key] += 1
        poly_meta_by_key.setdefault(
            key,
            {
                "title": row.get("title"),
                "outcome": row.get("outcome"),
            },
        )

    keys = sorted(set(local_by_key) | set(poly_by_key))
    rows = []
    for token, day in keys:
        local_cost = local_by_key.get((token, day), 0.0)
        poly_money = poly_by_key.get((token, day), 0.0)
        rows.append(
            {
                "asset": token,
                "date": day,
                "local_cost": round(local_cost, 6),
                "local_fills": local_count_by_key.get((token, day), 0),
                "poly_buy_money": round(poly_money, 6),
                "poly_buys": poly_count_by_key.get((token, day), 0),
                "poly_minus_local": round(poly_money - local_cost, 6),
                "known_local_token": token in all_local_tokens,
                "local_meta": local_meta_by_key.get((token, day)),
                "poly_meta": poly_meta_by_key.get((token, day)),
            }
        )
    poly_only = [row for row in rows if row["poly_buy_money"] and not row["local_cost"]]
    local_only = [row for row in rows if row["local_cost"] and not row["poly_buy_money"]]
    both = [row for row in rows if row["local_cost"] and row["poly_buy_money"]]
    top_abs_gaps = sorted(rows, key=lambda row: abs(float(row["poly_minus_local"])), reverse=True)[:25]
    return {
        "asset_day_keys": len(rows),
        "both_sides_keys": len(both),
        "local_total_cost": round(sum(float(row["local_cost"]) for row in rows), 6),
        "poly_total_buy_money": round(sum(float(row["poly_buy_money"]) for row in rows), 6),
        "net_poly_minus_local": round(sum(float(row["poly_minus_local"]) for row in rows), 6),
        "sum_abs_gap": round(sum(abs(float(row["poly_minus_local"])) for row in rows), 6),
        "poly_only_keys": len(poly_only),
        "poly_only_money": round(sum(float(row["poly_buy_money"]) for row in poly_only), 6),
        "poly_only_known_local_token_money": round(
            sum(float(row["poly_buy_money"]) for row in poly_only if row["known_local_token"]),
            6,
        ),
        "local_only_keys": len(local_only),
        "local_only_cost": round(sum(float(row["local_cost"]) for row in local_only), 6),
        "top_abs_gaps": top_abs_gaps,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="inclusive UTC date/datetime")
    parser.add_argument("--end", required=True, help="exclusive UTC date/datetime, date means YYYY-MM-DD 00:00")
    parser.add_argument("--target-start", default=None, help="inclusive target_date for settled day PnL")
    parser.add_argument("--target-end", default=None, help="inclusive target_date for settled day PnL")
    parser.add_argument("--db", type=Path, default=ROOT / "runtime" / "weather.db")
    parser.add_argument("--max-rows", type=int, default=10000)
    parser.add_argument("--seconds-tolerance", type=int, default=180)
    parser.add_argument("--money-tolerance", type=float, default=0.02)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    wallet = os.getenv("PM_ADDRESS", "").strip()
    if not wallet:
        raise SystemExit("missing PM_ADDRESS")

    start = parse_utc_date(args.start)
    end = parse_utc_date(args.end)
    all_activity = iter_activity(wallet, max_rows=args.max_rows)
    activity = filter_activity(all_activity, start, end)
    local_fills = fetch_local_fills(args.db, start, end)
    all_local_tokens = fetch_all_local_tokens(args.db)
    match = match_local_to_activity(
        local_fills,
        activity,
        seconds_tolerance=args.seconds_tolerance,
        money_tolerance=args.money_tolerance,
        all_local_tokens=all_local_tokens,
    )

    target_summary = None
    if args.target_start and args.target_end:
        target_rows = fetch_local_target_trades(args.db, args.target_start, args.target_end)
        target_summary = summarize_local(target_rows)

    payload = {
        "window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        "target_date_window": {"start": args.target_start, "end": args.target_end},
        "local_fill_window": summarize_local(local_fills),
        "local_target_window": target_summary,
        "polymarket_activity_window": summarize_activity(activity),
        "local_vs_polymarket_trade_buy_match": match,
        "asset_day_aggregate_gap": asset_day_aggregate_gap(
            local_fills,
            activity,
            all_local_tokens=all_local_tokens,
        ),
        "notes": [
            "Polymarket public activity has no local fill_id, so matching uses token_id/asset, timestamp, and notional tolerance.",
            "asset_day_aggregate_gap is a diagnostic only; it does not prove exact order-level reconciliation.",
            "REDEEM belongs to account cashflow and settlement payout timing; it is not one-to-one with fills in the same fill_date window.",
            "For daily-settled weather markets, target_date PnL should be read from fact_trades; account cash should be read from activity BUY/SELL/REDEEM/REBATE.",
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
