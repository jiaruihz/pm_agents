#!/usr/bin/env python3
"""Bad-week split sensitivity for regime-routed expression router v3.

This report checks whether the weak 2026-06-21+ window is a single split artifact
or a broader non-stationarity/sample-fragility signal under live-like entry.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DETAILS = ROOT / "docs/analysis/2026-06/generated/regime_routed_expression_router_v3_live_like_entry_v1/trade_details.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_v3_bad_week_split_sensitivity_20260629"
OUT_SUMMARY = OUT_DIR / "summary.json"
OUT_SPLITS = OUT_DIR / "split_window_summary.csv"
OUT_ROLLING = OUT_DIR / "rolling_block_summary.csv"
OUT_ROUTE = OUT_DIR / "forward_route_summary.csv"
OUT_DAILY = OUT_DIR / "forward_daily_summary.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-29-regime-routed-v3-bad-week-split-sensitivity.md"


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


def load_details() -> pd.DataFrame:
    df = pd.read_csv(DETAILS, low_memory=False)
    df["target_date"] = df["target_date"].astype(str)
    return df[df["router_payoff"].notna()].copy()


def summarize(group: pd.DataFrame, **labels: Any) -> dict[str, Any]:
    cost = float(group["router_cost_usd"].sum())
    pnl = float(group["router_pnl_usd"].sum())
    wcost = float(group["router_weighted_cost_usd"].sum())
    wpnl = float(group["router_weighted_pnl_usd"].sum())
    out = {
        **labels,
        "rows": int(len(group)),
        "dates": int(group["target_date"].nunique()) if len(group) else 0,
        "cities": int(group["city"].nunique()) if len(group) else 0,
        "wins": int(pd.to_numeric(group["router_payoff"], errors="coerce").sum()) if len(group) else 0,
        "win_rate": float(pd.to_numeric(group["router_payoff"], errors="coerce").mean()) if len(group) else None,
        "avg_ask": float(pd.to_numeric(group["router_ask"], errors="coerce").mean()) if len(group) else None,
        "cost_usd": cost,
        "pnl_usd": pnl,
        "roi": pnl / cost if cost else None,
        "weighted_cost_usd": wcost,
        "weighted_pnl_usd": wpnl,
        "weighted_roi": wpnl / wcost if wcost else None,
    }
    return out


def split_window_summary(df: pd.DataFrame) -> pd.DataFrame:
    starts = ["2026-06-19", "2026-06-21", "2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26"]
    rows: list[dict[str, Any]] = []
    for policy, policy_df in df.groupby("entry_policy", dropna=False):
        for start in starts:
            forward = policy_df[policy_df["target_date"].ge(start)]
            train = policy_df[policy_df["target_date"].lt(start)]
            rows.append(summarize(train, entry_policy=policy, split_start=start, window=f"train_before_{start}"))
            rows.append(summarize(forward, entry_policy=policy, split_start=start, window=f"forward_{start}_plus"))
    return pd.DataFrame(rows)


def rolling_block_summary(df: pd.DataFrame) -> pd.DataFrame:
    daily = (
        df.groupby(["entry_policy", "target_date"], as_index=False)
        .agg(
            rows=("city", "size"),
            wins=("router_payoff", "sum"),
            cost_usd=("router_cost_usd", "sum"),
            pnl_usd=("router_pnl_usd", "sum"),
            weighted_cost_usd=("router_weighted_cost_usd", "sum"),
            weighted_pnl_usd=("router_weighted_pnl_usd", "sum"),
        )
        .sort_values(["entry_policy", "target_date"])
    )
    out: list[dict[str, Any]] = []
    for policy, group in daily.groupby("entry_policy", dropna=False):
        group = group.reset_index(drop=True)
        for block_days in [3, 5, 6, 8]:
            for i in range(0, len(group) - block_days + 1):
                block = group.iloc[i : i + block_days]
                cost = float(block["cost_usd"].sum())
                pnl = float(block["pnl_usd"].sum())
                wcost = float(block["weighted_cost_usd"].sum())
                wpnl = float(block["weighted_pnl_usd"].sum())
                out.append(
                    {
                        "entry_policy": policy,
                        "block_days": block_days,
                        "start": block["target_date"].iloc[0],
                        "end": block["target_date"].iloc[-1],
                        "rows": int(block["rows"].sum()),
                        "wins": int(block["wins"].sum()),
                        "win_rate": float(block["wins"].sum() / block["rows"].sum()) if block["rows"].sum() else None,
                        "pnl_usd": pnl,
                        "roi": pnl / cost if cost else None,
                        "weighted_pnl_usd": wpnl,
                        "weighted_roi": wpnl / wcost if wcost else None,
                    }
                )
    return pd.DataFrame(out).sort_values(["entry_policy", "block_days", "roi"]).reset_index(drop=True)


def forward_daily_summary(df: pd.DataFrame) -> pd.DataFrame:
    forward = df[df["target_date"].ge("2026-06-21")].copy()
    rows: list[dict[str, Any]] = []
    for (policy, date), group in forward.groupby(["entry_policy", "target_date"], dropna=False):
        row = summarize(group, entry_policy=policy, target_date=date)
        row["route_mix"] = ",".join(f"{k}:{v}" for k, v in group["router_route"].value_counts().sort_index().items())
        row["loss_cities"] = ",".join(
            group.loc[pd.to_numeric(group["router_payoff"], errors="coerce").eq(0), "city"]
            .astype(str)
            .sort_values()
            .tolist()
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["entry_policy", "target_date"]).reset_index(drop=True)


def forward_route_summary(df: pd.DataFrame) -> pd.DataFrame:
    forward = df[df["target_date"].ge("2026-06-21")].copy()
    rows: list[dict[str, Any]] = []
    for (policy, route), group in forward.groupby(["entry_policy", "router_route"], dropna=False):
        rows.append(summarize(group, entry_policy=policy, router_route=route))
    return pd.DataFrame(rows).sort_values(["entry_policy", "rows"], ascending=[True, False]).reset_index(drop=True)


def render_md(payload: dict[str, Any], splits: pd.DataFrame, rolling: pd.DataFrame, route: pd.DataFrame, daily: pd.DataFrame) -> str:
    def table(df: pd.DataFrame, cols: list[str], limit: int | None = None) -> str:
        shown = df if limit is None else df.head(limit)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in shown.iterrows():
            vals = []
            for col in cols:
                val = row.get(col)
                if col.endswith("roi") or col.endswith("rate"):
                    vals.append(pct(val))
                elif col.endswith("usd"):
                    vals.append(money(val))
                elif isinstance(val, float):
                    vals.append(f"{val:.3f}" if math.isfinite(val) else "NA")
                else:
                    vals.append("" if pd.isna(val) else str(val))
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)

    selected_splits = splits[splits["window"].str.startswith("forward_")].copy()
    worst_rolling = rolling.groupby(["entry_policy", "block_days"], as_index=False).head(5)
    return "\n".join(
        [
            "# Regime-Routed V3 Bad-Week Split Sensitivity",
            "",
            "## Conclusion",
            "",
            "Changing the forward split changes the headline, but it does not create a stable positive forward conclusion.  If 2026-06-21 and 2026-06-22 are absorbed into the known/training window, 2026-06-23+ is roughly flat to mildly positive; starting at 2026-06-24 or 2026-06-25 turns negative again.  That pattern is sample fragility and regime non-stationarity, not a robust repair.",
            "",
            f"Verdict: `{payload['verdict']['conclusion']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## Forward Split Summary",
            "",
            table(
                selected_splits,
                ["entry_policy", "split_start", "window", "rows", "dates", "win_rate", "avg_ask", "pnl_usd", "roi", "weighted_pnl_usd", "weighted_roi"],
            ),
            "",
            "## 2026-06-21+ Daily",
            "",
            table(
                daily,
                ["entry_policy", "target_date", "rows", "wins", "win_rate", "pnl_usd", "roi", "route_mix", "loss_cities"],
            ),
            "",
            "## Forward Route Summary",
            "",
            table(
                route,
                ["entry_policy", "router_route", "rows", "dates", "win_rate", "avg_ask", "pnl_usd", "roi", "weighted_roi"],
            ),
            "",
            "## Worst Rolling Blocks",
            "",
            table(
                worst_rolling,
                ["entry_policy", "block_days", "start", "end", "rows", "wins", "win_rate", "pnl_usd", "roi", "weighted_roi"],
                limit=40,
            ),
            "",
            "## Interpretation",
            "",
            "- `first_eligible` and `fixed_noon_priority` are both live-like entry policies; neither uses the day's eventual lowest ask.",
            "- 2026-06-21 and 2026-06-22 are bad, but the problem is not isolated to those two days: 2026-06-24+ and 2026-06-25+ remain negative.",
            "- Treating the split that looks best as proof would be overfit.  The useful evidence is the opposite: the result is sensitive to short date blocks, so promotion should remain blocked.",
        ]
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_details()
    splits = split_window_summary(df)
    rolling = rolling_block_summary(df)
    daily = forward_daily_summary(df)
    route = forward_route_summary(df)
    payload = {
        "generated_at_utc": now_utc(),
        "strategy": "regime_routed_v3_bad_week_split_sensitivity_20260629",
        "source": {
            "details": str(DETAILS.relative_to(ROOT)),
            "rows": int(len(df)),
            "date_min": str(df["target_date"].min()) if len(df) else None,
            "date_max": str(df["target_date"].max()) if len(df) else None,
            "policies": sorted(df["entry_policy"].astype(str).unique().tolist()),
        },
        "split_window_summary": finite(splits.to_dict(orient="records")),
        "forward_daily_summary": finite(daily.to_dict(orient="records")),
        "forward_route_summary": finite(route.to_dict(orient="records")),
        "worst_rolling_blocks": finite(rolling.groupby(["entry_policy", "block_days"], as_index=False).head(5).to_dict(orient="records")),
        "outputs": {
            "summary": str(OUT_SUMMARY.relative_to(ROOT)),
            "split_window_summary": str(OUT_SPLITS.relative_to(ROOT)),
            "rolling_block_summary": str(OUT_ROLLING.relative_to(ROOT)),
            "forward_route_summary": str(OUT_ROUTE.relative_to(ROOT)),
            "forward_daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "significance": "FAIL_OR_THIN_FORWARD_WINDOWS",
            "baseline": "NA_SPLIT_SENSITIVITY",
            "forward": "FAIL_SPLIT_SENSITIVE",
            "conclusion": "inconclusive_shadow_only",
            "live_ready": False,
        },
    }
    splits.to_csv(OUT_SPLITS, index=False)
    rolling.to_csv(OUT_ROLLING, index=False)
    route.to_csv(OUT_ROUTE, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    OUT_SUMMARY.write_text(json.dumps(finite(payload), indent=2, sort_keys=True) + "\n")
    OUT_MD.write_text(render_md(payload, splits, rolling, route, daily) + "\n")
    print(json.dumps(finite(payload), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
