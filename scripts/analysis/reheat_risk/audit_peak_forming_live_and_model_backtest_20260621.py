#!/usr/bin/env python3
"""Audit peak-forming live orders and compare existing peak-forming models."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
RAW_ORDERS = (
    ROOT
    / "runtime/weather_edge_v1/remote_pm_agent/live/theta_current_yes_peak_forming_micro_tiny_live_v1_orders.jsonl"
)
OUT = ROOT / "runtime/analysis_outputs/peak_forming_live_and_model_backtest_20260621.json"

RULE_FILES = {
    "peak_forming_hazard_v2": ROOT
    / "docs/analysis/2026-06/generated/current_yes_peak_forming_hazard_v2/rule_comparison.csv",
    "peak_forming_hazard_v1": ROOT
    / "docs/analysis/2026-06/generated/current_yes_peak_forming_hazard_v1/rule_comparison.csv",
}
FADE_SUMMARY = ROOT / "docs/analysis/2026-06/generated/theta_current_yes_fade_confirmed_model_v1/summary.json"


def f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_settlements() -> dict[tuple[str, str, str], dict[str, Any]]:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    rows = conn.execute(
        """
        SELECT city, target_date, bracket, settlement_status, final_price, condition_id, market_id
        FROM settlement_outcomes
        WHERE source_system='pm_history'
        """
    ).fetchall()
    return {
        (str(r["city"]), str(r["target_date"]), str(r["bracket"])): dict(r)
        for r in rows
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(r["cost_usd"] for r in rows)
    pnl = sum(r["pnl_usd"] for r in rows if r.get("settled"))
    settled = [r for r in rows if r.get("settled")]
    wins = sum(1 for r in settled if r.get("final_yes") == 1.0)
    return {
        "orders": len(rows),
        "settled_orders": len(settled),
        "active_target_dates": len({r["target_date"] for r in rows}),
        "cities": len({r["city"] for r in rows}),
        "cost_usd": round(cost, 6),
        "settled_cost_usd": round(sum(r["cost_usd"] for r in settled), 6),
        "settled_pnl_usd": round(pnl, 6),
        "settled_roi": round(pnl / sum(r["cost_usd"] for r in settled), 6)
        if settled and sum(r["cost_usd"] for r in settled) > 0
        else None,
        "settled_win_rate": round(wins / len(settled), 6) if settled else None,
        "open_cost_usd": round(sum(r["cost_usd"] for r in rows if not r.get("settled")), 6),
        "avg_actual_price": round(cost / sum(r["shares"] for r in rows), 6)
        if sum(r["shares"] for r in rows) > 0
        else None,
        "avg_logged_limit_price": round(sum(r["posted_price"] for r in rows) / len(rows), 6)
        if rows
        else None,
    }


def live_orders() -> dict[str, Any]:
    settlements = load_settlements()
    out_rows: list[dict[str, Any]] = []
    unmatched_rows = 0
    for raw in read_jsonl(RAW_ORDERS):
        place = (raw.get("exchange_response") or {}).get("place") or {}
        if str(place.get("status") or "").lower() != "matched":
            unmatched_rows += 1
            continue
        cost = f(place.get("makingAmount"))
        shares = f(place.get("takingAmount"))
        if cost <= 0 or shares <= 0:
            unmatched_rows += 1
            continue
        key = (str(raw.get("city")), str(raw.get("target_date")), str(raw.get("bracket")))
        settlement = settlements.get(key)
        final_yes = f(settlement.get("final_price")) if settlement else None
        settled = bool(settlement and settlement.get("settlement_status") == "settled")
        pnl = shares * final_yes - cost if settled and final_yes is not None else 0.0
        out_rows.append(
            {
                "created_at_utc": raw.get("created_at_utc"),
                "target_date": raw.get("target_date"),
                "city": raw.get("city"),
                "bracket": str(raw.get("bracket")),
                "posted_price": f(raw.get("posted_price")),
                "cost_usd": cost,
                "shares": shares,
                "actual_price": cost / shares,
                "model_p_yes": f(raw.get("model_p_yes_used")),
                "quote_edge": f(raw.get("quote_edge")),
                "minutes_since_running_max": f(raw.get("minutes_since_running_max"), float("nan")),
                "obs_age_min": f(raw.get("obs_age_min"), float("nan")),
                "settled": settled,
                "final_yes": final_yes,
                "pnl_usd": pnl,
                "order_id": place.get("orderID"),
            }
        )
    by_date = defaultdict(list)
    for row in out_rows:
        by_date[row["target_date"]].append(row)
    return {
        "raw_order_file": str(RAW_ORDERS.relative_to(ROOT)),
        "matched_orders": len(out_rows),
        "unmatched_or_unfilled_order_rows": unmatched_rows,
        "overall": summarize(out_rows),
        "settled_only": summarize([r for r in out_rows if r.get("settled")]),
        "by_target_date": {date: summarize(rows) for date, rows in sorted(by_date.items())},
        "orders": out_rows,
    }


def read_rule_file(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def model_comparison() -> dict[str, Any]:
    keep_rules = {
        "market_price_only_tradable",
        "base_v9_peak_rule",
        "base_v9_peak_rule_plus_approx_guard",
        "hazard_v1_peak_rule",
        "hazard_v1_peak_rule_plus_approx_guard",
        "hazard_v2_peak_rule",
        "hazard_v2_peak_rule_plus_approx_guard",
        "hazard_v2_stalled_peak_rule",
        "train_selected_grid_h13_p0.75_edge0.08",
    }
    out: dict[str, Any] = {}
    for name, path in RULE_FILES.items():
        rows = []
        for row in read_rule_file(path):
            if row.get("rule") not in keep_rules:
                continue
            rows.append(
                {
                    "rule": row["rule"],
                    "orders": int(float(row["orders"])),
                    "active_dates": int(float(row["active_dates"])),
                    "cities": int(float(row["cities"])),
                    "roi": round(f(row["roi"]), 6),
                    "win_rate": round(f(row["win_rate"]), 6),
                    "avg_ask": round(f(row["avg_ask"]), 6),
                    "avg_p": round(f(row["avg_p"]), 6),
                    "bootstrap_roi_ci95": row.get("bootstrap_roi_ci95"),
                }
            )
        out[name] = rows

    fade = json.loads(FADE_SUMMARY.read_text(encoding="utf-8"))
    out["fade_confirmed_specialist_v1"] = {
        "note": "different entry profile, not a peak-forming replacement",
        "holdout_metrics": fade.get("holdout_metrics"),
        "live_like_comparison": fade.get("live_like_comparison"),
    }
    return out


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "evidence": {
            "live_orders": "raw live order JSONL matched responses; not fact_trades because current-YES fills are not in fact_trades yet",
            "settlement": "settlement_outcomes source_system=pm_history by city,target_date,bracket",
            "model_backtests": "existing generated peak-forming scored-rule comparisons",
        },
        "live_peak_forming_micro": live_orders(),
        "model_comparison": model_comparison(),
    }
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
