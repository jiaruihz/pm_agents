#!/usr/bin/env python3
"""Build the base HeadA source-quality mechanism score.

The score is intentionally hand-built from mechanism components instead of
fitted to PnL:

- city/source predictability from as-of best model MAE;
- active source gap to the as-of best model;
- directional hot-underforecast bias;
- high active-source MAE noise penalty.

This tests whether the score is a reasonable telemetry / sizing feature for the
running HeadA forecast-tail low-price YES sleeve.  It does not change live.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
IN_ROWS = ROOT / "docs/analysis/2026-07/generated/low_price_yes_forecast_source_calibration_v1/candidate_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_source_quality_score_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/generated/low_price_yes_source_quality_score_v1/report.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/generated/low_price_yes_source_quality_score_v1/report.json"

TRAIN_END = "2026-06-20"
RECENT_START = "2026-06-21"
N_BOOT = 5000
RNG_SEED = 20260708


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


def fmt_float(value: Any, digits: int = 2) -> str:
    x = to_float(value)
    if not math.isfinite(x):
        return ""
    return f"{x:.{digits}f}"


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


def daily(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["target_date", "rows", "cost", "pnl", "roi"])
    out = frame.groupby("target_date", as_index=False).agg(
        rows=("row_id", "count"),
        cost=("cost", "sum"),
        pnl=("pnl", "sum"),
    )
    out["roi"] = out["pnl"] / out["cost"]
    return out


def date_block_ci(frame: pd.DataFrame) -> tuple[float | None, float | None]:
    d = daily(frame)
    if len(d) < 3:
        return None, None
    rng = np.random.default_rng(RNG_SEED)
    pnl = d["pnl"].to_numpy(float)
    cost = d["cost"].to_numpy(float)
    vals: list[float] = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(d), len(d))
        c = float(cost[idx].sum())
        if c > 0:
            vals.append(float(pnl[idx].sum() / c))
    if not vals:
        return None, None
    arr = np.asarray(vals, dtype=float)
    return float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))


def paired_delta_ci(a: pd.DataFrame, b: pd.DataFrame) -> tuple[float | None, float | None, float | None]:
    if a.empty or b.empty:
        return None, None, None
    da = daily(a).set_index("target_date")
    db = daily(b).set_index("target_date")
    dates = sorted(set(da.index) | set(db.index))
    if len(dates) < 3:
        return None, None, None
    da = da.reindex(dates).fillna(0.0)
    db = db.reindex(dates).fillna(0.0)
    if float(da["cost"].sum()) <= 0 or float(db["cost"].sum()) <= 0:
        return None, None, None
    point = float(da["pnl"].sum() / da["cost"].sum() - db["pnl"].sum() / db["cost"].sum())
    rng = np.random.default_rng(RNG_SEED)
    ap, ac = da["pnl"].to_numpy(float), da["cost"].to_numpy(float)
    bp, bc = db["pnl"].to_numpy(float), db["cost"].to_numpy(float)
    vals: list[float] = []
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(dates), len(dates))
        ca = float(ac[idx].sum())
        cb = float(bc[idx].sum())
        if ca > 0 and cb > 0:
            vals.append(float(ap[idx].sum() / ca - bp[idx].sum() / cb))
    if not vals:
        return point, None, None
    arr = np.asarray(vals, dtype=float)
    return point, float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))


def top_removed_roi(frame: pd.DataFrame, n: int = 5) -> float:
    if len(frame) <= n:
        return math.nan
    sub = frame.sort_values("pnl", ascending=False).iloc[n:]
    cost = float(sub["cost"].sum())
    return float(sub["pnl"].sum() / cost) if cost > 0 else math.nan


def summarize(frame: pd.DataFrame, *, label: str, period: str) -> dict[str, Any]:
    if frame.empty:
        return {"label": label, "period": period, "rows": 0}
    d = daily(frame)
    cost = float(frame["cost"].sum())
    pnl = float(frame["pnl"].sum())
    lo, hi = date_block_ci(frame)
    return {
        "label": label,
        "period": period,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "win_rate": float(frame["win"].mean()),
        "avg_entry": float(frame["entry"].mean()),
        "avg_source_quality_score": float(frame["source_quality_score_v1"].mean())
        if "source_quality_score_v1" in frame
        else math.nan,
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost > 0 else math.nan,
        "roi_ci_low": lo,
        "roi_ci_high": hi,
        "top5_removed_roi": top_removed_roi(frame, 5),
        "losing_days": int((d["pnl"] < 0).sum()),
        "le_minus50pct_days": int((d["roi"] <= -0.5).sum()),
        "max_daily_loss_usd": float(d["pnl"].min()) if not d.empty else math.nan,
        "max_daily_loss_roi": float(d["roi"].min()) if not d.empty else math.nan,
    }


def periods(frame: pd.DataFrame) -> dict[str, pd.Series]:
    target = frame["target_date"].astype(str)
    return {
        "full": pd.Series(True, index=frame.index),
        "train_le_2026_06_20": target <= TRAIN_END,
        "recent_ge_2026_06_21": target >= RECENT_START,
    }


def add_source_quality_score(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in [
        "best_mae_f",
        "source_gap_to_best_f",
        "source_bias_f",
        "source_mae_f",
        "source_underforecast_ge_1f_pct",
    ]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    rel = out["best_reliability_bucket"].astype(str)
    out["sq_predictability_component"] = np.select(
        [
            rel.isin(["A_<=1.25F", "B_1.25-2F"]),
            rel.eq("C_2-2.5F"),
            rel.eq("D_>2.5F"),
        ],
        [1.0, 0.0, -1.0],
        default=0.0,
    )
    out["sq_source_gap_component"] = np.select(
        [
            out["source_gap_to_best_f"].le(0.5),
            out["source_gap_to_best_f"].le(1.0),
            out["source_gap_to_best_f"].gt(1.0),
        ],
        [1.0, 0.5, -1.0],
        default=0.0,
    )
    hot_direction = out["source_bias_f"].gt(0.0) & out["source_underforecast_ge_1f_pct"].ge(55.0)
    strong_hot_direction = out["source_bias_f"].gt(0.0) & out["source_underforecast_ge_1f_pct"].ge(65.0)
    cold_or_random = out["source_bias_f"].le(0.0) | out["source_underforecast_ge_1f_pct"].lt(45.0)
    out["sq_hot_direction_component"] = np.select(
        [strong_hot_direction, hot_direction, cold_or_random],
        [1.25, 1.0, -1.0],
        default=0.0,
    )
    out["sq_noise_component"] = np.select(
        [
            out["source_mae_f"].gt(2.5),
            out["source_mae_f"].le(2.0),
        ],
        [-1.0, 0.5],
        default=0.0,
    )
    out["source_quality_score_v1"] = (
        out["sq_predictability_component"]
        + out["sq_source_gap_component"]
        + out["sq_hot_direction_component"]
        + out["sq_noise_component"]
    )
    out["source_quality_tier_v1"] = np.select(
        [
            out["source_quality_score_v1"].ge(3.0),
            out["source_quality_score_v1"].ge(1.5),
            out["source_quality_score_v1"].ge(0.0),
        ],
        ["high", "mid", "neutral"],
        default="low",
    )
    out["source_quality_downweight_candidate_v1"] = out["source_quality_score_v1"].lt(0.0)
    out["source_quality_confidence_candidate_v1"] = out["source_quality_score_v1"].ge(1.5)
    return out


def selector_summary(rows: pd.DataFrame) -> pd.DataFrame:
    selectors = {
        "baseline_hot_dist_gt0": pd.Series(True, index=rows.index),
        "source_quality_high": rows["source_quality_tier_v1"].eq("high"),
        "source_quality_mid_or_high": rows["source_quality_tier_v1"].isin(["mid", "high"]),
        "source_quality_low": rows["source_quality_tier_v1"].eq("low"),
        "exclude_source_quality_low": ~rows["source_quality_tier_v1"].eq("low"),
        "source_quality_confidence_candidate": rows["source_quality_confidence_candidate_v1"],
        "source_quality_downweight_candidate": rows["source_quality_downweight_candidate_v1"],
        "exclude_downweight_candidate": ~rows["source_quality_downweight_candidate_v1"],
    }
    records: list[dict[str, Any]] = []
    for period, pmask in periods(rows).items():
        period_rows = rows[pmask].copy()
        for label, mask in selectors.items():
            selected = period_rows[mask.reindex(period_rows.index).fillna(False)].copy()
            rec = summarize(selected, label=label, period=period)
            if label != "baseline_hot_dist_gt0":
                complement = period_rows.drop(index=selected.index)
                point, lo, hi = paired_delta_ci(selected, complement)
                rec.update(
                    {
                        "complement_rows": int(len(complement)),
                        "selected_share": float(len(selected) / len(period_rows)) if len(period_rows) else math.nan,
                        "delta_vs_complement": point,
                        "delta_vs_complement_ci_low": lo,
                        "delta_vs_complement_ci_high": hi,
                    }
                )
            records.append(rec)
    return pd.DataFrame(records)


def group_summary(rows: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for period, pmask in periods(rows).items():
        period_rows = rows[pmask].copy()
        for key, group in period_rows.groupby(group_cols, dropna=False):
            if not isinstance(key, tuple):
                key = (key,)
            rec = summarize(group.copy(), label="|".join(str(x) for x in key), period=period)
            for col, val in zip(group_cols, key, strict=True):
                rec[col] = val
            records.append(rec)
    return pd.DataFrame(records)


def daily_rank_correlation(rows: pd.DataFrame) -> dict[str, Any]:
    by_day: list[dict[str, Any]] = []
    for target_date, group in rows.groupby("target_date"):
        if len(group) < 3 or group["source_quality_score_v1"].nunique() < 2 or group["win"].nunique() < 2:
            continue
        corr = group["source_quality_score_v1"].corr(group["win"].astype(float), method="spearman")
        if math.isfinite(to_float(corr)):
            by_day.append({"target_date": target_date, "spearman_score_win": float(corr), "rows": int(len(group))})
    if not by_day:
        return {"days": 0, "mean_spearman_score_win": None}
    vals = np.asarray([r["spearman_score_win"] for r in by_day], dtype=float)
    return {
        "days": int(len(by_day)),
        "mean_spearman_score_win": float(vals.mean()),
        "median_spearman_score_win": float(np.median(vals)),
        "positive_days": int((vals > 0).sum()),
        "negative_days": int((vals < 0).sum()),
    }


def markdown_table(frame: pd.DataFrame, columns: list[str], *, max_rows: int = 30) -> str:
    if frame.empty:
        return "_empty_"
    data = frame.loc[:, columns].head(max_rows).copy()
    pct_cols = {
        "win_rate",
        "roi",
        "roi_ci_low",
        "roi_ci_high",
        "top5_removed_roi",
        "selected_share",
        "delta_vs_complement",
        "delta_vs_complement_ci_low",
        "delta_vs_complement_ci_high",
        "max_daily_loss_roi",
    }
    for col in data.columns:
        if col in pct_cols:
            data[col] = data[col].map(lambda x: fmt_pct(x))
        elif col.startswith("avg_") or col.endswith("_f") or col in {"cost", "pnl", "max_daily_loss_usd"}:
            data[col] = data[col].map(lambda x: fmt_float(x, 2))
    rows = [[str(c) for c in data.columns], ["---" for _ in data.columns]]
    for _, rec in data.iterrows():
        rows.append([str(rec[col]) for col in data.columns])
    return "\n".join("| " + " | ".join(row) + " |" for row in rows)


def build_report(
    rows: pd.DataFrame,
    selectors: pd.DataFrame,
    groups: pd.DataFrame,
    payload: dict[str, Any],
) -> str:
    full = selectors[selectors["period"].eq("full")]
    train = selectors[selectors["period"].eq("train_le_2026_06_20")]
    recent = selectors[selectors["period"].eq("recent_ge_2026_06_21")]
    tier_full = groups[groups["period"].eq("full") & groups["grouping"].eq("source_quality_tier_v1")]
    score_full = groups[groups["period"].eq("full") & groups["grouping"].eq("source_quality_score_v1")]
    components = groups[groups["period"].eq("full") & groups["grouping"].eq("sq_predictability_component|sq_hot_direction_component|sq_noise_component")]

    return f"""# HeadA Source-Quality Score v1

Generated: {payload["generated_at_utc"]}

## Question

Is a mechanism-based `source_quality_score` a reasonable observation feature for HeadA, beyond raw ABCD buckets?

## Score Definition

No fitted coefficients, no threshold search:

```text
source_quality_score_v1 =
  predictability_component(best model MAE bucket)
  + source_gap_component(active source MAE - best MAE)
  + hot_direction_component(actual > forecast rate / bias)
  + noise_component(active source MAE)
```

High is `>=3.0`, mid is `1.5..3.0`, neutral is `0..1.5`, low is `<0`.

## Bottom Line

Verdict: `{payload["verdict"]}`.

This is a **reasonable telemetry/confidence feature**, not a live selector yet. It has the right mechanism shape: low score rows are bad, and high/mid rows have better point estimates. But recent support is small and noisy, so the correct live use is forward observation and maybe future sizing input, not hard filtering.

Daily rank check: {payload["daily_rank_correlation"]["days"]} eligible days, mean Spearman(score, win) {fmt_float(payload["daily_rank_correlation"].get("mean_spearman_score_win"), 3)}, positive days {payload["daily_rank_correlation"].get("positive_days")}, negative days {payload["daily_rank_correlation"].get("negative_days")}.

## Selector A/B

{markdown_table(full, [
    "label", "rows", "dates", "cities", "win_rate", "avg_entry", "avg_source_quality_score", "roi",
    "roi_ci_low", "roi_ci_high", "top5_removed_roi", "selected_share", "delta_vs_complement",
    "delta_vs_complement_ci_low", "delta_vs_complement_ci_high",
])}

## Train / Recent

Train:

{markdown_table(train, [
    "label", "rows", "win_rate", "avg_source_quality_score", "roi", "roi_ci_low", "roi_ci_high",
    "delta_vs_complement", "delta_vs_complement_ci_low", "delta_vs_complement_ci_high",
])}

Recent:

{markdown_table(recent, [
    "label", "rows", "win_rate", "avg_source_quality_score", "roi", "roi_ci_low", "roi_ci_high",
    "delta_vs_complement", "delta_vs_complement_ci_low", "delta_vs_complement_ci_high",
])}

## Tier View

{markdown_table(tier_full.sort_values("source_quality_tier_v1"), [
    "source_quality_tier_v1", "rows", "dates", "cities", "win_rate", "avg_source_quality_score",
    "roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi",
])}

## Raw Score View

{markdown_table(score_full.sort_values("source_quality_score_v1"), [
    "source_quality_score_v1", "rows", "dates", "cities", "win_rate", "roi", "roi_ci_low", "roi_ci_high",
], max_rows=40)}

## Component Interaction

{markdown_table(components.sort_values(["sq_predictability_component", "sq_hot_direction_component", "sq_noise_component"]), [
    "sq_predictability_component", "sq_hot_direction_component", "sq_noise_component",
    "rows", "cities", "win_rate", "roi", "roi_ci_low", "roi_ci_high",
], max_rows=60)}

## Decision

Keep initial HeadA live unchanged. Add/keep these fields as telemetry. The first practical use should be `no size-up when source_quality_score_v1 < 0` and `allow normal/high confidence only when score >= 1.5`, but that should wait for fresh forward rows.
"""


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = pd.read_csv(IN_ROWS, low_memory=False)
    rows = add_source_quality_score(rows)
    selectors = selector_summary(rows)
    group_frames: list[pd.DataFrame] = []
    for cols in [
        ["source_quality_tier_v1"],
        ["source_quality_score_v1"],
        ["sq_predictability_component", "sq_hot_direction_component", "sq_noise_component"],
        ["source_quality_tier_v1", "best_reliability_bucket"],
    ]:
        g = group_summary(rows, cols)
        g["grouping"] = "|".join(cols)
        group_frames.append(g)
    groups = pd.concat(group_frames, ignore_index=True)

    rows.to_csv(OUT_DIR / "candidate_rows.csv", index=False)
    selectors.to_csv(OUT_DIR / "selector_summary.csv", index=False)
    groups.to_csv(OUT_DIR / "group_summary.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "input_rows": str(IN_ROWS.relative_to(ROOT)),
        "denominator": {
            "rows": int(len(rows)),
            "dates": int(rows["target_date"].nunique()),
            "cities": int(rows["city"].nunique()),
            "start": str(rows["target_date"].min()),
            "end": str(rows["target_date"].max()),
        },
        "daily_rank_correlation": daily_rank_correlation(rows),
        "verdict": "shadow_telemetry_reasonable_not_live_selector",
        "key_selectors": selectors[
            selectors["period"].eq("full")
            & selectors["label"].isin(
                [
                    "baseline_hot_dist_gt0",
                    "source_quality_mid_or_high",
                    "source_quality_low",
                    "exclude_source_quality_low",
                    "source_quality_confidence_candidate",
                    "source_quality_downweight_candidate",
                ]
            )
        ].to_dict(orient="records"),
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(build_report(rows, selectors, groups, payload), encoding="utf-8")
    print(json.dumps(json_ready(payload), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
