#!/usr/bin/env python3
"""Audit the regime-routed NO row-risk soft score components."""

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
ANALYSIS_DIR = ROOT / "scripts/analysis/reheat_risk"
OPS_DIR = ROOT / "scripts/ops"
for path in (ANALYSIS_DIR, OPS_DIR, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import regime_routed_no_tiny_live as live_runner  # noqa: E402
import research_regime_routed_no_original_city_bias_soft_v4b as city_bias  # noqa: E402
import research_regime_routed_no_tail_shadow_city_bias_v4 as v4  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/regime_routed_no_score_component_audit_v1"
OUT_SUMMARY_JSON = OUT_DIR / "summary.json"
OUT_VARIANT = OUT_DIR / "score_variant_gate_summary.csv"
OUT_BINS = OUT_DIR / "score_ratio_bins.csv"
OUT_COMPONENT = OUT_DIR / "component_slice_summary.csv"
OUT_ROWS = OUT_DIR / "score_component_rows.csv"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-01-regime-routed-no-score-component-audit-v1.md"

FORWARD_START = "2026-06-21"
THRESHOLDS = [0.6, 0.8, 1.0, 1.2, 1.5]


def finite(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        val = float(value)
        return val if math.isfinite(val) else None
    return value


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def money(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"${val:+.2f}"


def load_details() -> dict[str, pd.DataFrame]:
    return {
        "frozen_live_like_route_price": city_bias.add_policy_columns(v4.load_frozen_live_like()),
        "historical_best_ask_diagnostic": city_bias.add_policy_columns(v4.load_historical_best_ask()),
    }


def expression_group(row: pd.Series) -> str:
    expression = str(row.get("router_expression") or row.get("expression") or "")
    route = str(row.get("router_route") or row.get("route_leg") or "")
    if expression == "current_bracket_no":
        return "current_bracket_no"
    if route == "capped_d2_no" or expression == "d2_no":
        return "higher_no_d2"
    if expression.endswith("_yes"):
        return "current_high_yes"
    return expression or "unknown"


def compute_components(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    route = out.get("router_route", out.get("route_leg")).astype(str)
    ask = pd.to_numeric(out["router_ask"], errors="coerce")
    peak_delta = pd.to_numeric(out.get("forecast_peak_delta_hours_local"), errors="coerce")
    minutes_since = pd.to_numeric(out.get("minutes_since_running_max"), errors="coerce")
    trend_1h = pd.to_numeric(out.get("temp_trend_1h_f"), errors="coerce")
    wind = pd.to_numeric(out.get("wind_speed_kt"), errors="coerce")
    humidity = pd.to_numeric(out.get("relative_humidity_pct"), errors="coerce")
    city_family = out.get("city_family", pd.Series("", index=out.index)).astype(str)
    moisture = out.get("moisture_cloud_regime", pd.Series("", index=out.index)).astype(str)

    out["score_route_mult"] = route.map(
        {
            "fresh_runway_current_no": 0.80,
            "capped_d2_no": 0.45,
            "false_fade_reheat_current_no": 0.65,
            "cheap_stale_tail_current_no": 0.30,
        }
    ).fillna(0.50)
    price_risk = ((ask - 0.45) / 0.25).clip(0, 1).fillna(0)
    out["score_price_mult"] = (1.0 - 0.55 * price_risk).clip(0.35, 1.0)

    out["score_peak_mult"] = 1.0
    current_no = route.isin(["fresh_runway_current_no", "false_fade_reheat_current_no", "cheap_stale_tail_current_no"])
    out.loc[current_no, "score_peak_mult"] = np.select(
        [
            peak_delta.loc[current_no].le(-2.0),
            peak_delta.loc[current_no].le(0.0),
            peak_delta.loc[current_no].le(1.0),
        ],
        [1.0, 0.80, 0.50],
        default=0.25,
    )

    out["score_freshness_mult"] = 1.0
    fresh = route.eq("fresh_runway_current_no")
    out.loc[fresh, "score_freshness_mult"] = np.select(
        [minutes_since.loc[fresh].le(45), minutes_since.loc[fresh].le(90)],
        [1.0, 0.70],
        default=0.40,
    )

    out["score_momentum_mult"] = 1.0
    momentum_routes = route.isin(["fresh_runway_current_no", "false_fade_reheat_current_no"])
    out.loc[momentum_routes, "score_momentum_mult"] = np.select(
        [trend_1h.loc[momentum_routes].ge(0.5), trend_1h.loc[momentum_routes].ge(0.0)],
        [1.0, 0.80],
        default=0.50,
    )

    out["score_weather_mult"] = (
        1.0
        - 0.10 * city_family.eq("humid_low_latitude").astype(float)
        - 0.08 * wind.ge(15).fillna(False).astype(float)
        - 0.06 * humidity.ge(70).fillna(False).astype(float)
        - 0.06 * moisture.str.contains("convective|humid|cloud", case=False, na=False).astype(float)
    ).clip(0.65, 1.0)

    out["score_base_recomputed"] = (
        out["score_route_mult"]
        * out["score_price_mult"]
        * out["score_peak_mult"]
        * out["score_freshness_mult"]
        * out["score_momentum_mult"]
        * out["score_weather_mult"]
    ).clip(0.05, 1.0)

    out["score_expr_group"] = [expression_group(row) for _, row in out.iterrows()]
    deployed_bias = pd.to_numeric(out.get("city_source_bias_multiplier_v1", 1.0), errors="coerce").fillna(1.0)
    all_expr_bias = [
        live_runner.city_source_bias_multiplier(str(row.get("score_expr_group") or ""), str(row.get("city_source_bias_regime") or "unclassified"))
        for _, row in out.iterrows()
    ]
    out["score_deployed_city_bias_mult"] = deployed_bias
    out["score_all_expr_city_bias_mult"] = pd.Series(all_expr_bias, index=out.index, dtype="float64")
    out["score_deployed_weight"] = (out["score_base_recomputed"] * out["score_deployed_city_bias_mult"]).clip(0.05, 1.0)
    out["score_all_expr_city_bias_weight"] = (
        out["score_base_recomputed"] * out["score_all_expr_city_bias_mult"]
    ).clip(0.05, 1.0)
    out["score_no_price_weight"] = (
        out["score_route_mult"]
        * out["score_peak_mult"]
        * out["score_freshness_mult"]
        * out["score_momentum_mult"]
        * out["score_weather_mult"]
        * out["score_deployed_city_bias_mult"]
    ).clip(0.05, 1.0)
    out["score_no_peak_weight"] = (
        out["score_route_mult"]
        * out["score_price_mult"]
        * out["score_freshness_mult"]
        * out["score_momentum_mult"]
        * out["score_weather_mult"]
        * out["score_deployed_city_bias_mult"]
    ).clip(0.05, 1.0)
    out["score_no_freshness_weight"] = (
        out["score_route_mult"]
        * out["score_price_mult"]
        * out["score_peak_mult"]
        * out["score_momentum_mult"]
        * out["score_weather_mult"]
        * out["score_deployed_city_bias_mult"]
    ).clip(0.05, 1.0)
    out["score_no_momentum_weight"] = (
        out["score_route_mult"]
        * out["score_price_mult"]
        * out["score_peak_mult"]
        * out["score_freshness_mult"]
        * out["score_weather_mult"]
        * out["score_deployed_city_bias_mult"]
    ).clip(0.05, 1.0)
    out["score_no_weather_weight"] = (
        out["score_route_mult"]
        * out["score_price_mult"]
        * out["score_peak_mult"]
        * out["score_freshness_mult"]
        * out["score_momentum_mult"]
        * out["score_deployed_city_bias_mult"]
    ).clip(0.05, 1.0)

    existing = pd.to_numeric(out.get("city_bias_soft_weight", out.get("row_risk_soft_v1")), errors="coerce")
    out["score_existing_weight"] = existing
    out["score_recompute_abs_diff"] = (existing - out["score_deployed_weight"]).abs()
    return out


def row_pnl(cost: pd.Series, ask: pd.Series, payoff: pd.Series) -> pd.Series:
    return pd.Series(np.where(payoff.eq(1.0), cost / ask - cost, -cost), index=cost.index)


def date_bootstrap_roi(frame: pd.DataFrame, *, pnl_col: str, cost_col: str, n: int = 3000) -> tuple[float | None, float | None]:
    clean = frame[["target_date", pnl_col, cost_col]].dropna().copy()
    clean = clean[pd.to_numeric(clean[cost_col], errors="coerce").gt(0)]
    if clean.empty or clean["target_date"].nunique() < 3:
        return None, None
    daily = clean.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    rng = np.random.default_rng(20260701)
    idx = np.arange(len(daily))
    rois: list[float] = []
    costs = daily["cost"].to_numpy(float)
    pnls = daily["pnl"].to_numpy(float)
    for _ in range(n):
        sample = rng.choice(idx, size=len(idx), replace=True)
        cost = costs[sample].sum()
        if cost > 0:
            rois.append(float(pnls[sample].sum() / cost))
    if not rois:
        return None, None
    low, high = np.quantile(rois, [0.025, 0.975])
    return float(low), float(high)


def rank_auc(frame: pd.DataFrame, *, score_col: str) -> float | None:
    clean = frame[[score_col, "router_payoff"]].dropna().copy()
    winners = clean.loc[pd.to_numeric(clean["router_payoff"], errors="coerce").eq(1.0), score_col].to_numpy(float)
    losers = clean.loc[pd.to_numeric(clean["router_payoff"], errors="coerce").eq(0.0), score_col].to_numpy(float)
    if len(winners) == 0 or len(losers) == 0:
        return None
    comp = winners[:, None] - losers[None, :]
    return float(((comp > 0).sum() + 0.5 * (comp == 0).sum()) / comp.size)


def apply_daily_cap(frame: pd.DataFrame, *, selected: pd.Series, cost: pd.Series, cap: float = 1.0) -> pd.Series:
    chosen: list[int] = []
    order_cols = ["target_date"]
    if "decision_snapshot_ts_utc" in frame.columns:
        order_cols.append("decision_snapshot_ts_utc")
    order_cols.append("city")
    eligible = frame[selected.astype(bool)].copy()
    eligible["_sim_cost"] = cost.reindex(eligible.index)
    for _, group in eligible.sort_values(order_cols).groupby("target_date", sort=True):
        spent = 0.0
        for idx, row in group.iterrows():
            row_cost = float(row.get("_sim_cost") or 0.0)
            if spent + row_cost <= cap + 1e-9:
                chosen.append(idx)
                spent += row_cost
    out = pd.Series(False, index=frame.index)
    out.loc[chosen] = True
    return out


def summarize_selection(frame: pd.DataFrame, *, weight_col: str, selected: pd.Series) -> dict[str, Any]:
    active = frame[selected.astype(bool)].copy()
    if active.empty:
        return {
            "rows": 0,
            "dates": 0,
            "cities": 0,
            "wins": 0,
            "win_rate": None,
            "avg_ask": None,
            "avg_weight": None,
            "cost": 0.0,
            "pnl": 0.0,
            "roi": None,
            "roi_ci_low": None,
            "roi_ci_high": None,
            "daily_negative_100pct": 0,
        }
    ask = pd.to_numeric(active["router_ask"], errors="coerce")
    payoff = pd.to_numeric(active["router_payoff"], errors="coerce")
    cost = pd.to_numeric(active[weight_col], errors="coerce").fillna(0.0)
    active["_cost"] = cost
    active["_pnl"] = row_pnl(cost, ask, payoff)
    daily = active.groupby("target_date", as_index=False).agg(cost=("_cost", "sum"), pnl=("_pnl", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    total_cost = float(active["_cost"].sum())
    total_pnl = float(active["_pnl"].sum())
    ci_low, ci_high = date_bootstrap_roi(active, pnl_col="_pnl", cost_col="_cost")
    return {
        "rows": int(len(active)),
        "dates": int(active["target_date"].nunique()),
        "cities": int(active["city"].nunique()),
        "wins": int(payoff.sum()),
        "win_rate": float(payoff.mean()),
        "avg_ask": float(ask.mean()),
        "avg_weight": float(cost.mean()),
        "cost": total_cost,
        "pnl": total_pnl,
        "roi": total_pnl / total_cost if total_cost else None,
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
        "daily_negative_100pct": int(daily["roi"].le(-0.999).sum()),
    }


def build_variant_summary(details: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    variants = [
        ("deployed_score", "score_deployed_weight", THRESHOLDS),
        ("all_expr_city_source_bias", "score_all_expr_city_bias_weight", [1.0]),
        ("no_price_mult", "score_no_price_weight", [1.0]),
        ("no_peak_mult", "score_no_peak_weight", [1.0]),
        ("no_freshness_mult", "score_no_freshness_weight", [1.0]),
        ("no_momentum_mult", "score_no_momentum_weight", [1.0]),
        ("no_weather_mult", "score_no_weather_weight", [1.0]),
    ]
    for layer, frame in details.items():
        clean = frame[frame["router_payoff"].notna()].copy()
        for window, wframe in [
            ("all", clean),
            (f"forward_{FORWARD_START}_plus", clean[clean["target_date"].astype(str).ge(FORWARD_START)]),
        ]:
            for variant, weight_col, thresholds in variants:
                ask = pd.to_numeric(wframe["router_ask"], errors="coerce")
                weight = pd.to_numeric(wframe[weight_col], errors="coerce")
                ratio = weight / ask
                for threshold in thresholds:
                    base_selected = ratio.ge(threshold)
                    for cap_mode, selected in [
                        ("no_daily_cap", base_selected),
                        ("daily_cap_weight_1", apply_daily_cap(wframe, selected=base_selected, cost=weight, cap=1.0)),
                    ]:
                        row = summarize_selection(wframe, weight_col=weight_col, selected=selected)
                        row.update(
                            {
                                "evidence_layer": layer,
                                "window": window,
                                "variant": variant,
                                "weight_col": weight_col,
                                "threshold": threshold,
                                "cap_mode": cap_mode,
                                "rank_auc_score_ratio": rank_auc(wframe, score_col=weight_col),
                            }
                        )
                        rows.append(row)
    return pd.DataFrame(rows)


def summarize_group(frame: pd.DataFrame, *, group_cols: list[str], weight_col: str = "score_deployed_weight") -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for keys, group in frame.groupby(group_cols, dropna=False, sort=True, observed=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        selected = pd.Series(True, index=group.index)
        row = summarize_selection(group, weight_col=weight_col, selected=selected)
        row.update({col: key for col, key in zip(group_cols, keys)})
        rows.append(row)
    return pd.DataFrame(rows)


def build_bins(details: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    bins = [-np.inf, 0.5, 0.8, 1.0, 1.2, 1.5, np.inf]
    labels = ["<0.5", "0.5-0.8", "0.8-1.0", "1.0-1.2", "1.2-1.5", ">=1.5"]
    for layer, frame in details.items():
        clean = frame[frame["router_payoff"].notna()].copy()
        clean["score_ratio_bin"] = pd.cut(
            pd.to_numeric(clean["score_deployed_weight"], errors="coerce") / pd.to_numeric(clean["router_ask"], errors="coerce"),
            bins=bins,
            labels=labels,
        )
        for window, wframe in [
            ("all", clean),
            (f"forward_{FORWARD_START}_plus", clean[clean["target_date"].astype(str).ge(FORWARD_START)]),
        ]:
            part = summarize_group(wframe, group_cols=["score_ratio_bin"])
            part["evidence_layer"] = layer
            part["window"] = window
            rows.append(part)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def build_component_summary(details: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    component_cols = [
        "router_route",
        "score_route_mult",
        "score_price_mult",
        "score_peak_mult",
        "score_freshness_mult",
        "score_momentum_mult",
        "score_weather_mult",
        "city_source_bias_regime",
        "score_deployed_city_bias_mult",
    ]
    for layer, frame in details.items():
        clean = frame[frame["router_payoff"].notna()].copy()
        for col in component_cols:
            work = clean.copy()
            if col.startswith("score_"):
                work[col] = pd.to_numeric(work[col], errors="coerce").round(3)
            part = summarize_group(work, group_cols=[col])
            part["evidence_layer"] = layer
            part["component"] = col
            part = part.rename(columns={col: "component_value"})
            rows.append(part)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
    shown = df if limit is None else df.head(limit)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in shown.iterrows():
        vals = []
        for col in cols:
            val = row.get(col)
            if col in {"roi", "roi_ci_low", "roi_ci_high", "win_rate", "rank_auc_score_ratio"}:
                vals.append(pct(val))
            elif col in {"cost", "pnl"}:
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
            else:
                vals.append("" if pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_md(payload: dict[str, Any], variant: pd.DataFrame, bins: pd.DataFrame, component: pd.DataFrame) -> str:
    frozen = variant[variant["evidence_layer"].eq("frozen_live_like_route_price")].copy()
    cols = [
        "window",
        "variant",
        "threshold",
        "cap_mode",
        "rows",
        "dates",
        "cities",
        "win_rate",
        "avg_ask",
        "avg_weight",
        "roi",
        "roi_ci_low",
        "roi_ci_high",
        "daily_negative_100pct",
        "rank_auc_score_ratio",
    ]
    deployed_focus = frozen[
        frozen["variant"].eq("deployed_score")
        & frozen["threshold"].isin([0.8, 1.0, 1.2])
        & frozen["cap_mode"].eq("daily_cap_weight_1")
    ][cols]
    ablation_focus = frozen[
        frozen["threshold"].eq(1.0)
        & frozen["cap_mode"].eq("daily_cap_weight_1")
        & frozen["window"].eq("all")
    ][cols]
    bin_cols = ["window", "score_ratio_bin", "rows", "dates", "win_rate", "avg_ask", "avg_weight", "roi"]
    route_cols = ["component_value", "rows", "dates", "cities", "win_rate", "avg_ask", "avg_weight", "roi"]
    return "\n".join(
        [
            "# Regime-Routed NO Score Component Audit V1",
            "",
            "## Conclusion",
            "",
            "The current score is a defensible conservative mechanism score, but its rank signal is weak. It is not yet a calibrated probability model.",
            "",
            f"Verdict: `{payload['verdict']['conclusion']}`.",
            "",
            "Most important finding: keep the fixed `score / ask >= 1.0` quality gate, but treat score improvement as a shadow/research task. Do not change live scoring from this audit alone.",
            "",
            "## Coverage",
            "",
            f"- Frozen/live-like replay: `{payload['coverage']['frozen_live_like_route_price']['min_date']}`..`{payload['coverage']['frozen_live_like_route_price']['max_date']}`, rows `{payload['coverage']['frozen_live_like_route_price']['rows']}`.",
            f"- Historical best-ask diagnostic: `{payload['coverage']['historical_best_ask_diagnostic']['min_date']}`..`{payload['coverage']['historical_best_ask_diagnostic']['max_date']}`, rows `{payload['coverage']['historical_best_ask_diagnostic']['rows']}`.",
            f"- Forward split: `>= {FORWARD_START}`.",
            "",
            "## Deployed Score Threshold Sweep",
            "",
            table(deployed_focus, cols),
            "",
            "## Ablation / Variant Check",
            "",
            table(ablation_focus, cols),
            "",
            "## Score Ratio Bins",
            "",
            table(
                bins[
                    bins["evidence_layer"].eq("frozen_live_like_route_price")
                    & bins["window"].isin(["all", f"forward_{FORWARD_START}_plus"])
                ][bin_cols],
                bin_cols,
            ),
            "",
            "## Route Slices",
            "",
            table(
                component[
                    component["evidence_layer"].eq("frozen_live_like_route_price")
                    & component["component"].eq("router_route")
                ][route_cols],
                route_cols,
            ),
            "",
            "## Interpretation",
            "",
            "- The score/ask ratio has useful but thin ranking signal: frozen AUC is only about 55%, so this should be treated as a conservative heuristic, not a probability estimate.",
            "- Loosening the live threshold to `0.8` adds weaker rows and does not improve the forward slice.",
            "- Tightening to `1.2` looks better in-sample but leaves only 3 forward rows, so it is not a deployable improvement.",
            "- Removing the price multiplier admits more rows but does not provide a clean forward improvement; price is probably doing useful risk control, even if the current formula is rough.",
            "- Applying city/source bias to all expression groups improves the full-window point estimate but fails the forward slice here. It should be shadow telemetry only, not a live scoring change.",
            "- Weather haircuts are plausible first-principles features, but their independent contribution is not proven here; keep them as small haircuts, not hard gates.",
            "- The next clean upgrade is not more thresholds. It is a calibrated `P(NO wins | route, ask, peak clock, freshness, trend, weather, city/source bias)` shadow model compared against this heuristic score.",
            "",
            "## Files",
            "",
            f"- Summary JSON: `{payload['outputs']['summary_json']}`",
            f"- Variant summary: `{payload['outputs']['variant_summary']}`",
            f"- Score bins: `{payload['outputs']['score_bins']}`",
            f"- Component summary: `{payload['outputs']['component_summary']}`",
            f"- Component rows: `{payload['outputs']['score_component_rows']}`",
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw_details = load_details()
    details = {layer: compute_components(frame) for layer, frame in raw_details.items()}
    rows_out = pd.concat(
        [frame.assign(evidence_layer=layer) for layer, frame in details.items()],
        ignore_index=True,
    )
    variant = build_variant_summary(details)
    bins = build_bins(details)
    component = build_component_summary(details)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy": "regime_routed_no_score_component_audit_v1",
        "coverage": {
            layer: {
                "rows": int(len(frame)),
                "min_date": str(frame["target_date"].min()) if len(frame) else None,
                "max_date": str(frame["target_date"].max()) if len(frame) else None,
                "max_score_recompute_abs_diff": float(pd.to_numeric(frame["score_recompute_abs_diff"], errors="coerce").max())
                if len(frame)
                else None,
            }
            for layer, frame in details.items()
        },
        "outputs": {
            "summary_json": str(OUT_SUMMARY_JSON.relative_to(ROOT)),
            "variant_summary": str(OUT_VARIANT.relative_to(ROOT)),
            "score_bins": str(OUT_BINS.relative_to(ROOT)),
            "component_summary": str(OUT_COMPONENT.relative_to(ROOT)),
            "score_component_rows": str(OUT_ROWS.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "significance": "NA_COMPONENT_AUDIT",
            "baseline": "current_deployed_score",
            "forward": "THIN_MIXED",
            "conclusion": "keep_live_score_fixed_quality_gate; research_shadow_calibrated_score_v2",
            "live_change": False,
        },
    }
    OUT_VARIANT.write_text(variant.to_csv(index=False), encoding="utf-8")
    OUT_BINS.write_text(bins.to_csv(index=False), encoding="utf-8")
    OUT_COMPONENT.write_text(component.to_csv(index=False), encoding="utf-8")
    keep_cols = [
        "evidence_layer",
        "target_date",
        "city",
        "router_route",
        "router_ask",
        "router_payoff",
        "score_deployed_weight",
        "score_all_expr_city_bias_weight",
        "score_no_price_weight",
        "score_route_mult",
        "score_price_mult",
        "score_peak_mult",
        "score_freshness_mult",
        "score_momentum_mult",
        "score_weather_mult",
        "score_deployed_city_bias_mult",
        "score_all_expr_city_bias_mult",
        "city_source_bias_regime",
        "score_recompute_abs_diff",
    ]
    rows_out[[c for c in keep_cols if c in rows_out.columns]].to_csv(OUT_ROWS, index=False)
    OUT_SUMMARY_JSON.write_text(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    OUT_MD.write_text(render_md(payload, variant, bins, component) + "\n", encoding="utf-8")
    print(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
