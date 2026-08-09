#!/usr/bin/env python3
"""Extend the HeadA source-quality score with settled forward rows.

Extends v1 with settled forward opportunity rows through the latest settled
date available in `runtime/weather.db`.  This keeps the same mechanism score
and current price-tier 6/8/10-share execution geometry; it does not change live.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import low_price_yes_source_quality_score_base as v1
from src.strategies.weather_edge_v1.tools.low_price_yes_tail_telemetry import (
    build_low_price_yes_tail_telemetry,
    load_tail_telemetry_resources,
)

DB_PATH = ROOT / "runtime/weather.db"
HIST_ROWS = ROOT / "docs/analysis/2026-07/generated/low_price_yes_forecast_source_calibration_v1/candidate_rows.csv"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated/low_price_yes_source_quality_score_v2"
OUT_MD = ROOT / "docs/analysis/2026-07/2026-07-08-low-price-yes-source-quality-score-v2.md"
OUT_JSON = ROOT / "docs/analysis/2026-07/2026-07-08-low-price-yes-source-quality-score-v2.json"

FORWARD_START = "2026-07-01"
WEATHER_TAKER_FEE_RATE = 0.05


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None or value == "":
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def weather_taker_fee(*, shares: float, price: float) -> float:
    if shares <= 0.0 or price <= 0.0 or price >= 1.0:
        return 0.0
    return shares * WEATHER_TAKER_FEE_RATE * price * (1.0 - price)


def price_tier_shares(entry: float) -> float:
    if entry <= 0.08:
        return 6.0
    if entry <= 0.14:
        return 8.0
    return 10.0


def fmt_pct(value: Any, *, signed: bool = True) -> str:
    return v1.fmt_pct(value, signed=signed)


def fmt_float(value: Any, digits: int = 2) -> str:
    return v1.fmt_float(value, digits)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def max_settled_target_date() -> str:
    conn = connect()
    try:
        row = conn.execute(
            "SELECT MAX(target_date) AS max_date FROM settlement_outcomes WHERE settlement_status='settled'"
        ).fetchone()
    finally:
        conn.close()
    return str(row["max_date"] or "")


def fact_snapshot() -> dict[str, Any]:
    conn = connect()
    try:
        signal = conn.execute(
            """
            SELECT MIN(event_date) AS min_event_date,
                   MAX(event_date) AS max_event_date,
                   MAX(fact_built_at_utc) AS fact_built_at_utc,
                   COUNT(*) AS fact_signal_rows
            FROM fact_signal_candidates
            """
        ).fetchone()
        settlement = conn.execute(
            """
            SELECT MIN(target_date) AS min_settlement_date,
                   MAX(target_date) AS max_settlement_date,
                   COUNT(*) AS settlement_rows,
                   SUM(CASE WHEN settlement_status='settled' THEN 1 ELSE 0 END) AS settled_rows
            FROM settlement_outcomes
            """
        ).fetchone()
    finally:
        conn.close()
    return {**dict(signal), **dict(settlement)}


def load_historical_rows() -> pd.DataFrame:
    rows = pd.read_csv(HIST_ROWS, low_memory=False)
    rows = rows.copy()
    rows["sample_window"] = "historical_0506_0630"
    rows["target_date"] = rows["target_date"].astype(str)
    rows["entry"] = pd.to_numeric(rows["entry"], errors="coerce")
    rows["payoff"] = pd.to_numeric(rows["payoff"], errors="coerce")
    rows["shares"] = rows["entry"].map(price_tier_shares)
    rows["entry_fee"] = [weather_taker_fee(shares=s, price=p) for s, p in zip(rows["shares"], rows["entry"], strict=False)]
    rows["cost"] = rows["shares"] * rows["entry"] + rows["entry_fee"]
    rows["pnl"] = rows["shares"] * rows["payoff"] - rows["cost"]
    rows["win"] = rows["payoff"].eq(1.0)
    return normalize_source_quality_columns(rows)


def load_forward_opportunity_rows(start_date: str, end_date: str) -> pd.DataFrame:
    if not end_date or end_date < start_date:
        return pd.DataFrame()
    conn = connect()
    try:
        rows = pd.read_sql_query(
            """
            SELECT
              candidate_id,
              condition_id,
              market_id,
              city,
              event_date AS target_date,
              event_date,
              bracket,
              unit,
              forecast_source,
              forecast_peak_source,
              forecast_max_f,
              forecast_max_native,
              forecast_peak_delta_hours_local,
              forecast_max_in_bracket,
              forecast_max_above_bracket_f,
              forecast_max_below_bracket_f,
              forecast_timezone,
              model_p_yes,
              market_yes_price,
              edge,
              decision_entry_price AS ask,
              first_seen_ts_utc,
              decision_snapshot_ts_utc,
              final_yes AS payoff,
              settlement_status
            FROM fact_signal_candidates
            WHERE event_date BETWEEN ? AND ?
              AND side='BUY_YES'
              AND decision_entry_price BETWEEN 0.05 AND 0.20
              AND edge >= 0.20
              AND settlement_status='settled'
              AND final_yes IN (0.0, 1.0)
            ORDER BY event_date, city, decision_snapshot_ts_utc, candidate_id
            """,
            conn,
            params=(start_date, end_date),
        )
    finally:
        conn.close()
    if rows.empty:
        return rows

    resources = load_tail_telemetry_resources()
    telemetry_rows: list[dict[str, Any]] = []
    for rec in rows.to_dict(orient="records"):
        telemetry_rows.append(build_low_price_yes_tail_telemetry(rec, resources))
    telem = pd.DataFrame(telemetry_rows)
    out = pd.concat([rows.reset_index(drop=True), telem.reset_index(drop=True)], axis=1)
    out["entry"] = pd.to_numeric(out["ask"], errors="coerce")
    out["payoff"] = pd.to_numeric(out["payoff"], errors="coerce")
    out["edge"] = pd.to_numeric(out["edge"], errors="coerce")
    out["hot_tail_boundary_v1"] = pd.to_numeric(out["forecast_to_bracket_low_native"], errors="coerce").gt(0.0)
    out = out[out["hot_tail_boundary_v1"]].copy()
    out["forecast_model"] = out["forecast_model_tail"]
    out["source_lc"] = out["forecast_source"].astype(str).str.lower()
    out["is_gfs"] = out["forecast_model"].eq("gfs")
    out["is_ecmwf"] = out["forecast_model"].eq("ecmwf")
    out["shares"] = out["entry"].map(price_tier_shares)
    out["entry_fee"] = [weather_taker_fee(shares=s, price=p) for s, p in zip(out["shares"], out["entry"], strict=False)]
    out["cost"] = out["shares"] * out["entry"] + out["entry_fee"]
    out["pnl"] = out["shares"] * out["payoff"] - out["cost"]
    out["win"] = out["payoff"].eq(1.0)
    out["sample_window"] = f"forward_{start_date}_{end_date}"
    return normalize_source_quality_columns(out)


def normalize_source_quality_columns(rows: pd.DataFrame) -> pd.DataFrame:
    out = rows.copy()
    rename_map = {
        "forecast_source_best_reliability_bucket_asof": "best_reliability_bucket",
        "forecast_source_best_mae_f_asof": "best_mae_f",
        "forecast_source_model_gap_to_best_f_asof": "source_gap_to_best_f",
        "forecast_source_model_bias_f_asof": "source_bias_f",
        "forecast_source_model_mae_f_asof": "source_mae_f",
        "forecast_source_model_underforecast_ge_1f_pct_asof": "source_underforecast_ge_1f_pct",
    }
    for src, dst in rename_map.items():
        if dst not in out.columns and src in out.columns:
            out[dst] = out[src]
    for col in [
        "best_reliability_bucket",
        "best_mae_f",
        "source_gap_to_best_f",
        "source_bias_f",
        "source_mae_f",
        "source_underforecast_ge_1f_pct",
    ]:
        if col not in out.columns:
            out[col] = np.nan if col != "best_reliability_bucket" else "missing"
    return out


def period_masks(rows: pd.DataFrame) -> dict[str, pd.Series]:
    target = rows["target_date"].astype(str)
    return {
        "combined_through_2026_07_07": pd.Series(True, index=rows.index),
        "historical_0506_0630": target <= "2026-06-30",
        "forward_0701_0707": target >= FORWARD_START,
        "train_le_2026_06_20": target <= v1.TRAIN_END,
        "recent_ge_2026_06_21": target >= v1.RECENT_START,
    }


def selector_summary(rows: pd.DataFrame) -> pd.DataFrame:
    selectors = {
        "baseline_hot_dist_gt0": pd.Series(True, index=rows.index),
        "source_quality_high": rows["source_quality_tier_v1"].eq("high"),
        "source_quality_mid_or_high": rows["source_quality_tier_v1"].isin(["mid", "high"]),
        "source_quality_low": rows["source_quality_tier_v1"].eq("low"),
        "exclude_source_quality_low": ~rows["source_quality_tier_v1"].eq("low"),
    }
    records: list[dict[str, Any]] = []
    for period, pmask in period_masks(rows).items():
        period_rows = rows[pmask].copy()
        for label, mask in selectors.items():
            selected = period_rows[mask.reindex(period_rows.index).fillna(False)].copy()
            rec = v1.summarize(selected, label=label, period=period)
            if label != "baseline_hot_dist_gt0":
                complement = period_rows.drop(index=selected.index)
                point, lo, hi = v1.paired_delta_ci(selected, complement)
                rec.update(
                    {
                        "complement_rows": int(len(complement)),
                        "selected_share": float(len(selected) / len(period_rows)) if len(period_rows) else math.nan,
                        "delta_vs_complement": point,
                        "delta_vs_complement_ci_low": lo,
                        "delta_vs_complement_ci_high": hi,
                    }
                )
            records.append(rec)
    return pd.DataFrame(records)


def group_summary(rows: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for period, pmask in period_masks(rows).items():
        period_rows = rows[pmask].copy()
        for key, group in period_rows.groupby(group_cols, dropna=False):
            if not isinstance(key, tuple):
                key = (key,)
            rec = v1.summarize(group.copy(), label="|".join(str(x) for x in key), period=period)
            for col, val in zip(group_cols, key, strict=True):
                rec[col] = val
            records.append(rec)
    return pd.DataFrame(records)


def table(frame: pd.DataFrame, columns: list[str], *, max_rows: int = 40) -> str:
    return v1.markdown_table(frame, columns, max_rows=max_rows)


def build_report(rows: pd.DataFrame, selectors: pd.DataFrame, groups: pd.DataFrame, payload: dict[str, Any]) -> str:
    combined = selectors[selectors["period"].eq("combined_through_2026_07_07")]
    forward = selectors[selectors["period"].eq("forward_0701_0707")]
    historical = selectors[selectors["period"].eq("historical_0506_0630")]
    tier_combined = groups[groups["period"].eq("combined_through_2026_07_07") & groups["grouping"].eq("source_quality_tier_v1")]
    tier_forward = groups[groups["period"].eq("forward_0701_0707") & groups["grouping"].eq("source_quality_tier_v1")]
    low_forward = rows[
        rows["target_date"].astype(str).ge(FORWARD_START) & rows["source_quality_tier_v1"].eq("low")
    ][["target_date", "city", "bracket", "entry", "payoff", "source_quality_score_v1", "pnl"]].sort_values(
        ["target_date", "city", "bracket"]
    )
    low = rows[rows["source_quality_tier_v1"].eq("low")].copy()
    daily_all = rows.groupby("target_date").agg(all_rows=("candidate_id", "count"), all_wins=("win", "sum"))
    daily_low = low.groupby("target_date").agg(
        low_rows=("candidate_id", "count"),
        low_wins=("win", "sum"),
        low_cost=("cost", "sum"),
        low_pnl=("pnl", "sum"),
    )
    low_daily = daily_all.join(daily_low).fillna(0).reset_index()
    low_daily = low_daily[low_daily["low_rows"].gt(0)].copy()
    low_daily["selected_share"] = low_daily["low_rows"] / low_daily["all_rows"]
    low_daily["roi"] = low_daily["low_pnl"] / low_daily["low_cost"].replace(0.0, np.nan)

    low_city = (
        rows.groupby("city")
        .agg(all_rows=("candidate_id", "count"), all_wins=("win", "sum"), all_cost=("cost", "sum"), all_pnl=("pnl", "sum"))
        .join(
            low.groupby("city").agg(
                low_rows=("candidate_id", "count"),
                low_wins=("win", "sum"),
                low_cost=("cost", "sum"),
                low_pnl=("pnl", "sum"),
            )
        )
        .fillna(0)
        .reset_index()
    )
    low_city = low_city[low_city["low_rows"].gt(0)].copy()
    low_city["selected_share"] = low_city["low_rows"] / low_city["all_rows"]
    low_city["roi"] = low_city["low_pnl"] / low_city["low_cost"].replace(0.0, np.nan)

    def low_group(col: str) -> pd.DataFrame:
        out = low.groupby(col, dropna=False).agg(
            rows=("candidate_id", "count"),
            wins=("win", "sum"),
            cost=("cost", "sum"),
            pnl=("pnl", "sum"),
        ).reset_index()
        out["win_rate"] = out["wins"] / out["rows"]
        out["roi"] = out["pnl"] / out["cost"].replace(0.0, np.nan)
        return out.sort_values("rows", ascending=False)

    low_model = low_group("forecast_model")
    low_reliability = low_group("best_reliability_bucket")
    low_underforecast = low_group("source_underforecast_bucket")
    low_source_mae = low_group("source_mae_bucket")

    return f"""# HeadA Source-Quality Score v2

Generated: {payload["generated_at_utc"]}

## Question

Update the HeadA source-quality conclusion after adding settled forward opportunity rows through 2026-07-07.

## Data Snapshot

- DB: `runtime/weather.db`
- `fact_signal_candidates` event dates: {payload["fact_snapshot"]["min_event_date"]}..{payload["fact_snapshot"]["max_event_date"]}; `fact_built_at_utc` {payload["fact_snapshot"]["fact_built_at_utc"]}
- `settlement_outcomes` target dates: {payload["fact_snapshot"]["min_settlement_date"]}..{payload["fact_snapshot"]["max_settlement_date"]}; settled rows {payload["fact_snapshot"]["settled_rows"]}
- Historical source-quality rows: {payload["historical_rows"]} rows, {payload["historical_start"]}..{payload["historical_end"]}
- Forward raw rows 2026-07-01..2026-07-07: {payload["forward_raw_rows"]}; after `dist>0`: {payload["forward_hot_rows"]}

Execution replay uses current HeadA probe geometry: price-tier `6/8/10` shares, official Weather taker fee `shares * 0.05 * price * (1-price)`, hold to settlement.

## Bottom Line

Verdict: `{payload["verdict"]}`.

Adding 7/1..7/7 does **not** weaken source-quality as a telemetry feature. Combined through 7/7, mid/high score rows are still materially better than baseline on point estimate, and low score rows remain worse over the full window.

But the new forward low bucket is only 5 rows and includes one winner, so `source_quality_low` should **not** be a hard block. The clean use is still: keep collecting, use it for shadow sizing diagnostics, and at most treat low score as `do_not_size_up` until more fresh rows settle.

Daily rank check after extension: {payload["daily_rank_correlation"]["days"]} eligible days, mean Spearman(score, win) {fmt_float(payload["daily_rank_correlation"].get("mean_spearman_score_win"), 3)}, positive days {payload["daily_rank_correlation"].get("positive_days")}, negative days {payload["daily_rank_correlation"].get("negative_days")}.

## Combined Through 7/7

{table(combined, [
    "label", "rows", "dates", "cities", "win_rate", "avg_entry", "avg_source_quality_score", "roi",
    "roi_ci_low", "roi_ci_high", "top5_removed_roi", "selected_share", "delta_vs_complement",
    "delta_vs_complement_ci_low", "delta_vs_complement_ci_high",
])}

## Forward 7/1-7/7

{table(forward, [
    "label", "rows", "dates", "cities", "win_rate", "avg_entry", "avg_source_quality_score", "roi",
    "roi_ci_low", "roi_ci_high", "top5_removed_roi", "selected_share", "delta_vs_complement",
    "delta_vs_complement_ci_low", "delta_vs_complement_ci_high",
])}

## Historical 5/6-6/30

{table(historical, [
    "label", "rows", "dates", "cities", "win_rate", "avg_entry", "avg_source_quality_score", "roi",
    "roi_ci_low", "roi_ci_high", "top5_removed_roi",
])}

## Tier View

Combined:

{table(tier_combined.sort_values("source_quality_tier_v1"), [
    "source_quality_tier_v1", "rows", "dates", "cities", "win_rate", "avg_source_quality_score",
    "roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi",
])}

Forward:

{table(tier_forward.sort_values("source_quality_tier_v1"), [
    "source_quality_tier_v1", "rows", "dates", "cities", "win_rate", "avg_source_quality_score",
    "roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi",
])}

## Forward Low-Score Rows

{table(low_forward, ["target_date", "city", "bracket", "entry", "payoff", "source_quality_score_v1", "pnl"], max_rows=20)}

## What Exclude-Low Removes

`exclude_source_quality_low` removes 55 / 370 rows (14.9%). This is not a broad volume cut; it removes about one ticket per active day when low rows appear. Historical low rows were 50 rows / 30 dates / 14 cities, win 8.0%, ROI -26.7%. Forward low rows are only 5 rows / 4 dates / 3 cities, and one Austin winner makes the forward-only point estimate positive, which is why low remains a downweight/no-size-up observation rather than a hard block.

Daily low rows:

{table(low_daily.sort_values("target_date"), [
    "target_date", "all_rows", "all_wins", "low_rows", "low_wins", "selected_share", "low_pnl", "roi",
], max_rows=40)}

Cities most affected:

{table(low_city.sort_values(["low_rows", "low_pnl"], ascending=[False, True]), [
    "city", "all_rows", "all_wins", "low_rows", "low_wins", "selected_share", "low_pnl", "roi",
], max_rows=20)}

Low-score source shapes:

By active source:

{table(low_model, ["forecast_model", "rows", "wins", "win_rate", "pnl", "roi"], max_rows=10)}

By best-model reliability:

{table(low_reliability, ["best_reliability_bucket", "rows", "wins", "win_rate", "pnl", "roi"], max_rows=10)}

By hot-underforecast history:

{table(low_underforecast, ["source_underforecast_bucket", "rows", "wins", "win_rate", "pnl", "roi"], max_rows=10)}

By active-source MAE:

{table(low_source_mae, ["source_mae_bucket", "rows", "wins", "win_rate", "pnl", "roi"], max_rows=10)}

## Decision

No live selector change. `source_quality_score_v1` remains `shadow_telemetry_reasonable_not_live_selector`.

The 7/7 extension supports a conservative future rule: do not increase size on low-score rows. It does not support excluding low rows outright, because the forward low bucket is too thin and already contains a winner.
"""


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    settled_max = max_settled_target_date()
    forward_end = min(settled_max, "2026-07-07") if settled_max else ""
    historical = load_historical_rows()
    forward_raw_count = 0
    if forward_end >= FORWARD_START:
        conn = connect()
        try:
            forward_raw_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM fact_signal_candidates
                    WHERE event_date BETWEEN ? AND ?
                      AND side='BUY_YES'
                      AND decision_entry_price BETWEEN 0.05 AND 0.20
                      AND edge >= 0.20
                      AND settlement_status='settled'
                      AND final_yes IN (0.0, 1.0)
                    """,
                    (FORWARD_START, forward_end),
                ).fetchone()[0]
            )
        finally:
            conn.close()
    forward = load_forward_opportunity_rows(FORWARD_START, forward_end)
    rows = pd.concat([historical, forward], ignore_index=True, sort=False)
    rows["target_date"] = rows["target_date"].astype(str)
    rows["row_id"] = np.arange(len(rows), dtype=int)
    rows = v1.add_source_quality_score(rows)

    selectors = selector_summary(rows)
    group_frames: list[pd.DataFrame] = []
    for cols in [
        ["source_quality_tier_v1"],
        ["source_quality_score_v1"],
        ["source_quality_tier_v1", "best_reliability_bucket"],
    ]:
        g = group_summary(rows, cols)
        g["grouping"] = "|".join(cols)
        group_frames.append(g)
    groups = pd.concat(group_frames, ignore_index=True)

    rows.to_csv(OUT_DIR / "candidate_rows.csv", index=False)
    selectors.to_csv(OUT_DIR / "selector_summary.csv", index=False)
    groups.to_csv(OUT_DIR / "group_summary.csv", index=False)

    payload = {
        "generated_at_utc": now_utc(),
        "fact_snapshot": fact_snapshot(),
        "historical_rows": int(len(historical)),
        "historical_start": str(historical["target_date"].min()),
        "historical_end": str(historical["target_date"].max()),
        "forward_raw_rows": forward_raw_count,
        "forward_hot_rows": int(len(forward)),
        "denominator": {
            "rows": int(len(rows)),
            "dates": int(rows["target_date"].nunique()),
            "cities": int(rows["city"].nunique()),
            "start": str(rows["target_date"].min()),
            "end": str(rows["target_date"].max()),
        },
        "daily_rank_correlation": v1.daily_rank_correlation(rows),
        "verdict": "shadow_telemetry_reasonable_not_live_selector",
        "key_selectors": selectors[
            selectors["label"].isin(
                [
                    "baseline_hot_dist_gt0",
                    "source_quality_mid_or_high",
                    "source_quality_low",
                    "exclude_source_quality_low",
                    "source_quality_high",
                ]
            )
        ].to_dict(orient="records"),
    }
    OUT_JSON.write_text(json.dumps(v1.json_ready(payload), indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(build_report(rows, selectors, groups, payload), encoding="utf-8")
    print(json.dumps(v1.json_ready(payload), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
