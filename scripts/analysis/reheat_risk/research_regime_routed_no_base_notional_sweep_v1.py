#!/usr/bin/env python3
"""Base-notional sweep for regime-routed NO city-bias soft sizing."""

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

import research_regime_routed_no_original_city_bias_soft_v4b as city_bias  # noqa: E402
import research_regime_routed_no_tail_shadow_city_bias_v4 as v4  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-07/generated/regime_routed_no_base_notional_sweep_v1"
OUT_SUMMARY_JSON = OUT_DIR / "summary.json"
OUT_SWEEP = OUT_DIR / "base_notional_sweep.csv"
OUT_INCREMENTAL = OUT_DIR / "incremental_rows_vs_base5.csv"
OUT_REQUIRED = OUT_DIR / "required_base_notional_distribution.csv"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-01-regime-routed-no-base-notional-sweep-v1.md"

FORWARD_START = "2026-06-21"
MIN_ORDER_SHARES = 5.0
BASE_VALUES = [5.0, 6.0, 7.5, 8.0, 10.0, 12.0, 15.0]


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


def attach_sizing(frame: pd.DataFrame, *, base_n: float) -> pd.DataFrame:
    out = frame.copy()
    ask = pd.to_numeric(out["router_ask"], errors="coerce")
    weight = pd.to_numeric(out["city_bias_soft_weight"], errors="coerce")
    payoff = pd.to_numeric(out["router_payoff"], errors="coerce")
    out["sim_base_notional_usd"] = float(base_n)
    out["sim_cost_usd"] = float(base_n) * weight
    out["sim_shares"] = out["sim_cost_usd"] / ask
    out["sim_min5_executable"] = out["sim_shares"].ge(MIN_ORDER_SHARES) & payoff.notna()
    out["sim_soft_weight_to_ask_ratio"] = weight / ask
    out["sim_weight_price_quality_ok"] = out["sim_soft_weight_to_ask_ratio"].ge(1.0) & payoff.notna()
    out["sim_pnl_usd"] = np.where(payoff.eq(1.0), out["sim_cost_usd"] / ask - out["sim_cost_usd"], -out["sim_cost_usd"])
    return out


def apply_daily_cap(frame: pd.DataFrame, *, cap_usd: float | None, base_mask_col: str = "sim_min5_executable") -> pd.Series:
    base_mask = frame[base_mask_col].astype(bool)
    if cap_usd is None:
        return base_mask
    chosen: list[int] = []
    order_cols = ["target_date"]
    if "decision_snapshot_ts_utc" in frame.columns:
        order_cols.append("decision_snapshot_ts_utc")
    order_cols.append("city")
    eligible = frame[base_mask].sort_values(order_cols)
    for _, group in eligible.groupby("target_date", sort=True):
        spent = 0.0
        for idx, row in group.iterrows():
            cost = float(row.get("sim_cost_usd") or 0.0)
            if spent + cost <= cap_usd + 1e-9:
                chosen.append(idx)
                spent += cost
    selected = pd.Series(False, index=frame.index)
    selected.loc[chosen] = True
    return selected


def summarize(frame: pd.DataFrame, *, selected: pd.Series) -> dict[str, Any]:
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
            "avg_cost_usd": None,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "roi": None,
            "daily_negative_100pct": 0,
        }
    daily = active.groupby("target_date", as_index=False).agg(cost=("sim_cost_usd", "sum"), pnl=("sim_pnl_usd", "sum"))
    daily["roi"] = daily["pnl"] / daily["cost"]
    cost = float(active["sim_cost_usd"].sum())
    pnl = float(active["sim_pnl_usd"].sum())
    return {
        "rows": int(len(active)),
        "dates": int(active["target_date"].nunique()),
        "cities": int(active["city"].nunique()),
        "wins": int(pd.to_numeric(active["router_payoff"], errors="coerce").sum()),
        "win_rate": float(pd.to_numeric(active["router_payoff"], errors="coerce").mean()),
        "avg_ask": float(pd.to_numeric(active["router_ask"], errors="coerce").mean()),
        "avg_weight": float(pd.to_numeric(active["city_bias_soft_weight"], errors="coerce").mean()),
        "avg_cost_usd": float(active["sim_cost_usd"].mean()),
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "daily_negative_100pct": int(daily["roi"].le(-0.999).sum()),
    }


def route_mix(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""
    return ",".join(f"{k}:{v}" for k, v in frame["router_route"].value_counts().sort_index().items())


def build_outputs(details: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    sweep_rows: list[dict[str, Any]] = []
    incremental_rows: list[dict[str, Any]] = []
    required_rows: list[dict[str, Any]] = []

    for layer, base_frame in details.items():
        base_frame = base_frame[base_frame["router_payoff"].notna()].copy()
        required = (
            MIN_ORDER_SHARES
            * pd.to_numeric(base_frame["router_ask"], errors="coerce")
            / pd.to_numeric(base_frame["city_bias_soft_weight"], errors="coerce")
        ).replace([np.inf, -np.inf], np.nan)
        for q, value in required.dropna().quantile([0.25, 0.5, 0.75, 0.9, 0.95, 1.0]).items():
            required_rows.append({"evidence_layer": layer, "quantile": q, "required_base_notional_usd": float(value)})

        base5 = attach_sizing(base_frame, base_n=5.0)
        base5_executable = base5["sim_min5_executable"].astype(bool)

        for base_n in BASE_VALUES:
            sized = attach_sizing(base_frame, base_n=base_n)
            for cap_mode, cap in [
                ("no_daily_cap_signal_quality", None),
                ("daily_cap_equals_baseN_live_like", base_n),
                ("daily_cap_fixed5_diagnostic", 5.0),
                ("fixed_weight_price_quality_gate_daily_cap_equals_baseN", base_n),
            ]:
                base_mask_col = (
                    "sim_weight_price_quality_ok"
                    if cap_mode == "fixed_weight_price_quality_gate_daily_cap_equals_baseN"
                    else "sim_min5_executable"
                )
                selected = apply_daily_cap(sized, cap_usd=cap, base_mask_col=base_mask_col)
                for window, window_frame in [
                    ("all", sized),
                    (f"forward_{FORWARD_START}_plus", sized[sized["target_date"].astype(str).ge(FORWARD_START)]),
                ]:
                    window_selected = selected.reindex(window_frame.index).fillna(False)
                    row = summarize(window_frame, selected=window_selected)
                    row.update(
                        {
                            "evidence_layer": layer,
                            "window": window,
                            "cap_mode": cap_mode,
                            "base_notional_usd": base_n,
                            "daily_cap_usd": cap,
                        }
                    )
                    sweep_rows.append(row)

            if base_n == 5.0:
                continue
            incremental_mask = sized["sim_min5_executable"].astype(bool) & ~base5_executable
            inc = sized[incremental_mask].copy()
            for window, inc_window in [
                ("all", inc),
                (f"forward_{FORWARD_START}_plus", inc[inc["target_date"].astype(str).ge(FORWARD_START)]),
            ]:
                if inc_window.empty:
                    cost = pnl = 0.0
                    win_rate = roi = None
                else:
                    cost = float(inc_window["sim_cost_usd"].sum())
                    pnl = float(inc_window["sim_pnl_usd"].sum())
                    win_rate = float(pd.to_numeric(inc_window["router_payoff"], errors="coerce").mean())
                    roi = pnl / cost if cost else None
                incremental_rows.append(
                    {
                        "evidence_layer": layer,
                        "window": window,
                        "base_notional_usd": base_n,
                        "incremental_rows_vs_base5": int(len(inc_window)),
                        "dates": int(inc_window["target_date"].nunique()) if len(inc_window) else 0,
                        "cities": int(inc_window["city"].nunique()) if len(inc_window) else 0,
                        "win_rate": win_rate,
                        "cost_usd": cost,
                        "pnl_usd": pnl,
                        "roi": roi,
                        "route_mix": route_mix(inc_window),
                        "loss_cities": ",".join(
                            inc_window.loc[
                                pd.to_numeric(inc_window["router_payoff"], errors="coerce").eq(0), "city"
                            ]
                            .astype(str)
                            .sort_values()
                        ),
                    }
                )

    sweep = pd.DataFrame(sweep_rows)
    incremental = pd.DataFrame(incremental_rows)
    required_df = pd.DataFrame(required_rows)

    frozen_live_like = sweep[
        sweep["evidence_layer"].eq("frozen_live_like_route_price")
        & sweep["cap_mode"].eq("daily_cap_equals_baseN_live_like")
        & sweep["window"].eq("all")
    ].copy()
    fixed_quality = sweep[
        sweep["evidence_layer"].eq("frozen_live_like_route_price")
        & sweep["cap_mode"].eq("fixed_weight_price_quality_gate_daily_cap_equals_baseN")
        & sweep["window"].eq("all")
    ].copy()
    preferred = frozen_live_like[frozen_live_like["base_notional_usd"].isin([5.0, 6.0, 8.0, 10.0])].copy()
    verdict = {
        "conclusion": "decouple_signal_quality_from_base_notional_with_weight_to_ask_ratio_gate",
        "recommended_live_base_notional_usd": 5.0,
        "recommended_min_soft_weight_to_ask_ratio": 1.0,
        "base_notional_sizeup_policy": "allowed_only_after_explicit_notional_decision; do_not_change_signal_denominator",
        "basis": finite(preferred.to_dict(orient="records")),
        "fixed_quality_basis": finite(
            fixed_quality[fixed_quality["base_notional_usd"].isin([5.0, 8.0, 10.0, 15.0])].to_dict(orient="records")
        ),
    }
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy": "regime_routed_no_city_bias_soft_base_notional_sweep_v1",
        "coverage": {
            layer: {
                "rows": int(len(frame)),
                "min_date": str(frame["target_date"].min()) if len(frame) else None,
                "max_date": str(frame["target_date"].max()) if len(frame) else None,
            }
            for layer, frame in details.items()
        },
        "parameters": {
            "base_notional_values_usd": BASE_VALUES,
            "min_order_shares": MIN_ORDER_SHARES,
            "forward_start": FORWARD_START,
        },
        "outputs": {
            "summary_json": str(OUT_SUMMARY_JSON.relative_to(ROOT)),
            "sweep_csv": str(OUT_SWEEP.relative_to(ROOT)),
            "incremental_csv": str(OUT_INCREMENTAL.relative_to(ROOT)),
            "required_base_notional_distribution_csv": str(OUT_REQUIRED.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": verdict,
    }
    return sweep, incremental, required_df, payload


def table(df: pd.DataFrame, cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        vals = []
        for col in cols:
            val = row.get(col)
            if col in {"roi", "win_rate"}:
                vals.append(pct(val))
            elif col.endswith("usd"):
                vals.append(money(val))
            elif isinstance(val, float):
                vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
            else:
                vals.append("" if pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_md(payload: dict[str, Any], sweep: pd.DataFrame, incremental: pd.DataFrame, required_df: pd.DataFrame) -> str:
    frozen = sweep[sweep["evidence_layer"].eq("frozen_live_like_route_price")].copy()
    hist = sweep[sweep["evidence_layer"].eq("historical_best_ask_diagnostic")].copy()
    live_like_cols = [
        "base_notional_usd",
        "daily_cap_usd",
        "rows",
        "dates",
        "cities",
        "win_rate",
        "avg_weight",
        "avg_cost_usd",
        "cost_usd",
        "pnl_usd",
        "roi",
        "daily_negative_100pct",
    ]
    inc_cols = [
        "base_notional_usd",
        "window",
        "incremental_rows_vs_base5",
        "dates",
        "cities",
        "win_rate",
        "cost_usd",
        "pnl_usd",
        "roi",
        "route_mix",
        "loss_cities",
    ]
    req_cols = ["evidence_layer", "quantile", "required_base_notional_usd"]
    return "\n".join(
        [
            "# Regime-Routed NO Base Notional Sweep V1",
            "",
            "## Conclusion",
            "",
            "Increasing `base_N` should not change the strategy denominator. The correct fix is to keep a fixed signal-quality gate, `city_bias_soft_weight / ask >= 1.0`, and leave the 5-share rule as execution plumbing.",
            "",
            f"Verdict: `{payload['verdict']['conclusion']}`.",
            "",
            "Recommended live default remains `base_N=5`. If notional is raised later, keep the fixed weight/price quality gate so lower-confidence rows do not enter only because the order size grew.",
            "",
            "## Coverage",
            "",
            f"- Frozen/live-like replay: `{payload['coverage']['frozen_live_like_route_price']['min_date']}`..`{payload['coverage']['frozen_live_like_route_price']['max_date']}`, rows `{payload['coverage']['frozen_live_like_route_price']['rows']}`.",
            f"- Historical best-ask diagnostic: `{payload['coverage']['historical_best_ask_diagnostic']['min_date']}`..`{payload['coverage']['historical_best_ask_diagnostic']['max_date']}`, rows `{payload['coverage']['historical_best_ask_diagnostic']['rows']}`.",
            f"- Forward split: `>= {FORWARD_START}`.",
            "",
            "## Frozen Live-Like: Daily Cap Equals Base N",
            "",
            table(
                frozen[
                    frozen["cap_mode"].eq("daily_cap_equals_baseN_live_like") & frozen["window"].eq("all")
                ][live_like_cols],
                live_like_cols,
            ),
            "",
            "## Frozen Forward: Daily Cap Equals Base N",
            "",
            table(
                frozen[
                    frozen["cap_mode"].eq("daily_cap_equals_baseN_live_like")
                    & frozen["window"].eq(f"forward_{FORWARD_START}_plus")
                ][live_like_cols],
                live_like_cols,
            ),
            "",
            "## Frozen Live-Like With Fixed Weight/Price Quality Gate",
            "",
            table(
                frozen[
                    frozen["cap_mode"].eq("fixed_weight_price_quality_gate_daily_cap_equals_baseN")
                    & frozen["window"].eq("all")
                ][live_like_cols],
                live_like_cols,
            ),
            "",
            "## Frozen Forward With Fixed Weight/Price Quality Gate",
            "",
            table(
                frozen[
                    frozen["cap_mode"].eq("fixed_weight_price_quality_gate_daily_cap_equals_baseN")
                    & frozen["window"].eq(f"forward_{FORWARD_START}_plus")
                ][live_like_cols],
                live_like_cols,
            ),
            "",
            "## Incremental Rows Versus Base N = 5",
            "",
            table(
                incremental[
                    incremental["evidence_layer"].eq("frozen_live_like_route_price")
                    & incremental["window"].isin(["all", f"forward_{FORWARD_START}_plus"])
                ][inc_cols],
                inc_cols,
            ),
            "",
            "## Historical Diagnostic Cross-Check",
            "",
            table(
                hist[
                    hist["cap_mode"].eq("daily_cap_equals_baseN_live_like") & hist["window"].eq("all")
                ][live_like_cols],
                live_like_cols,
            ),
            "",
            "## Required Base N To Clear 5 Shares",
            "",
            table(required_df, req_cols),
            "",
            "## Interpretation",
            "",
            "- The old executable definition used `base_N * weight / ask >= 5`; raising `base_N` mechanically lowers the required weight/price quality.",
            "- The fixed quality gate uses `weight / ask >= 1.0`, which reproduces the current `base_N=5` quality threshold and remains stable under future notional changes.",
            "- With that gate, raising `base_N` scales dollars on the same quality set instead of admitting weak rows.",
            "- The 5-share rule should remain only as execution plumbing for exchange/order-size constraints and top-of-book depth.",
            "",
            "## Files",
            "",
            f"- Summary JSON: `{payload['outputs']['summary_json']}`",
            f"- Sweep CSV: `{payload['outputs']['sweep_csv']}`",
            f"- Incremental CSV: `{payload['outputs']['incremental_csv']}`",
            f"- Required base N distribution: `{payload['outputs']['required_base_notional_distribution_csv']}`",
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    details = load_details()
    sweep, incremental, required_df, payload = build_outputs(details)
    sweep.to_csv(OUT_SWEEP, index=False)
    incremental.to_csv(OUT_INCREMENTAL, index=False)
    required_df.to_csv(OUT_REQUIRED, index=False)
    OUT_SUMMARY_JSON.write_text(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    OUT_MD.write_text(render_md(payload, sweep, incremental, required_df) + "\n", encoding="utf-8")
    print(json.dumps(finite(payload), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
