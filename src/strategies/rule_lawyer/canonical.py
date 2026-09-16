"""Incremental canonical materializer for dispute-repricing forward evidence.

Append-only JSONL remains raw truth.  This module builds a queryable domain
mart with stable cross-layer identities and byte-offset lineage; it never
rewrites or deletes the source files.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator

from src.platform.market_data.identity import canonical_json_hash


SCHEMA_VERSION = 2


def _first_not_none(*values: Any) -> Any:
    """Return the first present value without treating numeric zero as missing."""
    return next((value for value in values if value is not None), None)

DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY,
  description TEXT NOT NULL,
  applied_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS ingest_watermarks (
  source_path TEXT PRIMARY KEY,
  byte_offset INTEGER NOT NULL,
  source_size INTEGER NOT NULL,
  prefix_bytes INTEGER NOT NULL,
  head_sha256 TEXT NOT NULL,
  rows_ingested INTEGER NOT NULL,
  updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS dispute_cases (
  case_id TEXT PRIMARY KEY,
  market_id TEXT,
  condition_id TEXT,
  opportunity_cluster_id TEXT,
  title TEXT,
  first_observed_at_utc TEXT,
  latest_snapshot_at_utc TEXT,
  request_class TEXT,
  latest_contract_revision_id TEXT,
  latest_market_group_snapshot_id TEXT,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS case_events (
  event_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  event_ts_utc TEXT,
  detected_at_utc TEXT,
  request_id TEXT,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contract_fragments (
  contract_revision_id TEXT NOT NULL,
  fragment_id TEXT NOT NULL,
  case_id TEXT NOT NULL,
  origin TEXT,
  legal_role TEXT,
  effective_at_utc TEXT,
  first_observed_at_utc TEXT,
  content_sha256 TEXT,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL,
  PRIMARY KEY(contract_revision_id, fragment_id)
);
CREATE TABLE IF NOT EXISTS market_book_snapshots (
  book_snapshot_id TEXT PRIMARY KEY,
  token_id TEXT NOT NULL,
  request_batch_capture_id TEXT,
  exchange_book_ts_utc TEXT,
  request_started_at_utc TEXT,
  response_received_at_utc TEXT,
  parsed_at_utc TEXT,
  available_at_utc TEXT,
  raw_payload_hash TEXT,
  clock_lineage_status TEXT,
  event_time_pit_scorable INTEGER,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_group_snapshots (
  group_snapshot_id TEXT PRIMARY KEY,
  case_id TEXT,
  group_id TEXT NOT NULL,
  group_kind TEXT NOT NULL,
  captured_at_utc TEXT,
  available_at_utc TEXT,
  capture_batch_id TEXT,
  batch_complete INTEGER NOT NULL,
  blockers_json TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_group_expressions (
  group_snapshot_id TEXT NOT NULL,
  expression_id TEXT NOT NULL,
  market_id TEXT,
  condition_id TEXT,
  outcome_index INTEGER,
  outcome_label TEXT,
  token_id TEXT,
  ordinal INTEGER,
  book_snapshot_id TEXT,
  metadata_json TEXT NOT NULL,
  PRIMARY KEY(group_snapshot_id, expression_id)
);
CREATE TABLE IF NOT EXISTS adjudication_revisions (
  adjudication_revision_id TEXT PRIMARY KEY,
  case_id TEXT,
  market_id TEXT,
  status TEXT,
  winning_outcome TEXT,
  confidence REAL,
  resolver_id TEXT,
  prompt_version TEXT,
  observed_at_utc TEXT,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  proof_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fact_signal_candidates (
  candidate_id TEXT PRIMARY KEY,
  strategy_key TEXT NOT NULL,
  policy_id TEXT,
  case_id TEXT,
  market_id TEXT,
  opportunity_cluster_id TEXT,
  trigger_event_id TEXT,
  adjudication_revision_id TEXT,
  execution_book_snapshot_id TEXT,
  selected_token_id TEXT,
  selected_outcome TEXT,
  track TEXT,
  candidate_status TEXT NOT NULL,
  policy_selected INTEGER NOT NULL,
  model_probability REAL,
  market_probability REAL,
  execution_price REAL,
  fee_adjusted_edge REAL,
  decision_ts_utc TEXT,
  blockers_json TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capture_demands (
  demand_id TEXT PRIMARY KEY,
  consumer_id TEXT NOT NULL,
  strategy_key TEXT NOT NULL,
  condition_id TEXT,
  token_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  priority TEXT NOT NULL,
  desired_transport TEXT NOT NULL,
  requested_at_utc TEXT NOT NULL,
  expires_at_utc TEXT NOT NULL,
  trigger_event_id TEXT,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capture_receipts (
  receipt_id TEXT PRIMARY KEY,
  demand_id TEXT NOT NULL,
  token_id TEXT NOT NULL,
  resolution_status TEXT NOT NULL,
  subscription_epoch_id TEXT NOT NULL,
  owner_producer TEXT NOT NULL,
  owner_build_id TEXT,
  accepted_at_utc TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rest_checkpoint_receipts (
  receipt_id TEXT PRIMARY KEY,
  demand_id TEXT NOT NULL,
  case_id TEXT,
  token_id TEXT NOT NULL,
  checkpoint_seconds INTEGER NOT NULL,
  due_at_utc TEXT,
  captured_at_utc TEXT NOT NULL,
  lateness_seconds INTEGER NOT NULL,
  capture_status TEXT NOT NULL,
  book_snapshot_id TEXT NOT NULL,
  owner TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trade_intents (
  intent_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  requested_size REAL NOT NULL,
  token_id TEXT NOT NULL,
  side TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_plans (
  plan_id TEXT PRIMARY KEY,
  intent_id TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  token_id TEXT NOT NULL,
  side TEXT NOT NULL,
  hypothetical_shares REAL NOT NULL,
  limit_price REAL NOT NULL,
  book_snapshot_id TEXT,
  created_at_utc TEXT NOT NULL,
  mode TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_orders (
  order_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  intent_id TEXT NOT NULL,
  token_id TEXT NOT NULL,
  status TEXT NOT NULL,
  actual_notional REAL NOT NULL,
  accepted_at_utc TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_fills (
  fill_id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL,
  plan_id TEXT NOT NULL,
  intent_id TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  token_id TEXT NOT NULL,
  hypothetical_shares REAL NOT NULL,
  fill_price REAL NOT NULL,
  hypothetical_fee_per_share REAL NOT NULL,
  actual_shares REAL NOT NULL,
  actual_cost REAL NOT NULL,
  observed_at_utc TEXT NOT NULL,
  book_snapshot_id TEXT,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_positions (
  position_id TEXT PRIMARY KEY,
  candidate_id TEXT,
  case_id TEXT,
  market_id TEXT,
  token_id TEXT,
  outcome TEXT,
  policy_id TEXT,
  quantity REAL,
  entry_price REAL,
  entry_cost REAL,
  opened_at_utc TEXT,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_markouts (
  markout_id TEXT PRIMARY KEY,
  position_id TEXT NOT NULL,
  checkpoint TEXT,
  checkpoint_target_seconds INTEGER,
  observed_at_utc TEXT,
  exit_bid_vwap REAL,
  net_pnl REAL,
  return_on_entry_cost REAL,
  settlement_payout REAL,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settlement_labels (
  settlement_label_id TEXT PRIMARY KEY,
  case_id TEXT NOT NULL,
  market_id TEXT,
  request_class TEXT,
  settlement_price TEXT,
  market_closed INTEGER,
  observed_at_utc TEXT,
  source_path TEXT NOT NULL,
  source_byte_offset INTEGER NOT NULL,
  metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_market ON dispute_cases(market_id);
CREATE INDEX IF NOT EXISTS idx_case_events_case ON case_events(case_id, event_ts_utc);
CREATE INDEX IF NOT EXISTS idx_books_token_time ON market_book_snapshots(token_id, available_at_utc);
CREATE INDEX IF NOT EXISTS idx_candidates_case ON fact_signal_candidates(case_id, decision_ts_utc);
CREATE INDEX IF NOT EXISTS idx_demands_token_time ON capture_demands(token_id, requested_at_utc);
CREATE INDEX IF NOT EXISTS idx_receipts_demand ON capture_receipts(demand_id, accepted_at_utc);
CREATE INDEX IF NOT EXISTS idx_rest_checkpoints_demand ON rest_checkpoint_receipts(demand_id, checkpoint_seconds);
CREATE INDEX IF NOT EXISTS idx_paper_fills_intent ON paper_fills(intent_id);
CREATE INDEX IF NOT EXISTS idx_markouts_position ON shadow_markouts(position_id, observed_at_utc);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _prefix_hash(path: Path, prefix_bytes: int) -> str:
    with path.open("rb") as handle:
        return hashlib.sha256(handle.read(max(0, int(prefix_bytes)))).hexdigest()


def _new_rows(path: Path, offset: int) -> Iterator[tuple[int, int, dict[str, Any]]]:
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            start = handle.tell()
            raw = handle.readline()
            if not raw:
                return
            end = handle.tell()
            if not raw.endswith(b"\n"):
                raise ValueError(f"partial JSONL row at byte {start}: {path}")
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL row at byte {start}: {path}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row is not an object at byte {start}: {path}")
            yield start, end, row


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    watermark_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(ingest_watermarks)")
    }
    if "prefix_bytes" not in watermark_columns:
        conn.execute(
            "ALTER TABLE ingest_watermarks ADD COLUMN prefix_bytes INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            """UPDATE ingest_watermarks
               SET prefix_bytes=CASE WHEN source_size < 65536 THEN source_size ELSE 65536 END
               WHERE prefix_bytes=0 AND source_size>0"""
        )
    conn.execute(
        "INSERT OR IGNORE INTO schema_version(version,description) VALUES (?,?)",
        (SCHEMA_VERSION, "dispute repricing canonical mart v2 paper/capture lineage"),
    )


def _case_event(conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int) -> None:
    case_id = str(row.get("case_id") or "")
    if not case_id:
        raise ValueError("case event missing case_id")
    detected = str(row.get("first_observed_at_utc") or "") or None
    raw_request = row.get("raw_request") if isinstance(row.get("raw_request"), dict) else {}
    event_id = canonical_json_hash(
        {
            "schema": "dispute_case_event_v1",
            "case_id": case_id,
            "event_type": "dispute_first_seen",
            "request_id": raw_request.get("id"),
            "dispute_hash": raw_request.get("disputeHash"),
            "detected_at_utc": detected,
        }
    )
    conn.execute(
        """INSERT OR IGNORE INTO case_events VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            event_id,
            case_id,
            "dispute_first_seen",
            raw_request.get("disputeTimestamp"),
            detected,
            raw_request.get("id"),
            source,
            offset,
            _json(row),
        ),
    )
    conn.execute(
        """INSERT INTO dispute_cases(
          case_id,first_observed_at_utc,source_path,source_byte_offset,metadata_json
        ) VALUES (?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET
          first_observed_at_utc=COALESCE(dispute_cases.first_observed_at_utc,excluded.first_observed_at_utc)
        """,
        (case_id, detected, source, offset, _json({"raw_request_id": raw_request.get("id")})),
    )


def _snapshot(conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int) -> None:
    case_id = str(row.get("case_id") or "")
    if not case_id:
        raise ValueError("snapshot missing case_id")
    captured = str(row.get("captured_at_utc") or "") or None
    corpus = row.get("contract_corpus") if isinstance(row.get("contract_corpus"), dict) else {}
    contract_revision = str(corpus.get("contract_corpus_sha256") or "") or canonical_json_hash(corpus)
    group = row.get("market_group_snapshot") if isinstance(row.get("market_group_snapshot"), dict) else {}
    group_snapshot_id = str(group.get("group_snapshot_id") or "") or None
    proposal = row.get("proposal") if isinstance(row.get("proposal"), dict) else {}
    conn.execute(
        """INSERT INTO dispute_cases(
          case_id,market_id,condition_id,opportunity_cluster_id,title,first_observed_at_utc,
          latest_snapshot_at_utc,request_class,latest_contract_revision_id,
          latest_market_group_snapshot_id,source_path,source_byte_offset,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(case_id) DO UPDATE SET
          market_id=excluded.market_id,condition_id=excluded.condition_id,
          opportunity_cluster_id=excluded.opportunity_cluster_id,title=excluded.title,
          latest_snapshot_at_utc=excluded.latest_snapshot_at_utc,
          request_class=excluded.request_class,
          latest_contract_revision_id=excluded.latest_contract_revision_id,
          latest_market_group_snapshot_id=excluded.latest_market_group_snapshot_id,
          source_path=excluded.source_path,source_byte_offset=excluded.source_byte_offset,
          metadata_json=excluded.metadata_json
        WHERE dispute_cases.latest_snapshot_at_utc IS NULL
           OR excluded.latest_snapshot_at_utc >= dispute_cases.latest_snapshot_at_utc
        """,
        (
            case_id,
            row.get("market_id"),
            row.get("condition_id"),
            row.get("opportunity_cluster_id"),
            row.get("title"),
            captured,
            captured,
            proposal.get("request_class"),
            contract_revision,
            group_snapshot_id,
            source,
            offset,
            _json({"schema_version": row.get("schema_version"), "request_id": row.get("request_id")}),
        ),
    )
    for fragment in corpus.get("fragments") or []:
        if not isinstance(fragment, dict):
            continue
        fragment_id = str(fragment.get("fragment_id") or "") or canonical_json_hash(fragment)
        conn.execute(
            """INSERT OR IGNORE INTO contract_fragments VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                contract_revision,
                fragment_id,
                case_id,
                fragment.get("origin"),
                fragment.get("legal_role"),
                fragment.get("effective_at_utc"),
                fragment.get("first_observed_at_utc"),
                fragment.get("content_sha256"),
                source,
                offset,
                _json(fragment),
            ),
        )
    for token_id, capture in (row.get("book_captures") or {}).items():
        if not isinstance(capture, dict) or not capture.get("book_capture_id"):
            continue
        conn.execute(
            """INSERT OR IGNORE INTO market_book_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                capture["book_capture_id"],
                str(token_id),
                capture.get("request_batch_capture_id"),
                capture.get("exchange_book_ts_utc"),
                capture.get("request_started_at_utc"),
                capture.get("response_received_at_utc"),
                capture.get("parsed_at_utc"),
                capture.get("available_at_utc"),
                capture.get("raw_payload_hash"),
                capture.get("clock_lineage_status"),
                int(bool(capture.get("event_time_pit_scorable"))),
                source,
                offset,
                _json(capture),
            ),
        )
    if group_snapshot_id:
        conn.execute(
            """INSERT OR IGNORE INTO market_group_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                group_snapshot_id,
                case_id,
                group.get("group_id"),
                group.get("group_kind"),
                group.get("captured_at_utc"),
                group.get("available_at_utc"),
                group.get("capture_batch_id"),
                int(bool(group.get("batch_complete"))),
                _json(group.get("blockers") or []),
                source,
                offset,
                _json(group.get("metadata") or {}),
            ),
        )
        refs = group.get("book_snapshot_ids") or {}
        for expression in group.get("expressions") or []:
            if not isinstance(expression, dict) or not expression.get("expression_id"):
                continue
            conn.execute(
                """INSERT OR IGNORE INTO market_group_expressions VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    group_snapshot_id,
                    expression["expression_id"],
                    expression.get("market_id"),
                    expression.get("condition_id"),
                    expression.get("outcome_index"),
                    expression.get("outcome_label"),
                    expression.get("token_id"),
                    expression.get("ordinal"),
                    refs.get(str(expression.get("token_id") or "")),
                    _json(expression.get("metadata") or {}),
                ),
            )
    verdict = row.get("rule_verdict") if isinstance(row.get("rule_verdict"), dict) else {}
    if verdict:
        adjudication_id = canonical_json_hash(
            {"case_id": case_id, "captured_at_utc": captured, "proof": verdict}
        )
        conn.execute(
            """INSERT OR IGNORE INTO adjudication_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                adjudication_id,
                case_id,
                row.get("market_id"),
                verdict.get("status"),
                verdict.get("winning_outcome"),
                verdict.get("confidence"),
                verdict.get("resolver_id"),
                verdict.get("prompt_version"),
                verdict.get("observed_at_utc") or captured,
                source,
                offset,
                _json(verdict),
            ),
        )
    request_class = str(proposal.get("request_class") or "")
    if request_class and request_class != "unsettled":
        settlement_id = canonical_json_hash(
            {"case_id": case_id, "request_class": request_class, "captured_at_utc": captured}
        )
        conn.execute(
            """INSERT OR IGNORE INTO settlement_labels VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                settlement_id,
                case_id,
                row.get("market_id"),
                request_class,
                proposal.get("settlement_price"),
                int(bool((row.get("market_status") or {}).get("closed"))),
                captured,
                source,
                offset,
                _json({"proposal": proposal, "market_status": row.get("market_status")}),
            ),
        )


def _adjudication(conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int) -> None:
    adjudication_id = canonical_json_hash(row)
    conn.execute(
        """INSERT OR IGNORE INTO adjudication_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            adjudication_id,
            row.get("case_id"),
            row.get("market_id"),
            row.get("status"),
            row.get("winning_outcome") or row.get("determination"),
            row.get("confidence"),
            row.get("resolver_id"),
            row.get("prompt_version"),
            row.get("reviewed_at_utc") or row.get("observed_at_utc"),
            source,
            offset,
            _json(row),
        ),
    )


def _clarification_quote(
    conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int
) -> None:
    """Materialize the court's independently-owned exact candidate-time quote."""

    capture = row.get("book_capture") if isinstance(row.get("book_capture"), dict) else {}
    snapshot_id = str(capture.get("book_capture_id") or "")
    token_id = str(row.get("token_id") or capture.get("token_id") or "")
    if not snapshot_id or not token_id:
        raise ValueError("clarification quote missing book_capture_id or token_id")
    conn.execute(
        """INSERT OR IGNORE INTO market_book_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            snapshot_id,
            token_id,
            capture.get("request_batch_capture_id"),
            capture.get("exchange_book_ts_utc"),
            capture.get("request_started_at_utc"),
            capture.get("response_received_at_utc"),
            capture.get("parsed_at_utc"),
            capture.get("available_at_utc"),
            capture.get("raw_payload_hash"),
            capture.get("clock_lineage_status"),
            int(bool(capture.get("event_time_pit_scorable"))),
            source,
            offset,
            _json({"capture": capture, "quote": row}),
        ),
    )


def _candidate(conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int) -> None:
    candidate_id = str(row.get("candidate_id") or "")
    if not candidate_id:
        raise ValueError("signal candidate missing candidate_id")
    blockers = row.get("blockers") or []
    selected = bool(row.get("eligible_shadow") or row.get("eligible") or row.get("policy_selected"))
    status = "selected" if selected else "blocked" if blockers else "scored"
    conn.execute(
        """INSERT OR IGNORE INTO fact_signal_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            candidate_id,
            "rule_lawyer.dispute_repricing",
            row.get("policy_id"),
            row.get("case_id") or row.get("source_snapshot_case_id"),
            row.get("market_id"),
            row.get("opportunity_cluster_id"),
            row.get("trigger_event_id") or row.get("update_cluster_id"),
            row.get("adjudication_revision_id") or row.get("input_sha256"),
            row.get("execution_book_snapshot_id"),
            row.get("selected_token") or row.get("reverse_token"),
            row.get("selected_outcome") or row.get("reverse_outcome"),
            row.get("track"),
            status,
            int(selected),
            _first_not_none(row.get("p_reverse"), row.get("adjudication_probability")),
            row.get("market_probability"),
            _first_not_none(row.get("ask_vwap"), row.get("reverse_ask_vwap")),
            _first_not_none(
                row.get("policy_edge_per_share"), row.get("net_edge_per_share")
            ),
            row.get("quote_observed_at_utc") or row.get("snapshot_ts_utc"),
            _json(blockers),
            source,
            offset,
            _json(row),
        ),
    )


def _demand(conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO capture_demands VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row["demand_id"],
            row["consumer_id"],
            row["strategy_key"],
            row.get("condition_id"),
            row["token_id"],
            row["reason"],
            row["priority"],
            row["desired_transport"],
            row["requested_at_utc"],
            row["expires_at_utc"],
            row.get("trigger_event_id"),
            source,
            offset,
            _json(row.get("metadata") or {}),
        ),
    )


def _receipt(conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO capture_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row["receipt_id"], row["demand_id"], row["token_id"],
            row["resolution_status"], row["subscription_epoch_id"],
            row["owner_producer"], row.get("owner_build_id"), row["accepted_at_utc"],
            source, offset, _json(row),
        ),
    )


def _rest_checkpoint_receipt(
    conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO rest_checkpoint_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row["receipt_id"], row["demand_id"], row.get("case_id"), row["token_id"],
            row["checkpoint_seconds"], row.get("due_at_utc"), row["captured_at_utc"],
            row["lateness_seconds"], row["capture_status"], row["book_snapshot_id"],
            row["owner"], source, offset, _json(row),
        ),
    )


def _position(conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO shadow_positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row["position_id"],
            row.get("signal_id"),
            row.get("case_id"),
            row.get("market_id"),
            row.get("token_id"),
            row.get("outcome"),
            row.get("policy_id"),
            row.get("quantity"),
            row.get("entry_ask_vwap"),
            row.get("entry_cost"),
            row.get("opened_at_utc"),
            source,
            offset,
            _json(row),
        ),
    )


def _intent(conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO trade_intents VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            row["intent_id"],
            row["candidate_id"],
            row["mode"],
            row["requested_size"],
            row["token_id"],
            row["venue_side"],
            source,
            offset,
            _json(row),
        ),
    )


def _paper_plan(conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO paper_plans VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row["plan_id"], row["intent_id"], row["candidate_id"], row["token_id"],
            row["side"], row["hypothetical_shares"], row["limit_price"],
            row.get("book_snapshot_id"), row["created_at_utc"], row["mode"],
            source, offset, _json(row),
        ),
    )


def _paper_order(conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO paper_orders VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            row["order_id"], row["plan_id"], row["intent_id"], row["token_id"],
            row["status"], row["actual_notional"], row["accepted_at_utc"],
            source, offset, _json(row),
        ),
    )


def _paper_fill(conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO paper_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row["fill_id"], row["order_id"], row["plan_id"], row["intent_id"],
            row["candidate_id"], row["token_id"], row["hypothetical_shares"],
            row["fill_price"], row["hypothetical_fee_per_share"], row["actual_shares"],
            row["actual_cost"], row["observed_at_utc"], row.get("book_snapshot_id"),
            source, offset, _json(row),
        ),
    )


def _markout(conn: sqlite3.Connection, row: dict[str, Any], source: str, offset: int) -> None:
    markout_id = canonical_json_hash(
        {
            "position_id": row.get("position_id"),
            "checkpoint": row.get("checkpoint"),
            "observed_at_utc": row.get("observed_at_utc"),
        }
    )
    conn.execute(
        """INSERT OR IGNORE INTO shadow_markouts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            markout_id,
            row["position_id"],
            row.get("checkpoint"),
            row.get("checkpoint_target_seconds"),
            row.get("observed_at_utc"),
            row.get("exit_bid_vwap"),
            row.get("net_pnl"),
            row.get("return_on_entry_cost"),
            row.get("settlement_payout"),
            source,
            offset,
            _json(row),
        ),
    )


HANDLERS: tuple[tuple[str, Callable[[sqlite3.Connection, dict[str, Any], str, int], None]], ...] = (
    ("events.jsonl", _case_event),
    ("snapshots.jsonl", _snapshot),
    ("semantic_reviews.jsonl", _adjudication),
    ("signals.jsonl", _candidate),
    ("clarification_quote_snapshots.jsonl", _clarification_quote),
    ("clarification_signals.jsonl", _candidate),
    ("capture_demands.jsonl", _demand),
    ("capture_receipts.jsonl", _receipt),
    ("capture_checkpoint_receipts.jsonl", _rest_checkpoint_receipt),
    ("trade_intents.jsonl", _intent),
    ("paper_plans.jsonl", _paper_plan),
    ("paper_orders.jsonl", _paper_order),
    ("paper_fills.jsonl", _paper_fill),
    ("shadow_positions.jsonl", _position),
    ("shadow_markouts.jsonl", _markout),
)


def materialize(source_root: Path, db_path: Path) -> dict[str, Any]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    initialize(conn)
    summary: dict[str, Any] = {"source_root": str(source_root), "db_path": str(db_path), "sources": {}}
    try:
        for filename, handler in HANDLERS:
            path = source_root / filename
            if not path.exists():
                summary["sources"][filename] = {"status": "missing", "rows_ingested": 0}
                continue
            source = str(path.resolve())
            size = path.stat().st_size
            watermark = conn.execute(
                """SELECT byte_offset,source_size,prefix_bytes,head_sha256,rows_ingested
                   FROM ingest_watermarks WHERE source_path=?""",
                (source,),
            ).fetchone()
            offset = int(watermark[0]) if watermark else 0
            prior_rows = int(watermark[4]) if watermark else 0
            prior_prefix_bytes = int(watermark[2]) if watermark else None
            prefix_bytes = prior_prefix_bytes if watermark else min(size, 65_536)
            if watermark and prefix_bytes == 0 and size > 0:
                # An initially empty source has no protected prefix.  Establish
                # one on its first append so later replacement is detectable.
                prefix_bytes = min(size, 65_536)
            head = _prefix_hash(path, prefix_bytes)
            if size < offset:
                raise RuntimeError(f"append-only source shrank: {path}: {size} < {offset}")
            if watermark and prior_prefix_bytes and str(watermark[3]) != head:
                raise RuntimeError(f"append-only source prefix changed: {path}")
            new_rows = 0
            final_offset = offset
            with conn:
                for start, end, row in _new_rows(path, offset):
                    handler(conn, row, source, start)
                    final_offset = end
                    new_rows += 1
                conn.execute(
                    """INSERT INTO ingest_watermarks(
                      source_path,byte_offset,source_size,prefix_bytes,head_sha256,rows_ingested
                    ) VALUES (?,?,?,?,?,?) ON CONFLICT(source_path) DO UPDATE SET
                      byte_offset=excluded.byte_offset,source_size=excluded.source_size,
                      prefix_bytes=excluded.prefix_bytes,
                      head_sha256=excluded.head_sha256,rows_ingested=excluded.rows_ingested,
                      updated_at_utc=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                    (
                        source,
                        final_offset,
                        size,
                        prefix_bytes,
                        head,
                        prior_rows + new_rows,
                    ),
                )
            summary["sources"][filename] = {
                "status": "ok",
                "rows_ingested": new_rows,
                "byte_offset_before": offset,
                "byte_offset_after": final_offset,
            }
        summary["table_counts"] = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "dispute_cases",
                "case_events",
                "contract_fragments",
                "market_book_snapshots",
                "market_group_snapshots",
                "market_group_expressions",
                "adjudication_revisions",
                "fact_signal_candidates",
                "capture_demands",
                "capture_receipts",
                "rest_checkpoint_receipts",
                "trade_intents",
                "paper_plans",
                "paper_orders",
                "paper_fills",
                "shadow_positions",
                "shadow_markouts",
                "settlement_labels",
            )
        }
        return summary
    finally:
        conn.close()
