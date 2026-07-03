"""
P2 offline EV shadow for the intraday Tmax local distribution fusion.

This is not a live strategy. It consumes frozen P1 forward predictions and asks:

    If the local four-bucket probability estimate is used as a price model,
    which executable token expressions would have positive model EV?

Executable expressions are limited to asks visible in the atlas:
    - current YES
    - current bracket NO
    - d1 NO
    - d2 NO

No normalized midpoint is used as an execution price.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
ATLAS_PATH = (
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/"
    / "intraday_weather_regime_state_rows.csv"
)
P1_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p1_fusion_scorecard_v1"
P0_SCORED_PATH = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p0_anchor_scorecard_v1/scored_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p2_ev_shadow_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-02-tmax-distribution-p2-ev-shadow-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-02-tmax-distribution-p2-ev-shadow-v1.json"

BUCKETS = ["current", "d1", "d2", "tail"]
PREDICTION_SCOPES = {
    "fixed_forward": P1_DIR / "fixed_forward_predictions.csv",
    "expanding_forward": P1_DIR / "expanding_forward_predictions.csv",
}
METHODS = [
    "fusion_city_blend",
    "fusion_context_blend",
    "fusion_numeric_blend",
    "market_recal_blend",
    # Diagnostic only: local normalized market distribution is not an executable
    # probability model, but comparing it highlights proxy/normalization effects.
    "market_local_norm",
]
EXPRESSIONS = ["current_yes", "current_no", "d1_no", "d2_no"]
EDGE_THRESHOLDS = [0.0, 0.02, 0.05, 0.10]
EPS = 1e-9


def _date_block_roi_ci(rows: pd.DataFrame, n_boot: int = 1000, seed: int = 23) -> dict[str, float]:
    if rows.empty:
        return {"roi": math.nan, "ci_low": math.nan, "ci_high": math.nan, "n_dates": 0}
    by_date = rows.groupby("target_date").agg(cost=("ask", "sum"), pnl=("unit_pnl", "sum"))
    if len(by_date) < 3:
        roi = float(rows["unit_pnl"].sum() / rows["ask"].sum()) if rows["ask"].sum() else math.nan
        return {"roi": roi, "ci_low": math.nan, "ci_high": math.nan, "n_dates": int(len(by_date))}
    rng = np.random.default_rng(seed)
    arr = by_date[["cost", "pnl"]].to_numpy(dtype=float)
    boot = []
    for _ in range(n_boot):
        sample = arr[rng.integers(0, len(arr), size=len(arr))]
        cost = sample[:, 0].sum()
        pnl = sample[:, 1].sum()
        boot.append(float(pnl / cost) if cost else math.nan)
    roi = float(rows["unit_pnl"].sum() / rows["ask"].sum()) if rows["ask"].sum() else math.nan
    return {
        "roi": roi,
        "ci_low": float(np.nanpercentile(boot, 2.5)),
        "ci_high": float(np.nanpercentile(boot, 97.5)),
        "n_dates": int(len(by_date)),
    }


def _load_base() -> pd.DataFrame:
    p0_cols = [
        "city",
        "target_date",
        "decision_hour_local",
        "actual_bucket",
        "current_yes_ask",
        "current_bracket_no_ask",
        "d1_no_ask",
        "d2_no_ask",
        "market_raw_local_mass",
        "market_tail_residual_raw",
        "market_local_overround_raw",
        "market_local_undermass_raw",
        "day_regime",
        "intraday_state",
        "forecast_source",
        "city_family",
    ]
    p0 = pd.read_csv(P0_SCORED_PATH, usecols=p0_cols)
    atlas = pd.read_csv(
        ATLAS_PATH,
        usecols=[
            "city",
            "target_date",
            "decision_hour_local",
            "current_no_ask_size",
            "d1_no_ask_size",
            "d2_no_ask_size",
        ],
    )
    return p0.merge(atlas, on=["city", "target_date", "decision_hour_local"], how="left", validate="one_to_one")


def _win_probability(row: pd.Series, method: str, expression: str) -> float:
    prefix = "market" if method == "market_local_norm" else method
    p_current = float(row[f"{prefix}_p_current"])
    p_d1 = float(row[f"{prefix}_p_d1"])
    p_d2 = float(row[f"{prefix}_p_d2"])
    if expression == "current_yes":
        return p_current
    if expression == "current_no":
        return 1.0 - p_current
    if expression == "d1_no":
        return 1.0 - p_d1
    if expression == "d2_no":
        return 1.0 - p_d2
    raise ValueError(expression)


def _ask_and_size(row: pd.Series, expression: str) -> tuple[float | None, float | None]:
    if expression == "current_yes":
        return _as_float(row.get("current_yes_ask")), None
    if expression == "current_no":
        return _as_float(row.get("current_bracket_no_ask")), _as_float(row.get("current_no_ask_size"))
    if expression == "d1_no":
        return _as_float(row.get("d1_no_ask")), _as_float(row.get("d1_no_ask_size"))
    if expression == "d2_no":
        return _as_float(row.get("d2_no_ask")), _as_float(row.get("d2_no_ask_size"))
    raise ValueError(expression)


def _as_float(x: object) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def _actual_win(actual_bucket: str, expression: str) -> float:
    if expression == "current_yes":
        return float(actual_bucket == "current")
    if expression == "current_no":
        return float(actual_bucket != "current")
    if expression == "d1_no":
        return float(actual_bucket != "d1")
    if expression == "d2_no":
        return float(actual_bucket != "d2")
    raise ValueError(expression)


def _build_opportunities(scope: str, base: pd.DataFrame, preds: pd.DataFrame) -> pd.DataFrame:
    keys = ["city", "target_date", "decision_hour_local", "actual_bucket"]
    df = base.merge(preds, on=keys, how="inner", validate="one_to_one")
    rows: list[dict[str, Any]] = []
    for item in df.to_dict("records"):
        s = pd.Series(item)
        for method in METHODS:
            for expression in EXPRESSIONS:
                ask, ask_size = _ask_and_size(s, expression)
                if ask is None or ask <= 0.0 or ask >= 1.0:
                    continue
                p_win = _win_probability(s, method, expression)
                p_win = max(EPS, min(1.0 - EPS, p_win))
                win = _actual_win(str(item["actual_bucket"]), expression)
                rows.append(
                    {
                        "scope": scope,
                        "method": method,
                        "expression": expression,
                        "city": item["city"],
                        "target_date": item["target_date"],
                        "decision_hour_local": item["decision_hour_local"],
                        "actual_bucket": item["actual_bucket"],
                        "ask": ask,
                        "ask_size": ask_size,
                        "p_win": p_win,
                        "model_edge": p_win - ask,
                        "model_roi": (p_win - ask) / ask,
                        "win": win,
                        "unit_pnl": win - ask,
                        "actual_roi": (win - ask) / ask,
                        "market_raw_local_mass": item.get("market_raw_local_mass"),
                        "market_tail_residual_raw": item.get("market_tail_residual_raw"),
                        "market_local_overround_raw": item.get("market_local_overround_raw"),
                        "market_local_undermass_raw": item.get("market_local_undermass_raw"),
                        "day_regime": item.get("day_regime"),
                        "intraday_state": item.get("intraday_state"),
                        "forecast_source": item.get("forecast_source"),
                        "city_family": item.get("city_family"),
                    }
                )
    return pd.DataFrame(rows)


def _summarize_selected(opps: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for threshold in EDGE_THRESHOLDS:
        selected = opps[opps["model_edge"] >= threshold].copy()
        for (scope, method, expression), grp in selected.groupby(["scope", "method", "expression"], dropna=False):
            ci = _date_block_roi_ci(grp)
            rows.append(
                {
                    "scope": scope,
                    "method": method,
                    "expression": expression,
                    "edge_threshold": threshold,
                    "selected_rows": int(len(grp)),
                    "dates": int(grp["target_date"].nunique()),
                    "cities": int(grp["city"].nunique()),
                    "avg_ask": float(grp["ask"].mean()),
                    "avg_p_win": float(grp["p_win"].mean()),
                    "avg_model_edge": float(grp["model_edge"].mean()),
                    "win_rate": float(grp["win"].mean()),
                    "cost": float(grp["ask"].sum()),
                    "pnl": float(grp["unit_pnl"].sum()),
                    "roi": ci["roi"],
                    "roi_ci_low": ci["ci_low"],
                    "roi_ci_high": ci["ci_high"],
                    "avg_ask_size": float(grp["ask_size"].mean()) if grp["ask_size"].notna().any() else math.nan,
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["scope", "edge_threshold", "method", "expression"]
    ).reset_index(drop=True)


def _daily_summary(selected: pd.DataFrame, threshold: float, method: str) -> pd.DataFrame:
    sub = selected[(selected["model_edge"] >= threshold) & (selected["method"] == method)].copy()
    if sub.empty:
        return pd.DataFrame()
    rows = []
    for (scope, target_date), grp in sub.groupby(["scope", "target_date"]):
        rows.append(
            {
                "scope": scope,
                "target_date": target_date,
                "method": method,
                "edge_threshold": threshold,
                "rows": int(len(grp)),
                "dates": 1,
                "cities": int(grp["city"].nunique()),
                "cost": float(grp["ask"].sum()),
                "pnl": float(grp["unit_pnl"].sum()),
                "roi": float(grp["unit_pnl"].sum() / grp["ask"].sum()) if grp["ask"].sum() else math.nan,
            }
        )
    return pd.DataFrame(rows).sort_values(["scope", "target_date"]).reset_index(drop=True)


def _bucket_summary(selected: pd.DataFrame, threshold: float, method: str) -> pd.DataFrame:
    sub = selected[(selected["model_edge"] >= threshold) & (selected["method"] == method)].copy()
    if sub.empty:
        return pd.DataFrame()
    rows = []
    for (scope, expression, actual_bucket), grp in sub.groupby(["scope", "expression", "actual_bucket"]):
        rows.append(
            {
                "scope": scope,
                "method": method,
                "expression": expression,
                "actual_bucket": actual_bucket,
                "edge_threshold": threshold,
                "rows": int(len(grp)),
                "cost": float(grp["ask"].sum()),
                "pnl": float(grp["unit_pnl"].sum()),
                "roi": float(grp["unit_pnl"].sum() / grp["ask"].sum()) if grp["ask"].sum() else math.nan,
            }
        )
    return pd.DataFrame(rows).sort_values(["scope", "expression", "actual_bucket"]).reset_index(drop=True)


def _best_expression_per_state(opps: pd.DataFrame, threshold: float) -> pd.DataFrame:
    selected = opps[opps["model_edge"] >= threshold].copy()
    if selected.empty:
        return selected
    keys = ["scope", "method", "city", "target_date", "decision_hour_local"]
    return selected.sort_values("model_edge", ascending=False).groupby(keys, as_index=False).head(1)


def _summarize_deduped(rows_df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (scope, method), grp in rows_df.groupby(["scope", "method"], dropna=False):
        ci = _date_block_roi_ci(grp)
        expr_counts = grp["expression"].value_counts().to_dict()
        rows.append(
            {
                "scope": scope,
                "method": method,
                "edge_threshold": threshold,
                "selected_rows": int(len(grp)),
                "dates": int(grp["target_date"].nunique()),
                "cities": int(grp["city"].nunique()),
                "cost": float(grp["ask"].sum()),
                "pnl": float(grp["unit_pnl"].sum()),
                "roi": ci["roi"],
                "roi_ci_low": ci["ci_low"],
                "roi_ci_high": ci["ci_high"],
                "current_yes_rows": int(expr_counts.get("current_yes", 0)),
                "current_no_rows": int(expr_counts.get("current_no", 0)),
                "d1_no_rows": int(expr_counts.get("d1_no", 0)),
                "d2_no_rows": int(expr_counts.get("d2_no", 0)),
            }
        )
    return pd.DataFrame(rows).sort_values(["scope", "method"]).reset_index(drop=True)


def _deduped_daily(rows_df: pd.DataFrame, threshold: float, method: str) -> pd.DataFrame:
    sub = rows_df[rows_df["method"] == method].copy()
    if sub.empty:
        return pd.DataFrame()
    rows = []
    for (scope, target_date), grp in sub.groupby(["scope", "target_date"]):
        rows.append(
            {
                "scope": scope,
                "target_date": target_date,
                "method": method,
                "edge_threshold": threshold,
                "rows": int(len(grp)),
                "cities": int(grp["city"].nunique()),
                "cost": float(grp["ask"].sum()),
                "pnl": float(grp["unit_pnl"].sum()),
                "roi": float(grp["unit_pnl"].sum() / grp["ask"].sum()) if grp["ask"].sum() else math.nan,
            }
        )
    return pd.DataFrame(rows).sort_values(["scope", "target_date"]).reset_index(drop=True)


def _top_table(summary: pd.DataFrame, scope: str, threshold: float, methods: list[str]) -> list[str]:
    sub = summary[
        (summary["scope"] == scope)
        & (summary["edge_threshold"] == threshold)
        & (summary["method"].isin(methods))
    ].copy()
    if sub.empty:
        return ["No rows."]
    lines = [
        "| method | expression | rows | dates | avg_ask | avg_edge | win | ROI | CI |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in sub.sort_values(["method", "expression"]).itertuples(index=False):
        ci = "NA" if math.isnan(r.roi_ci_low) else f"[{r.roi_ci_low:+.1%}, {r.roi_ci_high:+.1%}]"
        lines.append(
            f"| {r.method} | {r.expression} | {int(r.selected_rows)} | {int(r.dates)} | "
            f"{r.avg_ask:.3f} | {r.avg_model_edge:+.3f} | {r.win_rate:.1%} | {r.roi:+.1%} | {ci} |"
        )
    return lines


def _write_report(
    opps: pd.DataFrame,
    summary: pd.DataFrame,
    deduped_summary: pd.DataFrame,
    deduped_daily: pd.DataFrame,
    daily: pd.DataFrame,
    bucket: pd.DataFrame,
    report: dict[str, Any],
) -> None:
    primary_method = "fusion_city_blend"
    conservative_method = "fusion_numeric_blend"
    threshold = 0.02
    primary = summary[
        (summary["scope"] == "expanding_forward")
        & (summary["method"] == primary_method)
        & (summary["edge_threshold"] == threshold)
    ]
    conservative = summary[
        (summary["scope"] == "expanding_forward")
        & (summary["method"] == conservative_method)
        & (summary["edge_threshold"] == threshold)
    ]
    p_rows = int(primary["selected_rows"].sum()) if not primary.empty else 0
    p_cost = float(primary["cost"].sum()) if not primary.empty else math.nan
    p_pnl = float(primary["pnl"].sum()) if not primary.empty else math.nan
    p_roi = p_pnl / p_cost if p_cost and not math.isnan(p_cost) else math.nan
    c_rows = int(conservative["selected_rows"].sum()) if not conservative.empty else 0
    c_cost = float(conservative["cost"].sum()) if not conservative.empty else math.nan
    c_pnl = float(conservative["pnl"].sum()) if not conservative.empty else math.nan
    c_roi = c_pnl / c_cost if c_cost and not math.isnan(c_cost) else math.nan
    report["primary_method"] = primary_method
    report["conservative_method"] = conservative_method
    report["primary_threshold"] = threshold
    report["primary_expanding_rows"] = p_rows
    report["primary_expanding_roi"] = p_roi
    report["conservative_expanding_rows"] = c_rows
    report["conservative_expanding_roi"] = c_roi
    report["verdict"] = "inconclusive_ev_shadow_only"
    dprimary = deduped_summary[
        (deduped_summary["scope"] == "expanding_forward")
        & (deduped_summary["method"] == primary_method)
    ]
    if not dprimary.empty:
        report["deduped_primary_expanding_rows"] = int(dprimary["selected_rows"].iloc[0])
        report["deduped_primary_expanding_roi"] = float(dprimary["roi"].iloc[0])

    lines = [
        "# Tmax Distribution P2 EV Shadow v1",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        "> Scope: offline EV / telemetry diagnostic only; no live runner/order behavior changed.",
        "",
        "## 结论",
        "",
        "- P2 没有重新训练模型，也没有在 forward 上挑模型；它只消费 P1 fixed/expanding predictions。",
        "- 执行价格只用 atlas 里的真实 ask：`current_yes_ask`、`current_bracket_no_ask`、`d1_no_ask`、`d2_no_ask`。",
        "- `market_local_norm` 在这里仅是 diagnostic；不能当成交概率或交易 approval，因为它是局部分布归一化 proxy。",
        (
            f"- Expanding forward, threshold `model_edge >= {threshold:.2f}`："
            f"`{primary_method}` 选出 {p_rows} expression rows，ROI {p_roi:+.1%}；"
            f"`{conservative_method}` 选出 {c_rows} rows，ROI {c_roi:+.1%}。"
        ),
        (
            f"- 更接近 shadow 的一状态一表达 dedupe：`{primary_method}` "
            f"{report.get('deduped_primary_expanding_rows', 0)} rows，"
            f"ROI {report.get('deduped_primary_expanding_roi', math.nan):+.1%}。"
        ),
        "- Verdict: `inconclusive_ev_shadow_only`。这只能说明哪些表达值得 telemetry，不支持 live。",
        "",
        "## Why This Is Still Not Live",
        "",
        "- Forward 只有 2026-06-21..2026-06-26 六天。",
        "- P1 的概率增量主要来自 `current` bucket；P2 必须防止 current 表达掩盖 d1/d2 退化。",
        "- Tail 概率仍来自 local residual proxy，不是完整 bracket ladder。",
        "- 本报告没有模拟真实 fill、滑点、订单容量、重复 city-day 限额或资金 sizing。",
        "",
        "## Expanding Forward, Edge >= 0.02",
        "",
        *_top_table(summary, "expanding_forward", threshold, [primary_method, conservative_method, "fusion_context_blend"]),
        "",
        "## Fixed Forward, Edge >= 0.02",
        "",
        *_top_table(summary, "fixed_forward", threshold, [primary_method, conservative_method, "fusion_context_blend"]),
        "",
        "## Dedupe: Best Expression Per State",
        "",
        "| scope | method | rows | dates | cost | pnl | ROI | CI | expression mix |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    dsum = deduped_summary[deduped_summary["method"].isin([primary_method, conservative_method, "fusion_context_blend"])]
    for r in dsum.itertuples(index=False):
        ci = "NA" if math.isnan(r.roi_ci_low) else f"[{r.roi_ci_low:+.1%}, {r.roi_ci_high:+.1%}]"
        mix = f"YES {int(r.current_yes_rows)} / currentNO {int(r.current_no_rows)} / d1NO {int(r.d1_no_rows)} / d2NO {int(r.d2_no_rows)}"
        lines.append(
            f"| {r.scope} | {r.method} | {int(r.selected_rows)} | {int(r.dates)} | "
            f"{r.cost:.2f} | {r.pnl:+.2f} | {r.roi:+.1%} | {ci} | {mix} |"
        )
    lines.extend(
        [
            "",
            "## Dedupe Daily Primary",
            "",
            "| scope | date | rows | cities | cost | pnl | ROI |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    dd = deduped_daily[deduped_daily["method"] == primary_method]
    for r in dd.itertuples(index=False):
        lines.append(
            f"| {r.scope} | {r.target_date} | {int(r.rows)} | {int(r.cities)} | "
            f"{r.cost:.2f} | {r.pnl:+.2f} | {r.roi:+.1%} |"
        )
    lines.extend(
        [
            "",
            "## Threshold Sweep, Expanding Forward",
            "",
            *_top_table(summary, "expanding_forward", 0.0, [primary_method, conservative_method]),
            "",
            *_top_table(summary, "expanding_forward", 0.05, [primary_method, conservative_method]),
            "",
            "## Daily Primary",
            "",
            "| scope | date | rows | cities | cost | pnl | ROI |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    dsub = daily[(daily["method"] == primary_method) & (daily["edge_threshold"] == threshold)]
    for r in dsub.itertuples(index=False):
        lines.append(
            f"| {r.scope} | {r.target_date} | {int(r.rows)} | {int(r.cities)} | "
            f"{r.cost:.2f} | {r.pnl:+.2f} | {r.roi:+.1%} |"
        )
    lines.extend(
        [
            "",
            "## Bucket / Expression Breakdown",
            "",
        "| scope | expression | actual_bucket | rows | cost | pnl | ROI |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    bsub = bucket[(bucket["edge_threshold"] == threshold)]
    bsub = bsub[bsub["scope"].eq("expanding_forward") & bsub["method"].eq(primary_method)]
    for r in bsub.itertuples(index=False):
        lines.append(
            f"| {r.scope} | {r.expression} | {r.actual_bucket} | {int(r.rows)} | "
            f"{r.cost:.2f} | {r.pnl:+.2f} | {r.roi:+.1%} |"
        )
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            "- `docs/analysis/2026-07/generated/tmax_distribution_p2_ev_shadow_v1/opportunities.csv`",
            "- `docs/analysis/2026-07/generated/tmax_distribution_p2_ev_shadow_v1/selected_summary.csv`",
            "- `docs/analysis/2026-07/generated/tmax_distribution_p2_ev_shadow_v1/deduped_best_expression_summary.csv`",
            "- `docs/analysis/2026-07/generated/tmax_distribution_p2_ev_shadow_v1/daily_primary.csv`",
            "- `docs/analysis/2026-07/generated/tmax_distribution_p2_ev_shadow_v1/bucket_breakdown.csv`",
            f"- `{SUMMARY_JSON_PATH.relative_to(ROOT)}`",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = _load_base()
    all_opps = []
    for scope, path in PREDICTION_SCOPES.items():
        preds = pd.read_csv(path)
        all_opps.append(_build_opportunities(scope, base, preds))
    opps = pd.concat(all_opps, ignore_index=True)
    summary = _summarize_selected(opps)
    deduped_rows = _best_expression_per_state(opps, 0.02)
    deduped_summary = _summarize_deduped(deduped_rows, 0.02)
    deduped_daily = pd.concat(
        [_deduped_daily(deduped_rows, 0.02, method) for method in METHODS],
        ignore_index=True,
    )
    daily = pd.concat(
        [_daily_summary(opps, 0.02, method) for method in METHODS],
        ignore_index=True,
    )
    bucket = pd.concat(
        [_bucket_summary(opps, 0.02, method) for method in METHODS],
        ignore_index=True,
    )
    opps.to_csv(OUT_DIR / "opportunities.csv", index=False)
    summary.to_csv(OUT_DIR / "selected_summary.csv", index=False)
    deduped_rows.to_csv(OUT_DIR / "deduped_best_expression_rows.csv", index=False)
    deduped_summary.to_csv(OUT_DIR / "deduped_best_expression_summary.csv", index=False)
    deduped_daily.to_csv(OUT_DIR / "deduped_best_expression_daily.csv", index=False)
    daily.to_csv(OUT_DIR / "daily_primary.csv", index=False)
    bucket.to_csv(OUT_DIR / "bucket_breakdown.csv", index=False)

    report: dict[str, Any] = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "atlas_path": str(ATLAS_PATH.relative_to(ROOT)),
        "p1_dir": str(P1_DIR.relative_to(ROOT)),
        "report_path": str(REPORT_PATH.relative_to(ROOT)),
        "opportunity_rows": int(len(opps)),
        "prediction_scopes": list(PREDICTION_SCOPES),
        "methods": METHODS,
        "expressions": EXPRESSIONS,
        "edge_thresholds": EDGE_THRESHOLDS,
        "selected_summary": summary.to_dict("records"),
    }
    _write_report(opps, summary, deduped_summary, deduped_daily, daily, bucket, report)
    SUMMARY_JSON_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "selected_summary"}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
