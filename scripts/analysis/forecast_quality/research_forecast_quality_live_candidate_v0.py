#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(ROOT / "scripts/analysis/market_structure_edge"))
import research_range_rv_scanner as scanner  # noqa: E402

DB = ROOT / "runtime/weather.db"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-13-forecast-quality-live-candidate-v0.md"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-13-forecast-quality-live-candidate-v0.json"
SHADOW_MD = ROOT / "docs/analysis/2026-06/2026-06-13-forecast-quality-shadow-candidates-v0.md"
SHADOW_CSV = ROOT / "docs/analysis/2026-06/2026-06-13-forecast-quality-shadow-candidates-v0.csv"
GATE_JSON = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
ORDERBOOK = str(scanner.ORDERBOOK_GLOB_DEFAULT)

SEED = 20260613
ITERS = 1000
ORDER_SIZE_USD = 5.0


def pct(x: float | None) -> str:
    return "NA" if x is None else f"{x * 100:+.1f}%"


def money(x: float | None) -> str:
    return "NA" if x is None else f"${x:+.2f}"


def div(a: float, b: float) -> float | None:
    return a / b if b else None


def percentile(vals: list[float], q: float) -> float | None:
    vals = sorted(v for v in vals if v is not None and math.isfinite(v))
    if not vals:
        return None
    pos = (len(vals) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def ci95(vals: list[float]) -> list[float | None]:
    return [percentile(vals, 0.025), percentile(vals, 0.975)]


def table(headers: list[str], rows: list[list[Any]]) -> str:
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
        + ["| " + " | ".join(str(v) for v in row) + " |" for row in rows]
    )


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def entropy(probs: list[float]) -> float:
    vals = [p for p in probs if p > 0]
    if not vals:
        return 0.0
    denom = math.log(len(probs)) if len(probs) > 1 else 1.0
    return -sum(p * math.log(p) for p in vals) / denom


def norm(vals: list[float]) -> list[float]:
    total = sum(vals)
    return [v / total if total else 0.0 for v in vals]


def lead_bucket(hours: Any) -> str:
    if hours is None:
        return "unknown"
    h = float(hours)
    if h < 18:
        return "T<18"
    if h < 24:
        return "T18-24"
    if h < 30:
        return "T24-30"
    if h < 36:
        return "T30-36"
    return "T36+"


def side_cost(side: str, yes_price: float) -> float:
    return yes_price if side == "BUY_YES" else 1.0 - yes_price


def side_pnl(side: str, yes_price: float, final_yes: float) -> float:
    return (final_yes if side == "BUY_YES" else 1.0 - final_yes) - side_cost(side, yes_price)


def side_score(side: str, model_p_yes: float, yes_price: float) -> float:
    return model_p_yes - yes_price if side == "BUY_YES" else yes_price - model_p_yes


def data_self_check(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "fact_trades_max_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "fact_signal_candidates_max_built_at_utc": scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"),
        "fact_trades_by_class": rows(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class"),
        "fact_trades_by_settlement_status": rows(
            conn,
            "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
            "FROM fact_trades GROUP BY COALESCE(settlement_status, '')",
        ),
        "fact_signal_candidate_coverage": rows(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
            "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        )[0],
        "clob_order_fill_join": rows(
            conn,
            "SELECT o.status, COUNT(*) AS orders, "
            "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
            "FROM orders o LEFT JOIN fills f USING(execution_id) "
            "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
        ),
    }


def load_gate_status() -> dict[str, Any]:
    if not GATE_JSON.exists():
        return {"gate_pass": None, "path": str(GATE_JSON), "missing": True}
    try:
        data = json.loads(GATE_JSON.read_text())
        return {
            "gate_pass": data.get("gate_pass"),
            "fail_reasons": data.get("fail_reasons", []),
            "path": str(GATE_JSON),
        }
    except json.JSONDecodeError as exc:
        return {"gate_pass": None, "path": str(GATE_JSON), "error": str(exc)}


def load_fact_rows(conn: sqlite3.Connection, *, settled_only: bool) -> list[dict[str, Any]]:
    where_settled = "AND settlement_status='settled' AND final_yes IS NOT NULL" if settled_only else ""
    return rows(
        conn,
        f"""
        SELECT candidate_id, condition_id, market_id, event_date, city, city_pool, bracket, side,
               forecast_source, model_version, decision_hours_to_settle, decision_snapshot_ts_utc,
               model_p_yes, market_yes_price, decision_entry_price, yes_spread, no_spread,
               eligible, paper_ordered, live_filled, settlement_status, final_yes, fact_built_at_utc
        FROM fact_signal_candidates
        WHERE side IN ('BUY_YES','BUY_NO')
          AND decision_window_missing=0
          AND decision_snapshot_ts_utc IS NOT NULL
          AND condition_id IS NOT NULL AND market_id IS NOT NULL
          AND event_date IS NOT NULL AND city IS NOT NULL
          AND model_p_yes IS NOT NULL AND market_yes_price IS NOT NULL
          AND market_yes_price>0 AND market_yes_price<1
          {where_settled}
        """,
    )


def quality_key(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row["city"]),
        str(row["event_date"]),
        str(row["forecast_source"]),
        str(row["model_version"]),
        str(row["decision_snapshot_ts_utc"]),
    )


def choose_row(prev: dict[str, Any] | None, row: dict[str, Any]) -> dict[str, Any]:
    if prev is None:
        return row
    if prev.get("final_yes") is None and row.get("final_yes") is not None:
        return row
    if prev.get("side") != "BUY_YES" and row.get("side") == "BUY_YES":
        return row
    return prev


def quality_decision_sets(candidate_rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in candidate_rows:
        key = quality_key(row)
        bracket = str(row["bracket"])
        grouped[key][bracket] = choose_row(grouped[key].get(bracket), row)
    out: list[list[dict[str, Any]]] = []
    for by_bracket in grouped.values():
        items = sorted(by_bracket.values(), key=lambda r: scanner.bracket_sort_value(str(r["bracket"])))
        if len(items) >= 3:
            out.append(items)
    return out


def build_quality_rows(decision_sets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for items in decision_sets:
        n = len(items)
        base = items[0]
        model = norm([float(row["model_p_yes"]) for row in items])
        market = norm([float(row["market_yes_price"]) for row in items])
        model_mode = max(range(n), key=lambda i: model[i])
        market_mode = max(range(n), key=lambda i: market[i])
        adj3_start = min(max(0, model_mode - 1), n - 3)
        adj3 = set(range(adj3_start, adj3_start + 3))
        adj2_start = model_mode
        if model_mode > 0 and model[model_mode - 1] > model[min(model_mode + 1, n - 1)]:
            adj2_start = model_mode - 1
        adj2_start = min(max(0, adj2_start), n - 2)
        adj2 = set(range(adj2_start, adj2_start + 2))
        winners = [
            idx for idx, row in enumerate(items) if row.get("final_yes") is not None and float(row["final_yes"]) >= 0.5
        ]
        winner = winners[0] if winners else None
        mean_model_idx = sum(i * p for i, p in enumerate(model))
        mean_market_idx = sum(i * p for i, p in enumerate(market))
        row = {
            "quality_key": "|".join(quality_key(base)),
            "city": base["city"],
            "city_pool": base.get("city_pool"),
            "event_date": base["event_date"],
            "forecast_source": base["forecast_source"],
            "model_version": base["model_version"],
            "decision_snapshot_ts_utc": base["decision_snapshot_ts_utc"],
            "decision_hours_to_settle": base.get("decision_hours_to_settle"),
            "lead_bucket": lead_bucket(base.get("decision_hours_to_settle")),
            "n_brackets": n,
            "model_mode_bracket": items[model_mode]["bracket"],
            "market_mode_bracket": items[market_mode]["bracket"],
            "winner_bracket": items[winner]["bracket"] if winner is not None else None,
            "model_mode_idx": model_mode,
            "market_mode_idx": market_mode,
            "winner_idx": winner,
            "model_mode_probability": model[model_mode],
            "market_mode_probability": market[market_mode],
            "model_adjacent2_mass": sum(model[i] for i in adj2),
            "model_adjacent3_mass": sum(model[i] for i in adj3),
            "market_adjacent3_mass_at_model_mode": sum(market[i] for i in adj3),
            "model_tail_mass_outside_adjacent3": sum(model[i] for i in range(n) if i not in adj3),
            "market_tail_mass_outside_model_adjacent3": sum(market[i] for i in range(n) if i not in adj3),
            "model_entropy": entropy(model),
            "market_entropy": entropy(market),
            "model_market_entropy_gap": entropy(model) - entropy(market),
            "model_market_l1_gap": sum(abs(a - b) for a, b in zip(model, market)),
            "model_market_mode_distance": abs(model_mode - market_mode),
            "model_market_mode_agree": int(model_mode == market_mode),
            "model_distribution_variance": sum(((i - mean_model_idx) ** 2) * p for i, p in enumerate(model)),
            "market_distribution_variance": sum(((i - mean_market_idx) ** 2) * p for i, p in enumerate(market)),
            "model_mode_hit": None if winner is None else int(winner == model_mode),
            "market_mode_hit": None if winner is None else int(winner == market_mode),
            "model_adjacent2_hit": None if winner is None else int(winner in adj2),
            "model_adjacent3_hit": None if winner is None else int(winner in adj3),
            "model_mode_miss_distance": None if winner is None else abs(winner - model_mode),
            "market_mode_miss_distance": None if winner is None else abs(winner - market_mode),
        }
        out.append(row)
    return out


def split_dates_from_rows(rows_in: list[dict[str, Any]]) -> tuple[set[str], set[str], list[str]]:
    dates = sorted({str(row["event_date"]) for row in rows_in})
    split = max(1, min(len(dates) - 1, int(math.floor(len(dates) * 0.70)))) if dates else 0
    return set(dates[:split]), set(dates[split:]), dates


def train_thresholds(quality_rows: list[dict[str, Any]], train_dates: set[str]) -> dict[str, float]:
    train = [row for row in quality_rows if row["event_date"] in train_dates]
    out: dict[str, float] = {}
    for key in [
        "model_adjacent3_mass",
        "model_tail_mass_outside_adjacent3",
        "model_entropy",
        "model_mode_probability",
        "model_market_l1_gap",
    ]:
        vals = [float(row[key]) for row in train]
        for q in (0.25, 0.40, 0.50, 0.60, 0.75):
            out[f"{key}_p{int(q * 100)}"] = percentile(vals, q) or 0.0
    return out


def city_model_stats(quality_rows: list[dict[str, Any]], train_dates: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in quality_rows:
        if row["event_date"] in train_dates and row.get("model_adjacent3_hit") is not None:
            grouped[(str(row["city"]), str(row["model_version"]))].append(row)
    stats: dict[tuple[str, str], dict[str, Any]] = {}
    rates: list[float] = []
    misses: list[float] = []
    for key, items in grouped.items():
        hit_rate = sum(int(row["model_adjacent3_hit"]) for row in items) / len(items)
        miss = sum(float(row["model_mode_miss_distance"]) for row in items) / len(items)
        rates.append(hit_rate)
        misses.append(miss)
        stats[key] = {
            "city_model_train_n": len(items),
            "city_model_train_adj3_hit_rate": hit_rate,
            "city_model_train_avg_miss_distance": miss,
        }
    hit_median = percentile(rates, 0.50) or 0.0
    miss_p75 = percentile(misses, 0.75) or 999.0
    for value in stats.values():
        value["city_model_reliable"] = int(
            value["city_model_train_n"] >= 3
            and value["city_model_train_adj3_hit_rate"] >= hit_median
            and value["city_model_train_avg_miss_distance"] <= miss_p75
        )
        value["city_model_unreliable"] = int(
            value["city_model_train_n"] >= 3
            and (
                value["city_model_train_adj3_hit_rate"] < hit_median
                or value["city_model_train_avg_miss_distance"] > miss_p75
            )
        )
    return stats


def attach_quality_labels(
    quality_rows: list[dict[str, Any]], thresholds: dict[str, float], stats: dict[tuple[str, str], dict[str, Any]]
) -> None:
    for row in quality_rows:
        st = stats.get((str(row["city"]), str(row["model_version"])), {})
        high = (
            row["model_adjacent3_mass"] >= thresholds["model_adjacent3_mass_p60"]
            and row["model_tail_mass_outside_adjacent3"] <= thresholds["model_tail_mass_outside_adjacent3_p40"]
            and row["model_market_mode_distance"] <= 1
        )
        medium = (
            row["model_adjacent3_mass"] >= thresholds["model_adjacent3_mass_p40"]
            and row["model_tail_mass_outside_adjacent3"] <= thresholds["model_tail_mass_outside_adjacent3_p60"]
            and row["model_market_mode_distance"] <= 2
        )
        low = (
            row["model_tail_mass_outside_adjacent3"] >= thresholds["model_tail_mass_outside_adjacent3_p75"]
            or row["model_entropy"] >= thresholds["model_entropy_p75"]
            or row["model_market_l1_gap"] >= thresholds["model_market_l1_gap_p75"]
            or row["model_market_mode_distance"] >= 3
        )
        row.update(
            {
                "forecast_quality_high": int(high),
                "forecast_quality_medium": int((not high) and medium),
                "forecast_quality_medium_plus": int(high or medium),
                "forecast_quality_low": int(low),
                "city_model_reliable": int(st.get("city_model_reliable", 0)),
                "city_model_unreliable": int(st.get("city_model_unreliable", 0)),
                "city_model_train_n": int(st.get("city_model_train_n", 0)),
                "city_model_train_adj3_hit_rate": st.get("city_model_train_adj3_hit_rate"),
                "city_model_train_avg_miss_distance": st.get("city_model_train_avg_miss_distance"),
                "model_market_disagreement_high": int(
                    row["model_market_mode_distance"] >= 2
                    or row["model_market_l1_gap"] >= thresholds["model_market_l1_gap_p75"]
                ),
                "market_lag_candidate": int(
                    row["model_adjacent3_mass"] >= thresholds["model_adjacent3_mass_p60"]
                    and row["model_market_mode_distance"] >= 2
                ),
                "high_uncertainty": int(
                    row["model_entropy"] >= thresholds["model_entropy_p75"]
                    or row["model_adjacent3_mass"] <= thresholds["model_adjacent3_mass_p25"]
                ),
                "sharp_model_confident": int(
                    row["model_mode_probability"] >= thresholds["model_mode_probability_p75"]
                    and row["model_adjacent3_mass"] >= thresholds["model_adjacent3_mass_p60"]
                ),
                "tail_risk_high": int(
                    row["model_tail_mass_outside_adjacent3"] >= thresholds["model_tail_mass_outside_adjacent3_p75"]
                ),
            }
        )


def build_quality_layer(all_fact_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    decision_sets = quality_decision_sets(all_fact_rows)
    quality_rows = build_quality_rows(decision_sets)
    train_dates, holdout_dates, all_dates = split_dates_from_rows(quality_rows)
    thresholds = train_thresholds(quality_rows, train_dates)
    stats = city_model_stats(quality_rows, train_dates)
    attach_quality_labels(quality_rows, thresholds, stats)
    by_key = {row["quality_key"]: row for row in quality_rows}
    summary = {
        "decision_sets": len(decision_sets),
        "label_rows": len(quality_rows),
        "settled_label_rows": sum(1 for row in quality_rows if row.get("model_adjacent3_hit") is not None),
        "date_start": min(all_dates) if all_dates else None,
        "date_end": max(all_dates) if all_dates else None,
        "train_start": min(train_dates) if train_dates else None,
        "train_end": max(train_dates) if train_dates else None,
        "holdout_start": min(holdout_dates) if holdout_dates else None,
        "holdout_end": max(holdout_dates) if holdout_dates else None,
        "thresholds": thresholds,
    }
    return by_key, summary


def enrich_candidates(candidate_rows: list[dict[str, Any]], quality_by_key: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    missing = 0
    label_fields = [
        "forecast_quality_high",
        "forecast_quality_medium_plus",
        "forecast_quality_low",
        "city_model_reliable",
        "city_model_unreliable",
        "model_market_disagreement_high",
        "market_lag_candidate",
        "high_uncertainty",
        "sharp_model_confident",
        "tail_risk_high",
        "lead_bucket",
        "model_adjacent3_mass",
        "model_entropy",
    ]
    for row in candidate_rows:
        key = "|".join(quality_key(row))
        label = quality_by_key.get(key)
        if not label:
            missing += 1
            continue
        yes = float(row["market_yes_price"])
        mp = float(row["model_p_yes"])
        rr = dict(row)
        rr.update(
            {
                "eval_cost": side_cost(str(row["side"]), yes),
                "eval_pnl": None
                if row.get("final_yes") is None
                else side_pnl(str(row["side"]), yes, float(row["final_yes"])),
                "score": side_score(str(row["side"]), mp, yes),
                "decision_dt": scanner.parse_ts(str(row["decision_snapshot_ts_utc"])),
                "price_source": "decision_market_proxy",
            }
        )
        for field in label_fields:
            rr[field] = label.get(field)
        out.append(rr)
    return out, missing


def attach_orderbook(rows_in: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    legs = []
    for row in rows_in:
        legs.append(
            {
                "candidate_id": row["candidate_id"],
                "condition_id": row["condition_id"],
                "market_id": row["market_id"],
                "event_date": row["event_date"],
                "city": row["city"],
                "bracket": row["bracket"],
                "side": row["side"],
                "market_yes_price": row["market_yes_price"],
                "decision_entry_price": row["eval_cost"],
                "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                "decision_dt": row["decision_dt"],
                "final_yes": row["final_yes"],
                "counterfactual_pnl": None,
                "live_filled": row.get("live_filled") or 0,
                "outcome": "yes" if row["side"] == "BUY_YES" else "no",
                "price_bucket": "",
            }
        )
    matched, coverage = scanner.match_time_aligned_orderbooks(legs, ORDERBOOK)
    by_id = {row["candidate_id"]: row for row in matched}
    out: list[dict[str, Any]] = []
    max_age = None
    for row in rows_in:
        matched_row = by_id.get(row["candidate_id"])
        if matched_row and matched_row.get("taker_cost_usd") is not None and matched_row.get("taker_pnl_usd") is not None:
            rr = dict(row)
            rr["eval_cost"] = float(matched_row["taker_cost_usd"])
            rr["eval_pnl"] = float(matched_row["taker_pnl_usd"])
            rr["orderbook_age_minutes"] = matched_row.get("orderbook_age_minutes")
            rr["price_source"] = "time_aligned_orderbook"
            if rr["orderbook_age_minutes"] is not None:
                max_age = max(float(rr["orderbook_age_minutes"]), max_age or 0.0)
            out.append(rr)
    coverage["rows_in"] = len(rows_in)
    coverage["matched_rows"] = len(out)
    coverage["matched_rate"] = div(len(out), len(rows_in))
    coverage["max_orderbook_age_minutes"] = max_age
    coverage["constraint"] = "latest orderbook snapshot_ts_utc <= decision_snapshot_ts_utc"
    return out, coverage


def select_rows(rows_in: list[dict[str, Any]], profile: dict[str, Any], *, include_edge: bool) -> list[dict[str, Any]]:
    selected = []
    for row in rows_in:
        if profile["side"] != "ALL" and row["side"] != profile["side"]:
            continue
        if profile["model"] != "ALL" and row["model_version"] != profile["model"]:
            continue
        lo, hi = profile["cost_band"]
        if not (lo <= float(row["eval_cost"]) <= hi):
            continue
        quality = profile.get("quality", "none")
        if quality == "exclude_low" and int(row.get("forecast_quality_low") or 0):
            continue
        if quality == "medium_plus" and not int(row.get("forecast_quality_medium_plus") or 0):
            continue
        if quality == "reliable" and not int(row.get("city_model_reliable") or 0):
            continue
        if include_edge and float(row["score"]) < profile["edge_min"]:
            continue
        selected.append(row)
    if profile.get("cityday_top1"):
        best = {}
        for row in selected:
            key = (row["city"], row["event_date"])
            cur = best.get(key)
            if cur is None or (float(row["score"]), -float(row["eval_cost"])) > (
                float(cur["score"]),
                -float(cur["eval_cost"]),
            ):
                best[key] = row
        selected = list(best.values())
    return sorted(selected, key=lambda r: (r["event_date"], r["city"], -float(r["score"])))


def summarize(rows_in: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(float(row["eval_cost"]) for row in rows_in)
    pnl = sum(float(row.get("eval_pnl") or 0.0) for row in rows_in)
    by_date: dict[str, dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "pnl": 0.0, "orders": 0})
    city_date = Counter()
    for row in rows_in:
        slot = by_date[str(row["event_date"])]
        slot["cost"] += float(row["eval_cost"])
        slot["pnl"] += float(row.get("eval_pnl") or 0.0)
        slot["orders"] += 1
        city_date[(row["city"], row["event_date"])] += 1
    top5 = {date for date, _ in sorted(by_date.items(), key=lambda kv: kv[1]["pnl"], reverse=True)[:5]}
    drop_cost = sum(value["cost"] for date, value in by_date.items() if date not in top5)
    drop_pnl = sum(value["pnl"] for date, value in by_date.items() if date not in top5)
    order_counts = [int(value["orders"]) for value in by_date.values()]
    min_shares = min((ORDER_SIZE_USD / float(row["eval_cost"]) for row in rows_in if float(row["eval_cost"]) > 0), default=None)
    sim_cost = len(rows_in) * ORDER_SIZE_USD
    sim_pnl = sum(
        float(row.get("eval_pnl") or 0.0) * (ORDER_SIZE_USD / float(row["eval_cost"]))
        for row in rows_in
        if float(row["eval_cost"]) > 0
    )
    return {
        "rows": len(rows_in),
        "active_dates": len(by_date),
        "active_city_days": len(city_date),
        "avg_orders_per_active_day": div(sum(order_counts), len(order_counts)) or 0.0,
        "max_orders_per_day": max(order_counts) if order_counts else 0,
        "max_same_city_date_orders": max(city_date.values()) if city_date else 0,
        "unit_cost": cost,
        "unit_pnl": pnl,
        "unit_roi": div(pnl, cost),
        "top5_removed_roi": div(drop_pnl, drop_cost),
        "top5_removed_dates": sorted(top5),
        "sim_notional_usd": sim_cost,
        "sim_pnl_usd": sim_pnl,
        "sim_roi": div(sim_pnl, sim_cost),
        "min_shares_at_5usd": min_shares,
    }


def bootstrap(selected: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> dict[str, list[float | None]]:
    selected_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    baseline_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        selected_by_date[str(row["event_date"])].append(row)
    for row in baseline:
        baseline_by_date[str(row["event_date"])].append(row)
    dates = sorted(set(selected_by_date) | set(baseline_by_date))
    if not dates:
        return {"roi_ci95": [None, None], "excess_roi_ci95": [None, None]}
    rng = random.Random(SEED)
    rois: list[float] = []
    excess: list[float] = []
    for _ in range(ITERS):
        ss: list[dict[str, Any]] = []
        bs: list[dict[str, Any]] = []
        for _date in dates:
            pick = rng.choice(dates)
            ss.extend(selected_by_date.get(pick, []))
            bs.extend(baseline_by_date.get(pick, []))
        sr = summarize(ss)["unit_roi"]
        br = summarize(bs)["unit_roi"]
        if sr is not None:
            rois.append(sr)
        if sr is not None and br is not None:
            excess.append(sr - br)
    return {"roi_ci95": ci95(rois), "excess_roi_ci95": ci95(excess)}


def evaluate_profile(
    rows_in: list[dict[str, Any]], profile: dict[str, Any], train_dates: set[str], holdout_dates: set[str]
) -> dict[str, Any]:
    train_rows = [row for row in rows_in if row["event_date"] in train_dates]
    holdout_rows = [row for row in rows_in if row["event_date"] in holdout_dates]
    train_selected = select_rows(train_rows, profile, include_edge=True)
    train_baseline = select_rows(train_rows, profile, include_edge=False)
    holdout_selected = select_rows(holdout_rows, profile, include_edge=True)
    holdout_baseline = select_rows(holdout_rows, profile, include_edge=False)
    train_summary = summarize(train_selected)
    train_base_summary = summarize(train_baseline)
    holdout_summary = summarize(holdout_selected)
    holdout_base_summary = summarize(holdout_baseline)
    train_boot = bootstrap(train_selected, train_baseline)
    holdout_boot = bootstrap(holdout_selected, holdout_baseline)
    return {
        "profile": profile,
        "train": {
            "selected": train_summary,
            "baseline": train_base_summary,
            "excess_roi": None
            if train_summary["unit_roi"] is None or train_base_summary["unit_roi"] is None
            else train_summary["unit_roi"] - train_base_summary["unit_roi"],
            **train_boot,
        },
        "holdout": {
            "selected": holdout_summary,
            "baseline": holdout_base_summary,
            "excess_roi": None
            if holdout_summary["unit_roi"] is None or holdout_base_summary["unit_roi"] is None
            else holdout_summary["unit_roi"] - holdout_base_summary["unit_roi"],
            **holdout_boot,
        },
    }


def bucket_cost(cost: float) -> str:
    if cost < 0.40:
        return "0.25-0.40"
    if cost < 0.55:
        return "0.40-0.55"
    return "0.55-0.75"


def bucket_edge(score: float) -> str:
    if score < 0.10:
        return "<0.10"
    if score < 0.15:
        return "0.10-0.15"
    if score < 0.25:
        return "0.15-0.25"
    return "0.25+"


def contribution(rows_in: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows_in:
        grouped[str(row[key])].append(row)
    out = [{"key": name, **summarize(items)} for name, items in grouped.items()]
    return sorted(out, key=lambda row: row["sim_pnl_usd"], reverse=True)


def eval_row(name: str, result: dict[str, Any]) -> list[Any]:
    tr = result["train"]
    ho = result["holdout"]
    return [
        name,
        tr["selected"]["rows"],
        tr["selected"]["active_dates"],
        pct(tr["selected"]["unit_roi"]),
        pct(tr["excess_roi"]),
        f"[{pct(tr['excess_roi_ci95'][0])}, {pct(tr['excess_roi_ci95'][1])}]",
        ho["selected"]["rows"],
        ho["selected"]["active_dates"],
        pct(ho["selected"]["unit_roi"]),
        pct(ho["excess_roi"]),
        f"[{pct(ho['excess_roi_ci95'][0])}, {pct(ho['excess_roi_ci95'][1])}]",
        pct(ho["selected"]["top5_removed_roi"]),
        f"{ho['selected']['avg_orders_per_active_day']:.2f}",
        ho["selected"]["max_same_city_date_orders"],
        f"{ho['selected']['min_shares_at_5usd']:.2f}" if ho["selected"]["min_shares_at_5usd"] else "NA",
    ]


def range_decision_sets(rows_in: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows_in:
        if row["side"] != "BUY_YES":
            continue
        if row["model_version"] != "ecmwf":
            continue
        if int(row.get("forecast_quality_low") or 0):
            continue
        key = quality_key(row)
        grouped[key][str(row["bracket"])] = row
    out = []
    for by_bracket in grouped.values():
        items = sorted(by_bracket.values(), key=lambda r: scanner.bracket_sort_value(str(r["bracket"])))
        if len(items) >= 3:
            out.append(items)
    return out


def select_adjacent3(rows_in: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        row
        for row in rows_in
        if row.get("range_type") == "adjacent_3"
        and row.get("orderbook_taker_cost") is not None
        and abs(float(row["range_edge"])) >= 0.10
    ]
    best = {}
    for row in selected:
        key = (row["city"], row["event_date"])
        cur = best.get(key)
        if cur is None or abs(float(row["range_edge"])) > abs(float(cur["range_edge"])):
            best[key] = row
    return list(best.values())


def range_summary(rows_in: list[dict[str, Any]]) -> dict[str, Any]:
    simple = [
        {
            "event_date": row["event_date"],
            "city": row["city"],
            "eval_cost": float(row["orderbook_taker_cost"]),
            "eval_pnl": float(row["orderbook_taker_pnl"]),
        }
        for row in rows_in
    ]
    return summarize(simple)


def minimal_range_rv_comparison(
    settled_rows: list[dict[str, Any]],
    train_dates: set[str],
    holdout_dates: set[str],
    buy_no_result: dict[str, Any],
) -> dict[str, Any]:
    decision_sets = range_decision_sets(settled_rows)
    range_rows = scanner.enumerate_ranges(decision_sets)
    coverage = scanner.attach_orderbook_costs(range_rows, ORDERBOOK)
    matched = [
        row
        for row in range_rows
        if row.get("range_type") == "adjacent_3" and row.get("orderbook_taker_cost") is not None
    ]
    selected = select_adjacent3(matched)
    train_selected = [row for row in selected if row["event_date"] in train_dates]
    holdout_selected = [row for row in selected if row["event_date"] in holdout_dates]
    train_summary = range_summary(train_selected)
    holdout_summary = range_summary(holdout_selected)
    buy_no_holdout = buy_no_result["holdout"]["selected"]
    beats_buy_no = (
        holdout_summary["unit_roi"] is not None
        and buy_no_holdout["unit_roi"] is not None
        and holdout_summary["unit_roi"] > buy_no_holdout["unit_roi"]
        and holdout_summary["top5_removed_roi"] is not None
        and buy_no_holdout["top5_removed_roi"] is not None
        and holdout_summary["top5_removed_roi"] > buy_no_holdout["top5_removed_roi"]
    )
    return {
        "question": "Does fixed adjacent3 RangeRV in forecast_quality_low=0 beat the BUY_NO candidate?",
        "rule": "ecmwf, forecast_quality_low=0, range_type=adjacent_3, abs(range_edge)>=0.10, city-date top1, time-aligned orderbook",
        "coverage": coverage,
        "train": train_summary,
        "holdout": holdout_summary,
        "beats_buy_no": beats_buy_no,
        "verdict": "range_rv_not_in_current_live_queue" if not beats_buy_no else "range_rv_needs_deeper_shadow_comparison",
    }


def build_shadow_candidates(unsettled_rows: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    fresh_event_date_min = datetime.now().date().isoformat()
    fresh_rows = [row for row in unsettled_rows if str(row["event_date"]) >= fresh_event_date_min]
    selected = select_rows(fresh_rows, profile, include_edge=True)
    for row in selected:
        row["shares_at_5usd"] = ORDER_SIZE_USD / float(row["eval_cost"]) if float(row["eval_cost"]) > 0 else None
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        by_date[str(row["event_date"])].append(row)
    daily = []
    for date, items in sorted(by_date.items()):
        daily.append(
            {
                "event_date": date,
                "orders": len(items),
                "cities": sorted({str(row["city"]) for row in items}),
                "direction": "BUY_NO",
                "notional_usd": len(items) * ORDER_SIZE_USD,
            }
        )
    return {
        "rows": selected,
        "summary": {
            "fresh_event_date_min": fresh_event_date_min,
            "orders": len(selected),
            "active_dates": len(by_date),
            "max_orders_per_day": max((len(items) for items in by_date.values()), default=0),
            "avg_orders_per_active_day": div(len(selected), len(by_date)) or 0.0,
            "max_daily_notional_usd": max((len(items) * ORDER_SIZE_USD for items in by_date.values()), default=0.0),
            "cities": sorted({str(row["city"]) for row in selected}),
            "min_shares_at_5usd": min((row["shares_at_5usd"] for row in selected if row["shares_at_5usd"]), default=None),
        },
        "daily": daily,
    }


def write_shadow_files(shadow: dict[str, Any], generated_at: str, self_check: dict[str, Any], gate: dict[str, Any]) -> None:
    SHADOW_CSV.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "event_date",
        "city",
        "bracket",
        "direction",
        "no_cost",
        "no_edge",
        "shares_at_5usd",
        "notional_usd",
        "decision_snapshot_ts_utc",
        "forecast_quality_low",
        "order_type",
    ]
    with SHADOW_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in shadow["rows"]:
            writer.writerow(
                {
                    "event_date": row["event_date"],
                    "city": row["city"],
                    "bracket": row["bracket"],
                    "direction": "BUY_NO",
                    "no_cost": f"{float(row['eval_cost']):.4f}",
                    "no_edge": f"{float(row['score']):.4f}",
                    "shares_at_5usd": f"{float(row['shares_at_5usd']):.2f}",
                    "notional_usd": f"{ORDER_SIZE_USD:.2f}",
                    "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
                    "forecast_quality_low": int(row.get("forecast_quality_low") or 0),
                    "order_type": "zero_notional_shadow",
                }
            )
    md = [
        "# Forecast Quality Shadow Candidates v0",
        "",
        f"> generated_at_utc: `{generated_at}`",
        "> order_type: `zero_notional_shadow`; no live order action.",
        "",
        "## 数据快照",
        "",
        f"- DB: `{DB}`",
        f"- fact_trades MAX(fact_built_at_utc): `{self_check['fact_trades_max_built_at_utc']}`",
        f"- fact_signal_candidates MAX(fact_built_at_utc): `{self_check['fact_signal_candidates_max_built_at_utc']}`",
        f"- CLOB coverage gate: `{gate.get('gate_pass')}`; live_real PnL/ROI/rank/curve not published.",
        "",
        "## Profile",
        "",
        "```text",
        "BUY_NO ecmwf, forecast_quality_low=0, 0.40<=no_cost<=0.75, no_edge>=0.10, city-date top1, $5/order",
        "```",
        "",
        f"- fresh event_date min: `{shadow['summary']['fresh_event_date_min']}`",
        f"- orders: `{shadow['summary']['orders']}`",
        f"- active dates: `{shadow['summary']['active_dates']}`",
        f"- avg orders / active day: `{shadow['summary']['avg_orders_per_active_day']:.2f}`",
        f"- max orders / day: `{shadow['summary']['max_orders_per_day']}`",
        f"- max daily notional if tiny-live sized later: `${shadow['summary']['max_daily_notional_usd']:.2f}`",
        f"- min shares @ $5/order: `{shadow['summary']['min_shares_at_5usd']:.2f}`"
        if shadow["summary"]["min_shares_at_5usd"]
        else "- min shares @ $5/order: `NA`",
        f"- cities: `{', '.join(shadow['summary']['cities']) if shadow['summary']['cities'] else 'none'}`",
        "",
        "## By Date",
        "",
        table(
            ["date", "orders", "cities", "direction", "shadow notional"],
            [
                [row["event_date"], row["orders"], ",".join(row["cities"]), row["direction"], f"${row['notional_usd']:.2f}"]
                for row in shadow["daily"]
            ],
        ),
        "",
        "## Files",
        "",
        f"- CSV: `{SHADOW_CSV}`",
    ]
    SHADOW_MD.write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    generated_at = datetime.now(timezone.utc).isoformat()
    conn = connect()
    self_check = data_self_check(conn)
    gate = load_gate_status()
    db_last_modified = datetime.fromtimestamp(DB.stat().st_mtime, timezone.utc).isoformat()
    all_fact = load_fact_rows(conn, settled_only=False)
    settled_fact = [row for row in all_fact if row.get("settlement_status") == "settled" and row.get("final_yes") is not None]
    quality_by_key, quality_summary = build_quality_layer(all_fact)
    settled, settled_missing_labels = enrich_candidates(settled_fact, quality_by_key)
    unsettled_raw = [row for row in all_fact if row.get("settlement_status") != "settled" or row.get("final_yes") is None]
    unsettled, unsettled_missing_labels = enrich_candidates(unsettled_raw, quality_by_key)

    train_dates, holdout_dates, all_settled_dates = split_dates_from_rows(settled)
    orderbook_rows, orderbook_coverage = attach_orderbook(settled)

    final_profile = {
        "side": "BUY_NO",
        "model": "ecmwf",
        "quality": "exclude_low",
        "cost_band": [0.40, 0.75],
        "edge_min": 0.10,
        "cityday_top1": True,
    }
    profiles = {
        "ecmwf_buy_no_exclude_low_edge010_cost40_75_cityday_top1_v0": final_profile,
        "raw_multi_bucket_cost40_75": {**final_profile, "cityday_top1": False},
        "no_quality_cost40_75": {**final_profile, "quality": "none"},
        "cost25_75": {**final_profile, "cost_band": [0.25, 0.75]},
        "cost55_75": {**final_profile, "cost_band": [0.55, 0.75]},
        "edge08": {**final_profile, "edge_min": 0.08},
        "edge12": {**final_profile, "edge_min": 0.12},
        "medium_plus": {**final_profile, "quality": "medium_plus"},
        "city_model_reliable": {**final_profile, "quality": "reliable"},
    }
    evaluations = {name: evaluate_profile(orderbook_rows, profile, train_dates, holdout_dates) for name, profile in profiles.items()}
    final_result = evaluations["ecmwf_buy_no_exclude_low_edge010_cost40_75_cityday_top1_v0"]
    final_selected = select_rows(orderbook_rows, final_profile, include_edge=True)
    for row in final_selected:
        row["cost_bucket"] = bucket_cost(float(row["eval_cost"]))
        row["edge_bucket"] = bucket_edge(float(row["score"]))
    final_holdout = [row for row in final_selected if row["event_date"] in holdout_dates]

    reasons = []
    if final_result["train"]["selected"]["active_dates"] < 12:
        reasons.append("train_orderbook_active_dates<12")
    if final_result["train"]["excess_roi_ci95"][0] is None or final_result["train"]["excess_roi_ci95"][0] <= 0:
        reasons.append("train_excess_ci_crosses_zero")
    if final_result["holdout"]["excess_roi_ci95"][0] is None or final_result["holdout"]["excess_roi_ci95"][0] <= 0:
        reasons.append("holdout_excess_ci_crosses_zero")
    if final_result["holdout"]["selected"]["top5_removed_roi"] is None or final_result["holdout"]["selected"]["top5_removed_roi"] <= 0:
        reasons.append("holdout_top5_removed_not_positive")
    if final_result["holdout"]["selected"]["max_same_city_date_orders"] > 1:
        reasons.append("cityday_top1_failed")
    if final_result["holdout"]["selected"]["min_shares_at_5usd"] is None or final_result["holdout"]["selected"]["min_shares_at_5usd"] < 5:
        reasons.append("min_shares_at_5usd<5")

    verdict = (
        "confirmed_live_candidate"
        if not reasons
        else "near_live_candidate"
        if not any(reason.startswith("holdout") or reason in {"cityday_top1_failed", "min_shares_at_5usd<5"} for reason in reasons)
        else "shadow_only"
    )

    range_rv = minimal_range_rv_comparison(orderbook_rows, train_dates, holdout_dates, final_result)
    shadow = build_shadow_candidates(unsettled, final_profile)
    write_shadow_files(shadow, generated_at, self_check, gate)

    report = {
        "generated_at_utc": generated_at,
        "target": "forecast_quality_live_candidate_v0",
        "data_source": str(DB),
        "db_last_modified_utc": db_last_modified,
        "data_refresh_status": {
            "sync_weather_remote": "success_this_run",
            "run_stack": "fact_tables_built_but_exit_nonzero_due_clob_coverage_gate",
            "clob_coverage_gate": gate,
        },
        "data_self_check": self_check,
        "quality_layer": quality_summary,
        "orderbook_source": ORDERBOOK,
        "orderbook_coverage": orderbook_coverage,
        "input": {
            "fact_signal_candidates_rows": len(all_fact),
            "settled_candidate_rows": len(settled_fact),
            "settled_rows_with_quality": len(settled),
            "settled_missing_quality_label_rows": settled_missing_labels,
            "unsettled_rows_with_quality": len(unsettled),
            "unsettled_missing_quality_label_rows": unsettled_missing_labels,
            "event_dates": len(all_settled_dates),
            "train_start": min(train_dates) if train_dates else None,
            "train_end": max(train_dates) if train_dates else None,
            "holdout_start": min(holdout_dates) if holdout_dates else None,
            "holdout_end": max(holdout_dates) if holdout_dates else None,
        },
        "final_profile": final_profile,
        "order_size_usd": ORDER_SIZE_USD,
        "verdict": verdict,
        "verdict_reasons": reasons,
        "evaluations": evaluations,
        "range_rv_comparison": range_rv,
        "shadow_candidates": {
            "summary": shadow["summary"],
            "daily": shadow["daily"],
            "csv": str(SHADOW_CSV),
            "markdown": str(SHADOW_MD),
            "note": "zero-notional shadow candidates only; no live orders.",
        },
        "contribution": {
            "holdout_by_city": contribution(final_holdout, "city"),
            "holdout_by_event_date": contribution(final_holdout, "event_date"),
            "all_by_cost_bucket": contribution(final_selected, "cost_bucket"),
            "all_by_edge_bucket": contribution(final_selected, "edge_bucket"),
        },
        "final_selected_rows": final_selected,
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    range_holdout = range_rv["holdout"]
    md = [
        "# Forecast Quality Live Candidate v0",
        "",
        f"> generated_at_utc: `{generated_at}`",
        "> scope: local research only; no N100/live config changed; no live action.",
        "",
        "## 数据快照",
        "",
        f"- 数据源: `{DB}`",
        f"- DB last_modified_utc: `{db_last_modified}`",
        f"- fact_trades MAX(fact_built_at_utc): `{self_check['fact_trades_max_built_at_utc']}`",
        f"- fact_signal_candidates MAX(fact_built_at_utc): `{self_check['fact_signal_candidates_max_built_at_utc']}`",
        f"- CLOB coverage gate: `{gate.get('gate_pass')}`; fail_reasons: `{', '.join(gate.get('fail_reasons', []))}`",
        "- run_stack status: `fact_tables_built_but_exit_nonzero_due_clob_coverage_gate`",
        f"- raw settled candidate rows: `{len(settled_fact)}`; settled quality labels missing: `{settled_missing_labels}`",
        f"- quality label rows rebuilt from fact_signal_candidates: `{quality_summary['label_rows']}`",
        f"- orderbook matched: `{orderbook_coverage['matched_rows']}` / `{orderbook_coverage['rows_in']}` ({pct(orderbook_coverage['matched_rate'])})",
        f"- orderbook constraint: `{orderbook_coverage['constraint']}`",
        f"- train: `{min(train_dates) if train_dates else None}` -> `{max(train_dates) if train_dates else None}`; "
        f"holdout: `{min(holdout_dates) if holdout_dates else None}` -> `{max(holdout_dates) if holdout_dates else None}`",
        "",
        "### 5 行 SQL 自检",
        "",
        "```text",
        f"SELECT MAX(fact_built_at_utc) FROM fact_trades; -> {self_check['fact_trades_max_built_at_utc']}",
        f"SELECT trade_class, COUNT(*) FROM fact_trades GROUP BY trade_class; -> {self_check['fact_trades_by_class']}",
        f"SELECT settlement_status, COUNT(*) FROM fact_trades GROUP BY settlement_status; -> {self_check['fact_trades_by_settlement_status']}",
        f"SELECT COUNT(*), SUM(eligible), SUM(paper_ordered), SUM(live_filled) FROM fact_signal_candidates; -> {self_check['fact_signal_candidate_coverage']}",
        f"SELECT o.status, COUNT(*), with_fill FROM orders LEFT JOIN fills ...; -> {self_check['clob_order_fill_join']}",
        "```",
        "",
        "## Final Candidate",
        "",
        "```text",
        "ecmwf_buy_no_exclude_low_edge010_cost40_75_cityday_top1_v0",
        "BUY_NO only",
        "model_version = ecmwf",
        "forecast_quality_low = 0",
        "0.40 <= no_cost <= 0.75",
        "no_edge = market_yes_price - model_p_yes >= 0.10",
        "per city + event_date keep only highest no_edge bracket",
        "$5/order; shares = $5 / no_cost",
        "```",
        "",
        "这不是裸逐 bucket 策略，而是 forecast quality base + single-leg BUY_NO selector + city-date top1。",
        "",
        "## Evaluation",
        "",
        table(
            [
                "profile",
                "tr rows",
                "tr dates",
                "tr ROI",
                "tr excess",
                "tr excess CI",
                "ho rows",
                "ho dates",
                "ho ROI",
                "ho excess",
                "ho excess CI",
                "ho top5 removed",
                "ho avg/day",
                "max city-date",
                "min shares @ $5",
            ],
            [eval_row(name, evaluations[name]) for name in evaluations],
        ),
        "",
        "## Holdout City Contribution ($5/order)",
        "",
        table(
            ["city", "orders", "dates", "notional", "pnl", "roi"],
            [
                [
                    row["key"],
                    row["rows"],
                    row["active_dates"],
                    f"${row['sim_notional_usd']:.2f}",
                    money(row["sim_pnl_usd"]),
                    pct(row["sim_roi"]),
                ]
                for row in report["contribution"]["holdout_by_city"][:20]
            ],
        ),
        "",
        "## Holdout Date Contribution ($5/order)",
        "",
        table(
            ["event_date", "orders", "notional", "pnl", "roi"],
            [
                [
                    row["key"],
                    row["rows"],
                    f"${row['sim_notional_usd']:.2f}",
                    money(row["sim_pnl_usd"]),
                    pct(row["sim_roi"]),
                ]
                for row in report["contribution"]["holdout_by_event_date"]
            ],
        ),
        "",
        "## Cost / Edge Buckets",
        "",
        table(
            ["cost bucket", "orders", "dates", "pnl", "roi"],
            [
                [row["key"], row["rows"], row["active_dates"], money(row["sim_pnl_usd"]), pct(row["sim_roi"])]
                for row in report["contribution"]["all_by_cost_bucket"]
            ],
        ),
        "",
        table(
            ["edge bucket", "orders", "dates", "pnl", "roi"],
            [
                [row["key"], row["rows"], row["active_dates"], money(row["sim_pnl_usd"]), pct(row["sim_roi"])]
                for row in report["contribution"]["all_by_edge_bucket"]
            ],
        ),
        "",
        "## Fresh Zero-Notional Shadow Candidates",
        "",
        f"- output: `{SHADOW_MD}` and `{SHADOW_CSV}`",
        f"- fresh event_date min: `{shadow['summary']['fresh_event_date_min']}`",
        f"- orders: `{shadow['summary']['orders']}`; active_dates: `{shadow['summary']['active_dates']}`; "
        f"avg/day: `{shadow['summary']['avg_orders_per_active_day']:.2f}`; max daily notional: `${shadow['summary']['max_daily_notional_usd']:.2f}`",
        f"- cities: `{', '.join(shadow['summary']['cities']) if shadow['summary']['cities'] else 'none'}`",
        "- direction: `BUY_NO` only; order_type: `zero_notional_shadow`.",
        "",
        "## Minimal RangeRV / Adjacent3 Check",
        "",
        f"- rule: `{range_rv['rule']}`",
        f"- train: rows `{range_rv['train']['rows']}`, dates `{range_rv['train']['active_dates']}`, ROI `{pct(range_rv['train']['unit_roi'])}`, top5 removed `{pct(range_rv['train']['top5_removed_roi'])}`",
        f"- holdout: rows `{range_holdout['rows']}`, dates `{range_holdout['active_dates']}`, ROI `{pct(range_holdout['unit_roi'])}`, top5 removed `{pct(range_holdout['top5_removed_roi'])}`",
        f"- verdict: `{range_rv['verdict']}`",
        "- RangeRV 不进当前 live queue，除非后续同 universe 明显打败 BUY_NO candidate。",
        "",
        "## Verdict",
        "",
        f"- verdict: `{verdict}`",
        f"- reasons: `{', '.join(reasons) if reasons else 'none'}`",
        "- significance=FAIL, baseline=PASS, forward=FAIL, conclusion=shadow_only/inconclusive.",
        "- stale rerun曾接近 near-live；fresh rerun降级为 shadow_only，因为 holdout excess CI 跨 0 且 top5 removed 为负。",
        "- 适合进入 zero-notional shadow；不适合直接 $5/order tiny live，因为 train active_dates<12，且 holdout 稳健性不通过。",
        "- 因 CLOB coverage gate=false，本报告不发布 live_real PnL/ROI/rank/curve。",
        "",
        "## Files",
        "",
        f"- JSON: `{OUT_JSON}`",
        f"- Markdown: `{OUT_MD}`",
    ]
    OUT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "wrote": [str(OUT_JSON), str(OUT_MD), str(SHADOW_MD), str(SHADOW_CSV)],
                "verdict": verdict,
                "reasons": reasons,
                "final_train": final_result["train"],
                "final_holdout": final_result["holdout"],
                "range_rv": range_rv,
                "shadow_summary": shadow["summary"],
                "orderbook_coverage": orderbook_coverage,
                "gate": gate,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
