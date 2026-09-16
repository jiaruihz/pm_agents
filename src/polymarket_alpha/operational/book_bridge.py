"""Caller-supplied owner book artifact bridge into the formal review seam.

GLM-OP-03 glue only: this module verifies that two explicit leg submissions
plus an owner receipt bind to one released :class:`BookCaptureDemand` and
its :class:`OwnerDemandBundle`, recomputes the raw payload hash, and then
reuses the released ``FrozenOwnerBookArtifact`` validation and
``normalize_paired_owner_books``.  It owns no book parser, depth calculator,
socket, production-path scanner, or owner process.  The accepted output
(legit :class:`FrozenOwnerBookArtifact` pair plus the accepted paired
normalization) feeds ``pipeline.review.accept_formal_book`` unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from pydantic import ValidationError

from src.platform.market_data.capture_contract import COLLECTOR_EXACT_RESPONSE_CLOCK
from src.platform.market_data.identity import canonical_json_hash

from ..books.adapter import (
    CAPTURE_OWNER,
    BookCaptureStatus,
    FrozenOwnerBookArtifact,
    OwnerDemandBundle,
    PairedBookNormalization,
    normalize_paired_owner_books,
)
from ..artifacts import normalize_locator
from ..contracts import BookCaptureDemand
from ..contracts.base import ensure_utc
from .demand_outbox import validate_owner_demand_bundle


BOOK_BRIDGE_VERSION = "op_book_bridge_v1"


class BookBridgeFailureCode(StrEnum):
    """Typed fail-closed reasons for one bridge attempt."""

    OWNER_MISMATCH = "OWNER_MISMATCH"
    RECEIPT_MISMATCH = "RECEIPT_MISMATCH"
    DEMAND_BUNDLE_MISMATCH = "DEMAND_BUNDLE_MISMATCH"
    LEG_MISSING = "LEG_MISSING"
    LOCATOR_INVALID = "LOCATOR_INVALID"
    RAW_BYTES_INVALID = "RAW_BYTES_INVALID"
    HASH_MISMATCH = "HASH_MISMATCH"
    UNSCORABLE_CLOCK = "UNSCORABLE_CLOCK"
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    TOKEN_MISMATCH = "TOKEN_MISMATCH"
    DEMAND_EXPIRED = "DEMAND_EXPIRED"
    BOOK_STALE = "BOOK_STALE"
    NORMALIZATION_FAILED = "NORMALIZATION_FAILED"


@dataclass(frozen=True)
class OwnerBookLegSubmission:
    """One explicit leg: locator, raw CLOB payload bytes and capture metadata."""

    locator: str
    raw_book_bytes: bytes
    capture: Mapping[str, Any]
    raw_artifact_id: str


@dataclass(frozen=True)
class BookBridgeFailure:
    code: BookBridgeFailureCode
    detail: str


@dataclass(frozen=True)
class BookBridgeResult:
    accepted: bool
    failure: BookBridgeFailure | None
    yes_artifact: FrozenOwnerBookArtifact | None = None
    no_artifact: FrozenOwnerBookArtifact | None = None
    normalization: PairedBookNormalization | None = None


def _receipt_text(receipt: Mapping[str, Any], field: str) -> str:
    value = receipt.get(field)
    text = "" if value is None else str(value).strip()
    if not text:
        raise KeyError(field)
    return text


def _build_leg(
    submission: OwnerBookLegSubmission,
    *,
    expected_token_id: str,
    expected_batch: str,
    expected_capture_id: str,
) -> FrozenOwnerBookArtifact:
    try:
        normalize_locator(submission.locator)
    except ValueError as error:
        raise _LegInvalid(BookBridgeFailureCode.LOCATOR_INVALID, str(error)) from error
    try:
        raw_book = json.loads(submission.raw_book_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _LegInvalid(BookBridgeFailureCode.RAW_BYTES_INVALID, str(error)) from error
    if not isinstance(raw_book, Mapping):
        raise _LegInvalid(
            BookBridgeFailureCode.RAW_BYTES_INVALID, "raw book bytes must decode to a JSON object"
        )
    if submission.capture.get("raw_payload_hash") != canonical_json_hash(raw_book):
        raise _LegInvalid(
            BookBridgeFailureCode.HASH_MISMATCH,
            f"recomputed raw payload hash for leg {submission.locator} does not match capture metadata",
        )
    if str(submission.capture.get("token_id") or "") != expected_token_id:
        raise _LegInvalid(
            BookBridgeFailureCode.TOKEN_MISMATCH,
            f"leg {submission.locator} capture token does not match the demand token",
        )
    if str(submission.capture.get("request_batch_capture_id") or "") != expected_batch:
        raise _LegInvalid(
            BookBridgeFailureCode.RECEIPT_MISMATCH,
            f"leg {submission.locator} request batch does not match the owner receipt",
        )
    if str(submission.capture.get("book_capture_id") or "") != expected_capture_id:
        raise _LegInvalid(
            BookBridgeFailureCode.RECEIPT_MISMATCH,
            f"leg {submission.locator} capture id does not match the owner receipt",
        )
    if submission.capture.get("clock_lineage_status") != COLLECTOR_EXACT_RESPONSE_CLOCK or (
        submission.capture.get("event_time_pit_scorable") is not True
    ):
        raise _LegInvalid(
            BookBridgeFailureCode.UNSCORABLE_CLOCK,
            f"leg {submission.locator} lacks exact response-clock PIT lineage",
        )
    try:
        return FrozenOwnerBookArtifact(
            token_id=expected_token_id,
            raw_book=dict(raw_book),
            capture=dict(submission.capture),
            raw_artifact_id=submission.raw_artifact_id,
        )
    except (ValidationError, ValueError) as error:
        raise _LegInvalid(
            BookBridgeFailureCode.ARTIFACT_INVALID,
            f"leg {submission.locator} is not a released frozen owner artifact: {error}",
        ) from error


class _LegInvalid(Exception):
    def __init__(self, code: BookBridgeFailureCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _fail(code: BookBridgeFailureCode, detail: str) -> BookBridgeResult:
    return BookBridgeResult(accepted=False, failure=BookBridgeFailure(code=code, detail=detail))


def bridge_owner_books(
    *,
    demand: BookCaptureDemand,
    owner_demands: OwnerDemandBundle,
    owner_receipt: Mapping[str, Any],
    yes_submission: OwnerBookLegSubmission | None,
    no_submission: OwnerBookLegSubmission | None,
    received_at: datetime,
) -> BookBridgeResult:
    """Bridge two caller-supplied owner legs into a paired normalization.

    Binding contract: the owner receipt must name the sole existing owner
    (``weather_market_books``) and bind the Alpha demand id, condition, both
    tokens, the shared request batch and each leg's capture id.  Only after
    every binding, hash and clock check passes is
    ``normalize_paired_owner_books`` called; its released receipt statuses map
    to typed failures here, and only an ACCEPTED (fresh, non-stale) pair is
    bridged.
    """

    received_at = ensure_utc(received_at)
    try:
        if str(owner_receipt.get("capture_owner") or "") != CAPTURE_OWNER:
            return _fail(
                BookBridgeFailureCode.OWNER_MISMATCH,
                f"capture owner must be the existing {CAPTURE_OWNER} owner",
            )
        if _receipt_text(owner_receipt, "alpha_demand_id") != demand.demand_id:
            return _fail(
                BookBridgeFailureCode.RECEIPT_MISMATCH,
                "owner receipt does not bind the Alpha demand id",
            )
        condition = _receipt_text(owner_receipt, "condition_id")
        if condition != demand.identity.condition_id:
            return _fail(
                BookBridgeFailureCode.RECEIPT_MISMATCH,
                "owner receipt condition does not match the Alpha demand",
            )
        if _receipt_text(owner_receipt, "yes_token_id") != demand.identity.yes_token_id or (
            _receipt_text(owner_receipt, "no_token_id") != demand.identity.no_token_id
        ):
            return _fail(
                BookBridgeFailureCode.RECEIPT_MISMATCH,
                "owner receipt tokens do not match the Alpha YES/NO identity",
            )
        batch = _receipt_text(owner_receipt, "request_batch_capture_id")
        yes_capture_id = _receipt_text(owner_receipt, "yes_book_capture_id")
        no_capture_id = _receipt_text(owner_receipt, "no_book_capture_id")
    except KeyError as error:
        return _fail(
            BookBridgeFailureCode.RECEIPT_MISMATCH,
            f"owner receipt is missing required field {error}",
        )
    if owner_demands.alpha_demand.demand_id != demand.demand_id:
        return _fail(
            BookBridgeFailureCode.DEMAND_BUNDLE_MISMATCH,
            "owner demand bundle does not bind this Alpha demand",
        )
    try:
        validate_owner_demand_bundle(owner_demands)
    except ValueError as error:
        return _fail(BookBridgeFailureCode.DEMAND_BUNDLE_MISMATCH, str(error))
    if yes_submission is None or no_submission is None:
        return _fail(
            BookBridgeFailureCode.LEG_MISSING, "both YES and NO leg submissions are required"
        )
    if received_at >= demand.valid_until:
        return _fail(
            BookBridgeFailureCode.DEMAND_EXPIRED,
            "owner artifacts were received after the demand expired",
        )
    try:
        yes_artifact = _build_leg(
            yes_submission,
            expected_token_id=demand.identity.yes_token_id,
            expected_batch=batch,
            expected_capture_id=yes_capture_id,
        )
        no_artifact = _build_leg(
            no_submission,
            expected_token_id=demand.identity.no_token_id,
            expected_batch=batch,
            expected_capture_id=no_capture_id,
        )
    except _LegInvalid as invalid:
        return _fail(invalid.code, invalid.detail)
    try:
        normalization = normalize_paired_owner_books(
            demand=demand,
            yes_artifact=yes_artifact,
            no_artifact=no_artifact,
            received_at=received_at,
        )
    except ValueError as error:
        # The released owner raises plain ValueError for clock-order and
        # malformed book-content violations; the bridge must stay typed.
        return _fail(BookBridgeFailureCode.NORMALIZATION_FAILED, str(error))
    receipt = normalization.receipt
    if normalization.snapshot is None or receipt.status == BookCaptureStatus.STALE:
        code_by_status = {
            BookCaptureStatus.EXPIRED: BookBridgeFailureCode.DEMAND_EXPIRED,
            BookCaptureStatus.SKIPPED: BookBridgeFailureCode.LEG_MISSING,
            BookCaptureStatus.STALE: BookBridgeFailureCode.BOOK_STALE,
            BookCaptureStatus.FAILED: BookBridgeFailureCode.NORMALIZATION_FAILED,
        }
        code = code_by_status.get(receipt.status, BookBridgeFailureCode.NORMALIZATION_FAILED)
        detail = receipt.error_code or str(receipt.status)
        return _fail(code, f"paired owner normalization failed: {detail}")
    return BookBridgeResult(
        accepted=True,
        failure=None,
        yes_artifact=yes_artifact,
        no_artifact=no_artifact,
        normalization=normalization,
    )
