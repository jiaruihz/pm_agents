"""Frozen Amsterdam next-print -> previous-running-max NO action helpers.

The action target is a short-horizon, direct-token markout.  A next EHAM
routine-print probability is never treated as a Weather Underground settlement
probability.  Every economic value uses the recorded direct NO token, the
5-share executable sweep, and the official Weather taker fee already embedded
in the sweep values.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


SUPPORT = tuple(range(-10, 11))
POLICY_ARMS = ("MR0", "B2", "M1", "M2")
MATERIALIZER_VERSION = "amsterdam_frozen_ws_checkpoint_v1"
ENTRY_CHECKPOINT = "entry_after_p95_latency"
PRIMARY_EXIT_CHECKPOINT = "official_plus_30s"


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_utc(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def minutes_to_expected_routine(entry_at_utc: Any) -> float:
    """Minutes to the next scheduled EHAM :25/:55 routine report clock."""
    timestamp = parse_utc(entry_at_utc)
    candidates = [timestamp.replace(minute=25, second=0, microsecond=0),
                  timestamp.replace(minute=55, second=0, microsecond=0)]
    candidates.append((timestamp + timedelta(hours=1)).replace(minute=25, second=0, microsecond=0))
    future = min(item for item in candidates if item > timestamp)
    return (future - timestamp).total_seconds() / 60.0


def next_official_break_probability(prediction: Mapping[str, Any], model: str) -> float:
    """P(next EHAM routine value exceeds the official running maximum)."""
    if model not in {"B2", "M1", "M2"}:
        raise ValueError(f"unsupported next-print model: {model}")
    feature = prediction.get("feature_vector") or {}
    threshold = float(feature["official_running_max"]) - float(feature["last_official_native_value"])
    pmf = [float(value) for value in prediction[f"{model}_PMF"]]
    if len(pmf) != len(SUPPORT) or not math.isclose(sum(pmf), 1.0, abs_tol=1e-9):
        raise ValueError(f"invalid {model} PMF")
    return sum(probability for tick, probability in zip(SUPPORT, pmf) if tick > threshold)


def resolved_no_identity(identity_rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    identities: dict[tuple[str, str], dict[str, Any]] = {}
    for row in identity_rows:
        if row.get("status") != "resolved":
            continue
        for item in row.get("identities") or ():
            if str(item.get("side") or "").upper() != "NO":
                continue
            condition_id, token_id = str(item.get("condition_id") or ""), str(item.get("token_id") or "")
            if not condition_id or not token_id:
                continue
            identities[(condition_id, token_id)] = dict(item)
    if not identities:
        return None, "EXACT_PREVIOUS_MAX_NO_IDENTITY_MISSING"
    if len(identities) != 1:
        return None, "AMBIGUOUS_PREVIOUS_MAX_NO_IDENTITY"
    return next(iter(identities.values())), None


def previous_max_identity_reason(
    prediction: Mapping[str, Any], identity: Mapping[str, Any]
) -> str | None:
    lineage = prediction.get("raw_source_lineage") or {}
    target_date = str(lineage.get("target_date") or "")
    running_max = (prediction.get("feature_vector") or {}).get("official_running_max")
    if not target_date or running_max is None:
        return "PREVIOUS_MAX_IDENTITY_CONTRACT_INPUT_MISSING"
    if str(identity.get("side") or "").upper() != "NO":
        return "PREVIOUS_MAX_IDENTITY_SIDE_MISMATCH"
    if str(identity.get("target_date") or "") != target_date:
        return "PREVIOUS_MAX_IDENTITY_TARGET_DATE_MISMATCH"
    try:
        expected_bracket = int(round(float(running_max)))
        actual_bracket = int(round(float(identity.get("native_bracket"))))
    except (TypeError, ValueError):
        return "PREVIOUS_MAX_IDENTITY_BRACKET_INVALID"
    if actual_bracket != expected_bracket:
        return "PREVIOUS_MAX_IDENTITY_BRACKET_MISMATCH"
    return None


def checkpoint_row(
    markout_rows: Sequence[Mapping[str, Any]], *, token_id: str, checkpoint: str
) -> tuple[dict[str, Any] | None, str | None]:
    matches = [
        dict(row)
        for row in markout_rows
        if str(row.get("token_id") or "") == str(token_id)
        and row.get("materializer_version") == MATERIALIZER_VERSION
        and row.get("checkpoint") == checkpoint
    ]
    if not matches:
        return None, f"{checkpoint.upper()}_NOT_MATERIALIZED"
    if len(matches) > 1:
        first = canonical_hash(matches[0])
        if any(canonical_hash(row) != first for row in matches[1:]):
            return None, f"AMBIGUOUS_{checkpoint.upper()}_BOOK"
    matches.sort(key=lambda row: (str(row.get("checkpoint_at_utc") or ""), str(row.get("markout_row_id") or "")))
    return matches[0], None


def entry_feature_row(entry: Mapping[str, Any], *, shares: int = 5) -> tuple[dict[str, float] | None, str | None]:
    if entry.get("book_valid") is not True:
        return None, "ENTRY_BOOK_INVALID"
    if entry.get("primary_status") != "VALID_TWO_SIDED_DEPTH":
        return None, f"ENTRY_{str(entry.get('primary_status') or 'UNKNOWN')}"
    ask, bid = entry.get("best_ask"), entry.get("best_bid")
    sweep = (entry.get("sweeps") or {}).get(f"buy_{shares}") or {}
    if ask is None or bid is None:
        return None, "ENTRY_BBO_MISSING"
    if sweep.get("fully_executable") is not True or sweep.get("effective_value_usd") is None:
        return None, f"ENTRY_INSUFFICIENT_{shares}_SHARE_DEPTH"
    return {
        "entry_effective_cost_per_share": float(sweep["effective_value_usd"]) / float(shares),
        "entry_spread": float(ask) - float(bid),
        "minutes_to_expected_routine": minutes_to_expected_routine(entry["checkpoint_at_utc"]),
    }, None


def exit_value_per_share(exit_row: Mapping[str, Any], *, shares: int = 5) -> tuple[float | None, str | None]:
    if exit_row.get("book_valid") is not True:
        return None, "EXIT_BOOK_INVALID"
    if exit_row.get("primary_status") != "VALID_TWO_SIDED_DEPTH":
        return None, f"EXIT_{str(exit_row.get('primary_status') or 'UNKNOWN')}"
    sweep = (exit_row.get("sweeps") or {}).get(f"sell_{shares}") or {}
    if sweep.get("fully_executable") is not True or sweep.get("effective_value_usd") is None:
        return None, f"EXIT_INSUFFICIENT_{shares}_SHARE_DEPTH"
    return float(sweep["effective_value_usd"]) / float(shares), None


def model_features(
    entry_features: Mapping[str, float], prediction: Mapping[str, Any], arm: str
) -> dict[str, float]:
    values = dict(entry_features)
    if arm != "MR0":
        values[f"{arm.lower()}_p_next_break"] = next_official_break_probability(prediction, arm)
    return values


def predict_markout_per_share(
    artifact: Mapping[str, Any], arm: str, features: Mapping[str, float]
) -> float:
    model = (artifact.get("models") or {}).get(arm)
    if not isinstance(model, Mapping):
        raise ValueError(f"reaction artifact missing arm {arm}")
    names = list(model.get("feature_names") or ())
    means = list(model.get("feature_means") or ())
    scales = list(model.get("feature_scales") or ())
    coefficients = list(model.get("coefficients") or ())
    if not names or not (len(names) == len(means) == len(scales) == len(coefficients)):
        raise ValueError(f"invalid reaction artifact arm {arm}")
    total = float(model["intercept"])
    for name, mean, scale, coefficient in zip(names, means, scales, coefficients):
        value = float(features[name])
        denominator = float(scale)
        if not math.isfinite(value) or not math.isfinite(denominator) or denominator <= 0:
            raise ValueError(f"invalid reaction feature {name}")
        total += float(coefficient) * ((value - float(mean)) / denominator)
    return total


def validate_policy_artifact(policy: Mapping[str, Any], artifact: Mapping[str, Any]) -> None:
    if policy.get("schema_version") != "wcir_amsterdam_prevmax_no_action_shadow_v1":
        raise ValueError("unexpected Amsterdam action policy schema")
    if artifact.get("schema_version") != "wcir_amsterdam_prevmax_no_reaction_ridge_v1":
        raise ValueError("unexpected Amsterdam reaction artifact schema")
    if list(policy.get("arms") or ()) != list(POLICY_ARMS):
        raise ValueError("Amsterdam policy arms drift")
    if int(policy.get("selection_shares") or 0) != 5:
        raise ValueError("Amsterdam action shadow is frozen to 5 shares")
    if policy.get("entry_checkpoint") != ENTRY_CHECKPOINT or policy.get("primary_exit_checkpoint") != PRIMARY_EXIT_CHECKPOINT:
        raise ValueError("Amsterdam action checkpoint drift")
    if any(arm not in (artifact.get("models") or {}) for arm in POLICY_ARMS):
        raise ValueError("Amsterdam reaction artifact is incomplete")
    if policy.get("reaction_artifact_id") != artifact.get("artifact_id"):
        raise ValueError("Amsterdam policy/artifact identity mismatch")


def training_observation(joined: Mapping[str, Any], *, shares: int = 5) -> tuple[dict[str, Any] | None, str | None]:
    prediction = joined.get("prediction") or {}
    identity, reason = resolved_no_identity(joined.get("market_identities") or ())
    if identity is None:
        return None, reason
    reason = previous_max_identity_reason(prediction, identity)
    if reason is not None:
        return None, reason
    entry, reason = checkpoint_row(
        joined.get("markouts") or (), token_id=str(identity["token_id"]), checkpoint=ENTRY_CHECKPOINT
    )
    if entry is None:
        return None, reason
    exit_row, reason = checkpoint_row(
        joined.get("markouts") or (), token_id=str(identity["token_id"]), checkpoint=PRIMARY_EXIT_CHECKPOINT
    )
    if exit_row is None:
        return None, reason
    features, reason = entry_feature_row(entry, shares=shares)
    if features is None:
        return None, reason
    exit_value, reason = exit_value_per_share(exit_row, shares=shares)
    if exit_value is None:
        return None, reason
    linked_labels = [row for row in joined.get("labels") or () if row.get("status") == "linked"]
    if len(linked_labels) != 1:
        return None, "UNIQUE_NEXT_PRINT_LABEL_MISSING"
    entry_at = parse_utc(entry["checkpoint_at_utc"])
    available_at = (prediction.get("raw_source_lineage") or {}).get("available_at_utc")
    if not available_at or entry_at < parse_utc(available_at):
        return None, "ENTRY_BEFORE_PREDICTION_AVAILABLE"
    if parse_utc(exit_row["checkpoint_at_utc"]) < entry_at:
        return None, "EXIT_BEFORE_ENTRY"
    official_first_seen = parse_utc(linked_labels[0]["official_first_seen_at_utc"])
    if entry_at >= official_first_seen:
        return None, "ENTRY_NOT_STRICTLY_BEFORE_OFFICIAL_FIRST_SEEN"
    target_date = str((prediction.get("raw_source_lineage") or {}).get("target_date") or "")
    if not target_date:
        return None, "TARGET_DATE_MISSING"
    output: dict[str, Any] = {
        "prediction_row_id": str(joined.get("prediction_row_id") or prediction.get("prediction_row_id") or ""),
        "target_date": target_date,
        "native_bracket": str(identity.get("native_bracket") or ""),
        "entry_checkpoint_at_utc": str(entry["checkpoint_at_utc"]),
        "entry_markout_row_id": str(entry.get("markout_row_id") or ""),
        "exit_markout_row_id": str(exit_row.get("markout_row_id") or ""),
        "opportunity_matched": "NOT_OPPORTUNITY_MATCHED" not in (prediction.get("abstain_reasons") or ()),
        "realized_markout_per_share": exit_value - features["entry_effective_cost_per_share"],
        **features,
    }
    for arm in ("B2", "M1", "M2"):
        output[f"{arm.lower()}_p_next_break"] = next_official_break_probability(prediction, arm)
    return output, None


def action_candidates(
    joined: Mapping[str, Any], policy: Mapping[str, Any], artifact: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Build PIT candidates without consulting a future label or settlement."""
    prediction = joined.get("prediction") or {}
    lineage = prediction.get("raw_source_lineage") or {}
    available_at = lineage.get("available_at_utc")
    if not available_at or parse_utc(available_at) < parse_utc(policy["forward_start_utc"]):
        return []
    if "NOT_OPPORTUNITY_MATCHED" in (prediction.get("abstain_reasons") or ()):
        return []
    identity, reason = resolved_no_identity(joined.get("market_identities") or ())
    if identity is None:
        return []
    if previous_max_identity_reason(prediction, identity) is not None:
        return []
    entry, reason = checkpoint_row(
        joined.get("markouts") or (),
        token_id=str(identity["token_id"]),
        checkpoint=str(policy["entry_checkpoint"]),
    )
    if entry is None:
        return []
    if parse_utc(entry["checkpoint_at_utc"]) < parse_utc(available_at):
        return []
    shares = int(policy["selection_shares"])
    entry_features, reason = entry_feature_row(entry, shares=shares)
    if entry_features is None:
        return []
    prediction_id = str(joined.get("prediction_row_id") or prediction.get("prediction_row_id") or "")
    target_date = str(lineage.get("target_date") or "")
    bracket = str(identity.get("native_bracket") or "")
    if not prediction_id or not target_date or not bracket:
        return []
    rows: list[dict[str, Any]] = []
    for arm in policy["arms"]:
        features = model_features(entry_features, prediction, str(arm))
        expected = predict_markout_per_share(artifact, str(arm), features)
        body = {
            "policy_epoch_id": policy["policy_epoch_id"],
            "reaction_artifact_id": artifact["artifact_id"],
            "source_model_epoch_id": policy["source_model_epoch_id"],
            "prediction_row_id": prediction_id,
            "arm": str(arm),
            "action": policy["action"],
            "target_date": target_date,
            "native_bracket": bracket,
            "market_id": str(identity.get("market_id") or ""),
            "condition_id": str(identity.get("condition_id") or ""),
            "token_id": str(identity["token_id"]),
            "side": "NO",
            "entry_markout_row_id": str(entry.get("markout_row_id") or ""),
            "entry_checkpoint_at_utc": str(entry["checkpoint_at_utc"]),
            "selection_shares": shares,
            "entry_effective_cost_per_share": entry_features["entry_effective_cost_per_share"],
            "counterfactual_entry_cost_usd": entry_features["entry_effective_cost_per_share"] * shares,
            "expected_after_fee_markout_per_share": expected,
            "min_expected_markout_per_share": float(policy["min_expected_markout_per_share"]),
            "eligible": expected > float(policy["min_expected_markout_per_share"]),
            "features": features,
            "logical_decision_at_utc": str(entry["checkpoint_at_utc"]),
            "orders": 0,
            "fills": 0,
            "notional": 0,
            "actual_notional": 0.0,
        }
        body["policy_candidate_id"] = canonical_hash({"kind": "policy_candidate", **body})
        rows.append(body)
    return rows


def select_shadow_decisions(
    candidates: Sequence[Mapping[str, Any]],
    existing: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Select the earliest currently materialized positive candidate per arm/key."""
    seen = {
        (str(row["arm"]), str(row["target_date"]), str(row["native_bracket"]))
        for row in existing
        if row.get("policy_epoch_id") == policy["policy_epoch_id"]
    }
    arm_order = {arm: index for index, arm in enumerate(policy["arms"])}
    ordered = sorted(
        (row for row in candidates if row.get("eligible") is True),
        key=lambda row: (
            str(row["entry_checkpoint_at_utc"]),
            str(row["prediction_row_id"]),
            arm_order[str(row["arm"])],
        ),
    )
    output: list[dict[str, Any]] = []
    for candidate in ordered:
        key = (str(candidate["arm"]), str(candidate["target_date"]), str(candidate["native_bracket"]))
        if key in seen:
            continue
        seen.add(key)
        body = {
            "policy_epoch_id": policy["policy_epoch_id"],
            "policy_candidate_id": candidate["policy_candidate_id"],
            "prediction_row_id": candidate["prediction_row_id"],
            "arm": candidate["arm"],
            "action": candidate["action"],
            "target_date": candidate["target_date"],
            "native_bracket": candidate["native_bracket"],
            "condition_id": candidate["condition_id"],
            "token_id": candidate["token_id"],
            "side": "NO",
            "selection_shares": candidate["selection_shares"],
            "entry_markout_row_id": candidate["entry_markout_row_id"],
            "entry_checkpoint_at_utc": candidate["entry_checkpoint_at_utc"],
            "counterfactual_entry_cost_usd": candidate["counterfactual_entry_cost_usd"],
            "expected_after_fee_markout_per_share": candidate["expected_after_fee_markout_per_share"],
            "decision": "SHADOW_BUY_NO",
            "execution_mode": "zero_notional_action_shadow",
            "orders": 0,
            "fills": 0,
            "notional": 0,
            "actual_notional": 0.0,
        }
        body["shadow_decision_id"] = canonical_hash({"kind": "shadow_decision", **body})
        output.append(body)
    return output


def shadow_results(
    decisions: Sequence[Mapping[str, Any]],
    candidates_by_id: Mapping[str, Mapping[str, Any]],
    joined_by_prediction: Mapping[str, Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Materialize fee-adjusted +30s and final settlement counterfactuals."""
    output: list[dict[str, Any]] = []
    for decision in decisions:
        candidate = candidates_by_id.get(str(decision["policy_candidate_id"]))
        joined = joined_by_prediction.get(str(decision["prediction_row_id"]))
        if candidate is None or joined is None:
            continue
        shares = int(decision["selection_shares"])
        cost = float(decision["counterfactual_entry_cost_usd"])
        exit_row, _ = checkpoint_row(
            joined.get("markouts") or (),
            token_id=str(decision["token_id"]),
            checkpoint=str(policy["primary_exit_checkpoint"]),
        )
        if exit_row is not None:
            if parse_utc(exit_row["checkpoint_at_utc"]) < parse_utc(decision["entry_checkpoint_at_utc"]):
                exit_row = None
        if exit_row is not None:
            exit_per_share, _ = exit_value_per_share(exit_row, shares=shares)
            if exit_per_share is not None:
                proceeds = exit_per_share * shares
                body = {
                    "policy_epoch_id": policy["policy_epoch_id"],
                    "shadow_decision_id": decision["shadow_decision_id"],
                    "policy_candidate_id": decision["policy_candidate_id"],
                    "prediction_row_id": decision["prediction_row_id"],
                    "arm": decision["arm"],
                    "target_date": decision["target_date"],
                    "native_bracket": decision["native_bracket"],
                    "token_id": decision["token_id"],
                    "result_type": "OFFICIAL_PLUS_30S_MARKOUT",
                    "exit_markout_row_id": exit_row.get("markout_row_id"),
                    "counterfactual_cost_usd": cost,
                    "counterfactual_proceeds_usd": proceeds,
                    "fee_adjusted_pnl_usd": proceeds - cost,
                    "win": proceeds > cost,
                    "orders": 0,
                    "fills": 0,
                    "notional": 0,
                    "actual_notional": 0.0,
                }
                body["shadow_result_id"] = canonical_hash({"kind": "shadow_result", **body})
                output.append(body)
        linked = [
            row for row in joined.get("settlements") or ()
            if row.get("status") == "linked" and str(row.get("token_id") or "") == str(decision["token_id"])
        ]
        if len(linked) == 1:
            final_price = (linked[0].get("settlement") or {}).get("final_price")
            if final_price is not None:
                proceeds = float(final_price) * shares
                body = {
                    "policy_epoch_id": policy["policy_epoch_id"],
                    "shadow_decision_id": decision["shadow_decision_id"],
                    "policy_candidate_id": decision["policy_candidate_id"],
                    "prediction_row_id": decision["prediction_row_id"],
                    "arm": decision["arm"],
                    "target_date": decision["target_date"],
                    "native_bracket": decision["native_bracket"],
                    "token_id": decision["token_id"],
                    "result_type": "FINAL_SETTLEMENT_SENSITIVITY",
                    "settlement_row_id": linked[0].get("settlement_row_id"),
                    "counterfactual_cost_usd": cost,
                    "counterfactual_proceeds_usd": proceeds,
                    "fee_adjusted_pnl_usd": proceeds - cost,
                    "win": proceeds > cost,
                    "orders": 0,
                    "fills": 0,
                    "notional": 0,
                    "actual_notional": 0.0,
                }
                body["shadow_result_id"] = canonical_hash({"kind": "shadow_result", **body})
                output.append(body)
    return output


def action_health(
    candidates: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for arm in POLICY_ARMS:
        arm_candidates = [row for row in candidates if row.get("arm") == arm]
        arm_decisions = [row for row in decisions if row.get("arm") == arm]
        primary = [
            row for row in results
            if row.get("arm") == arm and row.get("result_type") == "OFFICIAL_PLUS_30S_MARKOUT"
        ]
        settlement = [
            row for row in results
            if row.get("arm") == arm and row.get("result_type") == "FINAL_SETTLEMENT_SENSITIVITY"
        ]
        cost = sum(float(row["counterfactual_cost_usd"]) for row in primary)
        pnl = sum(float(row["fee_adjusted_pnl_usd"]) for row in primary)
        output[arm] = {
            "candidates": len(arm_candidates),
            "positive_edge_candidates": sum(row.get("eligible") is True for row in arm_candidates),
            "signals": len(arm_decisions),
            "signal_target_dates": len({str(row["target_date"]) for row in arm_decisions}),
            "matured_official_plus_30s": len(primary),
            "wins": sum(row.get("win") is True for row in primary),
            "win_rate": (sum(row.get("win") is True for row in primary) / len(primary) if primary else None),
            "counterfactual_cost_usd": cost,
            "fee_adjusted_pnl_usd": pnl,
            "fee_adjusted_roi": (pnl / cost if cost else None),
            "settlement_sensitivity_results": len(settlement),
        }
    return output
