"""Transport-neutral references that keep feature and execution books separate."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping

from src.platform.market_data.identity import canonical_json_hash
from src.platform.market_data.ws_incremental_book import ReconstructedBook


LOGICAL_BOOK_SCHEMA_VERSION = "platform_logical_book_snapshot_v1"
BOOK_PAIR_SCHEMA_VERSION = "platform_feature_execution_book_pair_v1"
BOOK_PAIR_PARITY_STATUS = (
    "same_transport_native_snapshot_exchange_clock_and_availability"
)
_BOOK_ROLES = {"model_feature", "execution_quote"}
_TRANSPORTS = {"REST", "WS_RECONSTRUCTED"}
_COVERAGE = {"full_token_depth", "selected_token_depth"}
_EXECUTABLE_DEPTH = {
    "five_share_executable",
    "requested_shares_two_way_executable",
}
_WS_GAP_FREE = {
    "sequence_and_best_quote_parity_checked",
    "best_quote_parity_checked_sequence_unavailable",
}


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("book availability clock must be timezone-aware")
    return parsed


@dataclass(frozen=True)
class LogicalBookSnapshot:
    logical_snapshot_id: str
    native_snapshot_id: str
    token_id: str
    transport: str
    book_role: str
    available_at_utc: str
    exchange_clock: str | int | None
    coverage_scope: str
    depth_status: str
    sequence_status: str
    gap_detection_status: str
    raw_lineage_id: str
    source_schema_version: str
    schema_version: str = LOGICAL_BOOK_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LOGICAL_BOOK_SCHEMA_VERSION:
            raise ValueError("logical book schema mismatch")
        if self.transport not in _TRANSPORTS:
            raise ValueError(f"unsupported book transport: {self.transport}")
        if self.book_role not in _BOOK_ROLES:
            raise ValueError(f"unsupported book role: {self.book_role}")
        if not all(
            str(value).strip()
            for value in (
                self.native_snapshot_id,
                self.token_id,
                self.raw_lineage_id,
                self.source_schema_version,
                self.sequence_status,
                self.gap_detection_status,
            )
        ):
            raise ValueError("logical book identity/lineage is incomplete")
        _parse_utc(self.available_at_utc)
        if self.coverage_scope not in _COVERAGE:
            raise ValueError("partial book coverage cannot become a logical snapshot")
        if self.transport == "WS_RECONSTRUCTED" and (
            self.gap_detection_status not in _WS_GAP_FREE
        ):
            raise ValueError("coverage_gap:ws_sequence_gap")
        if self.book_role == "execution_quote" and (
            self.depth_status not in _EXECUTABLE_DEPTH
        ):
            raise ValueError("execution book has insufficient requested-share depth")
        if self.logical_snapshot_id != canonical_json_hash(self.identity_payload()):
            raise ValueError("logical book snapshot identity mismatch")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "native_snapshot_id": self.native_snapshot_id,
            "token_id": self.token_id,
            "transport": self.transport,
            "book_role": self.book_role,
            "available_at_utc": self.available_at_utc,
            "exchange_clock": self.exchange_clock,
            "coverage_scope": self.coverage_scope,
            "depth_status": self.depth_status,
            "sequence_status": self.sequence_status,
            "gap_detection_status": self.gap_detection_status,
            "raw_lineage_id": self.raw_lineage_id,
            "source_schema_version": self.source_schema_version,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureExecutionBookPair:
    pair_id: str
    token_id: str
    feature_book_snapshot_id: str
    execution_book_snapshot_id: str
    feature_available_at_utc: str
    execution_available_at_utc: str
    transport: str
    native_snapshot_id: str
    exchange_clock: str | int | None
    parity_status: str = BOOK_PAIR_PARITY_STATUS
    schema_version: str = BOOK_PAIR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.feature_book_snapshot_id or not self.execution_book_snapshot_id:
            raise ValueError("feature and execution book references are both required")
        _parse_utc(self.feature_available_at_utc)
        _parse_utc(self.execution_available_at_utc)
        if self.transport not in _TRANSPORTS:
            raise ValueError("feature/execution pair transport is invalid")
        if not self.native_snapshot_id:
            raise ValueError("feature/execution pair native snapshot is required")
        if self.parity_status != BOOK_PAIR_PARITY_STATUS:
            raise ValueError("feature/execution pair parity evidence is invalid")
        if self.pair_id != canonical_json_hash(self.identity_payload()):
            raise ValueError("feature/execution book pair identity mismatch")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "token_id": self.token_id,
            "feature_book_snapshot_id": self.feature_book_snapshot_id,
            "execution_book_snapshot_id": self.execution_book_snapshot_id,
            "feature_available_at_utc": self.feature_available_at_utc,
            "execution_available_at_utc": self.execution_available_at_utc,
            "transport": self.transport,
            "native_snapshot_id": self.native_snapshot_id,
            "exchange_clock": self.exchange_clock,
            "parity_status": self.parity_status,
        }


def logical_book_from_ws(
    book: ReconstructedBook,
    *,
    coverage_scope: str = "selected_token_depth",
) -> LogicalBookSnapshot:
    payload = {
        "schema_version": LOGICAL_BOOK_SCHEMA_VERSION,
        "native_snapshot_id": book.book_snapshot_id,
        "token_id": book.token_id,
        "transport": "WS_RECONSTRUCTED",
        "book_role": book.book_role,
        "available_at_utc": book.last_frame_received_at_utc,
        "exchange_clock": book.exchange_ts_ms,
        "coverage_scope": coverage_scope,
        "depth_status": book.depth_status,
        "sequence_status": book.sequence_status,
        "gap_detection_status": book.gap_detection_status,
        "raw_lineage_id": book.raw_lineage_id,
        "source_schema_version": book.schema_version,
    }
    return LogicalBookSnapshot(
        logical_snapshot_id=canonical_json_hash(payload), **payload
    )


def logical_book_from_rest(
    capture: Mapping[str, Any],
    *,
    book_role: str,
    depth_status: str,
    coverage_scope: str = "full_token_depth",
) -> LogicalBookSnapshot:
    if capture.get("clock_lineage_status") != "collector_exact_response_clock":
        raise ValueError("REST book lacks exact response availability clock")
    native_id = str(capture.get("book_capture_id") or "")
    token_id = str(capture.get("token_id") or "")
    raw_lineage_id = str(capture.get("raw_payload_hash") or "")
    if not native_id or not token_id or not raw_lineage_id:
        raise ValueError("REST book capture identity is incomplete")
    payload = {
        "schema_version": LOGICAL_BOOK_SCHEMA_VERSION,
        "native_snapshot_id": native_id,
        "token_id": token_id,
        "transport": "REST",
        "book_role": book_role,
        "available_at_utc": str(capture["response_received_at_utc"]),
        "exchange_clock": capture.get("exchange_book_ts_raw"),
        "coverage_scope": coverage_scope,
        "depth_status": depth_status,
        "sequence_status": "rest_immutable_snapshot",
        "gap_detection_status": "not_applicable_rest_snapshot",
        "raw_lineage_id": raw_lineage_id,
        "source_schema_version": str(capture.get("schema_version") or ""),
    }
    return LogicalBookSnapshot(
        logical_snapshot_id=canonical_json_hash(payload), **payload
    )


def bind_feature_execution_books(
    *,
    feature_book: LogicalBookSnapshot | None,
    execution_book: LogicalBookSnapshot | None,
) -> FeatureExecutionBookPair:
    """Bind explicit refs; never substitute REST for WS or one role for another."""

    if feature_book is None or execution_book is None:
        raise ValueError("feature/execution book fallback is forbidden")
    if feature_book.book_role != "model_feature":
        raise ValueError("feature_book must carry model_feature role")
    if execution_book.book_role != "execution_quote":
        raise ValueError("execution_book must carry execution_quote role")
    if feature_book.token_id != execution_book.token_id:
        raise ValueError("feature/execution book token mismatch")
    if feature_book.transport != execution_book.transport:
        raise ValueError("feature/execution book transport mismatch")
    if feature_book.native_snapshot_id != execution_book.native_snapshot_id:
        raise ValueError("feature/execution native snapshot mismatch")
    if feature_book.exchange_clock != execution_book.exchange_clock:
        raise ValueError("feature/execution exchange clock mismatch")
    if _parse_utc(feature_book.available_at_utc) != _parse_utc(
        execution_book.available_at_utc
    ):
        raise ValueError("feature/execution availability clock mismatch")
    payload = {
        "schema_version": BOOK_PAIR_SCHEMA_VERSION,
        "token_id": feature_book.token_id,
        "feature_book_snapshot_id": feature_book.logical_snapshot_id,
        "execution_book_snapshot_id": execution_book.logical_snapshot_id,
        "feature_available_at_utc": feature_book.available_at_utc,
        "execution_available_at_utc": execution_book.available_at_utc,
        "transport": feature_book.transport,
        "native_snapshot_id": feature_book.native_snapshot_id,
        "exchange_clock": feature_book.exchange_clock,
        "parity_status": BOOK_PAIR_PARITY_STATUS,
    }
    return FeatureExecutionBookPair(pair_id=canonical_json_hash(payload), **payload)
