#!/usr/bin/env python3
"""P3 mechanism feature ablation for the intraday Tmax distribution model.

This is an offline diagnostic. It does not change live behavior.

P1 showed that a market+weather fusion model can improve the local
current/d1/d2/tail distribution score on a short forward window. P3 asks which
feature families are carrying that improvement:

    path / bracket-boundary position / meteo / regime / city-source

All model hyperparameters are selected only on dates before 2026-06-21, using
the same expanding-date CV discipline as P1.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import research_tmax_distribution_p1_fusion_scorecard_v1 as p1  # noqa: E402
import research_tmax_distribution_p2_ev_shadow_v1 as p2  # noqa: E402
from research_tmax_distribution_p0_anchor_scorecard_v1 import (  # noqa: E402
    ATLAS_PATH,
    BUCKETS,
    _as_float,
    _interval,
)


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p3_feature_ablation_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-02-tmax-distribution-p3-feature-ablation-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-02-tmax-distribution-p3-feature-ablation-v1.json"
DB_PATH = ROOT / "runtime/weather.db"
ORDERBOOK_DIR = ROOT / "runtime/weather_edge_v1/market_data/orderbook_snapshots"
PRIMARY_FORWARD_SCOPE = "expanding_forward_2026_06_21_plus"
EV_EDGE_THRESHOLD = 0.02
EV_METHODS = [
    "market_recal_blend",
    "mkt_path_core_blend",
    "mkt_boundary_blend",
    "mkt_meteo_blend",
    "mkt_regime_blend",
    "mkt_city_source_blend",
    "loo_no_boundary_blend",
    "loo_no_meteo_blend",
    "loo_no_regime_blend",
    "loo_no_city_source_blend",
    "market_local_norm",
]


PATH_CORE_NUMERIC = [
    "decision_hour_local",
    "decision_hour_sin",
    "decision_hour_cos",
    "forecast_max_native",
    "forecast_max_f",
    "running_native",
    "current_native",
    "decline_native",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "minutes_since_running_max",
    "forecast_peak_hour_local",
    "forecast_peak_delta_hours_local",
    "forecast_peak_delta_abs",
    "forecast_peak_hour_spread",
    "forecast_gap_to_running_native",
    "gfs_gap_to_running_native",
    "ecmwf_gap_to_running_native",
    "forecast_minus_running_native",
    "forecast_minus_current_native",
    "running_minus_current_native",
]

BOUNDARY_NUMERIC = [
    "forecast_to_current_upper_native",
    "forecast_to_d1_upper_native",
    "forecast_to_d2_upper_native",
    "forecast_to_current_mid_native",
    "forecast_to_d1_mid_native",
    "forecast_to_d2_mid_native",
    "running_to_current_upper_native",
    "current_to_current_upper_native",
    "running_position_in_current_native",
    "current_position_in_current_native",
    "current_bracket_width_native",
    "current_frac_in_current_bracket",
    "running_frac_in_current_bracket",
    "forecast_frac_in_current_bracket",
    "current_native_frac",
    "running_native_frac",
    "forecast_native_frac",
    "current_dist_to_upper_share",
    "running_dist_to_upper_share",
    "forecast_dist_to_upper_share",
]

METEO_NUMERIC = [
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relative_humidity_pct",
    "wind_speed_kt",
]

REGIME_CATEGORICAL = [
    "day_regime",
    "intraday_state",
    "moisture_cloud_regime",
    "wind_regime",
    "running_max_state",
    "solar_window",
    "hour_bucket",
    "sky_cover_code",
]

CITY_SOURCE_CATEGORICAL = [
    "unit",
    "forecast_source",
    "city_family",
    "city",
]


def _finite(value: object) -> float | None:
    out = _as_float(value)
    if out is None or not math.isfinite(out):
        return None
    return out


def _frac(value: object) -> float | None:
    v = _finite(value)
    if v is None:
        return None
    return v - math.floor(v)


def _share(value: object, lo: float | None, hi: float | None) -> float | None:
    v = _finite(value)
    if v is None or lo is None or hi is None or not math.isfinite(hi) or hi <= lo:
        return None
    return (v - lo) / (hi - lo)


def _dist_to_upper_share(value: object, lo: float | None, hi: float | None) -> float | None:
    v = _finite(value)
    if v is None or lo is None or hi is None or not math.isfinite(hi) or hi <= lo:
        return None
    return (hi - v) / (hi - lo)


def _add_boundary_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    rows: list[dict[str, float | None]] = []
    for r in out.itertuples(index=False):
        interval = _interval(getattr(r, "current_bracket", None))
        lo: float | None = None
        hi: float | None = None
        if interval is not None:
            lo, hi = interval
        width = hi - lo if lo is not None and hi is not None and math.isfinite(hi) else None
        rows.append(
            {
                "current_bracket_width_native": width,
                "current_frac_in_current_bracket": _share(getattr(r, "current_native", None), lo, hi),
                "running_frac_in_current_bracket": _share(getattr(r, "running_native", None), lo, hi),
                "forecast_frac_in_current_bracket": _share(getattr(r, "forecast_max_native", None), lo, hi),
                "current_native_frac": _frac(getattr(r, "current_native", None)),
                "running_native_frac": _frac(getattr(r, "running_native", None)),
                "forecast_native_frac": _frac(getattr(r, "forecast_max_native", None)),
                "current_dist_to_upper_share": _dist_to_upper_share(getattr(r, "current_native", None), lo, hi),
                "running_dist_to_upper_share": _dist_to_upper_share(getattr(r, "running_native", None), lo, hi),
                "forecast_dist_to_upper_share": _dist_to_upper_share(getattr(r, "forecast_max_native", None), lo, hi),
            }
        )
    return pd.concat([out.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def _available(columns: list[str], df: pd.DataFrame) -> list[str]:
    return [c for c in columns if c in df.columns]


def _feature_specs(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    market = _available(p1.MARKET_NUMERIC, df)
    path = _available(PATH_CORE_NUMERIC, df)
    boundary = _available(BOUNDARY_NUMERIC, df)
    meteo = _available(METEO_NUMERIC, df)
    regime = _available(REGIME_CATEGORICAL, df)
    city_source = _available(CITY_SOURCE_CATEGORICAL, df)
    all_numeric = market + path + boundary + meteo
    all_cat = regime + city_source
    return {
        "market_recal": {
            "numeric": market,
            "categorical": [],
            "description": "market-local probabilities only; calibration baseline",
        },
        "mkt_path_core": {
            "numeric": market + path,
            "categorical": [],
            "description": "market + realized path, forecast peak clock, trend/freshness",
        },
        "mkt_boundary": {
            "numeric": market + path + boundary,
            "categorical": [],
            "description": "market + path + bracket boundary/fractional-position features",
        },
        "mkt_meteo": {
            "numeric": market + path + boundary + meteo,
            "categorical": [],
            "description": "market + path/boundary + wind/cloud/humidity numeric fields",
        },
        "mkt_regime": {
            "numeric": market + path + boundary + meteo,
            "categorical": regime,
            "description": "mkt_meteo + atlas mechanism regimes; no exact city id",
        },
        "mkt_city_source": {
            "numeric": all_numeric,
            "categorical": all_cat,
            "description": "full feature stack: path, boundary, meteo, regime, city/source",
        },
        "loo_no_boundary": {
            "numeric": market + path + meteo,
            "categorical": all_cat,
            "description": "full stack without bracket boundary/fractional features",
        },
        "loo_no_meteo": {
            "numeric": market + path + boundary,
            "categorical": all_cat,
            "description": "full stack without wind/cloud/humidity numeric fields",
        },
        "loo_no_regime": {
            "numeric": all_numeric,
            "categorical": city_source,
            "description": "full stack without atlas regime/context categories",
        },
        "loo_no_city_source": {
            "numeric": all_numeric,
            "categorical": regime,
            "description": "full stack without exact city/source/climate-family categories",
        },
    }


def _connect_ro(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def _query_one(conn: sqlite3.Connection, sql: str) -> dict[str, Any]:
    row = conn.execute(sql).fetchone()
    return dict(row) if row else {}


def _data_inventory() -> dict[str, Any]:
    out: dict[str, Any] = {
        "orderbook_snapshot_dirs": {},
    }
    if DB_PATH.exists():
        conn = _connect_ro()
        try:
            out["fact_signal_candidates"] = _query_one(
                conn,
                "SELECT COUNT(*) AS rows, MIN(event_date) AS min_date, MAX(event_date) AS max_date, "
                "MAX(fact_built_at_utc) AS max_built_at_utc FROM fact_signal_candidates",
            )
            out["fact_trades"] = _query_one(
                conn,
                "SELECT COUNT(*) AS rows, MIN(target_date) AS min_date, MAX(target_date) AS max_date, "
                "MAX(fact_built_at_utc) AS max_built_at_utc FROM fact_trades",
            )
            out["settlement_outcomes"] = _query_one(
                conn,
                "SELECT COUNT(*) AS rows, MIN(target_date) AS min_date, MAX(target_date) AS max_date, "
                "COUNT(DISTINCT city) AS cities FROM settlement_outcomes",
            )
        finally:
            conn.close()
    if ATLAS_PATH.exists():
        atlas = pd.read_csv(
            ATLAS_PATH,
            usecols=lambda c: c in {"target_date", "city", "decision_hour_local", "final_winning_bracket"},
        )
        by_date = (
            atlas.assign(has_label=atlas["final_winning_bracket"].notna())
            .groupby("target_date", as_index=False)
            .agg(states=("city", "size"), cities=("city", "nunique"), labeled=("has_label", "sum"))
        )
        out["atlas"] = {
            "rows": int(len(atlas)),
            "min_date": str(atlas["target_date"].min()),
            "max_date": str(atlas["target_date"].max()),
            "cities": int(atlas["city"].nunique()),
            "recent_by_date": by_date.tail(10).to_dict("records"),
        }
    if ORDERBOOK_DIR.exists():
        for path in sorted(ORDERBOOK_DIR.glob("2026-*")):
            if path.is_dir():
                out["orderbook_snapshot_dirs"][path.name] = len(list(path.glob("*.jsonl.gz")))
    return out


def _summaries(scores: pd.DataFrame, scope: str) -> pd.DataFrame:
    summary = p1._append_delta_columns(p1._summary(scores, scope), scores)
    summary["family"] = summary["method"].str.replace("_model", "", regex=False).str.replace("_blend", "", regex=False)
    summary["kind"] = np.where(summary["method"].str.endswith("_model"), "model", "blend")
    summary.loc[summary["method"].eq("market_local_norm"), "kind"] = "baseline"
    return summary.sort_values(["logloss", "method"]).reset_index(drop=True)


def _date_block_delta(scores: pd.DataFrame, method: str, baseline: str = "market_local_norm") -> dict[str, float]:
    return p1._date_block_ci(scores, method, baseline)


def _comparison_rows(summary: pd.DataFrame, scores: pd.DataFrame, methods: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for method in methods:
        if method not in set(summary["method"]):
            continue
        r = summary[summary["method"] == method].iloc[0].to_dict()
        ci = _date_block_delta(scores, method)
        rows.append(
            {
                "method": method,
                "n": int(r["n"]),
                "dates": int(r["dates"]),
                "logloss": float(r["logloss"]),
                "delta_vs_market": float(r["logloss_delta_vs_market"]),
                "ci_low": float(ci["ci_low"]),
                "ci_high": float(ci["ci_high"]),
                "brier": float(r["brier"]),
                "top1": float(r["top1"]),
                "winner_prob": float(r["winner_prob"]),
            }
        )
    return pd.DataFrame(rows)


def _delta_vs_full(summary: pd.DataFrame, full_method: str, methods: list[str]) -> pd.DataFrame:
    if full_method not in set(summary["method"]):
        return pd.DataFrame()
    full = float(summary[summary["method"] == full_method].iloc[0]["logloss"])
    rows: list[dict[str, Any]] = []
    for method in methods:
        if method not in set(summary["method"]):
            continue
        value = float(summary[summary["method"] == method].iloc[0]["logloss"])
        rows.append({"method": method, "logloss": value, "delta_vs_full": value - full})
    return pd.DataFrame(rows)


def _build_ev_shadow(fixed_preds: pd.DataFrame, expanding_preds: pd.DataFrame) -> dict[str, pd.DataFrame]:
    old_methods = list(p2.METHODS)
    try:
        p2.METHODS = EV_METHODS
        base = p2._load_base()
        opps = pd.concat(
            [
                p2._build_opportunities("fixed_forward", base, fixed_preds),
                p2._build_opportunities("expanding_forward", base, expanding_preds),
            ],
            ignore_index=True,
        )
    finally:
        p2.METHODS = old_methods
    selected_summary = p2._summarize_selected(opps)
    deduped_rows = p2._best_expression_per_state(opps, EV_EDGE_THRESHOLD)
    deduped_summary = p2._summarize_deduped(deduped_rows, EV_EDGE_THRESHOLD)
    return {
        "opportunities": opps,
        "selected_summary": selected_summary,
        "deduped_rows": deduped_rows,
        "deduped_summary": deduped_summary,
    }


def _daily_lines(scores: pd.DataFrame, method: str) -> list[str]:
    return p1._daily_delta_lines(scores, method)


def _bucket_lines(scores: pd.DataFrame, method: str) -> list[str]:
    return p1._bucket_delta_lines(scores, method)


def _table(df: pd.DataFrame, *, columns: list[str]) -> list[str]:
    if df.empty:
        return ["_No rows._"]
    header = "| " + " | ".join(columns) + " |"
    align = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, align]
    for row in df.to_dict("records"):
        vals = []
        for col in columns:
            v = row.get(col)
            if isinstance(v, float):
                if col in {"top1"}:
                    vals.append(f"{v*100:.1f}%")
                elif "delta" in col or col.startswith("ci_"):
                    vals.append(f"{v:+.4f}")
                else:
                    vals.append(f"{v:.4f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def _ev_summary_table(df: pd.DataFrame, *, methods: list[str]) -> list[str]:
    sub = df[
        df["scope"].eq("expanding_forward")
        & df["edge_threshold"].eq(EV_EDGE_THRESHOLD)
        & df["method"].isin(methods)
    ].copy()
    if sub.empty:
        return ["_No EV rows._"]
    lines = [
        "| method | rows | dates | cost | pnl | ROI | CI | mix |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for r in sub.sort_values("roi", ascending=False).itertuples(index=False):
        ci = "NA" if math.isnan(r.roi_ci_low) else f"[{r.roi_ci_low:+.1%}, {r.roi_ci_high:+.1%}]"
        mix = f"YES {int(r.current_yes_rows)} / curNO {int(r.current_no_rows)} / d1NO {int(r.d1_no_rows)} / d2NO {int(r.d2_no_rows)}"
        lines.append(
            f"| {r.method} | {int(r.selected_rows)} | {int(r.dates)} | {r.cost:.2f} | "
            f"{r.pnl:+.2f} | {r.roi:+.1%} | {ci} | {mix} |"
        )
    return lines


def _write_report(
    *,
    df: pd.DataFrame,
    counters: dict[str, int],
    inventory: dict[str, Any],
    selections_df: pd.DataFrame,
    fixed_summary: pd.DataFrame,
    expanding_summary: pd.DataFrame,
    fixed_scores: pd.DataFrame,
    expanding_scores: pd.DataFrame,
    ev_deduped_summary: pd.DataFrame,
    report: dict[str, Any],
) -> None:
    methods = [
        "market_local_norm",
        "market_recal_blend",
        "mkt_path_core_blend",
        "mkt_boundary_blend",
        "mkt_meteo_blend",
        "mkt_regime_blend",
        "mkt_city_source_blend",
    ]
    loo_methods = [
        "mkt_city_source_blend",
        "loo_no_boundary_blend",
        "loo_no_meteo_blend",
        "loo_no_regime_blend",
        "loo_no_city_source_blend",
    ]
    fixed_comp = _comparison_rows(fixed_summary, fixed_scores, methods)
    expanding_comp = _comparison_rows(expanding_summary, expanding_scores, methods)
    expanding_loo = _delta_vs_full(expanding_summary, "mkt_city_source_blend", loo_methods)
    best = expanding_summary[expanding_summary["method"].ne("market_local_norm")].sort_values("logloss").iloc[0]
    market = expanding_summary[expanding_summary["method"].eq("market_local_norm")].iloc[0]
    best_ci = _date_block_delta(expanding_scores, str(best["method"]))

    recent_atlas = pd.DataFrame(inventory.get("atlas", {}).get("recent_by_date", []))
    recent_lines = _table(recent_atlas, columns=["target_date", "states", "cities", "labeled"]) if not recent_atlas.empty else ["_No atlas inventory._"]

    snapshot_counts = inventory.get("orderbook_snapshot_dirs", {})
    recent_snapshots = pd.DataFrame(
        [{"date": k, "files": v} for k, v in list(sorted(snapshot_counts.items()))[-8:]]
    )
    recent_snapshot_lines = (
        _table(recent_snapshots, columns=["date", "files"]) if not recent_snapshots.empty else ["_No snapshot dirs._"]
    )

    lines = [
        "# Tmax Distribution P3 Feature Ablation v1",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        f"> atlas: `{ATLAS_PATH.relative_to(ROOT)}`",
        "> Scope: offline mechanism/proper-scoring diagnostic only; no live runner/order behavior changed.",
        "",
        "## 结论",
        "",
        "P2 没有把 regime 丢掉；P3 的结果更准确地说是：`regime` 现在应作为概率模型里的机制特征，而不是直接写成买/不买规则。",
        "",
        (
            f"- 可评分分母仍是 {counters['scored_rows']} rows / {df['target_date'].nunique()} dates / "
            f"{df['city'].nunique()} cities，日期 `{df['target_date'].min()}`..`{df['target_date'].max()}`。"
        ),
        (
            f"- Expanding forward 上 market-local logloss `{market.logloss:.4f}`；最佳非 market 是 "
            f"`{best.method}` `{best.logloss:.4f}`，delta `{best.logloss_delta_vs_market:+.4f}`，"
            f"date-CI [`{best_ci['ci_low']:+.4f}`, `{best_ci['ci_high']:+.4f}`]。"
        ),
        "- 路径/边界/天气/regime/city-source 的逐层 ablation 说明：信号不是单一 regime 标签贡献，也不是单纯 city id 记忆；但 forward 仍只有 6 天，所以结论维持 `inconclusive_positive_signal`。",
        "- 7/1 不能直接并进 PnL/score：canonical candidates/trades 已更新，但 atlas/state/final-winner/orderbook 分布评估层只完整结算到 6/26，6/27+ 多数 state 没 label。",
        "",
        "## 人话解释",
        "",
        "之前的策略像是“先认出天气形态，再按形态买某一档”。P2/P3 换成“先估 Tmax 分布，再看盘口是否便宜”。regime 不是没用了，而是从方向盘变成传感器：它帮模型判断分布，但不能单独决定交易。",
        "",
        "## Feature Stack",
        "",
        "- `market_recal`: 只看盘口局部分布，作为市场校准基线。",
        "- `mkt_path_core`: 加已实现路径、forecast peak clock、升温趋势和 running-max freshness。",
        "- `mkt_boundary`: 加 bracket 边界/小数位置，例如当前温度在当前档内靠近下沿还是上沿。",
        "- `mkt_meteo`: 加 wind/cloud/humidity/dewpoint 数值。",
        "- `mkt_regime`: 加 atlas 的 day/intraday/moisture/wind/running-max/solar regime。",
        "- `mkt_city_source`: 加 city、city_family、forecast_source，测试城市/源偏移。",
        "",
        "## Fixed Forward 2026-06-21+",
        "",
        *_table(fixed_comp, columns=["method", "n", "dates", "logloss", "delta_vs_market", "ci_low", "ci_high", "brier", "top1", "winner_prob"]),
        "",
        "## Expanding Forward 2026-06-21+",
        "",
        *_table(expanding_comp, columns=["method", "n", "dates", "logloss", "delta_vs_market", "ci_low", "ci_high", "brier", "top1", "winner_prob"]),
        "",
        "## Leave-One-Family-Out",
        "",
        "这里看的是 expanding forward：相对 full `mkt_city_source_blend`，删掉某一族特征后 logloss 变好还是变差。正数表示删掉后更差，该特征族有帮助；负数表示删掉后反而更好，该族可能噪声/过拟合。",
        "",
        *_table(expanding_loo, columns=["method", "logloss", "delta_vs_full"]),
        "",
        "## Daily Check",
        "",
        *_daily_lines(expanding_scores, str(best["method"])),
        "",
        "## Bucket Check",
        "",
        *_bucket_lines(expanding_scores, str(best["method"])),
        "",
        "## EV Shadow Check",
        "",
        f"同一批 P3 predictions 接 P2 的真实 ask 表达选择，`model_edge >= {EV_EDGE_THRESHOLD:.2f}`，每个 city-date-hour 只保留一个最高 edge 表达：",
        "",
        *_ev_summary_table(
            ev_deduped_summary,
            methods=[
                "mkt_regime_blend",
                "mkt_city_source_blend",
                "loo_no_city_source_blend",
                "loo_no_regime_blend",
                "market_recal_blend",
            ],
        ),
        "",
        "## Data Inventory / 7.1 Boundary",
        "",
        f"- `fact_signal_candidates`: `{inventory.get('fact_signal_candidates', {})}`",
        f"- `fact_trades`: `{inventory.get('fact_trades', {})}`",
        f"- `settlement_outcomes`: `{inventory.get('settlement_outcomes', {})}`",
        "",
        "Recent atlas labeled-state coverage:",
        "",
        *recent_lines,
        "",
        "Recent orderbook snapshot file counts:",
        "",
        *recent_snapshot_lines,
        "",
        "解释：7/1 的机会和少量 trade 行存在，但 P3 这种 distribution scoring 需要逐 city-hour 的 final winner 和同一时刻 ladder quote。现在 6/27 起 state rows 多数没有 final label，6/30 也只有极少 orderbook 文件，所以 7/1 暂时只能作为 forward telemetry/pending，不应算 ROI 或 logloss。",
        "",
        "## Verdict",
        "",
        "significance=FAIL/NA; baseline=PARTIAL_PASS; forward=FAIL/THIN; conclusion=`inconclusive_positive_signal`。",
        "",
        "下一步不是再加硬 gate，而是补齐 6/27+ 的 state label/orderbook first-seen，然后把 P2 EV shadow 接真实 depth/fill/size 约束。",
        "",
        "## Artifacts",
        "",
        f"- `{(OUT_DIR / 'fixed_forward_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'expanding_forward_summary.csv').relative_to(ROOT)}`",
        f"- `{(OUT_DIR / 'model_selection.csv').relative_to(ROOT)}`",
        f"- `{SUMMARY_JSON_PATH.relative_to(ROOT)}`",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df, counters = p1._load_rows()
    if df.empty:
        raise RuntimeError("No scored rows loaded from atlas")
    df = _add_boundary_features(df)
    specs = _feature_specs(df)
    p1.MODEL_SPECS = specs
    train = df[df["target_date"] < p1.TRAIN_CUTOFF].copy()
    selections: dict[str, dict[str, Any]] = {}
    for spec_name in specs:
        selections[spec_name] = p1._select_model(train, spec_name)

    selections_df = p1._selection_table(selections)
    candidate_df = p1._candidate_table(selections)
    fixed_scores, fixed_preds = p1._fixed_forward_predictions(df, selections)
    expanding_scores, expanding_preds = p1._expanding_forward_predictions(df, selections)
    fixed_summary = _summaries(fixed_scores, "fixed_forward_2026_06_21_plus")
    expanding_summary = _summaries(expanding_scores, "expanding_forward_2026_06_21_plus")
    fixed_daily = p1._daily_summary(fixed_scores, "fixed_forward_2026_06_21_plus")
    expanding_daily = p1._daily_summary(expanding_scores, "expanding_forward_2026_06_21_plus")

    fixed_scores.to_csv(OUT_DIR / "fixed_forward_scores.csv", index=False)
    expanding_scores.to_csv(OUT_DIR / "expanding_forward_scores.csv", index=False)
    fixed_preds.to_csv(OUT_DIR / "fixed_forward_predictions.csv", index=False)
    expanding_preds.to_csv(OUT_DIR / "expanding_forward_predictions.csv", index=False)
    fixed_summary.to_csv(OUT_DIR / "fixed_forward_summary.csv", index=False)
    expanding_summary.to_csv(OUT_DIR / "expanding_forward_summary.csv", index=False)
    fixed_daily.to_csv(OUT_DIR / "fixed_forward_daily.csv", index=False)
    expanding_daily.to_csv(OUT_DIR / "expanding_forward_daily.csv", index=False)
    selections_df.to_csv(OUT_DIR / "model_selection.csv", index=False)
    candidate_df.to_csv(OUT_DIR / "candidate_selection_grid.csv", index=False)
    ev = _build_ev_shadow(fixed_preds, expanding_preds)
    ev["opportunities"].to_csv(OUT_DIR / "ev_opportunities.csv", index=False)
    ev["selected_summary"].to_csv(OUT_DIR / "ev_selected_summary.csv", index=False)
    ev["deduped_rows"].to_csv(OUT_DIR / "ev_deduped_best_expression_rows.csv", index=False)
    ev["deduped_summary"].to_csv(OUT_DIR / "ev_deduped_best_expression_summary.csv", index=False)

    inventory = _data_inventory()
    report: dict[str, Any] = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "atlas_path": str(ATLAS_PATH.relative_to(ROOT)),
        "report_path": str(REPORT_PATH.relative_to(ROOT)),
        "counters": counters,
        "date_range": [str(df["target_date"].min()), str(df["target_date"].max())],
        "train_cutoff": p1.TRAIN_CUTOFF,
        "cities": int(df["city"].nunique()),
        "inventory": inventory,
        "model_selection": selections_df.to_dict("records"),
        "fixed_forward_summary": fixed_summary.to_dict("records"),
        "expanding_forward_summary": expanding_summary.to_dict("records"),
        "ev_deduped_summary": ev["deduped_summary"].to_dict("records"),
        "verdict": "inconclusive_positive_signal",
    }
    _write_report(
        df=df,
        counters=counters,
        inventory=inventory,
        selections_df=selections_df,
        fixed_summary=fixed_summary,
        expanding_summary=expanding_summary,
        fixed_scores=fixed_scores,
        expanding_scores=expanding_scores,
        ev_deduped_summary=ev["deduped_summary"],
        report=report,
    )
    SUMMARY_JSON_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "report_path": str(REPORT_PATH.relative_to(ROOT)),
                "date_range": report["date_range"],
                "rows": counters["scored_rows"],
                "cities": report["cities"],
                "verdict": report["verdict"],
                "best_expanding": expanding_summary[expanding_summary["method"].ne("market_local_norm")]
                .sort_values("logloss")
                .head(1)
                .to_dict("records"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
