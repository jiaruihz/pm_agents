"""P0-06C pure controversy/dispute recall provider.

The provider is a pure function of caller-supplied inputs: frozen dispute
source payloads, the expected source identities, an as-of cutoff, the frozen
corpus revision and a market mapping.  It never opens a database, touches the
network, reads the clock, or inspects the filesystem; byte integrity is
verified only over the artifact text the caller passes in.

Every accepted hit binds absolute source identity (path plus device/inode, or
an explicit frozen fixture identity), the schema/corpus revision hash, the
source artifact id, the PIT effective time and exact case/source offsets.
Hits may only state that a rule/dispute is contested, disputed or
clarification-dependent; they never estimate probability, price or fair value.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator, model_validator

from ..contracts import (
    AlphaContract,
    ProvenanceRef,
    RecallHit,
    RecallerType,
    bytes_sha256,
    canonical_datetime,
    content_sha256,
    stable_record_id,
)
from ..contracts.base import ensure_utc, validate_sha256
from .registry import ProviderDescriptor


CONTROVERSY_PROVIDER_ID = "controversy"
CONTROVERSY_RECALLER_VERSION = "controversy-v1"
# Constant attention weight for aggregation scoring only; it carries no
# probability, price or fair-value semantics.
CONTROVERSY_RAW_SCORE = Decimal("1")


class ControversyFinding(StrEnum):
    """Closed vocabulary of controversy claims a hit may state."""

    RULE_CONTESTED = "RULE_CONTESTED"
    RESOLUTION_DISPUTED = "RESOLUTION_DISPUTED"
    CLARIFICATION_PENDING = "CLARIFICATION_PENDING"
    AMBIGUITY_RAISED = "AMBIGUITY_RAISED"


CONTROVERSY_REASON_CODES = frozenset(
    f"CONTROVERSY_{finding.value}" for finding in ControversyFinding
)


class ControversySourceIdentity(AlphaContract):
    """Absolute identity of a dispute corpus source.

    Exactly one grounding is allowed: a real file identity (device and inode
    beside the absolute path) or an explicit frozen fixture identity.  Mixing
    or omitting both is a structural error.
    """

    source_path: str
    device: int | None = Field(default=None, ge=0)
    inode: int | None = Field(default=None, gt=0)
    fixture_id: str | None = None
    schema_sha256: str

    @field_validator("source_path")
    @classmethod
    def source_path_is_absolute(cls, value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError("controversy source_path must be absolute")
        return str(path)

    @field_validator("fixture_id")
    @classmethod
    def fixture_id_is_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("fixture_id must be null or non-blank")
        return value

    @field_validator("schema_sha256")
    @classmethod
    def schema_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def identity_has_exactly_one_grounding(self) -> "ControversySourceIdentity":
        if (self.device is None) != (self.inode is None):
            raise ValueError("device and inode must be provided together")
        if self.device is None:
            if self.fixture_id is None:
                raise ValueError("source identity requires device/inode or an explicit fixture_id")
        elif self.fixture_id is not None:
            raise ValueError("source identity cannot claim file and fixture grounding at once")
        return self


class ControversyMarketMapping(AlphaContract):
    market_ref: str
    market_id: str

    @field_validator("market_ref", "market_id")
    @classmethod
    def mapping_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("market mapping values must not be blank")
        return value


class ControversyDisputeCase(AlphaContract):
    """One frozen dispute case with exact offsets into its source artifact."""

    case_id: str
    market_ref: str
    finding: ControversyFinding
    effective_at: datetime
    quote_start: int = Field(ge=0)
    quote_end: int = Field(gt=0)

    @field_validator("case_id", "market_ref")
    @classmethod
    def case_identity_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("dispute case identifiers must not be blank")
        return value

    @field_validator("effective_at")
    @classmethod
    def effective_at_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @model_validator(mode="after")
    def quote_span_is_ordered(self) -> "ControversyDisputeCase":
        if self.quote_end <= self.quote_start:
            raise ValueError("quote offsets must select a non-empty span")
        return self


class ControversySourceExpectation(AlphaContract):
    """The source identity the caller froze and expects to recall from."""

    identity: ControversySourceIdentity
    source_artifact_id: str
    artifact_sha256: str

    @field_validator("source_artifact_id")
    @classmethod
    def artifact_id_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source artifact id must not be blank")
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def artifact_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)


class ControversySourcePayload(AlphaContract):
    """Frozen source payload supplied by the caller; never fetched here."""

    identity: ControversySourceIdentity
    source_artifact_id: str
    artifact_sha256: str
    artifact_text: str
    cases: tuple[ControversyDisputeCase, ...] = ()

    @field_validator("source_artifact_id")
    @classmethod
    def artifact_id_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source artifact id must not be blank")
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def artifact_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)


class ControversyRecallRequest(AlphaContract):
    run_id: str
    provider_id: str = CONTROVERSY_PROVIDER_ID
    as_of: datetime
    corpus_revision_sha256: str
    expected_sources: tuple[ControversySourceExpectation, ...] = ()
    sources: tuple[ControversySourcePayload, ...] = ()
    market_mapping: tuple[ControversyMarketMapping, ...] = ()

    @field_validator("run_id", "provider_id")
    @classmethod
    def identifiers_are_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("recall identifiers must not be blank")
        return value

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("corpus_revision_sha256")
    @classmethod
    def corpus_revision_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def request_structure_is_unique(self) -> "ControversyRecallRequest":
        expected_paths = [item.identity.source_path for item in self.expected_sources]
        if len(expected_paths) != len(set(expected_paths)):
            raise ValueError("expected source paths must be unique")
        payload_paths = [item.identity.source_path for item in self.sources]
        if len(payload_paths) != len(set(payload_paths)):
            raise ValueError("source payload paths must be unique")
        refs = [item.market_ref for item in self.market_mapping]
        if len(refs) != len(set(refs)):
            raise ValueError(
                "market mapping refs must be unique; conflicting or blank mappings are rejected"
            )
        return self


class ControversySkipReason(StrEnum):
    SOURCE_MISSING = "SOURCE_MISSING"
    SOURCE_UNEXPECTED = "SOURCE_UNEXPECTED"


class ControversySkip(AlphaContract):
    provider_id: str
    source_path: str
    reason: ControversySkipReason
    detail: str

    @field_validator("source_path", "detail")
    @classmethod
    def skip_fields_are_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("skip fields must not be blank")
        return value


class ControversyRejectionReason(StrEnum):
    SOURCE_IDENTITY_MISMATCH = "SOURCE_IDENTITY_MISMATCH"
    SOURCE_HASH_MISMATCH = "SOURCE_HASH_MISMATCH"
    SOURCE_INCOMPLETE = "SOURCE_INCOMPLETE"
    POST_CUTOFF_EVIDENCE = "POST_CUTOFF_EVIDENCE"
    UNMAPPED_MARKET = "UNMAPPED_MARKET"
    DUPLICATE_CASE = "DUPLICATE_CASE"
    DUPLICATE_CASE_CONFLICT = "DUPLICATE_CASE_CONFLICT"


class ControversyRejection(AlphaContract):
    provider_id: str
    source_path: str
    case_id: str
    reason: ControversyRejectionReason
    detail: str

    @field_validator("source_path", "case_id", "detail")
    @classmethod
    def rejection_fields_are_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("rejection fields must not be blank")
        return value


# Closed feature vocabulary: source/PIT binding only.  Anything outside it
# (probability, price, fair value, edge, direction, ...) is rejected.
_ALLOWED_FEATURE_KEYS = frozenset(
    {
        "case_id",
        "corpus_revision_sha256",
        "effective_at",
        "finding",
        "fixture_id",
        "quote_end",
        "quote_sha256",
        "quote_start",
        "schema_sha256",
        "source_artifact_id",
        "source_device",
        "source_inode",
        "source_path",
    }
)


class ControversyRecallOutcome(AlphaContract):
    provider_id: str
    recaller_version: str
    as_of: datetime
    request_sha256: str
    hits: tuple[RecallHit, ...] = ()
    rejections: tuple[ControversyRejection, ...] = ()
    skips: tuple[ControversySkip, ...] = ()

    @field_validator("as_of")
    @classmethod
    def as_of_is_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)

    @field_validator("request_sha256")
    @classmethod
    def request_hash_is_valid(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def hits_stay_inside_the_controversy_contract(self) -> "ControversyRecallOutcome":
        for hit in self.hits:
            if hit.recaller != RecallerType.CONTROVERSY:
                raise ValueError("controversy outcome may only contain CONTROVERSY hits")
            if hit.source != self.provider_id or hit.source_version != self.recaller_version:
                raise ValueError("controversy hits must carry the provider identity")
            if not set(hit.reason_codes) <= CONTROVERSY_REASON_CODES:
                raise ValueError("reason codes must come from the controversy vocabulary")
            unexpected = set(hit.features) - _ALLOWED_FEATURE_KEYS
            if unexpected:
                raise ValueError(f"controversy features are restricted to source binding: {sorted(unexpected)}")
        for item in self.rejections:
            if item.provider_id != self.provider_id:
                raise ValueError("rejections must carry the provider identity")
        for item in self.skips:
            if item.provider_id != self.provider_id:
                raise ValueError("skips must carry the provider identity")
        return self


def _case_sort_key(case: ControversyDisputeCase) -> tuple[str, str]:
    return (case.case_id, content_sha256(case))


def _canonical_request_view(request: ControversyRecallRequest) -> dict[str, object]:
    """Order-free request projection so request_sha256 ignores input ordering."""

    return {
        "provider_id": request.provider_id,
        "run_id": request.run_id,
        "as_of": request.as_of,
        "corpus_revision_sha256": request.corpus_revision_sha256,
        "market_mapping": [
            {"market_id": item.market_id, "market_ref": item.market_ref}
            for item in sorted(request.market_mapping, key=lambda item: item.market_ref)
        ],
        "expected_sources": [
            {
                "artifact_sha256": item.artifact_sha256,
                "identity": item.identity.model_dump(mode="python"),
                "source_artifact_id": item.source_artifact_id,
            }
            for item in sorted(
                request.expected_sources, key=lambda item: item.identity.source_path
            )
        ],
        "sources": [
            {
                "artifact_sha256": item.artifact_sha256,
                "artifact_text": item.artifact_text,
                "cases": [
                    case.model_dump(mode="python")
                    for case in sorted(item.cases, key=_case_sort_key)
                ],
                "identity": item.identity.model_dump(mode="python"),
                "source_artifact_id": item.source_artifact_id,
            }
            for item in sorted(request.sources, key=lambda item: item.identity.source_path)
        ],
    }


class ControversyRecaller:
    """Pure controversy/dispute recall provider (no DB, no network, no clock)."""

    provider_id: str = CONTROVERSY_PROVIDER_ID
    recaller_version: str = CONTROVERSY_RECALLER_VERSION

    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self.provider_id,
            recaller=RecallerType.CONTROVERSY,
            recaller_version=self.recaller_version,
        )

    def recall(self, request: ControversyRecallRequest) -> ControversyRecallOutcome:
        if request.provider_id != self.provider_id:
            raise ValueError("controversy recall request targets a different provider")

        skips: list[ControversySkip] = []
        rejections: list[ControversyRejection] = []

        expected_by_path = {
            item.identity.source_path: item
            for item in sorted(request.expected_sources, key=lambda item: item.identity.source_path)
        }
        payload_by_path = {
            item.identity.source_path: item
            for item in sorted(request.sources, key=lambda item: item.identity.source_path)
        }

        for path in sorted(set(expected_by_path) - set(payload_by_path)):
            skips.append(
                ControversySkip(
                    provider_id=self.provider_id,
                    source_path=path,
                    reason=ControversySkipReason.SOURCE_MISSING,
                    detail=(
                        "expected dispute source payload absent: "
                        f"{expected_by_path[path].source_artifact_id}"
                    ),
                )
            )
        for path in sorted(set(payload_by_path) - set(expected_by_path)):
            skips.append(
                ControversySkip(
                    provider_id=self.provider_id,
                    source_path=path,
                    reason=ControversySkipReason.SOURCE_UNEXPECTED,
                    detail="payload has no matching expected source identity",
                )
            )

        market_by_ref = {item.market_ref: item.market_id for item in request.market_mapping}

        # Survivors carry (payload, case, market_id) for duplicate adjudication.
        survivors: list[tuple[ControversySourcePayload, ControversyDisputeCase, str]] = []
        for path in sorted(set(expected_by_path) & set(payload_by_path)):
            expected = expected_by_path[path]
            payload = payload_by_path[path]
            identity_matches = (
                payload.identity == expected.identity
                and payload.source_artifact_id == expected.source_artifact_id
                and payload.artifact_sha256 == expected.artifact_sha256
            )
            if not identity_matches:
                for case in sorted(payload.cases, key=_case_sort_key):
                    rejections.append(
                        ControversyRejection(
                            provider_id=self.provider_id,
                            source_path=path,
                            case_id=case.case_id,
                            reason=ControversyRejectionReason.SOURCE_IDENTITY_MISMATCH,
                            detail="payload identity/artifact does not match the frozen expectation",
                        )
                    )
                continue
            if bytes_sha256(payload.artifact_text.encode("utf-8")) != payload.artifact_sha256:
                for case in sorted(payload.cases, key=_case_sort_key):
                    rejections.append(
                        ControversyRejection(
                            provider_id=self.provider_id,
                            source_path=path,
                            case_id=case.case_id,
                            reason=ControversyRejectionReason.SOURCE_HASH_MISMATCH,
                            detail="artifact bytes do not match the declared content hash",
                        )
                    )
                continue
            for case in sorted(payload.cases, key=_case_sort_key):
                if case.effective_at > request.as_of:
                    rejections.append(
                        ControversyRejection(
                            provider_id=self.provider_id,
                            source_path=path,
                            case_id=case.case_id,
                            reason=ControversyRejectionReason.POST_CUTOFF_EVIDENCE,
                            detail=(
                                f"effective_at {canonical_datetime(case.effective_at)} is after "
                                f"as_of {canonical_datetime(request.as_of)}"
                            ),
                        )
                    )
                    continue
                if case.market_ref not in market_by_ref:
                    rejections.append(
                        ControversyRejection(
                            provider_id=self.provider_id,
                            source_path=path,
                            case_id=case.case_id,
                            reason=ControversyRejectionReason.UNMAPPED_MARKET,
                            detail=f"market_ref {case.market_ref} has no unambiguous mapping",
                        )
                    )
                    continue
                if case.quote_end > len(payload.artifact_text) or not payload.artifact_text[
                    case.quote_start : case.quote_end
                ].strip():
                    rejections.append(
                        ControversyRejection(
                            provider_id=self.provider_id,
                            source_path=path,
                            case_id=case.case_id,
                            reason=ControversyRejectionReason.SOURCE_INCOMPLETE,
                            detail="case offsets do not select a non-empty in-bounds source span",
                        )
                    )
                    continue
                survivors.append((payload, case, market_by_ref[case.market_ref]))

        hits: list[RecallHit] = []
        by_case_id: dict[str, list[tuple[ControversySourcePayload, ControversyDisputeCase, str]]] = {}
        for survivor in survivors:
            by_case_id.setdefault(survivor[1].case_id, []).append(survivor)
        for case_id in sorted(by_case_id):
            group = sorted(
                by_case_id[case_id],
                key=lambda item: (content_sha256(item[1]), item[0].identity.source_path),
            )
            content_hashes = {content_sha256(item[1]) for item in group}
            if len(content_hashes) > 1:
                for payload, case, _ in group:
                    rejections.append(
                        ControversyRejection(
                            provider_id=self.provider_id,
                            source_path=payload.identity.source_path,
                            case_id=case_id,
                            reason=ControversyRejectionReason.DUPLICATE_CASE_CONFLICT,
                            detail=(
                                f"{len(group)} conflicting entries share case_id; "
                                f"content hashes {sorted(content_hashes)}"
                            ),
                        )
                    )
                continue
            chosen = group[0]
            for payload, case, _ in group[1:]:
                rejections.append(
                    ControversyRejection(
                        provider_id=self.provider_id,
                        source_path=payload.identity.source_path,
                        case_id=case_id,
                        reason=ControversyRejectionReason.DUPLICATE_CASE,
                        detail="redundant entry; the identical logical case is retained exactly once",
                    )
                )
            hits.append(self._build_hit(request, chosen[0], chosen[1], chosen[2]))

        return ControversyRecallOutcome(
            provider_id=self.provider_id,
            recaller_version=self.recaller_version,
            as_of=request.as_of,
            request_sha256=content_sha256(_canonical_request_view(request)),
            hits=tuple(sorted(hits, key=lambda item: item.record_id)),
            rejections=tuple(
                sorted(
                    rejections,
                    key=lambda item: (item.source_path, item.case_id, item.reason.value, item.detail),
                )
            ),
            skips=tuple(sorted(skips, key=lambda item: (item.source_path, item.reason.value))),
        )

    def _build_hit(
        self,
        request: ControversyRecallRequest,
        payload: ControversySourcePayload,
        case: ControversyDisputeCase,
        market_id: str,
    ) -> RecallHit:
        reason_code = f"CONTROVERSY_{case.finding.value}"
        excerpt = payload.artifact_text[case.quote_start : case.quote_end]
        identity = {
            "provider_id": self.provider_id,
            "recaller_version": self.recaller_version,
            "run_id": request.run_id,
            "created_at": request.as_of,
            "market_id": market_id,
            "reason_code": reason_code,
            "case_id": case.case_id,
            "effective_at": case.effective_at,
            "quote_start": case.quote_start,
            "quote_end": case.quote_end,
            "quote_sha256": bytes_sha256(excerpt.encode("utf-8")),
            "source_path": payload.identity.source_path,
            "source_device": payload.identity.device,
            "source_inode": payload.identity.inode,
            "fixture_id": payload.identity.fixture_id,
            "schema_sha256": payload.identity.schema_sha256,
            "corpus_revision_sha256": request.corpus_revision_sha256,
            "source_artifact_id": payload.source_artifact_id,
        }
        return RecallHit(
            record_id=stable_record_id("recall_hit", identity),
            run_id=request.run_id,
            created_at=request.as_of,
            source=self.provider_id,
            source_version=self.recaller_version,
            provenance=(
                ProvenanceRef(
                    source_artifact_id=payload.source_artifact_id,
                    relation="controversy_dispute_case",
                    content_sha256=payload.artifact_sha256,
                    source_observed_at=case.effective_at,
                ),
            ),
            extensions={},
            market_id=market_id,
            recaller=RecallerType.CONTROVERSY,
            recaller_version=self.recaller_version,
            reason_codes=(reason_code,),
            features={
                "case_id": case.case_id,
                "finding": case.finding.value,
                "effective_at": case.effective_at,
                "quote_start": case.quote_start,
                "quote_end": case.quote_end,
                "quote_sha256": identity["quote_sha256"],
                "source_path": payload.identity.source_path,
                "source_device": payload.identity.device,
                "source_inode": payload.identity.inode,
                "fixture_id": payload.identity.fixture_id,
                "schema_sha256": payload.identity.schema_sha256,
                "corpus_revision_sha256": request.corpus_revision_sha256,
                "source_artifact_id": payload.source_artifact_id,
            },
            raw_score=CONTROVERSY_RAW_SCORE,
            observed_at=case.effective_at,
            valid_until=None,
            historical_only=False,
        )
