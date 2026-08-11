"""Explicit adapters from city shadow v1/v2 journals into shared facts."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Mapping

from src.strategies.weather_city_probability_shadow.core import (
    CityScore,
    migrate_evaluation_row,
)
from weather_dashboard.ingest.state_checkpoints import build_state_checkpoint
from weather_data_feed.information_events import (
    build_information_event,
    canonical_json_hash,
)

from .contracts import ModelOutput, SignalCandidate, TradeIntent


@dataclass(frozen=True)
class DecisionBundle:
    information_event: dict[str, Any]
    state_checkpoint: dict[str, Any]
    model_output: ModelOutput
    signal_candidate: SignalCandidate

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "DecisionBundle":
        required = {
            "information_event",
            "state_checkpoint",
            "model_output",
            "signal_candidate",
        }
        missing = sorted(required - set(row))
        if missing:
            raise ValueError(f"decision bundle missing fields: {missing}")
        return cls(
            information_event=dict(row["information_event"]),
            state_checkpoint=dict(row["state_checkpoint"]),
            model_output=ModelOutput.from_dict(row["model_output"]),
            signal_candidate=SignalCandidate.from_dict(row["signal_candidate"]),
        )


# Compatibility name for callers that want to make the source explicit.
LegacyDecisionBundle = DecisionBundle


def _city_score(row: Mapping[str, Any]) -> CityScore:
    names = {item.name for item in fields(CityScore)}
    values = {name: row[name] for name in names if name in row}
    missing = sorted(name for name in names if name not in values and name not in {
        "evaluation_status", "not_scorable_reason"
    })
    if missing:
        raise ValueError(f"legacy evaluation is missing CityScore fields: {missing}")
    return CityScore(**values)


def _input_refs(lineage: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    refs = []
    for key, value in sorted(lineage.items()):
        if key.endswith("_input_ref") and isinstance(value, Mapping):
            refs.append({"role": key.removesuffix("_input_ref"), **dict(value)})
    return tuple(refs)


def _legacy_event_and_checkpoint(
    score: CityScore,
) -> tuple[dict[str, Any], dict[str, Any]]:
    lineage = score.lineage or {}
    source = str(lineage.get("source") or "legacy_city_probability_shadow")
    first_seen = lineage.get("source_first_seen_at_utc")
    pit_class = "collector_exact" if first_seen else "archive_known_available"
    normalized_payload = {
        "source_obs_ts_utc": score.source_obs_ts_utc,
        "source_payload_hash": lineage.get("source_payload_hash"),
        "source_raw_payload_hash": lineage.get("source_raw_payload_hash"),
    }
    content_parts = [
        score.city,
        score.target_date,
        score.source_obs_ts_utc,
        str(lineage.get("source_payload_hash") or "payload_hash_unknown"),
    ]
    if not first_seen:
        content_parts.append(score.decision_ts_utc)
    event = build_information_event(
        event_kind="observation",
        event_role="new_content",
        source=source,
        city=score.city,
        station_id=None,
        provider_item_id=str(score.source_obs_ts_utc),
        content_key="|".join(content_parts),
        normalized_payload=normalized_payload,
        source_event_ts_utc=score.source_obs_ts_utc,
        detected_at_utc=first_seen,
        first_seen_at_utc=first_seen,
        available_at_utc=str(first_seen or score.decision_ts_utc),
        pit_lineage_class=pit_class,
        raw_source_path=(
            str(_input_refs(lineage)[0].get("physical_path"))
            if _input_refs(lineage)
            else None
        ),
    )
    feature_set_id = canonical_json_hash(sorted(score.features))
    artifact_id = str(
        lineage.get("model_artifact_sha256") or score.model_id
    )
    checkpoint = build_state_checkpoint(
        city=score.city,
        target_date=score.target_date,
        trigger_event=event,
        as_of_ts_utc=score.decision_ts_utc,
        feature_frame_ref={
            "store_frame_id": f"legacy:{score.model_id}",
            "feature_row_id": canonical_json_hash(
                {
                    "city": score.city,
                    "target_date": score.target_date,
                    "decision_ts_utc": score.decision_ts_utc,
                    "features": score.features,
                }
            ),
            "feature_schema_version": "legacy_city_score_features_v1",
            "feature_version_manifest": {
                "feature_set_id": feature_set_id,
                "model_artifact_id": artifact_id,
                "adapter": "CityScore",
            },
            "source_profile_id": lineage.get("profile_id"),
        },
        input_events=[event],
        pit_provenance=(
            "live_capture" if pit_class == "collector_exact" else "archive_reconstruction"
        ),
        created_at_utc=score.decision_ts_utc,
    )
    return event, checkpoint


def legacy_bundle_from_evaluation(row: Mapping[str, Any]) -> DecisionBundle:
    """Convert one evaluation without changing or deleting the legacy row."""

    migrated = migrate_evaluation_row(dict(row))
    score = _city_score(migrated)
    event, checkpoint = _legacy_event_and_checkpoint(score)
    lineage = score.lineage or {}
    market = score.market or {}
    feature_set_id = canonical_json_hash(sorted(score.features))
    artifact_id = str(lineage.get("model_artifact_sha256") or score.model_id)
    model_output = ModelOutput.create(
        checkpoint_id=checkpoint["state_checkpoint_id"],
        trigger_event_id=event["information_event_id"],
        city=score.city,
        target_date=score.target_date,
        decision_ts_utc=score.decision_ts_utc,
        target_id=(
            f"{lineage.get('probability_target') or f'legacy_exact_bracket_{score.current_bracket}'}"
            f":{score.market_side.lower()}"
        ),
        target_kind="market_expression",
        p_model=score.model_probability,
        model_id=score.model_id,
        model_artifact_id=artifact_id,
        feature_set_id=feature_set_id,
        input_refs=_input_refs(lineage),
        scorable_status=(
            "scorable" if score.evaluation_status == "scored" else "not_scorable"
        ),
        blocker_reason=score.not_scorable_reason,
        market_feature_role=str(
            lineage.get("market_feature_role") or "joint_feature"
        ),
        market_feature_clock=str(
            lineage.get("market_feature_clock") or "decision_current"
        ),
        feature_book_snapshot_id=str(
            lineage.get("book_snapshot_id") or market.get("book_snapshot_id") or ""
        )
        or None,
        metadata={
            "legacy_schema_version": migrated.get("source_schema_version")
            or migrated.get("schema_version"),
            "legacy_evaluation_id": migrated.get("evaluation_id"),
            "checkpoint_identity_status": "legacy_derived_from_city_score_lineage",
            "weather_probability_stay": lineage.get("weather_probability_stay"),
            "source_obs_ts_utc": score.source_obs_ts_utc,
            "feature_coverage": score.feature_coverage,
            "missing_features": list(score.missing_features),
        },
    )

    condition_id = market.get("condition_id")
    raw_token_id = market.get("token_id")
    market_outcome = str(market.get("outcome") or "").upper()
    token_id = raw_token_id if market_outcome == str(score.market_side).upper() else None
    book_snapshot_id = (
        lineage.get("book_snapshot_id") or market.get("book_snapshot_id")
    )
    blocker = score.not_scorable_reason
    if raw_token_id and not market_outcome:
        blocker = "token_outcome_unknown"
    elif raw_token_id and market_outcome != str(score.market_side).upper():
        blocker = "token_outcome_mismatch"
    elif not condition_id or not token_id:
        blocker = "missing_market_expression_identity"
    elif blocker:
        pass
    elif score.market_entry_price is None:
        blocker = "missing_executable_quote"
    elif score.model_probability is None:
        blocker = blocker or "missing_model_probability"
    status = "blocked" if blocker else "scored"
    selected = (
        migrated.get("record_kind") == "paper_intent"
        and bool(migrated.get("would_enter"))
        and status == "scored"
    )
    profile_id = str(lineage.get("profile_id") or "legacy_city_probability")
    policy_id = str(
        lineage.get("probability_policy")
        or "legacy_edge_threshold"
    )
    side = str(score.market_side).upper()
    expression_id = str(
        condition_id
        or canonical_json_hash(
            {
                "city": score.city,
                "target_date": score.target_date,
                "bracket": score.current_bracket,
                "side": side,
            }
        )
    )
    candidate = SignalCandidate.create(
        checkpoint_id=model_output.checkpoint_id,
        trigger_event_id=model_output.trigger_event_id,
        city=score.city,
        target_date=score.target_date,
        decision_ts_utc=score.decision_ts_utc,
        target_id=model_output.target_id,
        target_kind="market_expression",
        expression_id=expression_id,
        condition_id=str(condition_id) if condition_id else None,
        market_id=str(market.get("market_id")) if market.get("market_id") else None,
        token_id=str(token_id) if token_id else None,
        bracket=str(score.current_bracket),
        side=side,
        p_model=score.model_probability,
        market_p=score.market_probability,
        executable_cost=migrated.get("effective_cost_per_share")
        if migrated.get("effective_cost_per_share") is not None
        else score.market_entry_price,
        model_id=score.model_id,
        model_artifact_id=artifact_id,
        feature_set_id=feature_set_id,
        feature_book_snapshot_id=model_output.feature_book_snapshot_id,
        execution_book_snapshot_id=str(book_snapshot_id) if book_snapshot_id else None,
        strategy_key=f"weather_city_probability:{profile_id}",
        policy_id=policy_id,
        candidate_status=status,
        blocker_reason=blocker,
        selected=selected,
        market_evidence_status=(
            "available"
            if score.market_probability is not None and score.market_entry_price is not None
            else str(score.not_scorable_reason or "missing_market_evidence")
        ),
        input_refs=model_output.input_refs,
        metadata={
            "legacy_evaluation_id": migrated.get("evaluation_id"),
            "edge_after_fee": migrated.get("edge_after_fee"),
            "edge_threshold": migrated.get("edge_threshold"),
            "legacy_would_enter": bool(migrated.get("would_enter")),
            "feature_coverage": score.feature_coverage,
            "missing_features": list(score.missing_features),
        },
    )
    return DecisionBundle(
        information_event=event,
        state_checkpoint=checkpoint,
        model_output=model_output,
        signal_candidate=candidate,
    )


def legacy_trade_intent_from_paper_intent(
    row: Mapping[str, Any],
    *,
    execution_profile: str = "legacy_zero_notional",
) -> TradeIntent:
    """Convert a legacy paper_intent; it can never grant live authority."""

    bundle = legacy_bundle_from_evaluation(row)
    candidate = bundle.signal_candidate
    if candidate.candidate_status != "scored":
        raise ValueError(
            f"legacy paper intent maps to blocked candidate: {candidate.blocker_reason}"
        )
    if not candidate.condition_id or not candidate.token_id:
        raise ValueError("legacy paper intent lacks condition/token identity")
    position_key = str(row.get("position_key") or candidate.candidate_id)
    return TradeIntent.create(
        candidate_id=candidate.candidate_id,
        condition_id=candidate.condition_id,
        token_id=candidate.token_id,
        side="BUY",
        requested_size=0.0,
        sizing_profile="legacy_zero_size",
        execution_profile=execution_profile,
        max_cost=candidate.executable_cost,
        ttl_seconds=(
            int(row["ttl_seconds"]) if row.get("ttl_seconds") is not None else None
        ),
        dedupe_key=position_key,
        exposure_bucket=f"{candidate.city}|{candidate.target_date}",
        mode="zero_notional",
        metadata={
            "legacy_record_kind": row.get("record_kind"),
            "legacy_intent_kind": row.get("intent_kind"),
        },
    )
