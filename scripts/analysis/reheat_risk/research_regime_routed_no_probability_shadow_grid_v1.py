#!/usr/bin/env python3
"""Shadow grid for regime-routed NO calibrated probability variants."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import research_regime_routed_no_calibrated_score_v2 as calibrated


ROOT = Path(__file__).resolve().parents[3]
INPUT = ROOT / "docs/analysis/2026-07/generated/regime_routed_no_calibrated_score_v2/scored_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/regime_routed_no_probability_shadow_grid_v1"
OUT_SUMMARY = OUT_DIR / "summary.json"
OUT_GRID = OUT_DIR / "shadow_grid_summary.csv"
OUT_DAILY = OUT_DIR / "shadow_grid_daily.csv"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-02-regime-routed-no-probability-shadow-grid-v1.md"
FORWARD_START = "2026-06-21"


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "NA"
    return f"{float(value) * 100:+.1f}%"


def money(value: Any) -> str:
    if value is None or pd.isna(value):
        return "NA"
    return f"${float(value):+.2f}"


def summarize(frame: pd.DataFrame, *, selected: pd.Series, cost: pd.Series) -> tuple[dict[str, Any], pd.DataFrame]:
    active = frame[selected.fillna(False).astype(bool)].copy()
    if active.empty:
        return {
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "win_rate": None,
            "avg_ask": None,
            "avg_cost": None,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "roi_ci_low": None,
            "roi_ci_high": None,
            "daily_negative_100pct": 0,
        }, pd.DataFrame()
    ask = pd.to_numeric(active["router_ask"], errors="coerce")
    payoff = pd.to_numeric(active["label_no_win"], errors="coerce")
    active["_cost"] = cost.reindex(active.index).fillna(0.0)
    active["_pnl"] = calibrated.row_pnl(active["_cost"], ask, payoff)
    daily = active.groupby("target_date", as_index=False).agg(
        rows=("city", "count"),
        cost=("_cost", "sum"),
        pnl=("_pnl", "sum"),
        wins=("label_no_win", "sum"),
    )
    daily["roi"] = daily["pnl"] / daily["cost"]
    ci_low, ci_high = calibrated.date_bootstrap_roi(active)
    total_cost = float(active["_cost"].sum())
    total_pnl = float(active["_pnl"].sum())
    return {
        "rows": int(len(active)),
        "dates": int(active["target_date"].nunique()),
        "cities": int(active["city"].nunique()),
        "win_rate": float(payoff.mean()),
        "avg_ask": float(ask.mean()),
        "avg_cost": float(active["_cost"].mean()),
        "cost": total_cost,
        "pnl": total_pnl,
        "roi": total_pnl / total_cost if total_cost else None,
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
        "daily_negative_100pct": int(daily["roi"].le(-0.999).sum()),
    }, daily


def variant_specs(frame: pd.DataFrame, p_col: str) -> list[tuple[str, pd.Series, pd.Series, pd.Series]]:
    ask = pd.to_numeric(frame["router_ask"], errors="coerce")
    p = pd.to_numeric(frame[p_col], errors="coerce").clip(0.0, 1.0)
    score = pd.to_numeric(frame["score_deployed_weight"], errors="coerce").fillna(0.0).clip(0.0, 1.0)
    score_ratio = score / ask
    p_ratio = p / ask
    blend50 = (0.50 * score + 0.50 * p).clip(0.0, 1.0)
    blend70 = (0.70 * score + 0.30 * p).clip(0.0, 1.0)
    min_score_p = pd.concat([score, p], axis=1).min(axis=1).clip(0.0, 1.0)
    return [
        ("baseline_current_score_gate_size_score", score_ratio.ge(1.0), score, score_ratio),
        ("prob_p_ge_ask_size_p", p.ge(ask), p.clip(0.05, 1.0), p_ratio),
        ("prob_p_ge_ask_size_current_score", p.ge(ask), score, p_ratio),
        ("prob_p_ge_ask_plus05_size_p", p.ge(ask + 0.05), p.clip(0.05, 1.0), p_ratio),
        ("prob_ratio_ge125_size_p", p_ratio.ge(1.25), p.clip(0.05, 1.0), p_ratio),
        ("agreement_current_and_prob_size_score", score_ratio.ge(1.0) & p.ge(ask), score, p_ratio),
        ("current_gate_size_min_score_p", score_ratio.ge(1.0), min_score_p, p_ratio),
        ("blend50_gate_size_blend50", (blend50 / ask).ge(1.0), blend50, blend50 / ask),
        ("blend70score_gate_size_blend70", (blend70 / ask).ge(1.0), blend70, blend70 / ask),
        ("diagnostic_current_gate_prob_disagrees", score_ratio.ge(1.0) & p.lt(ask), score, p_ratio),
    ]


def table(df: pd.DataFrame, cols: list[str], limit: int = 16) -> str:
    shown = df.head(limit)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in shown.iterrows():
        vals: list[str] = []
        for col in cols:
            val = row.get(col)
            if col in {"win_rate", "roi", "roi_ci_low", "roi_ci_high"}:
                vals.append(pct(val))
            elif col in {"cost", "pnl", "avg_cost"}:
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
            else:
                vals.append("" if pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(INPUT)
    df["target_date"] = df["target_date"].astype(str)
    df = df[df["label_no_win"].isin([0.0, 1.0]) & df["router_ask"].notna()].copy()
    p_cols = [c for c in df.columns if c.startswith("p_no_wf_")]
    rows: list[dict[str, Any]] = []
    daily_rows: list[pd.DataFrame] = []
    for evidence_layer, layer_frame in df.groupby("evidence_layer", sort=True):
        for scope_name, scope_frame in [
            ("expanding_walk_forward", layer_frame),
            (f"forward_{FORWARD_START}_plus", layer_frame[layer_frame["target_date"].ge(FORWARD_START)]),
        ]:
            for p_col in p_cols:
                frame = scope_frame[scope_frame[p_col].notna()].copy()
                if frame.empty:
                    continue
                model = p_col.replace("p_no_wf_", "")
                for variant, selected, cost, edge_ratio in variant_specs(frame, p_col):
                    work = frame.copy()
                    work["_edge_ratio"] = edge_ratio
                    selected_capped = calibrated.apply_daily_cap(work, selected=selected, cost=cost, cap=1.0)
                    summary, daily = summarize(work, selected=selected_capped, cost=cost)
                    summary.update(
                        {
                            "evidence_layer": evidence_layer,
                            "scope": scope_name,
                            "model": model,
                            "p_col": p_col,
                            "shadow_variant": variant,
                        }
                    )
                    rows.append(summary)
                    if not daily.empty:
                        daily["evidence_layer"] = evidence_layer
                        daily["scope"] = scope_name
                        daily["model"] = model
                        daily["shadow_variant"] = variant
                        daily_rows.append(daily)
    grid = pd.DataFrame(rows)
    daily = pd.concat(daily_rows, ignore_index=True) if daily_rows else pd.DataFrame()
    grid.to_csv(OUT_GRID, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    frozen_forward = grid[
        grid["evidence_layer"].eq("frozen_live_like_route_price")
        & grid["scope"].eq(f"forward_{FORWARD_START}_plus")
    ].sort_values(["roi", "rows"], ascending=[False, False])
    frozen_wf = grid[
        grid["evidence_layer"].eq("frozen_live_like_route_price")
        & grid["scope"].eq("expanding_walk_forward")
    ].sort_values(["roi", "rows"], ascending=[False, False])
    frozen_forward_candidate = frozen_forward[
        ~frozen_forward["shadow_variant"].str.startswith("diagnostic")
    ].copy()
    frozen_wf_candidate = frozen_wf[
        ~frozen_wf["shadow_variant"].str.startswith("diagnostic")
    ].copy()
    diagnostic = grid[grid["shadow_variant"].eq("diagnostic_current_gate_prob_disagrees")].copy()
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input": str(INPUT.relative_to(ROOT)),
        "outputs": {
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "grid": str(OUT_GRID.relative_to(ROOT)),
            "daily": str(OUT_DAILY.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "coverage": {
            "rows": int(len(df)),
            "evidence_layers": sorted(df["evidence_layer"].dropna().unique().tolist()),
            "probability_columns": p_cols,
        },
        "verdict": "shadow_only_more_data_needed",
    }
    OUT_SUMMARY.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    cols = [
        "model",
        "shadow_variant",
        "rows",
        "dates",
        "cities",
        "win_rate",
        "avg_ask",
        "avg_cost",
        "roi",
        "roi_ci_low",
        "roi_ci_high",
        "daily_negative_100pct",
    ]
    diagnostic_cols = ["evidence_layer", "scope"] + cols
    OUT_MD.write_text(
        "\n".join(
            [
                "# Regime-Routed NO Probability Shadow Grid V1",
                "",
                "Verdict: `shadow_only_more_data_needed`. 这份报告只比较概率模型 overlay，不改变 live 下单、entry 或 sizing。",
                "",
                "## 结论",
                "",
                "- `frozen_live_like_route_price` 的 6/21+ forward 太薄：候选变体最多 5 笔，CI 全跨 0，不能证明概率模型优于当前 fixed-quality score。",
                "- expanding walk-forward 上，部分概率 overlay 点估比当前 baseline 高，但样本仍只有 15-23 笔，并且有 4-6 个单日 -100% day blocks。",
                "- 现在最合理的用法是：继续把 `p_no_wf_*` 作为 shadow telemetry / size 研究字段，不替换 live score。",
                "",
                "## Shadow Variants",
                "",
                "- `baseline_current_score_gate_size_score`: 当前 fixed-quality score/ask 口径，作为主对照。",
                "- `prob_p_ge_ask_size_p`: 概率模型认为 `P(NO win) >= ask` 才入场，并按概率 size。",
                "- `prob_p_ge_ask_size_current_score`: 用概率做 entry gate，但仍按当前 score size。",
                "- `prob_p_ge_ask_plus05_size_p`: 概率至少高出 ask 5pct 才入场。",
                "- `prob_ratio_ge125_size_p`: 概率/ask 至少 1.25。",
                "- `agreement_current_and_prob_size_score`: 当前 score 和概率模型同时同意才入场。",
                "- `current_gate_size_min_score_p`: 当前 entry 不变，但 size 取当前 score 和概率的较小值。",
                "- `blend50` / `blend70score`: 当前 score 与概率做软融合。",
                "- `diagnostic_current_gate_prob_disagrees`: 当前 score 想买、概率模型反对的 wrong-way detector；只做诊断，不参与候选排序。",
                "",
                "## Frozen Forward Candidates",
                "",
                table(frozen_forward_candidate, cols, limit=20),
                "",
                "## Frozen Expanding Walk-Forward Candidates",
                "",
                table(frozen_wf_candidate, cols, limit=20),
                "",
                "## Current-Strategy Disagreement Diagnostic",
                "",
                "Rows here are current heuristic entries where the probability model says `p_no < ask`. This is a wrong-way detector candidate, not a live veto yet.",
                "",
                table(diagnostic.sort_values(["evidence_layer", "scope", "model"]), diagnostic_cols, limit=24),
                "",
                "## Files",
                "",
                f"- Summary: `{payload['outputs']['summary']}`",
                f"- Grid CSV: `{payload['outputs']['grid']}`",
                f"- Daily CSV: `{payload['outputs']['daily']}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
