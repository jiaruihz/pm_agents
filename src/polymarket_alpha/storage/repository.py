"""Minimal canonical-contract repository for the Alpha P0 schema."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
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
    BlindResearchPacket, MarketResearchPacket,
)
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
        elif isinstance(c, ClaimEvidence):
            conn.execute("INSERT INTO alpha_evidence_item VALUES (?, ?)", (c.evidence_id, c.content_sha256))
        elif isinstance(c, ReviewDecision):
            conn.execute("INSERT INTO alpha_review_decision VALUES (?, ?, ?)", (c.record_id, c.market_id, c.rule_hash))
        elif isinstance(c, PredictionRecord):
            conn.execute(
                "INSERT INTO alpha_prediction_record VALUES (?, ?, ?, ?, ?, ?, ?)",
                (c.prediction_id, c.market_id, c.decision_id, c.probability_estimate_id, c.packet_id, c.orderbook_snapshot_id, c.position_state.value),
            )

    def _save_existing_projection(
        self, conn: sqlite3.Connection, contract: CommonEnvelope
    ) -> None:
        """Repair only projections whose replay is independently idempotent."""

        self._save_raw_artifact_projection(conn, contract)

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
