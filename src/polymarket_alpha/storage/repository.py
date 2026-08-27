"""Minimal canonical-contract repository for the Alpha P0 schema."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from decimal import Decimal, localcontext
from typing import Any, Sequence

from ..contracts import (
    ALPHA_CONTRACT_VERSION,
    CommonEnvelope,
    canonical_datetime,
    canonical_json,
    content_sha256,
)
from ..contracts.models import (
    CandidateCard, CandidateTransition, ClaimEvidence, MarketAlias, MarketSnapshot,
    OrderbookSnapshot, RecallHit, ReviewDecision, RuleContract, PredictionRecord,
    BlindResearchPacket, MarketResearchPacket, MarketChangeEvent, BookCaptureDemand,
    BookCaptureReceipt, SourceArtifact, ResearchResultEnvelope, ResearchImportReceipt,
    CalibrationReport, MarketResolution, PredictionResolutionLink, PredictionScore,
    ResolutionAdjudicationStatus, ResolutionOutcome, ScoringEligibility,
)
from ..contracts.base import canonical_decimal
from ..rules.models import RuleGateDecision
from .migrations import migrate


class ContractConflictError(ValueError):
    """A stable record id was submitted with different canonical content."""


class StoredContractCorruptionError(RuntimeError):
    """Stored canonical JSON no longer matches its sealed hash/version."""


class AlphaRepository:
    def __init__(self, target: str | Path | sqlite3.Connection) -> None:
        self._target = target

    def migrate(self) -> dict[str, str]:
        return migrate(self._target)

    def market_id_for_condition(self, condition_id: str) -> str | None:
        """Return the sole canonical market for a non-blank condition id."""
        if not isinstance(condition_id, str) or not condition_id.strip():
            raise ValueError("condition_id must be a non-blank string")
        self.migrate()
        conn, owns = self._conn()
        try:
            rows = conn.execute(
                "SELECT market_id FROM alpha_market WHERE condition_id = ?", (condition_id.strip(),)
            ).fetchall()
            if len(rows) > 1:
                raise ContractConflictError(f"condition_id {condition_id!r} has duplicate canonical markets")
            return None if not rows else str(rows[0][0])
        finally:
            if owns:
                conn.close()

    def save_market_alias(self, alias: MarketAlias) -> None:
        """Persist one non-overlapping alias interval, closing a changed active value."""
        if not isinstance(alias, MarketAlias):
            raise TypeError("alias must be a MarketAlias")
        values = tuple(
            value.strip()
            for value in (alias.market_id, alias.source, alias.alias_type, alias.alias_value)
        )
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("market alias identifiers must be non-blank strings")
        if alias.effective_to is not None:
            raise ValueError("new market aliases must be open-ended")
        self.migrate()
        conn, owns = self._conn()
        start = canonical_datetime(alias.effective_from)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            exact = conn.execute(
                "SELECT 1 FROM alpha_market_alias WHERE market_id=? AND source=? AND alias_type=? "
                "AND alias_value=? AND effective_from_utc=?",
                (*values, start),
            ).fetchone()
            if exact is not None:
                conn.execute("COMMIT")
                return
            active = conn.execute(
                "SELECT alias_value, effective_from_utc FROM alpha_market_alias WHERE market_id=? "
                "AND source=? AND alias_type=? AND effective_to_utc IS NULL",
                values[:3],
            ).fetchall()
            if len(active) > 1:
                raise ContractConflictError("multiple active market alias intervals")
            if active:
                active_value, active_from = active[0]
                if str(active_value) == values[3]:
                    # A later observation of the unchanged value is one active interval.
                    conn.execute("COMMIT")
                    return
                if start <= str(active_from):
                    raise ContractConflictError("market alias change has same or earlier effective clock")
                conn.execute(
                    "UPDATE alpha_market_alias SET effective_to_utc=? WHERE market_id=? AND source=? "
                    "AND alias_type=? AND effective_to_utc IS NULL",
                    (start, *values[:3]),
                )
            conn.execute(
                "INSERT INTO alpha_market_alias VALUES (?, ?, ?, ?, ?, NULL)", (*values, start)
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise ContractConflictError("market alias violates catalog integrity") from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    def link_run_artifact(self, run_id: str, artifact_id: str, relation: str) -> None:
        """Associate an existing immutable artifact with a run idempotently."""
        if any(not isinstance(value, str) or not value.strip() for value in (run_id, artifact_id, relation)):
            raise ValueError("run_id, artifact_id, and relation must be non-blank strings")
        self.migrate()
        conn, owns = self._conn()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO alpha_run_artifact_link VALUES (?, ?, ?)",
                (run_id.strip(), artifact_id.strip(), relation.strip()),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise ContractConflictError("run-artifact link violates repository integrity") from exc
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    def _conn(self) -> tuple[sqlite3.Connection, bool]:
        if isinstance(self._target, sqlite3.Connection):
            return self._target, False
        return sqlite3.connect(str(self._target), timeout=10, isolation_level=None), True

    def save_contract(self, contract: CommonEnvelope) -> str:
        if contract.schema_version != ALPHA_CONTRACT_VERSION:
            raise ValueError("unsupported contract schema version")
        if isinstance(contract, ResearchResultEnvelope):
            return self.save_research_result(contract)
        self.migrate()
        payload = canonical_json(contract)
        digest = contract.canonical_sha256
        conn, owns = self._conn()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT canonical_sha256 FROM alpha_contract_record WHERE record_id = ?", (contract.record_id,)).fetchone()
            if existing is not None:
                if existing[0] != digest:
                    raise ContractConflictError(f"record_id {contract.record_id} has different canonical content")
                # An artifact may have been stored before its typed projection
                # migration/code landed.  Exact replay must repair that
                # projection instead of returning early forever.
                self._save_existing_projection(conn, contract)
                conn.execute("COMMIT")
                return digest
            conn.execute(
                "INSERT INTO alpha_contract_record VALUES (?, ?, ?, ?, ?, ?)",
                (
                    contract.record_id,
                    type(contract).__name__,
                    contract.schema_version,
                    payload,
                    digest,
                    canonical_datetime(contract.created_at),
                ),
            )
            self._save_projection(conn, contract, digest)
            conn.execute("COMMIT")
            return digest
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    def save_research_result(self, result: ResearchResultEnvelope) -> str:
        """Atomically persist a result plus immutable evidence/artifact children."""
        if result.schema_version != ALPHA_CONTRACT_VERSION:
            raise ValueError("unsupported contract schema version")
        self.migrate()
        conn, owns = self._conn()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            # Child records are exact contract objects, not JSON fragments. This
            # permits standalone replay and makes the result transaction all-or-nothing.
            for artifact in result.source_artifacts:
                self._insert_contract_row(conn, artifact)
            for evidence in result.evidence:
                self._insert_contract_row(conn, evidence)
            self._insert_contract_row(conn, result.probability_estimate)
            self._insert_contract_row(conn, result)
            conn.execute("COMMIT")
            return result.canonical_sha256
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    def save_contracts_atomic(
        self, contracts: Sequence[CommonEnvelope]
    ) -> tuple[str, ...]:
        """Persist an ordered contract group in one repository transaction.

        This is the public boundary for domain facts that are only meaningful
        together, such as a ReviewDecision and its PredictionRecord.  Exact
        replay remains idempotent; any content conflict or projection failure
        rolls the entire group back.
        """

        contracts = tuple(contracts)
        if not contracts:
            raise ValueError("atomic contract group must not be empty")
        if any(item.schema_version != ALPHA_CONTRACT_VERSION for item in contracts):
            raise ValueError("unsupported contract schema version")
        record_ids = [item.record_id for item in contracts]
        if len(record_ids) != len(set(record_ids)):
            raise ValueError("atomic contract group record ids must be unique")
        self.migrate()
        conn, owns = self._conn()
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("BEGIN IMMEDIATE")
            for contract in contracts:
                if isinstance(contract, ResearchResultEnvelope):
                    for artifact in contract.source_artifacts:
                        self._insert_contract_row(conn, artifact)
                    for evidence in contract.evidence:
                        self._insert_contract_row(conn, evidence)
                    self._insert_contract_row(conn, contract.probability_estimate)
                self._insert_contract_row(conn, contract)
            conn.execute("COMMIT")
            return tuple(item.canonical_sha256 for item in contracts)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            if owns:
                conn.close()

    def _insert_contract_row(self, conn: sqlite3.Connection, contract: CommonEnvelope) -> None:
        """Insert/replay one sealed row within a caller-owned transaction."""
        payload = canonical_json(contract)
        digest = contract.canonical_sha256
        existing = conn.execute(
            "SELECT canonical_sha256 FROM alpha_contract_record WHERE record_id = ?",
            (contract.record_id,),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != digest:
                raise ContractConflictError(
                    f"record_id {contract.record_id} has different canonical content"
                )
            self._save_existing_projection(conn, contract)
            return
        conn.execute(
            "INSERT INTO alpha_contract_record VALUES (?, ?, ?, ?, ?, ?)",
            (
                contract.record_id,
                type(contract).__name__,
                contract.schema_version,
                payload,
                digest,
                canonical_datetime(contract.created_at),
            ),
        )
        self._save_projection(conn, contract, digest)

    def get_contract_json(self, record_id: str) -> str | None:
        self.migrate()
        conn, owns = self._conn()
        try:
            row = conn.execute(
                "SELECT schema_version, canonical_json, canonical_sha256 "
                "FROM alpha_contract_record WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                return None
            schema_version, payload, expected_hash = tuple(row)
            if schema_version != ALPHA_CONTRACT_VERSION:
                raise StoredContractCorruptionError("stored contract has an unsupported schema version")
            try:
                parsed = json.loads(str(payload))
            except (TypeError, json.JSONDecodeError) as exc:
                raise StoredContractCorruptionError("stored contract JSON is invalid") from exc
            if content_sha256(parsed) != expected_hash:
                raise StoredContractCorruptionError("stored contract hash mismatch")
            return str(payload)
        finally:
            if owns:
                conn.close()

    def get_contract(self, record_id: str) -> dict[str, Any] | None:
        payload = self.get_contract_json(record_id)
        return None if payload is None else json.loads(payload)

    def _save_projection(self, conn: sqlite3.Connection, c: CommonEnvelope, digest: str) -> None:
        if self._save_raw_artifact_projection(conn, c):
            return
        if isinstance(c, MarketSnapshot):
            i = c.identity
            event_ids = self._market_event_ids(c)
            for event_id in event_ids:
                conn.execute("INSERT OR IGNORE INTO alpha_event(event_id) VALUES (?)", (event_id,))
            if i.condition_id is not None:
                condition_owner = conn.execute(
                    "SELECT market_id FROM alpha_market WHERE condition_id = ?", (i.condition_id,)
                ).fetchone()
                if condition_owner is not None and str(condition_owner[0]) != i.market_id:
                    raise ContractConflictError(
                        f"condition_id {i.condition_id!r} belongs to market {condition_owner[0]}"
                    )
            conn.execute("INSERT OR IGNORE INTO alpha_market VALUES (?, ?, ?, ?, ?)", (i.market_id, i.event_id, i.condition_id, i.yes_token_id, i.no_token_id))
            stored_identity = conn.execute(
                "SELECT event_id, condition_id, yes_token_id, no_token_id FROM alpha_market WHERE market_id = ?",
                (i.market_id,),
            ).fetchone()
            if stored_identity is None or tuple(stored_identity) != (
                i.event_id,
                i.condition_id,
                i.yes_token_id,
                i.no_token_id,
            ):
                raise ContractConflictError(f"market_id {i.market_id} has conflicting canonical identity")
            for event_id in event_ids:
                conn.execute("INSERT OR IGNORE INTO alpha_event_market VALUES (?, ?)", (event_id, i.market_id))
            conn.execute(
                "INSERT OR IGNORE INTO alpha_market_token_map VALUES (?, ?, 'YES')",
                (i.market_id, i.yes_token_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO alpha_market_token_map VALUES (?, ?, 'NO')",
                (i.market_id, i.no_token_id),
            )
            conn.execute("INSERT INTO alpha_market_snapshot_revision VALUES (?, ?, ?)", (c.record_id, i.market_id, digest))
        elif isinstance(c, MarketChangeEvent):
            self._require_contract_hash(conn, c.current_snapshot_id, c.current_snapshot_sha256)
            if c.previous_snapshot_id is not None:
                assert c.previous_snapshot_sha256 is not None
                self._require_contract_hash(conn, c.previous_snapshot_id, c.previous_snapshot_sha256)
            conn.execute(
                "INSERT OR IGNORE INTO alpha_market_change_event_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (c.change_event_id, c.market_id, c.previous_snapshot_id, c.current_snapshot_id,
                 c.previous_snapshot_sha256, c.current_snapshot_sha256,
                 None if c.previous_status is None else c.previous_status.value, c.current_status.value,
                 c.previous_rule_hash, c.current_rule_hash, canonical_datetime(c.effective_at),
                 canonical_datetime(c.detected_at)),
            )
            for change_type in c.change_types:
                conn.execute("INSERT OR IGNORE INTO alpha_market_change_type_v2 VALUES (?, ?)", (c.change_event_id, change_type.value))
            for field_name in c.changed_fields:
                conn.execute("INSERT OR IGNORE INTO alpha_market_change_field_v2 VALUES (?, ?)", (c.change_event_id, field_name))
        elif isinstance(c, BookCaptureDemand):
            i = c.identity
            stored = conn.execute("SELECT yes_token_id, no_token_id FROM alpha_market WHERE market_id=?", (i.market_id,)).fetchone()
            if stored is None or tuple(stored) != (i.yes_token_id, i.no_token_id):
                raise ContractConflictError("book demand identity is not the frozen catalog identity")
            self._require_contract_hash(conn, c.trigger_artifact_id, c.trigger_artifact_sha256)
            conn.execute(
                "INSERT OR IGNORE INTO alpha_book_capture_demand_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (c.demand_id, i.market_id, i.yes_token_id, i.no_token_id, c.purpose.value,
                 c.trigger_artifact_id, c.trigger_artifact_sha256, c.blind_result_id,
                 canonical_datetime(c.requested_at), canonical_datetime(c.valid_until), c.max_staleness_seconds),
            )
            for target_size in c.target_sizes:
                conn.execute("INSERT OR IGNORE INTO alpha_book_capture_demand_target_v2 VALUES (?, ?)", (c.demand_id, str(target_size)))
        elif isinstance(c, BookCaptureReceipt):
            self._require_contract_hash(conn, c.demand_id, c.demand_sha256)
            demand = conn.execute("SELECT market_id, purpose FROM alpha_book_capture_demand_v2 WHERE demand_id=?", (c.demand_id,)).fetchone()
            if demand is None or tuple(demand) != (c.market_id, c.purpose.value):
                raise ContractConflictError("book receipt does not match its frozen capture demand")
            if c.orderbook_snapshot_id is not None:
                assert c.orderbook_snapshot_sha256 is not None
                assert c.source_observed_at is not None
                self._require_contract_hash(conn, c.orderbook_snapshot_id, c.orderbook_snapshot_sha256)
                snapshot_row = conn.execute(
                    "SELECT s.market_id, r.canonical_json "
                    "FROM alpha_orderbook_snapshot s "
                    "JOIN alpha_contract_record r ON r.record_id=s.snapshot_id "
                    "WHERE s.snapshot_id=?",
                    (c.orderbook_snapshot_id,),
                ).fetchone()
                if snapshot_row is None or str(snapshot_row[0]) != c.market_id:
                    raise ContractConflictError(
                        "book receipt snapshot is absent or belongs to another market"
                    )
                snapshot_payload = json.loads(str(snapshot_row[1]))
                if (
                    snapshot_payload.get("capture_group_id") != c.capture_group_id
                    or snapshot_payload.get("source_observed_at")
                    != canonical_datetime(c.source_observed_at)
                ):
                    raise ContractConflictError(
                        "book receipt does not match snapshot capture lineage"
                    )
            conn.execute(
                "INSERT OR IGNORE INTO alpha_book_capture_receipt_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (c.receipt_id, c.demand_id, c.demand_sha256, c.market_id, c.purpose.value,
                 c.status.value, c.capture_owner, canonical_datetime(c.received_at),
                 c.orderbook_snapshot_id, c.orderbook_snapshot_sha256, c.capture_group_id,
                 None if c.source_observed_at is None else canonical_datetime(c.source_observed_at), c.error_code),
            )
        elif isinstance(c, SourceArtifact):
            conn.execute(
                "INSERT OR IGNORE INTO alpha_source_artifact_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (c.artifact_id, c.source_name, c.source_url_or_source_id, c.media_type,
                 canonical_datetime(c.captured_at), canonical_datetime(c.effective_as_of), c.capture_scope.value,
                 None if c.hash_scope is None else c.hash_scope.value, c.content_sha256,
                 c.content_length_bytes, c.artifact_locator, c.replayability.value),
            )
        elif isinstance(c, RecallHit):
            conn.execute("INSERT INTO alpha_recall_hit VALUES (?, ?, ?, ?, ?)", (c.record_id, c.market_id, c.recaller.value, c.recaller_version, digest))
        elif isinstance(c, CandidateCard):
            existing_candidate = conn.execute(
                "SELECT market_id FROM alpha_candidate WHERE candidate_id = ?",
                (c.candidate_id,),
            ).fetchone()
            if existing_candidate is None:
                conn.execute(
                    "INSERT INTO alpha_candidate VALUES (?, ?, ?, ?, ?)",
                    (c.candidate_id, c.market_id, c.record_id, c.state.value, digest),
                )
            elif existing_candidate[0] != c.market_id:
                raise ContractConflictError(
                    f"candidate_id {c.candidate_id} has conflicting market identity"
                )
            conn.execute(
                "INSERT INTO alpha_candidate_revision VALUES (?, ?, ?)",
                (c.record_id, c.candidate_id, digest),
            )
            conn.execute(
                "UPDATE alpha_candidate SET current_card_id = ?, state = ?, canonical_sha256 = ? "
                "WHERE candidate_id = ?",
                (c.record_id, c.state.value, digest, c.candidate_id),
            )
            for hit_id in c.recall_hit_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO alpha_candidate_recall_hit VALUES (?, ?)",
                    (c.candidate_id, hit_id),
                )
        elif isinstance(c, CandidateTransition):
            conn.execute("INSERT INTO alpha_candidate_transition VALUES (?, ?, ?, ?, ?)", (c.record_id, c.candidate_id, c.from_state.value, c.to_state.value, c.event_type.value))
        elif isinstance(c, RuleContract):
            corpus_revision_key = c.contract_corpus_sha256 or "NO_CORPUS"
            conn.execute(
                "INSERT INTO alpha_rule_contract_instance_v3 "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    c.record_id,
                    c.market_id,
                    c.rule_hash,
                    c.contract_corpus_sha256,
                    corpus_revision_key,
                    c.contract_revision_id,
                    c.parser_version,
                    c.source_version,
                ),
            )
        elif isinstance(c, RuleGateDecision):
            conn.execute(
                "INSERT INTO alpha_rule_gate_decision_v3 VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    c.record_id,
                    c.rule_contract_id,
                    c.stage.value,
                    c.decision,
                    c.rule_hash,
                    c.contract_revision_id,
                    c.compiler_version,
                ),
            )
        elif isinstance(c, OrderbookSnapshot):
            conn.execute("INSERT INTO alpha_orderbook_snapshot VALUES (?, ?)", (c.record_id, c.identity.market_id))
            conn.execute(
                "INSERT INTO alpha_orderbook_leg VALUES (?, 'YES', ?)",
                (c.record_id, c.yes_leg.token_id),
            )
            conn.execute(
                "INSERT INTO alpha_orderbook_leg VALUES (?, 'NO', ?)",
                (c.record_id, c.no_leg.token_id),
            )
        elif isinstance(c, (BlindResearchPacket, MarketResearchPacket)):
            conn.execute("INSERT INTO alpha_research_packet VALUES (?, ?)", (c.record_id, c.packet_stage.value))
        elif isinstance(c, ResearchResultEnvelope):
            packet = conn.execute("SELECT packet_stage FROM alpha_research_packet WHERE packet_id=?", (c.packet_id,)).fetchone()
            if packet is None or str(packet[0]) != c.packet_stage.value:
                raise ContractConflictError("research result packet is absent or has a mismatched stage")
            self._require_contract_hash(conn, c.packet_id, c.packet_sha256)
            conn.execute(
                "INSERT OR IGNORE INTO alpha_research_result_envelope_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (c.result_id, c.packet_stage.value, c.packet_id, c.packet_sha256,
                 c.probability_estimate.record_id, canonical_datetime(c.completed_at), c.producer, c.producer_version),
            )
            for artifact in c.source_artifacts:
                conn.execute("INSERT OR IGNORE INTO alpha_research_result_artifact_v2 VALUES (?, ?)", (c.result_id, artifact.artifact_id))
            for evidence in c.evidence:
                conn.execute("INSERT OR IGNORE INTO alpha_research_result_evidence_v2 VALUES (?, ?, ?)", (c.result_id, evidence.evidence_id, evidence.source_artifact_id))
        elif isinstance(c, ResearchImportReceipt):
            packet = conn.execute("SELECT packet_stage FROM alpha_research_packet WHERE packet_id=?", (c.packet_id,)).fetchone()
            if packet is None or str(packet[0]) != c.packet_stage.value:
                raise ContractConflictError("research import packet is absent or has a mismatched stage")
            self._require_contract_hash(conn, c.packet_id, c.packet_sha256)
            if conn.execute(
                "SELECT 1 FROM alpha_source_artifact_v2 WHERE artifact_id=?",
                (c.submitted_artifact_id,),
            ).fetchone() is None:
                raise ContractConflictError("submitted research artifact is absent")
            if c.quarantine_artifact_id is not None and conn.execute(
                "SELECT 1 FROM alpha_source_artifact_v2 WHERE artifact_id=?",
                (c.quarantine_artifact_id,),
            ).fetchone() is None:
                raise ContractConflictError("quarantine research artifact is absent")
            if c.status.value == "ACCEPTED":
                assert c.accepted_result_id is not None and c.accepted_result_sha256 is not None
                self._require_contract_hash(conn, c.accepted_result_id, c.accepted_result_sha256)
                accepted_result = conn.execute(
                    "SELECT packet_stage, packet_id, packet_sha256 "
                    "FROM alpha_research_result_envelope_v2 WHERE result_id=?",
                    (c.accepted_result_id,),
                ).fetchone()
                if accepted_result is None or tuple(accepted_result) != (
                    c.packet_stage.value,
                    c.packet_id,
                    c.packet_sha256,
                ):
                    raise ContractConflictError(
                        "accepted research result is bound to another packet"
                    )
            conn.execute(
                "INSERT OR IGNORE INTO alpha_research_import_receipt_v2 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (c.import_receipt_id, c.packet_stage.value, c.packet_id, c.packet_sha256,
                 c.submitted_artifact_id, c.submitted_result_sha256, c.status.value,
                 canonical_datetime(c.imported_at), c.importer_version, c.accepted_result_id,
                 c.accepted_result_sha256, c.quarantine_artifact_id),
            )
            for reason in c.reasons:
                conn.execute("INSERT OR IGNORE INTO alpha_research_import_reason_v2 VALUES (?, ?)", (c.import_receipt_id, reason.value))
        elif isinstance(c, ClaimEvidence):
            conn.execute("INSERT INTO alpha_evidence_item VALUES (?, ?)", (c.evidence_id, c.content_sha256))
        elif isinstance(c, ReviewDecision):
            conn.execute("INSERT INTO alpha_review_decision VALUES (?, ?, ?)", (c.record_id, c.market_id, c.rule_hash))
        elif isinstance(c, PredictionRecord):
            conn.execute(
                "INSERT INTO alpha_prediction_record VALUES (?, ?, ?, ?, ?, ?, ?)",
                (c.prediction_id, c.market_id, c.decision_id, c.probability_estimate_id, c.packet_id, c.orderbook_snapshot_id, c.position_state.value),
            )
        elif isinstance(c, MarketResolution):
            c = MarketResolution.model_validate(c.model_dump(mode="python"))
            self._require_contract_hash(conn, c.source_artifact_id, c.source_artifact_sha256)
            source_row = conn.execute(
                "SELECT capture_scope, content_sha256 FROM alpha_source_artifact_v2 WHERE artifact_id=?",
                (c.source_artifact_id,),
            ).fetchone()
            if source_row is None or str(source_row[0]) == "REFERENCE_ONLY" or source_row[1] is None:
                raise ContractConflictError("resolution source artifact is absent or not replayable")
            self._require_contract_hash(conn, c.rule_contract_id, c.rule_contract_sha256)
            rule_row = conn.execute(
                "SELECT market_id, rule_hash, contract_revision_id "
                "FROM alpha_rule_contract_instance_v3 WHERE rule_contract_id=?",
                (c.rule_contract_id,),
            ).fetchone()
            if rule_row is None or tuple(rule_row) != (
                c.market_id,
                c.rule_hash,
                c.contract_revision_id,
            ):
                raise ContractConflictError("resolution rule contract lineage mismatch")
            market_row = conn.execute(
                "SELECT condition_id FROM alpha_market WHERE market_id=?", (c.market_id,)
            ).fetchone()
            if market_row is None or market_row[0] != c.condition_id:
                raise ContractConflictError("resolution canonical market identity mismatch")
            if c.supersedes_resolution_id is not None:
                assert c.supersedes_resolution_sha256 is not None
                self._require_contract_hash(
                    conn, c.supersedes_resolution_id, c.supersedes_resolution_sha256
                )
                superseded = conn.execute(
                    "SELECT market_id FROM alpha_market_resolution_v1 WHERE resolution_id=?",
                    (c.supersedes_resolution_id,),
                ).fetchone()
                if superseded is None or str(superseded[0]) != c.market_id:
                    raise ContractConflictError("superseded resolution belongs to another market")
            conn.execute(
                "INSERT OR IGNORE INTO alpha_market_resolution_v1 VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    c.resolution_id,
                    c.market_id,
                    c.condition_id,
                    c.outcome.value,
                    c.adjudication_status.value,
                    canonical_datetime(c.resolved_at),
                    canonical_datetime(c.source_observed_at),
                    c.source_artifact_id,
                    c.source_artifact_sha256,
                    c.rule_contract_id,
                    c.rule_contract_sha256,
                    c.rule_hash,
                    c.contract_revision_id,
                    c.parser_version,
                    c.supersedes_resolution_id,
                    c.supersedes_resolution_sha256,
                ),
            )
        elif isinstance(c, PredictionResolutionLink):
            c = PredictionResolutionLink.model_validate(c.model_dump(mode="python"))
            for record_id, expected_hash in (
                (c.prediction_id, c.prediction_sha256),
                (c.decision_id, c.decision_sha256),
                (c.probability_estimate_id, c.probability_estimate_sha256),
                (c.rule_contract_id, c.rule_contract_sha256),
                (c.resolution_id, c.resolution_sha256),
            ):
                self._require_contract_hash(conn, record_id, expected_hash)
            duplicate = conn.execute(
                "SELECT link_id FROM alpha_prediction_resolution_link_v1 "
                "WHERE prediction_id=? AND resolution_id=?",
                (c.prediction_id, c.resolution_id),
            ).fetchone()
            if duplicate is not None and str(duplicate[0]) != c.link_id:
                raise ContractConflictError("prediction-resolution pair already has another link")
            prediction_row = conn.execute(
                "SELECT market_id, decision_id, probability_estimate_id, orderbook_snapshot_id "
                "FROM alpha_prediction_record WHERE prediction_id=?",
                (c.prediction_id,),
            ).fetchone()
            if prediction_row is None or tuple(prediction_row[:3]) != (
                c.market_id,
                c.decision_id,
                c.probability_estimate_id,
            ):
                raise ContractConflictError("prediction-resolution link prediction lineage mismatch")
            prediction_payload = self._contract_payload(conn, c.prediction_id)
            decision_payload = self._contract_payload(conn, c.decision_id)
            if decision_payload.get("execution") != "NO_ORDER":
                raise ContractConflictError("prediction-resolution link requires a NO_ORDER decision")
            decision_row = conn.execute(
                "SELECT market_id FROM alpha_review_decision WHERE decision_id=?", (c.decision_id,)
            ).fetchone()
            if decision_row is None or str(decision_row[0]) != c.market_id:
                raise ContractConflictError("prediction-resolution link decision market mismatch")
            resolution_row = conn.execute(
                "SELECT market_id, outcome, adjudication_status FROM alpha_market_resolution_v1 "
                "WHERE resolution_id=?",
                (c.resolution_id,),
            ).fetchone()
            if resolution_row is None or str(resolution_row[0]) != c.market_id:
                raise ContractConflictError("prediction-resolution link resolution market mismatch")
            expected_eligibility = (
                ScoringEligibility.EXCLUDED_PENDING_DISPUTE
                if str(resolution_row[2]) == ResolutionAdjudicationStatus.PENDING_DISPUTE.value
                else ScoringEligibility.EXCLUDED_INVALID
                if str(resolution_row[1]) == ResolutionOutcome.INVALID.value
                else ScoringEligibility.ELIGIBLE
            )
            if c.scoring_eligibility != expected_eligibility:
                raise ContractConflictError("link scoring eligibility contradicts resolution")
            rule_row = conn.execute(
                "SELECT market_id, rule_hash FROM alpha_rule_contract_instance_v3 WHERE rule_contract_id=?",
                (c.rule_contract_id,),
            ).fetchone()
            if rule_row is None or tuple(rule_row) != (c.market_id, self._contract_json_field(conn, c.prediction_id, "rule_hash")):
                raise ContractConflictError("prediction-resolution link rule lineage mismatch")
            estimate = self._contract_payload(conn, c.probability_estimate_id)
            if (
                estimate.get("estimate_stage") != "FINAL"
                or estimate.get("market_id") != c.market_id
                or str(estimate.get("p_event_yes_mid")) != canonical_decimal(c.predicted_probability)
            ):
                raise ContractConflictError("prediction-resolution link estimate lineage mismatch")
            baseline = estimate.get("p_market_yes_mid")
            if (None if baseline is None else str(baseline)) != (
                None
                if c.market_baseline_probability is None
                else canonical_decimal(c.market_baseline_probability)
            ):
                raise ContractConflictError("prediction-resolution link baseline probability mismatch")
            rule_payload = self._contract_payload(conn, c.rule_contract_id)
            if str(rule_payload.get("clarity_score")) != canonical_decimal(c.rule_clarity):
                raise ContractConflictError("prediction-resolution link rule clarity mismatch")
            entry = c.entry_basis
            position_state = prediction_payload.get("position_state")
            if (position_state == "SIMULATED") != (entry is not None):
                raise ContractConflictError("simulation entry presence contradicts prediction position state")
            if position_state not in ("SIMULATED", "NO_POSITION"):
                raise ContractConflictError("prediction-resolution link has unsupported position state")
            if entry is not None:
                if prediction_row[3] != entry.orderbook_snapshot_id:
                    raise ContractConflictError("simulation entry does not use prediction orderbook")
                self._require_contract_hash(
                    conn, entry.orderbook_snapshot_id, entry.orderbook_snapshot_sha256
                )
                book_row = conn.execute(
                    "SELECT market_id FROM alpha_orderbook_snapshot WHERE snapshot_id=?",
                    (entry.orderbook_snapshot_id,),
                ).fetchone()
                if book_row is None or str(book_row[0]) != c.market_id:
                    raise ContractConflictError("simulation entry orderbook market mismatch")
                if (
                    decision_payload.get("action") != "SIMULATE"
                    or decision_payload.get("direction") != entry.direction
                    or str(decision_payload.get("target_size"))
                    != canonical_decimal(entry.quantity)
                ):
                    raise ContractConflictError("simulation entry contradicts frozen decision")
                book_payload = self._contract_payload(conn, entry.orderbook_snapshot_id)
                identity = book_payload.get("identity")
                depth_field = "yes_depth" if entry.direction == "YES" else "no_depth"
                token_field = "yes_token_id" if entry.direction == "YES" else "no_token_id"
                if not isinstance(identity, dict) or identity.get(token_field) != entry.token_id:
                    raise ContractConflictError("simulation entry token mapping mismatch")
                matching_depth = tuple(
                    row
                    for row in book_payload.get(depth_field, ())
                    if isinstance(row, dict)
                    and str(row.get("target_size")) == canonical_decimal(entry.quantity)
                )
                if (
                    len(matching_depth) != 1
                    or matching_depth[0].get("buy_insufficient_depth") is not False
                    or str(matching_depth[0].get("buy_vwap"))
                    != canonical_decimal(entry.entry_vwap)
                ):
                    raise ContractConflictError("simulation entry does not match frozen book depth")
            from ..learning.resolution import (
                LearningResolutionError,
                build_prediction_resolution_link,
            )

            try:
                expected_link = build_prediction_resolution_link(
                    prediction=PredictionRecord.model_validate(prediction_payload),
                    decision=ReviewDecision.model_validate(decision_payload),
                    probability_estimate=self._model_from_contract(
                        conn, c.probability_estimate_id, "ProbabilityEstimate"
                    ),
                    rule_contract=RuleContract.model_validate(rule_payload),
                    resolution=MarketResolution.model_validate(
                        self._contract_payload(conn, c.resolution_id)
                    ),
                    orderbook=OrderbookSnapshot.model_validate(
                        self._contract_payload(conn, str(prediction_row[3]))
                    ),
                    market_type=c.market_type,
                    linked_at=c.linked_at,
                    run_id=c.run_id,
                    fee_amount=Decimal("0") if entry is None else entry.fee_amount,
                    fee_model_version=(
                        "no_fee_v1" if entry is None else entry.fee_model_version
                    ),
                )
            except (LearningResolutionError, ValueError) as exc:
                raise ContractConflictError(
                    "prediction-resolution link cannot be replayed from parents"
                ) from exc
            if expected_link != c:
                raise ContractConflictError(
                    "prediction-resolution link differs from deterministic parent replay"
                )
            conn.execute(
                "INSERT OR IGNORE INTO alpha_prediction_resolution_link_v1 VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    c.link_id,
                    c.market_id,
                    c.prediction_id,
                    c.prediction_sha256,
                    c.decision_id,
                    c.decision_sha256,
                    c.probability_estimate_id,
                    c.probability_estimate_sha256,
                    c.rule_contract_id,
                    c.rule_contract_sha256,
                    c.resolution_id,
                    c.resolution_sha256,
                    canonical_datetime(c.linked_at),
                    c.scoring_eligibility.value,
                    c.exclusion_reason,
                    canonical_decimal(c.predicted_probability),
                    None if c.market_baseline_probability is None else canonical_decimal(c.market_baseline_probability),
                    c.market_type,
                    canonical_decimal(c.rule_clarity),
                    None if entry is None else entry.orderbook_snapshot_id,
                    None if entry is None else entry.orderbook_snapshot_sha256,
                    None if entry is None else entry.direction,
                    None if entry is None else entry.token_id,
                    None if entry is None else canonical_decimal(entry.quantity),
                    None if entry is None else canonical_decimal(entry.entry_vwap),
                    None if entry is None else canonical_decimal(entry.gross_cost),
                    None if entry is None else canonical_decimal(entry.fee_amount),
                    None if entry is None else entry.fee_model_version,
                ),
            )
        elif isinstance(c, PredictionScore):
            c = PredictionScore.model_validate(c.model_dump(mode="python"))
            self._require_contract_hash(conn, c.link_id, c.link_sha256)
            duplicate = conn.execute(
                "SELECT score_id FROM alpha_prediction_score_v1 WHERE link_id=? AND scoring_policy_sha256=?",
                (c.link_id, c.scoring_policy_sha256),
            ).fetchone()
            if duplicate is not None and str(duplicate[0]) != c.score_id:
                raise ContractConflictError("link and scoring policy already have another score")
            link_row = conn.execute(
                "SELECT prediction_id, resolution_id, scoring_eligibility, predicted_probability, "
                "market_baseline_probability, market_type, rule_clarity, entry_vwap "
                "FROM alpha_prediction_resolution_link_v1 WHERE link_id=?",
                (c.link_id,),
            ).fetchone()
            if link_row is None or tuple(link_row[:3]) != (
                c.prediction_id,
                c.resolution_id,
                ScoringEligibility.ELIGIBLE.value,
            ):
                raise ContractConflictError("prediction score link lineage mismatch")
            if tuple(link_row[3:8]) != (
                canonical_decimal(c.predicted_probability),
                None if c.market_baseline_probability is None else canonical_decimal(c.market_baseline_probability),
                c.market_type,
                canonical_decimal(c.rule_clarity),
                None if c.entry_vwap is None else canonical_decimal(c.entry_vwap),
            ):
                raise ContractConflictError("prediction score does not preserve link inputs")
            expected_policy_sha256 = content_sha256(
                {
                    "scoring_policy_version": c.scoring_policy_version,
                    "log_loss_epsilon": c.log_loss_epsilon,
                }
            )
            if c.scoring_policy_sha256 != expected_policy_sha256:
                raise ContractConflictError("prediction score policy hash mismatch")
            resolution_outcome = conn.execute(
                "SELECT outcome FROM alpha_market_resolution_v1 WHERE resolution_id=?",
                (c.resolution_id,),
            ).fetchone()
            if resolution_outcome is None or str(resolution_outcome[0]) != c.outcome:
                raise ContractConflictError("prediction score outcome contradicts resolution")
            label = Decimal(c.outcome_label)
            if c.brier_score != (c.predicted_probability - label) ** 2:
                raise ContractConflictError("prediction Brier score is not replayable")
            if c.log_loss != self._binary_log_loss(
                c.predicted_probability, c.outcome_label, c.log_loss_epsilon
            ):
                raise ContractConflictError("prediction log loss is not replayable")
            if c.market_baseline_probability is not None:
                assert c.market_baseline_brier_score is not None
                assert c.market_baseline_log_loss is not None
                if c.market_baseline_brier_score != (
                    c.market_baseline_probability - label
                ) ** 2:
                    raise ContractConflictError("market baseline Brier score is not replayable")
                if c.market_baseline_log_loss != self._binary_log_loss(
                    c.market_baseline_probability, c.outcome_label, c.log_loss_epsilon
                ):
                    raise ContractConflictError("market baseline log loss is not replayable")
            link_payload = self._contract_payload(conn, c.link_id)
            entry_payload = link_payload.get("entry_basis")
            if entry_payload is not None:
                if not isinstance(entry_payload, dict):
                    raise ContractConflictError("prediction score entry basis is malformed")
                quantity = Decimal(str(entry_payload["quantity"]))
                payout = quantity if str(entry_payload["direction"]) == c.outcome else Decimal("0")
                expected_pnl = payout - Decimal(str(entry_payload["gross_cost"])) - Decimal(
                    str(entry_payload["fee_amount"])
                )
                if c.simulated_pnl != expected_pnl:
                    raise ContractConflictError("prediction simulated PnL is not replayable")
            from ..learning.scoring import LearningScoringError, ScoringPolicy, score_prediction

            try:
                expected_score = score_prediction(
                    link=PredictionResolutionLink.model_validate(link_payload),
                    resolution=MarketResolution.model_validate(
                        self._contract_payload(conn, c.resolution_id)
                    ),
                    policy=ScoringPolicy(
                        version=c.scoring_policy_version,
                        probability_epsilon=c.log_loss_epsilon,
                    ),
                    scored_at=c.created_at,
                    run_id=c.run_id,
                )
            except (LearningScoringError, ValueError) as exc:
                raise ContractConflictError(
                    "prediction score cannot be replayed from parents"
                ) from exc
            if expected_score != c:
                raise ContractConflictError(
                    "prediction score differs from deterministic parent replay"
                )
            conn.execute(
                "INSERT OR IGNORE INTO alpha_prediction_score_v1 VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    c.score_id,
                    c.link_id,
                    c.link_sha256,
                    c.prediction_id,
                    c.resolution_id,
                    c.outcome,
                    c.outcome_label,
                    canonical_decimal(c.predicted_probability),
                    None if c.market_baseline_probability is None else canonical_decimal(c.market_baseline_probability),
                    canonical_decimal(c.brier_score),
                    canonical_decimal(c.log_loss),
                    None if c.market_baseline_brier_score is None else canonical_decimal(c.market_baseline_brier_score),
                    None if c.market_baseline_log_loss is None else canonical_decimal(c.market_baseline_log_loss),
                    None if c.simulated_pnl is None else canonical_decimal(c.simulated_pnl),
                    c.market_type,
                    canonical_decimal(c.rule_clarity),
                    None if c.entry_vwap is None else canonical_decimal(c.entry_vwap),
                    c.scoring_policy_version,
                    canonical_decimal(c.log_loss_epsilon),
                    c.scoring_policy_sha256,
                ),
            )
        elif isinstance(c, CalibrationReport):
            c = CalibrationReport.model_validate(c.model_dump(mode="python"))
            score_objects: list[PredictionScore] = []
            for ref in c.score_references:
                self._require_contract_hash(conn, ref.score_id, ref.score_sha256)
                if conn.execute(
                    "SELECT 1 FROM alpha_prediction_score_v1 WHERE score_id=?", (ref.score_id,)
                ).fetchone() is None:
                    raise ContractConflictError("calibration report references a non-score contract")
                score_objects.append(
                    PredictionScore.model_validate(self._contract_payload(conn, ref.score_id))
                )
            from ..learning.scoring import (
                CalibrationPolicy,
                LearningScoringError,
                build_calibration_report,
            )

            try:
                expected_report = build_calibration_report(
                    scores=tuple(score_objects),
                    dimension=c.dimension,
                    policy=CalibrationPolicy(
                        version=c.calibration_policy_version,
                        rule_clarity_boundaries=c.rule_clarity_boundaries,
                        entry_price_boundaries=c.entry_price_boundaries,
                    ),
                    reported_at=c.created_at,
                    run_id=c.run_id,
                )
            except (LearningScoringError, ValueError) as exc:
                raise ContractConflictError(
                    "calibration report cannot be replayed from referenced scores"
                ) from exc
            if expected_report != c:
                raise ContractConflictError(
                    "calibration report differs from deterministic score replay"
                )
            conn.execute(
                "INSERT OR IGNORE INTO alpha_calibration_report_v1 VALUES (?, ?, ?, ?, ?, ?)",
                (
                    c.report_id,
                    c.dimension.value,
                    c.calibration_policy_version,
                    canonical_json(c.rule_clarity_boundaries),
                    canonical_json(c.entry_price_boundaries),
                    c.calibration_policy_sha256,
                ),
            )
            for ref in c.score_references:
                conn.execute(
                    "INSERT OR IGNORE INTO alpha_calibration_report_score_v1 VALUES (?, ?, ?)",
                    (c.report_id, ref.score_id, ref.score_sha256),
                )
            for score_id in c.excluded_score_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO alpha_calibration_report_exclusion_v1 VALUES (?, ?)",
                    (c.report_id, score_id),
                )
            for row in c.slices:
                conn.execute(
                    "INSERT OR IGNORE INTO alpha_calibration_slice_v1 VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        c.report_id,
                        row.slice_key,
                        row.count,
                        canonical_decimal(row.mean_predicted_probability),
                        canonical_decimal(row.observed_yes_rate),
                        canonical_decimal(row.mean_brier_score),
                        canonical_decimal(row.mean_log_loss),
                        None if row.mean_market_baseline_brier_score is None else canonical_decimal(row.mean_market_baseline_brier_score),
                        None if row.mean_market_baseline_log_loss is None else canonical_decimal(row.mean_market_baseline_log_loss),
                        None if row.mean_simulated_pnl is None else canonical_decimal(row.mean_simulated_pnl),
                    ),
                )

    def _save_existing_projection(
        self, conn: sqlite3.Connection, contract: CommonEnvelope
    ) -> None:
        """Repair only projections whose replay is independently idempotent."""

        if self._save_raw_artifact_projection(conn, contract):
            return
        if isinstance(contract, (MarketChangeEvent, BookCaptureDemand, BookCaptureReceipt,
                                 SourceArtifact, ResearchResultEnvelope, ResearchImportReceipt,
                                 MarketResolution, PredictionResolutionLink, PredictionScore,
                                 CalibrationReport)):
            self._save_projection(conn, contract, contract.canonical_sha256)

    @staticmethod
    def _require_contract_hash(conn: sqlite3.Connection, record_id: str, digest: str) -> None:
        row = conn.execute("SELECT canonical_sha256 FROM alpha_contract_record WHERE record_id=?", (record_id,)).fetchone()
        if row is None or str(row[0]) != digest:
            raise ContractConflictError(f"referenced contract {record_id} is absent or hash-mismatched")

    @staticmethod
    def _contract_payload(conn: sqlite3.Connection, record_id: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT canonical_json, canonical_sha256 FROM alpha_contract_record WHERE record_id=?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise ContractConflictError(f"referenced contract {record_id} is absent")
        try:
            payload = json.loads(str(row[0]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise StoredContractCorruptionError("referenced contract JSON is invalid") from exc
        if not isinstance(payload, dict) or content_sha256(payload) != str(row[1]):
            raise StoredContractCorruptionError("referenced contract hash mismatch")
        return payload

    @classmethod
    def _contract_json_field(
        cls, conn: sqlite3.Connection, record_id: str, field_name: str
    ) -> Any:
        return cls._contract_payload(conn, record_id).get(field_name)

    @classmethod
    def _model_from_contract(
        cls, conn: sqlite3.Connection, record_id: str, expected_type: str
    ) -> Any:
        row = conn.execute(
            "SELECT contract_type FROM alpha_contract_record WHERE record_id=?", (record_id,)
        ).fetchone()
        if row is None or str(row[0]) != expected_type:
            raise ContractConflictError(
                f"referenced contract {record_id} is not {expected_type}"
            )
        payload = cls._contract_payload(conn, record_id)
        if expected_type == "ProbabilityEstimate":
            from ..contracts.models import ProbabilityEstimate

            return ProbabilityEstimate.model_validate(payload)
        raise ContractConflictError(f"unsupported typed contract replay: {expected_type}")

    @staticmethod
    def _binary_log_loss(probability: Decimal, label: int, epsilon: Decimal) -> Decimal:
        clipped = max(epsilon, min(Decimal("1") - epsilon, probability))
        with localcontext() as context:
            context.prec = 50
            return -(clipped.ln() if label == 1 else (Decimal("1") - clipped).ln())

    @staticmethod
    def _save_raw_artifact_projection(
        conn: sqlite3.Connection, contract: CommonEnvelope
    ) -> bool:
        """Project known Gamma raw shapes without importing their adapter module."""

        contract_type = type(contract).__name__
        if contract_type == "GammaPageArtifact":
            items = getattr(contract, "items", None)
            if not isinstance(items, tuple):
                raise ContractConflictError("GammaPageArtifact items must be a tuple")
            computed = content_sha256(list(items))
            declared = getattr(contract, "page_sha256", None)
        elif contract_type == "GammaMarketPayloadArtifact":
            if not hasattr(contract, "payload"):
                raise ContractConflictError("GammaMarketPayloadArtifact payload is missing")
            computed = content_sha256(getattr(contract, "payload"))
            declared = getattr(contract, "payload_sha256", None)
        else:
            return False
        if declared != computed:
            raise ContractConflictError(
                f"{contract_type} declared content hash does not match stored payload"
            )
        existing = conn.execute(
            "SELECT content_sha256 FROM alpha_raw_artifact WHERE artifact_id = ?",
            (contract.record_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO alpha_raw_artifact VALUES (?, ?)",
                (contract.record_id, computed),
            )
        elif str(existing[0]) != computed:
            raise ContractConflictError(
                f"raw artifact {contract.record_id} has conflicting projected content"
            )
        return True

    @staticmethod
    def _market_event_ids(snapshot: MarketSnapshot) -> tuple[str, ...]:
        """Validate the adapter's deterministic complete event relationship set."""
        supplied = snapshot.extensions.get("gamma_event_ids")
        if supplied is None:
            return (snapshot.identity.event_id,)
        if not isinstance(supplied, Sequence) or isinstance(supplied, (str, bytes, bytearray)):
            raise ContractConflictError("gamma_event_ids must be a non-empty string sequence")
        event_ids = tuple(supplied)
        if not event_ids or any(not isinstance(value, str) or not value.strip() for value in event_ids):
            raise ContractConflictError("gamma_event_ids must be a non-empty string sequence")
        normalized = tuple(value.strip() for value in event_ids)
        if normalized != tuple(sorted(set(normalized))):
            raise ContractConflictError("gamma_event_ids must be sorted and deduplicated")
        if snapshot.identity.event_id not in normalized:
            raise ContractConflictError("primary event_id must be included in gamma_event_ids")
        return normalized
