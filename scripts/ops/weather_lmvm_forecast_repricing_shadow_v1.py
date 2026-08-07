#!/usr/bin/env python3
"""Collect D-2/D-1 forecast-update single-YES repricing lineage, zero notional.

The runner consumes immutable full-ladder paper snapshots.  It treats the first
observed state of every city/target/model stream as a left-censored baseline and
only emits a decision when ``forecast_values_hash`` changes afterwards.  Every
rung remains in the candidate denominator; one YES rung is selected by
``delta(model probability) - delta(market probability)``.

It records ModelOutput -> SignalCandidate -> zero-notional TradeIntent plus
subsequent quote markouts.  It never creates a plan, order, or exchange call.
Quote crossing is explicitly telemetry, not an inferred maker fill.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge.research_lmvm_single_yes_repricing_v1 import (  # noqa: E402
    parse_snapshot_file,
    weather_fee_per_share,
)
from weather_city_runtime.contracts import ModelOutput, SignalCandidate, TradeIntent  # noqa: E402
from weather_dashboard.ingest.state_checkpoints import build_state_checkpoint  # noqa: E402
from weather_data_feed.information_events import (  # noqa: E402
    build_information_event,
    canonical_json_hash,
)


SCHEMA_VERSION = "weather_lmvm_forecast_repricing_shadow_v1"
CLOCK_CONTRACT_VERSION = "weather_orderbook_capture_v3_available_clock"
STRATEGY_KEY = "lmvm_forecast_innovation_single_yes_v1"
POLICY_ID = "delta_model_minus_delta_market_argmax_v1"
FEATURE_SET_ID = canonical_json_hash(
    [
        "model_probability_before_after",
        "market_probability_before_after",
        "direct_yes_bid_ask_depth",
        "forecast_first_seen_hash",
        "lead_days",
    ]
)
DEFAULT_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_OUTPUT = DEFAULT_RUNTIME / "output/lmvm_forecast_repricing_shadow_v1"
DEFAULT_SNAPSHOTS = (
    DEFAULT_RUNTIME / "full_ladder_output/paper_snapshots",
    DEFAULT_RUNTIME / "targeted_output/paper_snapshots",
)


def parse_clock_exact_snapshot_file(
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Keep only v3 ladders with a true downstream-available clock."""

    rows, parsed = parse_snapshot_file(str(path))
    counters: Counter[str] = Counter(parsed)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        counters["clock_payload_invalid"] += 1
        return [], dict(counters)
    records = [row for row in payload.get("records") or [] if isinstance(row, dict)]
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (
            str(record.get("city") or ""),
            str(record.get("target_date") or record.get("event_date") or ""),
            str(record.get("event_slug") or ""),
        )
        groups.setdefault(key, []).append(record)
    exact: list[dict[str, Any]] = []
    for source in rows:
        key = (
            str(source.get("city") or ""),
            str(source.get("target_date") or ""),
            str(source.get("event_slug") or ""),
        )
        group = groups.get(key) or []
        available_values = [
            str(record.get("ladder_available_at_utc") or "")
            for record in group
            if record.get("ladder_available_at_utc")
        ]
        clock_exact = bool(group) and len(available_values) == len(group) and all(
            record.get("event_time_pit_scorable") is True for record in group
        )
        if not clock_exact:
            counters["legacy_or_incomplete_clock_ladders_blocked"] += 1
            continue
        decision_ts = max(available_values)
        decision_dt = parse_utc(decision_ts)
        if decision_dt is None:
            counters["invalid_ladder_available_clock"] += 1
            continue
        row = dict(source)
        row["decision_ts_utc"] = decision_dt.isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        row["decision_epoch"] = decision_dt.timestamp()
        row["clock_lineage_status"] = "collector_exact_full_ladder_clock"
        row["event_time_pit_scorable"] = True
        exact.append(row)
    counters["collector_exact_clock_ladders"] += len(exact)
    return exact, dict(counters)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "bootstrapped": False,
            "seen_files": [],
            "streams": {},
            "open_candidates": {},
            "markout_keys": [],
            "totals": {},
            "clock_contract_version": CLOCK_CONTRACT_VERSION,
            "legacy_clock_quarantine": None,
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported state schema: {value.get('schema_version')}")
    if value.get("clock_contract_version") != CLOCK_CONTRACT_VERSION:
        value["legacy_clock_quarantine"] = {
            "migrated_at_utc": utc_now(),
            "prior_stream_count": len(value.get("streams") or {}),
            "prior_open_candidate_count": len(value.get("open_candidates") or {}),
            "prior_markout_key_count": len(value.get("markout_keys") or []),
            "reason": "legacy_snapshot_ts_not_collector_exact_available_clock",
        }
        # Preserve append-only journals and seen-file history, but force the
        # first v3 state in every stream to be a new left-censored baseline.
        value["streams"] = {}
        value["open_candidates"] = {}
        value["markout_keys"] = []
        value["bootstrapped"] = False
        value["clock_contract_version"] = CLOCK_CONTRACT_VERSION
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def append_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    materialized = list(rows)
    if not materialized:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in materialized:
            handle.write(
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
                + "\n"
            )
    return len(materialized)


def stream_key(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(key) or "")
        for key in ("city", "target_date", "event_slug", "forecast_source", "forecast_model")
    )


def discovered_files(snapshot_dirs: Iterable[Path]) -> list[Path]:
    files: set[Path] = set()
    for directory in snapshot_dirs:
        if not directory.exists():
            continue
        files.update(directory.glob("snapshot_*.json"))
    return sorted(files, key=lambda path: (path.name, str(path)))


def state_rungs(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(rung["condition_id"]): dict(rung) for rung in row.get("rungs") or []}


def maker_quote(rung: dict[str, Any]) -> dict[str, Any]:
    bid = float(rung["yes_bid"])
    ask = float(rung["yes_ask"])
    tick = finite(rung.get("tick_size")) or 0.001
    limit_price = max(bid, min(bid + tick, ask - tick))
    joins_best = math.isclose(limit_price, bid, abs_tol=tick / 10.0)
    return {
        "maker_limit_price": round(limit_price, 6),
        "tick_size": tick,
        "tick_size_source": "snapshot" if finite(rung.get("tick_size")) is not None else "weather_default_0.001",
        "visible_queue_ahead_shares": finite(rung.get("yes_bid_size")) if joins_best else 0.0,
        "queue_evidence_status": "top_of_book_visible_only",
        "maker_fill_status": "not_observable_without_order_or_trade_prints",
    }


def paired_rungs(previous: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    before = state_rungs(previous)
    output = []
    for rung in current.get("rungs") or []:
        prior = before.get(str(rung.get("condition_id")))
        if prior is None:
            continue
        model_before = finite(prior.get("model_prob"))
        model_after = finite(rung.get("model_prob"))
        market_before = finite(prior.get("market_prob"))
        market_after = finite(rung.get("market_prob"))
        if None in (model_before, model_after, market_before, market_after):
            continue
        model_delta = float(model_after) - float(model_before)
        market_delta = float(market_after) - float(market_before)
        output.append(
            {
                **dict(rung),
                "model_probability_before": float(model_before),
                "model_probability_after": float(model_after),
                "market_probability_before": float(market_before),
                "market_probability_after": float(market_after),
                "model_probability_delta": model_delta,
                "market_probability_delta": market_delta,
                "forecast_innovation_score": model_delta - market_delta,
            }
        )
    return output


def model_identity(row: dict[str, Any]) -> tuple[str, str]:
    model_id = ":".join(
        part for part in (
            str(row.get("forecast_source") or "forecast_source_unknown"),
            str(row.get("forecast_model") or "forecast_model_unknown"),
        ) if part
    )
    artifact = str(row.get("model_version") or model_id)
    return model_id, artifact


def build_update(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]] | None:
    paired = paired_rungs(previous, current)
    if len(paired) != len(current.get("rungs") or []):
        return None
    selected = max(
        paired,
        key=lambda rung: (
            float(rung["forecast_innovation_score"]),
            float(rung["model_probability_delta"]),
            float(rung["model_probability_after"]),
            -float(rung["yes_ask"]),
        ),
    )
    generated = utc_now()
    normalized_payload = {
        "forecast_state_before": previous["forecast_state_key"],
        "forecast_state_after": current["forecast_state_key"],
        "forecast_model": current.get("forecast_model"),
        "forecast_source": current.get("forecast_source"),
        "target_date": current["target_date"],
        "probabilities": [
            {
                "condition_id": rung["condition_id"],
                "bracket": rung["bracket"],
                "p_before": rung["model_probability_before"],
                "p_after": rung["model_probability_after"],
            }
            for rung in paired
        ],
    }
    event = build_information_event(
        event_kind="forecast_update",
        event_role="new_content",
        source=str(current.get("forecast_source") or current.get("forecast_model") or "forecast"),
        city=current["city"],
        station_id=None,
        provider_item_id=str(current["forecast_state_key"]),
        content_key=f"{stream_key(current)}|{current['forecast_state_key']}",
        normalized_payload=normalized_payload,
        source_event_ts_utc=current.get("model_init_utc_estimated"),
        issued_at_utc=current.get("model_init_utc_estimated"),
        detected_at_utc=current["decision_ts_utc"],
        first_seen_at_utc=current["decision_ts_utc"],
        available_at_utc=current["decision_ts_utc"],
        pit_lineage_class="collector_exact",
        raw_source_path=current["source_path"],
        raw_row_hash=current["snapshot_id"],
    )
    checkpoint = build_state_checkpoint(
        city=current["city"],
        target_date=current["target_date"],
        trigger_event=event,
        as_of_ts_utc=current["decision_ts_utc"],
        feature_frame_ref={
            "store_frame_id": current["snapshot_id"],
            "feature_row_id": stream_key(current),
            "feature_schema_version": "lmvm_forecast_update_features_v1",
            "feature_version_manifest": {
                "selector": POLICY_ID,
                "snapshot_parser": "lmvm_single_yes_repricing_v1",
            },
            "source_profile_id": str(current.get("forecast_source") or "forecast"),
        },
        input_events=[event],
        pit_provenance="collector_exact_snapshot_first_seen",
        created_at_utc=generated,
    )
    model_id, artifact_id = model_identity(current)
    bundles = []
    selected_candidate: SignalCandidate | None = None
    for rung in paired:
        is_selected = str(rung["condition_id"]) == str(selected["condition_id"])
        target_id = f"{rung['condition_id']}:YES"
        output = ModelOutput.create(
            checkpoint_id=checkpoint["state_checkpoint_id"],
            trigger_event_id=event["information_event_id"],
            city=current["city"],
            target_date=current["target_date"],
            decision_ts_utc=current["decision_ts_utc"],
            target_id=target_id,
            target_kind="market_expression",
            p_model=rung["model_probability_after"],
            model_id=model_id,
            model_artifact_id=artifact_id,
            feature_set_id=FEATURE_SET_ID,
            input_refs=(
                {"role": "forecast_and_feature_book", "physical_path": current["source_path"], "snapshot_id": current["snapshot_id"]},
            ),
            scorable_status="scorable",
            blocker_reason=None,
            market_feature_role="comparison_baseline_and_innovation_offset",
            market_feature_clock=current["decision_ts_utc"],
            feature_book_snapshot_id=current["snapshot_id"],
            metadata={
                "forecast_state_before": previous["forecast_state_key"],
                "forecast_state_after": current["forecast_state_key"],
                "model_probability_before": rung["model_probability_before"],
                "market_probability_before": rung["market_probability_before"],
            },
        )
        quote = maker_quote(rung)
        ask = float(rung["yes_ask"])
        entry_fee = weather_fee_per_share(ask)
        candidate = SignalCandidate.create(
            checkpoint_id=checkpoint["state_checkpoint_id"],
            trigger_event_id=event["information_event_id"],
            city=current["city"],
            target_date=current["target_date"],
            decision_ts_utc=current["decision_ts_utc"],
            target_id=target_id,
            target_kind="market_expression",
            expression_id=target_id,
            condition_id=str(rung["condition_id"]),
            market_id=None if rung.get("market_id") is None else str(rung.get("market_id")),
            token_id=None if rung.get("yes_token_id") is None else str(rung.get("yes_token_id")),
            bracket=str(rung["bracket"]),
            side="YES",
            p_model=rung["model_probability_after"],
            market_p=rung["market_probability_after"],
            executable_cost=ask,
            model_id=model_id,
            model_artifact_id=artifact_id,
            feature_set_id=FEATURE_SET_ID,
            feature_book_snapshot_id=current["snapshot_id"],
            execution_book_snapshot_id=current["snapshot_id"],
            strategy_key=STRATEGY_KEY,
            policy_id=POLICY_ID,
            candidate_status="scored",
            blocker_reason=None,
            selected=is_selected,
            market_evidence_status="complete_two_sided_direct_ladder",
            input_refs=(
                {"role": "forecast_and_feature_book", "physical_path": current["source_path"], "snapshot_id": current["snapshot_id"]},
            ),
            metadata={
                "candidate_grain": "forecast_update_exact_bracket_v1",
                "lead_days": current["lead_days"],
                "model_probability_before": rung["model_probability_before"],
                "model_probability_after": rung["model_probability_after"],
                "model_probability_delta": rung["model_probability_delta"],
                "market_probability_before": rung["market_probability_before"],
                "market_probability_after": rung["market_probability_after"],
                "market_probability_delta": rung["market_probability_delta"],
                "forecast_innovation_score": rung["forecast_innovation_score"],
                "entry_bid": rung["yes_bid"],
                "entry_ask": ask,
                "entry_bid_size": rung.get("yes_bid_size"),
                "entry_ask_size": rung.get("yes_ask_size"),
                "taker_entry_fee_per_share": entry_fee,
                "static_model_edge_after_taker_fee": rung["model_probability_after"] - ask - entry_fee,
                **quote,
                "zero_notional": True,
                "no_order_placed": True,
            },
        )
        bundles.append(
            {
                "schema_version": SCHEMA_VERSION,
                "information_event": event,
                "state_checkpoint": checkpoint,
                "model_output": output.to_dict(),
                "signal_candidate": candidate.to_dict(),
            }
        )
        if is_selected:
            selected_candidate = candidate
    if selected_candidate is None or not selected_candidate.token_id:
        return None
    selected_quote = maker_quote(selected)
    intent = TradeIntent.create(
        candidate_id=selected_candidate.candidate_id,
        condition_id=str(selected_candidate.condition_id),
        token_id=str(selected_candidate.token_id),
        side="BUY",
        requested_size=0.0,
        sizing_profile="zero_notional_v1",
        execution_profile="single_yes_maker_first_repricing_v1",
        max_cost=float(selected_quote["maker_limit_price"]),
        ttl_seconds=180 * 60,
        dedupe_key=f"{STRATEGY_KEY}|{selected_candidate.candidate_id}",
        exposure_bucket=f"{current['city']}|{current['target_date']}",
        mode="zero_notional",
        metadata={
            "record_only": True,
            "no_plan": True,
            "no_order": True,
            "maker_fill_requires_trade_or_order_evidence": True,
        },
    )
    update = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "lmvm_forecast_update",
        "generated_at_utc": generated,
        "stream_key": stream_key(current),
        "lead_days": current["lead_days"],
        "forecast_state_before": previous["forecast_state_key"],
        "forecast_state_after": current["forecast_state_key"],
        "snapshot_id": current["snapshot_id"],
        "snapshot_ts_utc": current["snapshot_ts_utc"],
        "decision_ts_utc": current["decision_ts_utc"],
        "clock_lineage_status": current["clock_lineage_status"],
        "source_path": current["source_path"],
        "city": current["city"],
        "target_date": current["target_date"],
        "rung_count": len(paired),
        "selected_candidate_id": selected_candidate.candidate_id,
        "selected_condition_id": selected_candidate.condition_id,
        "selected_bracket": selected_candidate.bracket,
        "selected_innovation_score": selected["forecast_innovation_score"],
        "zero_notional": True,
        "no_order_placed": True,
    }
    open_candidate = {
        "candidate_id": selected_candidate.candidate_id,
        "stream_key": stream_key(current),
        "condition_id": selected_candidate.condition_id,
        "bracket": selected_candidate.bracket,
        "decision_ts_utc": current["decision_ts_utc"],
        "decision_epoch": current["decision_epoch"],
        "entry_bid": selected["yes_bid"],
        "entry_ask": selected["yes_ask"],
        "entry_ask_size": selected.get("yes_ask_size"),
        "entry_fee_per_share": weather_fee_per_share(float(selected["yes_ask"])),
        **selected_quote,
    }
    return update, bundles, intent.to_dict(), open_candidate


def markouts_for_state(
    row: dict[str, Any],
    open_candidates: dict[str, dict[str, Any]],
    markout_keys: set[str],
    follow_minutes: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    output = []
    expired = []
    rungs = state_rungs(row)
    for candidate_id, candidate in open_candidates.items():
        if candidate["stream_key"] != stream_key(row):
            continue
        elapsed = (float(row["decision_epoch"]) - float(candidate["decision_epoch"])) / 60.0
        if elapsed <= 0:
            continue
        if elapsed > follow_minutes:
            expired.append(candidate_id)
            continue
        key = canonical_json_hash({"candidate_id": candidate_id, "snapshot_id": row["snapshot_id"]})
        if key in markout_keys:
            continue
        rung = rungs.get(str(candidate["condition_id"]))
        if rung is None:
            continue
        bid = float(rung["yes_bid"])
        ask = float(rung["yes_ask"])
        exit_fee = weather_fee_per_share(bid)
        taker_net = bid - exit_fee - float(candidate["entry_ask"]) - float(candidate["entry_fee_per_share"])
        maker_price = float(candidate["maker_limit_price"])
        output.append(
            {
                "schema_version": SCHEMA_VERSION,
                "record_type": "lmvm_quote_markout",
                "observed_at_utc": row["decision_ts_utc"],
                "candidate_id": candidate_id,
                "city": row["city"],
                "target_date": row["target_date"],
                "condition_id": candidate["condition_id"],
                "bracket": candidate["bracket"],
                "elapsed_minutes": elapsed,
                "snapshot_id": row["snapshot_id"],
                "source_path": row["source_path"],
                "yes_bid": bid,
                "yes_ask": ask,
                "yes_bid_size": rung.get("yes_bid_size"),
                "yes_ask_size": rung.get("yes_ask_size"),
                "taker_entry_to_taker_exit_net_per_share": taker_net,
                "maker_entry_to_taker_exit_net_per_share_before_maker_fee": bid - exit_fee - maker_price,
                "maker_quote_crossed": ask <= maker_price,
                "maker_fill_status": "not_inferred_from_quote_cross",
                "trade_print_coverage": "absent",
                "zero_notional": True,
                "no_order_placed": True,
            }
        )
        markout_keys.add(key)
    return output, expired


def bootstrap(files: list[Path], state: dict[str, Any], lookback_files: int) -> dict[str, int]:
    counters: Counter[str] = Counter()
    for path in files[-max(1, lookback_files) :]:
        rows, parsed = parse_clock_exact_snapshot_file(path)
        counters.update(parsed)
        for row in sorted(rows, key=lambda item: item["decision_epoch"]):
            state["streams"][stream_key(row)] = row
    state["seen_files"] = [str(path) for path in files]
    state["bootstrapped"] = True
    counters["baseline_streams"] = len(state["streams"])
    return dict(counters)


def run_cycle(args: argparse.Namespace, state: dict[str, Any]) -> dict[str, Any]:
    files = discovered_files(args.snapshot_dir)
    if not files:
        raise FileNotFoundError("no snapshot_*.json found in configured snapshot directories")
    if not state.get("bootstrapped"):
        cycle_counts = bootstrap(files, state, args.bootstrap_lookback_files)
        state["totals"] = dict(Counter(state.get("totals") or {}) + Counter(cycle_counts))
        return {
            "status": "ok",
            "phase": "left_censored_baseline",
            "generated_at_utc": utc_now(),
            "cycle": cycle_counts,
            "streams": len(state["streams"]),
            "open_candidates": 0,
            "zero_notional": True,
            "no_plan_created": True,
            "no_order_placed": True,
            "maker_fill_evidence": "quote_cross_only_not_fill",
        }

    seen = set(state.get("seen_files") or [])
    new_files = [path for path in files if str(path) not in seen]
    counts: Counter[str] = Counter(new_snapshot_files=len(new_files))
    updates = []
    bundles = []
    intents = []
    markouts = []
    open_candidates = dict(state.get("open_candidates") or {})
    markout_keys = set(state.get("markout_keys") or [])
    parsed_states = []
    for path in new_files:
        rows, parsed = parse_clock_exact_snapshot_file(path)
        counts.update(parsed)
        parsed_states.extend(rows)
        seen.add(str(path))
    for row in sorted(parsed_states, key=lambda item: (item["decision_epoch"], item["snapshot_id"])):
        observed, expired = markouts_for_state(row, open_candidates, markout_keys, args.follow_minutes)
        markouts.extend(observed)
        for candidate_id in expired:
            open_candidates.pop(candidate_id, None)
        key = stream_key(row)
        previous = state["streams"].get(key)
        if previous is not None and previous.get("forecast_state_key") != row.get("forecast_state_key"):
            built = build_update(previous, row)
            if built is None:
                counts["blocked_unpaired_ladder"] += 1
            else:
                update, new_bundles, intent, open_candidate = built
                updates.append(update)
                bundles.extend(new_bundles)
                intents.append(intent)
                open_candidates[open_candidate["candidate_id"]] = open_candidate
                counts["forecast_updates"] += 1
                counts["candidate_rows"] += len(new_bundles)
        state["streams"][key] = row

    out = args.output_dir
    counts["updates_written"] = append_jsonl(out / "forecast_updates.jsonl", updates)
    counts["bundles_written"] = append_jsonl(out / "decision_bundles.jsonl", bundles)
    counts["intents_written"] = append_jsonl(out / "trade_intents.jsonl", intents)
    counts["markouts_written"] = append_jsonl(out / "quote_markouts.jsonl", markouts)
    state["seen_files"] = sorted(seen)
    state["open_candidates"] = open_candidates
    state["markout_keys"] = sorted(markout_keys)
    totals = Counter(state.get("totals") or {})
    totals.update(counts)
    state["totals"] = dict(totals)
    latest_ts = max(
        (parse_utc(row.get("decision_ts_utc")) for row in state["streams"].values()),
        default=None,
    )
    age = (datetime.now(timezone.utc) - latest_ts).total_seconds() if latest_ts else None
    return {
        "status": "ok" if age is not None and age <= args.max_snapshot_age_seconds else "stale",
        "phase": "forward",
        "generated_at_utc": utc_now(),
        "latest_snapshot_ts_utc": None if latest_ts is None else latest_ts.isoformat().replace("+00:00", "Z"),
        "latest_snapshot_age_seconds": age,
        "cycle": dict(counts),
        "totals": dict(totals),
        "streams": len(state["streams"]),
        "open_candidates": len(open_candidates),
        "zero_notional": True,
        "no_plan_created": True,
        "no_order_placed": True,
        "maker_fill_evidence": "quote_cross_only_not_fill",
        "signal_funnel_unit": "forecast_update_city_target_model_stream",
        "evidence_funnel_unit": "candidate_snapshot_markout",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", action="append", type=Path, default=[])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--bootstrap-lookback-files", type=int, default=64)
    parser.add_argument("--follow-minutes", type=float, default=180.0)
    parser.add_argument("--max-snapshot-age-seconds", type=float, default=5400.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.snapshot_dir:
        args.snapshot_dir = list(DEFAULT_SNAPSHOTS)
    args.output_dir = args.output_dir.resolve()
    args.state = (args.state or (args.output_dir / "state.json")).resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(args.state)
    while True:
        result = run_cycle(args, state)
        state["last_cycle_at_utc"] = utc_now()
        write_json_atomic(args.state, state)
        write_json_atomic(args.output_dir / "latest.json", result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False), flush=True)
        if not args.loop:
            return 0
        time.sleep(max(1.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
