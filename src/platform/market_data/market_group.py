"""Generic Polymarket group/ladder snapshot contract.

This layer describes tradable outcome expressions and book references.  It
does not know about weather cities/brackets, crypto thresholds, or dispute
semantics; domain materializers add those projections separately.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from src.platform.market_data.identity import canonical_json_hash


MARKET_EXPRESSION_SCHEMA_VERSION = "polymarket_market_expression_v1"
MARKET_GROUP_SNAPSHOT_SCHEMA_VERSION = "polymarket_market_group_snapshot_v1"


@dataclass(frozen=True)
class MarketExpression:
    expression_id: str
    group_id: str
    market_id: str
    condition_id: str
    outcome_index: int
    outcome_label: str
    token_id: str
    ordinal: int | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = MARKET_EXPRESSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not all(
            str(value).strip()
            for value in (self.group_id, self.market_id, self.condition_id, self.outcome_label, self.token_id)
        ):
            raise ValueError("market expression identities and outcome label are required")
        if self.outcome_index < 0:
            raise ValueError("outcome_index must be non-negative")
        if self.lower_bound is not None and self.upper_bound is not None and self.lower_bound > self.upper_bound:
            raise ValueError("lower_bound cannot exceed upper_bound")
        object.__setattr__(self, "metadata", dict(self.metadata))
        expected = self.identity(
            group_id=self.group_id,
            market_id=self.market_id,
            condition_id=self.condition_id,
            outcome_index=self.outcome_index,
            outcome_label=self.outcome_label,
            token_id=self.token_id,
        )
        if self.expression_id != expected:
            raise ValueError("expression_id does not match canonical identity")

    @staticmethod
    def identity(**values: Any) -> str:
        return canonical_json_hash(
            {"schema_version": MARKET_EXPRESSION_SCHEMA_VERSION, **values}
        )

    @classmethod
    def create(cls, **values: Any) -> "MarketExpression":
        identity_fields = {
            name: values[name]
            for name in (
                "group_id",
                "market_id",
                "condition_id",
                "outcome_index",
                "outcome_label",
                "token_id",
            )
        }
        return cls(expression_id=cls.identity(**identity_fields), **values)

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["metadata"] = dict(self.metadata)
        return row


@dataclass(frozen=True)
class MarketGroupSnapshot:
    group_snapshot_id: str
    group_id: str
    group_kind: str
    captured_at_utc: str
    available_at_utc: str
    expression_manifest_hash: str
    expressions: tuple[MarketExpression, ...]
    book_snapshot_ids: Mapping[str, str | None]
    capture_batch_id: str | None
    batch_complete: bool
    blockers: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = MARKET_GROUP_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.group_id or not self.group_kind or not self.captured_at_utc or not self.available_at_utc:
            raise ValueError("group identity, kind, captured and available clocks are required")
        expressions = tuple(self.expressions)
        if not expressions:
            raise ValueError("market group snapshot requires at least one expression")
        if any(row.group_id != self.group_id for row in expressions):
            raise ValueError("market expression belongs to a different group")
        object.__setattr__(self, "expressions", expressions)
        object.__setattr__(self, "book_snapshot_ids", dict(self.book_snapshot_ids))
        object.__setattr__(self, "blockers", tuple(sorted(set(self.blockers))))
        object.__setattr__(self, "metadata", dict(self.metadata))
        manifest_hash = canonical_json_hash([row.to_dict() for row in expressions])
        if self.expression_manifest_hash != manifest_hash:
            raise ValueError("expression_manifest_hash does not match expressions")
        expected = self.identity(
            group_id=self.group_id,
            group_kind=self.group_kind,
            captured_at_utc=self.captured_at_utc,
            available_at_utc=self.available_at_utc,
            expression_manifest_hash=manifest_hash,
            book_snapshot_ids=self.book_snapshot_ids,
            capture_batch_id=self.capture_batch_id,
        )
        if self.group_snapshot_id != expected:
            raise ValueError("group_snapshot_id does not match canonical identity")

    @staticmethod
    def identity(**values: Any) -> str:
        return canonical_json_hash(
            {"schema_version": MARKET_GROUP_SNAPSHOT_SCHEMA_VERSION, **values}
        )

    @classmethod
    def create(
        cls,
        *,
        group_id: str,
        group_kind: str,
        captured_at_utc: str,
        available_at_utc: str,
        expressions: Iterable[MarketExpression],
        book_snapshot_ids: Mapping[str, str | None],
        capture_batch_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "MarketGroupSnapshot":
        expression_rows = tuple(expressions)
        manifest_hash = canonical_json_hash([row.to_dict() for row in expression_rows])
        refs = {str(token): (None if value is None else str(value)) for token, value in book_snapshot_ids.items()}
        blockers = tuple(
            sorted(
                f"book_snapshot_missing:{row.token_id}"
                for row in expression_rows
                if not refs.get(row.token_id)
            )
        )
        identity_fields = {
            "group_id": group_id,
            "group_kind": group_kind,
            "captured_at_utc": captured_at_utc,
            "available_at_utc": available_at_utc,
            "expression_manifest_hash": manifest_hash,
            "book_snapshot_ids": refs,
            "capture_batch_id": capture_batch_id,
        }
        return cls(
            group_snapshot_id=cls.identity(**identity_fields),
            expression_manifest_hash=manifest_hash,
            expressions=expression_rows,
            book_snapshot_ids=refs,
            batch_complete=not blockers,
            blockers=blockers,
            metadata=dict(metadata or {}),
            **{key: identity_fields[key] for key in ("group_id", "group_kind", "captured_at_utc", "available_at_utc", "capture_batch_id")},
        )

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["expressions"] = [item.to_dict() for item in self.expressions]
        row["book_snapshot_ids"] = dict(self.book_snapshot_ids)
        row["metadata"] = dict(self.metadata)
        return row


def binary_market_group_snapshot(
    *,
    group_id: str,
    market_id: str,
    condition_id: str,
    outcomes: Iterable[str],
    token_ids: Iterable[str],
    captured_at_utc: str,
    available_at_utc: str,
    book_snapshot_ids: Mapping[str, str | None],
    capture_batch_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> MarketGroupSnapshot:
    labels = tuple(str(value) for value in outcomes)
    tokens = tuple(str(value) for value in token_ids)
    if len(labels) != 2 or len(tokens) != 2:
        raise ValueError("binary market group requires exactly two outcomes and tokens")
    expressions = tuple(
        MarketExpression.create(
            group_id=group_id,
            market_id=market_id,
            condition_id=condition_id,
            outcome_index=index,
            outcome_label=label,
            token_id=tokens[index],
            ordinal=index,
        )
        for index, label in enumerate(labels)
    )
    return MarketGroupSnapshot.create(
        group_id=group_id,
        group_kind="binary_condition",
        captured_at_utc=captured_at_utc,
        available_at_utc=available_at_utc,
        expressions=expressions,
        book_snapshot_ids=book_snapshot_ids,
        capture_batch_id=capture_batch_id,
        metadata=metadata,
    )


def condition_market_group_snapshot(
    *,
    group_id: str,
    group_kind: str,
    markets: Iterable[Mapping[str, Any]],
    captured_at_utc: str,
    available_at_utc: str,
    capture_batch_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> MarketGroupSnapshot:
    """Materialize a multi-condition group such as a weather or crypto ladder.

    Each input row declares one condition and its complete outcome/token map.
    Domain order/bracket semantics stay in expression metadata rather than in
    the shared schema.
    """

    normalized = sorted(
        (dict(row) for row in markets),
        key=lambda row: (str(row.get("market_id") or ""), str(row.get("condition_id") or "")),
    )
    expressions: list[MarketExpression] = []
    book_refs: dict[str, str | None] = {}
    for market_ordinal, row in enumerate(normalized):
        market_id = str(row.get("market_id") or "")
        condition_id = str(row.get("condition_id") or "")
        outcomes = tuple(str(value) for value in (row.get("outcomes") or ()))
        token_ids = tuple(str(value) for value in (row.get("token_ids") or ()))
        refs = tuple(row.get("book_snapshot_ids") or ())
        if not market_id or not condition_id or not outcomes or len(outcomes) != len(token_ids):
            raise ValueError("condition group row requires market, condition and aligned outcomes/tokens")
        if refs and len(refs) != len(token_ids):
            raise ValueError("book_snapshot_ids must align with token_ids")
        expression_metadata = dict(row.get("metadata") or {})
        for outcome_index, (label, token_id) in enumerate(zip(outcomes, token_ids, strict=True)):
            expressions.append(
                MarketExpression.create(
                    group_id=group_id,
                    market_id=market_id,
                    condition_id=condition_id,
                    outcome_index=outcome_index,
                    outcome_label=label,
                    token_id=token_id,
                    ordinal=market_ordinal,
                    metadata={
                        **expression_metadata,
                        "market_ordinal": market_ordinal,
                    },
                )
            )
            book_refs[token_id] = None if not refs else refs[outcome_index]
    return MarketGroupSnapshot.create(
        group_id=group_id,
        group_kind=group_kind,
        captured_at_utc=captured_at_utc,
        available_at_utc=available_at_utc,
        expressions=expressions,
        book_snapshot_ids=book_refs,
        capture_batch_id=capture_batch_id,
        metadata=metadata,
    )
