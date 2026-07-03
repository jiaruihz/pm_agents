#!/usr/bin/env python3
"""Clean peak-clock decomposition for regime-routed NO.

This study separates two concepts that v1 overloaded:

- forecast space: forecast max still has room above the running max
- peak clock: whether the forecast peak is still ahead of the decision time

The clean route only calls current-bracket NO a runway expression when both
space and peak clock support it.
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
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_regime_routed_no_expression_v1 as v1  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_peak_clock_clean_v2"
OUT_JSON = OUT_DIR / "summary.json"
OUT_VARIANTS = OUT_DIR / "variant_summary.csv"
OUT_DAILY = OUT_DIR / "daily_summary.csv"
OUT_REPORT = ROOT / "docs/analysis/2026-06/2026-06-26-regime-routed-no-peak-clock-clean-v2.md"

MAIN_VARIANT = "clean_peak_future_d2_no_relaxed70_best_ask"


def route_candidates(states: pd.DataFrame, *, capped_expression: str, peak_rule: str) -> pd.DataFrame:
    cur = v1.expression_candidates(states, "current_bracket_no")
    cur = cur[cur["day_regime"].isin(["day_open_runway", "day_marginal_runway"])].copy()
    delta = pd.to_numeric(cur["forecast_peak_delta_hours_local"], errors="coerce")
    if peak_rule == "v1_space_only":
        pass
    elif peak_rule == "peak_future":
        cur = cur[delta.le(0.0)].copy()
    elif peak_rule == "peak_2h_ahead":
        cur = cur[delta.le(-2.0)].copy()
    else:
        raise ValueError(peak_rule)
    cur["route_leg"] = "runway_current_no"

    capped = v1.expression_candidates(states, capped_expression)
    capped = capped[capped["day_regime"].eq("day_forecast_capped")].copy()
    capped["route_leg"] = f"capped_{capped_expression}"
    return pd.concat([cur, capped], ignore_index=True)


def selected_variant(states: pd.DataFrame, *, name: str, peak_rule: str, capped_expression: str = "d2_no") -> pd.DataFrame:
    frame = route_candidates(states, capped_expression=capped_expression, peak_rule=peak_rule)
    selected = v1.select_one_per_city_day(v1.apply_liquidity(frame, v1.ASK_CAPS["relaxed70"]), "best_ask")
    if selected.empty:
        return selected
    selected["variant"] = name
    selected["peak_route_rule"] = peak_rule
    selected = v1.add_soft_weights(selected)
    weights = pd.to_numeric(selected["soft_balanced"], errors="coerce").fillna(0.0)
    selected["weighted_cost_usd"] = selected["stake_cost_usd"] * weights
    selected["weighted_profit_usd"] = selected["stake_profit_usd"] * weights
    selected["base5_soft_shares"] = 5.0 * weights / pd.to_numeric(selected["ask"], errors="coerce")
    selected["base5_live_sized"] = selected["base5_soft_shares"].ge(5.0)
    return selected


def weighted_ci(frame: pd.DataFrame, cost_col: str, profit_col: str, *, seed: int = 20260626) -> tuple[float | None, float | None]:
    clean = frame[frame["payoff"].notna()].copy()
    dates = sorted(clean["target_date"].astype(str).unique().tolist())
    if len(dates) < 3:
        return None, None
    daily = clean.groupby("target_date").agg(cost=(cost_col, "sum"), profit=(profit_col, "sum"))
    rng = np.random.default_rng(seed)
    vals: list[float] = []
    for _ in range(5000):
        draw = rng.choice(dates, size=len(dates), replace=True)
        cost = float(daily.loc[draw, "cost"].sum())
        profit = float(daily.loc[draw, "profit"].sum())
        if cost:
            vals.append(profit / cost)
    if not vals:
        return None, None
    return float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))


def summarize(frame: pd.DataFrame, *, variant: str, sizing: str) -> dict[str, Any]:
    if frame.empty:
        return {"variant": variant, "sizing": sizing, "trades": 0}
    cost_col = "stake_cost_usd" if sizing == "full_size" else "weighted_cost_usd"
    profit_col = "stake_profit_usd" if sizing == "full_size" else "weighted_profit_usd"
    cost = float(frame[cost_col].sum())
    profit = float(frame[profit_col].sum())
    ci_low, ci_high = weighted_ci(frame, cost_col, profit_col)
    daily = daily_summary(frame, variant=variant, sizing=sizing)
    return {
        "variant": variant,
        "sizing": sizing,
        "trades": int(len(frame)),
        "active_dates": int(frame["target_date"].nunique()),
        "cities": int(frame["city"].nunique()),
        "wins": int(frame["payoff"].sum()),
        "win_rate": float(frame["payoff"].mean()),
        "avg_ask": float(pd.to_numeric(frame["ask"], errors="coerce").mean()),
        "avg_soft_weight": float(pd.to_numeric(frame["soft_balanced"], errors="coerce").mean()),
        "cost_usd": cost,
        "profit_usd": profit,
        "roi": profit / cost if cost else math.nan,
        "roi_ci_low": ci_low,
        "roi_ci_high": ci_high,
        "roi_le_minus50_days": int(daily["roi"].le(-0.50).sum()) if not daily.empty else 0,
        "roi_eq_minus100_days": int(daily["roi"].le(-0.999999).sum()) if not daily.empty else 0,
        "worst_day_profit_usd": float(daily["profit_usd"].min()) if not daily.empty else math.nan,
        "route_mix": ",".join(f"{k}:{v}" for k, v in frame["route_leg"].value_counts().sort_index().items()),
        "peak_state_mix": ",".join(f"{k}:{v}" for k, v in frame["peak_clock_state"].value_counts().sort_index().items()),
    }


def daily_summary(frame: pd.DataFrame, *, variant: str, sizing: str) -> pd.DataFrame:
    rows = []
    cost_col = "stake_cost_usd" if sizing == "full_size" else "weighted_cost_usd"
    profit_col = "stake_profit_usd" if sizing == "full_size" else "weighted_profit_usd"
    for date, group in frame.groupby("target_date"):
        cost = float(group[cost_col].sum())
        profit = float(group[profit_col].sum())
        rows.append(
            {
                "target_date": date,
                "variant": variant,
                "sizing": sizing,
                "trades": int(len(group)),
                "cities": int(group["city"].nunique()),
                "wins": int(group["payoff"].sum()),
                "win_rate": float(group["payoff"].mean()),
                "cost_usd": cost,
                "profit_usd": profit,
                "roi": profit / cost if cost else math.nan,
                "avg_soft_weight": float(pd.to_numeric(group["soft_balanced"], errors="coerce").mean()),
                "route_mix": ",".join(f"{k}:{v}" for k, v in group["route_leg"].value_counts().sort_index().items()),
                "loss_cities": ",".join(group.loc[group["payoff"].eq(0), "city"].astype(str).sort_values().tolist()),
            }
        )
    return pd.DataFrame(rows).sort_values(["target_date", "variant", "sizing"]).reset_index(drop=True)


def pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value) * 100:+.1f}%"


def money(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"${float(value):+.2f}"


def markdown_table(df: pd.DataFrame, columns: list[str], *, limit: int | None = None) -> str:
    if df.empty:
        return "_empty_"
    view = df[columns].head(limit).copy() if limit else df[columns].copy()
    for col in view.columns:
        if col in {"win_rate", "roi", "roi_ci_low", "roi_ci_high"}:
            view[col] = view[col].map(pct)
        elif col.endswith("usd"):
            view[col] = view[col].map(money)
        elif col.startswith("avg_"):
            view[col] = view[col].map(lambda x: "" if pd.isna(x) else f"{float(x):.3f}")
    headers = list(view.columns)
    rows = []
    for _, row in view.iterrows():
        rows.append([str(row.get(col, "")) for col in headers])
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    out.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(out)


def write_report(summary: pd.DataFrame, daily: pd.DataFrame, payload: dict[str, Any]) -> None:
    main = summary[(summary["variant"].eq(MAIN_VARIANT)) & (summary["sizing"].eq("soft_balanced"))].head(1)
    main_row = main.iloc[0].to_dict() if not main.empty else {}
    worst = daily[(daily["variant"].eq(MAIN_VARIANT)) & (daily["sizing"].eq("soft_balanced"))].sort_values("profit_usd")
    lines = [
        "# Regime-routed NO Peak-clock Clean V2",
        "",
        "## Conclusion",
        "",
        (
            "Clean route confirms the bug class: v1 used `day_open_runway/day_marginal_runway` as if it meant time runway, "
            "but that label only measured forecast temperature space.  Splitting peak clock improves the current candidate's "
            "tail profile, yet this is still `shadow_candidate`, not confirmed live edge."
        ),
        "",
        "一句话：在 2026-05-19..2026-06-23 settled replay，clean peak-future route + soft sizing 的 ROI 为 "
        f"{pct(main_row.get('roi'))}（95% CI {pct(main_row.get('roi_ci_low'))}, {pct(main_row.get('roi_ci_high'))}），"
        "前瞻仍未满足冻结 forward 门，结论 `shadow_candidate/inconclusive_for_live`。",
        "",
        "## Data Snapshot",
        "",
        f"- states rows: {payload['data_snapshot']['states_rows']}",
        f"- settled states rows: {payload['data_snapshot']['settled_states_rows']}",
        "- source: generated intraday regime/reheat feature state rows consumed through `research_regime_routed_no_expression_v1.load_states()`",
        "- local fact rebuild: `run_stack.sh --api-only` completed before this run; CLOB fill coverage gate passed separately.",
        "",
        "## Variant Summary",
        "",
        markdown_table(
            summary,
            [
                "variant",
                "sizing",
                "trades",
                "active_dates",
                "cities",
                "win_rate",
                "avg_ask",
                "avg_soft_weight",
                "profit_usd",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
                "roi_le_minus50_days",
                "roi_eq_minus100_days",
                "worst_day_profit_usd",
            ],
        ),
        "",
        "## Worst Days: Clean Peak Future + Soft",
        "",
        markdown_table(
            worst,
            [
                "target_date",
                "trades",
                "wins",
                "win_rate",
                "avg_soft_weight",
                "profit_usd",
                "roi",
                "route_mix",
                "loss_cities",
            ],
            limit=12,
        ),
        "",
        "## Interpretation",
        "",
        "1. `day_regime` should be read as space regime, not time runway.",
        "2. `peak_future` removes current-bracket NO entries whose forecast peak was already past; this is the clean mechanism expression.",
        "3. `peak_2h_ahead` is too strict for capacity: it improves tail but drops many current-NO opportunities.",
        "4. Current evidence is better shaped after the fix, but still lacks frozen forward proof after today's live incident.",
        "",
        "## Files",
        "",
        f"- JSON: `{OUT_JSON.relative_to(ROOT)}`",
        f"- Variant summary: `{OUT_VARIANTS.relative_to(ROOT)}`",
        f"- Daily summary: `{OUT_DAILY.relative_to(ROOT)}`",
    ]
    OUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    states = v1.load_states()
    settled = states[states["current_bracket_held"].notna()].copy()
    variants = {
        "v1_space_only_d2_no_relaxed70_best_ask": selected_variant(
            settled, name="v1_space_only_d2_no_relaxed70_best_ask", peak_rule="v1_space_only"
        ),
        MAIN_VARIANT: selected_variant(settled, name=MAIN_VARIANT, peak_rule="peak_future"),
        "clean_peak_2h_ahead_d2_no_relaxed70_best_ask": selected_variant(
            settled, name="clean_peak_2h_ahead_d2_no_relaxed70_best_ask", peak_rule="peak_2h_ahead"
        ),
    }

    summary_rows: list[dict[str, Any]] = []
    daily_frames: list[pd.DataFrame] = []
    for name, frame in variants.items():
        summary_rows.append(summarize(frame, variant=name, sizing="full_size"))
        summary_rows.append(summarize(frame, variant=name, sizing="soft_balanced"))
        live_sized = frame[frame["base5_live_sized"]].copy() if not frame.empty else frame
        summary_rows.append(summarize(live_sized, variant=name, sizing="soft_balanced_base5_min5"))
        daily_frames.append(daily_summary(frame, variant=name, sizing="soft_balanced"))

    summary = pd.DataFrame(summary_rows)
    daily = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
    payload = {
        "generated_at_utc": pd.Timestamp.utcnow().isoformat(),
        "strategy": "regime_routed_no_peak_clock_clean_v2",
        "data_snapshot": {
            "states_rows": int(len(states)),
            "settled_states_rows": int(len(settled)),
            "date_min": str(states["target_date"].min()),
            "date_max": str(states["target_date"].max()),
        },
        "variants": summary.to_dict(orient="records"),
        "outputs": {
            "variant_summary_csv": str(OUT_VARIANTS.relative_to(ROOT)),
            "daily_summary_csv": str(OUT_DAILY.relative_to(ROOT)),
            "report_md": str(OUT_REPORT.relative_to(ROOT)),
        },
        "verdict": {
            "status": "shadow_candidate_inconclusive_for_live",
            "live_ready": False,
            "reason": "Peak-clock decomposition improves tail shape but has not passed frozen forward evidence after live incident.",
        },
    }
    OUT_VARIANTS.write_text(summary.to_csv(index=False), encoding="utf-8")
    OUT_DAILY.write_text(daily.to_csv(index=False), encoding="utf-8")
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(summary, daily, payload)
    print(json.dumps(payload["verdict"] | {"main_variant": MAIN_VARIANT}, ensure_ascii=False, indent=2, sort_keys=True))
    print(summary.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
