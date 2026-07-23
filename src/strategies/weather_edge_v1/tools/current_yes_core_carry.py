"""Frozen current-YES core carry scoring and entry-cost helpers.

The module is intentionally execution-neutral.  It turns one PIT weather/book
state into a model probability and a zero-notional entry decision.  Real order
submission remains a separate, explicitly enabled execution-layer action.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[4]
LEGACY_V1_ARTIFACT = (
    ROOT
    / "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v1.json"
)
DEFAULT_ARTIFACT = (
    ROOT
    / "src/strategies/weather_edge_v1/config/current_yes_core_carry_model_v2.json"
)


def canonical_hash(payload: Mapping[str, Any]) -> str:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_artifact(path: Path | str = DEFAULT_ARTIFACT) -> dict[str, Any]:
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    claimed = str(artifact.get("artifact_hash") or "")
    unsigned = {key: value for key, value in artifact.items() if key != "artifact_hash"}
    actual = canonical_hash(unsigned)
    if not claimed or claimed != actual:
        raise ValueError(f"current-YES core artifact hash mismatch: claimed={claimed!r} actual={actual!r}")
    return artifact


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def official_weather_fee_per_share(price: float) -> float:
    return round(0.05 * price * (1.0 - price), 5)


def market_features(state: Mapping[str, Any]) -> tuple[dict[str, float], list[str]]:
    bid = finite(state.get("current_yes_bid"))
    ask = finite(state.get("current_yes_ask"))
    raw_hour = finite(state.get("decision_hour_local", state.get("decision_hour_local_float")))
    missing: list[str] = []
    if bid is None or ask is None or not (0 < bid <= ask < 1):
        missing.append("valid_two_sided_current_yes_book")
        market_mid = None
    else:
        market_mid = (bid + ask) / 2.0
    values = {
        "market_logit": (
            math.log(market_mid / (1.0 - market_mid)) if market_mid is not None else math.nan
        ),
        # The historical model was trained on the integer local-hour bucket.
        # Fractional minutes belong to checkpoint scheduling, not model input.
        "decision_hour_local": None if raw_hour is None else math.floor(raw_hour),
        "forecast_peak_delta_hours_local": finite(state.get("forecast_peak_delta_hours_local")),
        "dewpoint_depression_f": finite(state.get("dewpoint_depression_f")),
        "wind_speed_kt": finite(state.get("wind_speed_kt")),
        "obs_age_min": finite(state.get("obs_age_min", state.get("obs_age_minutes"))),
    }
    for feature, value in values.items():
        if value is None or not math.isfinite(float(value)):
            values[feature] = math.nan
    return values, missing


def score_probability(features: Mapping[str, Any], artifact: Mapping[str, Any]) -> float:
    names = list(artifact["numeric_features"])
    medians = [float(value) for value in artifact["numeric_medians"]]
    means = [float(value) for value in artifact["numeric_means"]]
    scales = [float(value) for value in artifact["numeric_scales"]]
    coefficients = [float(value) for value in artifact["coef"]]
    if not (len(names) == len(medians) == len(means) == len(scales) == len(coefficients)):
        raise ValueError("invalid current-YES core artifact vector lengths")
    logit = float(artifact["intercept"])
    for index, name in enumerate(names):
        value = finite(features.get(name))
        raw = medians[index] if value is None else value
        scale = scales[index]
        normalized = 0.0 if abs(scale) < 1e-12 else (raw - means[index]) / scale
        logit += coefficients[index] * normalized
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, logit))))


def walk_ask_ladder(
    asks: Sequence[Mapping[str, Any] | Sequence[Any]], quantity: float
) -> dict[str, Any]:
    levels: list[tuple[float, float]] = []
    for raw in asks:
        if isinstance(raw, Mapping):
            price, size = finite(raw.get("price")), finite(raw.get("size"))
        else:
            try:
                price, size = finite(raw[0]), finite(raw[1])
            except (IndexError, TypeError):
                continue
        if price is not None and size is not None and 0 < price < 1 and size > 0:
            levels.append((price, size))
    levels.sort(key=lambda item: item[0])
    remaining = float(quantity)
    principal = 0.0
    fee = 0.0
    max_ask_price = None
    for price, available in levels:
        taken = min(remaining, available)
        principal += taken * price
        fee += taken * official_weather_fee_per_share(price)
        if taken > 0:
            max_ask_price = price
        remaining -= taken
        if remaining <= 1e-9:
            break
    executable = remaining <= 1e-9 and quantity > 0
    return {
        "quantity": float(quantity),
        "executable": executable,
        "unfilled_shares": max(0.0, remaining),
        "principal": principal if executable else None,
        "fee": fee if executable else None,
        "principal_vwap": principal / quantity if executable else None,
        "effective_cost": principal + fee if executable else None,
        "effective_cost_per_share": (principal + fee) / quantity if executable else None,
        "best_ask": levels[0][0] if levels else None,
        "max_ask_price": max_ask_price if executable else None,
        "available_shares": sum(size for _price, size in levels),
    }


def evaluate_entry(
    state: Mapping[str, Any],
    asks: Sequence[Mapping[str, Any] | Sequence[Any]],
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    config = artifact["entry_policy"]
    features, fatal_reasons = market_features(state)
    policy_reasons: list[str] = []
    imputed_features = [
        name
        for name in artifact["numeric_features"]
        if not math.isfinite(float(features.get(name, math.nan)))
    ]
    hour = finite(features.get("decision_hour_local"))
    mid = None
    if math.isfinite(features["market_logit"]):
        mid = 1.0 / (1.0 + math.exp(-features["market_logit"]))
    if hour is None or not float(config["local_hour_start"]) <= hour <= float(config["local_hour_end"]):
        policy_reasons.append("outside_local_hour_window")
    if mid is None or mid < float(config["market_mid_floor"]):
        policy_reasons.append("outside_carry_market_mid_domain")
    if not bool(state.get("checkpoint_eligible", False)):
        policy_reasons.append("checkpoint_not_eligible")
    bracket = str(state.get("current_bracket") or "").strip().lower()
    question = str(state.get("current_question") or "").strip().lower()
    if (
        "+" in bracket
        or "or above" in question
        or "or higher" in question
        or "or below" in question
        or "or lower" in question
        or "or less" in question
    ):
        policy_reasons.append("open_ended_not_exact_bracket")
    ladder = walk_ask_ladder(asks, float(config["taker_shares"]))
    if not ladder["executable"]:
        policy_reasons.append("insufficient_ask_ladder_depth")
    probability = None if fatal_reasons else score_probability(features, artifact)
    effective_cost = ladder.get("effective_cost_per_share")
    edge = (
        None
        if probability is None or effective_cost is None
        else probability - float(effective_cost)
    )
    eligible = (
        edge is not None
        and not policy_reasons
        and edge > float(config["min_edge_after_fee_and_depth"])
    )
    reasons = sorted(set([*fatal_reasons, *policy_reasons]))
    if not reasons and not eligible:
        reasons = ["non_positive_taker_ev"]
    return {
        "model_version": artifact["artifact_version"],
        "artifact_hash": artifact["artifact_hash"],
        "features": features,
        "imputed_features": imputed_features,
        "market_mid": mid,
        "model_probability_hold": probability,
        "taker_ladder": ladder,
        "model_edge_after_fee_and_depth": edge,
        "eligible": eligible,
        "decision_status": "positive_taker_ev" if eligible else "not_eligible",
        "reasons": reasons,
    }
