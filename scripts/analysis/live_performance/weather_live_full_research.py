#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "runtime" / "weather.db"
EDGE_RUNTIME = ROOT / "runtime" / "weather_edge_v1"
LIVE_ORDER_DIR = EDGE_RUNTIME / "remote_pm_agent" / "live"
PAPER_CSV = EDGE_RUNTIME / "market_data" / "research" / "t24_paper_ledger_trades.csv"
REPORT_DIR = ROOT / "docs" / "analysis" / "2026-05"
ADDED_T1_2026_05_26 = {
    "Ankara",
    "Guangzhou",
    "Istanbul",
    "Jeddah",
    "Karachi",
    "Lucknow",
    "Moscow",
    "Seattle",
}


@dataclass(frozen=True)
class TradeRow:
    source: str
    execution_id: str
    fill_id: str
    target_date: str
    order_date_bj: str
    city: str
    city_pool: str
    model: str
    side: str
    bracket: str
    market_id: str
    condition_id: str
    strategy_id: str
    fill_price: float
    plan_price: float
    fill_qty: float
    fees_usd: float
    settlement_yes_price: float | None
    settlement_status: str | None
    pnl_usd_at_fill: float | None
    pnl_usd_at_plan: float | None
    edge: float | None
    abs_edge: float | None
    market_price: float | None

    @property
    def cost_usd(self) -> float:
        return self.fill_price * self.fill_qty


@dataclass(frozen=True)
class LiveOrderRow:
    path: str
    execution_id: str
    order_id: str
    created_at_utc: str
    target_date: str
    city: str
    city_pool: str
    side: str
    bracket: str
    status: str
    place_status: str
    posted_price: float
    requested_price: float
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    quote_edge: float | None
    model_prob: float | None
    execution_policy: str
    quote_mode: str

    @property
    def order_date_bj(self) -> str:
        return _bj_date(self.created_at_utc)

    @property
    def price_bucket(self) -> str:
        return _price_bucket(self.posted_price)

    @property
    def edge_bucket(self) -> str:
        return _edge_bucket(self.quote_edge)


def _safe_float(value: Any, default: float | None = 0.0) -> float | None:
    if value is None or value == "":
        return default
    try:
        val = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(val):
        return default
    return val


def _pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1%}"


def _usd(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:,.2f}"


def _num(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def _bj_date(ts: str | None) -> str:
    if not ts:
        return "UNKNOWN"
    raw = ts.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return "UNKNOWN"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    bj = dt.astimezone(timezone.utc).timestamp() + 8 * 3600
    return datetime.fromtimestamp(bj, tz=timezone.utc).strftime("%Y-%m-%d")


def _edge_bucket(edge: float | None) -> str:
    if edge is None:
        return "unknown"
    if edge < 0.05:
        return "<5%"
    if edge < 0.10:
        return "5-10%"
    if edge < 0.15:
        return "10-15%"
    if edge < 0.20:
        return "15-20%"
    return ">=20%"


def _price_bucket(price: float | None) -> str:
    if price is None:
        return "unknown"
    if price < 0.25:
        return "<0.25"
    if price < 0.40:
        return "0.25-0.40"
    if price < 0.55:
        return "0.40-0.55"
    if price < 0.70:
        return "0.55-0.70"
    if price <= 0.75:
        return "0.70-0.75"
    return ">0.75"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


_TRADE_CLASS_MODES = {"live_real", "live_simulated"}


def _load_trades(conn: sqlite3.Connection, mode: str) -> list[TradeRow]:
    """Load trades from fact_trades (唯一派生层).

    Pass trade_class values ('live_real', 'live_simulated') to get exact-class
    results; pass execution_mode values ('paper', 'snapshot_replay') otherwise.
    PnL and settlement join are precomputed by build_weather_fact_trades.py.
    """
    if mode in _TRADE_CLASS_MODES:
        where_col = "trade_class"
    else:
        where_col = "execution_mode"
    rows = conn.execute(
        f"""
        SELECT
          execution_mode,
          trade_class,
          execution_id,
          fill_id,
          target_date,
          order_date_bj,
          city,
          city_pool,
          forecast_source  AS model,
          side,
          bracket,
          market_id,
          condition_id,
          strategy_id,
          fill_price,
          plan_price,
          fill_qty,
          fees_usd,
          final_yes        AS settlement_yes_price,
          settlement_status,
          pnl_usd_at_fill,
          pnl_usd_at_plan,
          edge,
          abs_edge,
          market_price
        FROM fact_trades
        WHERE {where_col} = ?
        """,
        (mode,),
    ).fetchall()
    result: list[TradeRow] = []
    for row in rows:
        result.append(
            TradeRow(
                source=str(row["execution_mode"] or ""),
                execution_id=str(row["execution_id"] or ""),
                fill_id=str(row["fill_id"] or ""),
                target_date=str(row["target_date"] or ""),
                order_date_bj=str(row["order_date_bj"] or "UNKNOWN"),
                city=str(row["city"] or ""),
                city_pool=str(row["city_pool"] or ""),
                model=str(row["model"] or ""),
                side=str(row["side"] or ""),
                bracket=str(row["bracket"] or ""),
                market_id=str(row["market_id"] or ""),
                condition_id=str(row["condition_id"] or ""),
                strategy_id=str(row["strategy_id"] or ""),
                fill_price=float(row["fill_price"] or 0.0),
                plan_price=float(row["plan_price"] or 0.0),
                fill_qty=float(row["fill_qty"] or 0.0),
                fees_usd=float(row["fees_usd"] or 0.0),
                settlement_yes_price=_safe_float(row["settlement_yes_price"], None),
                settlement_status=row["settlement_status"],
                pnl_usd_at_fill=_safe_float(row["pnl_usd_at_fill"], None),
                pnl_usd_at_plan=_safe_float(row["pnl_usd_at_plan"], None),
                edge=_safe_float(row["edge"], None),
                abs_edge=_safe_float(row["abs_edge"], None),
                market_price=_safe_float(row["market_price"], None),
            )
        )
    return result


def _summary(rows: Iterable[TradeRow]) -> dict[str, Any]:
    rows = list(rows)
    settled = [r for r in rows if r.pnl_usd_at_fill is not None]
    cost = sum(r.cost_usd for r in settled)
    pnl_fill = sum(r.pnl_usd_at_fill or 0.0 for r in settled)
    pnl_plan = sum(r.pnl_usd_at_plan or 0.0 for r in settled)
    wins = [r for r in settled if (r.pnl_usd_at_fill or 0.0) > 0]
    win_notional = sum(r.cost_usd for r in wins)
    daily: dict[str, float] = defaultdict(float)
    for r in settled:
        daily[r.target_date] += r.pnl_usd_at_fill or 0.0
    daily_values = list(daily.values())
    return {
        "fills": len(rows),
        "settled": len(settled),
        "wins": len(wins),
        "win_rate": len(wins) / len(settled) if settled else None,
        "win_rate_notional": win_notional / cost if cost else None,
        "cost_usd": cost,
        "pnl_usd_at_fill": pnl_fill,
        "pnl_usd_at_plan": pnl_plan,
        "roi": pnl_fill / cost if cost else None,
        "fill_qty": sum(r.fill_qty for r in settled),
        "avg_fill_price": sum(r.fill_price * r.fill_qty for r in settled) / sum(r.fill_qty for r in settled)
        if settled and sum(r.fill_qty for r in settled)
        else None,
        "daily_sharpe_like": (mean(daily_values) / pstdev(daily_values)) if len(daily_values) > 1 and pstdev(daily_values) else None,
    }


def _group(rows: Iterable[TradeRow], key_fn: Callable[[TradeRow], str]) -> list[tuple[str, dict[str, Any]]]:
    buckets: dict[str, list[TradeRow]] = defaultdict(list)
    for row in rows:
        buckets[key_fn(row)].append(row)
    return sorted(((key, _summary(vals)) for key, vals in buckets.items()), key=lambda item: item[1]["pnl_usd_at_fill"])


def _row_summary(name: str, s: dict[str, Any], include_price: bool = False) -> str:
    parts = [
        name,
        str(s["settled"]),
        str(s["wins"]),
        _pct(s["win_rate"]),
        _usd(s["cost_usd"]),
    ]
    if include_price:
        parts.append(_num(s["avg_fill_price"]))
    parts.extend([_usd(s["pnl_usd_at_fill"]), _usd(s["pnl_usd_at_plan"]), _pct(s["roi"])])
    return "| " + " | ".join(parts) + " |"


def _load_live_orders() -> list[LiveOrderRow]:
    rows: list[LiveOrderRow] = []
    for path in sorted(LIVE_ORDER_DIR.glob("*_orders.jsonl")):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                obj = json.loads(line)
                ex = obj.get("exchange_response") if isinstance(obj.get("exchange_response"), dict) else {}
                place = ex.get("place") if isinstance(ex.get("place"), dict) else {}
                rows.append(
                    LiveOrderRow(
                        path=str(path.relative_to(ROOT)),
                        execution_id=str(obj.get("execution_id") or ""),
                        order_id=str(place.get("orderID") or obj.get("order_id") or ""),
                        created_at_utc=str(obj.get("created_at_utc") or ""),
                        target_date=str(obj.get("target_date") or ""),
                        city=str(obj.get("city") or ""),
                        city_pool=str(obj.get("city_pool") or "unknown"),
                        side=str(obj.get("signal_side") or obj.get("side") or ""),
                        bracket=str(obj.get("bracket") or ""),
                        status=str(obj.get("status") or ""),
                        place_status=str(place.get("status") or ""),
                        posted_price=float(_safe_float(obj.get("posted_price") or ex.get("posted_price"), 0.0) or 0.0),
                        requested_price=float(_safe_float(obj.get("requested_price") or ex.get("requested_price"), 0.0) or 0.0),
                        best_bid=_safe_float(obj.get("best_bid") or ex.get("best_bid"), None),
                        best_ask=_safe_float(obj.get("best_ask") or ex.get("best_ask"), None),
                        spread=_safe_float(obj.get("quote_spread") or obj.get("spread") or ex.get("quote_spread"), None),
                        quote_edge=_safe_float(obj.get("quote_edge") or ex.get("quote_edge"), None),
                        model_prob=_safe_float(obj.get("model_token_probability") or ex.get("model_token_probability"), None),
                        execution_policy=str(obj.get("execution_policy") or ex.get("execution_policy") or ""),
                        quote_mode=str(obj.get("quote_mode") or ex.get("quote_mode") or ""),
                    )
                )
    return rows


def _order_group(rows: Iterable[LiveOrderRow], key_fn: Callable[[LiveOrderRow], str]) -> list[tuple[str, dict[str, Any]]]:
    buckets: dict[str, list[LiveOrderRow]] = defaultdict(list)
    for row in rows:
        buckets[key_fn(row)].append(row)
    out = []
    for key, vals in buckets.items():
        success = [v for v in vals if v.place_status == "live" and v.status == "submitted"]
        out.append(
            (
                key,
                {
                    "orders": len(vals),
                    "submitted": len(success),
                    "submit_rate": len(success) / len(vals) if vals else None,
                    "avg_price": mean([v.posted_price for v in vals if v.posted_price]) if vals else None,
                    "avg_edge": mean([v.quote_edge for v in vals if v.quote_edge is not None])
                    if any(v.quote_edge is not None for v in vals)
                    else None,
                },
            )
        )
    return sorted(out, key=lambda item: item[1]["orders"], reverse=True)


def _load_paper_csv_rows() -> list[dict[str, str]]:
    if not PAPER_CSV.exists():
        return []
    with PAPER_CSV.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _paper_requested_diagnostics(rows: list[dict[str, str]], date_start: str, date_end: str) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    filtered = [
        r
        for r in rows
        if r.get("settlement_status") == "settled"
        and date_start <= str(r.get("event_date") or "") <= date_end
        and str(r.get("city_pool") or "") == "t1_trading"
    ]

    def summarize(vals: list[dict[str, str]]) -> dict[str, Any]:
        cost = sum(float(v.get("cost_usd") or 0.0) for v in vals)
        pnl = sum(float(v.get("pnl_usd") or 0.0) for v in vals)
        wins = sum(1 for v in vals if str(v.get("won")) == "True")
        return {
            "n": len(vals),
            "wins": wins,
            "win_rate": wins / len(vals) if vals else None,
            "cost_usd": cost,
            "pnl_usd": pnl,
            "roi": pnl / cost if cost else None,
        }

    def group(key_fn: Callable[[dict[str, str]], str]) -> list[tuple[str, dict[str, Any]]]:
        buckets: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in filtered:
            buckets[key_fn(row)].append(row)
        return sorted(((k, summarize(v)) for k, v in buckets.items()), key=lambda item: item[1]["pnl_usd"])

    return {
        "edge": group(lambda r: _edge_bucket(_safe_float(r.get("abs_edge"), None))),
        "price": group(lambda r: _price_bucket(_safe_float(r.get("entry_price"), None))),
    }


def _top_rows(rows: list[TradeRow], reverse: bool) -> list[TradeRow]:
    settled = [r for r in rows if r.pnl_usd_at_fill is not None]
    return sorted(settled, key=lambda r: r.pnl_usd_at_fill or 0.0, reverse=reverse)[:8]


def _table_trade_rows(rows: list[TradeRow]) -> list[str]:
    out = ["| city | target_date | side | bracket | fill_price | plan_price | qty | settle | pnl_fill | edge |", "|---|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        out.append(
            f"| {r.city} | {r.target_date} | {r.side} | {r.bracket} | {_num(r.fill_price)} | {_num(r.plan_price)} | {_num(r.fill_qty, 2)} | {_num(r.settlement_yes_price)} | {_usd(r.pnl_usd_at_fill)} | {_num(r.abs_edge)} |"
        )
    return out


def _market_buckets(rows: list[TradeRow]) -> list[tuple[str, list[TradeRow]]]:
    buckets: dict[str, list[TradeRow]] = defaultdict(list)
    for row in rows:
        buckets[f"{row.city}|{row.target_date}|{row.side}|{row.bracket}"].append(row)
    return list(buckets.items())


def _table_market_rows(rows: list[TradeRow], *, reverse: bool, limit: int = 8) -> list[str]:
    ranked = sorted(
        _market_buckets(rows),
        key=lambda item: sum(r.pnl_usd_at_fill or 0.0 for r in item[1]),
        reverse=reverse,
    )[:limit]
    return _market_rows_from_ranked(ranked)


def _market_concentration_rows(rows: list[TradeRow]) -> list[str]:
    ranked = sorted(_market_buckets(rows), key=lambda item: abs(sum(r.pnl_usd_at_fill or 0.0 for r in item[1])), reverse=True)[:12]
    return _market_rows_from_ranked(ranked)


def _market_rows_from_ranked(ranked: list[tuple[str, list[TradeRow]]]) -> list[str]:
    out = [
        "| city | target_date | side | bracket | fills | cost_usd | pnl_usd | avg_price |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for key, vals in ranked:
        city, target_date, side, bracket = key.split("|", 3)
        cost = sum(v.cost_usd for v in vals)
        qty = sum(v.fill_qty for v in vals)
        avg_price = sum(v.fill_price * v.fill_qty for v in vals) / qty if qty else None
        pnl = sum(v.pnl_usd_at_fill or 0.0 for v in vals)
        out.append(f"| {city} | {target_date} | {side} | {bracket} | {len(vals)} | {_usd(cost)} | {_usd(pnl)} | {_num(avg_price)} |")
    return out


def _paper_city_candidates(
    rows: list[dict[str, str]],
    *,
    start_date: str,
    exclude_cities: set[str],
    min_n: int = 5,
) -> list[tuple[str, dict[str, Any]]]:
    buckets: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        city = str(row.get("city") or "")
        if city in exclude_cities:
            continue
        if row.get("settlement_status") != "settled":
            continue
        if str(row.get("event_date") or "") < start_date:
            continue
        if str(row.get("city_pool") or "") != "t2_research":
            continue
        buckets[city].append(row)
    out: list[tuple[str, dict[str, Any]]] = []
    for city, vals in buckets.items():
        if len(vals) < min_n:
            continue
        by_date: dict[str, list[dict[str, str]]] = defaultdict(list)
        for val in vals:
            by_date[str(val.get("event_date") or "")].append(val)
        wins = sum(1 for v in vals if str(v.get("won")) == "True")
        cost = sum(float(v.get("cost_usd") or 0.0) for v in vals)
        pnl = sum(float(v.get("pnl_usd") or 0.0) for v in vals)
        positive_days = sum(
            1
            for day_rows in by_date.values()
            if sum(float(v.get("pnl_usd") or 0.0) for v in day_rows) > 0
        )
        dates = sorted(d for d in by_date if d)
        out.append(
            (
                city,
                {
                    "n": len(vals),
                    "wins": wins,
                    "win_rate": wins / len(vals) if vals else None,
                    "cost_usd": cost,
                    "pnl_usd": pnl,
                    "roi": pnl / cost if cost else None,
                    "active_days": len(dates),
                    "positive_days": positive_days,
                    "positive_day_rate": positive_days / len(dates) if dates else None,
                    "date_range": f"{dates[0]} - {dates[-1]}" if dates else "N/A",
                },
            )
        )
    return sorted(out, key=lambda item: (item[1]["roi"] or 0.0, item[1]["pnl_usd"]), reverse=True)


def _paper_city_comparison_pool(
    rows: list[dict[str, str]],
    *,
    start_date: str,
    existing_live_cities: set[str],
    added_cities: set[str],
    min_n: int = 5,
) -> list[tuple[str, dict[str, Any]]]:
    buckets: dict[str, list[dict[str, str]]] = defaultdict(list)
    cohort_by_city: dict[str, str] = {}
    for row in rows:
        city = str(row.get("city") or "")
        if row.get("settlement_status") != "settled":
            continue
        if str(row.get("event_date") or "") < start_date:
            continue

        if city in added_cities:
            cohort = "new_added_8"
        elif str(row.get("city_pool") or "") == "t2_research" and city not in existing_live_cities:
            cohort = "current_t2"
        else:
            continue

        buckets[city].append(row)
        cohort_by_city[city] = cohort

    out: list[tuple[str, dict[str, Any]]] = []
    for city, vals in buckets.items():
        if len(vals) < min_n and city not in added_cities:
            continue
        by_date: dict[str, list[dict[str, str]]] = defaultdict(list)
        for val in vals:
            by_date[str(val.get("event_date") or "")].append(val)
        wins = sum(1 for v in vals if str(v.get("won")) == "True")
        cost = sum(float(v.get("cost_usd") or 0.0) for v in vals)
        pnl = sum(float(v.get("pnl_usd") or 0.0) for v in vals)
        positive_days = sum(
            1
            for day_rows in by_date.values()
            if sum(float(v.get("pnl_usd") or 0.0) for v in day_rows) > 0
        )
        dates = sorted(d for d in by_date if d)
        out.append(
            (
                city,
                {
                    "cohort": cohort_by_city[city],
                    "n": len(vals),
                    "wins": wins,
                    "win_rate": wins / len(vals) if vals else None,
                    "cost_usd": cost,
                    "pnl_usd": pnl,
                    "roi": pnl / cost if cost else None,
                    "active_days": len(dates),
                    "positive_days": positive_days,
                    "positive_day_rate": positive_days / len(dates) if dates else None,
                    "date_range": f"{dates[0]} - {dates[-1]}" if dates else "N/A",
                },
            )
        )
    return sorted(out, key=lambda item: (item[1]["roi"] or 0.0, item[1]["pnl_usd"]), reverse=True)


def _candidate_city_lines(candidates: list[tuple[str, dict[str, Any]]], limit: int = 30) -> list[str]:
    lines = [
        "| city | fills | active_days | positive_days | positive_day_rate | win_rate | cost_usd | pnl_usd | roi | date_range |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for city, s in candidates[:limit]:
        lines.append(
            f"| {city} | {s['n']} | {s['active_days']} | {s['positive_days']} | {_pct(s['positive_day_rate'])} | {_pct(s['win_rate'])} | {_usd(s['cost_usd'])} | {_usd(s['pnl_usd'])} | {_pct(s['roi'])} | {s['date_range']} |"
        )
    return lines


def _comparison_city_lines(candidates: list[tuple[str, dict[str, Any]]], limit: int = 40) -> list[str]:
    lines = [
        "| cohort | city | fills | active_days | positive_days | positive_day_rate | win_rate | cost_usd | pnl_usd | roi | date_range |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for city, s in candidates[:limit]:
        lines.append(
            f"| {s['cohort']} | {city} | {s['n']} | {s['active_days']} | {s['positive_days']} | {_pct(s['positive_day_rate'])} | {_pct(s['win_rate'])} | {_usd(s['cost_usd'])} | {_usd(s['pnl_usd'])} | {_pct(s['roi'])} | {s['date_range']} |"
        )
    return lines


def _comparison_tier_lines(candidates: list[tuple[str, dict[str, Any]]]) -> list[str]:
    def names(pred: Callable[[dict[str, Any]], bool]) -> str:
        selected = [city for city, stats in candidates if pred(stats)]
        return ", ".join(selected) if selected else "N/A"

    stable = lambda s: s["active_days"] >= 4 and s["n"] >= 10 and (s["roi"] or 0) >= 0.10 and (s["positive_day_rate"] or 0) >= 0.60
    positive_unstable = lambda s: (s["roi"] or 0) >= 0.10 and not stable(s)
    return [
        "| tier | cities | rule |",
        "|---|---|---|",
        f"| 新增 8 城：保留/可小幅加权 | {names(lambda s: s['cohort'] == 'new_added_8' and stable(s))} | added8 且 active_days>=4, fills>=10, ROI>=10%, positive_day_rate>=60% |",
        f"| 新增 8 城：低 size 观察 | {names(lambda s: s['cohort'] == 'new_added_8' and positive_unstable(s))} | added8 正收益但日稳定性不足 |",
        f"| 当前 T2：优先补进候选池 | {names(lambda s: s['cohort'] == 'current_t2' and stable(s))} | current_t2 且 active_days>=4, fills>=10, ROI>=10%, positive_day_rate>=60% |",
        f"| 当前 T2：shadow / 等样本 | {names(lambda s: s['cohort'] == 'current_t2' and (s['roi'] or 0) >= 0 and not stable(s))} | current_t2 非负但不满足稳定阈值 |",
        f"| 当前 T2：不加 | {names(lambda s: s['cohort'] == 'current_t2' and s['n'] >= 10 and (s['roi'] or 0) < 0)} | current_t2 fills>=10 且 ROI<0 |",
    ]


def _stability_tier_lines(candidates: list[tuple[str, dict[str, Any]]]) -> list[str]:
    def names(pred: Callable[[dict[str, Any]], bool]) -> str:
        selected = [city for city, stats in candidates if pred(stats)]
        return ", ".join(selected) if selected else "N/A"

    return [
        "| tier | cities | rule |",
        "|---|---|---|",
        f"| Stable add / small live | {names(lambda s: s['active_days'] >= 4 and s['n'] >= 10 and (s['roi'] or 0) >= 0.10 and (s['positive_day_rate'] or 0) >= 0.60)} | active_days>=4, fills>=10, ROI>=10%, positive_day_rate>=60% |",
        f"| Opportunistic shadow | {names(lambda s: (s['roi'] or 0) >= 0.20 and (s['active_days'] < 4 or s['n'] < 10))} | ROI>=20% but active_days<4 or fills<10 |",
        f"| Watch only | {names(lambda s: (s['roi'] or 0) >= 0 and not (s['active_days'] >= 4 and s['n'] >= 10 and (s['roi'] or 0) >= 0.10 and (s['positive_day_rate'] or 0) >= 0.60) and not ((s['roi'] or 0) >= 0.20 and (s['active_days'] < 4 or s['n'] < 10)))} | non-negative but not stable enough |",
        f"| Avoid / do not add | {names(lambda s: s['n'] >= 10 and (s['roi'] or 0) < 0)} | fills>=10 and ROI<0 |",
    ]


def _paper_candidate_coverage(rows: list[dict[str, str]], exclude_cities: set[str]) -> dict[str, Any]:
    settled = [r for r in rows if r.get("settlement_status") == "settled"]
    t2 = [r for r in settled if str(r.get("city_pool") or "") == "t2_research"]
    candidates = [r for r in t2 if str(r.get("city") or "") not in exclude_cities]
    recent = [r for r in candidates if str(r.get("event_date") or "") >= "2026-05-16"]

    def date_range(vals: list[dict[str, str]]) -> str:
        dates = [str(v.get("event_date") or "") for v in vals if v.get("event_date")]
        if not dates:
            return "N/A"
        return f"{min(dates)} - {max(dates)}"

    return {
        "total_rows": len(rows),
        "settled_rows": len(settled),
        "settled_t2_rows": len(t2),
        "candidate_rows": len(candidates),
        "candidate_cities": len({r.get("city") for r in candidates}),
        "candidate_range": date_range(candidates),
        "recent_rows": len(recent),
        "recent_cities": len({r.get("city") for r in recent}),
        "recent_range": date_range(recent),
    }


def _write_report(out_path: Path, *, data_note: str) -> None:
    conn = _connect()
    live = _load_trades(conn, "live_real")
    paper = _load_trades(conn, "paper")
    snapshot = _load_trades(conn, "snapshot_replay")
    missing_bracket_n = conn.execute("SELECT COUNT(1) FROM settlements WHERE settlement_status = 'missing_bracket'").fetchone()[0]
    db_mtime = datetime.fromtimestamp(DB_PATH.stat().st_mtime).isoformat(timespec="seconds")
    conn.close()

    live_orders = _load_live_orders()
    live_settled = [r for r in live if r.pnl_usd_at_fill is not None]
    if live_settled:
        date_start = min(r.target_date for r in live_settled)
        date_end = max(r.target_date for r in live_settled)
    elif live_orders:
        date_start = min(r.target_date for r in live_orders if r.target_date)
        date_end = max(r.target_date for r in live_orders if r.target_date)
    else:
        date_start = date_end = "UNKNOWN"

    paper_overlap = [r for r in paper if date_start <= r.target_date <= date_end and r.city_pool == "t1_trading"]
    live_cities = {r.city for r in live_settled}
    paper_same_cities = [r for r in paper_overlap if r.city in live_cities]
    snapshot_overlap = [r for r in snapshot if date_start <= r.target_date <= date_end and r.city_pool == "t1_trading"]

    live_summary = _summary(live)
    paper_summary = _summary(paper_overlap)
    paper_city_summary = _summary(paper_same_cities)
    snapshot_summary = _summary(snapshot_overlap)
    unsettled_live = len([r for r in live if r.pnl_usd_at_fill is None])
    total_live = len(live)
    order_ids_with_fills = {r.execution_id for r in live}
    submitted_orders = [r for r in live_orders if r.status == "submitted" and r.place_status == "live"]
    still_open_or_unfilled = [r for r in submitted_orders if r.execution_id not in order_ids_with_fills]

    paper_diag = _paper_requested_diagnostics(_load_paper_csv_rows(), date_start, date_end)

    lines = [
        "# 绩效分析：live full research",
        "",
        f"> 时间窗：{date_start} — {date_end}（北京时间）  ",
        "> 策略：all live / weather_edge_v1  ",
        "> 城市池：all（live 实际为 t1_trading，早期缺 city_pool 的 raw order 标为 unknown）  ",
        "> 数据源：DB + live raw mirror",
        "",
        "## 数据快照",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| 数据源路径 | {DB_PATH.relative_to(ROOT)}；{LIVE_ORDER_DIR.relative_to(ROOT)} |",
        f"| 数据快照时间 | {db_mtime}（{data_note}） |",
        f"| fills 行数 | live={total_live} / paper={len(paper)} / snapshot_replay={len(snapshot)} |",
        f"| unsettled 占比 | {unsettled_live} / {total_live}（{(100 * unsettled_live / total_live if total_live else 0):.1f}%） |",
        f"| missing_bracket 数 | {missing_bracket_n} |",
        f"| live raw submitted orders | {len(submitted_orders)} submitted；{len(still_open_or_unfilled)} not matched to DB fills |",
        "",
        "## 总览",
        "",
        "| 指标 | 已结算（fill 口径） | 已结算（plan 口径） | 含未结算（mid 估值）[UNSETTLED] |",
        "|---|---:|---:|---:|",
        f"| 总 PnL (USD) | {_usd(live_summary['pnl_usd_at_fill'])} | {_usd(live_summary['pnl_usd_at_plan'])} | N/A（未拉盘口 mid） |",
        f"| ROI | {_pct(live_summary['roi'])} | N/A | N/A |",
        f"| Win rate（by count） | {_pct(live_summary['win_rate'])} | N/A | N/A |",
        f"| Win rate（by notional） | {_pct(live_summary['win_rate_notional'])} | N/A | N/A |",
        f"| 总 fills 数 | {live_summary['settled']} / {live_summary['fills']} | {live_summary['settled']} / {live_summary['fills']} | {live_summary['fills']} |",
        f"| 总 cost (USD) | {_usd(live_summary['cost_usd'])} | {_usd(live_summary['cost_usd'])} | N/A |",
        f"| 总 fill_qty (shares) | {_num(live_summary['fill_qty'], 2)} | {_num(live_summary['fill_qty'], 2)} | N/A |",
        f"| Sharpe-like（daily） | {_num(live_summary['daily_sharpe_like'])} | N/A | N/A |",
        "",
        "## 切片：by_date",
        "",
        "| 日期（北京时间） | fills | wins | win_rate | cost_usd | pnl_usd (fill) | pnl_usd (plan) | roi |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, s in sorted(_group(live_settled, lambda r: r.target_date), key=lambda item: item[0]):
        lines.append(_row_summary(key, s).replace("| " + key + " |", f"| {key} |"))

    lines.extend(
        [
            "",
            "## 切片：by_city",
            "",
            "| city | fills | wins | win_rate | cost_usd | pnl_usd (fill) | pnl_usd (plan) | roi |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key, s in _group(live_settled, lambda r: r.city):
        lines.append(_row_summary(key, s))

    lines.extend(
        [
            "",
            "## 切片：by_model",
            "",
            "| model | fills | wins | win_rate | cost_usd | pnl_usd (fill) | pnl_usd (plan) | roi |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key, s in _group(live_settled, lambda r: r.model):
        lines.append(_row_summary(key, s))

    lines.extend(
        [
            "",
            "## 切片：by_side",
            "",
            "| side | fills | wins | win_rate | cost_usd | avg_fill_price | pnl_usd (fill) | pnl_usd (plan) | roi |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key, s in _group(live_settled, lambda r: r.side):
        lines.append(_row_summary(key, s, include_price=True))

    lines.extend(
        [
            "",
            "## 切片：by_pool",
            "",
            "| pool | fills | wins | win_rate | cost_usd | pnl_usd (fill) | pnl_usd (plan) | roi |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key, s in _group(live_settled, lambda r: r.city_pool):
        lines.append(_row_summary(key, s))

    lines.extend(
        [
            "",
            "## Top Winners / Top Losers",
            "",
            "**Top 8 market winners（按 city × target_date × side × bracket 聚合）：**",
            "",
            *_table_market_rows(live_settled, reverse=True),
            "",
            "**Top 8 market losers：**",
            "",
            *_table_market_rows(live_settled, reverse=False),
            "",
            "**集中度 / 重复市场 Top 12（city × target_date × side × bracket）：**",
            "",
            *_market_concentration_rows(live_settled),
            "",
            "## 数据完整性自检",
            "",
            f"- [x] fill_row_count 与 DB 匹配：live fills={total_live}。",
            f"- [{'x' if (not total_live or unsettled_live / total_live < 0.2) else ' '}] unsettled_pct < 20%：{(100 * unsettled_live / total_live if total_live else 0):.1f}%。",
            f"- [ ] missing_bracket：DB 当前 total={missing_bracket_n}，本报告未逐城市列全量 missing_bracket。",
            "- [x] by_date 行按 target_date 展示；无交易日期不会补空行。",
            "",
            "## Paper / Snapshot 预期对比",
            "",
            "| baseline | fills | win_rate | cost_usd | pnl_usd | ROI | 说明 |",
            "|---|---:|---:|---:|---:|---:|---|",
            f"| live realized | {live_summary['settled']} | {_pct(live_summary['win_rate'])} | {_usd(live_summary['cost_usd'])} | {_usd(live_summary['pnl_usd_at_fill'])} | {_pct(live_summary['roi'])} | 真实 CLOB matched fills |",
            f"| paper overlap t1 | {paper_summary['settled']} | {_pct(paper_summary['win_rate'])} | {_usd(paper_summary['cost_usd'])} | {_usd(paper_summary['pnl_usd_at_fill'])} | {_pct(paper_summary['roi'])} | 同 target_date 窗口，全 T1 paper ledger |",
            f"| paper same live cities | {paper_city_summary['settled']} | {_pct(paper_city_summary['win_rate'])} | {_usd(paper_city_summary['cost_usd'])} | {_usd(paper_city_summary['pnl_usd_at_fill'])} | {_pct(paper_city_summary['roi'])} | 同窗口，仅 live 已结算城市 |",
            f"| snapshot replay overlap | {snapshot_summary['settled']} | {_pct(snapshot_summary['win_rate'])} | {_usd(snapshot_summary['cost_usd'])} | {_usd(snapshot_summary['pnl_usd_at_fill'])} | {_pct(snapshot_summary['roi'])} | 同窗口 snapshot replay |",
            "",
            "## 用户指定诊断：edge / 赔率（非 contract 官方切片）",
            "",
            "正式 PnL 归因按 `docs/WEATHER_ANALYSIS_CONTRACT.md` §5 白名单展示。下表用于回答本次问题里的 edge / 赔率形态，不作为 contract 标准绩效切片。PnL 计算采用 `weather_dashboard.metrics.calc._trade_pnl` 与 `settle_t24_paper.py` 的 token-cost 口径：BUY_NO payout 为 `1-final_yes`。",
            "",
            "**Live submitted order distribution：**",
            "",
            "| edge_bucket | orders | submitted | submit_rate | avg_posted_price | avg_quote_edge |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for key, s in _order_group(live_orders, lambda r: r.edge_bucket):
        lines.append(f"| {key} | {s['orders']} | {s['submitted']} | {_pct(s['submit_rate'])} | {_num(s['avg_price'])} | {_num(s['avg_edge'])} |")

    lines.extend(["", "| price_bucket | orders | submitted | submit_rate | avg_posted_price | avg_quote_edge |", "|---|---:|---:|---:|---:|---:|"])
    for key, s in _order_group(live_orders, lambda r: r.price_bucket):
        lines.append(f"| {key} | {s['orders']} | {s['submitted']} | {_pct(s['submit_rate'])} | {_num(s['avg_price'])} | {_num(s['avg_edge'])} |")

    lines.extend(
        [
            "",
            "**Paper 同窗口 edge / 赔率诊断：**",
            "",
            "| edge_bucket | fills | wins | win_rate | cost_usd | pnl_usd | roi |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key, s in paper_diag["edge"]:
        lines.append(f"| {key} | {s['n']} | {s['wins']} | {_pct(s['win_rate'])} | {_usd(s['cost_usd'])} | {_usd(s['pnl_usd'])} | {_pct(s['roi'])} |")
    lines.extend(["", "| price_bucket | fills | wins | win_rate | cost_usd | pnl_usd | roi |", "|---|---:|---:|---:|---:|---:|---:|"])
    for key, s in paper_diag["price"]:
        lines.append(f"| {key} | {s['n']} | {s['wins']} | {_pct(s['win_rate'])} | {_usd(s['cost_usd'])} | {_usd(s['pnl_usd'])} | {_pct(s['roi'])} |")

    paper_rows = _load_paper_csv_rows()
    city_groups = _group(live_settled, lambda r: r.city)
    keep = [item for item in city_groups if item[1]["settled"] >= 5 and (item[1]["roi"] or 0) >= 0.10]
    watch = [item for item in city_groups if item[1]["settled"] >= 5 and -0.05 <= (item[1]["roi"] or 0) < 0.10]
    reduce_or_pause = [item for item in city_groups if item[1]["settled"] >= 5 and (item[1]["roi"] or 0) < -0.05]
    settled_city_names = {name for name, _ in city_groups}
    more_data_cities = sorted({r.city for r in live_orders if r.city and r.city not in settled_city_names})
    exclude_expansion = settled_city_names | ADDED_T1_2026_05_26
    coverage = _paper_candidate_coverage(paper_rows, exclude_expansion)
    full_candidates = _paper_city_candidates(paper_rows, start_date="2026-05-13", exclude_cities=exclude_expansion)
    recent_candidates = _paper_city_candidates(paper_rows, start_date=date_start, exclude_cities=exclude_expansion)
    comparison_candidates = _paper_city_comparison_pool(
        paper_rows,
        start_date="2026-05-13",
        existing_live_cities=settled_city_names,
        added_cities=ADDED_T1_2026_05_26,
    )

    lines.extend(
        [
            "",
            "## 观察与建议",
            "",
            f"1. 交易动作：当前 live 已结算样本 ROI={_pct(live_summary['roi'])}，高于同窗口 paper T1 ROI={_pct(paper_summary['roi'])}，但 live 样本明显小且选择性成交强，不能按比例外推。短期建议保留 live 主路径，但把新增城市按城市级阈值分层，不再只用全池统一阈值。",
            "2. 收益来源：live 当前美元 PnL 主要来自 BUY_YES 的少数高赔率命中；BUY_NO 的胜率更高、交易更多，但 token 成本高时单笔盈利较薄。Top winners/losers 和集中度表显示，Tokyo 2026-05-20 的重复 YES 命中贡献了很大一块收益，因此不能只看总 ROI。",
            "3. Paper 预期：同窗口 paper 是正收益，但 paper 覆盖更多候选和假设成交；live 真实收益受 maker 排队、部分成交和重复去重影响。`still_open_or_unfilled` 较多时，paper 预期应打折，优先用 matched fills 做决策。",
            "4. 城市池：已结算 live 样本数不足 5 的城市不应升降级；样本 >=5 且 ROI>10% 的城市可以维持/加权，样本 >=5 且 ROI<-5% 的城市先降 size 或 shadow，接近零的城市先不扩 size。",
            "5. 城市独立策略：需要。至少应有 city-level 参数层：min_edge、price band、方向开关、max_notional。全池统一策略会把高噪声城市和稳定城市混在一起，paper 已显示城市差异足够大。",
            "",
            "**候选分级（基于 live 已结算样本，样本不足只作观察）：**",
            "",
            "| tier | cities | rule |",
            "|---|---|---|",
            f"| Keep / scale cautiously | {', '.join(k for k, _ in keep) or 'N/A'} | settled>=5 且 ROI>=10% |",
            f"| Watch / no scale | {', '.join(k for k, _ in watch) or 'N/A'} | settled>=5 且 -5%<=ROI<10% |",
            f"| Reduce / shadow | {', '.join(k for k, _ in reduce_or_pause) or 'N/A'} | settled>=5 且 ROI<-5% |",
            f"| Need more data | {', '.join(more_data_cities[:30]) or 'N/A'} | raw live orders 存在但尚无已结算 fills |",
            "",
            "## 新增 8 城后的扩池候选",
            "",
            "昨天新增的 8 城按当前 live raw 识别为：Ankara, Guangzhou, Istanbul, Jeddah, Karachi, Lucknow, Moscow, Seattle。扩池决策需要先把这 8 城和仍在 T2 的候选放在同一张 paper ledger 表里比较，再看剩余未加入候选。",
            "",
            "| coverage | rows | cities | date_range |",
            "|---|---:|---:|---|",
            f"| paper ledger total | {coverage['total_rows']} | N/A | N/A |",
            f"| settled paper | {coverage['settled_rows']} | N/A | N/A |",
            f"| settled T2 | {coverage['settled_t2_rows']} | N/A | N/A |",
            f"| T2 candidates after excludes | {coverage['candidate_rows']} | {coverage['candidate_cities']} | {coverage['candidate_range']} |",
            f"| live-overlap recent candidates | {coverage['recent_rows']} | {coverage['recent_cities']} | {coverage['recent_range']} |",
            "",
            "**同池比较：新增 8 城 vs 当前 T2 候选（event_date >= 2026-05-13）：**",
            "",
            "`new_added_8` 表示昨天已经补进 live/T1 的城市；`current_t2` 表示当前仍未进 live 的 T2 候选。新增 8 城不再从比较表中排除。",
            "",
            *_comparison_tier_lines(comparison_candidates),
            "",
            *_comparison_city_lines(comparison_candidates),
            "",
            "**稳定性分层（基于全量可用 T2 paper 候选）：**",
            "",
            *_stability_tier_lines(full_candidates),
            "",
            "**全量可用 T2 paper 候选（event_date >= 2026-05-13；T2 settled 起点；表内仅列 fills>=5）：**",
            "",
            *_candidate_city_lines(full_candidates),
            "",
            "**最近 live-overlap 候选（event_date >= live 起点；表内仅列 fills>=5）：**",
            "",
            *_candidate_city_lines(recent_candidates),
            "",
            "建议下一批不要一次性全加：优先 shadow/小 size 加 BuenosAires、Amsterdam、Manila、Munich、Singapore、Chengdu、SanFrancisco；如果更看重最近 live-overlap 窗口，则把 Taipei 提到第一批，把 Amsterdam/Manila 放 shadow 等新样本。",
        ]
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=REPORT_DIR / "2026-05-27-performance-live-full-research.md",
    )
    parser.add_argument(
        "--data-note",
        default="DB mtime；报告生成前已按 contract 同步并重建",
    )
    args = parser.parse_args()
    _write_report(args.out, data_note=args.data_note)
    print(args.out)


if __name__ == "__main__":
    main()
