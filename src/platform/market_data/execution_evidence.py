"""Bounded materialization of WS tape and reconstructed public-book evidence.

Raw WebSocket frames remain transport truth.  This recorder derives two
append-only products from the same single-owner stream:

* exchange-reported public trade prints;
* compact reconstructed public-book states at baselines, tape events,
  requested strategy checkpoints, and a bounded periodic cadence.

Neither product is an own fill, queue position, or execution claim.  A later
join to a decision and private order lifecycle is required before a book state
may be called execution evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Iterable, Mapping, Sequence

from src.platform.market_data.identity import canonical_json_hash
from src.platform.market_data.ws_incremental_book import (
    BookReconstructionError,
    IncrementalBookReconstructor,
    MarketTradePrint,
    ReconstructedBook,
    extract_market_trade_prints,
)
from weather_clock_contract import parse_utc, utc_text


EXECUTION_BOOK_EVIDENCE_SCHEMA_VERSION = "weather_public_book_evidence_v1"
EXECUTION_EVIDENCE_HEALTH_SCHEMA_VERSION = "weather_execution_evidence_health_v1"


class _HourlyJsonlWriter:
    def __init__(self, root: Path, stem: str) -> None:
        self.root = root
        self.stem = stem
        self.stream_id = f"{time.time_ns()}_{os.getpid()}"
        self.hour = ""
        self.path: Path | None = None
        self.fd: int | None = None

    def write(self, payload: Mapping[str, Any], now_utc: datetime) -> tuple[Path, int]:
        hour = now_utc.strftime("%Y%m%d_%H")
        if hour != self.hour:
            self.close()
            day_root = self.root / now_utc.strftime("%Y-%m-%d")
            day_root.mkdir(parents=True, exist_ok=True)
            self.path = day_root / f"{self.stem}_{hour}_{self.stream_id}.jsonl"
            self.fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
            self.hour = hour
        encoded = (
            json.dumps(
                dict(payload),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        pending = memoryview(encoded)
        assert self.fd is not None and self.path is not None
        while pending:
            written = os.write(self.fd, pending)
            pending = pending[written:]
        return self.path, len(encoded)

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
        self.fd = None


@dataclass(frozen=True)
class _PendingCheckpoint:
    checkpoint_id: str
    demand_id: str
    token_id: str
    strategy_key: str | None
    trigger_event_id: str | None
    due_at_utc: datetime
    expires_at_utc: datetime
    offset_seconds: int


def _message_event_types(message: Any) -> tuple[set[str], set[str]]:
    messages = message if isinstance(message, list) else [message]
    event_types: set[str] = set()
    baseline_tokens: set[str] = set()
    for row in messages:
        if not isinstance(row, Mapping):
            continue
        event_type = str(row.get("event_type") or row.get("type") or "")
        if event_type:
            event_types.add(event_type)
        if event_type == "book" and row.get("asset_id"):
            baseline_tokens.add(str(row["asset_id"]))
    return event_types, baseline_tokens


def _public_trade_identity_id(trade_print: MarketTradePrint) -> str:
    """Return a subscription-epoch-independent public match identity.

    The exchange payload has no sequence number for ``last_trade_price``.
    Transaction hash is retained when present; otherwise the complete
    exchange-reported event tuple is the strongest available identity.
    """

    return canonical_json_hash(
        {
            "schema_version": "weather_public_trade_identity_v1",
            "token_id": trade_print.token_id,
            "market": trade_print.market,
            "price": trade_print.price,
            "size": trade_print.size,
            "side": trade_print.side,
            "exchange_ts_ms": trade_print.exchange_ts_ms,
            "transaction_hash": trade_print.transaction_hash,
            "fee_rate_bps": trade_print.fee_rate_bps,
        }
    )


class ExecutionEvidenceRecorder:
    """Incrementally derive bounded evidence from already-persisted raw frames."""

    def __init__(
        self,
        root: Path,
        *,
        min_periodic_interval_sec: float = 10.0,
        checkpoint_grace_sec: float = 20.0,
        daily_budget_bytes: int = 1_000_000_000,
        requested_shares: Sequence[float] = (1.0, 5.0, 10.0),
        enabled: bool = True,
    ) -> None:
        if min_periodic_interval_sec <= 0:
            raise ValueError("min_periodic_interval_sec must be positive")
        if checkpoint_grace_sec < 0:
            raise ValueError("checkpoint_grace_sec must be non-negative")
        if daily_budget_bytes <= 0:
            raise ValueError("daily_budget_bytes must be positive")
        if not requested_shares or any(
            float(value) <= 0 for value in requested_shares
        ):
            raise ValueError("requested_shares must be positive")
        self.root = root
        self.enabled = bool(enabled)
        self.min_periodic_interval_sec = float(min_periodic_interval_sec)
        self.checkpoint_grace_sec = float(checkpoint_grace_sec)
        self.daily_budget_bytes = int(daily_budget_bytes)
        self.requested_shares = tuple(
            sorted({float(value) for value in requested_shares})
        )
        self.engine = IncrementalBookReconstructor(strict_best_parity=True)
        self.book_writer = _HourlyJsonlWriter(root / "public_books", "public_books")
        self.trade_writer = _HourlyJsonlWriter(
            root / "market_trade_prints", "market_trade_prints"
        )
        self.token_rows: dict[str, dict[str, Any]] = {}
        self.pending: dict[str, _PendingCheckpoint] = {}
        self.completed_checkpoints: set[str] = set()
        self.seen_evidence_ids: set[str] = set()
        self.seen_trade_prints: set[str] = set()
        self.last_periodic_at: dict[str, datetime] = {}
        self.last_materialized_state_id: dict[str, str] = {}
        self._last_materialized_at: dict[str, datetime] = {}
        self._last_evidence_at: datetime | None = None
        self._last_trade_print_at: datetime | None = None
        self.counter_date_utc = datetime.now(timezone.utc).date().isoformat()
        self.session_bytes = 0
        self.input_frames = 0
        self.applied_frames = 0
        self.reconstruction_errors = 0
        self.book_evidence_rows = 0
        self.trade_print_rows = 0
        self.checkpoint_rows = 0
        self.missed_checkpoints = 0
        self.skipped_budget_rows = 0
        self.last_evidence_at_utc: str | None = None
        self.last_trade_print_at_utc: str | None = None
        self.last_error: str | None = None
        self.dedupe_restore_errors = 0
        self.integration_errors = 0
        self.last_integration_error: str | None = None
        self._restore_recent_state(self.counter_date_utc)
        self.day_bytes = self._existing_day_bytes(self.counter_date_utc)
        self.budget_blocked = self.day_bytes >= self.daily_budget_bytes

    def _existing_day_bytes(self, date_text: str) -> int:
        total = 0
        for product in ("public_books", "market_trade_prints"):
            day_root = self.root / product / date_text
            try:
                total += sum(path.stat().st_size for path in day_root.glob("*.jsonl"))
            except OSError:
                continue
        return total

    def _iter_product_rows(
        self, product: str, date_text: str
    ) -> Iterable[Mapping[str, Any]]:
        day_root = self.root / product / date_text
        for path in sorted(day_root.glob("*.jsonl")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            # A torn final append is not a committed evidence
                            # row.  Count it and allow replay to materialize a
                            # complete deterministic row.
                            self.dedupe_restore_errors += 1
                            continue
                        if isinstance(row, Mapping):
                            yield row
            except OSError:
                self.dedupe_restore_errors += 1

    def _parsed_restore_clock(self, value: Any, *, field: str) -> datetime | None:
        try:
            return parse_utc(value, field=field, allow_none=True)
        except (TypeError, ValueError):
            self.dedupe_restore_errors += 1
            return None

    def _restore_recent_state(self, date_text: str) -> None:
        """Restore a crash-safe two-day dedupe window from append-only rows.

        The product itself is the source of truth: scanning committed JSONL
        also covers a crash after the row append but before any auxiliary
        cursor could have been persisted.
        """

        current = datetime.fromisoformat(date_text).date()
        dates = ((current - timedelta(days=1)).isoformat(), current.isoformat())
        for restore_date in dates:
            for row in self._iter_product_rows("public_books", restore_date):
                evidence_id = str(row.get("evidence_id") or "")
                if evidence_id:
                    self.seen_evidence_ids.add(evidence_id)
                checkpoint_ref = row.get("checkpoint_ref")
                if isinstance(checkpoint_ref, Mapping):
                    checkpoint_id = str(checkpoint_ref.get("checkpoint_id") or "")
                    if checkpoint_id:
                        self.completed_checkpoints.add(checkpoint_id)
                token_id = str(row.get("token_id") or "")
                state_id = str(row.get("public_book_state_id") or "")
                observed = self._parsed_restore_clock(
                    row.get("book_observed_at_utc"),
                    field="book_observed_at_utc",
                )
                if observed is not None:
                    if (
                        self._last_evidence_at is None
                        or observed >= self._last_evidence_at
                    ):
                        self._last_evidence_at = observed
                        self.last_evidence_at_utc = utc_text(observed)
                    previous = self._last_materialized_at.get(token_id)
                    if token_id and state_id and (
                        previous is None or observed >= previous
                    ):
                        self._last_materialized_at[token_id] = observed
                        self.last_materialized_state_id[token_id] = state_id
                    if (
                        row.get("evidence_reason") == "periodic_changed_state"
                        and token_id
                    ):
                        previous_periodic = self.last_periodic_at.get(token_id)
                        if previous_periodic is None or observed >= previous_periodic:
                            self.last_periodic_at[token_id] = observed
            for row in self._iter_product_rows("market_trade_prints", restore_date):
                trade_print_id = str(row.get("trade_print_id") or "")
                if trade_print_id:
                    self.seen_trade_prints.add(trade_print_id)
                received = self._parsed_restore_clock(
                    row.get("received_at_utc"),
                    field="received_at_utc",
                )
                if received is not None and (
                    self._last_trade_print_at is None
                    or received >= self._last_trade_print_at
                ):
                    self._last_trade_print_at = received
                    self.last_trade_print_at_utc = utc_text(received)

    def _clear_and_restore_recent_state(self, date_text: str) -> None:
        self.completed_checkpoints.clear()
        self.seen_evidence_ids.clear()
        self.seen_trade_prints.clear()
        self.last_periodic_at.clear()
        self.last_materialized_state_id.clear()
        self._last_materialized_at.clear()
        self._last_evidence_at = None
        self._last_trade_print_at = None
        self.last_evidence_at_utc = None
        self.last_trade_print_at_utc = None
        self._restore_recent_state(date_text)
        for checkpoint_id in self.completed_checkpoints:
            self.pending.pop(checkpoint_id, None)

    def _reset_day(self, now_utc: datetime) -> None:
        date_text = now_utc.date().isoformat()
        if date_text != self.counter_date_utc:
            self.counter_date_utc = date_text
            self._clear_and_restore_recent_state(date_text)
            self.day_bytes = self._existing_day_bytes(date_text)
            self.budget_blocked = self.day_bytes >= self.daily_budget_bytes

    @property
    def budget_exhausted(self) -> bool:
        return self.budget_blocked or self.day_bytes >= self.daily_budget_bytes

    def _write(
        self,
        writer: _HourlyJsonlWriter,
        payload: Mapping[str, Any],
        now_utc: datetime,
    ) -> bool:
        self._reset_day(now_utc)
        encoded_size = len(
            (
                json.dumps(
                    dict(payload),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        )
        if self.budget_exhausted or self.day_bytes + encoded_size > self.daily_budget_bytes:
            self.budget_blocked = True
            self.skipped_budget_rows += 1
            return False
        try:
            _path, size = writer.write(payload, now_utc)
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        self.day_bytes += size
        self.session_bytes += size
        return True

    def activate_epoch(self, epoch: Mapping[str, Any]) -> None:
        """Activate the exact subscription declaration written by the owner."""

        if not self.enabled:
            return
        reason = str(epoch.get("reason") or "")
        carry = reason == "selector_reconcile" and self.engine.epoch_id is not None
        self.engine.activate_epoch(
            str(epoch["subscription_epoch_id"]),
            epoch.get("token_ids") or (),
            carry_forward=carry,
            producer_build_id=(
                str(epoch.get("producer_build_id"))
                if epoch.get("producer_build_id")
                else None
            ),
            selector_version=(
                str(epoch.get("selector_version"))
                if epoch.get("selector_version")
                else None
            ),
            capture_policy=epoch.get("capture_policy") or {},
            token_rows=epoch.get("token_rows") or {},
        )
        self.token_rows = {
            str(token): dict(row)
            for token, row in (epoch.get("token_rows") or {}).items()
        }

    def register_capture_demands(self, demands: Iterable[Mapping[str, Any]]) -> None:
        """Register immutable requested checkpoints from resolved demands."""

        if not self.enabled:
            return
        for raw in demands:
            demand = dict(raw)
            if not str(demand.get("resolution_status") or "").startswith("resolved_"):
                continue
            demand_id = str(
                demand.get("demand_id") or demand.get("capture_request_id") or ""
            )
            requested = parse_utc(
                demand.get("requested_at_utc"),
                field="requested_at_utc",
                allow_none=True,
            )
            expires = parse_utc(
                demand.get("expires_at_utc"),
                field="expires_at_utc",
                allow_none=True,
            )
            if not demand_id or requested is None or expires is None:
                continue
            token_ids = {
                str(value)
                for value in demand.get("resolved_token_ids") or ()
                if value
            }
            if demand.get("token_id"):
                token_ids.add(str(demand["token_id"]))
            offsets = demand.get("requested_checkpoints_seconds") or (0,)
            for token_id in sorted(token_ids):
                for raw_offset in offsets:
                    try:
                        offset = int(raw_offset)
                    except (TypeError, ValueError):
                        continue
                    if offset < 0:
                        continue
                    due = requested + timedelta(seconds=offset)
                    checkpoint_id = canonical_json_hash(
                        {
                            "demand_id": demand_id,
                            "token_id": token_id,
                            "offset_seconds": offset,
                            "due_at_utc": utc_text(due),
                        }
                    )
                    if checkpoint_id in self.completed_checkpoints:
                        continue
                    self.pending.setdefault(
                        checkpoint_id,
                        _PendingCheckpoint(
                            checkpoint_id=checkpoint_id,
                            demand_id=demand_id,
                            token_id=token_id,
                            strategy_key=(
                                str(demand.get("strategy_key"))
                                if demand.get("strategy_key")
                                else None
                            ),
                            trigger_event_id=(
                                str(demand.get("trigger_event_id"))
                                if demand.get("trigger_event_id")
                                else None
                            ),
                            due_at_utc=due,
                            expires_at_utc=max(
                                expires,
                                due + timedelta(seconds=self.checkpoint_grace_sec),
                            ),
                            offset_seconds=offset,
                        ),
                    )

    def _sweep_summary(
        self, token_id: str, observed_at_utc: str
    ) -> tuple[ReconstructedBook, list[dict[str, Any]]]:
        snapshots = [
            self.engine.snapshot(
                token_id,
                observed_at_utc=observed_at_utc,
                requested_shares=shares,
                book_role="market_state_evidence",
            )
            for shares in self.requested_shares
        ]
        primary = min(snapshots, key=lambda row: abs(row.requested_shares - 5.0))
        sweeps = [
            {
                "shares": row.requested_shares,
                "buy_cost": row.buy_cost,
                "sell_proceeds": row.sell_proceeds,
                "depth_status": row.depth_status,
            }
            for row in snapshots
        ]
        return primary, sweeps

    def _book_payload(
        self,
        token_id: str,
        *,
        now_utc: datetime,
        reason: str,
        checkpoint: _PendingCheckpoint | None = None,
        trade_print_ids: Sequence[str] = (),
    ) -> tuple[dict[str, Any], str]:
        observed = utc_text(now_utc)
        book, sweeps = self._sweep_summary(token_id, observed)
        checkpoint_ref = (
            {
                "checkpoint_id": checkpoint.checkpoint_id,
                "demand_id": checkpoint.demand_id,
                "strategy_key": checkpoint.strategy_key,
                "trigger_event_id": checkpoint.trigger_event_id,
                "offset_seconds": checkpoint.offset_seconds,
                "requested_checkpoint_at_utc": utc_text(checkpoint.due_at_utc),
                "materialization_lag_ms": round(
                    (now_utc - checkpoint.due_at_utc).total_seconds() * 1000.0, 3
                ),
            }
            if checkpoint is not None
            else None
        )
        public_book_state_id = canonical_json_hash(
            {
                "token_id": token_id,
                "bids": book.bids,
                "asks": book.asks,
            }
        )
        evidence_id = canonical_json_hash(
            {
                "schema_version": EXECUTION_BOOK_EVIDENCE_SCHEMA_VERSION,
                "reason": reason,
                "token_id": token_id,
                "public_book_state_id": public_book_state_id,
                "exchange_ts_ms": book.exchange_ts_ms,
                "exchange_book_hash": book.exchange_book_hash,
                "checkpoint_id": checkpoint.checkpoint_id if checkpoint else None,
                "trade_print_ids": list(trade_print_ids),
            }
        )
        token_metadata = dict(self.token_rows.get(token_id) or {})
        payload = {
            "schema_version": EXECUTION_BOOK_EVIDENCE_SCHEMA_VERSION,
            "evidence_id": evidence_id,
            "evidence_reason": reason,
            "evidence_class": "public_orderbook_state_not_fill_or_queue",
            "execution_claim_status": "unjoined_requires_decision_and_private_order_lifecycle",
            "subscription_epoch_id": book.subscription_epoch_id,
            "token_id": token_id,
            "condition_id": token_metadata.get("condition_id"),
            "market_id": token_metadata.get("market_id"),
            "city": token_metadata.get("city"),
            "target_date": token_metadata.get("event_date")
            or token_metadata.get("target_date"),
            "bracket": token_metadata.get("bracket"),
            "outcome": token_metadata.get("outcome"),
            "strategy_key": token_metadata.get("strategy_key"),
            "book_observed_at_utc": observed,
            "exchange_ts_ms": book.exchange_ts_ms,
            "exchange_book_hash": book.exchange_book_hash,
            "baseline_received_at_utc": book.baseline_received_at_utc,
            "last_frame_received_at_utc": book.last_frame_received_at_utc,
            "book_snapshot_id": book.book_snapshot_id,
            # ``book_snapshot_id`` intentionally includes raw lineage.  The
            # state id does not, so periodic_changed_state does not emit a new
            # row merely because an unchanged parity/receipt frame advanced
            # lineage.
            "public_book_state_id": public_book_state_id,
            "feature_book_snapshot_id": None,
            "execution_book_snapshot_id": None,
            "best_bid": book.best_bid,
            "best_ask": book.best_ask,
            "best_bid_size": book.best_bid_size,
            "best_ask_size": book.best_ask_size,
            "top_bids": [list(value) for value in book.bids[:5]],
            "top_asks": [list(value) for value in book.asks[:5]],
            "sweeps": sweeps,
            "sequence_status": book.sequence_status,
            "gap_detection_status": book.gap_detection_status,
            "last_exchange_sequence": book.last_exchange_sequence,
            "raw_lineage_id": book.raw_lineage_id,
            "baseline_raw_frame_ref": book.baseline_raw_frame_ref.__dict__,
            "delta_first_raw_frame_ref": (
                book.delta_first_raw_frame_ref.__dict__
                if book.delta_first_raw_frame_ref
                else None
            ),
            "delta_last_raw_frame_ref": (
                book.delta_last_raw_frame_ref.__dict__
                if book.delta_last_raw_frame_ref
                else None
            ),
            "delta_frame_count": book.delta_frame_count,
            "delta_chain_hash": book.delta_chain_hash,
            "capture_policy_id": book.capture_policy_id,
            "token_map_id": book.token_map_id,
            "subscription_set_id": book.subscription_set_id,
            "producer_build_id": book.producer_build_id,
            "selector_version": book.selector_version,
            "checkpoint_ref": checkpoint_ref,
            "market_trade_print_ids": list(trade_print_ids),
        }
        return payload, public_book_state_id

    def _record_book(
        self,
        token_id: str,
        *,
        now_utc: datetime,
        reason: str,
        checkpoint: _PendingCheckpoint | None = None,
        trade_print_ids: Sequence[str] = (),
    ) -> bool:
        self._reset_day(now_utc)
        try:
            payload, state_id = self._book_payload(
                token_id,
                now_utc=now_utc,
                reason=reason,
                checkpoint=checkpoint,
                trade_print_ids=trade_print_ids,
            )
        except BookReconstructionError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False
        evidence_id = str(payload["evidence_id"])
        if evidence_id in self.seen_evidence_ids:
            if checkpoint is not None:
                self.completed_checkpoints.add(checkpoint.checkpoint_id)
                self.pending.pop(checkpoint.checkpoint_id, None)
            return False
        if reason == "periodic_changed_state":
            if self.last_materialized_state_id.get(token_id) == state_id:
                return False
        if not self._write(self.book_writer, payload, now_utc):
            return False
        self.seen_evidence_ids.add(evidence_id)
        self.book_evidence_rows += 1
        self.last_evidence_at_utc = payload["book_observed_at_utc"]
        self.last_materialized_state_id[token_id] = state_id
        self._last_materialized_at[token_id] = now_utc
        if reason == "periodic_changed_state":
            self.last_periodic_at[token_id] = now_utc
        if checkpoint is not None:
            self.checkpoint_rows += 1
            self.completed_checkpoints.add(checkpoint.checkpoint_id)
            self.pending.pop(checkpoint.checkpoint_id, None)
        return True

    def note_integration_error(self, exc: Exception) -> None:
        """Expose a derived-layer fault without crashing the raw WS owner."""

        message = f"{type(exc).__name__}: {exc}"
        self.integration_errors += 1
        self.last_integration_error = message
        self.last_error = message

    def _record_trade_print(
        self, trade_print: MarketTradePrint, *, now_utc: datetime
    ) -> bool:
        self._reset_day(now_utc)
        public_trade_id = _public_trade_identity_id(trade_print)
        if public_trade_id in self.seen_trade_prints:
            return False
        payload = trade_print.to_dict()
        payload["transport_trade_print_id"] = trade_print.trade_print_id
        payload["trade_print_id"] = public_trade_id
        payload["evidence_class"] = "public_exchange_trade_not_own_fill"
        payload["own_fill_claim_status"] = "not_joined"
        if not self._write(self.trade_writer, payload, now_utc):
            return False
        self.seen_trade_prints.add(public_trade_id)
        self.trade_print_rows += 1
        self.last_trade_print_at_utc = trade_print.received_at_utc
        return True

    def ingest(self, envelope: Mapping[str, Any], *, now_utc: datetime) -> None:
        """Consume one raw frame only after the owner persisted it."""

        if not self.enabled:
            return
        self._reset_day(now_utc)
        self.input_frames += 1
        try:
            trade_prints = extract_market_trade_prints(envelope)
        except BookReconstructionError as exc:
            trade_prints = ()
            self.reconstruction_errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
        for row in trade_prints:
            self._record_trade_print(row, now_utc=now_utc)
        try:
            updated_tokens = set(self.engine.apply_envelope(envelope))
        except BookReconstructionError as exc:
            self.reconstruction_errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.poll(now_utc)
            return
        self.applied_frames += 1
        _event_types, baseline_tokens = _message_event_types(envelope.get("message"))
        print_ids_by_token: dict[str, list[str]] = {}
        for row in trade_prints:
            print_ids_by_token.setdefault(row.token_id, []).append(
                _public_trade_identity_id(row)
            )
        for token_id in sorted(baseline_tokens):
            self._record_book(
                token_id, now_utc=now_utc, reason="fresh_book_baseline"
            )
        for token_id, print_ids in sorted(print_ids_by_token.items()):
            self._record_book(
                token_id,
                now_utc=now_utc,
                reason="public_trade_context",
                trade_print_ids=print_ids,
            )
        for token_id in sorted(updated_tokens - baseline_tokens - set(print_ids_by_token)):
            previous = self.last_periodic_at.get(token_id)
            if previous is None or (
                now_utc - previous
            ).total_seconds() >= self.min_periodic_interval_sec:
                self._record_book(
                    token_id,
                    now_utc=now_utc,
                    reason="periodic_changed_state",
                )
        self.poll(now_utc)

    def poll(self, now_utc: datetime) -> None:
        """Materialize due strategy checkpoints from the latest verified state."""

        if not self.enabled:
            return
        self._reset_day(now_utc)
        for checkpoint_id, checkpoint in list(self.pending.items()):
            if now_utc < checkpoint.due_at_utc:
                continue
            if now_utc > checkpoint.expires_at_utc:
                self.missed_checkpoints += 1
                self.completed_checkpoints.add(checkpoint_id)
                self.pending.pop(checkpoint_id, None)
                continue
            self._record_book(
                checkpoint.token_id,
                now_utc=now_utc,
                reason="requested_strategy_checkpoint",
                checkpoint=checkpoint,
            )

    def health(self, now_utc: datetime) -> dict[str, Any]:
        self.poll(now_utc)
        if not self.enabled:
            status = "disabled"
        elif self.last_error and self.book_evidence_rows == 0 and self.input_frames > 0:
            status = "degraded"
        elif self.budget_exhausted:
            # The evidence layer is intentionally bounded. Exhausting its own
            # derived-artifact budget must remain visible without declaring the
            # raw WS owner unhealthy (which would trigger a restart loop and
            # lose the very stream needed for later replay).
            status = "budget_exhausted"
        elif self.engine.epoch_id is None:
            status = "warming"
        else:
            status = "ok"
        return {
            "schema_version": EXECUTION_EVIDENCE_HEALTH_SCHEMA_VERSION,
            "status": status,
            "enabled": self.enabled,
            "generated_at_utc": utc_text(now_utc),
            "subscription_epoch_id": self.engine.epoch_id,
            "input_frames": self.input_frames,
            "applied_frames": self.applied_frames,
            "reconstruction_errors": self.reconstruction_errors,
            "blocked_tokens": dict(sorted(self.engine.blocked_tokens.items())),
            "reconstructable_tokens": len(self.engine.books),
            "book_evidence_rows": self.book_evidence_rows,
            "market_trade_print_rows": self.trade_print_rows,
            "requested_checkpoint_rows": self.checkpoint_rows,
            "pending_requested_checkpoints": len(self.pending),
            "missed_requested_checkpoints": self.missed_checkpoints,
            "last_evidence_at_utc": self.last_evidence_at_utc,
            "last_trade_print_at_utc": self.last_trade_print_at_utc,
            "counter_date_utc": self.counter_date_utc,
            "day_bytes": self.day_bytes,
            "session_bytes": self.session_bytes,
            "daily_budget_bytes": self.daily_budget_bytes,
            "budget_exhausted": self.budget_exhausted,
            "skipped_budget_rows": self.skipped_budget_rows,
            "last_error": self.last_error,
            "integration_errors": self.integration_errors,
            "last_integration_error": self.last_integration_error,
            "dedupe_scope": "current_and_previous_utc_date_from_committed_jsonl",
            "dedupe_evidence_ids": len(self.seen_evidence_ids),
            "dedupe_trade_print_ids": len(self.seen_trade_prints),
            "dedupe_completed_checkpoint_ids": len(self.completed_checkpoints),
            "dedupe_restore_errors": self.dedupe_restore_errors,
            "semantics": {
                "public_trade_print": "exchange match not own fill",
                "public_book": "quote state not queue or execution",
                "execution_evidence": "requires decision plus private order lifecycle join",
            },
        }

    def close(self) -> None:
        self.book_writer.close()
        self.trade_writer.close()
