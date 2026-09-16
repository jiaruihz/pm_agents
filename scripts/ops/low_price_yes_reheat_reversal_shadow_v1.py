#!/usr/bin/env python3
"""Append low-price YES reheat-reversal candidates to a zero-notional journal.

This runner does not submit, sign, or place orders.  It consumes the v1 scored
feature rows and records only would-order decision fields for forward shadow
evaluation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
SCORED_ROWS_DEFAULT = ROOT / "docs/analysis/2026-06/generated/low_price_yes_reheat_reversal_v1/scored_rows.csv"
JOURNAL_DEFAULT = ROOT / "runtime/weather_edge_v1/low_price_yes_reheat_reversal_v1/shadow_candidates.jsonl"
SUMMARY_DEFAULT = ROOT / "runtime/weather_edge_v1/low_price_yes_reheat_reversal_v1/shadow_summary.json"

STRATEGY_ID = "low_price_yes_reheat_reversal_shadow_v1"
RULE_ID = "d1d2_adjusted_edge_ge_008_ask_le_025_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scored-rows", default=str(SCORED_ROWS_DEFAULT))
    parser.add_argument("--journal", default=str(JOURNAL_DEFAULT))
    parser.add_argument("--summary", default=str(SUMMARY_DEFAULT))
    parser.add_argument("--min-target-date", default=None, help="Default: max target_date in scored rows, for forward-like appends.")
    parser.add_argument("--max-target-date", default=None)
    parser.add_argument("--min-edge", type=float, default=0.08)
    parser.add_argument("--max-ask", type=float, default=0.25)
    parser.add_argument("--distance-buckets", default="d1,d2")
    parser.add_argument("--exclude-deep-fade", action="store_true")
    parser.add_argument("--hypothetical-notional-usd", type=float, default=2.0)
    parser.add_argument("--max-candidates-per-run", type=int, default=200)
    return parser.parse_args()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_ready(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def existing_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("shadow_decision_id"):
                ids.add(str(row["shadow_decision_id"]))
    return ids


def decision_id(row: pd.Series) -> str:
    raw = "|".join(
        [
            RULE_ID,
            str(row["target_date"]),
            str(row["city"]),
            str(row["decision_snapshot_ts_utc"]),
            str(row["decision_hour_local"]),
            str(row["bracket"]),
            str(row.get("token_id", "")),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def load_and_filter(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = Path(args.scored_rows)
    df = pd.read_csv(path)
    df["target_date"] = df["target_date"].astype(str)
    buckets = {item.strip() for item in args.distance_buckets.split(",") if item.strip()}
    min_target_date = args.min_target_date or str(df["target_date"].max())
    mask = (
        df["target_yes_ask"].le(args.max_ask)
        & df["target_distance_native"].gt(0)
        & df["distance_bucket"].isin(buckets)
        & df["reheat_adjusted_edge"].ge(args.min_edge)
        & df["target_date"].ge(min_target_date)
    )
    if args.max_target_date:
        mask &= df["target_date"].le(args.max_target_date)
    if args.exclude_deep_fade:
        mask &= ~df["deep_fade_state"].astype(bool)
    selected = df[mask].copy()
    selected = selected.sort_values(
        ["target_date", "decision_snapshot_ts_utc", "city", "reheat_adjusted_edge"],
        ascending=[True, True, True, False],
    ).head(args.max_candidates_per_run)
    meta = {
        "source_rows": int(len(df)),
        "source_min_target_date": str(df["target_date"].min()),
        "source_max_target_date": str(df["target_date"].max()),
        "effective_min_target_date": min_target_date,
        "effective_max_target_date": args.max_target_date,
        "selected_rows_before_dedupe": int(len(selected)),
    }
    return selected, meta


def journal_row(row: pd.Series, args: argparse.Namespace) -> dict[str, Any]:
    ask = float(row["target_yes_ask"])
    notional = float(args.hypothetical_notional_usd)
    return {
        "record_type": "low_price_yes_reheat_reversal_shadow_candidate",
        "journal_schema_version": 1,
        "strategy_id": STRATEGY_ID,
        "rule_id": RULE_ID,
        "shadow_decision_id": decision_id(row),
        "created_at_utc": now_utc(),
        "execution_mode": "zero_notional_shadow",
        "no_order_placed": True,
        "city": json_ready(row["city"]),
        "target_date": json_ready(row["target_date"]),
        "decision_snapshot_ts_utc": json_ready(row["decision_snapshot_ts_utc"]),
        "decision_hour_local": int(row["decision_hour_local"]),
        "decision_timezone": json_ready(row.get("timezone")),
        "bracket": json_ready(row["bracket"]),
        "side": "BUY_YES",
        "distance_bucket": json_ready(row["distance_bucket"]),
        "token_id": json_ready(row.get("token_id")),
        "market_id": json_ready(row.get("market_id")),
        "condition_id": json_ready(row.get("condition_id")),
        "target_yes_ask": ask,
        "target_yes_ask_size": json_ready(row.get("target_yes_ask_size")),
        "target_yes_bid": json_ready(row.get("target_yes_bid")),
        "target_yes_spread": json_ready(row.get("target_yes_spread")),
        "hypothetical_notional_usd": notional,
        "hypothetical_shares": notional / ask if ask > 0 else None,
        "raw_model_p_yes": json_ready(row.get("raw_model_p_yes")),
        "p_reheat_context": json_ready(row.get("p_reheat_context")),
        "p_target_yes_wins": json_ready(row.get("p_target_yes_wins")),
        "raw_edge": json_ready(row.get("raw_edge")),
        "blended_edge": json_ready(row.get("blended_edge")),
        "reheat_adjusted_edge": json_ready(row.get("reheat_adjusted_edge")),
        "current_native": json_ready(row.get("current_native")),
        "running_native": json_ready(row.get("running_native")),
        "decline_native": json_ready(row.get("decline_native")),
        "target_distance_native": json_ready(row.get("target_distance_native")),
        "gap_current_to_target_low_native": json_ready(row.get("gap_current_to_target_low_native")),
        "minutes_since_running_max": json_ready(row.get("minutes_since_running_max")),
        "tmpf_now": json_ready(row.get("tmpf_now")),
        "dwpf_now": json_ready(row.get("dwpf_now")),
        "relative_humidity_pct": json_ready(row.get("relative_humidity_pct")),
        "wind_speed_kt": json_ready(row.get("wind_speed_kt")),
        "sky_cover_code": json_ready(row.get("sky_cover_code")),
        "temp_trend_1h_f": json_ready(row.get("temp_trend_1h_f")),
        "temp_trend_3h_f": json_ready(row.get("temp_trend_3h_f")),
        "forecast_clock_source": json_ready(row.get("forecast_clock_source")),
        "gfs_forecast_peak_delta_hours_local": json_ready(row.get("gfs_forecast_peak_delta_hours_local")),
        "ecmwf_forecast_peak_delta_hours_local": json_ready(row.get("ecmwf_forecast_peak_delta_hours_local")),
        "forecast_peak_hour_spread": json_ready(row.get("forecast_peak_hour_spread")),
        "gfs_forecast_gap_to_running_native": json_ready(row.get("gfs_forecast_gap_to_running_native")),
        "ecmwf_forecast_gap_to_running_native": json_ready(row.get("ecmwf_forecast_gap_to_running_native")),
        "same_state_current_yes_ask": json_ready(row.get("current_yes_ask")),
        "same_state_d1_no_bracket": json_ready(row.get("d1_no_bracket")),
        "same_state_d1_no_ask": json_ready(row.get("d1_no_ask")),
        "same_state_d2_no_bracket": json_ready(row.get("d2_no_bracket")),
        "same_state_d2_no_ask": json_ready(row.get("d2_no_ask")),
        "config": {
            "max_ask": float(args.max_ask),
            "min_edge": float(args.min_edge),
            "distance_buckets": args.distance_buckets,
            "exclude_deep_fade": bool(args.exclude_deep_fade),
            "hypothetical_notional_usd": notional,
        },
        "source_scored_rows": str(Path(args.scored_rows).resolve().relative_to(ROOT)),
    }


def write_summary(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    journal_path = Path(args.journal)
    summary_path = Path(args.summary)
    selected, meta = load_and_filter(args)
    seen = existing_ids(journal_path)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    appended = 0
    skipped_existing = 0
    with journal_path.open("a", encoding="utf-8") as fh:
        for _, row in selected.iterrows():
            entry = journal_row(row, args)
            if entry["shadow_decision_id"] in seen:
                skipped_existing += 1
                continue
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            seen.add(entry["shadow_decision_id"])
            appended += 1
    summary = {
        "generated_at_utc": now_utc(),
        "strategy_id": STRATEGY_ID,
        "rule_id": RULE_ID,
        "execution_mode": "zero_notional_shadow",
        "journal": str(journal_path.relative_to(ROOT)),
        "summary": str(summary_path.relative_to(ROOT)),
        "appended": appended,
        "skipped_existing": skipped_existing,
        "known_shadow_decision_ids": len(seen),
        **meta,
    }
    write_summary(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
