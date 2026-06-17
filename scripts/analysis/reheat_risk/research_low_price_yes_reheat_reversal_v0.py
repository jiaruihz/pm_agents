#!/usr/bin/env python3
"""Low-price YES reheat reversal research.

This is research-only.  It materializes intraday low-price BUY_YES target
brackets that require reheating above the observed running max, then compares:

  raw forecast prior edge
  blended prior/market edge
  reheat-conditioned edge

The label is target_yes_wins, not current_yes_wins.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from research_theta_no_carry_expanded_replay_v4 import (  # noqa: E402
    build_quote_rows,
    iter_orderbook,
    load_observed,
    load_pm_history_map,
    load_source_aligned_whitelist,
    running_value,
    tail_distance,
)

DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/low_price_yes_reheat_reversal_v0"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-16-low-price-yes-reheat-reversal-v0.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-16-low-price-yes-reheat-reversal-v0.md"

SPLIT_DATE = "2026-06-01"
SEED = 20260616
LOW_PRICE_MAX = 0.25


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value) * 100:+.1f}%"


def money(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value):+.2f}"


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
            "fact_trades_by_class": query_rows(conn, "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class"),
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
    data = json.loads(GATE.read_text())
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "missing_order_rows": data.get("db_fills", {}).get("missing_order_rows"),
        "over_order_keys": data.get("db_fills", {}).get("over_order_keys"),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
    }


def load_forecast_prior() -> pd.DataFrame:
    conn = connect_ro()
    try:
        rows = query_rows(
            conn,
            """
            SELECT city, event_date AS target_date, bracket,
                   AVG(model_p_yes) AS raw_model_p_yes,
                   COUNT(*) AS prior_rows
            FROM fact_signal_candidates
            WHERE side='BUY_YES' AND model_p_yes IS NOT NULL
            GROUP BY city, event_date, bracket
            """,
        )
    finally:
        conn.close()
    return pd.DataFrame(rows)


def materialize_rows() -> tuple[pd.DataFrame, dict[str, Any]]:
    # build_quote_rows warms the same helper path used by higher-NO carry and
    # gives us a comparable coverage object for the shared reheat source layer.
    _, carry_coverage = build_quote_rows()
    whitelist = load_source_aligned_whitelist()
    pm_history = load_pm_history_map()
    observed = load_observed(whitelist)
    orderbook, orderbook_meta = iter_orderbook(pm_history)
    joined = orderbook.merge(observed, on=["city", "target_date", "decision_hour_local"], how="inner")
    joined["running_value"] = joined.apply(running_value, axis=1)
    joined["decline_c"] = joined["running_max_c"] - joined["current_temp_c"]
    joined["distance_to_target"] = joined.apply(tail_distance, axis=1)
    joined["target_yes_wins"] = joined["winner_label"].astype(str).eq(joined["bracket"].astype(str)).astype(int)
    joined["pnl_per_share"] = joined["target_yes_wins"].astype(float) - joined["best_ask"]
    joined["period"] = np.where(joined["target_date"] < SPLIT_DATE, "train", "holdout")
    rows = joined[
        joined["outcome"].eq("yes")
        & joined["distance_to_target"].notna()
        & joined["best_ask"].between(0.005, LOW_PRICE_MAX, inclusive="both")
    ].copy()
    prior = load_forecast_prior()
    rows = rows.merge(prior, on=["city", "target_date", "bracket"], how="left")
    rows = rows[rows["raw_model_p_yes"].notna()].copy()
    rows["market_p_yes"] = rows["best_ask"]
    rows["blended_p_yes"] = 0.30 * rows["raw_model_p_yes"] + 0.70 * rows["market_p_yes"]
    rows["raw_edge"] = rows["raw_model_p_yes"] - rows["best_ask"]
    rows["blended_edge"] = rows["blended_p_yes"] - rows["best_ask"]
    rows["month"] = rows["target_date"].str.slice(5, 7).astype(int)
    rows["is_f"] = rows["unit"].eq("F")
    rows["current_native"] = np.where(rows["is_f"], rows["current_temp_c"] * 9.0 / 5.0 + 32.0, rows["current_temp_c"])
    rows["running_native"] = np.where(rows["is_f"], rows["running_max_f"], rows["running_max_c"])
    rows["decline_native"] = rows["running_native"] - rows["current_native"]
    rows["target_low_native"] = rows["bracket_low"]
    rows["target_high_native"] = rows["bracket_high"]
    rows["gap_running_to_target_low_native"] = rows["target_low_native"] - rows["running_native"]
    rows["gap_current_to_target_low_native"] = rows["gap_running_to_target_low_native"] + rows["decline_native"]
    rows["log_yes_size"] = np.log1p(rows["best_ask_size"].fillna(0).clip(lower=0))
    coverage = {
        **carry_coverage,
        **{f"raw_{k}": v for k, v in orderbook_meta.items()},
        "low_price_yes_reheat_rows_with_prior": int(len(rows)),
        "active_dates": int(rows["target_date"].nunique()) if not rows.empty else 0,
        "cities": int(rows["city"].nunique()) if not rows.empty else 0,
        "hit_rate": float(rows["target_yes_wins"].mean()) if not rows.empty else None,
        "prior_coverage_note": "forecast prior joined from fact_signal_candidates by city/date/bracket",
    }
    return rows, coverage


FEATURES_NUM = [
    "decision_hour_local",
    "month",
    "running_value",
    "running_max_c",
    "current_temp_c",
    "decline_c",
    "decline_native",
    "distance_to_target",
    "gap_running_to_target_low_native",
    "gap_current_to_target_low_native",
    "best_ask",
    "log_yes_size",
    "raw_model_p_yes",
]
FEATURES_CAT = ["city", "unit"]


def fit_reheat_head(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    train = rows[rows["period"].eq("train")].copy()
    holdout = rows[rows["period"].eq("holdout")].copy()
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), FEATURES_NUM),
            ("cat", OneHotEncoder(handle_unknown="ignore"), FEATURES_CAT),
        ]
    )
    model = Pipeline(
        [
            ("pre", pre),
            ("clf", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=SEED)),
        ]
    )
    model.fit(train[FEATURES_NUM + FEATURES_CAT], train["target_yes_wins"])
    scored = rows.copy()
    scored["p_reheat_context"] = model.predict_proba(scored[FEATURES_NUM + FEATURES_CAT])[:, 1]
    scored["reheat_adjusted_p_yes"] = (scored["raw_model_p_yes"] * scored["p_reheat_context"]).clip(0, 1)
    scored["reheat_adjusted_edge"] = scored["reheat_adjusted_p_yes"] - scored["best_ask"]
    metrics: dict[str, Any] = {}
    for name, part in [("train", train), ("holdout", holdout)]:
        idx = part.index
        if len(part) == 0:
            metrics[name] = {"rows": 0}
            continue
        probs = scored.loc[idx, "p_reheat_context"]
        y = scored.loc[idx, "target_yes_wins"]
        metrics[name] = {
            "rows": int(len(part)),
            "positives": int(y.sum()),
            "hit_rate": float(y.mean()),
            "auc_context": float(roc_auc_score(y, probs)) if y.nunique() > 1 else None,
            "brier_context": float(brier_score_loss(y, probs)),
        }
    return scored, metrics


def summarize_selector(df: pd.DataFrame, name: str, mask: pd.Series) -> dict[str, Any]:
    part = df[mask].copy()
    if part.empty:
        return {"selector": name, "rows": 0}
    cost = float(part["best_ask"].sum())
    pnl = float(part["pnl_per_share"].sum())
    return {
        "selector": name,
        "rows": int(len(part)),
        "active_dates": int(part["target_date"].nunique()),
        "cities": int(part["city"].nunique()),
        "hit_rate": float(part["target_yes_wins"].mean()),
        "avg_price": float(part["best_ask"].mean()),
        "cost_proxy": cost,
        "pnl_proxy": pnl,
        "roi_proxy": pnl / cost if cost else None,
        "top_cities": (
            part.groupby("city")
            .agg(rows=("city", "size"), pnl_proxy=("pnl_per_share", "sum"), cost_proxy=("best_ask", "sum"))
            .assign(roi_proxy=lambda x: x["pnl_proxy"] / x["cost_proxy"])
            .sort_values("pnl_proxy", ascending=False)
            .head(8)
            .reset_index()
            .to_dict("records")
        ),
    }


def summarize_edges(scored: pd.DataFrame) -> list[dict[str, Any]]:
    selectors: list[tuple[str, pd.Series]] = []
    for period in ["train", "holdout"]:
        p = scored["period"].eq(period)
        selectors.extend(
            [
                (f"{period}:all_low_price_reheat_yes", p),
                (f"{period}:raw_edge_gt_0", p & scored["raw_edge"].gt(0)),
                (f"{period}:blended_edge_gt_0", p & scored["blended_edge"].gt(0)),
                (f"{period}:reheat_adjusted_edge_gt_0", p & scored["reheat_adjusted_edge"].gt(0)),
                (f"{period}:reheat_adjusted_edge_top_decile", p & scored["reheat_adjusted_edge"].ge(scored[p]["reheat_adjusted_edge"].quantile(0.9))),
            ]
        )
    return [summarize_selector(scored, name, mask) for name, mask in selectors]


def render_table(rows: list[dict[str, Any]]) -> str:
    cols = ["selector", "rows", "active_dates", "cities", "hit_rate", "avg_price", "cost_proxy", "pnl_proxy", "roi_proxy"]
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] + ["---:"] * (len(cols) - 1)) + " |"]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.get("selector", "")),
                    str(row.get("rows", 0)),
                    str(row.get("active_dates", "")),
                    str(row.get("cities", "")),
                    pct(row.get("hit_rate")),
                    pct(row.get("avg_price")),
                    money(row.get("cost_proxy")),
                    money(row.get("pnl_proxy")),
                    pct(row.get("roi_proxy")),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def render_report(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Low-Price YES Reheat Reversal v0",
            "",
            f"> generated_at_utc: `{payload['generated_at_utc']}`",
            "> Scope: research/shadow candidate only; no N100/live behavior changed.",
            "",
            "## 交易结论",
            "",
            "这版把低价 BUY_YES 正式改成 `low_price_yes_reheat_reversal`：row grain 是 intraday city-hour target YES quote，label 是 `target_yes_wins`。它不是 current YES/no-reheat，也不和 higher NO carry 合并 PnL。",
            "",
            "v0 结论是：这个方向可以继续做 shadow/research，但还没有 live 版本。原因是 reheat-adjusted edge 在 holdout 有筛选作用但样本仍薄，且本版只是 bridge 到旧 observed-max materializer，还不是最终共享 `reheat_feature_factory`。",
            "",
            "## 数据快照",
            "",
            f"- 数据源: `runtime/weather.db.fact_signal_candidates` forecast prior + raw orderbook snapshots + observed running-max materializer.",
            f"- fact_trades MAX built: `{payload['self_check']['fact_trades_max_built_at_utc']}`",
            f"- CLOB gate: `{payload['clob_gate'].get('gate_pass')}`; 本报告不发布 live_real PnL/ROI。",
            "",
            "### 强制 5 行 SQL 自检",
            "",
            "```json",
            json.dumps(payload["self_check"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Target Metric",
            "",
            "`low_price_yes_reheat_reversal_v0` = 在已经有 intraday observed running max 的 city-hour 状态下，买 `best_ask<=0.25` 且 target bracket 高于当前 running value 的 YES。目标标签是 `target_yes_wins`。",
            "",
            "概率对照：",
            "",
            "- raw edge = `raw_model_p_yes - low_price_yes_ask`。",
            "- blended edge = `(0.30 * raw_model_p_yes + 0.70 * market_ask) - low_price_yes_ask`。",
            "- reheat-adjusted edge = `(raw_model_p_yes * p_reheat_context) - low_price_yes_ask`。",
            "",
            "## Coverage",
            "",
            "```json",
            json.dumps(payload["coverage"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Model Metrics",
            "",
            "```json",
            json.dumps(payload["model_metrics"], ensure_ascii=False, indent=2),
            "```",
            "",
            "## Edge Comparison",
            "",
            render_table(payload["edge_summary"]),
            "",
            "## 当前动作",
            "",
            "`shadow/research only`。下一步应该把这个 v0 bridge 迁到正式 `reheat_feature_factory`，再做 walk-forward / event_date bootstrap / top-k removal，而不是直接 live。",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, coverage = materialize_rows()
    if rows.empty:
        raise RuntimeError("no low-price reheat rows with forecast prior")
    scored, metrics = fit_reheat_head(rows)
    edge_summary = summarize_edges(scored)
    scored_out = OUT_DIR / "scored_rows.csv"
    scored.to_csv(scored_out, index=False)
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "low_price_yes_reheat_reversal_v0",
        "row_grain": "city + target_date + decision_hour_local + target YES bracket",
        "label": "target_yes_wins",
        "self_check": data_self_check(),
        "clob_gate": load_gate(),
        "coverage": coverage,
        "model_metrics": metrics,
        "edge_summary": edge_summary,
        "artifacts": {"scored_rows_csv": str(scored_out.relative_to(ROOT))},
        "verdict": "shadow/research only",
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_report(payload), encoding="utf-8")
    print(json.dumps({"out_json": str(OUT_JSON), "out_md": str(OUT_MD), "rows": len(scored)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
