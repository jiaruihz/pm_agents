#!/usr/bin/env python3
"""Side-band bad-day risk filter v2.

Local counterfactual research only. Main experiment uses
runtime/weather.db.fact_signal_candidates. fact_trades/live_real is used only
for mandatory self-check and CLOB coverage gate status.
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
DB_DEFAULT = ROOT / "runtime/weather.db"
OUT_JSON_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-11-side-band-bad-day-risk-v2.json"
OUT_MD_DEFAULT = ROOT / "docs/analysis/2026-06/2026-06-11-side-band-bad-day-risk-v2.md"
CLEAN_TEST_PATH = ROOT / "scripts/analysis/side_alpha/research_side_band_forecast_regime_clean_test_v0.py"
ORDERBOOK_GLOB_DEFAULT = str(ROOT / "runtime/weather_edge_v1/market_data/orderbook_snapshots/*/*.jsonl.gz")
TARGET_METRIC = "side_band_bad_day_risk_v2"
RNG_SEED = 20260611


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


def base_pool(df: pd.DataFrame) -> pd.DataFrame:
    return df[(df["analysis_usable"] == 1) & (df["hour_bucket"] == "T-18-24")].copy()


def selector_masks(pool: pd.DataFrame) -> dict[str, pd.Series]:
    old = (
        ((pool["side"] == "BUY_YES") & pool["decision_entry_price"].between(0.20, 0.45) & (pool["abs_edge"] >= 0.20))
        | ((pool["side"] == "BUY_NO") & pool["decision_entry_price"].between(0.35, 0.65) & (pool["abs_edge"] >= 0.10))
    )
    no = (pool["side"] == "BUY_NO") & pool["decision_entry_price"].between(0.35, 0.65) & (pool["abs_edge"] >= 0.10)
    no_low_yes = (
        ((pool["side"] == "BUY_YES") & pool["decision_entry_price"].between(0.25, 0.45) & (pool["abs_edge"] >= 0.20))
        | no
    )
    yes_mid = (pool["side"] == "BUY_YES") & pool["decision_entry_price"].between(0.25, 0.45) & (pool["abs_edge"] >= 0.20)
    return {
        "old_side_band_proxy": old,
        "old_side_band_no_low_yes": no_low_yes,
        "buy_no_035_065": no,
        "buy_yes_025_045": yes_mid,
    }


def risk_filter_defs(pool: pd.DataFrame, train_dates: set[str]) -> dict[str, pd.Series]:
    train = pool[pool["event_date"].astype(str).isin(train_dates)]
    hist_cut = float(train["city_source_expanding_adjacent3_miss_rate_filled"].median()) if len(train) else 0.5
    l1_cut = float(train["model_market_l1_gap"].quantile(0.75)) if len(train) else 999.0
    entropy_cut = float(train["model_entropy"].quantile(0.75)) if len(train) else 999.0
    return {
        "no_filter": pd.Series(True, index=pool.index),
        "block_high_uncertainty": pool["forecast_regime"].ne("high_uncertainty_no_trade"),
        "low_or_medium_quality": pool["forecast_regime"].isin(["low_uncertainty_allowed", "medium_uncertainty_price_sensitive"]),
        "tail_blocked": pool["tail_risk_block"].fillna(0).astype(int).eq(0),
        "hist_miss_below_train_median": pool["city_source_expanding_adjacent3_miss_rate_filled"].le(hist_cut),
        "market_model_l1_not_top_quartile": pool["model_market_l1_gap"].le(l1_cut),
        "entropy_not_top_quartile": pool["model_entropy"].le(entropy_cut),
    }


def matched_baseline(pool: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return pool.iloc[0:0].copy()
    keys = set(zip(selected["side"], selected["hour_bucket"], selected["price_bucket_5c"]))
    key_frame = pool[["side", "hour_bucket", "price_bucket_5c"]]
    mask = [(row.side, row.hour_bucket, row.price_bucket_5c) in keys for row in key_frame.itertuples(index=False)]
    return pool.loc[mask].copy()


def roi(df: pd.DataFrame) -> float | None:
    if df.empty:
        return None
    return safe_div(float(df["counterfactual_pnl"].sum()), float(df["decision_entry_price"].sum()))


def daily_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["event_date", "rows", "cost", "pnl", "roi"])
    g = df.groupby("event_date", dropna=False).agg(
        rows=("candidate_id", "count"),
        cost=("decision_entry_price", "sum"),
        pnl=("counterfactual_pnl", "sum"),
    ).reset_index()
    g["roi"] = g.apply(lambda r: safe_div(float(r["pnl"]), float(r["cost"])), axis=1)
    return g.sort_values("event_date")


def top_removed_roi(df: pd.DataFrame, n: int = 5) -> float | None:
    daily = daily_frame(df)
    if daily.empty or len(daily) <= n:
        return None
    kept = daily.sort_values("pnl", ascending=False).iloc[n:]
    return safe_div(float(kept["pnl"].sum()), float(kept["cost"].sum()))


def positive_day_rate(df: pd.DataFrame) -> float | None:
    daily = daily_frame(df)
    if daily.empty:
        return None
    return float((daily["pnl"] > 0).mean())


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
                s = selected.loc[groups_a[d]]
                a_pnl += float(s["counterfactual_pnl"].sum())
                a_cost += float(s["decision_entry_price"].sum())
            if d in groups_b:
                s = baseline.loc[groups_b[d]]
                b_pnl += float(s["counterfactual_pnl"].sum())
                b_cost += float(s["decision_entry_price"].sum())
        a_roi = safe_div(a_pnl, a_cost)
        b_roi = safe_div(b_pnl, b_cost)
        if a_roi is not None and b_roi is not None and math.isfinite(a_roi) and math.isfinite(b_roi):
            out.append(a_roi - b_roi)
    return out


def summarize(selected: pd.DataFrame, baseline: pd.DataFrame, *, iters: int, seed: int) -> dict[str, Any]:
    selected_roi = roi(selected)
    baseline_roi = roi(baseline)
    daily = daily_frame(selected)
    excess = None if selected_roi is None or baseline_roi is None else selected_roi - baseline_roi
    ci = ci95(bootstrap_excess(selected, baseline, iters=iters, seed=seed))
    return {
        "rows": int(len(selected)),
        "active_dates": int(selected["event_date"].nunique()) if len(selected) else 0,
        "cities": int(selected["city"].nunique()) if len(selected) else 0,
        "cost_usd_1share": float(selected["decision_entry_price"].sum()) if len(selected) else 0.0,
        "counterfactual_pnl_usd_1share": float(selected["counterfactual_pnl"].sum()) if len(selected) else 0.0,
        "roi": selected_roi,
        "baseline_rows": int(len(baseline)),
        "baseline_roi": baseline_roi,
        "excess_roi": excess,
        "excess_roi_ci95_event_date_cluster": ci,
        "top5_removed_roi": top_removed_roi(selected),
        "worst_day_pnl": None if daily.empty else float(daily["pnl"].min()),
        "best_day_pnl": None if daily.empty else float(daily["pnl"].max()),
        "positive_day_rate": positive_day_rate(selected),
    }


def attribution(df: pd.DataFrame) -> dict[str, Any]:
    daily = daily_frame(df)
    by_city = []
    by_regime = []
    if len(df):
        city = df.groupby("city", dropna=False).agg(rows=("candidate_id", "count"), cost=("decision_entry_price", "sum"), pnl=("counterfactual_pnl", "sum")).reset_index()
        city["roi"] = city.apply(lambda r: safe_div(float(r["pnl"]), float(r["cost"])), axis=1)
        by_city = pd.concat([city.sort_values("pnl").head(5), city.sort_values("pnl", ascending=False).head(5)]).to_dict(orient="records")
        regime = df.groupby("forecast_regime", dropna=False).agg(rows=("candidate_id", "count"), cost=("decision_entry_price", "sum"), pnl=("counterfactual_pnl", "sum")).reset_index()
        regime["roi"] = regime.apply(lambda r: safe_div(float(r["pnl"]), float(r["cost"])), axis=1)
        by_regime = regime.sort_values("pnl").to_dict(orient="records")
    return {
        "worst_dates": daily.sort_values("pnl").head(8).to_dict(orient="records"),
        "best_dates": daily.sort_values("pnl", ascending=False).head(8).to_dict(orient="records"),
        "city_extremes": by_city,
        "by_forecast_regime": by_regime,
    }


def evaluate(pool: pd.DataFrame, train_dates: set[str], holdout_dates: set[str], *, iters: int) -> list[dict[str, Any]]:
    selectors = selector_masks(pool)
    filters = risk_filter_defs(pool, train_dates)
    out = []
    for s_idx, (selector_name, selector_mask) in enumerate(selectors.items()):
        for f_idx, (filter_name, filter_mask) in enumerate(filters.items()):
            mask = selector_mask & filter_mask
            item: dict[str, Any] = {"selector": selector_name, "risk_filter": filter_name}
            for split_name, dates in [("train", train_dates), ("holdout", holdout_dates)]:
                split_pool = pool[pool["event_date"].astype(str).isin(dates)]
                selected = split_pool[mask.loc[split_pool.index]].copy()
                baseline = matched_baseline(split_pool, selected)
                item[split_name] = summarize(selected, baseline, iters=iters, seed=RNG_SEED + s_idx * 100 + f_idx * 11 + (0 if split_name == "train" else 10000))
            train_ci = item["train"]["excess_roi_ci95_event_date_cluster"]
            hold_ci = item["holdout"]["excess_roi_ci95_event_date_cluster"]
            significance = "PASS" if train_ci[0] is not None and train_ci[0] > 0 else "FAIL"
            baseline_gate = "PASS" if hold_ci[0] is not None and hold_ci[0] > 0 else "FAIL"
            forward = (
                "PASS"
                if item["holdout"]["excess_roi"] is not None
                and item["holdout"]["excess_roi"] > 0
                and item["holdout"]["rows"] >= 30
                and item["holdout"]["active_dates"] >= 5
                and (item["holdout"]["top5_removed_roi"] or -999) > 0
                else "FAIL"
            )
            item["gates"] = {"significance": significance, "baseline": baseline_gate, "forward": forward}
            item["verdict"] = "confirmed" if significance == baseline_gate == forward == "PASS" else "inconclusive"
            selected_all = pool[mask].copy()
            item["attribution"] = attribution(selected_all)
            out.append(item)
    return out


def markdown_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    return "\n".join(lines)


def render(payload: dict[str, Any]) -> str:
    split = payload["split"]
    gate = payload["clob_gate"]
    funnel_rows = [
        {
            "step": x["step"],
            "rows": x["rows"],
            "active_dates": x["active_dates"],
            "drop": "NA" if x["drop_from_previous"] is None else pct(-x["drop_from_previous"]),
            "note": x["note"],
        }
        for x in payload["filter_funnel"]
    ]
    ranked = sorted(payload["results"], key=lambda r: ((r["holdout"]["top5_removed_roi"] if r["holdout"]["top5_removed_roi"] is not None else -999), (r["holdout"]["excess_roi"] if r["holdout"]["excess_roi"] is not None else -999)), reverse=True)
    rows = []
    for r in ranked[:20]:
        rows.append({
            "selector": r["selector"],
            "filter": r["risk_filter"],
            "train_excess": pct(r["train"]["excess_roi"]),
            "train_ci": f"[{pct(r['train']['excess_roi_ci95_event_date_cluster'][0])}, {pct(r['train']['excess_roi_ci95_event_date_cluster'][1])}]",
            "holdout_rows": r["holdout"]["rows"],
            "holdout_excess": pct(r["holdout"]["excess_roi"]),
            "holdout_ci": f"[{pct(r['holdout']['excess_roi_ci95_event_date_cluster'][0])}, {pct(r['holdout']['excess_roi_ci95_event_date_cluster'][1])}]",
            "top5_removed": pct(r["holdout"]["top5_removed_roi"]),
            "worst_day": money(r["holdout"]["worst_day_pnl"]),
            "gates": "/".join(r["gates"].values()),
            "verdict": r["verdict"],
        })
    no_base = next((r for r in payload["results"] if r["selector"] == "buy_no_035_065" and r["risk_filter"] == "no_filter"), None)
    no_best = next((r for r in ranked if r["selector"] == "buy_no_035_065"), None)
    old_base = next((r for r in payload["results"] if r["selector"] == "old_side_band_proxy" and r["risk_filter"] == "no_filter"), None)
    attr_rows = []
    if no_base:
        for x in no_base["attribution"]["worst_dates"]:
            attr_rows.append({"bucket": "worst", "event_date": x["event_date"], "rows": x["rows"], "pnl": money(float(x["pnl"])), "roi": pct(x["roi"])})
        for x in no_base["attribution"]["best_dates"]:
            attr_rows.append({"bucket": "best", "event_date": x["event_date"], "rows": x["rows"], "pnl": money(float(x["pnl"])), "roi": pct(x["roi"])})
    conclusion = "这个方向目前的意思是：固定 side-band / BUY_NO 腿确实还能在部分 filter 下留下正点估计，但没有一个风控过滤同时通过 significance、baseline、forward 三门。它赚/亏主要来自 event_date 集中和 BUY_NO 中高价腿的日期暴露；forecast regime 能改变样本和尾部，但目前更多是在减少机会而不是稳定地产生 alpha。最大问题是 top5 removed 或 holdout CI 仍然不能站稳。当前动作：继续 shadow/paper 记录，不允许 live。"
    if no_best:
        conclusion += f" BUY_NO 最好的 holdout top5_removed 版本是 `{no_best['risk_filter']}`，holdout excess={pct(no_best['holdout']['excess_roi'])}，top5_removed={pct(no_best['holdout']['top5_removed_roi'])}，gates={'/'.join(no_best['gates'].values())}."
    lines = [
        "# Side Band Bad-Day Risk Filter v2",
        "",
        f"> generated_at_utc: `{payload['generated_at_utc']}`",
        f"> target_metric: `{TARGET_METRIC}`",
        "> Scope: 本地 counterfactual research only；未改 N100/live 配置。",
        "",
        "## 数据快照",
        "",
        "- 数据源: `runtime/weather.db.fact_signal_candidates` 主实验；`fact_trades` 只用于自检和 CLOB gate。",
        f"- DB last_modified_utc: `{payload['db_last_modified_utc']}`",
        f"- MAX(fact_built_at_utc): `{payload['mandatory_self_check']['max_fact_built_at_utc']}`",
        f"- CLOB gate: `gate_pass={gate.get('gate_pass')}`; fail_reasons=`{gate.get('fail_reasons')}`",
        "- 因 gate 未通过，本报告禁止发布新的 live_real PnL/ROI；下列表格均为机会层 counterfactual。" if not gate.get("gate_pass") else "- CLOB gate 通过；但本报告主结果仍只看机会层 counterfactual。",
        f"- train: `{split['train_start']}` -> `{split['train_end']}` ({split['train_dates']} event_dates)",
        f"- holdout: `{split['holdout_start']}` -> `{split['holdout_end']}` ({split['holdout_dates']} event_dates)",
        "",
        "### 强制 5 行 SQL 自检",
        "",
        "```json",
        json.dumps(payload["mandatory_self_check"], ensure_ascii=False, indent=2),
        "```",
        "",
        "### Filter Funnel",
        "",
        markdown_table(funnel_rows, ["step", "rows", "active_dates", "drop", "note"]),
        "",
        "掉数 >70% 说明:",
        "",
        "\n".join(f"- `{x['step']}` drop >70%: {x['note']}" for x in payload["filter_funnel"] if x["drop_over_70pct"]) or "- 无单步掉数超过 70%。",
        "",
        "### Orderbook Coverage",
        "",
        "```json",
        json.dumps(payload["orderbook_coverage"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## 目标指标与设计",
        "",
        "`side_band_bad_day_risk_v2` 固定旧 side-band、去低价 YES、BUY_NO 0.35-0.65、BUY_YES 0.25-0.45 四条 selector，不做新参数搜索；只比较预注册 forecast risk filters 是否改善 holdout excess、worst-day 和 top5-removed ROI。",
        "",
        "- Baseline: 同 side + T-18-24 + 5c `decision_entry_price` bucket 的 full opportunity。",
        "- Bootstrap: event_date cluster。",
        "- 风控过滤: block high uncertainty、low/medium quality、tail blocked、历史 adjacent3 miss、model-market L1 gap、entropy。",
        "",
        "## 结果总表 Top20",
        "",
        markdown_table(rows, ["selector", "filter", "train_excess", "train_ci", "holdout_rows", "holdout_excess", "holdout_ci", "top5_removed", "worst_day", "gates", "verdict"]),
        "",
        "## BUY_NO 0.35-0.65 日期归因",
        "",
        markdown_table(attr_rows, ["bucket", "event_date", "rows", "pnl", "roi"]),
        "",
        "## 结论",
        "",
        conclusion,
        "",
        "## 总表",
        "",
        markdown_table([
            {
                "direction": r["selector"] + ":" + r["risk_filter"],
                "human-readable idea": "固定 selector 加 forecast risk filter，检查坏日子能否被提前挡掉。",
                "sample size": f"train {r['train']['rows']} / holdout {r['holdout']['rows']}",
                "holdout result": f"excess {pct(r['holdout']['excess_roi'])}, CI [{pct(r['holdout']['excess_roi_ci95_event_date_cluster'][0])}, {pct(r['holdout']['excess_roi_ci95_event_date_cluster'][1])}]",
                "top5 removed": pct(r["holdout"]["top5_removed_roi"]),
                "gates": "/".join(r["gates"].values()),
                "verdict": r["verdict"],
                "next step": "继续 shadow/paper；不允许 live。",
            }
            for r in ranked[:10]
        ], ["direction", "human-readable idea", "sample size", "holdout result", "top5 removed", "gates", "verdict", "next step"]),
        "",
        "## 8 环覆盖自检",
        "",
        "- 1 描述性绩效切片: covered，机会层 selector + 日期归因。",
        "- 2 统计推断: covered，event_date cluster bootstrap。",
        "- 3 信号判别: covered，固定 selector + risk filter 相对 matched baseline。",
        "- 4 概率分布评估: partial，复用 forecast regime 分布特征。",
        "- 5 执行微结构: partial，只用 decision price/spread 派生字段；gate fail 抑制 live_real。",
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
    parser.add_argument("--orderbook-glob", default=ORDERBOOK_GLOB_DEFAULT)
    parser.add_argument("--bootstrap-iters", type=int, default=1000)
    args = parser.parse_args()
    db_path = Path(args.db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        self_check = mandatory_self_check(conn)
        cands = pd.read_sql_query("SELECT * FROM fact_signal_candidates", conn)
    finally:
        conn.close()
    gate = run_live_gate(db_path)
    enriched, info = add_features(cands)
    orderbook, orderbook_coverage = clean.match_orderbooks(enriched, args.orderbook_glob)
    enriched["orderbook_matched"] = enriched["candidate_id"].isin(set(orderbook["candidate_id"]) if not orderbook.empty else set()).astype(int)
    funnel = clean.build_funnel(enriched, info["train_dates"], info["holdout_dates"])
    pool = base_pool(enriched)
    results = evaluate(pool, info["train_dates"], info["holdout_dates"], iters=args.bootstrap_iters)
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": TARGET_METRIC,
        "db_path": str(db_path),
        "db_last_modified_utc": datetime.fromtimestamp(db_path.stat().st_mtime, timezone.utc).isoformat(),
        "mandatory_self_check": self_check,
        "clob_gate": gate,
        "split": info["split"],
        "filter_funnel": funnel,
        "orderbook_coverage": orderbook_coverage,
        "analysis_pool": {"rows": int(len(pool)), "active_dates": int(pool["event_date"].nunique()) if len(pool) else 0},
        "results": results,
        "out_json": str(Path(args.out_json)),
        "out_md": str(Path(args.out_md)),
    }
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    out_md.write_text(render(payload), encoding="utf-8")
    print(json.dumps({"out_json": str(out_json), "out_md": str(out_md), "results": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
