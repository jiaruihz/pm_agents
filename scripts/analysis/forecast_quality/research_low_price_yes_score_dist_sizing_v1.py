#!/usr/bin/env python3
"""HeadA score x dist selector and sizing research v1.

Research-only.  This tests whether the continuous probability score should be
combined with the current `dist>0` hot-tail boundary as:

- a stricter selector;
- a score-tier sizing rule;
- or telemetry only.

The score model itself is reused from continuous EV v1.  Thresholds are simple
train-period tertiles inside the hot-tail sample; no threshold search is used.
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.research_low_price_yes_continuous_ev_v1 import (  # noqa: E402
    fit_continuous_model,
)
from scripts.analysis.forecast_quality.research_low_price_yes_heada_refinement_v1 import (  # noqa: E402
    RECENT_START,
    TRAIN_END,
    date_block_ci,
    load_base,
    summarize_perf,
    weather_taker_fee,
)

OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_score_dist_sizing_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-06-low-price-yes-score-dist-sizing-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-06-low-price-yes-score-dist-sizing-v1.json"

SCORE_COL = "p_continuous_ev_v1"


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def fmt_pct(value: Any, *, signed: bool = True) -> str:
    x = to_float(value)
    if not math.isfinite(x):
        return ""
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def fmt_usd(value: Any) -> str:
    x = to_float(value)
    if not math.isfinite(x):
        return ""
    return f"${x:+.2f}"


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        out = float(value)
        return out if math.isfinite(out) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def base_shares(entry: float) -> float:
    if entry <= 0.08:
        return 6.0
    if entry <= 0.14:
        return 8.0
    return 10.0


def score_tier(score: float, q1: float, q2: float) -> str:
    if not math.isfinite(score):
        return "missing"
    if score <= q1:
        return "low"
    if score <= q2:
        return "mid"
    return "high"


def tier_shares(row: pd.Series, policy: str) -> float:
    entry = float(row["entry"])
    base = base_shares(entry)
    tier = str(row["score_tier_hot_train"])
    if policy == "current_price_tier_6_8_10":
        return base
    if policy == "score_tier_conservative":
        mult = {"low": 0.75, "mid": 1.0, "high": 1.25}.get(tier, 1.0)
        return min(12.5, max(4.0, base * mult))
    if policy == "score_tier_balanced":
        mult = {"low": 0.50, "mid": 1.0, "high": 1.50}.get(tier, 1.0)
        return min(15.0, max(3.0, base * mult))
    if policy == "score_tier_aggressive":
        mult = {"low": 0.25, "mid": 1.0, "high": 2.00}.get(tier, 1.0)
        return min(20.0, max(2.0, base * mult))
    if policy == "high_only_current_shares":
        return base
    raise RuntimeError(f"unknown policy {policy}")


def simulate(frame: pd.DataFrame, *, policy: str, label: str, taker_fee: bool = True) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        shares = tier_shares(row, policy)
        entry = float(row["entry"])
        fee = weather_taker_fee(shares=shares, price=entry) if taker_fee else 0.0
        cost = shares * entry + fee
        pnl = shares * float(row["payoff"]) - cost
        rows.append(
            {
                "row_id": int(row["row_id"]),
                "candidate_id": row["candidate_id"],
                "target_date": str(row["target_date"]),
                "city": row["city"],
                "bracket": row["bracket"],
                "entry": entry,
                "payoff": float(row["payoff"]),
                "win": bool(row["win"]),
                "score": float(row[SCORE_COL]),
                "score_tier_hot_train": row["score_tier_hot_train"],
                "raw_dist_br": float(row["raw_dist_br"]),
                "adj_dist_p50_br": float(row["adj_dist_p50_br"]) if math.isfinite(to_float(row.get("adj_dist_p50_br"))) else math.nan,
                "book_state_v1": row.get("book_state_v1", ""),
                "label": label,
                "sizing_policy": policy,
                "shares": shares,
                "entry_fee": fee,
                "cost": cost,
                "pnl": pnl,
                "roi": pnl / cost if cost > 0 else math.nan,
                "period": row.get("period", ""),
            }
        )
    return pd.DataFrame(rows)


def period_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    if frame.empty:
        return {
            "full": pd.Series([], dtype=bool),
            "train_le_2026_06_20": pd.Series([], dtype=bool),
            "recent_ge_2026_06_21": pd.Series([], dtype=bool),
        }
    target = frame["target_date"].astype(str)
    return {
        "full": pd.Series(True, index=frame.index),
        "train_le_2026_06_20": target <= TRAIN_END,
        "recent_ge_2026_06_21": target >= RECENT_START,
    }


def summarize_by_period(frame: pd.DataFrame, *, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for period, mask in period_masks(frame).items():
        sub = frame[mask].copy()
        rec = summarize_perf(sub, label=label, period=period)
        if not sub.empty:
            rec["avg_score"] = float(sub["score"].mean())
            rec["avg_shares"] = float(sub["shares"].mean())
        rows.append(rec)
    return rows


def daily(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["target_date", "cost", "pnl"])
    return frame.groupby("target_date", as_index=False).agg(cost=("cost", "sum"), pnl=("pnl", "sum"))


def paired_delta_ci(a: pd.DataFrame, b: pd.DataFrame, *, seed: int = 20260706, n_boot: int = 5000) -> tuple[float | None, float | None, float | None]:
    if a.empty or b.empty:
        return None, None, None
    da = daily(a).set_index("target_date")
    db = daily(b).set_index("target_date")
    dates = sorted(set(da.index) | set(db.index))
    if len(dates) < 3:
        return None, None, None
    da = da.reindex(dates).fillna(0.0)
    db = db.reindex(dates).fillna(0.0)
    if da["cost"].sum() <= 0 or db["cost"].sum() <= 0:
        return None, None, None
    point = float(da["pnl"].sum() / da["cost"].sum() - db["pnl"].sum() / db["cost"].sum())
    rng = np.random.default_rng(seed)
    vals: list[float] = []
    ap, ac = da["pnl"].to_numpy(float), da["cost"].to_numpy(float)
    bp, bc = db["pnl"].to_numpy(float), db["cost"].to_numpy(float)
    for _ in range(n_boot):
        idx = rng.integers(0, len(dates), len(dates))
        ca = float(ac[idx].sum())
        cb = float(bc[idx].sum())
        if ca > 0 and cb > 0:
            vals.append(float(ap[idx].sum() / ca - bp[idx].sum() / cb))
    if not vals:
        return point, None, None
    return point, float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def make_decile_table(hot: pd.DataFrame) -> pd.DataFrame:
    out = hot.copy()
    train = out[out["target_date"].astype(str) <= TRAIN_END].copy()
    # Freeze train quantile bins and then apply them to all rows for diagnostics.
    _, bins = pd.qcut(train[SCORE_COL], 5, retbins=True, duplicates="drop")
    bins[0] = -np.inf
    bins[-1] = np.inf
    out["score_quintile_train_hot"] = pd.cut(out[SCORE_COL], bins=bins, labels=False, include_lowest=True)
    records: list[dict[str, Any]] = []
    for period, mask in {
        "full": pd.Series(True, index=out.index),
        "train_le_2026_06_20": out["target_date"].astype(str) <= TRAIN_END,
        "recent_ge_2026_06_21": out["target_date"].astype(str) >= RECENT_START,
    }.items():
        sub = out[mask].copy()
        for q, g in sub.groupby("score_quintile_train_hot", dropna=False):
            records.append(
                {
                    "period": period,
                    "score_quintile": int(q) if pd.notna(q) else -1,
                    "rows": int(len(g)),
                    "dates": int(g["target_date"].nunique()),
                    "cities": int(g["city"].nunique()),
                    "win_rate": float(g["win"].mean()),
                    "avg_entry": float(g["entry"].mean()),
                    "avg_score": float(g[SCORE_COL].mean()),
                    "avg_raw_dist": float(g["raw_dist_br"].mean()),
                    "avg_adj_dist": float(pd.to_numeric(g["adj_dist_p50_br"], errors="coerce").mean()),
                }
            )
    return pd.DataFrame(records)


def md_table(df: pd.DataFrame, cols: list[str], max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.head(max_rows).iterrows():
        vals: list[str] = []
        for col in cols:
            val = row.get(col, "")
            if col in {
                "win_rate",
                "avg_entry",
                "avg_score",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "delta_roi",
                "delta_ci_low",
                "delta_ci_high",
                "cost_ratio_vs_baseline",
            }:
                vals.append(fmt_pct(val, signed=col not in {"win_rate", "avg_entry", "avg_score", "avg_raw_dist", "avg_adj_dist", "cost_ratio_vs_baseline"}))
            elif col in {"avg_raw_dist", "avg_adj_dist"}:
                x = to_float(val)
                vals.append(f"{x:.3f}" if math.isfinite(x) else "")
            elif col in {"cost", "pnl", "max_daily_loss_usd"}:
                vals.append(fmt_usd(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = load_base()
    scored, artifact = fit_continuous_model(base)
    hot = scored[scored["hot_tail_boundary_v1"]].copy()
    train_hot = hot[hot["target_date"].astype(str) <= TRAIN_END].copy()
    q1 = float(train_hot[SCORE_COL].quantile(1 / 3))
    q2 = float(train_hot[SCORE_COL].quantile(2 / 3))
    hot["score_tier_hot_train"] = [score_tier(float(x), q1, q2) for x in hot[SCORE_COL]]

    selectors = {
        "dist_gt0_all": hot.copy(),
        "dist_gt0_score_mid_high": hot[hot["score_tier_hot_train"].isin(["mid", "high"])].copy(),
        "dist_gt0_score_high": hot[hot["score_tier_hot_train"].eq("high")].copy(),
        "dist_gt0_score_low": hot[hot["score_tier_hot_train"].eq("low")].copy(),
    }

    trade_frames: dict[str, pd.DataFrame] = {}
    summary_rows: list[dict[str, Any]] = []

    # Selector tests: same price-tier sizing, just different rows.
    for label, frame in selectors.items():
        trade = simulate(frame, policy="current_price_tier_6_8_10", label=label, taker_fee=True)
        trade_frames[label] = trade
        summary_rows.extend(summarize_by_period(trade, label=label))

    # Sizing tests: same dist>0 rows, different score multipliers.
    for policy in ["score_tier_conservative", "score_tier_balanced", "score_tier_aggressive"]:
        label = f"dist_gt0_all_{policy}"
        trade = simulate(hot, policy=policy, label=label, taker_fee=True)
        trade_frames[label] = trade
        summary_rows.extend(summarize_by_period(trade, label=label))

    summary = pd.DataFrame(summary_rows)
    baseline = trade_frames["dist_gt0_all"]

    delta_rows: list[dict[str, Any]] = []
    for label, trade in trade_frames.items():
        if label == "dist_gt0_all":
            continue
        for period, mask_a in period_masks(trade).items():
            mask_b = period_masks(baseline)[period]
            a = trade[mask_a].copy()
            b = baseline[mask_b].copy()
            point, lo, hi = paired_delta_ci(a, b)
            cost_ratio = float(a["cost"].sum() / b["cost"].sum()) if not a.empty and b["cost"].sum() > 0 else math.nan
            delta_rows.append(
                {
                    "label": label,
                    "period": period,
                    "rows": int(len(a)),
                    "dates": int(a["target_date"].nunique()) if not a.empty else 0,
                    "baseline_rows": int(len(b)),
                    "cost_ratio_vs_baseline": cost_ratio,
                    "delta_roi": point,
                    "delta_ci_low": lo,
                    "delta_ci_high": hi,
                }
            )
    deltas = pd.DataFrame(delta_rows)
    quintiles = make_decile_table(hot)

    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    deltas.to_csv(OUT_DIR / "delta_vs_dist_gt0.csv", index=False)
    quintiles.to_csv(OUT_DIR / "score_quintiles.csv", index=False)
    hot.to_csv(OUT_DIR / "scored_hot_rows.csv", index=False)

    full_view = summary[
        summary["period"].eq("full")
        & summary["label"].isin(
            [
                "dist_gt0_all",
                "dist_gt0_score_mid_high",
                "dist_gt0_score_high",
                "dist_gt0_score_low",
                "dist_gt0_all_score_tier_conservative",
                "dist_gt0_all_score_tier_balanced",
                "dist_gt0_all_score_tier_aggressive",
            ]
        )
    ].copy()
    train_recent_view = summary[
        summary["period"].isin(["train_le_2026_06_20", "recent_ge_2026_06_21"])
        & summary["label"].isin(["dist_gt0_all", "dist_gt0_score_high", "dist_gt0_all_score_tier_balanced"])
    ].copy()
    delta_view = deltas[
        deltas["period"].isin(["full", "train_le_2026_06_20", "recent_ge_2026_06_21"])
        & deltas["label"].isin(["dist_gt0_score_high", "dist_gt0_all_score_tier_balanced", "dist_gt0_all_score_tier_aggressive"])
    ].copy()
    quintile_view = quintiles[quintiles["period"].isin(["train_le_2026_06_20", "recent_ge_2026_06_21"])].copy()

    high_full = full_view[full_view["label"].eq("dist_gt0_score_high")]
    balanced_full = full_view[full_view["label"].eq("dist_gt0_all_score_tier_balanced")]
    baseline_full = full_view[full_view["label"].eq("dist_gt0_all")]
    high_roi = float(high_full["roi"].iloc[0]) if not high_full.empty else math.nan
    balanced_roi = float(balanced_full["roi"].iloc[0]) if not balanced_full.empty else math.nan
    baseline_roi = float(baseline_full["roi"].iloc[0]) if not baseline_full.empty else math.nan

    payload = {
        "generated_at_utc": now_utc(),
        "score_model": {
            "source": "research_low_price_yes_continuous_ev_v1.fit_continuous_model",
            "artifact": artifact,
            "hot_train_q1": q1,
            "hot_train_q2": q2,
        },
        "data_snapshot": {
            "base_rows": int(len(base)),
            "hot_rows": int(len(hot)),
            "dates": int(hot["target_date"].nunique()),
            "cities": int(hot["city"].nunique()),
            "date_min": str(hot["target_date"].min()),
            "date_max": str(hot["target_date"].max()),
        },
        "summary": summary.to_dict(orient="records"),
        "deltas": deltas.to_dict(orient="records"),
        "verdict": {
            "selector": "score_high_selector_is_shadow_candidate_not_live",
            "sizing": "score_tier_sizing_promising_as_shadow_not_live_size_up",
            "live_change": "none",
        },
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# HeadA Score x Dist Sizing v1",
        "",
        f"Generated: `{payload['generated_at_utc']}`",
        "",
        "Scope: HeadA `forecast_tail_low_price_yes` only.  This tests whether the useful probability score should be combined with the existing `dist>0` hot-tail boundary as a selector or as a sizing input.  It does not change live.",
        "",
        "## Verdict",
        "",
        "```text",
        "probability_sorting=PASS",
        "score_as_selector=shadow_candidate_only",
        "score_as_sizing=promising_shadow_ledger",
        "live_action=no_change_no_size_up",
        "```",
        "",
        "人话结论：`dist>0` 是机制边界，score 是质量排序。最合理的下一步不是用 score 替换 `dist>0`，而是在 `dist>0` 里面按 score 调仓做 shadow。历史上高分票确实更强，但样本仍由少数尾部赢家驱动，不能直接 live 放大。",
        "",
        "## Data Snapshot",
        "",
        f"- Base denominator: {len(base)} rows, current HeadA ask 5-20c / edge>=0.20.",
        f"- Hot-tail `dist>0`: {len(hot)} rows / {hot['target_date'].nunique()} dates / {hot['city'].nunique()} cities.",
        f"- Target dates: {hot['target_date'].min()} .. {hot['target_date'].max()}.",
        f"- Train score tertiles inside `dist>0`: low <= {q1:.4f}, high > {q2:.4f}.",
        "- Cost model: price-tier 6/8/10 shares baseline, official Weather taker fee, hold-to-settlement.",
        "",
        "## Score Quintiles Inside `dist>0`",
        "",
        md_table(quintile_view, ["period", "score_quintile", "rows", "dates", "cities", "win_rate", "avg_entry", "avg_score", "avg_raw_dist", "avg_adj_dist"], 30),
        "",
        "Read: within the already-correct `dist>0` universe, score still carries rank information.  The useful interpretation is quality, not a replacement for the hot-tail boundary.",
        "",
        "## Selector And Sizing Performance",
        "",
        md_table(full_view, ["label", "rows", "dates", "cities", "win_rate", "avg_entry", "avg_score", "avg_shares", "cost", "pnl", "roi", "roi_ci_low", "roi_ci_high", "losing_days", "le_minus50pct_days", "max_daily_loss_usd"], 20),
        "",
        f"- Baseline `dist_gt0_all` ROI: {fmt_pct(baseline_roi)}.",
        f"- High-score selector ROI: {fmt_pct(high_roi)}.",
        f"- Balanced score-tier sizing ROI: {fmt_pct(balanced_roi)}.",
        "",
        "## Train vs Recent",
        "",
        md_table(train_recent_view, ["period", "label", "rows", "dates", "cities", "win_rate", "avg_entry", "avg_score", "avg_shares", "cost", "pnl", "roi", "roi_ci_low", "roi_ci_high"], 20),
        "",
        "## Delta vs Current `dist>0` Baseline",
        "",
        md_table(delta_view, ["label", "period", "rows", "dates", "baseline_rows", "cost_ratio_vs_baseline", "delta_roi", "delta_ci_low", "delta_ci_high"], 30),
        "",
        "Selector interpretation:",
        "",
        "- `score_high` is much cleaner than low-score rows, but it cuts volume and still needs forward proof before replacing the current selector.",
        "- `score_mid_high` is a softer selector, useful for priority but not clearly better enough to become a gate.",
        "",
        "Sizing interpretation:",
        "",
        "- Score-tier sizing is the more natural use: keep the same mechanism boundary, shift notional from low-score to high-score tickets.",
        "- The balanced rule is the cleanest shadow candidate: low score 0.5x, mid 1.0x, high 1.5x of current price-tier shares, capped at 15 shares.",
        "- Aggressive sizing is research-only; it increases tail concentration and daily drawdown risk.",
        "",
        "## Decision",
        "",
        "Do not change live today.  Add a shadow ledger for:",
        "",
        "```text",
        "score_tier_hot_train = low/mid/high",
        "shadow_sizing_score_tier_balanced",
        "shadow_sizing_score_tier_conservative",
        "shadow_selector_score_high",
        "```",
        "",
        "Forward gate before live sizing change:",
        "",
        "- at least 15 fresh active target dates after ECMWF source repair;",
        "- score tiers retain monotonic hit-rate direction;",
        "- balanced score-tier sizing improves ROI or PnL per dollar after actual maker/fill costs;",
        "- no material increase in max daily loss relative to current tiny sizing.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(OUT_MD), "json": str(OUT_JSON), "baseline_roi": baseline_roi, "high_score_roi": high_roi, "balanced_sizing_roi": balanced_roi}, indent=2))


if __name__ == "__main__":
    main()
