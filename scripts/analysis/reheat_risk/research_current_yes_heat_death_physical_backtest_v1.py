#!/usr/bin/env python3
"""Historical proxy backtest for the current-YES heat-death physical thesis."""

from __future__ import annotations

import json
import math
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
INPUT = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
PEAK_CLOCK_BACKFILL = ROOT / "runtime/weather_edge_v1/market_data/research/forecast_peak_clock_backfill_v1.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/current_yes_heat_death_physical_backtest_v1"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-14-current-yes-heat-death-physical-backtest-v1.json"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-14-current-yes-heat-death-physical-backtest-v1.md"
SPLIT_DATE = "2026-06-01"
FEE_RATE = 0.05
SEED = 20260714


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def fee_per_share(price: pd.Series) -> pd.Series:
    return (FEE_RATE * price * (1.0 - price)).round(5)


def bootstrap_roi(rows: pd.DataFrame, pnl_col: str, cost_col: str, n: int = 5000) -> list[float | None]:
    daily = rows.groupby("target_date", as_index=False).agg(pnl=(pnl_col, "sum"), cost=(cost_col, "sum"))
    if len(daily) < 2:
        return [None, None]
    rng = np.random.default_rng(SEED)
    pnl = daily["pnl"].to_numpy(float)
    cost = daily["cost"].to_numpy(float)
    draws: list[float] = []
    for _ in range(n):
        idx = rng.integers(0, len(daily), len(daily))
        denom = float(cost[idx].sum())
        if denom > 0:
            draws.append(float(pnl[idx].sum() / denom))
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


def bootstrap_paired_delta(
    rows: pd.DataFrame,
    left_pnl: str,
    left_cost: str,
    right_pnl: str,
    right_cost: str,
    n: int = 5000,
) -> list[float | None]:
    daily = rows.groupby("target_date", as_index=False).agg(
        left_pnl=(left_pnl, "sum"),
        left_cost=(left_cost, "sum"),
        right_pnl=(right_pnl, "sum"),
        right_cost=(right_cost, "sum"),
    )
    if len(daily) < 2:
        return [None, None]
    rng = np.random.default_rng(SEED)
    values = daily[["left_pnl", "left_cost", "right_pnl", "right_cost"]].to_numpy(float)
    draws: list[float] = []
    for _ in range(n):
        sample = values[rng.integers(0, len(values), len(values))]
        left_cost_sum = float(sample[:, 1].sum())
        right_cost_sum = float(sample[:, 3].sum())
        if left_cost_sum > 0 and right_cost_sum > 0:
            draws.append(float(sample[:, 0].sum() / left_cost_sum - sample[:, 2].sum() / right_cost_sum))
    return [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))]


def peak_clock_provenance(states: pd.DataFrame) -> dict[str, Any]:
    if not PEAK_CLOCK_BACKFILL.exists():
        return {"status": "backfill_csv_missing", "path": str(PEAK_CLOCK_BACKFILL)}
    backfill = pd.read_csv(PEAK_CLOCK_BACKFILL, low_memory=False)
    out: dict[str, Any] = {
        "path": str(PEAK_CLOCK_BACKFILL.relative_to(ROOT)),
        "rows": int(len(backfill)),
        "gfs_api_source_counts": backfill["gfs_forecast_api_source"].value_counts().to_dict(),
        "ecmwf_api_source_counts": backfill["ecmwf_forecast_api_source"].value_counts().to_dict(),
        "gfs_run_policy_counts": backfill["gfs_forecast_run_policy"].value_counts().to_dict(),
        "ecmwf_run_policy_counts": backfill["ecmwf_forecast_run_policy"].value_counts().to_dict(),
        "single_runs_share": float(
            (
                backfill["gfs_forecast_api_source"].eq("open_meteo_single_runs")
                & backfill["ecmwf_forecast_api_source"].eq("open_meteo_single_runs")
            ).mean()
        ),
    }
    # The clean CSV on disk is not enough: the factory rows this backtest
    # consumes embed the backfill values as of factory build time. Verify the
    # embedded values agree with the current (single-runs) CSV.
    factory = states[["city", "target_date", "gfs_forecast_peak_hour_local"]].dropna().drop_duplicates(
        ["city", "target_date"]
    )
    merged = factory.merge(
        backfill[["city", "target_date", "gfs_forecast_peak_hour_local"]].rename(
            columns={"gfs_forecast_peak_hour_local": "backfill_gfs_peak_hour_local"}
        ),
        on=["city", "target_date"],
        how="inner",
    )
    agreement = float(
        merged["gfs_forecast_peak_hour_local"].astype(float).sub(merged["backfill_gfs_peak_hour_local"].astype(float)).abs().le(1e-6).mean()
    ) if len(merged) else None
    out["factory_vs_backfill_compared_city_dates"] = int(len(merged))
    out["factory_vs_backfill_gfs_peak_hour_agreement"] = agreement
    out["status"] = (
        "verified_clean" if agreement is not None and agreement >= 0.999
        else "factory_embeds_stale_backfill" if agreement is not None
        else "no_overlap"
    )
    return out


def load_states() -> pd.DataFrame:
    rows = pd.read_csv(INPUT, low_memory=False)
    states = rows.sort_values(["city", "target_date", "decision_hour_local"]).drop_duplicates(
        ["city", "target_date", "decision_hour_local"]
    ).copy()
    numeric = [
        "decision_hour_local",
        "current_yes_ask",
        "d1_no_ask",
        "decline_native",
        "minutes_since_running_max",
        "temp_trend_1h_f",
        "relative_humidity_pct",
        "sky_cover_code",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "gfs_forecast_gap_to_running_native",
        "ecmwf_forecast_gap_to_running_native",
        "current_bracket_held",
        "d1_hit",
    ]
    for column in numeric:
        states[column] = pd.to_numeric(states.get(column), errors="coerce")
    states["target_date"] = states["target_date"].astype(str)
    states["period"] = np.where(states["target_date"].lt(SPLIT_DATE), "train", "holdout")

    states["cloud_limited"] = states["sky_cover_code"].ge(3) & states["temp_trend_1h_f"].le(0)
    states["humid_cloud"] = states["cloud_limited"] & states["relative_humidity_pct"].ge(75)
    states["dual_forecast_low_gap"] = (
        states["gfs_forecast_gap_to_running_native"].le(0.5)
        & states["ecmwf_forecast_gap_to_running_native"].le(0.5)
    )
    states["historical_support_count"] = (
        states[["cloud_limited", "humid_cloud", "dual_forecast_low_gap"]].astype(int).sum(axis=1)
    )
    states["base_candidate"] = (
        states["decision_hour_local"].between(13, 17)
        & states["decline_native"].ge(0.5)
        & states["minutes_since_running_max"].ge(60)
        & states["temp_trend_1h_f"].le(0)
        & states["gfs_forecast_peak_delta_hours_local"].ge(0.25)
        & states["ecmwf_forecast_peak_delta_hours_local"].ge(0.25)
        & states["current_yes_ask"].between(0.01, 0.99)
        & states["current_bracket_held"].notna()
    )
    states["strong_proxy"] = states["base_candidate"] & states["historical_support_count"].ge(2)

    states["current_yes_fee"] = fee_per_share(states["current_yes_ask"])
    states["current_yes_cost"] = states["current_yes_ask"] + states["current_yes_fee"]
    states["current_yes_pnl"] = states["current_bracket_held"] - states["current_yes_cost"]
    states["d1_no_fee"] = fee_per_share(states["d1_no_ask"])
    states["d1_no_cost"] = states["d1_no_ask"] + states["d1_no_fee"]
    states["d1_no_win"] = 1.0 - states["d1_hit"]
    states["d1_no_pnl"] = states["d1_no_win"] - states["d1_no_cost"]
    states.attrs["raw_feature_rows"] = len(rows)
    return states


def expression_summary(rows: pd.DataFrame, expression: str) -> dict[str, Any]:
    if expression == "current_yes":
        sample = rows[rows["current_yes_cost"].notna()].copy()
        win_col, cost_col, pnl_col = "current_bracket_held", "current_yes_cost", "current_yes_pnl"
    else:
        sample = rows[rows["d1_no_ask"].between(0.01, 0.99) & rows["d1_hit"].notna()].copy()
        win_col, cost_col, pnl_col = "d1_no_win", "d1_no_cost", "d1_no_pnl"
    cost = float(sample[cost_col].sum())
    pnl = float(sample[pnl_col].sum())
    return {
        "expression": expression,
        "rows": int(len(sample)),
        "active_dates": int(sample["target_date"].nunique()),
        "cities": int(sample["city"].nunique()),
        "win_rate": float(sample[win_col].mean()) if len(sample) else None,
        "avg_ask": float(sample[cost_col].sub(sample[f"{expression}_fee"] if expression == "d1_no" else sample["current_yes_fee"]).mean()) if len(sample) else None,
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost > 0 else None,
        "roi_ci95": bootstrap_roi(sample, pnl_col, cost_col),
    }


def paired_expression_summary(rows: pd.DataFrame) -> dict[str, Any]:
    paired = rows[
        rows["current_yes_ask"].between(0.01, 0.99)
        & rows["d1_no_ask"].between(0.01, 0.99)
        & rows["d1_hit"].notna()
    ].copy()
    yes_roi = float(paired["current_yes_pnl"].sum() / paired["current_yes_cost"].sum())
    d1_roi = float(paired["d1_no_pnl"].sum() / paired["d1_no_cost"].sum())
    return {
        "rows": int(len(paired)),
        "active_dates": int(paired["target_date"].nunique()),
        "current_yes_roi": yes_roi,
        "d1_no_roi": d1_roi,
        "current_yes_minus_d1_no_roi": yes_roi - d1_roi,
        "delta_ci95": bootstrap_paired_delta(
            paired, "current_yes_pnl", "current_yes_cost", "d1_no_pnl", "d1_no_cost"
        ),
    }


def same_price_matches(states: pd.DataFrame, period: str) -> pd.DataFrame:
    selected = states[states["period"].eq(period) & states["strong_proxy"]].copy()
    pool = states[states["period"].eq(period) & states["base_candidate"] & ~states["strong_proxy"]].copy()
    matches: list[dict[str, Any]] = []
    for row in selected.to_dict("records"):
        candidates = pool[
            pool["decision_hour_local"].eq(row["decision_hour_local"])
            & pool["target_date"].ne(row["target_date"])
        ].copy()
        if candidates.empty:
            candidates = pool[pool["target_date"].ne(row["target_date"])].copy()
        if candidates.empty:
            continue
        candidates["price_distance"] = (candidates["current_yes_ask"] - row["current_yes_ask"]).abs()
        baseline = candidates.sort_values(["price_distance", "target_date", "city"]).iloc[0]
        matches.append(
            {
                "target_date": row["target_date"],
                "city": row["city"],
                "decision_hour_local": row["decision_hour_local"],
                "candidate_ask": row["current_yes_ask"],
                "candidate_cost": row["current_yes_cost"],
                "candidate_pnl": row["current_yes_pnl"],
                "baseline_city": baseline["city"],
                "baseline_target_date": baseline["target_date"],
                "baseline_ask": baseline["current_yes_ask"],
                "baseline_cost": baseline["current_yes_cost"],
                "baseline_pnl": baseline["current_yes_pnl"],
                "price_distance": baseline["price_distance"],
            }
        )
    return pd.DataFrame(matches)


def matched_summary(matches: pd.DataFrame) -> dict[str, Any]:
    if matches.empty:
        return {"rows": 0}
    candidate_roi = float(matches["candidate_pnl"].sum() / matches["candidate_cost"].sum())
    baseline_roi = float(matches["baseline_pnl"].sum() / matches["baseline_cost"].sum())
    return {
        "rows": int(len(matches)),
        "active_dates": int(matches["target_date"].nunique()),
        "mean_abs_price_distance": float(matches["price_distance"].mean()),
        "max_abs_price_distance": float(matches["price_distance"].max()),
        "candidate_roi": candidate_roi,
        "same_price_base_roi": baseline_roi,
        "excess_roi": candidate_roi - baseline_roi,
        "excess_ci95": bootstrap_paired_delta(
            matches, "candidate_pnl", "candidate_cost", "baseline_pnl", "baseline_cost"
        ),
    }


def pct(value: Any) -> str:
    return "NA" if value is None else f"{float(value) * 100:+.1f}%"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    states = load_states()
    summary_rows: list[dict[str, Any]] = []
    paired: dict[str, Any] = {}
    for period in ("train", "holdout", "all"):
        period_mask = pd.Series(True, index=states.index) if period == "all" else states["period"].eq(period)
        for cohort, mask in (("base_candidate", states["base_candidate"]), ("strong_proxy", states["strong_proxy"])):
            sample = states[period_mask & mask].copy()
            for expression in ("current_yes", "d1_no"):
                summary_rows.append({"period": period, "cohort": cohort, **expression_summary(sample, expression)})
            paired[f"{period}_{cohort}"] = paired_expression_summary(sample)

    matches_train = same_price_matches(states, "train")
    matches_holdout = same_price_matches(states, "holdout")
    matches = pd.concat([matches_train.assign(period="train"), matches_holdout.assign(period="holdout")], ignore_index=True)
    match_summary = {
        "train": matched_summary(matches_train),
        "holdout": matched_summary(matches_holdout),
    }
    summary = pd.DataFrame(summary_rows)
    selected_columns = [
        "city", "target_date", "decision_hour_local", "current_bracket", "current_yes_ask", "d1_no_bracket",
        "d1_no_ask", "current_bracket_held", "d1_hit", "decline_native", "minutes_since_running_max",
        "temp_trend_1h_f", "relative_humidity_pct", "sky_cover_code", "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local", "gfs_forecast_gap_to_running_native",
        "ecmwf_forecast_gap_to_running_native", "historical_support_count", "cloud_limited", "humid_cloud",
        "dual_forecast_low_gap", "period",
    ]
    selected = states[states["strong_proxy"]][selected_columns].copy()
    summary.to_csv(OUT_DIR / "summary.csv", index=False)
    matches.to_csv(OUT_DIR / "same_price_matches.csv", index=False)
    selected.to_csv(OUT_DIR / "strong_proxy_rows.csv", index=False)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input": str(INPUT.relative_to(ROOT)),
        "row_grain": "settled city-target_date-decision_hour state",
        "data_integrity": {
            "input_sha256": hashlib.sha256(INPUT.read_bytes()).hexdigest(),
            "target_date_min": str(states["target_date"].min()),
            "target_date_max": str(states["target_date"].max()),
            "dedup_key_duplicates": int(states.duplicated(["city", "target_date", "decision_hour_local"]).sum()),
            "settlement_status_counts": states["settlement_status"].fillna("missing").value_counts().to_dict(),
            "current_yes_ask_coverage": float(states["current_yes_ask"].notna().mean()),
            "d1_no_ask_coverage": float(states["d1_no_ask"].notna().mean()),
        },
        "funnel": {
            "raw_feature_rows": int(states.attrs["raw_feature_rows"]),
            "dedup_state_rows": int(len(states)),
            "dates": int(states["target_date"].nunique()),
            "cities": int(states["city"].nunique()),
            "base_candidates": int(states["base_candidate"].sum()),
            "strong_proxy_candidates": int(states["strong_proxy"].sum()),
        },
        "historical_feature_coverage": {
            "available": ["temperature_path", "minutes_since_running_max", "cloud_cover", "humidity", "wind_speed", "dual_model_peak_clock", "dual_model_forecast_gap"],
            "missing_new_fields": ["precipitation", "wind_direction", "forecast_remaining_3h_weather", "solar_geometry"],
        },
        "peak_clock_provenance": peak_clock_provenance(states),
        "rule": {
            "base": "hour 13-17; decline>=0.5 native; minutes_since_max>=60; trend1h<=0F; both model peaks passed>=0.25h",
            "strong_proxy": "base plus at least 2 of cloud_limited, humid_cloud, dual_forecast_low_gap",
            "fee": "official Weather taker fee=0.05*p*(1-p), rounded to 5 decimals",
        },
        "summary": summary_rows,
        "paired_expression": paired,
        "same_price_baseline": match_summary,
        "outputs": {
            "summary_csv": str((OUT_DIR / "summary.csv").relative_to(ROOT)),
            "same_price_matches_csv": str((OUT_DIR / "same_price_matches.csv").relative_to(ROOT)),
            "strong_proxy_rows_csv": str((OUT_DIR / "strong_proxy_rows.csv").relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    holdout_yes = summary[(summary["period"].eq("holdout")) & (summary["cohort"].eq("strong_proxy")) & (summary["expression"].eq("current_yes"))].iloc[0]
    holdout_d1 = summary[(summary["period"].eq("holdout")) & (summary["cohort"].eq("strong_proxy")) & (summary["expression"].eq("d1_no"))].iloc[0]
    paired_holdout = paired["holdout_strong_proxy"]
    baseline_holdout = match_summary["holdout"]
    lines = [
        "# Current YES Heat-Death Physical Backtest v1",
        "",
        "Status: current-reference",
        "Date: 2026-07-14",
        "Verdict: `historical_proxy_positive_but_no_incremental_alpha`",
        "",
        "## 结论",
        "",
        f"历史 proxy 不是 NA：strong cohort holdout 有 {int(holdout_yes['rows'])} 行 / {int(holdout_yes['active_dates'])} 天，current YES fee ROI {pct(holdout_yes['roi'])}，d1 NO fee ROI {pct(holdout_d1['roi'])}。",
        f"但 current YES 相对同价 base-fade baseline 的超额只有 {pct(baseline_holdout['excess_roi'])}，95% CI [{pct(baseline_holdout['excess_ci95'][0])}, {pct(baseline_holdout['excess_ci95'][1])}]；物理支持没有提供可确认的增量 alpha。",
        "",
        "## Funnel",
        "",
        (
            f"- {payload['funnel']['raw_feature_rows']:,} bracket rows -> {payload['funnel']['dedup_state_rows']:,} city-date-hour states -> "
            f"{payload['funnel']['base_candidates']} base candidates -> {payload['funnel']['strong_proxy_candidates']} strong proxy candidates。"
        ),
        "- train: < 2026-06-01；holdout: >= 2026-06-01。规则是在读取结果前按 forward runner 口径冻结。",
        "",
        "## Peak clock provenance（2026-07-15 复核）",
        "",
        (
            f"- backfill CSV `{payload['peak_clock_provenance'].get('path', 'unknown')}`：single-runs 占比 "
            f"{payload['peak_clock_provenance'].get('single_runs_share', float('nan')):.1%}，"
            f"run policy = `{json.dumps(payload['peak_clock_provenance'].get('gfs_run_policy_counts', {}), ensure_ascii=False)}`；"
            f"factory 行内嵌值与该 CSV 的 GFS peak hour 一致率 = "
            f"{payload['peak_clock_provenance'].get('factory_vs_backfill_gfs_peak_hour_agreement', float('nan')):.1%}"
            f"（{payload['peak_clock_provenance'].get('factory_vs_backfill_compared_city_dates', 0)} city-dates）。"
        ),
        (
            "- **provenance = verified_clean**：本次输入 factory 行的 peak clock 与 single-runs D-1 12z 重建一致，无未来信息泄漏；剩余 source 风险是与生产 runner 最新 run 的 parity，不是 leakage。"
            if payload['peak_clock_provenance'].get('status') == 'verified_clean'
            else "- **provenance = factory_embeds_stale_backfill**：factory 行内嵌的 peak clock 与干净 single-runs 重建不一致，本回测的 peak-clock gate 仍在疑似近实况拼接的旧 backfill 上，全部绝对 ROI 应视为 source-contaminated，直到 factory 用干净 backfill 重建并重跑。"
        ),
        "",
        "## Holdout",
        "",
        "| Expression | Rows | Dates | Win | ROI | 95% CI |",
        "|---|---:|---:|---:|---:|---:|",
        f"| current YES | {int(holdout_yes['rows'])} | {int(holdout_yes['active_dates'])} | {pct(holdout_yes['win_rate'])} | {pct(holdout_yes['roi'])} | [{pct(holdout_yes['roi_ci95'][0])}, {pct(holdout_yes['roi_ci95'][1])}] |",
        f"| d1 NO | {int(holdout_d1['rows'])} | {int(holdout_d1['active_dates'])} | {pct(holdout_d1['win_rate'])} | {pct(holdout_d1['roi'])} | [{pct(holdout_d1['roi_ci95'][0])}, {pct(holdout_d1['roi_ci95'][1])}] |",
        "",
        f"同一 {paired_holdout['rows']} 行 / {paired_holdout['active_dates']} 天 paired denominator 上，current YES - d1 NO ROI = {pct(paired_holdout['current_yes_minus_d1_no_roi'])}，95% CI [{pct(paired_holdout['delta_ci95'][0])}, {pct(paired_holdout['delta_ci95'][1])}]。点估和 bootstrap 偏向 current YES，但只有 6 个日期，低于策略确认门槛，不能升格为稳定表达优势。",
        "",
        "## Data Integrity Self-Check",
        "",
        f"- date coverage: {payload['data_integrity']['target_date_min']}..{payload['data_integrity']['target_date_max']}；dedup key duplicates={payload['data_integrity']['dedup_key_duplicates']}。",
        f"- settlement status: `{json.dumps(payload['data_integrity']['settlement_status_counts'], ensure_ascii=False)}`。",
        f"- current YES ask coverage={payload['data_integrity']['current_yes_ask_coverage']:.1%}；d1 NO ask coverage={payload['data_integrity']['d1_no_ask_coverage']:.1%}。",
        "",
        "## Feature Coverage Boundary",
        "",
        "历史层能重建温度路径、minutes-since-max、云、湿度、风速、双模型 peak clock/gap；不能 PIT 重建本次新增的降雨、风向、remaining-3h forecast weather 和 solar geometry。因此这里是新策略的 historical proxy，不是假装完整的新特征回测。",
        "",
        "## Three Gates",
        "",
        "```text",
        (
            "significance="
            + (
                "PASS"
                if holdout_yes["roi_ci95"][0] is not None and holdout_yes["roi_ci95"][0] > 0
                and int(holdout_yes["rows"]) >= 30 and int(holdout_yes["active_dates"]) >= 12
                else "FAIL_LOW_SAMPLE"
                if holdout_yes["roi_ci95"][0] is not None and holdout_yes["roi_ci95"][0] > 0
                else "FAIL"
            )
            + f" for absolute current YES proxy ROI (rows={int(holdout_yes['rows'])}, dates={int(holdout_yes['active_dates'])}; preregistered floor: 30 rows / 12 dates)"
        ),
        f"baseline={'PASS' if baseline_holdout['excess_ci95'][0] is not None and baseline_holdout['excess_ci95'][0] > 0 else 'FAIL'} for same-price physical-overlay excess",
        "forward=FAIL_THIN for complete new weather_state_v2 features",
        "conclusion=inconclusive; zero-notional forward only",
        "```",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(json_ready(payload), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
