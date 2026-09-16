#!/usr/bin/env python3
"""Replay original regime-routed NO with city/source bias as soft sizing only."""

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
for path in (ANALYSIS_DIR, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import research_regime_routed_no_tail_shadow_city_bias_v4 as v4  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/regime_routed_no_original_city_bias_soft_v4b"
OUT_SUMMARY_JSON = OUT_DIR / "summary.json"
OUT_POLICY = OUT_DIR / "policy_summary.csv"
OUT_ROUTE = OUT_DIR / "route_summary.csv"
OUT_DAILY = OUT_DIR / "daily_summary.csv"
OUT_CHANGED = OUT_DIR / "city_bias_changed_rows.csv"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-01-regime-routed-no-original-city-bias-soft-v4b.md"

STAKE_USD = v4.STAKE_USD
MIN_ORDER_SHARES = v4.MIN_ORDER_SHARES
FORWARD_START = v4.FORWARD_START


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


def add_policy_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["city_bias_soft_weight"] = (
        pd.to_numeric(out["base_weight"], errors="coerce").fillna(1.0)
        * pd.to_numeric(out["city_source_bias_multiplier_v1"], errors="coerce").fillna(1.0)
    ).clip(0.05, 1.0)

    out["original_cost_usd"] = STAKE_USD * pd.to_numeric(out["base_weight"], errors="coerce").fillna(0.0)
    out["original_pnl_usd"] = pd.to_numeric(out["router_pnl_usd"], errors="coerce").fillna(0.0) * pd.to_numeric(
        out["base_weight"], errors="coerce"
    ).fillna(0.0)
    out["original_shares"] = out["original_cost_usd"] / pd.to_numeric(out["router_ask"], errors="coerce")
    out["original_exec_cost_usd"] = out["original_cost_usd"].where(out["original_shares"].ge(MIN_ORDER_SHARES), 0.0)
    out["original_exec_pnl_usd"] = out["original_pnl_usd"].where(out["original_shares"].ge(MIN_ORDER_SHARES), 0.0)

    out["city_bias_soft_cost_usd"] = STAKE_USD * out["city_bias_soft_weight"]
    out["city_bias_soft_pnl_usd"] = pd.to_numeric(out["router_pnl_usd"], errors="coerce").fillna(0.0) * out[
        "city_bias_soft_weight"
    ]
    out["city_bias_soft_shares"] = out["city_bias_soft_cost_usd"] / pd.to_numeric(out["router_ask"], errors="coerce")
    out["city_bias_soft_exec_cost_usd"] = out["city_bias_soft_cost_usd"].where(
        out["city_bias_soft_shares"].ge(MIN_ORDER_SHARES), 0.0
    )
    out["city_bias_soft_exec_pnl_usd"] = out["city_bias_soft_pnl_usd"].where(
        out["city_bias_soft_shares"].ge(MIN_ORDER_SHARES), 0.0
    )

    out["city_bias_effect"] = np.select(
        [
            pd.to_numeric(out["city_source_bias_multiplier_v1"], errors="coerce").fillna(1.0).lt(1.0),
            pd.to_numeric(out["city_source_bias_multiplier_v1"], errors="coerce").fillna(1.0).gt(1.0),
        ],
        ["downweight", "upweight"],
        default="unchanged",
    )
    return out


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


def summarize_policy(frame: pd.DataFrame, *, policy: str, cost_col: str, pnl_col: str) -> dict[str, Any]:
    clean = frame[frame["router_payoff"].notna()].copy()
    active = clean[pd.to_numeric(clean[cost_col], errors="coerce").gt(0)].copy()
    cost = float(pd.to_numeric(active[cost_col], errors="coerce").sum())
    pnl = float(pd.to_numeric(active[pnl_col], errors="coerce").sum())
    ci_low, ci_high = date_bootstrap_roi(active, pnl_col=pnl_col, cost_col=cost_col)
    daily = active.groupby("target_date", as_index=False).agg(cost=(cost_col, "sum"), pnl=(pnl_col, "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"] if not daily.empty else np.nan
    return {
        "policy": policy,
        "rows_seen": int(len(clean)),
        "traded_rows": int(len(active)),
        "dates": int(active["target_date"].nunique()) if len(active) else 0,
        "cities": int(active["city"].nunique()) if len(active) else 0,
        "wins": int(pd.to_numeric(active["router_payoff"], errors="coerce").sum()) if len(active) else 0,
        "win_rate": float(pd.to_numeric(active["router_payoff"], errors="coerce").mean()) if len(active) else None,
        "avg_ask": float(pd.to_numeric(active["router_ask"], errors="coerce").mean()) if len(active) else None,
        "avg_weight": float(cost / (STAKE_USD * len(active))) if len(active) else None,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
        "daily_negative_100pct": int(daily["roi"].le(-0.999).sum()) if len(daily) else 0,
    }


def build_summaries(details: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    policy_rows: list[dict[str, Any]] = []
    route_rows: list[dict[str, Any]] = []
    daily_rows: list[dict[str, Any]] = []
    policy_specs = [
        ("original_weighted_no_min5", "original_cost_usd", "original_pnl_usd"),
        ("original_executable_min5", "original_exec_cost_usd", "original_exec_pnl_usd"),
        ("original_plus_city_bias_soft_no_min5", "city_bias_soft_cost_usd", "city_bias_soft_pnl_usd"),
        ("original_plus_city_bias_soft_executable_min5", "city_bias_soft_exec_cost_usd", "city_bias_soft_exec_pnl_usd"),
        ("v4_tail_shadow_city_bias_no_min5", "patched_cost_usd", "patched_pnl_usd"),
        ("v4_tail_shadow_city_bias_executable_min5", "patched_exec_cost_usd", "patched_exec_pnl_usd"),
    ]
    groups: list[tuple[str, str, pd.DataFrame]] = []
    for layer, layer_group in details.groupby("evidence_layer", dropna=False):
        groups.append((str(layer), "all", layer_group.copy()))
        for window, group in layer_group.groupby("window", dropna=False):
            groups.append((str(layer), str(window), group.copy()))

    for layer, window, group in groups:
        for label, cost_col, pnl_col in policy_specs:
            row = summarize_policy(group, policy=label, cost_col=cost_col, pnl_col=pnl_col)
            row.update({"evidence_layer": layer, "window": window})
            policy_rows.append(row)

        for route, rgroup in group.groupby("router_route", dropna=False):
            row = summarize_policy(
                rgroup,
                policy="original_plus_city_bias_soft_executable_min5",
                cost_col="city_bias_soft_exec_cost_usd",
                pnl_col="city_bias_soft_exec_pnl_usd",
            )
            row.update({"evidence_layer": layer, "window": window, "router_route": route})
            route_rows.append(row)

        active = group[pd.to_numeric(group["city_bias_soft_exec_cost_usd"], errors="coerce").gt(0)].copy()
        for target_date, dgroup in active.groupby("target_date", sort=True):
            cost = float(dgroup["city_bias_soft_exec_cost_usd"].sum())
            pnl = float(dgroup["city_bias_soft_exec_pnl_usd"].sum())
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

    changed_cols = [
        "evidence_layer",
        "target_date",
        "city",
        "router_route",
        "router_ask",
        "router_payoff",
        "base_weight",
        "city_bias_soft_weight",
        "city_bias_soft_shares",
        "city_bias_effect",
        "forecast_source",
        "row_forecast_model",
        "city_source_bias_regime",
        "city_source_bias",
        "city_source_hot_underforecast_rate",
    ]
    changed = details[details["city_bias_effect"].ne("unchanged")][changed_cols].copy()
    return pd.DataFrame(policy_rows), pd.DataFrame(route_rows), pd.DataFrame(daily_rows), changed


def render_md(payload: dict[str, Any], policy: pd.DataFrame, route: pd.DataFrame, daily: pd.DataFrame, changed: pd.DataFrame) -> str:
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

    frozen = policy[policy["evidence_layer"].eq("frozen_live_like_route_price")].copy()
    historical = policy[policy["evidence_layer"].eq("historical_best_ask_diagnostic")].copy()
    frozen_forward_daily = daily[
        daily["evidence_layer"].eq("frozen_live_like_route_price") & daily["window"].eq(f"forward_{FORWARD_START}_plus")
    ].copy()
    cols = [
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
    ]
    return "\n".join(
        [
            "# Regime-Routed NO Original + City Bias Soft V4B Replay",
            "",
            "## Conclusion",
            "",
            (
                "This replay keeps the original regime-routed candidate set eligible and applies city/source forecast-bias "
                "only as a soft sizing multiplier. It compares that against the stricter V4 tail-shadow policy on the same denominator."
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
            "- `historical_best_ask_diagnostic` is not live-causal; use it only as a broad diagnostic.",
            "- `frozen_live_like_route_price` is the relevant replay for the current runner shape.",
            "",
            "## Frozen/Live-Like Same-Denominator Result",
            "",
            table(frozen.sort_values(["window", "policy"]), cols),
            "",
            "## Historical Best-Ask Diagnostic",
            "",
            table(historical.sort_values(["window", "policy"]), cols),
            "",
            "## Frozen Forward Daily: Original + City Bias Soft Executable",
            "",
            table(frozen_forward_daily, ["target_date", "rows", "cities", "wins", "cost_usd", "pnl_usd", "roi", "route_mix", "loss_cities"]),
            "",
            "## Route Contribution: Original + City Bias Soft Executable",
            "",
            table(
                route[route["evidence_layer"].eq("frozen_live_like_route_price")].sort_values(["window", "router_route"]),
                ["window", "router_route", "traded_rows", "dates", "cities", "win_rate", "pnl_usd", "roi"],
                limit=40,
            ),
            "",
            "## City Bias Changed Rows",
            "",
            table(
                changed[changed["evidence_layer"].eq("frozen_live_like_route_price")].sort_values(["target_date", "city"]).head(40),
                [
                    "target_date",
                    "city",
                    "router_route",
                    "router_ask",
                    "router_payoff",
                    "base_weight",
                    "city_bias_soft_weight",
                    "city_bias_soft_shares",
                    "city_bias_effect",
                    "city_source_bias_regime",
                ],
            ),
            "",
            "## Interpretation",
            "",
            "- Keeping the original route set and applying city/source bias as soft sizing preserves almost all signal count.",
            "- The strict V4 patch is cleaner for mechanism accounting, but it is too conservative as the main execution expression because it thins forward rows.",
            "- City/source bias is a reasonable overlay because it is based on historical forecast-vs-station behavior and changes size rather than rewriting the trade label.",
            "- This is still not a live-confirmed edge: the forward executable sample remains very small, so it should replace V4 only as the preferred shadow/main-candidate replay, not as an unqualified size-up approval.",
            "",
            "## Files",
            "",
            f"- Summary JSON: `{payload['outputs']['summary_json']}`",
            f"- Policy summary: `{payload['outputs']['policy_summary']}`",
            f"- Route summary: `{payload['outputs']['route_summary']}`",
            f"- Daily summary: `{payload['outputs']['daily_summary']}`",
            f"- Changed rows: `{payload['outputs']['changed_rows']}`",
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    historical = add_policy_columns(v4.load_historical_best_ask())
    frozen = add_policy_columns(v4.load_frozen_live_like())
    details = pd.concat([historical, frozen], ignore_index=True)
    policy, route, daily, changed = build_summaries(details)
    payload = {
        "generated_at_utc": now_utc(),
        "strategy": "regime_routed_no_original_city_bias_soft_v4b",
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
        "outputs": {
            "summary_json": str(OUT_SUMMARY_JSON.relative_to(ROOT)),
            "policy_summary": str(OUT_POLICY.relative_to(ROOT)),
            "route_summary": str(OUT_ROUTE.relative_to(ROOT)),
            "daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "changed_rows": str(OUT_CHANGED.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "significance": "MIXED_FORWARD_THIN",
            "baseline": "original_regime_routed_same_denominator",
            "forward": "BETTER_THAN_V4_BUT_TOO_THIN",
            "conclusion": "prefer_original_plus_city_bias_soft_over_tail_shadow_v4_for_shadow_candidate",
            "live_ready": False,
        },
    }
    policy.to_csv(OUT_POLICY, index=False)
    route.to_csv(OUT_ROUTE, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    changed.to_csv(OUT_CHANGED, index=False)
    OUT_SUMMARY_JSON.write_text(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(render_md(payload, policy, route, daily, changed) + "\n", encoding="utf-8")
    print(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
