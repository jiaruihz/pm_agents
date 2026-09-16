#!/usr/bin/env python3
"""HeadA clean EV expression selector research v1.

This is the mechanism-first alternative to adding more HeadA gates:

1. Build same-snapshot sibling YES expressions for the current HeadA hot-tail
   universe.
2. Estimate P(expression wins) with low-dimensional, no-city models.
3. Compute net EV = P(win) - ask - official Weather taker fee per share.
4. Select the expression with highest positive net EV, or no trade.

Research-only.  No live config is changed.
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
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.research_low_price_yes_heada_refinement_v1 import (  # noqa: E402
    FRESH_FORWARD_START,
    RECENT_START,
    TRAIN_END,
    bracket_low_f,
    bracket_width_f,
    build_expression_replay,
    daily,
    date_block_ci,
    load_base,
    weather_taker_fee,
)

OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_clean_ev_expression_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-05-low-price-yes-clean-ev-expression-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-05-low-price-yes-clean-ev-expression-v1.json"

RNG_SEED = 20260705
N_BOOT = 5000
LOW_PRICE_CAP = 0.20

SINGLE_EXPRESSIONS = ["selected_yes", "next_hotter_yes", "two_hotter_yes", "higher_plus_yes"]

PHYSICS_FEATURES = [
    "expr_raw_dist_br",
    "expr_adj_dist_p50_br",
    "expr_step",
    "expr_is_plus",
    "bias_n_asof",
    "bias_mean_asof",
    "bias_p50_asof",
    "bias_p90_asof",
    "hot_tail_pct_asof",
    "cold_tail_pct_asof",
    "forecast_peak_delta_hours_local",
    "forecast_gap_to_running_native",
    "gfs_gap_to_running_native",
    "ecmwf_gap_to_running_native",
    "remaining_heat_native",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "relative_humidity_pct",
    "wind_speed_kt",
    "minutes_since_running_max",
    "regime_score",
]

MARKET_FEATURES = PHYSICS_FEATURES + ["entry"]
ATTENTION_FEATURES = MARKET_FEATURES + ["book_missing", "book_thin_wide", "book_feasible"]


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


def price_tier_shares(entry: float) -> float:
    if entry <= 0.08:
        return 6.0
    if entry <= 0.14:
        return 8.0
    return 10.0


def top_removed_roi(frame: pd.DataFrame, n: int = 5) -> float:
    if frame.empty or len(frame) <= n:
        return math.nan
    sub = frame.sort_values("pnl", ascending=False).iloc[n:]
    cost = float(sub["cost"].sum())
    return float(sub["pnl"].sum() / cost) if cost > 0 else math.nan


def summarize_trades(frame: pd.DataFrame, *, label: str, period: str) -> dict[str, Any]:
    if frame.empty:
        return {"label": label, "period": period, "rows": 0}
    d = daily(frame)
    cost = float(frame["cost"].sum())
    pnl = float(frame["pnl"].sum())
    ci_low, ci_high = date_block_ci(frame)
    return {
        "label": label,
        "period": period,
        "rows": int(len(frame)),
        "dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "win_rate": float(frame["win"].mean()),
        "avg_entry": float(frame["entry"].mean()),
        "avg_net_ev": float(frame["net_ev"].mean()) if "net_ev" in frame else math.nan,
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost > 0 else math.nan,
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
        "top5_removed_roi": top_removed_roi(frame),
        "losing_days": int((d["pnl"] < 0).sum()),
        "le_minus50pct_days": int((d["roi"] <= -0.5).sum()),
        "max_daily_loss_usd": float(d["pnl"].min()) if not d.empty else math.nan,
        "max_daily_loss_roi": float(d["roi"].min()) if not d.empty else math.nan,
    }


def period_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    if frame.empty:
        return {
            "full": pd.Series([], dtype=bool),
            "train_le_2026_06_20": pd.Series([], dtype=bool),
            "recent_ge_2026_06_21": pd.Series([], dtype=bool),
            "fresh_ge_2026_07_04": pd.Series([], dtype=bool),
        }
    target = frame["target_date"].astype(str)
    return {
        "full": pd.Series(True, index=frame.index),
        "train_le_2026_06_20": target <= TRAIN_END,
        "recent_ge_2026_06_21": target >= RECENT_START,
        "fresh_ge_2026_07_04": target >= FRESH_FORWARD_START,
    }


def summarize_by_period(frame: pd.DataFrame, *, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for period, mask in period_masks(frame).items():
        rows.append(summarize_trades(frame[mask].copy(), label=label, period=period))
    return rows


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
    if da["cost"].sum() <= 0 or db["cost"].sum() <= 0:
        return None, None, None
    point = float(da["pnl"].sum() / da["cost"].sum() - db["pnl"].sum() / db["cost"].sum())
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


def add_expression_features(expr: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "row_id",
        "unit",
        "forecast_max_f_used",
        "adj_forecast_p50_f",
        "bracket_width_f",
        "bias_n_asof",
        "bias_mean_asof",
        "bias_p50_asof",
        "bias_p90_asof",
        "hot_tail_pct_asof",
        "cold_tail_pct_asof",
        "forecast_peak_delta_hours_local",
        "forecast_gap_to_running_native",
        "gfs_gap_to_running_native",
        "ecmwf_gap_to_running_native",
        "remaining_heat_native",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "relative_humidity_pct",
        "wind_speed_kt",
        "minutes_since_running_max",
        "regime_score",
        "book_state_v1",
    ]
    out = expr[expr["expression"].isin(SINGLE_EXPRESSIONS)].copy()
    out = out.merge(base[cols], on="row_id", how="left", suffixes=("", "_base"))
    out["target_date"] = out["target_date"].astype(str)
    out["entry"] = pd.to_numeric(out["entry"], errors="coerce")
    out["payoff"] = pd.to_numeric(out["payoff"], errors="coerce")
    out["win"] = out["payoff"].ge(0.5)
    out["expr_low_f"] = [
        bracket_low_f(bracket, unit)
        for bracket, unit in zip(out["expression_bracket"], out["unit"], strict=False)
    ]
    out["expr_width_f"] = [bracket_width_f(unit) for unit in out["unit"]]
    out["expr_raw_dist_br"] = (
        out["expr_low_f"] - pd.to_numeric(out["forecast_max_f_used"], errors="coerce")
    ) / out["expr_width_f"]
    out["expr_adj_dist_p50_br"] = (
        out["expr_low_f"] - pd.to_numeric(out["adj_forecast_p50_f"], errors="coerce")
    ) / out["expr_width_f"]
    out["expr_step"] = out["expression"].map(
        {"selected_yes": 0.0, "next_hotter_yes": 1.0, "two_hotter_yes": 2.0, "higher_plus_yes": 3.0}
    )
    out["expr_is_plus"] = out["expression"].eq("higher_plus_yes").astype(float)
    out["book_missing"] = out["book_state_v1"].astype(str).eq("missing").astype(float)
    out["book_thin_wide"] = out["book_state_v1"].astype(str).eq("thin_wide").astype(float)
    out["book_feasible"] = out["book_state_v1"].astype(str).eq("feasible").astype(float)
    out["fee_per_share"] = [0.05 * p * (1.0 - p) for p in out["entry"]]
    out = out[
        out["entry"].between(0.001, 0.99)
        & out["payoff"].isin([0.0, 1.0])
        & out["expr_raw_dist_br"].notna()
    ].copy()
    return out.reset_index(drop=True)


def fill_matrix(frame: pd.DataFrame, train: pd.DataFrame, features: list[str]) -> tuple[pd.DataFrame, dict[str, float], np.ndarray, np.ndarray]:
    out = frame.copy()
    medians: dict[str, float] = {}
    for col in features:
        values = pd.to_numeric(train[col], errors="coerce")
        median = float(values.median()) if values.notna().any() else 0.0
        medians[col] = median
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(median)
    x_train = out.loc[train.index, features].to_numpy(float)
    mu = x_train.mean(axis=0)
    sd = x_train.std(axis=0)
    sd[sd <= 0] = 1.0
    return out, medians, mu, sd


def fit_model(expr: pd.DataFrame, *, name: str, features: list[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    train_mask = expr["target_date"].astype(str) <= TRAIN_END
    train_raw = expr[train_mask].copy()
    filled, medians, mu, sd = fill_matrix(expr, train_raw, features)
    x = filled[features].to_numpy(float)
    x_train = filled.loc[train_mask, features].to_numpy(float)
    y_train = filled.loc[train_mask, "win"].astype(int).to_numpy()
    model = LogisticRegression(C=0.2, solver="liblinear", max_iter=1000, random_state=RNG_SEED)
    model.fit((x_train - mu) / sd, y_train)
    pred_col = f"p_{name}"
    ev_col = f"net_ev_{name}"
    filled[pred_col] = model.predict_proba((x - mu) / sd)[:, 1]
    filled[ev_col] = filled[pred_col] - filled["entry"] - filled["fee_per_share"]
    artifact = {
        "name": name,
        "features": features,
        "medians": medians,
        "mu": mu.tolist(),
        "sd": sd.tolist(),
        "coef": model.coef_[0].tolist(),
        "intercept": float(model.intercept_[0]),
        "train_rows": int(train_mask.sum()),
        "train_wins": int(y_train.sum()),
    }
    return filled, artifact


def model_metrics(scored: pd.DataFrame, *, model_name: str) -> pd.DataFrame:
    pred_col = f"p_{model_name}"
    rows: list[dict[str, Any]] = []
    for period, mask in period_masks(scored).items():
        sub = scored[mask].copy()
        if sub.empty:
            continue
        y = sub["win"].astype(int).to_numpy()
        p = sub[pred_col].clip(1e-6, 1 - 1e-6).to_numpy(float)
        rows.append(
            {
                "model": model_name,
                "period": period,
                "rows": int(len(sub)),
                "dates": int(sub["target_date"].nunique()),
                "wins": int(y.sum()),
                "win_rate": float(y.mean()),
                "avg_p": float(p.mean()),
                "brier": float(brier_score_loss(y, p)),
                "logloss": float(log_loss(y, p, labels=[0, 1])),
            }
        )
    return pd.DataFrame(rows)


def add_trade_pnl(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["shares"] = [price_tier_shares(float(p)) for p in out["entry"]]
    out["entry_fee"] = [weather_taker_fee(shares=float(s), price=float(p)) for s, p in zip(out["shares"], out["entry"], strict=False)]
    out["cost"] = out["shares"] * out["entry"] + out["entry_fee"]
    out["pnl"] = out["shares"] * out["payoff"] - out["cost"]
    return out


def choose_by_ev(scored: pd.DataFrame, *, model_name: str, threshold: float, cap_price: float | None) -> pd.DataFrame:
    ev_col = f"net_ev_{model_name}"
    p_col = f"p_{model_name}"
    frame = scored.copy()
    if cap_price is not None:
        frame = frame[frame["entry"] <= cap_price + 1e-9].copy()
    frame = frame[frame[ev_col] >= threshold].copy()
    if frame.empty:
        return add_trade_pnl(frame)
    idx = frame.sort_values(["row_id", ev_col, p_col, "entry"], ascending=[True, False, False, True]).groupby("row_id").tail(1).index
    chosen = frame.loc[idx].copy().sort_values(["target_date", "city", "row_id"]).reset_index(drop=True)
    chosen["selector"] = f"{model_name}_ev_ge_{threshold:.4f}" + ("_cap20" if cap_price == LOW_PRICE_CAP else "_uncapped")
    chosen["p_model"] = chosen[p_col]
    chosen["net_ev"] = chosen[ev_col]
    return add_trade_pnl(chosen)


def same_count_threshold(scored: pd.DataFrame, *, model_name: str, cap_price: float | None, train_target_count: int) -> float:
    ev_col = f"net_ev_{model_name}"
    frame = scored[scored["target_date"].astype(str) <= TRAIN_END].copy()
    if cap_price is not None:
        frame = frame[frame["entry"] <= cap_price + 1e-9].copy()
    if frame.empty or train_target_count <= 0:
        return math.inf
    best = frame.sort_values(["row_id", ev_col], ascending=[True, False]).groupby("row_id").tail(1)
    vals = best[ev_col].dropna().sort_values(ascending=False).to_numpy(float)
    if len(vals) < train_target_count:
        return float(vals[-1]) if len(vals) else math.inf
    return float(vals[train_target_count - 1])


def selected_baseline(scored: pd.DataFrame, row_ids: set[int] | None = None) -> pd.DataFrame:
    base = scored[scored["expression"].eq("selected_yes")].copy()
    if row_ids is not None:
        base = base[base["row_id"].isin(row_ids)].copy()
    base["selector"] = "current_selected_yes_baseline"
    base["p_model"] = math.nan
    base["net_ev"] = math.nan
    return add_trade_pnl(base)


def selector_summaries(scored: pd.DataFrame, selectors: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for label, frame in selectors.items():
        for rec in summarize_by_period(frame, label=label):
            rows.append(rec)
    return pd.DataFrame(rows)


def expression_counts(selectors: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for label, frame in selectors.items():
        if frame.empty or "expression" not in frame.columns:
            rows.append({"selector": label, "expression": "", "rows": 0})
            continue
        for expression, g in frame.groupby("expression", dropna=False):
            rows.append(
                {
                    "selector": label,
                    "expression": expression,
                    "rows": int(len(g)),
                    "dates": int(g["target_date"].nunique()),
                    "win_rate": float(g["win"].mean()),
                    "avg_entry": float(g["entry"].mean()),
                    "roi": float(g["pnl"].sum() / g["cost"].sum()) if g["cost"].sum() > 0 else math.nan,
                }
            )
    return pd.DataFrame(rows)


def same_row_ab(scored: pd.DataFrame, selectors: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for label, frame in selectors.items():
        if label == "current_selected_yes_baseline" or frame.empty:
            continue
        row_ids = set(frame["row_id"].astype(int))
        baseline = selected_baseline(scored, row_ids=row_ids)
        for side, g in [("selector", frame), ("same_rows_selected_yes", baseline)]:
            rec = summarize_trades(g, label=side, period=label)
            rec["selector_name"] = label
            rows.append(rec)
        point, lo, hi = paired_delta_ci(frame, baseline)
        rows.append(
            {
                "selector_name": label,
                "label": "selector_minus_same_rows_selected_yes",
                "period": label,
                "rows": int(len(frame)),
                "dates": int(frame["target_date"].nunique()),
                "delta_roi": point,
                "delta_ci_low": lo,
                "delta_ci_high": hi,
            }
        )
    return pd.DataFrame(rows)


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
                "avg_p",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "top5_removed_roi",
                "delta_roi",
                "delta_ci_low",
                "delta_ci_high",
                "avg_net_ev",
            }:
                vals.append(fmt_pct(val, signed=col not in {"win_rate", "avg_entry", "avg_p"}))
            elif col in {"cost", "pnl", "max_daily_loss_usd"}:
                vals.append(fmt_usd(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def make_markdown(
    *,
    base: pd.DataFrame,
    scored: pd.DataFrame,
    model_metric_df: pd.DataFrame,
    summary: pd.DataFrame,
    counts: pd.DataFrame,
    ab: pd.DataFrame,
    artifacts: dict[str, Any],
) -> str:
    main_labels = [
        "current_selected_yes_baseline",
        "physics_ev_positive_cap20",
        "physics_same_count_cap20",
        "market_blend_ev_positive_cap20",
        "market_blend_same_count_cap20",
        "attention_blend_ev_positive_cap20",
        "attention_blend_same_count_cap20",
    ]
    full = summary[summary["period"].eq("full") & summary["label"].isin(main_labels)].copy()
    full["_order"] = full["label"].map({name: i for i, name in enumerate(main_labels)}).fillna(99)
    full = full.sort_values("_order")

    recent = summary[summary["period"].isin(["train_le_2026_06_20", "recent_ge_2026_06_21"]) & summary["label"].isin(main_labels)].copy()
    recent["_order"] = recent["label"].map({name: i for i, name in enumerate(main_labels)}).fillna(99)
    recent = recent.sort_values(["_order", "period"])

    count_view = counts[counts["selector"].isin(main_labels)].copy()
    count_view["_order"] = count_view["selector"].map({name: i for i, name in enumerate(main_labels)}).fillna(99)
    count_view = count_view.sort_values(["_order", "expression"])

    ab_view = ab[ab["label"].isin(["selector", "same_rows_selected_yes", "selector_minus_same_rows_selected_yes"])].copy()
    ab_view = ab_view[ab_view["selector_name"].isin(main_labels[1:])]
    ab_view["_order"] = ab_view["selector_name"].map({name: i for i, name in enumerate(main_labels)}).fillna(99)
    ab_view = ab_view.sort_values(["_order", "label"])

    metrics_view = model_metric_df[model_metric_df["period"].isin(["train_le_2026_06_20", "recent_ge_2026_06_21", "full"])].copy()
    metrics_view = metrics_view.sort_values(["model", "period"])

    current = full[full["label"].eq("current_selected_yes_baseline")]
    current_roi = float(current["roi"].iloc[0]) if not current.empty else math.nan

    return f"""# HeadA Clean EV Expression Selector v1

Generated: `{now_utc()}`

Scope: HeadA `forecast_tail_low_price_yes`, expression layer only.  This is the cleaner mechanism formulation requested: estimate `P(expression wins)`, subtract ask and official fee, then choose the best expression or no trade.  It does **not** change live.

## Clean Mechanism

Instead of:

```text
low-price YES + more filters + maybe a hotter bracket patch
```

the cleaner expression is:

```text
For each city-date snapshot:
  for each sibling YES expression e:
      p_e = P(e wins | forecast distance, as-of station bias, forecast uncertainty, regime context)
      net_ev_e = p_e - ask_e - fee_per_share(ask_e)

  trade argmax(net_ev_e) only if net_ev_e > 0
```

The primary universe is still the HeadA hot-tail universe because that is the strategy family definition, not a patch: early low-price YES tickets above the forecast max.

## Verdict

The clean EV expression selector is the right architecture, but this first version is **not ready to replace the live selector**.

```text
significance=FAIL/PARTIAL
baseline=FAIL
forward=FAIL
conclusion=inconclusive_clean_architecture; keep HeadA live unchanged
```

Why: the EV models can rank expression candidates and sometimes choose hotter legs, but same-row A/B does not beat simply holding the original selected YES.  The market price is already carrying a lot of information; the current selected expression remains the best historical expression on this denominator.

Current selected baseline ROI in this run: {fmt_pct(current_roi)}.

## Data Snapshot

- Denominator: HeadA hot-tail expression rows generated from `low_price_yes_heada_refinement_v1`.
- Base rows: {len(base)} current HeadA candidates; expression candidates scored: {len(scored)} single-leg sibling rows.
- Date range: {base['target_date'].min()}..{base['target_date'].max()}, {base['target_date'].nunique()} dates, {base['city'].nunique()} cities.
- Cost model: official Weather taker fee `shares * 0.05 * price * (1-price)`.
- Expression price cap for primary selectors: `ask <= {LOW_PRICE_CAP:.2f}`.
- No city identity is used in the models.

## Model Quality

{md_table(metrics_view, ['model', 'period', 'rows', 'dates', 'wins', 'win_rate', 'avg_p', 'brier', 'logloss'], 20)}

Read: `physics` is the purest model but underpowered.  `market_blend` improves probability calibration because ask is informative.  `attention_blend` can look better historically because book state captures attention/staleness, but that is exactly the part requiring fresh fillability evidence.

## Selector Performance

All selectors use price-tier shares and taker-fee hold-to-settlement.  Positive selectors use the natural rule `net_ev > 0`.  Same-count selectors set one train threshold so train trade count matches the current selected baseline; they are diagnostic, not live rules.

{md_table(full, ['label', 'rows', 'dates', 'cities', 'win_rate', 'avg_entry', 'avg_net_ev', 'roi', 'roi_ci_low', 'roi_ci_high', 'top5_removed_roi', 'losing_days', 'max_daily_loss_usd'], 20)}

## Train vs Recent

{md_table(recent, ['period', 'label', 'rows', 'dates', 'win_rate', 'avg_entry', 'avg_net_ev', 'roi', 'roi_ci_low', 'roi_ci_high', 'top5_removed_roi'], 30)}

Forward/recent does not rescue the selector.  This is the point where the little EV-engine says, quite politely, “nice architecture, not enough edge yet.”

## Expression Choice Mix

{md_table(count_view, ['selector', 'expression', 'rows', 'dates', 'win_rate', 'avg_entry', 'roi'], 40)}

The selector does choose hotter legs sometimes, but those switches are not reliably profitable.  This confirms the prior mechanism finding: overshoot exists, but “buy hotter” is not the answer unless the probability lift beats the extra ask.

## Same-Row A/B Against Selected YES

This is the important table.  For every row where the EV selector trades, compare its chosen expression against buying the original selected YES on exactly the same row.

{md_table(ab_view, ['selector_name', 'label', 'rows', 'dates', 'win_rate', 'avg_entry', 'roi', 'roi_ci_low', 'roi_ci_high', 'delta_roi', 'delta_ci_low', 'delta_ci_high'], 60)}

Interpretation:

- If a cleaner expression selector were genuinely better, this table should show positive `selector_minus_same_rows_selected_yes`.
- It does not.  The original selected YES remains a stubbornly good expression on the same rows.
- So the next improvement should focus on **whether to trade** and **whether the quote is real/fillable**, not on blanket expression switching.

## What This Means Mechanistically

The clean HeadA strategy should be:

```text
1. Define a hot-tail opportunity above forecast max.
2. Estimate probability for each available YES expression.
3. Select expression by net EV.
4. Require executable quote quality and realistic maker fill model.
5. Size from EV confidence and liquidity, not fixed cash.
```

But after this replay, the best current expression remains:

```text
selected low-price YES, not next-hotter / plus / basket
```

The cleaner optimization is therefore not a new bracket expression today.  It is a cleaner **EV architecture** to shadow forward:

```text
shadow tag:
  p_physics_selected
  p_market_blend_selected
  p_attention_blend_selected
  best_sibling_expression
  best_sibling_net_ev
  same_row_selected_yes_net_ev
  ev_selector_would_switch_expression
```

## Decision

- Do not change HeadA live.
- Keep live expression as selected YES.
- Keep dynamic maker lifecycle and price-tier shares.
- Add this EV expression selector as shadow telemetry, not as a selector.
- Next real blocker remains book-state/fillability: if the selected YES edge lives only in thin/missing books, the EV model must include executable fill probability before it can govern live.

## Artifacts

- Script: `scripts/analysis/forecast_quality/research_low_price_yes_clean_ev_expression_v1.py`
- Scored expressions: `docs/analysis/2026-07/generated/low_price_yes_clean_ev_expression_v1/expression_scored.csv`
- Selected trades: `docs/analysis/2026-07/generated/low_price_yes_clean_ev_expression_v1/selected_trades.csv`
- Selector summary: `docs/analysis/2026-07/generated/low_price_yes_clean_ev_expression_v1/selector_summary.csv`
- Same-row A/B: `docs/analysis/2026-07/generated/low_price_yes_clean_ev_expression_v1/same_row_ab.csv`
- JSON: `docs/analysis/2026-07/2026-07-05-low-price-yes-clean-ev-expression-v1.json`
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
    base = load_base()
    expr_raw = build_expression_replay(base)
    expr = add_expression_features(expr_raw, base)

    scored = expr.copy()
    artifacts: dict[str, Any] = {}
    metrics: list[pd.DataFrame] = []
    for name, features in {
        "physics": PHYSICS_FEATURES,
        "market_blend": MARKET_FEATURES,
        "attention_blend": ATTENTION_FEATURES,
    }.items():
        scored, artifact = fit_model(scored, name=name, features=features)
        artifacts[name] = artifact
        metrics.append(model_metrics(scored, model_name=name))
    metric_df = pd.concat(metrics, ignore_index=True)

    baseline = selected_baseline(scored)
    train_target_count = int((baseline["target_date"].astype(str) <= TRAIN_END).sum())

    selectors: dict[str, pd.DataFrame] = {"current_selected_yes_baseline": baseline}
    for model_name in ["physics", "market_blend", "attention_blend"]:
        pos = choose_by_ev(scored, model_name=model_name, threshold=0.0, cap_price=LOW_PRICE_CAP)
        selectors[f"{model_name}_ev_positive_cap20"] = pos
        theta = same_count_threshold(
            scored,
            model_name=model_name,
            cap_price=LOW_PRICE_CAP,
            train_target_count=train_target_count,
        )
        selectors[f"{model_name}_same_count_cap20"] = choose_by_ev(
            scored,
            model_name=model_name,
            threshold=theta,
            cap_price=LOW_PRICE_CAP,
        )
        artifacts[f"{model_name}_same_count_theta_cap20"] = theta

    summary = selector_summaries(scored, selectors)
    counts = expression_counts(selectors)
    ab = same_row_ab(scored, selectors)

    selected_all = pd.concat(
        [frame.assign(selector_name=label) for label, frame in selectors.items()],
        ignore_index=True,
    )
    scored.to_csv(OUT_DIR / "expression_scored.csv", index=False)
    selected_all.to_csv(OUT_DIR / "selected_trades.csv", index=False)
    metric_df.to_csv(OUT_DIR / "model_metrics.csv", index=False)
    summary.to_csv(OUT_DIR / "selector_summary.csv", index=False)
    counts.to_csv(OUT_DIR / "expression_choice_counts.csv", index=False)
    ab.to_csv(OUT_DIR / "same_row_ab.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "train_end": TRAIN_END,
        "recent_start": RECENT_START,
        "low_price_cap": LOW_PRICE_CAP,
        "base_rows": int(len(base)),
        "expression_rows": int(len(scored)),
        "selector_summary": summary.to_dict(orient="records"),
        "same_row_ab": ab.to_dict(orient="records"),
        "model_metrics": metric_df.to_dict(orient="records"),
        "artifacts": artifacts,
        "verdict": "inconclusive_clean_architecture_keep_headA_live_expression_unchanged",
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(
        make_markdown(
            base=base,
            scored=scored,
            model_metric_df=metric_df,
            summary=summary,
            counts=counts,
            ab=ab,
            artifacts=artifacts,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {OUT_MD.relative_to(ROOT)}")
    print(f"Wrote {OUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
