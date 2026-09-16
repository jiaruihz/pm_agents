"""Build append-only zero-notional intents from selected dispute candidates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.platform.execution_runtime import TradeIntent
from src.platform.storage.jsonl import append_jsonl_row


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open() as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    return rows


SIGNAL_JOURNALS = ("signals.jsonl", "clarification_signals.jsonl")


def read_signal_candidates(output_root: Path) -> list[dict[str, Any]]:
    """Read the independently-owned deterministic and clarification journals."""

    candidates: dict[str, dict[str, Any]] = {}
    for filename in SIGNAL_JOURNALS:
        for row in _read_jsonl(output_root / filename):
            candidate_id = str(row.get("candidate_id") or "")
            if candidate_id:
                candidates[candidate_id] = row
    return list(candidates.values())


def _latest_snapshots(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        case_id = str(row.get("case_id") or "")
        if case_id and str(row.get("captured_at_utc") or "") >= str(
            latest.get(case_id, {}).get("captured_at_utc") or ""
        ):
            latest[case_id] = row
    return latest


def _snapshots_by_case_time(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(row.get("case_id") or ""), str(row.get("captured_at_utc") or "")): row
        for row in _read_jsonl(path)
    }


def _book_snapshot_for_token(snapshot: dict[str, Any], token_id: str) -> str | None:
    group = snapshot.get("market_group_snapshot") or {}
    value = (group.get("book_snapshot_ids") or {}).get(token_id)
    if value:
        return str(value)
    capture = (snapshot.get("book_captures") or {}).get(token_id) or {}
    return str(capture.get("book_capture_id") or "") or None


def sync_zero_notional_trade_intents(output_root: Path) -> dict[str, Any]:
    signals = read_signal_candidates(output_root)
    snapshots = _latest_snapshots(output_root / "snapshots.jsonl")
    snapshots_by_case_time = _snapshots_by_case_time(output_root / "snapshots.jsonl")
    intents_path = output_root / "trade_intents.jsonl"
    existing_ids = {
        str(row.get("intent_id") or "") for row in _read_jsonl(intents_path)
    }
    rows: list[dict[str, Any]] = []
    blocked_missing_mapping = 0
    for signal in signals:
        if not bool(signal.get("eligible_shadow")) or not bool(signal.get("zero_notional")):
            continue
        case_id = str(signal.get("case_id") or signal.get("source_snapshot_case_id") or "")
        snapshot = snapshots_by_case_time.get(
            (case_id, str(signal.get("snapshot_ts_utc") or "")),
            snapshots.get(case_id, {}),
        )
        token_id = str(signal.get("selected_token") or signal.get("reverse_token") or "")
        outcome = str(signal.get("selected_outcome") or signal.get("reverse_outcome") or "")
        condition_id = str(signal.get("condition_id") or snapshot.get("condition_id") or "")
        if not case_id or not token_id or not outcome or not condition_id:
            blocked_missing_mapping += 1
            continue
        policy_id = str(signal.get("policy_id") or "")
        candidate_id = str(signal.get("candidate_id") or "")
        cluster = str(signal.get("opportunity_cluster_id") or case_id)
        cost = signal.get("selected_ask_vwap")
        if cost is None:
            cost = signal.get("reverse_ask_vwap")
        if cost is None:
            cost = signal.get("ask_vwap")
        execution_book_snapshot_id = str(
            signal.get("execution_book_snapshot_id")
            or _book_snapshot_for_token(snapshot, token_id)
            or ""
        ) or None
        intent = TradeIntent.create(
            candidate_id=candidate_id,
            strategy_key="rule_lawyer.dispute_repricing",
            policy_id=policy_id,
            condition_id=condition_id,
            token_id=token_id,
            venue_side="BUY",
            outcome_label=outcome,
            requested_size=0.0,
            sizing_profile="zero_notional_v1",
            execution_profile="taker_25share_observation_v1",
            dedupe_key=f"{policy_id}|{candidate_id}",
            exposure_bucket=f"{policy_id}|{cluster}",
            mode="zero_notional",
            created_at_utc=str(
                signal.get("quote_observed_at_utc")
                or signal.get("snapshot_ts_utc")
                or snapshot.get("captured_at_utc")
            ),
            max_cost=None if cost is None else float(cost),
            ttl_seconds=300,
            execution_book_snapshot_id=execution_book_snapshot_id,
            metadata={
                "case_id": case_id,
                "market_id": signal.get("market_id"),
                "opportunity_cluster_id": cluster,
                "intended_observation_quantity": signal.get("quantity"),
                "fee_adjusted_edge": (
                    signal.get("policy_edge_per_share")
                    if signal.get("policy_edge_per_share") is not None
                    else signal.get("net_edge_per_share")
                ),
                "no_live_authority": True,
                "price_lineage_status": (
                    "candidate_time_book"
                    if execution_book_snapshot_id
                    else "candidate_embedded_legacy"
                ),
            },
        )
        if intent.intent_id not in existing_ids:
            rows.append(intent.to_dict())
            existing_ids.add(intent.intent_id)
    if rows:
        for row in rows:
            append_jsonl_row(intents_path, row)
    return {
        "schema_version": "dispute_trade_intent_sync_v1",
        "selected_zero_notional_candidates": sum(
            bool(row.get("eligible_shadow")) and bool(row.get("zero_notional"))
            for row in signals
        ),
        "new_trade_intents": len(rows),
        "total_trade_intents": len(existing_ids),
        "blocked_missing_market_mapping": blocked_missing_mapping,
        "live_authority": False,
    }
