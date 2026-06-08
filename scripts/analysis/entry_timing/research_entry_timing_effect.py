#!/usr/bin/env python3
"""Rigorous entry timing baseline research.

Contract:
- live fill PnL source: fact_trades.pnl_usd_at_fill
- opportunity source: fact_signal_candidates.counterfactual_pnl
- no forecast-run checkpoint conclusion is emitted unless source fields exist
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime" / "weather.db"
OUT_DIR = ROOT / "docs" / "analysis" / "2026-06"
DEFAULT_STEM = "2026-06-08-entry-timing-effect-baseline"


TIMING_BINS = [
    ("<T-18", lambda h: h < 18),
    ("T-18-20", lambda h: 18 <= h < 20),
    ("T-20-22", lambda h: 20 <= h < 22),
    ("T-22-24", lambda h: 22 <= h <= 24),
    ("T-24-26", lambda h: 24 < h <= 26),
    ("T-26-28", lambda h: 26 < h <= 28),
    (">T-28", lambda h: h > 28),
]
BIN_ORDER = {name: i for i, (name, _) in enumerate(TIMING_BINS)}


def timing_bin(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    for name, pred in TIMING_BINS:
        if pred(float(hours)):
            return name
    return "unknown"


def period_label(date_text: str | None) -> str:
    if not date_text:
        return "unknown"
    if date_text >= "2026-06-01":
        return "post_2026_06_01"
    if date_text >= "2026-05-26":
        return "holdout_2026_05_26_to_05_31"
    return "pre_2026_05_26"


def pct(n: float | None, d: float | None) -> float | None:
    if not d:
        return None
    return n / d


def r4(x: float | None) -> float | None:
    if x is None:
        return None
    return round(float(x), 4)


def money(x: float | None) -> float:
    return round(float(x or 0.0), 4)


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def add_agg(agg: dict[str, Any], row: sqlite3.Row, pnl_col: str, cost_col: str | None = None) -> None:
    agg["n"] += 1
    if cost_col:
        agg["cost"] += row[cost_col] or 0.0
    agg["pnl"] += row[pnl_col] or 0.0
    agg["wins"] += row["win_by_count"] or 0


def summarize_trade_rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "n": 0,
            "city_days": set(),
            "cost": 0.0,
            "pnl": 0.0,
            "wins": 0,
            "avg_fill_price_num": 0.0,
        }
    )
    for r in rows:
        b = timing_bin(r["hours_to_settle"])
        a = agg[b]
        add_agg(a, r, "pnl_usd_at_fill", "cost_usd")
        a["city_days"].add((r["target_date"], r["city"], r["strategy_id"]))
        a["avg_fill_price_num"] += r["fill_price"] or 0.0
    out = []
    for b, a in agg.items():
        n = a["n"]
        out.append(
            {
                "timing_bin": b,
                "fills": n,
                "city_days": len(a["city_days"]),
                "cost_usd": money(a["cost"]),
                "pnl_usd": money(a["pnl"]),
                "roi": r4(pct(a["pnl"], a["cost"])),
                "win_rate": r4(pct(a["wins"], n)),
                "avg_fill_price": r4(pct(a["avg_fill_price_num"], n)),
            }
        )
    return sort_bins(out)


def sort_bins(rows: list[dict[str, Any]], key: str = "timing_bin") -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: (BIN_ORDER.get(r.get(key), 999), str(r.get(key))))


def markdown_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._\n"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for r in rows:
        vals = []
        for c in columns:
            v = r.get(c)
            if v is None:
                vals.append("")
            elif isinstance(v, float):
                vals.append(f"{v:.4f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def self_checks(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "db_path": str(DB),
        "db_mtime_utc": datetime.fromtimestamp(DB.stat().st_mtime, timezone.utc).isoformat(),
        "db_size_bytes": DB.stat().st_size,
        "fact_built_at": fetch_dicts(
            conn, "SELECT MAX(fact_built_at_utc) AS max_fact_built_at_utc FROM fact_trades"
        ),
        "trade_class": fetch_dicts(
            conn,
            "SELECT trade_class, COUNT(*) AS n FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
        ),
        "settlement_status": fetch_dicts(
            conn,
            "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS n "
            "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "signal_candidates": fetch_dicts(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        ),
        "orders_fills": fetch_dicts(
            conn,
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
        ),
    }


def opportunity_layers(conn: sqlite3.Connection) -> dict[str, Any]:
    signal_rows = conn.execute(
        """
        SELECT hours_to_settle AS h, abs_edge, market_price
        FROM signals
        """
    ).fetchall()
    signal_universe: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"signal_rows": 0, "abs_edge_sum": 0.0, "market_price_sum": 0.0, "market_price_n": 0}
    )
    for r in signal_rows:
        b = timing_bin(r["h"])
        a = signal_universe[b]
        a["signal_rows"] += 1
        a["abs_edge_sum"] += r["abs_edge"] or 0.0
        if r["market_price"] is not None:
            a["market_price_sum"] += r["market_price"]
            a["market_price_n"] += 1
    signal_universe_out = []
    for b, a in signal_universe.items():
        n = a["signal_rows"]
        signal_universe_out.append(
            {
                "timing_bin": b,
                "signal_rows": n,
                "avg_abs_edge": r4(pct(a["abs_edge_sum"], n)),
                "avg_market_price": r4(pct(a["market_price_sum"], a["market_price_n"])),
            }
        )

    l0_rows = conn.execute(
        """
        SELECT decision_hours_to_settle AS h, eligible, paper_ordered, live_filled,
               abs_edge, decision_entry_price, settlement_status
        FROM fact_signal_candidates
        WHERE final_yes IS NOT NULL AND decision_window_missing=0
        """
    ).fetchall()
    l0: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "opportunities": 0,
            "eligible": 0,
            "paper_ordered": 0,
            "live_filled": 0,
            "abs_edge_sum": 0.0,
            "entry_price_sum": 0.0,
            "entry_price_n": 0,
        }
    )
    for r in l0_rows:
        b = timing_bin(r["h"])
        a = l0[b]
        a["opportunities"] += 1
        a["eligible"] += r["eligible"] or 0
        a["paper_ordered"] += r["paper_ordered"] or 0
        a["live_filled"] += r["live_filled"] or 0
        a["abs_edge_sum"] += r["abs_edge"] or 0.0
        if r["decision_entry_price"] is not None:
            a["entry_price_sum"] += r["decision_entry_price"]
            a["entry_price_n"] += 1

    l0_out = []
    for b, a in l0.items():
        n = a["opportunities"]
        l0_out.append(
            {
                "timing_bin": b,
                "opportunities": n,
                "eligible": a["eligible"],
                "eligible_rate": r4(pct(a["eligible"], n)),
                "paper_ordered": a["paper_ordered"],
                "live_filled": a["live_filled"],
                "live_fill_rate": r4(pct(a["live_filled"], n)),
                "avg_abs_edge": r4(pct(a["abs_edge_sum"], n)),
                "avg_decision_entry_price": r4(pct(a["entry_price_sum"], a["entry_price_n"])),
            }
        )

    l1_rows = conn.execute(
        """
        SELECT decision_hours_to_settle AS h, side, city, model_version, event_date,
               decision_entry_price, counterfactual_pnl, win_by_count, paper_ordered, live_filled
        FROM fact_signal_candidates
        WHERE final_yes IS NOT NULL AND decision_window_missing=0 AND eligible=1
        """
    ).fetchall()
    l1: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "eligible_opportunities": 0,
            "cf_cost_proxy": 0.0,
            "cf_pnl": 0.0,
            "wins": 0,
            "paper_ordered": 0,
            "live_filled": 0,
        }
    )
    l1_side: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: defaultdict(float))
    for r in l1_rows:
        b = timing_bin(r["h"])
        a = l1[b]
        a["eligible_opportunities"] += 1
        a["cf_cost_proxy"] += r["decision_entry_price"] or 0.0
        a["cf_pnl"] += r["counterfactual_pnl"] or 0.0
        a["wins"] += r["win_by_count"] or 0
        a["paper_ordered"] += r["paper_ordered"] or 0
        a["live_filled"] += r["live_filled"] or 0

        s = l1_side[(b, r["side"])]
        s["eligible_opportunities"] += 1
        s["cf_cost_proxy"] += r["decision_entry_price"] or 0.0
        s["cf_pnl"] += r["counterfactual_pnl"] or 0.0
        s["wins"] += r["win_by_count"] or 0
        s["live_filled"] += r["live_filled"] or 0

    def l1_format(b: str, a: dict[str, Any], side: str | None = None) -> dict[str, Any]:
        n = a["eligible_opportunities"]
        out = {
            "timing_bin": b,
            "eligible_opportunities": int(n),
            "cf_cost_proxy": money(a["cf_cost_proxy"]),
            "cf_pnl": money(a["cf_pnl"]),
            "cf_roi_proxy": r4(pct(a["cf_pnl"], a["cf_cost_proxy"])),
            "cf_win_rate": r4(pct(a["wins"], n)),
            "live_filled": int(a["live_filled"]),
            "live_fill_rate": r4(pct(a["live_filled"], n)),
        }
        if side:
            out["side"] = side
        return out

    return {
        "signal_snapshot_universe": sort_bins(signal_universe_out),
        "l0_opportunity": sort_bins(l0_out),
        "l1_eligible": sort_bins([l1_format(b, a) for b, a in l1.items()]),
        "l1_eligible_by_side": sort_bins(
            [l1_format(b, a, side) for (b, side), a in l1_side.items()]
        ),
    }


def submitted_layer(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.hours_to_settle AS h, o.status, o.notional, o.cost_usd, o.execution_id,
               CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END AS with_fill
        FROM orders o
        JOIN plans p ON p.plan_id=o.plan_id
        JOIN signals s ON s.signal_id=p.signal_id
        LEFT JOIN fills f ON f.execution_id=o.execution_id
        WHERE o.venue='polymarket_clob'
        """
    ).fetchall()
    agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "submitted_orders": 0,
            "submitted_notional": 0.0,
            "posted_cost": 0.0,
            "filled_executions": set(),
            "statuses": defaultdict(int),
        }
    )
    seen_exec: set[tuple[str, str]] = set()
    for r in rows:
        b = timing_bin(r["h"])
        key = (b, r["execution_id"])
        if key not in seen_exec:
            seen_exec.add(key)
            agg[b]["submitted_orders"] += 1
            agg[b]["submitted_notional"] += r["notional"] or 0.0
            agg[b]["posted_cost"] += r["cost_usd"] or 0.0
            agg[b]["statuses"][r["status"]] += 1
        if r["with_fill"]:
            agg[b]["filled_executions"].add(r["execution_id"])
    out = []
    for b, a in agg.items():
        orders = a["submitted_orders"]
        filled_executions = len(a["filled_executions"])
        out.append(
            {
                "timing_bin": b,
                "submitted_orders": orders,
                "submitted_notional_usd": money(a["submitted_notional"]),
                "posted_cost_usd": money(a["posted_cost"]),
                "filled_executions": filled_executions,
                "execution_fill_rate": r4(pct(filled_executions, orders)),
                "status_counts": dict(a["statuses"]),
            }
        )
    return sort_bins(out)


def trade_layers(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT trade_class, strategy_id, strategy_name, execution_policy, city, target_date,
               side, model_version, settlement_status, hours_to_settle, fill_price, cost_usd,
               pnl_usd_at_fill, win_by_count
        FROM fact_trades
        """
    ).fetchall()
    live_settled = [
        r for r in rows if r["trade_class"] == "live_real" and r["settlement_status"] == "settled"
    ]
    paper_settled = [
        r for r in rows if r["trade_class"] == "paper" and r["settlement_status"] == "settled"
    ]

    by_trade_class: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "cost": 0.0, "pnl": 0.0, "wins": 0}
    )
    by_strategy: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "cost": 0.0, "pnl": 0.0, "wins": 0, "city_days": set()}
    )
    by_period: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "cost": 0.0, "pnl": 0.0, "wins": 0}
    )
    by_side_model: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "cost": 0.0, "pnl": 0.0, "wins": 0}
    )
    settlement_by_bin: dict[tuple[str, str], int] = defaultdict(int)

    for r in rows:
        if r["trade_class"] == "live_real":
            settlement_by_bin[(timing_bin(r["hours_to_settle"]), r["settlement_status"] or "")] += 1
        if r["settlement_status"] != "settled":
            continue
        b = timing_bin(r["hours_to_settle"])
        tc = r["trade_class"]
        add_agg(by_trade_class[(tc, b)], r, "pnl_usd_at_fill", "cost_usd")
        if tc == "live_real":
            add_agg(by_strategy[(r["strategy_id"], r["execution_policy"], b)], r, "pnl_usd_at_fill", "cost_usd")
            by_strategy[(r["strategy_id"], r["execution_policy"], b)]["city_days"].add(
                (r["target_date"], r["city"])
            )
            add_agg(by_period[(period_label(r["target_date"]), b)], r, "pnl_usd_at_fill", "cost_usd")
            add_agg(by_side_model[(b, r["side"], r["model_version"])], r, "pnl_usd_at_fill", "cost_usd")

    def agg_fmt(a: dict[str, Any]) -> dict[str, Any]:
        return {
            "fills": int(a["n"]),
            "cost_usd": money(a["cost"]),
            "pnl_usd": money(a["pnl"]),
            "roi": r4(pct(a["pnl"], a["cost"])),
            "win_rate": r4(pct(a["wins"], a["n"])),
        }

    trade_class_out = []
    for (tc, b), a in by_trade_class.items():
        x = {"trade_class": tc, "timing_bin": b}
        x.update(agg_fmt(a))
        trade_class_out.append(x)

    strategy_out = []
    for (sid, policy, b), a in by_strategy.items():
        x = {
            "strategy_id": sid,
            "execution_policy": policy,
            "timing_bin": b,
            "city_days": len(a["city_days"]),
        }
        x.update(agg_fmt(a))
        strategy_out.append(x)

    period_out = []
    for (period, b), a in by_period.items():
        x = {"period": period, "timing_bin": b}
        x.update(agg_fmt(a))
        period_out.append(x)

    side_model_out = []
    for (b, side, model), a in by_side_model.items():
        x = {"timing_bin": b, "side": side, "model_version": model}
        x.update(agg_fmt(a))
        side_model_out.append(x)

    settlement_out = [
        {"timing_bin": b, "settlement_status": s, "fills": n}
        for (b, s), n in settlement_by_bin.items()
    ]

    return {
        "l3_live_real": summarize_trade_rows(live_settled),
        "paper_settled": summarize_trade_rows(paper_settled),
        "settled_by_trade_class": sorted(
            trade_class_out,
            key=lambda r: (str(r["trade_class"]), BIN_ORDER.get(r["timing_bin"], 999)),
        ),
        "live_real_by_strategy": sorted(
            strategy_out,
            key=lambda r: (str(r["execution_policy"]), str(r["strategy_id"]), BIN_ORDER.get(r["timing_bin"], 999)),
        ),
        "live_real_by_period": sorted(
            period_out,
            key=lambda r: (str(r["period"]), BIN_ORDER.get(r["timing_bin"], 999)),
        ),
        "live_real_by_side_model": sorted(
            side_model_out,
            key=lambda r: (BIN_ORDER.get(r["timing_bin"], 999), str(r["side"]), str(r["model_version"])),
        ),
        "live_real_settlement_by_bin": sort_bins(settlement_out),
    }


def city_day_layer(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT target_date, city, strategy_id, execution_policy,
               CASE
                 WHEN hours_to_settle < 18 THEN '<T-18'
                 WHEN hours_to_settle >= 18 AND hours_to_settle < 20 THEN 'T-18-20'
                 WHEN hours_to_settle >= 20 AND hours_to_settle < 22 THEN 'T-20-22'
                 WHEN hours_to_settle >= 22 AND hours_to_settle <= 24 THEN 'T-22-24'
                 WHEN hours_to_settle > 24 AND hours_to_settle <= 26 THEN 'T-24-26'
                 WHEN hours_to_settle > 26 AND hours_to_settle <= 28 THEN 'T-26-28'
                 WHEN hours_to_settle > 28 THEN '>T-28'
                 ELSE 'unknown'
               END AS timing_bin,
               COUNT(*) AS fills,
               SUM(cost_usd) AS cost,
               SUM(pnl_usd_at_fill) AS pnl
        FROM fact_trades
        WHERE trade_class='live_real' AND settlement_status='settled'
        GROUP BY target_date, city, strategy_id, execution_policy, timing_bin
        """
    ).fetchall()
    agg: dict[str, dict[str, Any]] = defaultdict(lambda: {"city_days": 0, "cost": 0.0, "pnl": 0.0, "pnls": []})
    worst = []
    for r in rows:
        b = r["timing_bin"]
        pnl = r["pnl"] or 0.0
        cost = r["cost"] or 0.0
        agg[b]["city_days"] += 1
        agg[b]["cost"] += cost
        agg[b]["pnl"] += pnl
        agg[b]["pnls"].append(pnl)
        worst.append(
            {
                "target_date": r["target_date"],
                "city": r["city"],
                "strategy_id": r["strategy_id"],
                "execution_policy": r["execution_policy"],
                "timing_bin": b,
                "fills": r["fills"],
                "cost_usd": money(cost),
                "pnl_usd": money(pnl),
                "roi": r4(pct(pnl, cost)),
            }
        )
    out = []
    for b, a in agg.items():
        pnls = a["pnls"]
        out.append(
            {
                "timing_bin": b,
                "city_days": a["city_days"],
                "cost_usd": money(a["cost"]),
                "pnl_usd": money(a["pnl"]),
                "roi": r4(pct(a["pnl"], a["cost"])),
                "avg_city_day_pnl": r4(pct(sum(pnls), len(pnls))),
                "median_city_day_pnl": r4(median(pnls) if pnls else None),
                "negative_city_day_rate": r4(pct(sum(1 for p in pnls if p < 0), len(pnls))),
            }
        )
    return {
        "l4_city_day": sort_bins(out),
        "worst_city_days": sorted(worst, key=lambda r: r["pnl_usd"])[:20],
    }


def data_gaps(conn: sqlite3.Connection) -> list[dict[str, str]]:
    gaps = []
    required_forecast_cols = {
        "last_forecast_run_ts_utc",
        "next_forecast_run_ts_utc",
        "minutes_to_next_forecast_run",
        "minutes_since_last_forecast_run",
        "forecast_update_checkpoint",
    }
    for table in ["fact_trades", "fact_signal_candidates", "signals", "plans"]:
        cols = table_columns(conn, table)
        missing = sorted(required_forecast_cols - cols)
        if missing:
            gaps.append(
                {
                    "table": table,
                    "missing_fields": ", ".join(missing),
                    "impact": "Cannot publish forecast-update checkpoint performance from DB facts yet.",
                }
            )
    return gaps


def raw_snapshot_metadata_probe(max_files: int = 120) -> dict[str, Any]:
    snapshot_dir = ROOT / "runtime" / "weather_edge_v1" / "market_data" / "paper_snapshots"
    files = sorted(snapshot_dir.glob("snapshot_*.json"))
    sampled = files[-max_files:] if max_files and len(files) > max_files else files
    totals: dict[str, Any] = {
        "snapshot_dir": str(snapshot_dir),
        "file_count": len(files),
        "sampled_file_count": len(sampled),
        "record_count": 0,
        "with_model_init_utc_estimated": 0,
        "with_model_run_age_hours_estimated": 0,
        "with_forecast_source": 0,
        "with_settle_utc": 0,
        "sample_files": [str(p.name) for p in sampled[:3]],
        "latest_sample_files": [str(p.name) for p in sampled[-3:]],
    }
    by_checkpoint: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {"records": 0, "age_sum": 0.0, "age_n": 0}
    )
    for path in sampled:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for rec in payload.get("records", []):
            totals["record_count"] += 1
            if rec.get("model_init_utc_estimated") is not None:
                totals["with_model_init_utc_estimated"] += 1
            if rec.get("model_run_age_hours_estimated") is not None:
                totals["with_model_run_age_hours_estimated"] += 1
            if rec.get("forecast_source") is not None:
                totals["with_forecast_source"] += 1
            if rec.get("settle_utc") is not None:
                totals["with_settle_utc"] += 1
            b = timing_bin(rec.get("hours_to_settle"))
            model = rec.get("model") or rec.get("model_version") or "unknown"
            model_init = rec.get("model_init_utc_estimated") or "unknown"
            a = by_checkpoint[(b, model, model_init)]
            a["records"] += 1
            if rec.get("model_run_age_hours_estimated") is not None:
                a["age_sum"] += float(rec["model_run_age_hours_estimated"])
                a["age_n"] += 1
    checkpoint_rows = []
    for (b, model, model_init), a in by_checkpoint.items():
        checkpoint_rows.append(
            {
                "timing_bin": b,
                "model": model,
                "model_init_utc_estimated": model_init,
                "records": int(a["records"]),
                "avg_model_run_age_hours_estimated": r4(pct(a["age_sum"], a["age_n"])),
            }
        )
    checkpoint_rows = sorted(
        checkpoint_rows,
        key=lambda r: (BIN_ORDER.get(r["timing_bin"], 999), str(r["model"]), str(r["model_init_utc_estimated"])),
    )
    totals["coverage"] = {
        "model_init_utc_estimated": r4(pct(totals["with_model_init_utc_estimated"], totals["record_count"])),
        "model_run_age_hours_estimated": r4(
            pct(totals["with_model_run_age_hours_estimated"], totals["record_count"])
        ),
        "forecast_source": r4(pct(totals["with_forecast_source"], totals["record_count"])),
        "settle_utc": r4(pct(totals["with_settle_utc"], totals["record_count"])),
    }
    totals["timing_model_init_distribution"] = checkpoint_rows[:80]
    return totals


def make_markdown(result: dict[str, Any]) -> str:
    gate = result["coverage_gate"]
    lines = [
        "# Entry Timing Effect Baseline - 2026-06-08",
        "",
        "## 数据快照",
        "",
        f"- 数据源: `runtime/weather.db` (`{result['self_checks']['db_path']}`)",
        f"- DB mtime UTC: `{result['self_checks']['db_mtime_utc']}`",
        f"- fact_built_at_utc: `{result['self_checks']['fact_built_at'][0]['max_fact_built_at_utc']}`",
        f"- CLOB coverage gate: `gate_pass={gate.get('gate_pass')}`, live_real fill_ids={gate.get('fact_trades_live_real', {}).get('fill_ids')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}",
        f"- run_stack note: {result['run_stack_note']}",
        "",
        "### 强制 SQL 自检",
        "",
        "trade_class:",
        markdown_table(result["self_checks"]["trade_class"], ["trade_class", "n"]),
        "settlement_status:",
        markdown_table(result["self_checks"]["settlement_status"], ["settlement_status", "n"]),
        "fact_signal_candidates:",
        markdown_table(result["self_checks"]["signal_candidates"], ["rows", "eligible", "paper_ordered", "live_filled"]),
        "orders x fills:",
        markdown_table(result["self_checks"]["orders_fills"], ["status", "orders", "with_fill"]),
        "## 结论摘要",
        "",
        result["summary"],
        "",
        "## L0 Opportunity",
        "",
        "注意：`signals` 是 snapshot signal row 粒度，只能看覆盖和 edge 分布，不是结算反事实 PnL。",
        "",
        markdown_table(
            result["opportunity_layers"]["signal_snapshot_universe"],
            ["timing_bin", "signal_rows", "avg_abs_edge", "avg_market_price"],
        ),
        "## L0 Decision-Window Candidates",
        "",
        "注意：当前 `fact_signal_candidates` 只覆盖决策窗，实际只有 `T-22-24`；其它 timing bin 的机会层 alpha 是数据缺口，不应填 `0`。",
        "",
        markdown_table(
            result["opportunity_layers"]["l0_opportunity"],
            [
                "timing_bin",
                "opportunities",
                "eligible",
                "eligible_rate",
                "paper_ordered",
                "live_filled",
                "live_fill_rate",
                "avg_abs_edge",
                "avg_decision_entry_price",
            ],
        ),
        "## L1 Eligible Opportunity",
        "",
        markdown_table(
            result["opportunity_layers"]["l1_eligible"],
            [
                "timing_bin",
                "eligible_opportunities",
                "cf_cost_proxy",
                "cf_pnl",
                "cf_roi_proxy",
                "cf_win_rate",
                "live_filled",
                "live_fill_rate",
            ],
        ),
        "## L2 Submitted Orders",
        "",
        markdown_table(
            result["submitted_layer"],
            [
                "timing_bin",
                "submitted_orders",
                "submitted_notional_usd",
                "posted_cost_usd",
                "filled_executions",
                "execution_fill_rate",
                "status_counts",
            ],
        ),
        "## L3 Live Real Settled",
        "",
        markdown_table(
            result["trade_layers"]["l3_live_real"],
            ["timing_bin", "fills", "city_days", "cost_usd", "pnl_usd", "roi", "win_rate", "avg_fill_price"],
        ),
        "## L4 Live Real City-Day Portfolio",
        "",
        markdown_table(
            result["city_day_layer"]["l4_city_day"],
            [
                "timing_bin",
                "city_days",
                "cost_usd",
                "pnl_usd",
                "roi",
                "avg_city_day_pnl",
                "median_city_day_pnl",
                "negative_city_day_rate",
            ],
        ),
        "## Period Split - Live Real",
        "",
        markdown_table(
            result["trade_layers"]["live_real_by_period"],
            ["period", "timing_bin", "fills", "cost_usd", "pnl_usd", "roi", "win_rate"],
        ),
        "## Strategy Split - Live Real",
        "",
        markdown_table(
            result["trade_layers"]["live_real_by_strategy"],
            [
                "strategy_id",
                "execution_policy",
                "timing_bin",
                "city_days",
                "fills",
                "cost_usd",
                "pnl_usd",
                "roi",
                "win_rate",
            ],
        ),
        "## Side x Model Split - Live Real",
        "",
        markdown_table(
            result["trade_layers"]["live_real_by_side_model"],
            ["timing_bin", "side", "model_version", "fills", "cost_usd", "pnl_usd", "roi", "win_rate"],
        ),
        "## Settlement Status by Timing - Live Real",
        "",
        markdown_table(
            result["trade_layers"]["live_real_settlement_by_bin"],
            ["timing_bin", "settlement_status", "fills"],
        ),
        "## Worst City-Days",
        "",
        markdown_table(
            result["city_day_layer"]["worst_city_days"][:12],
            [
                "target_date",
                "city",
                "execution_policy",
                "timing_bin",
                "fills",
                "cost_usd",
                "pnl_usd",
                "roi",
            ],
        ),
        "## Forecast Checkpoint Data Gap",
        "",
        "### Raw Snapshot Metadata Probe",
        "",
        f"- snapshot_dir: `{result['raw_snapshot_metadata_probe']['snapshot_dir']}`",
        f"- sampled files: {result['raw_snapshot_metadata_probe']['sampled_file_count']} / {result['raw_snapshot_metadata_probe']['file_count']}",
        f"- sampled records: {result['raw_snapshot_metadata_probe']['record_count']}",
        f"- coverage: `{result['raw_snapshot_metadata_probe']['coverage']}`",
        "",
        "raw snapshot 原料里已有 estimated forecast run metadata，但当前 DB/fact 未派生这些字段；下一步应先接入 DB 再发布 checkpoint PnL。",
        "",
        markdown_table(
            result["raw_snapshot_metadata_probe"]["timing_model_init_distribution"],
            ["timing_bin", "model", "model_init_utc_estimated", "records", "avg_model_run_age_hours_estimated"],
        ),
        "### DB/Facts Missing Fields",
        "",
        markdown_table(result["data_gaps"], ["table", "missing_fields", "impact"]),
        "## 下一步",
        "",
        "1. 先把 `last_forecast_run_ts_utc` / `forecast_update_checkpoint` 从 snapshot 或 cache lineage 接入 fact 派生层，再做 `T-24-26 x pre/post update` 结论。",
        "2. 对 `T-24-26`、`T-26-28`、`<T-22` 做 leave-date-out policy simulation；本报告只给当前 DB 基线，不宣称最优 cut。",
        "3. 对亏损 city-day 追 `forecast_jump_after_entry_f` 和 `side_flip_after_entry`，否则不能区分 timing 坏和 forecast stale 坏。",
        "",
    ]
    return "\n".join(lines)


def build_summary(result: dict[str, Any]) -> str:
    live = {r["timing_bin"]: r for r in result["trade_layers"]["l3_live_real"]}
    l1 = {r["timing_bin"]: r for r in result["opportunity_layers"]["l1_eligible"]}
    city = {r["timing_bin"]: r for r in result["city_day_layer"]["l4_city_day"]}

    def row_desc(b: str) -> str:
        lr = live.get(b, {})
        er = l1.get(b)
        cr = city.get(b, {})
        if er:
            l1_part = f"L1 cf_roi_proxy={er.get('cf_roi_proxy')}"
        else:
            l1_part = "L1 unavailable outside current decision-window fact table"
        return (
            f"- `{b}`: {l1_part}, "
            f"L3 live_real roi={lr.get('roi')} (fills={lr.get('fills')}), "
            f"L4 city-day roi={cr.get('roi')} (city_days={cr.get('city_days')})."
        )

    bullets = [
        "当前 DB 支持的结论先限定在 timing 基线，不发布 forecast 更新卡点收益结论。",
        row_desc("<T-18"),
        row_desc("T-18-20"),
        row_desc("T-20-22"),
        row_desc("T-22-24"),
        row_desc("T-24-26"),
        row_desc("T-26-28"),
        row_desc(">T-28"),
        "`T-24-26` 不能直接并入主窗口：本轮可见 live_real/city-day 基线需要和 forecast checkpoint 再拆一次。",
        "`fact_signal_candidates` 当前只给 T-22~24 决策窗，不能用它证明 `<T-22` 或 `T-24-26` 的机会层 alpha；这些窗口先以 live fill/city-day 结果作为风险基线。",
        "raw paper snapshots 已有 estimated model init/run age metadata；forecast checkpoint 字段当前不在 fact/signals/plans 中，下一步是把这些原料接入派生层，不能用理论 6h cadence 硬贴标签。",
    ]
    return "\n".join(bullets)


def load_gate() -> dict[str, Any]:
    import subprocess

    proc = subprocess.run(
        ["python3", "scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-stem", default=DEFAULT_STEM)
    parser.add_argument(
        "--run-stack-note",
        default="sync_weather_remote.sh succeeded; run_stack.sh rebuilt DB/facts but API start returned non-zero because port 8000 was already in use.",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    result: dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "self_checks": self_checks(conn),
        "coverage_gate": load_gate(),
        "run_stack_note": args.run_stack_note,
        "opportunity_layers": opportunity_layers(conn),
        "submitted_layer": submitted_layer(conn),
        "trade_layers": trade_layers(conn),
        "city_day_layer": city_day_layer(conn),
        "raw_snapshot_metadata_probe": raw_snapshot_metadata_probe(),
        "data_gaps": data_gaps(conn),
    }
    result["summary"] = build_summary(result)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / f"{args.output_stem}.json"
    md_path = OUT_DIR / f"{args.output_stem}.md"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(make_markdown(result), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "markdown": str(md_path)}, indent=2))


if __name__ == "__main__":
    main()
