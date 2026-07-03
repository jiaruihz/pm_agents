#!/usr/bin/env python3
"""P4 observed-max label extension for the intraday Tmax distribution model.

This is a research-only extension. It does not write canonical settlements and
does not change live behavior.

P0-P3 only score rows with `final_winning_bracket` from settlement_outcomes.
For dates without settlement_outcomes, the atlas may still have observed final
max. This script derives a local current/d1/d2/tail label from
`final_max_native` and the local bracket intervals, then evaluates the same P3
feature stacks on:

  - verified settlement labels: any forward row backed by settlement_outcomes
  - observed-max-derived labels: forward rows still missing official settlement

Derived labels are for model validation only, not canonical PnL.
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


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_tmax_distribution_p1_fusion_scorecard_v1 as p1  # noqa: E402
import research_tmax_distribution_p2_ev_shadow_v1 as p2  # noqa: E402
import research_tmax_distribution_p3_feature_ablation_v1 as p3  # noqa: E402
from research_tmax_distribution_p0_anchor_scorecard_v1 import (  # noqa: E402
    ATLAS_PATH,
    BUCKETS,
    EPS,
    _as_float,
    _entropy_norm,
    _hour_bucket,
    _interval,
    _label,
    _market_distribution,
    _normalize,
    _score_distribution,
    _soft_anchor_distribution,
)


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p4_observed_label_extension_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-03-tmax-distribution-p4-observed-label-extension-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-03-tmax-distribution-p4-observed-label-extension-v1.json"
DB_PATH = ROOT / "runtime/weather.db"
EDGE_THRESHOLD = 0.02

METHODS = [
    "market_local_norm",
    "market_recal_blend",
    "mkt_regime_blend",
    "mkt_city_source_blend",
    "loo_no_city_source_blend",
    "loo_no_regime_blend",
]

VERIFIED_SLICE = "verified_forward_settlement"
EXTENSION_SLICE = "extension_forward_observed_max"


def _derive_bucket(row: pd.Series) -> tuple[str | None, str]:
    actual = _label(row)
    if actual is not None:
        return actual, "settlement_outcomes"
    final_max = _as_float(row.get("final_max_native"))
    cur_iv = _interval(row.get("current_bracket"))
    d1_iv = _interval(row.get("d1_no_bracket"))
    d2_iv = _interval(row.get("d2_no_bracket"))
    if final_max is None or cur_iv is None or d1_iv is None or d2_iv is None:
        return None, "missing"
    if len({str(row.get("current_bracket")), str(row.get("d1_no_bracket")), str(row.get("d2_no_bracket"))}) != 3:
        return None, "invalid_grid"
    if final_max < cur_iv[0] - 1e-6:
        return None, "final_below_current"
    if cur_iv[0] - 1e-6 <= final_max <= cur_iv[1] + 1e-6:
        return "current", "observed_max_derived"
    if d1_iv[0] - 1e-6 <= final_max <= d1_iv[1] + 1e-6:
        return "d1", "observed_max_derived"
    if d2_iv[0] - 1e-6 <= final_max <= d2_iv[1] + 1e-6:
        return "d2", "observed_max_derived"
    if final_max > d2_iv[1] + 1e-6:
        return "tail", "observed_max_derived"
    return None, "unmapped"


def _safe_upper(interval: tuple[float, float] | None) -> float | None:
    if interval is None:
        return None
    hi = interval[1]
    if math.isinf(hi):
        return None
    return hi


def _safe_mid(interval: tuple[float, float] | None) -> float | None:
    if interval is None:
        return None
    lo, hi = interval
    if math.isinf(lo) or math.isinf(hi):
        return None
    return (lo + hi) / 2.0


def _delta(a: object, b: object) -> float | None:
    av, bv = _as_float(a), _as_float(b)
    if av is None or bv is None:
        return None
    return av - bv


def _load_rows_extended() -> tuple[pd.DataFrame, dict[str, Any]]:
    needed = set(
        [
            "city",
            "target_date",
            "decision_hour_local",
            "decision_snapshot_ts_utc",
            "unit",
            "current_bracket",
            "d1_no_bracket",
            "d2_no_bracket",
            "final_winning_bracket",
            "current_yes_ask",
            "current_bracket_no_ask",
            "current_no_bid",
            "d1_no_ask",
            "d1_no_bid",
            "d2_no_ask",
            "d2_no_bid",
            "forecast_max_native",
            "forecast_max_f",
            "running_native",
            "current_native",
            "decline_native",
            "tmpf_now",
            "dwpf_now",
            "dewpoint_depression_f",
            "relative_humidity_pct",
            "wind_speed_kt",
            "sky_cover_code",
            "temp_trend_1h_f",
            "temp_trend_3h_f",
            "minutes_since_running_max",
            "forecast_peak_hour_local",
            "forecast_peak_delta_hours_local",
            "forecast_peak_hour_spread",
            "forecast_gap_to_running_native",
            "gfs_gap_to_running_native",
            "ecmwf_gap_to_running_native",
            "forecast_source",
            "day_regime",
            "intraday_state",
            "moisture_cloud_regime",
            "wind_regime",
            "running_max_state",
            "solar_window",
            "city_family",
            "final_max_native",
        ]
    )
    raw = pd.read_csv(ATLAS_PATH, usecols=lambda c: c in needed)
    counters: dict[str, Any] = {"raw_rows": int(len(raw))}
    skipped = {
        "missing_or_invalid_label": 0,
        "missing_interval": 0,
        "missing_market_quote": 0,
        "observed_derived_before_extension_window": 0,
    }
    rows: list[dict[str, Any]] = []
    label_sources: dict[str, int] = {}
    for item in raw.to_dict("records"):
        s = pd.Series(item)
        actual, label_source = _derive_bucket(s)
        label_sources[label_source] = label_sources.get(label_source, 0) + 1
        if actual is None:
            skipped["missing_or_invalid_label"] += 1
            continue
        target_date = str(item.get("target_date"))
        if label_source == "observed_max_derived" and target_date < "2026-06-27":
            skipped["observed_derived_before_extension_window"] += 1
            continue
        current_iv = _interval(s.get("current_bracket"))
        d1_iv = _interval(s.get("d1_no_bracket"))
        d2_iv = _interval(s.get("d2_no_bracket"))
        if current_iv is None or d1_iv is None or d2_iv is None:
            skipped["missing_interval"] += 1
            continue
        market = _market_distribution(s)
        if market is None:
            skipped["missing_market_quote"] += 1
            continue
        forecast_anchor = _soft_anchor_distribution(_as_float(s.get("forecast_max_native")), current_iv, d1_iv, d2_iv)
        running_anchor = _soft_anchor_distribution(_as_float(s.get("running_native")), current_iv, d1_iv, d2_iv)
        row = dict(item)
        row["actual_bucket"] = actual
        row["label_source"] = label_source
        row["eval_slice"] = (
            "train_pre_2026_06_21"
            if target_date < p1.TRAIN_CUTOFF
            else (
                VERIFIED_SLICE
                if label_source == "settlement_outcomes"
                else EXTENSION_SLICE
            )
        )
        row["split"] = "train_pre_2026_06_21" if target_date < p1.TRAIN_CUTOFF else "forward_2026_06_21_plus"
        row["hour_bucket"] = _hour_bucket(item.get("decision_hour_local"))
        hour = _as_float(item.get("decision_hour_local"))
        if hour is not None:
            row["decision_hour_sin"] = math.sin(2.0 * math.pi * hour / 24.0)
            row["decision_hour_cos"] = math.cos(2.0 * math.pi * hour / 24.0)
        row["forecast_peak_delta_abs"] = (
            abs(_as_float(item.get("forecast_peak_delta_hours_local")))
            if _as_float(item.get("forecast_peak_delta_hours_local")) is not None
            else None
        )
        current_upper = _safe_upper(current_iv)
        d1_upper = _safe_upper(d1_iv)
        d2_upper = _safe_upper(d2_iv)
        current_mid = _safe_mid(current_iv)
        d1_mid = _safe_mid(d1_iv)
        d2_mid = _safe_mid(d2_iv)
        row["forecast_minus_running_native"] = _delta(item.get("forecast_max_native"), item.get("running_native"))
        row["forecast_minus_current_native"] = _delta(item.get("forecast_max_native"), item.get("current_native"))
        row["running_minus_current_native"] = _delta(item.get("running_native"), item.get("current_native"))
        row["forecast_to_current_upper_native"] = _delta(item.get("forecast_max_native"), current_upper)
        row["forecast_to_d1_upper_native"] = _delta(item.get("forecast_max_native"), d1_upper)
        row["forecast_to_d2_upper_native"] = _delta(item.get("forecast_max_native"), d2_upper)
        row["forecast_to_current_mid_native"] = _delta(item.get("forecast_max_native"), current_mid)
        row["forecast_to_d1_mid_native"] = _delta(item.get("forecast_max_native"), d1_mid)
        row["forecast_to_d2_mid_native"] = _delta(item.get("forecast_max_native"), d2_mid)
        row["running_to_current_upper_native"] = _delta(item.get("running_native"), current_upper)
        row["current_to_current_upper_native"] = _delta(item.get("current_native"), current_upper)
        if current_mid is not None:
            row["running_position_in_current_native"] = _delta(item.get("running_native"), current_mid)
            row["current_position_in_current_native"] = _delta(item.get("current_native"), current_mid)
        for bucket in BUCKETS:
            row[f"market_p_{bucket}"] = market[bucket]
            row[f"market_log_p_{bucket}"] = math.log(max(EPS, market[bucket]))
            row[f"forecast_anchor_p_{bucket}"] = forecast_anchor[bucket]
            row[f"runningmax_anchor_p_{bucket}"] = running_anchor[bucket]
        row["market_entropy"] = _entropy_norm(market)
        probs_sorted = sorted(market.values(), reverse=True)
        row["market_top_p"] = probs_sorted[0]
        row["market_top2_gap"] = probs_sorted[0] - probs_sorted[1]
        rows.append(row)
    counters.update(skipped)
    counters["scored_rows"] = int(len(rows))
    counters["label_sources_raw"] = label_sources
    df = pd.DataFrame(rows)
    for col in sorted(set(p1.CONTEXT_CATEGORICAL + p1.CITY_CATEGORICAL)):
        if col in df.columns:
            df[col] = df[col].where(df[col].notna(), "unknown").astype(str)
    return p3._add_boundary_features(df), counters


def _score_scope(scores: pd.DataFrame, scope_name: str) -> pd.DataFrame:
    rows = []
    for method, grp in scores.groupby("method"):
        rows.append(
            {
                "scope": scope_name,
                "method": method,
                "n": int(len(grp)),
                "dates": int(grp["target_date"].nunique()),
                "cities": int(grp["city"].nunique()),
                "logloss": float(grp["logloss"].mean()),
                "brier": float(grp["brier"].mean()),
                "top1": float(grp["top1"].mean()),
                "winner_prob": float(grp["winner_prob"].mean()),
            }
        )
    return pd.DataFrame(rows).sort_values(["scope", "logloss"]).reset_index(drop=True)


def _add_delta(summary: pd.DataFrame, scores: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in summary.to_dict("records"):
        if r["method"] == "market_local_norm":
            r["logloss_delta_vs_market"] = 0.0
        else:
            pivot = scores[scores["method"].isin(["market_local_norm", r["method"]])].pivot_table(
                index=["city", "target_date", "decision_hour_local", "actual_bucket"],
                columns="method",
                values="logloss",
                aggfunc="first",
            )
            r["logloss_delta_vs_market"] = float((pivot[r["method"]] - pivot["market_local_norm"]).mean())
        rows.append(r)
    return pd.DataFrame(rows)


def _build_ev(base: pd.DataFrame, preds: pd.DataFrame, scope: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    old_methods = list(p2.METHODS)
    try:
        p2.METHODS = METHODS
        opps = p2._build_opportunities(scope, base, preds)
    finally:
        p2.METHODS = old_methods
    deduped = p2._best_expression_per_state(opps, EDGE_THRESHOLD)
    return opps, p2._summarize_deduped(deduped, EDGE_THRESHOLD)


def _inventory() -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        one = lambda sql: dict(conn.execute(sql).fetchone())
        return {
            "fact_signal_candidates": one(
                "SELECT COUNT(*) AS rows, MIN(event_date) AS min_date, MAX(event_date) AS max_date, MAX(fact_built_at_utc) AS max_built_at_utc FROM fact_signal_candidates"
            ),
            "fact_trades": one(
                "SELECT COUNT(*) AS rows, MIN(target_date) AS min_date, MAX(target_date) AS max_date, MAX(fact_built_at_utc) AS max_built_at_utc FROM fact_trades"
            ),
            "settlement_outcomes": one(
                "SELECT COUNT(*) AS rows, MIN(target_date) AS min_date, MAX(target_date) AS max_date, COUNT(DISTINCT city) AS cities FROM settlement_outcomes"
            ),
        }
    finally:
        conn.close()


def _table(df: pd.DataFrame, cols: list[str]) -> list[str]:
    if df.empty:
        return ["_No rows._"]
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in df.to_dict("records"):
        vals = []
        for c in cols:
            v = row.get(c)
            if isinstance(v, float):
                if math.isnan(v):
                    vals.append("n/a")
                    continue
                if c in {"top1"}:
                    vals.append(f"{v:.1%}")
                elif "delta" in c:
                    vals.append(f"{v:+.4f}")
                elif c in {"roi", "roi_ci_low", "roi_ci_high"}:
                    vals.append(f"{v:+.1%}")
                else:
                    vals.append(f"{v:.4f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def _ev_table(df: pd.DataFrame) -> list[str]:
    sub = df[(df["scope"].isin(["verified_forward", "extension_forward"])) & df["method"].isin(METHODS)].copy()
    if sub.empty:
        return ["_No EV rows._"]
    sub = sub.sort_values(["scope", "roi"], ascending=[True, False])
    rows = []
    for r in sub.to_dict("records"):
        rows.append(
            {
                "scope": r["scope"],
                "method": r["method"],
                "rows": int(r["selected_rows"]),
                "dates": int(r["dates"]),
                "cost": float(r["cost"]),
                "pnl": float(r["pnl"]),
                "roi": float(r["roi"]),
                "roi_ci_low": float(r["roi_ci_low"]),
                "roi_ci_high": float(r["roi_ci_high"]),
                "mix": f"YES {int(r['current_yes_rows'])} / curNO {int(r['current_no_rows'])} / d1NO {int(r['d1_no_rows'])} / d2NO {int(r['d2_no_rows'])}",
            }
        )
    return _table(pd.DataFrame(rows), ["scope", "method", "rows", "dates", "cost", "pnl", "roi", "roi_ci_low", "roi_ci_high", "mix"])


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df, counters = _load_rows_extended()
    specs = p3._feature_specs(df)
    p1.MODEL_SPECS = specs
    train = df[df["target_date"] < p1.TRAIN_CUTOFF].copy()
    selections = {name: p1._select_model(train, name) for name in specs}
    selection_df = p1._selection_table(selections)

    _fixed_scores, fixed_preds = p1._fixed_forward_predictions(df, selections)
    expanding_scores, expanding_preds = p1._expanding_forward_predictions(df, selections)
    expanding_preds = expanding_preds.merge(
        df[["city", "target_date", "decision_hour_local", "actual_bucket", "label_source", "eval_slice"]],
        on=["city", "target_date", "decision_hour_local", "actual_bucket"],
        how="left",
        validate="one_to_one",
    )
    scored_with_slice = expanding_scores.merge(
        df[["city", "target_date", "decision_hour_local", "actual_bucket", "label_source", "eval_slice"]],
        on=["city", "target_date", "decision_hour_local", "actual_bucket"],
        how="left",
        validate="many_to_one",
    )
    verified_scores = scored_with_slice[scored_with_slice["eval_slice"].eq(VERIFIED_SLICE)].copy()
    observed_scores = scored_with_slice[scored_with_slice["eval_slice"].eq(EXTENSION_SLICE)].copy()
    verified_summary = _add_delta(_score_scope(verified_scores, "verified_forward"), verified_scores)
    observed_summary = _add_delta(_score_scope(observed_scores, "extension_forward"), observed_scores)
    summary = pd.concat([verified_summary, observed_summary], ignore_index=True)

    base = p2._load_base()
    # Add derived-label state rows that P2 base excludes due missing final_winner.
    needed_base_cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "actual_bucket",
        "current_yes_ask",
        "current_bracket_no_ask",
        "d1_no_ask",
        "d2_no_ask",
        "day_regime",
        "intraday_state",
        "forecast_source",
        "city_family",
    ]
    ext_base = df[df["eval_slice"].eq(EXTENSION_SLICE)][needed_base_cols].drop_duplicates(
        ["city", "target_date", "decision_hour_local", "actual_bucket"]
    )
    for col in ["market_raw_local_mass", "market_tail_residual_raw", "market_local_overround_raw", "market_local_undermass_raw"]:
        ext_base[col] = np.nan
    for col in ["current_no_ask_size", "d1_no_ask_size", "d2_no_ask_size"]:
        ext_base[col] = np.nan
    base_ext = pd.concat([base, ext_base], ignore_index=True).drop_duplicates(
        ["city", "target_date", "decision_hour_local", "actual_bucket"], keep="first"
    )

    ev_summaries = []
    ev_opps = []
    for scope, pred in [
        ("verified_forward", expanding_preds[expanding_preds["eval_slice"].eq(VERIFIED_SLICE)]),
        ("extension_forward", expanding_preds[expanding_preds["eval_slice"].eq(EXTENSION_SLICE)]),
    ]:
        opps, ev_sum = _build_ev(base_ext, pred.drop(columns=["label_source", "eval_slice"]), scope)
        ev_opps.append(opps)
        ev_summaries.append(ev_sum)
    ev_opps_df = pd.concat(ev_opps, ignore_index=True)
    ev_summary = pd.concat(ev_summaries, ignore_index=True)

    selection_df.to_csv(OUT_DIR / "model_selection.csv", index=False)
    summary.to_csv(OUT_DIR / "score_summary.csv", index=False)
    scored_with_slice.to_csv(OUT_DIR / "scored_rows.csv", index=False)
    expanding_preds.to_csv(OUT_DIR / "expanding_forward_predictions.csv", index=False)
    ev_opps_df.to_csv(OUT_DIR / "ev_opportunities.csv", index=False)
    ev_summary.to_csv(OUT_DIR / "ev_deduped_summary.csv", index=False)

    inv = _inventory()
    report = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "atlas_path": str(ATLAS_PATH.relative_to(ROOT)),
        "date_range": [str(df["target_date"].min()), str(df["target_date"].max())],
        "counters": counters,
        "inventory": inv,
        "score_summary": summary.to_dict("records"),
        "ev_deduped_summary": ev_summary.to_dict("records"),
        "verdict": "research_observed_label_extension_only",
    }
    SUMMARY_JSON_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    focus_methods = ["market_local_norm", "mkt_regime_blend", "loo_no_city_source_blend", "mkt_city_source_blend", "loo_no_regime_blend"]
    score_focus = summary[summary["method"].isin(focus_methods)].sort_values(["scope", "logloss"])
    lines = [
        "# Tmax Distribution P4 Observed-Label Extension v1",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> atlas: `{ATLAS_PATH.relative_to(ROOT)}`",
        "> Scope: research-only observed-max label extension; no canonical settlement/live behavior changed.",
        "",
        "## 结论",
        "",
        "- 本轮补的是效果验证，不是 live 改动：用 `final_max_native` 推导 6/27+ 的 local bucket label，明确标记为 `observed_max_derived`。",
        f"- 可评分行扩到 `{df['target_date'].min()}`..`{df['target_date'].max()}`，其中 raw label sources: `{counters['label_sources_raw']}`。",
        "- 在 6/21-6/26 verified settlement 上，P3 机制版继续优于 market。",
        "- 在 6/27-6/29 extension 上，结果只能看方向和压力，不能当正式 PnL。",
        "",
        "## Proper Scoring",
        "",
        *_table(score_focus[["scope", "method", "n", "dates", "cities", "logloss", "logloss_delta_vs_market", "brier", "top1", "winner_prob"]], ["scope", "method", "n", "dates", "cities", "logloss", "logloss_delta_vs_market", "brier", "top1", "winner_prob"]),
        "",
        "## EV Shadow, Edge >= 0.02, One Expression Per State",
        "",
        *_ev_table(ev_summary[ev_summary["method"].isin(focus_methods)]),
        "",
        "## Data Boundary",
        "",
        f"- `fact_signal_candidates`: `{inv['fact_signal_candidates']}`",
        f"- `fact_trades`: `{inv['fact_trades']}`",
        f"- `settlement_outcomes`: `{inv['settlement_outcomes']}`",
        "",
        "6/27+ 的正式 settlement 仍未完整进入 `settlement_outcomes`。本报告的 observed-derived 部分只回答“如果 observed max 口径成立，模型新日期表现如何”，不替代结算。",
        "",
        "## Verdict",
        "",
        "conclusion=`research_observed_label_extension_only`; no live action.",
        "",
        "## Artifacts",
        "",
        f"- `{(OUT_DIR / 'score_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'ev_deduped_summary.csv').relative_to(ROOT)}`",
        f"- `{SUMMARY_JSON_PATH.relative_to(ROOT)}`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"report_path": str(REPORT_PATH.relative_to(ROOT)), "date_range": report["date_range"], "rows": int(len(df)), "verdict": report["verdict"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
