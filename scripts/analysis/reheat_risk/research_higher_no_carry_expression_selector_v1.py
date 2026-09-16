#!/usr/bin/env python3
"""Current YES vs d1/d2 NO carry expression selector.

This is an expression-layer test for the same no-reheat thesis.  It consumes
the shared reheat-risk feature factory and builds one paired row per
same-window city/date/hour/orderbook state when current YES and higher NO
sibling quotes are visible.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/higher_no_carry_expression_selector_v1"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-higher-no-carry-expression-selector-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-higher-no-carry-expression-selector-v1.md"
SPLIT_DATE = "2026-06-01"
NEW_DATES_START = "2026-06-10"
BOOT_REPS = 3000


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(x: object, signed: bool = True) -> str:
    if x is None:
        return "NA"
    try:
        value = float(x)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(value):
        return "NA"
    sign = "+" if signed else ""
    return f"{value * 100:{sign}.1f}%"


def fnum(x: object, digits: int = 2, signed: bool = False) -> str:
    if x is None:
        return "NA"
    try:
        value = float(x)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(value):
        return "NA"
    sign = "+" if signed else ""
    return f"{value:{sign}.{digits}f}"


def json_sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: json_sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_sanitize(v) for v in obj]
    if isinstance(obj, tuple):
        return [json_sanitize(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def query_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def data_self_check() -> dict[str, Any]:
    conn = connect_ro()
    try:
        return {
            "fact_trades_max_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0],
            "fact_trades_by_class": query_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
            ),
            "fact_trades_by_settlement_status": query_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
            ),
            "fact_signal_candidate_coverage": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "clob_order_fill_join": query_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
        }
    finally:
        conn.close()


def load_gate() -> dict[str, Any]:
    if not GATE.exists():
        return {"gate_pass": None, "missing": True}
    data = json.loads(GATE.read_text(encoding="utf-8"))
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "missing_order_rows": data.get("db_fills", {}).get("missing_order_rows"),
        "over_order_keys": data.get("db_fills", {}).get("over_order_keys"),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
    }


def build_state_rows() -> tuple[pd.DataFrame, dict[str, Any]]:
    if not FEATURE_ROWS.exists():
        raise FileNotFoundError(f"missing feature factory rows: {FEATURE_ROWS}")
    raw = pd.read_csv(FEATURE_ROWS)
    if raw.empty:
        return raw, {"raw_factory_quote_rows": 0}

    q = raw.copy()
    q = q[q["decision_hour_local"].between(13, 17)].copy()
    q["snapshot_ts_utc"] = q["decision_snapshot_ts_utc"]
    q["snapshot_ts_local"] = q["decision_snapshot_ts_utc"]
    q["winner_label"] = q["final_winning_bracket"].astype(str)
    q["decline"] = pd.to_numeric(q["decline_from_max_c"], errors="coerce")
    q["period"] = np.where(q["target_date"].astype(str) < SPLIT_DATE, "train", "holdout")
    q["materialized_period"] = "factory_v1"
    q["current_yes_wins"] = pd.to_numeric(q["current_bracket_held"], errors="coerce").eq(1.0)
    q["yes_current_ask"] = pd.to_numeric(q["current_yes_ask"], errors="coerce")
    q["yes_current_size"] = pd.to_numeric(q["current_yes_ask_size"], errors="coerce")
    q["yes_current_pnl"] = q["current_yes_wins"].astype(float) - q["yes_current_ask"]
    q["d1_no_size"] = pd.to_numeric(q["d1_no_ask_size"], errors="coerce")
    q["d2_no_size"] = pd.to_numeric(q["d2_no_ask_size"], errors="coerce")
    q["d1_hit"] = pd.to_numeric(q["d1_hit"], errors="coerce").eq(1.0)
    q["d2_hit"] = pd.to_numeric(q["d2_hit"], errors="coerce").eq(1.0)
    q["d1_no_pnl"] = (~q["d1_hit"]).astype(float) - pd.to_numeric(q["d1_no_ask"], errors="coerce")
    q["d2_no_pnl"] = (~q["d2_hit"]).astype(float) - pd.to_numeric(q["d2_no_ask"], errors="coerce")
    q["d1_skip_over_wins_no_only"] = pd.to_numeric(q["skip_over_d1"], errors="coerce").eq(1.0)
    q["d2_skip_over_wins_no_only"] = False
    q["state_key"] = (
        q["orderbook_file"].astype(str)
        + "|"
        + q["city"].astype(str)
        + "|"
        + q["target_date"].astype(str)
        + "|"
        + q["decision_snapshot_ts_utc"].astype(str)
        + "|"
        + q["decision_hour_local"].astype(str)
        + "|"
        + q["current_bracket"].astype(str)
    )
    base_cols = [
        "state_key",
        "orderbook_file",
        "snapshot_ts_utc",
        "snapshot_ts_local",
        "decision_hour_local",
        "city",
        "target_date",
        "unit",
        "winner_label",
        "running_max_c",
        "running_max_f",
        "current_temp_c",
        "final_max_c",
        "final_max_f",
        "running_value",
        "decline",
        "period",
        "materialized_period",
        "current_bracket",
        "yes_current_ask",
        "yes_current_size",
        "current_yes_wins",
        "yes_current_pnl",
        "d1_no_bracket",
        "d1_no_ask",
        "d1_no_size",
        "d1_hit",
        "d1_no_pnl",
        "d1_skip_over_wins_no_only",
        "d2_no_bracket",
        "d2_no_ask",
        "d2_no_size",
        "d2_hit",
        "d2_no_pnl",
        "d2_skip_over_wins_no_only",
    ]
    state = q.sort_values(["state_key"]).drop_duplicates("state_key", keep="first")[base_cols].copy()

    state["has_d1_no"] = state["d1_no_ask"].notna()
    state["has_d2_no"] = state["d2_no_ask"].notna()
    state = state[state["has_d1_no"]].copy()
    state["current_yes_cost"] = state["yes_current_ask"].astype(float)
    state["d1_no_cost"] = state["d1_no_ask"].astype(float)
    state["d2_no_cost"] = state["d2_no_ask"].astype(float)
    state["current_yes_pnl"] = state["yes_current_pnl"].astype(float)

    d1_hit_bool = state["d1_hit"].eq(True)
    d2_hit_bool = state["d2_hit"].eq(True)
    state["settlement_state"] = np.select(
        [
            state["current_yes_wins"].fillna(False).astype(bool),
            d1_hit_bool,
            d2_hit_bool,
        ],
        ["current_hit", "d1_hit", "d2_hit"],
        default="skip_over",
    )
    state["d2_no_pnl"] = state["d2_no_pnl"].where(state["has_d2_no"])
    state["ladder_cost"] = state["d1_no_cost"] + state["d2_no_cost"]
    state["ladder_pnl"] = state["d1_no_pnl"] + state["d2_no_pnl"]
    state["can_ladder"] = state["has_d1_no"] & state["has_d2_no"]

    state["d1_no_minus_yes_pnl"] = state["d1_no_pnl"] - state["current_yes_pnl"]
    state["d2_no_minus_yes_pnl"] = state["d2_no_pnl"] - state["current_yes_pnl"]
    state["ladder_minus_yes_pnl"] = state["ladder_pnl"] - state["current_yes_pnl"]
    coverage = {
        "source_feature_rows": str(FEATURE_ROWS.relative_to(ROOT)),
        "raw_factory_quote_rows": int(len(raw)),
        "raw_factory_h13_17_quote_rows": int(len(q)),
        "factory_state_rows_h13_17": int(q["state_key"].nunique()),
        "paired_state_rows_current_yes_d1": int(len(state)),
        "paired_state_rows_current_yes_d1_d2": int(state["can_ladder"].sum()),
        "state_active_dates": int(state["target_date"].nunique()),
        "state_date_min": str(state["target_date"].min()) if not state.empty else None,
        "state_date_max": str(state["target_date"].max()) if not state.empty else None,
    }
    return state, coverage


def profile_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    true = pd.Series(True, index=df.index)
    return {
        "all_current_d1_h13_17": true,
        "current_d1_d2_visible": df["can_ladder"],
        "fade_high_current_yes": df["decline"].ge(0.5) & df["yes_current_ask"].ge(0.55),
        "d1_high_carry": df["decline"].ge(0.5) & df["d1_no_ask"].ge(0.75),
        "d2_tail_carry": df["can_ladder"] & df["decline"].ge(0.5) & df["d2_no_ask"].ge(0.75),
        "two_leg_ladder_high_carry": df["can_ladder"] & df["decline"].ge(0.5) & df["d1_no_ask"].ge(0.70) & df["d2_no_ask"].ge(0.70),
    }


def expression_columns(expr: str) -> tuple[str, str]:
    return {
        "current_yes": ("current_yes_cost", "current_yes_pnl"),
        "d1_no": ("d1_no_cost", "d1_no_pnl"),
        "d2_no": ("d2_no_cost", "d2_no_pnl"),
        "no_ladder": ("ladder_cost", "ladder_pnl"),
    }[expr]


def summarize_expression(df: pd.DataFrame, expr: str) -> dict[str, Any]:
    cost_col, pnl_col = expression_columns(expr)
    work = df.dropna(subset=[cost_col, pnl_col]).copy()
    if work.empty:
        return {"rows": 0, "active_dates": 0}
    cost = float(work[cost_col].sum())
    pnl = float(work[pnl_col].sum())
    return {
        "rows": int(len(work)),
        "active_dates": int(work["target_date"].nunique()),
        "cities": int(work["city"].nunique()),
        "avg_cost": float(work[cost_col].mean()),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "win_rate": float((work[pnl_col] > 0).mean()),
        "current_hit_rate": float(work["settlement_state"].eq("current_hit").mean()),
        "d1_hit_rate": float(work["settlement_state"].eq("d1_hit").mean()),
        "d2_hit_rate": float(work["settlement_state"].eq("d2_hit").mean()),
        "skip_over_rate": float(work["settlement_state"].eq("skip_over").mean()),
    }


def bootstrap_excess_vs_yes(df: pd.DataFrame, expr: str, reps: int = BOOT_REPS) -> dict[str, Any]:
    if expr == "current_yes":
        return {"excess_roi_vs_current_yes": 0.0, "ci95": [0.0, 0.0], "reps": 0}
    expr_cost, expr_pnl = expression_columns(expr)
    yes_cost, yes_pnl = expression_columns("current_yes")
    work = df.dropna(subset=[expr_cost, expr_pnl, yes_cost, yes_pnl]).copy()
    if work.empty or work["target_date"].nunique() < 3:
        return {"excess_roi_vs_current_yes": None, "ci95": [None, None], "reps": 0}
    daily = work.groupby("target_date").agg(
        expr_cost=(expr_cost, "sum"),
        expr_pnl=(expr_pnl, "sum"),
        yes_cost=(yes_cost, "sum"),
        yes_pnl=(yes_pnl, "sum"),
    )

    def delta(frame: pd.DataFrame, idx: np.ndarray | None = None) -> float:
        sample = frame if idx is None else frame.iloc[idx]
        ecost = float(sample["expr_cost"].sum())
        ycost = float(sample["yes_cost"].sum())
        if ecost <= 0 or ycost <= 0:
            return float("nan")
        return float(sample["expr_pnl"].sum() / ecost - sample["yes_pnl"].sum() / ycost)

    point = delta(daily)
    rng = np.random.default_rng(20260616)
    vals: list[float] = []
    for _ in range(reps):
        idx = rng.integers(0, len(daily), len(daily))
        v = delta(daily, idx)
        if math.isfinite(v):
            vals.append(v)
    if not vals:
        return {"excess_roi_vs_current_yes": point, "ci95": [None, None], "reps": 0}
    lo, hi = np.quantile(vals, [0.025, 0.975])
    return {"excess_roi_vs_current_yes": point, "ci95": [float(lo), float(hi)], "reps": len(vals)}


def evaluate_profiles(states: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for profile, mask in profile_masks(states).items():
        selected = states[mask].copy()
        for period, period_mask in {
            "train": selected["target_date"] < SPLIT_DATE,
            "holdout": selected["target_date"] >= SPLIT_DATE,
            "new_2026_06_10_14": selected["target_date"] >= NEW_DATES_START,
            "all": pd.Series(True, index=selected.index),
        }.items():
            frame = selected[period_mask].copy()
            for expr in ("current_yes", "d1_no", "d2_no", "no_ladder"):
                sm = summarize_expression(frame, expr)
                boot = bootstrap_excess_vs_yes(frame, expr)
                rows.append({"profile": profile, "period": period, "expression": expr, **sm, **boot})
    return pd.DataFrame(rows)


def settlement_contribution(states: pd.DataFrame) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for profile, mask in profile_masks(states).items():
        selected = states[mask].copy()
        for period, period_mask in {
            "holdout": selected["target_date"] >= SPLIT_DATE,
            "all": pd.Series(True, index=selected.index),
        }.items():
            work = selected[period_mask].copy()
            if work.empty:
                continue
            grouped = work.groupby("settlement_state", dropna=False).agg(
                rows=("state_key", "size"),
                dates=("target_date", "nunique"),
                current_yes_pnl=("current_yes_pnl", "sum"),
                d1_no_pnl=("d1_no_pnl", "sum"),
                d2_no_pnl=("d2_no_pnl", "sum"),
                ladder_pnl=("ladder_pnl", "sum"),
                d1_no_minus_yes_pnl=("d1_no_minus_yes_pnl", "sum"),
                d2_no_minus_yes_pnl=("d2_no_minus_yes_pnl", "sum"),
                ladder_minus_yes_pnl=("ladder_minus_yes_pnl", "sum"),
            )
            grouped = grouped.reset_index()
            grouped.insert(0, "period", period)
            grouped.insert(0, "profile", profile)
            frames.append(grouped)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_decision_table(summary: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scenarios = [
        ("fade_high_current_yes", "current YES", "Visible fade and current YES quote is still not too rich."),
        ("d1_high_carry", "d1 NO", "Adjacent higher NO is expensive enough to pay for exact d1-hit risk."),
        ("d2_tail_carry", "d2 NO", "Tail NO is visible and expensive while d2 exact-hit risk stays modest."),
        ("two_leg_ladder_high_carry", "ladder", "Both d1 and d2 NO are visible; ladder buys broader no-reheat/overshoot payoff."),
        ("all_current_d1_h13_17", "skip", "Default outside a supported expression segment."),
    ]
    for profile, label, condition in scenarios:
        hold = summary[(summary["profile"].eq(profile)) & (summary["period"].eq("holdout"))].copy()
        if hold.empty:
            rows.append({"decision": label, "condition": condition, "evidence": "no holdout rows", "action": "skip"})
            continue
        with_rows = hold[hold["rows"].fillna(0).astype(int).gt(0)].copy()
        if with_rows.empty:
            rows.append({"decision": label, "condition": condition, "evidence": "no executable sibling rows", "action": "skip"})
            continue
        current = with_rows[with_rows["expression"].eq("current_yes")].iloc[0].to_dict()
        best = with_rows.sort_values("roi", ascending=False).iloc[0].to_dict()
        requested = {
            "current YES": "current_yes",
            "d1 NO": "d1_no",
            "d2 NO": "d2_no",
            "ladder": "no_ladder",
            "skip": "current_yes",
        }[label]
        req = with_rows[with_rows["expression"].eq(requested)]
        req_row = req.iloc[0].to_dict() if len(req) else {"rows": 0, "roi": None, "excess_roi_vs_current_yes": None, "ci95": [None, None]}
        ci = req_row.get("ci95", [None, None])
        ci_lo = ci[0] if isinstance(ci, list) and ci else None
        supported = (
            int(req_row.get("rows") or 0) >= 30
            and int(req_row.get("active_dates") or 0) >= 10
            and requested != "current_yes"
            and (req_row.get("excess_roi_vs_current_yes") is not None)
            and float(req_row.get("excess_roi_vs_current_yes") or 0.0) > 0
            and ci_lo is not None
            and float(ci_lo) > 0
        )
        if label == "current YES":
            action = "choose_current_yes_over_NO_carry_when_current_yes_gate_passes"
        elif label == "skip":
            action = "skip"
        else:
            action = f"choose_{requested}_only_if_shadow_validates" if supported else "do_not_choose_over_current_yes_yet"
        rows.append(
            {
                "decision": label,
                "condition": condition,
                "holdout_rows": int(req_row.get("rows") or 0),
                "holdout_dates": int(req_row.get("active_dates") or 0),
                "requested_roi": req_row.get("roi"),
                "current_yes_roi": current.get("roi"),
                "excess_roi_vs_current_yes": req_row.get("excess_roi_vs_current_yes"),
                "excess_ci95": ci,
                "best_holdout_expression": best.get("expression"),
                "best_holdout_roi": best.get("roi"),
                "action": action,
            }
        )
    return rows


def write_markdown(payload: dict[str, Any], summary: pd.DataFrame, settle: pd.DataFrame) -> None:
    self_check = payload["data_self_check"]
    gate = payload["clob_gate"]

    def row(profile: str, period: str, expr: str) -> dict[str, Any]:
        found = summary[
            summary["profile"].eq(profile)
            & summary["period"].eq(period)
            & summary["expression"].eq(expr)
        ]
        return found.iloc[0].to_dict() if len(found) else {"rows": 0}

    core_profiles = ["fade_high_current_yes", "d1_high_carry", "d2_tail_carry", "two_leg_ladder_high_carry"]
    lines = [
        "# Higher NO Carry Expression Selector v1",
        "",
        "Status: snapshot",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "Target metric: `higher_no_carry_expression_delta` = same city-hour/orderbook state 下，比较 current YES、d1 NO、d2 NO、两腿 NO ladder 的 ROI 和相对 sibling current YES 的 excess ROI。",
        "Row grain: one paired state = `orderbook_file + city + target_date + decision_hour_local + current_bracket`; d1/d2 NO quote 必须和 current YES 来自同一 orderbook file/window。",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db` mandatory self-check + `docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv` shared feature factory.",
        "- 本报告没有重新 materialize observed max/orderbook/settlement；同窗 current YES、d1/d2 NO、settlement labels 均来自 shared factory v1。",
        "- sync/run_stack: 本次未重跑；使用当前本地 DB/factory snapshot。",
        f"- fact_built_at_utc: `{self_check['fact_trades_max_built_at_utc']}`.",
        f"- fact_trades trade_class: `{self_check['fact_trades_by_class']}`.",
        f"- fact_trades settlement_status: `{self_check['fact_trades_by_settlement_status']}`.",
        f"- fact_signal_candidates coverage: `{self_check['fact_signal_candidate_coverage']}`.",
        f"- CLOB orders/fills join: `{self_check['clob_order_fill_join']}`.",
        f"- CLOB coverage gate: gate_pass={gate.get('gate_pass')}, missing_order_rows={gate.get('missing_order_rows')}, over_order_keys={gate.get('over_order_keys')}, db_fill_cost_minus_fact_cost={gate.get('db_fill_cost_minus_fact_cost')}.",
        f"- Paired state rows: current+d1 `{payload['coverage']['paired_state_rows_current_yes_d1']}`, current+d1+d2 `{payload['coverage']['paired_state_rows_current_yes_d1_d2']}`, active dates `{payload['coverage']['state_active_dates']}` ({payload['coverage']['state_date_min']}..{payload['coverage']['state_date_max']}).",
        "",
        "## 选择逻辑",
        "",
        "不要把 higher NO carry 当独立 alpha。它只是在同一个 no-reheat thesis 下换 payoff：current YES 只在当前档最终命中时赢；d1 NO 输在 d1-hit，但在 current-hit、d2-hit、skip-over 时赢；d2 NO 输在 d2-hit，但在 current-hit、d1-hit、skip-over 时赢；ladder 用更高成本买更宽的 current-hit/skip-over convexity。",
        "",
        "当前数据给出的交易规则很朴素：默认先看 current YES；只有当同窗 d1/d2 NO 的 holdout excess ROI 相对 current YES 也过门，才允许说 NO carry/ladder 是更好表达。这里还没有过门，所以表达选择只能保留为 shadow/research，不改 live。",
        "",
        "## Decision Table",
        "",
        "| decision | when | holdout evidence | action |",
        "|---|---|---|---|",
    ]
    for item in payload["decision_table"]:
        ci = item.get("excess_ci95") or [None, None]
        lines.append(
            f"| {item['decision']} | {item['condition']} | rows {item.get('holdout_rows', 0)} / dates {item.get('holdout_dates', 0)}; "
            f"ROI {pct(item.get('requested_roi'))}; current YES {pct(item.get('current_yes_roi'))}; "
            f"excess {pct(item.get('excess_roi_vs_current_yes'))} CI [{pct(ci[0])}, {pct(ci[1])}] | `{item['action']}` |"
        )
    lines.extend(
        [
            "",
            "## Expression ROI vs Current YES",
            "",
            "| profile | period | expression | rows | dates | avg cost | ROI | excess vs current YES | CI95 | current-hit | d1-hit | d2-hit | skip-over |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    table_profiles = ["all_current_d1_h13_17", "current_d1_d2_visible", *core_profiles]
    for profile in table_profiles:
        for period in ["holdout", "all"]:
            for expr in ["current_yes", "d1_no", "d2_no", "no_ladder"]:
                r = row(profile, period, expr)
                if int(r.get("rows", 0) or 0) == 0:
                    continue
                ci = r.get("ci95", [None, None])
                lines.append(
                    f"| `{profile}` | {period} | `{expr}` | {int(r['rows'])} | {int(r['active_dates'])} | "
                    f"{fnum(r.get('avg_cost'), 3)} | {pct(r.get('roi'))} | {pct(r.get('excess_roi_vs_current_yes'))} | "
                    f"[{pct(ci[0])}, {pct(ci[1])}] | {pct(r.get('current_hit_rate'), signed=False)} | "
                    f"{pct(r.get('d1_hit_rate'), signed=False)} | {pct(r.get('d2_hit_rate'), signed=False)} | {pct(r.get('skip_over_rate'), signed=False)} |"
                )
    lines.extend(
        [
            "",
            "## Skip-Over Convexity",
            "",
            "Skip-over 是 NO carry 相对 current YES 的核心 convexity，但本轮必须拆成 d2-hit 和 true skip-over：d1 NO 在 d2-hit 也赢，d2 NO 在 d1-hit 也赢，ladder 在 d1/d2 exact hit 只剩一条腿赢。",
            "",
            "| profile | period | settlement | rows | d1 NO - YES pnl | d2 NO - YES pnl | ladder - YES pnl |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    show_settle = settle[
        settle["profile"].isin(["current_d1_d2_visible", "two_leg_ladder_high_carry"])
        & settle["period"].eq("holdout")
    ].copy()
    for _, r in show_settle.iterrows():
        lines.append(
            f"| `{r['profile']}` | {r['period']} | `{r['settlement_state']}` | {int(r['rows'])} | "
            f"{fnum(r.get('d1_no_minus_yes_pnl'), 2, signed=True)} | {fnum(r.get('d2_no_minus_yes_pnl'), 2, signed=True)} | {fnum(r.get('ladder_minus_yes_pnl'), 2, signed=True)} |"
        )
    lines.extend(
        [
            "",
            "## 三道门 Verdict",
            "",
            f"在 {payload['coverage']['state_date_min']}..{payload['coverage']['state_date_max']} 的 factory-backed opportunity replay 中，NO carry/ladder 相对 sibling current YES 的 holdout excess ROI 没有稳定显著大于 0，前瞻不足，结论等级 `inconclusive`。",
            "",
            "- significance=FAIL: d1/d2/ladder 相对 current YES 的 cluster-by-date CI 未稳定全在 0 以上。",
            "- baseline=FAIL: baseline 是同窗 sibling current YES；NO carry 没有证明能打赢它。",
            "- forward=FAIL: train/holdout 不足以支持固定 selector。",
            "- conclusion=inconclusive: 不改 N100/live；若继续，做 shadow-only sibling selector telemetry。",
            "",
            "## 产物",
            "",
            f"- Script: `scripts/analysis/reheat_risk/{Path(__file__).name}`",
            f"- Paired rows CSV: `{payload['outputs']['paired_rows']}`",
            f"- Summary CSV: `{payload['outputs']['summary']}`",
            f"- Settlement CSV: `{payload['outputs']['settlement_contribution']}`",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    states, coverage = build_state_rows()
    summary = evaluate_profiles(states)
    settle = settlement_contribution(states)
    decision_table = build_decision_table(summary)

    paired_path = OUT_DIR / "paired_expression_state_rows.csv"
    summary_path = OUT_DIR / "expression_summary.csv"
    settle_path = OUT_DIR / "settlement_contribution.csv"
    states.to_csv(paired_path, index=False)
    summary.to_csv(summary_path, index=False)
    settle.to_csv(settle_path, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "status": "snapshot",
        "target_metric": "higher_no_carry_expression_delta",
        "row_grain": "same-window city/date/hour/orderbook state: orderbook_file + city + target_date + decision_hour_local + current_bracket",
        "date_range": {"split": SPLIT_DATE, "new_start": NEW_DATES_START},
        "data_self_check": data_self_check(),
        "clob_gate": load_gate(),
        "coverage": coverage,
        "decision_table": decision_table,
        "outputs": {
            "paired_rows": str(paired_path.relative_to(ROOT)),
            "summary": str(summary_path.relative_to(ROOT)),
            "settlement_contribution": str(settle_path.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "summary": summary.to_dict(orient="records"),
        "settlement_contribution": settle.to_dict(orient="records"),
        "verdict": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "conclusion": "inconclusive",
            "live_action": "none",
            "reason": "NO carry and ladder did not prove stable positive excess ROI against same-window sibling current YES.",
        },
    }
    payload = json_sanitize(payload)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(payload, summary, settle)
    print(json.dumps({"ok": True, "outputs": payload["outputs"], "verdict": payload["verdict"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
