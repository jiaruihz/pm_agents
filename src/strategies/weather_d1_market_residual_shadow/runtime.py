"""Offline/zero-notional runtime for a frozen D-1 market-residual artifact.

This module deliberately has no exchange, venue, order, credential, or network
dependency.  Its furthest side effect is writing research JSON/JSONL evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from weather_city_runtime import ModelOutput, SignalCandidate, TradeIntent
from weather_model_evaluation.contracts import parse_utc, stable_sha256, utc_text


ARTIFACT_SCHEMA_VERSION = "weather_d1_market_residual_artifact_v1"
CHECKPOINT_SCHEMA_VERSION = "weather_d1_market_residual_checkpoint_v1"
PREDICTION_SCHEMA_VERSION = "weather_d1_market_residual_prediction_v1"
RUN_SUMMARY_SCHEMA_VERSION = "weather_d1_market_residual_shadow_summary_v1"
STRATEGY_KEY = "weather.d1_market_residual_probability"
FRAMEWORK_ID = "weather_city_intraday_runtime_v1"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_line(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(_canonical_line(row) + "\n")


def _safe_research_output(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    text = str(resolved)
    allowed = (
        text.startswith("/tmp/"),
        text.startswith("/private/tmp/"),
        "/runtime/research/" in text,
        "/research_outputs/" in text,
        "/research/artifact_store/active/" in text,
    )
    if not any(allowed):
        raise ValueError(
            "shadow output must be under /tmp, runtime/research, "
            "research_outputs, or the active research artifact store"
        )
    return resolved


def apply_market_residual(
    market_probability: Sequence[float], residual_delta: Sequence[float]
) -> list[float]:
    """Return normalized ``market * exp(delta)`` probabilities.

    Zero delta returns the normalized market distribution exactly, including
    exact zeros.  This is the multiclass equivalent of a fixed market logit
    offset and guarantees that an uninformative weather head cannot move price.
    """

    if len(market_probability) != len(residual_delta) or not market_probability:
        raise ValueError("market_probability and residual_delta must have equal nonzero length")
    market = [float(value) for value in market_probability]
    delta = [float(value) for value in residual_delta]
    if any(not math.isfinite(value) or value < 0.0 for value in market):
        raise ValueError("market probabilities must be finite and non-negative")
    if any(not math.isfinite(value) for value in delta):
        raise ValueError("residual deltas must be finite")
    market_sum = sum(market)
    if market_sum <= 0.0:
        raise ValueError("market probabilities must have positive mass")
    normalized = [value / market_sum for value in market]
    offset = max(delta)
    weighted = [probability * math.exp(value - offset) for probability, value in zip(normalized, delta)]
    total = sum(weighted)
    if not math.isfinite(total) or total <= 0.0:
        raise ValueError("posterior normalization failed")
    return [value / total for value in weighted]


class ResidualModel(Protocol):
    artifact: "FrozenResidualArtifact"

    def score_delta(self, rung: Mapping[str, Any], features: Mapping[str, float]) -> float: ...


@dataclass(frozen=True)
class FrozenResidualArtifact:
    path: Path
    sha256: str
    payload: Mapping[str, Any]

    @property
    def artifact_id(self) -> str:
        return str(self.payload["artifact_id"])

    @property
    def model_id(self) -> str:
        return str(self.payload["model_id"])

    @property
    def feature_set_id(self) -> str:
        return str(self.payload["feature_set_id"])

    @classmethod
    def load(cls, path: Path, *, expected_sha256: str | None = None) -> "FrozenResidualArtifact":
        resolved = path.expanduser().resolve()
        raw = resolved.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("frozen artifact sha256 mismatch")
        payload = json.loads(raw)
        required = {
            "schema_version",
            "artifact_id",
            "model_id",
            "feature_set_id",
            "model_family",
            "training_cutoff",
            "frozen_at_utc",
            "target_kind",
            "market_feature_role",
            "market_feature_clock",
            "required_features",
            "parameters",
        }
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError(f"artifact missing fields: {missing}")
        if payload["schema_version"] != ARTIFACT_SCHEMA_VERSION:
            raise ValueError("unsupported residual artifact schema")
        if payload["model_family"] != "linear_market_log_offset":
            raise ValueError("unsupported residual model family")
        if payload["target_kind"] != "market_expression":
            raise ValueError("artifact target_kind must be market_expression")
        if payload["market_feature_role"] != "prior_offset":
            raise ValueError("artifact market_feature_role must be prior_offset")
        if payload["market_feature_clock"] not in {
            "pre_event",
            "first_post_event",
            "decision_current",
        }:
            raise ValueError("artifact must declare a supported market feature clock")
        parse_utc(payload["frozen_at_utc"])
        try:
            datetime.fromisoformat(str(payload["training_cutoff"]))
        except ValueError as exc:
            raise ValueError("training_cutoff must be an ISO date/datetime") from exc
        if not isinstance(payload["required_features"], list):
            raise ValueError("required_features must be a list")
        required_features = {str(name) for name in payload["required_features"]}
        parameter_rows = [payload["parameters"].get("default") or {}]
        parameter_rows.extend((payload["parameters"].get("by_expression") or {}).values())
        declared_weights = {
            str(name)
            for row in parameter_rows
            for name in (row.get("weights") or {})
        }
        unknown_weights = sorted(declared_weights - required_features)
        if unknown_weights:
            raise ValueError(f"artifact weights missing from required_features: {unknown_weights}")
        return cls(path=resolved, sha256=digest, payload=payload)


class LinearMarketResidualModel:
    """Minimal frozen artifact implementation; other families can implement ResidualModel."""

    def __init__(self, artifact: FrozenResidualArtifact):
        self.artifact = artifact
        parameters = artifact.payload["parameters"]
        self.default = dict(parameters.get("default") or {})
        self.by_expression = dict(parameters.get("by_expression") or {})
        self.max_abs_delta = float(parameters.get("max_abs_delta", 4.0))
        if not 0.0 < self.max_abs_delta <= 20.0:
            raise ValueError("max_abs_delta must be in (0, 20]")

    def score_delta(self, rung: Mapping[str, Any], features: Mapping[str, float]) -> float:
        expression_id = str(rung["expression_id"])
        specification = dict(self.default)
        override = self.by_expression.get(expression_id) or self.by_expression.get(str(rung["bracket"])) or {}
        weights = dict(specification.get("weights") or {})
        weights.update(override.get("weights") or {})
        intercept = float(override.get("intercept", specification.get("intercept", 0.0)))
        value = intercept + sum(float(weights.get(name, 0.0)) * float(feature) for name, feature in features.items())
        return max(-self.max_abs_delta, min(self.max_abs_delta, value))


@dataclass(frozen=True)
class ShadowPolicy:
    emit_candidates: bool = False
    emit_intents: bool = False
    minimum_edge: float = 0.0
    max_selected_per_checkpoint: int = 1
    minimum_depth: float = 1.0
    execution_profile: str = "research_record_only_v1"
    sizing_profile: str = "zero_notional_v1"

    def __post_init__(self) -> None:
        if self.emit_intents and not self.emit_candidates:
            raise ValueError("emit_intents requires emit_candidates")
        if self.max_selected_per_checkpoint < 0:
            raise ValueError("max_selected_per_checkpoint must be non-negative")
        if self.minimum_depth < 0.0:
            raise ValueError("minimum_depth must be non-negative")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ShadowPolicy":
        row = dict(value or {})
        return cls(
            emit_candidates=bool(row.get("emit_candidates", False)),
            emit_intents=bool(row.get("emit_intents", False)),
            minimum_edge=float(row.get("minimum_edge", 0.0)),
            max_selected_per_checkpoint=int(row.get("max_selected_per_checkpoint", 1)),
            minimum_depth=float(row.get("minimum_depth", 1.0)),
            execution_profile=str(row.get("execution_profile", "research_record_only_v1")),
            sizing_profile=str(row.get("sizing_profile", "zero_notional_v1")),
        )


class ShadowRuntime:
    """Deterministic, file-input-only shadow scorer."""

    def __init__(self, model: ResidualModel, policy: ShadowPolicy | None = None):
        self.model = model
        self.policy = policy or ShadowPolicy()

    def _validate_checkpoint(self, checkpoint: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[str]]:
        if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
            return [], ["unsupported_checkpoint_schema"]
        required = (
            "checkpoint_id",
            "trigger_event_id",
            "city",
            "target_date",
            "decision_ts_utc",
            "event_available_at_utc",
            "target_id",
            "feature_book_snapshot",
            "features",
        )
        missing = [name for name in required if checkpoint.get(name) in (None, "")]
        if missing:
            return [], [f"missing_checkpoint_fields:{','.join(missing)}"]
        try:
            decision = parse_utc(checkpoint["decision_ts_utc"])
            available = parse_utc(checkpoint["event_available_at_utc"])
        except ValueError:
            return [], ["invalid_checkpoint_clock"]
        if available > decision:
            return [], ["event_not_available_at_decision"]
        book = checkpoint["feature_book_snapshot"]
        if not isinstance(book, Mapping) or not book.get("snapshot_id"):
            return [], ["missing_feature_book_snapshot_id"]
        try:
            if parse_utc(book["observed_at_utc"]) > decision:
                return [], ["feature_book_after_decision"]
        except (KeyError, ValueError):
            return [], ["invalid_feature_book_clock"]
        rungs = list(book.get("rungs") or [])
        if not rungs:
            return [], ["empty_native_ladder"]
        errors: list[str] = []
        identities = [str(row.get("expression_id")) for row in rungs]
        orders = [row.get("native_order") for row in rungs]
        if len(set(identities)) != len(identities) or "None" in identities:
            errors.append("duplicate_or_missing_expression_id")
        if any(not isinstance(value, int) for value in orders) or sorted(orders) != list(range(len(rungs))):
            errors.append("native_lattice_not_contiguous")
        if book.get("rung_completeness") != "complete":
            errors.append("native_ladder_incomplete")
        if not book.get("ladder_hash"):
            errors.append("native_ladder_hash_missing")
        if sum(bool(row.get("open_bottom")) for row in rungs) != 1:
            errors.append("native_ladder_bottom_semantics_invalid")
        if sum(bool(row.get("open_top")) for row in rungs) != 1:
            errors.append("native_ladder_top_semantics_invalid")
        for row in rungs:
            probability = row.get("market_probability")
            if probability is None or not 0.0 <= float(probability) <= 1.0:
                errors.append("invalid_market_probability")
                break
        if not errors:
            ordered = sorted(rungs, key=lambda row: int(row["native_order"]))
            if not ordered[0].get("open_bottom") or not ordered[-1].get("open_top"):
                errors.append("native_ladder_open_tail_order_invalid")
            market_mass = sum(float(row["market_probability"]) for row in ordered)
            if not math.isfinite(market_mass) or market_mass <= 0.0:
                errors.append("market_probability_mass_invalid")
        return sorted(rungs, key=lambda row: int(row["native_order"])), sorted(set(errors))

    def _blocker(self, checkpoint: Mapping[str, Any], reasons: Sequence[str]) -> dict[str, Any]:
        return {
            "schema_version": PREDICTION_SCHEMA_VERSION,
            "record_kind": "checkpoint_blocker",
            "checkpoint_id": checkpoint.get("checkpoint_id"),
            "trigger_event_id": checkpoint.get("trigger_event_id"),
            "city": checkpoint.get("city"),
            "target_date": checkpoint.get("target_date"),
            "decision_ts_utc": checkpoint.get("decision_ts_utc"),
            "base_target_id": checkpoint.get("target_id"),
            "scorable_status": "not_scorable",
            "blocker_reasons": list(reasons),
            "feature_book_snapshot_id": (checkpoint.get("feature_book_snapshot") or {}).get("snapshot_id"),
            "execution_book_snapshot_id": (checkpoint.get("execution_book_snapshot") or {}).get("snapshot_id"),
            "model_id": self.model.artifact.model_id,
            "model_artifact_id": self.model.artifact.sha256,
            "no_order_placed": True,
        }

    @staticmethod
    def _execution_by_expression(checkpoint: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], str | None]:
        snapshot = checkpoint.get("execution_book_snapshot")
        if not isinstance(snapshot, Mapping) or not snapshot.get("snapshot_id"):
            return {}, "missing_execution_book_snapshot"
        try:
            if parse_utc(snapshot["observed_at_utc"]) > parse_utc(checkpoint["decision_ts_utc"]):
                return {}, "execution_book_after_decision"
        except (KeyError, ValueError):
            return {}, "invalid_execution_book_clock"
        return {
            str(row["expression_id"]): row for row in snapshot.get("rungs") or [] if row.get("expression_id")
        }, None

    def score_checkpoint(self, checkpoint: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
        rungs, structural_errors = self._validate_checkpoint(checkpoint)
        if not rungs:
            return {"predictions": [], "candidates": [], "intents": [], "blockers": [self._blocker(checkpoint, structural_errors)]}
        required_features = [str(name) for name in self.model.artifact.payload["required_features"]]
        raw_features = checkpoint.get("features") or {}
        feature_errors = list(structural_errors)
        if parse_utc(self.model.artifact.payload["frozen_at_utc"]) > parse_utc(checkpoint["decision_ts_utc"]):
            feature_errors.append("model_artifact_not_available_at_decision")
        features_by_rung: list[dict[str, float]] = []
        missing_by_rung: dict[str, list[str]] = {}
        invalid_by_rung: list[str] = []
        for rung in rungs:
            expression_id = str(rung["expression_id"])
            merged_features = dict(raw_features)
            rung_features = rung.get("features") or {}
            if not isinstance(rung_features, Mapping):
                invalid_by_rung.append(expression_id)
                features_by_rung.append({})
                continue
            merged_features.update(rung_features)
            missing = [name for name in required_features if merged_features.get(name) is None]
            if missing:
                missing_by_rung[expression_id] = sorted(missing)
                features_by_rung.append({})
                continue
            try:
                features = {name: float(merged_features[name]) for name in required_features}
                if any(not math.isfinite(value) for value in features.values()):
                    raise ValueError
                features_by_rung.append(features)
            except (TypeError, ValueError):
                invalid_by_rung.append(expression_id)
                features_by_rung.append({})
        if missing_by_rung:
            missing_names = sorted({name for names in missing_by_rung.values() for name in names})
            feature_errors.append("missing_required_features:" + ",".join(missing_names))
            feature_errors.append(
                "missing_rung_feature_values:" + ",".join(sorted(missing_by_rung))
            )
        if invalid_by_rung:
            feature_errors.append("invalid_required_feature_value:" + ",".join(sorted(invalid_by_rung)))
        feature_snapshot = checkpoint["feature_book_snapshot"]
        execution_by_expression, execution_error = self._execution_by_expression(checkpoint)
        market = [float(row["market_probability"]) for row in rungs]
        market_sum = sum(market)
        normalized_market = [value / market_sum for value in market] if market_sum > 0 else [None] * len(rungs)
        posterior: list[float | None]
        deltas: list[float | None]
        if feature_errors:
            deltas = [None] * len(rungs)
            posterior = [None] * len(rungs)
        else:
            deltas = [
                self.model.score_delta(row, features_by_rung[index])
                for index, row in enumerate(rungs)
            ]
            posterior = apply_market_residual(market, [float(value) for value in deltas])
        predictions: list[dict[str, Any]] = []
        for index, rung in enumerate(rungs):
            expression_id = str(rung["expression_id"])
            execution = execution_by_expression.get(expression_id) or {}
            executable_cost = execution.get("fee_adjusted_ask")
            raw_ask = execution.get("ask")
            executable_depth = execution.get("depth_at_or_better")
            row_reasons = list(feature_errors)
            scorable = posterior[index] is not None
            model_output = ModelOutput.create(
                checkpoint_id=str(checkpoint["checkpoint_id"]),
                trigger_event_id=str(checkpoint["trigger_event_id"]),
                city=str(checkpoint["city"]),
                target_date=str(checkpoint["target_date"]),
                decision_ts_utc=utc_text(checkpoint["decision_ts_utc"]),
                target_id=f"{checkpoint['target_id']}::{expression_id}",
                target_kind="market_expression",
                p_model=(float(posterior[index]) if scorable else None),
                model_id=self.model.artifact.model_id,
                model_artifact_id=self.model.artifact.sha256,
                feature_set_id=self.model.artifact.feature_set_id,
                input_refs=(
                    {"kind": "checkpoint", "id": checkpoint["checkpoint_id"]},
                    {"kind": "feature_book", "id": feature_snapshot["snapshot_id"]},
                ),
                scorable_status="scorable" if scorable else "not_scorable",
                blocker_reason=(None if scorable else ";".join(row_reasons)),
                market_feature_role="prior_offset",
                market_feature_clock=str(self.model.artifact.payload["market_feature_clock"]),
                feature_book_snapshot_id=str(feature_snapshot["snapshot_id"]),
                metadata={"residual_delta": deltas[index], "native_order": rung["native_order"]},
            )
            predictions.append({
                "schema_version": PREDICTION_SCHEMA_VERSION,
                "record_kind": "prediction",
                "checkpoint_id": checkpoint["checkpoint_id"],
                "trigger_event_id": checkpoint["trigger_event_id"],
                "city": checkpoint["city"],
                "target_date": checkpoint["target_date"],
                "decision_ts_utc": utc_text(checkpoint["decision_ts_utc"]),
                "event_available_at_utc": utc_text(checkpoint["event_available_at_utc"]),
                "base_target_id": checkpoint["target_id"],
                "expression_id": expression_id,
                "condition_id": rung.get("condition_id"),
                "market_id": rung.get("market_id"),
                "token_id": rung.get("token_id"),
                "bracket": rung.get("bracket"),
                "native_order": rung["native_order"],
                "open_bottom": bool(rung.get("open_bottom")),
                "open_top": bool(rung.get("open_top")),
                "market_probability_raw": market[index],
                "market_probability": normalized_market[index],
                "market_probability_sum_raw": market_sum,
                "residual_delta": deltas[index],
                "posterior_probability": posterior[index],
                "executable_cost": (float(executable_cost) if executable_cost is not None else None),
                "raw_ask": (float(raw_ask) if raw_ask is not None else None),
                "executable_depth": (float(executable_depth) if executable_depth is not None else None),
                "scorable_status": "scorable" if scorable else "not_scorable",
                "blocker_reasons": row_reasons,
                "model_id": self.model.artifact.model_id,
                "model_artifact_id": self.model.artifact.sha256,
                "feature_set_id": self.model.artifact.feature_set_id,
                "feature_book_snapshot_id": feature_snapshot["snapshot_id"],
                "execution_book_snapshot_id": (checkpoint.get("execution_book_snapshot") or {}).get("snapshot_id"),
                "feature_book_observed_at_utc": utc_text(feature_snapshot["observed_at_utc"]),
                "execution_book_observed_at_utc": (
                    utc_text(checkpoint["execution_book_snapshot"]["observed_at_utc"])
                    if not execution_error else None
                ),
                "model_output": model_output.to_dict(),
                "candidate_id": None,
                "selected": False,
                "no_order_placed": True,
            })
        candidates: list[dict[str, Any]] = []
        intents: list[dict[str, Any]] = []
        if self.policy.emit_candidates:
            eligible: list[tuple[float, int]] = []
            for index, row in enumerate(predictions):
                if (
                    row["posterior_probability"] is not None
                    and row["executable_cost"] is not None
                    and row["executable_depth"] is not None
                    and float(row["executable_depth"]) >= self.policy.minimum_depth
                    and row["condition_id"]
                    and row["token_id"]
                ):
                    edge = float(row["posterior_probability"]) - float(row["executable_cost"])
                    if edge >= self.policy.minimum_edge:
                        eligible.append((edge, index))
            selected_indexes = {
                index for _edge, index in sorted(eligible, reverse=True)[: self.policy.max_selected_per_checkpoint]
            }
            for index, row in enumerate(predictions):
                expression_id = row["expression_id"]
                blocker = None
                if row["posterior_probability"] is None:
                    blocker = ";".join(row["blocker_reasons"])
                elif execution_error:
                    blocker = execution_error
                elif expression_id not in execution_by_expression:
                    blocker = "execution_expression_missing"
                elif not row["condition_id"] or not row["token_id"]:
                    blocker = "missing_condition_or_token_identity"
                elif row["executable_cost"] is None:
                    blocker = "official_fee_adjusted_cost_missing"
                elif row["executable_depth"] is None:
                    blocker = "executable_depth_missing"
                elif float(row["executable_depth"]) < self.policy.minimum_depth:
                    blocker = "insufficient_executable_depth"
                status = "blocked" if blocker else "scored"
                selected = index in selected_indexes and status == "scored"
                candidate = SignalCandidate.create(
                    checkpoint_id=str(row["checkpoint_id"]),
                    trigger_event_id=str(row["trigger_event_id"]),
                    city=str(row["city"]),
                    target_date=str(row["target_date"]),
                    decision_ts_utc=str(row["decision_ts_utc"]),
                    target_id=f"{row['base_target_id']}::{expression_id}",
                    target_kind="market_expression",
                    expression_id=str(expression_id),
                    condition_id=row["condition_id"],
                    market_id=row["market_id"],
                    token_id=row["token_id"],
                    bracket=str(row["bracket"]),
                    side="YES",
                    p_model=row["posterior_probability"],
                    market_p=row["market_probability"],
                    executable_cost=row["executable_cost"],
                    model_id=self.model.artifact.model_id,
                    model_artifact_id=self.model.artifact.sha256,
                    feature_set_id=self.model.artifact.feature_set_id,
                    feature_book_snapshot_id=str(row["feature_book_snapshot_id"]),
                    execution_book_snapshot_id=row["execution_book_snapshot_id"],
                    strategy_key=STRATEGY_KEY,
                    policy_id="d1_market_residual_shadow_policy_v1",
                    candidate_status=status,
                    blocker_reason=blocker,
                    selected=selected,
                    market_evidence_status=("available" if not blocker else "coverage_gap"),
                    input_refs=tuple(row["model_output"]["input_refs"]),
                    metadata={
                        "edge": (
                            None if row["executable_cost"] is None or row["posterior_probability"] is None
                            else row["posterior_probability"] - row["executable_cost"]
                        ),
                        "residual_delta": row["residual_delta"],
                        "no_order_placed": True,
                    },
                )
                candidate_row = candidate.to_dict()
                candidate_row["no_order_placed"] = True
                candidates.append(candidate_row)
                row["candidate_id"] = candidate.candidate_id
                row["selected"] = selected
                if selected and self.policy.emit_intents:
                    if not candidate.condition_id or not candidate.token_id:
                        continue
                    intent = TradeIntent.create(
                        candidate_id=candidate.candidate_id,
                        condition_id=candidate.condition_id,
                        token_id=candidate.token_id,
                        side="BUY",
                        requested_size=0.0,
                        sizing_profile=self.policy.sizing_profile,
                        execution_profile=self.policy.execution_profile,
                        max_cost=candidate.executable_cost,
                        ttl_seconds=None,
                        dedupe_key=f"{STRATEGY_KEY}:{candidate.candidate_id}",
                        exposure_bucket=f"weather:{candidate.city}:{candidate.target_date}",
                        mode="zero_notional",
                        metadata={"no_order_placed": True, "venue_calls": 0},
                    )
                    intent_row = intent.to_dict()
                    intent_row["no_order_placed"] = True
                    intents.append(intent_row)
        blockers = [self._blocker(checkpoint, structural_errors)] if structural_errors else []
        return {"predictions": predictions, "candidates": candidates, "intents": intents, "blockers": blockers}

    def run(self, checkpoints: Sequence[Mapping[str, Any]], output_dir: Path) -> dict[str, Any]:
        output = _safe_research_output(output_dir)
        predictions: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        intents: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        for checkpoint in checkpoints:
            result = self.score_checkpoint(checkpoint)
            predictions.extend(result["predictions"])
            candidates.extend(result["candidates"])
            intents.extend(result["intents"])
            blockers.extend(result["blockers"])
        output.mkdir(parents=True, exist_ok=True)
        _write_jsonl(output / "predictions.jsonl", predictions)
        _write_jsonl(output / "signal_candidates.jsonl", candidates)
        _write_jsonl(output / "trade_intents.jsonl", intents)
        _write_jsonl(output / "checkpoint_blockers.jsonl", blockers)
        summary = {
            "schema_version": RUN_SUMMARY_SCHEMA_VERSION,
            "framework_id": FRAMEWORK_ID,
            "strategy_key": STRATEGY_KEY,
            "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "model_id": self.model.artifact.model_id,
            "model_artifact_id": self.model.artifact.sha256,
            "model_artifact_path": str(self.model.artifact.path),
            "policy": asdict(self.policy),
            "checkpoint_rows": len(checkpoints),
            "prediction_rows": len(predictions),
            "scored_rows": sum(row["scorable_status"] == "scorable" for row in predictions),
            "blocked_prediction_rows": sum(row["scorable_status"] != "scorable" for row in predictions),
            "checkpoint_blockers": len(blockers),
            "candidate_rows": len(candidates),
            "selected_candidates": sum(bool(row["selected"]) for row in candidates),
            "intent_rows": len(intents),
            "orders_submitted": 0,
            "venue_calls": 0,
            "no_order_placed": True,
        }
        summary["evidence_hash"] = stable_sha256({
            "predictions": predictions,
            "candidates": candidates,
            "intents": intents,
            "blockers": blockers,
        })
        (output / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            rows.append(value)
    return rows
