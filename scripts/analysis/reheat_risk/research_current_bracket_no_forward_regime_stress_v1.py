#!/usr/bin/env python3
"""Forward-regime stress test for current-bracket NO.

Questions answered:

1. Why did a signal that looked profitable on historical/holdout collapse on
   the settled forward block 2026-06-21..2026-06-22?
2. If that settled forward block is included in training and other dates are
   held out as pseudo-forward blocks, do similar collapses appear?

This uses blocked cross-validation by target_date.  The 2026-06-23 rows are
open/unsettled in the current snapshot, so they are excluded from label-based
training and ROI evaluation.
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
from sklearn.metrics import brier_score_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import research_current_bracket_no_capped_day_regime_v1 as cap  # noqa: E402
import research_current_bracket_no_remaining_heat_mechanism_features_v3 as v3  # noqa: E402
import research_current_bracket_no_remaining_heat_model_v1 as v1  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_forward_regime_stress_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_DAILY = OUT_DIR / "live_like_daily_regime.csv"
OUT_BLOCKS = OUT_DIR / "rolling_2day_block_cv.csv"
OUT_DIST = OUT_DIR / "regime_distribution_compare.csv"
OUT_OPEN = OUT_DIR / "open_2026_06_23_shadow_rows.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-forward-regime-stress-v1.md"

LIVE_TRAIN_END = "2026-06-10"
SETTLED_FORWARD_DATES = ["2026-06-21", "2026-06-22"]
OPEN_DATE = "2026-06-23"
SEED = 20260624


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


def load_combined() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    hist, hist_stats = cap.prepare_history()
    forward, forward_stats = cap.prepare_forward()
    settled_forward = forward[forward["label_no_wins"].notna()].copy()
    combined = pd.concat([hist, settled_forward], ignore_index=True)
    combined = combined[combined["label_no_wins"].notna()].copy()
    combined["target_date"] = combined["target_date"].astype(str)
    open_rows = forward[forward["label_no_wins"].isna()].copy()
    stats = {
        "historical": hist_stats,
        "forward": {"mechanism_rows": int(len(forward)), "source_stats": forward_stats},
        "combined_rows": int(len(combined)),
        "combined_dates": sorted(combined["target_date"].unique().tolist()),
        "open_rows": int(len(open_rows)),
        "open_dates": sorted(open_rows["target_date"].astype(str).unique().tolist()) if len(open_rows) else [],
    }
    return combined.reset_index(drop=True), open_rows.reset_index(drop=True), stats


def fit_live_like(frame: pd.DataFrame) -> tuple[pd.DataFrame, Any, float, Any, float]:
    train_mask = frame["target_date"].le(LIVE_TRAIN_END)
    scored, heat_model, heat_sigma = v3.score_model(frame, train_mask, v3.ENHANCED_NUM_FEATURES, v3.ENHANCED_CAT_FEATURES)
    scored = cap.add_regime_columns(scored)
    cap_model = cap.fit_cap_model(scored, "early", cap.CAP_CAT_WEATHER)
    scored = cap.score_cap_model(scored, cap_model, cap.CAP_CAT_WEATHER)
    train_selected = cap.select_base_p40(scored[train_mask].copy())
    threshold = float(train_selected["p_cap"].quantile(0.75)) if len(train_selected) else 1.0
    return scored, heat_model, heat_sigma, cap_model, threshold


def score_open(open_rows: pd.DataFrame, heat_model: Any, heat_sigma: float, cap_model: Any) -> pd.DataFrame:
    if open_rows.empty:
        return open_rows.copy()
    scored = cap.score_remaining_heat(open_rows, heat_model, heat_sigma)
    scored = cap.score_cap_model(scored, cap_model, cap.CAP_CAT_WEATHER)
    return scored


def perf(selected: pd.DataFrame) -> dict[str, Any]:
    settled = selected[selected["label_no_wins"].notna()].copy()
    cost = float(settled["stake_cost_usd"].sum()) if len(settled) else 0.0
    profit = float(settled["stake_profit_usd"].sum()) if len(settled) else 0.0
    return {
        "trades": int(len(selected)),
        "settled_trades": int(len(settled)),
        "wins": float(settled["label_no_wins"].sum()) if len(settled) else 0.0,
        "win_rate": float(settled["label_no_wins"].mean()) if len(settled) else None,
        "cost_usd": cost,
        "profit_usd": profit,
        "roi": profit / cost if cost else None,
        "avg_p_cross": float(selected["p_cross_upper"].mean()) if len(selected) else None,
        "avg_p_cap": float(selected["p_cap"].mean()) if len(selected) else None,
        "avg_pred_error_f": float(settled["pred_error_f"].mean()) if len(settled) else None,
        "avg_actual_margin_f": float(settled["actual_margin_f"].mean()) if len(settled) else None,
        "overconf_trades": int(((settled["p_cross_upper"].ge(0.95)) & (settled["p_cap"].lt(0.30))).sum()) if len(settled) else 0,
    }


def live_like_daily(scored: pd.DataFrame, threshold: float) -> pd.DataFrame:
    rows = []
    for date, part in scored.groupby("target_date"):
        base = cap.select_base_p40(part.copy())
        veto = cap.select_cap_veto(part.copy(), threshold)
        adjusted = cap.select_cap_adjusted(part.copy())
        for variant, selected in [("base_p40_ev10", base), ("cap_veto_train_top25_risk", veto), ("cap_adjusted_p40_ev10", adjusted)]:
            row = {"target_date": date, "variant": variant, **perf(selected)}
            if len(selected):
                row["cities"] = ",".join(sorted(selected["city"].astype(str).unique()))
                row["loss_reason_counts"] = ",".join(
                    f"{k}:{v}" for k, v in selected["loss_reason"].value_counts().sort_index().to_dict().items()
                )
            else:
                row["cities"] = ""
                row["loss_reason_counts"] = ""
            rows.append(row)
    out = pd.DataFrame(rows)
    out["is_settled_forward"] = out["target_date"].isin(SETTLED_FORWARD_DATES)
    out["period"] = np.select(
        [out["target_date"].le(LIVE_TRAIN_END), out["target_date"].isin(SETTLED_FORWARD_DATES), out["target_date"].gt(LIVE_TRAIN_END)],
        ["train", "settled_forward", "post_train_hist"],
        default="other",
    )
    return out.sort_values(["target_date", "variant"]).reset_index(drop=True)


def train_score_arbitrary(frame: pd.DataFrame, train_dates: set[str]) -> tuple[pd.DataFrame, float]:
    train_mask = frame["target_date"].isin(train_dates)
    scored, _heat_model, _heat_sigma = v3.score_model(frame, train_mask, v3.ENHANCED_NUM_FEATURES, v3.ENHANCED_CAT_FEATURES)
    scored = cap.add_regime_columns(scored)
    cap_model = cap.build_cap_classifier(cap.CAP_CAT_WEATHER)
    train = scored[train_mask].copy()
    cap_model.fit(train[cap.CAP_NUM_FEATURES + cap.CAP_CAT_WEATHER], train["cap_label"].astype(int))
    scored = cap.score_cap_model(scored, cap_model, cap.CAP_CAT_WEATHER)
    train_selected = cap.select_base_p40(scored[train_mask].copy())
    threshold = float(train_selected["p_cap"].quantile(0.75)) if len(train_selected) else 1.0
    return scored, threshold


def model_cap_metrics(scored_holdout: pd.DataFrame) -> dict[str, Any]:
    d = scored_holdout.copy()
    y = d["cap_label"].astype(int)
    p = pd.to_numeric(d["p_cap"], errors="coerce")
    return {
        "holdout_rows": int(len(d)),
        "holdout_cap_rate": float(y.mean()) if len(d) else None,
        "holdout_cap_auc": float(roc_auc_score(y, p)) if y.nunique() > 1 else None,
        "holdout_cap_brier": float(brier_score_loss(y, p)) if y.nunique() > 1 else None,
    }


def rolling_block_cv(frame: pd.DataFrame, block_days: int = 2) -> pd.DataFrame:
    dates = sorted(frame["target_date"].unique().tolist())
    rows = []
    all_dates = set(dates)
    for start in range(0, len(dates) - block_days + 1):
        holdout_dates = dates[start : start + block_days]
        train_dates = all_dates - set(holdout_dates)
        scored, threshold = train_score_arbitrary(frame, train_dates)
        holdout = scored[scored["target_date"].isin(holdout_dates)].copy()
        metric = model_cap_metrics(holdout)
        for variant, selected in [
            ("base_p40_ev10", cap.select_base_p40(holdout.copy())),
            ("cap_veto_train_top25_risk", cap.select_cap_veto(holdout.copy(), threshold)),
            ("cap_adjusted_p40_ev10", cap.select_cap_adjusted(holdout.copy())),
        ]:
            row = {
                "holdout_start": holdout_dates[0],
                "holdout_end": holdout_dates[-1],
                "holdout_dates": ",".join(holdout_dates),
                "variant": variant,
                "train_includes_2026_06_21": "2026-06-21" in train_dates,
                "train_includes_2026_06_22": "2026-06-22" in train_dates,
                "cap_veto_threshold": threshold,
                **metric,
                **perf(selected),
            }
            if len(selected):
                row["cities"] = ",".join(sorted(selected["city"].astype(str).unique()))
            else:
                row["cities"] = ""
            rows.append(row)
    out = pd.DataFrame(rows)
    return out.sort_values(["variant", "roi", "holdout_start"], ascending=[True, True, True]).reset_index(drop=True)


def distribution_compare(daily: pd.DataFrame, blocks: pd.DataFrame) -> pd.DataFrame:
    rows = []
    base_daily = daily[daily["variant"].eq("base_p40_ev10")].copy()
    base_blocks = blocks[blocks["variant"].eq("base_p40_ev10")].copy()
    forward_daily = base_daily[base_daily["is_settled_forward"]]
    hist_daily = base_daily[~base_daily["is_settled_forward"]]
    for metric in ["roi", "win_rate", "avg_pred_error_f", "avg_actual_margin_f", "overconf_trades"]:
        h = pd.to_numeric(hist_daily[metric], errors="coerce").dropna()
        f = pd.to_numeric(forward_daily[metric], errors="coerce").dropna()
        if h.empty or f.empty:
            continue
        rows.append(
            {
                "grain": "daily_base_p40",
                "metric": metric,
                "hist_mean": float(h.mean()),
                "hist_p10": float(h.quantile(0.10)),
                "hist_p50": float(h.quantile(0.50)),
                "hist_p90": float(h.quantile(0.90)),
                "settled_forward_mean": float(f.mean()),
                "settled_forward_min": float(f.min()),
                "settled_forward_max": float(f.max()),
                "forward_percentile_vs_hist": float((h.le(f.mean()).mean())),
            }
        )
    target_block = base_blocks[base_blocks["holdout_dates"].eq(",".join(SETTLED_FORWARD_DATES))]
    for metric in ["roi", "win_rate", "avg_pred_error_f", "avg_actual_margin_f", "overconf_trades", "holdout_cap_rate"]:
        vals = pd.to_numeric(base_blocks[metric], errors="coerce").dropna()
        tb = pd.to_numeric(target_block[metric], errors="coerce").dropna()
        if vals.empty or tb.empty:
            continue
        value = float(tb.iloc[0])
        rows.append(
            {
                "grain": "rolling_2day_base_p40",
                "metric": metric,
                "hist_mean": float(vals.mean()),
                "hist_p10": float(vals.quantile(0.10)),
                "hist_p50": float(vals.quantile(0.50)),
                "hist_p90": float(vals.quantile(0.90)),
                "settled_forward_mean": value,
                "settled_forward_min": value,
                "settled_forward_max": value,
                "forward_percentile_vs_hist": float(vals.le(value).mean()),
            }
        )
    return pd.DataFrame(rows)


def summarize_blocks(blocks: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for variant, group in blocks.groupby("variant"):
        rois = pd.to_numeric(group["roi"], errors="coerce").dropna()
        bad = group[group["roi"].le(-0.50)].copy()
        target = group[group["holdout_dates"].eq(",".join(SETTLED_FORWARD_DATES))]
        out[variant] = {
            "blocks": int(len(group)),
            "roi_mean": float(rois.mean()) if len(rois) else None,
            "roi_median": float(rois.median()) if len(rois) else None,
            "roi_p10": float(rois.quantile(0.10)) if len(rois) else None,
            "roi_p90": float(rois.quantile(0.90)) if len(rois) else None,
            "bad_blocks_roi_le_minus50": int(len(bad)),
            "bad_block_dates": bad["holdout_dates"].head(20).tolist(),
            "target_forward_block": finite_or_none(target.to_dict(orient="records")),
        }
    return out


def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    shown = df if limit is None else df.head(limit)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in shown.iterrows():
        vals = []
        for col in cols:
            val = row.get(col)
            if col.endswith("roi") or col.endswith("rate") or col.startswith("win_rate"):
                vals.append(pct(val))
            elif col.endswith("usd"):
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}")
            else:
                vals.append("" if pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_md(payload: dict[str, Any], daily: pd.DataFrame, blocks: pd.DataFrame, dist: pd.DataFrame, open_rows: pd.DataFrame) -> str:
    daily_focus = daily[
        daily["variant"].eq("base_p40_ev10")
        & (daily["target_date"].isin(SETTLED_FORWARD_DATES) | daily["roi"].le(-0.90) | daily["overconf_trades"].ge(2))
    ].sort_values(["target_date"]).copy()
    worst_blocks = blocks[blocks["variant"].eq("base_p40_ev10")].sort_values(["roi", "holdout_start"]).head(12).copy()
    target_blocks = blocks[blocks["holdout_dates"].eq(",".join(SETTLED_FORWARD_DATES))].copy()
    other_bad_with_forward_training = blocks[
        blocks["variant"].eq("base_p40_ev10")
        & blocks["train_includes_2026_06_21"]
        & blocks["train_includes_2026_06_22"]
        & blocks["roi"].le(-0.50)
    ].sort_values(["roi", "holdout_start"]).copy()
    open_focus = open_rows[
        ["target_date", "city", "no_ask", "p_cross_upper", "p_cap", "p_cross_cap_adjusted", "mechanism_edge", "cap_adjusted_edge"]
    ].sort_values(["p_cross_upper"], ascending=False).head(20) if not open_rows.empty else pd.DataFrame()
    return "\n".join(
        [
            "# Current-Bracket NO Forward Regime Stress V1",
            "",
            "## 结论",
            "",
            "6/21-6/22 崩盘不是单纯因为没有把这两天放进训练；滚动 2 日 block CV 显示，类似的坏 block 在历史里也存在。区别是 6/21-6/22 的 regime 更集中：cap rate 明显更高，p40 选中票的 actual margin 偏负，且出现 `p_cross` 极高但实际不穿 upper 的 forecast-overconfidence。",
            "",
            "把 6/21-6/22 已结算数据放进训练后，换其它历史日期当 pseudo-forward，仍会出现 ROI <= -50% 的坏 block。这说明这是典型的 regime / non-stationarity 问题，不是简单换一个训练集就能治好。",
            "",
            f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## Data",
            "",
            f"- Generated at UTC: `{payload['generated_at_utc']}`",
            f"- Sync/rebuild: `{payload['data_refresh_note']}`",
            f"- Settled forward dates: `{','.join(SETTLED_FORWARD_DATES)}`",
            f"- Open/unsettled date excluded from label training: `{OPEN_DATE}` rows={payload['open_2026_06_23_rows']}",
            f"- Combined settled dates: `{payload['settled_date_min']}`..`{payload['settled_date_max']}`",
            "",
            "## Why 6/21-6/22 Broke",
            "",
            table(daily_focus, ["target_date", "period", "trades", "win_rate", "roi", "profit_usd", "avg_p_cross", "avg_p_cap", "avg_pred_error_f", "avg_actual_margin_f", "overconf_trades", "cities"], limit=80),
            "",
            "## Distribution Compare",
            "",
            table(dist, ["grain", "metric", "hist_mean", "hist_p10", "hist_p50", "hist_p90", "settled_forward_mean", "forward_percentile_vs_hist"]),
            "",
            "## Rolling 2-Day Block CV: Target Block",
            "",
            table(target_blocks, ["variant", "holdout_dates", "trades", "win_rate", "roi", "profit_usd", "avg_p_cross", "avg_p_cap", "avg_pred_error_f", "avg_actual_margin_f", "overconf_trades", "holdout_cap_rate"]),
            "",
            "## Worst Rolling Blocks",
            "",
            table(worst_blocks, ["holdout_dates", "trades", "win_rate", "roi", "profit_usd", "avg_p_cross", "avg_p_cap", "avg_pred_error_f", "avg_actual_margin_f", "overconf_trades", "holdout_cap_rate", "cities"], limit=12),
            "",
            "## Other Bad Blocks After Training Includes 6/21-6/22",
            "",
            table(other_bad_with_forward_training, ["holdout_dates", "trades", "win_rate", "roi", "profit_usd", "avg_p_cross", "avg_p_cap", "avg_pred_error_f", "avg_actual_margin_f", "overconf_trades", "cities"], limit=20),
            "",
            "## 6/23 Open Shadow",
            "",
            table(open_focus, ["target_date", "city", "no_ask", "p_cross_upper", "p_cap", "p_cross_cap_adjusted", "mechanism_edge", "cap_adjusted_edge"], limit=20),
            "",
            "## Quant Read",
            "",
            "1. 这是 blocked walk-forward / regime validation 问题：随机拆样本会高估稳定性，因为同一天多城市高度相关。",
            "2. 6/21-6/22 不是唯一坏 block；历史也有 5/25、5/31、6/01、6/10 这类同步亏损日。策略收益来自好 regime 覆盖坏 regime，而不是每个 regime 都稳。",
            "3. 把坏 block 放进训练可以让模型见过这种形态，但如果特征没有表达 forecast-overconfidence 的根因，它仍会在其它坏 block 上复发。",
            "4. 下一步不是继续换 split，而是建 regime-aware policy：当 day-regime 不可判别时降低交易频率或只 shadow。",
            "",
            "## Files",
            "",
            f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Daily regime: `{OUT_DAILY.relative_to(ROOT)}`",
            f"- Rolling blocks: `{OUT_BLOCKS.relative_to(ROOT)}`",
            f"- Distribution compare: `{OUT_DIST.relative_to(ROOT)}`",
            f"- 6/23 open shadow: `{OUT_OPEN.relative_to(ROOT)}`",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    combined, open_rows, stats = load_combined()
    live_scored, heat_model, heat_sigma, cap_model, threshold = fit_live_like(combined)
    daily = live_like_daily(live_scored, threshold)
    open_scored = score_open(open_rows, heat_model, heat_sigma, cap_model)
    if not open_scored.empty:
        open_selected = cap.select_base_p40(open_scored[open_scored["target_date"].eq(OPEN_DATE)].copy())
        open_selected.to_csv(OUT_OPEN, index=False)
    else:
        pd.DataFrame().to_csv(OUT_OPEN, index=False)

    blocks = rolling_block_cv(combined, block_days=2)
    dist = distribution_compare(daily, blocks)

    daily.to_csv(OUT_DAILY, index=False)
    blocks.to_csv(OUT_BLOCKS, index=False)
    dist.to_csv(OUT_DIST, index=False)

    base_blocks = blocks[blocks["variant"].eq("base_p40_ev10")]
    target_block = base_blocks[base_blocks["holdout_dates"].eq(",".join(SETTLED_FORWARD_DATES))]
    bad_with_forward_training = base_blocks[
        base_blocks["train_includes_2026_06_21"] & base_blocks["train_includes_2026_06_22"] & base_blocks["roi"].le(-0.50)
    ]
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_forward_regime_stress_v1",
        "data_refresh_note": "sync_weather_remote.sh completed; run_stack rebuilt fact tables and CLOB gate, then exited non-clean because FE port 5174 stayed busy.",
        "source_stats": stats,
        "settled_date_min": str(combined["target_date"].min()),
        "settled_date_max": str(combined["target_date"].max()),
        "open_2026_06_23_rows": int((open_rows["target_date"].astype(str).eq(OPEN_DATE)).sum()) if len(open_rows) else 0,
        "live_like_cap_threshold": threshold,
        "target_forward_block_base_p40": finite_or_none(target_block.to_dict(orient="records")),
        "rolling_block_summary": summarize_blocks(blocks),
        "bad_base_blocks_after_training_includes_6_21_6_22": finite_or_none(
            bad_with_forward_training.sort_values(["roi", "holdout_start"]).head(20).to_dict(orient="records")
        ),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "live_like_daily_regime_csv": str(OUT_DAILY.relative_to(ROOT)),
            "rolling_2day_block_cv_csv": str(OUT_BLOCKS.relative_to(ROOT)),
            "regime_distribution_compare_csv": str(OUT_DIST.relative_to(ROOT)),
            "open_2026_06_23_shadow_rows_csv": str(OUT_OPEN.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "forward_failure_is_regime_nonstationarity_not_single_split_bug",
            "live_ready": False,
            "reason": "6/21-6/22 is a high-cap/high-overconfidence bad regime; rolling block CV shows similar collapses still appear even when the settled forward block is included in training.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, daily, blocks, dist, open_scored if not open_scored.empty else pd.DataFrame()), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
