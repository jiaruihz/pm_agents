#!/usr/bin/env python3
"""Build the WCIR Stage 2/3 full evidence seal v2 from immutable evidence only.

The builder does not read mutable runtime state, use the network, train a model,
or change production.  It adds the causal-entry, latency-provenance, and layered
denominator evidence requested by the independent Stage 2/3 rev2 review.
"""

from __future__ import annotations

import argparse
import collections
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.wcir_stage23_rev2_closure import (
    CLOSURE as PRIOR_CLOSURE,
    REVIEW_ROOT,
    STAGE2,
    STAGE3,
    _feasible,
    build_reconciliation,
    build_validity_corrigendum,
    identity,
    read_json,
    read_jsonl_gz,
    verify_legacy_manifest,
    write_json,
    write_jsonl_gz,
)


OUTPUT = REVIEW_ROOT / "stage_02_03_rev2_full_seal_v2"
EVENTS = REVIEW_ROOT / "stage_03/evidence/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz"
LEGACY_LATENCY_POLICY_ID = "wcir_legacy_global_pooled_p95_49p773_v1"
LEGACY_LATENCY_SLO_SECONDS = 49.772999999999996
POLICIES = (
    "CROSS_ONLY_SEMANTIC_ORACLE",
    "PRIOR_CURRENT_EXACT_NO_ON_UPWARD_CROSS",
    "ACTUAL_NEXT_PRINT_BRACKET_YES",
    "LOWER_IMPOSSIBLE_BRACKETS_NO_ON_MULTI_TICK_CROSS",
    "CURRENT_BRACKET_YES_NO_NEW_MAX_DIAGNOSTIC",
    "NO_TRADE",
)
SIZES = (1, 5)
HORIZONS = (5, 15, 30, 60, 120)


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def ts_text(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def causal_entry_gate(event: Mapping[str, Any], oracle: Mapping[str, Any]) -> dict[str, Any]:
    source = parse_ts(event.get("source_detect_ts_utc") or oracle.get("source_first_seen_at_utc"))
    official = parse_ts(event.get("official_first_seen_at_utc") or oracle.get("official_first_seen_at_utc"))
    raw_slo = oracle.get("execution_latency_seconds")
    try:
        slo = float(raw_slo) if raw_slo is not None else None
    except (TypeError, ValueError):
        slo = None
    ready = source + timedelta(seconds=slo) if source is not None and slo is not None and math.isfinite(slo) and slo >= 0 else None
    lead = (official - ready).total_seconds() if official is not None and ready is not None else None
    eligible = bool(ready is not None and official is not None and ready < official and lead is not None and lead > 0)
    if source is None:
        reason = "MISSING_OR_UNCOMPARABLE_SOURCE_CLOCK"
    elif official is None:
        reason = "MISSING_OR_UNCOMPARABLE_OFFICIAL_FIRST_SEEN_CLOCK"
    elif ready is None:
        reason = "MISSING_OR_INVALID_LATENCY_SLO"
    elif ready == official:
        reason = "ENTRY_EQUALS_OFFICIAL_FIRST_SEEN_FAIL_CLOSED"
    elif ready > official or lead is None or lead <= 0:
        reason = "ENTRY_AFTER_OFFICIAL_FIRST_SEEN_FAIL_CLOSED"
    else:
        reason = None
    frozen_lead = oracle.get("effective_lead_seconds")
    if lead is not None and frozen_lead is not None and abs(lead - float(frozen_lead)) > 1e-6:
        raise RuntimeError(f"effective lead drift for {event.get('event_id')}: {lead} != {frozen_lead}")
    return {
        "effective_entry_decision_ready_ts_utc": ts_text(ready),
        "official_first_seen_ts_utc": ts_text(official),
        "effective_lead_seconds": lead,
        "causal_entry_eligible": eligible,
        "causal_exclusion_reason": reason,
        "strict_inequality_contract": "effective_entry_decision_ready_ts_utc < official_first_seen_ts_utc AND effective_lead_seconds > 0",
        "entry_equal_official_first_seen_fails_closed": True,
        "missing_or_uncomparable_clock_fails_closed": True,
    }


def entry_checkpoint_gate(entry_row: Mapping[str, Any], causal_gate: Mapping[str, Any]) -> dict[str, Any]:
    checkpoint = parse_ts(entry_row.get("checkpoint_at_utc"))
    ready = parse_ts(causal_gate.get("effective_entry_decision_ready_ts_utc"))
    official = parse_ts(causal_gate.get("official_first_seen_ts_utc"))
    passed = bool(
        causal_gate.get("causal_entry_eligible")
        and checkpoint is not None
        and ready is not None
        and official is not None
        and checkpoint <= ready
        and checkpoint < official
    )
    if not causal_gate.get("causal_entry_eligible"):
        reason = causal_gate.get("causal_exclusion_reason")
    elif checkpoint is None:
        reason = "MISSING_OR_UNCOMPARABLE_ENTRY_BOOK_CHECKPOINT"
    elif ready is None or official is None:
        reason = "MISSING_CAUSAL_CUTOFF_CLOCK"
    elif checkpoint > ready:
        reason = "ENTRY_BOOK_CHECKPOINT_AFTER_DECISION_READY_CUTOFF"
    elif checkpoint >= official:
        reason = "ENTRY_BOOK_CHECKPOINT_AT_OR_AFTER_OFFICIAL_FIRST_SEEN"
    else:
        reason = None
    return {
        "entry_book_checkpoint_at_utc": ts_text(checkpoint),
        "entry_checkpoint_causal_guard_passed": passed,
        "entry_checkpoint_causal_exclusion_reason": reason,
    }


def latency_provenance_rows(
    events: Sequence[Mapping[str, Any]],
    oracle_rows: Sequence[Mapping[str, Any]],
    latency_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    oracle = {str(row["event_id"]): row for row in oracle_rows}
    dates = sorted({str(row.get("event_key", "")).split("|")[1] for row in latency_rows if len(str(row.get("event_key", "")).split("|")) > 1})
    output = []
    for event in sorted(events, key=lambda row: str(row["event_id"])):
        event_id = str(event["event_id"])
        row = oracle[event_id]
        output.append({
            "event_id": event_id,
            "city": event.get("city"),
            "source": event.get("source"),
            "collector_epoch": None,
            "latency_policy_id": LEGACY_LATENCY_POLICY_ID,
            "slo_seconds": float(row.get("execution_latency_seconds")),
            "estimation_window_start_date": dates[0] if dates else None,
            "estimation_window_end_date": dates[-1] if dates else None,
            "estimation_cutoff_utc": "2026-08-26T23:59:59.999999Z",
            "frozen_at_utc": "2026-08-27T00:00:00Z",
            "fallback_policy": "NONE",
            "historical_or_forward_status": "LEGACY_DIAGNOSTIC_ONLY",
            "forward_primary_status": "NOT_FORWARD_PRIMARY",
            "heterogeneous_global_pooling": True,
            "collector_epoch_missing": True,
            "latency_contract_id": row.get("latency_contract_id"),
        })
    bad = [row for row in output if abs(row["slo_seconds"] - LEGACY_LATENCY_SLO_SECONDS) > 1e-9]
    if bad or len(output) != 841:
        raise RuntimeError(f"latency provenance mismatch rows={len(output)} bad_slo={len(bad)}")
    return output, {
        "row_count": len(output),
        "latency_policy_id": LEGACY_LATENCY_POLICY_ID,
        "slo_seconds": LEGACY_LATENCY_SLO_SECONDS,
        "legacy_status": "LEGACY_DIAGNOSTIC_ONLY",
        "forward_primary_status": "NOT_FORWARD_PRIMARY",
        "collector_epoch_materialized": False,
        "future_primary_requirement": "city + source + collector_epoch frozen operational SLO estimated only from pre-cutoff observations",
        "status": "PASS",
    }


def _policy_candidates(event: Mapping[str, Any], tokens: Sequence[Mapping[str, Any]], policy: str) -> list[Mapping[str, Any]]:
    prior = float(event["metar_running_max_round_c"])
    actual = float(event["official_round_c"])
    selected = []
    for token in tokens:
        roles = set(token.get("roles") or ())
        outcome = str(token["outcome"])
        bracket = float(token["bracket_order"])
        desired = False
        if policy == "CROSS_ONLY_SEMANTIC_ORACLE":
            desired = actual > prior and ((outcome == "no" and "prior_exact_bracket" in roles) or (outcome == "yes" and "actual_next_print_bracket" in roles))
        elif policy == "PRIOR_CURRENT_EXACT_NO_ON_UPWARD_CROSS":
            desired = actual > prior and outcome == "no" and "prior_exact_bracket" in roles
        elif policy == "ACTUAL_NEXT_PRINT_BRACKET_YES":
            desired = actual > prior and outcome == "yes" and "actual_next_print_bracket" in roles
        elif policy == "LOWER_IMPOSSIBLE_BRACKETS_NO_ON_MULTI_TICK_CROSS":
            desired = actual >= prior + 2 and outcome == "no" and bracket < actual
        elif policy == "CURRENT_BRACKET_YES_NO_NEW_MAX_DIAGNOSTIC":
            desired = actual <= prior and outcome == "yes" and "prior_exact_bracket" in roles
        if desired:
            selected.append(token)
    return selected


def build_oracle_family_rows(
    events: Sequence[Mapping[str, Any]],
    universes: Sequence[Mapping[str, Any]],
    matrix: Sequence[Mapping[str, Any]],
    oracle_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    universe = {str(row["event_id"]): row for row in universes}
    books: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in matrix:
        key = (str(row["event_id"]), str(row["checkpoint"]), str(row["token_id"]))
        if key in books:
            raise RuntimeError(f"duplicate book checkpoint key: {key}")
        books[key] = row
    oracle = {str(row["event_id"]): row for row in oracle_rows}
    output = []
    for event in sorted(events, key=lambda row: str(row["event_id"])):
        event_id = str(event["event_id"])
        gate = causal_entry_gate(event, oracle[event_id])
        tokens = list(universe[event_id]["tokens"])
        for policy in POLICIES:
            candidates = _policy_candidates(event, tokens, policy)
            for shares in SIZES:
                ranked = []
                checkpoint_failures: list[str] = []
                for token in candidates:
                    entry = books.get((event_id, "entry_after_p95_latency", str(token["token_id"])), {})
                    sweep = (entry.get("sweeps") or {}).get(f"buy_{shares}")
                    checkpoint_gate = entry_checkpoint_gate(entry, gate)
                    if not checkpoint_gate["entry_checkpoint_causal_guard_passed"]:
                        checkpoint_failures.append(str(checkpoint_gate["entry_checkpoint_causal_exclusion_reason"]))
                    if checkpoint_gate["entry_checkpoint_causal_guard_passed"] and entry.get("book_valid") and _feasible(sweep):
                        ranked.append((float(sweep["effective_value_usd"]), str(token["token_id"]), token, sweep, entry, checkpoint_gate))
                selected = min(ranked, key=lambda value: (value[0], value[1])) if ranked else None
                for horizon in HORIZONS:
                    no_trade_policy = policy == "NO_TRADE"
                    token = selected[2] if selected else None
                    entry_sweep = selected[3] if selected else None
                    checkpoint_gate = selected[5] if selected else None
                    exit_row = books.get((event_id, f"official_plus_{horizon}s", str(token["token_id"])), {}) if token else {}
                    exit_sweep = (exit_row.get("sweeps") or {}).get(f"sell_{shares}")
                    paired = bool(selected and exit_row.get("book_valid") and _feasible(exit_sweep))
                    pnl = float(exit_sweep["effective_value_usd"]) - float(entry_sweep["effective_value_usd"]) if paired else (0.0 if no_trade_policy else None)
                    if not gate["causal_entry_eligible"] and selected is not None:
                        raise RuntimeError(f"noncausal selection escaped gate: {event_id}")
                    output.append({
                        "event_id": event_id,
                        "city": event["city"],
                        "source": event.get("source"),
                        "target_date": event["target_date"],
                        "official_print_id": event.get("information_event_id"),
                        "entry_mode": "LEGACY_FROZEN_GLOBAL_P95_DIAGNOSTIC",
                        "latency_policy_id": LEGACY_LATENCY_POLICY_ID,
                        "latency_status": "LEGACY_DIAGNOSTIC_ONLY",
                        "forward_primary_status": "NOT_FORWARD_PRIMARY",
                        "policy": policy,
                        "shares": shares,
                        "horizon_seconds": horizon,
                        **gate,
                        "event_universe_included": True,
                        "action": "NO_TRADE" if no_trade_policy or not selected else "BUY_OUTCOME_TOKEN",
                        "abstain_reason": None if selected else (
                            "POLICY_NO_TRADE" if no_trade_policy else (
                                gate["causal_exclusion_reason"]
                                or (sorted(set(checkpoint_failures))[0] if checkpoint_failures else None)
                                or "SEMANTIC_NOT_APPLICABLE_OR_NO_EXECUTABLE_ENTRY"
                            )
                        ),
                        "token_id": str(token["token_id"]) if token else None,
                        "entry_book_checkpoint_at_utc": checkpoint_gate["entry_book_checkpoint_at_utc"] if checkpoint_gate else None,
                        "entry_checkpoint_causal_guard_passed": checkpoint_gate["entry_checkpoint_causal_guard_passed"] if checkpoint_gate else False,
                        "entry_checkpoint_causal_exclusion_reason": checkpoint_gate["entry_checkpoint_causal_exclusion_reason"] if checkpoint_gate else None,
                        "entry_eligible": bool(selected),
                        "exit_eligible": bool(paired),
                        "paired_executable": paired,
                        "net_pnl_or_null": pnl,
                        "post_official_book_reaction_or_exit_used_for_action": False,
                        "action_market_data_cutoff_utc": gate["effective_entry_decision_ready_ts_utc"],
                        "exit_data_used_only_for_measurement": bool(token),
                    })
    expected = 841 * len(POLICIES) * len(SIZES) * len(HORIZONS)
    if len(output) != expected:
        raise RuntimeError(f"oracle family row count mismatch {len(output)} != {expected}")
    return output, {
        "event_count": 841,
        "policy_count": len(POLICIES),
        "sizes": list(SIZES),
        "horizons_seconds": list(HORIZONS),
        "row_count": len(output),
        "causal_hard_gate_applied_to_every_row": True,
        "entry_equal_official_first_seen_fails_closed": True,
        "missing_or_uncomparable_clock_fails_closed": True,
        "post_official_market_data_used_for_action": False,
        "entry_checkpoint_not_after_decision_ready_guard_applied": True,
        "legacy_latency_entry_materialized": True,
        "forward_primary_latency_entry_materialized": False,
        "model_or_policy_selection_authorized": False,
        "status": "PASS",
    }


def build_denominator_rows(
    baseline_rows: Sequence[Mapping[str, Any]],
    oracle_rows: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    baseline = {str(row["event_id"]): row for row in baseline_rows}
    oracle = {str(row["event_id"]): row for row in oracle_rows}
    output = []
    for event in sorted(events, key=lambda row: str(row["event_id"])):
        event_id = str(event["event_id"])
        primary = oracle[event_id]
        base = baseline[event_id]
        action = primary.get("action") or {}
        gate = causal_entry_gate(event, primary)
        semantic_abstain = action.get("action") == "NO_TRADE" and action.get("reason") != "no_semantic_candidate_has_feasible_entry"
        paired = bool(primary.get("paired_executable") and gate["causal_entry_eligible"])
        if semantic_abstain:
            operational = "POLICY_ABSTAIN"
            operational_pnl = 0.0
        elif paired:
            operational = "POLICY_ACTION"
            operational_pnl = float(primary["net_pnl_usd"])
        else:
            operational = "DATA_FAIL_CLOSED"
            operational_pnl = 0.0
        market_eligible = paired
        pairwise = bool(market_eligible and base.get("same_row_intersection_eligible"))
        output.append({
            "event_id": event_id,
            "city": event.get("city"),
            "target_date": event.get("target_date"),
            "official_print_id": event.get("information_event_id"),
            "EVENT_UNIVERSE": True,
            "OPERATIONAL_FAIL_CLOSED": True,
            "RESEARCH_MARKET_DATA_ELIGIBLE": market_eligible,
            "PAIRWISE_BASELINE_COMPARABLE": pairwise,
            "operational_disposition": operational,
            "research_disposition": "RESEARCH_ELIGIBLE" if market_eligible else "RESEARCH_INELIGIBLE",
            "operational_pnl_usd": operational_pnl,
            "conditional_alpha_pnl_usd": float(primary["net_pnl_usd"]) if market_eligible else None,
            "pairwise_alpha_pnl_usd": float(primary["net_pnl_usd"]) if pairwise else None,
            "policy_abstain": operational == "POLICY_ABSTAIN",
            "data_fail_closed": operational == "DATA_FAIL_CLOSED",
            "research_ineligible": not market_eligible,
            "causal_entry_eligible": gate["causal_entry_eligible"],
            "primary_action": action.get("action"),
            "primary_reason": action.get("reason"),
            "paired_executable": paired,
        })
    counts = collections.Counter(row["operational_disposition"] for row in output)
    summary = {
        "row_count": len(output),
        "layers": {
            "EVENT_UNIVERSE": sum(row["EVENT_UNIVERSE"] for row in output),
            "OPERATIONAL_FAIL_CLOSED": sum(row["OPERATIONAL_FAIL_CLOSED"] for row in output),
            "RESEARCH_MARKET_DATA_ELIGIBLE": sum(row["RESEARCH_MARKET_DATA_ELIGIBLE"] for row in output),
            "PAIRWISE_BASELINE_COMPARABLE": sum(row["PAIRWISE_BASELINE_COMPARABLE"] for row in output),
        },
        "operational_dispositions": dict(sorted(counts.items())),
        "operational_missing_data_pnl_is_zero": True,
        "conditional_alpha_missing_data_is_null": True,
        "policy_abstain_distinct_from_data_fail_closed": True,
        "alpha_or_futility_claim_authorized": False,
        "status": "PASS",
    }
    if len(output) != 841 or summary["layers"]["RESEARCH_MARKET_DATA_ELIGIBLE"] != 89:
        raise RuntimeError(f"denominator layer drift: {summary}")
    return output, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage2", type=Path, default=STAGE2)
    parser.add_argument("--stage3", type=Path, default=STAGE3)
    parser.add_argument("--events", type=Path, default=EVENTS)
    parser.add_argument("--prior-closure", type=Path, default=PRIOR_CLOSURE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    legacy = [verify_legacy_manifest(args.stage2), verify_legacy_manifest(args.stage3)]
    events = read_jsonl_gz(args.events)
    universes = read_jsonl_gz(args.stage2 / "evidence/FROZEN_EVENT_MARKET_UNIVERSE.jsonl.gz")
    matrix = read_jsonl_gz(args.stage2 / "evidence/EVENT_BOOK_COVERAGE_MATRIX.jsonl.gz")
    latency = read_jsonl_gz(args.stage2 / "evidence/FROZEN_PIPELINE_LATENCY_ROWS.jsonl.gz")
    oracle = read_jsonl_gz(args.stage3 / "evidence/PRIMARY_ORACLE_ROWS.jsonl.gz")
    reaction = read_jsonl_gz(args.stage3 / "evidence/REACTION_WINDOW_ROWS.jsonl.gz")
    baseline = read_jsonl_gz(args.stage3 / "evidence/MATCHED_BASELINE_ROWS.jsonl.gz")

    reconciled, reconciliation = build_reconciliation(
        oracle, reaction, baseline,
        read_json(args.stage3 / "CITY_GATES.json"),
        read_json(args.stage3 / "REACTION_INFERENCE_AND_CONCENTRATION.json"),
    )
    write_jsonl_gz(args.output / "evidence/PRIMARY_REACTION_GATE_ROW_RECONCILIATION.jsonl.gz", reconciled)
    write_json(args.output / "PRIMARY_REACTION_GATE_RECONCILIATION_SUMMARY.json", reconciliation)
    validity = build_validity_corrigendum(matrix, {str(row["event_id"]): row for row in oracle})
    write_json(args.output / "BOOK_VALIDITY_CORRIGENDUM_RESULTS.json", validity)

    provenance, provenance_summary = latency_provenance_rows(events, oracle, latency)
    write_jsonl_gz(args.output / "evidence/LATENCY_POLICY_PROVENANCE_ROWS.jsonl.gz", provenance)
    write_json(args.output / "LATENCY_POLICY_PROVENANCE_SUMMARY.json", provenance_summary)
    family, family_summary = build_oracle_family_rows(events, universes, matrix, oracle)
    write_jsonl_gz(args.output / "evidence/ORACLE_FAMILY_MEASUREMENT_ROWS.jsonl.gz", family)
    write_json(args.output / "ORACLE_FAMILY_MEASUREMENT_HARNESS.json", family_summary)
    denominator, denominator_summary = build_denominator_rows(baseline, oracle, events)
    write_jsonl_gz(args.output / "evidence/DENOMINATOR_LAYER_ROWS.jsonl.gz", denominator)
    write_json(args.output / "DENOMINATOR_LAYER_SUMMARY.json", denominator_summary)

    prior_required = (
        "COMPLETE_REV2_EVIDENCE_PACKAGE_AUDIT.json",
        "FINAL_CODE_AND_ENVIRONMENT_FREEZE.json",
        "TEST_COMMANDS_AND_RAW_OUTPUT.txt",
        "PACKAGE_FAIL_CLOSED_TESTS.txt",
        "EVIDENCE_MANIFEST.json",
        "REPRODUCE_STAGE23_REV2.sh",
    )
    prior_identities = []
    for name in prior_required:
        path = args.prior_closure / name
        if not path.is_file():
            raise RuntimeError(f"missing prior full-seal evidence: {path}")
        prior_identities.append(identity(path))
    audit = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "immutable_stage2_stage3_manifests": legacy,
        "prior_full_seal_required_artifacts": prior_identities,
        "new_row_evidence": [
            identity(args.output / "evidence/PRIMARY_REACTION_GATE_ROW_RECONCILIATION.jsonl.gz"),
            identity(args.output / "evidence/LATENCY_POLICY_PROVENANCE_ROWS.jsonl.gz"),
            identity(args.output / "evidence/ORACLE_FAMILY_MEASUREMENT_ROWS.jsonl.gz"),
            identity(args.output / "evidence/DENOMINATOR_LAYER_ROWS.jsonl.gz"),
        ],
        "raw_15m_frame_replay_repeated": False,
        "reason_raw_replay_not_repeated": "immutable stage2/stage3 manifests and prior full seal are verified before deterministic closure rebuild",
        "production_files_modified": False,
        "orders": 0,
        "fills": 0,
        "notional_usd": 0,
        "modeling_performed": False,
        "stage4_work_performed": False,
        "status": "PASS",
    }
    write_json(args.output / "FULL_EVIDENCE_SEAL_V2_AUDIT.json", audit)
    print(json.dumps({"output": str(args.output), "status": "PASS", "denominator": denominator_summary["layers"], "oracle_rows": len(family)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
