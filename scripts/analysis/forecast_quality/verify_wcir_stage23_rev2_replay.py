#!/usr/bin/env python3
"""Verify WCIR rev2 WS replay determinism without repeating REST/forecast scans."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.research_wcir_stage02_stage03_rev2 import (  # noqa: E402
    build_checkpoint_queries,
    canonical_hash,
    file_identity,
    load_frozen_events,
    load_subscription_epochs,
    read_jsonl,
    replay_ws_archive,
    write_json,
)


ARCHIVE_IDENTITY_KEYS = (
    "ws_files",
    "raw_frame_count",
    "raw_frame_ordered_identity",
    "coverage_identity",
    "ws_file_count",
    "query_count",
    "relevant_token_count",
    "ever_subscribed_relevant_token_count",
    "unknown_epoch_frame_count",
    "applied_frame_count",
    "duplicate_frame_count",
    "reconstruction_error_count",
    "clock_uncertainty_frame_count",
    "blocker_reason_counts",
    "day_summaries",
    "transport_day_count",
    "normal_day_count",
    "reconnect_or_gap_day_count",
)


def verified_identity_payload(
    *,
    event_identity: dict,
    universe_identity: dict,
    epoch_files: list[dict],
    archive: dict,
) -> dict:
    return {
        "frozen_event_identity": event_identity,
        "frozen_universe_identity": universe_identity,
        "subscription_epoch_files": epoch_files,
        "archive": {key: archive[key] for key in ARCHIVE_IDENTITY_KEYS},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, default=Path("/Volumes/jrs/weather_data_feed_service_runtime"))
    parser.add_argument(
        "--frozen-events",
        type=Path,
        default=ROOT / "reviews/wcir_next_print/stage_03/evidence/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz",
    )
    parser.add_argument(
        "--frozen-universe",
        type=Path,
        default=ROOT / "reviews/wcir_next_print/stage_02_rev2/evidence/FROZEN_EVENT_MARKET_UNIVERSE.jsonl.gz",
    )
    parser.add_argument(
        "--collector-freeze",
        type=Path,
        default=ROOT / "reviews/wcir_next_print/stage_02_rev2/COLLECTOR_AND_INPUT_FREEZE.json",
    )
    parser.add_argument(
        "--expected-manifest",
        type=Path,
        default=ROOT / "reviews/wcir_next_print/stage_02_rev2/FULL_ARCHIVE_REPLAY_MANIFEST.json",
    )
    parser.add_argument("--file-order", choices=("reverse", "chunked"), required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument(
        "--comparison",
        type=Path,
        default=ROOT / "reviews/wcir_next_print/stage_02_rev2/DETERMINISM_IDENTITY_COMPARISON.json",
    )
    args = parser.parse_args()

    events = load_frozen_events(args.frozen_events)
    universes = list(read_jsonl(args.frozen_universe))
    freeze = json.loads(args.collector_freeze.read_text())
    latency = float(freeze["latency_contract"]["value_seconds"])
    expected = json.loads(args.expected_manifest.read_text())
    epochs, epoch_files = load_subscription_epochs(args.runtime_root)
    queries = build_checkpoint_queries(events, universes, latency)
    _, actual, _ = replay_ws_archive(
        args.runtime_root, epochs, queries, file_order=args.file_order
    )
    actual_event_identity = file_identity(args.frozen_events, row_count=len(events))
    actual_universe_identity = file_identity(args.frozen_universe, row_count=len(universes))
    expected_event_identity = freeze["frozen_legacy_event_dataset"]
    expected_universe_identity = expected["frozen_market_universe"]
    expected_verified_identity = verified_identity_payload(
        event_identity=expected_event_identity,
        universe_identity=expected_universe_identity,
        epoch_files=expected["subscription_epoch_files"],
        archive=expected,
    )
    actual_verified_identity = verified_identity_payload(
        event_identity=actual_event_identity,
        universe_identity=actual_universe_identity,
        epoch_files=epoch_files,
        archive=actual,
    )
    checks = {
        "frozen_event_identity": actual_event_identity == expected_event_identity,
        "frozen_universe_identity": actual_universe_identity == expected_universe_identity,
        "subscription_epoch_files": epoch_files == expected["subscription_epoch_files"],
        "raw_frame_count": actual["raw_frame_count"] == expected["raw_frame_count"],
        "raw_frame_ordered_identity": (
            actual["raw_frame_ordered_identity"]
            == expected["raw_frame_ordered_identity"]
        ),
        "coverage_identity": actual["coverage_identity"] == expected["coverage_identity"],
        "ws_file_complete_identity": actual["ws_files"] == expected["ws_files"],
        "day_summaries": actual["day_summaries"] == expected["day_summaries"],
        "complete_verified_identity_hash": (
            canonical_hash(actual_verified_identity)
            == canonical_hash(expected_verified_identity)
        ),
    }
    for key in (
        "ws_file_count",
        "query_count",
        "relevant_token_count",
        "ever_subscribed_relevant_token_count",
        "unknown_epoch_frame_count",
        "applied_frame_count",
        "duplicate_frame_count",
        "reconstruction_error_count",
        "clock_uncertainty_frame_count",
        "blocker_reason_counts",
        "transport_day_count",
        "normal_day_count",
        "reconnect_or_gap_day_count",
    ):
        checks[key] = actual[key] == expected[key]
    result = {
        "file_order": args.file_order,
        "checks": checks,
        "status": "pass" if all(checks.values()) else "fail",
        "expected": {
            key: expected[key]
            for key in ("raw_frame_count", "raw_frame_ordered_identity", "coverage_identity")
        },
        "actual": {
            key: actual[key]
            for key in ("raw_frame_count", "raw_frame_ordered_identity", "coverage_identity")
        },
    }
    write_json(args.result, result)
    if result["status"] != "pass":
        raise RuntimeError(f"replay identity mismatch: {checks}")
    comparison = {}
    if args.comparison.is_file():
        comparison = json.loads(args.comparison.read_text())
    runs = dict(comparison.get("runs") or {})
    runs.setdefault(
        "forward",
        {
            "raw_frame_ordered_identity": expected["raw_frame_ordered_identity"],
            "coverage_identity": expected["coverage_identity"],
            "verified_identity_hash": canonical_hash(expected_verified_identity),
            "status": "reference",
        },
    )
    runs[args.file_order] = {
        "raw_frame_ordered_identity": actual["raw_frame_ordered_identity"],
        "coverage_identity": actual["coverage_identity"],
        "verified_identity_hash": canonical_hash(actual_verified_identity),
        "result_file": file_identity(args.result),
        "status": result["status"],
    }
    required = ("forward", "reverse", "chunked")
    comparison = {
        "required_orders": list(required),
        "runs": runs,
        "all_required_runs_present": all(order in runs for order in required),
        "all_checks_pass": all(
            runs.get(order, {}).get("status") in {"reference", "pass"}
            for order in required
            if order in runs
        ),
    }
    comparison["status"] = (
        "pass"
        if comparison["all_required_runs_present"] and comparison["all_checks_pass"]
        else "pending"
    )
    write_json(args.comparison, comparison)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
