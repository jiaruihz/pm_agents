"""Versioned decision facts between city models and shared execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Mapping

from weather_data_feed.information_events import canonical_json_hash
from weather_model_evaluation.contracts import parse_utc, utc_text


MODEL_OUTPUT_SCHEMA_VERSION = "weather_city_model_output_v1"
SIGNAL_CANDIDATE_SCHEMA_VERSION = "weather_city_signal_candidate_v1"
TRADE_INTENT_SCHEMA_VERSION = "weather_city_trade_intent_v1"

TARGET_KINDS = {"physical_path", "settlement_outcome", "market_expression"}
SCORABLE_STATUSES = {"scorable", "not_scorable"}
CANDIDATE_STATUSES = {"observed", "scored", "blocked"}
EXPRESSION_SIDES = {"YES", "NO"}
INTENT_SIDES = {"BUY", "SELL"}
NON_LIVE_MODES = {"research", "shadow", "zero_notional"}


def _probability(value: float | None, field_name: str) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} must be in [0, 1]")
    return normalized


@dataclass(frozen=True)
class ModelOutput:
    output_id: str
    checkpoint_id: str
    trigger_event_id: str
    city: str
    target_date: str
    decision_ts_utc: str
    target_id: str
    target_kind: str
    p_model: float | None
    model_id: str
    model_artifact_id: str
    feature_set_id: str
    input_refs: tuple[Mapping[str, Any], ...]
    scorable_status: str
    blocker_reason: str | None
    market_feature_role: str
    market_feature_clock: str
    feature_book_snapshot_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = MODEL_OUTPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MODEL_OUTPUT_SCHEMA_VERSION:
            raise ValueError(f"unsupported ModelOutput schema: {self.schema_version}")
        if not all(
            (
                self.checkpoint_id,
                self.trigger_event_id,
                self.city,
                self.target_date,
                self.target_id,
                self.model_id,
                self.model_artifact_id,
                self.feature_set_id,
            )
        ):
            raise ValueError("ModelOutput identity fields are required")
        parse_utc(self.decision_ts_utc)
        if self.target_kind not in TARGET_KINDS:
            raise ValueError(f"unsupported target_kind: {self.target_kind}")
        if self.scorable_status not in SCORABLE_STATUSES:
            raise ValueError(f"unsupported scorable_status: {self.scorable_status}")
        _probability(self.p_model, "p_model")
        if self.scorable_status == "scorable" and self.p_model is None:
            raise ValueError("scorable ModelOutput requires p_model")
        if self.scorable_status == "not_scorable" and not self.blocker_reason:
            raise ValueError("not_scorable ModelOutput requires blocker_reason")
        expected = canonical_json_hash(
            {
                "schema_version": MODEL_OUTPUT_SCHEMA_VERSION,
                "checkpoint_id": self.checkpoint_id,
                "target_id": self.target_id,
                "model_id": self.model_id,
                "model_artifact_id": self.model_artifact_id,
            }
        )
        if self.output_id != expected:
            raise ValueError("output_id does not match contract identity")

    @classmethod
    def create(cls, **values: Any) -> "ModelOutput":
        output_id = canonical_json_hash(
            {
                "schema_version": MODEL_OUTPUT_SCHEMA_VERSION,
                "checkpoint_id": values["checkpoint_id"],
                "target_id": values["target_id"],
                "model_id": values["model_id"],
                "model_artifact_id": values["model_artifact_id"],
            }
        )
        return cls(output_id=output_id, **values)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["decision_ts_utc"] = utc_text(self.decision_ts_utc)
        return value

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "ModelOutput":
        value = dict(row)
        value["input_refs"] = tuple(value.get("input_refs") or ())
        return cls(**value)


def candidate_identity(
    *,
    checkpoint_id: str,
    trigger_event_id: str,
    strategy_key: str,
    model_artifact_id: str,
    condition_id: str | None,
    bracket: str | None,
    side: str,
) -> str:
    """Match the existing v2 event-checkpoint canonical candidate grain."""

    return canonical_json_hash(
        {
            "candidate_grain_version": "v2_event_checkpoint",
            "strategy_key": strategy_key,
            "model_artifact_id": model_artifact_id,
            "condition_id": condition_id,
            "bracket": bracket,
            "expression_side": side,
            "trigger_event_id": trigger_event_id,
            "state_checkpoint_id": checkpoint_id,
        }
    )


@dataclass(frozen=True)
class SignalCandidate:
    candidate_id: str
    checkpoint_id: str
    trigger_event_id: str
    city: str
    target_date: str
    decision_ts_utc: str
    target_id: str
    target_kind: str
    expression_id: str
    condition_id: str | None
    market_id: str | None
    token_id: str | None
    bracket: str | None
    side: str
    p_model: float | None
    market_p: float | None
    executable_cost: float | None
    model_id: str
    model_artifact_id: str
    feature_set_id: str
    feature_book_snapshot_id: str | None
    execution_book_snapshot_id: str | None
    strategy_key: str
    policy_id: str
    candidate_status: str
    blocker_reason: str | None
    selected: bool
    market_evidence_status: str
    input_refs: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SIGNAL_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SIGNAL_CANDIDATE_SCHEMA_VERSION:
            raise ValueError(f"unsupported SignalCandidate schema: {self.schema_version}")
        if self.side not in EXPRESSION_SIDES:
            raise ValueError(f"candidate side must be YES/NO, got {self.side}")
        if self.target_kind != "market_expression":
            raise ValueError(
                "SignalCandidate requires an explicitly mapped market_expression target"
            )
        if self.candidate_status not in CANDIDATE_STATUSES:
            raise ValueError(f"unsupported candidate_status: {self.candidate_status}")
        parse_utc(self.decision_ts_utc)
        _probability(self.p_model, "p_model")
        _probability(self.market_p, "market_p")
        if self.executable_cost is not None and not 0.0 <= float(self.executable_cost) <= 1.0:
            raise ValueError("executable_cost must be in [0, 1]")
        if self.candidate_status == "blocked" and not self.blocker_reason:
            raise ValueError("blocked candidate requires blocker_reason")
        if self.selected and self.candidate_status != "scored":
            raise ValueError("only scored candidates may be selected")
        expected = candidate_identity(
            checkpoint_id=self.checkpoint_id,
            trigger_event_id=self.trigger_event_id,
            strategy_key=self.strategy_key,
            model_artifact_id=self.model_artifact_id,
            condition_id=self.condition_id,
            bracket=self.bracket,
            side=self.side,
        )
        if self.candidate_id != expected:
            raise ValueError("candidate_id does not match canonical identity")

    @classmethod
    def create(cls, **values: Any) -> "SignalCandidate":
        identity = candidate_identity(
            checkpoint_id=values["checkpoint_id"],
            trigger_event_id=values["trigger_event_id"],
            strategy_key=values["strategy_key"],
            model_artifact_id=values["model_artifact_id"],
            condition_id=values.get("condition_id"),
            bracket=values.get("bracket"),
            side=values["side"],
        )
        return cls(candidate_id=identity, **values)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["decision_ts_utc"] = utc_text(self.decision_ts_utc)
        return value

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "SignalCandidate":
        value = dict(row)
        value["input_refs"] = tuple(value.get("input_refs") or ())
        return cls(**value)

    def to_canonical_input(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "state_checkpoint_id": self.checkpoint_id,
            "strategy_key": self.strategy_key,
            "model_artifact_id": self.model_artifact_id,
            "condition_id": self.condition_id,
            "market_id": self.market_id,
            "bracket": self.bracket,
            "side": self.side,
            "decision_ts_utc": utc_text(self.decision_ts_utc),
            "candidate_status": self.candidate_status,
            "candidate_blocker": self.blocker_reason,
            "policy_selected": int(self.selected),
            "first_city_day_selected": 0,
            "market_evidence_status": self.market_evidence_status,
            "model_probability_after": self.p_model,
            "market_probability": self.market_p,
            "decision_entry_price": self.executable_cost,
            "book_snapshot_id": self.execution_book_snapshot_id,
            "target_id": self.target_id,
            "target_kind": self.target_kind,
            "expression_id": self.expression_id,
            "token_id": self.token_id,
            "feature_set_id": self.feature_set_id,
            "feature_book_snapshot_id": self.feature_book_snapshot_id,
            "execution_book_snapshot_id": self.execution_book_snapshot_id,
            "policy_id": self.policy_id,
            "candidate_schema_version": self.schema_version,
            "input_refs_json": json.dumps(
                self.input_refs, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
            "candidate_metadata_json": json.dumps(
                self.metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ),
        }


@dataclass(frozen=True)
class TradeIntent:
    intent_id: str
    candidate_id: str
    condition_id: str
    token_id: str
    side: str
    requested_size: float
    sizing_profile: str
    execution_profile: str
    max_cost: float | None
    ttl_seconds: int | None
    dedupe_key: str
    exposure_bucket: str
    mode: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = TRADE_INTENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != TRADE_INTENT_SCHEMA_VERSION:
            raise ValueError(f"unsupported TradeIntent schema: {self.schema_version}")
        if not self.condition_id or not self.token_id or not self.dedupe_key:
            raise ValueError("TradeIntent market and dedupe identities are required")
        if self.side not in INTENT_SIDES:
            raise ValueError(f"intent side must be BUY/SELL, got {self.side}")
        if self.mode not in NON_LIVE_MODES:
            raise ValueError(
                "Phase 2 TradeIntent cannot grant live mode; shared authorized execution owns live"
            )
        if float(self.requested_size) < 0:
            raise ValueError("requested_size must be non-negative")
        if self.mode == "zero_notional" and float(self.requested_size) != 0.0:
            raise ValueError("zero_notional intent must request zero size")
        if self.max_cost is not None and not 0.0 <= float(self.max_cost) <= 1.0:
            raise ValueError("max_cost must be in [0, 1]")
        expected = self.identity(
            candidate_id=self.candidate_id,
            execution_profile=self.execution_profile,
            sizing_profile=self.sizing_profile,
            dedupe_key=self.dedupe_key,
            mode=self.mode,
        )
        if self.intent_id != expected:
            raise ValueError("intent_id does not match contract identity")

    @staticmethod
    def identity(
        *,
        candidate_id: str,
        execution_profile: str,
        sizing_profile: str,
        dedupe_key: str,
        mode: str,
    ) -> str:
        return canonical_json_hash(
            {
                "schema_version": TRADE_INTENT_SCHEMA_VERSION,
                "candidate_id": candidate_id,
                "execution_profile": execution_profile,
                "sizing_profile": sizing_profile,
                "dedupe_key": dedupe_key,
                "mode": mode,
            }
        )

    @classmethod
    def create(cls, **values: Any) -> "TradeIntent":
        identity = cls.identity(
            candidate_id=values["candidate_id"],
            execution_profile=values["execution_profile"],
            sizing_profile=values["sizing_profile"],
            dedupe_key=values["dedupe_key"],
            mode=values["mode"],
        )
        return cls(intent_id=identity, **values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "TradeIntent":
        return cls(**dict(row))
