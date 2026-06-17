#!/usr/bin/env python3
"""Score forecast-peak-clock slices from the shared reheat feature factory."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DB = ROOT / "runtime/weather.db"
GATE = ROOT / "runtime/_dashboard_logs/clob_fill_coverage_gate.json"
FEATURE_ROWS = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_v1/reheat_feature_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-06/generated/reheat_feature_factory_forecast_peak_slices_v18"
OUT_JSON = ROOT / "docs/analysis/2026-06/2026-06-18-reheat-feature-factory-forecast-peak-slices-v18.json"
OUT_MD = ROOT / "docs/analysis/2026-06/2026-06-18-reheat-feature-factory-forecast-peak-slices-v18.md"

SPLIT_DATE = "2026-06-01"
TAKER_CUSHION = 0.02
SEED = 20260618


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=1.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


def query_rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def data_self_check() -> dict[str, Any]:
    conn = connect_ro()
    try:
        return {
            "fact_trades_max_built_at_utc": conn.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0],
            "fact_trades_by_class": query_rows(
                conn,
                "SELECT trade_class, COUNT(*) AS rows FROM fact_trades GROUP BY trade_class ORDER BY trade_class",
            ),
            "fact_trades_by_settlement_status": query_rows(
                conn,
                "SELECT COALESCE(settlement_status, '') AS settlement_status, COUNT(*) AS rows "
                "FROM fact_trades GROUP BY settlement_status ORDER BY settlement_status",
            ),
            "fact_signal_candidate_coverage": query_rows(
                conn,
                "SELECT COUNT(*) AS rows, SUM(eligible) AS eligible, SUM(paper_ordered) AS paper_ordered, "
                "SUM(live_filled) AS live_filled FROM fact_signal_candidates",
            )[0],
            "clob_order_fill_join": query_rows(
                conn,
                "SELECT o.status, COUNT(*) AS orders, "
                "SUM(CASE WHEN f.execution_id IS NOT NULL THEN 1 ELSE 0 END) AS with_fill "
                "FROM orders o LEFT JOIN fills f USING(execution_id) "
                "WHERE o.venue='polymarket_clob' GROUP BY o.status ORDER BY o.status",
            ),
        }
    finally:
        conn.close()


def load_gate() -> dict[str, Any]:
    if not GATE.exists():
        return {"gate_pass": None, "missing": True}
    data = json.loads(GATE.read_text(encoding="utf-8"))
    return {
        "gate_pass": data.get("gate_pass"),
        "fail_reasons": data.get("fail_reasons", []),
        "missing_order_rows": data.get("db_fills", {}).get("missing_order_rows"),
        "over_order_keys": data.get("db_fills", {}).get("over_order_keys"),
        "db_fill_cost_minus_fact_cost": data.get("db_fill_cost_minus_fact_cost"),
    }


def pct(value: Any, signed: bool = True) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{100 * f:{'+' if signed else ''}.1f}%"


def fnum(value: Any, digits: int = 3) -> str:
    if value is None:
        return "NA"
    try:
        f = float(value)
    except Exception:
        return "NA"
    if not math.isfinite(f):
        return "NA"
    return f"{f:.{digits}f}"


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    return value


def block_bootstrap_roi(df: pd.DataFrame, pnl_col: str, cost_col: str, n: int = 3000) -> tuple[float | None, float | None]:
    daily = df.groupby("target_date", as_index=False).agg(pnl=(pnl_col, "sum"), cost=(cost_col, "sum"))
    daily = daily[daily["cost"].gt(0)].copy()
    if daily.empty or len(daily) < 2:
        return None, None
    rng = np.random.default_rng(SEED)
    values = []
    pnl = daily["pnl"].to_numpy(dtype=float)
    cost = daily["cost"].to_numpy(dtype=float)
    for _ in range(n):
        idx = rng.integers(0, len(daily), size=len(daily))
        denom = float(cost[idx].sum())
        values.append(float(pnl[idx].sum() / denom) if denom > 0 else np.nan)
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return None, None
    return float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))


def block_bootstrap_delta(df: pd.DataFrame, left: str, right: str, n: int = 3000) -> tuple[float | None, float | None]:
    daily = df.groupby("target_date", as_index=False).agg(delta=(left, "sum"), base=(right, "sum"))
    if daily.empty or len(daily) < 2:
        return None, None
    rng = np.random.default_rng(SEED)
    vals = []
    delta = daily["delta"].to_numpy(dtype=float)
    base = daily["base"].to_numpy(dtype=float)
    for _ in range(n):
        idx = rng.integers(0, len(daily), size=len(daily))
        denom = float(base[idx].sum())
        vals.append(float(delta[idx].sum() / denom) if denom > 0 else np.nan)
    arr = np.asarray(vals, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return None, None
    return float(np.quantile(arr, 0.025)), float(np.quantile(arr, 0.975))


def normalize_bool(series: pd.Series) -> pd.Series:
    if series.dtype == bool:
        return series
    numeric = pd.to_numeric(series, errors="coerce")
    numeric_mask = numeric.notna()
    text = series.astype(str).str.lower()
    return numeric.gt(0.5).where(numeric_mask, text.isin(["true", "1", "1.0", "yes"]))


def load_states() -> pd.DataFrame:
    rows = pd.read_csv(FEATURE_ROWS, low_memory=False)
    state_cols = ["city", "target_date", "decision_hour_local"]
    states = rows.sort_values(state_cols).drop_duplicates(state_cols).copy()
    states["target_date"] = states["target_date"].astype(str)
    states["period"] = np.where(states["target_date"].lt(SPLIT_DATE), "train", "holdout")
    for col in [
        "current_yes_ask",
        "d1_no_ask",
        "decline_native",
        "gfs_forecast_peak_delta_hours_local",
        "ecmwf_forecast_peak_delta_hours_local",
        "gfs_forecast_gap_to_running_native",
        "ecmwf_forecast_gap_to_running_native",
        "forecast_peak_hour_spread",
    ]:
        states[col] = pd.to_numeric(states.get(col), errors="coerce")
    states["current_bracket_held_bool"] = normalize_bool(states["current_bracket_held"])
    states["d1_hit_bool"] = normalize_bool(states["d1_hit"])
    states["models_agree_bool"] = normalize_bool(states["forecast_peak_models_agree_le_1h"])
    states["yes_cost"] = (states["current_yes_ask"] + TAKER_CUSHION).clip(upper=0.99)
    states["d1_no_cost"] = (states["d1_no_ask"] + TAKER_CUSHION).clip(upper=0.99)
    states["yes_pnl"] = np.where(states["current_bracket_held_bool"], 1.0 - states["yes_cost"], -states["yes_cost"])
    states["d1_no_pnl"] = np.where(~states["d1_hit_bool"], 1.0 - states["d1_no_cost"], -states["d1_no_cost"])
    states["yes_minus_d1_no_pnl"] = states["yes_pnl"] - states["d1_no_pnl"]
    states["paired_cost"] = states[["yes_cost", "d1_no_cost"]].mean(axis=1)
    return states


def rules(states: pd.DataFrame) -> dict[str, pd.Series]:
    has_yes = states["current_yes_ask"].between(0.01, 0.97)
    has_d1 = states["d1_no_ask"].between(0.01, 0.97)
    has_clock = states["gfs_forecast_peak_delta_hours_local"].notna() & states["ecmwf_forecast_peak_delta_hours_local"].notna()
    both_after_0_4 = (
        states["gfs_forecast_peak_delta_hours_local"].between(0, 4)
        & states["ecmwf_forecast_peak_delta_hours_local"].between(0, 4)
    )
    near_peak = (
        states["gfs_forecast_peak_delta_hours_local"].between(-1, 1)
        & states["ecmwf_forecast_peak_delta_hours_local"].between(-1, 1)
    )
    low_gap = (
        states["gfs_forecast_gap_to_running_native"].le(0.5)
        & states["ecmwf_forecast_gap_to_running_native"].le(0.5)
    )
    return {
        "all_clocked_current_yes": has_yes & has_clock,
        "v9_like_fade_confirmed_physical": has_yes
        & has_clock
        & states["decision_hour_local"].between(13, 15)
        & states["decline_native"].ge(0.5),
        "forecast_fade_both_peaks_passed": has_yes & has_clock & states["decline_native"].ge(0.5) & both_after_0_4,
        "forecast_fade_passed_low_gap": has_yes & has_clock & states["decline_native"].ge(0.5) & both_after_0_4 & low_gap,
        "peak_forming_near_agree": has_yes
        & has_clock
        & states["decline_native"].le(0.1)
        & near_peak
        & states["models_agree_bool"],
        "danger_gfs_peak_still_2h_ahead": has_yes & has_clock & states["gfs_forecast_peak_delta_hours_local"].le(-2),
        "diagnostic_gfs_1_to_4h_after_peak": has_yes
        & has_clock
        & states["gfs_forecast_peak_delta_hours_local"].between(1, 4),
        "paired_current_yes_vs_d1_no_clocked": has_yes & has_d1 & has_clock,
    }


def summarize_rule(name: str, mask: pd.Series, states: pd.DataFrame, period: str) -> dict[str, Any]:
    if period == "all":
        df = states[mask].copy()
    else:
        df = states[mask & states["period"].eq(period)].copy()
    paired = df[df["d1_no_ask"].between(0.01, 0.97)].copy()
    yes_cost = float(df["yes_cost"].sum()) if len(df) else 0.0
    yes_pnl = float(df["yes_pnl"].sum()) if len(df) else 0.0
    yes_ci = block_bootstrap_roi(df, "yes_pnl", "yes_cost") if len(df) else (None, None)
    d1_cost = float(paired["d1_no_cost"].sum()) if len(paired) else 0.0
    d1_pnl = float(paired["d1_no_pnl"].sum()) if len(paired) else 0.0
    d1_ci = block_bootstrap_roi(paired, "d1_no_pnl", "d1_no_cost") if len(paired) else (None, None)
    delta_ci = block_bootstrap_delta(paired, "yes_minus_d1_no_pnl", "paired_cost") if len(paired) else (None, None)
    return {
        "rule": name,
        "period": period,
        "rows": int(len(df)),
        "active_dates": int(df["target_date"].nunique()) if len(df) else 0,
        "cities": int(df["city"].nunique()) if len(df) else 0,
        "yes_win_rate": float(df["current_bracket_held_bool"].mean()) if len(df) else None,
        "yes_cost": yes_cost,
        "yes_pnl": yes_pnl,
        "yes_roi": yes_pnl / yes_cost if yes_cost > 0 else None,
        "yes_roi_ci95": yes_ci,
        "paired_rows": int(len(paired)),
        "d1_no_win_rate": float((~paired["d1_hit_bool"]).mean()) if len(paired) else None,
        "d1_no_cost": d1_cost,
        "d1_no_pnl": d1_pnl,
        "d1_no_roi": d1_pnl / d1_cost if d1_cost > 0 else None,
        "d1_no_roi_ci95": d1_ci,
        "yes_minus_d1_no_roi_on_paired_cost": float(paired["yes_minus_d1_no_pnl"].sum() / paired["paired_cost"].sum())
        if len(paired) and float(paired["paired_cost"].sum()) > 0
        else None,
        "yes_minus_d1_no_roi_ci95": delta_ci,
    }


def make_summary(states: pd.DataFrame) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    masks = rules(states)
    rows = []
    for name, mask in masks.items():
        for period in ["train", "holdout", "all"]:
            rows.append(summarize_rule(name, mask, states, period))
    return rows, pd.DataFrame(rows)


def write_report(payload: dict[str, Any], summary_df: pd.DataFrame) -> None:
    holdout = summary_df[summary_df["period"].eq("holdout")].copy()
    ordered = [
        "v9_like_fade_confirmed_physical",
        "forecast_fade_both_peaks_passed",
        "forecast_fade_passed_low_gap",
        "peak_forming_near_agree",
        "danger_gfs_peak_still_2h_ahead",
        "diagnostic_gfs_1_to_4h_after_peak",
        "paired_current_yes_vs_d1_no_clocked",
    ]
    holdout["order"] = holdout["rule"].map({name: idx for idx, name in enumerate(ordered)}).fillna(999)
    holdout = holdout.sort_values(["order", "rule"])

    lines = [
        "# Reheat Feature Factory Forecast Peak Slices v18",
        "",
        "## Target Metric",
        "",
        "`forecast_peak_clock_model_incremental_value` = using the shared reheat feature table, does forecast peak clock separate current-YES states with different hold probability and executable ROI proxy?",
        "",
        "This report is an opportunity/replay slice. It is not live fill PnL and it does not change N100 behavior.",
        "",
        "## Data Snapshot",
        "",
        f"- Generated at UTC: `{payload['generated_at_utc']}`.",
        f"- Feature table: `{payload['inputs']['feature_rows']}`.",
        f"- DB fact built at UTC: `{payload['data_self_check']['fact_trades_max_built_at_utc']}`.",
        f"- CLOB gate pass: `{payload['clob_fill_gate'].get('gate_pass')}`.",
        f"- State rows: {payload['funnel']['state_rows']}; clocked current-YES rows: {payload['funnel']['clocked_current_yes_rows']}.",
        "",
        "## Human Summary",
        "",
        "The forecast peak clock is now useful as a shared model feature, but it still has not earned the right to become a live hard gate.",
        "",
        "The clearest practical use is risk labeling: states where GFS still places the peak at least two local hours ahead are bad for current YES in holdout. The more selective profitable bins exist, but they are too small to promote by themselves.",
        "",
        "## Mandatory SQL Self-Check",
        "",
        "```json",
        json.dumps(payload["data_self_check"], indent=2, ensure_ascii=False),
        "```",
        "",
        "## Holdout Success And ROI",
        "",
        "| Slice | Rows | Dates | YES win | YES ROI | YES 95% CI | d1 NO ROI | YES - d1 NO |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in holdout.to_dict("records"):
        yes_ci = row.get("yes_roi_ci95") or [None, None]
        delta_ci = row.get("yes_minus_d1_no_roi_ci95") or [None, None]
        lines.append(
            "| `{rule}` | {rows} | {active_dates} | {yes_win} | {yes_roi} | [{lo}, {hi}] | {d1_roi} | {delta} [{dlo}, {dhi}] |".format(
                rule=row["rule"],
                rows=int(row["rows"]),
                active_dates=int(row["active_dates"]),
                yes_win=pct(row["yes_win_rate"], signed=False),
                yes_roi=pct(row["yes_roi"]),
                lo=pct(yes_ci[0]),
                hi=pct(yes_ci[1]),
                d1_roi=pct(row["d1_no_roi"]),
                delta=pct(row["yes_minus_d1_no_roi_on_paired_cost"]),
                dlo=pct(delta_ci[0]),
                dhi=pct(delta_ci[1]),
            )
        )
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            "For live promotion, the fixed v9 current-YES rule remains the reference. Forecast peak clock now belongs in the shared model layer and forward telemetry, not in the live rule as a hard filter.",
            "",
            "```text",
            "significance=FAIL for forecast-clock hard-gate promotion",
            "baseline=PASS only for the existing fixed v9 current-YES reference",
            "forward=FAIL until N100 forward telemetry produces settled rows",
            "conclusion=inconclusive for live gating; continue telemetry/modeling",
            "```",
            "",
            "## Output Files",
            "",
            f"- JSON: `{payload['outputs']['json']}`",
            f"- CSV: `{payload['outputs']['rule_summary_csv']}`",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    self_check = data_self_check()
    gate = load_gate()
    states = load_states()
    rows, summary_df = make_summary(states)
    summary_csv = OUT_DIR / "rule_summary.csv"
    summary_df.to_csv(summary_csv, index=False)
    payload = {
        "generated_at_utc": now_utc(),
        "target_metric": "forecast_peak_clock_model_incremental_value",
        "evidence_layer": "shared reheat feature factory opportunity replay, not live fills",
        "split_date": SPLIT_DATE,
        "taker_cushion": TAKER_CUSHION,
        "data_self_check": self_check,
        "clob_fill_gate": gate,
        "funnel": {
            "state_rows": int(len(states)),
            "active_dates": int(states["target_date"].nunique()),
            "cities": int(states["city"].nunique()),
            "clocked_current_yes_rows": int(
                (
                    states["current_yes_ask"].between(0.01, 0.97)
                    & states["gfs_forecast_peak_delta_hours_local"].notna()
                    & states["ecmwf_forecast_peak_delta_hours_local"].notna()
                ).sum()
            ),
        },
        "rule_summary": rows,
        "outputs": {
            "json": str(OUT_JSON.relative_to(ROOT)),
            "markdown": str(OUT_MD.relative_to(ROOT)),
            "rule_summary_csv": str(summary_csv.relative_to(ROOT)),
        },
        "inputs": {
            "feature_rows": str(FEATURE_ROWS.relative_to(ROOT)),
        },
    }
    OUT_JSON.write_text(json.dumps(json_ready(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(payload, summary_df)
    print(json.dumps({"rules": len(rows), "out_json": str(OUT_JSON), "out_md": str(OUT_MD)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
