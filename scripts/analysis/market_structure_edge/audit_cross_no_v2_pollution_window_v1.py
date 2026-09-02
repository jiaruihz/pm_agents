#!/usr/bin/env python3
"""Replay the Cross NO V2 polluted production window on corrected semantics.

This is an impact audit, not a fill reconstruction. It uses the latest complete
market-book batch available before each source event. Because the broken runtime
did not request an event-time fresh REST book for early-blocked events, simulated
orders are reported as as-of-snapshot potential orders, never confirmed fills.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import gzip
import json
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.ops import weather_cross_no_v2_metar as strategy  # noqa: E402


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _batch_index(root: Path) -> tuple[list[Any], list[Path]]:
    indexed: list[tuple[Any, Path]] = []
    for path in sorted(root.rglob("*.jsonl.gz")):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                first = json.loads(next(handle))
            available = strategy.parse_utc(first.get("available_at_utc"))
        except (OSError, StopIteration, json.JSONDecodeError):
            continue
        if available is not None:
            indexed.append((available, path))
    indexed.sort()
    return [item[0] for item in indexed], [item[1] for item in indexed]


def _materialize_batch(path: Path, output: Path) -> Path:
    if output.exists():
        return output
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    output.write_text(json.dumps({"records": rows}), encoding="utf-8")
    return output


def _load_events(
    evidence_db: Path,
    cursor_path: Path,
    *,
    allowlist: dict[str, str],
    timezones: dict[str, str],
) -> list[dict[str, Any]]:
    _events, state = strategy.direct_evidence_events(
        evidence_db,
        cursor_path=cursor_path,
        allowlist=allowlist,
        city_timezones=timezones,
        initialize_at_current=False,
        allow_ended_run_for_replay=True,
    )
    strategy._save_cursor(cursor_path, state)
    events, _state = strategy.direct_evidence_events(
        evidence_db,
        cursor_path=cursor_path,
        allowlist=allowlist,
        city_timezones=timezones,
        initialize_at_current=False,
        allow_ended_run_for_replay=True,
    )
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-db", type=Path, required=True)
    parser.add_argument("--universe-config", type=Path, required=True)
    parser.add_argument("--market-batch-root", type=Path, required=True)
    parser.add_argument("--old-output-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-json", type=Path, required=True)
    args = parser.parse_args()

    allowlist, timezones, market_units, live_eligible = strategy.load_universe_config(
        args.universe_config
    )
    batch_times, batch_paths = _batch_index(args.market_batch_root)
    if not batch_times:
        raise SystemExit("no historical market-book batches found")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    old_opportunities = _jsonl(args.old_output_dir / "opportunities.jsonl")
    if not old_opportunities:
        raise SystemExit("old output has no opportunities; pollution window is undefined")
    old_event_keys = {
        strategy.source_event_identity(row)
        for row in old_opportunities
        if strategy.source_event_identity(row)
    }
    old_orders = _jsonl(args.old_output_dir / "orders.jsonl")

    with tempfile.TemporaryDirectory(prefix="cross_no_v2_impact_") as temp_name:
        temp = Path(temp_name)
        events = _load_events(
            args.evidence_db,
            temp / "cursor.json",
            allowlist=allowlist,
            timezones=timezones,
        )
        events = [
            event
            for event in events
            if strategy.source_event_identity(event) in old_event_keys
        ]
        batch_cache: dict[Path, Path] = {}
        event_batches: dict[str, dict[str, Any]] = {}
        already_replayed = set(
            map(
                str,
                strategy._load_strategy_state(args.output_dir / "state.json").get(
                    "processed_event_keys"
                )
                or [],
            )
        )
        # The impact report does not need to rematerialize source-attribution
        # aggregates every 30 event-time seconds during a compressed replay.
        strategy.ATTRIBUTION_REFRESH_SEC = 10**12

        for event in events:
            received = strategy.parse_utc(event.get("transport_received_at_utc"))
            if received is None:
                continue
            batch_position = bisect.bisect_right(batch_times, received) - 1
            if batch_position < 0:
                continue
            batch_path = batch_paths[batch_position]
            event_id = strategy.source_event_identity(event)
            event_batches[event_id] = {
                "market_batch_path": str(batch_path),
                "market_batch_available_at_utc": strategy.iso(batch_times[batch_position]),
                "market_batch_age_seconds": round(
                    (received - batch_times[batch_position]).total_seconds(), 6
                ),
            }
            if event_id in already_replayed:
                continue
            materialized = batch_cache.get(batch_path)
            if materialized is None:
                materialized = _materialize_batch(
                    batch_path,
                    temp / f"batch_{len(batch_cache):04d}.json",
                )
                batch_cache[batch_path] = materialized

            def simulated_place(order: dict[str, Any], *, identity: str = event_id) -> dict[str, Any]:
                order_id = f"counterfactual:{identity}"
                return {
                    "order_id": order_id,
                    "order_type": "GTC",
                    "order_mode": "counterfactual_asof_snapshot_only",
                    "post_only": False,
                    "place": {
                        "success": True,
                        "status": "matched",
                        "order_id": order_id,
                        "takingAmount": str(order["size"]),
                        "makingAmount": str(order["submitted_notional_usd"]),
                    },
                }

            received_mono = int(event.get("transport_received_monotonic_ns") or 0)
            strategy.run_probe(
                source_events_root=Path("."),
                market_books_latest=materialized,
                output_dir=args.output_dir,
                live=True,
                confirm_live=True,
                place_fn=simulated_place,
                now=received + timedelta(milliseconds=100),
                max_source_age_sec=30.0,
                max_observation_delay_sec=3600.0,
                official_fee_rate=strategy.WEATHER_TAKER_FEE_RATE,
                allowlist=allowlist,
                market_units=market_units,
                live_eligible_cities=live_eligible,
                events_override=[event],
                allow_clock_invalid_same_boot_monotonic_probe=True,
                clock_uncertainty_ms=strategy.safe_float(event.get("clock_uncertainty_ms")),
                boot_monotonic_start_ns=0,
                monotonic_now_ns=received_mono + 100_000_000,
                code_identity="counterfactual_corrected_worktree",
            )

    corrected_raw = _jsonl(args.output_dir / "opportunities.jsonl")
    corrected_by_event: dict[str, dict[str, Any]] = {}
    for row in corrected_raw:
        identity = strategy.source_event_identity(row)
        if identity:
            corrected_by_event.setdefault(identity, row)
    corrected = list(corrected_by_event.values())
    simulated_orders = _jsonl(args.output_dir / "orders.jsonl")
    corrected_crosses = [
        row
        for row in corrected
        if row.get("previous_official_bracket") is not None
        and row.get("new_source_bracket") is not None
        and str(row.get("previous_official_bracket"))
        != str(row.get("new_source_bracket"))
        and strategy.safe_float(row.get("source_running_max")) is not None
        and strategy.safe_float(row.get("prior_official_running_max")) is not None
        and float(row["source_running_max"]) > float(row["prior_official_running_max"])
    ]
    unique_crosses: dict[str, dict[str, Any]] = {}
    for row in corrected_crosses:
        identity = str(row.get("economic_cross_id") or row.get("execution_race_key") or "")
        if identity:
            unique_crosses.setdefault(identity, row)

    order_details: list[dict[str, Any]] = []
    for row in simulated_orders:
        event_identity = strategy.source_event_identity(row)
        order_details.append(
            {
                "source_event_identity": event_identity,
                "information_event_id": row.get("information_event_id"),
                "transport_received_at_utc": row.get("transport_received_at_utc"),
                "source_event_ts_utc": row.get("source_event_ts_utc"),
                "source": row.get("source"),
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "market_unit": row.get("market_unit"),
                "prior_official_running_max": row.get("prior_official_running_max"),
                "source_running_max": row.get("source_running_max"),
                "previous_official_bracket": row.get("previous_official_bracket"),
                "new_source_bracket": row.get("new_source_bracket"),
                "condition_id": row.get("condition_id"),
                "token_id": row.get("token_id"),
                "worst_ask_for_five_shares": row.get("worst_ask_for_five_shares"),
                "expected_five_share_vwap": row.get("expected_five_share_vwap"),
                "planned_principal_usd": row.get("planned_principal_usd"),
                **event_batches.get(event_identity, {}),
                "classification": "asof_snapshot_potential_order_not_confirmed_event_time_fill",
            }
        )
    snapshot_depth_observed: list[dict[str, Any]] = []
    for identity, row in unique_crosses.items():
        blockers = set(map(str, row.get("blockers") or []))
        if blockers - {"full_depth_execution_book_missing"}:
            continue
        if "full_depth_execution_book_missing" not in blockers:
            continue
        event_identity = strategy.source_event_identity(row)
        snapshot_depth_observed.append(
            {
                "economic_cross_id": identity,
                "source_event_identity": event_identity,
                "information_event_id": row.get("information_event_id"),
                "transport_received_at_utc": row.get("transport_received_at_utc"),
                "source_event_ts_utc": row.get("source_event_ts_utc"),
                "source": row.get("source"),
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "market_unit": row.get("market_unit"),
                "prior_official_running_max": row.get("prior_official_running_max"),
                "source_running_max": row.get("source_running_max"),
                "previous_official_bracket": row.get("previous_official_bracket"),
                "new_source_bracket": row.get("new_source_bracket"),
                "condition_id": row.get("condition_id"),
                "token_id": row.get("token_id"),
                "worst_ask_for_five_shares": row.get("worst_ask_for_five_shares"),
                "expected_five_share_vwap": row.get("expected_five_share_vwap"),
                "planned_principal_usd": row.get("planned_principal_usd"),
                **event_batches.get(event_identity, {}),
                "classification": "five_share_depth_observed_but_full_depth_contract_unverified",
            }
        )

    blocker_counts = collections.Counter(
        blocker for row in old_opportunities for blocker in row.get("blockers") or []
    )
    report = {
        "schema_version": "cross_no_v2_pollution_window_audit_v2",
        "generated_at_utc": strategy.iso(),
        "polluted_release_sha": "a96a1fe8a4d7b6cc28da7ff40015cb6b850ef430",
        "pollution_window": {
            "first_opportunity_at_utc": old_opportunities[0].get("created_at_utc") if old_opportunities else None,
            "last_opportunity_at_utc": old_opportunities[-1].get("created_at_utc") if old_opportunities else None,
            "source_events_replayed": len(events),
        },
        "observed_old_runtime": {
            "opportunities": len(old_opportunities),
            "orders": len(old_orders),
            "exact_condition_or_no_token_unverified": blocker_counts[
                "exact_condition_or_no_token_unverified"
            ],
            "market_not_found": blocker_counts["market_not_found"],
        },
        "corrected_replay": {
            "denominator_rows": len(corrected),
            "duplicate_replay_rows_discarded": len(corrected_raw) - len(corrected),
            "deterministic_unique_cross_signals": len(unique_crosses),
            "contract_qualified_asof_potential_orders": len(order_details),
            "snapshot_five_share_depth_observed_unverified_full_depth": len(
                snapshot_depth_observed
            ),
            "contract_qualified_potential_order_principal_usd": round(
                sum(float(row.get("planned_principal_usd") or 0.0) for row in order_details),
                6,
            ),
            "snapshot_depth_observed_by_city": dict(
                sorted(
                    collections.Counter(
                        row["city"] for row in snapshot_depth_observed
                    ).items()
                )
            ),
            "snapshot_depth_observed_by_source": dict(
                sorted(
                    collections.Counter(
                        row["source"] for row in snapshot_depth_observed
                    ).items()
                )
            ),
        },
        "contract_qualified_potential_orders": order_details,
        "snapshot_five_share_depth_observed": snapshot_depth_observed,
        "limitations": [
            "The broken runtime blocked before requesting a fresh event-time REST book, so missed actual venue orders/fills cannot be proven after the fact.",
            "Historical batch rows do not explicitly assert full_depth_valid=true; visible five-share depth is reported separately and is not promoted to an executable-order claim.",
            "No counterfactual order in this audit was sent to the exchange.",
        ],
    }
    args.report_json.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
