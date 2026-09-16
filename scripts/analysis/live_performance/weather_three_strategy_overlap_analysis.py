#!/usr/bin/env python3
"""Corrected overlap analysis for current weather strategy instances."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


DB_PATH = Path("runtime/weather.db")
LIVE_CYCLE_DIR = Path("runtime/weather_edge_v1/remote_pm_agent/live_cycle")

CORE_CITIES = (
    "Boston",
    "LA",
    "London",
    "Miami",
    "NYC",
    "Phoenix",
    "Shanghai",
    "Tokyo",
    "Warsaw",
)
NEW_T1_V2_CITIES = (
    "Ankara",
    "Guangzhou",
    "Istanbul",
    "Jeddah",
    "Karachi",
    "Lucknow",
    "Moscow",
    "Seattle",
)
NEW_T1_V3_CITIES = (
    "BuenosAires",
    "Amsterdam",
    "Manila",
    "Munich",
    "Singapore",
    "Chengdu",
)

INSTANCE_EXPR = """
CASE
  WHEN execution_policy='mid_price_core_v2' AND entry_price_window='0.25-0.75'
    THEN 'mid_price_core_v2_25_75'
  WHEN execution_policy='mid_price_core_v1' AND entry_price_window='0.25-0.75'
    THEN 'mid_price_core_v1_25_75'
  WHEN execution_policy='mid_price_core_v1' AND entry_price_window IN ('0.20-0.45','0.35-0.65')
    THEN 'mid_price_core_v1_side_band'
  ELSE strategy_id
END
"""


def fetch(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def emit(name: str, data: Any) -> None:
    print(f"@@{name}")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def summarize_side_band_runs() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(LIVE_CYCLE_DIR.glob("*mid_price_core_v1_side_band.json")):
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        sig = data.get("signals") or {}
        planner = data.get("planner") or {}
        executor = data.get("executor") or {}
        skipped = sig.get("skipped") or {}
        rows.append(
            {
                "run_id": data.get("run_id"),
                "snapshot": Path(str(sig.get("snapshot") or "")).name,
                "records": sig.get("records", 0),
                "candidate_signals": sig.get("candidate_signals", 0),
                "signals": sig.get("signals", 0),
                "plans": planner.get("plans", 0),
                "accepted": planner.get("accepted", 0),
                "live_orders": executor.get("live_orders", 0),
                "paper_written": executor.get("paper_written", 0),
                "city_not_allowed": skipped.get("city_not_allowed", 0),
                "city_pool_not_t1": skipped.get("city_pool_not_t1_trading", 0),
                "hours_below": skipped.get("hours_to_settle_below_min", 0),
                "hours_above": skipped.get("hours_to_settle_above_max", 0),
            }
        )
    return rows


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    core_sel = ",".join("?" for _ in CORE_CITIES)
    v2_sel = ",".join("?" for _ in NEW_T1_V2_CITIES)
    v3_sel = ",".join("?" for _ in NEW_T1_V3_CITIES)
    base_params = CORE_CITIES + NEW_T1_V3_CITIES + NEW_T1_V2_CITIES
    base = f"""
    WITH live_base AS (
      SELECT *,
             {INSTANCE_EXPR} AS strategy_instance,
             CASE
               WHEN city IN ({core_sel}) THEN 'core_9_overlap'
               WHEN city IN ({v3_sel}) THEN 'new_t1_v3_2026_05_27'
               WHEN city IN ({v2_sel}) THEN 'new_t1_v2_2026_05_26'
               ELSE 'other_current_or_historical'
             END AS city_generation
      FROM fact_trades
      WHERE trade_class='live_real'
    )
    """

    emit(
        "all_three_full_live_summary",
        fetch(
            conn,
            f"""
            {base}
            SELECT strategy_instance,
                   COUNT(*) AS fills,
                   SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled,
                   SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) AS missing_bracket,
                   SUM(CASE WHEN settlement_status IS NULL THEN 1 ELSE 0 END) AS null_status,
                   SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS cost,
                   SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
                   AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate,
                   AVG(CASE WHEN settlement_status='settled' THEN fill_price END) AS avg_fill_price,
                   AVG(CASE WHEN settlement_status='settled' THEN fill_price-plan_price END) AS avg_fill_minus_plan
            FROM live_base
            WHERE strategy_instance IN ('mid_price_core_v1_25_75','mid_price_core_v2_25_75','mid_price_core_v1_side_band')
            GROUP BY strategy_instance
            ORDER BY strategy_instance
            """,
            base_params,
        ),
    )

    emit(
        "core9_v1_v2_same_dates",
        fetch(
            conn,
            f"""
            {base}
            SELECT strategy_instance,
                   COUNT(*) AS fills,
                   SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled,
                   SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) AS missing_bracket,
                   SUM(CASE WHEN settlement_status IS NULL THEN 1 ELSE 0 END) AS null_status,
                   COUNT(DISTINCT city) AS cities,
                   COUNT(DISTINCT target_date) AS target_days,
                   SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS cost,
                   SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
                   AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate,
                   AVG(CASE WHEN settlement_status='settled' THEN fill_price END) AS avg_fill_price,
                   AVG(CASE WHEN settlement_status='settled' THEN fill_price-plan_price END) AS avg_fill_minus_plan
            FROM live_base
            WHERE city_generation='core_9_overlap'
              AND target_date BETWEEN '2026-05-29' AND '2026-06-01'
              AND strategy_instance IN ('mid_price_core_v1_25_75','mid_price_core_v2_25_75')
            GROUP BY strategy_instance
            ORDER BY strategy_instance
            """,
            base_params,
        ),
    )

    emit(
        "core9_v1_v2_same_dates_by_side",
        fetch(
            conn,
            f"""
            {base}
            SELECT strategy_instance, side,
                   COUNT(*) AS fills,
                   SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled,
                   SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS cost,
                   SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
                   AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate,
                   AVG(CASE WHEN settlement_status='settled' THEN fill_price END) AS avg_fill_price
            FROM live_base
            WHERE city_generation='core_9_overlap'
              AND target_date BETWEEN '2026-05-29' AND '2026-06-01'
              AND strategy_instance IN ('mid_price_core_v1_25_75','mid_price_core_v2_25_75')
            GROUP BY strategy_instance, side
            ORDER BY strategy_instance, side
            """,
            base_params,
        ),
    )

    emit(
        "new_city_only_v1_v2_same_dates",
        fetch(
            conn,
            f"""
            {base}
            SELECT strategy_instance, city_generation,
                   COUNT(*) AS fills,
                   SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled,
                   COUNT(DISTINCT city) AS cities,
                   SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS cost,
                   SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
                   AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate
            FROM live_base
            WHERE city_generation IN ('new_t1_v2_2026_05_26','new_t1_v3_2026_05_27')
              AND target_date BETWEEN '2026-05-29' AND '2026-06-01'
              AND strategy_instance IN ('mid_price_core_v1_25_75','mid_price_core_v2_25_75')
            GROUP BY strategy_instance, city_generation
            ORDER BY strategy_instance, city_generation
            """,
            base_params,
        ),
    )

    emit(
        "live_status_by_target_since_0529",
        fetch(
            conn,
            f"""
            {base}
            SELECT strategy_instance, target_date,
                   COUNT(*) AS fills,
                   SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled,
                   SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) AS missing_bracket,
                   SUM(CASE WHEN settlement_status IS NULL THEN 1 ELSE 0 END) AS null_status,
                   SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS cost,
                   SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl
            FROM live_base
            WHERE target_date >= '2026-05-29'
              AND strategy_instance IN ('mid_price_core_v1_25_75','mid_price_core_v2_25_75','mid_price_core_v1_side_band')
            GROUP BY strategy_instance, target_date
            ORDER BY target_date, strategy_instance
            """,
            base_params,
        ),
    )

    emit(
        "new_city_by_city_since_0529",
        fetch(
            conn,
            f"""
            {base}
            SELECT strategy_instance, city_generation, city,
                   COUNT(*) AS fills,
                   SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled,
                   SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) AS missing_bracket,
                   SUM(CASE WHEN settlement_status IS NULL THEN 1 ELSE 0 END) AS null_status,
                   SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS cost,
                   SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
                   AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate
            FROM live_base
            WHERE city_generation IN ('new_t1_v2_2026_05_26','new_t1_v3_2026_05_27')
              AND target_date >= '2026-05-29'
              AND strategy_instance IN ('mid_price_core_v1_25_75','mid_price_core_v2_25_75')
            GROUP BY strategy_instance, city_generation, city
            ORDER BY strategy_instance, city_generation, pnl
            """,
            base_params,
        ),
    )

    side_rows = summarize_side_band_runs()
    totals = {
        "runs": len(side_rows),
        "records": sum(int(r["records"] or 0) for r in side_rows),
        "candidate_signals": sum(int(r["candidate_signals"] or 0) for r in side_rows),
        "signals": sum(int(r["signals"] or 0) for r in side_rows),
        "plans": sum(int(r["plans"] or 0) for r in side_rows),
        "accepted": sum(int(r["accepted"] or 0) for r in side_rows),
        "live_orders": sum(int(r["live_orders"] or 0) for r in side_rows),
        "city_not_allowed": sum(int(r["city_not_allowed"] or 0) for r in side_rows),
        "city_pool_not_t1": sum(int(r["city_pool_not_t1"] or 0) for r in side_rows),
        "hours_below": sum(int(r["hours_below"] or 0) for r in side_rows),
        "hours_above": sum(int(r["hours_above"] or 0) for r in side_rows),
    }
    emit("side_band_raw_run_totals", totals)
    emit("side_band_recent_runs", side_rows[-12:])


if __name__ == "__main__":
    main()
