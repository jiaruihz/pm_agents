"""Transactional, additive migrations for the Alpha P0 SQLite namespace."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3
from typing import Iterator

from ..contracts import ALPHA_CONTRACT_VERSION, content_sha256


ALPHA_SCHEMA_VERSION = ALPHA_CONTRACT_VERSION
MIGRATION_ID = "alpha_p0_0001"
RULE_CORPUS_REVISION_MIGRATION_ID = "alpha_p0_0002_rule_corpus_revision"
CATALOG_INTEGRITY_MIGRATION_ID = "alpha_p0_0003_catalog_integrity"
RULE_CONTRACT_INSTANCE_MIGRATION_ID = "alpha_p0_0004_rule_contract_instances"
P0_01R2_PROJECTION_MIGRATION_ID = "alpha_p0_0005_p0_01r2_projections"
P1_RESOLUTION_LEARNING_MIGRATION_ID = "alpha_p1_0001_resolution_learning"

# Every object is alpha-namespaced so a shared legacy research database is never
# altered.  This migration is intentionally additive; rollback is a reader pin,
# never a DROP or DELETE operation.
MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS alpha_schema_migrations (
    migration_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    sql_sha256 TEXT NOT NULL CHECK(length(sql_sha256) = 64),
    applied_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alpha_schema_manifest (
    schema_version TEXT PRIMARY KEY,
    migration_id TEXT NOT NULL REFERENCES alpha_schema_migrations(migration_id),
    contract_version TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256) = 64),
    applied_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alpha_contract_record (
    record_id TEXT PRIMARY KEY,
    contract_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    canonical_json TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64),
    created_at_utc TEXT NOT NULL,
    UNIQUE(contract_type, canonical_sha256)
);
CREATE TABLE IF NOT EXISTS alpha_event (
    event_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS alpha_market (
    market_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES alpha_event(event_id),
    condition_id TEXT,
    yes_token_id TEXT NOT NULL,
    no_token_id TEXT NOT NULL,
    CHECK(yes_token_id <> no_token_id)
);
CREATE TABLE IF NOT EXISTS alpha_event_market (
    event_id TEXT NOT NULL REFERENCES alpha_event(event_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    PRIMARY KEY(event_id, market_id)
);
CREATE TABLE IF NOT EXISTS alpha_market_alias (
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    source TEXT NOT NULL, alias_type TEXT NOT NULL,
    alias_value TEXT NOT NULL, effective_from_utc TEXT NOT NULL,
    effective_to_utc TEXT,
    PRIMARY KEY(market_id, source, alias_type, alias_value, effective_from_utc)
);
CREATE TABLE IF NOT EXISTS alpha_raw_artifact (
    artifact_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64)
);
CREATE TABLE IF NOT EXISTS alpha_market_snapshot_revision (
    snapshot_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    revision_sha256 TEXT NOT NULL CHECK(length(revision_sha256) = 64),
    UNIQUE(market_id, revision_sha256)
);
CREATE TABLE IF NOT EXISTS alpha_market_token_map (
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id), token_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('YES','NO')), PRIMARY KEY(market_id, side), UNIQUE(market_id, token_id)
);
CREATE TABLE IF NOT EXISTS alpha_change_event (
    change_event_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id)
);
CREATE TABLE IF NOT EXISTS alpha_orderbook_snapshot (
    snapshot_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id)
);
CREATE TABLE IF NOT EXISTS alpha_orderbook_leg (
    snapshot_id TEXT NOT NULL REFERENCES alpha_orderbook_snapshot(snapshot_id),
    side TEXT NOT NULL CHECK(side IN ('YES','NO')), token_id TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, side)
);
CREATE TABLE IF NOT EXISTS alpha_recall_hit (
    recall_hit_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id), recaller TEXT NOT NULL, recaller_version TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64),
    UNIQUE(recaller, recaller_version, canonical_sha256)
);
CREATE TABLE IF NOT EXISTS alpha_candidate (
    candidate_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    current_card_id TEXT NOT NULL REFERENCES alpha_contract_record(record_id),
    state TEXT NOT NULL,
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64)
);
CREATE TABLE IF NOT EXISTS alpha_candidate_revision (
    card_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    candidate_id TEXT NOT NULL REFERENCES alpha_candidate(candidate_id),
    canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64),
    UNIQUE(candidate_id, canonical_sha256)
);
CREATE TABLE IF NOT EXISTS alpha_candidate_recall_hit (
    candidate_id TEXT NOT NULL REFERENCES alpha_candidate(candidate_id),
    recall_hit_id TEXT NOT NULL REFERENCES alpha_recall_hit(recall_hit_id),
    PRIMARY KEY(candidate_id, recall_hit_id)
);
CREATE TABLE IF NOT EXISTS alpha_candidate_transition (
    transition_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    candidate_id TEXT NOT NULL REFERENCES alpha_candidate(candidate_id),
    from_state TEXT NOT NULL, to_state TEXT NOT NULL, event_type TEXT NOT NULL,
    CHECK(event_type <> 'STATE_TRANSITION' OR from_state <> to_state)
);
CREATE TABLE IF NOT EXISTS alpha_rule_contract_revision (
    rule_contract_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id), rule_hash TEXT NOT NULL CHECK(length(rule_hash) = 64),
    UNIQUE(market_id, rule_hash)
);
CREATE TABLE IF NOT EXISTS alpha_rule_gate_decision (
    gate_decision_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    rule_contract_id TEXT REFERENCES alpha_rule_contract_revision(rule_contract_id)
);
CREATE TABLE IF NOT EXISTS alpha_research_packet (
    packet_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id), packet_stage TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alpha_research_result (
    result_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id), packet_id TEXT REFERENCES alpha_research_packet(packet_id)
);
CREATE TABLE IF NOT EXISTS alpha_evidence_item (
    evidence_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id), content_sha256 TEXT CHECK(content_sha256 IS NULL OR length(content_sha256) = 64)
);
CREATE TABLE IF NOT EXISTS alpha_review_decision (
    decision_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id), market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    rule_hash TEXT NOT NULL CHECK(length(rule_hash) = 64)
);
CREATE TABLE IF NOT EXISTS alpha_prediction_record (
    prediction_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL, decision_id TEXT NOT NULL REFERENCES alpha_review_decision(decision_id),
    probability_estimate_id TEXT NOT NULL REFERENCES alpha_contract_record(record_id),
    packet_id TEXT NOT NULL REFERENCES alpha_research_packet(packet_id),
    orderbook_snapshot_id TEXT NOT NULL REFERENCES alpha_orderbook_snapshot(snapshot_id),
    position_state TEXT NOT NULL CHECK(position_state IN ('NO_POSITION','SIMULATED'))
);
CREATE TABLE IF NOT EXISTS alpha_run_artifact_link (
    run_id TEXT NOT NULL, artifact_id TEXT NOT NULL REFERENCES alpha_contract_record(record_id),
    relation TEXT NOT NULL, PRIMARY KEY(run_id, artifact_id, relation)
);
"""

# P0-07 requires rule text and the point-in-time contract corpus to carry
# independent revisions.  The original table's UNIQUE(market_id, rule_hash)
# cannot represent a corpus-only change, so this additive replacement keeps the
# v1 table readable and backfills it into a corpus-aware projection.  No legacy
# or v1 Alpha object is dropped or rewritten.
RULE_CORPUS_REVISION_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS alpha_rule_contract_revision_v2 (
    rule_contract_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    rule_hash TEXT NOT NULL CHECK(length(rule_hash) = 64),
    contract_corpus_sha256 TEXT CHECK(
        contract_corpus_sha256 IS NULL OR length(contract_corpus_sha256) = 64
    ),
    corpus_revision_key TEXT NOT NULL,
    contract_revision_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    compiler_version TEXT NOT NULL,
    UNIQUE(market_id, contract_revision_id)
);
CREATE TABLE IF NOT EXISTS alpha_rule_gate_decision_v2 (
    gate_decision_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    rule_contract_id TEXT NOT NULL REFERENCES alpha_rule_contract_revision_v2(rule_contract_id),
    stage TEXT NOT NULL CHECK(stage IN ('A','B')),
    decision TEXT NOT NULL,
    rule_hash TEXT NOT NULL CHECK(length(rule_hash) = 64),
    contract_revision_id TEXT NOT NULL,
    compiler_version TEXT NOT NULL
);
"""

# This is deliberately a separate, additive migration.  In particular, do not
# fold this index into 0001: its sealed SQL hash is already an evidence input.
# SQLite validates all existing rows while building the index, so a legacy
# duplicate aborts the surrounding migration transaction rather than leaving a
# partially upgraded catalog.
CATALOG_INTEGRITY_MIGRATION_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS alpha_market_condition_uidx
ON alpha_market(condition_id) WHERE condition_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS alpha_market_alias_active_uidx
ON alpha_market_alias(market_id, source, alias_type)
WHERE effective_to_utc IS NULL;
"""

# A RuleContract revision is a stable semantic identity, while each compiler
# run emits an immutable instance carrying its own run/clock/snapshot lineage.
# The sealed v2 projection made the semantic revision unique and therefore
# could not store more than one concrete instance.  V3 is additive and keeps
# the v2 tables readable while separating these two grains correctly.
RULE_CONTRACT_INSTANCE_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS alpha_rule_contract_instance_v3 (
    rule_contract_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    rule_hash TEXT NOT NULL CHECK(length(rule_hash) = 64),
    contract_corpus_sha256 TEXT CHECK(
        contract_corpus_sha256 IS NULL OR length(contract_corpus_sha256) = 64
    ),
    corpus_revision_key TEXT NOT NULL,
    contract_revision_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    compiler_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS alpha_rule_contract_instance_revision_idx
ON alpha_rule_contract_instance_v3(market_id, contract_revision_id);
CREATE TABLE IF NOT EXISTS alpha_rule_gate_decision_v3 (
    gate_decision_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    rule_contract_id TEXT NOT NULL REFERENCES alpha_rule_contract_instance_v3(rule_contract_id),
    stage TEXT NOT NULL CHECK(stage IN ('A','B')),
    decision TEXT NOT NULL,
    rule_hash TEXT NOT NULL CHECK(length(rule_hash) = 64),
    contract_revision_id TEXT NOT NULL,
    compiler_version TEXT NOT NULL
);
"""

# P0-01R2 introduced append-only change, capture and research-import contracts.
# Keep their projections separate from the sealed 0001--0004 schema.  In
# particular, this does not retrofit mutable state into alpha_research_result.
P0_01R2_PROJECTION_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS alpha_market_change_event_v2 (
    change_event_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    previous_snapshot_id TEXT REFERENCES alpha_market_snapshot_revision(snapshot_id),
    current_snapshot_id TEXT NOT NULL REFERENCES alpha_market_snapshot_revision(snapshot_id),
    previous_snapshot_sha256 TEXT CHECK(previous_snapshot_sha256 IS NULL OR length(previous_snapshot_sha256)=64),
    current_snapshot_sha256 TEXT NOT NULL CHECK(length(current_snapshot_sha256)=64),
    previous_status TEXT CHECK(previous_status IS NULL OR previous_status IN ('ACTIVE','CLOSED','RESOLVED','SUPERSEDED')),
    current_status TEXT NOT NULL CHECK(current_status IN ('ACTIVE','CLOSED','RESOLVED','SUPERSEDED')),
    previous_rule_hash TEXT CHECK(previous_rule_hash IS NULL OR length(previous_rule_hash)=64),
    current_rule_hash TEXT NOT NULL CHECK(length(current_rule_hash)=64),
    effective_at_utc TEXT NOT NULL,
    detected_at_utc TEXT NOT NULL,
    CHECK(previous_snapshot_id IS NULL OR previous_snapshot_id <> current_snapshot_id),
    CHECK(detected_at_utc >= effective_at_utc)
);
CREATE TABLE IF NOT EXISTS alpha_market_change_type_v2 (
    change_event_id TEXT NOT NULL REFERENCES alpha_market_change_event_v2(change_event_id),
    change_type TEXT NOT NULL CHECK(change_type IN ('NEW','RULE_CHANGED','LIFECYCLE_CHANGED','CLOSED','RESOLVED','METADATA_CHANGED','FAMILY_CHANGED')),
    PRIMARY KEY(change_event_id, change_type)
);
CREATE TABLE IF NOT EXISTS alpha_market_change_field_v2 (
    change_event_id TEXT NOT NULL REFERENCES alpha_market_change_event_v2(change_event_id),
    field_name TEXT NOT NULL CHECK(length(trim(field_name)) > 0),
    PRIMARY KEY(change_event_id, field_name)
);
CREATE TABLE IF NOT EXISTS alpha_book_capture_demand_v2 (
    demand_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    yes_token_id TEXT NOT NULL,
    no_token_id TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK(purpose IN ('SENSING','FORMAL_REVIEW')),
    trigger_artifact_id TEXT NOT NULL REFERENCES alpha_contract_record(record_id),
    trigger_artifact_sha256 TEXT NOT NULL CHECK(length(trigger_artifact_sha256)=64),
    blind_result_id TEXT REFERENCES alpha_contract_record(record_id),
    requested_at_utc TEXT NOT NULL,
    valid_until_utc TEXT NOT NULL,
    max_staleness_seconds INTEGER NOT NULL CHECK(max_staleness_seconds > 0),
    CHECK(yes_token_id <> no_token_id),
    CHECK(valid_until_utc > requested_at_utc),
    CHECK((purpose='FORMAL_REVIEW' AND blind_result_id IS NOT NULL) OR (purpose='SENSING' AND blind_result_id IS NULL)),
    FOREIGN KEY(market_id, yes_token_id) REFERENCES alpha_market_token_map(market_id, token_id),
    FOREIGN KEY(market_id, no_token_id) REFERENCES alpha_market_token_map(market_id, token_id)
);
CREATE TABLE IF NOT EXISTS alpha_book_capture_demand_target_v2 (
    demand_id TEXT NOT NULL REFERENCES alpha_book_capture_demand_v2(demand_id),
    target_size TEXT NOT NULL CHECK(length(trim(target_size)) > 0 AND CAST(target_size AS REAL) > 0),
    PRIMARY KEY(demand_id, target_size)
);
CREATE TABLE IF NOT EXISTS alpha_book_capture_receipt_v2 (
    receipt_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    demand_id TEXT NOT NULL REFERENCES alpha_book_capture_demand_v2(demand_id),
    demand_sha256 TEXT NOT NULL CHECK(length(demand_sha256)=64),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    purpose TEXT NOT NULL CHECK(purpose IN ('SENSING','FORMAL_REVIEW')),
    status TEXT NOT NULL CHECK(status IN ('ACCEPTED','STALE','SKIPPED','FAILED','EXPIRED')),
    capture_owner TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    orderbook_snapshot_id TEXT REFERENCES alpha_orderbook_snapshot(snapshot_id),
    orderbook_snapshot_sha256 TEXT CHECK(orderbook_snapshot_sha256 IS NULL OR length(orderbook_snapshot_sha256)=64),
    capture_group_id TEXT,
    source_observed_at_utc TEXT,
    error_code TEXT,
    CHECK((status='ACCEPTED' AND orderbook_snapshot_id IS NOT NULL AND orderbook_snapshot_sha256 IS NOT NULL AND capture_group_id IS NOT NULL AND source_observed_at_utc IS NOT NULL AND error_code IS NULL)
       OR (status='STALE' AND orderbook_snapshot_id IS NOT NULL AND orderbook_snapshot_sha256 IS NOT NULL AND capture_group_id IS NOT NULL AND source_observed_at_utc IS NOT NULL AND error_code IS NOT NULL)
       OR (status IN ('SKIPPED','FAILED','EXPIRED') AND orderbook_snapshot_id IS NULL AND orderbook_snapshot_sha256 IS NULL AND capture_group_id IS NULL AND source_observed_at_utc IS NULL AND error_code IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS alpha_source_artifact_v2 (
    artifact_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    source_name TEXT NOT NULL,
    source_url_or_source_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    captured_at_utc TEXT NOT NULL,
    effective_as_of_utc TEXT NOT NULL,
    capture_scope TEXT NOT NULL CHECK(capture_scope IN ('FULL_DOCUMENT','EXCERPT_ONLY','REFERENCE_ONLY')),
    hash_scope TEXT CHECK(hash_scope IS NULL OR hash_scope IN ('RAW_BYTES','NORMALIZED_TEXT','CLAIM_EXCERPT')),
    content_sha256 TEXT CHECK(content_sha256 IS NULL OR length(content_sha256)=64),
    content_length_bytes INTEGER CHECK(content_length_bytes IS NULL OR content_length_bytes > 0),
    artifact_locator TEXT,
    replayability TEXT NOT NULL CHECK(replayability IN ('FULL','EXCERPT','REFERENCE_ONLY')),
    CHECK(effective_as_of_utc <= captured_at_utc),
    CHECK((capture_scope='REFERENCE_ONLY' AND hash_scope IS NULL AND content_sha256 IS NULL AND content_length_bytes IS NULL AND artifact_locator IS NULL AND replayability='REFERENCE_ONLY')
       OR (capture_scope <> 'REFERENCE_ONLY' AND hash_scope IS NOT NULL AND content_sha256 IS NOT NULL AND content_length_bytes IS NOT NULL AND artifact_locator IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS alpha_research_result_envelope_v2 (
    result_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    packet_stage TEXT NOT NULL CHECK(packet_stage IN ('BLIND','MARKET_AWARE')),
    packet_id TEXT NOT NULL REFERENCES alpha_research_packet(packet_id),
    packet_sha256 TEXT NOT NULL CHECK(length(packet_sha256)=64),
    probability_estimate_id TEXT NOT NULL REFERENCES alpha_contract_record(record_id),
    completed_at_utc TEXT NOT NULL,
    producer TEXT NOT NULL,
    producer_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alpha_research_result_artifact_v2 (
    result_id TEXT NOT NULL REFERENCES alpha_research_result_envelope_v2(result_id),
    artifact_id TEXT NOT NULL REFERENCES alpha_source_artifact_v2(artifact_id),
    PRIMARY KEY(result_id, artifact_id)
);
CREATE TABLE IF NOT EXISTS alpha_research_result_evidence_v2 (
    result_id TEXT NOT NULL REFERENCES alpha_research_result_envelope_v2(result_id),
    evidence_id TEXT NOT NULL REFERENCES alpha_evidence_item(evidence_id),
    source_artifact_id TEXT NOT NULL REFERENCES alpha_source_artifact_v2(artifact_id),
    PRIMARY KEY(result_id, evidence_id),
    UNIQUE(result_id, source_artifact_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS alpha_research_import_receipt_v2 (
    import_receipt_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    packet_stage TEXT NOT NULL CHECK(packet_stage IN ('BLIND','MARKET_AWARE')),
    packet_id TEXT NOT NULL REFERENCES alpha_research_packet(packet_id),
    packet_sha256 TEXT NOT NULL CHECK(length(packet_sha256)=64),
    submitted_artifact_id TEXT NOT NULL REFERENCES alpha_source_artifact_v2(artifact_id),
    submitted_result_sha256 TEXT NOT NULL CHECK(length(submitted_result_sha256)=64),
    status TEXT NOT NULL CHECK(status IN ('ACCEPTED','REJECTED','QUARANTINED')),
    imported_at_utc TEXT NOT NULL,
    importer_version TEXT NOT NULL,
    accepted_result_id TEXT REFERENCES alpha_research_result_envelope_v2(result_id),
    accepted_result_sha256 TEXT CHECK(accepted_result_sha256 IS NULL OR length(accepted_result_sha256)=64),
    quarantine_artifact_id TEXT REFERENCES alpha_source_artifact_v2(artifact_id),
    CHECK((status='ACCEPTED' AND accepted_result_id IS NOT NULL AND accepted_result_sha256 IS NOT NULL AND quarantine_artifact_id IS NULL)
       OR (status='REJECTED' AND accepted_result_id IS NULL AND accepted_result_sha256 IS NULL AND quarantine_artifact_id IS NULL)
       OR (status='QUARANTINED' AND accepted_result_id IS NULL AND accepted_result_sha256 IS NULL AND quarantine_artifact_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS alpha_research_import_reason_v2 (
    import_receipt_id TEXT NOT NULL REFERENCES alpha_research_import_receipt_v2(import_receipt_id),
    reason TEXT NOT NULL CHECK(reason IN ('ACCEPTED','SCHEMA_VERSION_MISMATCH','PACKET_STAGE_MISMATCH','PACKET_ID_MISMATCH','PACKET_HASH_MISMATCH','RESULT_HASH_MISMATCH','SOURCE_ARTIFACT_MISSING','SOURCE_ARTIFACT_HASH_MISMATCH','BLIND_SEMANTIC_LEAK','VALIDATION_FAILED')),
    PRIMARY KEY(import_receipt_id, reason)
);
"""

# P1 closes predictions by appending new settlement/link/score/report facts.
# Existing PredictionRecord contracts and alpha_prediction_record rows remain
# immutable; no column below is an UPDATE target for a P0 record.
P1_RESOLUTION_LEARNING_MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS alpha_market_resolution_v1 (
    resolution_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    condition_id TEXT,
    outcome TEXT NOT NULL CHECK(outcome IN ('YES','NO','INVALID')),
    adjudication_status TEXT NOT NULL CHECK(adjudication_status IN ('FINAL','PENDING_DISPUTE')),
    resolved_at_utc TEXT NOT NULL,
    source_observed_at_utc TEXT NOT NULL,
    source_artifact_id TEXT NOT NULL REFERENCES alpha_source_artifact_v2(artifact_id),
    source_artifact_sha256 TEXT NOT NULL CHECK(length(source_artifact_sha256)=64),
    rule_contract_id TEXT NOT NULL REFERENCES alpha_rule_contract_instance_v3(rule_contract_id),
    rule_contract_sha256 TEXT NOT NULL CHECK(length(rule_contract_sha256)=64),
    rule_hash TEXT NOT NULL CHECK(length(rule_hash)=64),
    contract_revision_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    supersedes_resolution_id TEXT REFERENCES alpha_market_resolution_v1(resolution_id),
    supersedes_resolution_sha256 TEXT CHECK(supersedes_resolution_sha256 IS NULL OR length(supersedes_resolution_sha256)=64),
    CHECK(resolved_at_utc <= source_observed_at_utc),
    CHECK((supersedes_resolution_id IS NULL) = (supersedes_resolution_sha256 IS NULL)),
    CHECK(supersedes_resolution_id IS NULL OR supersedes_resolution_id <> resolution_id)
);
CREATE INDEX IF NOT EXISTS alpha_market_resolution_market_clock_idx
ON alpha_market_resolution_v1(market_id, resolved_at_utc, source_observed_at_utc);
CREATE TABLE IF NOT EXISTS alpha_prediction_resolution_link_v1 (
    link_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    market_id TEXT NOT NULL REFERENCES alpha_market(market_id),
    prediction_id TEXT NOT NULL REFERENCES alpha_prediction_record(prediction_id),
    prediction_sha256 TEXT NOT NULL CHECK(length(prediction_sha256)=64),
    decision_id TEXT NOT NULL REFERENCES alpha_review_decision(decision_id),
    decision_sha256 TEXT NOT NULL CHECK(length(decision_sha256)=64),
    probability_estimate_id TEXT NOT NULL REFERENCES alpha_contract_record(record_id),
    probability_estimate_sha256 TEXT NOT NULL CHECK(length(probability_estimate_sha256)=64),
    rule_contract_id TEXT NOT NULL REFERENCES alpha_rule_contract_instance_v3(rule_contract_id),
    rule_contract_sha256 TEXT NOT NULL CHECK(length(rule_contract_sha256)=64),
    resolution_id TEXT NOT NULL REFERENCES alpha_market_resolution_v1(resolution_id),
    resolution_sha256 TEXT NOT NULL CHECK(length(resolution_sha256)=64),
    linked_at_utc TEXT NOT NULL,
    scoring_eligibility TEXT NOT NULL CHECK(scoring_eligibility IN ('ELIGIBLE','EXCLUDED_INVALID','EXCLUDED_PENDING_DISPUTE')),
    exclusion_reason TEXT,
    predicted_probability TEXT NOT NULL,
    market_baseline_probability TEXT,
    market_type TEXT NOT NULL,
    rule_clarity TEXT NOT NULL,
    orderbook_snapshot_id TEXT REFERENCES alpha_orderbook_snapshot(snapshot_id),
    orderbook_snapshot_sha256 TEXT CHECK(orderbook_snapshot_sha256 IS NULL OR length(orderbook_snapshot_sha256)=64),
    entry_direction TEXT CHECK(entry_direction IS NULL OR entry_direction IN ('YES','NO')),
    entry_token_id TEXT,
    entry_quantity TEXT,
    entry_vwap TEXT,
    entry_gross_cost TEXT,
    entry_fee_amount TEXT,
    entry_fee_model_version TEXT,
    UNIQUE(prediction_id, resolution_id),
    CHECK((scoring_eligibility='ELIGIBLE' AND exclusion_reason IS NULL) OR (scoring_eligibility<>'ELIGIBLE' AND length(trim(exclusion_reason))>0)),
    CHECK((orderbook_snapshot_id IS NULL AND orderbook_snapshot_sha256 IS NULL AND entry_direction IS NULL AND entry_token_id IS NULL AND entry_quantity IS NULL AND entry_vwap IS NULL AND entry_gross_cost IS NULL AND entry_fee_amount IS NULL AND entry_fee_model_version IS NULL)
       OR (orderbook_snapshot_id IS NOT NULL AND orderbook_snapshot_sha256 IS NOT NULL AND entry_direction IS NOT NULL AND entry_token_id IS NOT NULL AND entry_quantity IS NOT NULL AND entry_vwap IS NOT NULL AND entry_gross_cost IS NOT NULL AND entry_fee_amount IS NOT NULL AND entry_fee_model_version IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS alpha_prediction_resolution_link_prediction_idx
ON alpha_prediction_resolution_link_v1(prediction_id, linked_at_utc);
CREATE INDEX IF NOT EXISTS alpha_prediction_resolution_link_resolution_idx
ON alpha_prediction_resolution_link_v1(resolution_id);
CREATE TABLE IF NOT EXISTS alpha_prediction_score_v1 (
    score_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    link_id TEXT NOT NULL REFERENCES alpha_prediction_resolution_link_v1(link_id),
    link_sha256 TEXT NOT NULL CHECK(length(link_sha256)=64),
    prediction_id TEXT NOT NULL REFERENCES alpha_prediction_record(prediction_id),
    resolution_id TEXT NOT NULL REFERENCES alpha_market_resolution_v1(resolution_id),
    outcome TEXT NOT NULL CHECK(outcome IN ('YES','NO')),
    outcome_label INTEGER NOT NULL CHECK(outcome_label IN (0,1)),
    predicted_probability TEXT NOT NULL,
    market_baseline_probability TEXT,
    brier_score TEXT NOT NULL,
    log_loss TEXT NOT NULL,
    market_baseline_brier_score TEXT,
    market_baseline_log_loss TEXT,
    simulated_pnl TEXT,
    market_type TEXT NOT NULL,
    rule_clarity TEXT NOT NULL,
    entry_vwap TEXT,
    scoring_policy_version TEXT NOT NULL,
    log_loss_epsilon TEXT NOT NULL,
    scoring_policy_sha256 TEXT NOT NULL CHECK(length(scoring_policy_sha256)=64),
    UNIQUE(link_id, scoring_policy_sha256),
    CHECK((market_baseline_probability IS NULL AND market_baseline_brier_score IS NULL AND market_baseline_log_loss IS NULL)
       OR (market_baseline_probability IS NOT NULL AND market_baseline_brier_score IS NOT NULL AND market_baseline_log_loss IS NOT NULL)),
    CHECK((entry_vwap IS NULL) = (simulated_pnl IS NULL))
);
CREATE TABLE IF NOT EXISTS alpha_calibration_report_v1 (
    report_id TEXT PRIMARY KEY REFERENCES alpha_contract_record(record_id),
    dimension TEXT NOT NULL CHECK(dimension IN ('MARKET_TYPE','RULE_CLARITY','ENTRY_PRICE')),
    calibration_policy_version TEXT NOT NULL,
    rule_clarity_boundaries_json TEXT NOT NULL,
    entry_price_boundaries_json TEXT NOT NULL,
    calibration_policy_sha256 TEXT NOT NULL CHECK(length(calibration_policy_sha256)=64)
);
CREATE TABLE IF NOT EXISTS alpha_calibration_report_score_v1 (
    report_id TEXT NOT NULL REFERENCES alpha_calibration_report_v1(report_id),
    score_id TEXT NOT NULL REFERENCES alpha_prediction_score_v1(score_id),
    score_sha256 TEXT NOT NULL CHECK(length(score_sha256)=64),
    PRIMARY KEY(report_id, score_id)
);
CREATE TABLE IF NOT EXISTS alpha_calibration_report_exclusion_v1 (
    report_id TEXT NOT NULL REFERENCES alpha_calibration_report_v1(report_id),
    score_id TEXT NOT NULL REFERENCES alpha_prediction_score_v1(score_id),
    PRIMARY KEY(report_id, score_id)
);
CREATE TABLE IF NOT EXISTS alpha_calibration_slice_v1 (
    report_id TEXT NOT NULL REFERENCES alpha_calibration_report_v1(report_id),
    slice_key TEXT NOT NULL,
    sample_count INTEGER NOT NULL CHECK(sample_count > 0),
    mean_predicted_probability TEXT NOT NULL,
    observed_yes_rate TEXT NOT NULL,
    mean_brier_score TEXT NOT NULL,
    mean_log_loss TEXT NOT NULL,
    mean_market_baseline_brier_score TEXT,
    mean_market_baseline_log_loss TEXT,
    mean_simulated_pnl TEXT,
    PRIMARY KEY(report_id, slice_key),
    CHECK((mean_market_baseline_brier_score IS NULL) = (mean_market_baseline_log_loss IS NULL))
);
"""


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def schema_manifest() -> dict[str, str]:
    """Return the pinned manifest used by readers and migrations."""
    return {"schema_version": ALPHA_SCHEMA_VERSION, "migration_id": MIGRATION_ID, "sql_sha256": _sha(MIGRATION_SQL)}


def rule_corpus_revision_manifest() -> dict[str, str]:
    """Return the additive P0-07 storage repair manifest."""

    return {
        "schema_version": ALPHA_SCHEMA_VERSION,
        "migration_id": RULE_CORPUS_REVISION_MIGRATION_ID,
        "sql_sha256": _sha(RULE_CORPUS_REVISION_MIGRATION_SQL),
    }


def catalog_integrity_manifest() -> dict[str, str]:
    """Return the additive catalog identity/alias integrity manifest."""

    return {
        "schema_version": ALPHA_SCHEMA_VERSION,
        "migration_id": CATALOG_INTEGRITY_MIGRATION_ID,
        "sql_sha256": _sha(CATALOG_INTEGRITY_MIGRATION_SQL),
    }


def rule_contract_instance_manifest() -> dict[str, str]:
    """Return the additive semantic-revision/concrete-instance manifest."""

    return {
        "schema_version": ALPHA_SCHEMA_VERSION,
        "migration_id": RULE_CONTRACT_INSTANCE_MIGRATION_ID,
        "sql_sha256": _sha(RULE_CONTRACT_INSTANCE_MIGRATION_SQL),
    }


def p0_01r2_projection_manifest() -> dict[str, str]:
    """Return the additive storage manifest for released P0-01R2 contracts."""

    return {
        "schema_version": ALPHA_SCHEMA_VERSION,
        "migration_id": P0_01R2_PROJECTION_MIGRATION_ID,
        "sql_sha256": _sha(P0_01R2_PROJECTION_MIGRATION_SQL),
    }


def p1_resolution_learning_manifest() -> dict[str, str]:
    """Return the additive P1 settlement and learning projection manifest."""

    return {
        "schema_version": ALPHA_SCHEMA_VERSION,
        "migration_id": P1_RESOLUTION_LEARNING_MIGRATION_ID,
        "sql_sha256": _sha(P1_RESOLUTION_LEARNING_MIGRATION_SQL),
    }


def _backfill_rule_contract_revisions(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT r.rule_contract_id, r.market_id, r.rule_hash, c.canonical_json "
        "FROM alpha_rule_contract_revision r "
        "JOIN alpha_contract_record c ON c.record_id = r.rule_contract_id"
    ).fetchall()
    for rule_contract_id, market_id, rule_hash, payload in rows:
        try:
            contract = json.loads(str(payload))
            contract_corpus_sha256 = contract.get("contract_corpus_sha256")
            contract_revision_id = contract["contract_revision_id"]
            parser_version = contract["parser_version"]
            compiler_version = contract["source_version"]
        except (AttributeError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("cannot backfill invalid RuleContract canonical JSON") from exc
        except KeyError as exc:
            raise RuntimeError("cannot backfill incomplete RuleContract canonical JSON") from exc
        corpus_revision_key = contract_corpus_sha256 or "NO_CORPUS"
        conn.execute(
            "INSERT OR IGNORE INTO alpha_rule_contract_revision_v2 "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rule_contract_id,
                market_id,
                rule_hash,
                contract_corpus_sha256,
                corpus_revision_key,
                contract_revision_id,
                parser_version,
                compiler_version,
            ),
        )


def _backfill_rule_contract_instances(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO alpha_rule_contract_instance_v3 "
        "SELECT rule_contract_id, market_id, rule_hash, contract_corpus_sha256, "
        "corpus_revision_key, contract_revision_id, parser_version, compiler_version "
        "FROM alpha_rule_contract_revision_v2"
    )
    conn.execute(
        "INSERT OR IGNORE INTO alpha_rule_gate_decision_v3 "
        "SELECT gate_decision_id, rule_contract_id, stage, decision, rule_hash, "
        "contract_revision_id, compiler_version FROM alpha_rule_gate_decision_v2"
    )


@contextmanager
def _connection(target: str | Path | sqlite3.Connection) -> Iterator[tuple[sqlite3.Connection, bool]]:
    if isinstance(target, sqlite3.Connection):
        yield target, False
        return
    if not isinstance(target, (str, Path)):
        raise TypeError("target must be a caller-provided SQLite path or connection")
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        yield conn, True
    finally:
        conn.close()


def migrate(target: str | Path | sqlite3.Connection) -> dict[str, str]:
    """Apply the owned Alpha migrations atomically and idempotently."""
    manifest = schema_manifest()
    rule_revision_manifest = rule_corpus_revision_manifest()
    catalog_integrity = catalog_integrity_manifest()
    rule_instances = rule_contract_instance_manifest()
    p0_01r2_projections = p0_01r2_projection_manifest()
    p1_resolution_learning = p1_resolution_learning_manifest()
    with _connection(target) as (conn, _owned):
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute("BEGIN IMMEDIATE")
            # ``sqlite3.Connection.executescript`` implicitly commits before it
            # runs, so execute statements individually to keep DDL transactional.
            for statement in MIGRATION_SQL.split(";\n"):
                if statement.strip():
                    conn.execute(statement)
            for statement in RULE_CORPUS_REVISION_MIGRATION_SQL.split(";\n"):
                if statement.strip():
                    conn.execute(statement)
            for statement in CATALOG_INTEGRITY_MIGRATION_SQL.split(";\n"):
                if statement.strip():
                    conn.execute(statement)
            for statement in RULE_CONTRACT_INSTANCE_MIGRATION_SQL.split(";\n"):
                if statement.strip():
                    conn.execute(statement)
            for statement in P0_01R2_PROJECTION_MIGRATION_SQL.split(";\n"):
                if statement.strip():
                    conn.execute(statement)
            for statement in P1_RESOLUTION_LEARNING_MIGRATION_SQL.split(";\n"):
                if statement.strip():
                    conn.execute(statement)
            _backfill_rule_contract_revisions(conn)
            _backfill_rule_contract_instances(conn)
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            conn.execute(
                "INSERT OR IGNORE INTO alpha_schema_migrations VALUES (?, ?, ?, ?)",
                (MIGRATION_ID, ALPHA_SCHEMA_VERSION, manifest["sql_sha256"], now),
            )
            conn.execute(
                "INSERT OR IGNORE INTO alpha_schema_migrations VALUES (?, ?, ?, ?)",
                (
                    RULE_CORPUS_REVISION_MIGRATION_ID,
                    ALPHA_SCHEMA_VERSION,
                    rule_revision_manifest["sql_sha256"],
                    now,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO alpha_schema_migrations VALUES (?, ?, ?, ?)",
                (
                    P0_01R2_PROJECTION_MIGRATION_ID,
                    ALPHA_SCHEMA_VERSION,
                    p0_01r2_projections["sql_sha256"],
                    now,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO alpha_schema_migrations VALUES (?, ?, ?, ?)",
                (
                    RULE_CONTRACT_INSTANCE_MIGRATION_ID,
                    ALPHA_SCHEMA_VERSION,
                    rule_instances["sql_sha256"],
                    now,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO alpha_schema_migrations VALUES (?, ?, ?, ?)",
                (
                    CATALOG_INTEGRITY_MIGRATION_ID,
                    ALPHA_SCHEMA_VERSION,
                    catalog_integrity["sql_sha256"],
                    now,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO alpha_schema_migrations VALUES (?, ?, ?, ?)",
                (
                    P1_RESOLUTION_LEARNING_MIGRATION_ID,
                    ALPHA_SCHEMA_VERSION,
                    p1_resolution_learning["sql_sha256"],
                    now,
                ),
            )
            manifest_sha256 = content_sha256(manifest)
            conn.execute(
                "INSERT OR IGNORE INTO alpha_schema_manifest VALUES (?, ?, ?, ?, ?)",
                (ALPHA_SCHEMA_VERSION, MIGRATION_ID, ALPHA_CONTRACT_VERSION, manifest_sha256, now),
            )
            row = conn.execute(
                "SELECT schema_version, sql_sha256 FROM alpha_schema_migrations WHERE migration_id = ?",
                (MIGRATION_ID,),
            ).fetchone()
            if row is None or tuple(row) != (ALPHA_SCHEMA_VERSION, manifest["sql_sha256"]):
                raise RuntimeError("incompatible Alpha migration already recorded")
            rule_revision_row = conn.execute(
                "SELECT schema_version, sql_sha256 FROM alpha_schema_migrations WHERE migration_id = ?",
                (RULE_CORPUS_REVISION_MIGRATION_ID,),
            ).fetchone()
            if rule_revision_row is None or tuple(rule_revision_row) != (
                ALPHA_SCHEMA_VERSION,
                rule_revision_manifest["sql_sha256"],
            ):
                raise RuntimeError("incompatible Alpha rule/corpus revision migration already recorded")
            catalog_integrity_row = conn.execute(
                "SELECT schema_version, sql_sha256 FROM alpha_schema_migrations WHERE migration_id = ?",
                (CATALOG_INTEGRITY_MIGRATION_ID,),
            ).fetchone()
            if catalog_integrity_row is None or tuple(catalog_integrity_row) != (
                ALPHA_SCHEMA_VERSION,
                catalog_integrity["sql_sha256"],
            ):
                raise RuntimeError("incompatible Alpha catalog integrity migration already recorded")
            rule_instance_row = conn.execute(
                "SELECT schema_version, sql_sha256 FROM alpha_schema_migrations WHERE migration_id = ?",
                (RULE_CONTRACT_INSTANCE_MIGRATION_ID,),
            ).fetchone()
            if rule_instance_row is None or tuple(rule_instance_row) != (
                ALPHA_SCHEMA_VERSION,
                rule_instances["sql_sha256"],
            ):
                raise RuntimeError("incompatible Alpha rule contract instance migration already recorded")
            p0_01r2_row = conn.execute(
                "SELECT schema_version, sql_sha256 FROM alpha_schema_migrations WHERE migration_id = ?",
                (P0_01R2_PROJECTION_MIGRATION_ID,),
            ).fetchone()
            if p0_01r2_row is None or tuple(p0_01r2_row) != (
                ALPHA_SCHEMA_VERSION,
                p0_01r2_projections["sql_sha256"],
            ):
                raise RuntimeError("incompatible Alpha P0-01R2 projection migration already recorded")
            p1_resolution_row = conn.execute(
                "SELECT schema_version, sql_sha256 FROM alpha_schema_migrations WHERE migration_id = ?",
                (P1_RESOLUTION_LEARNING_MIGRATION_ID,),
            ).fetchone()
            if p1_resolution_row is None or tuple(p1_resolution_row) != (
                ALPHA_SCHEMA_VERSION,
                p1_resolution_learning["sql_sha256"],
            ):
                raise RuntimeError("incompatible Alpha P1 resolution/learning migration already recorded")
            manifest_row = conn.execute(
                "SELECT migration_id, contract_version, manifest_sha256 FROM alpha_schema_manifest WHERE schema_version = ?",
                (ALPHA_SCHEMA_VERSION,),
            ).fetchone()
            if manifest_row is None or tuple(manifest_row) != (MIGRATION_ID, ALPHA_CONTRACT_VERSION, manifest_sha256):
                raise RuntimeError("incompatible Alpha schema manifest already recorded")
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return manifest
