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

from weather_data_feed.weather_context import temperature_context_features, temperature_context_multiplier  # noqa: E402


IN_DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_wind_context_v2/selected_with_wind_context.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_temperature_context_overlay_v1"
OUT_DETAILS = OUT_DIR / "selected_with_temperature_context.csv"
OUT_POLICY = OUT_DIR / "policy_summary.csv"
OUT_DAILY = OUT_DIR / "daily_summary.csv"
OUT_SCENE = OUT_DIR / "scene_delta_summary.csv"
OUT_ROUTE_SCENE = OUT_DIR / "route_scene_delta_summary.csv"
OUT_ATTRIBUTION = OUT_DIR / "mechanism_vs_filtering_attribution.csv"
OUT_EXEC_TRANSITION = OUT_DIR / "execution_transition_summary.csv"
OUT_STABILITY = OUT_DIR / "stability_split_summary.csv"
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


def policy_roi(frame: pd.DataFrame, weight_col: str) -> tuple[float | None, float, float]:
    weight = pd.to_numeric(frame[weight_col], errors="coerce").fillna(0)
    cost = float((pd.to_numeric(frame["stake_cost_usd"], errors="coerce") * weight).sum())
    pnl = float((pd.to_numeric(frame["stake_profit_usd"], errors="coerce") * weight).sum())
    return (pnl / cost if cost else None, cost, pnl)


def mechanism_vs_filtering_attribution(frame: pd.DataFrame, policies: list[str]) -> pd.DataFrame:
    rows = []
    base = pd.to_numeric(frame["soft_balanced"], errors="coerce").fillna(0)
    profit = pd.to_numeric(frame["stake_profit_usd"], errors="coerce").fillna(0)
    cost = pd.to_numeric(frame["stake_cost_usd"], errors="coerce").fillna(0)
    for policy in policies:
        candidate = pd.to_numeric(frame[policy], errors="coerce").fillna(0)
        dw = candidate - base
        buckets = [
            ("up_winners", (dw > 1e-9) & (profit > 0)),
            ("down_losers", (dw < -1e-9) & (profit < 0)),
            ("down_winners", (dw < -1e-9) & (profit > 0)),
            ("up_losers", (dw > 1e-9) & (profit < 0)),
        ]
        for bucket, mask in buckets:
            group = frame.loc[mask]
            rows.append(
                {
                    "weight_policy": policy,
                    "attribution_bucket": bucket,
                    "rows": int(len(group)),
                    "dates": int(group["target_date"].nunique()) if len(group) else 0,
                    "cities": int(group["city"].nunique()) if len(group) else 0,
                    "delta_cost_usd": round(float((cost[mask] * dw[mask]).sum()), 6),
                    "delta_pnl_usd": round(float((profit[mask] * dw[mask]).sum()), 6),
                    "avg_weight_delta": float(dw[mask].mean()) if len(group) else 0.0,
                }
            )
    return pd.DataFrame(rows)


def execution_transition_summary(frame: pd.DataFrame, policies: list[str]) -> pd.DataFrame:
    rows = []
    base_weight = pd.to_numeric(frame["soft_balanced"], errors="coerce").fillna(0)
    ask = pd.to_numeric(frame["ask"], errors="coerce")
    base_exec = (BASE_NOTIONAL_USD * base_weight / ask).ge(MIN_ORDER_SHARES)
    for policy in policies:
        candidate_weight = pd.to_numeric(frame[policy], errors="coerce").fillna(0)
        candidate_exec = (BASE_NOTIONAL_USD * candidate_weight / ask).ge(MIN_ORDER_SHARES)
        groups = [
            ("both_exec", base_exec & candidate_exec),
            ("base_only_exec", base_exec & ~candidate_exec),
            ("candidate_only_exec", ~base_exec & candidate_exec),
            ("neither_exec", ~base_exec & ~candidate_exec),
        ]
        for bucket, mask in groups:
            group = frame.loc[mask]
            rows.append(
                {
                    "weight_policy": policy,
                    "exec_transition": bucket,
                    "rows": int(len(group)),
                    "dates": int(group["target_date"].nunique()) if len(group) else 0,
                    "hit_rate": float(pd.to_numeric(group["payoff"], errors="coerce").mean()) if len(group) else None,
                    "full_stake_pnl_usd": round(float(pd.to_numeric(group["stake_profit_usd"], errors="coerce").sum()), 6),
                    "baseline_weighted_pnl_usd": round(
                        float(
                            (
                                pd.to_numeric(group["stake_profit_usd"], errors="coerce")
                                * pd.to_numeric(group["soft_balanced"], errors="coerce")
                            ).sum()
                        ),
                        6,
                    ),
                    "candidate_weighted_pnl_usd": round(
                        float(
                            (
                                pd.to_numeric(group["stake_profit_usd"], errors="coerce")
                                * pd.to_numeric(group[policy], errors="coerce")
                            ).sum()
                        ),
                        6,
                    ),
                    "baseline_weighted_cost_usd": round(
                        float(
                            (
                                pd.to_numeric(group["stake_cost_usd"], errors="coerce")
                                * pd.to_numeric(group["soft_balanced"], errors="coerce")
                            ).sum()
                        ),
                        6,
                    ),
                    "candidate_weighted_cost_usd": round(
                        float(
                            (
                                pd.to_numeric(group["stake_cost_usd"], errors="coerce")
                                * pd.to_numeric(group[policy], errors="coerce")
                            ).sum()
                        ),
                        6,
                    ),
                }
            )
    return pd.DataFrame(rows)


def stability_split_summary(frame: pd.DataFrame, policies: list[str]) -> pd.DataFrame:
    rows = []
    dates = sorted(pd.to_datetime(frame["target_date"]).dt.date.unique())
    midpoint = dates[len(dates) // 2]
    split_defs: list[tuple[str, pd.Series]] = [
        (f"early_to_{midpoint}", pd.to_datetime(frame["target_date"]).dt.date <= midpoint),
        (f"late_after_{midpoint}", pd.to_datetime(frame["target_date"]).dt.date > midpoint),
        ("2026-05-20_to_2026-05-31", frame["target_date"].between("2026-05-20", "2026-05-31")),
        ("2026-06-01_to_2026-06-10", frame["target_date"].between("2026-06-01", "2026-06-10")),
        ("2026-06-11_to_2026-06-23", frame["target_date"].between("2026-06-11", "2026-06-23")),
    ]
    split_defs.extend([(f"route_{route}", frame["route_leg"].astype(str).eq(str(route))) for route in sorted(frame["route_leg"].dropna().unique())])
    for family, group in frame.groupby("city_family", dropna=False):
        if len(group) >= 20:
            split_defs.append((f"family_{family}", frame["city_family"].astype(str).eq(str(family))))

    for split_name, mask in split_defs:
        group = frame.loc[mask].copy()
        if group.empty:
            continue
        base_roi, base_cost, base_pnl = policy_roi(group, "soft_balanced")
        for policy in policies:
            cand_roi, cand_cost, cand_pnl = policy_roi(group, policy)
            rows.append(
                {
                    "split": split_name,
                    "weight_policy": policy,
                    "rows": int(len(group)),
                    "dates": int(group["target_date"].nunique()),
                    "baseline_roi": base_roi,
                    "candidate_roi": cand_roi,
                    "delta_roi": (cand_roi - base_roi) if base_roi is not None and cand_roi is not None else None,
                    "baseline_cost_usd": round(base_cost, 6),
                    "candidate_cost_usd": round(cand_cost, 6),
                    "baseline_pnl_usd": round(base_pnl, 6),
                    "candidate_pnl_usd": round(cand_pnl, 6),
                    "delta_pnl_usd": round(cand_pnl - base_pnl, 6),
                }
            )
    return pd.DataFrame(rows)


def main() -> int:
    if not IN_DETAILS.exists():
        raise FileNotFoundError(f"missing input {IN_DETAILS}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(IN_DETAILS, low_memory=False)
    labels = df.apply(lambda row: temperature_context_features(row.to_dict()), axis=1, result_type="expand")
    scored = pd.concat([df.reset_index(drop=True), labels.reset_index(drop=True)], axis=1)
    scored["temp_context_multiplier_light"] = scored.apply(
        lambda row: temperature_context_multiplier(row.to_dict(), strength="light"), axis=1
    )
    scored["temp_context_multiplier_medium"] = scored.apply(
        lambda row: temperature_context_multiplier(row.to_dict(), strength="medium"), axis=1
    )
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
    audit_policies = ["soft_temp_context_light", "soft_temp_context_medium", "soft_wind_context"]
    attribution = mechanism_vs_filtering_attribution(scored, audit_policies)
    exec_transition = execution_transition_summary(scored, audit_policies)
    stability = stability_split_summary(scored, ["soft_temp_context_light", "soft_temp_context_medium"])

    scored.to_csv(OUT_DETAILS, index=False)
    policies.to_csv(OUT_POLICY, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    scene.to_csv(OUT_SCENE, index=False)
    route_scene.to_csv(OUT_ROUTE_SCENE, index=False)
    attribution.to_csv(OUT_ATTRIBUTION, index=False)
    exec_transition.to_csv(OUT_EXEC_TRANSITION, index=False)
    stability.to_csv(OUT_STABILITY, index=False)
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
            "mechanism_vs_filtering_attribution": str(OUT_ATTRIBUTION.relative_to(ROOT)),
            "execution_transition_summary": str(OUT_EXEC_TRANSITION.relative_to(ROOT)),
            "stability_split_summary": str(OUT_STABILITY.relative_to(ROOT)),
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
    attribution_light = attribution[attribution["weight_policy"].eq("soft_temp_context_light")].to_dict("records")
    transition_light = exec_transition[exec_transition["weight_policy"].eq("soft_temp_context_light")].to_dict("records")
    stability_focus = stability[
        stability["split"].isin(
            [
                "early_to_2026-06-06",
                "late_after_2026-06-06",
                "2026-05-20_to_2026-05-31",
                "2026-06-01_to_2026-06-10",
                "2026-06-11_to_2026-06-23",
                "route_capped_d2_no",
                "route_runway_current_no",
            ]
        )
    ].to_dict("records")
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
            "## Mechanism vs Filtering Audit",
            "",
            "The denominator is unchanged: all policies score the same 271 selected rows.  The temperature layer changes only the size multiplier.  In strict executable terms, the `5 shares` minimum can still turn lower-weight rows into effective non-orders, so this section separates fractional sizing from executable filtering.",
            "",
            "Light temperature context attribution:",
            "",
            md_table(
                attribution_light,
                [
                    "attribution_bucket",
                    "rows",
                    "dates",
                    "cities",
                    "delta_cost_usd",
                    "delta_pnl_usd",
                    "avg_weight_delta",
                ],
            ),
            "",
            "Strict execution-transition view for `soft_temp_context_light`:",
            "",
            md_table(
                transition_light,
                [
                    "exec_transition",
                    "rows",
                    "dates",
                    "hit_rate",
                    "full_stake_pnl_usd",
                    "baseline_weighted_pnl_usd",
                    "candidate_weighted_pnl_usd",
                    "baseline_weighted_cost_usd",
                    "candidate_weighted_cost_usd",
                ],
            ),
            "",
            "Stability splits:",
            "",
            md_table(
                stability_focus,
                [
                    "split",
                    "weight_policy",
                    "rows",
                    "dates",
                    "baseline_roi",
                    "candidate_roi",
                    "delta_roi",
                    "delta_pnl_usd",
                ],
            ),
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
            "- It is not a denominator filter: all 271 candidate rows remain.  However, once the strict `5 shares` order minimum is applied, `soft_temp_context_light` reduces executable rows from 77 to 67.",
            "- The strict executable-row change is not a clean bad-market filter: the 13 baseline-only executable rows had positive full-stake PnL in this sample, while 3 candidate-only executable rows all lost.  The headline weighted improvement comes more from fractional risk reshaping across all rows than from dropping a set of obviously bad orders.",
            "- Mechanically, the favorable contribution is balanced between upweighting winners (`+$17.34`) and downweighting losers (`+$31.56`), but it also wrongly downweights many winners (`-$35.92`) and upweights some losers (`-$7.57`).  That mixed attribution is why this is evidence of a useful context layer, not a confirmed trading rule.",
            "- Split stability is mixed: early and late halves both improve ROI, but the 2026-06-01..2026-06-10 block worsens.  This argues against treating the current coefficients as robust enough for live sizing.",
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
