#!/usr/bin/env python3
"""Tmax coherence v2 + below materializer audit + hazard prototype.

Research-only. This script does not touch live runners, runtime configs, or
orders. It reuses the materialized P5/P0 research layer and official
settlement_outcomes as point-in-time evidence for strategy research.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_tmax_cross_hour_coherence_audit_v1 as v1  # noqa: E402
import research_tmax_distribution_p0_anchor_scorecard_v1 as p0  # noqa: E402
import research_tmax_distribution_p1_fusion_scorecard_v1 as p1  # noqa: E402
import research_tmax_distribution_p4_observed_label_extension_v1 as p4  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v2"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-06-tmax-cross-hour-coherence-audit-v2.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-06-tmax-cross-hour-coherence-audit-v2.json"
DB_PATH = ROOT / "runtime/weather.db"

METHOD = v1.METHOD
MODEL_SPEC = v1.MODEL_SPEC
ALPHA_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        return _json_ready(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _safe_float(value: object) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _fmt_pct(value: object, *, signed: bool = True) -> str:
    v = _safe_float(value)
    if v is None:
        return "n/a"
    sign = "+" if signed else ""
    return f"{v:{sign}.1%}"


def _fmt_num(value: object, digits: int = 3) -> str:
    v = _safe_float(value)
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}"


def _table(df: pd.DataFrame, columns: list[str], max_rows: int | None = None) -> list[str]:
    if df.empty:
        return ["_No rows._"]
    if max_rows is not None:
        df = df.head(max_rows)
    pct_cols = {
        "v1_incoherence_mean",
        "v2_incoherence_mean",
        "v2_minus_v1_mean",
        "positive_rate_v1",
        "positive_rate_v2",
        "below_rate",
        "logloss_delta_vs_market",
        "brier_delta_vs_market",
    }
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in df.to_dict("records"):
        vals: list[str] = []
        for col in columns:
            val = row.get(col)
            if col in pct_cols:
                vals.append(_fmt_pct(val))
            elif isinstance(val, float):
                vals.append(_fmt_num(val, 4))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def _coherence_pairs_v1() -> pd.DataFrame:
    path = ROOT / "docs/analysis/2026-07/generated/tmax_cross_hour_coherence_audit_v1/coherence_pairs.csv"
    if path.exists():
        return pd.read_csv(path)
    _, pred_all, _, _, _ = v1._prediction_frames()
    pairs, _ = v1.build_coherence_pairs(pred_all)
    return pairs


def _date_block_mean_ci(rows: pd.DataFrame, value_col: str) -> dict[str, float]:
    if rows.empty:
        return {"mean": math.nan, "ci_low": math.nan, "ci_high": math.nan, "dates": 0}
    by_date = rows.groupby("target_date", as_index=False)[value_col].mean()
    mean = float(by_date[value_col].mean())
    if len(by_date) < 3:
        return {"mean": mean, "ci_low": math.nan, "ci_high": math.nan, "dates": int(len(by_date))}
    rng = np.random.default_rng(20260706)
    arr = by_date[value_col].to_numpy(dtype=float)
    boot = [float(np.mean(rng.choice(arr, size=len(arr), replace=True))) for _ in range(1000)]
    return {
        "mean": mean,
        "ci_low": float(np.percentile(boot, 2.5)),
        "ci_high": float(np.percentile(boot, 97.5)),
        "dates": int(len(by_date)),
    }


def build_coherence_v2(pairs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Add a minimal empirical survival update for no-break current states.

    For reached==current, v1 held P(current) fixed. v2 conditions on one more
    hour passing without reaching d1: P(current | no_break) =
    p_current / (p_current + (1-p_current) * q), where q is the dev_cv empirical
    probability that a non-current final path still has not broken d1 by the
    next adjacent hour.
    """

    out = pairs.copy()
    dev = out[out["scope"].eq("dev_cv")].copy()
    non_current = dev["actual_bucket_t1"].astype(str).ne("current")
    q_den = int(non_current.sum())
    q_num = int((non_current & dev["reached_bucket_from_t"].astype(str).eq("current")).sum())
    q = q_num / q_den if q_den else 1.0
    q = min(0.999, max(0.001, q))

    out["coherent_p_current_v1"] = pd.to_numeric(out["coherent_p_current"], errors="coerce")
    out["coherent_p_current_v2"] = out["coherent_p_current_v1"]
    mask = out["reached_bucket_from_t"].astype(str).eq("current")
    p_current = pd.to_numeric(out.loc[mask, "prev_p_current"], errors="coerce")
    out.loc[mask, "coherent_p_current_v2"] = p_current / (p_current + (1.0 - p_current) * q)
    out["incoherence_v1"] = pd.to_numeric(out["incoherence"], errors="coerce")
    out["incoherence_v2"] = pd.to_numeric(out["refit_p_current"], errors="coerce") - out["coherent_p_current_v2"]
    out["v2_survival_q_noncurrent_no_break"] = q

    rows: list[dict[str, Any]] = []
    for cols in [["scope", "reanchor_group"], ["reanchor_group"]]:
        for key, grp in out.groupby(cols, dropna=False):
            if not isinstance(key, tuple):
                key = (key,)
            row = {col: val for col, val in zip(cols, key)}
            ci_v1 = _date_block_mean_ci(grp, "incoherence_v1")
            ci_v2 = _date_block_mean_ci(grp, "incoherence_v2")
            row.update(
                {
                    "grouping": "+".join(cols),
                    "rows": int(len(grp)),
                    "dates": int(grp["target_date"].nunique()),
                    "cities": int(grp["city"].nunique()),
                    "v1_incoherence_mean": float(grp["incoherence_v1"].mean()),
                    "v2_incoherence_mean": float(grp["incoherence_v2"].mean()),
                    "v2_minus_v1_mean": float(grp["incoherence_v2"].mean() - grp["incoherence_v1"].mean()),
                    "positive_rate_v1": float((grp["incoherence_v1"] > 0).mean()),
                    "positive_rate_v2": float((grp["incoherence_v2"] > 0).mean()),
                    "v1_ci_low": ci_v1["ci_low"],
                    "v1_ci_high": ci_v1["ci_high"],
                    "v2_ci_low": ci_v2["ci_low"],
                    "v2_ci_high": ci_v2["ci_high"],
                }
            )
            rows.append(row)
    summary = pd.DataFrame(rows).sort_values(["grouping", "scope", "reanchor_group"], na_position="last")
    meta = {
        "survival_update": "p_current/(p_current+(1-p_current)*q)",
        "q_source": "dev_cv adjacent pairs where final_at_t1 is non-current",
        "q_noncurrent_no_break": q,
        "q_num": q_num,
        "q_den": q_den,
    }
    return out, summary, meta


def _settlement_winners() -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    try:
        return pd.read_sql_query(
            """
            SELECT city, target_date, bracket AS settlement_bracket, unit AS settlement_unit,
                   final_price, source_system
            FROM settlement_outcomes
            WHERE final_price >= 0.99
            """,
            conn,
        )
    finally:
        conn.close()


def _bucket_from_final(row: pd.Series, final_col: str) -> str:
    cur = p0._interval(row.get("current_bracket"))
    d1 = p0._interval(row.get("d1_no_bracket"))
    d2 = p0._interval(row.get("d2_no_bracket"))
    final = p0._interval(row.get(final_col))
    if cur is None or d1 is None or d2 is None or final is None:
        return "missing"
    if final[1] < cur[0] - 1e-9:
        return "below"
    if abs(final[0] - cur[0]) < 1e-9 and abs(final[1] - cur[1]) < 1e-9:
        return "current"
    if abs(final[0] - d1[0]) < 1e-9 and abs(final[1] - d1[1]) < 1e-9:
        return "d1"
    if abs(final[0] - d2[0]) < 1e-9 and abs(final[1] - d2[1]) < 1e-9:
        return "d2"
    if final[0] > d2[1] + 1e-9:
        return "tail"
    return "other"


def build_e2_below_audit() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "unit",
        "current_bracket",
        "d1_no_bracket",
        "d2_no_bracket",
        "final_winning_bracket",
        "current_yes_ask",
        "current_bracket_no_ask",
    ]
    atlas = pd.read_csv(p0.ATLAS_PATH, usecols=lambda c: c in cols)
    winners = _settlement_winners()
    winners = winners.sort_values(["city", "target_date", "final_price"], ascending=[True, True, False])
    winners = winners.drop_duplicates(["city", "target_date"], keep="first")
    merged = atlas.merge(winners, on=["city", "target_date"], how="left", validate="many_to_one")
    merged["bucket_from_settlement"] = merged.apply(lambda r: _bucket_from_final(r, "settlement_bracket"), axis=1)
    merged["bucket_from_atlas_final"] = merged.apply(lambda r: _bucket_from_final(r, "final_winning_bracket"), axis=1)
    summary_rows: list[dict[str, Any]] = []
    for source_col in ["bucket_from_settlement", "bucket_from_atlas_final"]:
        for target_slice, grp in merged.groupby(pd.cut(pd.to_datetime(merged["target_date"]), bins=[pd.Timestamp("1900-01-01"), pd.Timestamp("2026-06-20"), pd.Timestamp("2100-01-01")], labels=["train_pre_2026_06_21", "forward_2026_06_21_plus"]), observed=False):
            counts = grp[source_col].value_counts(dropna=False).to_dict()
            total = int(len(grp))
            summary_rows.append(
                {
                    "source": source_col,
                    "slice": str(target_slice),
                    "rows": total,
                    "cities": int(grp["city"].nunique()),
                    "dates": int(grp["target_date"].nunique()),
                    "below_rows": int(counts.get("below", 0)),
                    "below_rate": float(counts.get("below", 0) / total) if total else math.nan,
                    "current": int(counts.get("current", 0)),
                    "d1": int(counts.get("d1", 0)),
                    "d2": int(counts.get("d2", 0)),
                    "tail": int(counts.get("tail", 0)),
                    "other": int(counts.get("other", 0)),
                    "missing": int(counts.get("missing", 0)),
                }
            )
    summary = pd.DataFrame(summary_rows)
    meta = {
        "atlas_rows": int(len(atlas)),
        "atlas_date_range": [str(atlas["target_date"].min()), str(atlas["target_date"].max())],
        "settlement_winners": int(len(winners)),
        "settlement_date_range": [str(winners["target_date"].min()), str(winners["target_date"].max())],
        "joined_rows": int(merged["settlement_bracket"].notna().sum()),
        "below_rows_settlement": int((merged["bucket_from_settlement"] == "below").sum()),
    }
    return merged, summary, meta


HAZARD_NUMERIC = [
    "decision_hour_local",
    "forecast_peak_delta_hours_local",
    "forecast_peak_delta_abs",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "forecast_to_current_upper_native",
    "forecast_to_d1_upper_native",
    "forecast_to_d2_upper_native",
    "running_to_current_upper_native",
    "current_to_current_upper_native",
    "forecast_minus_running_native",
    "forecast_minus_current_native",
    "running_minus_current_native",
    "wind_speed_kt",
    "relative_humidity_pct",
    "dewpoint_depression_f",
    "minutes_since_running_max",
]

HAZARD_CATEGORICAL = [
    "unit",
    "forecast_source",
    "day_regime",
    "intraday_state",
    "moisture_cloud_regime",
    "wind_regime",
    "running_max_state",
    "solar_window",
    "city_family",
]


def _make_logit(feature_cols_num: list[str], feature_cols_cat: list[str], c: float = 0.5) -> Pipeline:
    try:
        onehot = OneHotEncoder(handle_unknown="ignore", min_frequency=5, sparse_output=True)
    except TypeError:
        onehot = OneHotEncoder(handle_unknown="ignore", sparse=True)
    pre = ColumnTransformer(
        transformers=[
            ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), feature_cols_num),
            ("cat", Pipeline([("impute", SimpleImputer(strategy="most_frequent")), ("onehot", onehot)]), feature_cols_cat),
        ],
        remainder="drop",
    )
    return Pipeline(
        [
            ("pre", pre),
            ("clf", LogisticRegression(C=c, solver="lbfgs", max_iter=1000, class_weight=None)),
        ]
    )


def _fit_binary(train: pd.DataFrame, test: pd.DataFrame, y_col: str, numeric: list[str], categorical: list[str]) -> np.ndarray:
    y = train[y_col].astype(int)
    if y.nunique() < 2:
        return np.full(len(test), float(y.mean()) if len(y) else 0.5)
    model = _make_logit(numeric, categorical)
    model.fit(train[numeric + categorical], y)
    return model.predict_proba(test[numeric + categorical])[:, 1]


def _score_dist_rows(rows: pd.DataFrame, prefix: str, label_col: str = "actual_bucket") -> dict[str, float]:
    probs = rows[[f"{prefix}_p_{b}" for b in p0.BUCKETS]].to_numpy(dtype=float)
    actual = rows[label_col].astype(str).to_list()
    y = np.array([[1.0 if a == b else 0.0 for b in p0.BUCKETS] for a in actual], dtype=float)
    labels = list(range(len(p0.BUCKETS)))
    actual_idx = [p0.BUCKETS.index(a) for a in actual]
    return {
        "logloss": float(log_loss(actual_idx, probs, labels=labels)),
        "brier": float(np.mean(np.sum((probs - y) ** 2, axis=1))),
    }


def build_hazard_chain_scorecard(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    work = df[df["actual_bucket"].isin(p0.BUCKETS)].copy()
    for col in HAZARD_CATEGORICAL:
        if col in work.columns:
            work[col] = work[col].where(work[col].notna(), "unknown").astype(str)
    numeric = [c for c in HAZARD_NUMERIC if c in work.columns]
    categorical = [c for c in HAZARD_CATEGORICAL if c in work.columns]
    dates = sorted(d for d in work["target_date"].dropna().unique() if d >= p1.TRAIN_CUTOFF)
    frames: list[pd.DataFrame] = []
    for date in dates:
        train = work[work["target_date"] < date].copy()
        test = work[work["target_date"] == date].copy()
        if train["target_date"].nunique() < 10 or test.empty:
            continue
        train["h1_escape_current"] = train["actual_bucket"].astype(str).ne("current").astype(int)
        train_h2 = train[train["h1_escape_current"].eq(1)].copy()
        train_h2["h2_escape_d1"] = train_h2["actual_bucket"].astype(str).isin(["d2", "tail"]).astype(int)
        train_h3 = train_h2[train_h2["h2_escape_d1"].eq(1)].copy()
        train_h3["h3_escape_d2"] = train_h3["actual_bucket"].astype(str).eq("tail").astype(int)
        q1 = _fit_binary(train, test, "h1_escape_current", numeric, categorical)
        q2 = _fit_binary(train_h2, test, "h2_escape_d1", numeric, categorical)
        q3 = _fit_binary(train_h3, test, "h3_escape_d2", numeric, categorical)
        pred = test[["city", "target_date", "decision_hour_local", "actual_bucket", "eval_slice"]].copy()
        pred["hazard_p_current"] = 1.0 - q1
        pred["hazard_p_d1"] = q1 * (1.0 - q2)
        pred["hazard_p_d2"] = q1 * q2 * (1.0 - q3)
        pred["hazard_p_tail"] = q1 * q2 * q3
        denom = pred[[f"hazard_p_{b}" for b in p0.BUCKETS]].sum(axis=1)
        for bucket in p0.BUCKETS:
            pred[f"hazard_p_{bucket}"] = pred[f"hazard_p_{bucket}"] / denom
        market_cols = [f"market_p_{b}" for b in p0.BUCKETS]
        for col in market_cols:
            if col in test.columns:
                pred[col] = pd.to_numeric(test[col], errors="coerce").to_numpy()
        frames.append(pred)
    pred_all = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if pred_all.empty:
        return pred_all, pd.DataFrame()
    pred_all["scope"] = np.where(pred_all["eval_slice"].eq(p4.VERIFIED_SLICE), "verified_forward", "extension_forward")
    rows: list[dict[str, Any]] = []
    for scope, grp in pred_all.groupby("scope", dropna=False):
        if grp.empty:
            continue
        market = _score_dist_rows(grp.rename(columns={f"market_p_{b}": f"market_p_{b}" for b in p0.BUCKETS}), "market")
        for alpha in ALPHA_GRID:
            tmp = grp.copy()
            for bucket in p0.BUCKETS:
                tmp[f"alpha_p_{bucket}"] = (
                    (1.0 - alpha) * pd.to_numeric(tmp[f"market_p_{bucket}"], errors="coerce")
                    + alpha * pd.to_numeric(tmp[f"hazard_p_{bucket}"], errors="coerce")
                )
            denom = tmp[[f"alpha_p_{b}" for b in p0.BUCKETS]].sum(axis=1)
            for bucket in p0.BUCKETS:
                tmp[f"alpha_p_{bucket}"] = tmp[f"alpha_p_{bucket}"] / denom
            score = _score_dist_rows(tmp, "alpha")
            rows.append(
                {
                    "scope": scope,
                    "alpha": alpha,
                    "rows": int(len(tmp)),
                    "dates": int(tmp["target_date"].nunique()),
                    "logloss": score["logloss"],
                    "market_logloss": market["logloss"],
                    "logloss_delta_vs_market": score["logloss"] - market["logloss"],
                    "brier": score["brier"],
                    "market_brier": market["brier"],
                    "brier_delta_vs_market": score["brier"] - market["brier"],
                }
            )
    summary = pd.DataFrame(rows).sort_values(["scope", "logloss"])
    return pred_all, summary


def _write_report(
    *,
    coherence_summary: pd.DataFrame,
    coherence_meta: dict[str, Any],
    e2_summary: pd.DataFrame,
    e2_meta: dict[str, Any],
    hazard_summary: pd.DataFrame,
    meta: dict[str, Any],
    report: dict[str, Any],
) -> None:
    coh_focus = coherence_summary[coherence_summary["grouping"].isin(["scope+reanchor_group", "reanchor_group"])].copy()
    lines = [
        "# Tmax Cross-Hour Coherence Audit v2",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        "> Scope: research-only. No tmax live runner/config/order behavior changed.",
        "",
        "## 结论 / 交易动作",
        "",
        "- Fable 的核心修正成立：P0e v1 的 `no_reanchor +7pp` 主要是 coherent baseline 没有吃进“又过一小时仍未突破”的 survival 信息，不是一个可交易的 no-reanchor debt。",
        "- 修正后 `no_reanchor` verified 残差从约 `+7.0pp` 收敛到接近 `0pp`；`d1_reanchor` 仍接近 0 或略负，`d2_reanchor` 负。结论：不要做 `reanchor penalty`，Lucknow 是尾部个案，不是系统性 reanchor 规则。",
        "- E2 below materializer 这轮用官方 `settlement_outcomes` 重新 join，仍得到 `below_rows=0`。这不等于 below 风险不存在，而是说明当前 materialized current/running-max 分母没有覆盖 Fable 所说的 below 负例；上一版 `current_yes verified +25.8%` 必须标为 `censored-inflated`，不能作为 live/current-YES 依据。",
        "- Hazard-chain 原型已跑：它是正确方向的模型形态，但在当前四桶、below 未补齐的分母上只能算 shadow prototype；是否继续投入看它是否稳定压过 market 分布评分。",
        "- 交易动作：`tmax live` 继续停；`current_yes` 继续 shadow；下一步不是加 gate，而是把真实 five-bucket/below 目标和 target-book ledger 做成可重放账本。",
        "",
        "## 数据与冻结口径",
        "",
        f"- Materialized rows: `{meta['rows']}` rows, `{meta['date_range'][0]}`..`{meta['date_range'][1]}`, `{meta['cities']}` cities.",
        f"- P0e v2 survival update: `{coherence_meta['survival_update']}` with `q={coherence_meta['q_noncurrent_no_break']:.4f}` from dev_cv `{coherence_meta['q_num']}/{coherence_meta['q_den']}` non-current paths that still had no next-bracket break by the next adjacent hour.",
        f"- E2 official settlement join: `{e2_meta}`",
        "",
        "## P0e v2 Coherence",
        "",
        *_table(
            coh_focus,
            [
                "grouping",
                "scope",
                "reanchor_group",
                "rows",
                "dates",
                "cities",
                "v1_incoherence_mean",
                "v2_incoherence_mean",
                "v2_minus_v1_mean",
                "positive_rate_v1",
                "positive_rate_v2",
            ],
            max_rows=80,
        ),
        "",
        "## E2 Below-Bucket Audit",
        "",
        "Interpretation: `below_rows=0` after official settlement join means the present materialized layer cannot answer E2. It also means any four-bucket current-YES ROI remains censored because the target space itself has not been rebuilt to emit below/current/d1/d2/tail.",
        "",
        *_table(
            e2_summary,
            [
                "source",
                "slice",
                "rows",
                "cities",
                "dates",
                "below_rows",
                "below_rate",
                "current",
                "d1",
                "d2",
                "tail",
                "other",
                "missing",
            ],
            max_rows=20,
        ),
        "",
        "## P1 Hazard-Chain Prototype",
        "",
        "This prototype models `P(escape current)`, then `P(escape d1 | escaped current)`, then `P(tail | escaped d2)` with physical/context features and no per-city free parameter. It is a prototype on the current four-bucket layer, not a live-ready model.",
        "",
        *_table(
            hazard_summary,
            [
                "scope",
                "alpha",
                "rows",
                "dates",
                "logloss",
                "market_logloss",
                "logloss_delta_vs_market",
                "brier",
                "market_brier",
                "brier_delta_vs_market",
            ],
            max_rows=20,
        ),
        "",
        "## 三道门",
        "",
        "- significance: `PARTIAL` for P0e v2 correction; `FAIL/UNANSWERED` for E2 below because current layer still has zero below rows.",
        "- baseline: `PARTIAL` for hazard-chain prototype only if it beats market scoring on verified_forward; otherwise `FAIL`.",
        "- forward: `FAIL` for live promotion. No fresh forward target-book ledger yet, no below target, and no position-aware execution replay attached to real holdings.",
        "",
        "Verdict: `shadow_candidate_model_rebuild_not_live`.",
        "",
        "## Artifacts",
        "",
        f"- `{(OUT_DIR / 'coherence_pairs_v2.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'coherence_summary_v2.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'e2_below_materializer_rows.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'e2_below_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'hazard_chain_predictions.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'hazard_chain_scorecard.csv').relative_to(ROOT)}`",
        f"- `{SUMMARY_JSON_PATH.relative_to(ROOT)}`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pairs_v1 = _coherence_pairs_v1()
    pairs_v2, coherence_summary, coherence_meta = build_coherence_v2(pairs_v1)
    e2_rows, e2_summary, e2_meta = build_e2_below_audit()
    df, pred_all, _, _, meta = v1._prediction_frames()
    hazard_pred, hazard_summary = build_hazard_chain_scorecard(df)

    pairs_v2.to_csv(OUT_DIR / "coherence_pairs_v2.csv", index=False)
    coherence_summary.to_csv(OUT_DIR / "coherence_summary_v2.csv", index=False)
    e2_rows.to_csv(OUT_DIR / "e2_below_materializer_rows.csv", index=False)
    e2_summary.to_csv(OUT_DIR / "e2_below_summary.csv", index=False)
    hazard_pred.to_csv(OUT_DIR / "hazard_chain_predictions.csv", index=False)
    hazard_summary.to_csv(OUT_DIR / "hazard_chain_scorecard.csv", index=False)

    report = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "method": METHOD,
        "model_spec": MODEL_SPEC,
        "verdict": "shadow_candidate_model_rebuild_not_live",
        "meta": meta,
        "coherence_meta": coherence_meta,
        "e2_meta": e2_meta,
        "coherence_summary": coherence_summary.to_dict("records"),
        "e2_summary": e2_summary.to_dict("records"),
        "hazard_summary": hazard_summary.to_dict("records"),
    }
    SUMMARY_JSON_PATH.write_text(json.dumps(_json_ready(report), indent=2, sort_keys=True), encoding="utf-8")
    _write_report(
        coherence_summary=coherence_summary,
        coherence_meta=coherence_meta,
        e2_summary=e2_summary,
        e2_meta=e2_meta,
        hazard_summary=hazard_summary,
        meta=meta,
        report=report,
    )
    print(
        json.dumps(
            {
                "report_path": str(REPORT_PATH.relative_to(ROOT)),
                "date_range": meta["date_range"],
                "rows": meta["rows"],
                "coherence_pairs": int(len(pairs_v2)),
                "e2_below_rows": e2_meta["below_rows_settlement"],
                "hazard_rows": int(len(hazard_pred)),
                "verdict": report["verdict"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
