#!/usr/bin/env python3
"""Candidate-policy shadow for tmax distribution edge.

This is zero-notional only. It reselects clean_edge02 opportunities from the P6
shadow event ledger after applying executable policy knobs such as ask floor and
fixed shares. Reselecting matters: if the first city-day edge pass is below the
ask floor, a later same-city-day row may become the first executable candidate.

Daily gross caps are reported only as diagnostics. They are not a recommended
selector because time-ordered caps systematically favor early-clock/early-timezone
cities and can turn an alpha test into an execution-order artifact.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DEFAULT = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_p6_shadow_telemetry_v1/shadow_events.csv"
RUNTIME_DEFAULT = ROOT / "runtime/weather_edge_v1/tmax_distribution_edge_candidate_shadow_v1"
STRATEGY_INSTANCE = "tmax_distribution_edge_candidate_shadow_v1"
STRATEGY_FAMILY = "reheat_risk.tmax_distribution_edge"
TREND3H_FLAT_LOW_DEFAULT = -0.5
TREND3H_FLAT_HIGH_DEFAULT = 0.5


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def json_ready(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    return value


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(json_ready(payload), ensure_ascii=False, sort_keys=True) + "\n")


def parse_cap(value: str) -> float | None:
    if value.lower() in {"none", "null", "na", "n/a", "0"}:
        return None
    return float(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "loop"], nargs="?", default="run")
    parser.add_argument("--source", default=str(SOURCE_DEFAULT))
    parser.add_argument("--runtime-dir", default=str(RUNTIME_DEFAULT))
    parser.add_argument("--config-id", default="tmax_dist_clean_edge02")
    parser.add_argument("--scope", action="append", default=[], help="Repeatable. Default: verified_forward.")
    parser.add_argument("--include-dev-cv", action="store_true")
    parser.add_argument("--edge-threshold", type=float, default=0.02)
    parser.add_argument("--ask-floor", type=float, action="append", default=[])
    parser.add_argument("--ask-ceiling", type=float, default=0.99)
    parser.add_argument("--exclude-trend3h-flat", action="store_true")
    parser.add_argument("--block-missing-trend3h", action="store_true", default=True)
    parser.add_argument("--allow-missing-trend3h", action="store_false", dest="block_missing_trend3h")
    parser.add_argument("--trend3h-flat-low", type=float, default=TREND3H_FLAT_LOW_DEFAULT)
    parser.add_argument("--trend3h-flat-high", type=float, default=TREND3H_FLAT_HIGH_DEFAULT)
    parser.add_argument("--diagnostic-daily-cap-usd", action="append", default=[])
    parser.add_argument("--fixed-shares", type=float, default=5.0)
    parser.add_argument("--default-ask-floor", type=float, default=0.20)
    parser.add_argument("--interval-seconds", type=float, default=900.0)
    return parser.parse_args()


def load_source(path: Path, args: argparse.Namespace) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"missing source CSV: {path}")
    df = pd.read_csv(path)
    required = {
        "shadow_config_id",
        "scope",
        "city",
        "target_date",
        "decision_hour_local",
        "chosen_expression",
        "ask",
        "p_win",
        "model_edge",
        "model_roi",
        "win",
        "unit_pnl",
    }
    missing = sorted(required - set(df.columns))
    if missing:
        raise RuntimeError(f"source missing columns: {missing}")
    df = df[df["shadow_config_id"].eq(args.config_id)].copy()
    if not args.include_dev_cv:
        df = df[~df["scope"].eq("dev_cv")].copy()
    scopes = set(args.scope or ["verified_forward"])
    if scopes:
        df = df[df["scope"].isin(scopes)].copy()
    for col in ["ask", "p_win", "model_edge", "model_roi", "win", "unit_pnl", "decision_hour_local", "temp_trend_3h_f"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["ask", "model_edge", "decision_hour_local"])


def trend3h_flat_mask(df: pd.DataFrame, *, low: float, high: float) -> pd.Series:
    if "temp_trend_3h_f" not in df.columns:
        return pd.Series([False] * len(df), index=df.index)
    trend = pd.to_numeric(df["temp_trend_3h_f"], errors="coerce")
    return trend.ge(float(low)) & trend.lt(float(high))


def select_policy(
    df: pd.DataFrame,
    *,
    ask_floor: float,
    ask_ceiling: float,
    edge_threshold: float,
    fixed_shares: float,
    exclude_trend3h_flat: bool,
    block_missing_trend3h: bool,
    trend3h_flat_low: float,
    trend3h_flat_high: float,
) -> pd.DataFrame:
    missing_trend3h = (
        pd.to_numeric(df["temp_trend_3h_f"], errors="coerce").isna()
        if "temp_trend_3h_f" in df.columns
        else pd.Series([True] * len(df), index=df.index)
    )
    flat_trend3h = trend3h_flat_mask(df, low=trend3h_flat_low, high=trend3h_flat_high)
    eligible = df[
        (df["model_edge"] >= edge_threshold)
        & (df["ask"] >= ask_floor)
        & (df["ask"] <= ask_ceiling)
        & (~flat_trend3h if exclude_trend3h_flat else True)
        & (~missing_trend3h if exclude_trend3h_flat and block_missing_trend3h else True)
    ].copy()
    if eligible.empty:
        return eligible
    eligible = eligible.sort_values(
        ["scope", "target_date", "city", "decision_hour_local", "model_edge", "model_roi"],
        ascending=[True, True, True, True, False, False],
    )
    first_city_day = eligible.groupby(["scope", "city", "target_date"], as_index=False).head(1).copy()
    first_city_day = first_city_day.sort_values(["scope", "target_date", "decision_hour_local", "city"]).copy()
    first_city_day["policy_shares"] = float(fixed_shares)
    first_city_day["policy_cost"] = first_city_day["policy_shares"] * first_city_day["ask"]
    first_city_day["policy_pnl"] = first_city_day["policy_shares"] * first_city_day["unit_pnl"]
    first_city_day["policy_selected"] = True
    first_city_day["policy_block_reason"] = None
    return first_city_day


def blocked_rows_for_policy(
    df: pd.DataFrame,
    selected: pd.DataFrame,
    *,
    ask_floor: float,
    ask_ceiling: float,
    edge_threshold: float,
    fixed_shares: float,
    exclude_trend3h_flat: bool,
    block_missing_trend3h: bool,
    trend3h_flat_low: float,
    trend3h_flat_high: float,
) -> pd.DataFrame:
    rows = df.copy()
    rows["policy_block_reason"] = None
    rows.loc[rows["model_edge"] < edge_threshold, "policy_block_reason"] = "below_edge_threshold"
    rows.loc[rows["ask"] < ask_floor, "policy_block_reason"] = "below_ask_floor"
    rows.loc[rows["ask"] > ask_ceiling, "policy_block_reason"] = "above_ask_ceiling"
    if exclude_trend3h_flat:
        missing = (
            pd.to_numeric(rows["temp_trend_3h_f"], errors="coerce").isna()
            if "temp_trend_3h_f" in rows.columns
            else pd.Series([True] * len(rows), index=rows.index)
        )
        flat = trend3h_flat_mask(rows, low=trend3h_flat_low, high=trend3h_flat_high)
        rows.loc[flat, "policy_block_reason"] = "trend3h_flat"
        if block_missing_trend3h:
            rows.loc[missing, "policy_block_reason"] = "trend3h_missing"
    selected_keys = set()
    if not selected.empty:
        selected_keys = {
            (r.scope, r.city, r.target_date, r.decision_hour_local, r.chosen_expression)
            for r in selected[["scope", "city", "target_date", "decision_hour_local", "chosen_expression"]].itertuples(index=False)
        }
    edge_price_ok = rows[
        (rows["policy_block_reason"].isna())
        & (rows["model_edge"] >= edge_threshold)
        & (rows["ask"] >= ask_floor)
        & (rows["ask"] <= ask_ceiling)
    ].copy()
    if not edge_price_ok.empty:
        selected_city_days = set()
        if not selected.empty:
            selected_city_days = {(r.scope, r.city, r.target_date) for r in selected[["scope", "city", "target_date"]].itertuples(index=False)}
        mask_after_first = edge_price_ok.apply(lambda r: (r["scope"], r["city"], r["target_date"]) in selected_city_days, axis=1)
        idx = edge_price_ok[mask_after_first].index
        rows.loc[idx, "policy_block_reason"] = "city_day_after_first_selected"
    if selected_keys:
        selected_idx = rows.apply(
            lambda r: (r["scope"], r["city"], r["target_date"], r["decision_hour_local"], r["chosen_expression"]) in selected_keys,
            axis=1,
        )
        rows.loc[selected_idx, "policy_block_reason"] = None
    blocked = rows[rows["policy_block_reason"].notna()].copy()
    if blocked.empty:
        return blocked
    blocked["policy_shares"] = float(fixed_shares)
    blocked["policy_cost"] = blocked["policy_shares"] * blocked["ask"]
    blocked["policy_pnl"] = blocked["policy_shares"] * blocked["unit_pnl"]
    blocked["policy_selected"] = False
    return blocked


def cap_diagnostic(rows: pd.DataFrame, cap: float) -> dict[str, Any]:
    """Report what a naive time-ordered cap would do; do not use as selector."""
    selected_flags: list[bool] = []
    for _, day in rows.groupby(["scope", "target_date"], sort=False):
        spent = 0.0
        for _, row in day.sort_values(["decision_hour_local", "city"]).iterrows():
            cost = float(row["policy_cost"])
            if spent + cost <= cap + 1e-9:
                selected_flags.append(True)
                spent += cost
            else:
                selected_flags.append(False)
    if len(selected_flags) != len(rows):
        return {"daily_cap_usd": cap, "error": "diagnostic_alignment_failed"}
    capped = rows.copy()
    capped["_cap_selected"] = selected_flags
    sel = capped[capped["_cap_selected"].eq(True)]
    cost = float(sel["policy_cost"].sum()) if not sel.empty else 0.0
    pnl = float(sel["policy_pnl"].sum()) if not sel.empty else 0.0
    return {
        "daily_cap_usd": cap,
        "selected_rows": int(len(sel)),
        "blocked_rows": int(len(rows) - len(sel)),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "daily_rows_avg": float(sel.groupby("target_date").size().mean()) if not sel.empty else 0.0,
        "note": "diagnostic_only_time_ordered_cap_biases_early_timezones",
    }


def summarize_policy(
    rows: pd.DataFrame,
    *,
    ask_floor: float,
    ask_ceiling: float,
    edge_threshold: float,
    fixed_shares: float,
    exclude_trend3h_flat: bool,
    block_missing_trend3h: bool,
    trend3h_flat_low: float,
    trend3h_flat_high: float,
    diagnostic_caps: list[float],
) -> dict[str, Any]:
    selected = rows[rows["policy_selected"].eq(True)].copy() if "policy_selected" in rows.columns else rows.iloc[0:0].copy()
    cost = float(selected["policy_cost"].sum()) if not selected.empty else 0.0
    pnl = float(selected["policy_pnl"].sum()) if not selected.empty else 0.0
    by_day = []
    if not selected.empty:
        daily = selected.groupby("target_date", as_index=False).agg(
            rows=("city", "count"),
            cities=("city", "nunique"),
            cost=("policy_cost", "sum"),
            pnl=("policy_pnl", "sum"),
            wins=("win", "sum"),
            avg_ask=("ask", "mean"),
            min_ask=("ask", "min"),
            max_ask=("ask", "max"),
        )
        daily["roi"] = daily["pnl"] / daily["cost"]
        daily["win_rate"] = daily["wins"] / daily["rows"]
        by_day = daily.round(6).to_dict("records")
    return {
        "policy_id": (
            f"clean_edge02_edge{edge_threshold:.2f}_ask{ask_floor:.2f}_"
            f"{'no3hflat_' if exclude_trend3h_flat else ''}fixed{fixed_shares:g}shares"
        ),
        "selection_rule": "first_eligible_per_city_day_after_edge_ask_floor_and_mechanism_filters",
        "daily_cap_policy": "none",
        "ask_floor": ask_floor,
        "ask_ceiling": ask_ceiling,
        "edge_threshold": edge_threshold,
        "fixed_shares": fixed_shares,
        "exclude_trend3h_flat": bool(exclude_trend3h_flat),
        "block_missing_trend3h": bool(block_missing_trend3h),
        "trend3h_flat_low": trend3h_flat_low,
        "trend3h_flat_high": trend3h_flat_high,
        "eligible_city_day_rows": int(len(rows)),
        "selected_rows": int(len(selected)),
        "dates": int(selected["target_date"].nunique()) if not selected.empty else 0,
        "cities": int(selected["city"].nunique()) if not selected.empty else 0,
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "win_rate": float(selected["win"].mean()) if not selected.empty else None,
        "avg_order_cost": float(selected["policy_cost"].mean()) if not selected.empty else None,
        "avg_ask": float(selected["ask"].mean()) if not selected.empty else None,
        "daily_rows_avg": float(pd.Series([r["rows"] for r in by_day]).mean()) if by_day else None,
        "daily_rows_min": int(min([r["rows"] for r in by_day])) if by_day else 0,
        "daily_rows_max": int(max([r["rows"] for r in by_day])) if by_day else 0,
        "diagnostic_daily_caps": [cap_diagnostic(rows, cap) for cap in diagnostic_caps] if not rows.empty else [],
        "daily": by_day,
    }


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.source)
    runtime_dir = Path(args.runtime_dir)
    ask_floors = args.ask_floor or [0.05, 0.10, 0.20, 0.25, 0.40]
    diagnostic_caps = [
        cap for cap in (parse_cap(v) for v in (args.diagnostic_daily_cap_usd or ["10", "15", "20"]))
        if cap is not None
    ]

    df = load_source(source, args)
    source_stat = source.stat()
    policy_summaries: list[dict[str, Any]] = []
    default_events = pd.DataFrame()
    for ask_floor in ask_floors:
        rows = select_policy(
            df,
            ask_floor=float(ask_floor),
            ask_ceiling=float(args.ask_ceiling),
            edge_threshold=float(args.edge_threshold),
            fixed_shares=float(args.fixed_shares),
            exclude_trend3h_flat=bool(args.exclude_trend3h_flat),
            block_missing_trend3h=bool(args.block_missing_trend3h),
            trend3h_flat_low=float(args.trend3h_flat_low),
            trend3h_flat_high=float(args.trend3h_flat_high),
        )
        summary = summarize_policy(
            rows,
            ask_floor=float(ask_floor),
            ask_ceiling=float(args.ask_ceiling),
            edge_threshold=float(args.edge_threshold),
            fixed_shares=float(args.fixed_shares),
            exclude_trend3h_flat=bool(args.exclude_trend3h_flat),
            block_missing_trend3h=bool(args.block_missing_trend3h),
            trend3h_flat_low=float(args.trend3h_flat_low),
            trend3h_flat_high=float(args.trend3h_flat_high),
            diagnostic_caps=diagnostic_caps,
        )
        policy_summaries.append(summary)
        if math.isclose(float(ask_floor), float(args.default_ask_floor)):
            default_events = rows[rows["policy_selected"].eq(True)].copy() if not rows.empty else rows

    latest_events = []
    latest_blocked = []
    if not default_events.empty:
        keep_cols = [
            "scope",
            "city",
            "target_date",
            "decision_hour_local",
            "chosen_expression",
            "ask",
            "p_win",
            "model_edge",
            "model_roi",
            "win",
            "unit_pnl",
            "policy_shares",
            "policy_cost",
            "policy_pnl",
            "day_regime",
            "intraday_state",
            "wind_regime",
            "moisture_cloud_regime",
            "temp_trend_3h_f",
            "policy_block_reason",
        ]
        latest_events = default_events[[c for c in keep_cols if c in default_events.columns]].round(6).to_dict("records")
    default_blocked = blocked_rows_for_policy(
        df,
        default_events,
        ask_floor=float(args.default_ask_floor),
        ask_ceiling=float(args.ask_ceiling),
        edge_threshold=float(args.edge_threshold),
        fixed_shares=float(args.fixed_shares),
        exclude_trend3h_flat=bool(args.exclude_trend3h_flat),
        block_missing_trend3h=bool(args.block_missing_trend3h),
        trend3h_flat_low=float(args.trend3h_flat_low),
        trend3h_flat_high=float(args.trend3h_flat_high),
    )
    if not default_blocked.empty:
        keep_block_cols = [
            "scope",
            "city",
            "target_date",
            "decision_hour_local",
            "chosen_expression",
            "ask",
            "p_win",
            "model_edge",
            "model_roi",
            "win",
            "unit_pnl",
            "policy_shares",
            "policy_cost",
            "policy_pnl",
            "policy_block_reason",
            "day_regime",
            "intraday_state",
            "running_max_state",
            "wind_regime",
            "moisture_cloud_regime",
            "forecast_source",
            "temp_trend_3h_f",
        ]
        latest_blocked = default_blocked[[c for c in keep_block_cols if c in default_blocked.columns]].round(6).to_dict("records")
    payload = {
        "generated_at_utc": utc_now(),
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_family": STRATEGY_FAMILY,
        "execution_mode": "zero_notional_shadow",
        "no_order_placed": True,
        "source_artifact": rel(source),
        "source_mtime_utc": datetime.fromtimestamp(source_stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        "source_rows": int(len(df)),
        "config_id": args.config_id,
        "scopes": sorted(df["scope"].dropna().astype(str).unique().tolist()),
        "target_dates": sorted(df["target_date"].dropna().astype(str).unique().tolist()),
        "edge_threshold": float(args.edge_threshold),
        "ask_ceiling": float(args.ask_ceiling),
        "exclude_trend3h_flat": bool(args.exclude_trend3h_flat),
        "block_missing_trend3h": bool(args.block_missing_trend3h),
        "trend3h_flat_low": float(args.trend3h_flat_low),
        "trend3h_flat_high": float(args.trend3h_flat_high),
        "default_policy": {
            "ask_floor": float(args.default_ask_floor),
            "fixed_shares": float(args.fixed_shares),
            "event_count": len(latest_events),
            "blocked_count": len(latest_blocked),
            "events_path": rel(runtime_dir / "latest_events.json"),
            "blocked_path": rel(runtime_dir / "latest_blocked.json"),
        },
        "policy_summaries": policy_summaries,
    }
    write_json(runtime_dir / "latest_summary.json", payload)
    write_json(runtime_dir / "latest_events.json", {"generated_at_utc": payload["generated_at_utc"], "events": latest_events})
    write_json(runtime_dir / "latest_blocked.json", {"generated_at_utc": payload["generated_at_utc"], "events": latest_blocked})
    append_jsonl(runtime_dir / "summary_history.jsonl", payload)
    compact = {
        **{k: payload[k] for k in [
            "generated_at_utc",
            "strategy_instance",
            "execution_mode",
            "no_order_placed",
            "source_artifact",
            "source_mtime_utc",
            "source_rows",
            "config_id",
            "scopes",
            "target_dates",
            "edge_threshold",
            "ask_ceiling",
            "default_policy",
        ]},
        "policy_summaries": [
            {k: v for k, v in item.items() if k != "daily"}
            for item in policy_summaries
        ],
    }
    print(json.dumps(json_ready(compact), ensure_ascii=False, sort_keys=True))
    return payload


def main() -> int:
    args = parse_args()
    if args.command == "run":
        run_once(args)
        return 0
    while True:
        try:
            run_once(args)
        except Exception as exc:  # noqa: BLE001
            runtime_dir = Path(args.runtime_dir)
            append_jsonl(
                runtime_dir / "summary_history.jsonl",
                {
                    "generated_at_utc": utc_now(),
                    "strategy_instance": STRATEGY_INSTANCE,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "no_order_placed": True,
                },
            )
            print(f"error: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(float(args.interval_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
