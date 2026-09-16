#!/usr/bin/env python3
"""Source-policy A/B for current-bracket NO payoff model.

This keeps the payoff label, trade-base universe, thresholds, and first
city-day selection fixed while comparing forecast source policies:

- preferred_route: city calibration routed forecast from the 2026-06-24 replay.
- forced_gfs: same rows, but all forecast features are forced to the available
  GFS forecast for that city-date.
- preferred_gfs_route_only: a conservative source gate that only trades rows
  whose preferred route is already GFS.

The non-GFS history remains source-corrected replay rather than true
previous-day PIT.  The goal here is not live promotion; it is to isolate
whether the preferred-source routing is helping this expression.
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

import research_current_bracket_no_preferred_model_payoff_v1 as pref  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_source_policy_ab_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_POLICY = OUT_DIR / "policy_summary.csv"
OUT_CITY = OUT_DIR / "city_policy_summary.csv"
OUT_FORWARD = OUT_DIR / "forward_policy_summary.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-source-policy-ab-v1.md"

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


def apply_source_policy(df: pd.DataFrame, policy: str) -> pd.DataFrame:
    out = df.copy()
    if policy == "preferred_route":
        out["source_policy"] = policy
        return out
    if policy != "forced_gfs":
        raise ValueError(f"unknown source policy: {policy}")

    out["source_policy"] = policy
    route_is_gfs = out["forecast_route_model"].astype(str).eq("gfs")
    gfs_max = pd.to_numeric(out["gfs_forecast_max_native"], errors="coerce")
    gfs_max = gfs_max.mask(gfs_max.isna() & route_is_gfs, pd.to_numeric(out["forecast_max_native"], errors="coerce"))
    gfs_peak = pd.to_numeric(out["gfs_forecast_peak_hour_local"], errors="coerce")
    gfs_peak = gfs_peak.mask(gfs_peak.isna() & route_is_gfs, pd.to_numeric(out["forecast_peak_hour_local"], errors="coerce"))
    decision_hour = pd.to_numeric(out["decision_hour_local"], errors="coerce")
    running = pd.to_numeric(out["running_native"], errors="coerce")
    bracket_upper = pd.to_numeric(out["bracket_upper"], errors="coerce")
    out["forecast_route_model"] = "gfs"
    out["forecast_route_status"] = "forced_gfs_same_rows"
    out["forecast_max_native"] = gfs_max
    out["forecast_peak_hour_local"] = gfs_peak
    out["forecast_gap_to_bracket_upper_native"] = gfs_max - bracket_upper
    out["preferred_gap_to_running_native"] = gfs_max - running
    out["preferred_minus_gfs_gap_to_upper"] = 0.0
    out["forecast_peak_delta_hours_local"] = decision_hour - gfs_peak
    return out


def prepare_policy_frame(base: pd.DataFrame, policy: str, common_mask: pd.Series) -> pd.DataFrame:
    mask = common_mask.reindex(base.index, fill_value=False).astype(bool)
    frame = apply_source_policy(base.loc[mask].copy(), policy)
    frame["trade_base_policy"] = (
        frame["no_ask"].between(0.10, 0.35)
        & frame["depth5_notional"].ge(pref.STAKE_USD)
        & pd.to_numeric(frame["forecast_max_native"], errors="coerce").notna()
        & pd.to_numeric(frame["forecast_peak_hour_local"], errors="coerce").notna()
    )
    return frame.reset_index(drop=True)


def effective_gfs_available(df: pd.DataFrame) -> pd.Series:
    route_is_gfs = df["forecast_route_model"].astype(str).eq("gfs")
    gfs_max = pd.to_numeric(df["gfs_forecast_max_native"], errors="coerce")
    gfs_max = gfs_max.mask(gfs_max.isna() & route_is_gfs, pd.to_numeric(df["forecast_max_native"], errors="coerce"))
    gfs_peak = pd.to_numeric(df["gfs_forecast_peak_hour_local"], errors="coerce")
    gfs_peak = gfs_peak.mask(gfs_peak.isna() & route_is_gfs, pd.to_numeric(df["forecast_peak_hour_local"], errors="coerce"))
    return gfs_max.notna() & gfs_peak.notna()


def split_dates(df: pd.DataFrame) -> tuple[str, pd.Series]:
    dates = sorted(df["target_date"].dropna().astype(str).unique())
    split_idx = max(1, int(len(dates) * 0.70))
    split_date = dates[split_idx - 1]
    return split_date, df["target_date"].astype(str).le(split_date)


def train_policy_model(frame: pd.DataFrame, split_mask: pd.Series, label_col: str) -> tuple[Any, dict[str, Any]]:
    train = frame[split_mask & frame["trade_base_policy"]].copy()
    holdout = frame[~split_mask & frame["trade_base_policy"]].copy()
    pipe = pref.build_model()
    pipe.fit(train[pref.NUM_FEATURES + pref.CAT_FEATURES], train[label_col].astype(int))
    return pipe, {
        "train": pref.compute_model_metrics(pipe, train, label_col),
        "holdout": pref.compute_model_metrics(pipe, holdout, label_col),
    }


def add_predictions(frame: pd.DataFrame, split_mask: pd.Series) -> tuple[pd.DataFrame, dict[str, Any]]:
    out = frame.copy()
    out["period_split"] = np.where(split_mask, "train", "holdout")
    metrics: dict[str, Any] = {}
    for label_col, p_col, edge_col in [
        ("label_no_wins", "p_no_win", "edge_no_win"),
        ("label_up_margin", "p_up_margin", "edge_up_margin"),
    ]:
        pipe, label_metrics = train_policy_model(out, split_mask, label_col)
        out[p_col] = pipe.predict_proba(out[pref.NUM_FEATURES + pref.CAT_FEATURES])[:, 1]
        out[edge_col] = out[p_col] - pd.to_numeric(out["no_ask"], errors="coerce")
        metrics[label_col] = label_metrics
    return out, metrics


def variants_for(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    tb = frame[frame["trade_base_policy"]].copy()
    out = {
        "baseline_trade_base": tb,
        "payoff_ev05_p35": tb[tb["p_no_win"].ge(0.35) & tb["edge_no_win"].ge(0.05)].copy(),
        "payoff_ev10_p40": tb[tb["p_no_win"].ge(0.40) & tb["edge_no_win"].ge(0.10)].copy(),
        "up_margin_ev05_p30": tb[tb["p_up_margin"].ge(0.30) & tb["edge_up_margin"].ge(0.05)].copy(),
        "preferred_gfs_route_only": tb[
            tb["p_no_win"].ge(0.35)
            & tb["edge_no_win"].ge(0.05)
            & tb["forecast_route_model"].astype(str).eq("gfs")
        ].copy(),
    }
    out["payoff_ev05_p35_max2_day"] = pref.select_top_per_date(out["payoff_ev05_p35"], 2, "edge_no_win")
    return out


def summarize(policy: str, variant: str, raw: pd.DataFrame, baseline: pd.DataFrame | None) -> dict[str, Any]:
    row = pref.summarize_variant(variant, raw, baseline)
    row["source_policy"] = policy
    row["variant"] = variant
    row["trade_base_rows"] = int(raw["trade_base_policy"].sum()) if "trade_base_policy" in raw else None
    row["significance"] = (
        "PASS"
        if row.get("roi_ci_low") is not None and row.get("roi_ci_high") is not None and row["roi_ci_low"] > 0
        else "FAIL"
    )
    row["forward"] = "NA"
    row["conclusion"] = "inconclusive"
    return row


def city_summary(policy: str, variant: str, raw: pd.DataFrame) -> pd.DataFrame:
    selected = pref.select_first(raw)
    if selected.empty:
        return pd.DataFrame()
    out = (
        selected.groupby(["city", "forecast_route_model", "calibration_best_model"], dropna=False)
        .agg(
            trades=("city", "size"),
            dates=("target_date", "nunique"),
            wins=("label_no_wins", "sum"),
            cost_usd=("stake_cost_usd", "sum"),
            profit_usd=("stake_profit_usd", "sum"),
            avg_no_ask=("no_ask", "mean"),
            avg_p_no_win=("p_no_win", "mean"),
            avg_margin=("actual_margin_to_upper_native", "mean"),
        )
        .reset_index()
    )
    out["source_policy"] = policy
    out["variant"] = variant
    out["win_rate"] = out["wins"] / out["trades"]
    out["roi"] = out["profit_usd"] / out["cost_usd"]
    return out


def forward_eval(
    policy: str,
    historical: pd.DataFrame,
    historical_common: pd.Series,
    forward_base: pd.DataFrame,
    forward_common: pd.Series,
) -> list[dict[str, Any]]:
    split_date, split_mask = split_dates(historical)
    hist_policy = prepare_policy_frame(historical, policy, historical_common)
    hist_policy, _metrics = add_predictions(hist_policy, hist_policy["target_date"].astype(str).le(split_date))
    pipes: dict[str, Any] = {}
    train = hist_policy[hist_policy["target_date"].astype(str).le(split_date) & hist_policy["trade_base_policy"]].copy()
    for label_col in ["label_no_wins", "label_up_margin"]:
        pipe = pref.build_model()
        pipe.fit(train[pref.NUM_FEATURES + pref.CAT_FEATURES], train[label_col].astype(int))
        pipes[label_col] = pipe

    f = prepare_policy_frame(forward_base, policy, forward_common)
    if f.empty:
        return []
    f["p_no_win"] = pipes["label_no_wins"].predict_proba(f[pref.NUM_FEATURES + pref.CAT_FEATURES])[:, 1]
    f["p_up_margin"] = pipes["label_up_margin"].predict_proba(f[pref.NUM_FEATURES + pref.CAT_FEATURES])[:, 1]
    f["edge_no_win"] = f["p_no_win"] - pd.to_numeric(f["no_ask"], errors="coerce")
    f["edge_up_margin"] = f["p_up_margin"] - pd.to_numeric(f["no_ask"], errors="coerce")
    rows = []
    for variant, raw in variants_for(f).items():
        selected = pref.select_first(raw)
        if selected.empty:
            rows.append({"source_policy": policy, "variant": variant, "selected_trades": 0})
            continue
        settled = selected[selected["label_no_wins"].notna()].copy()
        profit = float(settled["stake_profit_usd"].sum()) if not settled.empty else 0.0
        cost = float(settled["stake_cost_usd"].sum()) if not settled.empty else 0.0
        rows.append(
            {
                "source_policy": policy,
                "variant": variant,
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


def render_md(payload: dict[str, Any], policy_df: pd.DataFrame, city_df: pd.DataFrame, forward_df: pd.DataFrame) -> str:
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
                    vals.append(f"{val:.3f}")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    focus_variants = ["baseline_trade_base", "payoff_ev05_p35", "payoff_ev10_p40", "payoff_ev05_p35_max2_day"]
    policy_focus = policy_df[policy_df["variant"].isin(focus_variants)].copy()
    city_focus = city_df[city_df["variant"].eq("payoff_ev05_p35")].sort_values(["source_policy", "roi"]).copy()
    lines = [
        "# Current-Bracket NO Source Policy A/B V1",
        "",
        "## 结论",
        "",
        "这轮固定 payoff label、交易分母、ask/depth、first city-day selection 和阈值，单独比较 forecast source policy。",
        "结果是：在当前可验证样本里，`forced_gfs` 明显优于 `preferred_route`；ECMWF/preferred routing 没有通过交易层验证。",
        "",
        "但这不是 live promotion。非 GFS 历史仍不是 strict previous-day PIT，6/21..6/23 forward 对两种 policy 都失败。",
        "",
        "结论等级：`inconclusive` / `shadow_only` / 不改 live。",
        "",
        "## 数据层",
        "",
        f"- Generated at UTC: `{payload['generated_at_utc']}`",
        f"- Historical rows: `{payload['dataset']['historical_rows']}`",
        f"- Common source rows: `{payload['dataset']['common_source_rows']}`",
        f"- Trade-base rows per policy: `{payload['dataset']['trade_base_rows_per_policy']}`",
        f"- Date range: `{payload['dataset']['date_min']}`..`{payload['dataset']['date_max']}`",
        f"- Split date: `{payload['dataset']['split_date']}`",
        "",
        "## Policy Summary",
        "",
        table(
            policy_focus,
            [
                "source_policy",
                "variant",
                "selected_trades",
                "active_dates",
                "cities",
                "no_win_rate",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "holdout_roi",
                "selected_all_loss_days",
            ],
        ),
        "",
        "## Forward 6/21..6/23",
        "",
        table(
            forward_df[forward_df["variant"].isin(focus_variants)],
            [
                "source_policy",
                "variant",
                "selected_trades",
                "settled_trades",
                "open_shadow_trades",
                "settled_win_rate",
                "settled_roi",
                "settled_profit_usd",
                "dates",
            ],
        ),
        "",
        "## City Detail: payoff_ev05_p35",
        "",
        table(
            city_focus,
            [
                "source_policy",
                "city",
                "forecast_route_model",
                "calibration_best_model",
                "trades",
                "dates",
                "win_rate",
                "roi",
                "avg_p_no_win",
                "avg_margin",
            ],
            limit=80,
        ),
        "",
        "## 读法",
        "",
        "1. `forced_gfs` 不是新 live rule，只是源策略反事实：同一批 rows 如果不用 ECMWF/preferred routing，会怎样。",
        "2. preferred-route 的坏处集中在 ECMWF/fallback-ECMWF 城市，但不是所有 ECMWF 城市都坏；所以不能写成 ECMWF 永久黑名单。",
        "3. 由于 forward 两边都没过，当前动作只能是：这个 current-bracket NO 表达先锁在 shadow/research，下一步补 day-regime classifier 和真实非 GFS PIT。",
        "",
        "## Files",
        "",
        f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        f"- Policy summary: `{OUT_POLICY.relative_to(ROOT)}`",
        f"- City summary: `{OUT_CITY.relative_to(ROOT)}`",
        f"- Forward summary: `{OUT_FORWARD.relative_to(ROOT)}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base, _forecast_rows, _forecast_stats = pref.load_dataset()
    split_date, split_mask = split_dates(base)
    common_mask = (
        base["no_ask"].between(0.10, 0.35)
        & base["depth5_notional"].ge(pref.STAKE_USD)
        & effective_gfs_available(base)
        & pd.to_numeric(base["forecast_max_native"], errors="coerce").notna()
        & pd.to_numeric(base["forecast_peak_hour_local"], errors="coerce").notna()
    )
    summaries = []
    city_parts = []
    model_metrics = {}
    for policy in ["preferred_route", "forced_gfs"]:
        frame = prepare_policy_frame(base, policy, common_mask)
        frame, metrics = add_predictions(frame, frame["target_date"].astype(str).le(split_date))
        model_metrics[policy] = metrics
        variants = variants_for(frame)
        baseline = pref.select_first(variants["baseline_trade_base"])
        for variant, raw in variants.items():
            summaries.append(summarize(policy, variant, raw, None if variant == "baseline_trade_base" else baseline))
            csum = city_summary(policy, variant, raw)
            if not csum.empty:
                city_parts.append(csum)

    policy_df = pd.DataFrame(summaries)
    city_df = pd.concat(city_parts, ignore_index=True) if city_parts else pd.DataFrame()

    forward_df = pd.DataFrame()
    forward_base, forward_stats = pref.load_forward_dataset()
    if not forward_base.empty:
        forward_common = (
            forward_base["no_ask"].between(0.10, 0.35)
            & forward_base["depth5_notional"].ge(pref.STAKE_USD)
            & effective_gfs_available(forward_base)
            & pd.to_numeric(forward_base["forecast_max_native"], errors="coerce").notna()
            & pd.to_numeric(forward_base["forecast_peak_hour_local"], errors="coerce").notna()
        )
        rows = []
        for policy in ["preferred_route", "forced_gfs"]:
            rows.extend(forward_eval(policy, base, common_mask, forward_base, forward_common))
        forward_df = pd.DataFrame(rows)

    policy_df.to_csv(OUT_POLICY, index=False)
    city_df.to_csv(OUT_CITY, index=False)
    forward_df.to_csv(OUT_FORWARD, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_source_policy_ab_v1",
        "dataset": {
            "historical_rows": int(len(base)),
            "common_source_rows": int(common_mask.sum()),
            "trade_base_rows_per_policy": int(common_mask.sum()),
            "date_min": str(base["target_date"].min()),
            "date_max": str(base["target_date"].max()),
            "split_date": split_date,
            "forward_stats": forward_stats,
        },
        "model_metrics": finite_or_none(model_metrics),
        "policy_summary": finite_or_none(policy_df.to_dict(orient="records")),
        "city_summary": finite_or_none(city_df.to_dict(orient="records")),
        "forward_summary": finite_or_none(forward_df.to_dict(orient="records")),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "policy_summary_csv": str(OUT_POLICY.relative_to(ROOT)),
            "city_policy_summary_csv": str(OUT_CITY.relative_to(ROOT)),
            "forward_policy_summary_csv": str(OUT_FORWARD.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "inconclusive_shadow_only",
            "live_ready": False,
            "reason": "Forced-GFS beats preferred-route in historical counterfactual, but forward still fails and non-GFS replay is not strict PIT.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, policy_df, city_df, forward_df), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
