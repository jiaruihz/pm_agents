#!/usr/bin/env python3
"""Fail-closed seal for the Weather-first MM W2 narrow-maker shadow.

This deliberately evaluates every first-positive signal in the input denominator.
It never infers a maker fill from public book behaviour: only an explicitly marked,
authoritative own-order lifecycle record can enter the maker outcome column.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import random
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from src.platform.market_data.ws_incremental_book import (
    BookReconstructionError,
    canonical_ws_frame_id,
    materialize_reconstructed_books,
)
from src.strategies.weather_edge_v1.execution.contracts import (
    BookLevel,
    ExecutionContractError,
    FeeSchedule,
    MarketBook,
    VenueCapabilities,
)
from src.strategies.weather_edge_v1.execution.selective_maker import (
    PITMarketState,
    PassiveFillEstimate,
    RouteInput,
    TransitionHazardEstimate,
    route_candidate,
)
from src.strategies.weather_edge_v1.tools.current_yes_core_carry import (
    official_weather_fee_per_share,
)


DEFAULT_PREREG = Path("src/strategies/weather_edge_v1/config/weather_mm_w2_narrow_maker_prereg_v1.json")
DEFAULT_RECONNECT_AUDIT = Path("/private/tmp/w2_reconnect_audit")
DECISIONS_FILE = "market_state_decisions.jsonl"


def decimal(value: Any, default: Decimal | None = None) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return default
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default
    return parsed if parsed.is_finite() else default


def money(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl_snapshot(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read one exact append-only prefix and hash the bytes actually consumed."""

    rows: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    bytes_consumed = 0
    physical_lines = 0
    with path.open("rb") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            physical_lines = line_no
            digest.update(raw_line)
            bytes_consumed += len(raw_line)
            if not raw_line.strip():
                continue
            try:
                item = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError(f"{path}:{line_no}: incomplete or invalid JSONL row") from exc
            if not isinstance(item, dict):
                raise ValueError(f"{path}:{line_no}: expected object")
            rows.append(item)
    return rows, {
        "path": str(path),
        "sha256_consumed_prefix": digest.hexdigest(),
        "bytes_consumed": bytes_consumed,
        "physical_lines_consumed": physical_lines,
        "object_rows_consumed": len(rows),
    }


def state_details(
    row: dict[str, Any], prereg: Mapping[str, Any]
) -> tuple[str | None, str]:
    aliases = dict(prereg.get("state_aliases") or {})
    raw = row.get("candidate_state_v2") or row.get("candidate_state")
    if not raw and isinstance(row.get("state_v2"), dict):
        raw = row["state_v2"].get("candidate_state")
    fallback = prereg.get("causal_policy_state_fallback")
    replace_unknown = (
        isinstance(fallback, Mapping)
        and fallback.get("enabled") is True
        and fallback.get("replace_direct_unknown") is True
        and str(raw or "").lower() in {"unknown", "unavailable", "none"}
    )
    if raw and not replace_unknown:
        return aliases.get(str(raw), str(raw)), "direct_candidate_state"

    # V2.1 repairs the impossible dependency between an on-signal subscription
    # and the old classifier's required pre-signal window.  It may reuse only a
    # preregistered, causal stage action whose information horizon is explicit;
    # it never consults terminal price or a later fill.  This is a new forward
    # hypothesis, not a relabeling of the frozen V1 result.
    if isinstance(fallback, Mapping) and fallback.get("enabled") is True:
        stage = str(fallback.get("stage") or "")
        action_row = (
            (row.get("maker_policy_actions_by_stage") or {}).get(stage)
            if isinstance(row.get("maker_policy_actions_by_stage"), Mapping)
            else None
        )
        policy = (
            action_row.get("policy")
            if isinstance(action_row, Mapping)
            and isinstance(action_row.get("policy"), Mapping)
            else (row.get("maker_policy_v3") or {}).get(stage)
            if isinstance(row.get("maker_policy_v3"), Mapping)
            else None
        )
        if isinstance(policy, Mapping):
            required_reason = str(fallback.get("required_reason") or "")
            reasons = {str(value) for value in policy.get("reason_codes") or ()}
            horizon = decimal(policy.get("information_horizon_seconds"))
            required_horizon = decimal(fallback.get("information_horizon_seconds"))
            if (
                str(policy.get("action") or "") == str(fallback.get("action") or "")
                and (not required_reason or required_reason in reasons)
                and horizon is not None
                and horizon == required_horizon
            ):
                mapped = str(fallback.get("mapped_state") or "")
                if mapped:
                    return aliases.get(mapped, mapped), f"causal_policy_{stage}"
    return (str(raw), "direct_unknown") if raw else (None, "state_unavailable")


def state_for(
    row: dict[str, Any], aliases_or_prereg: Mapping[str, Any]
) -> str | None:
    # Backward-compatible public helper: callers may still pass only aliases.
    prereg = (
        dict(aliases_or_prereg)
        if "state_aliases" in aliases_or_prereg
        else {"state_aliases": dict(aliases_or_prereg)}
    )
    return state_details(row, prereg)[0]


def join_policy_actions(
    rows: list[dict[str, Any]], actions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Join the latest append-only action revision without mutating raw rows."""

    by_event: dict[str, dict[str, dict[str, Any]]] = {}
    by_signal: dict[str, dict[str, dict[str, Any]]] = {}
    for action in actions:
        stage = str(action.get("stage") or "")
        if not stage:
            continue
        event_id = str(action.get("event_id") or "")
        signal_id = str(action.get("signal_id") or "")
        if event_id:
            by_event.setdefault(event_id, {})[stage] = action
        if signal_id:
            by_signal.setdefault(signal_id, {})[stage] = action
    output: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        event_id = str(row.get("event_id") or "")
        signal_id = str(row.get("signal_id") or "")
        joined = by_event.get(event_id) or by_signal.get(signal_id) or {}
        row["maker_policy_actions_by_stage"] = dict(joined)
        output.append(row)
    return output


def join_decision_packet_model_inputs(
    rows: list[dict[str, Any]],
    decision_packets_path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Join the frozen weather probability without importing future outcomes."""

    if decision_packets_path is None:
        return rows, {
            "attempted": False,
            "path": None,
            "matched_rows": 0,
            "missing_rows": len(rows),
            "identity_conflicts": [],
        }
    if not decision_packets_path.exists():
        raise FileNotFoundError(decision_packets_path)
    packets, prefix_identity = read_jsonl_snapshot(decision_packets_path)
    revisions: dict[str, list[dict[str, Any]]] = {}
    for packet in packets:
        signal = packet.get("signal")
        signal_id = (
            str(signal.get("signal_id") or "")
            if isinstance(signal, Mapping)
            else ""
        )
        if signal_id:
            revisions.setdefault(signal_id, []).append(packet)
    latest = {identity: values[-1] for identity, values in revisions.items()}

    def invariant(packet: Mapping[str, Any]) -> dict[str, Any]:
        signal = packet.get("signal")
        signal = signal if isinstance(signal, Mapping) else {}
        trigger = packet.get("trigger")
        trigger = trigger if isinstance(trigger, Mapping) else {}
        payload = trigger.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        return {
            "signal": dict(signal),
            "trigger_status": trigger.get("status"),
            "model_probability_hold": payload.get("model_probability_hold"),
            "probability_status": payload.get("probability_status"),
            "model_version": payload.get("model_version"),
            "artifact_hash": payload.get("artifact_hash"),
            "feature_schema_version": payload.get("feature_schema_version"),
            "feature_version_manifest": payload.get("feature_version_manifest"),
            "current_yes_tick_size": payload.get("current_yes_tick_size"),
            "strategy_identity": packet.get("strategy_identity"),
            "snapshot_references": packet.get("snapshot_references"),
            "decision_clocks": packet.get("decision_clocks"),
        }

    all_revision_invariant_conflicts = sorted(
        identity
        for identity, values in revisions.items()
        if len(
            {
                json.dumps(
                    invariant(packet),
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )
                for packet in values
            }
        )
        > 1
    )
    requested_identities = {row_identity(row) for row in rows if row_identity(row)}
    revision_invariant_conflicts = sorted(
        requested_identities.intersection(all_revision_invariant_conflicts)
    )
    revision_lineage = {
        identity: [row_sha256(packet) for packet in values]
        for identity, values in revisions.items()
        if identity in requested_identities and len(values) > 1
    }
    output: list[dict[str, Any]] = []
    token_conflicts: list[str] = []
    matched = 0
    for raw in rows:
        row = dict(raw)
        signal_id = row_identity(row)
        packet = (
            None
            if signal_id in revision_invariant_conflicts
            else latest.get(signal_id)
        )
        if packet is not None:
            signal = packet.get("signal")
            signal = signal if isinstance(signal, Mapping) else {}
            trigger = packet.get("trigger")
            trigger = trigger if isinstance(trigger, Mapping) else {}
            payload = trigger.get("payload")
            payload = payload if isinstance(payload, Mapping) else {}
            packet_token = str(signal.get("token_id") or "")
            counterfactuals = row.get("execution_counterfactuals")
            counterfactuals = (
                counterfactuals if isinstance(counterfactuals, Mapping) else {}
            )
            maker = counterfactuals.get("shared_passive_maker")
            maker = maker if isinstance(maker, Mapping) else {}
            row_token = str(maker.get("token_id") or "")
            if packet_token and row_token and packet_token != row_token:
                token_conflicts.append(signal_id)
            else:
                row["v2_1_model_input"] = {
                    "packet_id": packet.get("packet_id"),
                    "model_probability_hold": payload.get(
                        "model_probability_hold"
                    ),
                    "probability_status": payload.get("probability_status"),
                    "model_version": payload.get("model_version"),
                    "artifact_hash": payload.get("artifact_hash"),
                    "feature_schema_version": payload.get(
                        "feature_schema_version"
                    ),
                    "feature_version_manifest": payload.get(
                        "feature_version_manifest"
                    ),
                    "current_yes_tick_size": payload.get(
                        "current_yes_tick_size"
                    ),
                    "strategy_identity": packet.get("strategy_identity"),
                    "source_clock": "signal_decision_t0_frozen_for_t30",
                }
                matched += 1
        output.append(row)
    return output, {
        "attempted": True,
        "path": str(decision_packets_path),
        "prefix_identity": prefix_identity,
        "packet_signal_identities": len(latest),
        "revised_identities": len(revision_lineage),
        "revision_rows_beyond_first": sum(
            len(revisions[identity]) - 1 for identity in revision_lineage
        ),
        "revision_lineage_sha256": revision_lineage,
        "revision_invariant_conflicts": revision_invariant_conflicts,
        "unscoped_revision_invariant_conflict_count": (
            len(all_revision_invariant_conflicts)
            - len(revision_invariant_conflicts)
        ),
        "matched_rows": matched,
        "missing_rows": len(rows) - matched,
        "token_identity_conflicts": sorted(set(token_conflicts)),
        "identity_conflicts": sorted(
            set(token_conflicts) | set(revision_invariant_conflicts)
        ),
    }


def policy_for(row: Mapping[str, Any], stage: str) -> Mapping[str, Any] | None:
    actions = row.get("maker_policy_actions_by_stage")
    action = actions.get(stage) if isinstance(actions, Mapping) else None
    if isinstance(action, Mapping) and isinstance(action.get("policy"), Mapping):
        return action["policy"]
    policies = row.get("maker_policy_v3")
    policy = policies.get(stage) if isinstance(policies, Mapping) else None
    return policy if isinstance(policy, Mapping) else None


def shadow_comparison_readiness(
    row: Mapping[str, Any], prereg: Mapping[str, Any]
) -> tuple[bool, tuple[str, ...]]:
    """Validate a no-fill maker/taker/skip row at one causal decision clock."""

    blockers: list[str] = []
    fallback = prereg.get("causal_policy_state_fallback")
    stage = str(fallback.get("stage") or "") if isinstance(fallback, Mapping) else ""
    policy = policy_for(row, stage) if stage else None
    if policy is None:
        blockers.append("same_stage_maker_policy_missing")
        maker_shares = maker_limit = None
    else:
        maker_shares = decimal(policy.get("requested_shares"))
        maker_limit = decimal(policy.get("limit_price"))
        if maker_shares is None or maker_shares <= 0:
            blockers.append("same_stage_maker_shares_missing")
        if maker_limit is None or not Decimal("0") < maker_limit < Decimal("1"):
            blockers.append("same_stage_post_only_limit_missing")
    cfs = row.get("execution_counterfactuals")
    cfs = cfs if isinstance(cfs, Mapping) else {}
    baseline_keys = tuple(
        prereg.get("same_stage_baseline", {}).get(
            "taker_counterfactual_keys", ("immediate_taker",)
        )
    )
    baseline_key = next(
        (
            key
            for key in baseline_keys
            if isinstance(cfs.get(key), Mapping)
            and decimal(cfs[key].get("all_in_cost")) is not None
        ),
        None,
    )
    taker = cfs.get(baseline_key) if baseline_key else None
    taker_shares = decimal(taker.get("shares")) if isinstance(taker, Mapping) else None
    if baseline_key is None:
        blockers.append("same_stage_taker_baseline_missing")
    elif maker_shares is None or taker_shares != maker_shares:
        blockers.append("same_stage_maker_taker_shares_mismatch")
    skip = cfs.get("skip_incremental")
    if (
        not isinstance(skip, Mapping)
        or decimal(skip.get("shares")) != Decimal("0")
        or decimal(skip.get("all_in_cost")) != Decimal("0")
    ):
        blockers.append("same_row_skip_baseline_missing")
    return not blockers, tuple(sorted(set(blockers)))


def _utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _iter_jsonl_any(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL") from exc
            if isinstance(row, dict):
                yield row


def _decision_epoch_ids(row: Mapping[str, Any]) -> tuple[str, ...]:
    coverage = row.get("coverage_v2")
    streams = coverage.get("streams") if isinstance(coverage, Mapping) else None
    values: set[str] = set()
    for stream in streams or ():
        if (
            isinstance(stream, Mapping)
            and stream.get("stream") == "current_book"
            and stream.get("window") == "decision"
        ):
            values.update(
                str(value)
                for value in stream.get("subscription_epoch_ids") or ()
                if value
            )
    return tuple(sorted(values))


def _hourly_frame_paths(
    ws_root: Path, start_at: datetime, end_at: datetime
) -> tuple[Path, ...]:
    cursor = start_at.replace(minute=0, second=0, microsecond=0)
    finish = end_at.replace(minute=0, second=0, microsecond=0)
    paths: list[Path] = []
    while cursor <= finish:
        directory = ws_root / cursor.date().isoformat()
        prefix = cursor.strftime("market_books_ws_%Y%m%d_%H_")
        paths.extend(sorted(directory.glob(f"{prefix}*.jsonl*")))
        cursor += timedelta(hours=1)
    return tuple(sorted(set(paths)))


def reconstruct_same_stage_taker_baselines(
    rows: list[dict[str, Any]],
    prereg: Mapping[str, Any],
    ws_root: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Backfill a PIT t30 taker arm from the exact public WS book chain.

    This is an execution counterfactual only.  It does not infer maker fills,
    queue priority, or realized fees/rewards.
    """

    contract = prereg.get("same_stage_baseline")
    if (
        ws_root is None
        or not isinstance(contract, Mapping)
        or contract.get("reconstruct_from_ws") is not True
    ):
        return rows, {
            "attempted": False,
            "ws_root": None if ws_root is None else str(ws_root),
            "ready_rows": 0,
            "failures": {},
        }
    stage_seconds = int(contract.get("information_horizon_seconds") or 30)
    output_key = str(contract.get("reconstructed_taker_key") or "same_stage_taker")
    allowed = set(prereg.get("allowed_regimes") or ())
    candidates: list[tuple[int, dict[str, Any], datetime, tuple[str, ...], Decimal]] = []
    failures: Counter[str] = Counter()
    wanted_epochs: set[str] = set()
    for index, row in enumerate(rows):
        regime, _ = state_details(row, prereg)
        if regime not in allowed:
            continue
        event_at = _utc(row.get("signal_event_at_utc"))
        fallback = prereg.get("causal_policy_state_fallback")
        stage = str(fallback.get("stage") or "") if isinstance(fallback, Mapping) else ""
        policy = policy_for(row, stage) if stage else None
        shares = decimal(policy.get("requested_shares")) if policy else None
        epoch_ids = _decision_epoch_ids(row)
        if event_at is None:
            failures["event_clock_missing"] += 1
        elif shares is None or shares <= 0:
            failures["maker_shares_missing"] += 1
        elif not epoch_ids:
            failures["decision_epoch_identity_missing"] += 1
        else:
            candidates.append((index, row, event_at, epoch_ids, shares))
            wanted_epochs.update(epoch_ids)

    all_epoch_rows: dict[str, dict[str, Any]] = {}
    for path in sorted((ws_root / "subscription_epochs").glob("*.jsonl*")):
        for epoch in _iter_jsonl_any(path):
            identity = str(epoch.get("subscription_epoch_id") or "")
            if identity:
                all_epoch_rows[identity] = epoch

    # A selector-reconcile epoch can legally carry the prior verified state.
    # Reconstruct the same predecessor prefix used by the production feature
    # producer instead of pretending the first in-window epoch is a fresh root.
    expanded_candidates: list[
        tuple[int, dict[str, Any], datetime, tuple[str, ...], Decimal]
    ] = []
    for index, row, event_at, epoch_ids, shares in candidates:
        selected = list(epoch_ids)
        first = min(
            (all_epoch_rows.get(identity) for identity in selected),
            key=lambda item: str((item or {}).get("started_at_utc") or ""),
            default=None,
        )
        steps = 0
        pre_window_start = event_at - timedelta(seconds=120)
        while isinstance(first, Mapping) and steps < 64:
            first_started = _utc(first.get("started_at_utc"))
            if (
                str(first.get("reason") or "") != "selector_reconcile"
                and first_started is not None
                and first_started <= pre_window_start
            ):
                break
            previous = str(first.get("previous_subscription_epoch_id") or "")
            if not previous or previous in selected or previous not in all_epoch_rows:
                break
            selected.insert(0, previous)
            first = all_epoch_rows[previous]
            steps += 1
        ordered_ids = tuple(
            sorted(
                selected,
                key=lambda identity: str(
                    all_epoch_rows.get(identity, {}).get("started_at_utc") or ""
                ),
            )
        )
        expanded_candidates.append((index, row, event_at, ordered_ids, shares))
        wanted_epochs.update(ordered_ids)
    candidates = expanded_candidates
    epoch_rows = {
        identity: all_epoch_rows[identity]
        for identity in wanted_epochs
        if identity in all_epoch_rows
    }
    frames_by_epoch: dict[str, list[dict[str, Any]]] = {
        identity: [] for identity in wanted_epochs
    }
    frame_paths: set[Path] = set()
    maximum_end: dict[str, datetime] = {}
    minimum_start: dict[str, datetime] = {}
    for _, _, event_at, epoch_ids, _ in candidates:
        end_at = event_at + timedelta(seconds=stage_seconds)
        starts = [
            _utc(epoch_rows[identity].get("started_at_utc"))
            for identity in epoch_ids
            if identity in epoch_rows
        ]
        start_at = min((value for value in starts if value is not None), default=None)
        if start_at is None:
            continue
        frame_paths.update(_hourly_frame_paths(ws_root, start_at, end_at))
        for identity in epoch_ids:
            prior_start = minimum_start.get(identity)
            prior_end = maximum_end.get(identity)
            minimum_start[identity] = start_at if prior_start is None else min(prior_start, start_at)
            maximum_end[identity] = end_at if prior_end is None else max(prior_end, end_at)
    seen_frames: set[str] = set()
    for path in sorted(frame_paths):
        for frame in _iter_jsonl_any(path):
            identity = str(frame.get("subscription_epoch_id") or "")
            if identity not in wanted_epochs:
                continue
            received = _utc(frame.get("received_at_utc"))
            if (
                received is None
                or received < minimum_start.get(identity, received)
                or received > maximum_end.get(identity, received)
            ):
                continue
            frame_id = canonical_ws_frame_id(frame)
            if frame_id in seen_frames:
                continue
            seen_frames.add(frame_id)
            frames_by_epoch[identity].append(frame)

    enriched = [dict(row) for row in rows]
    evidence: list[dict[str, Any]] = []
    for index, row, event_at, epoch_ids, shares in candidates:
        epochs = [epoch_rows[identity] for identity in epoch_ids if identity in epoch_rows]
        if len(epochs) != len(epoch_ids):
            failures["subscription_epoch_manifest_missing"] += 1
            continue
        stage_at = event_at + timedelta(seconds=stage_seconds)
        frames = [
            frame
            for identity in epoch_ids
            for frame in frames_by_epoch.get(identity, ())
            if (_utc(frame.get("received_at_utc")) or stage_at) <= stage_at
        ]
        try:
            run = materialize_reconstructed_books(
                epochs,
                frames,
                requested_shares=float(shares),
                strict_best_parity=True,
            )
        except (BookReconstructionError, ValueError) as exc:
            failures[f"reconstruction_error:{type(exc).__name__}"] += 1
            continue
        cfs = row.get("execution_counterfactuals")
        cfs = dict(cfs) if isinstance(cfs, Mapping) else {}
        maker = cfs.get("shared_passive_maker")
        token_id = str(maker.get("token_id") or "") if isinstance(maker, Mapping) else ""
        snapshots = [
            snapshot
            for snapshot in run.snapshots
            if snapshot.token_id == token_id
            and (_utc(snapshot.observed_at_utc) or stage_at) <= stage_at
        ]
        if not snapshots:
            failures["same_stage_book_snapshot_missing"] += 1
            continue
        snapshot = max(
            snapshots,
            key=lambda item: _utc(item.observed_at_utc)
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        remaining = float(shares)
        gross = fee = 0.0
        for price, size in sorted(snapshot.asks):
            take = min(remaining, float(size))
            gross += take * float(price)
            fee += take * official_weather_fee_per_share(float(price))
            remaining -= take
            if remaining <= 1e-12:
                break
        if remaining > 1e-12:
            failures["same_stage_full_depth_missing"] += 1
            continue
        model_input = row.get("v2_1_model_input")
        model_input = model_input if isinstance(model_input, Mapping) else {}
        tick_size = decimal(model_input.get("current_yes_tick_size"))
        snapshot_at = _utc(snapshot.observed_at_utc)
        last_market_update_age_sec = (
            None
            if snapshot_at is None
            else max(Decimal("0"), Decimal(str((stage_at - snapshot_at).total_seconds())))
        )
        cfs[output_key] = {
            "token_id": token_id,
            "shares": money(shares),
            "gross_buy_cost": repr(gross),
            "modeled_taker_fee": repr(fee),
            "all_in_cost": repr(gross + fee),
            "book_snapshot_id": snapshot.book_snapshot_id,
            "observed_at_utc": snapshot.observed_at_utc,
            "subscription_epoch_ids": list(epoch_ids),
            "reconstruction_run_id": run.run_id,
            "fee_status": "official_weather_v2_forecast_not_realized",
            "market_book_reconstruction": {
                "status": "ok",
                "fetched_at_utc": stage_at.isoformat(),
                "last_market_update_at_utc": snapshot.observed_at_utc,
                "venue_timestamp_utc": (
                    None
                    if snapshot.exchange_ts_ms is None
                    else datetime.fromtimestamp(
                        snapshot.exchange_ts_ms / 1000, tz=timezone.utc
                    ).isoformat()
                ),
                "book_epoch_ref": snapshot.book_snapshot_id,
                "tick_size": money(tick_size),
                "tick_size_source": "same_signal_decision_packet_t0",
                "minimum_order_shares": money(shares),
                "minimum_order_shares_source": "measurement_probe_requested_shares_not_venue_attestation",
                "book_age_sec_at_route": "0",
                "last_market_update_age_sec": money(last_market_update_age_sec),
                "asof_carry_forward_contract": "continuous_gap_checked_epoch_state_materialized_at_exact_route_clock",
                "bids": [
                    {"price": repr(price), "size": repr(size)}
                    for price, size in snapshot.bids
                ],
                "asks": [
                    {"price": repr(price), "size": repr(size)}
                    for price, size in snapshot.asks
                ],
                "sequence_status": snapshot.sequence_status,
                "gap_detection_status": snapshot.gap_detection_status,
                "raw_lineage_id": snapshot.raw_lineage_id,
            },
        }
        enriched[index]["execution_counterfactuals"] = cfs
        evidence.append(
            {
                "identity": row_identity(row),
                "counterfactual_key": output_key,
                "book_snapshot_id": snapshot.book_snapshot_id,
                "reconstruction_run_id": run.run_id,
                "subscription_epoch_ids": list(epoch_ids),
                "raw_frames": len(frames),
            }
        )
    return enriched, {
        "attempted": True,
        "ws_root": str(ws_root),
        "candidate_rows": len(candidates),
        "ready_rows": len(evidence),
        "failures": dict(sorted(failures.items())),
        "evidence": evidence,
        "maker_fill_inference_allowed": False,
    }


def _historical_route_book(
    row: Mapping[str, Any], prereg: Mapping[str, Any]
) -> tuple[MarketBook, Decimal, Decimal, str] | tuple[None, None, None, str]:
    contract = prereg.get("same_stage_baseline")
    contract = contract if isinstance(contract, Mapping) else {}
    key = str(contract.get("reconstructed_taker_key") or "t30_immediate_taker")
    cfs = row.get("execution_counterfactuals")
    cfs = cfs if isinstance(cfs, Mapping) else {}
    taker = cfs.get(key)
    if not isinstance(taker, Mapping):
        return None, None, None, "same_stage_taker_missing"
    reconstructed = taker.get("market_book_reconstruction")
    if not isinstance(reconstructed, Mapping):
        return None, None, None, "same_stage_book_reconstruction_missing"
    tick = decimal(reconstructed.get("tick_size"))
    shares = decimal(taker.get("shares"))
    fallback = prereg.get("causal_policy_state_fallback")
    stage = str(fallback.get("stage") or "") if isinstance(fallback, Mapping) else ""
    maker_policy = policy_for(row, stage) if stage else None
    maker_shares = (
        decimal(maker_policy.get("requested_shares"))
        if isinstance(maker_policy, Mapping)
        else None
    )
    fee = decimal(taker.get("modeled_taker_fee"))
    age = decimal(reconstructed.get("book_age_sec_at_route"))
    if tick is None or tick <= 0:
        return None, None, None, "same_stage_tick_size_missing"
    if shares is None or shares <= 0:
        return None, None, None, "same_stage_shares_missing"
    if maker_shares is None or maker_shares <= 0:
        return None, None, None, "same_stage_maker_shares_missing"
    if maker_shares != shares:
        return None, None, None, "same_stage_maker_taker_shares_mismatch"
    if fee is None or fee < 0:
        return None, None, None, "same_stage_fee_forecast_missing"
    if age is None or age < 0:
        return None, None, None, "same_stage_book_age_missing"
    try:
        bids = tuple(
            BookLevel(item["price"], item["size"])
            for item in reconstructed.get("bids") or ()
            if isinstance(item, Mapping)
            and Decimal(str(item.get("price"))) > 0
            and Decimal(str(item.get("price"))) < 1
            and Decimal(str(item.get("size"))) > 0
        )
        asks = tuple(
            BookLevel(item["price"], item["size"])
            for item in reconstructed.get("asks") or ()
            if isinstance(item, Mapping)
            and Decimal(str(item.get("price"))) > 0
            and Decimal(str(item.get("price"))) < 1
            and Decimal(str(item.get("size"))) > 0
        )
        book = MarketBook(
            token_id=str(taker.get("token_id") or ""),
            status=str(reconstructed.get("status") or ""),
            fetched_at_utc=str(reconstructed.get("fetched_at_utc") or ""),
            venue_timestamp_utc=(
                None
                if reconstructed.get("venue_timestamp_utc") in (None, "")
                else str(reconstructed.get("venue_timestamp_utc"))
            ),
            book_epoch_ref=str(reconstructed.get("book_epoch_ref") or ""),
            tick_size=tick,
            tick_size_source=str(reconstructed.get("tick_size_source") or ""),
            minimum_order_shares=shares,
            bids=bids,
            asks=asks,
        )
    except (ExecutionContractError, InvalidOperation, ValueError, TypeError):
        return None, None, None, "same_stage_market_book_contract_invalid"
    return book, age, fee, "ok"


def selective_maker_route_audit(
    rows: list[dict[str, Any]], prereg: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run V2.1 on reconstructed t30 rows without promoting assumptions.

    The observed route deliberately has no transition-hazard or calibrated
    passive-fill estimate, so maker must fail closed.  A grid using explicitly
    hypothetical calibrated probabilities is emitted only to show what future
    measurement must establish; it is never counted as profitability evidence.
    """

    router = prereg.get("v2_1_router")
    if not isinstance(router, Mapping):
        return {
            "attempted": False,
            "blocker": "v2_1_router_contract_missing",
        }, []
    allowed = set(prereg.get("allowed_regimes") or ())
    max_age = str(router.get("maximum_book_age_sec") or "5")
    minimum_edge = str(
        router.get("minimum_retained_edge_usd_per_share") or "0.01"
    )
    uncertainty = str(
        router.get("uncertainty_buffer_usd_per_share") or "0.005"
    )
    inventory_risk = str(router.get("inventory_risk_per_share") or "0")
    hazard_penalty = str(
        router.get("transition_hazard_penalty_usd_per_share") or "0.05"
    )
    nonfill = str(
        router.get("nonfill_fallback_edge_usd_per_share") or "0"
    )
    maker_cost = str(router.get("maker_order_cost_usd_per_share") or "0")
    maker_margin = str(
        router.get("maker_selection_margin_over_taker_usd_per_share")
        or "0.005"
    )
    sensitivity = router.get("exploratory_sensitivity_only")
    sensitivity = sensitivity if isinstance(sensitivity, Mapping) else {}
    hazards = tuple(str(value) for value in sensitivity.get("transition_hazard") or ())
    fill_probabilities = tuple(
        str(value)
        for value in sensitivity.get("conservative_fill_probability") or ()
    )
    observed_counts: Counter[str] = Counter()
    observed_blockers: Counter[str] = Counter()
    scenario_counts: dict[str, Counter[str]] = {}
    details: list[dict[str, Any]] = []
    input_ready = 0
    for row in rows:
        regime, _ = state_details(row, prereg)
        if regime not in allowed:
            continue
        identity = row_identity(row)
        model = row.get("v2_1_model_input")
        model = model if isinstance(model, Mapping) else {}
        fair = decimal(model.get("model_probability_hold"))
        probability_status = str(model.get("probability_status") or "")
        book, book_age, taker_fee, book_status = _historical_route_book(row, prereg)
        if (
            fair is None
            or not Decimal("0") <= fair <= Decimal("1")
            or probability_status != "scored_by_current_yes_core_artifact"
        ):
            observed_blockers["frozen_weather_fair_value_missing"] += 1
            continue
        if book is None or book_age is None or taker_fee is None:
            observed_blockers[book_status] += 1
            continue
        feature_epoch_ref = f"{model.get('packet_id')}:{book.book_epoch_ref}"
        fee_schedule = FeeSchedule(
            venue="polymarket_clob",
            fee_schedule_ref="historical_weather_v2_reconstruction",
            fee_schedule_fetched_at_utc=book.fetched_at_utc,
            fee_formula_id="official_weather_v2_research_counterfactual",
            taker_fee_parameters={"source": "same_stage_reconstruction"},
            maker_fee_parameters={"realized_only": True},
        )
        capabilities = VenueCapabilities(
            venue="polymarket_clob",
            protocol_version="historical_reconstruction_not_live",
            client_version="selective_maker_v2_1",
            collateral_asset="UNVERIFIED_RESEARCH_ONLY",
            supported_order_types=("GTC",),
            post_only_order_types=("GTC",),
            price_precision=6,
            size_precision=6,
            amount_precision_by_order_type={"GTC": 6},
            gtd_security_threshold_sec=0,
            capabilities_fetched_at_utc=book.fetched_at_utc,
            fee_schedule_ref=fee_schedule.fee_schedule_ref,
        )
        pit = PITMarketState(
            candidate_id=identity,
            feature_epoch_ref=feature_epoch_ref,
            market_book=book,
            book_age_sec=book_age,
            max_book_age_sec=max_age,
            coverage_complete=True,
            dislocation_observed=regime == "transient_dislocation_candidate",
        )
        common = dict(
            pit=pit,
            venue_side="BUY",
            shares=str(book.minimum_order_shares),
            fee_schedule=fee_schedule,
            venue_capabilities=capabilities,
            fair_value=str(fair),
            taker_fee_estimate=str(taker_fee),
            minimum_retained_edge=minimum_edge,
            uncertainty_buffer=uncertainty,
            transition_hazard_penalty=hazard_penalty,
            inventory_risk_per_share=inventory_risk,
            nonfill_fallback_edge_per_share=nonfill,
            maker_order_cost_per_share=maker_cost,
            maker_selection_margin=maker_margin,
        )
        observed = route_candidate(RouteInput(**common))
        input_ready += 1
        observed_counts[observed.selected_route] += 1
        observed_blockers.update(observed.blockers)
        details.append(
            {
                "identity": identity,
                "target_date": row.get("target_date"),
                "route_evidence": "observed_missing_hazard_and_fill_calibration",
                "transition_hazard": None,
                "conservative_fill_probability": None,
                "selected_route": observed.selected_route,
                "maker_conditional_edge_per_share": money(
                    observed.maker_edge_per_share
                ),
                "maker_expected_edge_per_share": money(
                    observed.maker_expected_edge_per_share
                ),
                "taker_edge_per_share": money(observed.taker_edge_per_share),
                "blockers": "|".join(observed.blockers),
            }
        )
        for hazard in hazards:
            for fill_probability in fill_probabilities:
                estimate = PassiveFillEstimate(
                    model_id="hypothetical_actual_calibrated_sensitivity_only",
                    feature_epoch_ref=feature_epoch_ref,
                    horizon_seconds=int(
                        prereg.get("same_stage_baseline", {}).get(
                            "information_horizon_seconds", 30
                        )
                    ),
                    probability=fill_probability,
                    conservative_probability=fill_probability,
                    evidence_kind="actual_own_order_calibrated",
                )
                hazard_estimate = TransitionHazardEstimate(
                    model_id="hypothetical_actual_markout_calibrated_sensitivity_only",
                    feature_epoch_ref=feature_epoch_ref,
                    horizon_seconds=int(
                        prereg.get("same_stage_baseline", {}).get(
                            "information_horizon_seconds", 30
                        )
                    ),
                    probability=hazard,
                    conservative_upper_probability=hazard,
                    evidence_kind="actual_markout_calibrated",
                )
                decision = route_candidate(
                    RouteInput(
                        **common,
                        transition_hazard_estimate=hazard_estimate,
                        passive_fill_estimate=estimate,
                    )
                )
                scenario = f"hazard={hazard}|fill_lower={fill_probability}"
                scenario_counts.setdefault(scenario, Counter())[
                    decision.selected_route
                ] += 1
                details.append(
                    {
                        "identity": identity,
                        "target_date": row.get("target_date"),
                        "route_evidence": "hypothetical_sensitivity_not_profit_evidence",
                        "transition_hazard": hazard,
                        "conservative_fill_probability": fill_probability,
                        "selected_route": decision.selected_route,
                        "maker_conditional_edge_per_share": money(
                            decision.maker_edge_per_share
                        ),
                        "maker_expected_edge_per_share": money(
                            decision.maker_expected_edge_per_share
                        ),
                        "taker_edge_per_share": money(
                            decision.taker_edge_per_share
                        ),
                        "blockers": "|".join(decision.blockers),
                    }
                )
    return {
        "attempted": True,
        "version": "weather_first_selective_maker_v2_1",
        "candidate_rows": sum(
            state_details(row, prereg)[0] in allowed for row in rows
        ),
        "route_input_ready_rows": input_ready,
        "observed_route_counts": dict(sorted(observed_counts.items())),
        "observed_profit_route_maker_rows": observed_counts.get("maker", 0),
        "observed_blocker_counts": dict(sorted(observed_blockers.items())),
        "profit_route_ready": False,
        "profit_route_blockers": [
            "transition_hazard_not_calibrated_at_t30",
            "passive_fill_probability_not_actual_own_order_calibrated",
        ],
        "measurement_candidate_contract": (
            "causal candidate plus same-clock maker/taker/skip is ready; "
            "profit maker route remains blocked until micro-live labels calibrate fill and hazard"
        ),
        "sensitivity_is_promotion_evidence": False,
        "hypothetical_scenario_route_counts": {
            key: dict(sorted(value.items()))
            for key, value in sorted(scenario_counts.items())
        },
    }, details


def row_identity(row: dict[str, Any]) -> str:
    return str(row.get("signal_id") or row.get("event_id") or "")


def row_sha256(row: Mapping[str, Any]) -> str:
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def actual_maker(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return actual lineage only; a quote/cross/touch cannot satisfy this."""
    counterfactuals = row.get("execution_counterfactuals")
    nested = (
        counterfactuals.get("shared_passive_maker")
        if isinstance(counterfactuals, Mapping)
        else None
    )
    item = nested if isinstance(nested, dict) else row.get("actual_shared_maker")
    if not isinstance(item, dict):
        return None
    status = str(item.get("fill_status") or "").lower()
    authoritative = item.get("authoritative_own_order_lifecycle") is True
    if not authoritative or status not in {"filled", "partial_fill", "actual_fill"}:
        return None
    return item


def realized_rebate(
    row: Mapping[str, Any], namespace: str
) -> tuple[Decimal, str | None, bool]:
    """Return only immutable realized rebate evidence for one policy row."""

    ledger = row.get("realized_rebate_ledger")
    candidates: list[Mapping[str, Any]] = []
    if isinstance(ledger, Mapping):
        direct = ledger.get(namespace)
        if isinstance(direct, Mapping):
            candidates.append(direct)
        entries = ledger.get("entries")
        if isinstance(entries, list):
            candidates.extend(
                item
                for item in entries
                if isinstance(item, Mapping) and item.get("namespace") == namespace
            )
        if ledger.get("namespace") == namespace:
            candidates.append(ledger)
    for item in candidates:
        identity = str(
            item.get("payout_identity")
            or item.get("eligibility_identity")
            or item.get("ledger_identity")
            or ""
        ).strip()
        amount = decimal(item.get("amount_usd", item.get("amount")))
        if item.get("realized") is True and identity and amount is not None:
            return amount, identity, True
    return Decimal("0"), None, False


def terminal_value(
    row: dict[str, Any], fallback: dict[str, Any], shares: Decimal | None
) -> Decimal | None:
    direct = decimal(fallback.get("terminal_value"))
    if direct is not None:
        return direct
    # The producer's legacy convenience field is explicitly a 5-share sweep.
    # Scaling it to another quantity would invent depth, so fail closed.
    if shares != Decimal("5"):
        return None
    terminal = (row.get("execution_counterfactuals") or {}).get("terminal_1800s") or {}
    return decimal(terminal.get("current_executable_sell_proceeds_5"))


@dataclass(frozen=True)
class EvaluatedRow:
    identity: str
    target_date: str
    city: str
    regime: str | None
    state_source: str
    selected: bool
    evidence_complete: bool
    private_lifecycle_complete: bool
    maker_actual_fill: bool
    target_shares: Decimal | None
    immediate_taker_cost: Decimal | None
    taker_baseline_key: str | None
    maker_cost: Decimal | None
    maker_terminal_value: Decimal | None
    immediate_terminal_value: Decimal | None
    realized_maker_rebate: Decimal
    realized_taker_rebate: Decimal
    maker_rebate_identity: str | None
    taker_rebate_identity: str | None
    maker_profit_usd: Decimal | None
    taker_profit_usd: Decimal | None
    maker_minus_taker_usd: Decimal | None
    maker_minus_taker_per_share: Decimal | None
    maker_minus_skip_per_share: Decimal | None
    hazard_stratum: str | None
    price_stratum: str | None
    depth_stratum: str | None
    blockers: tuple[str, ...]


def evaluate(row: dict[str, Any], prereg: dict[str, Any]) -> EvaluatedRow:
    regime, state_source = state_details(row, prereg)
    selected = regime in set(prereg["allowed_regimes"])
    coverage = row.get("coverage_v2") or {}
    private_complete = coverage.get("private_order_feed_complete") is True
    maker = actual_maker(row)
    maker_actual_fill = maker is not None
    cfs = row.get("execution_counterfactuals") or {}
    baseline_keys = tuple(
        prereg.get("same_stage_baseline", {}).get(
            "taker_counterfactual_keys", ("immediate_taker",)
        )
    )
    taker_baseline_key = next(
        (
            key
            for key in baseline_keys
            if isinstance(cfs.get(key), Mapping)
            and decimal(cfs[key].get("all_in_cost")) is not None
        ),
        None,
    )
    immediate = cfs.get(taker_baseline_key) if taker_baseline_key else {}
    immediate_cost = decimal(immediate.get("all_in_cost"))
    maker_cost = decimal(maker.get("all_in_cost")) if maker else None
    immediate_shares = decimal(immediate.get("shares"))
    maker_shares = decimal(maker.get("shares")) if maker else None
    target_shares = maker_shares or immediate_shares
    maker_value = terminal_value(row, maker, maker_shares) if maker else None
    immediate_value = (
        terminal_value(row, immediate, immediate_shares)
        if immediate_cost is not None
        else None
    )
    maker_rebate, maker_rebate_identity, maker_rebate_complete = realized_rebate(
        row, "maker_rebate"
    )
    taker_rebate, taker_rebate_identity, taker_rebate_complete = realized_rebate(
        row, "taker_rebate"
    )
    blockers: list[str] = []
    if not selected:
        blockers.append("regime_not_preregistered_narrow_candidate")
    if not private_complete:
        blockers.append("private_order_lifecycle_incomplete")
    if not maker_actual_fill:
        blockers.append("actual_maker_own_order_lineage_missing")
    if immediate_cost is None or immediate_value is None:
        blockers.append("same_row_immediate_taker_baseline_missing")
    if maker_actual_fill and (maker_cost is None or maker_value is None):
        blockers.append("actual_maker_value_or_cost_missing")
    if target_shares is None or target_shares <= 0:
        blockers.append("same_row_target_shares_missing")
    if (
        maker_actual_fill
        and maker_shares is not None
        and immediate_shares is not None
        and maker_shares != immediate_shares
    ):
        blockers.append("maker_taker_target_shares_mismatch")
    if maker_actual_fill and not maker_rebate_complete:
        blockers.append("realized_maker_rebate_ledger_missing")
    if maker_actual_fill and not taker_rebate_complete:
        blockers.append("realized_taker_rebate_ledger_missing")
    maker_profit = taker_profit = maker_minus_taker = None
    maker_minus_taker_per_share = maker_minus_skip_per_share = None
    if not blockers:
        assert maker_value is not None and maker_cost is not None
        assert immediate_value is not None and immediate_cost is not None
        assert target_shares is not None
        maker_profit = maker_value - maker_cost + maker_rebate
        taker_profit = immediate_value - immediate_cost + taker_rebate
        maker_minus_taker = maker_profit - taker_profit
        maker_minus_taker_per_share = maker_minus_taker / target_shares
        maker_minus_skip_per_share = maker_profit / target_shares
    stratum_source = maker or row
    return EvaluatedRow(
        identity=row_identity(row), target_date=str(row.get("target_date") or ""), city=str(row.get("city") or ""),
        regime=regime, state_source=state_source, selected=selected, evidence_complete=not blockers, private_lifecycle_complete=private_complete,
        maker_actual_fill=maker_actual_fill, target_shares=target_shares,
        immediate_taker_cost=immediate_cost, taker_baseline_key=taker_baseline_key, maker_cost=maker_cost,
        maker_terminal_value=maker_value, immediate_terminal_value=immediate_value,
        realized_maker_rebate=maker_rebate,
        realized_taker_rebate=taker_rebate,
        maker_rebate_identity=maker_rebate_identity,
        taker_rebate_identity=taker_rebate_identity,
        maker_profit_usd=maker_profit,
        taker_profit_usd=taker_profit,
        maker_minus_taker_usd=maker_minus_taker,
        maker_minus_taker_per_share=maker_minus_taker_per_share,
        maker_minus_skip_per_share=maker_minus_skip_per_share,
        hazard_stratum=(
            str(stratum_source.get("hazard_stratum"))
            if stratum_source.get("hazard_stratum") not in (None, "")
            else None
        ),
        price_stratum=(
            str(stratum_source.get("price_stratum"))
            if stratum_source.get("price_stratum") not in (None, "")
            else None
        ),
        depth_stratum=(
            str(stratum_source.get("depth_stratum"))
            if stratum_source.get("depth_stratum") not in (None, "")
            else None
        ),
        blockers=tuple(blockers),
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row}) or ["identity"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def zero_notional_audit(rows: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict[str, Any]:
    failures: list[str] = []
    for record_kind, records in (("decision", rows), ("action", actions)):
        for index, row in enumerate(records):
            for required in (
                "declared_notional",
                "trade_intent_created",
                "venue_call_allowed",
            ):
                if required not in row:
                    failures.append(f"{record_kind}_{index}_{required}_missing")
            if record_kind == "decision" and "shadow_notional" not in row:
                failures.append(f"{record_kind}_{index}_shadow_notional_missing")
            if decimal(row.get("declared_notional")) != Decimal("0"):
                failures.append(f"{record_kind}_{index}_declared_notional_not_explicit_zero")
            if record_kind == "decision" and decimal(row.get("shadow_notional")) != Decimal("0"):
                failures.append(f"{record_kind}_{index}_shadow_notional_not_explicit_zero")
            policy = row.get("policy") if isinstance(row.get("policy"), Mapping) else {}
            if row.get("trade_intent_created") is not False:
                failures.append(f"{record_kind}_{index}_trade_intent_not_explicit_false")
            if policy.get("trade_intent_created") is True:
                failures.append(f"{record_kind}_{index}_policy_trade_intent_created")
            if row.get("venue_call_allowed") is not False:
                failures.append(f"{record_kind}_{index}_venue_call_not_explicit_false")
            if policy.get("venue_call_allowed") is True:
                failures.append(f"{record_kind}_{index}_policy_venue_call_allowed")
    return {"pass": not failures, "checked_rows": len(rows) + len(actions), "failures": failures}


def load_actions(input_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    path = input_dir / "maker_policy_actions.jsonl"
    return read_jsonl_snapshot(path) if path.exists() else ([], None)


def target_date_block_bootstrap(
    rows: list[EvaluatedRow], *, repetitions: int, seed: int
) -> dict[str, Any]:
    by_date: dict[str, tuple[Decimal, Decimal]] = {}
    for row in rows:
        if row.maker_minus_taker_usd is None or row.target_shares is None:
            continue
        prior_delta, prior_shares = by_date.get(
            row.target_date, (Decimal("0"), Decimal("0"))
        )
        by_date[row.target_date] = (
            prior_delta + row.maker_minus_taker_usd,
            prior_shares + row.target_shares,
        )
    dates = sorted(date for date, (_, shares) in by_date.items() if date and shares > 0)
    if not dates:
        return {
            "target_date_blocks": 0,
            "point_estimate_usd_per_share": None,
            "ci95_usd_per_share": [None, None],
        }
    date_rates = {
        date: by_date[date][0] / by_date[date][1]
        for date in dates
    }
    point = sum(date_rates.values(), Decimal("0")) / Decimal(len(dates))
    if len(dates) == 1:
        return {
            "target_date_blocks": 1,
            "point_estimate_usd_per_share": money(point),
            "ci95_usd_per_share": [None, None],
        }
    rng = random.Random(seed)
    draws: list[Decimal] = []
    for _ in range(repetitions):
        sampled = [dates[rng.randrange(len(dates))] for _ in dates]
        draws.append(
            sum((date_rates[date] for date in sampled), Decimal("0"))
            / Decimal(len(sampled))
        )
    draws.sort()
    lower = draws[int(Decimal("0.025") * Decimal(len(draws) - 1))]
    upper = draws[int(Decimal("0.975") * Decimal(len(draws) - 1))]
    return {
        "target_date_blocks": len(dates),
        "point_estimate_usd_per_share": money(point),
        "ci95_usd_per_share": [money(lower), money(upper)],
    }


def load_stage0_gate(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "available": False,
            "path": None if path is None else str(path),
            "sha256": None,
            "stage0a_fee_truth_pass": False,
            "stage0b_own_order_truth_pass": False,
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    stage0a = payload.get("stage0a", {}).get("gate", {})
    stage0b = payload.get("stage0b", {}).get("gate", {})
    return {
        "available": True,
        "path": str(path),
        "sha256": sha256(path),
        "stage0a_fee_truth_pass": (
            stage0a.get("actual_fill_fee_provenance_exact") is True
            and stage0a.get("historical_raw_v2_fee_details_complete") is True
            and stage0a.get("realized_taker_rebate_ledger_complete") is True
        ),
        "stage0b_own_order_truth_pass": (
            stage0b.get("identity_parity_100pct") is True
            and stage0b.get("all_unknown_reconciled") is True
            and stage0b.get("private_user_ws_wired") is True
            and stage0b.get("sole_target_order_reconciler_wired") is True
        ),
        "raw_stage0a_gate": stage0a,
        "raw_stage0b_gate": stage0b,
    }


def copy_reconnect(output_dir: Path, reconnect_dir: Path) -> dict[str, Any]:
    summary = reconnect_dir / "impact_summary.json"
    events = reconnect_dir / "affected_events.csv"
    if not summary.exists():
        return {"available": False, "blocker": "reconnect_impact_audit_missing"}
    shutil.copy2(summary, output_dir / "reconnect_impact_summary.json")
    if events.exists():
        shutil.copy2(events, output_dir / "reconnect_impact.csv")
    return {"available": True, "summary_sha256": sha256(summary), "events_available": events.exists()}


def build_research_record(
    *,
    report: Mapping[str, Any],
    output_dir: Path,
    input_dir: Path,
    prereg_path: Path,
    reconnect_dir: Path,
    stage0_report: Path | None,
    ws_root: Path | None,
    decision_packets_path: Path | None,
) -> dict[str, Any]:
    observed = str(report["generated_at_utc"])
    run_id = output_dir.name.lower()
    artifact_run_uri = (
        "artifact://weather-mm-w2-selective-maker-v2-1/" + output_dir.name
    )
    decisions = report["input_identity"]["decisions"]
    actions = report["input_identity"].get("actions")
    packet = report["input_identity"].get("decision_packets") or {}
    reconstruction = report["same_denominator_baselines"][
        "same_stage_reconstruction"
    ]
    reconstruction_identity = hashlib.sha256(
        json.dumps(
            reconstruction.get("evidence") or (),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    inputs: list[dict[str, Any]] = [
        {
            "input_id": "market-state-decisions-prefix",
            "kind": "jsonl",
            "locator": f"runtime://weather-edge-v1/{input_dir.name}/{DECISIONS_FILE}",
            "identity": (
                f"bytes={decisions['bytes_consumed']},"
                f"rows={decisions['object_rows_consumed']},"
                f"sha256-prefix={decisions['sha256_consumed_prefix']}"
            ),
            "coverage": (
                f"{report['denominator']['unique_rows']} unique first-positive "
                f"signals across {report['denominator']['full_signal_target_dates']} target dates"
            ),
            "observed_at_utc": observed,
        }
    ]
    if isinstance(actions, Mapping):
        inputs.append(
            {
                "input_id": "maker-policy-actions-prefix",
                "kind": "jsonl",
                "locator": f"runtime://weather-edge-v1/{input_dir.name}/maker_policy_actions.jsonl",
                "identity": (
                    f"bytes={actions['bytes_consumed']},"
                    f"rows={actions['object_rows_consumed']},"
                    f"sha256-prefix={actions['sha256_consumed_prefix']}"
                ),
                "coverage": "zero-notional t0/t30/t120 policy actions in the exact consumed prefix",
                "observed_at_utc": observed,
            }
        )
    if packet.get("attempted") is True:
        identity = packet["prefix_identity"]
        inputs.append(
            {
                "input_id": "decision-packets-prefix",
                "kind": "jsonl",
                "locator": f"runtime://weather-edge-v1/{decision_packets_path.parent.name}/{decision_packets_path.name}",
                "identity": (
                    f"bytes={identity['bytes_consumed']},"
                    f"rows={identity['object_rows_consumed']},"
                    f"sha256-prefix={identity['sha256_consumed_prefix']}"
                ),
                "coverage": (
                    f"frozen weather fair value matched "
                    f"{packet['matched_rows']}/{report['denominator']['unique_rows']} rows"
                ),
                "observed_at_utc": observed,
            }
        )
    if reconstruction.get("attempted") is True:
        inputs.append(
            {
                "input_id": "same-stage-ws-reconstruction",
                "kind": "jsonl",
                "locator": "runtime://weather-data-feed-service/market-books/ws-incremental",
                "identity": f"evidence-sha256={reconstruction_identity}",
                "coverage": (
                    f"exact t30 full-depth baseline ready "
                    f"{reconstruction.get('ready_rows', 0)}/"
                    f"{reconstruction.get('candidate_rows', 0)} candidate rows; "
                    f"failures={reconstruction.get('failures', {})}"
                ),
                "observed_at_utc": observed,
            }
        )
    stage0 = report["input_identity"].get("stage0") or {}
    if stage0.get("available") is True:
        inputs.append(
            {
                "input_id": "stage0-truth-gate",
                "kind": "json",
                "locator": (
                    "artifact://weather-mm-stage0-truth-audit-v1/"
                    f"{stage0_report.parent.name}/{stage0_report.name}"
                ),
                "identity": f"sha256={stage0['sha256']}",
                "coverage": "Stage 0A fee/incentive and Stage 0B own-order truth gates",
                "observed_at_utc": observed,
            }
        )
    reconnect = report.get("reconnect_impact") or {}
    if reconnect.get("available") is True:
        inputs.append(
            {
                "input_id": "reconnect-impact-audit",
                "kind": "json",
                "locator": f"{artifact_run_uri}/reconnect_impact_summary.json",
                "identity": f"sha256={reconnect['summary_sha256']}",
                "coverage": "bounded historical reconnect impact and patched replay",
                "observed_at_utc": observed,
            }
        )
    command = (
        ".venv/bin/python scripts/analysis/market_making/"
        "research_weather_mm_w2_narrow_maker_v1.py "
        f"--input-dir {input_dir} --output-dir <new-immutable-output-dir> "
        f"--prereg {prereg_path} --reconnect-audit-dir {reconnect_dir}"
    )
    if stage0_report is not None:
        command += f" --stage0-report {stage0_report}"
    if ws_root is not None:
        command += f" --ws-root {ws_root}"
    if decision_packets_path is not None:
        command += f" --decision-packets {decision_packets_path}"
    selective_path = Path(
        "src/strategies/weather_edge_v1/execution/selective_maker.py"
    )
    route = report["v2_1_route_audit"]
    zero_gate = report["stage_gates"]["zero_notional_readiness"]
    return {
        "schema_version": "pm_agents_research_record_v1",
        "record_id": f"research:weather:weather_first_mm_w2_selective_maker:{run_id}",
        "domain": "weather",
        "family": "weather_first_mm_w2_selective_maker",
        "run_id": run_id,
        "skill": "weather-strategy-research",
        "lifecycle_status": "complete",
        "observed_at_utc": observed,
        "question": {
            "hypothesis": (
                "A causal low-toxicity weather dislocation can route acquisition "
                "between maker, immediate taker, and skip using weather fair value, "
                "transition hazard, inventory cost, and calibrated passive-fill probability."
            ),
            "decision_target": (
                "Whether Selective Maker V2.1 is ready for zero-notional observation, "
                "separately authorized micro-live measurement, or profitability promotion."
            ),
            "scope": (
                f"Exact consumed prefix with {report['denominator']['unique_rows']} "
                f"first-positive signals through {observed}."
            ),
            "exclusions": [
                "generic continuous two-sided market making",
                "future touch or public cross treated as own fill",
                "estimated incentives credited as realized",
                "production deployment or live orders",
                "hypothetical hazard/fill sensitivity treated as promotion evidence",
            ],
        },
        "method": {
            "grain": "unique first-positive signal blocked equally by target_date",
            "denominator_scope": (
                "Every first-positive signal in the exact append-only prefix; "
                "unknown, non-fill, and execution-blocked rows remain in denominator."
            ),
            "pit_or_asof_policy": (
                "Frozen t0 weather probability plus exact t30 gap-checked WS book; "
                "no t120, terminal, own-fill, or settlement evidence enters routing."
            ),
            "label_contract": (
                "The new causal t30 mapping is exploratory before the formal 2026-08-31 "
                "forward start and does not rewrite the frozen V1 result."
            ),
            "primary_metrics": [
                "same-clock maker/taker/skip readiness",
                "maker expected edge versus immediate taker edge per share",
                "actual-fill maker minus taker residual by target-date block",
            ],
            "baselines": [
                "authoritative own-order maker lifecycle",
                "same-clock full-depth taker including modeled or actual fee",
                "skip with zero incremental cost",
            ],
            "fee_and_execution_basis": (
                "Routing credits no estimated rebates/rewards; profitability credits "
                "only immutable realized payout allocations and authoritative fills."
            ),
            "forward_policy": (
                "Formal forward starts 2026-08-31T00:00:00Z; update only each 10 "
                "new target dates without reselecting city, price band, or state."
            ),
            "acceptance_gates": [
                "zero-notional same-stage comparison complete with no side effects",
                "separate explicit funds authorization plus Stage 0 and production health for measurement",
                "100 narrow decisions, 30 target dates, and 20 authoritative maker fills",
                "95 percent evidence and 100 percent private lifecycle coverage",
                "target-date lower confidence bound above 0.005 USD/share with tail and capacity evidence",
            ],
            "evidence_layers": [
                "market-state and maker-action consumed prefixes",
                "frozen decision packet weather fair values",
                "gap-checked exact-t30 public WS reconstruction",
                "private own-order and realized incentive evidence only when available",
            ],
        },
        "inputs": inputs,
        "execution": {
            "producer": "scripts/analysis/market_making/research_weather_mm_w2_narrow_maker_v1.py",
            "code_identity": (
                f"runner-sha256={sha256(Path(__file__))};"
                f"selective-maker-sha256={sha256(selective_path)}"
            ),
            "config_identity": f"sha256={sha256(prereg_path)}",
            "config_locator": str(prereg_path),
            "reproduce_command": command,
        },
        "outputs": {
            "artifact_root_contract": "production://research_artifact_root/weather_mm_w2_selective_maker_v2_1",
            "canonical_machine_format": "json",
            "artifact_manifest": f"{artifact_run_uri}/report.json",
            "compact_summary_locator": "docs/design/weather_market_making/WEATHER_FIRST_MM_EXECUTION_V1.md",
        },
        "knowledge": {
            "family_living_doc": "docs/design/weather_market_making/WEATHER_FIRST_MM_EXECUTION_V1.md",
            "registry_or_index": "docs/WEATHER_STRATEGY_REGISTRY.md",
            "dated_snapshot": f"{artifact_run_uri}/report.json",
            "durable_conclusion": (
                f"V2.1 recovered {report['narrow_regime_observed']['selected_rows']} causal "
                f"candidates and exact same-clock baselines; zero-notional readiness="
                f"{zero_gate['pass']}. Profit-maker routes remain {route.get('observed_profit_route_maker_rows', 0)} "
                "because transition hazard and passive-fill probability lack authoritative calibration; "
                "actual maker fills remain zero, so profitability is inconclusive."
            ),
            "action": (
                "Keep V2.1 at zero notional. Do not deploy or place live orders. "
                "Only after Stage 0, production health, and explicit funds authorization may a "
                "capped micro-live measurement collect fill/non-fill, hazard, and incentive labels."
            ),
            "superseded_record_ids": [
                "research:weather:weather_first_mm_w2_narrow_maker:w2_narrow_maker_20260830t080000z",
                "research:weather:weather_first_mm_w2_selective_maker:w2_selective_maker_v21_20260830t113000z",
            ],
        },
    }


def run(
    input_dir: Path,
    output_dir: Path,
    prereg_path: Path,
    reconnect_dir: Path = DEFAULT_RECONNECT_AUDIT,
    stage0_report: Path | None = None,
    ws_root: Path | None = None,
    decision_packets_path: Path | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    decisions_path = input_dir / DECISIONS_FILE
    if not decisions_path.exists():
        raise FileNotFoundError(decisions_path)
    all_rows, decisions_identity = read_jsonl_snapshot(decisions_path)
    action_rows, actions_identity = load_actions(input_dir)
    raw_denom = [row for row in all_rows if row.get("event_kind") == prereg["denominator"]["include_event_kind"]]
    revisions: dict[str, list[dict[str, Any]]] = {}
    missing_identity_rows = 0
    for row in raw_denom:
        identity = row_identity(row)
        if not identity:
            missing_identity_rows += 1
            continue
        revisions.setdefault(identity, []).append(row)
    # The consumed file prefix is ordered append-only evidence.  Later rows
    # may add authoritative lifecycle/rebate evidence; selecting the first row
    # would silently discard that evidence.  We therefore use the latest row
    # and preserve every revision hash plus invariant-conflict diagnostics.
    deduped_raw = {identity: rows[-1] for identity, rows in revisions.items()}
    revision_lineage = {
        identity: [row_sha256(row) for row in rows]
        for identity, rows in revisions.items()
        if len(rows) > 1
    }
    revision_invariant_conflicts = sorted(
        identity
        for identity, rows in revisions.items()
        if len({(str(row.get("target_date") or ""), str(row.get("city") or "")) for row in rows})
        > 1
    )
    joined_latest = join_policy_actions(
        [deduped_raw[key] for key in sorted(deduped_raw)], action_rows
    )
    joined_latest, decision_packet_evidence = join_decision_packet_model_inputs(
        joined_latest, decision_packets_path
    )
    joined_latest, same_stage_reconstruction = reconstruct_same_stage_taker_baselines(
        joined_latest, prereg, ws_root
    )
    deduped = {row_identity(row): row for row in joined_latest}
    denominator = [deduped[key] for key in sorted(deduped)]
    evaluated = [evaluate(row, prereg) for row in denominator]
    selected = [item for item in evaluated if item.selected]
    complete = [item for item in selected if item.evidence_complete]
    dates = sorted({item.target_date for item in evaluated if item.target_date})
    selected_dates = sorted({item.target_date for item in selected if item.target_date})
    private_coverage = (Decimal(sum(item.private_lifecycle_complete for item in selected)) / Decimal(len(selected))) if selected else Decimal("0")
    evidence_coverage = (Decimal(len(complete)) / Decimal(len(selected))) if selected else Decimal("0")
    denominator_private_coverage = (Decimal(sum(item.private_lifecycle_complete for item in evaluated)) / Decimal(len(evaluated))) if evaluated else Decimal("0")
    rebates = {
        "maker": {
            "credited_usd": money(
                sum((item.realized_maker_rebate for item in complete), Decimal("0"))
            ),
            "rows_with_immutable_identity": sum(
                item.maker_rebate_identity is not None for item in complete
            ),
            "complete": bool(complete)
            and all(item.maker_rebate_identity is not None for item in complete),
        },
        "taker": {
            "credited_usd": money(
                sum((item.realized_taker_rebate for item in complete), Decimal("0"))
            ),
            "rows_with_immutable_identity": sum(
                item.taker_rebate_identity is not None for item in complete
            ),
            "complete": bool(complete)
            and all(item.taker_rebate_identity is not None for item in complete),
        },
        "missing_evidence_credit_policy": "zero_credit_and_promotion_blocker",
    }
    zero_audit = zero_notional_audit(all_rows, action_rows)
    reconnect = copy_reconnect(output_dir, reconnect_dir)
    stage0 = load_stage0_gate(stage0_report)
    gate = prereg["promotion_gate"]
    actual_fills = [item for item in selected if item.maker_actual_fill]
    strata = {
        "hazard": sorted({item.hazard_stratum for item in actual_fills if item.hazard_stratum}),
        "price": sorted({item.price_stratum for item in actual_fills if item.price_stratum}),
        "depth": sorted({item.depth_stratum for item in actual_fills if item.depth_stratum}),
    }
    selected_source_rows = [deduped[item.identity] for item in selected]
    route_audit, route_audit_rows = selective_maker_route_audit(
        selected_source_rows, prereg
    )
    shadow_readiness_rows = [
        shadow_comparison_readiness(row, prereg) for row in selected_source_rows
    ]
    shadow_comparison_ready = sum(ready for ready, _ in shadow_readiness_rows)
    shadow_comparison_blockers = Counter(
        blocker
        for _, row_blockers in shadow_readiness_rows
        for blocker in row_blockers
    )
    tail_evidence_complete = bool(selected_source_rows) and all(
        row.get("tail_risk_evidence_complete") is True for row in selected_source_rows
    )
    capacity_evidence_complete = bool(selected_source_rows) and all(
        row.get("capacity_evidence_complete") is True for row in selected_source_rows
    )
    bootstrap = target_date_block_bootstrap(
        complete,
        repetitions=int(prereg["inference"]["bootstrap_repetitions"]),
        seed=int(prereg["inference"]["bootstrap_seed"]),
    )
    mere = Decimal(str(prereg["mere"]["value_usd_per_share"]))
    lower = decimal(bootstrap["ci95_usd_per_share"][0])
    upper = decimal(bootstrap["ci95_usd_per_share"][1])
    economic_gate = (
        "PASS"
        if lower is not None
        and lower > mere
        and tail_evidence_complete
        and capacity_evidence_complete
        else "FUTILITY"
        if upper is not None and upper < mere
        else "INCONCLUSIVE"
    )
    blockers: list[str] = []
    if missing_identity_rows:
        blockers.append("first_positive_rows_missing_stable_identity")
    if revision_invariant_conflicts:
        blockers.append("append_revision_identity_invariant_conflict")
    if decision_packet_evidence.get("identity_conflicts"):
        blockers.append("decision_packet_identity_or_revision_conflict")
    if len(selected) < gate["minimum_narrow_regime_decisions"]:
        blockers.append("fewer_than_100_narrow_regime_decisions")
    if len(selected_dates) < gate["minimum_independent_target_dates"]:
        blockers.append("fewer_than_30_narrow_regime_target_dates")
    if len(actual_fills) < gate["minimum_actual_maker_fills"]:
        blockers.append("fewer_than_20_actual_maker_fills")
    if evidence_coverage < Decimal(gate["minimum_evidence_coverage"]):
        blockers.append("narrow_regime_evidence_coverage_below_95pct")
    if private_coverage < Decimal(gate["require_private_lifecycle_coverage"]):
        blockers.append("private_order_lifecycle_reconciliation_below_100pct")
    if not complete:
        blockers.append("actual_maker_own_order_lineage_missing")
    if len(strata["hazard"]) < gate["minimum_hazard_strata_with_actual_fills"]:
        blockers.append("actual_fills_do_not_cover_hazard_strata")
    if len(strata["price"]) < gate["minimum_price_strata_with_actual_fills"]:
        blockers.append("actual_fills_do_not_cover_price_strata")
    if len(strata["depth"]) < gate["minimum_depth_strata_with_actual_fills"]:
        blockers.append("actual_fills_do_not_cover_depth_strata")
    if not rebates["maker"]["complete"]:
        blockers.append("realized_maker_rebate_ledger_missing_or_incomplete")
    if not rebates["taker"]["complete"]:
        blockers.append("realized_taker_rebate_ledger_missing_or_incomplete")
    if not stage0["stage0a_fee_truth_pass"]:
        blockers.append("stage0a_fee_and_incentive_truth_not_closed")
    if not stage0["stage0b_own_order_truth_pass"]:
        blockers.append("stage0b_authoritative_own_order_truth_not_closed")
    if not zero_audit["pass"]:
        blockers.append("zero_notional_or_no_side_effect_contract_failed")
    if not reconnect["available"]:
        blockers.append("reconnect_impact_audit_missing")
    if not tail_evidence_complete:
        blockers.append("tail_risk_evidence_missing_or_incomplete")
    if not capacity_evidence_complete:
        blockers.append("capacity_evidence_missing_or_incomplete")
    if economic_gate != "PASS":
        blockers.append("target_date_lower_bound_not_above_frozen_mere")

    # The three stages are deliberately non-circular.  Zero-notional readiness
    # asks whether the software emits a safe, same-clock comparison.  Admission
    # to a separately authorized micro-live measurement asks for venue/order
    # truth but never for fills that only that measurement can create.  Actual
    # fill counts and confidence intervals belong solely to profitability.
    readiness_blockers: list[str] = []
    if missing_identity_rows:
        readiness_blockers.append("first_positive_rows_missing_stable_identity")
    if revision_invariant_conflicts:
        readiness_blockers.append("append_revision_identity_invariant_conflict")
    if decision_packet_evidence.get("identity_conflicts"):
        readiness_blockers.append("decision_packet_identity_or_revision_conflict")
    if not zero_audit["pass"]:
        readiness_blockers.append("zero_notional_or_no_side_effect_contract_failed")
    if not selected:
        readiness_blockers.append("no_causal_narrow_regime_decisions_observed")
    if selected and shadow_comparison_ready != len(selected):
        readiness_blockers.append("same_stage_maker_taker_skip_comparison_incomplete")
    if (
        selected
        and decision_packets_path is not None
        and ws_root is not None
        and route_audit.get("route_input_ready_rows") != len(selected)
    ):
        readiness_blockers.append("v2_1_value_route_input_incomplete")

    measurement_blockers = list(readiness_blockers)
    if not stage0["stage0a_fee_truth_pass"]:
        measurement_blockers.append("stage0a_fee_and_incentive_truth_not_closed")
    if not stage0["stage0b_own_order_truth_pass"]:
        measurement_blockers.append("stage0b_authoritative_own_order_truth_not_closed")
    measurement_blockers.extend(
        (
            "production_health_not_attested_healthy",
            "explicit_micro_live_authorization_missing",
        )
    )
    denom_csv = []
    evidence_csv = []
    for item in evaluated:
        record = {"identity": item.identity, "target_date": item.target_date, "city": item.city, "regime": item.regime,
                  "state_source": item.state_source,
                  "selected_narrow_regime": item.selected, "evidence_complete": item.evidence_complete,
                  "private_lifecycle_complete": item.private_lifecycle_complete, "actual_maker_fill": item.maker_actual_fill,
                  "target_shares": money(item.target_shares),
                  "taker_baseline_key": item.taker_baseline_key,
                  "immediate_taker_all_in_cost": money(item.immediate_taker_cost), "skip_all_in_cost": "0",
                  "maker_all_in_cost": money(item.maker_cost),
                  "maker_terminal_value_usd": money(item.maker_terminal_value),
                  "immediate_taker_terminal_value_usd": money(item.immediate_terminal_value),
                  "realized_maker_rebate_usd": money(item.realized_maker_rebate),
                  "realized_taker_rebate_usd": money(item.realized_taker_rebate),
                  "maker_rebate_identity": item.maker_rebate_identity,
                  "taker_rebate_identity": item.taker_rebate_identity,
                  "maker_profit_usd": money(item.maker_profit_usd),
                  "taker_profit_usd": money(item.taker_profit_usd),
                  "maker_minus_taker_usd": money(item.maker_minus_taker_usd),
                  "maker_minus_taker_usd_per_share": money(item.maker_minus_taker_per_share),
                  "maker_minus_skip_usd_per_share": money(item.maker_minus_skip_per_share),
                  "hazard_stratum": item.hazard_stratum,
                  "price_stratum": item.price_stratum,
                  "depth_stratum": item.depth_stratum,
                  "blockers": "|".join(item.blockers)}
        denom_csv.append(record)
        if item.selected:
            evidence_csv.append(record)
    write_csv(output_dir / "denominator.csv", denom_csv)
    write_csv(output_dir / "evidence.csv", evidence_csv)
    write_csv(output_dir / "route_audit.csv", route_audit_rows)
    report = {
        "schema_version": prereg.get(
            "report_schema_version", "weather_mm_w2_narrow_maker_report_v1"
        ),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_status": prereg.get(
            "research_status", "w2_zero_notional_falsification_fail_closed"
        ),
        "input_identity": {"input_dir": str(input_dir), "decisions": decisions_identity,
                           "actions": actions_identity, "prereg_path": str(prereg_path),
                           "prereg_sha256": sha256(prereg_path), "stage0": stage0,
                           "decision_packets": decision_packet_evidence},
        "denominator": {"definition": prereg["denominator"], "raw_rows": len(raw_denom), "unique_rows": len(denominator),
                          "missing_identity_rows": missing_identity_rows,
                          "revised_identities": len(revision_lineage),
                          "revision_rows_beyond_first": sum(len(rows) - 1 for rows in revisions.values()),
                          "revision_invariant_conflicts": revision_invariant_conflicts,
                          "revision_lineage_sha256": revision_lineage,
                          "full_signal_target_dates": len(dates), "target_dates": dates,
                          "narrow_regime_target_dates": len(selected_dates),
                          "review_checkpoints_reached": len(selected_dates) // gate["review_cadence_target_dates"]},
        "narrow_regime_observed": {"allowed": prereg["allowed_regimes"], "selected_rows": len(selected), "selected_target_dates": selected_dates,
                                     "state_counts": dict(sorted(Counter(item.regime or "unmapped" for item in evaluated).items())),
                                     "state_source_counts": dict(sorted(Counter(item.state_source for item in evaluated).items()))},
        "coverage": {"evidence_complete_rows": len(complete), "evidence_coverage": money(evidence_coverage),
                     "private_lifecycle_coverage_in_narrow_regime": money(private_coverage),
                     "producer_private_feed_flag_coverage_in_full_denominator": money(denominator_private_coverage),
                     "actual_maker_fills": len(actual_fills), "actual_fill_strata": strata,
                     "tail_risk_evidence_complete": tail_evidence_complete,
                     "capacity_evidence_complete": capacity_evidence_complete},
        "same_denominator_baselines": {"maker": "actual authoritative own-order lifecycle only",
                                         "immediate_taker": "all_in_cost and terminal value from same row",
                                         "skip": "0 cost / 0 shares / 0 incremental PnL",
                                         "complete_maker_minus_taker_usd_per_share": [money(item.maker_minus_taker_per_share) for item in complete],
                                         "same_stage_reconstruction": same_stage_reconstruction},
        "v2_1_route_audit": route_audit,
        "inference": {"frozen_mere_usd_per_share": money(mere), "target_date_block_bootstrap": bootstrap,
                      "economic_gate": economic_gate, "sequential_update_every_target_dates": gate["review_cadence_target_dates"]},
        "historical_negative_prior": prereg["historical_negative_prior"],
        "realized_rebate_completeness": rebates,
        "zero_notional_audit": zero_audit, "reconnect_impact": reconnect,
        "stage_gates": {
            "zero_notional_readiness": {
                "pass": not readiness_blockers,
                "blockers": sorted(set(readiness_blockers)),
                "selected_rows": len(selected),
                "same_stage_comparison_ready_rows": shadow_comparison_ready,
                "same_stage_comparison_blocker_counts": dict(sorted(shadow_comparison_blockers.items())),
                "requires_actual_fills": False,
            },
            "micro_live_measurement": {
                "authorized": False,
                "blockers": sorted(set(measurement_blockers)),
                "requires_actual_fills_before_entry": False,
                "purpose": "collect_authoritative_fills_nonfills_and_realized_incentives_under_separate_funds_authorization",
            },
            "frozen_forward_profitability": {
                "pass": not blockers,
                "blockers": sorted(set(blockers)),
                "first_look_actual_fill_floor": gate["minimum_actual_maker_fills"],
                "economic_gate": economic_gate,
            },
        },
        "gate": {"pass": not blockers, "blockers": blockers, "action": "keep_zero_notional_shadow" if blockers else "eligible_for_explicit_review_only"},
        "promotion_verdict": "FAIL_CLOSED" if blockers else "REVIEW_REQUIRED",
        "outputs": {"report_json": str(output_dir / "report.json"),
                    "research_record_json": str(output_dir / "research_record.json") if prereg.get("strategy_family") == "weather_first_selective_maker" else None,
                    "denominator_csv": str(output_dir / "denominator.csv"),
                    "evidence_csv": str(output_dir / "evidence.csv"),
                    "route_audit_csv": str(output_dir / "route_audit.csv"),
                    "reconnect_impact_summary_json": str(output_dir / "reconnect_impact_summary.json") if reconnect["available"] else None,
                    "reconnect_impact_csv": str(output_dir / "reconnect_impact.csv") if reconnect.get("events_available") else None},
    }
    research_record = (
        build_research_record(
            report=report,
            output_dir=output_dir,
            input_dir=input_dir,
            prereg_path=prereg_path,
            reconnect_dir=reconnect_dir,
            stage0_report=stage0_report,
            ws_root=ws_root,
            decision_packets_path=decision_packets_path,
        )
        if prereg.get("strategy_family") == "weather_first_selective_maker"
        else None
    )
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if research_record is not None:
        (output_dir / "research_record.json").write_text(
            json.dumps(research_record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--reconnect-audit-dir", type=Path, default=DEFAULT_RECONNECT_AUDIT)
    parser.add_argument("--stage0-report", type=Path)
    parser.add_argument("--ws-root", type=Path)
    parser.add_argument("--decision-packets", type=Path)
    args = parser.parse_args()
    report = run(
        args.input_dir,
        args.output_dir,
        args.prereg,
        args.reconnect_audit_dir,
        args.stage0_report,
        args.ws_root,
        args.decision_packets,
    )
    print(json.dumps({"promotion_verdict": report["promotion_verdict"], "blockers": report["gate"]["blockers"]}, sort_keys=True))


if __name__ == "__main__":
    main()
