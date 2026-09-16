from __future__ import annotations

import math

import pytest

from weather_model_evaluation.exact_bracket_adapters import (
    ExactBracketAdapterBlocked,
    precomputed_posterior_head,
    settlement_ladder_from_rungs,
    source_event_transport_head,
)


def _rungs() -> list[dict]:
    return [
        {"bracket": "21", "condition_id": "c21", "lower_native": None, "upper_native": 21},
        {"bracket": "22", "condition_id": "c22", "lower_native": 22, "upper_native": 22},
        {"bracket": "23+", "condition_id": "c23", "lower_native": 23, "upper_native": None},
    ]


def _book_rows() -> list[dict]:
    probabilities = {"c21": 0.2, "c22": 0.5, "c23": 0.3}
    rows = []
    for condition, probability in probabilities.items():
        rows.extend(
            [
                {
                    "condition_id": condition,
                    "outcome": "yes",
                    "raw": {
                        "bids": [{"price": probability - 0.01}],
                        "asks": [{"price": probability + 0.01}],
                    },
                },
                {
                    "condition_id": condition,
                    "outcome": "no",
                    "raw": {
                        "bids": [{"price": 1.0 - probability - 0.01}],
                        "asks": [{"price": 1.0 - probability + 0.01}],
                    },
                },
            ]
        )
    return rows


def test_source_event_transport_uses_market_prior_and_conserves_mass() -> None:
    transport = {
        "record_type": "full_ladder_weather_probability_transport",
        "city": "Helsinki",
        "target_date": "2026-08-10",
        "settlement_unit": "C",
        "information_event_id": "event-1",
        "source_event_first_seen_at_utc": "2026-08-10T10:00:01Z",
        "weather_adapter_id": "weather-path-v1",
        "forecast_capture_id": "forecast-1",
        "rungs": [
            _rungs()[0] | {"p_weather_before": 0.30, "p_weather_after": 0.10},
            _rungs()[1] | {"p_weather_before": 0.50, "p_weather_after": 0.40},
            _rungs()[2] | {"p_weather_before": 0.20, "p_weather_after": 0.50},
        ],
    }
    snapshot = {
        "capture_status": "complete",
        "source_event_id": "event-1",
        "pre_evaluation_snapshot_id": "book-pre-1",
        "ts_utc": "2026-08-10T10:00:00Z",
        "records": _book_rows(),
    }

    head, metadata = source_event_transport_head(
        transport=transport,
        pre_event_snapshot=snapshot,
        settlement_source="WU:EFHK",
    )

    stack = head.probability_stack
    assert stack.market_prior.probabilities == pytest.approx((0.2, 0.5, 0.3))
    assert stack.final_distribution.probabilities == pytest.approx(
        (0.0547945205, 0.3287671233, 0.6164383562)
    )
    assert math.isclose(sum(stack.final_distribution.probabilities), 1.0)
    assert metadata["source_basis_status"] == "identity_unfitted"
    assert head.model_book_snapshot_id == "book-pre-1"


def test_deployed_legacy_labels_need_explicit_opt_in_for_outer_tail_inference() -> None:
    rungs = [
        {"bracket": "21", "condition_id": "c21"},
        {"bracket": "22", "condition_id": "c22"},
        {"bracket": "23+", "condition_id": "c23"},
    ]
    with pytest.raises(ExactBracketAdapterBlocked):
        settlement_ladder_from_rungs(
            city="Helsinki",
            target_date="2026-08-10",
            settlement_source="WU:EFHK",
            native_unit="degC_integer",
            native_step=1,
            rungs=rungs,
        )
    ladder = settlement_ladder_from_rungs(
        city="Helsinki",
        target_date="2026-08-10",
        settlement_source="WU:EFHK",
        native_unit="degC_integer",
        native_step=1,
        rungs=rungs,
        allow_legacy_outer_tail_inference=True,
    )
    assert ladder.brackets[0].lower_native is None
    assert ladder.brackets[-1].upper_native is None


def test_precomputed_d1_market_posterior_is_exactly_preserved() -> None:
    ladder = settlement_ladder_from_rungs(
        city="Tokyo",
        target_date="2026-08-10",
        settlement_source="canonical_settlement_outcome",
        native_unit="degC_integer",
        native_step=1,
        rungs=_rungs(),
    )
    posterior = (0.10, 0.35, 0.55)
    head = precomputed_posterior_head(
        ladder=ladder,
        market_prior_probabilities=(0.20, 0.50, 0.30),
        posterior_probabilities=posterior,
        decision_ts_utc="2026-08-10T00:00:00Z",
        model_id="d1-market-residual-v1",
        model_snapshot_id="d1-state-1",
        market_snapshot_id="d1-book-1",
        weather_path_component_id="d1-residual-v1",
    )
    assert head.probability_stack.final_distribution.probabilities == pytest.approx(posterior)
    assert head.probability_stack.market_prior.probabilities == pytest.approx((0.2, 0.5, 0.3))
    assert head.probability_stack.source_basis_component_id == "source_basis_identity_unfitted_v1"


def test_missing_market_evidence_is_a_coverage_blocker() -> None:
    transport = {
        "record_type": "full_ladder_weather_probability_transport",
        "city": "Helsinki",
        "target_date": "2026-08-10",
        "settlement_unit": "C",
        "information_event_id": "event-1",
        "source_event_first_seen_at_utc": "2026-08-10T10:00:01Z",
        "rungs": [rung | {"p_weather_before": 1 / 3, "p_weather_after": 1 / 3} for rung in _rungs()],
    }
    snapshot = {
        "capture_status": "complete",
        "source_event_id": "event-1",
        "pre_evaluation_snapshot_id": "book-pre-1",
        "ts_utc": "2026-08-10T10:00:00Z",
        "records": _book_rows()[:1],
    }
    with pytest.raises(ExactBracketAdapterBlocked) as exc:
        source_event_transport_head(
            transport=transport,
            pre_event_snapshot=snapshot,
            settlement_source="WU:EFHK",
        )
    assert exc.value.reason == "missing_market_probability_evidence"


def test_one_sided_market_book_uses_explicit_probability_boundary() -> None:
    transport = {
        "record_type": "full_ladder_weather_probability_transport",
        "city": "Helsinki",
        "target_date": "2026-08-10",
        "settlement_unit": "C",
        "information_event_id": "event-1",
        "source_event_first_seen_at_utc": "2026-08-10T10:00:01Z",
        "rungs": [
            rung | {"p_weather_before": 1 / 3, "p_weather_after": 1 / 3}
            for rung in _rungs()
        ],
    }
    rows = _book_rows()
    rows[0]["raw"]["bids"] = []
    rows[1]["raw"]["asks"] = []
    snapshot = {
        "capture_status": "complete",
        "source_event_id": "event-1",
        "pre_evaluation_snapshot_id": "book-pre-1",
        "ts_utc": "2026-08-10T10:00:00Z",
        "records": rows,
    }

    head, metadata = source_event_transport_head(
        transport=transport,
        pre_event_snapshot=snapshot,
        settlement_source="WU:EFHK",
    )

    assert metadata["market_prior"]["one_sided_boundary_count"] == 1
    assert sum(head.probability_stack.market_prior.probabilities) == pytest.approx(1.0)
