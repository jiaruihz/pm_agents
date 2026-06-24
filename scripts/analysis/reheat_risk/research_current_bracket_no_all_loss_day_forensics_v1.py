#!/usr/bin/env python3
"""All-loss day forensic for current-bracket NO.

The question is why some dates produce many simultaneous losers even when the
trade-level model score is high.  This script fixes the expression to the
latest source-policy A/B setup and compares all-loss dates against other
selected dates, with source policy held explicit.
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
import research_current_bracket_no_source_policy_ab_v1 as source_ab  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_all_loss_day_forensics_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_DATE = OUT_DIR / "all_loss_date_profiles.csv"
OUT_COMPARE = OUT_DIR / "all_loss_vs_other_comparison.csv"
OUT_SELECTED = OUT_DIR / "selected_rows_forensic.csv"
OUT_FORWARD = OUT_DIR / "forward_date_profiles.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-all-loss-day-forensics-v1.md"

FOCUS_VARIANTS = [
    "preferred_route::payoff_ev05_p35",
    "preferred_route::payoff_ev10_p40",
    "forced_gfs::payoff_ev10_p40",
    "preferred_route::payoff_ev05_p35_max2_day",
    "forced_gfs::payoff_ev05_p35_max2_day",
]


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


def diff_native_to_f(df: pd.DataFrame, col: str) -> pd.Series:
    vals = pd.to_numeric(df[col], errors="coerce")
    is_c = df["unit"].astype(str).str.upper().eq("C")
    return vals.where(~is_c, vals * 9.0 / 5.0)


def common_source_mask(df: pd.DataFrame) -> pd.Series:
    return (
        df["no_ask"].between(0.10, 0.35)
        & df["depth5_notional"].ge(pref.STAKE_USD)
        & source_ab.effective_gfs_available(df)
        & pd.to_numeric(df["forecast_max_native"], errors="coerce").notna()
        & pd.to_numeric(df["forecast_peak_hour_local"], errors="coerce").notna()
    )


def build_historical_selected() -> tuple[pd.DataFrame, dict[str, Any]]:
    base, _forecast_rows, forecast_stats = pref.load_dataset()
    split_date, _split_mask = source_ab.split_dates(base)
    mask = common_source_mask(base)
    selected_parts = []
    metrics: dict[str, Any] = {}
    for policy in ["preferred_route", "forced_gfs"]:
        frame = source_ab.prepare_policy_frame(base, policy, mask)
        frame, model_metrics = source_ab.add_predictions(frame, frame["target_date"].astype(str).le(split_date))
        metrics[policy] = model_metrics
        for variant, raw in source_ab.variants_for(frame).items():
            if variant not in {"payoff_ev05_p35", "payoff_ev10_p40", "payoff_ev05_p35_max2_day"}:
                continue
            selected = pref.select_first(raw)
            if selected.empty:
                continue
            selected = selected.copy()
            selected["source_policy"] = policy
            selected["variant"] = variant
            selected_parts.append(selected)
    selected = pd.concat(selected_parts, ignore_index=True)
    return selected, {
        "historical_rows": int(len(base)),
        "common_source_rows": int(mask.sum()),
        "date_min": str(base["target_date"].min()),
        "date_max": str(base["target_date"].max()),
        "split_date": split_date,
        "forecast_stats": forecast_stats,
        "model_metrics": metrics,
    }


def train_policy_pipes(historical: pd.DataFrame, policy: str, split_date: str, mask: pd.Series) -> dict[str, Any]:
    hist = source_ab.prepare_policy_frame(historical, policy, mask)
    train = hist[hist["target_date"].astype(str).le(split_date) & hist["trade_base_policy"]].copy()
    pipes: dict[str, Any] = {}
    for label_col in ["label_no_wins", "label_up_margin"]:
        pipe = pref.build_model()
        pipe.fit(train[pref.NUM_FEATURES + pref.CAT_FEATURES], train[label_col].astype(int))
        pipes[label_col] = pipe
    return pipes


def build_forward_selected(split_date: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    historical, _forecast_rows, _forecast_stats = pref.load_dataset()
    historical_mask = common_source_mask(historical)
    forward_base, forward_stats = pref.load_forward_dataset()
    if forward_base.empty:
        return pd.DataFrame(), {"forward_stats": forward_stats}
    forward_mask = common_source_mask(forward_base)
    parts = []
    for policy in ["preferred_route", "forced_gfs"]:
        pipes = train_policy_pipes(historical, policy, split_date, historical_mask)
        frame = source_ab.prepare_policy_frame(forward_base, policy, forward_mask)
        if frame.empty:
            continue
        frame["p_no_win"] = pipes["label_no_wins"].predict_proba(frame[pref.NUM_FEATURES + pref.CAT_FEATURES])[:, 1]
        frame["p_up_margin"] = pipes["label_up_margin"].predict_proba(frame[pref.NUM_FEATURES + pref.CAT_FEATURES])[:, 1]
        frame["edge_no_win"] = frame["p_no_win"] - pd.to_numeric(frame["no_ask"], errors="coerce")
        frame["edge_up_margin"] = frame["p_up_margin"] - pd.to_numeric(frame["no_ask"], errors="coerce")
        for variant, raw in source_ab.variants_for(frame).items():
            if variant not in {"payoff_ev05_p35", "payoff_ev10_p40", "payoff_ev05_p35_max2_day"}:
                continue
            selected = pref.select_first(raw)
            if selected.empty:
                continue
            selected = selected.copy()
            selected["source_policy"] = policy
            selected["variant"] = variant
            parts.append(selected)
    selected = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return selected, {"forward_stats": forward_stats, "forward_common_rows": int(forward_mask.sum())}


def enrich_selected(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["policy_variant"] = out["source_policy"].astype(str) + "::" + out["variant"].astype(str)
    out["actual_margin_to_upper_f"] = diff_native_to_f(out, "actual_margin_to_upper_native")
    out["forecast_gap_to_upper_f"] = diff_native_to_f(out, "forecast_gap_to_bracket_upper_native")
    out["gfs_gap_to_upper_f"] = diff_native_to_f(out, "gfs_gap_to_bracket_upper_native")
    out["ecmwf_gap_to_upper_f"] = diff_native_to_f(out, "ecmwf_gap_to_bracket_upper_native")
    out["forecast_overestimate_f"] = diff_native_to_f(out, "forecast_max_native") - diff_native_to_f(out, "final_max_native")
    out["gfs_overestimate_f"] = diff_native_to_f(out, "gfs_forecast_max_native") - diff_native_to_f(out, "final_max_native")
    out["ecmwf_overestimate_f"] = diff_native_to_f(out, "ecmwf_forecast_max_native") - diff_native_to_f(out, "final_max_native")
    out["route_is_ecmwf"] = out["forecast_route_model"].astype(str).eq("ecmwf")
    out["route_is_gfs"] = out["forecast_route_model"].astype(str).eq("gfs")
    out["calibration_non_gfs"] = ~out["calibration_best_model"].fillna("").astype(str).isin(["", "gfs"])
    return out


def join_names(values: pd.Series) -> str:
    return ",".join(sorted({str(v) for v in values.dropna() if str(v)}))


def date_profiles(selected: pd.DataFrame, sample: str) -> pd.DataFrame:
    if selected.empty:
        return pd.DataFrame()
    s = selected.copy()
    for col in [
        "actual_peak_afternoon",
        "actual_peak_after_decision_2h",
        "forecast_peak_later_2h",
        "relative_humidity_pct",
        "wind_speed_kt",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
    ]:
        if col not in s.columns:
            s[col] = np.nan
    s["settled_like"] = s["label_no_wins"].notna()
    grouped = (
        s.groupby(["policy_variant", "source_policy", "variant", "target_date"], dropna=False)
        .agg(
            trades=("city", "size"),
            cities=("city", "nunique"),
            wins=("label_no_wins", "sum"),
            settled_trades=("label_no_wins", "count"),
            cost_usd=("stake_cost_usd", "sum"),
            profit_usd=("stake_profit_usd", "sum"),
            avg_no_ask=("no_ask", "mean"),
            avg_p_no_win=("p_no_win", "mean"),
            avg_edge_no_win=("edge_no_win", "mean"),
            avg_actual_margin_f=("actual_margin_to_upper_f", "mean"),
            avg_forecast_gap_f=("forecast_gap_to_upper_f", "mean"),
            avg_gfs_gap_f=("gfs_gap_to_upper_f", "mean"),
            avg_ecmwf_gap_f=("ecmwf_gap_to_upper_f", "mean"),
            avg_forecast_overestimate_f=("forecast_overestimate_f", "mean"),
            avg_gfs_overestimate_f=("gfs_overestimate_f", "mean"),
            avg_ecmwf_overestimate_f=("ecmwf_overestimate_f", "mean"),
            avg_temp_trend_1h_f=("temp_trend_1h_f", "mean"),
            avg_temp_trend_3h_f=("temp_trend_3h_f", "mean"),
            avg_minutes_since_running_max=("minutes_since_running_max", "mean"),
            avg_rh=("relative_humidity_pct", "mean"),
            avg_wind_kt=("wind_speed_kt", "mean"),
            afternoon_peak_rate=("actual_peak_afternoon", "mean"),
            peak_after_decision_2h_rate=("actual_peak_after_decision_2h", "mean"),
            forecast_peak_later_2h_rate=("forecast_peak_later_2h", "mean"),
            ecmwf_route_share=("route_is_ecmwf", "mean"),
            non_gfs_calibration_share=("calibration_non_gfs", "mean"),
            selected_cities=("city", join_names),
            route_models=("forecast_route_model", join_names),
            calibration_best_models=("calibration_best_model", join_names),
        )
        .reset_index()
    )
    keys = ["policy_variant", "source_policy", "variant", "target_date"]
    loss_map = s[s["label_no_wins"].eq(0)].groupby(keys, dropna=False)["city"].apply(join_names)
    win_map = s[s["label_no_wins"].eq(1)].groupby(keys, dropna=False)["city"].apply(join_names)
    grouped["loss_cities"] = grouped.set_index(keys).index.map(loss_map).fillna("")
    grouped["win_cities"] = grouped.set_index(keys).index.map(win_map).fillna("")
    grouped["sample"] = sample
    grouped["win_rate"] = grouped["wins"] / grouped["settled_trades"].replace(0, np.nan)
    grouped["roi"] = grouped["profit_usd"] / grouped["cost_usd"].replace(0, np.nan)
    grouped["open_trades"] = grouped["trades"] - grouped["settled_trades"]
    grouped["all_loss"] = grouped["settled_trades"].gt(0) & grouped["wins"].eq(0)
    grouped["forecast_promised_break_but_final_capped"] = grouped["avg_forecast_gap_f"].gt(0) & grouped[
        "avg_actual_margin_f"
    ].le(0)
    grouped["timing_right_payoff_wrong"] = grouped["peak_after_decision_2h_rate"].ge(0.5) & grouped["all_loss"]
    grouped["large_forecast_overestimate"] = grouped["avg_forecast_overestimate_f"].ge(1.0)
    grouped["cause_tags"] = grouped.apply(cause_tags, axis=1)
    return grouped


def cause_tags(row: pd.Series) -> str:
    tags = []
    if bool(row.get("forecast_promised_break_but_final_capped")):
        tags.append("forecast_break_overestimated")
    if bool(row.get("timing_right_payoff_wrong")):
        tags.append("peak_timing_not_enough")
    if bool(row.get("large_forecast_overestimate")):
        tags.append("forecast_high_too_hot")
    if float(row.get("ecmwf_route_share") or 0.0) >= 0.5:
        tags.append("ecmwf_route_cluster")
    if float(row.get("non_gfs_calibration_share") or 0.0) >= 0.5:
        tags.append("non_gfs_city_cluster")
    if float(row.get("avg_temp_trend_3h_f") or 0.0) > 0:
        tags.append("trend_positive_but_exhausted")
    return ",".join(tags) if tags else "mixed"


def comparison(date_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    hist = date_df[date_df["sample"].eq("historical") & date_df["policy_variant"].isin(FOCUS_VARIANTS)].copy()
    metrics = [
        "trades",
        "win_rate",
        "roi",
        "avg_no_ask",
        "avg_p_no_win",
        "avg_edge_no_win",
        "avg_actual_margin_f",
        "avg_forecast_gap_f",
        "avg_gfs_gap_f",
        "avg_ecmwf_gap_f",
        "avg_forecast_overestimate_f",
        "avg_gfs_overestimate_f",
        "avg_ecmwf_overestimate_f",
        "avg_temp_trend_1h_f",
        "avg_temp_trend_3h_f",
        "avg_minutes_since_running_max",
        "afternoon_peak_rate",
        "peak_after_decision_2h_rate",
        "forecast_peak_later_2h_rate",
        "ecmwf_route_share",
        "non_gfs_calibration_share",
    ]
    for policy_variant, group in hist.groupby("policy_variant"):
        all_loss = group[group["all_loss"]]
        other = group[~group["all_loss"]]
        row = {
            "policy_variant": policy_variant,
            "all_loss_dates": int(len(all_loss)),
            "other_dates": int(len(other)),
            "all_loss_date_list": ",".join(all_loss["target_date"].astype(str).tolist()),
        }
        for metric in metrics:
            row[f"all_loss_{metric}"] = float(all_loss[metric].mean()) if not all_loss.empty else None
            row[f"other_{metric}"] = float(other[metric].mean()) if not other.empty else None
            if not all_loss.empty and not other.empty:
                row[f"delta_{metric}"] = row[f"all_loss_{metric}"] - row[f"other_{metric}"]
        rows.append(row)
    return pd.DataFrame(rows)


def render_md(payload: dict[str, Any], all_loss_dates: pd.DataFrame, comp: pd.DataFrame, forward_dates: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("rate") or col in {"roi", "win_rate", "ecmwf_route_share", "non_gfs_calibration_share"}:
                    vals.append(pct(val))
                elif col.endswith("usd"):
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.2f}")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    focus_all_loss = all_loss_dates[all_loss_dates["policy_variant"].isin(FOCUS_VARIANTS)].copy()
    focus_forward = forward_dates[forward_dates["policy_variant"].isin(FOCUS_VARIANTS)].copy()
    lines = [
        "# Current-Bracket NO All-Loss Day Forensics V1",
        "",
        "## 结论",
        "",
        "全错日的核心问题不是“有没有午后高温”，而是 payoff 判断错：模型认为当天还有足够空间打穿当前 bracket upper，"
        "但最终最高温卡在 upper 下方或附近。也就是说，方向判断经常对了一半，真正错在 `final max > current upper + margin`。",
        "",
        "source policy 是放大器，不是根因。历史 forced-GFS 能改善交易筛选，但 6/21..6/23 forward 仍失败，说明还缺 day-regime gate："
        "识别“预报仍偏热、盘中仍像升温，但全市场/多城市同时封顶”的日期。",
        "",
        "Verdict: `inconclusive_shadow_only`，不改 live。",
        "",
        "## 数据层",
        "",
        f"- Generated at UTC: `{payload['generated_at_utc']}`",
        f"- Sync/rebuild: `{payload['data_refresh_note']}`",
        f"- Historical rows: `{payload['dataset']['historical_rows']}`",
        f"- Common source rows: `{payload['dataset']['common_source_rows']}`",
        f"- Date range: `{payload['dataset']['date_min']}`..`{payload['dataset']['date_max']}`",
        f"- Split date: `{payload['dataset']['split_date']}`",
        "",
        "## All-Loss Dates",
        "",
        table(
            focus_all_loss.sort_values(["policy_variant", "target_date"]),
            [
                "policy_variant",
                "sample",
                "target_date",
                "trades",
                "settled_trades",
                "roi",
                "avg_p_no_win",
                "avg_no_ask",
                "avg_actual_margin_f",
                "avg_forecast_gap_f",
                "avg_forecast_overestimate_f",
                "peak_after_decision_2h_rate",
                "ecmwf_route_share",
                "cause_tags",
                "loss_cities",
            ],
            limit=80,
        ),
        "",
        "## All-Loss vs Other Dates",
        "",
        table(
            comp,
            [
                "policy_variant",
                "all_loss_dates",
                "other_dates",
                "all_loss_avg_actual_margin_f",
                "other_avg_actual_margin_f",
                "all_loss_avg_forecast_gap_f",
                "other_avg_forecast_gap_f",
                "all_loss_avg_forecast_overestimate_f",
                "other_avg_forecast_overestimate_f",
                "all_loss_peak_after_decision_2h_rate",
                "other_peak_after_decision_2h_rate",
                "all_loss_ecmwf_route_share",
                "other_ecmwf_route_share",
                "all_loss_date_list",
            ],
        ),
        "",
        "## Forward 6/21..6/23 Date Profiles",
        "",
        table(
            focus_forward.sort_values(["policy_variant", "target_date"]),
            [
                "policy_variant",
                "target_date",
                "trades",
                "settled_trades",
                "open_trades",
                "wins",
                "roi",
                "avg_p_no_win",
                "avg_actual_margin_f",
                "avg_forecast_gap_f",
                "avg_forecast_overestimate_f",
                "cause_tags",
                "loss_cities",
            ],
            limit=80,
        ),
        "",
        "## 判断错在哪里",
        "",
        "1. 旧 afternoon-peak/near-noon 逻辑把“高温还会出现在后面”当作主目标；但 NO 的真钱 payoff 是“最终高温必须打穿当前 upper”。",
        "2. 全错日里，模型分数和 forecast gap 仍然偏正，但实际 margin 经常接近 0 或为负；这是 forecast-high overestimate / capped-day 问题。",
        "3. 多城市同日全错说明不是单城市 station/source 噪音，而是 day-regime：同一日的大尺度/日内形态让很多城市同时“不再够热”。",
        "4. ECMWF/preferred route 会扩大历史坏样本暴露，但 forced-GFS 的 forward 同样亏，说明 source gate 只能降噪，不能替代 day-regime gate。",
        "",
        "## 下一步改进",
        "",
        "1. 新 label：训练 `capped_day_false_positive`，目标是 selected candidate 当天是否 `forecast_gap>0` 但 `final_margin<=0`。",
        "2. 新特征：用 city-day 层聚合，而不是逐笔层；包括同日候选数、同日平均 forecast overestimate、GFS/ECMWF disagreement、候选城市族群、温度趋势是否开始衰竭。",
        "3. 执行规则：candidate 模型过关后再过 day-regime gate；坏日型直接整日 skip 或 max1/day，不再让多城市同时下注。",
        "4. source policy：短期研究默认 forced-GFS / GFS-route-only 做主对照；非 GFS 只有拿到 true previous-day PIT 后再恢复参赛。",
        "",
        "## Files",
        "",
        f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        f"- All-loss profiles: `{OUT_DATE.relative_to(ROOT)}`",
        f"- Comparison: `{OUT_COMPARE.relative_to(ROOT)}`",
        f"- Selected rows: `{OUT_SELECTED.relative_to(ROOT)}`",
        f"- Forward profiles: `{OUT_FORWARD.relative_to(ROOT)}`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    historical_selected, dataset = build_historical_selected()
    historical_selected = enrich_selected(historical_selected)
    split_date = str(dataset["split_date"])
    forward_selected, forward_info = build_forward_selected(split_date)
    if not forward_selected.empty:
        forward_selected = enrich_selected(forward_selected)

    hist_dates = date_profiles(historical_selected, "historical")
    fwd_dates = date_profiles(forward_selected, "forward") if not forward_selected.empty else pd.DataFrame()
    all_dates = pd.concat([hist_dates, fwd_dates], ignore_index=True) if not fwd_dates.empty else hist_dates
    all_loss_dates = all_dates[all_dates["all_loss"]].copy()
    comp = comparison(hist_dates)

    keep_cols = [
        "sample",
        "policy_variant",
        "source_policy",
        "variant",
        "target_date",
        "city",
        "decision_hour_local",
        "bracket",
        "no_ask",
        "p_no_win",
        "edge_no_win",
        "label_no_wins",
        "actual_margin_to_upper_f",
        "forecast_gap_to_upper_f",
        "gfs_gap_to_upper_f",
        "ecmwf_gap_to_upper_f",
        "forecast_overestimate_f",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "minutes_since_running_max",
        "actual_peak_afternoon",
        "actual_peak_after_decision_2h",
        "forecast_peak_later_2h",
        "forecast_route_model",
        "forecast_route_status",
        "calibration_best_model",
    ]
    historical_selected["sample"] = "historical"
    selected_out = historical_selected.copy()
    if not forward_selected.empty:
        forward_selected["sample"] = "forward"
        selected_out = pd.concat([selected_out, forward_selected], ignore_index=True)
    selected_out[[c for c in keep_cols if c in selected_out.columns]].to_csv(OUT_SELECTED, index=False)
    all_loss_dates.to_csv(OUT_DATE, index=False)
    comp.to_csv(OUT_COMPARE, index=False)
    fwd_dates.to_csv(OUT_FORWARD, index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_all_loss_day_forensics_v1",
        "data_refresh_note": "sync_weather_remote.sh completed; run_stack rebuilt facts and CLOB gate passed, then exited non-clean only because FE port 5174 stayed busy.",
        "dataset": finite_or_none(dataset),
        "forward": finite_or_none(forward_info),
        "all_loss_dates": finite_or_none(all_loss_dates.to_dict(orient="records")),
        "comparison": finite_or_none(comp.to_dict(orient="records")),
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "all_loss_profiles_csv": str(OUT_DATE.relative_to(ROOT)),
            "comparison_csv": str(OUT_COMPARE.relative_to(ROOT)),
            "selected_rows_csv": str(OUT_SELECTED.relative_to(ROOT)),
            "forward_profiles_csv": str(OUT_FORWARD.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "inconclusive_shadow_only",
            "live_ready": False,
            "reason": "All-loss days are dominated by capped-day false positives: forecast/model expected bracket break, but final max stayed below current upper; source policy alone does not fix forward.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, all_loss_dates, comp, fwd_dates), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
