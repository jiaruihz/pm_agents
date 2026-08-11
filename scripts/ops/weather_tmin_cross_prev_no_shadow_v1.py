#!/usr/bin/env python3
"""Materialize Tmin first-cross previous-warmer-NO candidates, with no orders.

The upstream source-event ladder observer owns weather and book collection.  This
consumer turns each first lower-bracket cross into one canonical WCIR
ModelOutput/SignalCandidate pair.  Until a frozen next-colder-NO model exists the
candidate is deliberately blocked and no TradeIntent is created.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.information_events import canonical_json_hash
from weather_city_runtime.contracts import ModelOutput, SignalCandidate


SCHEMA_VERSION = "weather_tmin_cross_prev_no_shadow_v1"
STRATEGY_KEY = "weather.tmin.cross_prev_no"
POLICY_ID = "tmin_first_lower_cross_prev_warmer_no_zero_notional_v1"
MODEL_ID = "tmin_next_colder_no_pending_v0"
MODEL_ARTIFACT_ID = "unavailable_pending_frozen_training"
FEATURE_SET_ID = "tmin_cross_prev_no_event_book_v1"


def utc_text(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def read_complete_jsonl(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        return [], offset
    size = path.stat().st_size
    if size < offset:
        raise RuntimeError(f"input journal shrank: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            start = handle.tell()
            line = handle.readline()
            if not line:
                return rows, handle.tell()
            if not line.endswith(b"\n"):
                return rows, start
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0.0 <= number <= 1.0 else None


def _event_is_tmin_cross(event: dict[str, Any]) -> bool:
    if event.get("extreme_kind") != "min":
        return False
    try:
        current = int(event["current_bracket_value"])
        previous = int(event["reference_running_extreme_round_c"])
    except (KeyError, TypeError, ValueError):
        return False
    return current < previous and str(event.get("previous_market_bracket")) == str(previous)


def _book_from_quote(quote: dict[str, Any]) -> dict[str, Any]:
    return dict((((quote.get("quotes") or {}).get("t_minus_1") or {}).get("no") or {}))


def _candidate_bundle(
    event: dict[str, Any], quote: dict[str, Any], *, events_path: Path, quotes_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    book = _book_from_quote(quote)
    event_key = str(event["event_key"])
    trigger_event_id = canonical_json_hash(
        {
            "schema_version": SCHEMA_VERSION,
            "event_key": event_key,
            "source_obs_ts_utc": event.get("source_obs_ts_utc"),
            "source_detect_ts_utc": event.get("source_detect_ts_utc"),
        }
    )
    decision_ts = str(
        quote.get("ts_utc")
        or book.get("fresh_fetched_at_utc")
        or event.get("created_at_utc")
    )
    execution_snapshot_id = canonical_json_hash(
        {
            "schema_version": SCHEMA_VERSION,
            "event_key": event_key,
            "decision_ts_utc": decision_ts,
            "condition_id": book.get("condition_id"),
            "token_id": book.get("token_id"),
            "fresh_best_bid": book.get("fresh_best_bid"),
            "fresh_best_ask": book.get("fresh_best_ask"),
        }
    )
    checkpoint_id = canonical_json_hash(
        {
            "schema_version": SCHEMA_VERSION,
            "trigger_event_id": trigger_event_id,
            "execution_book_snapshot_id": execution_snapshot_id,
        }
    )
    bracket = str(event.get("previous_market_bracket") or event.get("previous_no_bracket_c"))
    condition_id = str(book.get("condition_id") or "") or None
    market_id = str(book.get("market_id") or "") or None
    token_id = str(book.get("token_id") or "") or None
    bid = _float(book.get("fresh_best_bid"))
    ask = _float(book.get("fresh_best_ask"))
    market_p = (bid + ask) / 2.0 if bid is not None and ask is not None else bid or ask
    market_available = (
        book.get("fresh_status") == "ok" and condition_id is not None and token_id is not None
    )
    blocker = "next_colder_no_model_not_frozen"
    target_id = f"{event['city']}:{event['target_date']}:tmin_exact_bracket:{bracket}:NO"
    refs = (
        {
            "kind": "source_event",
            "path": str(events_path),
            "event_key": event_key,
            "event_time_utc": event.get("source_obs_ts_utc"),
            "first_seen_at_utc": event.get("source_detect_ts_utc"),
            "ingested_at_utc": event.get("created_at_utc"),
        },
        {
            "kind": "execution_book",
            "path": str(quotes_path),
            "observed_at_utc": decision_ts,
            "snapshot_id": execution_snapshot_id,
        },
    )
    model_output = ModelOutput.create(
        checkpoint_id=checkpoint_id,
        trigger_event_id=trigger_event_id,
        city=str(event["city"]),
        target_date=str(event["target_date"]),
        decision_ts_utc=decision_ts,
        target_id=target_id,
        target_kind="market_expression",
        p_model=None,
        model_id=MODEL_ID,
        model_artifact_id=MODEL_ARTIFACT_ID,
        feature_set_id=FEATURE_SET_ID,
        input_refs=refs,
        scorable_status="not_scorable",
        blocker_reason=blocker,
        market_feature_role="evaluation_only",
        market_feature_clock="same_checkpoint_fresh_book",
        feature_book_snapshot_id=execution_snapshot_id,
        metadata={
            "extreme_kind": "min",
            "mechanism": "first_lower_cross_buy_previous_warmer_bracket_no",
            "source": event.get("source"),
            "source_basis_class": event.get("source_basis_class"),
            "settlement_alignment_status": event.get("source_calibration_status"),
            "no_order_placed": True,
        },
    )
    candidate = SignalCandidate.create(
        checkpoint_id=checkpoint_id,
        trigger_event_id=trigger_event_id,
        city=str(event["city"]),
        target_date=str(event["target_date"]),
        decision_ts_utc=decision_ts,
        target_id=target_id,
        target_kind="market_expression",
        expression_id=f"BUY_NO:{condition_id or bracket}",
        condition_id=condition_id,
        market_id=market_id,
        token_id=token_id,
        bracket=bracket,
        side="NO",
        p_model=None,
        market_p=market_p,
        executable_cost=ask,
        model_id=MODEL_ID,
        model_artifact_id=MODEL_ARTIFACT_ID,
        feature_set_id=FEATURE_SET_ID,
        feature_book_snapshot_id=execution_snapshot_id,
        execution_book_snapshot_id=execution_snapshot_id,
        strategy_key=STRATEGY_KEY,
        policy_id=POLICY_ID,
        candidate_status="blocked",
        blocker_reason=blocker,
        selected=False,
        market_evidence_status="available" if market_available else "coverage_gap",
        input_refs=refs,
        metadata={
            "extreme_kind": "min",
            "crossed_from_bracket": event.get("reference_running_extreme_round_c"),
            "crossed_to_bracket": event.get("current_bracket_value"),
            "expression_previous_warmer_bracket": bracket,
            "raw_no_best_bid": bid,
            "raw_no_best_ask": ask,
            "official_fee_adjusted_cost_available": False,
            "source_live_eligible": bool(event.get("source_live_eligible")),
            "source_blocked_reason": event.get("source_blocked_reason"),
            "zero_notional": True,
            "trade_intent_created": False,
            "no_order_placed": True,
        },
    )
    model_row = model_output.to_dict() | {"no_order_placed": True}
    candidate_row = candidate.to_dict() | {"zero_notional": True, "no_order_placed": True}
    return model_row, candidate_row


def _new_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_offset": 0,
        "quote_offset": 0,
        "events": {},
        "first_quotes": {},
        "emitted_candidate_ids": [],
    }


def run_cycle(args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    events_path = Path(args.events)
    quotes_path = Path(args.quotes)
    event_rows, event_offset = read_complete_jsonl(events_path, int(state.get("event_offset", 0)))
    quote_rows, quote_offset = read_complete_jsonl(quotes_path, int(state.get("quote_offset", 0)))
    events = dict(state.get("events") or {})
    first_quotes = dict(state.get("first_quotes") or {})
    for event in event_rows:
        key = str(event.get("event_key") or "")
        if key and key not in events and _event_is_tmin_cross(event):
            events[key] = event
    for quote in quote_rows:
        key = str(quote.get("event_key") or "")
        if key in events and key not in first_quotes:
            first_quotes[key] = quote
    emitted = set(state.get("emitted_candidate_ids") or [])
    new_candidates: list[dict[str, Any]] = []
    for key in sorted(set(events) & set(first_quotes)):
        model_row, candidate_row = _candidate_bundle(
            events[key], first_quotes[key], events_path=events_path, quotes_path=quotes_path
        )
        candidate_id = str(candidate_row["candidate_id"])
        if candidate_id in emitted:
            continue
        append_jsonl(Path(args.output_dir) / "model_outputs.jsonl", model_row)
        append_jsonl(Path(args.output_dir) / "signal_candidates.jsonl", candidate_row)
        emitted.add(candidate_id)
        new_candidates.append(candidate_row)
    state.update(
        {
            "event_offset": event_offset,
            "quote_offset": quote_offset,
            "events": events,
            "first_quotes": first_quotes,
            "emitted_candidate_ids": sorted(emitted),
            "updated_at_utc": utc_text(),
        }
    )
    all_candidates_path = Path(args.output_dir) / "signal_candidates.jsonl"
    all_candidates = []
    if all_candidates_path.exists():
        with all_candidates_path.open(encoding="utf-8") as handle:
            all_candidates = [json.loads(line) for line in handle if line.strip()]
    dates = sorted({str(row.get("target_date")) for row in all_candidates})
    cities = sorted({str(row.get("city")) for row in all_candidates})
    market_covered = sum(row.get("market_evidence_status") == "available" for row in all_candidates)
    priced = sum(row.get("executable_cost") is not None for row in all_candidates)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "generated_at_utc": utc_text(),
        "strategy_key": STRATEGY_KEY,
        "policy_id": POLICY_ID,
        "execution_mode": "zero_notional_shadow",
        "mechanism": "first lower Tmin source bracket cross; observe NO on previous warmer exact bracket",
        "cities": cities,
        "target_dates": dates,
        "signal_funnel": {
            "raw_cross_events": len(events),
            "first_quote_joined_events": len(set(events) & set(first_quotes)),
            "canonical_signal_candidates": len(all_candidates),
            "scored_candidates": 0,
            "selected_candidates": 0,
        },
        "evidence_funnel": {
            "market_evidence_available": market_covered,
            "raw_executable_ask_available": priced,
            "official_fee_adjusted_cost_available": 0,
            "settled_labels_available": 0,
        },
        "new_event_rows": len(event_rows),
        "new_quote_rows": len(quote_rows),
        "new_candidates": len(new_candidates),
        "pending_quote_events": len(set(events) - set(first_quotes)),
        "model_status": "blocked_pending_frozen_next_colder_no_model",
        "signal_candidates_path": str(all_candidates_path),
        "model_outputs_path": str(Path(args.output_dir) / "model_outputs.jsonl"),
        "source_events_path": str(events_path),
        "source_quotes_path": str(quotes_path),
        "trade_intent_count": 0,
        "order_count": 0,
        "fill_count": 0,
        "venue_write_calls": 0,
        "zero_notional": True,
        "orders_enabled": False,
        "no_order_placed": True,
    }
    write_json(Path(args.output_dir) / "latest_summary.json", summary)
    write_json(Path(args.output_dir) / "state.json", state)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True)
    parser.add_argument("--quotes", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / "state.json"
    state = read_json(state_path) if state_path.exists() else _new_state()
    while True:
        summary = run_cycle(args, state)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        if not args.loop:
            return 0
        time.sleep(max(1.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
