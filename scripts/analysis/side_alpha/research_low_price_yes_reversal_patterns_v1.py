#!/usr/bin/env python3
"""Low-price BUY_YES reversal pattern research.

Local opportunity-layer research only. The metric is not high win rate; it is
where low-price BUY_YES rows produce right-tail reversals often enough to justify
a capped convexity sleeve shadow/paper trial.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime/weather.db"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-16-low-price-yes-reversal-patterns-v1.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-16-low-price-yes-reversal-patterns-v1.md"
GATE_JSON_DEFAULT = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
TARGET_METRIC = "low_price_yes_reversal_pattern_v1"
RNG_SEED = 20260616


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return "NA"
    return f"{value * 100:+.1f}%"


def num(value: float | None, digits: int = 2) -> str:
    if value is None or not np.isfinite(value):
        return "NA"
    return f"{value:+.{digits}f}"


def plain(value: float | None, digits: int = 2) -> str:
    if value is None or not np.isfinite(value):
        return "NA"
    return f"{value:.{digits}f}"


def safe_div(n: float, d: float) -> float | None:
    return n / d if d else None


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def sql_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def sql_scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def mandatory_self_check(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "max_fact_built_at_utc": sql_scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class_distribution": sql_rows(
            conn,
            "SELECT trade_class, COUNT(*) AS n FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
        ),
        "settlement_status_distribution": sql_rows(
            conn,
            "SELECT settlement_status, COUNT(*) AS n FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
        ),
        "candidate_coverage": sql_rows(
            conn,
            "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, SUM(live_filled) AS live_filled FROM fact_signal_candidates",
        ),
        "order_fill_coverage": sql_rows(
            conn,
            """
            SELECT o.status, COUNT(*) AS orders,
                   SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill
            FROM orders o
            LEFT JOIN fills f USING(execution_id)
            WHERE o.venue='polymarket_clob'
            GROUP BY o.status
            ORDER BY o.status
            """,
        ),
    }


def load_gate(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"gate_pass": None, "note": f"missing gate json: {path}"}
    payload = json.loads(path.read_text())
    return {
        "gate_pass": payload.get("gate_pass"),
        "fail_reasons": payload.get("fail_reasons"),
        "db_fill_cost_minus_fact_cost": payload.get("db_fill_cost_minus_fact_cost"),
        "db_vs_primary_cache": payload.get("db_vs_primary_cache"),
        "db_fills": payload.get("db_fills"),
        "fact_trades_live_real": payload.get("fact_trades_live_real"),
    }


def bucket(value: Any, bins: list[tuple[float, float, str]], *, missing: str = "missing") -> str:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return missing
    if not np.isfinite(x):
        return missing
    for lo, hi, label in bins:
        if lo <= x < hi:
            return label
    return bins[-1][2] if x >= bins[-1][0] else missing


def hour_bucket(hours: Any) -> str:
    return bucket(
        hours,
        [
            (0, 18, "T-00-18"),
            (18, 22, "T-18-22"),
            (22, 24, "T-22-24"),
            (24, 28, "T-24-28"),
            (28, 999, "T-28+"),
        ],
    )


def load_candidates(conn: sqlite3.Connection) -> pd.DataFrame:
    rows = sql_rows(
        conn,
        """
        SELECT candidate_id, condition_id, market_id, side, event_date, bracket,
               city, city_pool, icao, forecast_source, model_version, time_bucket,
               window, decision_window_label, decision_hours_to_settle,
               decision_snapshot_ts_utc, decision_window_missing, model_p_yes,
               market_yes_price, edge, abs_edge, decision_entry_price,
               yes_spread, yes_depth_ask_5c, no_spread, no_depth_ask_5c,
               n_snapshots, edge_max, edge_mean, best_entry_price,
               eligible, paper_ordered, live_filled, settlement_status,
               final_yes, bracket_hit, win_by_count, counterfactual_pnl,
               counterfactual_pnl_best, fact_built_at_utc
        FROM fact_signal_candidates
        """,
    )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    numeric_cols = [
        "decision_hours_to_settle",
        "model_p_yes",
        "market_yes_price",
        "edge",
        "abs_edge",
        "decision_entry_price",
        "yes_spread",
        "yes_depth_ask_5c",
        "no_spread",
        "no_depth_ask_5c",
        "n_snapshots",
        "edge_max",
        "edge_mean",
        "best_entry_price",
        "eligible",
        "paper_ordered",
        "live_filled",
        "final_yes",
        "bracket_hit",
        "win_by_count",
        "counterfactual_pnl",
        "counterfactual_pnl_best",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["event_date", "city", "city_pool", "forecast_source", "model_version", "bracket"]:
        df[col] = df[col].fillna("unknown").astype(str)
    df["hour_bucket"] = df["decision_hours_to_settle"].map(hour_bucket)
    df["price_bucket"] = df["decision_entry_price"].map(
        lambda x: bucket(
            x,
            [
                (0.00, 0.05, "0.00-0.05"),
                (0.05, 0.10, "0.05-0.10"),
                (0.10, 0.15, "0.10-0.15"),
                (0.15, 0.20, "0.15-0.20"),
                (0.20, 0.25, "0.20-0.25"),
                (0.25, 2.00, ">=0.25"),
            ],
        )
    )
    df["edge_bucket"] = df["edge"].map(
        lambda x: bucket(
            x,
            [
                (-9, 0.0, "<0"),
                (0.0, 0.05, "0.00-0.05"),
                (0.05, 0.10, "0.05-0.10"),
                (0.10, 0.20, "0.10-0.20"),
                (0.20, 9, ">=0.20"),
            ],
        )
    )
    df["model_p_bucket"] = df["model_p_yes"].map(
        lambda x: bucket(
            x,
            [
                (0.00, 0.05, "0.00-0.05"),
                (0.05, 0.10, "0.05-0.10"),
                (0.10, 0.20, "0.10-0.20"),
                (0.20, 0.35, "0.20-0.35"),
                (0.35, 2.00, ">=0.35"),
            ],
        )
    )
    df["spread_bucket"] = df["yes_spread"].map(
        lambda x: bucket(
            x,
            [(0.0, 0.01, "<0.01"), (0.01, 0.03, "0.01-0.03"), (0.03, 0.06, "0.03-0.06"), (0.06, 9, ">=0.06")],
        )
    )
    df["snapshot_bucket"] = df["n_snapshots"].map(
        lambda x: bucket(x, [(0, 2, "0-1"), (2, 5, "2-4"), (5, 10, "5-9"), (10, 99999, ">=10")])
    )
    df["is_low_yes"] = (
        df["side"].eq("BUY_YES")
        & df["decision_entry_price"].gt(0)
        & df["decision_entry_price"].lt(0.25)
        & df["decision_window_missing"].fillna(1).eq(0)
    )
    df["is_evaluable"] = (
        df["is_low_yes"]
        & df["settlement_status"].eq("settled")
        & df["final_yes"].notna()
        & df["counterfactual_pnl"].notna()
    )
    df["reversal_hit"] = df["is_evaluable"] & df["final_yes"].eq(1)
    return df


def split_dates(df: pd.DataFrame, holdout_frac: float = 0.30) -> dict[str, Any]:
    dates = sorted(x for x in df["event_date"].dropna().unique() if x and x != "unknown")
    idx = max(1, int(math.floor(len(dates) * (1 - holdout_frac))))
    if idx >= len(dates):
        idx = max(1, len(dates) - 1)
    return {"train_dates": dates[:idx], "holdout_dates": dates[idx:]}


def summarize(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "active_dates": 0,
            "cities": 0,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "hits": 0,
            "hit_rate": None,
        }
    cost = float(df["decision_entry_price"].sum())
    pnl = float(df["counterfactual_pnl"].sum())
    hits = int(df["reversal_hit"].sum())
    return {
        "rows": int(len(df)),
        "active_dates": int(df["event_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "cost": cost,
        "pnl": pnl,
        "roi": safe_div(pnl, cost),
        "hits": hits,
        "hit_rate": safe_div(hits, len(df)),
        "avg_price": float(df["decision_entry_price"].mean()),
        "avg_edge": float(df["edge"].mean()) if df["edge"].notna().any() else None,
        "avg_model_p_yes": float(df["model_p_yes"].mean()) if df["model_p_yes"].notna().any() else None,
        "median_spread": float(df["yes_spread"].median()) if df["yes_spread"].notna().any() else None,
        "median_depth_5c": float(df["yes_depth_ask_5c"].median()) if df["yes_depth_ask_5c"].notna().any() else None,
    }


def grouped_summary(df: pd.DataFrame, group_cols: list[str], *, min_rows: int = 1, min_dates: int = 1) -> list[dict[str, Any]]:
    if df.empty:
        return []
    rows = []
    for key, part in df.groupby(group_cols, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        s = summarize(part)
        if s["rows"] < min_rows or s["active_dates"] < min_dates:
            continue
        item = {col: value for col, value in zip(group_cols, key)}
        item.update(s)
        rows.append(item)
    return sorted(rows, key=lambda r: (r["pnl"], r["hits"], r["roi"] if r["roi"] is not None else -999), reverse=True)


def top_removed(df: pd.DataFrame, key: str, n: int) -> dict[str, Any]:
    if df.empty:
        return {"rows": 0, "roi": None, "removed": []}
    g = df.groupby(key, dropna=False).agg(pnl=("counterfactual_pnl", "sum")).reset_index()
    removed = g.sort_values("pnl", ascending=False).head(n)
    removed_keys = set(removed[key].astype(str))
    kept = df[~df[key].astype(str).isin(removed_keys)].copy()
    return {"rows": int(len(kept)), "roi": summarize(kept)["roi"], "removed": removed.to_dict("records")}


def daily_patterns(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows = grouped_summary(df, ["event_date"], min_rows=1)
    for row in rows:
        part = df[df["event_date"].eq(row["event_date"])]
        hit_part = part[part["reversal_hit"]]
        row["hit_cities"] = sorted(hit_part["city"].unique().tolist())
        row["hit_brackets"] = sorted(hit_part["bracket"].unique().tolist())
    return rows


def candidate_rules(df: pd.DataFrame, split: dict[str, Any]) -> list[dict[str, Any]]:
    train_dates = set(split["train_dates"])
    holdout_dates = set(split["holdout_dates"])
    train = df[df["event_date"].isin(train_dates)]
    holdout = df[df["event_date"].isin(holdout_dates)]

    city_stats = grouped_summary(train, ["city"], min_rows=5, min_dates=3)
    city_pool = [r["city"] for r in city_stats if r["hits"] >= 1 and (r["roi"] or -999) > 0][:12]
    rules: list[tuple[str, str, pd.Series]] = []
    rules.append(("all_low_yes", "All settled/evaluable low-price BUY_YES", df.index == df.index))
    rules.append(("price_lt_010", "price < 0.10", df["decision_entry_price"].lt(0.10)))
    rules.append(("edge_ge_020", "edge >= 0.20", df["edge"].ge(0.20)))
    rules.append(("price_lt_010_edge_ge_020", "price < 0.10 and edge >= 0.20", df["decision_entry_price"].lt(0.10) & df["edge"].ge(0.20)))
    rules.append(("model_p_ge_020", "model_p_yes >= 0.20", df["model_p_yes"].ge(0.20)))
    rules.append(("model_p_ge_035", "model_p_yes >= 0.35", df["model_p_yes"].ge(0.35)))
    rules.append(
        (
            "edge_ge_020_model_p_ge_035",
            "edge >= 0.20 and model_p_yes >= 0.35",
            df["edge"].ge(0.20) & df["model_p_yes"].ge(0.35),
        )
    )
    rules.append(
        (
            "price_015_020_edge_ge_020",
            "0.15 <= price < 0.20 and edge >= 0.20",
            df["decision_entry_price"].ge(0.15) & df["decision_entry_price"].lt(0.20) & df["edge"].ge(0.20),
        )
    )
    rules.append(("spread_le_003", "yes_spread <= 0.03", df["yes_spread"].le(0.03)))
    rules.append(("city_train_positive_top12", f"train-positive cities top12: {', '.join(city_pool[:8])}", df["city"].isin(city_pool)))
    rules.append(
        (
            "convexity_probe_v1",
            "city_train_positive_top12 and price <0.10 or edge>=0.20, one share budget candidate",
            df["city"].isin(city_pool) & (df["decision_entry_price"].lt(0.10) | df["edge"].ge(0.20)),
        )
    )
    out = []
    for name, desc, mask in rules:
        selected = df[mask].copy()
        tr = selected[selected["event_date"].isin(train_dates)]
        ho = selected[selected["event_date"].isin(holdout_dates)]
        out.append(
            {
                "rule": name,
                "description": desc,
                "overall": summarize(selected),
                "train": summarize(tr),
                "holdout": summarize(ho),
                "top3_date_removed": top_removed(selected, "event_date", 3),
                "top5_candidate_removed": top_removed(selected, "candidate_id", 5),
            }
        )
    return out


def current_shadow_candidates(df: pd.DataFrame) -> list[dict[str, Any]]:
    bj_today = (datetime.now(timezone.utc) + timedelta(hours=8)).date().isoformat()
    pool = df[
        df["is_low_yes"]
        & df["event_date"].ge(bj_today)
        & df["edge"].ge(0.20)
        & df["decision_entry_price"].notna()
    ].copy()
    if pool.empty:
        return []
    pool["edge_per_price"] = pool["edge"] / pool["decision_entry_price"]
    ranked = pool.sort_values(
        ["event_date", "city", "edge_per_price", "edge", "model_p_yes"],
        ascending=[True, True, False, False, False],
    )
    top = ranked.groupby(["event_date", "city"], dropna=False, as_index=False).head(1).copy()
    cols = [
        "event_date",
        "city",
        "bracket",
        "forecast_source",
        "model_version",
        "decision_entry_price",
        "edge",
        "model_p_yes",
        "yes_spread",
        "yes_depth_ask_5c",
        "decision_snapshot_ts_utc",
    ]
    return top[cols].to_dict("records")


def choose_recommended_rule(rules: list[dict[str, Any]]) -> dict[str, Any]:
    def score(row: dict[str, Any]) -> tuple[float, float, float, float, float]:
        holdout = row["holdout"]
        top3 = row["top3_date_removed"]
        holdout_rows = holdout["rows"]
        holdout_roi = holdout["roi"] if holdout["roi"] is not None else -999.0
        top3_roi = top3["roi"] if top3["roi"] is not None else -999.0
        overall_roi = row["overall"]["roi"] if row["overall"]["roi"] is not None else -999.0
        overall_dates = row["overall"]["active_dates"]
        return (
            1.0 if holdout_rows > 0 and holdout_roi > 0 else 0.0,
            1.0 if top3_roi > 0 else 0.0,
            min(holdout_rows, 20) / 20.0,
            overall_roi,
            overall_dates,
        )

    return sorted(rules, key=score, reverse=True)[0]


def filter_funnel(df: pd.DataFrame) -> list[dict[str, Any]]:
    steps = [
        ("fact_signal_candidates", pd.Series(True, index=df.index), "full opportunity"),
        ("BUY_YES", df["side"].eq("BUY_YES"), "side only"),
        ("BUY_YES with decision price", df["side"].eq("BUY_YES") & df["decision_entry_price"].notna(), "decision price present"),
        ("low-price BUY_YES <0.25", df["is_low_yes"], "convexity universe before settlement"),
        ("settled/evaluable low-price BUY_YES", df["is_evaluable"], "can score reversal"),
        ("reversal hits", df["reversal_hit"], "final_yes=1"),
    ]
    out = []
    prev = None
    full = len(df)
    for label, mask, note in steps:
        part = df[mask]
        rows = int(len(part))
        out.append(
            {
                "step": label,
                "rows": rows,
                "active_dates": int(part["event_date"].nunique()) if rows else 0,
                "cities": int(part["city"].nunique()) if rows else 0,
                "drop_from_previous": None if prev is None else rows - prev,
                "drop_from_previous_pct": None if prev in (None, 0) else (rows - prev) / prev,
                "retained_from_full_pct": safe_div(rows, full),
                "note": note,
            }
        )
        prev = rows
    return out


def to_table(rows: list[dict[str, Any]], cols: list[str], *, limit: int = 12) -> list[str]:
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join(["---"] + ["---:" for _ in cols[1:]]) + " |",
    ]
    for row in rows[:limit]:
        cells = []
        for col in cols:
            val = row.get(col)
            if col in {"roi", "hit_rate", "avg_price", "avg_edge", "avg_model_p_yes", "median_spread"}:
                cells.append(pct(val) if col in {"roi", "hit_rate"} else plain(val, 3))
            elif isinstance(val, float):
                cells.append(num(val) if col in {"pnl"} else plain(val))
            elif isinstance(val, list):
                cells.append(", ".join(map(str, val[:8])))
            else:
                cells.append(str(val))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def render_md(payload: dict[str, Any]) -> str:
    exp = payload["experiment"]
    self_check = payload["self_check"]
    gate = payload["clob_gate"]
    base = exp["base_summary"]
    best_rule = exp["recommended_rule"]
    lines: list[str] = [
        "# Low-Price YES Reversal Patterns v1",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        "> Scope: opportunity-layer convexity sleeve research only; no N100/live config changed; no orders placed.",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db.fact_signal_candidates` 主实验；`fact_trades` 只用于强制自检和 CLOB gate 状态。",
        f"- DB last_modified_utc: `{payload['db_last_modified_utc']}`",
        f"- MAX(fact_built_at_utc): `{self_check['max_fact_built_at_utc']}`",
        f"- CLOB gate: `gate_pass={gate.get('gate_pass')}`; fail_reasons=`{gate.get('fail_reasons')}`",
        "- 收益数字为机会层 `counterfactual_pnl`，不是 live fill PnL。",
        f"- train: `{exp['split']['train_dates'][0]}` -> `{exp['split']['train_dates'][-1]}` ({len(exp['split']['train_dates'])} event_dates)",
        f"- holdout: `{exp['split']['holdout_dates'][0]}` -> `{exp['split']['holdout_dates'][-1]}` ({len(exp['split']['holdout_dates'])} event_dates)",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(self_check, ensure_ascii=False, indent=2),
        "```",
        "",
        "### Funnel",
        "",
        "| step | rows | active_dates | cities | drop | retained_from_full | note |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload["filter_funnel"]:
        drop = "NA" if row["drop_from_previous"] is None else f"{row['drop_from_previous']} ({pct(row['drop_from_previous_pct'])})"
        lines.append(
            f"| {row['step']} | {row['rows']} | {row['active_dates']} | {row['cities']} | {drop} | {pct(row['retained_from_full_pct'])} | {row['note']} |"
        )
    lines.extend(
        [
            "",
            "## Target Metric",
            "",
            "`low_price_yes_reversal_pattern_v1` = 在 full-opportunity 分母上，观察 `BUY_YES`、`decision_entry_price < 0.25`、settled/evaluable 的机会中，哪些事前标签更容易发生 `final_yes=1` 的反转命中。这里不追求 90% 胜率；核心是小预算右尾暴露是否有可解释触发条件。",
            "",
            f"Base low-price YES: rows `{base['rows']}`, active_dates `{base['active_dates']}`, cities `{base['cities']}`, hits `{base['hits']}`, hit_rate `{pct(base['hit_rate'])}`, ROI `{pct(base['roi'])}`.",
            "",
            "## 反转集中在哪些城市",
            "",
        ]
    )
    lines.extend(to_table(exp["by_city"], ["city", "rows", "active_dates", "hits", "hit_rate", "pnl", "roi", "avg_price"], limit=15))
    lines.extend(["", "## 反转集中在哪些日期", ""])
    lines.extend(to_table(exp["by_date"], ["event_date", "rows", "cities", "hits", "hit_rate", "pnl", "roi", "hit_cities"], limit=15))
    lines.extend(["", "## 事前标签切片", "", "### Price bucket", ""])
    lines.extend(to_table(exp["by_price"], ["price_bucket", "rows", "active_dates", "hits", "hit_rate", "pnl", "roi", "avg_edge"], limit=10))
    lines.extend(["", "### Edge bucket", ""])
    lines.extend(to_table(exp["by_edge"], ["edge_bucket", "rows", "active_dates", "hits", "hit_rate", "pnl", "roi", "avg_price"], limit=10))
    lines.extend(["", "### Model probability bucket", ""])
    lines.extend(to_table(exp["by_model_p"], ["model_p_bucket", "rows", "active_dates", "hits", "hit_rate", "pnl", "roi", "avg_price"], limit=10))
    lines.extend(["", "### Hour bucket", ""])
    lines.extend(to_table(exp["by_hour"], ["hour_bucket", "rows", "active_dates", "hits", "hit_rate", "pnl", "roi", "avg_price"], limit=10))
    lines.extend(["", "### Forecast source / model", ""])
    lines.extend(to_table(exp["by_source_model"], ["forecast_source", "model_version", "rows", "active_dates", "hits", "hit_rate", "pnl", "roi"], limit=12))
    lines.extend(
        [
            "",
            "## 小仓位试探规则",
            "",
            "| rule | rows | dates | hits | hit_rate | ROI | train ROI | holdout rows | holdout ROI | top3 date removed ROI | note |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in exp["rules"]:
        lines.append(
            f"| `{row['rule']}` | {row['overall']['rows']} | {row['overall']['active_dates']} | {row['overall']['hits']} | "
            f"{pct(row['overall']['hit_rate'])} | {pct(row['overall']['roi'])} | {pct(row['train']['roi'])} | "
            f"{row['holdout']['rows']} | {pct(row['holdout']['roi'])} | {pct(row['top3_date_removed']['roi'])} | {row['description']} |"
        )
    lines.extend(
        [
            "",
            "## 当前 zero-notional shadow 候选",
            "",
            "下面只是一份按当前 BJ 日期、`edge>=0.20`、每 city-date 取 `edge/price` 最高一条的观察清单；不下单，不代表 live 建议。",
            "",
            "| event_date | city | bracket | price | edge | model_p_yes | spread | depth_5c | snapshot_ts |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in exp["current_shadow_candidates"]:
        lines.append(
            f"| {row['event_date']} | {row['city']} | {row['bracket']} | "
            f"{plain(row.get('decision_entry_price'), 4)} | {plain(row.get('edge'), 4)} | "
            f"{plain(row.get('model_p_yes'), 4)} | {plain(row.get('yes_spread'), 4)} | "
            f"{plain(row.get('yes_depth_ask_5c'), 1)} | {row.get('decision_snapshot_ts_utc')} |"
        )
    if not exp["current_shadow_candidates"]:
        lines.append("| NA | NA | NA | NA | NA | NA | NA | NA | NA |")
    lines.extend(
        [
            "",
            "## 8 环覆盖自检",
            "",
            "- 1 描述性绩效切片: covered，机会层 low-price BUY_YES reversal。",
            "- 2 统计推断: partial，本轮重点是模式发现；未把它声明为 confirmed live 策略。",
            "- 3 信号判别: partial，用 price/edge/model/source/hour/city/date 标签找反转集中区。",
            "- 4 概率分布评估: partial，使用 `model_p_yes` 分桶但不重训校准模型。",
            "- 5 执行微结构: partial，报告 spread/depth 标签；未额外 raw orderbook join。",
            "- 6 容量: partial，小仓位 sleeve，默认 `$1` 单笔级别。",
            "- 7 组合相关性: covered by event_date/city/date concentration。",
            "- 8 基准/反事实: partial，使用同 universe ROI/hit-rate 对照；不是 live PnL。",
            "",
            "## 结论",
            "",
            (
                "这个方向目前的意思是：低价 YES 不适合按高胜率策略评估，应该按小预算 convexity sleeve 评估。"
                f"全体 settled/evaluable low-price YES 的 hit_rate 是 `{pct(base['hit_rate'])}`，ROI `{pct(base['roi'])}`；"
                "利润高度集中在少数城市和日期。"
            ),
            "",
            (
                "反转更多出现的地方不是一个单独万能阈值，而是 city/date + 低价/高 edge 标签的组合。"
                f"本轮最适合继续 shadow 的规则是 `{best_rule['rule']}`：{best_rule['description']}。"
                f"它 overall ROI `{pct(best_rule['overall']['roi'])}`，holdout rows `{best_rule['holdout']['rows']}`，holdout ROI `{pct(best_rule['holdout']['roi'])}`。"
            ),
            "",
            (
                "当前动作：不要直接 live；可以建 zero-notional shadow / paper journal。"
                "试探方式应是 `$1/order`、每日/每周固定预算、每 city-date 最多 1 单，连续记录未来 20-40 个触发后再判断 tiny-live。"
            ),
            "",
            "## 产物",
            "",
            f"- JSON: `{payload['out_json']}`",
            f"- Markdown: `{payload['out_md']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def run(db: Path, out_json: Path, out_md: Path) -> dict[str, Any]:
    conn = connect(db)
    self_check = mandatory_self_check(conn)
    df = load_candidates(conn)
    low = df[df["is_evaluable"]].copy()
    split = split_dates(low)
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db),
        "db_last_modified_utc": datetime.fromtimestamp(db.stat().st_mtime, tz=timezone.utc).isoformat(),
        "self_check": self_check,
        "clob_gate": load_gate(GATE_JSON_DEFAULT),
        "filter_funnel": filter_funnel(df),
        "experiment": {
            "split": split,
            "base_summary": summarize(low),
            "by_city": grouped_summary(low, ["city"], min_rows=3),
            "by_date": daily_patterns(low),
            "by_price": grouped_summary(low, ["price_bucket"], min_rows=1),
            "by_edge": grouped_summary(low, ["edge_bucket"], min_rows=1),
            "by_model_p": grouped_summary(low, ["model_p_bucket"], min_rows=1),
            "by_hour": grouped_summary(low, ["hour_bucket"], min_rows=1),
            "by_source_model": grouped_summary(low, ["forecast_source", "model_version"], min_rows=1),
            "by_spread": grouped_summary(low, ["spread_bucket"], min_rows=1),
            "rules": candidate_rules(low, split),
            "current_shadow_candidates": current_shadow_candidates(df),
        },
        "out_json": str(out_json),
        "out_md": str(out_md),
    }
    payload["experiment"]["recommended_rule"] = choose_recommended_rule(payload["experiment"]["rules"])
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    out_md.write_text(render_md(payload))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Low-price BUY_YES reversal pattern research.")
    parser.add_argument("--db", type=Path, default=DB_DEFAULT)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON_DEFAULT)
    parser.add_argument("--out-md", type=Path, default=OUT_MD_DEFAULT)
    args = parser.parse_args()
    payload = run(args.db, args.out_json, args.out_md)
    print(json.dumps({"json": payload["out_json"], "markdown": payload["out_md"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
