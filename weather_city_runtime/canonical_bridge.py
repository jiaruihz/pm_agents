"""Incremental bridge from shared city candidates into a temporary canonical DB."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterable

from scripts.etl.build_weather_signal_candidates import CANDIDATE_DDL
from scripts.etl.materialize_weather_event_signal_candidates import (
    materialize_candidate_rows,
)
from weather_dashboard.db.apply_schema_canonical import apply_schema_canonical
from weather_dashboard.db.first_seen_schema import apply_first_seen_schema
from weather_dashboard.ingest.information_events import ingest_information_events
from weather_dashboard.ingest.state_checkpoints import ingest_state_checkpoints
from weather_data_feed.information_events import canonical_json_hash
from weather_model_evaluation.contracts import parse_utc

from .legacy_adapters import DecisionBundle


ROOT = Path(__file__).resolve().parents[1]


def _validate_temporary_db_path(path: Path) -> Path:
    resolved = path.resolve()
    production_compatibility_path = (ROOT / "runtime" / "weather.db").resolve()
    if resolved == production_compatibility_path:
        raise ValueError("Phase 2 canonical bridge may write only research/temp DB")
    allowed_roots = (
        Path(tempfile.gettempdir()).resolve(),
        Path("/tmp").resolve(),
        (ROOT / "runtime" / "research").resolve(),
        (ROOT / "research_outputs").resolve(),
    )
    if resolved.suffix != ".db":
        raise ValueError("temporary canonical bridge path must end in .db")
    if not any(resolved.is_relative_to(root) for root in allowed_roots):
        raise ValueError("Phase 2 canonical bridge may write only research/temp DB")
    return resolved


def _unique_by_id(
    rows: Iterable[dict[str, Any]],
    identity_field: str,
) -> tuple[list[dict[str, Any]], int]:
    unique: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for row in rows:
        identity = str(row[identity_field])
        previous = unique.get(identity)
        if previous is not None:
            if canonical_json_hash(previous) != canonical_json_hash(row):
                raise ValueError(f"conflicting duplicate {identity_field}: {identity}")
            duplicates += 1
            continue
        unique[identity] = row
    return [unique[key] for key in sorted(unique)], duplicates


_EVENT_IDENTITY_FIELDS = (
    "information_event_id",
    "event_kind",
    "event_role",
    "source",
    "city",
    "station_id",
    "provider_item_id",
    "content_key",
    "payload_hash",
    "revision_of_event_id",
    "source_event_ts_utc",
    "issued_at_utc",
    "valid_from_utc",
    "valid_to_utc",
    "pit_lineage_class",
    "original_first_seen_unknown",
    "material_state_change",
)


def _event_delivery_rank(row: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Rank duplicate legacy deliveries without mixing their clock tuple.

    Legacy city adapters can rediscover one immutable source observation from
    multiple model profiles or rollover shards.  The event identity remains
    stable, while the adapter-derived delivery header may differ.  Canonical
    lineage keeps the earliest complete delivery as the immutable header.
    """

    fallback = "9999-12-31T23:59:59.999999Z"
    return (
        str(row.get("first_seen_at_utc") or fallback),
        str(row.get("available_at_utc") or fallback),
        str(row.get("detected_at_utc") or fallback),
        str(row.get("raw_source_path") or "\uffff"),
        canonical_json_hash(row),
    )


def _coalesce_information_events(
    rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, int, int]:
    """Coalesce duplicate deliveries while rejecting identity conflicts.

    Returns unique events, duplicate deliveries, IDs whose delivery headers
    differed, and the number of non-winning conflicting deliveries.
    """

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["information_event_id"]), []).append(row)

    unique: list[dict[str, Any]] = []
    duplicate_deliveries = 0
    coalesced_ids = 0
    coalesced_deliveries = 0
    for identity in sorted(grouped):
        deliveries = grouped[identity]
        baseline_identity = {
            field: deliveries[0].get(field) for field in _EVENT_IDENTITY_FIELDS
        }
        for row in deliveries[1:]:
            current_identity = {
                field: row.get(field) for field in _EVENT_IDENTITY_FIELDS
            }
            if canonical_json_hash(baseline_identity) != canonical_json_hash(
                current_identity
            ):
                raise ValueError(
                    f"conflicting immutable information_event_id: {identity}"
                )
        winner = min(deliveries, key=_event_delivery_rank)
        duplicate_deliveries += len(deliveries) - 1
        distinct_headers = {
            canonical_json_hash(row) for row in deliveries
        }
        if len(distinct_headers) > 1:
            coalesced_ids += 1
            coalesced_deliveries += len(deliveries) - 1
        unique.append(winner)
    return unique, duplicate_deliveries, coalesced_ids, coalesced_deliveries


def _normalize_legacy_checkpoint_ref(
    row: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Remove model/profile aliases from legacy shared state checkpoints."""

    if row.get("feature_schema_version") != "legacy_city_score_features_v1":
        return row, False
    raw_manifest = row.get("feature_version_manifest")
    try:
        manifest = (
            json.loads(raw_manifest)
            if isinstance(raw_manifest, str)
            else dict(raw_manifest or {})
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return row, False
    if manifest.get("adapter") != "CityScore" or not manifest.get("feature_set_id"):
        return row, False
    stable_ref = f"legacy:{manifest['feature_set_id']}"
    if (
        row.get("feature_store_frame_id") == stable_ref
        and row.get("source_profile_id") == stable_ref
    ):
        return row, False
    return {
        **row,
        "feature_store_frame_id": stable_ref,
        "source_profile_id": stable_ref,
    }, True


def _unique_candidate_rows(
    rows: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    unique: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for row in rows:
        identity = str(row["candidate_id"])
        previous = unique.get(identity)
        if previous is None:
            unique[identity] = row
            continue
        left = {**previous, "policy_selected": 0, "first_city_day_selected": 0}
        right = {**row, "policy_selected": 0, "first_city_day_selected": 0}
        if canonical_json_hash(left) != canonical_json_hash(right):
            raise ValueError(f"conflicting duplicate candidate_id: {identity}")
        previous["policy_selected"] = max(
            int(previous.get("policy_selected") or 0),
            int(row.get("policy_selected") or 0),
        )
        previous["first_city_day_selected"] = max(
            int(previous.get("first_city_day_selected") or 0),
            int(row.get("first_city_day_selected") or 0),
        )
        duplicates += 1
    return [unique[key] for key in sorted(unique)], duplicates


def _validate_bundle(bundle: DecisionBundle) -> None:
    event = bundle.information_event
    checkpoint = bundle.state_checkpoint
    output = bundle.model_output
    candidate = bundle.signal_candidate
    event_id = str(event.get("information_event_id") or "")
    checkpoint_id = str(checkpoint.get("state_checkpoint_id") or "")
    if not event_id or checkpoint.get("trigger_event_id") != event_id:
        raise ValueError("bundle event/checkpoint trigger identity mismatch")
    if checkpoint_id != output.checkpoint_id or checkpoint_id != candidate.checkpoint_id:
        raise ValueError("bundle checkpoint identity mismatch")
    if event_id != output.trigger_event_id or event_id != candidate.trigger_event_id:
        raise ValueError("bundle trigger_event identity mismatch")
    for field in ("city", "target_date"):
        values = {
            str(checkpoint.get(field)),
            str(getattr(output, field)),
            str(getattr(candidate, field)),
        }
        if len(values) != 1:
            raise ValueError(f"bundle {field} mismatch")
    if parse_utc(str(checkpoint.get("as_of_ts_utc"))) != parse_utc(
        output.decision_ts_utc
    ) or parse_utc(output.decision_ts_utc) != parse_utc(candidate.decision_ts_utc):
        raise ValueError("bundle decision clock mismatch")
    for field in (
        "target_id",
        "target_kind",
        "model_id",
        "model_artifact_id",
        "feature_set_id",
    ):
        if getattr(output, field) != getattr(candidate, field):
            raise ValueError(f"bundle model/candidate {field} mismatch")
    if output.p_model != candidate.p_model:
        raise ValueError("bundle model/candidate probability mismatch")


class _CandidateCanonicalBridge:
    """Shared append implementation for isolated and production canonical DBs."""

    def __init__(self, db_path: Path, *, initialize_schema: bool):
        self.db_path = db_path
        self.initialize_schema = initialize_schema

    @staticmethod
    def _validate_existing_schema(conn: sqlite3.Connection) -> None:
        required_tables = {
            "weather_information_events",
            "weather_state_checkpoints",
            "fact_signal_candidates",
        }
        existing_tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing_tables = sorted(required_tables - existing_tables)
        if missing_tables:
            raise ValueError(
                f"canonical candidate bridge missing tables: {missing_tables}"
            )
        candidate_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(fact_signal_candidates)")
        }
        required_columns = {
            "candidate_id",
            "candidate_grain_version",
            "state_checkpoint_id",
            "trigger_event_id",
            "decision_ts_utc",
            "candidate_status",
            "policy_selected",
            "market_probability",
            "condition_id",
            "decision_entry_price",
        }
        missing_columns = sorted(required_columns - candidate_columns)
        if missing_columns:
            raise ValueError(
                f"canonical candidate bridge missing columns: {missing_columns}"
            )

    def append(self, bundles: Iterable[DecisionBundle]) -> dict[str, int]:
        values = list(bundles)
        for bundle in values:
            _validate_bundle(bundle)
        (
            events,
            duplicate_events,
            coalesced_event_ids,
            coalesced_event_deliveries,
        ) = _coalesce_information_events(
            [dict(bundle.information_event) for bundle in values],
        )
        checkpoint_rows = []
        normalized_checkpoint_deliveries = 0
        for bundle in values:
            checkpoint, normalized = _normalize_legacy_checkpoint_ref(
                dict(bundle.state_checkpoint)
            )
            checkpoint_rows.append(checkpoint)
            normalized_checkpoint_deliveries += int(normalized)
        checkpoints, duplicate_checkpoints = _unique_by_id(
            checkpoint_rows,
            "state_checkpoint_id",
        )
        candidates, duplicate_candidates_in_input = _unique_candidate_rows(
            [bundle.signal_candidate.to_canonical_input() for bundle in values],
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if self.initialize_schema:
                apply_schema_canonical(conn)
                conn.execute(CANDIDATE_DDL)
                apply_first_seen_schema(conn)
            else:
                self._validate_existing_schema(conn)
            event_result = ingest_information_events(conn, events)
            inserted_checkpoints = ingest_state_checkpoints(conn, checkpoints)
            result = materialize_candidate_rows(
                conn,
                candidates,
                initialize_schema=self.initialize_schema,
            )
            candidate_ids = [row["candidate_id"] for row in candidates]
            if candidate_ids:
                placeholders = ",".join("?" for _ in candidate_ids)
                canonical_count = int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM fact_signal_candidates "
                        f"WHERE candidate_id IN ({placeholders})",
                        candidate_ids,
                    ).fetchone()[0]
                )
            else:
                canonical_count = 0
            if canonical_count != len(candidate_ids):
                raise RuntimeError(
                    "raw/canonical candidate reconciliation failed: "
                    f"raw_unique={len(candidate_ids)} canonical={canonical_count}"
                )
        finally:
            conn.close()
        return {
            "raw_candidate_rows": len(values),
            "raw_unique_candidates": len(candidates),
            "canonical_candidates": canonical_count,
            "candidate_delta": len(candidates) - canonical_count,
            "inserted_candidates": result["inserted"],
            "existing_candidates": result["duplicates"],
            "input_duplicate_candidates": duplicate_candidates_in_input,
            "inserted_events": event_result["inserted"],
            "existing_events": event_result["duplicates"],
            "input_duplicate_events": duplicate_events,
            "input_coalesced_event_ids": coalesced_event_ids,
            "input_coalesced_event_deliveries": coalesced_event_deliveries,
            "inserted_checkpoints": inserted_checkpoints,
            "input_duplicate_checkpoints": duplicate_checkpoints,
            "input_normalized_legacy_checkpoint_deliveries": (
                normalized_checkpoint_deliveries
            ),
        }

    def candidate_funnels(
        self,
        candidate_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        identities = sorted(set(candidate_ids or ()))
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            if identities:
                placeholders = ",".join("?" for _ in identities)
                rows = conn.execute(
                    f"""
                    SELECT candidate_id, candidate_status, policy_selected,
                           market_probability, condition_id, decision_entry_price
                    FROM fact_signal_candidates
                    WHERE candidate_id IN ({placeholders})
                    """,
                    identities,
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                SELECT candidate_id, candidate_status, policy_selected,
                       market_probability, condition_id, decision_entry_price
                FROM fact_signal_candidates
                WHERE candidate_grain_version = 'v2_event_checkpoint'
                    """
                ).fetchall()
        finally:
            conn.close()
        return {
            "signal_funnel": {
                "unit": "expression_checkpoint",
                "raw_candidates": len(rows),
                "scored_candidates": sum(
                    row["candidate_status"] == "scored" for row in rows
                ),
                "blocked_candidates": sum(
                    row["candidate_status"] == "blocked" for row in rows
                ),
                "policy_selected": sum(bool(row["policy_selected"]) for row in rows),
            },
            "evidence_funnel": {
                "unit": "expression_checkpoint",
                "pit_market": sum(row["market_probability"] is not None for row in rows),
                "mapped_expression": sum(row["condition_id"] is not None for row in rows),
                "executable_expression": sum(
                    row["decision_entry_price"] is not None for row in rows
                ),
                "intent": "reported_from_intent_journal",
                "fill": "not_available_phase2",
            },
        }


class TemporaryCanonicalBridge(_CandidateCanonicalBridge):
    """Append candidates to an isolated DB and prove raw/canonical parity."""

    def __init__(self, db_path: Path):
        super().__init__(
            _validate_temporary_db_path(db_path), initialize_schema=True
        )


class CanonicalCandidateBridge(_CandidateCanonicalBridge):
    """Incrementally append WCIR facts to the physical canonical DB.

    The explicit physical identity check prevents a repo-local or split DB from
    becoming a second canonical writer. Production callers must still pass the
    production-manifest preflight before constructing this bridge.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        expected_db_path: Path = Path("/Volumes/jrs/pm_agents/runtime/weather.db"),
    ) -> None:
        requested = db_path.resolve()
        expected = expected_db_path.resolve()
        if not requested.is_file() or not expected.is_file():
            raise ValueError("canonical candidate bridge requires existing DB files")
        if not os.path.samefile(requested, expected):
            raise ValueError(
                "canonical candidate bridge DB identity mismatch: "
                f"requested={requested} expected={expected}"
            )
        super().__init__(requested, initialize_schema=False)
