#!/usr/bin/env python3
"""Analyze side-band strategy performance and entry diagnostics."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import quantiles
from typing import Any


DB_PATH = Path("runtime/weather.db")
LIVE_CYCLE_DIR = Path("runtime/weather_edge_v1/remote_pm_agent/live_cycle")
REMOTE_PM_AGENT_DIR = Path("runtime/weather_edge_v1/remote_pm_agent")
REPORT_PATH = Path("docs/archive/analysis/2026-06/2026-06-04-performance-side-band-entry-analysis.md")

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

INSTANCE_EXPR = """
CASE
  WHEN producer_run_id LIKE '%mid_price_core_v2_25_75%'
    THEN 'mid_price_core_v2_25_75'
  WHEN producer_run_id LIKE '%mid_price_core_v1_25_75%'
    THEN 'mid_price_core_v1_25_75'
  WHEN producer_run_id LIKE '%mid_price_core_v1_side_band%'
    THEN 'mid_price_core_v1_side_band'
  WHEN execution_policy='mid_price_core_v2' AND entry_price_window='0.25-0.75'
    THEN 'legacy_mid_price_core_v2_25_75'
  WHEN execution_policy='mid_price_core_v1' AND entry_price_window='0.25-0.75'
    THEN 'legacy_mid_price_core_v1_25_75'
  WHEN execution_policy='mid_price_core_v1' AND entry_price_window IN ('0.35-0.65','0.20-0.45')
    THEN 'legacy_mid_price_core_v1_side_band_window'
  ELSE strategy_id
END
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def rows(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def one(conn: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else {}


def money(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.2f}"


def num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}"


def pct(value: Any) -> str:
    if value is None:
        return "-"
    return f"{100 * float(value):+.1f}%"


def ratio(num_value: Any, den_value: Any) -> float | None:
    if not den_value:
        return None
    return float(num_value or 0) / float(den_value)


def md_table(headers: list[str], data: list[list[Any]]) -> str:
    if not data:
        return "_No rows._\n"
    out = ["| " + " | ".join(headers) + " |"]
    out.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in data:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out) + "\n"


def percentile(values: list[float], idx: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    qs = quantiles(values, n=4, method="inclusive")
    return qs[idx]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def load_runtime_records(instance: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    plans: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    for path in sorted((REMOTE_PM_AGENT_DIR / "plans").glob(f"live_{instance}_*_trade_plans.jsonl")):
        for row in read_jsonl(path):
            row["_source_file"] = path.name
            row["_record_group"] = "plan"
            plans.append(row)
    plan_by_id = {row.get("plan_id"): row for row in plans if row.get("plan_id")}
    for path in sorted((REMOTE_PM_AGENT_DIR / "live").glob(f"live_{instance}_*_orders.jsonl")):
        for row in read_jsonl(path):
            row["_source_file"] = path.name
            row["_record_group"] = "order"
            plan = plan_by_id.get(row.get("plan_id")) or {}
            for key in (
                "model_version",
                "profile",
                "edge",
                "market_price",
                "model_token_probability",
                "entry_price_min",
                "entry_price_max",
            ):
                if row.get(key) is None and plan.get(key) is not None:
                    row[key] = plan.get(key)
            orders.append(row)
    return plans, orders


def in_scope_runtime(row: dict[str, Any], model_versions: set[str], min_date: str, max_date: str) -> bool:
    city = row.get("city")
    target_date = row.get("target_date")
    model = row.get("model_version")
    return (
        city in CORE_CITIES
        and target_date is not None
        and min_date <= target_date <= max_date
        and (not model_versions or model in model_versions)
    )


def summarize_runtime(records: list[dict[str, Any]]) -> dict[str, Any]:
    prices = [float(r.get("posted_price") or r.get("limit_price")) for r in records if r.get("posted_price") is not None or r.get("limit_price") is not None]
    market_prices = [float(r["market_price"]) for r in records if r.get("market_price") is not None]
    edges = [float(r["edge"]) for r in records if r.get("edge") is not None]
    quote_edges = [float(r["quote_edge"]) for r in records if r.get("quote_edge") is not None]
    spreads = [float(r["quote_spread"]) for r in records if r.get("quote_spread") is not None]
    return {
        "n": len(records),
        "cities": len({r.get("city") for r in records if r.get("city")}),
        "days": len({r.get("target_date") for r in records if r.get("target_date")}),
        "models": ",".join(sorted({str(r.get("model_version")) for r in records if r.get("model_version")})) or "-",
        "sides": ",".join(
            f"{k}:{v}" for k, v in sorted(Counter(str(r.get("signal_side") or "-") for r in records).items())
        ),
        "avg_entry": sum(prices) / len(prices) if prices else None,
        "avg_market": sum(market_prices) / len(market_prices) if market_prices else None,
        "avg_edge": sum(edges) / len(edges) if edges else None,
        "avg_quote_edge": sum(quote_edges) / len(quote_edges) if quote_edges else None,
        "avg_quote_spread": sum(spreads) / len(spreads) if spreads else None,
    }


def runtime_by_side(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in records:
        groups.setdefault((str(row.get("model_version") or "-"), str(row.get("signal_side") or "-")), []).append(row)
    out: list[dict[str, Any]] = []
    for (model, side), group in sorted(groups.items()):
        summary = summarize_runtime(group)
        summary["model_version"] = model
        summary["side"] = side
        prices = [float(r.get("posted_price") or r.get("limit_price")) for r in group if r.get("posted_price") is not None or r.get("limit_price") is not None]
        summary["min_entry"] = min(prices) if prices else None
        summary["max_entry"] = max(prices) if prices else None
        out.append(summary)
    return out


def summarize_side_band_runs() -> dict[str, Any]:
    run_rows: list[dict[str, Any]] = []
    for path in sorted(LIVE_CYCLE_DIR.glob("*mid_price_core_v1_side_band.json")):
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        signals = data.get("signals") or {}
        planner = data.get("planner") or {}
        executor = data.get("executor") or {}
        skipped = signals.get("skipped") or {}
        cfg = data.get("config") or {}
        run_rows.append(
            {
                "path": path.name,
                "run_id": data.get("run_id"),
                "records": signals.get("records", 0),
                "candidate_signals": signals.get("candidate_signals", 0),
                "signals": signals.get("signals", 0),
                "plans": planner.get("plans", 0),
                "accepted_before_live_dedup": planner.get("accepted_before_live_dedup", 0),
                "accepted": planner.get("accepted", 0),
                "live_orders": executor.get("live_orders", 0),
                "paper_written": executor.get("paper_written", 0),
                "skipped": skipped,
                "config": cfg,
            }
        )
    totals: dict[str, Any] = {
        "runs": len(run_rows),
        "records": sum(r["records"] for r in run_rows),
        "candidate_signals": sum(r["candidate_signals"] for r in run_rows),
        "signals": sum(r["signals"] for r in run_rows),
        "plans": sum(r["plans"] for r in run_rows),
        "accepted_before_live_dedup": sum(r["accepted_before_live_dedup"] for r in run_rows),
        "accepted": sum(r["accepted"] for r in run_rows),
        "live_orders": sum(r["live_orders"] for r in run_rows),
        "paper_written": sum(r["paper_written"] for r in run_rows),
        "skipped": Counter(),
    }
    for run in run_rows:
        totals["skipped"].update(run["skipped"])
    latest_cfg = run_rows[-1]["config"] if run_rows else {}
    return {"runs": run_rows, "totals": totals, "latest_config": latest_cfg}


def build_report() -> str:
    conn = connect()
    core_placeholders = ",".join("?" for _ in CORE_CITIES)
    side_plans, side_orders = load_runtime_records("mid_price_core_v1_side_band")
    v1_plans, v1_orders = load_runtime_records("mid_price_core_v1_25_75")
    side_runtime_models = sorted(
        {
            str(row.get("model_version"))
            for row in (*side_plans, *side_orders)
            if row.get("model_version")
        }
    )
    side_runtime_dates = sorted(
        {
            str(row.get("target_date"))
            for row in (*side_plans, *side_orders)
            if row.get("target_date")
        }
    )
    side_models = [
        r["model_version"]
        for r in rows(
            conn,
            f"""
            SELECT DISTINCT model_version
            FROM fact_trades
            WHERE trade_class='live_real'
              AND ({INSTANCE_EXPR})='mid_price_core_v1_side_band'
              AND model_version IS NOT NULL
            ORDER BY model_version
            """,
        )
    ]
    side_forecast_sources = [
        r["forecast_source"]
        for r in rows(
            conn,
            f"""
            SELECT DISTINCT forecast_source
            FROM fact_trades
            WHERE trade_class='live_real'
              AND ({INSTANCE_EXPR})='mid_price_core_v1_side_band'
              AND forecast_source IS NOT NULL
            ORDER BY forecast_source
            """,
        )
    ]
    model_filter_label: str
    trade_model_filter_sql = ""
    candidate_model_filter_sql = ""
    model_params: tuple[Any, ...] = ()
    if side_models:
        model_placeholders = ",".join("?" for _ in side_models)
        trade_model_filter_sql = f"AND model_version IN ({model_placeholders})"
        candidate_model_filter_sql = f"AND model_version IN ({model_placeholders})"
        model_params = tuple(side_models)
        model_filter_label = f"`model_version IN ({', '.join(side_models)})`"
    elif side_forecast_sources:
        model_placeholders = ",".join("?" for _ in side_forecast_sources)
        trade_model_filter_sql = f"AND forecast_source IN ({model_placeholders})"
        candidate_model_filter_sql = f"AND forecast_source IN ({model_placeholders})"
        model_params = tuple(side_forecast_sources)
        model_filter_label = f"`forecast_source IN ({', '.join(side_forecast_sources)})`（side-band model_version 为空）"
    elif side_runtime_models:
        model_placeholders = ",".join("?" for _ in side_runtime_models)
        trade_model_filter_sql = f"AND model_version IN ({model_placeholders})"
        candidate_model_filter_sql = f"AND model_version IN ({model_placeholders})"
        model_params = tuple(side_runtime_models)
        model_filter_label = (
            f"`model_version IN ({', '.join(side_runtime_models)})`（来自 synced side-band plan/order；"
            "fact_trades live_real 为空）"
        )
    else:
        model_filter_label = "`model_version` / `forecast_source` 在 side-band live fill 中均为空；DB 无法执行同模型过滤"
    side_dates = one(
        conn,
        f"""
        SELECT MIN(target_date) AS min_date, MAX(target_date) AS max_date
        FROM fact_trades
        WHERE trade_class='live_real'
          AND ({INSTANCE_EXPR})='mid_price_core_v1_side_band'
        """,
    )
    min_date = side_dates.get("min_date") or (side_runtime_dates[0] if side_runtime_dates else "2026-05-31")
    max_date = side_dates.get("max_date") or (side_runtime_dates[-1] if side_runtime_dates else datetime.now(timezone.utc).date().isoformat())
    base_params = (*CORE_CITIES, *model_params, min_date, max_date)
    runtime_model_set = set(model_params) if model_params else set(side_runtime_models)
    side_orders_scope = [r for r in side_orders if in_scope_runtime(r, runtime_model_set, min_date, max_date)]
    v1_orders_scope = [r for r in v1_orders if in_scope_runtime(r, runtime_model_set, min_date, max_date)]
    side_plans_scope = [r for r in side_plans if in_scope_runtime(r, runtime_model_set, min_date, max_date)]
    v1_plans_scope = [r for r in v1_plans if in_scope_runtime(r, runtime_model_set, min_date, max_date)]

    db_stat = DB_PATH.stat()
    snapshot = one(
        conn,
        """
        SELECT COUNT(*) AS total_rows,
               SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_rows,
               SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) AS missing_bracket_rows,
               SUM(CASE WHEN settlement_status IS NULL OR settlement_status='unsettled' THEN 1 ELSE 0 END) AS unsettled_rows,
               SUM(CASE WHEN settlement_status='settled' AND pnl_usd_at_fill IS NULL THEN 1 ELSE 0 END) AS settled_null_pnl,
               MAX(fact_built_at_utc) AS fact_built_at_utc
        FROM fact_trades
        """,
    )
    class_rows = rows(
        conn,
        """
        SELECT trade_class, COUNT(*) AS rows,
               SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_rows
        FROM fact_trades
        GROUP BY trade_class
        ORDER BY trade_class
        """,
    )
    live_real_rows = next((r["rows"] for r in class_rows if r["trade_class"] == "live_real"), 0)
    join_rows = rows(
        conn,
        """
        SELECT COALESCE(settlement_join_method, 'null') AS settlement_join_method,
               COALESCE(settlement_status, 'null') AS settlement_status,
               COUNT(*) AS rows
        FROM fact_trades
        GROUP BY settlement_join_method, settlement_status
        ORDER BY rows DESC
        LIMIT 12
        """,
    )
    cand_snapshot = one(
        conn,
        """
        SELECT COUNT(*) AS rows,
               SUM(CASE WHEN eligible=1 THEN 1 ELSE 0 END) AS eligible_rows,
               SUM(CASE WHEN decision_window_missing=1 THEN 1 ELSE 0 END) AS decision_window_missing_rows
        FROM fact_signal_candidates
        """,
    )

    fair_summary = rows(
        conn,
        f"""
        WITH live_base AS (
          SELECT *, {INSTANCE_EXPR} AS strategy_instance
          FROM fact_trades
          WHERE trade_class='live_real'
            AND city IN ({core_placeholders})
            {trade_model_filter_sql}
            AND target_date BETWEEN ? AND ?
        )
        SELECT strategy_instance,
               COUNT(*) AS fills,
               SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled,
               SUM(CASE WHEN settlement_status='missing_bracket' THEN 1 ELSE 0 END) AS missing_bracket,
               SUM(CASE WHEN settlement_status IS NULL OR settlement_status='unsettled' THEN 1 ELSE 0 END) AS unsettled,
               COUNT(DISTINCT city) AS cities,
               COUNT(DISTINCT target_date) AS target_days,
               SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS cost,
               SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
               SUM(CASE WHEN settlement_status='settled' AND win_by_count=1 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN settlement_status='settled' AND win_by_count=1 THEN cost_usd ELSE 0 END) AS winning_cost,
               AVG(CASE WHEN settlement_status='settled' THEN fill_price END) AS avg_fill,
               AVG(CASE WHEN settlement_status='settled' THEN plan_price END) AS avg_plan,
               AVG(CASE WHEN settlement_status='settled' THEN fill_price-plan_price END) AS avg_fill_minus_plan,
               AVG(CASE WHEN settlement_status='settled' THEN market_price END) AS avg_market,
               AVG(CASE WHEN settlement_status='settled' THEN model_p_yes END) AS avg_model_p_yes,
               AVG(CASE WHEN settlement_status='settled' THEN edge END) AS avg_edge,
               AVG(CASE WHEN settlement_status='settled' THEN abs_edge END) AS avg_abs_edge,
               AVG(CASE WHEN settlement_status='settled' THEN hours_to_settle END) AS avg_hts
        FROM live_base
        WHERE strategy_instance IN ('mid_price_core_v1_25_75','mid_price_core_v1_side_band','mid_price_core_v2_25_75')
        GROUP BY strategy_instance
        ORDER BY strategy_instance
        """,
        base_params,
    )

    by_side = rows(
        conn,
        f"""
        WITH live_base AS (
          SELECT *, {INSTANCE_EXPR} AS strategy_instance
          FROM fact_trades
          WHERE trade_class='live_real'
            AND city IN ({core_placeholders})
            {trade_model_filter_sql}
            AND target_date BETWEEN ? AND ?
        )
        SELECT strategy_instance, model_version, side,
               COUNT(*) AS fills,
               SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled,
               SUM(CASE WHEN settlement_status='settled' THEN cost_usd ELSE 0 END) AS cost,
               SUM(CASE WHEN settlement_status='settled' THEN pnl_usd_at_fill ELSE 0 END) AS pnl,
               AVG(CASE WHEN settlement_status='settled' THEN CAST(win_by_count AS REAL) END) AS win_rate,
               AVG(CASE WHEN settlement_status='settled' THEN fill_price END) AS avg_fill,
               AVG(CASE WHEN settlement_status='settled' THEN edge END) AS avg_edge,
               AVG(CASE WHEN settlement_status='settled' THEN abs_edge END) AS avg_abs_edge,
               AVG(CASE WHEN settlement_status='settled' THEN hours_to_settle END) AS avg_hts
        FROM live_base
        WHERE strategy_instance IN ('mid_price_core_v1_25_75','mid_price_core_v1_side_band')
        GROUP BY strategy_instance, model_version, side
        ORDER BY model_version, side, strategy_instance
        """,
        base_params,
    )

    detail_rows = rows(
        conn,
        f"""
        SELECT target_date, order_date_bj, city, model_version, side, bracket,
               fill_price, plan_price, market_price, edge, abs_edge, hours_to_settle,
               cost_usd, pnl_usd_at_fill, settlement_status, final_yes
        FROM fact_trades
        WHERE trade_class='live_real'
          AND ({INSTANCE_EXPR})='mid_price_core_v1_side_band'
        ORDER BY target_date, city, side
        """,
    )

    dist_rows = []
    for instance in ("mid_price_core_v1_25_75", "mid_price_core_v1_side_band"):
        values = rows(
            conn,
            f"""
            WITH live_base AS (
              SELECT *, {INSTANCE_EXPR} AS strategy_instance
              FROM fact_trades
              WHERE trade_class='live_real'
                AND city IN ({core_placeholders})
                {trade_model_filter_sql}
                AND target_date BETWEEN ? AND ?
                AND settlement_status='settled'
            )
            SELECT fill_price, edge, abs_edge, hours_to_settle
            FROM live_base
            WHERE strategy_instance=?
            """,
            (*base_params, instance),
        )
        for field in ("fill_price", "edge", "abs_edge", "hours_to_settle"):
            vals = [float(r[field]) for r in values if r[field] is not None and math.isfinite(float(r[field]))]
            dist_rows.append(
                [
                    instance,
                    field,
                    len(vals),
                    num(min(vals), 3) if vals else "-",
                    num(percentile(vals, 0), 3) if vals else "-",
                    num(sum(vals) / len(vals), 3) if vals else "-",
                    num(percentile(vals, 2), 3) if vals else "-",
                    num(max(vals), 3) if vals else "-",
                ]
            )

    candidate_gate = rows(
        conn,
        f"""
        WITH universe AS (
          SELECT *,
                 CASE
                   WHEN decision_entry_price BETWEEN 0.25 AND 0.75 AND abs_edge >= 0.10
                     THEN 1 ELSE 0 END AS pass_v1_25_75,
                 CASE
                   WHEN side='BUY_YES' AND decision_entry_price BETWEEN 0.20 AND 0.45 AND abs_edge >= 0.20
                     THEN 1
                   WHEN side='BUY_NO' AND decision_entry_price BETWEEN 0.35 AND 0.65 AND abs_edge >= 0.10
                     THEN 1
                   ELSE 0 END AS pass_side_band
          FROM fact_signal_candidates
          WHERE city IN ({core_placeholders})
            AND city_pool='t1_trading'
            {candidate_model_filter_sql}
            AND event_date BETWEEN ? AND ?
            AND final_yes IS NOT NULL
            AND decision_window_missing=0
        )
        SELECT model_version, side,
               COUNT(*) AS evaluable,
               SUM(pass_v1_25_75) AS pass_v1_25_75,
               SUM(pass_side_band) AS pass_side_band,
               SUM(CASE WHEN pass_v1_25_75=1 AND pass_side_band=1 THEN 1 ELSE 0 END) AS pass_both,
               SUM(CASE WHEN pass_v1_25_75=1 AND pass_side_band=0 THEN 1 ELSE 0 END) AS v1_only,
               SUM(CASE WHEN pass_v1_25_75=0 AND pass_side_band=1 THEN 1 ELSE 0 END) AS side_only,
               SUM(CASE WHEN pass_side_band=1 THEN counterfactual_pnl ELSE 0 END) AS side_cf_pnl,
               SUM(CASE WHEN pass_v1_25_75=1 THEN counterfactual_pnl ELSE 0 END) AS v1_cf_pnl,
               AVG(CASE WHEN pass_side_band=1 THEN decision_entry_price END) AS side_avg_entry,
               AVG(CASE WHEN pass_v1_25_75=1 THEN decision_entry_price END) AS v1_avg_entry,
               AVG(CASE WHEN pass_side_band=1 THEN abs_edge END) AS side_avg_abs_edge,
               AVG(CASE WHEN pass_v1_25_75=1 THEN abs_edge END) AS v1_avg_abs_edge
        FROM universe
        GROUP BY model_version, side
        ORDER BY model_version, side
        """,
        base_params,
    )

    side_runs = summarize_side_band_runs()
    totals = side_runs["totals"]
    skipped = totals["skipped"]
    latest_cfg = side_runs["latest_config"]

    lines: list[str] = []
    lines.append("# Side-band 策略入场差异分析（同模型口径）\n")
    lines.append("## 数据快照\n")
    lines.append(
        "- 目标指标：`side_band_same_model_entry_delta` = 优先在 `trade_class='live_real'` 下比较 realized PnL；"
        "若 live_real 因 CLOB fill sync 缺失不可用，则降级到 synced live `plans/` 与 `live/` JSONL，"
        "在同一模型、core 9 城、同目标日期窗口下比较 submitted order 的入场价、edge、quote spread，"
        "并用 `fact_signal_candidates` 做 entry band gate 诊断。"
    )
    lines.append(f"- 数据源：`runtime/weather.db.fact_trades` + `runtime/weather.db.fact_signal_candidates`。")
    lines.append(f"- DB last modified：{datetime.fromtimestamp(db_stat.st_mtime).astimezone().isoformat(timespec='seconds')}。")
    lines.append(f"- fact built：{snapshot.get('fact_built_at_utc') or '-'}。")
    lines.append(f"- 同模型范围：{model_filter_label}。")
    lines.append(f"- 公平窗口：core 9 城，`target_date={min_date}..{max_date}`，来自 side-band synced plan/order 的目标日期窗口。")
    lines.append(f"- 记录行数：fact_trades {snapshot.get('total_rows')} rows；settled {snapshot.get('settled_rows')}。")
    lines.append(
        f"- 降级口径：side-band scoped accepted plans {len(side_plans_scope)} / submitted live orders {len(side_orders_scope)}；"
        f"v1 25-75 scoped accepted plans {len(v1_plans_scope)} / submitted live orders {len(v1_orders_scope)}。"
    )
    lines.append(
        f"- unsettled/null 占比：{snapshot.get('unsettled_rows')} / {snapshot.get('total_rows')} = "
        f"{pct(ratio(snapshot.get('unsettled_rows'), snapshot.get('total_rows')))}。"
    )
    lines.append(f"- missing_bracket 数：{snapshot.get('missing_bracket_rows')}。")
    lines.append(
        f"- fact_signal_candidates：{cand_snapshot.get('rows')} rows；eligible {cand_snapshot.get('eligible_rows')}；"
        f"decision_window_missing {cand_snapshot.get('decision_window_missing_rows')} "
        f"({pct(ratio(cand_snapshot.get('decision_window_missing_rows'), cand_snapshot.get('rows')))})。"
    )
    lines.append("\n完整性自检：\n")
    lines.append(
        md_table(
            ["check", "value"],
            [
                ["settled rows with null PnL", snapshot.get("settled_null_pnl")],
                *[[f"{r['trade_class']} rows / settled", f"{r['rows']} / {r['settled_rows']}"] for r in class_rows],
            ],
        )
    )
    lines.append("结算 join/status Top 12：\n")
    lines.append(
        md_table(
            ["join_method", "status", "rows"],
            [[r["settlement_join_method"], r["settlement_status"], r["rows"]] for r in join_rows],
        )
    )

    lines.append("## 结论先行\n")
    lines.append(
        "交易动作：**side-band 目前不应扩大 live size；应该继续 shadow/极小 size。** "
        "当前 DB 已恢复出部分 `live_real`，但按 `producer_run_id` 严格归因后 side-band 暂无 `live_real`，"
        "不能用这批数据判断 realized EV。"
    )
    lines.append(
        "入场行为上，side-band 已经把样本压得很窄：同 ecmwf/gfs、core 9、同 target_date 窗口里，"
        "它主要提交 BUY_NO 的 35-65c 中价带订单；YES 需要更高 edge，submitted 样本几乎被压没。"
        "这符合配置意图，但意味着继续原样跑很慢才会有统计功效。"
    )

    lines.append("## Fair Live Fill 对照\n")
    if not fair_summary:
        lines.append(
            f"`fact_trades` 当前有 live_real={live_real_rows}，但同模型/core 9/side-band 活跃 target_date 窗口内没有可比 live_real fill。"
            "因此本节不能给公平 realized PnL。\n"
        )
    fair_table = []
    for r in fair_summary:
        roi = ratio(r.get("pnl"), r.get("cost"))
        fair_table.append(
            [
                r["strategy_instance"],
                r["fills"],
                r["settled"],
                f"{r['missing_bracket']} / {r['unsettled']}",
                r["cities"],
                r["target_days"],
                num(r["cost"]),
                money(r["pnl"]),
                pct(roi),
                pct(ratio(r["wins"], r["settled"])),
                pct(ratio(r["winning_cost"], r["cost"])),
                num(r["avg_fill"], 3),
                num(r["avg_fill_minus_plan"], 4),
                num(r["avg_abs_edge"], 3),
                num(r["avg_hts"], 1),
            ]
        )
    lines.append(
        md_table(
            [
                "strategy_instance",
                "fills",
                "settled",
                "missing/unsettled",
                "cities",
                "days",
                "cost",
                "pnl",
                "ROI",
                "win_count",
                "win_notional",
                "avg_fill",
                "fill-plan",
                "avg_abs_edge",
                "avg_hts",
            ],
            fair_table,
        )
    )

    lines.append("## Submitted Order 入场对照（降级口径）\n")
    runtime_summary = [
        ("v1 25-75", "accepted plans", summarize_runtime(v1_plans_scope)),
        ("v1 25-75", "submitted live orders", summarize_runtime(v1_orders_scope)),
        ("side-band", "accepted plans", summarize_runtime(side_plans_scope)),
        ("side-band", "submitted live orders", summarize_runtime(side_orders_scope)),
    ]
    lines.append(
        md_table(
            [
                "strategy",
                "layer",
                "n",
                "cities",
                "days",
                "models",
                "side_mix",
                "avg_entry",
                "avg_market",
                "avg_edge",
                "avg_quote_edge",
                "avg_quote_spread",
            ],
            [
                [
                    name,
                    layer,
                    s["n"],
                    s["cities"],
                    s["days"],
                    s["models"],
                    s["sides"],
                    num(s["avg_entry"], 3),
                    num(s["avg_market"], 3),
                    num(s["avg_edge"], 3),
                    num(s["avg_quote_edge"], 3),
                    num(s["avg_quote_spread"], 3),
                ]
                for name, layer, s in runtime_summary
            ],
        )
    )
    order_side_rows: list[list[Any]] = []
    for strategy_name, scoped_orders in (("v1 25-75", v1_orders_scope), ("side-band", side_orders_scope)):
        for r in runtime_by_side(scoped_orders):
            order_side_rows.append(
                [
                    strategy_name,
                    r["model_version"],
                    r["side"],
                    r["n"],
                    r["cities"],
                    r["days"],
                    num(r["avg_entry"], 3),
                    f"{num(r['min_entry'], 3)}-{num(r['max_entry'], 3)}",
                    num(r["avg_market"], 3),
                    num(r["avg_edge"], 3),
                    num(r["avg_quote_edge"], 3),
                    num(r["avg_quote_spread"], 3),
                ]
            )
    lines.append(
        md_table(
            [
                "strategy",
                "model",
                "side",
                "orders",
                "cities",
                "days",
                "avg_entry",
                "entry_range",
                "avg_market",
                "avg_edge",
                "avg_quote_edge",
                "avg_quote_spread",
            ],
            order_side_rows,
        )
    )
    lines.append("side-band submitted live orders：\n")
    lines.append(
        md_table(
            ["target_date", "created_utc", "city", "model", "side", "bracket", "posted", "market", "edge", "quote_edge", "spread", "status"],
            [
                [
                    r.get("target_date", "-"),
                    r.get("created_at_utc", "-"),
                    r.get("city", "-"),
                    r.get("model_version", "-"),
                    r.get("signal_side", "-"),
                    r.get("bracket", "-"),
                    num(r.get("posted_price") or r.get("limit_price"), 3),
                    num(r.get("market_price"), 3),
                    num(r.get("edge"), 3),
                    num(r.get("quote_edge"), 3),
                    num(r.get("quote_spread"), 3),
                    r.get("status", "-"),
                ]
                for r in side_orders_scope
            ],
        )
    )

    lines.append("## 按模型和方向\n")
    lines.append(
        md_table(
            ["strategy_instance", "model", "side", "fills", "settled", "cost", "pnl", "win_rate", "avg_fill", "avg_edge", "avg_abs_edge", "avg_hts"],
            [
                [
                    r["strategy_instance"],
                    r["model_version"],
                    r["side"],
                    r["fills"],
                    r["settled"],
                    num(r["cost"]),
                    money(r["pnl"]),
                    pct(r["win_rate"]),
                    num(r["avg_fill"], 3),
                    num(r["avg_edge"], 3),
                    num(r["avg_abs_edge"], 3),
                    num(r["avg_hts"], 1),
                ]
                for r in by_side
            ],
        )
    )

    lines.append("## 入场分布\n")
    lines.append(
        md_table(
            ["strategy_instance", "field", "n", "min", "p25", "avg", "p75", "max"],
            dist_rows,
        )
    )

    lines.append("## side-band 实际成交明细\n")
    lines.append(
        md_table(
            ["target_date", "order_date_bj", "city", "model", "side", "bracket", "fill", "plan", "market", "edge", "abs_edge", "hts", "cost", "pnl", "status", "final_yes"],
            [
                [
                    r["target_date"],
                    r["order_date_bj"],
                    r["city"],
                    r["model_version"],
                    r["side"],
                    r["bracket"],
                    num(r["fill_price"], 3),
                    num(r["plan_price"], 3),
                    num(r["market_price"], 3),
                    num(r["edge"], 3),
                    num(r["abs_edge"], 3),
                    num(r["hours_to_settle"], 1),
                    num(r["cost_usd"]),
                    money(r["pnl_usd_at_fill"]),
                    r["settlement_status"],
                    num(r["final_yes"], 1),
                ]
                for r in detail_rows
            ],
        )
    )

    lines.append("## 候选机会层：entry band gate 差异\n")
    lines.append(
        "这段是机会粒度诊断，不是成交 PnL。分母只取 `final_yes IS NOT NULL AND decision_window_missing=0`，"
        "并在同模型、core 9、同日期窗口内用配置规则重放 entry band gate："
        "v1 = `0.25-0.75 + abs_edge>=0.10`；side-band YES = `0.20-0.45 + abs_edge>=0.20`，"
        "NO = `0.35-0.65 + abs_edge>=0.10`。"
    )
    lines.append(
        md_table(
            [
                "model",
                "side",
                "evaluable",
                "pass_v1",
                "pass_side",
                "both",
                "v1_only",
                "side_only",
                "v1_cf_pnl",
                "side_cf_pnl",
                "v1_avg_entry",
                "side_avg_entry",
                "v1_abs_edge",
                "side_abs_edge",
            ],
            [
                [
                    r["model_version"],
                    r["side"],
                    r["evaluable"],
                    r["pass_v1_25_75"],
                    r["pass_side_band"],
                    r["pass_both"],
                    r["v1_only"],
                    r["side_only"],
                    money(r["v1_cf_pnl"]),
                    money(r["side_cf_pnl"]),
                    num(r["v1_avg_entry"], 3),
                    num(r["side_avg_entry"], 3),
                    num(r["v1_avg_abs_edge"], 3),
                    num(r["side_avg_abs_edge"], 3),
                ]
                for r in candidate_gate
            ],
        )
    )

    lines.append("## live_cycle 链路统计\n")
    lines.append(
        md_table(
            ["layer", "count"],
            [
                ["side-band runs", totals["runs"]],
                ["records scanned", totals["records"]],
                ["candidate_signals", totals["candidate_signals"]],
                ["signals", totals["signals"]],
                ["accepted before live dedup", totals["accepted_before_live_dedup"]],
                ["accepted after live dedup", totals["accepted"]],
                ["live_orders", totals["live_orders"]],
                ["paper_written", totals["paper_written"]],
            ],
        )
    )
    top_skips = skipped.most_common(10)
    lines.append("主要 filter / skip：\n")
    lines.append(md_table(["reason", "count"], [[k, v] for k, v in top_skips]))
    lines.append("最新配置摘要：\n")
    lines.append(
        md_table(
            ["key", "value"],
            [
                ["allowed_cities", latest_cfg.get("allowed_cities", "-")],
                ["execution_policy", latest_cfg.get("execution_policy", "-")],
                ["min/max hours", f"{latest_cfg.get('min_hours_to_settle', '-')}-{latest_cfg.get('max_hours_to_settle', '-')}"],
                ["YES band / edge", f"{latest_cfg.get('yes_min_entry_price', '-')}-{latest_cfg.get('yes_max_entry_price', '-')} / {latest_cfg.get('yes_min_edge', '-')}"],
                ["NO band / edge", f"{latest_cfg.get('no_min_entry_price', '-')}-{latest_cfg.get('no_max_entry_price', '-')} / {latest_cfg.get('no_min_edge', '-')}"],
            ],
        )
    )

    lines.append("## 建议\n")
    lines.append(
        "1. **不扩大 side-band live。** 当前没有可用 live_real PnL，不能因为 submitted order 的入场更漂亮就加 size。"
    )
    lines.append(
        "2. **把实验从 live PnL 判断改成 entry gate 判断。** 先看同模型 candidate universe 中 `v1_only / side_only / both` 的 settled 反事实表现，"
        "等 pass_side 的可估值机会至少达到几十个 city-day 后再回到 live fill PnL。"
    )
    lines.append(
        "3. **优先放宽一个维度而不是同时改多处。** 如果想提高样本，最干净的是保留 core 9 和 22-28h，"
        "先把 YES edge 从 0.20 降到 0.15；NO 35-65c 可以先不动，因为它是这版 side-band 最核心的风险控制。"
    )
    lines.append(
        "4. **v1 主路径暂不因 side-band 改动。** side-band 当前更像一个入场过滤实验，不足以替代 v1 25-75。"
    )

    return "\n".join(lines) + "\n"


def main() -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = build_report()
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(REPORT_PATH)


if __name__ == "__main__":
    main()
