#!/usr/bin/env python3
"""Low-price YES tail calibrated-probability research v1.

This is a response to the fabel5 review.  The goal is not to optimize the
source-aware v3 bands.  It tests whether a point-in-time station-basis prior can
turn model probability and market ask into a more durable calibrated EV signal.

Two denominators are evaluated:
1. broad_no_dust: all settled BUY_YES candidates with ask 0.05..0.20.
2. v1_edge20: current live-like no-dust v1 rows, edge>=0.20 and ask 0.05..0.20.

All station-bias features are as-of target_date: only station-vs-forecast errors
strictly before the target date are used.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.special import logit
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
BROAD_DETAILS = ROOT / "docs/analysis/2026-07/generated/reversal_lottery_lab_v2/details.csv"
V1_DETAILS = ROOT / "docs/analysis/2026-07/generated/low_price_yes_lottery_selector_refinement_v1/details.csv"
BIAS_ROWS = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_tail_pcal_v1"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-02-low-price-yes-tail-pcal-v1.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-02-low-price-yes-tail-pcal-v1.json"

HOLDOUT_START = "2026-06-21"
FORWARD_START = "2026-06-27"
RNG_SEED = 20260702
THRESHOLDS = [-0.2, 0.0, 0.2, 0.5, 1.0]


NUMERIC_FEATURES = [
    "ask",
    "model_p_yes",
    "edge",
    "logit_model_p",
    "logit_ask",
    "bias_n",
    "bias_mean",
    "bias_p50",
    "bias_p90",
    "hot_tail_pct",
    "hot_tail2_pct",
    "cold_tail_pct",
    "bias_mae",
    "decision_hour_utc",
]


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: Any) -> str:
    try:
        x = float(value)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"{100 * x:+.1f}%"


def money(value: Any) -> str:
    try:
        x = float(value)
    except Exception:
        return ""
    if not math.isfinite(x):
        return ""
    return f"${x:+.2f}"


def md_table(df: pd.DataFrame, cols: list[tuple[str, str]], max_rows: int = 50) -> str:
    if df.empty:
        return "_No rows._"
    pct_cols = {"roi", "win_rate", "roi_ci_low", "roi_ci_high", "top_trade_removed_roi", "top3_removed_roi", "top5_removed_roi", "top10_removed_roi"}
    money_cols = {"pnl", "cost", "max_daily_loss", "pnl_per_active_day"}
    int_cols = {"rows", "dates", "cities", "wins", "losing_days", "roi_le_minus_50_days"}
    lines = ["| " + " | ".join(label for _, label in cols) + " |"]
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for _, row in df.head(max_rows).iterrows():
        vals: list[str] = []
        for key, _ in cols:
            val = row.get(key)
            if key in pct_cols or key.endswith("_roi") or key.endswith("_win_rate") or "roi_ci" in key:
                vals.append(pct(val))
            elif key in money_cols or key.endswith("_pnl") or key.endswith("_loss"):
                vals.append(money(val))
            elif key in int_cols and pd.notna(val):
                vals.append(str(int(val)))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "")
            else:
                vals.append("" if val is None or pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def bootstrap_ci(daily: pd.DataFrame, n_boot: int = 5000) -> tuple[float | None, float | None]:
    if len(daily) < 3:
        return (None, None)
    rng = np.random.default_rng(RNG_SEED)
    costs = daily["cost"].to_numpy(float)
    pnls = daily["pnl"].to_numpy(float)
    vals: list[float] = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(daily), len(daily))
        cost = float(costs[idx].sum())
        if cost > 0:
            vals.append(float(pnls[idx].sum() / cost))
    lo, hi = np.quantile(vals, [0.025, 0.975])
    return (float(lo), float(hi))


def normalize_candidate_frame(df: pd.DataFrame, dataset: str) -> pd.DataFrame:
    out = df.copy()
    numeric = ["ask", "edge", "model_p_yes", "payoff"]
    for col in numeric:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out[out["ask"].between(0.05, 0.20) & out["payoff"].isin([0.0, 1.0])].copy()
    out["dataset"] = dataset
    out["target_date"] = out["target_date"].astype(str)
    out["forecast_source"] = out["forecast_source"].fillna("")
    src = out["forecast_source"].str.lower()
    out["forecast_model"] = np.select([src.str.contains("gfs"), src.str.contains("ecmwf")], ["gfs", "ecmwf"], default="other")
    out["cost"] = 1.0
    out["shares"] = out["cost"] / out["ask"]
    out["pnl"] = out["payoff"] * out["shares"] - out["cost"]
    out["win"] = out["payoff"].astype(int)
    out["logit_model_p"] = logit(np.clip(out["model_p_yes"].astype(float), 0.001, 0.999))
    out["logit_ask"] = logit(np.clip(out["ask"].astype(float), 0.001, 0.999))
    out["decision_hour_utc"] = pd.to_datetime(out["decision_snapshot_ts_utc"], utc=True, errors="coerce").dt.hour
    return out.reset_index(drop=True)


def load_broad_rows() -> pd.DataFrame:
    df = pd.read_csv(BROAD_DETAILS, low_memory=False)
    df = df[df["family"].eq("fact_low_price_yes") & df["label"].eq("all_low_price_yes")].copy()
    return normalize_candidate_frame(df, "broad_no_dust")


def load_v1_rows() -> pd.DataFrame:
    df = pd.read_csv(V1_DETAILS, low_memory=False)
    df = df[
        df["selector"].eq("no_dust_edge20_ask05_20")
        & df["sizing"].eq("fixed5")
        & df["period"].isin(["historical", "forward"])
    ].copy()
    return normalize_candidate_frame(df, "v1_edge20")


def load_bias_index() -> dict[tuple[str, str], pd.DataFrame]:
    bias = pd.read_csv(BIAS_ROWS, low_memory=False)
    bias["date"] = bias["date"].astype(str)
    bias["error_f_actual_minus_forecast"] = pd.to_numeric(bias["error_f_actual_minus_forecast"], errors="coerce")
    bias = bias.dropna(subset=["city", "model", "date", "error_f_actual_minus_forecast"])
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for key, g in bias.sort_values("date").groupby(["city", "model"], dropna=False):
        out[(str(key[0]), str(key[1]))] = g[["date", "error_f_actual_minus_forecast"]].reset_index(drop=True)
    return out


def asof_summary(index: dict[tuple[str, str], pd.DataFrame], city: str, model: str, target_date: str) -> dict[str, float]:
    g = index.get((city, model))
    if g is None or g.empty:
        return {"bias_n": 0}
    x = g[g["date"] < target_date]["error_f_actual_minus_forecast"].dropna()
    if x.empty:
        return {"bias_n": 0}
    return {
        "bias_n": float(len(x)),
        "bias_mean": float(x.mean()),
        "bias_p50": float(x.quantile(0.50)),
        "bias_p90": float(x.quantile(0.90)),
        "hot_tail_pct": float((x >= 1.0).mean()),
        "hot_tail2_pct": float((x >= 2.0).mean()),
        "cold_tail_pct": float((x <= -1.0).mean()),
        "bias_mae": float(x.abs().mean()),
    }


def attach_asof_bias(df: pd.DataFrame, index: dict[tuple[str, str], pd.DataFrame]) -> pd.DataFrame:
    rows = [
        asof_summary(index, str(row.city), str(row.forecast_model), str(row.target_date))
        for row in df.itertuples(index=False)
    ]
    feats = pd.DataFrame(rows)
    out = pd.concat([df.reset_index(drop=True), feats.reset_index(drop=True)], axis=1)
    out["bias_n"] = pd.to_numeric(out["bias_n"], errors="coerce").fillna(0.0)
    return out


def periods_for(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    if df["dataset"].iloc[0] == "v1_edge20":
        historical = df[df["period"].eq("historical")]
        return {
            "train_pre_2026_06_21": historical[historical["target_date"] < HOLDOUT_START],
            "holdout_2026_06_21_26": historical[historical["target_date"] >= HOLDOUT_START],
            "forward_2026_06_27_30": df[df["period"].eq("forward")],
        }
    return {
        "train_pre_2026_06_21": df[df["target_date"] < HOLDOUT_START],
        "holdout_2026_06_21_26": df[df["target_date"] >= HOLDOUT_START],
    }


def model_pipeline(include_city: bool) -> tuple[Pipeline, list[str], list[str]]:
    cats = ["forecast_model"] + (["city"] if include_city else [])
    pre = ColumnTransformer(
        [
            ("num", Pipeline([("imp", SimpleImputer(strategy="median")), ("sc", StandardScaler())]), NUMERIC_FEATURES),
            (
                "cat",
                Pipeline(
                    [
                        ("imp", SimpleImputer(strategy="most_frequent")),
                        ("oh", OneHotEncoder(handle_unknown="ignore", min_frequency=5)),
                    ]
                ),
                cats,
            ),
        ]
    )
    pipe = Pipeline([("pre", pre), ("lr", LogisticRegression(C=0.2, solver="liblinear", max_iter=1000))])
    return pipe, NUMERIC_FEATURES, cats


def score_dataset(df: pd.DataFrame, include_city: bool, model_name: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    periods = periods_for(df)
    train = periods["train_pre_2026_06_21"]
    pipe, nums, cats = model_pipeline(include_city)
    pipe.fit(train[nums + cats], train["win"])
    out = df.copy()
    out["p_cal"] = pipe.predict_proba(out[nums + cats])[:, 1]
    out["p_cal_ev"] = out["p_cal"] / out["ask"] - 1.0
    out["pcal_model"] = model_name

    metrics: dict[str, Any] = {"dataset": df["dataset"].iloc[0], "model": model_name, "include_city": include_city}
    for period, frame in periods.items():
        if frame.empty:
            continue
        scored = out.loc[frame.index]
        metrics[f"{period}_rows"] = int(len(scored))
        metrics[f"{period}_positives"] = int(scored["win"].sum())
        metrics[f"{period}_p_mean"] = float(scored["p_cal"].mean())
        metrics[f"{period}_y_mean"] = float(scored["win"].mean())
        if scored["win"].nunique() > 1:
            metrics[f"{period}_auc"] = float(roc_auc_score(scored["win"], scored["p_cal"]))
            metrics[f"{period}_brier"] = float(brier_score_loss(scored["win"], scored["p_cal"]))
            metrics[f"{period}_log_loss"] = float(log_loss(scored["win"], scored["p_cal"]))
    return out, metrics


def pick_one_per_city_date(df: pd.DataFrame, mask: pd.Series, strategy: str) -> pd.DataFrame:
    g = df[mask].copy()
    if g.empty:
        return g
    g["strategy"] = strategy
    sort_cols = ["target_date", "city", "decision_snapshot_ts_utc"]
    if "p_cal_ev" in g.columns:
        sort_cols.append("p_cal_ev")
        return (
            g.sort_values(sort_cols, ascending=[True, True, True, False])
            .drop_duplicates(["target_date", "city"])
            .reset_index(drop=True)
        )
    return g.sort_values(sort_cols).drop_duplicates(["target_date", "city"]).reset_index(drop=True)


def summarize(frame: pd.DataFrame, strategy: str, period: str, dataset: str) -> dict[str, Any]:
    row: dict[str, Any] = {"dataset": dataset, "strategy": strategy, "period": period}
    if frame.empty:
        row.update({"rows": 0, "dates": 0, "cities": 0})
        return row
    daily = frame.groupby("target_date", as_index=False).agg(rows=("pnl", "size"), wins=("win", "sum"), cost=("cost", "sum"), pnl=("pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    ci_low, ci_high = bootstrap_ci(daily)
    sorted_pnl = frame.sort_values("pnl", ascending=False)
    def top_removed(n: int) -> float | None:
        rem = sorted_pnl.iloc[n:]
        if rem.empty or float(rem["cost"].sum()) <= 0:
            return None
        return float(rem["pnl"].sum() / rem["cost"].sum())
    row.update(
        {
            "rows": int(len(frame)),
            "dates": int(frame["target_date"].nunique()),
            "cities": int(frame["city"].nunique()),
            "wins": int(frame["win"].sum()),
            "win_rate": float(frame["win"].mean()),
            "avg_ask": float(frame["ask"].mean()),
            "avg_p_cal": float(frame["p_cal"].mean()) if "p_cal" in frame else None,
            "avg_ev": float(frame["p_cal_ev"].mean()) if "p_cal_ev" in frame else None,
            "cost": float(frame["cost"].sum()),
            "pnl": float(frame["pnl"].sum()),
            "roi": float(frame["pnl"].sum() / frame["cost"].sum()),
            "roi_ci_low": ci_low,
            "roi_ci_high": ci_high,
            "top_trade_removed_roi": top_removed(1),
            "top3_removed_roi": top_removed(3),
            "top5_removed_roi": top_removed(5),
            "top10_removed_roi": top_removed(10),
            "losing_days": int((daily["pnl"] < 0).sum()),
            "roi_le_minus_50_days": int((daily["roi"] <= -0.50).sum()),
            "max_daily_loss": float(daily["pnl"].min()),
            "pnl_per_active_day": float(frame["pnl"].sum() / frame["target_date"].nunique()),
        }
    )
    return row


def evaluate_strategies(scored_frames: list[pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    details: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    daily_rows: list[pd.DataFrame] = []

    for df in scored_frames:
        dataset = df["dataset"].iloc[0]
        model_name = df["pcal_model"].iloc[0]
        periods = periods_for(df)
        candidates: dict[str, pd.DataFrame] = {}
        if model_name.endswith("no_city"):
            candidates[f"{dataset}_baseline_all"] = pick_one_per_city_date(df, pd.Series(True, index=df.index), f"{dataset}_baseline_all")
        for thr in THRESHOLDS:
            name = f"{dataset}_{model_name}_ev_ge_{thr:g}"
            candidates[name] = pick_one_per_city_date(df, df["p_cal_ev"].ge(thr), name)

        for name, selected in candidates.items():
            if not selected.empty:
                details.append(selected)
            for period, base_period in periods.items():
                if selected.empty:
                    frame = selected
                else:
                    frame = selected[selected.index.isin(selected.index)]
                    # Select by dates/period membership to keep the one-per-city-date rows.
                    keys = set(zip(base_period["city"], base_period["target_date"], strict=False))
                    frame = selected[[tuple(x) in keys for x in zip(selected["city"], selected["target_date"], strict=False)]]
                summaries.append(summarize(frame, name, period, dataset))
                if not frame.empty:
                    d = frame.groupby("target_date", as_index=False).agg(rows=("pnl", "size"), wins=("win", "sum"), cost=("cost", "sum"), pnl=("pnl", "sum"))
                    d["roi"] = d["pnl"] / d["cost"]
                    d["dataset"] = dataset
                    d["strategy"] = name
                    d["period"] = period
                    daily_rows.append(d)

    return (
        pd.concat(details, ignore_index=True, sort=False) if details else pd.DataFrame(),
        pd.DataFrame(summaries),
        pd.concat(daily_rows, ignore_index=True, sort=False) if daily_rows else pd.DataFrame(),
    )


def station_slices(v1: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    train = v1[(v1["period"].eq("historical")) & (v1["target_date"] < HOLDOUT_START)]
    for feature in ["hot_tail_pct", "bias_mean", "bias_p90"]:
        qs = train[feature].quantile([0.33, 0.66]).to_list()
        for period, frame in periods_for(v1).items():
            for label, mask in [
                ("low", frame[feature] <= qs[0]),
                ("mid", (frame[feature] > qs[0]) & (frame[feature] <= qs[1])),
                ("high", frame[feature] > qs[1]),
            ]:
                g = frame[mask].copy()
                rows.append(summarize(g, f"{feature}_{label}", period, v1["dataset"].iloc[0]) | {"feature": feature, "bucket": label, "train_q33": qs[0], "train_q66": qs[1]})
    return pd.DataFrame(rows)


def render_report(summary: pd.DataFrame, model_metrics: pd.DataFrame, slices: pd.DataFrame) -> str:
    focus = summary[
        summary["strategy"].isin(
            [
                "broad_no_dust_baseline_all",
                "broad_no_dust_broad_no_dust_no_city_ev_ge_0.2",
                "broad_no_dust_broad_no_dust_no_city_ev_ge_0.5",
                "v1_edge20_baseline_all",
                "v1_edge20_v1_edge20_no_city_ev_ge_0.2",
                "v1_edge20_v1_edge20_no_city_ev_ge_0.5",
                "v1_edge20_v1_edge20_city_diag_ev_ge_0.2",
                "v1_edge20_v1_edge20_city_diag_ev_ge_0.5",
            ]
        )
    ].copy()
    pivot = focus.pivot_table(
        index=["dataset", "strategy"],
        columns="period",
        values=["rows", "dates", "win_rate", "avg_ask", "avg_p_cal", "roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi", "top10_removed_roi"],
        aggfunc="first",
    )
    pivot.columns = [f"{period}_{metric}" for metric, period in pivot.columns]
    pivot = pivot.reset_index()
    order = {
        "broad_no_dust_baseline_all": 0,
        "broad_no_dust_broad_no_dust_no_city_ev_ge_0.2": 1,
        "broad_no_dust_broad_no_dust_no_city_ev_ge_0.5": 2,
        "v1_edge20_baseline_all": 3,
        "v1_edge20_v1_edge20_no_city_ev_ge_0.2": 4,
        "v1_edge20_v1_edge20_no_city_ev_ge_0.5": 5,
        "v1_edge20_v1_edge20_city_diag_ev_ge_0.2": 6,
        "v1_edge20_v1_edge20_city_diag_ev_ge_0.5": 7,
    }
    pivot["rank"] = pivot["strategy"].map(order).fillna(99)
    pivot = pivot.sort_values("rank")

    model_metric_cols = [
        ("dataset", "dataset"),
        ("model", "model"),
        ("include_city", "city fixed"),
        ("train_pre_2026_06_21_rows", "train rows"),
        ("train_pre_2026_06_21_auc", "train AUC"),
        ("holdout_2026_06_21_26_rows", "holdout rows"),
        ("holdout_2026_06_21_26_auc", "holdout AUC"),
        ("forward_2026_06_27_30_rows", "fwd rows"),
        ("forward_2026_06_27_30_auc", "fwd AUC"),
    ]

    slice_focus = slices[
        slices["period"].isin(["train_pre_2026_06_21", "holdout_2026_06_21_26", "forward_2026_06_27_30"])
        & slices["bucket"].eq("high")
    ].copy()

    lines = [
        "# Low-Price YES Tail p_cal v1",
        "",
        f"Generated: {now_utc()}",
        "",
        "## Verdict",
        "",
        "`inconclusive` as a replacement selector.  The fabel5 direction is conceptually right, but this first calibrated-probability pass does not yet produce a clean live-valid alpha.",
        "",
        "What did hold up:",
        "",
        "- The v1 low-price tail universe still has real signal versus broad cheap YES.",
        "- As-of station-basis features carry explanatory information and should be logged.",
        "- Raw city fixed effects make the model look much stronger, which confirms the overfit risk rather than solving it.",
        "",
        "What did not hold up:",
        "",
        "- A no-city p_cal model trained on broad `ask 0.05..0.20` improves train but fails 6/21-6/26 holdout.",
        "- A no-city p_cal model inside the current v1 denominator does not improve the v1 baseline; stricter EV thresholds cut out winners in holdout/forward.",
        "- The only very attractive model includes city fixed effects, so it is not acceptable as a live rule without fresh forward proof.",
        "",
        "Action: keep `$1` v1 live unchanged; add p_cal/station-bias telemetry; do not switch selector or size up.",
        "",
        "Gate summary: `significance=FAIL`, `baseline=FAIL`, `forward=FAIL/NA`, `conclusion=inconclusive`.  The no-city p_cal filters do not beat the current v1 denominator out of sample; the city-fixed diagnostic is too proxy-heavy to count as confirmed alpha.",
        "",
        "## Evidence Window",
        "",
        f"- Broad rows: `{rel(BROAD_DETAILS)}` label `all_low_price_yes`, ask 0.05..0.20, settled through 2026-06-26.",
        f"- V1 rows: `{rel(V1_DETAILS)}` selector `no_dust_edge20_ask05_20`, historical through 2026-06-26 plus closed forward rows through 2026-06-30.",
        f"- As-of station bias: `{rel(BIAS_ROWS)}`; only errors with `date < target_date` enter each row.",
        "- Bracket distance remains a known gap: old May rows lack forecast max fields, so this p_cal v1 does not use bracket distance yet.",
        "",
        "## Model Quality",
        "",
        md_table(pd.DataFrame(model_metrics), model_metric_cols, max_rows=20),
        "",
        "## Strategy Comparison",
        "",
        md_table(
            pivot,
            [
                ("strategy", "strategy"),
                ("train_pre_2026_06_21_rows", "train rows"),
                ("train_pre_2026_06_21_win_rate", "train win"),
                ("train_pre_2026_06_21_avg_ask", "train ask"),
                ("train_pre_2026_06_21_roi", "train ROI"),
                ("train_pre_2026_06_21_roi_ci_low", "train CI low"),
                ("train_pre_2026_06_21_roi_ci_high", "train CI high"),
                ("holdout_2026_06_21_26_rows", "holdout rows"),
                ("holdout_2026_06_21_26_win_rate", "holdout win"),
                ("holdout_2026_06_21_26_roi", "holdout ROI"),
                ("forward_2026_06_27_30_rows", "fwd rows"),
                ("forward_2026_06_27_30_roi", "fwd ROI"),
                ("train_pre_2026_06_21_top10_removed_roi", "train top10 rm"),
            ],
            max_rows=20,
        ),
        "",
        "## Daily Risk",
        "",
        md_table(
            focus.sort_values(["dataset", "strategy", "period"]),
            [
                ("strategy", "strategy"),
                ("period", "period"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("cities", "cities"),
                ("roi", "ROI"),
                ("losing_days", "losing days"),
                ("roi_le_minus_50_days", "<= -50% days"),
                ("max_daily_loss", "max daily loss"),
                ("top5_removed_roi", "top5 rm"),
                ("top10_removed_roi", "top10 rm"),
            ],
            max_rows=80,
        ),
        "",
        "## Station-Basis Diagnostics",
        "",
        "High station-basis buckets are informative, but not yet a standalone selector.  The best-looking slices still rely on the existing v1 edge universe and are not fresh-forward validated.",
        "",
        md_table(
            slice_focus.sort_values(["feature", "period"]),
            [
                ("feature", "feature"),
                ("period", "period"),
                ("bucket", "bucket"),
                ("rows", "rows"),
                ("dates", "dates"),
                ("win_rate", "win"),
                ("avg_ask", "ask"),
                ("roi", "ROI"),
                ("top5_removed_roi", "top5 rm"),
            ],
            max_rows=40,
        ),
        "",
        "## Interpretation",
        "",
        "- fabel5 was right that source-aware v3 should be downgraded as a causal story.  Source is mostly a city/station proxy on this denominator.",
        "- But the first p_cal attempt also shows why we should not jump straight into a city/station selector.  Without raw city fixed effects, the calibrated EV filter is not better than v1 on holdout/forward.",
        "- The valuable next step is telemetry and a better feature layer: bracket distance, as-of station bias, book freshness/depth, and true fresh 7/03+ forward settlement.",
        "- If future p_cal needs raw city fixed effects to work, we should treat it as a city whitelist and reject it unless leave-one-city/leave-one-region tests survive.",
        "",
        "## Next Step",
        "",
        "Implement shadow columns on the running `$1` v1 journal: `p_cal_no_city`, `p_cal_city_diag`, `hot_tail_pct_asof`, `bias_p90_asof`, `bias_mean_asof`, `source_aware_v3`, and later `bracket_distance_f` once materialized.  The next promotion test should use only fresh rows after this telemetry exists.",
        "",
        "## Artifacts",
        "",
        f"- Script: `{rel(Path(__file__))}`",
        f"- JSON summary: `{rel(OUT_JSON)}`",
        f"- Details: `{rel(OUT_DIR / 'details.csv')}`",
        f"- Summary: `{rel(OUT_DIR / 'summary.csv')}`",
        f"- Daily: `{rel(OUT_DIR / 'daily.csv')}`",
        f"- Model metrics: `{rel(OUT_DIR / 'model_metrics.csv')}`",
        f"- Station slices: `{rel(OUT_DIR / 'station_slices.csv')}`",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bias_index = load_bias_index()
    broad = attach_asof_bias(load_broad_rows(), bias_index)
    v1 = attach_asof_bias(load_v1_rows(), bias_index)

    scored_frames: list[pd.DataFrame] = []
    metrics: list[dict[str, Any]] = []
    for dataset, frame in [("broad", broad), ("v1", v1)]:
        scored, metric = score_dataset(frame, include_city=False, model_name=f"{frame['dataset'].iloc[0]}_no_city")
        scored_frames.append(scored)
        metrics.append(metric)
        scored_city, metric_city = score_dataset(frame, include_city=True, model_name=f"{frame['dataset'].iloc[0]}_city_diag")
        scored_frames.append(scored_city)
        metrics.append(metric_city)

    details, summary, daily = evaluate_strategies(scored_frames)
    slices = station_slices(v1)

    details.to_csv(OUT_DIR / "details.csv", index=False)
    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    daily.to_csv(OUT_DIR / "daily.csv", index=False)
    pd.DataFrame(metrics).to_csv(OUT_DIR / "model_metrics.csv", index=False)
    slices.to_csv(OUT_DIR / "station_slices.csv", index=False)

    OUT_MD.write_text(render_report(summary, pd.DataFrame(metrics), slices), encoding="utf-8")

    payload = {
        "generated_at_utc": now_utc(),
        "verdict": "inconclusive",
        "strategy_family": "low_price_yes_tail_pcal_v1",
        "outputs": {
            "markdown": rel(OUT_MD),
            "summary_csv": rel(OUT_DIR / "summary.csv"),
            "details_csv": rel(OUT_DIR / "details.csv"),
            "daily_csv": rel(OUT_DIR / "daily.csv"),
            "model_metrics_csv": rel(OUT_DIR / "model_metrics.csv"),
            "station_slices_csv": rel(OUT_DIR / "station_slices.csv"),
        },
        "evidence_window": {
            "broad_rows": int(len(broad)),
            "broad_min_target_date": str(broad["target_date"].min()),
            "broad_max_target_date": str(broad["target_date"].max()),
            "v1_rows": int(len(v1)),
            "v1_min_target_date": str(v1["target_date"].min()),
            "v1_max_target_date": str(v1["target_date"].max()),
            "holdout_start": HOLDOUT_START,
            "forward_start": FORWARD_START,
        },
        "model_metrics": metrics,
        "summary": summary.to_dict(orient="records"),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    focus = summary[
        summary["strategy"].isin(
            [
                "broad_no_dust_broad_no_dust_no_city_ev_ge_0.5",
                "v1_edge20_v1_edge20_no_city_ev_ge_0.5",
                "v1_edge20_v1_edge20_city_diag_ev_ge_0.5",
            ]
        )
        & summary["period"].isin(["train_pre_2026_06_21", "holdout_2026_06_21_26", "forward_2026_06_27_30"])
    ]
    print(f"wrote {rel(OUT_MD)}")
    print(focus[["strategy", "period", "rows", "roi"]].to_string(index=False))


if __name__ == "__main__":
    main()
