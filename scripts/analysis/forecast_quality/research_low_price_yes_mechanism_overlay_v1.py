#!/usr/bin/env python3
"""HeadA low-price YES mechanism overlay research v1.

Research-only.  This script asks whether HeadA can be improved from first
principles by separating the forecast-tail sleeve into mechanism layers:

- forecast-distance / station-bias quality
- weather/regime complexity
- book-state / market-attention quality
- same-snapshot expression choice

It does not change live config.
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
    same_count_threshold,
)
from scripts.analysis.forecast_quality.research_low_price_yes_heada_refinement_v1 import (  # noqa: E402
    FRESH_FORWARD_START,
    RECENT_START,
    TRAIN_END,
    build_expression_replay,
    daily,
    date_block_ci,
    load_base,
    paired_expression_summary,
    simulate_execution,
    summarize_perf,
)

OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-05-low-price-yes-mechanism-overlay-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-05-low-price-yes-mechanism-overlay-v1.json"

RNG_SEED = 20260705
N_BOOT = 5000


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


def fmt_num(value: Any, digits: int = 2) -> str:
    x = to_float(value)
    if not math.isfinite(x):
        return ""
    return f"{x:.{digits}f}"


def fmt_usd(value: Any) -> str:
    x = to_float(value)
    if not math.isfinite(x):
        return ""
    return f"${x:+.2f}"


def bool_col(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    s = df[col]
    if s.dtype == bool:
        return s.fillna(False)
    return s.astype(str).str.lower().isin({"1", "true", "yes", "y"})


def add_period(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["period_bucket"] = np.where(
        out["target_date"].astype(str) <= TRAIN_END,
        "train_le_2026_06_20",
        np.where(out["target_date"].astype(str) >= RECENT_START, "recent_ge_2026_06_21", "gap"),
    )
    return out


def execution_focus(base: pd.DataFrame, *, sizing: str = "price_tier_6_8_10_shares") -> pd.DataFrame:
    sim = simulate_execution(base)
    out = sim[
        sim["sizing"].eq(sizing)
        & sim["entry_profile"].eq("taker_weather_fee")
        & sim["exit_policy"].eq("hold")
    ].copy()
    return add_period(out)


def selector_frame(focus: pd.DataFrame, base: pd.DataFrame, mask: pd.Series, *, label: str) -> pd.DataFrame:
    row_ids = set(base.loc[mask.fillna(False), "row_id"].astype(int))
    out = focus[focus["row_id"].isin(row_ids)].copy()
    out["selector"] = label
    return out


def top_removed_roi(frame: pd.DataFrame, n: int = 5) -> float:
    if frame.empty or len(frame) <= n:
        return math.nan
    sub = frame.sort_values("pnl", ascending=False).iloc[n:]
    cost = float(sub["cost"].sum())
    return float(sub["pnl"].sum() / cost) if cost > 0 else math.nan


def summarize_selector(frame: pd.DataFrame, *, label: str, period: str) -> dict[str, Any]:
    rec = summarize_perf(frame, label=label, period=period)
    if frame.empty:
        rec["top5_removed_roi"] = math.nan
        rec["avg_shares"] = math.nan
        return rec
    rec["top5_removed_roi"] = top_removed_roi(frame, 5)
    rec["avg_shares"] = float(frame["shares"].mean()) if "shares" in frame else math.nan
    return rec


def summarize_selectors(focus: pd.DataFrame, base: pd.DataFrame, selectors: dict[str, pd.Series]) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, Any]] = []
    daily_rows: list[pd.DataFrame] = []
    for label, mask in selectors.items():
        frame = selector_frame(focus, base, mask, label=label)
        for period, p_mask in {
            "full": pd.Series(True, index=frame.index),
            "train_le_2026_06_20": frame["target_date"].astype(str) <= TRAIN_END if not frame.empty else pd.Series([], dtype=bool),
            "recent_ge_2026_06_21": frame["target_date"].astype(str) >= RECENT_START if not frame.empty else pd.Series([], dtype=bool),
            "fresh_ge_2026_07_04": frame["target_date"].astype(str) >= FRESH_FORWARD_START if not frame.empty else pd.Series([], dtype=bool),
        }.items():
            sub = frame[p_mask].copy() if not frame.empty else frame
            summary_rows.append(summarize_selector(sub, label=label, period=period))
        d = daily(frame)
        if not d.empty:
            d["selector"] = label
            daily_rows.append(d)
    daily_out = pd.concat(daily_rows, ignore_index=True) if daily_rows else pd.DataFrame()
    return pd.DataFrame(summary_rows), daily_out


def paired_delta_ci(a: pd.DataFrame, b: pd.DataFrame) -> tuple[float, float, float] | tuple[None, None, None]:
    if a.empty or b.empty:
        return None, None, None
    da = daily(a).set_index("target_date")
    db = daily(b).set_index("target_date")
    dates = sorted(set(da.index) | set(db.index))
    if len(dates) < 3:
        return None, None, None
    da = da.reindex(dates).fillna(0.0)
    db = db.reindex(dates).fillna(0.0)
    a_cost = float(da["cost"].sum())
    b_cost = float(db["cost"].sum())
    if a_cost <= 0 or b_cost <= 0:
        return None, None, None
    point = float(da["pnl"].sum() / a_cost - db["pnl"].sum() / b_cost)
    rng = np.random.default_rng(RNG_SEED)
    vals: list[float] = []
    ap, ac = da["pnl"].to_numpy(float), da["cost"].to_numpy(float)
    bp, bc = db["pnl"].to_numpy(float), db["cost"].to_numpy(float)
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(dates), len(dates))
        ca = float(ac[idx].sum())
        cb = float(bc[idx].sum())
        if ca > 0 and cb > 0:
            vals.append(float(ap[idx].sum() / ca - bp[idx].sum() / cb))
    return point, float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def selector_delta_table(focus: pd.DataFrame, base: pd.DataFrame, selectors: dict[str, pd.Series], *, baseline: str) -> pd.DataFrame:
    base_frame = selector_frame(focus, base, selectors[baseline], label=baseline)
    rows: list[dict[str, Any]] = []
    for label, mask in selectors.items():
        if label == baseline:
            continue
        frame = selector_frame(focus, base, mask, label=label)
        point, lo, hi = paired_delta_ci(frame, base_frame)
        rows.append(
            {
                "selector": label,
                "baseline": baseline,
                "rows": int(len(frame)),
                "dates": int(frame["target_date"].nunique()) if not frame.empty else 0,
                "roi": float(frame["pnl"].sum() / frame["cost"].sum()) if not frame.empty and frame["cost"].sum() > 0 else math.nan,
                "baseline_rows": int(len(base_frame)),
                "baseline_roi": float(base_frame["pnl"].sum() / base_frame["cost"].sum()) if base_frame["cost"].sum() > 0 else math.nan,
                "delta_roi": point,
                "delta_ci_low": lo,
                "delta_ci_high": hi,
            }
        )
    return pd.DataFrame(rows)


def group_slice_summary(focus: pd.DataFrame, base: pd.DataFrame, *, group_cols: list[str]) -> pd.DataFrame:
    merged = focus.merge(base[["row_id", *group_cols]], on="row_id", how="left", suffixes=("", "_base"))
    records: list[dict[str, Any]] = []
    for group_col in group_cols:
        if group_col not in merged.columns:
            continue
        for value, g in merged.groupby(group_col, dropna=False):
            rec = summarize_selector(g.copy(), label=str(value), period="full")
            rec["slice_type"] = group_col
            rec["slice_value"] = value
            records.append(rec)
    return pd.DataFrame(records)


def build_selectors(base: pd.DataFrame) -> tuple[dict[str, pd.Series], dict[str, Any], pd.DataFrame]:
    scored, continuous_artifact = fit_continuous_model(base)
    theta = same_count_threshold(scored[scored["target_date"].astype(str) <= TRAIN_END].copy())
    base = base.merge(
        scored[["candidate_id", "p_continuous_ev_v1", "ev_continuous_v1"]],
        on="candidate_id",
        how="left",
    )
    hot = bool_col(base, "hot_tail_boundary_v1")
    attention_book = base["book_state_v1"].astype(str).isin({"thin_wide", "missing"})
    feasible_book = base["book_state_v1"].astype(str).eq("feasible")
    core_adj = hot & pd.to_numeric(base["adj_dist_p50_br"], errors="coerce").between(-0.5, 1.0, inclusive="both")
    core_raw = hot & pd.to_numeric(base["raw_dist_br"], errors="coerce").between(0.0, 1.0, inclusive="both")
    station_hot = hot & (
        pd.to_numeric(base["bias_p50_asof"], errors="coerce").ge(0.0)
        | pd.to_numeric(base["hot_tail_pct_asof"], errors="coerce").ge(0.40)
    )
    pcal_ev02 = hot & pd.to_numeric(base.get("p_cal_no_city_ev", np.nan), errors="coerce").ge(0.20)
    pcal_ev05 = hot & pd.to_numeric(base.get("p_cal_no_city_ev", np.nan), errors="coerce").ge(0.50)
    regime_score_ge4 = hot & pd.to_numeric(base.get("regime_score", np.nan), errors="coerce").ge(4.0)
    regime_score_ge5 = hot & pd.to_numeric(base.get("regime_score", np.nan), errors="coerce").ge(5.0)
    not_capped_busted = hot & bool_col(base, "score_not_capped_busted")
    open_or_marginal = hot & bool_col(base, "score_open_or_marginal")
    light_wind_humid = hot & bool_col(base, "score_humid_convective") & bool_col(base, "score_light_wind")
    model_disagree = hot & (
        (
            pd.to_numeric(base.get("gfs_gap_to_running_native", np.nan), errors="coerce")
            - pd.to_numeric(base.get("ecmwf_gap_to_running_native", np.nan), errors="coerce")
        )
        .abs()
        .ge(1.0)
    )
    continuous_same_count = pd.to_numeric(base["ev_continuous_v1"], errors="coerce").ge(theta)

    selectors = {
        "all_headA_denominator": pd.Series(True, index=base.index),
        "current_live_hot_dist_gt0": hot,
        "forecast_core_adj_dist_-0p5_to_1": core_adj,
        "forecast_core_raw_dist_0_to_1": core_raw,
        "station_hot_bias_or_tail_rate": station_hot,
        "pcal_no_city_ev_ge_0p20": pcal_ev02,
        "pcal_no_city_ev_ge_0p50": pcal_ev05,
        "book_feasible_hot": hot & feasible_book,
        "book_attention_thin_or_missing_hot": hot & attention_book,
        "regime_score_ge4_hot": regime_score_ge4,
        "regime_score_ge5_hot": regime_score_ge5,
        "regime_not_capped_busted_hot": not_capped_busted,
        "regime_open_or_marginal_hot": open_or_marginal,
        "regime_humid_lightwind_hot": light_wind_humid,
        "forecast_model_disagreement_hot": model_disagree,
        "composite_core_plus_attention": core_adj & attention_book,
        "composite_core_plus_regime_ge4": core_adj & regime_score_ge4,
        "composite_core_attention_regime_ge4": core_adj & attention_book & regime_score_ge4,
        "continuous_ev_same_count": continuous_same_count,
    }
    artifact = {
        "continuous_theta_same_count": theta,
        "continuous_model": continuous_artifact,
    }
    return selectors, artifact, base


def expression_tables(expr: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, Any]] = []
    for expression, g in expr.groupby("expression", dropna=False):
        summary_rows.append(summarize_selector(g.copy(), label=str(expression), period="full"))
    overall = pd.DataFrame(summary_rows)

    paired = paired_expression_summary(expr)

    by_group_rows: list[dict[str, Any]] = []
    for group_col in ["book_state_v1", "raw_dist_band", "adj_dist_band"]:
        if group_col not in expr.columns:
            continue
        for (group_value, expression), g in expr.groupby([group_col, "expression"], dropna=False):
            rec = summarize_selector(g.copy(), label=str(expression), period="full")
            rec["slice_type"] = group_col
            rec["slice_value"] = group_value
            rec["expression"] = expression
            by_group_rows.append(rec)
    by_group = pd.DataFrame(by_group_rows)
    return overall, paired, by_group


def md_table(df: pd.DataFrame, cols: list[str], max_rows: int = 30) -> str:
    if df.empty:
        return "_No rows._"
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.head(max_rows).iterrows():
        vals = []
        for col in cols:
            val = row.get(col, "")
            if col in {
                "win_rate",
                "avg_entry",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "top5_removed_roi",
                "delta_roi",
                "delta_ci_low",
                "delta_ci_high",
                "baseline_roi",
            }:
                vals.append(fmt_pct(val, signed=col not in {"avg_entry", "win_rate"}))
            elif col in {"cost", "pnl", "max_daily_loss_usd"}:
                vals.append(fmt_usd(val))
            elif col in {"avg_shares"}:
                vals.append(fmt_num(val, 1))
            elif isinstance(val, float):
                vals.append(fmt_num(val, 3))
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def make_markdown(
    *,
    base: pd.DataFrame,
    selectors_summary: pd.DataFrame,
    selector_delta: pd.DataFrame,
    group_slices: pd.DataFrame,
    expr_overall: pd.DataFrame,
    expr_paired: pd.DataFrame,
    expr_group: pd.DataFrame,
    artifact: dict[str, Any],
) -> str:
    current_full = selectors_summary[
        selectors_summary["label"].eq("current_live_hot_dist_gt0") & selectors_summary["period"].eq("full")
    ].copy()
    selector_view = selectors_summary[
        selectors_summary["period"].eq("full")
        & selectors_summary["label"].isin(
            [
                "current_live_hot_dist_gt0",
                "forecast_core_adj_dist_-0p5_to_1",
                "book_feasible_hot",
                "book_attention_thin_or_missing_hot",
                "regime_score_ge4_hot",
                "regime_not_capped_busted_hot",
                "composite_core_plus_attention",
                "composite_core_plus_regime_ge4",
                "composite_core_attention_regime_ge4",
                "continuous_ev_same_count",
            ]
        )
    ].copy()
    order = {
        "current_live_hot_dist_gt0": 1,
        "forecast_core_adj_dist_-0p5_to_1": 2,
        "book_feasible_hot": 3,
        "book_attention_thin_or_missing_hot": 4,
        "regime_score_ge4_hot": 5,
        "regime_not_capped_busted_hot": 6,
        "composite_core_plus_attention": 7,
        "composite_core_plus_regime_ge4": 8,
        "composite_core_attention_regime_ge4": 9,
        "continuous_ev_same_count": 10,
    }
    selector_view["_order"] = selector_view["label"].map(order).fillna(99)
    selector_view = selector_view.sort_values("_order")

    recent_view = selectors_summary[
        selectors_summary["period"].isin(["train_le_2026_06_20", "recent_ge_2026_06_21"])
        & selectors_summary["label"].isin(
            [
                "current_live_hot_dist_gt0",
                "forecast_core_adj_dist_-0p5_to_1",
                "book_attention_thin_or_missing_hot",
                "regime_score_ge4_hot",
                "composite_core_plus_attention",
                "continuous_ev_same_count",
            ]
        )
    ].copy()
    recent_view["_order"] = recent_view["label"].map(order).fillna(99)
    recent_view = recent_view.sort_values(["_order", "period"])

    delta_view = selector_delta[
        selector_delta["selector"].isin(
            [
                "forecast_core_adj_dist_-0p5_to_1",
                "book_attention_thin_or_missing_hot",
                "regime_score_ge4_hot",
                "composite_core_plus_attention",
                "composite_core_plus_regime_ge4",
                "continuous_ev_same_count",
            ]
        )
    ].copy()
    delta_view["_order"] = delta_view["selector"].map(order).fillna(99)
    delta_view = delta_view.sort_values("_order")

    slice_view = group_slices[
        group_slices["slice_type"].isin(
            ["book_state_v1", "adj_dist_band", "day_regime", "intraday_state", "moisture_cloud_regime", "wind_regime", "price_band"]
        )
    ].copy()
    slice_view = slice_view.sort_values(["slice_type", "rows"], ascending=[True, False])

    expr_pair_view = expr_paired[
        expr_paired["paired_alt"].isin(["next_hotter_yes", "selected_plus_next_basket", "higher_plus_yes"])
    ].copy()

    expr_group_view = expr_group[
        expr_group["slice_type"].eq("book_state_v1")
        & expr_group["expression"].isin(["selected_yes", "next_hotter_yes", "selected_plus_next_basket"])
    ].copy()
    expr_group_view = expr_group_view.sort_values(["slice_value", "expression"])

    dist_le0 = int((pd.to_numeric(base["raw_dist_br"], errors="coerce") <= 0).sum())
    dist_gt0 = int((pd.to_numeric(base["raw_dist_br"], errors="coerce") > 0).sum())
    current = current_full.iloc[0].to_dict() if not current_full.empty else {}

    return f"""# HeadA Low-Price YES Mechanism Overlay v1

Generated: `{now_utc()}`

Scope: HeadA `forecast_tail_low_price_yes` only.  This is a first-principles mechanism study over the current low-price YES denominator.  It does not change live.

## Verdict

HeadA does have plausible mechanism-improvement directions, but this run does **not** justify a new live gate.  The cleanest read is:

```text
forecast distance / station-bias: useful as probability/EV shape, not yet a superior selector
regime/weather complexity: useful diagnostic, weak as standalone selector
book state / attention: strongest unresolved mechanism; either real attention alpha or stale-quote illusion
expression alternatives: blanket hotter bracket or basket does not fix overshoot

significance=PARTIAL
baseline=PARTIAL
forward=FAIL/NA
conclusion=inconclusive_research_overlay; keep current tiny HeadA live unchanged
```

Current hot-only proxy (`price_tier_6_8_10_shares + taker fee + hold`) on the frozen denominator:

{md_table(current_full, ['label', 'period', 'rows', 'dates', 'cities', 'win_rate', 'avg_entry', 'roi', 'roi_ci_low', 'roi_ci_high', 'top5_removed_roi', 'losing_days', 'le_minus50pct_days', 'max_daily_loss_usd'], 5)}

## Data Snapshot

- Sync/rebuild before this run: Mac market-data synced through 2026-07-05 13:00 Asia/Shanghai; `run_stack.sh --api-only` rebuilt `runtime/weather.db` and fact tables.
- Denominator source: `low_price_yes_integrated_tail_v2` via `load_base()`, with canonical settlement and fact-signal fields reattached.
- Rows: {len(base)} total; {dist_gt0} `dist>0` hot-tail rows; {dist_le0} `dist<=0` rows excluded by current HeadA live boundary.
- Dates/cities: {base['target_date'].min()}..{base['target_date'].max()}, {base['target_date'].nunique()} dates, {base['city'].nunique()} cities.
- Cost model: official Weather taker fee `shares * 0.05 * price * (1-price)`; maker/rebate upside not counted here.

## Mechanism Selectors

All rows below use the same HeadA denominator and the same execution proxy: `price_tier_6_8_10_shares + taker_weather_fee + hold`.  These are not proposed live gates; they test mechanism shape.

{md_table(selector_view, ['label', 'rows', 'dates', 'cities', 'win_rate', 'avg_entry', 'avg_shares', 'roi', 'roi_ci_low', 'roi_ci_high', 'top5_removed_roi', 'losing_days', 'max_daily_loss_usd'], 20)}

Same-denominator-ish delta versus current `dist>0` baseline, by target-date block bootstrap:

{md_table(delta_view, ['selector', 'rows', 'dates', 'roi', 'baseline_roi', 'delta_roi', 'delta_ci_low', 'delta_ci_high'], 20)}

Interpretation:

- `forecast_core_adj_dist_-0p5_to_1` is the most physically coherent forecast-distance candidate: avoid cold/inside tickets and avoid far-tail tickets more than one bracket away after station-bias adjustment.  It improves the story, but not enough to promote by itself.
- `regime_score_ge4_hot` and `regime_not_capped_busted_hot` are real descriptors, but they do not dominate the existing selector.  Regime is a sensor, not the steering wheel.
- `book_attention_thin_or_missing_hot` remains the big unresolved piece.  If it fills live near decision ask, it is likely the alpha carrier.  If it fails to fill, the historical ROI is partly phantom.
- `continuous_ev_same_count` confirms the previous result: the probability shape ranks rows, but paired excess over `dist>0` is not clean enough yet.

## Train vs Recent

{md_table(recent_view, ['period', 'label', 'rows', 'dates', 'win_rate', 'avg_entry', 'roi', 'roi_ci_low', 'roi_ci_high', 'top5_removed_roi'], 30)}

Recent support is too thin and noisy to bless any overlay.  This is exactly why the 2026-07-04 forward clock matters more than more train slicing.

## Feature Slices

These slices explain mechanism contribution inside the hot-tail universe.  They are not independent strategies.

{md_table(slice_view, ['slice_type', 'slice_value', 'rows', 'dates', 'win_rate', 'avg_entry', 'roi', 'roi_ci_low', 'roi_ci_high', 'top5_removed_roi'], 80)}

Plain read:

- Distance matters, but in a curved way: too close can be non-tail, too far becomes wish-casting.  The productive zone is around bias-adjusted near-tail, not maximum distance.
- Weather regime labels do contain information, but their standalone edge is unstable.  They should feed EV calibration.
- Price band matters because fixed cash overweights the cheapest tickets; HeadA’s current price-tier sizing is directionally more coherent.

## Expression Impact

Expression replay rebuilds same-snapshot sibling YES legs for the hot-tail rows.

{md_table(expr_overall.sort_values('label'), ['label', 'rows', 'dates', 'cities', 'win_rate', 'avg_entry', 'roi', 'roi_ci_low', 'roi_ci_high', 'top5_removed_roi'], 20)}

Same row denominator checks:

{md_table(expr_pair_view, ['paired_alt', 'label', 'rows', 'dates', 'win_rate', 'avg_entry', 'roi', 'roi_ci_low', 'roi_ci_high'], 20)}

Expression by book state:

{md_table(expr_group_view, ['slice_value', 'expression', 'rows', 'dates', 'win_rate', 'avg_entry', 'roi', 'roi_ci_low', 'roi_ci_high'], 30)}

Conclusion: overshoot is a real failure mode, but the naive fix is bad.  `next_hotter_yes`, `higher_plus_yes`, and the small basket generally dilute the edge.  The right next experiment is not “always buy hotter”; it is an EV-ranked expression selector that only switches expression when the probability lift beats the extra ask.

## First-Principles Takeaway

HeadA should be modeled as:

```text
P(ticket wins)
  = f(
      bracket distance above forecast,
      as-of station/source bias,
      forecast uncertainty / regime complexity,
      market attention / book state
    )

trade only if P(ticket wins) - executable ask - fee > 0
then choose expression only if sibling expression has higher net EV on same snapshot
```

Regime belongs inside `f(...)`.  It should not be a standalone hard gate unless fresh-forward evidence shows a specific regime boundary has stable excess over the current selector.

## Next Work

1. Keep HeadA tiny live as-is: `dist>0`, price-tier 6/8/10 shares, maker-first dynamic lifecycle, hold to settlement.
2. Add/monitor forward telemetry for this overlay: `adj_dist_p50_br`, `regime_score`, `book_state_v1`, `continuous_ev_same_count`, and sibling-expression counterfactual.
3. The next durable script should be an executable EV selector replay: calibrate `P(win)` on train, then compare current expression vs sibling expressions with real orderbook depth and maker partial fills.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_mechanism_overlay_v1.py`
- Selector summary: `docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1/selector_summary.csv`
- Selector daily: `docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1/selector_daily.csv`
- Selector delta: `docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1/selector_delta_vs_current.csv`
- Feature slices: `docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1/group_slices.csv`
- Expression replay: `docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1/expression_replay.csv`
- Expression summaries: `docs/analysis/2026-07/generated/low_price_yes_mechanism_overlay_v1/expression_overall.csv`, `expression_paired.csv`, `expression_by_group.csv`
- JSON: `docs/analysis/2026-07/2026-07-05-low-price-yes-mechanism-overlay-v1.json`
"""


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


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base0 = load_base()
    selectors, artifact, base = build_selectors(base0)
    focus = execution_focus(base)
    selector_summary, selector_daily = summarize_selectors(focus, base, selectors)
    selector_delta = selector_delta_table(focus, base, selectors, baseline="current_live_hot_dist_gt0")

    hot_focus = selector_frame(focus, base, selectors["current_live_hot_dist_gt0"], label="current_live_hot_dist_gt0")
    group_cols = [
        "book_state_v1",
        "raw_dist_band",
        "adj_dist_band",
        "day_regime",
        "intraday_state",
        "moisture_cloud_regime",
        "wind_regime",
        "price_band",
    ]
    group_slices = group_slice_summary(hot_focus, base, group_cols=group_cols)

    expr = build_expression_replay(base)
    expr_overall, expr_paired, expr_group = expression_tables(expr)

    base.to_csv(OUT_DIR / "mechanism_base_rows.csv", index=False)
    selector_summary.to_csv(OUT_DIR / "selector_summary.csv", index=False)
    selector_daily.to_csv(OUT_DIR / "selector_daily.csv", index=False)
    selector_delta.to_csv(OUT_DIR / "selector_delta_vs_current.csv", index=False)
    group_slices.to_csv(OUT_DIR / "group_slices.csv", index=False)
    expr.to_csv(OUT_DIR / "expression_replay.csv", index=False)
    expr_overall.to_csv(OUT_DIR / "expression_overall.csv", index=False)
    expr_paired.to_csv(OUT_DIR / "expression_paired.csv", index=False)
    expr_group.to_csv(OUT_DIR / "expression_by_group.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "train_end": TRAIN_END,
        "recent_start": RECENT_START,
        "fresh_forward_start": FRESH_FORWARD_START,
        "rows": int(len(base)),
        "dates": int(base["target_date"].nunique()),
        "cities": int(base["city"].nunique()),
        "current_hot_rows": int(bool_col(base, "hot_tail_boundary_v1").sum()),
        "selector_summary": selector_summary.to_dict(orient="records"),
        "selector_delta_vs_current": selector_delta.to_dict(orient="records"),
        "expression_overall": expr_overall.to_dict(orient="records"),
        "expression_paired": expr_paired.to_dict(orient="records"),
        "artifact": artifact,
        "verdict": "inconclusive_research_overlay_keep_headA_tiny_live_unchanged",
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(
        make_markdown(
            base=base,
            selectors_summary=selector_summary,
            selector_delta=selector_delta,
            group_slices=group_slices,
            expr_overall=expr_overall,
            expr_paired=expr_paired,
            expr_group=expr_group,
            artifact=artifact,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {OUT_MD.relative_to(ROOT)}")
    print(f"Wrote {OUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
