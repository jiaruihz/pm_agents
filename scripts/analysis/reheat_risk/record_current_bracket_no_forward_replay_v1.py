#!/usr/bin/env python3
"""Frozen forward replay ledger for current-bracket NO.

This is the scientific record layer for the current-bracket NO work:

- freeze a training cutoff;
- train the current mechanism models only on data at or before that cutoff;
- record every later base-p40 candidate with benchmark/champion/challenger
  soft-size multipliers;
- never use later settlement labels to change the frozen policy.

It is a PIT replay ledger, not a live order runner.
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
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import research_current_bracket_no_capped_day_regime_v1 as cap  # noqa: E402
import research_current_bracket_no_regime_aware_policy_v1 as regime  # noqa: E402
import research_current_bracket_no_remaining_heat_mechanism_features_v3 as v3  # noqa: E402


OUT_DIR = ROOT / "docs/analysis/2026-06/generated/current_bracket_no_forward_replay_v1"
OUT_JSON = OUT_DIR / "protocol_summary.json"
OUT_LEDGER = OUT_DIR / "frozen_forward_candidate_ledger.csv"
OUT_DAILY = OUT_DIR / "frozen_forward_daily_policy_summary.csv"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-24-current-bracket-no-forward-recording-protocol-v1.md"

FREEZE_DATE = "2026-06-24"
TRAINING_CUTOFF_DATE = "2026-06-22"
BASE_NOTIONAL_USD = 5.0
MIN_MULTIPLIER = 0.25

POLICIES = {
    "benchmark_full_size_base_p40": "full_multiplier",
    "champion_trade_cap_soft_size": "trade_cap_multiplier",
    "challenger_overconf_cap_soft_size": "overconf_combined_multiplier",
    "diagnostic_principled_cap_soft_size": "principled_combined_multiplier",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite_or_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: finite_or_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite_or_none(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
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
    return f"${val:+,.2f}"


def load_all_rows() -> tuple[pd.DataFrame, dict[str, Any]]:
    hist, hist_stats = cap.prepare_history()
    forward, forward_stats = cap.prepare_forward()
    frame = pd.concat([hist, forward], ignore_index=True)
    frame["target_date"] = frame["target_date"].astype(str)
    stats = {
        "historical": hist_stats,
        "forward": {"mechanism_rows": int(len(forward)), "source_stats": forward_stats},
        "rows": int(len(frame)),
        "date_min": str(frame["target_date"].min()) if len(frame) else None,
        "date_max": str(frame["target_date"].max()) if len(frame) else None,
        "settled_rows": int(frame["label_no_wins"].notna().sum()) if "label_no_wins" in frame else 0,
        "open_rows": int(frame["label_no_wins"].isna().sum()) if "label_no_wins" in frame else 0,
    }
    return frame.reset_index(drop=True), stats


def train_frozen_models(frame: pd.DataFrame) -> pd.DataFrame:
    train_mask = frame["target_date"].le(TRAINING_CUTOFF_DATE) & frame["label_no_wins"].notna()
    scored, _heat_model, _heat_sigma = v3.score_model(frame, train_mask, v3.ENHANCED_NUM_FEATURES, v3.ENHANCED_CAT_FEATURES)
    scored = cap.add_regime_columns(scored)
    cap_model = cap.build_cap_classifier(cap.CAP_CAT_WEATHER)
    train = scored[train_mask].copy()
    cap_model.fit(train[cap.CAP_NUM_FEATURES + cap.CAP_CAT_WEATHER], train["cap_label"].astype(int))
    scored = cap.score_cap_model(scored, cap_model, cap.CAP_CAT_WEATHER)
    return scored


def add_policy_multipliers(selected: pd.DataFrame) -> pd.DataFrame:
    if selected.empty:
        return selected.copy()
    out = selected.copy()
    daily = regime.selected_daily_features(out)
    if daily.empty:
        out["overconf_cluster_multiplier"] = 1.0
        out["principled_cluster_multiplier"] = 1.0
    else:
        daily["principled_cluster_risk"] = (
            0.55 * daily["avg_p_cap"].clip(0, 1)
            + 0.25 * daily["high_pcap_share"].clip(0, 1)
            + 0.20 * (daily["overconf_count"] / 2.0).clip(0, 1)
        ).clip(0, 1)
        daily["principled_cluster_multiplier"] = MIN_MULTIPLIER + (1.0 - MIN_MULTIPLIER) * (
            1.0 - daily["principled_cluster_risk"]
        )
        daily["overconf_cluster_multiplier"] = 1.0 / (1.0 + (daily["overconf_count"] / 2.0).clip(lower=0))
        out["principled_cluster_multiplier"] = out["target_date"].map(
            daily.set_index("target_date")["principled_cluster_multiplier"].to_dict()
        )
        out["overconf_cluster_multiplier"] = out["target_date"].map(
            daily.set_index("target_date")["overconf_cluster_multiplier"].to_dict()
        )
    out["full_multiplier"] = 1.0
    out["trade_cap_multiplier"] = MIN_MULTIPLIER + (1.0 - MIN_MULTIPLIER) * (
        1.0 - pd.to_numeric(out["p_cap"], errors="coerce").clip(0, 1)
    )
    out["overconf_combined_multiplier"] = out["overconf_cluster_multiplier"] * out["trade_cap_multiplier"]
    out["principled_combined_multiplier"] = out["principled_cluster_multiplier"] * out["trade_cap_multiplier"]
    out["base_notional_usd"] = BASE_NOTIONAL_USD
    for policy, mult_col in POLICIES.items():
        out[f"{policy}_multiplier"] = out[mult_col]
        out[f"{policy}_notional_usd"] = BASE_NOTIONAL_USD * out[mult_col]
        out[f"{policy}_settled_profit_usd"] = np.where(
            out["label_no_wins"].notna(),
            pd.to_numeric(out["stake_profit_usd"], errors="coerce") * out[mult_col],
            np.nan,
        )
    return out


def build_forward_ledger(scored: pd.DataFrame) -> pd.DataFrame:
    forward = scored[scored["target_date"].gt(TRAINING_CUTOFF_DATE)].copy()
    selected = cap.select_base_p40(forward)
    selected = add_policy_multipliers(selected)
    selected["freeze_date"] = FREEZE_DATE
    selected["training_cutoff_date"] = TRAINING_CUTOFF_DATE
    selected["record_status"] = np.where(selected["label_no_wins"].notna(), "settled_replay", "open_forward")
    keep_cols = [
        "freeze_date",
        "training_cutoff_date",
        "record_status",
        "target_date",
        "decision_snapshot_ts_utc",
        "city",
        "city_family",
        "bracket",
        "no_ask",
        "no_ask_size",
        "no_depth_5c_notional_approx",
        "p_cross_upper",
        "p_cap",
        "p_cross_cap_adjusted",
        "mechanism_edge",
        "cap_adjusted_edge",
        "required_gap_f",
        "pred_remaining_heat_f",
        "future_delta_to_daymax_f",
        "actual_margin_f",
        "pred_error_f",
        "label_no_wins",
        "stake_cost_usd",
        "stake_profit_usd",
        "full_multiplier",
        "trade_cap_multiplier",
        "overconf_cluster_multiplier",
        "principled_cluster_multiplier",
        "overconf_combined_multiplier",
        "principled_combined_multiplier",
    ]
    for policy in POLICIES:
        keep_cols.extend([f"{policy}_multiplier", f"{policy}_notional_usd", f"{policy}_settled_profit_usd"])
    for col in keep_cols:
        if col not in selected.columns:
            selected[col] = np.nan
    return selected[keep_cols].sort_values(["target_date", "city", "bracket"]).reset_index(drop=True)


def daily_summary(ledger: pd.DataFrame) -> pd.DataFrame:
    if ledger.empty:
        return pd.DataFrame()
    rows = []
    for date, group in ledger.groupby("target_date"):
        status = "settled" if group["label_no_wins"].notna().all() else "open"
        for policy in POLICIES:
            cost_col = f"{policy}_notional_usd"
            profit_col = f"{policy}_settled_profit_usd"
            cost = float(pd.to_numeric(group[cost_col], errors="coerce").sum())
            settled_profit = pd.to_numeric(group[profit_col], errors="coerce")
            profit = float(settled_profit.sum()) if settled_profit.notna().any() else np.nan
            rows.append(
                {
                    "target_date": date,
                    "settlement_status": status,
                    "policy": policy,
                    "candidates": int(len(group)),
                    "cities": int(group["city"].nunique()),
                    "weighted_notional_usd": cost,
                    "settled_profit_usd": profit,
                    "settled_roi": profit / cost if cost and not math.isnan(profit) else np.nan,
                    "wins": float(group["label_no_wins"].sum()) if group["label_no_wins"].notna().any() else np.nan,
                    "win_rate": float(group["label_no_wins"].mean()) if group["label_no_wins"].notna().any() else np.nan,
                    "avg_p_cross": float(group["p_cross_upper"].mean()),
                    "avg_p_cap": float(group["p_cap"].mean()),
                    "overconf_count": int((group["p_cross_upper"].ge(0.95) & group["p_cap"].lt(0.30)).sum()),
                }
            )
    return pd.DataFrame(rows).sort_values(["target_date", "policy"]).reset_index(drop=True)


def policy_totals(daily: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    settled = daily[daily["settlement_status"].eq("settled")].copy()
    for policy, group in settled.groupby("policy"):
        cost = float(group["weighted_notional_usd"].sum())
        profit = float(group["settled_profit_usd"].sum())
        rows.append(
            {
                "policy": policy,
                "settled_dates": int(group["target_date"].nunique()),
                "weighted_notional_usd": cost,
                "settled_profit_usd": profit,
                "settled_roi": profit / cost if cost else None,
                "candidate_days": int(group["candidates"].sum()),
            }
        )
    return rows


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
                vals.append(f"{val:.3f}")
            else:
                vals.append("" if pd.isna(val) else str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def render_md(payload: dict[str, Any], ledger: pd.DataFrame, daily: pd.DataFrame) -> str:
    totals = pd.DataFrame(payload["policy_totals"])
    open_days = daily[daily["settlement_status"].eq("open")].copy()
    settled_days = daily[daily["settlement_status"].eq("settled")].copy()
    return "\n".join(
        [
            "# Current-Bracket NO Forward Recording Protocol V1",
            "",
            "## 结论",
            "",
            "已建立一个冻结规则的 PIT forward ledger。这个不是挂真钱/zero-notional runner，而是固定训练截止日和候选版本，用 cutoff 后的 point-in-time replay 持续记录 benchmark/champion/challenger 的候选、size multiplier 和结算表现。",
            "",
            f"Verdict: `{payload['verdict']['status']}`，live_ready=`{payload['verdict']['live_ready']}`。",
            "",
            "## Frozen Protocol",
            "",
            f"- Freeze date: `{FREEZE_DATE}`",
            f"- Training cutoff: `{TRAINING_CUTOFF_DATE}`",
            f"- Base notional: `${BASE_NOTIONAL_USD:.2f}` per candidate before multiplier",
            "- Benchmark: `benchmark_full_size_base_p40`",
            "- Champion: `champion_trade_cap_soft_size`",
            "- Challenger: `challenger_overconf_cap_soft_size`",
            "- Diagnostic: `diagnostic_principled_cap_soft_size`",
            "",
            "## Current Ledger State",
            "",
            f"- Candidate rows after cutoff: `{payload['ledger']['rows']}`",
            f"- Settled candidate rows: `{payload['ledger']['settled_rows']}`",
            f"- Open candidate rows: `{payload['ledger']['open_rows']}`",
            f"- Dates: `{payload['ledger']['date_min']}`..`{payload['ledger']['date_max']}`",
            "",
            "## Settled Policy Totals",
            "",
            table(totals, ["policy", "settled_dates", "candidate_days", "weighted_notional_usd", "settled_profit_usd", "settled_roi"]),
            "",
            "## Daily Settled Replay",
            "",
            table(
                settled_days,
                [
                    "target_date",
                    "policy",
                    "candidates",
                    "weighted_notional_usd",
                    "settled_profit_usd",
                    "settled_roi",
                    "win_rate",
                    "avg_p_cross",
                    "avg_p_cap",
                    "overconf_count",
                ],
                limit=80,
            ),
            "",
            "## Open Forward Rows",
            "",
            table(
                open_days,
                [
                    "target_date",
                    "policy",
                    "candidates",
                    "weighted_notional_usd",
                    "avg_p_cross",
                    "avg_p_cap",
                    "overconf_count",
                ],
                limit=40,
            ),
            "",
            "## Promotion Rules",
            "",
            "1. Research -> forward_candidate: already satisfied by this frozen ledger.",
            "2. Forward_candidate -> paper/shadow-runner: require at least 10 new settled forward dates, >=80 settled candidates, champion ROI > 0, champion excess over benchmark > +5pp, and worst rolling 2-day weighted loss improved by >=30% versus benchmark.",
            "3. Paper/shadow-runner -> tiny live review: require at least 20 settled forward dates, CLOB/PIT coverage clean, champion ROI positive after costs, no unresolved settlement/source anomaly, and a separate execution/capacity review.",
            "4. Any rule change resets the freeze version. Old ledger remains evidence, but cannot be merged into the new version's forward proof.",
            "",
            "## Files",
            "",
            f"- Protocol JSON: `{OUT_JSON.relative_to(ROOT)}`",
            f"- Candidate ledger: `{OUT_LEDGER.relative_to(ROOT)}`",
            f"- Daily summary: `{OUT_DAILY.relative_to(ROOT)}`",
            "",
        ]
    )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame, source_stats = load_all_rows()
    scored = train_frozen_models(frame)
    ledger = build_forward_ledger(scored)
    daily = daily_summary(ledger)
    ledger.to_csv(OUT_LEDGER, index=False)
    daily.to_csv(OUT_DAILY, index=False)
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "current_bracket_no_forward_replay_v1",
        "source_stats": source_stats,
        "frozen_protocol": {
            "freeze_date": FREEZE_DATE,
            "training_cutoff_date": TRAINING_CUTOFF_DATE,
            "base_notional_usd": BASE_NOTIONAL_USD,
            "policies": POLICIES,
            "policy_roles": {
                "benchmark_full_size_base_p40": "benchmark",
                "champion_trade_cap_soft_size": "champion",
                "challenger_overconf_cap_soft_size": "challenger",
                "diagnostic_principled_cap_soft_size": "diagnostic",
            },
        },
        "ledger": {
            "rows": int(len(ledger)),
            "settled_rows": int(ledger["label_no_wins"].notna().sum()) if len(ledger) else 0,
            "open_rows": int(ledger["label_no_wins"].isna().sum()) if len(ledger) else 0,
            "date_min": str(ledger["target_date"].min()) if len(ledger) else None,
            "date_max": str(ledger["target_date"].max()) if len(ledger) else None,
        },
        "policy_totals": finite_or_none(policy_totals(daily)),
        "outputs": {
            "protocol_json": str(OUT_JSON.relative_to(ROOT)),
            "candidate_ledger_csv": str(OUT_LEDGER.relative_to(ROOT)),
            "daily_policy_summary_csv": str(OUT_DAILY.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
        },
        "verdict": {
            "status": "frozen_forward_ledger_started",
            "live_ready": False,
            "reason": "Scientific forward record is now frozen and recording via PIT replay; promotion requires new settled forward dates under unchanged policy.",
        },
    }
    OUT_JSON.write_text(json.dumps(finite_or_none(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(payload, ledger, daily), encoding="utf-8")
    print(json.dumps(payload["verdict"], indent=2, ensure_ascii=False))
    print(OUT_MD.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
