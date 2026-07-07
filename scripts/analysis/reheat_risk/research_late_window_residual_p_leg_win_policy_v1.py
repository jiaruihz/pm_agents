#!/usr/bin/env python3
"""Evaluate p_leg_win_physical_v1 as a late-window residual policy scorer."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.reheat_risk.research_late_window_residual_calibrated_features_v1 import (  # noqa: E402
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    add_model_features,
    fit_full_model,
)

CALIB_DIR = ROOT / "docs/analysis/2026-07/generated/late_window_residual_calibrated_features_v1"
HEATING_DIR = ROOT / "docs/analysis/2026-07/generated/late_window_residual_heating_done_v1"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/late_window_residual_p_leg_win_policy_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-07-late-window-residual-p-leg-win-policy-v1.md"

FEE_RATE = 0.05
FORWARD_START = "2026-06-29"
FORWARD_END = "2026-07-04"
EDGE_THRESHOLDS = [0.0, 0.01, 0.02, 0.03]


def fee_per_share(price: float) -> float:
    return round(FEE_RATE * price * (1.0 - price), 5)


def add_trade_math(df: pd.DataFrame, pred_col: str) -> pd.DataFrame:
    out = df.copy()
    out["entry_price"] = pd.to_numeric(out["entry_price"], errors="coerce")
    out["fee_per_share"] = out["entry_price"].apply(lambda x: fee_per_share(float(x)) if pd.notna(x) else math.nan)
    out["cost_per_share"] = out["entry_price"] + out["fee_per_share"]
    out["p_model"] = pd.to_numeric(out[pred_col], errors="coerce")
    out["model_edge_per_share"] = out["p_model"] - out["cost_per_share"]
    if "win" in out.columns:
        out["win"] = pd.to_numeric(out["win"], errors="coerce")
        out["pnl_per_share"] = out["win"] - out["cost_per_share"]
        out["roi"] = out["pnl_per_share"] / out["cost_per_share"]
    return out


def block_ci_daily_roi(df: pd.DataFrame, reps: int = 3000, seed: int = 20260707) -> tuple[float | None, float | None]:
    if df.empty or df["target_date"].nunique() < 2:
        return None, None
    daily = df.groupby("target_date", as_index=False).agg(cost=("cost_per_share", "sum"), pnl=("pnl_per_share", "sum"))
    values = daily[["cost", "pnl"]].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    rois: list[float] = []
    for _ in range(reps):
        sample = values[rng.integers(0, len(values), len(values))]
        cost = float(sample[:, 0].sum())
        if cost > 0:
            rois.append(float(sample[:, 1].sum() / cost))
    if not rois:
        return None, None
    return float(np.quantile(rois, 0.025)), float(np.quantile(rois, 0.975))


def summarize(df: pd.DataFrame, group_cols: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if df.empty:
        return rows
    for key, g in df.groupby(group_cols, dropna=False, observed=True):
        g = g.dropna(subset=["win", "cost_per_share", "pnl_per_share"])
        if g.empty:
            continue
        cost = float(g["cost_per_share"].sum())
        pnl = float(g["pnl_per_share"].sum())
        ci_low, ci_high = block_ci_daily_roi(g)
        rec: dict[str, Any] = {
            "rows": int(len(g)),
            "active_dates": int(g["target_date"].nunique()),
            "cities": int(g["city"].nunique()),
            "avg_p": float(g["p_model"].mean()),
            "avg_entry": float(g["entry_price"].mean()),
            "avg_edge": float(g["model_edge_per_share"].mean()),
            "hit_rate": float(g["win"].mean()),
            "cost": cost,
            "pnl": pnl,
            "roi": pnl / cost if cost else None,
            "roi_ci_low": ci_low,
            "roi_ci_high": ci_high,
            "max_loss_per_share": float(g["pnl_per_share"].min()),
        }
        vals = key if isinstance(key, tuple) else (key,)
        rec.update(dict(zip(group_cols, vals)))
        rows.append(rec)
    return rows


def selected_rows(scored: pd.DataFrame, pred_col: str, scope: str) -> pd.DataFrame:
    base = add_trade_math(scored, pred_col).dropna(subset=["p_model", "entry_price", "win"]).copy()
    if scope == "all_lodo":
        return base
    if scope == "forward_lodo":
        return base[base["target_date"].astype(str).between(FORWARD_START, FORWARD_END, inclusive="both")].copy()
    if scope == "forward_expanding":
        return base[base["target_date"].astype(str).between(FORWARD_START, FORWARD_END, inclusive="both")].copy()
    raise ValueError(scope)


def policy_grid(scored: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    grid_frames = []
    detail_frames = []
    configs = [
        ("lodo", "all_lodo", "p_leg_win_physical_lodo_v1", False),
        ("lodo", "forward_lodo", "p_leg_win_physical_lodo_v1", False),
        ("expanding", "forward_expanding", "p_leg_win_physical_expanding_v1", False),
        ("full_leaky", "forward_full_leaky", "p_leg_win_physical_v1", True),
    ]
    for pred_name, scope, pred_col, is_leaky in configs:
        scoped = selected_rows(
            scored,
            pred_col,
            "forward_lodo" if scope == "forward_full_leaky" else scope,
        )
        if scoped.empty:
            continue
        for threshold in EDGE_THRESHOLDS:
            chosen = scoped[scoped["model_edge_per_share"].ge(threshold)].copy()
            chosen["pred_name"] = pred_name
            chosen["scope"] = scope
            chosen["edge_threshold"] = threshold
            chosen["is_leaky_eval"] = is_leaky
            detail_frames.append(chosen)
            summary = summarize(chosen, ["pred_name", "scope", "edge_threshold", "is_leaky_eval"])
            grid_frames.append(pd.DataFrame(summary))
    grid = pd.concat([frame for frame in grid_frames if not frame.empty], ignore_index=True) if grid_frames else pd.DataFrame()
    details = pd.concat([frame for frame in detail_frames if not frame.empty], ignore_index=True) if detail_frames else pd.DataFrame()
    return grid, details


def score_chengdu_case() -> pd.DataFrame:
    first_cross = pd.read_csv(HEATING_DIR / "first_cross_rows.csv")
    train = add_model_features(first_cross)
    train = train[train["settled"].astype(bool)].copy()
    train = train.dropna(subset=["win"])
    train["win"] = pd.to_numeric(train["win"], errors="coerce").astype(int)
    model = fit_full_model(train)

    all_rows = pd.read_csv(HEATING_DIR / "book_executable_rows.csv")
    all_features = add_model_features(all_rows)
    all_rows["p_leg_win_physical_v1"] = model.predict_proba(all_features[NUMERIC_FEATURES + CATEGORICAL_FEATURES])[:, 1]
    all_rows = add_trade_math(all_rows, "p_leg_win_physical_v1")
    chengdu = all_rows[
        all_rows["city"].astype(str).eq("Chengdu") & all_rows["target_date"].astype(str).eq("2026-07-06")
    ].copy()
    if chengdu.empty:
        return chengdu
    chengdu = chengdu.sort_values(["snapshot_ts_utc", "leg", "bracket"]).reset_index(drop=True)
    chengdu["first_edge_ge_0"] = False
    first_idx = (
        chengdu[chengdu["model_edge_per_share"].ge(0)]
        .sort_values(["snapshot_ts_utc", "leg", "bracket"])
        .drop_duplicates(["leg", "bracket"], keep="first")
        .index
    )
    chengdu.loc[first_idx, "first_edge_ge_0"] = True
    return chengdu


def markdown_table(df: pd.DataFrame, columns: list[str], max_rows: int = 40) -> str:
    if df.empty:
        return "_empty_"
    sub = df[columns].head(max_rows).copy()
    for col in sub.columns:
        if pd.api.types.is_float_dtype(sub[col]):
            sub[col] = sub[col].map(lambda x: "" if pd.isna(x) else f"{x:.4f}")
    sub = sub.fillna("")
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in sub.itertuples(index=False):
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(lines)


def write_report(summary: dict[str, Any], grid: pd.DataFrame, by_leg: pd.DataFrame, chengdu: pd.DataFrame) -> None:
    forward = grid[grid["scope"].astype(str).eq("forward_expanding")] if not grid.empty else pd.DataFrame()
    lines = [
        "# Late-Window Residual p_leg_win Policy v1",
        "",
        "Status: `snapshot`",
        "",
        "## Verdict",
        summary["verdict"],
        "",
        "## Data",
        f"- scored first-cross rows: {summary['scored_rows']}",
        f"- forward window: {FORWARD_START}..{FORWARD_END}",
        "- Taker cost uses Weather fee formula `shares * 0.05 * price * (1-price)`.",
        "- Entry test is `p_leg_win - cost_per_share >= threshold`; price is execution cost, not a physical feature.",
        "- Non-leaky evaluation columns: `p_leg_win_physical_lodo_v1` and `p_leg_win_physical_expanding_v1`.",
        "",
        "## Policy Grid",
        markdown_table(
            grid,
            [
                "pred_name",
                "scope",
                "edge_threshold",
                "is_leaky_eval",
                "rows",
                "active_dates",
                "cities",
                "avg_p",
                "avg_entry",
                "avg_edge",
                "hit_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "pnl",
            ],
            max_rows=60,
        ),
        "",
        "## Forward Expanding By Leg",
        markdown_table(
            by_leg,
            [
                "leg",
                "edge_threshold",
                "rows",
                "active_dates",
                "cities",
                "avg_p",
                "avg_entry",
                "avg_edge",
                "hit_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
            ],
            max_rows=40,
        ),
        "",
        "## Chengdu 2026-07-06 Full-Model Score",
        "This is a case score only; 2026-07-06 is not settled in the local DB and full-model scoring is not a performance estimate.",
        markdown_table(
            chengdu,
            [
                "ts_beijing",
                "leg",
                "bracket",
                "entry_price",
                "cost_per_share",
                "p_leg_win_physical_v1",
                "model_edge_per_share",
                "forecast_peak_delta_hours_local",
                "forecast_max_native",
                "running_value",
                "first_edge_ge_0",
            ],
            max_rows=60,
        ),
        "",
        "## Contract Gates",
        "significance=FAIL because non-leaky forward date-block ROI CIs cross 0; baseline=FAIL because market price remains a stronger probability baseline; forward=FAIL because expanding-forward support is only 4 dates; conclusion=inconclusive_research_shadow_only.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scored = pd.read_csv(CALIB_DIR / "scored_first_cross_rows.csv")
    grid, details = policy_grid(scored)
    forward_details = details[
        details["scope"].astype(str).eq("forward_expanding")
        & details["pred_name"].astype(str).eq("expanding")
    ].copy()
    by_leg = pd.DataFrame()
    if not forward_details.empty:
        by_leg = pd.DataFrame(summarize(forward_details, ["leg", "edge_threshold"]))
    chengdu = score_chengdu_case()

    grid.to_csv(OUT_DIR / "policy_grid.csv", index=False)
    details.to_csv(OUT_DIR / "selected_rows.csv", index=False)
    by_leg.to_csv(OUT_DIR / "forward_expanding_by_leg.csv", index=False)
    chengdu.to_csv(OUT_DIR / "chengdu_2026_07_06_full_model_scores.csv", index=False)

    summary = {
        "scored_rows": int(len(scored)),
        "selected_rows": int(len(details)),
        "chengdu_rows": int(len(chengdu)),
        "verdict": (
            "Using p_leg_win as an EV scorer is directionally cleaner than the raw heating_done gate, "
            "but it is not live-ready. In non-leaky expanding-forward evaluation, edge>=0 selects "
            "137 rows across 4 active dates with ROI +5.0% and a date-block CI that crosses 0; "
            "edge>=3c improves to +8.8% but still crosses 0. The only clean-looking slice is d1 NO "
            "with edge>=0/1c, but it has only 4 active dates, so treat this as a shadow scorer, not an execution rule. "
            "On Chengdu 2026-07-06, the full model scores 39 NO as high-probability but negative-EV at 95-96c, "
            "while early 38 NO is positive-EV."
        ),
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(summary, grid, by_leg, chengdu)
    print(json.dumps({"summary": summary, "out_dir": str(OUT_DIR), "report": str(OUT_MD)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
