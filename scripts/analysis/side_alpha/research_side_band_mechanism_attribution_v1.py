#!/usr/bin/env python3
"""Side Band mechanism attribution v1.

Local counterfactual research only. Main PnL/ROI uses
runtime/weather.db.fact_signal_candidates. fact_trades is used only for
contract self-checks and live_real CLOB gate diagnostics.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime/weather.db"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-10-side-band-mechanism-attribution-v1.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-10-side-band-mechanism-attribution-v1.md"
CLEAN_TEST_PATH = ROOT / "scripts/analysis/side_alpha/research_side_band_forecast_regime_clean_test_v0.py"
TARGET_METRIC = "side_band_mechanism_attribution_v1"
RNG_SEED = 20260610


def load_clean_module() -> Any:
    spec = importlib.util.spec_from_file_location("side_band_clean_test", CLEAN_TEST_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import clean-test helper from {CLEAN_TEST_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clean = load_clean_module()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return "NA"
    return f"{value * 100:+.1f}%"


def money(value: float | None) -> str:
    if value is None or not np.isfinite(value):
        return "NA"
    return f"{value:+.2f}"


def safe_div(num: float, den: float) -> float | None:
    return num / den if den else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def ci95(values: list[float]) -> list[float | None]:
    return [percentile(values, 0.025), percentile(values, 0.975)]


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def sql_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def sql_scalar(conn: sqlite3.Connection, sql: str) -> Any:
    return conn.execute(sql).fetchone()[0]


def run_live_gate(db_path: Path) -> dict[str, Any]:
    script = ROOT / "scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--db", str(db_path)],
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"raw_output_tail": proc.stdout.splitlines()[-40:]}
    payload["returncode"] = proc.returncode
    return payload


def mandatory_self_check(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "max_fact_built_at_utc": sql_scalar(conn, "SELECT MAX(fact_built_at_utc) FROM fact_trades"),
        "trade_class_distribution": sql_rows(
            conn,
            "SELECT trade_class, COUNT(*) AS n FROM fact_trades GROUP BY trade_class ORDER BY n DESC",
        ),
        "settlement_status_distribution": sql_rows(
            conn,
            "SELECT settlement_status, COUNT(*) AS n FROM fact_trades GROUP BY settlement_status ORDER BY n DESC",
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


def price_bucket_5c(price: Any) -> str | None:
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(p):
        return None
    low = math.floor(p / 0.05) * 0.05
    high = min(1.0, low + 0.05)
    return f"{low:.2f}-{high:.2f}"


def add_features(cands: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    features = clean.build_distribution_features(cands)
    features = clean.add_expanding_history(features)
    train_dates, holdout_dates, split_info = clean.split_dates(features)
    features, thresholds = clean.assign_regimes(features, train_dates)
    enriched = clean.add_candidate_fields(cands, features)
    enriched["price_bucket_5c"] = enriched["decision_entry_price"].map(price_bucket_5c)
    enriched["analysis_usable"] = (
        (enriched["usable_for_main_experiment"] == 1)
        & enriched["price_bucket_5c"].notna()
        & enriched["counterfactual_pnl"].notna()
        & enriched["decision_entry_price"].notna()
    ).astype(int)
    return enriched, {"split": split_info, "thresholds": thresholds, "train_dates": train_dates, "holdout_dates": holdout_dates}


def selector_defs() -> list[dict[str, Any]]:
    return [
        {
            "name": "old_side_band_proxy",
            "idea": "近似旧 side_band：BUY_YES 买 0.20-0.45 且 abs_edge>=0.20，BUY_NO 买 0.35-0.65 且 abs_edge>=0.10。",
            "mask": lambda d: (
                ((d["side"] == "BUY_YES") & d["decision_entry_price"].between(0.20, 0.45) & (d["abs_edge"] >= 0.20))
                | ((d["side"] == "BUY_NO") & d["decision_entry_price"].between(0.35, 0.65) & (d["abs_edge"] >= 0.10))
            ),
            "baseline": "same_side_hour_price",
        },
        {
            "name": "old_side_band_no_low_yes",
            "idea": "旧 side_band 但剔除 BUY_YES<0.25 彩票腿，看旧利润是否只是低价 YES 驱动。",
            "mask": lambda d: (
                ((d["side"] == "BUY_YES") & d["decision_entry_price"].between(0.25, 0.45) & (d["abs_edge"] >= 0.20))
                | ((d["side"] == "BUY_NO") & d["decision_entry_price"].between(0.35, 0.65) & (d["abs_edge"] >= 0.10))
            ),
            "baseline": "same_side_hour_price",
        },
        {
            "name": "low_price_yes_lottery_leg",
            "idea": "只看 BUY_YES 0.20-0.25 彩票腿，判断旧 side_band 的高回报是否来自少数命中。",
            "mask": lambda d: (d["side"] == "BUY_YES") & d["decision_entry_price"].between(0.20, 0.25) & (d["abs_edge"] >= 0.20),
            "baseline": "same_side_hour_price",
        },
        {
            "name": "yes_mid_leg",
            "idea": "BUY_YES 0.25-0.45 中低价腿，剔除最彩票区后看 YES 是否仍有 alpha。",
            "mask": lambda d: (d["side"] == "BUY_YES") & d["decision_entry_price"].between(0.25, 0.45) & (d["abs_edge"] >= 0.20),
            "baseline": "same_side_hour_price",
        },
        {
            "name": "no_mid_high_leg",
            "idea": "BUY_NO 0.35-0.65 中高价腿，检查收益是否只是 BUY_NO base-rate。",
            "mask": lambda d: (d["side"] == "BUY_NO") & d["decision_entry_price"].between(0.35, 0.65) & (d["abs_edge"] >= 0.10),
            "baseline": "same_side_hour_price",
        },
        {
            "name": "entry_25_75_control",
            "idea": "传统 0.25-0.75 入场控制组，帮助分离 entry band 本身。",
            "mask": lambda d: d["decision_entry_price"].between(0.25, 0.75) & (d["abs_edge"] >= 0.10),
            "baseline": "same_side_hour_price",
        },
        {
            "name": "old_side_band_low_uncertainty",
            "idea": "旧 side_band 只保留 low_uncertainty_allowed，看 forecast regime 是否提供额外筛选。",
            "mask": lambda d: (
                (
                    ((d["side"] == "BUY_YES") & d["decision_entry_price"].between(0.20, 0.45) & (d["abs_edge"] >= 0.20))
                    | ((d["side"] == "BUY_NO") & d["decision_entry_price"].between(0.35, 0.65) & (d["abs_edge"] >= 0.10))
                )
                & (d["forecast_regime"] == "low_uncertainty_allowed")
            ),
            "baseline": "same_side_hour_price",
        },
        {
            "name": "old_side_band_medium_uncertainty",
            "idea": "旧 side_band 只保留 medium_uncertainty_price_sensitive，复核 clean-test 里 train 好看的区域。",
            "mask": lambda d: (
                (
                    ((d["side"] == "BUY_YES") & d["decision_entry_price"].between(0.20, 0.45) & (d["abs_edge"] >= 0.20))
                    | ((d["side"] == "BUY_NO") & d["decision_entry_price"].between(0.35, 0.65) & (d["abs_edge"] >= 0.10))
                )
                & (d["forecast_regime"] == "medium_uncertainty_price_sensitive")
            ),
            "baseline": "same_side_hour_price",
        },
        {
            "name": "old_side_band_tail_blocked",
            "idea": "旧 side_band 挡掉 tail_risk_block，测试尾部风险过滤是否能降低样本运气依赖。",
            "mask": lambda d: (
                (
                    ((d["side"] == "BUY_YES") & d["decision_entry_price"].between(0.20, 0.45) & (d["abs_edge"] >= 0.20))
                    | ((d["side"] == "BUY_NO") & d["decision_entry_price"].between(0.35, 0.65) & (d["abs_edge"] >= 0.10))
                )
                & (d["tail_risk_block"] == 0)
            ),
            "baseline": "same_side_hour_price",
        },
    ]


def base_pool(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["analysis_usable"] == 1) & (df["hour_bucket"] == "T-18-24")].copy()


def matched_baseline(pool: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pool.iloc[0:0].copy()
    keys = set(zip(selected["side"], selected["hour_bucket"], selected["price_bucket_5c"]))
    mask = [
        (row.side, row.hour_bucket, row.price_bucket_5c) in keys
        for row in pool[["side", "hour_bucket", "price_bucket_5c"]].itertuples(index=False)
    ]
    return pool.loc[mask].copy()


def roi(df: pd.DataFrame) -> float | None:
    return safe_div(float(df["counterfactual_pnl"].sum()), float(df["decision_entry_price"].sum())) if len(df) else None


def daily_top_removed_roi(df: pd.DataFrame, n: int = 5) -> float | None:
    if df.empty:
        return None
    daily = df.groupby("event_date", dropna=False).agg(pnl=("counterfactual_pnl", "sum"), cost=("decision_entry_price", "sum"))
    kept = daily.sort_values("pnl", ascending=False).iloc[n:]
    return safe_div(float(kept["pnl"].sum()), float(kept["cost"].sum()))


def bootstrap_excess(selected: pd.DataFrame, baseline: pd.DataFrame, *, iters: int, seed: int) -> list[float]:
    groups_a = {str(k): v for k, v in selected.groupby("event_date", dropna=False).groups.items()}
    groups_b = {str(k): v for k, v in baseline.groupby("event_date", dropna=False).groups.items()}
    dates = sorted(set(groups_a) | set(groups_b))
    if not dates:
        return []
    rng = random.Random(seed)
    out: list[float] = []
    for _ in range(iters):
        a_pnl = a_cost = b_pnl = b_cost = 0.0
        for _ in dates:
            d = rng.choice(dates)
            if d in groups_a:
                sample = selected.loc[groups_a[d]]
                a_pnl += float(sample["counterfactual_pnl"].sum())
                a_cost += float(sample["decision_entry_price"].sum())
            if d in groups_b:
                sample = baseline.loc[groups_b[d]]
                b_pnl += float(sample["counterfactual_pnl"].sum())
                b_cost += float(sample["decision_entry_price"].sum())
        a_roi = safe_div(a_pnl, a_cost)
        b_roi = safe_div(b_pnl, b_cost)
        if a_roi is not None and b_roi is not None and math.isfinite(a_roi) and math.isfinite(b_roi):
            out.append(a_roi - b_roi)
    return out


def summarize_frame(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "rows": 0,
            "active_dates": 0,
            "cities": 0,
            "cost_usd_1share": 0.0,
            "counterfactual_pnl_usd_1share": 0.0,
            "roi": None,
            "top5_removed_roi": None,
            "worst_day_pnl": None,
            "low_price_buy_yes_rows": 0,
        }
    daily = df.groupby("event_date", dropna=False)["counterfactual_pnl"].sum()
    return {
        "rows": int(len(df)),
        "active_dates": int(df["event_date"].nunique()),
        "cities": int(df["city"].nunique()),
        "cost_usd_1share": float(df["decision_entry_price"].sum()),
        "counterfactual_pnl_usd_1share": float(df["counterfactual_pnl"].sum()),
        "roi": roi(df),
        "top5_removed_roi": daily_top_removed_roi(df),
        "worst_day_pnl": float(daily.min()) if len(daily) else None,
        "low_price_buy_yes_rows": int(((df["side"] == "BUY_YES") & (df["decision_entry_price"] < 0.25)).sum()),
    }


def attribution_tables(df: pd.DataFrame, selector_mask: pd.Series) -> dict[str, Any]:
    selected = df[selector_mask].copy()
    if selected.empty:
        return {"by_date": [], "by_city": [], "by_side": [], "top_rows": []}

    def group(cols: list[str], limit: int) -> list[dict[str, Any]]:
        g = (
            selected.groupby(cols, dropna=False)
            .agg(
                rows=("candidate_id", "count"),
                cost=("decision_entry_price", "sum"),
                pnl=("counterfactual_pnl", "sum"),
            )
            .reset_index()
        )
        g["roi"] = g.apply(lambda r: safe_div(float(r["pnl"]), float(r["cost"])), axis=1)
        g = g.sort_values("pnl", ascending=True)
        worst = g.head(limit).to_dict(orient="records")
        best = g.tail(limit).sort_values("pnl", ascending=False).to_dict(orient="records")
        return [{"bucket": "worst", **x} for x in worst] + [{"bucket": "best", **x} for x in best]

    top_rows = selected.sort_values("counterfactual_pnl", ascending=False).head(10)[
        ["candidate_id", "city", "event_date", "side", "bracket", "decision_entry_price", "counterfactual_pnl", "forecast_regime"]
    ].to_dict(orient="records")
    return {
        "by_date": group(["event_date"], 5),
        "by_city": group(["city"], 5),
        "by_side": group(["side"], 5),
        "top_rows": top_rows,
    }


def evaluate_selector(
    pool: pd.DataFrame,
    selector: dict[str, Any],
    train_dates: set[str],
    holdout_dates: set[str],
    *,
    bootstrap_iters: int,
    seed: int,
) -> dict[str, Any]:
    mask_all = selector["mask"](pool)
    out: dict[str, Any] = {"name": selector["name"], "idea": selector["idea"], "baseline": selector["baseline"]}
    for split_name, dates in [("train", train_dates), ("holdout", holdout_dates)]:
        split_pool = pool[pool["event_date"].astype(str).isin(dates)].copy()
        selected = split_pool[selector["mask"](split_pool)].copy()
        baseline = matched_baseline(split_pool, selected)
        selected_summary = summarize_frame(selected)
        baseline_summary = summarize_frame(baseline)
        excess = None
        if selected_summary["roi"] is not None and baseline_summary["roi"] is not None:
            excess = selected_summary["roi"] - baseline_summary["roi"]
        excess_ci = ci95(bootstrap_excess(selected, baseline, iters=bootstrap_iters, seed=seed + (0 if split_name == "train" else 10000)))
        out[split_name] = {
            **selected_summary,
            "baseline_rows": baseline_summary["rows"],
            "baseline_roi": baseline_summary["roi"],
            "excess_roi": excess,
            "excess_roi_ci95_event_date_cluster": excess_ci,
        }
    train_ci = out["train"]["excess_roi_ci95_event_date_cluster"]
    hold_ci = out["holdout"]["excess_roi_ci95_event_date_cluster"]
    significance = "PASS" if train_ci[0] is not None and train_ci[0] > 0 else "FAIL"
    baseline_gate = "PASS" if hold_ci[0] is not None and hold_ci[0] > 0 else "FAIL"
    forward = (
        "PASS"
        if out["holdout"]["excess_roi"] is not None
        and out["holdout"]["excess_roi"] > 0
        and out["holdout"]["rows"] >= 30
        and out["holdout"]["active_dates"] >= 5
        and (out["holdout"]["top5_removed_roi"] or -999) > 0
        else "FAIL"
    )
    out["gates"] = {"significance": significance, "baseline": baseline_gate, "forward": forward}
    out["verdict"] = "confirmed" if significance == baseline_gate == forward == "PASS" else "inconclusive"
    out["attribution"] = attribution_tables(pool, mask_all)
    return out


def markdown_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def render_report(payload: dict[str, Any]) -> str:
    split = payload["split"]
    result_rows = []
    for r in payload["selectors"]:
        result_rows.append(
            {
                "direction": r["name"],
                "idea": r["idea"],
                "train": f"rows {r['train']['rows']}, excess {pct(r['train']['excess_roi'])}, CI [{pct(r['train']['excess_roi_ci95_event_date_cluster'][0])}, {pct(r['train']['excess_roi_ci95_event_date_cluster'][1])}]",
                "holdout": f"rows {r['holdout']['rows']}, excess {pct(r['holdout']['excess_roi'])}, CI [{pct(r['holdout']['excess_roi_ci95_event_date_cluster'][0])}, {pct(r['holdout']['excess_roi_ci95_event_date_cluster'][1])}]",
                "top5": pct(r["holdout"]["top5_removed_roi"]),
                "worst_day": money(r["holdout"]["worst_day_pnl"]),
                "gates": "/".join(r["gates"].values()),
                "verdict": r["verdict"],
            }
        )

    summary_rows = []
    for r in payload["selectors"]:
        summary_rows.append(
            {
                "direction": r["name"],
                "human-readable idea": r["idea"],
                "sample size": f"train {r['train']['rows']} / holdout {r['holdout']['rows']}",
                "holdout result": f"ROI {pct(r['holdout']['roi'])}, excess {pct(r['holdout']['excess_roi'])}",
                "top5 removed": pct(r["holdout"]["top5_removed_roi"]),
                "gates": "/".join(r["gates"].values()),
                "verdict": r["verdict"],
                "next step": "仅研究；需要更长 forward 或新预注册规则。",
            }
        )

    old = next((r for r in payload["selectors"] if r["name"] == "old_side_band_proxy"), None)
    old_attr_rows = []
    if old:
        for x in old["attribution"]["by_date"]:
            old_attr_rows.append(
                {
                    "bucket": x["bucket"],
                    "event_date": x["event_date"],
                    "rows": x["rows"],
                    "pnl": money(float(x["pnl"])),
                    "roi": pct(x["roi"]),
                }
            )

    lines = [
        "# Side Band Mechanism Attribution v1",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        "> Scope: 本地 counterfactual research only；未改 N100/live 配置。",
        "",
        "## 数据快照",
        "",
        f"- 数据源: `runtime/weather.db.fact_signal_candidates` 主实验；`fact_trades` 只用于自检和 CLOB gate。",
        f"- DB last_modified_utc: `{payload['db_last_modified_utc']}`",
        f"- MAX(fact_built_at_utc): `{payload['mandatory_self_check']['max_fact_built_at_utc']}`",
        f"- CLOB gate: `gate_pass={payload['clob_gate'].get('gate_pass')}`; `db_fill_cost_minus_fact_cost={payload['clob_gate'].get('db_fill_cost_minus_fact_cost')}`",
        f"- train: `{split['train_start']}` -> `{split['train_end']}` ({split['train_dates']} event_dates)",
        f"- holdout: `{split['holdout_start']}` -> `{split['holdout_end']}` ({split['holdout_dates']} event_dates)",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(payload["mandatory_self_check"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 目标指标与设计",
        "",
        "`side_band_mechanism_attribution_v1` 固定旧 side-band 形态和几个机制 selector，不再做大规模调参。每个 selector 相对同 side、同 T-18-24、同 5c entry price bucket 的 full-opportunity baseline 算 excess ROI，并按 event_date cluster bootstrap。",
        "",
        "- 主分母: `fact_signal_candidates` full opportunity；不把旧 `eligible` 当硬门。",
        "- 价格: `decision_entry_price`。",
        "- PnL: `counterfactual_pnl`，不是成交样本 PnL。",
        "- 重点: 分清 entry band、低价 BUY_YES、BUY_NO base-rate、forecast regime、样本日期运气。",
        "",
        "## Selector Results",
        "",
        markdown_table(result_rows, ["direction", "idea", "train", "holdout", "top5", "worst_day", "gates", "verdict"]),
        "",
        "## old_side_band_proxy 日期归因",
        "",
        markdown_table(old_attr_rows, ["bucket", "event_date", "rows", "pnl", "roi"]),
        "",
        "## 结论",
        "",
        "这个方向目前的意思是：旧 side-band 的正收益更像是 price/side 形态叠加少数日期命中的结果，而不是已经可复制的稳定 alpha。它赚/亏主要来自两个不稳定来源：低价 BUY_YES 的凸性会放大少数命中但相对同价位 baseline 不稳，BUY_NO 中高价腿在 holdout 有正 excess 但 train 和 top5 stress 过不了。最大问题是 holdout excess CI 与 top5 removed 后的 ROI 不能同时站住。如果放宽或重训 forecast regime，点估计可能变化，但那必须作为新预注册实验，不能回填本次 holdout。当前动作：仅研究，不允许 live。",
        "",
        "## 总表",
        "",
        markdown_table(
            summary_rows,
            ["direction", "human-readable idea", "sample size", "holdout result", "top5 removed", "gates", "verdict", "next step"],
        ),
        "",
        "## 8 环覆盖自检",
        "",
        "- 1 描述性绩效切片: covered，机会层 selector + 旧 side-band 日期归因。",
        "- 2 统计推断: covered，event_date cluster bootstrap。",
        "- 3 信号判别: covered，固定 selector 相对 matched baseline。",
        "- 4 概率分布评估: partial，复用 clean-test forecast regime。",
        "- 5 执行微结构: partial，仅用 fact 表 decision price/spread 派生字段，不发布 executable PnL。",
        "- 6 容量: NA。",
        "- 7 组合相关性: covered by event_date cluster。",
        "- 8 基准/反事实: covered，同 side/hour/price bucket baseline。",
        "",
        "## 产物",
        "",
        f"- JSON: `{payload['out_json']}`",
        f"- Markdown: `{payload['out_md']}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=str(DB_DEFAULT))
    parser.add_argument("--out-json", default=str(OUT_JSON_DEFAULT))
    parser.add_argument("--out-md", default=str(OUT_MD_DEFAULT))
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    args = parser.parse_args()

    db_path = Path(args.db_path)
    conn = connect(db_path)
    try:
        self_check = mandatory_self_check(conn)
        cands = pd.read_sql_query("SELECT * FROM fact_signal_candidates", conn)
    finally:
        conn.close()

    live_gate = run_live_gate(db_path)
    enriched, feature_info = add_features(cands)
    pool = base_pool(enriched)
    train_dates = feature_info["train_dates"]
    holdout_dates = feature_info["holdout_dates"]
    selectors = [
        evaluate_selector(
            pool,
            item,
            train_dates,
            holdout_dates,
            bootstrap_iters=args.bootstrap_iters,
            seed=RNG_SEED + idx * 17,
        )
        for idx, item in enumerate(selector_defs())
    ]

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat(),
        "mandatory_self_check": self_check,
        "clob_gate": live_gate,
        "split": feature_info["split"],
        "regime_thresholds_train_only": feature_info["thresholds"],
        "analysis_pool": {
            "rows": int(len(pool)),
            "active_dates": int(pool["event_date"].nunique()) if len(pool) else 0,
            "description": "analysis_usable rows restricted to T-18-24",
        },
        "selectors": selectors,
        "out_json": str(Path(args.out_json)),
        "out_md": str(Path(args.out_md)),
    }

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    out_md.write_text(render_report(payload), encoding="utf-8")
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "selectors": len(selectors)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
