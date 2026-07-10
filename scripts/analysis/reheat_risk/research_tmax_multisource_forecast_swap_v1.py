#!/usr/bin/env python3
"""Tmax multi-source forecast swap backtest.

Research-only. This keeps the tmax exact-book opportunity denominator fixed and
swaps only the daily max forecast source used by a simple forecast-anchor
distribution. It does not change live runners or the production probability
model.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_tmax_distribution_p0_anchor_scorecard_v1 as p0  # noqa: E402


BRIDGE_CANDIDATES = ROOT / "docs/analysis/2026-07/generated/tmax_exact_book_bridge_v1/expression_candidates.csv"
ENRICH_ERRORS = ROOT / "docs/analysis/2026-07/generated/historical_forecast_enrichment_bias_v1/daily_error_rows.csv"
CITY_BEST = ROOT / "docs/analysis/2026-07/generated/historical_forecast_enrichment_bias_v1/city_best_model.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_multisource_forecast_swap_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-10-tmax-multisource-forecast-swap-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-10-tmax-multisource-forecast-swap-v1.json"

TRAIN_CUTOFF = "2026-06-21"
ACTIVE_EXPRESSIONS = ["current_no", "d1_no", "d2_no", "d1_yes", "d2_yes"]
ASK_FLOOR = 0.40
ASK_CEILING = 0.99
EDGE_THRESHOLD = 0.02
FEE_RATE = 0.05
BOOT_N = 1000
BOOT_SEED = 20260710
MIN_ASOF_MODEL_DAYS = 10

MODEL_KEYS = [
    "gfs_seamless",
    "gfs_global",
    "ecmwf_ifs025",
    "ecmwf_aifs025_single",
    "icon_seamless",
    "icon_eu",
    "icon_d2",
    "gem_global",
    "gem_seamless",
    "gem_regional",
    "jma_seamless",
    "ncep_aigfs025",
    "ncep_gfs_graphcast025",
    "ncep_nam_conus",
    "ncep_nbm_conus",
    "ncep_hrrr_conus",
    "meteofrance_arome_france_hd",
]


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        return _json_ready(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _fmt_pct(value: object, digits: int = 1) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    return f"{v:+.{digits}%}"


def _fmt_num(value: object, digits: int = 3) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    return f"{v:.{digits}f}"


def _native_from_f(temp_f: float, unit: str) -> float:
    if str(unit).upper() == "C":
        return (temp_f - 32.0) * 5.0 / 9.0
    return temp_f


def _fee(price: float) -> float:
    return FEE_RATE * price * (1.0 - price)


def _expression_prob(probs: dict[str, float], expression: str) -> float:
    if expression == "current_yes":
        return probs["current"]
    if expression == "current_no":
        return 1.0 - probs["current"]
    if expression == "d1_yes":
        return probs["d1"]
    if expression == "d1_no":
        return 1.0 - probs["d1"]
    if expression == "d2_yes":
        return probs["d2"]
    if expression == "d2_no":
        return 1.0 - probs["d2"]
    raise ValueError(expression)


def _score_probs(probs: dict[str, float], actual: str) -> dict[str, float]:
    p_win = max(p0.EPS, probs.get(actual, p0.EPS))
    brier = sum((probs[b] - (1.0 if b == actual else 0.0)) ** 2 for b in p0.BUCKETS)
    top = max(probs, key=probs.get)
    return {
        "logloss": -math.log(p_win),
        "brier": brier,
        "top1": float(top == actual),
        "winner_prob": p_win,
    }


def _date_block_roi_ci(rows: pd.DataFrame) -> dict[str, float]:
    if rows.empty:
        return {"roi": math.nan, "ci_low": math.nan, "ci_high": math.nan}
    cost = float(rows["cost_net"].sum())
    pnl = float(rows["pnl_net"].sum())
    roi = pnl / cost if cost else math.nan
    by_day = rows.groupby("target_date", as_index=False).agg(cost=("cost_net", "sum"), pnl=("pnl_net", "sum"))
    if len(by_day) < 3:
        return {"roi": roi, "ci_low": math.nan, "ci_high": math.nan}
    rng = np.random.default_rng(BOOT_SEED)
    arr = by_day[["cost", "pnl"]].to_numpy(dtype=float)
    boot = []
    for _ in range(BOOT_N):
        sample = arr[rng.integers(0, len(arr), size=len(arr))]
        c = sample[:, 0].sum()
        p = sample[:, 1].sum()
        boot.append(p / c if c else math.nan)
    return {
        "roi": roi,
        "ci_low": float(np.nanpercentile(boot, 2.5)),
        "ci_high": float(np.nanpercentile(boot, 97.5)),
    }


def _load_bridge() -> tuple[pd.DataFrame, pd.DataFrame]:
    if not BRIDGE_CANDIDATES.exists():
        raise FileNotFoundError(BRIDGE_CANDIDATES)
    df = pd.read_csv(BRIDGE_CANDIDATES, low_memory=False)
    df = df[df["scope"].isin(["dev_cv", "verified_forward", "extension_forward"])].copy()
    for col in ["decision_hour_local", "ask", "win", "p_win"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df["target_date"].astype(str).le("2026-07-07")].copy()
    state_cols = [
        "scope",
        "city",
        "target_date",
        "decision_hour_local",
        "actual_bucket",
        "current_bracket",
        "d1_no_bracket",
        "d2_no_bracket",
        "forecast_source",
        "day_regime",
        "intraday_state",
        "city_family",
        "label_source",
        "eval_slice",
    ]
    states = df[state_cols].drop_duplicates(["scope", "city", "target_date", "decision_hour_local"]).copy()
    states = states[states["actual_bucket"].isin(p0.BUCKETS)].copy()
    states["split"] = np.where(states["target_date"].astype(str) < TRAIN_CUTOFF, "dev_train_pre_2026_06_21", "verified_2026_06_21_plus")
    return df, states


def _load_forecasts(states: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if not ENRICH_ERRORS.exists():
        raise FileNotFoundError(ENRICH_ERRORS)
    raw = pd.read_csv(ENRICH_ERRORS)
    raw = raw[raw["model_key"].isin(MODEL_KEYS)].copy()
    raw["forecast_max_f"] = pd.to_numeric(raw["forecast_max_f"], errors="coerce")
    raw["abs_error_f"] = pd.to_numeric(raw["abs_error_f"], errors="coerce")
    raw = raw.dropna(subset=["forecast_max_f"])
    needed_keys = states[["city", "target_date"]].drop_duplicates()
    raw = raw.merge(needed_keys, on=["city", "target_date"], how="inner")

    city_best = pd.read_csv(CITY_BEST) if CITY_BEST.exists() else pd.DataFrame()
    full_best_map = dict(zip(city_best.get("city", []), city_best.get("best_model_key", [])))

    # Expanding as-of best model: for each city/date choose the historical model
    # with the lowest MAE strictly before that target date.
    best_asof_rows = []
    for (city, target_date), _grp in needed_keys.groupby(["city", "target_date"], dropna=False):
        hist = raw[(raw["city"].eq(city)) & (raw["target_date"].astype(str) < str(target_date))]
        if hist.empty:
            continue
        summ = (
            hist.groupby("model_key", as_index=False)
            .agg(n=("abs_error_f", "size"), mae=("abs_error_f", "mean"))
            .query("n >= @MIN_ASOF_MODEL_DAYS")
            .sort_values(["mae", "n"], ascending=[True, False])
        )
        if summ.empty:
            continue
        best_asof_rows.append({"city": city, "target_date": target_date, "best_model_asof": summ.iloc[0]["model_key"], "best_model_asof_n": int(summ.iloc[0]["n"]), "best_model_asof_mae_f": float(summ.iloc[0]["mae"])})
    best_asof = pd.DataFrame(best_asof_rows)

    wide = raw.pivot_table(index=["city", "target_date"], columns="model_key", values="forecast_max_f", aggfunc="first").reset_index()
    wide.columns = [str(c) for c in wide.columns]
    units = raw[["city", "target_date", "unit"]].drop_duplicates(["city", "target_date"])
    wide = wide.merge(units, on=["city", "target_date"], how="left")
    if not best_asof.empty:
        wide = wide.merge(best_asof, on=["city", "target_date"], how="left")
    else:
        wide["best_model_asof"] = np.nan
        wide["best_model_asof_n"] = np.nan
        wide["best_model_asof_mae_f"] = np.nan
    wide["best_model_full"] = wide["city"].map(full_best_map)
    for col_name, model_col in [("best_asof_forecast_f", "best_model_asof"), ("best_full_forecast_f", "best_model_full")]:
        values = []
        for item in wide.to_dict("records"):
            key = item.get(model_col)
            values.append(item.get(key, math.nan) if isinstance(key, str) and key in item else math.nan)
        wide[col_name] = values

    meta = {
        "forecast_rows": int(len(raw)),
        "forecast_min_date": str(raw["target_date"].min()) if len(raw) else "",
        "forecast_max_date": str(raw["target_date"].max()) if len(raw) else "",
        "forecast_cities": int(raw["city"].nunique()) if len(raw) else 0,
        "forecast_models": sorted(raw["model_key"].dropna().unique().tolist()),
        "best_asof_rows": int(len(best_asof)),
    }
    return raw, wide, meta


def _build_probability_rows(states: pd.DataFrame, forecast_wide: pd.DataFrame) -> pd.DataFrame:
    merged = states.merge(forecast_wide, on=["city", "target_date"], how="inner")
    variants = ["best_asof_forecast_f", "best_full_forecast_f", *MODEL_KEYS]
    rows = []
    for item in merged.to_dict("records"):
        current_iv = p0._interval(item.get("current_bracket"))
        d1_iv = p0._interval(item.get("d1_no_bracket"))
        d2_iv = p0._interval(item.get("d2_no_bracket"))
        if current_iv is None or d1_iv is None or d2_iv is None:
            continue
        actual = str(item["actual_bucket"])
        for variant in variants:
            f_f = item.get(variant, math.nan)
            try:
                f_f = float(f_f)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(f_f):
                continue
            forecast_native = _native_from_f(f_f, str(item.get("unit") or "F"))
            probs = p0._soft_anchor_distribution(forecast_native, current_iv, d1_iv, d2_iv)
            score = _score_probs(probs, actual)
            row = {
                "variant": variant,
                "city": item["city"],
                "target_date": item["target_date"],
                "decision_hour_local": item["decision_hour_local"],
                "scope": item["scope"],
                "split": item["split"],
                "actual_bucket": actual,
                "forecast_source": item.get("forecast_source"),
                "day_regime": item.get("day_regime"),
                "intraday_state": item.get("intraday_state"),
                "city_family": item.get("city_family"),
                "unit": item.get("unit"),
                "forecast_max_f_swap": f_f,
                "forecast_max_native_swap": forecast_native,
                "best_model_asof": item.get("best_model_asof"),
                "best_model_full": item.get("best_model_full"),
            }
            for bucket in p0.BUCKETS:
                row[f"p_{bucket}"] = probs[bucket]
            row.update(score)
            rows.append(row)
    return pd.DataFrame(rows)


def _score_summary(prob_rows: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (split, variant), grp in prob_rows.groupby(["split", "variant"], dropna=False):
        rows.append(
            {
                "split": split,
                "variant": variant,
                "rows": int(len(grp)),
                "dates": int(grp["target_date"].nunique()),
                "cities": int(grp["city"].nunique()),
                "logloss": float(grp["logloss"].mean()),
                "brier": float(grp["brier"].mean()),
                "top1": float(grp["top1"].mean()),
                "winner_prob": float(grp["winner_prob"].mean()),
            }
        )
    out = pd.DataFrame(rows)
    base = out[out["variant"].eq("gfs_seamless")][["split", "logloss", "brier"]].rename(columns={"logloss": "gfs_seamless_logloss", "brier": "gfs_seamless_brier"})
    out = out.merge(base, on="split", how="left")
    out["logloss_delta_vs_gfs_seamless"] = out["logloss"] - out["gfs_seamless_logloss"]
    out["brier_delta_vs_gfs_seamless"] = out["brier"] - out["gfs_seamless_brier"]
    return out.sort_values(["split", "logloss"]).reset_index(drop=True)


def _execution_replay(expr: pd.DataFrame, prob_rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    prob_cols = ["variant", "scope", "city", "target_date", "decision_hour_local"] + [f"p_{b}" for b in p0.BUCKETS]
    merged = expr.merge(prob_rows[prob_cols], on=["scope", "city", "target_date", "decision_hour_local"], how="inner")
    merged = merged[merged["expression"].isin(ACTIVE_EXPRESSIONS)].copy()
    out_rows = []
    for item in merged.to_dict("records"):
        probs = {bucket: float(item[f"p_{bucket}"]) for bucket in p0.BUCKETS}
        p_win = _expression_prob(probs, str(item["expression"]))
        ask = float(item["ask"])
        fee = _fee(ask)
        win = float(item["win"])
        row = {
            "variant": item["variant"],
            "scope": item["scope"],
            "city": item["city"],
            "target_date": item["target_date"],
            "decision_hour_local": item["decision_hour_local"],
            "expression": item["expression"],
            "actual_bucket": item["actual_bucket"],
            "ask": ask,
            "taker_fee": fee,
            "p_win": p_win,
            "fee_adjusted_edge": p_win - ask - fee,
            "win": win,
            "cost_net": ask + fee,
            "pnl_net": win - ask - fee,
            "day_regime": item.get("day_regime"),
            "intraday_state": item.get("intraday_state"),
            "city_family": item.get("city_family"),
        }
        out_rows.append(row)
    candidates = pd.DataFrame(out_rows)
    eligible = candidates[
        candidates["ask"].ge(ASK_FLOOR)
        & candidates["ask"].le(ASK_CEILING)
        & candidates["fee_adjusted_edge"].ge(EDGE_THRESHOLD)
    ].copy()
    if eligible.empty:
        return candidates, eligible
    best_by_hour = (
        eligible.sort_values(
            ["variant", "scope", "city", "target_date", "decision_hour_local", "fee_adjusted_edge", "p_win"],
            ascending=[True, True, True, True, True, False, False],
        )
        .groupby(["variant", "scope", "city", "target_date", "decision_hour_local"], as_index=False)
        .head(1)
    )
    selected = (
        best_by_hour.sort_values(["variant", "scope", "city", "target_date", "decision_hour_local"])
        .groupby(["variant", "scope", "city", "target_date"], as_index=False)
        .head(1)
        .reset_index(drop=True)
    )
    return candidates, selected


def _original_replay(expr: pd.DataFrame) -> pd.DataFrame:
    rows = expr[
        expr["expression"].isin(ACTIVE_EXPRESSIONS)
        & expr["ask"].ge(ASK_FLOOR)
        & expr["ask"].le(ASK_CEILING)
    ].copy()
    rows["fee_adjusted_edge"] = rows["p_win"] - rows["ask"] - rows["ask"].map(_fee)
    rows = rows[rows["fee_adjusted_edge"].ge(EDGE_THRESHOLD)].copy()
    if rows.empty:
        return rows
    rows["cost_net"] = rows["ask"] + rows["ask"].map(_fee)
    rows["pnl_net"] = rows["win"] - rows["cost_net"]
    rows["variant"] = "original_tmax_model"
    return (
        rows.sort_values(["variant", "scope", "city", "target_date", "decision_hour_local", "fee_adjusted_edge", "p_win"], ascending=[True, True, True, True, True, False, False])
        .groupby(["variant", "scope", "city", "target_date"], as_index=False)
        .head(1)
        .reset_index(drop=True)
    )


def _trade_summary(selected: pd.DataFrame) -> pd.DataFrame:
    rows = []
    if selected.empty:
        return pd.DataFrame()
    for (scope, variant), grp in selected.groupby(["scope", "variant"], dropna=False):
        ci = _date_block_roi_ci(grp)
        rows.append(
            {
                "scope": scope,
                "variant": variant,
                "rows": int(len(grp)),
                "dates": int(grp["target_date"].nunique()),
                "cities": int(grp["city"].nunique()),
                "win_rate": float(grp["win"].mean()),
                "avg_ask": float(grp["ask"].mean()),
                "cost_net": float(grp["cost_net"].sum()),
                "pnl_net": float(grp["pnl_net"].sum()),
                "roi_net": ci["roi"],
                "roi_net_ci_low": ci["ci_low"],
                "roi_net_ci_high": ci["ci_high"],
                "expr_mix": json.dumps(grp["expression"].value_counts().to_dict(), ensure_ascii=False, sort_keys=True),
            }
        )
    return pd.DataFrame(rows).sort_values(["scope", "roi_net"], ascending=[True, False]).reset_index(drop=True)


def _source_quality_on_denominator(raw_forecasts: pd.DataFrame, states: pd.DataFrame) -> pd.DataFrame:
    keys = states[["city", "target_date"]].drop_duplicates()
    sub = raw_forecasts.merge(keys, on=["city", "target_date"], how="inner")
    return (
        sub.groupby(["model_key", "model_label"], as_index=False)
        .agg(rows=("abs_error_f", "size"), dates=("target_date", "nunique"), cities=("city", "nunique"), mae_f=("abs_error_f", "mean"), bias_f=("error_f", "mean"), rmse_f=("error_f", lambda x: float(np.sqrt(np.mean(np.square(x))))))
        .sort_values(["mae_f", "rows"], ascending=[True, False])
        .reset_index(drop=True)
    )


def _write_report(meta: dict[str, Any], source_quality: pd.DataFrame, score_summary: pd.DataFrame, trade_summary: pd.DataFrame, selected: pd.DataFrame) -> None:
    def table(df: pd.DataFrame, cols: list[str], n: int = 20) -> str:
        if df.empty:
            return "_No rows._"
        view = df[cols].head(n).copy()
        headers = [str(c) for c in cols]
        body = []
        for item in view.to_dict("records"):
            row = []
            for col in cols:
                value = item.get(col, "")
                if isinstance(value, float):
                    value = "" if not math.isfinite(value) else f"{value:.4f}"
                row.append(str(value))
            body.append(row)
        lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in body)
        return "\n".join(lines)

    forward_scores = score_summary[score_summary["split"].eq("verified_2026_06_21_plus")].copy()
    forward_trades = trade_summary[trade_summary["scope"].eq("verified_forward")].copy()
    dev_trades = trade_summary[trade_summary["scope"].eq("dev_cv")].copy()

    city_rows = []
    fwd_sel = selected[selected["scope"].eq("verified_forward")].copy()
    if not fwd_sel.empty:
        for (variant, city), grp in fwd_sel.groupby(["variant", "city"], dropna=False):
            if len(grp) < 2:
                continue
            city_rows.append(
                {
                    "variant": variant,
                    "city": city,
                    "rows": int(len(grp)),
                    "wins": int(grp["win"].sum()),
                    "roi_net": float(grp["pnl_net"].sum() / grp["cost_net"].sum()) if grp["cost_net"].sum() else math.nan,
                }
            )
    city_summary = pd.DataFrame(city_rows).sort_values(["variant", "roi_net"], ascending=[True, False]) if city_rows else pd.DataFrame()

    lines = [
        "# Tmax Multi-Source Forecast Swap v1",
        "",
        "Generated: 2026-07-10",
        "",
        "## Question",
        "",
        "在 `tmax_distribution_edge` 的同一 exact-book 机会分母上，把原来 assigned GFS/ECMWF forecast max 换成其他 Open-Meteo 模型源，会不会更好？",
        "",
        "## Bottom Line",
        "",
        "有发现，但还不能直接切 live 模型：多模型 forecast 在 **source quality** 上明显有信息，尤其 ICON/GDPS/区域模型在部分城市优于旧 GFS/ECMWF；但把这些 forecast max 直接当成 tmax 的 forecast-anchor 执行 selector，整体仍然不如现有 tmax probability model。",
        "",
        "最实用的结论是：**其他 forecast source 应先作为 tmax 的 source-quality / uncertainty / sizing 特征接入，而不是替换模型主干。**",
        "",
        "## Data Snapshot",
        "",
        "```json",
        json.dumps(meta, indent=2, ensure_ascii=False),
        "```",
        "",
        "Important limitation: `historical_forecast_enrichment_bias_v1` 是 city-date 级 historical daily max 回放，不是完整决策时刻 PIT forecast version。这里是 source-swap 反事实和模型筛选，不是 live 许可。",
        "",
        "## Source Quality On Tmax Denominator",
        "",
        table(source_quality, ["model_key", "model_label", "rows", "dates", "cities", "mae_f", "bias_f", "rmse_f"], 20),
        "",
        "## Forecast-Anchor Probability Score",
        "",
        "这里只看四桶 `current/d1/d2/tail` 的 forecast-anchor proper scoring。数值越低越好。",
        "",
        table(
            forward_scores,
            ["variant", "rows", "dates", "cities", "logloss", "logloss_delta_vs_gfs_seamless", "brier", "top1", "winner_prob"],
            24,
        ),
        "",
        "## Execution Replay Proxy",
        "",
        f"规则：active expressions={ACTIVE_EXPRESSIONS}, ask in [{ASK_FLOOR},{ASK_CEILING}], fee-adjusted edge >= {EDGE_THRESHOLD}, 每 city-day 第一笔。",
        "",
        "### Verified Forward 2026-06-21+",
        "",
        table(
            forward_trades,
            ["variant", "rows", "dates", "cities", "win_rate", "avg_ask", "pnl_net", "roi_net", "roi_net_ci_low", "roi_net_ci_high", "expr_mix"],
            24,
        ),
        "",
        "### Dev CV / Pre-2026-06-21",
        "",
        table(
            dev_trades,
            ["variant", "rows", "dates", "cities", "win_rate", "avg_ask", "pnl_net", "roi_net", "roi_net_ci_low", "roi_net_ci_high", "expr_mix"],
            24,
        ),
        "",
        "## City Notes",
        "",
        "Verified forward 中每 variant 至少 2 笔的 city-level selected replay：",
        "",
        table(city_summary, ["variant", "city", "rows", "wins", "roi_net"], 60),
        "",
        "## Interpretation",
        "",
        "1. `best_asof_forecast_f` 是更合理的研究方向：只用目标日前历史选每城 best model，避免 full-window 偷看未来。它的 forecast quality 有意义，但直接 forecast-anchor selector 仍偏粗。",
        "2. `best_full_forecast_f` 和单日 best-model 表里的大幅改善只能当诊断，不能当策略，因为用了全窗口未来信息。",
        "3. 如果某个城市的 best model 与 assigned GFS/ECMWF 差距很大，tmax 应该把它变成 `source_reliability / forecast_disagreement / forecast_ceiling_uncertainty`，而不是硬切 source。",
        "4. 下一步工程上应把 `best_model_asof`, `best_model_mae`, `assigned_minus_best_forecast`, `model_spread`, `regional_model_available` 写入 tmax live/shadow feature payload，再用现有 tmax model 做 ablation。",
        "",
        "## Verdict",
        "",
        "```text",
        "significance=FAIL/NA for live replacement",
        "baseline=current tmax probability model",
        "forward=source-quality useful, source-swap selector not confirmed",
        "conclusion=shadow_feature_upgrade_not_live_selector",
        "```",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    expr, states = _load_bridge()
    raw_forecasts, forecast_wide, forecast_meta = _load_forecasts(states)
    prob_rows = _build_probability_rows(states, forecast_wide)
    score_summary = _score_summary(prob_rows)
    _, selected_swap = _execution_replay(expr, prob_rows)
    original_selected = _original_replay(expr)
    selected = pd.concat([selected_swap, original_selected], ignore_index=True, sort=False)
    trade_summary = _trade_summary(selected)
    source_quality = _source_quality_on_denominator(raw_forecasts, states)

    prob_rows.to_csv(OUT_DIR / "probability_rows.csv", index=False)
    score_summary.to_csv(OUT_DIR / "score_summary.csv", index=False)
    selected.to_csv(OUT_DIR / "selected_trades.csv", index=False)
    trade_summary.to_csv(OUT_DIR / "trade_summary.csv", index=False)
    source_quality.to_csv(OUT_DIR / "source_quality_on_tmax_denominator.csv", index=False)

    meta = {
        "bridge_candidates": str(BRIDGE_CANDIDATES.relative_to(ROOT)),
        "forecast_enrichment_rows": forecast_meta,
        "states": {
            "rows": int(len(states)),
            "min_date": str(states["target_date"].min()),
            "max_date": str(states["target_date"].max()),
            "cities": int(states["city"].nunique()),
            "scopes": states["scope"].value_counts().to_dict(),
        },
        "probability_rows": int(len(prob_rows)),
        "selected_rows": int(len(selected)),
        "policy": {
            "ask_floor": ASK_FLOOR,
            "ask_ceiling": ASK_CEILING,
            "edge_threshold_fee_adjusted": EDGE_THRESHOLD,
            "active_expressions": ACTIVE_EXPRESSIONS,
            "first_city_day": True,
        },
        "generated": {
            "probability_rows": str((OUT_DIR / "probability_rows.csv").relative_to(ROOT)),
            "score_summary": str((OUT_DIR / "score_summary.csv").relative_to(ROOT)),
            "selected_trades": str((OUT_DIR / "selected_trades.csv").relative_to(ROOT)),
            "trade_summary": str((OUT_DIR / "trade_summary.csv").relative_to(ROOT)),
            "source_quality": str((OUT_DIR / "source_quality_on_tmax_denominator.csv").relative_to(ROOT)),
            "report": str(REPORT_PATH.relative_to(ROOT)),
        },
    }
    SUMMARY_JSON_PATH.write_text(json.dumps(_json_ready(meta), indent=2, ensure_ascii=False), encoding="utf-8")
    _write_report(meta, source_quality, score_summary, trade_summary, selected)
    print(json.dumps(_json_ready(meta), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
