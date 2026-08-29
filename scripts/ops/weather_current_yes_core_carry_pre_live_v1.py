#!/usr/bin/env python3
"""Zero-notional pre-live runner for the frozen current-YES core carry v1.

This module has no signing or order-submission path.  It reuses the canonical
weather-state builder, reads a fresh direct CLOB book, scores one checkpoint
per city/day/local-hour, and writes counterfactual five-share taker plans.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import weather_current_yes_heat_death_shadow_v1 as base  # noqa: E402
from scripts.ops.weather_market_proxy import market_httpx_client  # noqa: E402
from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (  # noqa: E402
    evaluate_entry,
    load_artifact,
)
from weather_data_feed.city_calendar import CITY_TIMEZONE  # noqa: E402
from weather_data_feed.observation_cache import parse_utc  # noqa: E402
from src.platform.storage.jsonl_index import JsonlFieldRangeIndex  # noqa: E402


STRATEGY_ID = "current_yes_core_carry_v1"
STRATEGY_INSTANCE = "current_yes_core_carry_pre_live_v1"
DECISION_MODE = "frozen_core_probability_first_positive_five_share_taker_ev"
OUTPUT_DIR = ROOT / "runtime/weather_edge_v1" / STRATEGY_INSTANCE
ARTIFACT_PATH = (
    ROOT / "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v1.json"
)
CLOB_BOOK_API = "https://clob.polymarket.com/book"
FAMILY_LIVE_ORDER_FILES = (
    ROOT
    / "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_h1_late_carry_v1/live_orders.jsonl",
    ROOT
    / "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_h2_early_dislocation_v1/live_orders.jsonl",
    ROOT / "runtime/weather_edge_v1/current_yes_heat_death_tiny_live_v1/live_orders.jsonl",
)


def configure_identity() -> None:
    base.STRATEGY_ID = STRATEGY_ID
    base.STRATEGY_INSTANCE = STRATEGY_INSTANCE
    base.BUILDER_VERSION = STRATEGY_INSTANCE
    base.DECISION_MODE = DECISION_MODE


def checkpoint_clock(row: Mapping[str, Any]) -> tuple[int | None, int | None]:
    stamp = parse_utc(row.get("decision_snapshot_ts_utc") or row.get("as_of_ts_utc"))
    zone = CITY_TIMEZONE.get(str(row.get("city") or ""))
    if stamp is not None and zone:
        local = stamp.astimezone(ZoneInfo(zone))
        return local.hour, local.minute
    value = base.finite(row.get("decision_hour_local_float", row.get("decision_hour_local")))
    if value is None:
        return None, None
    hour = math.floor(value)
    minute = min(59, max(0, int(round((value - hour) * 60))))
    return hour, minute


def checkpoint_key(row: Mapping[str, Any]) -> str | None:
    hour, _minute = checkpoint_clock(row)
    city = str(row.get("city") or "")
    target_date = str(row.get("target_date") or "")
    return f"{city}|{target_date}|{hour:02d}" if city and target_date and hour is not None else None


def checkpoint_eligible(
    row: Mapping[str, Any],
    scored_keys: set[str],
    locked_city_days: set[str],
) -> tuple[bool, str]:
    city = str(row.get("city") or "")
    target_date = str(row.get("target_date") or "")
    city_day = f"{city}|{target_date}"
    hour, minute = checkpoint_clock(row)
    key = checkpoint_key(row)
    if not city or not target_date or hour is None or minute is None or key is None:
        return False, "missing_local_checkpoint_clock"
    if city_day in locked_city_days:
        return False, "city_day_locked_after_first_positive_ev"
    if not 13 <= hour <= 17:
        return False, "outside_local_hour_window"
    if minute < 30:
        return False, "before_local_half_hour_checkpoint"
    if key in scored_keys:
        return False, "local_hour_already_scored"
    return True, "first_unscored_checkpoint_at_or_after_local_half_hour"


def observation_freshness_valid(row: Mapping[str, Any]) -> tuple[bool, str]:
    """Treat source age as PIT validity evidence, never as model alpha."""

    if str(row.get("obs_status") or "").strip().lower() != "ok":
        return False, "observation_status_not_ok"
    if str(row.get("station_gap_state") or "").strip() != "within_expected_cadence":
        return False, "observation_outside_expected_cadence"
    age = base.finite(row.get("obs_age_min", row.get("obs_age_minutes")))
    cadence = base.finite(
        row.get("expected_report_cadence", row.get("observation_cadence_min"))
    )
    report_ts = parse_utc(row.get("source_report_ts_utc"))
    if age is None or age < 0 or cadence is None or cadence <= 0 or report_ts is None:
        return False, "invalid_observation_freshness_lineage"
    return True, "freshness_lineage_valid"


def fetch_full_book(
    client: httpx.Client,
    token_id: str,
) -> dict[str, Any]:
    observed_at = base.utc_now()
    if not token_id:
        return {
            "status": "missing_token",
            "fetched_at_utc": None,
            "request_started_at_utc": None,
            "response_received_at_utc": None,
            "parsed_at_utc": None,
            "error_observed_at_utc": observed_at,
            "clock_lineage_status": "direct_clob_request_not_started",
            "bids": [],
            "asks": [],
        }
    request_started_at = observed_at
    response_received_at: str | None = None
    try:
        response = client.get(CLOB_BOOK_API, params={"token_id": token_id})
        response_received_at = base.utc_now()
        response.raise_for_status()
        payload = response.json()
        parsed_at = base.utc_now()
        if not isinstance(payload, Mapping):
            raise ValueError("book response is not an object")
        bids = list(payload.get("bids") or [])
        asks = list(payload.get("asks") or [])
        summary = base._book_summary(payload)  # noqa: SLF001
        return {
            "status": "ok",
            # Compatibility alias.  This used to be stamped before the HTTP
            # request, which made it impossible to distinguish request start
            # from the response clock of the BBO actually scored below.
            "fetched_at_utc": response_received_at,
            "request_started_at_utc": request_started_at,
            "response_received_at_utc": response_received_at,
            "parsed_at_utc": parsed_at,
            "clock_lineage_status": "direct_clob_response_clock_v1",
            "bids": bids,
            "asks": asks,
            "tick_size": base.finite(payload.get("tick_size")),
            **summary,
        }
    except Exception as exc:  # noqa: BLE001
        error_observed_at = base.utc_now()
        return {
            "status": f"fetch_error:{type(exc).__name__}",
            "fetched_at_utc": response_received_at,
            "request_started_at_utc": request_started_at,
            "response_received_at_utc": response_received_at,
            "parsed_at_utc": None,
            "error_observed_at_utc": error_observed_at,
            "clock_lineage_status": "direct_clob_fetch_error_clock_v1",
            "bids": [],
            "asks": [],
        }


def read_snapshot_decisions(path: Path, snapshot_file: str) -> list[dict[str, Any]]:
    index = JsonlFieldRangeIndex.load(
        path,
        path.parent / ".indexes" / f"{path.name}.snapshot_file.sqlite3",
        "snapshot_file",
    )
    rows_by_id: dict[str, dict[str, Any]] = {}
    for row in index.rows_for(snapshot_file):
        decision_id = str(row.get("shadow_decision_id") or "")
        if not decision_id:
            decision_id = "|".join(
                [
                    str(row.get("city") or ""),
                    str(row.get("target_date") or ""),
                    str(row.get("current_bracket") or ""),
                    snapshot_file,
                ]
            )
        rows_by_id[decision_id] = row
    return list(rows_by_id.values())


def pre_live_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"scored_checkpoint_keys": [], "locked_city_days": []}
    payload = base.read_json(path)
    payload.setdefault("scored_checkpoint_keys", [])
    payload.setdefault("locked_city_days", [])
    return payload


def submitted_family_city_days(
    paths: tuple[Path, ...] = FAMILY_LIVE_ORDER_FILES,
) -> set[str]:
    city_days: set[str] = set()
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("status") or "") != "submitted":
                    continue
                city = str(row.get("city") or "")
                target_date = str(row.get("target_date") or "")
                if city and target_date:
                    city_days.add(f"{city}|{target_date}")
    return city_days


def score_snapshot(args: argparse.Namespace, collector_summary: Mapping[str, Any]) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    snapshot_file = str(collector_summary.get("snapshot_file") or "")
    rows = read_snapshot_decisions(output_dir / "state_decisions.jsonl", snapshot_file)
    state_path = output_dir / "pre_live_state.json"
    persistent = pre_live_state(state_path)
    scored_keys = set(map(str, persistent["scored_checkpoint_keys"]))
    locked_city_days = set(map(str, persistent["locked_city_days"]))
    artifact = load_artifact(args.artifact)
    evaluations: list[dict[str, Any]] = []
    would_orders: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    family_city_days: set[str] | None = None
    snapshot_ts = parse_utc(
        collector_summary.get("snapshot_available_at_utc")
        or collector_summary.get("decision_as_of_utc")
        or collector_summary.get("snapshot_ts_utc")
    )
    snapshot_age_min = (
        None
        if snapshot_ts is None
        else max(0.0, (datetime.now(timezone.utc) - snapshot_ts).total_seconds() / 60.0)
    )
    if snapshot_age_min is None or snapshot_age_min > float(args.max_snapshot_age_min):
        summary = {
            "status": "stale_snapshot",
            "generated_at_utc": base.utc_now(),
            "strategy_id": STRATEGY_ID,
            "strategy_instance": STRATEGY_INSTANCE,
            "mode": "zero_notional_pre_live",
            "snapshot_file": snapshot_file,
            "snapshot_age_min": snapshot_age_min,
            "max_snapshot_age_min": float(args.max_snapshot_age_min),
            "source_decision_rows": len(rows),
            "scores_written": 0,
            "would_orders_written": 0,
            "artifact_hash": artifact["artifact_hash"],
            "real_live_action": "none",
        }
        base.write_json(output_dir / "pre_live_latest_summary.json", summary)
        base.append_jsonl(output_dir / "pre_live_summary_history.jsonl", [summary])
        return summary

    candidates: list[tuple[dict[str, Any], str]] = []
    for row in rows:
        eligible, reason = checkpoint_eligible(row, scored_keys, locked_city_days)
        counts[reason] += 1
        if eligible:
            freshness_ok, freshness_reason = observation_freshness_valid(row)
            counts[freshness_reason] += 1
            if not freshness_ok:
                continue
            key = checkpoint_key(row)
            if key is not None:
                candidates.append((row, key))

    with market_httpx_client(args.book_proxy, timeout=float(args.book_timeout_sec)) as client:
        for row, key in candidates[: max(0, int(args.max_books_per_snapshot))]:
            book = fetch_full_book(client, str(row.get("current_yes_token_id") or ""))
            hour, minute = checkpoint_clock(row)
            scored_row = {
                **row,
                "current_yes_bid": book.get("bid"),
                "current_yes_ask": book.get("ask"),
                "current_yes_bid_size": book.get("bid_size"),
                "current_yes_ask_size": book.get("ask_size"),
                "current_yes_tick_size": book.get("tick_size"),
                "current_yes_book_status": book.get("status"),
                "current_yes_book_fetched_at_utc": book.get("fetched_at_utc"),
                "current_yes_book_request_started_at_utc": book.get(
                    "request_started_at_utc"
                ),
                "current_yes_book_response_received_at_utc": book.get(
                    "response_received_at_utc"
                ),
                "current_yes_book_parsed_at_utc": book.get("parsed_at_utc"),
                "current_yes_book_clock_lineage_status": book.get(
                    "clock_lineage_status"
                ),
                "checkpoint_hour_local": hour,
                "checkpoint_minute_local": minute,
                "checkpoint_key": key,
                "checkpoint_eligible": True,
                "observation_freshness_valid": True,
                "observation_freshness_role": "pit_data_validity_not_probability_feature",
            }
            result = evaluate_entry(scored_row, book.get("asks") or [], artifact)
            evaluation = {
                **scored_row,
                **result,
                "record_type": "weather_strategy_pre_live_score",
                "strategy_id": STRATEGY_ID,
                "strategy_instance": STRATEGY_INSTANCE,
                "mode": "zero_notional_pre_live",
                "zero_notional": True,
                "no_order_placed": True,
                "created_at_utc": base.utc_now(),
            }
            evaluations.append(evaluation)

            # A valid two-sided direct book is a completed hourly observation.
            # Five-share depth affects executability, not whether the weather
            # path is allowed to be resampled every 30 seconds.
            if result.get("model_probability_hold") is not None:
                scored_keys.add(key)
                counts["hourly_checkpoint_scored"] += 1
            else:
                counts["hourly_checkpoint_not_scored"] += 1
            if result.get("eligible"):
                city_day = f"{row.get('city')}|{row.get('target_date')}"
                if family_city_days is None:
                    family_city_days = submitted_family_city_days()
                family_conflict = city_day in family_city_days
                locked_city_days.add(city_day)
                evaluation["family_city_day_conflict"] = family_conflict
                evaluation["would_submit_after_family_dedupe"] = not family_conflict
                plan = {
                    "record_type": "weather_strategy_zero_notional_would_order",
                    "strategy_id": STRATEGY_ID,
                    "strategy_instance": STRATEGY_INSTANCE,
                    "fact_signal_candidate_id": row.get("fact_signal_candidate_id"),
                    "city": row.get("city"),
                    "target_date": row.get("target_date"),
                    "condition_id": row.get("current_condition_id"),
                    "token_id": row.get("current_yes_token_id"),
                    "side": "BUY_YES",
                    "order_type": "TAKER",
                    "shares": float(artifact["entry_policy"]["taker_shares"]),
                    "model_probability_hold": result.get("model_probability_hold"),
                    "effective_cost_per_share": result["taker_ladder"].get(
                        "effective_cost_per_share"
                    ),
                    "model_edge_after_fee_and_depth": result.get(
                        "model_edge_after_fee_and_depth"
                    ),
                    "checkpoint_key": key,
                    "artifact_hash": artifact["artifact_hash"],
                    "family_city_day_conflict": family_conflict,
                    "would_submit_after_family_dedupe": not family_conflict,
                    "zero_notional": True,
                    "no_order_placed": True,
                    "created_at_utc": base.utc_now(),
                }
                would_orders.append(plan)
                counts["positive_ev_would_order"] += 1
                if family_conflict:
                    counts["family_city_day_conflict"] += 1

    base.append_jsonl(output_dir / "pre_live_scores.jsonl", evaluations)
    base.append_jsonl(output_dir / "would_orders.jsonl", would_orders)
    base.write_json(
        state_path,
        {
            "scored_checkpoint_keys": sorted(scored_keys),
            "locked_city_days": sorted(locked_city_days),
            "updated_at_utc": base.utc_now(),
            "artifact_hash": artifact["artifact_hash"],
        },
    )
    summary = {
        "status": "ok",
        "generated_at_utc": base.utc_now(),
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "mode": "zero_notional_pre_live",
        "snapshot_file": snapshot_file,
        "source_decision_rows": len(rows),
        "snapshot_age_min": snapshot_age_min,
        "max_snapshot_age_min": float(args.max_snapshot_age_min),
        "checkpoint_candidates": len(candidates),
        "scores_written": len(evaluations),
        "would_orders_written": len(would_orders),
        "checkpoint_counts": dict(sorted(counts.items())),
        "scored_checkpoint_keys_total": len(scored_keys),
        "locked_city_days_total": len(locked_city_days),
        "artifact_hash": artifact["artifact_hash"],
        "real_live_action": "none",
    }
    base.write_json(output_dir / "pre_live_latest_summary.json", summary)
    base.append_jsonl(output_dir / "pre_live_summary_history.jsonl", [summary])
    return summary


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    configure_identity()
    # The base collector writes canonical feature rows but performs no direct
    # quote refresh here; this runner owns the exact current-YES book contract.
    args.disable_book_refresh = True
    collector = base.run_once(args)
    if collector.get("status") == "already_processed":
        return dict(collector)
    if collector.get("status") != "ok":
        return dict(collector)
    return score_snapshot(args, collector)


def parser() -> argparse.ArgumentParser:
    ap = base.parser()
    ap.description = __doc__
    ap.set_defaults(output_dir=str(OUTPUT_DIR), disable_book_refresh=True)
    ap.add_argument(
        "--artifact",
        default=str(ARTIFACT_PATH),
    )
    ap.add_argument("--max-books-per-snapshot", type=int, default=20)
    ap.add_argument("--max-snapshot-age-min", type=float, default=20.0)
    return ap


def main() -> int:
    args = parser().parse_args()
    if args.command == "run":
        print(json.dumps(run_once(args), ensure_ascii=False, sort_keys=True))
        return 0
    while True:
        try:
            summary = run_once(args)
            if summary.get("status") != "already_processed":
                print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        except Exception as exc:  # noqa: BLE001
            error = {
                "status": "error",
                "generated_at_utc": base.utc_now(),
                "strategy_instance": STRATEGY_INSTANCE,
                "error": f"{type(exc).__name__}: {exc}",
            }
            base.append_jsonl(Path(args.output_dir) / "pre_live_summary_history.jsonl", [error])
            print(json.dumps(error, ensure_ascii=False, sort_keys=True), flush=True)
        time.sleep(max(10.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
