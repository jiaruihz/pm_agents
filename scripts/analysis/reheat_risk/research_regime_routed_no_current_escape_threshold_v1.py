#!/usr/bin/env python3
"""A/B replay for current-bracket NO settlement escape threshold.

The strategy wants current-bracket NO only when the forecast peak can clear the
current integer settlement bucket.  For a 90-91F bracket that means forecast
max must be strictly above 91.5F, not merely above 91F.
"""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
ANALYSIS_DIR = ROOT / "scripts/analysis/reheat_risk"
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))

import research_regime_routed_no_expression_v1 as base  # noqa: E402


SELECTED = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_expression_v1/selected_trade_details.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/regime_routed_no_current_escape_threshold_v1"
OUT_JSON = OUT_DIR / "summary.json"
OUT_POLICY = OUT_DIR / "policy_summary.csv"
OUT_ROUTE = OUT_DIR / "route_summary.csv"
OUT_DAILY = OUT_DIR / "daily_summary.csv"
OUT_BLOCKED = OUT_DIR / "current_no_blocked_by_escape_threshold.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-27-regime-routed-no-current-escape-threshold-v1.md"

MAIN_VARIANT = "routed_capped_d2_no_relaxed70_best_ask"
BASE_NOTIONAL_USD = 5.0
MIN_ORDER_SHARES = 5.0


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pct(value: Any) -> str:
    try:
        val = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(val):
        return "NA"
    return f"{100.0 * val:+.1f}%"


def parse_bracket(value: Any) -> tuple[float | None, float | None]:
    if pd.isna(value):
        return None, None
    text = str(value).replace("°", "").replace("F", "").replace("C", "").strip()
    text = text.rstrip("+")
    nums = re.findall(r"-?\d+(?:\.\d+)?", text)
    if not nums:
        return None, None
    low = float(nums[0])
    high = float(nums[-1]) if len(nums) > 1 else low
    if high < low:
        low, high = high, low
    return low, high


def current_no_escape_threshold(row: pd.Series) -> float:
    if str(row.get("route_leg") or "") != "runway_current_no":
        return math.nan
    raw = str(row.get("current_bracket") or "").strip()
    if raw.endswith("+"):
        return math.nan
    _low, high = parse_bracket(raw)
    if high is None:
        return math.nan
    return float(high) + 0.5


def add_escape_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = base.add_soft_weights(frame).copy()
    out["is_current_no_route"] = out["route_leg"].astype(str).eq("runway_current_no") | out["expression"].astype(str).eq(
        "current_bracket_no"
    )
    out["current_no_escape_threshold_native"] = out.apply(current_no_escape_threshold, axis=1)
    out["current_no_escape_margin_native"] = pd.to_numeric(out["forecast_max_native"], errors="coerce") - pd.to_numeric(
        out["current_no_escape_threshold_native"], errors="coerce"
    )
    out["current_no_escape_ok"] = (~out["is_current_no_route"]) | pd.to_numeric(
        out["current_no_escape_margin_native"], errors="coerce"
    ).gt(0.0)
    return out


def executed_mask(frame: pd.DataFrame, weight_col: str) -> pd.Series:
    ask = pd.to_numeric(frame["ask"], errors="coerce")
    weight = pd.to_numeric(frame[weight_col], errors="coerce").fillna(0.0).clip(lower=0.0)
    shares = BASE_NOTIONAL_USD * weight / ask
    return ask.between(base.ASK_MIN, base.ASK_CAPS["relaxed70"]) & shares.ge(MIN_ORDER_SHARES)


def summarize(frame: pd.DataFrame, *, policy: str, weight_col: str, require_escape: bool) -> dict[str, Any]:
    clean = frame[frame["payoff"].notna()].copy()
    if require_escape:
        clean = clean[clean["current_no_escape_ok"].astype(bool)].copy()
    if clean.empty:
        return {"policy": policy, "rows": 0, "exec_rows": 0}
    weight = pd.to_numeric(clean[weight_col], errors="coerce").fillna(0.0).clip(lower=0.0)
    exec_mask = executed_mask(clean, weight_col)
    cost = pd.to_numeric(clean["stake_cost_usd"], errors="coerce") * weight
    pnl = pd.to_numeric(clean["stake_profit_usd"], errors="coerce") * weight
    exec_cost = cost[exec_mask]
    exec_pnl = pnl[exec_mask]
    return {
        "policy": policy,
        "weight_col": weight_col,
        "requires_current_no_escape_gt_0": bool(require_escape),
        "rows": int(len(clean)),
        "dates": int(clean["target_date"].nunique()),
        "cities": int(clean["city"].nunique()),
        "wins": int(pd.to_numeric(clean["payoff"], errors="coerce").sum()),
        "win_rate": float(pd.to_numeric(clean["payoff"], errors="coerce").mean()),
        "cost_usd": round(float(cost.sum()), 6),
        "pnl_usd": round(float(pnl.sum()), 6),
        "roi": float(pnl.sum() / cost.sum()) if float(cost.sum()) else None,
        "exec_rows": int(exec_mask.sum()),
        "exec_dates": int(clean.loc[exec_mask, "target_date"].nunique()),
        "exec_cost_usd": round(float(exec_cost.sum()), 6),
        "exec_pnl_usd": round(float(exec_pnl.sum()), 6),
        "exec_roi": float(exec_pnl.sum() / exec_cost.sum()) if float(exec_cost.sum()) else None,
        "current_no_rows": int(clean["route_leg"].astype(str).eq("runway_current_no").sum()),
        "capped_d2_rows": int(clean["route_leg"].astype(str).eq("capped_d2_no").sum()),
        "avg_weight": float(weight.mean()),
    }


def group_summary(frame: pd.DataFrame, policy: str, weight_col: str, require_escape: bool, group_cols: list[str]) -> pd.DataFrame:
    work = frame[frame["payoff"].notna()].copy()
    if require_escape:
        work = work[work["current_no_escape_ok"].astype(bool)].copy()
    rows: list[dict[str, Any]] = []
    for keys, group in work.groupby(group_cols, dropna=False):
        row = summarize(group, policy=policy, weight_col=weight_col, require_escape=False)
        if not isinstance(keys, tuple):
            keys = (keys,)
        for col, val in zip(group_cols, keys, strict=False):
            row[col] = str(val)
        rows.append(row)
    return pd.DataFrame(rows)


def daily_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for policy, weight_col, require_escape in [
        ("soft_balanced_baseline", "soft_balanced", False),
        ("soft_balanced_escape_gt_0", "soft_balanced", True),
    ]:
        work = frame[frame["payoff"].notna()].copy()
        if require_escape:
            work = work[work["current_no_escape_ok"].astype(bool)].copy()
        for date, group in work.groupby("target_date"):
            row = summarize(group, policy=policy, weight_col=weight_col, require_escape=False)
            row["target_date"] = str(date)
            row["loss_cities"] = ",".join(group.loc[group["payoff"].eq(0), "city"].astype(str).sort_values().tolist())
            rows.append(row)
    return pd.DataFrame(rows).sort_values(["target_date", "policy"]).reset_index(drop=True)


def md_table(rows: list[dict[str, Any]], cols: list[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in rows:
        vals = []
        for col in cols:
            val = row.get(col)
            if isinstance(val, float):
                if col.endswith("roi") or col in {"win_rate", "avg_weight"}:
                    vals.append(pct(val))
                elif "usd" in col:
                    vals.append(f"{val:+.2f}")
                else:
                    vals.append(f"{val:.3f}")
            elif val is None:
                vals.append("NA")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(SELECTED, low_memory=False)
    selected = raw[raw["variant"].eq(MAIN_VARIANT)].copy()
    enriched = add_escape_features(selected)

    policies = [
        summarize(enriched, policy="full_size_baseline", weight_col="full_size", require_escape=False),
        summarize(enriched, policy="full_size_escape_gt_0", weight_col="full_size", require_escape=True),
        summarize(enriched, policy="soft_balanced_baseline", weight_col="soft_balanced", require_escape=False),
        summarize(enriched, policy="soft_balanced_escape_gt_0", weight_col="soft_balanced", require_escape=True),
    ]
    route = pd.concat(
        [
            group_summary(enriched, "soft_balanced_baseline", "soft_balanced", False, ["route_leg", "day_regime"]),
            group_summary(enriched, "soft_balanced_escape_gt_0", "soft_balanced", True, ["route_leg", "day_regime"]),
        ],
        ignore_index=True,
    )
    daily = daily_summary(enriched)
    blocked = enriched[enriched["is_current_no_route"].astype(bool) & ~enriched["current_no_escape_ok"].astype(bool)].copy()
    blocked_cols = [
        "target_date",
        "city",
        "decision_hour_local",
        "day_regime",
        "current_bracket",
        "forecast_max_native",
        "current_no_escape_threshold_native",
        "current_no_escape_margin_native",
        "ask",
        "payoff",
        "stake_profit_usd",
        "soft_balanced",
        "wind_regime",
        "moisture_cloud_regime",
        "intraday_state",
        "forecast_peak_hour_local",
        "forecast_peak_delta_hours_local",
    ]
    blocked = blocked[[col for col in blocked_cols if col in blocked.columns]].sort_values(["target_date", "city"])

    pd.DataFrame(policies).to_csv(OUT_POLICY, index=False)
    route.to_csv(OUT_ROUTE, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    blocked.to_csv(OUT_BLOCKED, index=False)

    before = policies[2]
    after = policies[3]
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "regime_routed_no_current_escape_threshold_v1",
        "input": str(SELECTED.relative_to(ROOT)),
        "variant": MAIN_VARIANT,
        "threshold_rule": "current-bracket NO requires forecast_max_native > bracket_high + 0.5; e.g. 90-91F requires >91.5F",
        "rows": int(len(enriched)),
        "date_min": str(enriched["target_date"].min()) if len(enriched) else None,
        "date_max": str(enriched["target_date"].max()) if len(enriched) else None,
        "current_no_rows": int(enriched["route_leg"].astype(str).eq("runway_current_no").sum()),
        "capped_d2_rows": int(enriched["route_leg"].astype(str).eq("capped_d2_no").sum()),
        "blocked_current_no_rows": int(len(blocked)),
        "blocked_current_no_wins": int(pd.to_numeric(blocked.get("payoff"), errors="coerce").sum()) if len(blocked) else 0,
        "soft_balanced_delta": {
            "rows": int(after["rows"] - before["rows"]),
            "exec_rows": int(after["exec_rows"] - before["exec_rows"]),
            "pnl_usd": round(float(after["pnl_usd"] - before["pnl_usd"]), 6),
            "roi": after["roi"],
            "baseline_roi": before["roi"],
        },
        "policy_summary": policies,
        "outputs": {
            "summary_json": str(OUT_JSON.relative_to(ROOT)),
            "policy_summary": str(OUT_POLICY.relative_to(ROOT)),
            "route_summary": str(OUT_ROUTE.relative_to(ROOT)),
            "daily_summary": str(OUT_DAILY.relative_to(ROOT)),
            "blocked_rows": str(OUT_BLOCKED.relative_to(ROOT)),
            "report_md": str(OUT_MD.relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    route_rows = (
        route[
            [
                "policy",
                "route_leg",
                "day_regime",
                "rows",
                "wins",
                "win_rate",
                "pnl_usd",
                "roi",
                "exec_rows",
                "exec_pnl_usd",
                "exec_roi",
            ]
        ]
        .sort_values(["route_leg", "day_regime", "policy"])
        .to_dict("records")
    )
    text = "\n".join(
        [
            "# Regime-Routed NO Current Escape Threshold V1",
            "",
            f"Generated: `{payload['generated_at_utc']}`",
            "",
            "## Verdict",
            "",
            "This is a same-denominator A/B replay.  It fixes the payoff boundary for `runway_current_no`: a current-bracket NO only has an upward escape thesis when the forecast max is strictly above `bracket_high + 0.5` in the market's native unit.  A `90-91F` NO therefore needs `forecast_max_f > 91.5F`; `91.2F` is still inside the settlement bucket and should be blocked.",
            "",
            "## Policy Summary",
            "",
            md_table(
                policies,
                [
                    "policy",
                    "rows",
                    "dates",
                    "cities",
                    "wins",
                    "win_rate",
                    "current_no_rows",
                    "capped_d2_rows",
                    "pnl_usd",
                    "roi",
                    "exec_rows",
                    "exec_pnl_usd",
                    "exec_roi",
                    "avg_weight",
                ],
            ),
            "",
            "## Route Summary",
            "",
            md_table(
                route_rows,
                [
                    "policy",
                    "route_leg",
                    "day_regime",
                    "rows",
                    "wins",
                    "win_rate",
                    "pnl_usd",
                    "roi",
                    "exec_rows",
                    "exec_pnl_usd",
                    "exec_roi",
                ],
            ),
            "",
            "## Boundary",
            "",
            "- This replay does not retune city pools, prices, regime labels, wind, or temperature-context weights.",
            "- The new rule only removes current-bracket NO rows whose forecast peak does not clear the settlement boundary.  It does not touch `capped_d2_no` rows.",
            f"- Blocked current-NO rows: `{payload['blocked_current_no_rows']}`; blocked wins: `{payload['blocked_current_no_wins']}`.",
            "",
        ]
    )
    OUT_MD.write_text(text, encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
