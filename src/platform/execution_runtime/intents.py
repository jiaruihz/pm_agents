"""Strategy-neutral, non-live TradeIntent contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from src.platform.market_data.identity import canonical_json_hash


TRADE_INTENT_SCHEMA_VERSION = "polymarket_trade_intent_v1"
NON_LIVE_MODES = frozenset({"research", "zero_notional", "shadow"})


@dataclass(frozen=True)
class TradeIntent:
    intent_id: str
    candidate_id: str
    strategy_key: str
    policy_id: str
    condition_id: str
    token_id: str
    venue_side: str
    outcome_label: str
    requested_size: float
    sizing_profile: str
    execution_profile: str
    dedupe_key: str
    exposure_bucket: str
    mode: str
    created_at_utc: str
    max_cost: float | None = None
    ttl_seconds: int | None = None
    execution_book_snapshot_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = TRADE_INTENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        required = (
            self.intent_id,
            self.candidate_id,
            self.strategy_key,
            self.policy_id,
            self.condition_id,
            self.token_id,
            self.outcome_label,
            self.sizing_profile,
            self.execution_profile,
            self.dedupe_key,
            self.exposure_bucket,
            self.created_at_utc,
        )
        if not all(str(value).strip() for value in required):
            raise ValueError("TradeIntent identities, market mapping and profiles are required")
        if self.venue_side not in {"BUY", "SELL"}:
            raise ValueError("venue_side must be BUY or SELL")
        if self.mode not in NON_LIVE_MODES:
            raise ValueError("shared decision TradeIntent cannot grant live authority")
        if self.requested_size < 0:
            raise ValueError("requested_size must be non-negative")
        if self.mode == "zero_notional" and self.requested_size != 0:
            raise ValueError("zero_notional TradeIntent must request zero size")
        if self.mode == "shadow" and self.requested_size <= 0:
            raise ValueError("shadow TradeIntent must request positive size")
        if self.max_cost is not None and not 0 <= self.max_cost <= 1:
            raise ValueError("max_cost must be in [0,1]")
        if self.ttl_seconds is not None and self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        object.__setattr__(self, "metadata", dict(self.metadata))
        if self.intent_id != self.identity(
            candidate_id=self.candidate_id,
            strategy_key=self.strategy_key,
            policy_id=self.policy_id,
            condition_id=self.condition_id,
            token_id=self.token_id,
            venue_side=self.venue_side,
            outcome_label=self.outcome_label,
            sizing_profile=self.sizing_profile,
            execution_profile=self.execution_profile,
            mode=self.mode,
        ):
            raise ValueError("intent_id does not match canonical identity")

    @staticmethod
    def identity(**values: Any) -> str:
        return canonical_json_hash(
            {"schema_version": TRADE_INTENT_SCHEMA_VERSION, **values}
        )

    @classmethod
    def create(cls, **values: Any) -> "TradeIntent":
        identity_fields = {
            name: values[name]
            for name in (
                "candidate_id",
                "strategy_key",
                "policy_id",
                "condition_id",
                "token_id",
                "venue_side",
                "outcome_label",
                "sizing_profile",
                "execution_profile",
                "mode",
            )
        }
        return cls(intent_id=cls.identity(**identity_fields), **values)

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["metadata"] = dict(self.metadata)
        return row
