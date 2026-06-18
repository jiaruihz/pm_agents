#!/usr/bin/env python3
"""Low-price YES reheat-reversal v1.

This version uses the shared reheat feature factory rather than the older
lottery/side-band bridge.  The label is target_yes_wins: the higher target
bracket wins after the market had already seen a lower running max.
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
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/low_price_yes_reheat_reversal_v1"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-18-low-price-yes-reheat-reversal-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-18-low-price-yes-reheat-reversal-v1.md"

SPLIT_DATE = "2026-06-01"
LOW_PRICE_MAX = 0.25
SEED = 20260618


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value) * 100:+.1f}%"


def num(value: float | None, digits: int = 2) -> str:
    if value is None or not math.isfinite(float(value)):
        return "NA"
    return f"{float(value):+.{digits}f}"


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


def distance_bucket(row: pd.Series) -> str:
    bracket = str(row["bracket"])
    if bracket == str(row.get("d1_no_bracket")):
        return "d1"
    if bracket == str(row.get("d2_no_bracket")):
        return "d2"
    return "d3plus"


def load_candidates() -> tuple[pd.DataFrame, dict[str, Any]]:
    df = pd.read_csv(FEATURE_ROWS)
    df["target_date"] = df["target_date"].astype(str)
    prior = load_forecast_prior()
    df = df.merge(prior, on=["city", "target_date", "bracket"], how="left")
    df["period"] = np.where(df["target_date"] < SPLIT_DATE, "train", "holdout")
    df["target_yes_wins"] = df["target_hit"].astype(float)
    df["target_distance_native"] = df["bracket_low"] - df["running_native"]
    df["gap_current_to_target_low_native"] = df["bracket_low"] - df["current_native"]
    df["distance_bucket"] = df.apply(distance_bucket, axis=1)
    df["source_aligned"] = df["source_system"].eq("pm_history")
    df["target_yes_pnl"] = df["target_yes_wins"] - df["target_yes_ask"]
    df["current_yes_pnl"] = df["current_bracket_held"] - df["current_yes_ask"]
    df["d1_no_pnl"] = (1.0 - df["d1_hit"]) - df["d1_no_ask"]
    df["d2_no_pnl"] = (1.0 - df["d2_hit"]) - df["d2_no_ask"]
    df["dewpoint_depression_f"] = df["tmpf_now"] - df["dwpf_now"]
    df["log_target_yes_size"] = np.log1p(df["target_yes_ask_size"].fillna(0).clip(lower=0))
    df["deep_fade_state"] = (
        df["decline_native"].ge(2.0)
        & df["minutes_since_running_max"].ge(90)
        & df["temp_trend_1h_f"].le(0)
    )
    df["forecast_peak_any_present"] = df[["gfs_forecast_peak_present", "ecmwf_forecast_peak_present"]].eq(True).any(axis=1)

    base = df[
        df["outcome"].eq("yes")
        & df["settlement_status"].eq("settled")
        & df["source_aligned"]
        & df["target_yes_ask"].between(0.005, LOW_PRICE_MAX, inclusive="both")
        & df["target_distance_native"].gt(0)
        & df["raw_model_p_yes"].notna()
    ].copy()
    coverage = {
        "feature_rows": int(len(df)),
        "feature_states": int(df[["city", "target_date", "decision_snapshot_ts_utc", "decision_hour_local"]].drop_duplicates().shape[0]),
        "candidate_rows": int(len(base)),
        "candidate_active_dates": int(base["target_date"].nunique()),
        "candidate_cities": int(base["city"].nunique()),
        "candidate_hit_rate": float(base["target_yes_wins"].mean()) if not base.empty else None,
        "target_distance_bucket_rows": base["distance_bucket"].value_counts(dropna=False).to_dict(),
        "forecast_peak_any_present_rate": float(base["forecast_peak_any_present"].mean()) if not base.empty else None,
        "feature_factory_csv": str(FEATURE_ROWS.relative_to(ROOT)),
        "forecast_peak_note": "backfilled GFS/ECMWF peak-clock fields are mostly present and used as research features; production-native peak-clock availability is still not a live hard gate",
    }
    return base, coverage


NUM_FEATURES = [
    "decision_hour_local",
    "running_native",
    "current_native",
    "decline_native",
    "decline_from_max_c",
    "target_distance_native",
    "gap_current_to_target_low_native",
    "minutes_since_running_max",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "sky_cover_code",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "target_yes_ask",
    "target_yes_spread",
    "log_target_yes_size",
    "current_yes_ask",
    "d1_no_ask",
    "d2_no_ask",
    "raw_model_p_yes",
    "gfs_forecast_peak_delta_hours_local",
    "ecmwf_forecast_peak_delta_hours_local",
    "forecast_peak_hour_spread",
    "gfs_forecast_gap_to_running_native",
    "ecmwf_forecast_gap_to_running_native",
]
CAT_FEATURES = ["city", "unit", "distance_bucket"]


def fit_reheat_head(rows: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    train = rows[rows["period"].eq("train")].copy()
    if train["target_yes_wins"].nunique() < 2:
        raise RuntimeError("train set does not contain both labels")
    model = Pipeline(
        [
            (
                "pre",
                ColumnTransformer(
                    [
                        ("num", Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), NUM_FEATURES),
                        ("cat", OneHotEncoder(handle_unknown="ignore"), CAT_FEATURES),
                    ]
                ),
            ),
            ("clf", LogisticRegression(max_iter=1000, class_weight="balanced", random_state=SEED)),
        ]
    )
    model.fit(train[NUM_FEATURES + CAT_FEATURES], train["target_yes_wins"])
    scored = rows.copy()
    scored["p_reheat_context"] = model.predict_proba(scored[NUM_FEATURES + CAT_FEATURES])[:, 1]
    scored["raw_edge"] = scored["raw_model_p_yes"] - scored["target_yes_ask"]
    scored["blended_p_yes"] = 0.30 * scored["raw_model_p_yes"] + 0.70 * scored["target_yes_ask"]
    scored["blended_edge"] = scored["blended_p_yes"] - scored["target_yes_ask"]
    scored["p_target_yes_wins"] = (scored["raw_model_p_yes"] * scored["p_reheat_context"]).clip(0, 1)
    scored["reheat_adjusted_edge"] = scored["p_target_yes_wins"] - scored["target_yes_ask"]
    metrics: dict[str, Any] = {}
    for name in ["train", "holdout"]:
        part = scored[scored["period"].eq(name)].copy()
        if part.empty:
            metrics[name] = {"rows": 0}
            continue
        y = part["target_yes_wins"]
        p = part["p_reheat_context"]
        metrics[name] = {
            "rows": int(len(part)),
            "positives": int(y.sum()),
            "hit_rate": float(y.mean()),
            "auc_context": float(roc_auc_score(y, p)) if y.nunique() > 1 else None,
            "brier_context": float(brier_score_loss(y, p)),
        }
    return scored, metrics


def roi_ci_by_date(part: pd.DataFrame, pnl_col: str, cost_col: str, reps: int = 1000) -> dict[str, float | None]:
    valid = part[[pnl_col, cost_col, "target_date"]].dropna()
    if valid.empty or valid["target_date"].nunique() < 2:
        return {"ci_low": None, "ci_high": None, "mean": None}
    by_date = valid.groupby("target_date").agg(pnl=(pnl_col, "sum"), cost=(cost_col, "sum"))
    rng = np.random.default_rng(SEED)
    idx = np.arange(len(by_date))
    samples = []
    vals = by_date.to_numpy()
    for _ in range(reps):
        draw = vals[rng.choice(idx, size=len(idx), replace=True)]
        cost = draw[:, 1].sum()
        samples.append(draw[:, 0].sum() / cost if cost else np.nan)
    arr = np.asarray(samples)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"ci_low": None, "ci_high": None, "mean": None}
    return {
        "mean": float(np.mean(arr)),
        "ci_low": float(np.quantile(arr, 0.025)),
        "ci_high": float(np.quantile(arr, 0.975)),
    }


def summarize_expression(part: pd.DataFrame, pnl_col: str, cost_col: str) -> dict[str, Any]:
    valid = part[[pnl_col, cost_col]].dropna()
    if valid.empty:
        return {"rows": 0}
    cost = float(valid[cost_col].sum())
    pnl = float(valid[pnl_col].sum())
    return {
        "rows": int(len(valid)),
        "cost_proxy": cost,
        "pnl_proxy": pnl,
        "roi_proxy": pnl / cost if cost else None,
    }


def top_date_removed_roi(part: pd.DataFrame, pnl_col: str, cost_col: str, n: int) -> float | None:
    valid = part[["target_date", pnl_col, cost_col]].dropna()
    if valid.empty or valid["target_date"].nunique() <= n:
        return None
    by_date = valid.groupby("target_date").agg(pnl=(pnl_col, "sum"), cost=(cost_col, "sum"))
    remove = by_date.sort_values("pnl", ascending=False).head(n).index
    kept = valid[~valid["target_date"].isin(remove)]
    cost = float(kept[cost_col].sum())
    return float(kept[pnl_col].sum()) / cost if cost else None


def summarize_selector(df: pd.DataFrame, selector: str, mask: pd.Series) -> dict[str, Any]:
    part = df[mask].copy()
    if part.empty:
        return {"selector": selector, "rows": 0}
    target = summarize_expression(part, "target_yes_pnl", "target_yes_ask")
    current = summarize_expression(part, "current_yes_pnl", "current_yes_ask")
    d1 = summarize_expression(part, "d1_no_pnl", "d1_no_ask")
    d2 = summarize_expression(part, "d2_no_pnl", "d2_no_ask")
    return {
        "selector": selector,
        "rows": int(len(part)),
        "active_dates": int(part["target_date"].nunique()),
        "cities": int(part["city"].nunique()),
        "hit_rate": float(part["target_yes_wins"].mean()),
        "avg_ask": float(part["target_yes_ask"].mean()),
        "distance_bucket_rows": part["distance_bucket"].value_counts(dropna=False).to_dict(),
        "deep_fade_rate": float(part["deep_fade_state"].mean()),
        "target_yes": target,
        "same_state_current_yes": current,
        "same_state_d1_no": d1,
        "same_state_d2_no": d2,
        "target_roi_ci_by_date": roi_ci_by_date(part, "target_yes_pnl", "target_yes_ask"),
        "top1_date_removed_target_roi": top_date_removed_roi(part, "target_yes_pnl", "target_yes_ask", 1),
        "top3_date_removed_target_roi": top_date_removed_roi(part, "target_yes_pnl", "target_yes_ask", 3),
        "top_cities": (
            part.groupby("city")
            .agg(rows=("city", "size"), pnl_proxy=("target_yes_pnl", "sum"), cost_proxy=("target_yes_ask", "sum"))
            .assign(roi_proxy=lambda x: x["pnl_proxy"] / x["cost_proxy"])
            .sort_values("pnl_proxy", ascending=False)
            .head(8)
            .reset_index()
            .to_dict("records")
        ),
        "top_dates": (
            part.groupby("target_date")
            .agg(rows=("target_date", "size"), pnl_proxy=("target_yes_pnl", "sum"), cost_proxy=("target_yes_ask", "sum"))
            .assign(roi_proxy=lambda x: x["pnl_proxy"] / x["cost_proxy"])
            .sort_values("pnl_proxy", ascending=False)
            .head(8)
            .reset_index()
            .to_dict("records")
        ),
    }


def build_summaries(scored: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for period in ["train", "holdout"]:
        p = scored["period"].eq(period)
        d12 = scored["distance_bucket"].isin(["d1", "d2"])
        not_deep_fade = ~scored["deep_fade_state"]
        selectors = [
            (f"{period}:all_low_price_above_running", p),
            (f"{period}:raw_edge_gt_0", p & scored["raw_edge"].gt(0)),
            (f"{period}:blended_edge_gt_0", p & scored["blended_edge"].gt(0)),
        ]
        for threshold in [0.05, 0.08, 0.10]:
            edge = scored["reheat_adjusted_edge"].ge(threshold)
            selectors.extend(
                [
                    (f"{period}:adjusted_edge_ge_{threshold:.2f}", p & edge),
                    (f"{period}:d1d2_adjusted_edge_ge_{threshold:.2f}", p & d12 & edge),
                    (f"{period}:d1d2_not_deep_fade_adjusted_edge_ge_{threshold:.2f}", p & d12 & not_deep_fade & edge),
                ]
            )
        rows.extend(summarize_selector(scored, name, mask) for name, mask in selectors)
    return rows


def table(rows: list[dict[str, Any]]) -> str:
    cols = [
        "selector",
        "rows",
        "days",
        "cities",
        "hit",
        "ask",
        "target_roi",
        "ci",
        "top3_removed",
        "current_yes_roi",
        "d1_no_roi",
        "d2_no_roi",
    ]
    out = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] + ["---:"] * (len(cols) - 1)) + " |"]
    for row in rows:
        ci = row.get("target_roi_ci_by_date", {})
        out.append(
            "| "
            + " | ".join(
                [
                    str(row.get("selector", "")),
                    str(row.get("rows", 0)),
                    str(row.get("active_dates", "")),
                    str(row.get("cities", "")),
                    pct(row.get("hit_rate")),
                    pct(row.get("avg_ask")),
                    pct(row.get("target_yes", {}).get("roi_proxy")),
                    f"[{pct(ci.get('ci_low'))}, {pct(ci.get('ci_high'))}]" if ci else "NA",
                    pct(row.get("top3_date_removed_target_roi")),
                    pct(row.get("same_state_current_yes", {}).get("roi_proxy")),
                    pct(row.get("same_state_d1_no", {}).get("roi_proxy")),
                    pct(row.get("same_state_d2_no", {}).get("roi_proxy")),
                ]
            )
            + " |"
        )
    return "\n".join(out)


def short_shadow_verdict(edge_summary: list[dict[str, Any]]) -> dict[str, Any]:
    holdout = [r for r in edge_summary if r["selector"] == "holdout:d1d2_adjusted_edge_ge_0.08"]
    if not holdout or holdout[0].get("rows", 0) == 0:
        return {"status": "research_only", "reason": "no holdout rows for d1/d2 edge>=0.08"}
    row = holdout[0]
    ci = row.get("target_roi_ci_by_date", {})
    roi = row.get("target_yes", {}).get("roi_proxy")
    if row["rows"] >= 20 and row["active_dates"] >= 8 and roi is not None and roi > 0:
        return {
            "status": "shadow_candidate",
            "reference_rule": row["selector"],
            "reason": "positive holdout point estimate with enough rows to start zero-notional telemetry, but not live-significant",
            "significance_gate": "PASS" if ci.get("ci_low") is not None and ci["ci_low"] > 0 else "FAIL",
            "baseline_gate": "FAIL",
            "forward_gate": "PARTIAL",
        }
    return {
        "status": "research_only",
        "reference_rule": row["selector"],
        "reason": "holdout sample/point estimate is not enough even for a clean shadow trigger",
        "significance_gate": "FAIL",
        "baseline_gate": "FAIL",
        "forward_gate": "FAIL",
    }


def render_report(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Low-Price YES Reheat Reversal v1",
            "",
            f"> generated_at_utc: `{payload['generated_at_utc']}`",
            "> Scope: research / shadow-candidate evaluation only; no N100/live behavior changed.",
            "",
            "## 一句话结论",
            "",
            "低价 YES 这条已经从“便宜彩票”升级成 `low_price_yes_reheat_reversal` 的独立 reheat-conditioned head。v1 可以作为 zero-notional shadow 的候选继续跑，但不够 live：同状态替代表达（current YES / d1 NO / d2 NO）还没有形成稳定 baseline excess，forecast peak clock 在主窗口仍不是可用硬门。",
            "",
            "## 数据快照",
            "",
            f"- Feature source: `{payload['coverage']['feature_factory_csv']}`",
            f"- Forecast prior: `runtime/weather.db.fact_signal_candidates` raw `model_p_yes` by city/date/bracket",
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
            "`low_price_yes_reheat_reversal_v1` row grain = `city + target_date + decision_snapshot_ts_utc + decision_hour_local + target YES bracket`。",
            "",
            "- label: `target_yes_wins = target_hit`，不是 `current_yes_wins`。",
            "- candidate: `YES ask <= 0.25`、target bracket 高于 observed running max、source-aligned、settled、forecast prior 可 join。",
            "- probability: `p_target_yes_wins = raw_model_p_yes * p_reheat_context`。",
            "- edge: `p_target_yes_wins - target_yes_ask`，主阈值看 `0.05 / 0.08 / 0.10`。",
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
            "## Edge / Expression Comparison",
            "",
            table(payload["edge_summary"]),
            "",
            "## Shadow / Live Readiness",
            "",
            "当前判定：`shadow_candidate / not_live`。",
            "",
            "Shadow 还差的是工程化，不是再争论方向：",
            "",
            "1. 冻结一个 shadow trigger（建议先用 `d1/d2 + adjusted_edge>=0.08`，同时记录 0.05/0.10 旁路）。",
            "2. 接 fresh top-of-book ask/depth，写 zero-notional would-order journal，不下真钱。",
            "3. 每日 settlement 后输出同状态 current YES / d1 NO / d2 NO 的 paired scorecard。",
            "",
            "Live 还差的是证据门：",
            "",
            "1. forward shadow 至少覆盖多个 active dates，且相对 same-state 表达的 excess 不靠单日大赢家。",
            "2. official observation / forecast peak clock 在生产链路可用；当前 peak 字段不能作为硬门。",
            "3. tiny-live 前需要独立 risk cap、fresh ask 滑点/深度门、CLOB gate=true，以及 deploy skill 的 git-first 流程。",
            "",
            "## Contract Verdict",
            "",
            "```json",
            json.dumps(payload["verdict"], ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows, coverage = load_candidates()
    if rows.empty:
        raise RuntimeError("no low-price YES reheat candidates")
    scored, model_metrics = fit_reheat_head(rows)
    edge_summary = build_summaries(scored)
    scored_out = OUT_DIR / "scored_rows.csv"
    scored.to_csv(scored_out, index=False)
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "low_price_yes_reheat_reversal_v1",
        "row_grain": "city + target_date + decision_snapshot_ts_utc + decision_hour_local + target YES bracket",
        "label": "target_yes_wins",
        "self_check": data_self_check(),
        "clob_gate": load_gate(),
        "coverage": coverage,
        "model_metrics": model_metrics,
        "edge_summary": edge_summary,
        "artifacts": {"scored_rows_csv": str(scored_out.relative_to(ROOT))},
        "verdict": short_shadow_verdict(edge_summary),
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_report(payload), encoding="utf-8")
    print(json.dumps({"out_json": str(OUT_JSON), "out_md": str(OUT_MD), "rows": len(scored)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
