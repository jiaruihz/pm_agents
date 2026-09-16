import json
from pathlib import Path

from scripts.analysis.market_making.research_weather_mm_w2_narrow_maker_v1 import run
from scripts.analysis.market_making.research_weather_mm_w2_narrow_maker_v1 import (
    evaluate,
    join_decision_packet_model_inputs,
    read_jsonl_snapshot,
    selective_maker_route_audit,
    target_date_block_bootstrap,
    zero_notional_audit,
)


PREREG = Path("src/strategies/weather_edge_v1/config/weather_mm_w2_narrow_maker_prereg_v1.json")
PREREG_V21 = Path("src/strategies/weather_edge_v1/config/weather_mm_w2_selective_maker_prereg_v2_1.json")


def row(
    identity: str,
    date: str,
    state: str,
    *,
    actual: bool = False,
    lifecycle: bool = True,
    rebates: bool = False,
) -> dict:
    maker = (
        {
            "fill_status": "actual_fill",
            "authoritative_own_order_lifecycle": True,
            "all_in_cost": "4.00",
            "terminal_value": "4.80",
            "shares": "5",
            "hazard_stratum": "quiet",
            "price_stratum": "mid",
            "depth_stratum": "deep",
        }
        if actual
        else {"fill_status": "quote_touched", "all_in_cost": "4.00", "shares": "5"}
    )
    result = {
        "event_kind": "first_positive", "signal_id": identity, "target_date": date, "city": "Test",
        "candidate_state": state, "declared_notional": 0, "shadow_notional": 0,
        "trade_intent_created": False, "venue_call_allowed": False,
        "coverage_v2": {"private_order_feed_complete": lifecycle},
        "execution_counterfactuals": {
            "shared_passive_maker": maker,
            "immediate_taker": {"all_in_cost": "4.25", "shares": "5"},
            "terminal_1800s": {"current_executable_sell_proceeds_5": "4.75"},
        },
    }
    if rebates:
        result["realized_rebate_ledger"] = {
            "maker_rebate": {
                "realized": True,
                "amount_usd": "0",
                "payout_identity": f"maker:{identity}",
            },
            "taker_rebate": {
                "realized": True,
                "amount_usd": "0",
                "eligibility_identity": f"taker:{identity}",
            },
        }
    return result


def write_input(tmp_path: Path, rows: list[dict]) -> Path:
    source = tmp_path / "input"; source.mkdir()
    (source / "market_state_decisions.jsonl").write_text("".join(json.dumps(item) + "\n" for item in rows))
    (source / "maker_policy_actions.jsonl").write_text(json.dumps({"declared_notional": 0, "trade_intent_created": False, "venue_call_allowed": False}) + "\n")
    return source


def test_quote_touch_is_not_a_maker_fill_and_is_fail_closed(tmp_path: Path) -> None:
    source = write_input(tmp_path, [row("s1", "2026-08-01", "healthy_passive_candidate")])
    report = run(source, tmp_path / "out", PREREG, tmp_path / "missing-reconnect")
    assert report["promotion_verdict"] == "FAIL_CLOSED"
    assert report["coverage"]["evidence_complete_rows"] == 0
    assert "actual_maker_own_order_lineage_missing" in report["gate"]["blockers"]
    assert (tmp_path / "out" / "evidence.csv").exists()


def test_alias_maps_transient_regime_and_fixed_denominator_keeps_unselected(tmp_path: Path) -> None:
    source = write_input(tmp_path, [row("s1", "2026-08-01", "transient_dislocation"), row("s2", "2026-08-02", "unknown")])
    report = run(source, tmp_path / "out", PREREG, tmp_path / "missing-reconnect")
    assert report["denominator"]["unique_rows"] == 2
    assert report["narrow_regime_observed"]["selected_rows"] == 1
    assert report["narrow_regime_observed"]["state_counts"]["transient_dislocation_candidate"] == 1
    assert report["coverage"]["producer_private_feed_flag_coverage_in_full_denominator"] == "1"


def test_nonzero_or_intent_side_effect_fails_zero_notional_audit(tmp_path: Path) -> None:
    source = write_input(tmp_path, [row("s1", "2026-08-01", "healthy_passive_candidate")])
    payload = json.loads((source / "maker_policy_actions.jsonl").read_text())
    payload["declared_notional"] = 1
    (source / "maker_policy_actions.jsonl").write_text(json.dumps(payload) + "\n")
    report = run(source, tmp_path / "out", PREREG, tmp_path / "missing-reconnect")
    assert report["zero_notional_audit"]["pass"] is False
    assert "zero_notional_or_no_side_effect_contract_failed" in report["gate"]["blockers"]


def test_actual_maker_requires_rebate_ledger_before_promotion(tmp_path: Path) -> None:
    source = write_input(tmp_path, [row("s1", "2026-08-01", "healthy_passive_candidate", actual=True)])
    report = run(source, tmp_path / "out", PREREG, tmp_path / "missing-reconnect")
    assert report["coverage"]["evidence_complete_rows"] == 0
    assert "realized_maker_rebate_ledger_missing_or_incomplete" in report["gate"]["blockers"]


def test_actual_maker_with_realized_zero_rebates_computes_per_share_delta(
    tmp_path: Path,
) -> None:
    source = write_input(
        tmp_path,
        [row("s1", "2026-08-01", "healthy_passive_candidate", actual=True, rebates=True)],
    )
    report = run(source, tmp_path / "out", PREREG, tmp_path / "missing-reconnect")
    assert report["coverage"]["evidence_complete_rows"] == 1
    assert report["same_denominator_baselines"][
        "complete_maker_minus_taker_usd_per_share"
    ] == ["0.06"]
    assert report["inference"]["target_date_block_bootstrap"][
        "point_estimate_usd_per_share"
    ] == "0.06"


def test_target_date_bootstrap_is_deterministic_and_decimal() -> None:
    prereg = json.loads(PREREG.read_text())
    evaluated = [
        evaluate(
            row(f"s{index}", date, "healthy_passive_candidate", actual=True, rebates=True),
            prereg,
        )
        for index, date in enumerate(("2026-08-01", "2026-08-02"), 1)
    ]
    first = target_date_block_bootstrap(evaluated, repetitions=100, seed=7)
    second = target_date_block_bootstrap(evaluated, repetitions=100, seed=7)
    assert first == second
    assert first["point_estimate_usd_per_share"] == "0.06"
    assert first["ci95_usd_per_share"] == ["0.06", "0.06"]


def test_target_date_bootstrap_weights_dates_equally_not_shares() -> None:
    prereg = json.loads(PREREG.read_text())
    first = row("s1", "2026-08-01", "healthy_passive_candidate", actual=True, rebates=True)
    first_maker = first["execution_counterfactuals"]["shared_passive_maker"]
    first_taker = first["execution_counterfactuals"]["immediate_taker"]
    first_maker.update({"shares": "1", "all_in_cost": "0.8", "terminal_value": "1"})
    first_taker.update({"shares": "1", "all_in_cost": "0.9", "terminal_value": "1"})
    second = row("s2", "2026-08-02", "healthy_passive_candidate", actual=True, rebates=True)
    second_maker = second["execution_counterfactuals"]["shared_passive_maker"]
    second_taker = second["execution_counterfactuals"]["immediate_taker"]
    second_maker.update({"shares": "100", "all_in_cost": "70", "terminal_value": "100"})
    second_taker.update({"shares": "100", "all_in_cost": "90", "terminal_value": "100"})
    result = target_date_block_bootstrap(
        [evaluate(first, prereg), evaluate(second, prereg)],
        repetitions=100,
        seed=7,
    )
    assert result["point_estimate_usd_per_share"] == "0.15"


def test_non_five_share_row_cannot_reuse_five_share_terminal_sweep() -> None:
    prereg = json.loads(PREREG.read_text())
    payload = row(
        "s1",
        "2026-08-01",
        "healthy_passive_candidate",
        actual=True,
        rebates=True,
    )
    payload["execution_counterfactuals"]["shared_passive_maker"]["shares"] = "10"
    payload["execution_counterfactuals"]["immediate_taker"]["shares"] = "10"
    evaluated = evaluate(payload, prereg)
    assert evaluated.evidence_complete is False
    assert "same_row_immediate_taker_baseline_missing" in evaluated.blockers


def test_latest_append_revision_preserves_late_actual_fill_evidence(
    tmp_path: Path,
) -> None:
    source = write_input(
        tmp_path,
        [
            row("s1", "2026-08-01", "healthy_passive_candidate"),
            row(
                "s1",
                "2026-08-01",
                "healthy_passive_candidate",
                actual=True,
                rebates=True,
            ),
        ],
    )
    report = run(source, tmp_path / "out", PREREG, tmp_path / "missing-reconnect")
    assert report["denominator"]["unique_rows"] == 1
    assert report["denominator"]["revised_identities"] == 1
    assert report["coverage"]["evidence_complete_rows"] == 1
    assert len(report["denominator"]["revision_lineage_sha256"]["s1"]) == 2


def test_zero_notional_requires_explicit_fields_and_snapshot_hashes_prefix(
    tmp_path: Path,
) -> None:
    audit = zero_notional_audit(
        [{"declared_notional": 0, "trade_intent_created": False}], []
    )
    assert audit["pass"] is False
    assert any("venue_call_allowed_missing" in item for item in audit["failures"])
    source = tmp_path / "events.jsonl"
    raw = json.dumps({"event_kind": "first_positive"}) + "\n"
    source.write_text(raw)
    rows, identity = read_jsonl_snapshot(source)
    assert len(rows) == 1
    assert identity["bytes_consumed"] == len(raw.encode())
    assert identity["object_rows_consumed"] == 1


def test_v21_causal_t30_state_and_non_circular_stage_gates(tmp_path: Path) -> None:
    payload = row("s1", "2026-08-31", "unknown")
    payload["maker_policy_v3"] = {
        "t30": {
            "stage": "t30",
            "action": "place_probe_maker",
            "reason_codes": ["post_trigger_acute_window_admitted_probe"],
            "information_horizon_seconds": 30,
            "requested_shares": 1,
            "limit_price": "0.40",
        }
    }
    payload["execution_counterfactuals"]["same_stage_taker"] = {
        "all_in_cost": "0.43",
        "shares": 1,
    }
    payload["execution_counterfactuals"]["skip_incremental"] = {
        "all_in_cost": 0,
        "shares": 0,
    }
    source = write_input(tmp_path, [payload])
    report = run(
        source,
        tmp_path / "out",
        PREREG_V21,
        tmp_path / "missing-reconnect",
    )
    assert report["narrow_regime_observed"]["selected_rows"] == 1
    assert report["narrow_regime_observed"]["state_source_counts"] == {
        "causal_policy_t30": 1
    }
    readiness = report["stage_gates"]["zero_notional_readiness"]
    assert readiness["pass"] is True
    assert readiness["same_stage_comparison_ready_rows"] == 1
    measurement = report["stage_gates"]["micro_live_measurement"]
    assert measurement["requires_actual_fills_before_entry"] is False
    assert "fewer_than_20_actual_maker_fills" not in measurement["blockers"]
    assert report["stage_gates"]["frozen_forward_profitability"][
        "first_look_actual_fill_floor"
    ] == 20


def test_v21_keeps_causal_candidate_but_blocks_mismatched_same_stage_baseline(
    tmp_path: Path,
) -> None:
    payload = row("s1", "2026-08-31", "unknown")
    payload["maker_policy_v3"] = {
        "t30": {
            "stage": "t30",
            "action": "place_probe_maker",
            "reason_codes": ["post_trigger_acute_window_admitted_probe"],
            "information_horizon_seconds": 30,
            "requested_shares": 1,
            "limit_price": "0.40",
        }
    }
    payload["execution_counterfactuals"]["same_stage_taker"] = {
        "all_in_cost": "2.15",
        "shares": 5,
    }
    payload["execution_counterfactuals"]["skip_incremental"] = {
        "all_in_cost": 0,
        "shares": 0,
    }
    source = write_input(tmp_path, [payload])
    report = run(
        source,
        tmp_path / "out",
        PREREG_V21,
        tmp_path / "missing-reconnect",
    )
    assert report["narrow_regime_observed"]["selected_rows"] == 1
    readiness = report["stage_gates"]["zero_notional_readiness"]
    assert readiness["pass"] is False
    assert readiness["same_stage_comparison_blocker_counts"] == {
        "same_stage_maker_taker_shares_mismatch": 1
    }


def test_v21_joins_frozen_model_input_and_routes_maker_only_in_sensitivity(
    tmp_path: Path,
) -> None:
    payload = row("s1", "2026-08-31", "transient_dislocation_candidate")
    payload["execution_counterfactuals"]["shared_passive_maker"]["token_id"] = "yes"
    payload["maker_policy_v3"] = {
        "t30": {
            "action": "place_probe_maker",
            "reason_codes": ["post_trigger_acute_window_admitted_probe"],
            "information_horizon_seconds": 30,
            "requested_shares": 1,
            "limit_price": "0.40",
        }
    }
    payload["execution_counterfactuals"]["t30_immediate_taker"] = {
        "token_id": "yes",
        "shares": "1",
        "modeled_taker_fee": "0.01",
        "all_in_cost": "0.44",
        "market_book_reconstruction": {
            "status": "ok",
            "fetched_at_utc": "2026-08-30T00:00:30Z",
            "venue_timestamp_utc": None,
            "book_epoch_ref": "book-1",
            "tick_size": "0.01",
            "tick_size_source": "fixture",
            "book_age_sec_at_route": "0",
            "bids": [{"price": "0.40", "size": "10"}],
            "asks": [{"price": "0.43", "size": "10"}],
        },
    }
    packet_path = tmp_path / "decision_packets.jsonl"
    packet_path.write_text(
        json.dumps(
            {
                "packet_id": "packet-1",
                "signal": {"signal_id": "s1", "token_id": "yes"},
                "trigger": {
                    "payload": {
                        "model_probability_hold": 0.60,
                        "probability_status": "scored_by_current_yes_core_artifact",
                        "model_version": "model-v1",
                        "artifact_hash": "artifact-1",
                        "current_yes_tick_size": 0.01,
                    }
                },
                "strategy_identity": {"strategy_id": "fixture"},
            }
        )
        + "\n"
    )
    joined, evidence = join_decision_packet_model_inputs([payload], packet_path)
    assert evidence["matched_rows"] == 1
    assert joined[0]["v2_1_model_input"]["model_probability_hold"] == 0.60

    prereg = json.loads(PREREG_V21.read_text())
    audit, detail = selective_maker_route_audit(joined, prereg)
    assert audit["route_input_ready_rows"] == 1
    assert audit["observed_route_counts"] == {"taker": 1}
    assert audit["observed_profit_route_maker_rows"] == 0
    assert audit["sensitivity_is_promotion_evidence"] is False
    assert audit["hypothetical_scenario_route_counts"][
        "hazard=0|fill_lower=1"
    ] == {"maker": 1}
    assert len(detail) == 17

    mismatched = json.loads(json.dumps(joined[0]))
    mismatched["maker_policy_v3"]["t30"]["requested_shares"] = 2
    mismatch_audit, _ = selective_maker_route_audit([mismatched], prereg)
    assert mismatch_audit["route_input_ready_rows"] == 0
    assert mismatch_audit["observed_blocker_counts"] == {
        "same_stage_maker_taker_shares_mismatch": 1
    }


def test_v21_decision_packet_revision_conflict_fails_closed(tmp_path: Path) -> None:
    payload = row("s1", "2026-08-31", "transient_dislocation_candidate")
    payload["execution_counterfactuals"]["shared_passive_maker"]["token_id"] = "yes"
    base_packet = {
        "packet_id": "packet-1",
        "signal": {
            "signal_id": "s1",
            "token_id": "yes",
            "target_date": "2026-08-31",
        },
        "trigger": {
            "status": "scored",
            "payload": {
                "model_probability_hold": 0.60,
                "probability_status": "scored_by_current_yes_core_artifact",
                "model_version": "model-v1",
                "artifact_hash": "artifact-1",
                "feature_schema_version": "feature-v1",
                "current_yes_tick_size": 0.01,
            },
        },
        "strategy_identity": {"strategy_id": "fixture"},
    }
    changed = json.loads(json.dumps(base_packet))
    changed["packet_id"] = "packet-2"
    changed["trigger"]["payload"]["model_probability_hold"] = 0.70
    packet_path = tmp_path / "decision_packets.jsonl"
    packet_path.write_text(
        json.dumps(base_packet) + "\n" + json.dumps(changed) + "\n"
    )
    joined, evidence = join_decision_packet_model_inputs([payload], packet_path)
    assert evidence["revised_identities"] == 1
    assert evidence["revision_invariant_conflicts"] == ["s1"]
    assert evidence["identity_conflicts"] == ["s1"]
    assert evidence["matched_rows"] == 0
    assert "v2_1_model_input" not in joined[0]
