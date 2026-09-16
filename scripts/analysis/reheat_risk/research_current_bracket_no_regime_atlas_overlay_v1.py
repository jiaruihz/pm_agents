#!/usr/bin/env python3
"""Overlay intraday weather regime atlas onto current-bracket NO.

This answers two separate questions:

1. Why the original afternoon-peak classifier label can disagree with the
   current-bracket NO payoff label.
2. Whether regime-atlas context improves the current base-p40 expression when
   used as a shared feature layer / soft sizing input, not as a hard gate.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import research_current_bracket_no_capped_day_regime_v1 as cap  # noqa: E402
import research_current_bracket_no_forward_regime_stress_v1 as stress  # noqa: E402
import research_current_bracket_no_pass_through_v1 as pass_through  # noqa: E402
import research_current_bracket_no_regime_aware_policy_v1 as old_regime  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_regime_atlas_overlay_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_MISMATCH = OUT_DIR / "afternoon_label_payoff_mismatch.csv"
OUT_REGIME_SLICES = OUT_DIR / "base_p40_regime_slice_performance.csv"
OUT_BLOCKS = OUT_DIR / "atlas_soft_sizing_rolling_blocks.csv"
OUT_POLICY = OUT_DIR / "atlas_soft_sizing_policy_summary.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-25-current-bracket-no-regime-atlas-overlay-v1.md"

ATLAS = ROOT / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/intraday_weather_regime_state_rows.csv"
CLASSIFIER_SCORED = (
    ROOT / "docs/analysis/2026-06/generated/current_bracket_no_afternoon_peak_classifier_v1/scored_current_no_rows.csv"
)

STAKE_USD = 5.0
SEED = 20260625
MIN_MULT = 0.25
SHRINK_N = 30.0

ATLAS_COLS = [
    "city",
    "target_date",
    "decision_hour_local",
    "day_regime",
    "intraday_state",
    "moisture_cloud_regime",
    "wind_regime",
    "running_max_state",
    "solar_window",
    "city_family",
    "composite_regime",
]

PHYSICAL_DAY_MULTIPLIER = {
    "day_open_runway": 1.00,
    "day_marginal_runway": 0.80,
    "day_forecast_capped": 0.55,
    "day_forecast_busted": 0.45,
    "day_space_unknown": 0.65,
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite_or_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: finite_or_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite_or_none(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    return value


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def money(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"${val:+,.2f}"


def roi(frame: pd.DataFrame, profit_col: str = "stake_profit_usd", cost_col: str = "stake_cost_usd") -> float | None:
    if frame.empty:
        return None
    cost = float(pd.to_numeric(frame[cost_col], errors="coerce").sum())
    if cost <= 0:
        return None
    profit = float(pd.to_numeric(frame[profit_col], errors="coerce").sum())
    return profit / cost


def load_atlas() -> pd.DataFrame:
    atlas = pd.read_csv(ATLAS, usecols=ATLAS_COLS, low_memory=False)
    atlas["target_date"] = atlas["target_date"].astype(str)
    atlas["decision_hour_local"] = pd.to_numeric(atlas["decision_hour_local"], errors="coerce").astype("Int64")
    return atlas.drop_duplicates(["city", "target_date", "decision_hour_local"], keep="last")


def attach_atlas(frame: pd.DataFrame, atlas: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["target_date"] = out["target_date"].astype(str)
    out["decision_hour_local"] = pd.to_numeric(out["decision_hour_local"], errors="coerce").astype("Int64")
    out = out.merge(atlas, on=["city", "target_date", "decision_hour_local"], how="left", suffixes=("", "_atlas"))
    for col in ATLAS_COLS:
        if col in {"city", "target_date", "decision_hour_local"}:
            continue
        out[col] = out[col].fillna("atlas_missing")
    return out


def select_classifier_v1_rows(enriched: pd.DataFrame) -> pd.DataFrame:
    if not CLASSIFIER_SCORED.exists():
        return pd.DataFrame()
    scored = pd.read_csv(CLASSIFIER_SCORED, low_memory=False)
    scored["target_date"] = scored["target_date"].astype(str)
    scored["decision_hour_local"] = pd.to_numeric(scored["decision_hour_local"], errors="coerce")
    scored["no_ask"] = pd.to_numeric(scored["no_ask"], errors="coerce")
    raw = scored[scored["trade_base"].astype(bool) & scored["logit_c0p2_p"].ge(0.50)].copy()
    if raw.empty:
        return raw
    selected_keys = (
        raw.sort_values(["target_date", "city", "decision_hour_local", "no_ask"])
        .drop_duplicates(["target_date", "city"], keep="first")
        [["target_date", "city", "decision_hour_local", "bracket", "no_ask", "logit_c0p2_p"]]
        .copy()
    )
    e = enriched.copy()
    e["target_date"] = e["target_date"].astype(str)
    e["decision_hour_local"] = pd.to_numeric(e["decision_hour_local"], errors="coerce")
    e["no_ask_round"] = pd.to_numeric(e["no_ask"], errors="coerce").round(6)
    selected_keys["no_ask_round"] = pd.to_numeric(selected_keys["no_ask"], errors="coerce").round(6)
    joined = e.merge(
        selected_keys.drop(columns=["no_ask"]),
        on=["target_date", "city", "decision_hour_local", "bracket", "no_ask_round"],
        how="inner",
    )
    return joined.drop_duplicates(["target_date", "city"], keep="first")


def mismatch_summary() -> tuple[pd.DataFrame, dict[str, Any]]:
    raw = pass_through.load_feature_rows()
    enriched = pass_through.enrich_current_no(raw)
    enriched = enriched[enriched["midday_h10_14"] & enriched["actual_peak_afternoon"].notna()].copy()
    enriched["future_max_above_running"] = pd.to_numeric(enriched["final_max_native"], errors="coerce") > (
        pd.to_numeric(enriched["running_native"], errors="coerce") + 0.05
    )
    enriched["peak_already_started_by_decision"] = pd.to_numeric(
        enriched["actual_peak_first_hour_local"], errors="coerce"
    ) <= pd.to_numeric(enriched["decision_hour_local"], errors="coerce")
    enriched["final_did_not_cross_bracket_upper"] = ~enriched["final_max_above_current_upper"].fillna(False)
    enriched["afternoon_label_fp_vs_no"] = enriched["actual_peak_afternoon"].astype(bool) & enriched["label_no_wins"].eq(0)

    scored = select_classifier_v1_rows(enriched)
    cohorts = {
        "all_h10_14_current_no_states": enriched,
        "trade_base_ask10_35_depth5": enriched[
            enriched["no_ask"].between(0.10, 0.35) & (enriched["no_depth_5c_notional_approx"].ge(STAKE_USD))
        ].copy(),
        "classifier_v1_selected_logit_c0p2_ge_0p50": scored,
    }
    rows: list[dict[str, Any]] = []
    for name, frame in cohorts.items():
        if frame.empty:
            rows.append({"cohort": name, "rows": 0})
            continue
        fp = frame[frame["afternoon_label_fp_vs_no"]].copy()
        rows.append(
            {
                "cohort": name,
                "rows": int(len(frame)),
                "active_dates": int(frame["target_date"].nunique()),
                "cities": int(frame["city"].nunique()),
                "actual_peak_afternoon_rate": float(frame["actual_peak_afternoon"].mean()),
                "future_max_above_running_rate": float(frame["future_max_above_running"].mean()),
                "no_win_rate": float(frame["label_no_wins"].mean()),
                "roi": roi(frame),
                "afternoon_yes_no_loss_rows": int(len(fp)),
                "afternoon_yes_no_loss_share_of_afternoon": float(
                    len(fp) / max(1, int(frame["actual_peak_afternoon"].sum()))
                ),
                "fp_peak_already_started_share": float(fp["peak_already_started_by_decision"].mean()) if len(fp) else None,
                "fp_final_did_not_cross_upper_share": float(fp["final_did_not_cross_bracket_upper"].mean()) if len(fp) else None,
                "fp_future_max_above_running_share": float(fp["future_max_above_running"].mean()) if len(fp) else None,
                "actual_peak_and_no_win": int((frame["actual_peak_afternoon"].astype(bool) & frame["label_no_wins"].eq(1)).sum()),
                "actual_peak_and_no_loss": int((frame["actual_peak_afternoon"].astype(bool) & frame["label_no_wins"].eq(0)).sum()),
                "no_afternoon_and_no_win": int((~frame["actual_peak_afternoon"].astype(bool) & frame["label_no_wins"].eq(1)).sum()),
                "no_afternoon_and_no_loss": int((~frame["actual_peak_afternoon"].astype(bool) & frame["label_no_wins"].eq(0)).sum()),
            }
        )
    detail_cols = [
        "target_date",
        "city",
        "decision_hour_local",
        "bracket",
        "current_native",
        "running_native",
        "bracket_upper",
        "final_max_native",
        "actual_peak_first_hour_local",
        "actual_peak_last_hour_local",
        "actual_peak_afternoon",
        "future_max_above_running",
        "final_max_above_current_upper",
        "label_no_wins",
        "no_ask",
        "stake_profit_usd",
        "peak_already_started_by_decision",
        "final_did_not_cross_bracket_upper",
    ]
    details = enriched[enriched["afternoon_label_fp_vs_no"]].copy()
    for col in detail_cols:
        if col not in details.columns:
            details[col] = np.nan
    details = details[detail_cols].sort_values(["target_date", "city", "decision_hour_local"]).head(200)
    return details, {"cohorts": rows}


def add_basic_multipliers(selected: pd.DataFrame) -> pd.DataFrame:
    out = selected.copy()
    out["full_multiplier"] = 1.0
    out["trade_cap_multiplier"] = MIN_MULT + (1.0 - MIN_MULT) * (
        1.0 - pd.to_numeric(out["p_cap"], errors="coerce").clip(0, 1)
    )
    out["atlas_physical_multiplier"] = out["day_regime"].map(PHYSICAL_DAY_MULTIPLIER).fillna(0.65)
    out["atlas_physical_trade_cap_multiplier"] = out["atlas_physical_multiplier"] * out["trade_cap_multiplier"]
    return out


def fit_atlas_train_multipliers(train_selected: pd.DataFrame) -> dict[str, float]:
    settled = train_selected[train_selected["label_no_wins"].notna()].copy()
    if settled.empty:
        return {}
    global_roi = roi(settled) or 0.0
    multipliers: dict[str, float] = {}
    for regime, group in settled.groupby("day_regime"):
        n = len(group)
        group_roi = roi(group) or 0.0
        shrunk = (n / (n + SHRINK_N)) * group_roi + (SHRINK_N / (n + SHRINK_N)) * global_roi
        multipliers[str(regime)] = float(np.clip(0.75 + shrunk, MIN_MULT, 1.0))
    return multipliers


def weighted_perf(selected: pd.DataFrame, weight_col: str) -> dict[str, Any]:
    settled = selected[selected["label_no_wins"].notna()].copy()
    if settled.empty:
        return {
            "trades": int(len(selected)),
            "settled_trades": 0,
            "weighted_cost_usd": 0.0,
            "weighted_profit_usd": 0.0,
            "roi": None,
            "win_rate": None,
            "notional_retained": None,
        }
    w = pd.to_numeric(settled[weight_col], errors="coerce").fillna(1.0)
    cost = pd.to_numeric(settled["stake_cost_usd"], errors="coerce")
    profit = pd.to_numeric(settled["stake_profit_usd"], errors="coerce")
    weighted_cost = float((cost * w).sum())
    weighted_profit = float((profit * w).sum())
    return {
        "trades": int(len(selected)),
        "settled_trades": int(len(settled)),
        "weighted_cost_usd": weighted_cost,
        "weighted_profit_usd": weighted_profit,
        "roi": weighted_profit / weighted_cost if weighted_cost else None,
        "win_rate": float(settled["label_no_wins"].mean()),
        "notional_retained": float(weighted_cost / cost.sum()) if cost.sum() else None,
    }


def regime_slice_performance(selected: pd.DataFrame, by: str) -> pd.DataFrame:
    rows = []
    settled = selected[selected["label_no_wins"].notna()].copy()
    for key, group in settled.groupby(by, dropna=False):
        cost = float(group["stake_cost_usd"].sum())
        profit = float(group["stake_profit_usd"].sum())
        rows.append(
            {
                "slice_type": by,
                "slice": str(key),
                "trades": int(len(group)),
                "active_dates": int(group["target_date"].nunique()),
                "cities": int(group["city"].nunique()),
                "win_rate": float(group["label_no_wins"].mean()),
                "roi": profit / cost if cost else None,
                "profit_usd": profit,
                "cost_usd": cost,
                "avg_no_ask": float(group["no_ask"].mean()),
                "avg_p_cross": float(group["p_cross_upper"].mean()),
                "avg_p_cap": float(group["p_cap"].mean()),
                "avg_actual_margin_f": float(group["actual_margin_f"].mean()),
                "avg_pred_error_f": float(group["pred_error_f"].mean()),
            }
        )
    return pd.DataFrame(rows).sort_values(["slice_type", "roi"], ascending=[True, False])


def evaluate_block(frame: pd.DataFrame, atlas: pd.DataFrame, holdout_dates: list[str]) -> list[dict[str, Any]]:
    all_dates = set(frame["target_date"].astype(str).unique().tolist())
    train_dates = all_dates - set(holdout_dates)
    scored, _threshold = stress.train_score_arbitrary(frame, train_dates)
    train_selected = attach_atlas(cap.select_base_p40(scored[scored["target_date"].isin(train_dates)].copy()), atlas)
    holdout_selected = attach_atlas(cap.select_base_p40(scored[scored["target_date"].isin(holdout_dates)].copy()), atlas)
    if holdout_selected.empty:
        return []
    holdout_selected = add_basic_multipliers(holdout_selected)
    train_selected = add_basic_multipliers(train_selected)
    lookup = fit_atlas_train_multipliers(train_selected)
    train_default = float(np.clip(0.75 + (roi(train_selected) or 0.0), MIN_MULT, 1.0)) if len(train_selected) else 0.65
    holdout_selected["atlas_train_shrunk_multiplier"] = (
        holdout_selected["day_regime"].map(lookup).fillna(train_default).astype(float)
    )
    holdout_selected["atlas_train_shrunk_trade_cap_multiplier"] = (
        holdout_selected["atlas_train_shrunk_multiplier"] * holdout_selected["trade_cap_multiplier"]
    )
    rows = []
    for policy, weight_col in [
        ("full_size_base_p40", "full_multiplier"),
        ("trade_cap_soft_size", "trade_cap_multiplier"),
        ("atlas_physical_soft_size", "atlas_physical_multiplier"),
        ("atlas_physical_trade_cap_soft_size", "atlas_physical_trade_cap_multiplier"),
        ("atlas_train_shrunk_soft_size", "atlas_train_shrunk_multiplier"),
        ("atlas_train_shrunk_trade_cap_soft_size", "atlas_train_shrunk_trade_cap_multiplier"),
    ]:
        rows.append(
            {
                "holdout_dates": ",".join(holdout_dates),
                "policy": policy,
                "avg_atlas_physical_multiplier": float(holdout_selected["atlas_physical_multiplier"].mean()),
                "avg_atlas_train_shrunk_multiplier": float(holdout_selected["atlas_train_shrunk_multiplier"].mean()),
                "avg_trade_cap_multiplier": float(holdout_selected["trade_cap_multiplier"].mean()),
                "day_regime_mix": ",".join(
                    f"{k}:{v}" for k, v in holdout_selected["day_regime"].value_counts().sort_index().to_dict().items()
                ),
                **weighted_perf(holdout_selected, weight_col),
            }
        )
    return rows


def rolling_blocks(frame: pd.DataFrame, atlas: pd.DataFrame, block_days: int = 2) -> pd.DataFrame:
    dates = sorted(frame["target_date"].astype(str).unique().tolist())
    rows: list[dict[str, Any]] = []
    for start in range(0, len(dates) - block_days + 1):
        rows.extend(evaluate_block(frame, atlas, dates[start : start + block_days]))
    return pd.DataFrame(rows)


def policy_summary(blocks: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for policy, group in blocks.groupby("policy"):
        cost = float(group["weighted_cost_usd"].sum())
        profit = float(group["weighted_profit_usd"].sum())
        rows.append(
            {
                "policy": policy,
                "blocks": int(len(group)),
                "total_trades": int(group["settled_trades"].sum()),
                "weighted_cost_usd": cost,
                "weighted_profit_usd": profit,
                "cost_weighted_roi": profit / cost if cost else None,
                "mean_block_roi": float(group["roi"].mean()),
                "positive_blocks": int(group["roi"].gt(0).sum()),
                "bad_blocks_roi_le_minus50": int(group["roi"].le(-0.50).sum()),
                "avg_notional_retained": float(group["notional_retained"].mean()),
            }
        )
    return pd.DataFrame(rows).sort_values("cost_weighted_roi", ascending=False)


def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    shown = df if limit is None else df.head(limit)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in shown.iterrows():
        vals = []
        for col in cols:
            val = row.get(col)
            if col.endswith("roi") or col.endswith("rate") or col.endswith("retained") or col.endswith("share"):
                vals.append(pct(val))
            elif col.endswith("usd"):
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}")
            else:
                vals.append("" if pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_md(payload: dict[str, Any], mismatch: pd.DataFrame, slices: pd.DataFrame, policies: pd.DataFrame) -> str:
    mismatch_df = pd.DataFrame(payload["label_mismatch"]["cohorts"])
    day_slices = slices[slices["slice_type"].eq("day_regime")].copy()
    state_slices = slices[slices["slice_type"].eq("intraday_state")].copy()
    return "\n".join(
        [
            "# Current-Bracket NO Regime Atlas Overlay V1",
            "",
            "## 结论",
            "",
            "先纠正口径：最早 classifier 用的 `actual_peak_afternoon` 不是“决策后会不会升破当前温度”，而是“全天最高温第一次出现是否在 13 点以后”。这个 label 和 current-bracket NO payoff 相关，但不等价。真正交易 payoff 是 `label_no_wins = 1 - current_bracket_held`，或 remaining-heat 口径里的 `future_delta_to_daymax_f > required_gap_f`。",
            "",
            "把 intraday regime atlas 加进去以后，regime 能解释坏日/坏状态，但第一版 soft sizing 没有把策略变成 confirmed。它更适合作为共享机制特征和 forward 记录字段，而不是直接当买卖规则。",
            "",
            f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## Label Mismatch",
            "",
            table(
                mismatch_df,
                [
                    "cohort",
                    "rows",
                    "actual_peak_afternoon_rate",
                    "future_max_above_running_rate",
                    "no_win_rate",
                    "roi",
                    "afternoon_yes_no_loss_rows",
                    "afternoon_yes_no_loss_share_of_afternoon",
                    "fp_peak_already_started_share",
                    "fp_final_did_not_cross_upper_share",
                    "fp_future_max_above_running_share",
                ],
            ),
            "",
            "解释：`actual_peak_afternoon=1` 只说明最高温首次出现时间在午后；如果 13 点已经触顶，14 点买入时后面不再创新高，它仍会被这个 label 算作 afternoon peak。另一个错配来自最高温没有穿过所买 bracket upper + margin，NO payoff 仍输。少数明细里 raw/native 温度看似略高于 upper，但 settlement 的 `current_bracket_held` 仍为 1；这类属于 source/rounding/market-bracket 口径，交易 payoff 必须以 settlement label 为准。",
            "",
            "## Base P40 By Day Regime",
            "",
            table(
                day_slices,
                [
                    "slice",
                    "trades",
                    "active_dates",
                    "win_rate",
                    "roi",
                    "avg_no_ask",
                    "avg_p_cross",
                    "avg_p_cap",
                    "avg_actual_margin_f",
                    "avg_pred_error_f",
                ],
            ),
            "",
            "## Base P40 By Intraday State",
            "",
            table(
                state_slices,
                [
                    "slice",
                    "trades",
                    "active_dates",
                    "win_rate",
                    "roi",
                    "avg_no_ask",
                    "avg_p_cross",
                    "avg_p_cap",
                    "avg_actual_margin_f",
                    "avg_pred_error_f",
                ],
                limit=20,
            ),
            "",
            "## Rolling 2-Day Soft Sizing",
            "",
            table(
                policies,
                [
                    "policy",
                    "blocks",
                    "total_trades",
                    "weighted_cost_usd",
                    "weighted_profit_usd",
                    "cost_weighted_roi",
                    "positive_blocks",
                    "bad_blocks_roi_le_minus50",
                    "avg_notional_retained",
                ],
            ),
            "",
            "## 6/21-6/22 Forward Stress Block",
            "",
            table(
                pd.read_csv(OUT_BLOCKS)
                .query("holdout_dates == '2026-06-21,2026-06-22'")
                .sort_values("roi"),
                [
                    "policy",
                    "settled_trades",
                    "weighted_cost_usd",
                    "weighted_profit_usd",
                    "roi",
                    "notional_retained",
                    "avg_atlas_physical_multiplier",
                    "avg_trade_cap_multiplier",
                    "day_regime_mix",
                ],
            ),
            "",
            "这一块仍然很差：atlas+trade_cap 没救回 6/21-6/22，只是减少暴露。说明 atlas 第一版能解释 regime，不足以独立解决 forward tail。",
            "",
            "## Interpretation",
            "",
            "1. 你说的逻辑在严格定义下是对的：如果真的在决策时买 running max 所在单档 NO，且后面真实升破该档，那么 NO 应该赢。",
            "2. 早期错位不是这个物理逻辑错，而是 label 用了 `actual_peak_afternoon` 这种日级 peak-time proxy；它没有要求“决策后继续升破所买档”。",
            "3. atlas 加入后能看出 current-bracket NO 最怕 `forecast_capped/busted` 与成熟回落状态；但简单物理 multiplier / train-shrunk multiplier 仍只是减 tail，不是 confirmed alpha。",
            "4. 下一步应该把 atlas 字段接进 frozen forward ledger，每天记录 regime，再等新 settled dates，而不是直接把某个 regime 变 hard gate。",
            "",
            "## Files",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Label mismatch details: `{OUT_MISMATCH.relative_to(ROOT)}`",
            f"- Regime slice performance: `{OUT_REGIME_SLICES.relative_to(ROOT)}`",
            f"- Rolling blocks: `{OUT_BLOCKS.relative_to(ROOT)}`",
            f"- Policy summary: `{OUT_POLICY.relative_to(ROOT)}`",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    atlas = load_atlas()
    mismatch_details, mismatch_payload = mismatch_summary()
    mismatch_details.to_csv(OUT_MISMATCH, index=False)

    combined, _open_rows, source_stats = stress.load_combined()
    selected_all_parts = []
    scored_all, _threshold = stress.train_score_arbitrary(combined, set(combined["target_date"].astype(str).unique()))
    selected_all = attach_atlas(cap.select_base_p40(scored_all), atlas)
    selected_all.to_csv(OUT_DIR / "base_p40_selected_with_atlas.csv", index=False)
    selected_all_parts.append(regime_slice_performance(selected_all, "day_regime"))
    selected_all_parts.append(regime_slice_performance(selected_all, "intraday_state"))
    slices = pd.concat(selected_all_parts, ignore_index=True)
    slices.to_csv(OUT_REGIME_SLICES, index=False)

    blocks = rolling_blocks(combined, atlas, block_days=2)
    policies = policy_summary(blocks)
    blocks.to_csv(OUT_BLOCKS, index=False)
    policies.to_csv(OUT_POLICY, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_regime_atlas_overlay_v1",
        "data_snapshot": {
            "sync_ran": True,
            "run_stack_note": "fact tables and CLOB gate rebuilt; run_stack exited non-zero only because frontend port 5174 stayed busy",
            "clob_gate_pass": True,
            "atlas_rows": int(len(atlas)),
            "atlas_date_min": str(atlas["target_date"].min()),
            "atlas_date_max": str(atlas["target_date"].max()),
            "combined_rows": int(len(combined)),
            "combined_dates": sorted(combined["target_date"].astype(str).unique().tolist()),
        },
        "source_stats": source_stats,
        "label_mismatch": mismatch_payload,
        "policy_summary": finite_or_none(policies.to_dict(orient="records")),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "mismatch_csv": str(OUT_MISMATCH.relative_to(ROOT)),
            "regime_slice_csv": str(OUT_REGIME_SLICES.relative_to(ROOT)),
            "rolling_blocks_csv": str(OUT_BLOCKS.relative_to(ROOT)),
            "policy_summary_csv": str(OUT_POLICY.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "atlas_feature_layer_useful_but_not_confirmed_trading_rule",
            "live_ready": False,
            "reason": "Atlas labels explain payoff regimes and can support forward logging/soft sizing, but rolling policy results do not pass live gates.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, mismatch_details, slices, policies), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
