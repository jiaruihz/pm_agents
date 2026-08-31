#!/usr/bin/env python3
"""Build a deterministic Stage 0A/0B audit for Weather-first MM.

The audit is deliberately read-only.  It classifies historical V2 metadata,
actual fill fees, and the modern Core Carry execution journal.  It does not
call the exchange, infer User Channel messages, or mutate canonical facts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.execution.economics import (  # noqa: E402
    estimate_polymarket_v2_fee,
)
from src.strategies.weather_edge_v1.execution.own_order_truth import (  # noqa: E402
    OwnOrderTruthReducer,
)
from scripts.analysis.versioned_artifact_output import (  # noqa: E402
    prepare_new_run_output,
    resolve_run_output,
)


SCHEMA_VERSION = "weather_mm_stage0_truth_audit_v1"
V2_CUTOVER = "2026-04-28"
TAKER_REBATE_CUTOVER = "2026-05-28"
DEFAULT_FEE_RATE = Decimal("0.05")
DEFAULT_FEE_EXPONENT = Decimal("1")


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                yield {"_malformed_line": line_number}
                continue
            if isinstance(row, dict):
                row["_line_number"] = line_number
                yield row


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _db_rows(connection: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    connection.row_factory = sqlite3.Row
    orders = [
        dict(row)
        for row in connection.execute(
            """
            SELECT *
            FROM orders
            WHERE venue = 'polymarket_clob'
              AND COALESCE(placed_at_utc, created_at_utc) >= ?
            ORDER BY COALESCE(placed_at_utc, created_at_utc), execution_id
            """,
            (V2_CUTOVER,),
        )
    ]
    fills = [
        dict(row)
        for row in connection.execute(
            """
            SELECT f.fill_id, f.execution_id, f.order_id,
                   f.fill_qty AS filled_shares,
                   f.fill_price AS filled_price,
                   f.fees_usd, f.fee_source, f.fee_evidence_class,
                   f.fee_transaction_hash AS transaction_hash,
                   f.fill_ts_utc AS filled_at_utc,
                   o.maker_only, o.child_order_role, o.order_side,
                   o.requested_price, o.posted_price,
                   COALESCE(o.placed_at_utc, o.created_at_utc) AS order_at_utc
            FROM fact_trades AS f
            LEFT JOIN orders AS o USING (execution_id)
            WHERE f.trade_class = 'live_real'
              AND f.venue = 'polymarket_clob'
              AND f.fill_ts_utc >= ?
            ORDER BY f.fill_ts_utc, f.fill_id
            """,
            (V2_CUTOVER,),
        )
    ]
    return orders, fills


def _transport_payload(order: Mapping[str, Any]) -> dict[str, Any]:
    response = _json_object(order.get("exchange_response"))
    # Canonical rows may contain either the direct adapter response or the
    # legacy runner envelope.  Only direct named fields are audit evidence.
    candidates = [response]
    for key in ("transport", "adapter", "response", "exchange_response"):
        value = response.get(key)
        if isinstance(value, Mapping):
            candidates.append(dict(value))
    return next(
        (
            candidate
            for candidate in candidates
            if any(
                key in candidate
                for key in (
                    "estimated_fee_usd",
                    "fee_schedule_ref",
                    "collateral_asset",
                    "protocol_version",
                )
            )
        ),
        {},
    )


def _venue_order_id(order: Mapping[str, Any]) -> str | None:
    response = _json_object(order.get("exchange_response"))
    place = response.get("place") if isinstance(response.get("place"), Mapping) else {}
    value = _transport_payload(order).get("venue_order_id") or place.get("orderID")
    return str(value) if value else None


def audit_stage0a(
    orders: Sequence[Mapping[str, Any]],
    fills: Sequence[Mapping[str, Any]],
    *,
    fee_rate: Decimal = DEFAULT_FEE_RATE,
    fee_exponent: Decimal = DEFAULT_FEE_EXPONENT,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    fills_by_execution: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for fill in fills:
        fills_by_execution[str(fill.get("execution_id") or "")].append(fill)

    detail: list[dict[str, Any]] = []
    legacy_estimate_rows = 0
    old_total = Decimal("0")
    corrected_total = Decimal("0")
    wrong_rows = 0
    taker_wrong_rows = 0
    metadata_collateral_drift = 0
    metadata_schedule_drift = 0
    for order in orders:
        execution_id = str(order.get("execution_id") or "")
        transport = _transport_payload(order)
        old_estimate = _decimal(transport.get("estimated_fee_usd"))
        maker_only = order.get("maker_only") == 1 or transport.get("maker_only") is True
        shares = _decimal(transport.get("requested_shares")) or _decimal(order.get("shares"))
        price = (
            _decimal(transport.get("requested_price"))
            or _decimal(order.get("requested_price"))
            or _decimal(order.get("posted_price"))
            or _decimal(order.get("entry_price"))
        )
        corrected: Decimal | None = None
        if old_estimate is not None:
            legacy_estimate_rows += 1
            old_total += old_estimate
            if maker_only:
                corrected = Decimal("0")
            elif shares is not None and price is not None and Decimal("0") < price < Decimal("1"):
                corrected = estimate_polymarket_v2_fee(
                    shares=shares,
                    rate=fee_rate,
                    price=price,
                    exponent=fee_exponent,
                )
            if corrected is not None:
                corrected_total += corrected
                if corrected != old_estimate:
                    wrong_rows += 1
                    taker_wrong_rows += int(not maker_only)
        if transport.get("collateral_asset") == "USDC":
            metadata_collateral_drift += 1
        if transport.get("fee_schedule_ref") == "polymarket-token-fee-bps-v1":
            metadata_schedule_drift += 1
        actual_fills = fills_by_execution.get(execution_id, [])
        actual_fee = sum((_decimal(row.get("fees_usd")) or Decimal("0")) for row in actual_fills)
        if old_estimate is not None:
            detail.append(
                {
                    "execution_id": execution_id,
                    "order_id": order.get("order_id"),
                    "order_at_utc": order.get("placed_at_utc") or order.get("created_at_utc"),
                    "child_order_role": order.get("child_order_role"),
                    "maker_only": maker_only,
                    "shares": str(shares) if shares is not None else None,
                    "price": str(price) if price is not None else None,
                    "old_estimated_fee_usd": str(old_estimate),
                    "correct_v2_estimated_fee_usd": str(corrected) if corrected is not None else None,
                    "estimate_overstatement_usd": (
                        str(old_estimate - corrected) if corrected is not None else None
                    ),
                    "actual_fill_count": len(actual_fills),
                    "actual_fees_usd": str(actual_fee),
                    "actual_fee_sources": ";".join(
                        sorted({str(row.get("fee_source") or "") for row in actual_fills})
                    ),
                    "classification": (
                        "metadata_estimate_only_wrong"
                        if corrected is not None and old_estimate != corrected
                        else "estimate_matches_v2_formula"
                        if corrected is not None
                        else "insufficient_estimate_inputs"
                    ),
                    "collateral_metadata": transport.get("collateral_asset"),
                    "fee_schedule_metadata": transport.get("fee_schedule_ref"),
                }
            )

    fill_detail: list[dict[str, Any]] = []
    fill_fee_total = Decimal("0")
    fee_source_counts: Counter[str] = Counter()
    fee_evidence_counts: Counter[str] = Counter()
    taker_rebate_denominator = 0
    for fill in fills:
        fee = _decimal(fill.get("fees_usd")) or Decimal("0")
        fill_fee_total += fee
        source = str(fill.get("fee_source") or "legacy_unknown")
        fee_source_counts[source] += 1
        fee_evidence_counts[str(fill.get("fee_evidence_class") or "unknown")] += 1
        fill_at = str(fill.get("filled_at_utc") or fill.get("created_at_utc") or "")
        maker_only = str(fill.get("fee_source") or "") == "maker_zero"
        rebate_eligible_denominator = fill_at >= TAKER_REBATE_CUTOVER and not maker_only
        taker_rebate_denominator += int(rebate_eligible_denominator)
        fill_detail.append(
            {
                "fill_id": fill.get("fill_id"),
                "execution_id": fill.get("execution_id"),
                "order_id": fill.get("order_id"),
                "filled_at_utc": fill_at,
                "filled_shares": fill.get("filled_shares"),
                "filled_price": fill.get("filled_price"),
                "fees_usd": str(fee),
                "fee_source": source,
                "fee_evidence_class": fill.get("fee_evidence_class"),
                "maker_only": maker_only,
                "taker_rebate_denominator_after_cutover": rebate_eligible_denominator,
                "realized_taker_rebate_evidence": "missing",
            }
        )

    actual_side_effect_orders = sum(
        bool(_venue_order_id(order))
        for order in orders
    )
    summary = {
        "cutovers": {
            "clob_v2": V2_CUTOVER,
            "taker_rebate": TAKER_REBATE_CUTOVER,
        },
        "denominator": {
            "post_v2_order_rows": len(orders),
            "post_v2_actual_side_effect_order_rows": actual_side_effect_orders,
            "post_v2_fill_rows": len(fills),
            "legacy_estimate_rows": legacy_estimate_rows,
            "taker_rebate_candidate_fill_rows": taker_rebate_denominator,
        },
        "estimate_impact": {
            "rows_with_wrong_legacy_estimate": wrong_rows,
            "taker_rows_with_wrong_legacy_estimate": taker_wrong_rows,
            "old_estimated_fee_usd": str(old_total),
            "correct_v2_estimated_fee_usd": str(corrected_total),
            "overstatement_usd": str(old_total - corrected_total),
            "decision_changes": 0,
            "decision_change_basis": (
                "the transport estimate was emitted only after submit and is not read "
                "by CoreCarryRequestBuilder or risk admission"
            ),
        },
        "actual_fee_evidence": {
            "fill_fee_total_usd": str(fill_fee_total),
            "fee_source_counts": dict(sorted(fee_source_counts.items())),
            "fee_evidence_class_counts": dict(sorted(fee_evidence_counts.items())),
        },
        "metadata_drift": {
            "collateral_labeled_usdc_rows": metadata_collateral_drift,
            "legacy_fee_schedule_ref_rows": metadata_schedule_drift,
        },
        "gate": {
            "actual_fill_fee_provenance_known": all(
                str(fill.get("fee_evidence_class") or "") in {"exact", "estimate"}
                for fill in fills
            ),
            "actual_fill_fee_provenance_exact": all(
                str(fill.get("fee_evidence_class") or "") == "exact"
                for fill in fills
            ),
            "historical_raw_v2_fee_details_complete": False,
            "realized_taker_rebate_ledger_complete": False,
            "new_estimate_allowed_for_admission": False,
            "status": "FAIL_CLOSED",
        },
    }
    return summary, detail, fill_detail


def audit_journal_transport_metadata(
    journal_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Count post-submit transport labels from the append-only journal.

    The canonical ``orders.exchange_response`` payload is not guaranteed to
    retain every field emitted by the live transport.  The execution journal
    is therefore audited separately instead of silently treating absent DB
    fields as proof that the historical label was never emitted.
    """

    submitted_payloads: list[Mapping[str, Any]] = []
    for row in journal_rows:
        if row.get("event_type") != "outcome":
            continue
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if (
            payload.get("kind") == "submit"
            and payload.get("status") == "submitted"
            and payload.get("venue_order_id")
        ):
            submitted_payloads.append(payload)

    def counts(field: str) -> dict[str, int]:
        return dict(
            sorted(
                Counter(
                    str(payload[field])
                    for payload in submitted_payloads
                    if payload.get(field) not in (None, "")
                ).items()
            )
        )

    return {
        "submitted_side_effect_rows": len(submitted_payloads),
        "rows_with_estimated_fee": sum(
            payload.get("estimated_fee_usd") not in (None, "")
            for payload in submitted_payloads
        ),
        "rows_with_realized_maker_rebate": sum(
            payload.get("realized_maker_rebate_usd") not in (None, "")
            for payload in submitted_payloads
        ),
        "collateral_asset_counts": counts("collateral_asset"),
        "fee_schedule_ref_counts": counts("fee_schedule_ref"),
        "protocol_version_counts": counts("protocol_version"),
        "client_version_counts": counts("client_version"),
    }


def _walk_order_snapshots(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, Mapping):
        row = dict(value)
        if (
            row.get("order_state_provenance") == "polymarket_authenticated_order_lookup_v1"
            and row.get("order_id")
            and row.get("requested_shares") is not None
            and row.get("matched_shares") is not None
        ):
            yield {
                "id": row["order_id"],
                "owner": row.get("venue_owner"),
                "original_size": row["requested_shares"],
                "size_matched": row["matched_shares"],
                "status": row.get("raw_venue_status") or row.get("status"),
            }
        order_id = row.get("id") or row.get("order_id")
        if order_id and row.get("status") and row.get("original_size") is not None:
            yield row
        for child in row.values():
            yield from _walk_order_snapshots(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_order_snapshots(child)


def audit_stage0b(
    journal_rows: Sequence[Mapping[str, Any]],
    live_rows: Sequence[Mapping[str, Any]],
    orders: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    malformed_journal = sum("_malformed_line" in row for row in journal_rows)
    attempts: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    outcomes: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    submit_outcomes: list[Mapping[str, Any]] = []
    owners: set[str] = set()
    for row in journal_rows:
        if row.get("owner"):
            owners.add(str(row["owner"]))
        payload = row.get("payload") if isinstance(row.get("payload"), Mapping) else {}
        identity = str(payload.get("identity_key") or "")
        if row.get("event_type") == "attempt_before_side_effect" and identity:
            attempts[identity].append(row)
        if row.get("event_type") == "outcome" and identity:
            outcomes[identity].append(row)
            if payload.get("kind") == "submit":
                submit_outcomes.append(row)

    submitted = [
        row
        for row in submit_outcomes
        if isinstance(row.get("payload"), Mapping)
        and row["payload"].get("status") == "submitted"
        and row["payload"].get("venue_order_id")
    ]
    identity_to_orders: dict[str, set[str]] = defaultdict(set)
    order_to_identities: dict[str, set[str]] = defaultdict(set)
    for row in submitted:
        payload = row["payload"]
        identity = str(payload["identity_key"])
        order_id = str(payload["venue_order_id"])
        identity_to_orders[identity].add(order_id)
        order_to_identities[order_id].add(identity)

    rest_snapshots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for live_row in live_rows:
        received_at = str(live_row.get("created_at_utc") or live_row.get("decision_snapshot_ts_utc") or "")
        for snapshot in _walk_order_snapshots(live_row.get("exchange_response")):
            order_id = str(snapshot.get("id") or snapshot.get("order_id") or "")
            if order_id:
                rest_snapshots[order_id].append(
                    {
                        "received_at_utc": received_at,
                        "payload": snapshot,
                        "source_line": live_row.get("_line_number"),
                    }
                )

    db_by_order: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for order in orders:
        if order.get("order_id"):
            db_by_order[str(order["order_id"])].append(order)

    detail: list[dict[str, Any]] = []
    rest_clean = terminal = 0
    rest_blockers: Counter[str] = Counter()
    for row in submitted:
        payload = row["payload"]
        identity = str(payload["identity_key"])
        order_id = str(payload["venue_order_id"])
        snapshots = sorted(rest_snapshots.get(order_id, []), key=lambda item: item["received_at_utc"])
        state = None
        if snapshots:
            reducer = OwnOrderTruthReducer(
                expected_order_id=order_id,
                lifecycle_owner=str(row.get("writer_id") or row.get("owner") or "current_yes_core_carry_tiny_live_v2"),
            )
            observations = [
                {
                    "source": "rest_order",
                    "received_at_utc": snapshot["received_at_utc"],
                    "payload": snapshot["payload"],
                }
                for snapshot in snapshots
                if snapshot["received_at_utc"]
            ]
            state = reducer.replay(observations)
            rest_clean += int(state.clean)
            terminal += int(state.terminal)
            for blocker in state.blockers:
                rest_blockers[blocker.code] += 1
        detail.append(
            {
                "identity_key": identity,
                "venue_order_id": order_id,
                "child_role": payload.get("child_role"),
                "submitted_at_utc": row.get("recorded_at_utc"),
                "requested_shares": payload.get("requested_shares"),
                "requested_price": payload.get("requested_price"),
                "journal_attempt_count": len(attempts.get(identity, [])),
                "journal_outcome_count": len(outcomes.get(identity, [])),
                "venue_ids_for_identity": len(identity_to_orders[identity]),
                "identities_for_venue_id": len(order_to_identities[order_id]),
                "db_order_rows": len(db_by_order.get(order_id, [])),
                "rest_snapshot_count": len(snapshots),
                "private_user_ws_event_count": 0,
                "replay_status": None if state is None else state.status,
                "replay_matched_shares": None if state is None else str(state.effective_matched_shares),
                "replay_remaining_shares": None if state is None or state.remaining_shares is None else str(state.remaining_shares),
                "replay_terminal": None if state is None else state.terminal,
                "replay_clean": None if state is None else state.clean,
                "replay_blockers": "|".join(blocker.code for blocker in state.blockers) if state else "",
                "authoritative_state_version": None if state is None else state.authoritative_state_version,
            }
        )

    latest_status_by_identity: dict[str, str] = {}
    for identity, rows in outcomes.items():
        rows = sorted(rows, key=lambda row: str(row.get("recorded_at_utc") or ""))
        payload = rows[-1].get("payload") if isinstance(rows[-1].get("payload"), Mapping) else {}
        latest_status_by_identity[identity] = str(payload.get("status") or "")
    journal_unresolved_unknown = sorted(
        identity for identity, status in latest_status_by_identity.items() if status == "unknown"
    )
    unknown_ever = sorted(
        identity
        for identity, rows in outcomes.items()
        if any(
            isinstance(row.get("payload"), Mapping)
            and row["payload"].get("status") == "unknown"
            for row in rows
        )
    )
    explicit_reject_unknown = sorted(
        identity
        for identity in journal_unresolved_unknown
        if any(
            "status_code=403" in str(
                (row.get("payload") or {}).get("error")
                if isinstance(row.get("payload"), Mapping)
                else ""
            )
            for row in outcomes[identity]
        )
    )
    unresolved_unknown = sorted(set(journal_unresolved_unknown) - set(explicit_reject_unknown))
    duplicate_side_effect_identities = sorted(
        identity for identity, values in identity_to_orders.items() if len(values) > 1
    )
    shared_venue_ids = sorted(
        order_id for order_id, values in order_to_identities.items() if len(values) > 1
    )
    orphan_submits = sorted(
        str(row["payload"]["identity_key"])
        for row in submitted
        if not attempts.get(str(row["payload"]["identity_key"]))
    )
    summary = {
        "denominator": {
            "journal_rows": len(journal_rows),
            "malformed_journal_rows": malformed_journal,
            "unique_attempt_identities": len(attempts),
            "submit_outcome_rows": len(submit_outcomes),
            "submitted_order_rows": len(submitted),
            "unique_submitted_venue_orders": len(order_to_identities),
            "live_order_rows": len(live_rows),
        },
        "identity": {
            "lifecycle_owners": sorted(owners),
            "orphan_submitted_identities": orphan_submits,
            "duplicate_side_effect_identities": duplicate_side_effect_identities,
            "venue_ids_shared_across_identities": shared_venue_ids,
            "unknown_ever_identities": unknown_ever,
            "journal_unknown_but_explicit_http_reject_identities": explicit_reject_unknown,
            "unresolved_unknown_identities": unresolved_unknown,
        },
        "rest_replay": {
            "orders_with_rest_snapshot": sum(bool(row["rest_snapshot_count"]) for row in detail),
            "orders_with_clean_rest_replay": rest_clean,
            "orders_terminal_in_rest_replay": terminal,
            "blocker_counts": dict(sorted(rest_blockers.items())),
        },
        "private_user_ws": {
            "captured_events": 0,
            "documented_sequence_available": False,
            "reconnect_gap_policy": "REST reconcile required after every reconnect",
        },
        "gate": {
            "orphan_zero": not orphan_submits,
            "duplicate_side_effect_zero": not duplicate_side_effect_identities and not shared_venue_ids,
            "all_unknown_reconciled": not unresolved_unknown,
            "private_user_ws_wired": False,
            "sole_target_order_reconciler_wired": False,
            "identity_parity_100pct": (
                bool(detail)
                and all(row["journal_attempt_count"] >= 1 for row in detail)
                and not orphan_submits
                and not duplicate_side_effect_identities
                and not shared_venue_ids
            ),
            "status": "FAIL_CLOSED",
        },
    }
    return summary, detail


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(args: argparse.Namespace) -> dict[str, Any]:
    db_path = Path(args.db)
    journal_path = Path(args.execution_journal)
    live_orders_path = Path(args.live_orders)
    output_dir = prepare_new_run_output(
        resolve_run_output(
            "weather_mm_stage0_truth_audit_v1",
            run_id=args.run_id,
            explicit_output=Path(args.output_dir) if args.output_dir else None,
        )
    )
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    try:
        orders, fills = _db_rows(connection)
        fact_built_at = connection.execute(
            "SELECT MAX(fact_built_at_utc) FROM fact_trades"
        ).fetchone()[0]
    finally:
        connection.close()
    journal_rows = list(_iter_jsonl(journal_path))
    live_rows = list(_iter_jsonl(live_orders_path))
    stage0a, estimate_rows, fill_rows = audit_stage0a(orders, fills)
    stage0a["journal_transport_metadata"] = audit_journal_transport_metadata(
        journal_rows
    )
    stage0b, lifecycle_rows = audit_stage0b(journal_rows, live_rows, orders)
    _write_csv(output_dir / "stage0a_order_estimate_impact.csv", estimate_rows)
    _write_csv(output_dir / "stage0a_fill_fee_evidence.csv", fill_rows)
    _write_csv(output_dir / "stage0b_order_lifecycle_replay.csv", lifecycle_rows)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_status": "correctness_audit_fail_closed",
        "inputs": {
            "db": str(db_path),
            "db_device": db_path.stat().st_dev,
            "db_inode": db_path.stat().st_ino,
            "fact_trades_built_at_utc": fact_built_at,
            "execution_journal": str(journal_path),
            "execution_journal_sha256": _sha256(journal_path),
            "live_orders": str(live_orders_path),
            "live_orders_sha256": _sha256(live_orders_path),
        },
        "stage0a": stage0a,
        "stage0b": stage0b,
        "outputs": {
            "order_estimate_impact_csv": str(output_dir / "stage0a_order_estimate_impact.csv"),
            "fill_fee_evidence_csv": str(output_dir / "stage0a_fill_fee_evidence.csv"),
            "order_lifecycle_replay_csv": str(output_dir / "stage0b_order_lifecycle_replay.csv"),
        },
        "limitations": [
            "Historical raw V2 feeDetails were not captured for every order.",
            "No private User Channel events exist in the supplied runtime; REST replay is not a substitute.",
            "No realized taker-rebate payout ledger exists, so the post-2026-05-28 taker baseline remains incomplete.",
            "This audit is read-only and does not certify the current production manifest.",
        ],
    }
    report_path = output_dir / "report.json"
    payload["outputs"]["report_json"] = str(report_path)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser()
    out.add_argument("--db", required=True)
    out.add_argument("--execution-journal", required=True)
    out.add_argument("--live-orders", required=True)
    out.add_argument("--run-id")
    out.add_argument("--output-dir")
    return out


def main() -> int:
    payload = run(parser().parse_args())
    print(
        json.dumps(
            {
                "stage0a": payload["stage0a"]["gate"],
                "stage0b": payload["stage0b"]["gate"],
                "outputs": payload["outputs"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
