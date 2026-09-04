#!/usr/bin/env python3
"""Adapt frozen Europe D-1 basket locks into canonical decision contracts.

The source strategy remains the immutable zero-notional V1 release.  This
sidecar only projects its append-only basket journal into the shared
Event/Checkpoint/ModelOutput/SignalCandidate contracts and verifies that each
leg still resolves to the exact archived orderbook response used at decision
time.  It has no venue client and can only emit zero-size TradeIntent records.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_city_runtime import ModelOutput, SignalCandidate, TradeIntent
from weather_clock_contract import parse_utc, utc_text
from weather_dashboard.ingest.state_checkpoints import build_state_checkpoint
from weather_data_feed.information_events import (
    build_information_event,
    canonical_json_hash,
)
from weather_data_feed_service.io_utils import append_jsonl, read_json, write_json


RUNTIME_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_SOURCE = (
    RUNTIME_ROOT / "output/europe_d1_distance2_dual_no_shadow_v1/baskets.jsonl"
)
DEFAULT_OUTPUT = (
    RUNTIME_ROOT / "output/europe_d1_distance2_lineage_adapter_v1"
)
DEFAULT_CONFIG = ROOT / "configs/weather/europe_d1_distance2_dual_no_shadow_v1.json"
SOURCE_STRATEGY_INSTANCE = "europe_d1_distance2_dual_no_shadow_v1"
ADAPTER_INSTANCE = "europe_d1_distance2_lineage_adapter_v1"
STRATEGY_KEY = "europe_d1_distance2_dual_no_carry_v8"
POLICY_ID = "market_tail_le_0_10_v1"
MODEL_ID = "europe_d1_distance2_market_identity_baseline_v1"
FEATURE_SET_ID = "europe_d1_distance2_paired_book_v1"
DEFAULT_SOURCE_REPO_SHA = "0ed49db4602ab7410207070dc0e03da6a11d6c57"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row must be an object at {path}:{line_number}")
            rows.append(row)
    return rows


def _existing_values(path: Path, field: str) -> set[str]:
    return {
        str(row[field])
        for row in _read_jsonl(path)
        if row.get(field) not in (None, "")
    }


def _same_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)
    except (TypeError, ValueError):
        return False


class BookEvidenceIndex:
    """Cache exact NO book rows and fail closed on a lineage mismatch."""

    def __init__(self) -> None:
        self._paths: dict[Path, dict[tuple[str, str], list[dict[str, Any]]]] = {}

    def _load(self, path: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
        resolved = path.resolve()
        cached = self._paths.get(resolved)
        if cached is not None:
            return cached
        if not resolved.is_file():
            raise ValueError(f"referenced orderbook file is missing: {resolved}")
        index: dict[tuple[str, str], list[dict[str, Any]]] = {}
        with gzip.open(resolved, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid archived orderbook JSONL at {resolved}:{line_number}"
                    ) from exc
                if str(row.get("outcome") or "").lower() != "no":
                    continue
                key = (
                    str(row.get("request_batch_capture_id") or ""),
                    str(row.get("token_id") or ""),
                )
                index.setdefault(key, []).append(row)
        self._paths[resolved] = index
        return index

    def resolve(self, basket: dict[str, Any], leg: dict[str, Any]) -> dict[str, Any]:
        path = Path(str(basket.get("book_snapshot_path") or ""))
        batch_id = str(basket.get("request_batch_capture_id") or "")
        token_id = str(leg.get("token_id") or "")
        if not path.is_absolute() or not token_id:
            raise ValueError("basket lacks absolute book path or token id")
        index = self._load(path)
        if batch_id:
            matches = index.get((batch_id, token_id), [])
        else:
            fetched = str(leg.get("book_fetched_at_utc") or "")
            matches = [
                row
                for (_candidate_batch, candidate_token), rows in index.items()
                if candidate_token == token_id
                for row in rows
                if str(row.get("fetched_at_utc") or "") == fetched
            ]
        if len(matches) != 1:
            raise ValueError(
                "exact orderbook evidence must resolve once: "
                f"{basket.get('city_date_key')}:{leg.get('leg')} matches={len(matches)}"
            )
        row = matches[0]
        required_equal = {
            "city": basket.get("city"),
            "event_date": basket.get("target_date"),
            "condition_id": leg.get("condition_id"),
            "token_id": leg.get("token_id"),
            "bracket": leg.get("bracket"),
        }
        if basket.get("book_available_at_utc"):
            required_equal["available_at_utc"] = basket["book_available_at_utc"]
        mismatches = {
            field: (row.get(field), expected)
            for field, expected in required_equal.items()
            if str(row.get(field) or "") != str(expected or "")
        }
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        numeric_pairs = {
            "no_best_bid": (summary.get("best_bid"), leg.get("no_best_bid")),
            "no_best_ask": (summary.get("best_ask"), leg.get("no_best_ask")),
            "no_ask_size": (summary.get("ask_size"), leg.get("no_ask_size")),
        }
        mismatches.update(
            {
                field: values
                for field, values in numeric_pairs.items()
                if not _same_number(*values)
            }
        )
        if mismatches:
            raise ValueError(
                "archived orderbook evidence differs from frozen basket: "
                f"{basket.get('city_date_key')}:{leg.get('leg')}:{mismatches}"
            )
        if row.get("clock_lineage_status") != "collector_exact_response_clock":
            raise ValueError("referenced orderbook is not collector-exact response evidence")
        return row


def _validate_source_basket(
    row: dict[str, Any],
    *,
    formal_start: datetime,
    allowed_repo_shas: set[str],
) -> None:
    if row.get("strategy_instance") != SOURCE_STRATEGY_INSTANCE:
        raise ValueError("unexpected source strategy instance")
    if row.get("execution_mode") != "zero_notional_shadow":
        raise ValueError("source basket is not zero-notional shadow")
    if int(row.get("orders_submitted") or 0) != 0:
        raise ValueError("source basket unexpectedly reports an order")
    if float(row.get("actual_notional_usd") or 0.0) != 0.0:
        raise ValueError("source basket unexpectedly reports non-zero notional")
    if str(row.get("repo_sha") or "") not in allowed_repo_shas:
        raise ValueError(f"source basket repo_sha is outside frozen allowlist: {row.get('repo_sha')}")
    decision = parse_utc(row.get("decision_asof_utc"), field="decision_asof_utc")
    assert decision is not None
    if decision < formal_start:
        raise ValueError("source basket predates the formal frozen-forward boundary")
    legs = row.get("legs")
    if not isinstance(legs, list) or len(legs) != 2:
        raise ValueError("source basket must contain exactly two legs")
    if {str(leg.get("leg") or "") for leg in legs} != {
        "low_distance2_no",
        "high_distance2_no",
    }:
        raise ValueError("source basket leg identities are invalid")


def _contract_rows(
    basket: dict[str, Any],
    *,
    config_sha: str,
    book_index: BookEvidenceIndex,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence = [book_index.resolve(basket, leg) for leg in basket["legs"]]
    bundles: list[dict[str, Any]] = []
    intents: list[dict[str, Any]] = []
    paired_executable = bool(basket.get("paired_book_executable"))
    selected = bool(basket.get("market_tail_eligible")) and paired_executable
    for leg, raw_book in zip(basket["legs"], evidence, strict=True):
        batch_id = str(raw_book["request_batch_capture_id"])
        available_ts = utc_text(raw_book["available_at_utc"])
        # A blocked mixed-clock pair has no single causal basket decision.  Each
        # blocked expression is therefore timestamped at its own exact response;
        # selected pairs keep the common frozen decision clock.
        decision_ts = (
            utc_text(basket["decision_asof_utc"])
            if basket.get("paired_book_clock_complete")
            else available_ts
        )
        source_path = str(Path(str(basket["book_snapshot_path"])).resolve())
        source_payload = {
            "request_batch_capture_id": batch_id,
            "book_snapshot_path": source_path,
            "city_date_key": basket["city_date_key"],
            "book_capture_id": raw_book.get("book_capture_id"),
            "raw_payload_hash": raw_book.get("raw_payload_hash"),
            "condition_id": raw_book.get("condition_id"),
            "token_id": raw_book.get("token_id"),
        }
        event = build_information_event(
            event_kind="market_book_snapshot",
            event_role="new_content",
            source="polymarket_clob",
            city=str(basket["city"]),
            station_id=None,
            provider_item_id=str(raw_book.get("book_capture_id") or batch_id),
            content_key=f"{batch_id}|{basket['city_date_key']}|{leg['token_id']}",
            normalized_payload=source_payload,
            detected_at_utc=available_ts,
            first_seen_at_utc=available_ts,
            available_at_utc=available_ts,
            pit_lineage_class="collector_exact",
            raw_source_path=source_path,
            raw_row_hash=str(raw_book.get("raw_payload_hash") or ""),
        )
        checkpoint = build_state_checkpoint(
            city=str(basket["city"]),
            target_date=str(basket["target_date"]),
            trigger_event=event,
            as_of_ts_utc=decision_ts,
            feature_frame_ref={
                "store_frame_id": batch_id,
                "feature_row_id": canonical_json_hash(
                    {
                        "city_date_key": basket["city_date_key"],
                        "book_capture_id": raw_book.get("book_capture_id"),
                    }
                ),
                "feature_schema_version": "europe_d1_distance2_direct_no_book_state_v1",
                "feature_version_manifest": {
                    "config_sha256": config_sha,
                    "source_repo_sha": basket["repo_sha"],
                    "policy_id": POLICY_ID,
                },
                "source_profile_id": STRATEGY_KEY,
            },
            input_events=[event],
            pit_provenance="live_capture",
            created_at_utc=decision_ts,
        )
        market_exact = leg.get("market_p_exact")
        p_no = None if market_exact is None else 1.0 - float(market_exact)
        target_id = f"polymarket:{leg['condition_id']}:NO"
        blocker = None if paired_executable else str(basket.get("decision_status") or "paired_book_blocked")
        output = ModelOutput.create(
            checkpoint_id=str(checkpoint["state_checkpoint_id"]),
            trigger_event_id=str(event["information_event_id"]),
            city=str(basket["city"]),
            target_date=str(basket["target_date"]),
            decision_ts_utc=decision_ts,
            target_id=target_id,
            target_kind="market_expression",
            p_model=p_no,
            model_id=MODEL_ID,
            model_artifact_id=config_sha,
            feature_set_id=FEATURE_SET_ID,
            input_refs=(
                {
                    "role": "feature_and_execution_book",
                    "physical_path": source_path,
                    "snapshot_id": batch_id,
                    "book_capture_id": raw_book.get("book_capture_id"),
                    "raw_payload_hash": raw_book.get("raw_payload_hash"),
                    "condition_id": leg.get("condition_id"),
                    "token_id": leg.get("token_id"),
                },
            ),
            scorable_status="scorable" if p_no is not None else "not_scorable",
            blocker_reason=None if p_no is not None else "market_mid_unavailable",
            market_feature_role="identity_baseline_only",
            market_feature_clock="decision_current",
            feature_book_snapshot_id=batch_id,
            metadata={
                "event_available_at_utc": available_ts,
                "feature_book_available_at_utc": available_ts,
                "source_basket_sha256": canonical_json_hash(basket),
                "source_basket_decision_asof_utc": utc_text(
                    basket["decision_asof_utc"]
                ),
                "weather_features_used_for_eligibility": False,
            },
        )
        candidate = SignalCandidate.create(
            checkpoint_id=str(checkpoint["state_checkpoint_id"]),
            trigger_event_id=str(event["information_event_id"]),
            city=str(basket["city"]),
            target_date=str(basket["target_date"]),
            decision_ts_utc=decision_ts,
            target_id=target_id,
            target_kind="market_expression",
            expression_id=target_id,
            condition_id=str(leg["condition_id"]),
            market_id=(
                None if raw_book.get("market_id") is None else str(raw_book["market_id"])
            ),
            token_id=str(leg["token_id"]),
            bracket=str(leg["bracket"]),
            side="NO",
            p_model=p_no,
            market_p=p_no,
            executable_cost=(
                float(leg["cost_per_share"])
                if paired_executable and leg.get("cost_per_share") is not None
                else None
            ),
            model_id=MODEL_ID,
            model_artifact_id=config_sha,
            feature_set_id=FEATURE_SET_ID,
            feature_book_snapshot_id=batch_id,
            execution_book_snapshot_id=batch_id,
            strategy_key=STRATEGY_KEY,
            policy_id=POLICY_ID,
            candidate_status="scored" if paired_executable else "blocked",
            blocker_reason=blocker,
            selected=selected,
            market_evidence_status=(
                "collector_exact_paired_direct_no"
                if paired_executable
                else "paired_expression_blocked"
            ),
            input_refs=output.input_refs,
            metadata={
                "candidate_grain_version": "v2_event_checkpoint",
                "basket_grain": "first_city_day",
                "city_date_key": basket["city_date_key"],
                "leg": leg["leg"],
                "allocation_weight": leg["allocation_weight"],
                "joint_market_probability": basket.get("joint_market_probability"),
                "joint_market_probability_max": basket.get("joint_market_probability_max"),
                "source_repo_sha": basket["repo_sha"],
                "zero_notional": True,
                "no_plan": True,
                "no_order": True,
                "no_fill": True,
            },
        )
        bundle = {
            "schema_version": "europe_d1_distance2_decision_bundle_v1",
            "bundle_record_id": canonical_json_hash(
                {"candidate_id": candidate.candidate_id, "selected": selected}
            ),
            "source_basket_sha256": canonical_json_hash(basket),
            "information_event": event,
            "state_checkpoint": checkpoint,
            "model_output": output.to_dict(),
            "signal_candidate": candidate.to_dict(),
        }
        bundles.append(bundle)
        if selected:
            intent = TradeIntent.create(
                candidate_id=candidate.candidate_id,
                condition_id=str(candidate.condition_id),
                token_id=str(candidate.token_id),
                side="BUY",
                requested_size=0.0,
                sizing_profile="normalized_half_basket_zero_notional_v1",
                execution_profile="dual_no_hold_to_settlement_zero_notional_v1",
                max_cost=candidate.executable_cost,
                ttl_seconds=None,
                dedupe_key=f"{STRATEGY_KEY}|{basket['city_date_key']}|{leg['leg']}",
                exposure_bucket=f"weather:{basket['city']}:{basket['target_date']}",
                mode="zero_notional",
                metadata={
                    "record_only": True,
                    "no_plan": True,
                    "no_order": True,
                    "no_fill": True,
                    "venue_calls": 0,
                    "allocation_weight": leg["allocation_weight"],
                },
            )
            intents.append(intent.to_dict())
    return bundles, intents


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    config = read_json(args.config, {})
    frozen = config.get("frozen_forward")
    if not isinstance(frozen, dict) or not frozen.get("formal_forward_start_utc"):
        raise ValueError("frozen_forward.formal_forward_start_utc is required")
    formal_start = parse_utc(
        frozen["formal_forward_start_utc"], field="formal_forward_start_utc"
    )
    assert formal_start is not None
    allowed_repo_shas = set(args.source_repo_sha)
    source_rows = _read_jsonl(args.source_baskets)
    accepted: list[dict[str, Any]] = []
    seen_city_dates: set[str] = set()
    excluded_repo_rows = 0
    for row in source_rows:
        decision = parse_utc(
            row.get("decision_asof_utc"), field="decision_asof_utc", allow_none=True
        )
        if decision is None or decision < formal_start:
            continue
        if str(row.get("repo_sha") or "") not in allowed_repo_shas:
            excluded_repo_rows += 1
            continue
        _validate_source_basket(
            row,
            formal_start=formal_start,
            allowed_repo_shas=allowed_repo_shas,
        )
        city_date_key = str(row.get("city_date_key") or "")
        if not city_date_key or city_date_key in seen_city_dates:
            raise ValueError(f"duplicate or missing frozen city-date: {city_date_key}")
        seen_city_dates.add(city_date_key)
        accepted.append(row)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = args.output_dir / "decision_bundles.jsonl"
    intent_path = args.output_dir / "trade_intents.jsonl"
    existing_bundles = _existing_values(bundle_path, "bundle_record_id")
    existing_intents = _existing_values(intent_path, "intent_id")
    config_sha = _sha256(args.config)
    book_index = BookEvidenceIndex()
    new_bundles: list[dict[str, Any]] = []
    new_intents: list[dict[str, Any]] = []
    selected_candidates = 0
    for basket in accepted:
        bundles, intents = _contract_rows(
            basket,
            config_sha=config_sha,
            book_index=book_index,
        )
        selected_candidates += sum(
            bool(bundle["signal_candidate"]["selected"]) for bundle in bundles
        )
        new_bundles.extend(
            bundle
            for bundle in bundles
            if bundle["bundle_record_id"] not in existing_bundles
        )
        new_intents.extend(
            intent for intent in intents if intent["intent_id"] not in existing_intents
        )
    if new_bundles:
        append_jsonl(bundle_path, new_bundles)
    if new_intents:
        append_jsonl(intent_path, new_intents)

    total_bundles = len(existing_bundles) + len(new_bundles)
    total_intents = len(existing_intents) + len(new_intents)
    payload = {
        "schema_version": "europe_d1_distance2_lineage_adapter_health_v1",
        "status": "ok",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_instance": ADAPTER_INSTANCE,
        "source_strategy_instance": SOURCE_STRATEGY_INSTANCE,
        "execution_mode": "zero_notional_adapter",
        "formal_forward_start_utc": utc_text(formal_start),
        "source_repo_sha_allowlist": sorted(allowed_repo_shas),
        "source_basket_rows": len(source_rows),
        "excluded_non_frozen_repo_rows": excluded_repo_rows,
        "accepted_frozen_baskets": len(accepted),
        "accepted_target_dates": len({str(row["target_date"]) for row in accepted}),
        "selected_baskets": sum(bool(row.get("market_tail_eligible")) for row in accepted),
        "verified_book_legs": 2 * len(accepted),
        "decision_bundles": total_bundles,
        "selected_signal_candidates": selected_candidates,
        "trade_intents": total_intents,
        "new_decision_bundles": len(new_bundles),
        "new_trade_intents": len(new_intents),
        "orders_submitted": 0,
        "actual_fills": 0,
        "actual_notional_usd": 0.0,
        "venue_calls": 0,
        "source_baskets_path": str(args.source_baskets.resolve()),
        "decision_bundles_path": str(bundle_path.resolve()),
        "trade_intents_path": str(intent_path.resolve()),
        "config_sha256": config_sha,
    }
    write_json(args.output_dir / "latest.json", payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-baskets", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--source-repo-sha",
        action="append",
        default=None,
        help="Allowed frozen source release SHA; repeat only for reviewed parity migrations.",
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=60.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.source_repo_sha = args.source_repo_sha or [DEFAULT_SOURCE_REPO_SHA]
    while True:
        try:
            payload = run_once(args)
        except Exception as exc:
            payload = {
                "schema_version": "europe_d1_distance2_lineage_adapter_health_v1",
                "status": "error",
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "strategy_instance": ADAPTER_INSTANCE,
                "execution_mode": "zero_notional_adapter",
                "orders_submitted": 0,
                "actual_fills": 0,
                "actual_notional_usd": 0.0,
                "venue_calls": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }
            args.output_dir.mkdir(parents=True, exist_ok=True)
            write_json(args.output_dir / "latest.json", payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
        if not args.loop:
            return 0 if payload["status"] == "ok" else 1
        time.sleep(max(1.0, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
