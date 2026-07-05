#!/usr/bin/env python3
"""P6 zero-notional shadow telemetry pack for Tmax distribution-first.

This script does not place orders. It converts P5 expression opportunities into
shadow-event rows that can be used as the contract for a future runner:

  - one row per config x city-date-hour
  - keep both selected and blocked rows
  - select only the first eligible row per config x scope x city-day, matching
    the live execution unit; later same-city-day rows remain blocked telemetry
  - store the best expression, edge, ask, context, and eventual label if known
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
P5_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p5_walk_forward_execution_replay_v1"
P5_OPPS_PATH = P5_DIR / "opportunities.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1"
REPORT_PATH = ROOT / "docs/analysis/2026-07/2026-07-03-tmax-distribution-p6-shadow-telemetry-v1.md"
SUMMARY_JSON_PATH = ROOT / "docs/analysis/2026-07/2026-07-03-tmax-distribution-p6-shadow-telemetry-v1.json"

CONFIGS = [
    {
        "shadow_config_id": "tmax_dist_clean_edge02",
        "method": "loo_no_city_source_blend",
        "edge_threshold": 0.02,
        "role": "clean_mechanism_primary",
    },
    {
        "shadow_config_id": "tmax_dist_city_source_edge02",
        "method": "mkt_city_source_blend",
        "edge_threshold": 0.02,
        "role": "capacity_and_city_source_comparison",
    },
    {
        "shadow_config_id": "tmax_dist_clean_edge10",
        "method": "loo_no_city_source_blend",
        "edge_threshold": 0.10,
        "role": "dev_cv_high_edge_pressure_test",
    },
]

KEYS = ["scope", "method", "city", "target_date", "decision_hour_local"]
SELECTION_POLICY = "first_eligible_city_day"
SHADOW_SCHEMA_VERSION = 2


def _event_id(row: dict[str, Any]) -> str:
    raw = "|".join(
        str(row.get(k, ""))
        for k in [
            "shadow_config_id",
            "scope",
            "city",
            "target_date",
            "decision_hour_local",
            "method",
            "chosen_expression",
            "selection_policy",
            "shadow_schema_version",
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _date_block_roi_ci(rows: pd.DataFrame, n_boot: int = 1000, seed: int = 20260703) -> dict[str, float]:
    if rows.empty:
        return {"roi": math.nan, "ci_low": math.nan, "ci_high": math.nan}
    cost = float(rows["ask"].sum())
    pnl = float(rows["unit_pnl"].sum())
    roi = pnl / cost if cost else math.nan
    by_date = rows.groupby("target_date", as_index=False).agg(cost=("ask", "sum"), pnl=("unit_pnl", "sum"))
    if len(by_date) < 3:
        return {"roi": roi, "ci_low": math.nan, "ci_high": math.nan}
    rng = np.random.default_rng(seed)
    arr = by_date[["cost", "pnl"]].to_numpy(dtype=float)
    boot = []
    for _ in range(n_boot):
        sample = arr[rng.integers(0, len(arr), size=len(arr))]
        c = sample[:, 0].sum()
        p = sample[:, 1].sum()
        boot.append(p / c if c else math.nan)
    return {
        "roi": roi,
        "ci_low": float(np.nanpercentile(boot, 2.5)),
        "ci_high": float(np.nanpercentile(boot, 97.5)),
    }


def _fmt_pct(value: object, *, signed: bool = True) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if math.isnan(v):
        return "n/a"
    sign = "+" if signed else ""
    return f"{v:{sign}.1%}"


def _load_opps() -> pd.DataFrame:
    if not P5_OPPS_PATH.exists():
        raise FileNotFoundError(f"Missing P5 opportunities: {P5_OPPS_PATH}")
    df = pd.read_csv(P5_OPPS_PATH)
    required = {
        "scope",
        "method",
        "expression",
        "city",
        "target_date",
        "decision_hour_local",
        "actual_bucket",
        "ask",
        "p_win",
        "model_edge",
        "model_roi",
        "win",
        "unit_pnl",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise RuntimeError(f"P5 opportunities missing columns: {missing}")
    return df


def _best_rows_for_config(opps: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    method = str(config["method"])
    threshold = float(config["edge_threshold"])
    sub = opps[opps["method"].eq(method)].copy()
    if sub.empty:
        return pd.DataFrame()
    best = sub.sort_values(["model_edge", "model_roi"], ascending=False).groupby(KEYS, as_index=False).head(1)
    best = best.sort_values(
        ["scope", "city", "target_date", "decision_hour_local", "model_edge", "model_roi"],
        ascending=[True, True, True, True, False, False],
    ).copy()
    best["_edge_pass"] = best["model_edge"].astype(float) >= threshold
    best["_city_day_eligible_rank"] = np.nan
    edge_pass = best[best["_edge_pass"]].copy()
    if not edge_pass.empty:
        ranks = edge_pass.groupby(["scope", "city", "target_date"], sort=False).cumcount() + 1
        best.loc[edge_pass.index, "_city_day_eligible_rank"] = ranks.astype(float)
    rows = []
    for item in best.to_dict("records"):
        edge_passed = bool(item["_edge_pass"])
        city_day_rank = item.get("_city_day_eligible_rank")
        selected = edge_passed and float(city_day_rank) == 1.0
        if selected:
            selection_reason = "edge_pass_first_city_day"
        elif edge_passed:
            selection_reason = "city_day_after_first_selected"
        else:
            selection_reason = "below_edge_threshold"
        row = {
            "shadow_event_id": None,
            "shadow_schema_version": SHADOW_SCHEMA_VERSION,
            "shadow_config_id": config["shadow_config_id"],
            "shadow_role": config["role"],
            "selection_policy": SELECTION_POLICY,
            "zero_notional": True,
            "no_order_placed": True,
            "selection_status": "selected" if selected else "blocked",
            "selection_reason": selection_reason,
            "edge_threshold": threshold,
            "city_day_eligible_rank": city_day_rank,
            "scope": item["scope"],
            "city": item["city"],
            "target_date": item["target_date"],
            "decision_hour_local": item["decision_hour_local"],
            "method": item["method"],
            "chosen_expression": item["expression"],
            "ask": item["ask"],
            "p_win": item["p_win"],
            "model_edge": item["model_edge"],
            "model_roi": item["model_roi"],
            "actual_bucket": item.get("actual_bucket"),
            "label_source": item.get("label_source"),
            "eval_slice": item.get("eval_slice"),
            "win": item.get("win"),
            "unit_pnl": item.get("unit_pnl"),
            "day_regime": item.get("day_regime"),
            "intraday_state": item.get("intraday_state"),
            "moisture_cloud_regime": item.get("moisture_cloud_regime"),
            "wind_regime": item.get("wind_regime"),
            "running_max_state": item.get("running_max_state"),
            "solar_window": item.get("solar_window"),
            "forecast_source": item.get("forecast_source"),
            "city_family": item.get("city_family"),
            "minutes_since_running_max": item.get("minutes_since_running_max"),
            "forecast_peak_delta_hours_local": item.get("forecast_peak_delta_hours_local"),
            "temp_trend_1h_f": item.get("temp_trend_1h_f"),
            "temp_trend_3h_f": item.get("temp_trend_3h_f"),
            "wind_speed_kt": item.get("wind_speed_kt"),
            "relative_humidity_pct": item.get("relative_humidity_pct"),
        }
        row["shadow_event_id"] = _event_id(row)
        rows.append(row)
    return pd.DataFrame(rows)


def _build_shadow_events(opps: pd.DataFrame) -> pd.DataFrame:
    frames = [_best_rows_for_config(opps, config) for config in CONFIGS]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _summary(events: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (scope, cfg), grp in events.groupby(["scope", "shadow_config_id"], dropna=False):
        selected = grp[grp["selection_status"].eq("selected")].copy()
        ci = _date_block_roi_ci(selected)
        expr = selected["chosen_expression"].value_counts().to_dict() if not selected.empty else {}
        by_day = selected.groupby("target_date", as_index=False).agg(cost=("ask", "sum"), pnl=("unit_pnl", "sum"))
        rows.append(
            {
                "scope": scope,
                "shadow_config_id": cfg,
                "candidate_rows": int(len(grp)),
                "selected_rows": int(len(selected)),
                "blocked_rows": int((grp["selection_status"] == "blocked").sum()),
                "dates": int(selected["target_date"].nunique()) if not selected.empty else 0,
                "cities": int(selected["city"].nunique()) if not selected.empty else 0,
                "avg_ask": float(selected["ask"].mean()) if not selected.empty else math.nan,
                "avg_edge": float(selected["model_edge"].mean()) if not selected.empty else math.nan,
                "win_rate": float(selected["win"].mean()) if not selected.empty else math.nan,
                "cost": float(selected["ask"].sum()) if not selected.empty else 0.0,
                "pnl": float(selected["unit_pnl"].sum()) if not selected.empty else 0.0,
                "roi": ci["roi"],
                "roi_ci_low": ci["ci_low"],
                "roi_ci_high": ci["ci_high"],
                "daily_win_rate": float((by_day["pnl"] > 0).mean()) if len(by_day) else math.nan,
                "current_yes_rows": int(expr.get("current_yes", 0)),
                "current_no_rows": int(expr.get("current_no", 0)),
                "d1_no_rows": int(expr.get("d1_no", 0)),
                "d2_no_rows": int(expr.get("d2_no", 0)),
            }
        )
    return pd.DataFrame(rows).sort_values(["scope", "shadow_config_id"]).reset_index(drop=True)


def _table(df: pd.DataFrame, columns: list[str]) -> list[str]:
    if df.empty:
        return ["_No rows._"]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in df.to_dict("records"):
        vals = []
        for col in columns:
            val = row.get(col)
            if col in {"roi", "roi_ci_low", "roi_ci_high", "win_rate", "daily_win_rate"}:
                vals.append(_fmt_pct(val, signed=col != "win_rate" and col != "daily_win_rate"))
            elif isinstance(val, float):
                if math.isnan(val):
                    vals.append("n/a")
                else:
                    vals.append(f"{val:.3f}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |")
    return lines


def _write_report(events: pd.DataFrame, summary: pd.DataFrame, report: dict[str, Any]) -> None:
    focus = summary[summary["scope"].isin(["verified_forward", "extension_forward"])].copy()
    schema_cols = [
        "shadow_event_id",
        "shadow_schema_version",
        "shadow_config_id",
        "selection_policy",
        "zero_notional",
        "no_order_placed",
        "selection_status",
        "selection_reason",
        "edge_threshold",
        "city_day_eligible_rank",
        "scope",
        "city",
        "target_date",
        "decision_hour_local",
        "method",
        "chosen_expression",
        "ask",
        "p_win",
        "model_edge",
        "actual_bucket",
        "label_source",
        "day_regime",
        "intraday_state",
        "running_max_state",
    ]
    lines = [
        "# Tmax Distribution P6 Shadow Telemetry v1",
        "",
        f"> generated_at_utc: `{report['generated_at_utc']}`",
        "> Scope: zero-notional shadow telemetry pack; no live runner/order behavior changed.",
        "",
        "## 结论",
        "",
        "- P6 没有改模型，也没有改 live；它把 P5 的表达选择结果整理成未来 shadow runner 应该写出的事件格式。",
        "- 每个 config x city-date-hour 都保留一行，但主 `selected` 口径是 live-like：每个 config x scope x city-day 只取第一条 edge-pass 机会。",
        "- 同一 city-day 后续再次触发的小时信号不会丢，标成 `blocked/city_day_after_first_selected`，用于复盘“如果重复买会怎样”。",
        "",
        "## Candidate Configs",
        "",
        "| config | method | threshold | role |",
        "|---|---|---:|---|",
    ]
    for cfg in CONFIGS:
        lines.append(
            f"| {cfg['shadow_config_id']} | {cfg['method']} | {cfg['edge_threshold']:.2f} | {cfg['role']} |"
        )
    lines.extend(
        [
            "",
            "## Forward Summary",
            "",
            *_table(
                focus,
                [
                    "scope",
                    "shadow_config_id",
                    "candidate_rows",
                    "selected_rows",
                    "blocked_rows",
                    "dates",
                    "cities",
                    "avg_ask",
                    "avg_edge",
                    "win_rate",
                    "cost",
                    "pnl",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "daily_win_rate",
                    "current_yes_rows",
                    "current_no_rows",
                    "d1_no_rows",
                    "d2_no_rows",
                ],
            ),
            "",
            "## Event Schema",
            "",
            "Future live/shadow runner should write these fields per cycle. This report backfills them from P5 opportunities for validation.",
            "",
            "| field | meaning |",
            "|---|---|",
        ]
    )
    meanings = {
        "shadow_event_id": "deterministic id for config x state x chosen expression x selection policy",
        "shadow_schema_version": "event schema version",
        "shadow_config_id": "candidate policy identity",
        "selection_policy": "first eligible per config x scope x city-day",
        "zero_notional": "always true for P6",
        "no_order_placed": "always true for P6",
        "selection_status": "selected or blocked",
        "selection_reason": "edge_pass_first_city_day, city_day_after_first_selected, or below_edge_threshold",
        "edge_threshold": "config threshold",
        "city_day_eligible_rank": "1 for the first edge-pass row in a city-day; later edge-pass rows are blocked telemetry",
        "scope": "dev_cv / verified_forward / extension_forward",
        "city": "market city",
        "target_date": "weather contract date",
        "decision_hour_local": "local hour from state row",
        "method": "probability model method",
        "chosen_expression": "best expression by model_edge",
        "ask": "observed ask",
        "p_win": "model probability expression wins",
        "model_edge": "p_win - ask",
        "actual_bucket": "settlement bucket when available",
        "label_source": "settlement_outcomes or observed_max_derived",
        "day_regime": "atlas day regime",
        "intraday_state": "atlas intraday state",
        "running_max_state": "atlas running max state",
    }
    for col in schema_cols:
        lines.append(f"| `{col}` | {meanings.get(col, '')} |")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- `{(OUT_DIR / 'shadow_events.csv').relative_to(ROOT)}`",
            f"- `{(OUT_DIR / 'shadow_summary.csv').relative_to(ROOT)}`",
            f"- `{SUMMARY_JSON_PATH.relative_to(ROOT)}`",
            "",
            "## Verdict",
            "",
            "conclusion=`shadow_telemetry_contract_ready`; live_action=`none`.",
            "",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    opps = _load_opps()
    events = _build_shadow_events(opps)
    if events.empty:
        raise RuntimeError("No shadow events built")
    summary = _summary(events)
    events.to_csv(OUT_DIR / "shadow_events.csv", index=False)
    summary.to_csv(OUT_DIR / "shadow_summary.csv", index=False)
    report = {
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "source": str(P5_OPPS_PATH.relative_to(ROOT)),
        "selection_policy": SELECTION_POLICY,
        "shadow_schema_version": SHADOW_SCHEMA_VERSION,
        "rows": int(len(events)),
        "selected_rows": int((events["selection_status"] == "selected").sum()),
        "blocked_rows": int((events["selection_status"] == "blocked").sum()),
        "configs": CONFIGS,
        "verdict": "shadow_telemetry_contract_ready",
        "summary": summary.to_dict("records"),
    }
    SUMMARY_JSON_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    _write_report(events, summary, report)
    print(
        json.dumps(
            {
                "report_path": str(REPORT_PATH.relative_to(ROOT)),
                "rows": report["rows"],
                "selected_rows": report["selected_rows"],
                "blocked_rows": report["blocked_rows"],
                "verdict": report["verdict"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
