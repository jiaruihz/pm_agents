#!/usr/bin/env python3
"""Replay HeadA low-price YES maker TTL / fallback execution policies.

This research script answers a narrow execution question: did the current
maker-first HeadA probe miss profitable fills, and would a timed reprice/taker
fallback have improved results? It uses real live order rows, real CLOB fills,
canonical settlements, and time-aligned orderbook snapshots.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
ORDERS_PATH = ROOT / "runtime/weather_edge_v1/live/low_price_yes_lottery_tiny_live_v1_orders.jsonl"
FILLS_PATH = ROOT / "runtime/weather_edge_v1/clob_fills.jsonl"
DB_PATH = ROOT / "runtime/weather.db"
ORDERBOOK_ROOT = ROOT / "runtime/weather_edge_v1/market_data/orderbook_snapshots"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-05-low-price-yes-maker-fallback-v1.md"
GENERATED_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_maker_fallback_v1"
STRATEGY_ID = "low_price_yes_lottery_tiny_live_v1"

TTL_MINUTES = (10, 15, 30, 60, 120, 240, 360)
SPREAD_CAPS = (0.005, 0.01, 0.02, 0.03)
PRICE_CUSHIONS = (0.005, 0.01, 0.02)
FOCUS_POLICIES = {
    (15, 0.01, 0.01),
    (30, 0.01, 0.01),
    (60, 0.01, 0.01),
    (120, 0.01, 0.01),
    (120, 0.02, 0.02),
}


def parse_ts(value: Any) -> datetime | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_date(value: Any) -> date | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def json_ready(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    return value


def fmt_pct(value: Any) -> str:
    if value is None:
        return "NA"
    x = as_float(value, math.nan)
    return "NA" if not math.isfinite(x) else f"{x * 100:.1f}%"


def fmt_usd(value: Any) -> str:
    if value is None:
        return "NA"
    x = as_float(value, math.nan)
    return "NA" if not math.isfinite(x) else f"${x:.2f}"


def fmt_num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "NA"
    x = as_float(value, math.nan)
    return "NA" if not math.isfinite(x) else f"{x:.{digits}f}"


def order_id(row: dict[str, Any]) -> str:
    explicit = str(row.get("order_id") or "").strip()
    if explicit:
        return explicit
    exchange = row.get("exchange_response") if isinstance(row.get("exchange_response"), dict) else {}
    place = exchange.get("place") if isinstance(exchange.get("place"), dict) else {}
    return str(place.get("orderID") or place.get("order_id") or place.get("id") or "").strip()


def order_status(row: dict[str, Any]) -> str:
    explicit = str(row.get("status") or "").strip()
    if explicit:
        return explicit
    exchange = row.get("exchange_response") if isinstance(row.get("exchange_response"), dict) else {}
    place = exchange.get("place") if isinstance(exchange.get("place"), dict) else {}
    return str(place.get("status") or "").strip()


def level_rows(raw_levels: Any) -> list[dict[str, float]]:
    levels: list[dict[str, float]] = []
    if isinstance(raw_levels, list):
        for item in raw_levels:
            if not isinstance(item, dict):
                continue
            price = as_float(item.get("price"), math.nan)
            size = as_float(item.get("size"), math.nan)
            if math.isfinite(price) and math.isfinite(size) and price > 0 and size > 0:
                levels.append({"price": price, "size": size})
    return levels


def settlement_map(db_path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not db_path.exists():
        return out
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    for row in conn.execute(
        """
        SELECT city, target_date, bracket, final_price, settlement_status, created_at_utc
        FROM settlement_outcomes
        ORDER BY created_at_utc
        """
    ):
        out[(str(row["city"]), str(row["target_date"]), str(row["bracket"]))] = dict(row)
    conn.close()
    return out


def build_order_rows(now: datetime) -> list[dict[str, Any]]:
    orders = [
        row
        for row in read_jsonl(ORDERS_PATH)
        if str(row.get("record_type")) == "weather_edge_live_order"
        and str(row.get("strategy_id") or row.get("strategy_instance")) == STRATEGY_ID
        and bool(row.get("maker_only", False))
    ]
    fills_by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fill in read_jsonl(FILLS_PATH):
        oid = str(fill.get("order_id") or "").strip()
        if oid:
            fills_by_order[oid].append(fill)
    settlements = settlement_map(DB_PATH)

    rows: list[dict[str, Any]] = []
    for row in orders:
        oid = order_id(row)
        created = parse_ts(row.get("created_at_utc"))
        fills = []
        for fill in fills_by_order.get(oid, []):
            ts = parse_ts(fill.get("filled_at_utc"))
            shares = as_float(fill.get("filled_shares"), 0.0)
            price = as_float(fill.get("filled_price"), as_float(row.get("posted_price") or row.get("limit_price")))
            fee = as_float(fill.get("fees_usd"), 0.0)
            if ts and shares > 0 and price > 0:
                fills.append({"ts": ts, "shares": shares, "price": price, "fee": fee})
        fills.sort(key=lambda item: item["ts"])
        posted_price = as_float(row.get("posted_price") or row.get("limit_price") or row.get("requested_price"))
        posted_shares = as_float(row.get("size") or row.get("max_order_shares") or row.get("order_shares"))
        filled_shares = sum(fill["shares"] for fill in fills)
        fill_cost = sum(fill["shares"] * fill["price"] + fill["fee"] for fill in fills)
        fill_times = [fill["ts"] for fill in fills]
        first_fill_min = (fill_times[0] - created).total_seconds() / 60 if created and fill_times else None
        last_fill_min = (fill_times[-1] - created).total_seconds() / 60 if created and fill_times else None
        settlement = settlements.get((str(row.get("city")), str(row.get("target_date")), str(row.get("bracket"))), {})
        final_yes = as_float(settlement.get("final_price"), math.nan)
        settled = str(settlement.get("settlement_status") or "") == "settled" and math.isfinite(final_yes)
        rows.append(
            {
                "order_id": oid,
                "status": order_status(row),
                "created_at_utc": row.get("created_at_utc"),
                "created_dt": created,
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "target_date_dt": parse_date(row.get("target_date")),
                "bracket": row.get("bracket"),
                "token_id": str(row.get("token_id") or ""),
                "sizing_mode": row.get("sizing_mode"),
                "posted_price": posted_price,
                "posted_shares": posted_shares,
                "posted_notional": as_float(row.get("posted_notional") or row.get("notional"), posted_price * posted_shares),
                "quote_best_bid": as_float(row.get("quote_best_bid") or row.get("best_bid")),
                "quote_best_ask": as_float(row.get("quote_best_ask") or row.get("best_ask")),
                "quote_spread": as_float(row.get("quote_spread") or row.get("spread")),
                "quote_tick_size": as_float(row.get("quote_tick_size"), 0.001),
                "model_p_yes": as_float(row.get("model_p_yes_used") or row.get("model_token_probability")),
                "edge": as_float(row.get("edge_used_yes") or row.get("quote_edge")),
                "fills": fills,
                "fill_count": len(fills),
                "filled_shares": filled_shares,
                "fill_cost_usd": fill_cost,
                "fill_fraction": filled_shares / posted_shares if posted_shares > 0 else None,
                "any_fill": filled_shares > 1e-9,
                "full_fill": posted_shares > 0 and filled_shares >= posted_shares - 1e-6,
                "first_fill_wait_min": first_fill_min,
                "last_fill_wait_min": last_fill_min,
                "order_age_hours": (now - created).total_seconds() / 3600 if created else None,
                "settled": settled,
                "final_yes": final_yes if settled else None,
                "settlement_status": settlement.get("settlement_status") or "unsettled_or_missing",
                "actual_pnl_usd": (filled_shares * final_yes - fill_cost) if settled else None,
                "actual_missed_winner_shares": max(posted_shares - filled_shares, 0.0) if settled and final_yes >= 0.999 else 0.0,
                "actual_missed_winner_cost_usd": max(posted_shares - filled_shares, 0.0) * (1.0 - posted_price) if settled and final_yes >= 0.999 else 0.0,
            }
        )
    return rows


def iter_dates(start: date, end: date) -> list[date]:
    out: list[date] = []
    current = start
    while current <= end:
        out.append(current)
        current += timedelta(days=1)
    return out


def load_orderbook_series(order_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    wanted = {str(row.get("token_id") or "") for row in order_rows if row.get("token_id")}
    if not wanted:
        return {}
    min_date: date | None = None
    max_date: date | None = None
    for row in order_rows:
        created = row.get("created_dt")
        if isinstance(created, datetime):
            min_date = created.date() if min_date is None else min(min_date, created.date())
            max_date = created.date() if max_date is None else max(max_date, created.date())
        target = row.get("target_date_dt")
        if isinstance(target, date):
            end = target + timedelta(days=1)
            min_date = target if min_date is None else min(min_date, target)
            max_date = end if max_date is None else max(max_date, end)
    if min_date is None or max_date is None:
        return {}

    series: dict[str, list[dict[str, Any]]] = defaultdict(list)
    files_scanned = 0
    rows_matched = 0
    for day in iter_dates(min_date, max_date):
        folder = ORDERBOOK_ROOT / day.isoformat()
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.jsonl.gz")):
            files_scanned += 1
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    token_id = str(row.get("token_id") or "")
                    if token_id not in wanted:
                        continue
                    ts = parse_ts(row.get("snapshot_ts_utc") or row.get("fetched_at_utc"))
                    if not ts:
                        continue
                    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
                    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
                    asks = level_rows(raw.get("asks")) or level_rows(summary.get("asks"))
                    bids = level_rows(raw.get("bids")) or level_rows(summary.get("bids"))
                    asks.sort(key=lambda item: item["price"])
                    bids.sort(key=lambda item: item["price"], reverse=True)
                    best_ask = asks[0]["price"] if asks else as_float(summary.get("best_ask"), 0.0)
                    best_bid = bids[0]["price"] if bids else as_float(summary.get("best_bid"), 0.0)
                    series[token_id].append(
                        {
                            "snapshot_ts_utc": ts,
                            "snapshot_path": str(path.relative_to(ROOT)),
                            "best_ask": best_ask,
                            "best_bid": best_bid,
                            "spread": max(0.0, best_ask - best_bid) if best_ask > 0 and best_bid > 0 else None,
                            "ask_size": asks[0]["size"] if asks else as_float(summary.get("ask_size"), 0.0),
                            "bid_size": bids[0]["size"] if bids else as_float(summary.get("bid_size"), 0.0),
                            "asks": asks,
                            "bids": bids,
                        }
                    )
                    rows_matched += 1
    for token_series in series.values():
        token_series.sort(key=lambda item: item["snapshot_ts_utc"])
    load_orderbook_series.files_scanned = files_scanned  # type: ignore[attr-defined]
    load_orderbook_series.rows_matched = rows_matched  # type: ignore[attr-defined]
    return dict(series)


def first_snapshot_at_or_after(series: list[dict[str, Any]], cutoff: datetime) -> dict[str, Any] | None:
    for snap in series:
        if snap["snapshot_ts_utc"] >= cutoff:
            return snap
    return None


def latest_snapshot(series: list[dict[str, Any]]) -> dict[str, Any] | None:
    return series[-1] if series else None


def fills_before(order: dict[str, Any], cutoff: datetime) -> tuple[float, float, list[dict[str, Any]]]:
    selected = [fill for fill in order.get("fills", []) if fill["ts"] <= cutoff]
    shares = sum(fill["shares"] for fill in selected)
    cost = sum(fill["shares"] * fill["price"] + fill["fee"] for fill in selected)
    return shares, cost, selected


def taker_fee(price: float, shares: float, fee_rate: float) -> float:
    return max(0.0, shares * fee_rate * price * (1.0 - price))


def take_from_asks(asks: list[dict[str, float]], shares: float, max_price: float, fee_rate: float) -> dict[str, Any]:
    filled = 0.0
    cost = 0.0
    fees = 0.0
    max_level_price = 0.0
    for ask in asks:
        price = ask["price"]
        if price > max_price + 1e-9:
            break
        take = min(max(0.0, shares - filled), ask["size"])
        if take <= 0:
            break
        filled += take
        cost += take * price
        fees += taker_fee(price, take, fee_rate)
        max_level_price = max(max_level_price, price)
        if filled + 1e-9 >= shares:
            break
    return {
        "taker_filled_shares": filled,
        "taker_cost_usd": cost,
        "taker_fees_usd": fees,
        "taker_avg_price": cost / filled if filled > 0 else None,
        "taker_max_price_used": max_level_price if filled > 0 else None,
        "taker_full_fill": filled + 1e-9 >= shares,
    }


def simulate_policy(
    order: dict[str, Any],
    series: list[dict[str, Any]],
    *,
    ttl_min: int,
    spread_cap: float,
    price_cushion: float,
    taker_fee_rate: float,
) -> dict[str, Any]:
    created = order.get("created_dt")
    if not isinstance(created, datetime):
        return {"policy_status": "missing_created"}
    cutoff = created + timedelta(minutes=ttl_min)
    posted_price = as_float(order.get("posted_price"))
    posted_shares = as_float(order.get("posted_shares"))
    maker_pre_shares, maker_pre_cost, _pre_fills = fills_before(order, cutoff)
    remaining = max(0.0, posted_shares - maker_pre_shares)
    snap = first_snapshot_at_or_after(series, cutoff)
    baseline_cost = as_float(order.get("fill_cost_usd"))
    baseline_shares = as_float(order.get("filled_shares"))
    baseline_pnl = order.get("actual_pnl_usd")
    final_yes = order.get("final_yes")
    settled = bool(order.get("settled"))
    base = {
        "order_id": order.get("order_id"),
        "city": order.get("city"),
        "target_date": order.get("target_date"),
        "bracket": order.get("bracket"),
        "token_id": order.get("token_id"),
        "sizing_mode": order.get("sizing_mode"),
        "posted_price": posted_price,
        "posted_shares": posted_shares,
        "ttl_min": ttl_min,
        "spread_cap": spread_cap,
        "price_cushion": price_cushion,
        "settled": settled,
        "final_yes": final_yes,
        "maker_pre_shares": maker_pre_shares,
        "maker_pre_cost_usd": maker_pre_cost,
        "remaining_shares_at_ttl": remaining,
        "baseline_filled_shares": baseline_shares,
        "baseline_cost_usd": baseline_cost,
        "baseline_pnl_usd": baseline_pnl,
        "fallback_attempted": remaining > 1e-9,
        "snapshot_found": snap is not None,
    }
    if remaining <= 1e-9:
        policy_cost = baseline_cost
        policy_shares = baseline_shares
        policy_pnl = baseline_pnl
        return {
            **base,
            "policy_status": "already_filled_before_ttl",
            "fallback_eligible": False,
            "policy_filled_shares": policy_shares,
            "policy_cost_usd": policy_cost,
            "policy_pnl_usd": policy_pnl,
            "pnl_delta_usd": (policy_pnl - baseline_pnl) if settled and baseline_pnl is not None else None,
        }
    if snap is None:
        return {
            **base,
            "policy_status": "no_snapshot_after_ttl_keep_maker",
            "fallback_eligible": False,
            "policy_filled_shares": baseline_shares,
            "policy_cost_usd": baseline_cost,
            "policy_pnl_usd": baseline_pnl,
            "pnl_delta_usd": 0.0 if settled and baseline_pnl is not None else None,
        }
    best_ask = as_float(snap.get("best_ask"), 0.0)
    best_bid = as_float(snap.get("best_bid"), 0.0)
    spread = snap.get("spread")
    spread_value = as_float(spread, math.nan)
    max_taker_price = min(0.20, posted_price + price_cushion)
    eligible = (
        best_ask > 0
        and math.isfinite(spread_value)
        and spread_value <= spread_cap + 1e-9
        and best_ask <= max_taker_price + 1e-9
    )
    book_state = "eligible_taker" if eligible else "not_taker_eligible"
    if best_bid > posted_price + 1e-9:
        book_state = "maker_behind_better_bid"
    elif best_bid <= posted_price + 1e-9 and best_ask > posted_price + 1e-9:
        book_state = "maker_at_or_near_top_bid"
    if not eligible:
        return {
            **base,
            "policy_status": "keep_maker_fallback_not_eligible",
            "fallback_eligible": False,
            "snapshot_ts_utc": snap["snapshot_ts_utc"].isoformat(),
            "snapshot_best_bid": best_bid,
            "snapshot_best_ask": best_ask,
            "snapshot_spread": spread_value if math.isfinite(spread_value) else None,
            "max_taker_price": max_taker_price,
            "book_state_at_ttl": book_state,
            "policy_filled_shares": baseline_shares,
            "policy_cost_usd": baseline_cost,
            "policy_pnl_usd": baseline_pnl,
            "pnl_delta_usd": 0.0 if settled and baseline_pnl is not None else None,
        }
    take = take_from_asks(snap.get("asks", []), remaining, max_taker_price, taker_fee_rate)
    taker_shares = as_float(take.get("taker_filled_shares"))
    policy_shares = maker_pre_shares + taker_shares
    policy_cost = maker_pre_cost + as_float(take.get("taker_cost_usd")) + as_float(take.get("taker_fees_usd"))
    policy_pnl = (policy_shares * as_float(final_yes) - policy_cost) if settled else None
    return {
        **base,
        "policy_status": "fallback_taker" if taker_shares > 1e-9 else "eligible_but_no_depth",
        "fallback_eligible": True,
        "snapshot_ts_utc": snap["snapshot_ts_utc"].isoformat(),
        "snapshot_best_bid": best_bid,
        "snapshot_best_ask": best_ask,
        "snapshot_spread": spread_value,
        "max_taker_price": max_taker_price,
        "book_state_at_ttl": book_state,
        **take,
        "policy_filled_shares": policy_shares,
        "policy_cost_usd": policy_cost,
        "policy_pnl_usd": policy_pnl,
        "pnl_delta_usd": (policy_pnl - baseline_pnl) if settled and baseline_pnl is not None else None,
        "recovered_winner_shares": max(0.0, policy_shares - baseline_shares) if settled and as_float(final_yes) >= 0.999 else 0.0,
    }


def reprice_diagnostics(
    order: dict[str, Any],
    series: list[dict[str, Any]],
    *,
    ttl_min: int,
    price_cushion: float,
) -> dict[str, Any]:
    created = order.get("created_dt")
    if not isinstance(created, datetime):
        return {"status": "missing_created"}
    cutoff = created + timedelta(minutes=ttl_min)
    posted_price = as_float(order.get("posted_price"))
    posted_shares = as_float(order.get("posted_shares"))
    maker_pre_shares, _maker_pre_cost, _ = fills_before(order, cutoff)
    remaining = max(0.0, posted_shares - maker_pre_shares)
    snap = first_snapshot_at_or_after(series, cutoff)
    base = {
        "order_id": order.get("order_id"),
        "city": order.get("city"),
        "target_date": order.get("target_date"),
        "bracket": order.get("bracket"),
        "ttl_min": ttl_min,
        "price_cushion": price_cushion,
        "posted_price": posted_price,
        "remaining_shares_at_ttl": remaining,
        "settled": order.get("settled"),
        "final_yes": order.get("final_yes"),
    }
    if remaining <= 1e-9:
        return {**base, "status": "already_filled_before_ttl", "reprice_possible": False}
    if snap is None:
        return {**base, "status": "no_snapshot_after_ttl", "reprice_possible": False}
    best_bid = as_float(snap.get("best_bid"), 0.0)
    best_ask = as_float(snap.get("best_ask"), 0.0)
    tick = 0.001
    max_price = min(0.20, posted_price + price_cushion)
    cap_by_bid = best_bid + tick if best_bid > 0 else posted_price
    cap_by_ask = best_ask - tick if best_ask > 0 else max_price
    new_price = min(max_price, cap_by_bid, cap_by_ask)
    reprice_possible = new_price > posted_price + 1e-9 and new_price > 0
    future_touch = False
    if reprice_possible:
        for future in series:
            if future["snapshot_ts_utc"] <= snap["snapshot_ts_utc"]:
                continue
            future_best_ask = as_float(future.get("best_ask"), 0.0)
            if future_best_ask > 0 and future_best_ask <= new_price + 1e-9:
                future_touch = True
                break
    return {
        **base,
        "status": "checked",
        "snapshot_ts_utc": snap["snapshot_ts_utc"].isoformat(),
        "snapshot_best_bid": best_bid,
        "snapshot_best_ask": best_ask,
        "snapshot_spread": snap.get("spread"),
        "old_maker_behind_bid": best_bid > posted_price + 1e-9,
        "suggested_maker_price": new_price if reprice_possible else posted_price,
        "extra_price_paid_if_repriced": max(0.0, new_price - posted_price) if reprice_possible else 0.0,
        "reprice_possible": reprice_possible,
        "future_best_ask_touched_reprice": future_touch,
    }


def summarize_orders(order_rows: list[dict[str, Any]], orderbook_series: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    settled = [row for row in order_rows if row.get("settled")]
    cost = sum(as_float(row.get("fill_cost_usd")) for row in settled)
    pnl = sum(as_float(row.get("actual_pnl_usd")) for row in settled)
    waits = [row.get("first_fill_wait_min") for row in order_rows if row.get("first_fill_wait_min") is not None]
    long_wait = [row for row in order_rows if as_float(row.get("first_fill_wait_min"), -1.0) >= 60]
    return {
        "orders": len(order_rows),
        "settled_orders": len(settled),
        "open_orders": len(order_rows) - len(settled),
        "filled_orders": sum(1 for row in order_rows if row.get("any_fill")),
        "full_fill_orders": sum(1 for row in order_rows if row.get("full_fill")),
        "any_fill_rate": sum(1 for row in order_rows if row.get("any_fill")) / len(order_rows) if order_rows else None,
        "full_fill_rate": sum(1 for row in order_rows if row.get("full_fill")) / len(order_rows) if order_rows else None,
        "median_first_fill_wait_min": median(waits) if waits else None,
        "long_wait_fill_orders_60m_plus": len(long_wait),
        "long_wait_wins": sum(1 for row in long_wait if as_float(row.get("final_yes"), 0.0) >= 0.999),
        "settled_cost_usd": cost,
        "settled_pnl_usd": pnl,
        "settled_roi": pnl / cost if cost else None,
        "settled_wins": sum(1 for row in settled if as_float(row.get("final_yes")) >= 0.999),
        "actual_missed_winner_cost_usd": sum(as_float(row.get("actual_missed_winner_cost_usd")) for row in settled),
        "sizing_mode_counts": dict(Counter(str(row.get("sizing_mode") or "") for row in order_rows)),
        "tokens_with_orderbook_series": sum(1 for row in order_rows if orderbook_series.get(str(row.get("token_id") or ""))),
        "orderbook_files_scanned": getattr(load_orderbook_series, "files_scanned", 0),
        "orderbook_rows_matched": getattr(load_orderbook_series, "rows_matched", 0),
    }


def summarize_policies(policy_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, float, float], list[dict[str, Any]]] = defaultdict(list)
    for row in policy_rows:
        groups[(int(row["ttl_min"]), as_float(row["spread_cap"]), as_float(row["price_cushion"]))].append(row)
    out: list[dict[str, Any]] = []
    for (ttl, spread_cap, cushion), rows in sorted(groups.items()):
        settled = [row for row in rows if row.get("settled")]
        baseline_cost = sum(as_float(row.get("baseline_cost_usd")) for row in settled)
        baseline_pnl = sum(as_float(row.get("baseline_pnl_usd")) for row in settled)
        policy_cost = sum(as_float(row.get("policy_cost_usd")) for row in settled)
        policy_pnl = sum(as_float(row.get("policy_pnl_usd")) for row in settled)
        fallback_rows = [row for row in rows if row.get("policy_status") == "fallback_taker"]
        settled_fallback = [row for row in fallback_rows if row.get("settled")]
        out.append(
            {
                "ttl_min": ttl,
                "spread_cap": spread_cap,
                "price_cushion": cushion,
                "orders": len(rows),
                "settled_orders": len(settled),
                "fallback_attempted_orders": sum(1 for row in rows if row.get("fallback_attempted")),
                "fallback_taker_orders": len(fallback_rows),
                "settled_fallback_taker_orders": len(settled_fallback),
                "fallback_eligible_rate": len(fallback_rows) / len(rows) if rows else None,
                "taker_filled_shares_total": sum(as_float(row.get("taker_filled_shares")) for row in rows),
                "taker_cost_usd_total": sum(as_float(row.get("taker_cost_usd")) for row in rows),
                "taker_fees_usd_total": sum(as_float(row.get("taker_fees_usd")) for row in rows),
                "baseline_cost_usd": baseline_cost,
                "baseline_pnl_usd": baseline_pnl,
                "baseline_roi": baseline_pnl / baseline_cost if baseline_cost else None,
                "policy_cost_usd": policy_cost,
                "policy_pnl_usd": policy_pnl,
                "policy_roi": policy_pnl / policy_cost if policy_cost else None,
                "pnl_delta_usd": policy_pnl - baseline_pnl,
                "pnl_delta_per_settled_order_usd": (policy_pnl - baseline_pnl) / len(settled) if settled else None,
                "recovered_winner_shares": sum(as_float(row.get("recovered_winner_shares")) for row in settled),
                "status_counts": dict(Counter(str(row.get("policy_status") or "") for row in rows)),
            }
        )
    return out


def summarize_reprice(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, float], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(int(row["ttl_min"]), as_float(row["price_cushion"]))].append(row)
    out: list[dict[str, Any]] = []
    for (ttl, cushion), group in sorted(groups.items()):
        out.append(
            {
                "ttl_min": ttl,
                "price_cushion": cushion,
                "orders": len(group),
                "reprice_possible_orders": sum(1 for row in group if row.get("reprice_possible")),
                "old_maker_behind_bid_orders": sum(1 for row in group if row.get("old_maker_behind_bid")),
                "future_best_ask_touched_reprice_orders": sum(1 for row in group if row.get("future_best_ask_touched_reprice")),
                "mean_extra_price_if_repriced": (
                    sum(as_float(row.get("extra_price_paid_if_repriced")) for row in group if row.get("reprice_possible"))
                    / max(1, sum(1 for row in group if row.get("reprice_possible")))
                ),
                "status_counts": dict(Counter(str(row.get("status") or "") for row in group)),
            }
        )
    return out


def compact_order_rows(order_rows: list[dict[str, Any]], orderbook_series: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in order_rows:
        series = orderbook_series.get(str(row.get("token_id") or ""), [])
        latest = latest_snapshot(series)
        rows.append(
            {
                "created_at_utc": row.get("created_at_utc"),
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "bracket": row.get("bracket"),
                "sizing_mode": row.get("sizing_mode"),
                "status": row.get("status"),
                "posted_price": row.get("posted_price"),
                "posted_shares": row.get("posted_shares"),
                "filled_shares": row.get("filled_shares"),
                "fill_fraction": row.get("fill_fraction"),
                "first_fill_wait_min": row.get("first_fill_wait_min"),
                "last_fill_wait_min": row.get("last_fill_wait_min"),
                "settled": row.get("settled"),
                "final_yes": row.get("final_yes"),
                "actual_pnl_usd": row.get("actual_pnl_usd"),
                "latest_book_ts_utc": latest["snapshot_ts_utc"].isoformat() if latest else "",
                "latest_best_bid": latest.get("best_bid") if latest else None,
                "latest_best_ask": latest.get("best_ask") if latest else None,
                "latest_spread": latest.get("spread") if latest else None,
            }
        )
    return rows


def write_report(
    *,
    generated_at: datetime,
    order_rows: list[dict[str, Any]],
    order_summary: dict[str, Any],
    policy_summary: list[dict[str, Any]],
    reprice_summary: list[dict[str, Any]],
    focus_rows: list[dict[str, Any]],
) -> None:
    focus_summary = [
        row
        for row in policy_summary
        if (int(row["ttl_min"]), as_float(row["spread_cap"]), as_float(row["price_cushion"])) in FOCUS_POLICIES
    ]
    order_rows_compact = compact_order_rows(order_rows, orderbook_series_global)
    lines = [
        "# Low-Price YES Maker Fallback v1",
        "",
        f"Generated: `{generated_at.isoformat()}`",
        "",
        "## Verdict",
        "",
        "`forecast_tail_low_price_yes` maker-first has non-fill and long-wait behavior, but the current settled evidence does **not** support a blanket timed taker fallback.",
        "",
        "The useful signal is more specific: when a maker order sits unfilled and the book moves down sharply, the old maker order can become a stale overbid. "
        "In the current small sample, TTL refresh would have helped mainly by buying one loser much cheaper or by avoiding later stale fills, not by rescuing missed winners.",
        "",
        "Recommended action: keep current tiny maker-first live behavior unchanged for now; add a shadow lifecycle ledger for TTL refresh/reprice/taker fallback. "
        "A live fallback can be reconsidered only after forward rows show whether the improvement comes from genuine fill rescue, cheaper re-entry, or accidental loser underfill.",
        "",
        "```text",
        "significance=NA",
        "baseline=PASS (same live orders vs maker-only realized baseline)",
        "forward=FAIL (fresh settled sample too small; 7/05 orders still open)",
        "conclusion=inconclusive_execution_overlay",
        "```",
        "",
        "## Data Snapshot",
        "",
        f"- Live maker order rows: {order_summary['orders']} ({order_summary['settled_orders']} settled, {order_summary['open_orders']} open).",
        f"- Real maker fill coverage: any-fill {fmt_pct(order_summary['any_fill_rate'])}; full-fill {fmt_pct(order_summary['full_fill_rate'])}; median first fill {fmt_num(order_summary['median_first_fill_wait_min'], 1)} min.",
        f"- Settled maker-only actual ROI: {fmt_pct(order_summary['settled_roi'])} ({fmt_usd(order_summary['settled_pnl_usd'])} on {fmt_usd(order_summary['settled_cost_usd'])}); wins {order_summary['settled_wins']}/{order_summary['settled_orders']}.",
        f"- Actual missed-winner cost from unfilled winning shares: {fmt_usd(order_summary['actual_missed_winner_cost_usd'])}.",
        f"- Long-wait fills >=60m: {order_summary['long_wait_fill_orders_60m_plus']} orders; winners among them: {order_summary['long_wait_wins']}.",
        f"- Orderbook replay: {order_summary['tokens_with_orderbook_series']}/{order_summary['orders']} order tokens matched; {order_summary['orderbook_rows_matched']} book rows from {order_summary['orderbook_files_scanned']} files. Unmatched open rows are data gaps, not strategy evidence.",
        f"- Sizing modes: `{order_summary['sizing_mode_counts']}`.",
        "",
        "## TTL Taker Fallback Grid",
        "",
        "Policy definition: keep the maker order until TTL; if remaining shares are unfilled and the first book snapshot after TTL has `spread<=cap` and `best_ask<=posted_price+cushion` (also capped at 20c), buy the remaining shares as taker. Taker fees use the official weather formula `shares * 0.05 * price * (1-price)`. If the condition is not met, keep maker-only behavior.",
        "",
        "| TTL | Spread cap | Cushion | Fallback orders | Settled fallback | Taker cost | Fees | Baseline ROI | Policy ROI | PnL delta |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in focus_summary:
        lines.append(
            f"| {row['ttl_min']}m | {row['spread_cap']:.3f} | {row['price_cushion']:.3f} | "
            f"{row['fallback_taker_orders']}/{row['orders']} | {row['settled_fallback_taker_orders']} | "
            f"{fmt_usd(row['taker_cost_usd_total'])} | {fmt_usd(row['taker_fees_usd_total'])} | "
            f"{fmt_pct(row['baseline_roi'])} | {fmt_pct(row['policy_roi'])} | {fmt_usd(row['pnl_delta_usd'])} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- There are maker-driven non-fills and long waits: current price-tier live has open/unfilled rows, and several older maker fills waited hours.",
            "- The settled sample does not show profitable missed winners. `actual_missed_winner_cost` is zero because the only settled winner filled quickly.",
            "- The positive 15/30/60m point estimates are mostly a stale-overbid / cheaper-re-entry effect, not proof that sweeping faster finds winners.",
            "- Naive fast fallback is risky for this sleeve: when the book has not moved down, taker fallback simply pays spread and fee on the same lottery ticket.",
            "- Repricing maker is less dangerous than taker fallback, but historical book snapshots cannot prove queue fills. Treat reprice as telemetry until live cancel/repost logs exist.",
            "",
            "## Driver Cases",
            "",
            "Rows below are the actual fallback triggers inside the focused 15/30/60/120 minute policies. They show why this is a lifecycle problem rather than a simple taker switch.",
            "",
            "| TTL | City | Date | Bracket | Posted | TTL ask | Spread | Final | Baseline PnL | Policy PnL | Delta |",
            "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    driver_rows = [
        row
        for row in focus_rows
        if row.get("policy_status") == "fallback_taker"
        and int(row.get("ttl_min", 0)) in {15, 30, 60, 120}
        and as_float(row.get("spread_cap")) == 0.01
        and as_float(row.get("price_cushion")) == 0.01
    ]
    for row in driver_rows[:12]:
        lines.append(
            f"| {row['ttl_min']}m | {row['city']} | {row['target_date']} | {row['bracket']} | "
            f"{fmt_num(row['posted_price'], 3)} | {fmt_num(row.get('snapshot_best_ask'), 3)} | "
            f"{fmt_num(row.get('snapshot_spread'), 3)} | {fmt_num(row.get('final_yes'), 0)} | "
            f"{fmt_usd(row.get('baseline_pnl_usd'))} | {fmt_usd(row.get('policy_pnl_usd'))} | {fmt_usd(row.get('pnl_delta_usd'))} |"
        )
    lines.extend(
        [
            "",
            "## Reprice Diagnostics",
            "",
            "| TTL | Cushion | Reprice possible | Old maker behind bid | Future ask touched reprice | Mean extra price |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in reprice_summary:
        if int(row["ttl_min"]) not in {30, 60, 120} or as_float(row["price_cushion"]) not in {0.01, 0.02}:
            continue
        lines.append(
            f"| {row['ttl_min']}m | {row['price_cushion']:.3f} | {row['reprice_possible_orders']}/{row['orders']} | "
            f"{row['old_maker_behind_bid_orders']} | {row['future_best_ask_touched_reprice_orders']} | "
            f"{fmt_num(row['mean_extra_price_if_repriced'], 3)} |"
        )
    lines.extend(
        [
            "",
            "## Order Rows",
            "",
            "| Created UTC | City | Date | Bracket | Mode | Price | Shares | Filled | First fill | Settled | Final | Latest bid/ask |",
            "|---|---|---|---:|---|---:|---:|---:|---:|---|---:|---:|",
        ]
    )
    for row in order_rows_compact:
        lines.append(
            f"| {row['created_at_utc']} | {row['city']} | {row['target_date']} | {row['bracket']} | {row['sizing_mode']} | "
            f"{fmt_num(row['posted_price'], 3)} | {fmt_num(row['posted_shares'], 2)} | {fmt_num(row['filled_shares'], 2)} | "
            f"{fmt_num(row['first_fill_wait_min'], 1)} | {row['settled']} | {fmt_num(row['final_yes'], 0)} | "
            f"{fmt_num(row['latest_best_bid'], 3)}/{fmt_num(row['latest_best_ask'], 3)} |"
        )
    lines.extend(
        [
            "",
            "## Candidate Live Change",
            "",
            "Do **not** enable blanket taker fallback from this report. The safer next implementation is shadow-only lifecycle tracking:",
            "",
            "- every maker order gets a virtual TTL ladder at 15/30/60/120 minutes;",
            "- log whether the book moved down, the maker is behind bid, a reprice is possible, or a taker fallback is actually tight enough;",
            "- after settlement, score `saved_missed_winner`, `cheaper_reentry_saved_cost`, `paid_extra_loser`, `fee_paid`, and `pnl_delta_vs_maker_only`;",
            "- only promote if forward settled deltas are positive on the same real orders.",
            "",
            "If a tiny live fallback is later approved, the least aggressive candidate is a three-way TTL refresh rather than a blind sweep: cancel/repost lower when the book moved down, reprice maker when the old order is behind bid, and taker only when `spread<=1c`, `best_ask<=posted_price+1c`, the 20c hard band still holds, and official weather taker fee leaves edge. Current evidence is not enough to turn this on.",
            "",
            "## Artifacts",
            "",
            "- `docs/analysis/2026-07/generated/low_price_yes_maker_fallback_v1/order_lifecycle_rows.csv`",
            "- `docs/analysis/2026-07/generated/low_price_yes_maker_fallback_v1/fallback_policy_rows.csv`",
            "- `docs/analysis/2026-07/generated/low_price_yes_maker_fallback_v1/fallback_policy_summary.csv`",
            "- `docs/analysis/2026-07/generated/low_price_yes_maker_fallback_v1/reprice_diagnostics.csv`",
            "- `docs/analysis/2026-07/generated/low_price_yes_maker_fallback_v1/summary.json`",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


orderbook_series_global: dict[str, list[dict[str, Any]]] = {}


def main() -> int:
    global ORDERS_PATH, FILLS_PATH, DB_PATH, ORDERBOOK_ROOT, REPORT_PATH, GENERATED_DIR, orderbook_series_global
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=Path, default=ORDERS_PATH)
    parser.add_argument("--fills", type=Path, default=FILLS_PATH)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--orderbook-root", type=Path, default=ORDERBOOK_ROOT)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--generated-dir", type=Path, default=GENERATED_DIR)
    parser.add_argument("--taker-fee-rate", type=float, default=0.05)
    args = parser.parse_args()
    ORDERS_PATH = args.orders
    FILLS_PATH = args.fills
    DB_PATH = args.db
    ORDERBOOK_ROOT = args.orderbook_root
    REPORT_PATH = args.report
    GENERATED_DIR = args.generated_dir

    generated_at = datetime.now(timezone.utc)
    order_rows = build_order_rows(generated_at)
    orderbook_series_global = load_orderbook_series(order_rows)

    policy_rows: list[dict[str, Any]] = []
    for order in order_rows:
        series = orderbook_series_global.get(str(order.get("token_id") or ""), [])
        for ttl in TTL_MINUTES:
            for spread_cap in SPREAD_CAPS:
                for cushion in PRICE_CUSHIONS:
                    policy_rows.append(
                        simulate_policy(
                            order,
                            series,
                            ttl_min=ttl,
                            spread_cap=spread_cap,
                            price_cushion=cushion,
                            taker_fee_rate=args.taker_fee_rate,
                        )
                    )

    reprice_rows: list[dict[str, Any]] = []
    for order in order_rows:
        series = orderbook_series_global.get(str(order.get("token_id") or ""), [])
        for ttl in TTL_MINUTES:
            for cushion in PRICE_CUSHIONS:
                reprice_rows.append(reprice_diagnostics(order, series, ttl_min=ttl, price_cushion=cushion))

    policy_summary = summarize_policies(policy_rows)
    reprice_summary = summarize_reprice(reprice_rows)
    order_summary = summarize_orders(order_rows, orderbook_series_global)
    order_lifecycle_rows = compact_order_rows(order_rows, orderbook_series_global)
    focus_rows = [
        row
        for row in policy_rows
        if (int(row["ttl_min"]), as_float(row["spread_cap"]), as_float(row["price_cushion"])) in FOCUS_POLICIES
    ]

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(GENERATED_DIR / "order_lifecycle_rows.csv", order_lifecycle_rows)
    write_csv(GENERATED_DIR / "fallback_policy_rows.csv", policy_rows)
    write_csv(GENERATED_DIR / "fallback_policy_summary.csv", policy_summary)
    write_csv(GENERATED_DIR / "reprice_diagnostics.csv", reprice_rows)
    write_csv(GENERATED_DIR / "reprice_summary.csv", reprice_summary)
    output = {
        "generated_at_utc": generated_at.isoformat(),
        "source_files": {
            "orders": str(ORDERS_PATH.relative_to(ROOT)),
            "fills": str(FILLS_PATH.relative_to(ROOT)),
            "db": str(DB_PATH.relative_to(ROOT)),
            "orderbook_root": str(ORDERBOOK_ROOT.relative_to(ROOT)),
        },
        "taker_fee_rate": args.taker_fee_rate,
        "order_summary": order_summary,
        "focus_policies": sorted(list(FOCUS_POLICIES)),
        "policy_summary": policy_summary,
        "reprice_summary": reprice_summary,
    }
    (GENERATED_DIR / "summary.json").write_text(json.dumps(json_ready(output), indent=2, sort_keys=True), encoding="utf-8")
    write_report(
        generated_at=generated_at,
        order_rows=order_rows,
        order_summary=order_summary,
        policy_summary=policy_summary,
        reprice_summary=reprice_summary,
        focus_rows=focus_rows,
    )
    print(
        json.dumps(
            {
                "report": str(REPORT_PATH.relative_to(ROOT)),
                "orders": len(order_rows),
                "policy_rows": len(policy_rows),
                "orderbook_rows_matched": order_summary["orderbook_rows_matched"],
                "generated_dir": str(GENERATED_DIR.relative_to(ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
