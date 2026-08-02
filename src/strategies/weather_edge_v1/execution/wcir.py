"""Compatibility boundary from WCIR decisions to the shared execution runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Mapping

from weather_city_runtime.contracts import SignalCandidate, TradeIntent
from weather_model_evaluation.contracts import parse_utc, utc_text

from .contracts import (
    EXECUTION_SCHEMA_VERSION,
    ExecutionConstraints,
    ExecutionIntent,
    make_live_exposure_key,
    make_plan_dedupe_key,
)
from .profiles import execution_config_id_for_profile, resolve_execution_profile


WCIR_EXECUTION_HANDOFF_SCHEMA_VERSION = "weather_wcir_execution_handoff_v1"


class WCIRExecutionCompatibilityError(ValueError):
    """A WCIR decision cannot be mapped without guessing execution semantics."""


@dataclass(frozen=True)
class WCIRExecutionHandoff:
    source_intent_id: str
    source_candidate_id: str
    status: str
    reason: str
    execution_intent: ExecutionIntent | None = None
    schema_version: str = WCIR_EXECUTION_HANDOFF_SCHEMA_VERSION

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_intent_id": self.source_intent_id,
            "source_candidate_id": self.source_candidate_id,
            "status": self.status,
            "reason": self.reason,
            "execution_intent": (
                None if self.execution_intent is None else self.execution_intent.to_json()
            ),
        }


def _validate_link(intent: TradeIntent, candidate: SignalCandidate) -> None:
    if intent.candidate_id != candidate.candidate_id:
        raise WCIRExecutionCompatibilityError("candidate identity mismatch")
    if not candidate.selected or candidate.candidate_status != "scored":
        raise WCIRExecutionCompatibilityError("execution requires a selected scored candidate")
    if intent.condition_id != candidate.condition_id:
        raise WCIRExecutionCompatibilityError("condition identity mismatch")
    if intent.token_id != candidate.token_id:
        raise WCIRExecutionCompatibilityError("token identity mismatch")


def build_wcir_execution_handoff(
    *,
    trade_intent: TradeIntent,
    signal_candidate: SignalCandidate,
    strategy_instance: str,
    config_id: str,
) -> WCIRExecutionHandoff:
    """Resolve a WCIR intent at the existing execution-system boundary.

    Research and zero-notional intents remain auditable record-only handoffs.
    A positive-size shadow intent is converted to the existing
    :class:`ExecutionIntent`; an injected paper venue/run context may then pass
    it to ``OrderRuntime``.  This adapter cannot create a live run context.
    """

    _validate_link(trade_intent, signal_candidate)
    if trade_intent.mode in {"research", "zero_notional"}:
        return WCIRExecutionHandoff(
            source_intent_id=trade_intent.intent_id,
            source_candidate_id=signal_candidate.candidate_id,
            status="record_only",
            reason=f"{trade_intent.mode}_has_no_execution_side_effect",
        )
    if trade_intent.mode != "shadow":
        raise WCIRExecutionCompatibilityError(
            f"unsupported WCIR execution mode: {trade_intent.mode}"
        )
    if trade_intent.requested_size <= 0:
        raise WCIRExecutionCompatibilityError("shadow execution requires positive shares")

    resolution = resolve_execution_profile(trade_intent.execution_profile)
    deadline = None
    if trade_intent.ttl_seconds is not None:
        deadline = utc_text(
            parse_utc(signal_candidate.decision_ts_utc)
            + timedelta(seconds=trade_intent.ttl_seconds)
        )
    venue_side = trade_intent.side
    outcome_side = signal_candidate.side
    signal_side = f"{venue_side}_{outcome_side}"
    execution_intent = ExecutionIntent(
        execution_schema_version=EXECUTION_SCHEMA_VERSION,
        signal_id=signal_candidate.candidate_id,
        opportunity_id=signal_candidate.candidate_id,
        comparison_group_id=signal_candidate.candidate_id,
        strategy_id=signal_candidate.strategy_key,
        strategy_instance=strategy_instance,
        config_id=config_id,
        execution_profile=trade_intent.execution_profile,
        resolved_execution_profile=resolution.resolved_execution_profile,
        execution_config_id=execution_config_id_for_profile(
            trade_intent.execution_profile
        ),
        plan_dedupe_key=make_plan_dedupe_key(
            strategy_id=signal_candidate.strategy_key,
            strategy_instance=strategy_instance,
            config_id=config_id,
            opportunity_id=signal_candidate.candidate_id,
            execution_profile=resolution.resolved_execution_profile,
            child_role="single",
        ),
        live_exposure_key=make_live_exposure_key(
            authorized_scope=strategy_instance,
            opportunity_id=signal_candidate.candidate_id,
            token_id=trade_intent.token_id,
            venue_side=venue_side,
            outcome_side=outcome_side,
        ),
        token_id=trade_intent.token_id,
        venue_side=venue_side,
        outcome_side=outcome_side,
        signal_side=signal_side,
        total_shares=Decimal(str(trade_intent.requested_size)),
        created_at_utc=utc_text(parse_utc(signal_candidate.decision_ts_utc)),
        constraints=ExecutionConstraints(
            price_cap=(
                None
                if trade_intent.max_cost is None
                else Decimal(str(trade_intent.max_cost))
            ),
            maximum_shares=Decimal(str(trade_intent.requested_size)),
            deadline_utc=deadline,
        ),
        model_token_probability=(
            None
            if signal_candidate.p_model is None
            else Decimal(str(signal_candidate.p_model))
        ),
        fair_value=(
            None
            if signal_candidate.p_model is None
            else Decimal(str(signal_candidate.p_model))
        ),
        strategy_price_cap=(
            None
            if trade_intent.max_cost is None
            else Decimal(str(trade_intent.max_cost))
        ),
        data_epoch_ref=signal_candidate.checkpoint_id,
        metadata={
            "wcir_intent_id": trade_intent.intent_id,
            "wcir_candidate_id": signal_candidate.candidate_id,
            "wcir_condition_id": trade_intent.condition_id,
            "wcir_dedupe_key": trade_intent.dedupe_key,
            "wcir_exposure_bucket": trade_intent.exposure_bucket,
            "sizing_profile": trade_intent.sizing_profile,
        },
    )
    return WCIRExecutionHandoff(
        source_intent_id=trade_intent.intent_id,
        source_candidate_id=signal_candidate.candidate_id,
        status="ready_for_paper_runtime",
        reason="mapped_to_shared_execution_intent",
        execution_intent=execution_intent,
    )
