#!/usr/bin/env python3
"""Segment current-YES no-reheat state slices by city and market timing.

This is a follow-on to no_reheat_state_slices_v1.  It does not train a model.
It asks whether broad fixed states hide a smaller family of cities, local
hours, forecast gaps, or market-repricing windows where future-break hazard is
already below the YES ask implied hazard.
"""

from __future__ import annotations

import argparse
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

from scripts.analysis.reheat_risk import research_current_yes_no_reheat_state_slices_v1 as base
from weather_data_feed.city_family import CITY_FAMILY_CURRENT_BRACKET_NO_V1 as CITY_FAMILY

OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_yes_no_reheat_segment_breakdown_v1"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-24-current-yes-no-reheat-segment-breakdown-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-yes-no-reheat-segment-breakdown-v1.md"

HOLDOUT_START = base.HOLDOUT_START
SEED = 20260624

FOCUS_STATES = [
    "all_current_yes_tradable",
    "stalled_high_ge2obs",
    "stalled_no_warming",
    "fade_all_decline_ge_0_5",
    "strict_no_reheat_candidate",
]

FOCUS_BUCKETS = [
    ("all_ask_35_97", 0.35, 0.97),
    ("ask_35_50", 0.35, 0.50),
    ("ask_50_70", 0.50, 0.70),
    ("ask_70_90", 0.70, 0.90),
    ("ask_90_97", 0.90, 0.97),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--feature-rows", default=str(base.FEATURE_ROWS))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--out-json", default=str(OUT_JSON))
    parser.add_argument("--out-md", default=str(OUT_MD))
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        out = float(value)
        return None if not math.isfinite(out) else out
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def pct(value: Any, digits: int = 1) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f * 100:+.{digits}f}%"


def num(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f:.{digits}f}"


def add_segment_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["city_family"] = out["city"].map(CITY_FAMILY).fillna("other")
    hour = pd.to_numeric(out["decision_hour_local"], errors="coerce")
    out["local_hour_bin"] = pd.cut(
        hour,
        bins=[9.999, 13, 15, 16.5, 18, 21.001],
        labels=["h10_13", "h13_15", "h15_1630", "h1630_18", "h18_21"],
    ).astype(str)

    max_gap = pd.to_numeric(out["max_forecast_gap_to_running_native"], errors="coerce")
    out["forecast_gap_bin"] = pd.cut(
        max_gap,
        bins=[-np.inf, -1.0, 0.5, 2.0, np.inf],
        labels=["forecast_below_running_ge1", "forecast_near_or_below", "forecast_above_0p5_2", "forecast_above_gt2"],
    ).astype(str)

    min_peak_delta = pd.to_numeric(out["min_forecast_peak_delta_hours_local"], errors="coerce")
    out["forecast_peak_clock_bin"] = pd.cut(
        min_peak_delta,
        bins=[-np.inf, -1.0, 0.5, 2.0, np.inf],
        labels=["forecast_peak_past", "forecast_peak_now", "forecast_peak_soon", "forecast_peak_later"],
    ).astype(str)

    out = out.sort_values(["city", "target_date", "current_bracket", "decision_snapshot_ts_utc"]).copy()
    key = ["city", "target_date", "current_bracket"]
    out["first_current_yes_ask"] = out.groupby(key)["current_yes_ask"].transform("first")
    out["min_prior_current_yes_ask"] = (
        out.groupby(key)["current_yes_ask"].expanding().min().reset_index(level=key, drop=True)
    )
    out["ask_delta_from_first"] = out["current_yes_ask"] - out["first_current_yes_ask"]
    out["ask_delta_from_min_prior"] = out["current_yes_ask"] - out["min_prior_current_yes_ask"]
    out["market_reprice_bin"] = np.select(
        [
            out["current_yes_ask"].lt(0.50) & out["ask_delta_from_min_prior"].lt(0.10),
            out["current_yes_ask"].lt(0.70) & out["ask_delta_from_min_prior"].lt(0.15),
            out["current_yes_ask"].ge(0.70) & out["ask_delta_from_min_prior"].lt(0.15),
            out["ask_delta_from_min_prior"].ge(0.15),
        ],
        ["cheap_unrepriced", "mid_unrepriced", "high_but_not_moved", "already_repriced_up"],
        default="other",
    )
    return out


def date_bootstrap_roi(frame: pd.DataFrame, reps: int = 3000) -> list[float | None]:
    by_date = frame.groupby("target_date")[["current_yes_ask", "trade_pnl_per_share"]].sum()
    if by_date.shape[0] < 2:
        return [None, None]
    rng = np.random.default_rng(SEED)
    vals = by_date.to_numpy(dtype=float)
    out: list[float] = []
    for _ in range(reps):
        sample = vals[rng.integers(0, len(vals), size=len(vals))]
        cost = float(sample[:, 0].sum())
        if cost:
            out.append(float(sample[:, 1].sum() / cost))
    if not out:
        return [None, None]
    arr = np.asarray(out)
    return [float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))]


def summarize(frame: pd.DataFrame, baseline: pd.DataFrame, segment_kind: str, segment_value: str, state: str, bucket: str) -> dict[str, Any]:
    cost = float(frame["current_yes_ask"].sum())
    pnl = float(frame["trade_pnl_per_share"].sum())
    b_cost = float(baseline["current_yes_ask"].sum()) if len(baseline) else 0.0
    b_pnl = float(baseline["trade_pnl_per_share"].sum()) if len(baseline) else 0.0
    roi = pnl / cost if cost else None
    baseline_roi = b_pnl / b_cost if b_cost else None
    return {
        "segment_kind": segment_kind,
        "segment_value": segment_value,
        "state": state,
        "price_bucket": bucket,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "cost": cost,
        "pnl": pnl,
        "roi": roi,
        "roi_ci95": date_bootstrap_roi(frame),
        "same_segment_price_baseline_rows": int(len(baseline)),
        "same_segment_price_baseline_roi": baseline_roi,
        "excess_roi": roi - baseline_roi if roi is not None and baseline_roi is not None else None,
        "win_rate": float(frame["label_survive"].mean()) if len(frame) else None,
        "future_break_rate": float(frame["label_future_break"].mean()) if len(frame) else None,
        "avg_ask": float(frame["current_yes_ask"].mean()) if len(frame) else None,
        "avg_ask_delta_from_min_prior": float(frame["ask_delta_from_min_prior"].mean()) if len(frame) else None,
        "avg_minutes_since_max": float(pd.to_numeric(frame["minutes_since_running_max"], errors="coerce").mean()) if len(frame) else None,
        "avg_forecast_gap_to_running": float(pd.to_numeric(frame["max_forecast_gap_to_running_native"], errors="coerce").mean()) if len(frame) else None,
    }


def bucket_mask(df: pd.DataFrame, low: float, high: float) -> pd.Series:
    ask = pd.to_numeric(df["current_yes_ask"], errors="coerce")
    return ask.ge(low) & ask.lt(high)


def build_breakdowns(tradable: pd.DataFrame) -> pd.DataFrame:
    holdout = tradable[tradable["period"] == "holdout"].copy()
    masks = base.state_masks(holdout)
    segment_cols = ["city", "city_family", "local_hour_bin", "forecast_gap_bin", "forecast_peak_clock_bin", "market_reprice_bin"]
    rows: list[dict[str, Any]] = []
    for segment_col in segment_cols:
        for segment_value, segment_frame in holdout.groupby(segment_col, dropna=False):
            segment_value_str = str(segment_value)
            for bucket, low, high in FOCUS_BUCKETS:
                baseline = segment_frame[bucket_mask(segment_frame, low, high)]
                if baseline.empty:
                    continue
                for state in FOCUS_STATES:
                    mask = masks[state].reindex(segment_frame.index, fill_value=False)
                    sub = segment_frame[mask & bucket_mask(segment_frame, low, high)]
                    if sub.empty:
                        continue
                    rows.append(summarize(sub, baseline, segment_col, segment_value_str, state, bucket))
    return pd.DataFrame(rows)


def render_table(rows: list[dict[str, Any]], limit: int = 12) -> list[str]:
    lines = [
        "| segment | state | bucket | rows | dates | cities | break | avg ask | ROI | CI | same-seg baseline | excess |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows[:limit]:
        lines.append(
            "| {seg} | {state} | {bucket} | {rows} | {dates} | {cities} | {break_rate} | {ask} | {roi} | {ci} | {base_roi} | {excess} |".format(
                seg=f"{row['segment_kind']}={row['segment_value']}",
                state=row["state"],
                bucket=row["price_bucket"],
                rows=row["rows"],
                dates=row["dates"],
                cities=row["cities"],
                break_rate=pct(row["future_break_rate"]),
                ask=num(row["avg_ask"]),
                roi=pct(row["roi"]),
                ci=f"[{pct(row['roi_ci95'][0])}, {pct(row['roi_ci95'][1])}]",
                base_roi=pct(row["same_segment_price_baseline_roi"]),
                excess=pct(row["excess_roi"]),
            )
        )
    return lines


def build_markdown(payload: dict[str, Any], out_md: Path) -> None:
    lines = [
        "# Current-YES No-Reheat Segment Breakdown v1",
        "",
        "Status: research-only",
        f"Generated: {payload['generated_at_utc']}",
        "",
        "## 一句话结论",
        "",
        payload["headline"],
        "",
        "## 数据范围",
        "",
        f"- Feature rows: `{payload['inputs']['feature_rows']}`",
        f"- Feature target-date range: `{payload['data_snapshot']['feature_min_target_date']}`..`{payload['data_snapshot']['feature_max_target_date']}`",
        f"- Holdout: `{HOLDOUT_START}`..`{payload['data_snapshot']['feature_max_target_date']}`",
        f"- Tradable holdout rows: {payload['data_snapshot']['holdout_tradable_rows']} rows / {payload['data_snapshot']['holdout_dates']} dates / {payload['data_snapshot']['holdout_cities']} cities",
        "",
        "## 物理族群横切",
        "",
        *render_table(payload["family_highlights"], limit=12),
        "",
        "## 城市横切",
        "",
        *render_table(payload["city_highlights"], limit=16),
        "",
        "## 时间 / Forecast / Market Repricing 横切",
        "",
        *render_table(payload["timing_highlights"], limit=18),
        "",
        "## 解释",
        "",
        "- 城市族群有差异，但没有出现能直接 live 的稳定族群：很多正 ROI 是低价样本给出的点估计，日期 CI 仍跨 0。",
        "- 真正拖累不是“天气完全没信号”，而是 future-break hazard 下降得不够快；当 hazard 足够低时，YES ask 通常也已高。",
        "- `cheap_unrepriced` / `mid_unrepriced` 是下一步该盯的 market-lag transition；`high` 或 `already_repriced_up` 更像已被盘口吃掉。",
        "",
        "## Verdict",
        "",
        "significance=FAIL / baseline=FAIL / forward=NA / conclusion=inconclusive",
        "",
        "这些横切支持继续研究 no-reheat market-lag，但不支持只按城市或城市族群恢复真钱 current-YES peak/no-reheat。",
        "",
        "## Outputs",
        "",
        f"- segment summary CSV: `{payload['outputs']['segment_summary_csv']}`",
        f"- json: `{payload['outputs']['json']}`",
    ]
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    feature_path = Path(args.feature_rows)
    out_dir = Path(args.out_dir)
    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = base.load_current_yes_rows(feature_path)
    tradable = rows[
        rows["decision_hour_local"].between(10, 21)
        & rows["current_yes_ask"].between(0.35, 0.97, inclusive="left")
        & rows["current_yes_ask_size"].fillna(0).ge(5.0)
    ].copy()
    tradable = add_segment_features(tradable)
    holdout = tradable[tradable["period"] == "holdout"].copy()
    summary = build_breakdowns(tradable)
    summary_csv = out_dir / "segment_summary.csv"
    summary.to_csv(summary_csv, index=False)

    eligible = summary[(summary["rows"] >= 25) & (summary["dates"] >= 8)].copy()
    eligible["roi_sort"] = pd.to_numeric(eligible["roi"], errors="coerce")
    eligible["excess_sort"] = pd.to_numeric(eligible["excess_roi"], errors="coerce")
    eligible["break_sort"] = pd.to_numeric(eligible["future_break_rate"], errors="coerce")

    def take(kind: str, n: int) -> list[dict[str, Any]]:
        part = eligible[eligible["segment_kind"].eq(kind)].copy()
        if kind in {"city", "city_family"}:
            part = part.sort_values(["roi_sort", "excess_sort", "rows"], ascending=[False, False, False])
        else:
            part = part.sort_values(["roi_sort", "excess_sort", "rows"], ascending=[False, False, False])
        return part.drop(columns=["roi_sort", "excess_sort", "break_sort"]).head(n).to_dict("records")

    positive_support = eligible[
        eligible["roi"].gt(0)
        & eligible["excess_roi"].gt(0)
        & eligible["roi_ci95"].apply(lambda x: isinstance(x, list) and x[0] is not None and x[0] > 0)
    ]
    headline = (
        "横切后有几个点估计不错的低价/中价 no-reheat 子段，但没有城市、城市族群或 market-repricing 维度同时通过日期 CI 和 same-segment baseline；"
        "现在更像 shadow research，而不是 city-pool 直接筛选。"
    )

    payload = {
        "generated_at_utc": now_utc(),
        "inputs": {"feature_rows": str(feature_path.relative_to(ROOT))},
        "data_snapshot": {
            "feature_min_target_date": str(rows["target_date"].min()),
            "feature_max_target_date": str(rows["target_date"].max()),
            "current_yes_rows": int(len(rows)),
            "current_yes_dates": int(rows["target_date"].nunique()),
            "current_yes_cities": int(rows["city"].nunique()),
            "tradable_rows": int(len(tradable)),
            "holdout_tradable_rows": int(len(holdout)),
            "holdout_dates": int(holdout["target_date"].nunique()),
            "holdout_cities": int(holdout["city"].nunique()),
        },
        "headline": headline,
        "family_highlights": take("city_family", 12),
        "city_highlights": take("city", 20),
        "timing_highlights": (
            take("market_reprice_bin", 8)
            + take("local_hour_bin", 5)
            + take("forecast_gap_bin", 5)
            + take("forecast_peak_clock_bin", 5)
        ),
        "positive_ci_support_count": int(len(positive_support)),
        "outputs": {
            "segment_summary_csv": str(summary_csv.relative_to(ROOT)),
            "json": str(out_json.relative_to(ROOT)),
            "markdown": str(out_md.relative_to(ROOT)),
        },
    }
    out_json.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    build_markdown(json_ready(payload), out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
