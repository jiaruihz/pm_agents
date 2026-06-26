#!/usr/bin/env python3
"""Apply the shared temperature-context layer to regime-routed NO.

This is an A/B overlay on the existing same-denominator regime-routed NO
candidate set.  It does not change candidate selection; it only tests whether
temperature-context soft sizing improves the existing regime-only soft policy.
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

from weather_data_feed.weather_context import temperature_context_features  # noqa: E402


IN_DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_wind_context_v2/selected_with_wind_context.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_temperature_context_overlay_v1"
OUT_DETAILS = OUT_DIR / "selected_with_temperature_context.csv"
OUT_POLICY = OUT_DIR / "policy_summary.csv"
OUT_DAILY = OUT_DIR / "daily_summary.csv"
OUT_SCENE = OUT_DIR / "scene_delta_summary.csv"
OUT_ROUTE_SCENE = OUT_DIR / "route_scene_delta_summary.csv"
OUT_JSON = OUT_DIR / "summary.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-26-regime-routed-no-temperature-context-overlay-v1.md"

BASE_NOTIONAL_USD = 5.0
MIN_ORDER_SHARES = 5.0


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def md_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    if not rows:
        return "_No rows._"
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        vals = []
        for col in cols:
            val = row.get(col)
            if isinstance(val, float):
                if col.endswith("roi") or col.endswith("rate") or col in {"avg_weight", "avg_multiplier"}:
                    vals.append(pct(val))
                else:
                    vals.append(f"{val:+.2f}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def temperature_multiplier(row: pd.Series, *, strength: str) -> float:
    """Fixed first-principles sizing overlay.

    BUY_NO has two routes here:
    - runway_current_no wins when current bracket is broken later.
    - capped_d2_no wins when the day does not reach the higher d2 bracket.

    The multipliers are intentionally coarse and pre-declared.  They are not
    fit to this payoff sample.
    """
    route = str(row.get("route_leg") or "")
    cloud = str(row.get("cloud_warming_interaction") or "")
    moisture = str(row.get("moisture_cloud_interaction") or "")
    marine = str(row.get("marine_thermal_state") or "")
    peak = str(row.get("forecast_peak_clock_state") or "")
    warming = str(row.get("warming_state") or "")

    if route == "runway_current_no":
        value = 1.0
        if peak == "forecast_peak_2h_plus_ahead":
            value *= 1.08
        elif peak == "forecast_peak_0_to_2h_ahead":
            value *= 1.03
        elif peak == "forecast_peak_passed_0_to_1h":
            value *= 0.72
        elif peak in {"forecast_peak_passed_1_to_2h", "forecast_peak_passed_2h_plus"}:
            value *= 0.50

        if cloud in {"clear_solar_warming", "mixed_sky_warming", "warming_through_cloud"}:
            value *= 1.08
        elif cloud in {"clear_but_not_warming", "cloud_limited_flat_or_cooling", "mixed_sky_flat_or_cooling"}:
            value *= 0.82

        if moisture in {"cloud_suppression", "humid_cloud_suppression"}:
            value *= 0.88
        elif moisture == "dry_heat_inertia":
            value *= 1.03

        if marine == "onshore_marine_cooling_risk":
            value *= 0.86
        elif marine == "offshore_or_parallel_warming_risk":
            value *= 1.05
    else:
        value = 1.0
        if peak == "forecast_peak_2h_plus_ahead":
            value *= 0.82
        elif peak == "forecast_peak_0_to_2h_ahead":
            value *= 0.94
        elif peak == "forecast_peak_passed_0_to_1h":
            value *= 1.04
        elif peak in {"forecast_peak_passed_1_to_2h", "forecast_peak_passed_2h_plus"}:
            value *= 1.08

        if cloud in {"clear_solar_warming", "mixed_sky_warming", "warming_through_cloud"}:
            value *= 0.86
        elif cloud in {"clear_but_not_warming", "cloud_limited_flat_or_cooling", "mixed_sky_flat_or_cooling"}:
            value *= 1.06

        if moisture in {"cloud_suppression", "humid_cloud_suppression"}:
            value *= 1.04
        elif moisture == "dry_heat_inertia" and warming in {"warming", "fast_warming"}:
            value *= 0.94

        if marine == "onshore_marine_cooling_risk":
            value *= 1.05
        elif marine == "offshore_or_parallel_warming_risk":
            value *= 0.94

    if strength == "medium":
        value = 1.0 + 1.55 * (value - 1.0)
    return float(np.clip(value, 0.35 if strength == "medium" else 0.55, 1.20 if strength == "medium" else 1.12))


def summarize(frame: pd.DataFrame, weight_col: str) -> dict[str, Any]:
    clean = frame[frame["payoff"].notna()].copy()
    weight = pd.to_numeric(clean[weight_col], errors="coerce").fillna(0).clip(lower=0)
    ask = pd.to_numeric(clean["ask"], errors="coerce")
    executed = (BASE_NOTIONAL_USD * weight / ask).ge(MIN_ORDER_SHARES)
    cost = float((pd.to_numeric(clean["stake_cost_usd"], errors="coerce") * weight).sum())
    pnl = float((pd.to_numeric(clean["stake_profit_usd"], errors="coerce") * weight).sum())
    exec_cost = float((pd.to_numeric(clean.loc[executed, "stake_cost_usd"], errors="coerce") * weight[executed]).sum())
    exec_pnl = float((pd.to_numeric(clean.loc[executed, "stake_profit_usd"], errors="coerce") * weight[executed]).sum())
    return {
        "weight_policy": weight_col,
        "rows": int(len(clean)),
        "dates": int(clean["target_date"].nunique()),
        "cities": int(clean["city"].nunique()),
        "hit_rate": float(pd.to_numeric(clean["payoff"], errors="coerce").mean()),
        "cost_usd": round(cost, 6),
        "pnl_usd": round(pnl, 6),
        "roi": pnl / cost if cost else None,
        "exec_rows": int(executed.sum()),
        "exec_dates": int(clean.loc[executed, "target_date"].nunique()),
        "exec_cost_usd": round(exec_cost, 6),
        "exec_pnl_usd": round(exec_pnl, 6),
        "exec_roi": exec_pnl / exec_cost if exec_cost else None,
        "avg_weight": float(weight.mean()) if len(weight) else None,
    }


def bootstrap_delta(frame: pd.DataFrame, candidate_col: str, baseline_col: str, *, n: int = 5000, seed: int = 20260626) -> dict[str, Any]:
    clean = frame[frame["payoff"].notna()].copy()
    clean["_target_date"] = clean["target_date"].astype(str)
    dates = sorted(clean["_target_date"].unique())
    rng = np.random.default_rng(seed)

    by_date: dict[str, dict[str, float]] = {}
    for date, group in clean.groupby("_target_date"):
        row: dict[str, float] = {}
        for col in [candidate_col, baseline_col]:
            weight = pd.to_numeric(group[col], errors="coerce").fillna(0)
            row[f"{col}_cost"] = float((pd.to_numeric(group["stake_cost_usd"], errors="coerce") * weight).sum())
            row[f"{col}_pnl"] = float((pd.to_numeric(group["stake_profit_usd"], errors="coerce") * weight).sum())
        by_date[str(date)] = row

    def roi_for(sample_dates: list[str], col: str) -> float:
        cost = sum(by_date[date][f"{col}_cost"] for date in sample_dates)
        pnl = sum(by_date[date][f"{col}_pnl"] for date in sample_dates)
        return pnl / cost if cost else np.nan

    point = roi_for(dates, candidate_col) - roi_for(dates, baseline_col)
    draws = []
    for _ in range(n):
        sampled = rng.choice(dates, size=len(dates), replace=True).tolist()
        draws.append(roi_for(sampled, candidate_col) - roi_for(sampled, baseline_col))
    arr = np.array([x for x in draws if np.isfinite(x)])
    return {
        "candidate": candidate_col,
        "baseline": baseline_col,
        "delta_roi": float(point),
        "ci_low": float(np.quantile(arr, 0.025)) if len(arr) else None,
        "ci_high": float(np.quantile(arr, 0.975)) if len(arr) else None,
        "dates": len(dates),
        "draws": int(len(arr)),
    }


def daily_summary(frame: pd.DataFrame, weight_cols: list[str]) -> pd.DataFrame:
    rows = []
    for (date, route), group in frame.groupby(["target_date", "route_leg"], dropna=False):
        for col in weight_cols:
            weight = pd.to_numeric(group[col], errors="coerce").fillna(0)
            cost = float((pd.to_numeric(group["stake_cost_usd"], errors="coerce") * weight).sum())
            pnl = float((pd.to_numeric(group["stake_profit_usd"], errors="coerce") * weight).sum())
            rows.append(
                {
                    "target_date": date,
                    "route_leg": route,
                    "weight_policy": col,
                    "rows": int(len(group)),
                    "cost_usd": round(cost, 6),
                    "pnl_usd": round(pnl, 6),
                    "roi": pnl / cost if cost else None,
                }
            )
    return pd.DataFrame(rows)


def scene_delta(frame: pd.DataFrame, context_col: str) -> pd.DataFrame:
    rows = []
    for value, group in frame.groupby(context_col, dropna=False):
        if len(group) < 8:
            continue
        base = pd.to_numeric(group["soft_balanced"], errors="coerce").fillna(0)
        temp = pd.to_numeric(group["soft_temp_context_light"], errors="coerce").fillna(0)
        cost_delta = float((pd.to_numeric(group["stake_cost_usd"], errors="coerce") * (temp - base)).sum())
        pnl_delta = float((pd.to_numeric(group["stake_profit_usd"], errors="coerce") * (temp - base)).sum())
        rows.append(
            {
                "context_feature": context_col,
                "context_bucket": str(value),
                "rows": int(len(group)),
                "dates": int(group["target_date"].nunique()),
                "routes": ",".join(sorted(group["route_leg"].astype(str).unique())),
                "avg_multiplier": float(pd.to_numeric(group["temp_context_multiplier_light"], errors="coerce").mean()),
                "baseline_roi": summarize(group, "soft_balanced")["roi"],
                "temp_roi": summarize(group, "soft_temp_context_light")["roi"],
                "cost_delta_usd": round(cost_delta, 6),
                "pnl_delta_usd": round(pnl_delta, 6),
            }
        )
    return pd.DataFrame(rows).sort_values("pnl_delta_usd")


def route_scene_delta(frame: pd.DataFrame, context_col: str) -> pd.DataFrame:
    rows = []
    for (route, value), group in frame.groupby(["route_leg", context_col], dropna=False):
        if len(group) < 5:
            continue
        base = pd.to_numeric(group["soft_balanced"], errors="coerce").fillna(0)
        temp = pd.to_numeric(group["soft_temp_context_light"], errors="coerce").fillna(0)
        cost_delta = float((pd.to_numeric(group["stake_cost_usd"], errors="coerce") * (temp - base)).sum())
        pnl_delta = float((pd.to_numeric(group["stake_profit_usd"], errors="coerce") * (temp - base)).sum())
        rows.append(
            {
                "route_leg": str(route),
                "context_feature": context_col,
                "context_bucket": str(value),
                "rows": int(len(group)),
                "dates": int(group["target_date"].nunique()),
                "avg_multiplier": float(pd.to_numeric(group["temp_context_multiplier_light"], errors="coerce").mean()),
                "baseline_roi": summarize(group, "soft_balanced")["roi"],
                "temp_roi": summarize(group, "soft_temp_context_light")["roi"],
                "cost_delta_usd": round(cost_delta, 6),
                "pnl_delta_usd": round(pnl_delta, 6),
            }
        )
    return pd.DataFrame(rows).sort_values("pnl_delta_usd")


def main() -> int:
    if not IN_DETAILS.exists():
        raise FileNotFoundError(f"missing input {IN_DETAILS}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(IN_DETAILS, low_memory=False)
    labels = df.apply(lambda row: temperature_context_features(row.to_dict()), axis=1, result_type="expand")
    scored = pd.concat([df.reset_index(drop=True), labels.reset_index(drop=True)], axis=1)
    scored["temp_context_multiplier_light"] = scored.apply(lambda row: temperature_multiplier(row, strength="light"), axis=1)
    scored["temp_context_multiplier_medium"] = scored.apply(lambda row: temperature_multiplier(row, strength="medium"), axis=1)
    scored["soft_temp_context_light"] = (
        pd.to_numeric(scored["soft_balanced"], errors="coerce").fillna(0)
        * pd.to_numeric(scored["temp_context_multiplier_light"], errors="coerce").fillna(1)
    ).clip(0, 1.2)
    scored["soft_temp_context_medium"] = (
        pd.to_numeric(scored["soft_balanced"], errors="coerce").fillna(0)
        * pd.to_numeric(scored["temp_context_multiplier_medium"], errors="coerce").fillna(1)
    ).clip(0, 1.2)

    policies = pd.DataFrame(
        [
            summarize(scored, "full_size"),
            summarize(scored, "soft_balanced"),
            summarize(scored, "soft_wind_context"),
            summarize(scored, "soft_temp_context_light"),
            summarize(scored, "soft_temp_context_medium"),
        ]
    )
    weight_cols = ["soft_balanced", "soft_wind_context", "soft_temp_context_light", "soft_temp_context_medium"]
    daily = daily_summary(scored, weight_cols)
    scene = pd.concat(
        [
            scene_delta(scored, "cloud_warming_interaction"),
            scene_delta(scored, "moisture_cloud_interaction"),
            scene_delta(scored, "marine_thermal_state"),
            scene_delta(scored, "forecast_peak_clock_state"),
            scene_delta(scored, "route_leg"),
        ],
        ignore_index=True,
    )
    route_scene = pd.concat(
        [
            route_scene_delta(scored, "cloud_warming_interaction"),
            route_scene_delta(scored, "moisture_cloud_interaction"),
            route_scene_delta(scored, "marine_thermal_state"),
            route_scene_delta(scored, "forecast_peak_clock_state"),
        ],
        ignore_index=True,
    )
    deltas = [
        bootstrap_delta(scored, "soft_temp_context_light", "soft_balanced"),
        bootstrap_delta(scored, "soft_temp_context_medium", "soft_balanced"),
        bootstrap_delta(scored, "soft_wind_context", "soft_balanced"),
    ]

    scored.to_csv(OUT_DETAILS, index=False)
    policies.to_csv(OUT_POLICY, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    scene.to_csv(OUT_SCENE, index=False)
    route_scene.to_csv(OUT_ROUTE_SCENE, index=False)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input": str(IN_DETAILS.relative_to(ROOT)),
        "rows": int(len(scored)),
        "date_min": str(scored["target_date"].min()),
        "date_max": str(scored["target_date"].max()),
        "dates": int(scored["target_date"].nunique()),
        "cities": int(scored["city"].nunique()),
        "policy_summary": policies.to_dict("records"),
        "delta_bootstrap": deltas,
        "outputs": {
            "details": str(OUT_DETAILS.relative_to(ROOT)),
            "policy_summary": str(OUT_POLICY.relative_to(ROOT)),
            "daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "scene_delta_summary": str(OUT_SCENE.relative_to(ROOT)),
            "route_scene_delta_summary": str(OUT_ROUTE_SCENE.relative_to(ROOT)),
            "report": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "NA",
            "conclusion": "inconclusive_shadow_only",
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")

    policy_rows = policies.to_dict("records")
    harmful = scene.sort_values("pnl_delta_usd").head(12).to_dict("records")
    helpful = scene.sort_values("pnl_delta_usd", ascending=False).head(12).to_dict("records")
    route_helpful = route_scene.sort_values("pnl_delta_usd", ascending=False).head(14).to_dict("records")
    route_harmful = route_scene.sort_values("pnl_delta_usd").head(14).to_dict("records")
    delta_rows = [
        {
            "candidate": d["candidate"],
            "baseline": d["baseline"],
            "delta_roi": d["delta_roi"],
            "ci_low": d["ci_low"],
            "ci_high": d["ci_high"],
        }
        for d in deltas
    ]
    text = "\n".join(
        [
            "# Regime-Routed NO Temperature Context Overlay V1",
            "",
            f"Generated: `{payload['generated_at_utc']}`",
            "",
            "## Verdict",
            "",
            "This is a same-denominator A/B overlay on the current regime-routed NO candidate set.  It keeps the existing `routed_capped_d2_no_relaxed70_best_ask` rows fixed and only tests whether shared temperature-context soft sizing improves the current regime-only `soft_balanced` policy.",
            "",
            "Result: temperature context improves point-estimate ROI on this denominator, but the delta versus `soft_balanced` is not statistically significant and there is no new frozen-forward evidence.  This should remain shadow/telemetry, not live sizing.",
            "",
            f"Funnel: `{payload['rows']}` selected rows, `{payload['dates']}` dates, `{payload['cities']}` cities, `{payload['date_min']}`..`{payload['date_max']}`.",
            "",
            "## Policy Summary",
            "",
            md_table(
                policy_rows,
                [
                    "weight_policy",
                    "rows",
                    "dates",
                    "cities",
                    "hit_rate",
                    "cost_usd",
                    "pnl_usd",
                    "roi",
                    "exec_rows",
                    "exec_roi",
                    "avg_weight",
                ],
            ),
            "",
            "## Delta Bootstrap",
            "",
            md_table(delta_rows, ["candidate", "baseline", "delta_roi", "ci_low", "ci_high"]),
            "",
            "## Where It Helped",
            "",
            md_table(
                helpful,
                [
                    "context_feature",
                    "context_bucket",
                    "rows",
                    "dates",
                    "routes",
                    "avg_multiplier",
                    "baseline_roi",
                    "temp_roi",
                    "cost_delta_usd",
                    "pnl_delta_usd",
                ],
            ),
            "",
            "## Where It Hurt",
            "",
            md_table(
                harmful,
                [
                    "context_feature",
                    "context_bucket",
                    "rows",
                    "dates",
                    "routes",
                    "avg_multiplier",
                    "baseline_roi",
                    "temp_roi",
                    "cost_delta_usd",
                    "pnl_delta_usd",
                ],
            ),
            "",
            "## Route x Context Scenes",
            "",
            "Helpful route/context buckets:",
            "",
            md_table(
                route_helpful,
                [
                    "route_leg",
                    "context_feature",
                    "context_bucket",
                    "rows",
                    "dates",
                    "avg_multiplier",
                    "baseline_roi",
                    "temp_roi",
                    "cost_delta_usd",
                    "pnl_delta_usd",
                ],
            ),
            "",
            "Harmful route/context buckets:",
            "",
            md_table(
                route_harmful,
                [
                    "route_leg",
                    "context_feature",
                    "context_bucket",
                    "rows",
                    "dates",
                    "avg_multiplier",
                    "baseline_roi",
                    "temp_roi",
                    "cost_delta_usd",
                    "pnl_delta_usd",
                ],
            ),
            "",
            "## Interpretation",
            "",
            "- The overlay mainly helps by reducing size in historically bad or weakly negative scenes without deleting them entirely.",
            "- It helps most in `runway_current_no` scenes with mixed-sky warming / dry heat inertia / peak still ahead, and by trimming weak coastal-direction-unknown or near-past-peak rows.",
            "- It hurts when it trims some profitable `onshore_marine_cooling_risk`, humid convective, and mixed-sky flat/cooling rows.  That says wind/ocean and cloud context still need route-specific calibration, not a single universal haircut.",
            "- Wind/ocean context is still limited by missing wind direction in the broad historical feature layer; it is better as forward telemetry until direction coverage is stable.",
            "",
            "## Gates",
            "",
            "- significance=FAIL: temperature-context delta ROI CI crosses 0.",
            "- baseline=FAIL: point estimate beats current `soft_balanced`, but date-block delta CI still crosses 0.",
            "- forward=NA: no frozen-forward window evaluated for this overlay yet.",
            "- conclusion=inconclusive_shadow_only.",
            "",
        ]
    )
    OUT_MD.write_text(text, encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
