"""Pure contracts for the additive Core Carry near-Core maker probe.

This module deliberately contains no venue or filesystem side effects.  The
production runner owns persistence and execution; these helpers keep candidate
selection, sleeve identity, exposure accounting, and reporting deterministic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


SOURCE_SLEEVE = "CORE_CARRY_NEAR_CORE_MAKER_PROBE_5S"
STRATEGY_INSTANCE = "current_yes_core_carry_near_core_maker_probe_5s_v1"
CONFIG_ID = "current_yes_core_carry_near_core_fixed_rest_5s_ws1_v1"
EXECUTION_PROFILE = "near_core_fixed_rest_maker_ws1_v1"
EXPERIMENT_ID = "core_carry_near_core_maker_ws1_v1"
CLIENT_ORDER_PREFIX = "pmc_ccnc_"
FIXED_SHARES = 5.0
SOLE_BLOCKER = "non_positive_taker_ev"
LEDGER_SCHEMA_VERSION = "core_carry_near_core_maker_ledger_v1"
MANIFEST_SCHEMA_VERSION = "core_carry_near_core_maker_manifest_v1"


def city_day(row: Mapping[str, Any]) -> tuple[str, str]:
    return str(row.get("city") or ""), str(row.get("target_date") or "")


def is_candidate(row: Mapping[str, Any]) -> bool:
    """Select only exact frozen-score skips whose sole blocker is taker EV."""

    return (
        not bool(row.get("eligible"))
        and list(row.get("reasons") or ()) == [SOLE_BLOCKER]
        and bool(str(row.get("checkpoint_key") or ""))
        and bool(str(row.get("current_yes_token_id") or row.get("token_id") or ""))
        and all(city_day(row))
    )


def matched_shares(row: Mapping[str, Any]) -> float:
    """Read authoritative matched quantity when present, otherwise fail low."""

    candidates: list[Any] = [
        row.get("authoritative_matched_shares"),
        row.get("source_filled_shares"),
        row.get("matched_shares"),
        row.get("size_matched"),
    ]
    response = row.get("exchange_response")
    if isinstance(response, Mapping):
        state = response.get("authoritative_order_state")
        if isinstance(state, Mapping):
            candidates.extend(
                (
                    state.get("size_matched"),
                    state.get("sizeMatched"),
                    state.get("matched_shares"),
                )
            )
    values: list[float] = []
    for value in candidates:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            values.append(parsed)
    return min(FIXED_SHARES, max(values, default=0.0))


def filled_exposure_by_city_day(
    rows: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], float]:
    """Return maximum authoritative fill seen for each near-Core opportunity."""

    exposure: dict[tuple[str, str], float] = {}
    for row in rows:
        if str(row.get("strategy_instance") or "") != STRATEGY_INSTANCE:
            continue
        key = city_day(row)
        if not all(key):
            continue
        exposure[key] = max(exposure.get(key, 0.0), matched_shares(row))
    return exposure


def promotion_gate(ledger_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """WS-1 is evidence collection; it cannot self-promote an economic cancel."""

    rows = [dict(row) for row in ledger_rows]
    submitted = sum(str(row.get("status") or "") == "submitted" for row in rows)
    terminal = sum(
        str(row.get("status") or "")
        in {"filled", "cancelled", "canceled", "expired", "settled"}
        for row in rows
    )
    return {
        "schema_version": "core_carry_near_core_maker_promotion_gate_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gate_pass": False,
        "stage": "WS-1_BASELINE_EVIDENCE_COLLECTION",
        "submitted_order_rows": submitted,
        "terminal_order_rows": terminal,
        "fail_reasons": [
            "economic_ws_cancel_policy_not_frozen",
            "held_out_near_core_actual_order_evidence_not_complete",
            "ws2_randomization_not_authorized",
        ],
    }


def report(ledger_rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [dict(row) for row in ledger_rows]
    by_status: dict[str, int] = {}
    eligible = 0
    for row in rows:
        status = str(row.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        eligible += int(bool(row.get("eligible_checkpoint")))
    return {
        "schema_version": "core_carry_near_core_maker_report_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_sleeve": SOURCE_SLEEVE,
        "experiment_id": EXPERIMENT_ID,
        "denominator": "all_eligible_near_core_checkpoints_including_unfilled_zero_pnl",
        "eligible_checkpoints": eligible,
        "ledger_rows": len(rows),
        "status_counts": dict(sorted(by_status.items())),
        "pnl_pooling_with_existing_core": False,
    }
