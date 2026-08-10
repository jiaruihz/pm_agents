"""Adapters from deployed/research ladder rows to the shared probability stack.

The adapters are intentionally representation-only.  They do not fit a model,
select an expression, or create a candidate.  A precomputed posterior keeps its
existing probability vector; a source-event update uses the weather
before/after ratio as a full-ladder likelihood correction to a PIT market
prior.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
import math
import re
from typing import Any

from .exact_bracket_probability import (
    ExactBracketDistribution,
    FinalSettlementHeadOutput,
    LadderLogAdjustment,
    SettlementBracket,
    SettlementLadder,
    compose_exact_bracket_probability,
)


ADAPTER_SCHEMA_VERSION = "weather_exact_bracket_adapter_v1"
DEFAULT_PROBABILITY_FLOOR = 1e-9
_NUMBER_RE = re.compile(r"(?<![\d.])-?\d+(?:\.\d+)?")


class ExactBracketAdapterBlocked(ValueError):
    """A structured coverage failure; callers should journal the reason."""

    def __init__(self, reason: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = dict(details or {})


def _normalized(
    values: Sequence[float],
    *,
    field_name: str,
    probability_floor: float = 0.0,
) -> tuple[float, ...]:
    parsed = [float(value) for value in values]
    if not parsed or not all(math.isfinite(value) and value >= 0.0 for value in parsed):
        raise ExactBracketAdapterBlocked(f"invalid_{field_name}")
    if not math.isfinite(probability_floor) or probability_floor < 0.0:
        raise ValueError("probability_floor must be finite and non-negative")
    floored = [max(value, probability_floor) for value in parsed]
    total = sum(floored)
    if total <= 0.0:
        raise ExactBracketAdapterBlocked(f"zero_mass_{field_name}")
    return tuple(value / total for value in floored)


def _bounds_from_rungs(
    rungs: Sequence[Mapping[str, Any]],
    *,
    allow_legacy_outer_tail_inference: bool,
) -> tuple[SettlementBracket, ...]:
    brackets: list[SettlementBracket] = []
    for index, rung in enumerate(rungs):
        label = str(rung.get("bracket") or rung.get("label") or "").strip()
        bracket_id = str(rung.get("condition_id") or rung.get("bracket_id") or label)
        lower = rung.get("lower_native", rung.get("low"))
        upper = rung.get("upper_native", rung.get("high"))
        if "parsed_bracket" in rung:
            parsed = dict(rung.get("parsed_bracket") or {})
            lower = parsed.get("low")
            upper = parsed.get("high")
        if lower is None and upper is None:
            numbers = [float(value) for value in _NUMBER_RE.findall(label)]
            if not numbers:
                raise ExactBracketAdapterBlocked(
                    "unparseable_bracket", details={"label": label}
                )
            if index == 0 and allow_legacy_outer_tail_inference:
                upper = numbers[-1]
            elif index == len(rungs) - 1 and allow_legacy_outer_tail_inference:
                lower = numbers[0]
            elif label.endswith("+"):
                lower = numbers[0]
            elif len(numbers) >= 2:
                lower, upper = numbers[0], numbers[1]
            else:
                lower = upper = numbers[0]
        brackets.append(
            SettlementBracket(
                bracket_id=bracket_id,
                label=label,
                lower_native=None if lower is None else float(lower),
                upper_native=None if upper is None else float(upper),
            )
        )
    return tuple(brackets)


def settlement_ladder_from_rungs(
    *,
    city: str,
    target_date: str,
    settlement_source: str,
    native_unit: str,
    native_step: float,
    rungs: Sequence[Mapping[str, Any]],
    allow_legacy_outer_tail_inference: bool = False,
) -> SettlementLadder:
    """Build and strictly validate a complete settlement-native partition."""

    if len(rungs) < 2:
        raise ExactBracketAdapterBlocked("incomplete_ladder_too_few_rungs")
    try:
        return SettlementLadder(
            city=city,
            target_date=target_date,
            settlement_source=settlement_source,
            native_unit=native_unit,
            native_step=float(native_step),
            brackets=_bounds_from_rungs(
                rungs,
                allow_legacy_outer_tail_inference=allow_legacy_outer_tail_inference,
            ),
        )
    except ValueError as exc:
        raise ExactBracketAdapterBlocked(
            "invalid_settlement_native_ladder", details={"error": str(exc)}
        ) from exc


def _best(levels: Sequence[Mapping[str, Any]], *, side: str) -> float | None:
    prices = []
    for level in levels:
        try:
            price = float(level["price"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(price) and 0.0 <= price <= 1.0:
            prices.append(price)
    if not prices:
        return None
    return max(prices) if side == "bid" else min(prices)


def _row_bid_ask(row: Mapping[str, Any]) -> tuple[float | None, float | None]:
    raw = dict(row.get("raw") or {})
    bid = row.get("best_bid")
    ask = row.get("best_ask")
    try:
        bid_value = float(bid) if bid is not None else _best(raw.get("bids") or (), side="bid")
        ask_value = float(ask) if ask is not None else _best(raw.get("asks") or (), side="ask")
    except (TypeError, ValueError):
        return None, None
    return bid_value, ask_value


def market_prior_from_book_rows(
    ladder: SettlementLadder,
    rows: Sequence[Mapping[str, Any]],
    *,
    source_snapshot_id: str,
    observed_at_utc: str,
    probability_floor: float = DEFAULT_PROBABILITY_FLOOR,
) -> tuple[ExactBracketDistribution, dict[str, Any]]:
    """Derive one normalized YES ladder from direct and complementary books."""

    grouped: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in rows:
        condition_id = str(row.get("condition_id") or "")
        outcome = str(row.get("outcome") or "").lower()
        if condition_id and outcome in {"yes", "no"}:
            grouped.setdefault(condition_id, {})[outcome] = row
    raw_midpoints: list[float] = []
    bounds: list[dict[str, Any]] = []
    one_sided_boundary_count = 0
    for bracket_id in ladder.bracket_ids:
        sides = grouped.get(bracket_id) or {}
        yes_bid, yes_ask = _row_bid_ask(sides.get("yes") or {})
        no_bid, no_ask = _row_bid_ask(sides.get("no") or {})
        lower_candidates = [value for value in (yes_bid, None if no_ask is None else 1.0 - no_ask) if value is not None]
        upper_candidates = [value for value in (yes_ask, None if no_bid is None else 1.0 - no_bid) if value is not None]
        if not lower_candidates and not upper_candidates:
            raise ExactBracketAdapterBlocked(
                "missing_market_probability_evidence",
                details={"bracket_id": bracket_id},
            )
        if not lower_candidates:
            lower_candidates = [0.0]
            one_sided_boundary_count += 1
        if not upper_candidates:
            upper_candidates = [1.0]
            one_sided_boundary_count += 1
        lower, upper = max(lower_candidates), min(upper_candidates)
        if lower > upper + 1e-9:
            raise ExactBracketAdapterBlocked(
                "crossed_market_probability_interval",
                details={"bracket_id": bracket_id, "lower": lower, "upper": upper},
            )
        raw_midpoints.append((lower + upper) / 2.0)
        bounds.append({"bracket_id": bracket_id, "lower": lower, "upper": upper})
    normalized = _normalized(
        raw_midpoints,
        field_name="market_prior",
        probability_floor=probability_floor,
    )
    distribution = ExactBracketDistribution(
        ladder_id=ladder.ladder_id,
        bracket_ids=ladder.bracket_ids,
        probabilities=normalized,
        source_kind="market_prior",
        source_snapshot_id=source_snapshot_id,
        observed_at_utc=observed_at_utc,
    )
    return distribution, {
        "raw_yes_midpoint_sum": sum(raw_midpoints),
        "probability_floor": probability_floor,
        "probability_point_policy": "direct_or_complement_interval_midpoint_v1",
        "one_sided_boundary_count": one_sided_boundary_count,
        "intervals": bounds,
    }


def precomputed_posterior_head(
    *,
    ladder: SettlementLadder,
    market_prior_probabilities: Sequence[float],
    posterior_probabilities: Sequence[float],
    decision_ts_utc: str,
    model_id: str,
    model_snapshot_id: str,
    market_snapshot_id: str,
    weather_path_component_id: str,
    source_basis_component_id: str = "source_basis_identity_unfitted_v1",
    probability_floor: float = DEFAULT_PROBABILITY_FLOOR,
) -> FinalSettlementHeadOutput:
    """Represent an existing market-aware full-ladder posterior without refit."""

    prior_values = _normalized(
        market_prior_probabilities,
        field_name="market_prior",
        probability_floor=probability_floor,
    )
    posterior_values = _normalized(
        posterior_probabilities,
        field_name="posterior",
        probability_floor=probability_floor,
    )
    market_prior = ExactBracketDistribution(
        ladder_id=ladder.ladder_id,
        bracket_ids=ladder.bracket_ids,
        probabilities=prior_values,
        source_kind="market_prior",
        source_snapshot_id=market_snapshot_id,
        observed_at_utc=decision_ts_utc,
    )
    weather = LadderLogAdjustment(
        ladder_id=ladder.ladder_id,
        bracket_ids=ladder.bracket_ids,
        log_weights=tuple(
            math.log(posterior) - math.log(prior)
            for prior, posterior in zip(prior_values, posterior_values, strict=True)
        ),
        component_id=weather_path_component_id,
        observed_at_utc=decision_ts_utc,
        source_snapshot_ids=(model_snapshot_id,),
    )
    basis = LadderLogAdjustment(
        ladder_id=ladder.ladder_id,
        bracket_ids=ladder.bracket_ids,
        log_weights=(0.0,) * len(ladder.bracket_ids),
        component_id=source_basis_component_id,
        observed_at_utc=decision_ts_utc,
    )
    stack = compose_exact_bracket_probability(
        ladder,
        market_prior=market_prior,
        weather_path_residual=weather,
        source_basis_correction=basis,
    )
    return FinalSettlementHeadOutput(
        model_id=model_id,
        decision_ts_utc=decision_ts_utc,
        target_date=ladder.target_date,
        settlement_source=ladder.settlement_source,
        probability_stack=stack,
        model_book_snapshot_id=market_snapshot_id,
    )


def source_event_transport_head(
    *,
    transport: Mapping[str, Any],
    pre_event_snapshot: Mapping[str, Any],
    settlement_source: str,
    native_step: float = 1.0,
    probability_floor: float = DEFAULT_PROBABILITY_FLOOR,
    allow_legacy_outer_tail_inference: bool = False,
) -> tuple[FinalSettlementHeadOutput, dict[str, Any]]:
    """Apply a source-event weather likelihood ratio to its PIT market prior."""

    if transport.get("record_type") != "full_ladder_weather_probability_transport":
        raise ExactBracketAdapterBlocked("unsupported_transport_record_type")
    if pre_event_snapshot.get("capture_status") != "complete":
        raise ExactBracketAdapterBlocked("pre_event_full_ladder_incomplete")
    if str(pre_event_snapshot.get("source_event_id")) != str(transport.get("information_event_id")):
        raise ExactBracketAdapterBlocked("source_event_identity_mismatch")
    rungs = list(transport.get("rungs") or ())
    unit = str(transport.get("settlement_unit") or "").upper()
    ladder = settlement_ladder_from_rungs(
        city=str(transport.get("city") or ""),
        target_date=str(transport.get("target_date") or ""),
        settlement_source=settlement_source,
        native_unit=f"deg{unit}_integer",
        native_step=native_step,
        rungs=rungs,
        allow_legacy_outer_tail_inference=allow_legacy_outer_tail_inference,
    )
    market_snapshot_id = str(
        pre_event_snapshot.get("pre_evaluation_snapshot_id")
        or pre_event_snapshot.get("source_orderbook_archive_path")
        or ""
    )
    observed_at = str(pre_event_snapshot.get("ts_utc") or "")
    if not market_snapshot_id or not observed_at:
        raise ExactBracketAdapterBlocked("pre_event_market_identity_missing")
    market_prior, market_metadata = market_prior_from_book_rows(
        ladder,
        list(pre_event_snapshot.get("records") or ()),
        source_snapshot_id=market_snapshot_id,
        observed_at_utc=observed_at,
        probability_floor=probability_floor,
    )
    before = _normalized(
        [float(row["p_weather_before"]) for row in rungs],
        field_name="weather_before",
        probability_floor=probability_floor,
    )
    after = _normalized(
        [float(row["p_weather_after"]) for row in rungs],
        field_name="weather_after",
        probability_floor=probability_floor,
    )
    decision_ts = str(transport.get("source_event_first_seen_at_utc") or "")
    weather = LadderLogAdjustment(
        ladder_id=ladder.ladder_id,
        bracket_ids=ladder.bracket_ids,
        log_weights=tuple(
            math.log(right) - math.log(left)
            for left, right in zip(before, after, strict=True)
        ),
        component_id=str(
            transport.get("weather_adapter_id")
            or "source_event_weather_transport_v1"
        ),
        observed_at_utc=decision_ts,
        source_snapshot_ids=tuple(
            str(value)
            for value in (
                transport.get("forecast_capture_id"),
                transport.get("information_event_id"),
            )
            if value
        ),
    )
    basis = LadderLogAdjustment(
        ladder_id=ladder.ladder_id,
        bracket_ids=ladder.bracket_ids,
        log_weights=(0.0,) * len(ladder.bracket_ids),
        component_id="source_basis_identity_unfitted_v1",
        observed_at_utc=decision_ts,
    )
    stack = compose_exact_bracket_probability(
        ladder,
        market_prior=market_prior,
        weather_path_residual=weather,
        source_basis_correction=basis,
    )
    head = FinalSettlementHeadOutput(
        model_id="source_event_market_prior_posterior_v1",
        decision_ts_utc=decision_ts,
        target_date=ladder.target_date,
        settlement_source=ladder.settlement_source,
        probability_stack=stack,
        model_book_snapshot_id=market_snapshot_id,
    )
    metadata = {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "source_basis_status": "identity_unfitted",
        "calibration_status": "identity_unfitted",
        "market_prior": market_metadata,
        "weather_probability_floor": probability_floor,
        "legacy_outer_tail_inference": allow_legacy_outer_tail_inference,
    }
    return head, metadata


def final_settlement_head_dict(head: FinalSettlementHeadOutput) -> dict[str, Any]:
    """Stable JSON-ready representation for append-only producer journals."""

    return asdict(head)
