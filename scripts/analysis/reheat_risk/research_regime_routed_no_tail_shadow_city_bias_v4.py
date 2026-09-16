#!/usr/bin/env python3
"""Replay the runner patch: tail/fade shadow-only + capped d2 city/source bias sizing."""

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

import research_regime_routed_v3_route_specific_timing_wf_v1 as timing_wf  # noqa: E402
import regime_routed_no_tiny_live as live_runner  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/regime_routed_no_tail_shadow_city_bias_v4"
OUT_SUMMARY_JSON = OUT_DIR / "summary.json"
OUT_POLICY = OUT_DIR / "policy_summary.csv"
OUT_ROUTE = OUT_DIR / "route_summary.csv"
OUT_DAILY = OUT_DIR / "daily_summary.csv"
OUT_DIFF = OUT_DIR / "dropped_or_resized_rows.csv"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-01-regime-routed-no-tail-shadow-city-bias-v4.md"

BEST_ASK_DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_expression_router_v3/trade_details.csv"
FORWARD_START = "2026-06-21"
STAKE_USD = 5.0
MIN_ORDER_SHARES = 5.0
SHADOW_ONLY_ROUTES = {"false_fade_reheat_current_no", "cheap_stale_tail_current_no"}
FROZEN_CANDIDATE_ID = timing_wf.PROMISING_SHADOW_CANDIDATE


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def num(frame: pd.DataFrame, col: str, default: float = np.nan) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[col], errors="coerce")


def date_bootstrap_roi(
    frame: pd.DataFrame,
    *,
    pnl_col: str,
    cost_col: str,
    n: int = 5000,
    seed: int = 20260701,
) -> tuple[float | None, float | None]:
    clean = frame[[pnl_col, cost_col, "target_date"]].dropna().copy()
    clean = clean[pd.to_numeric(clean[cost_col], errors="coerce").gt(0)]
    if clean.empty or clean["target_date"].nunique() < 3:
        return None, None
    daily = clean.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    rng = np.random.default_rng(seed)
    idx = np.arange(len(daily))
    costs = daily["cost"].to_numpy(dtype=float)
    pnls = daily["pnl"].to_numpy(dtype=float)
    rois: list[float] = []
    for _ in range(n):
        sample = rng.choice(idx, size=len(idx), replace=True)
        cost = costs[sample].sum()
        if cost > 0:
            rois.append(float(pnls[sample].sum() / cost))
    if not rois:
        return None, None
    low, high = np.quantile(rois, [0.025, 0.975])
    return float(low), float(high)


def attach_bias(frame: pd.DataFrame) -> pd.DataFrame:
    lookup = live_runner.load_city_source_bias_lookup()
    out = frame.copy()
    models: list[str] = []
    regimes: list[str] = []
    multipliers: list[float] = []
    bias: list[float] = []
    hot_rate: list[float] = []
    cold_rate: list[float] = []
    for _, row in out.iterrows():
        model = live_runner.forecast_model_from_source(row.get("forecast_source"), row.get("forecast_clock_source"))
        info = lookup.get((str(row.get("city") or ""), model), {})
        regime = str(info.get("city_source_bias_regime") or "unclassified")
        is_capped = str(row.get("router_route") or "") == "capped_d2_no" or str(row.get("router_expression") or "") == "d2_no"
        mult = live_runner.city_source_bias_multiplier("higher_no_d2", regime) if is_capped else 1.0
        models.append(model)
        regimes.append(regime)
        multipliers.append(mult)
        bias.append(info.get("city_source_bias", np.nan))
        hot_rate.append(info.get("city_source_hot_underforecast_rate", np.nan))
        cold_rate.append(info.get("city_source_cold_overforecast_rate", np.nan))
    out["row_forecast_model"] = models
    out["city_source_bias_regime"] = regimes
    out["city_source_bias_multiplier_v1"] = multipliers
    out["city_source_bias"] = bias
    out["city_source_hot_underforecast_rate"] = hot_rate
    out["city_source_cold_overforecast_rate"] = cold_rate
    return out


def normalize_details(frame: pd.DataFrame, *, evidence_layer: str, base_weight_col: str) -> pd.DataFrame:
    out = frame.copy()
    out["target_date"] = out["target_date"].astype(str)
    if "router_route" not in out.columns and "route_leg" in out.columns:
        out["router_route"] = out["route_leg"].astype(str)
    out["router_ask"] = num(out, "router_ask", np.nan).fillna(num(out, "ask"))
    out["router_payoff"] = num(out, "router_payoff", np.nan).fillna(num(out, "payoff"))
    out["router_pnl_usd"] = num(out, "router_pnl_usd", np.nan)
    missing_pnl = out["router_pnl_usd"].isna()
    out.loc[missing_pnl, "router_pnl_usd"] = (
        out.loc[missing_pnl, "router_payoff"] * (STAKE_USD / out.loc[missing_pnl, "router_ask"]) - STAKE_USD
    )
    out["evidence_layer"] = evidence_layer
    out["base_weight"] = num(out, base_weight_col, 1.0).fillna(1.0).clip(lower=0)
    out = attach_bias(out)
    out["shadow_only_route"] = out["router_route"].astype(str).isin(SHADOW_ONLY_ROUTES)
    out["patched_weight"] = (
        out["base_weight"] * pd.to_numeric(out["city_source_bias_multiplier_v1"], errors="coerce").fillna(1.0)
    ).clip(0.05, 1.0)
    out.loc[out["shadow_only_route"], "patched_weight"] = 0.0
    out["base_cost_usd"] = STAKE_USD * out["base_weight"]
    out["base_pnl_usd"] = out["router_pnl_usd"] * out["base_weight"]
    out["patched_cost_usd"] = STAKE_USD * out["patched_weight"]
    out["patched_pnl_usd"] = out["router_pnl_usd"] * out["patched_weight"]
    out["patched_shares"] = out["patched_cost_usd"] / out["router_ask"]
    out["patched_executable"] = out["patched_shares"].ge(MIN_ORDER_SHARES)
    out["patched_exec_weight"] = out["patched_weight"].where(out["patched_executable"], 0.0)
    out["patched_exec_cost_usd"] = STAKE_USD * out["patched_exec_weight"]
    out["patched_exec_pnl_usd"] = out["router_pnl_usd"] * out["patched_exec_weight"]
    out["patch_effect"] = np.select(
        [
            out["shadow_only_route"],
            out["patched_weight"].gt(0) & ~out["patched_executable"],
            out["city_source_bias_multiplier_v1"].ne(1.0),
        ],
        ["shadow_only_tail_or_false_fade", "below_min5_after_sizing", "city_source_bias_resized"],
        default="unchanged",
    )
    out["window"] = np.where(out["target_date"].ge(FORWARD_START), f"forward_{FORWARD_START}_plus", "train_to_2026_06_20")
    return out


def load_historical_best_ask() -> pd.DataFrame:
    df = pd.read_csv(BEST_ASK_DETAILS, low_memory=False)
    df = timing_wf.add_row_sizing(df)
    return normalize_details(df, evidence_layer="historical_best_ask_diagnostic", base_weight_col="row_risk_soft_v1")


def load_frozen_live_like() -> pd.DataFrame:
    all_routed = timing_wf.build_all_routed_candidates()
    menu = timing_wf.build_candidate_menu(all_routed)
    frozen = menu[menu["candidate_id"].eq(FROZEN_CANDIDATE_ID)].copy()
    return normalize_details(frozen, evidence_layer="frozen_live_like_route_price", base_weight_col="candidate_weight")


def summarize_policy(frame: pd.DataFrame, *, policy_name: str, cost_col: str, pnl_col: str) -> dict[str, Any]:
    clean = frame[frame["router_payoff"].notna()].copy()
    cost = float(clean[cost_col].sum())
    pnl = float(clean[pnl_col].sum())
    ci_low, ci_high = date_bootstrap_roi(clean, pnl_col=pnl_col, cost_col=cost_col)
    active = clean[pd.to_numeric(clean[cost_col], errors="coerce").gt(0)].copy()
    daily = active.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"] if not daily.empty else np.nan
    return {
        "policy": policy_name,
        "rows_seen": int(len(clean)),
        "traded_rows": int(len(active)),
        "dates": int(active["target_date"].nunique()) if len(active) else 0,
        "cities": int(active["city"].nunique()) if len(active) else 0,
        "wins": int(pd.to_numeric(active["router_payoff"], errors="coerce").sum()) if len(active) else 0,
        "win_rate": float(pd.to_numeric(active["router_payoff"], errors="coerce").mean()) if len(active) else None,
        "avg_ask": float(pd.to_numeric(active["router_ask"], errors="coerce").mean()) if len(active) else None,
        "avg_weight": float(pd.to_numeric(active[cost_col], errors="coerce").sum() / (STAKE_USD * len(active))) if len(active) else None,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
        "daily_negative_100pct": int(daily["roi"].le(-0.999).sum()) if len(daily) else 0,
        "worst_day_pnl_usd": float(daily["pnl"].min()) if len(daily) else None,
        "best_day_pnl_usd": float(daily["pnl"].max()) if len(daily) else None,
    }


def build_summaries(details: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    policy_rows: list[dict[str, Any]] = []
    route_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    groups: list[tuple[str, str, pd.DataFrame]] = []
    for layer, layer_group in details.groupby("evidence_layer", dropna=False):
        groups.append((str(layer), "all", layer_group.copy()))
        for window, group in layer_group.groupby("window", dropna=False):
            groups.append((str(layer), str(window), group.copy()))
    for layer, window, group in groups:
        for label, cost_col, pnl_col in [
            ("pre_patch_weighted", "base_cost_usd", "base_pnl_usd"),
            ("v4_weighted_no_min5", "patched_cost_usd", "patched_pnl_usd"),
            ("v4_executable_min5", "patched_exec_cost_usd", "patched_exec_pnl_usd"),
        ]:
            row = summarize_policy(group, policy_name=label, cost_col=cost_col, pnl_col=pnl_col)
            row.update({"evidence_layer": layer, "window": window})
            policy_rows.append(row)
        for route, rgroup in group.groupby("router_route", dropna=False):
            row = summarize_policy(rgroup, policy_name="v4_executable_min5", cost_col="patched_exec_cost_usd", pnl_col="patched_exec_pnl_usd")
            row.update({"evidence_layer": layer, "window": window, "router_route": route})
            route_rows.append(row)
        active = group[pd.to_numeric(group["patched_exec_cost_usd"], errors="coerce").gt(0)].copy()
        for target_date, dgroup in active.groupby("target_date", sort=True):
            cost = float(dgroup["patched_exec_cost_usd"].sum())
            pnl = float(dgroup["patched_exec_pnl_usd"].sum())
            daily_rows.append(
                {
                    "evidence_layer": layer,
                    "window": window,
                    "target_date": target_date,
                    "rows": int(len(dgroup)),
                    "cities": int(dgroup["city"].nunique()),
                    "wins": int(pd.to_numeric(dgroup["router_payoff"], errors="coerce").sum()),
                    "cost_usd": cost,
                    "pnl_usd": pnl,
                    "roi": pnl / cost if cost else None,
                    "route_mix": ",".join(f"{k}:{v}" for k, v in dgroup["router_route"].value_counts().sort_index().items()),
                    "loss_cities": ",".join(
                        dgroup.loc[pd.to_numeric(dgroup["router_payoff"], errors="coerce").eq(0), "city"].astype(str).sort_values()
                    ),
                }
            )
    diff = details[details["patch_effect"].ne("unchanged")].copy()
    keep_cols = [
        "evidence_layer",
        "target_date",
        "city",
        "router_route",
        "router_expression",
        "router_ask",
        "router_payoff",
        "base_weight",
        "patched_weight",
        "patched_shares",
        "patch_effect",
        "forecast_source",
        "row_forecast_model",
        "city_source_bias_regime",
        "city_source_bias",
        "city_source_hot_underforecast_rate",
    ]
    return pd.DataFrame(policy_rows), pd.DataFrame(route_rows), pd.DataFrame(daily_rows), diff[keep_cols].copy()


def render_md(payload: dict[str, Any], policy: pd.DataFrame, route: pd.DataFrame, daily: pd.DataFrame, diff: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate") or col.endswith("ci_low") or col.endswith("ci_high"):
                    vals.append(pct(val))
                elif col.endswith("usd"):
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    frozen_policy = policy[policy["evidence_layer"].eq("frozen_live_like_route_price")].copy()
    historical_policy = policy[policy["evidence_layer"].eq("historical_best_ask_diagnostic")].copy()
    frozen_daily_forward = daily[
        daily["evidence_layer"].eq("frozen_live_like_route_price") & daily["window"].eq(f"forward_{FORWARD_START}_plus")
    ].copy()
    return "\n".join(
        [
            "# Regime-Routed NO Tail-Shadow + City Bias V4 Replay",
            "",
            "## Conclusion",
            "",
            (
                "This replay applies the live-runner patch: false-fade and cheap-stale current-NO are shadow-only; "
                "day_forecast_capped d2 NO keeps the same expression but receives city/source forecast-bias sizing."
            ),
            "",
            f"Verdict: `{payload['verdict']['conclusion']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## Data Snapshot",
            "",
            f"- Generated at: `{payload['generated_at_utc']}`",
            f"- Historical best-ask diagnostic: `{payload['coverage']['historical_min_date']}`..`{payload['coverage']['historical_max_date']}`",
            f"- Frozen/live-like replay: `{payload['coverage']['frozen_min_date']}`..`{payload['coverage']['frozen_max_date']}`",
            f"- Forward split: `>= {FORWARD_START}`",
            "- `historical_best_ask_diagnostic` uses the old best-ask selector and is not live-causal.",
            "- `frozen_live_like_route_price` is the relevant replay for the current runner shape.",
            "",
            "## Frozen/Live-Like Result",
            "",
            table(
                frozen_policy.sort_values(["window", "policy"]),
                [
                    "window",
                    "policy",
                    "rows_seen",
                    "traded_rows",
                    "dates",
                    "cities",
                    "win_rate",
                    "avg_ask",
                    "avg_weight",
                    "pnl_usd",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "daily_negative_100pct",
                ],
            ),
            "",
            "## Historical Best-Ask Diagnostic",
            "",
            table(
                historical_policy.sort_values(["window", "policy"]),
                [
                    "window",
                    "policy",
                    "rows_seen",
                    "traded_rows",
                    "dates",
                    "cities",
                    "win_rate",
                    "avg_ask",
                    "avg_weight",
                    "pnl_usd",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "daily_negative_100pct",
                ],
            ),
            "",
            "## Frozen Forward Daily",
            "",
            table(
                frozen_daily_forward,
                ["target_date", "rows", "cities", "wins", "cost_usd", "pnl_usd", "roi", "route_mix", "loss_cities"],
            ),
            "",
            "## V4 Route Contribution",
            "",
            table(
                route[route["policy"].eq("v4_executable_min5")].sort_values(["evidence_layer", "window", "router_route"]),
                ["evidence_layer", "window", "router_route", "traded_rows", "dates", "win_rate", "pnl_usd", "roi"],
                limit=40,
            ),
            "",
            "## Changed Rows",
            "",
            table(
                diff.sort_values(["evidence_layer", "target_date", "city"]).head(40),
                [
                    "evidence_layer",
                    "target_date",
                    "city",
                    "router_route",
                    "router_ask",
                    "router_payoff",
                    "base_weight",
                    "patched_weight",
                    "patched_shares",
                    "patch_effect",
                    "city_source_bias_regime",
                ],
            ),
            "",
            "## Interpretation",
            "",
            "- The patch is not a magic improvement on all historical point estimates. It deliberately removes two ambiguous current-NO mechanisms from live accounting.",
            "- The main benefit is risk cleanliness: frozen forward loses fewer dollars and has fewer active bad rows, but sample size also drops.",
            "- City/source bias mostly affects capped d2 NO; BuenosAires/ECMWF-style hot-underforecast rows become smaller or fall below min-share execution.",
            "",
            "## Files",
            "",
            f"- Summary JSON: `{payload['outputs']['summary_json']}`",
            f"- Policy summary: `{payload['outputs']['policy_summary']}`",
            f"- Daily summary: `{payload['outputs']['daily_summary']}`",
            f"- Changed rows: `{payload['outputs']['changed_rows']}`",
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    historical = load_historical_best_ask()
    frozen = load_frozen_live_like()
    details = pd.concat([historical, frozen], ignore_index=True)
    policy, route, daily, diff = build_summaries(details)
    payload = {
        "generated_at_utc": now_utc(),
        "strategy": "regime_routed_no_tail_shadow_city_bias_v4",
        "coverage": {
            "historical_rows": int(len(historical)),
            "historical_min_date": str(historical["target_date"].min()) if len(historical) else None,
            "historical_max_date": str(historical["target_date"].max()) if len(historical) else None,
            "frozen_rows": int(len(frozen)),
            "frozen_min_date": str(frozen["target_date"].min()) if len(frozen) else None,
            "frozen_max_date": str(frozen["target_date"].max()) if len(frozen) else None,
            "forward_start": FORWARD_START,
            "stake_usd": STAKE_USD,
            "min_order_shares": MIN_ORDER_SHARES,
        },
        "policy_summary": finite(policy.to_dict(orient="records")),
        "route_summary": finite(route.to_dict(orient="records")),
        "outputs": {
            "summary_json": str(OUT_SUMMARY_JSON.relative_to(ROOT)),
            "policy_summary": str(OUT_POLICY.relative_to(ROOT)),
            "route_summary": str(OUT_ROUTE.relative_to(ROOT)),
            "daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "changed_rows": str(OUT_DIFF.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "significance": "FAIL_OR_MIXED_CI",
            "baseline": "NA_SAME_DENOMINATOR_PATCH_REPLAY",
            "forward": "FAIL_THIN_AND_STILL_CROSSES_ZERO",
            "conclusion": "shadow_candidate_cleaner_expression_not_live_confirmed",
            "live_ready": False,
        },
    }
    policy.to_csv(OUT_POLICY, index=False)
    route.to_csv(OUT_ROUTE, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    diff.to_csv(OUT_DIFF, index=False)
    OUT_SUMMARY_JSON.write_text(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(render_md(payload, policy, route, daily, diff) + "\n", encoding="utf-8")
    print(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
