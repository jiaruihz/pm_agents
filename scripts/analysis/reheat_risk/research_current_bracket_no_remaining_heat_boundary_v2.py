#!/usr/bin/env python3
"""Boundary-conditioned remaining-heat model for current-bracket NO.

V1 showed the first-principles target is right, but training on every midday
row learns broad daily heating better than the near-boundary trade problem.
This V2 compares three training denominators for the same payoff mechanism:

- all_rows: all current-bracket NO midday rows with forecast/final max.
- near_boundary: rows where required_gap_f is small enough to resemble a
  current-bracket crossing decision.
- trade_base: rows that are executable by ask/depth.
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

import research_current_bracket_no_remaining_heat_model_v1 as v1  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_remaining_heat_boundary_v2"
OUT_JSON = OUT_DIR / "summary.json"
OUT_VARIANTS = OUT_DIR / "variant_summary.csv"
OUT_FORWARD = OUT_DIR / "forward_validation_and_shadow.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-remaining-heat-boundary-v2.md"


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
        fval = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(fval):
        return "NA"
    return f"{100.0 * fval:+.1f}%"


def money(value: Any) -> str:
    try:
        fval = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(fval):
        return "NA"
    return f"${fval:+,.2f}"


def training_masks(frame: pd.DataFrame, split_date: str) -> dict[str, pd.Series]:
    train_period = frame["target_date"].astype(str).le(split_date)
    return {
        "all_rows": train_period,
        "near_boundary_gap_le_2f": train_period & frame["required_gap_f"].between(0.0, 2.0),
        "trade_base": train_period & frame["trade_base_mechanism"],
    }


def score_with_scope(frame: pd.DataFrame, train_mask: pd.Series) -> tuple[pd.DataFrame, Any, dict[str, Any]]:
    return v1.add_predictions(frame, train_mask)


def evaluate_scope(scope: str, frame: pd.DataFrame, split_date: str, train_mask: pd.Series) -> tuple[pd.DataFrame, list[dict[str, Any]], list[dict[str, Any]], Any, float]:
    scored, model, residual = score_with_scope(frame, train_mask)
    scored["score_scope"] = scope
    scored["period_split"] = np.where(scored["target_date"].astype(str).le(split_date), "train", "holdout")
    scored["trade_base_mechanism"] = v1.trade_base_mask(scored)
    metrics = [
        {"score_scope": scope, "period": "train_scope", **v1.model_metrics(scored, train_mask)},
        {
            "score_scope": scope,
            "period": "trade_base_train",
            **v1.model_metrics(scored, scored["period_split"].eq("train") & scored["trade_base_mechanism"]),
        },
        {
            "score_scope": scope,
            "period": "trade_base_holdout",
            **v1.model_metrics(scored, scored["period_split"].eq("holdout") & scored["trade_base_mechanism"]),
        },
    ]
    variants = []
    baseline = v1.select_first(v1.variant_raws(scored)["baseline_trade_base"])
    for name, raw in v1.variant_raws(scored).items():
        row = v1.summarize_variant(f"{scope}::{name}", raw, None if name == "baseline_trade_base" else baseline)
        row["score_scope"] = scope
        row["base_variant"] = name
        variants.append(row)
    return scored, metrics, variants, model, float(residual["residual_sigma_f"])


def evaluate_forward_scope(scope: str, model: Any, sigma: float) -> list[dict[str, Any]]:
    forward, _stats = v1.load_forward_forced_gfs()
    if forward.empty:
        return []
    forward["pred_remaining_heat_f"] = model.predict(forward[v1.MECH_NUM_FEATURES + v1.MECH_CAT_FEATURES])
    forward["remaining_heat_sigma_f"] = sigma
    forward["p_cross_upper"] = v1.norm_sf((forward["required_gap_f"] - forward["pred_remaining_heat_f"]) / sigma)
    forward["mechanism_edge"] = forward["p_cross_upper"] - pd.to_numeric(forward["no_ask"], errors="coerce")
    forward["p_no_win"] = forward["p_cross_upper"]
    forward["p_up_margin"] = forward["p_cross_upper"]
    forward["edge_no_win"] = forward["mechanism_edge"]
    forward["edge_up_margin"] = forward["mechanism_edge"]
    forward["trade_base_mechanism"] = v1.trade_base_mask(forward)
    rows = []
    for name, raw in v1.variant_raws(forward).items():
        selected = v1.select_first(raw)
        if selected.empty:
            rows.append({"score_scope": scope, "base_variant": name, "variant": f"{scope}::{name}", "selected_trades": 0})
            continue
        settled = selected[selected["label_no_wins"].notna()].copy()
        profit = float(settled["stake_profit_usd"].sum()) if not settled.empty else 0.0
        cost = float(settled["stake_cost_usd"].sum()) if not settled.empty else 0.0
        rows.append(
            {
                "score_scope": scope,
                "base_variant": name,
                "variant": f"{scope}::{name}",
                "selected_trades": int(len(selected)),
                "active_dates": int(selected["target_date"].nunique()),
                "settled_trades": int(len(settled)),
                "open_shadow_trades": int(selected["label_no_wins"].isna().sum()),
                "settled_win_rate": None if settled.empty else float(settled["label_no_wins"].mean()),
                "settled_profit_usd": profit,
                "settled_roi": profit / cost if cost else None,
                "dates": ",".join(sorted(selected["target_date"].astype(str).unique())),
            }
        )
    return rows


def render_md(payload: dict[str, Any], metrics: pd.DataFrame, variants: pd.DataFrame, forward: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate") or col in {"roi_ci_low", "roi_ci_high", "holdout_roi"}:
                    vals.append(pct(val))
                elif col.endswith("usd"):
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.2f}")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    focus = variants[variants["base_variant"].isin(["baseline_trade_base", "remaining_heat_p40_ev10", "remaining_heat_p45_ev10"])].copy()
    ffocus = forward[forward["base_variant"].isin(["baseline_trade_base", "remaining_heat_p40_ev10", "remaining_heat_p45_ev10"])].copy()
    lines = [
        "# Current-Bracket NO Remaining-Heat Boundary V2",
        "",
        "## 结论",
        "",
        "V1 的 first-principles target 是对的，但训练分母不够贴交易边界。V2 比较 all rows、near-boundary、trade-base 三种训练分母。",
        "结果：边界训练能改变排序，但没有解决 6/21..6/23 forward；因此当前不是加 gate，而是说明可见特征仍不足以稳定估计临界 remaining heat。",
        "",
        f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
        "",
        "## 数据层",
        "",
        f"- Generated at UTC: `{payload['generated_at_utc']}`",
        f"- Raw rows: `{payload['dataset']['raw_rows']}`",
        f"- Mechanism rows: `{payload['dataset']['mechanism_rows']}`",
        f"- Trade-base rows: `{payload['dataset']['trade_base_rows']}`",
        f"- Split date: `{payload['dataset']['split_date']}`",
        "",
        "## Model Diagnostics",
        "",
        table(metrics, ["score_scope", "period", "rows", "active_dates", "mae_f", "rmse_f", "r2", "cross_rate", "cross_auc", "cross_brier"]),
        "",
        "## Trade Variants",
        "",
        table(
            focus,
            [
                "variant",
                "selected_trades",
                "active_dates",
                "win_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "holdout_roi",
                "avg_required_gap_f",
                "avg_pred_remaining_heat_f",
                "avg_actual_remaining_heat_f",
                "selected_all_loss_days",
                "selected_all_loss_dates",
            ],
        ),
        "",
        "## Forward 6/21..6/23",
        "",
        table(
            ffocus,
            ["variant", "selected_trades", "settled_trades", "open_shadow_trades", "settled_win_rate", "settled_roi", "settled_profit_usd", "dates"],
        ),
        "",
        "## 机制判断",
        "",
        "1. 全量模型会被大量非临界行主导，trade-base holdout 才是关键诊断层。",
        "2. trade-base/near-boundary 训练没有把 forward 拉正，说明当前可见特征还不能稳定估计临界剩余升温。",
        "3. 下一步应补真正机制特征：forecast hourly curve 的剩余斜率、观测曲线 plateau count、太阳高度/海风/云量变化，而不是继续换阈值。",
        "",
        "## Files",
        "",
        f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        f"- Variants: `{OUT_VARIANTS.relative_to(ROOT)}`",
        f"- Forward: `{OUT_FORWARD.relative_to(ROOT)}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame, stats = v1.load_historical_forced_gfs()
    split_date = v1.split_date_for(frame)
    frame["period_split"] = np.where(frame["target_date"].astype(str).le(split_date), "train", "holdout")
    frame["trade_base_mechanism"] = v1.trade_base_mask(frame)

    metrics_all = []
    variants_all = []
    forward_all = []
    for scope, mask in training_masks(frame, split_date).items():
        _scored, metrics, variants, model, sigma = evaluate_scope(scope, frame, split_date, mask)
        metrics_all.extend(metrics)
        variants_all.extend(variants)
        forward_all.extend(evaluate_forward_scope(scope, model, sigma))

    metrics_df = pd.DataFrame(metrics_all)
    variants_df = pd.DataFrame(variants_all)
    forward_df = pd.DataFrame(forward_all)
    variants_df.to_csv(OUT_VARIANTS, index=False)
    forward_df.to_csv(OUT_FORWARD, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_remaining_heat_boundary_v2",
        "data_refresh_note": "sync_weather_remote.sh completed; run_stack rebuilt facts and CLOB gate passed, then exited non-clean only because FE port 5174 stayed busy.",
        "dataset": {**stats, "split_date": split_date},
        "model_metrics": finite_or_none(metrics_df.to_dict(orient="records")),
        "variant_summary": finite_or_none(variants_df.to_dict(orient="records")),
        "forward_summary": finite_or_none(forward_df.to_dict(orient="records")),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "variant_summary_csv": str(OUT_VARIANTS.relative_to(ROOT)),
            "forward_validation_csv": str(OUT_FORWARD.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "mechanism_boundary_inconclusive_shadow_only",
            "live_ready": False,
            "reason": "Boundary-conditioned remaining-heat models are more aligned with the trade, but current features do not pass forward.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, metrics_df, variants_df, forward_df), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
