#!/usr/bin/env python3
"""Fail-closed WCIR Stage 2/3 rev2 closure built only from frozen evidence.

This module does not read the mutable runtime, use the network, train a model,
or touch production configuration.  It reconciles the immutable Stage 2/3
evidence and emits diagnostic contracts/results in a new review directory.
"""

from __future__ import annotations

import argparse
import collections
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
import platform
import locale
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[3]
REVIEW_ROOT = ROOT / "reviews/wcir_next_print"
STAGE2 = REVIEW_ROOT / "stage_02_rev2"
STAGE3 = REVIEW_ROOT / "stage_03_rev2"
CLOSURE = REVIEW_ROOT / "stage_02_03_rev2_closure"
FRESHNESS_POLICY_ID = "wcir_book_freshness_120s_v1"
FRESHNESS_LIMIT_SECONDS = 120.0
PRIMARY_WINDOW = "entry_to_official_plus_30s"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(root)) if root else str(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl_gz(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def write_jsonl_gz(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("wb") as raw:
        # mtime=0 makes regenerated evidence byte-identical.
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            for row in rows:
                zipped.write(canonical_bytes(row) + b"\n")
                count += 1
    return count


def verify_legacy_manifest(root: Path) -> dict[str, Any]:
    manifest = read_json(root / "EVIDENCE_MANIFEST.json")
    expected = {entry["path"] for entry in manifest["entries"]} | {"EVIDENCE_MANIFEST.json"}
    actual = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
    }
    if actual != expected:
        raise RuntimeError(
            f"legacy entry-set drift at {root}: missing={sorted(expected-actual)} extra={sorted(actual-expected)}"
        )
    for entry in manifest["entries"]:
        path = root / entry["path"]
        if path.stat().st_size != entry["size_bytes"] or sha256_file(path) != entry["sha256"]:
            raise RuntimeError(f"legacy evidence drift: {path}")
    return {
        "root": str(root),
        "entry_count": len(expected),
        "entry_set_verified": True,
        "manifest_sha256": sha256_file(root / "EVIDENCE_MANIFEST.json"),
    }


def _feasible(sweep: Any) -> bool:
    return bool(isinstance(sweep, Mapping) and sweep.get("fully_executable"))


def build_reconciliation(
    oracle_rows: Sequence[Mapping[str, Any]],
    reaction_rows: Sequence[Mapping[str, Any]],
    baseline_rows: Sequence[Mapping[str, Any]],
    city_gates: Mapping[str, Any] | None = None,
    concentration: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    def unique(rows: Sequence[Mapping[str, Any]], key_fn, label: str) -> dict[Any, Mapping[str, Any]]:
        output = {}
        for row in rows:
            key = key_fn(row)
            if key in output:
                raise RuntimeError(f"duplicate {label}: {key}")
            output[key] = row
        return output

    oracle_index = unique(oracle_rows, lambda row: str(row["event_id"]), "oracle event_id")
    if len(oracle_index) != 841:
        raise RuntimeError(f"oracle event set is not 841: {len(oracle_index)}")
    reaction_all = unique(
        reaction_rows,
        lambda row: (str(row["event_id"]), str(row["window"])),
        "reaction event/window",
    )
    unknown_reaction = {key[0] for key in reaction_all} - set(oracle_index)
    if unknown_reaction:
        raise RuntimeError(f"reaction contains unknown events: {sorted(unknown_reaction)[:10]}")
    reaction = {event_id: row for (event_id, window), row in reaction_all.items() if window == PRIMARY_WINDOW}
    expected_reaction = {
        event_id for event_id, row in oracle_index.items()
        if (row.get("action") or {}).get("token_id")
    }
    if set(reaction) != expected_reaction:
        raise RuntimeError(
            f"reaction primary event set mismatch missing={sorted(expected_reaction-set(reaction))[:10]} "
            f"extra={sorted(set(reaction)-expected_reaction)[:10]}"
        )
    baseline = unique(baseline_rows, lambda row: str(row["event_id"]), "baseline event_id")
    if set(baseline) != set(oracle_index):
        raise RuntimeError(
            f"baseline event set mismatch missing={sorted(set(oracle_index)-set(baseline))[:10]} "
            f"extra={sorted(set(baseline)-set(oracle_index))[:10]}"
        )
    rows: list[dict[str, Any]] = []
    for oracle in sorted(oracle_rows, key=lambda row: str(row["event_id"])):
        event_id = str(oracle["event_id"])
        action = oracle.get("action") or {}
        entry_book_ok = _feasible(oracle.get("entry_sweep"))
        exit_book_ok = _feasible(oracle.get("exit_sweep"))
        causal_lead_ok = (
            oracle.get("effective_lead_seconds") is not None
            and float(oracle["effective_lead_seconds"]) > 0
        )
        trade_action = action.get("action") == "BUY_OUTCOME_TOKEN"
        entry_eligible = bool(trade_action and causal_lead_ok and entry_book_ok)
        exit_eligible = bool(entry_eligible and exit_book_ok)
        paired = bool(entry_eligible and exit_eligible)
        if paired != bool(oracle.get("paired_executable")):
            raise RuntimeError(f"canonical primary differs from immutable oracle row: {event_id}")
        old_reaction = reaction.get(event_id, {})
        old_reaction_feasible = bool(old_reaction.get("paired_5_share_feasible"))
        included_reaction = bool(old_reaction_feasible and causal_lead_ok and entry_book_ok and exit_book_ok)
        concentration_included = bool(oracle.get("paired_executable") and oracle.get("net_pnl_usd") is not None)
        bootstrap_included = concentration_included
        city_gate_included = bool(oracle.get("paired_executable"))
        exclusion: dict[str, str | None] = {
            "primary": None,
            "reaction_plus_30s": None,
            "date_concentration": None,
            "bootstrap": None,
            "city_gate": None,
            "baseline_intersection": None,
        }
        if not trade_action:
            exclusion["primary"] = str(action.get("reason") or "NO_TRADE")
        elif not causal_lead_ok:
            exclusion["primary"] = "NONPOSITIVE_EFFECTIVE_LEAD_AFTER_FROZEN_LATENCY"
        elif not entry_book_ok:
            exclusion["primary"] = "MISSING_OR_INEXECUTABLE_ENTRY_BOOK"
        elif not exit_book_ok:
            exclusion["primary"] = "MISSING_OR_INEXECUTABLE_EXIT_BOOK"
        layer_values = {
            "reaction_plus_30s": included_reaction,
            "date_concentration": concentration_included,
            "bootstrap": bootstrap_included,
            "city_gate": city_gate_included,
        }
        for layer, included in layer_values.items():
            exclusion[layer] = exclusion["primary"] if not included else None
        base_eligible = bool(baseline.get(event_id, {}).get("same_row_intersection_eligible"))
        if not base_eligible:
            exclusion["baseline_intersection"] = "NOT_IN_FROZEN_ALL_BASELINE_INTERSECTION"
        rows.append(
            {
                "event_id": event_id,
                "city": oracle["city"],
                "target_date": oracle["target_date"],
                "official_print_id": oracle.get("official_print_id"),
                "primary_action": action.get("action"),
                "primary_no_trade_reason": action.get("reason") if action.get("action") == "NO_TRADE" else None,
                "entry_eligible": entry_eligible,
                "exit_eligible": exit_eligible,
                "paired_primary": paired,
                "included_in_reaction_plus_30s": included_reaction,
                "included_in_date_concentration": concentration_included,
                "included_in_bootstrap": bootstrap_included,
                "included_in_city_gate": city_gate_included,
                "included_in_baseline_intersection": base_eligible,
                "exclusion_reason_at_each_layer": exclusion,
                "net_pnl_or_null": oracle.get("net_pnl_usd") if paired else None,
                "diagnostic_old_reaction_plus_30s_feasible": old_reaction_feasible,
                "diagnostic_old_reaction_disagrees_with_causal_primary": old_reaction_feasible != included_reaction,
                "effective_lead_seconds": oracle.get("effective_lead_seconds"),
            }
        )

    def count(field: str) -> int:
        return sum(bool(row[field]) for row in rows)

    by_city = {}
    for city in sorted({str(row["city"]) for row in rows}):
        city_rows = [row for row in rows if row["city"] == city]
        by_city[city] = {
            "event_count": len(city_rows),
            "paired_primary": sum(row["paired_primary"] for row in city_rows),
            "reaction_plus_30s": sum(row["included_in_reaction_plus_30s"] for row in city_rows),
            "city_gate": sum(row["included_in_city_gate"] for row in city_rows),
        }
    mismatches = [row for row in rows if row["diagnostic_old_reaction_disagrees_with_causal_primary"]]
    summary = {
        "row_count": len(rows),
        "canonical_headlines_from_single_row_table": {
            "primary_oracle": count("paired_primary"),
            "reaction_primary_aggregate": count("included_in_reaction_plus_30s"),
            "date_concentration": count("included_in_date_concentration"),
            "bootstrap": count("included_in_bootstrap"),
            "city_gate": count("included_in_city_gate"),
            "baseline_intersection": count("included_in_baseline_intersection"),
        },
        "by_city": by_city,
        "legacy_reaction_mismatch_count": len(mismatches),
        "legacy_reaction_mismatch_rows": [
            {
                "event_id": row["event_id"],
                "city": row["city"],
                "target_date": row["target_date"],
                "effective_lead_seconds": row["effective_lead_seconds"],
                "old_reaction_feasible": row["diagnostic_old_reaction_plus_30s_feasible"],
                "canonical_reaction_included": row["included_in_reaction_plus_30s"],
                "disposition": "EXCLUDE_NONCAUSAL_ENTRY_AFTER_OFFICIAL_FIRST_SEEN",
            }
            for row in mismatches
        ],
        "resolution": (
            "The old reaction builder checked book feasibility but omitted the primary oracle's "
            "effective_lead_seconds > 0 guard. One Busan row entered after official first_seen. "
            "The immutable legacy files are unchanged; the closure table applies the primary causal gate to every layer."
        ),
        "status": "PASS",
    }
    # Independently reconcile the canonical row-level group-bys with immutable
    # aggregate artifacts. Those artifacts do not carry row IDs, so equality of
    # city counts and PnL is the strongest possible join without rewriting them.
    if city_gates is not None:
        for city, values in by_city.items():
            legacy = int(city_gates[city]["metrics"]["paired_5_share_feasible_events"])
            if values["city_gate"] != legacy:
                raise RuntimeError(f"city gate aggregate drift for {city}: {values['city_gate']} != {legacy}")
    if concentration is not None:
        legacy_concentration = concentration["by_city_concentration"]
        legacy_bootstrap = concentration["date_block_ci"]
        for city, values in by_city.items():
            selected = [row for row in rows if row["city"] == city and row["included_in_date_concentration"]]
            total = sum(float(row["net_pnl_or_null"]) for row in selected)
            legacy = legacy_concentration[city]
            if values["paired_primary"] != int(legacy["paired_rows"]) or abs(total - float(legacy["total_net_pnl_usd"])) > 1e-9:
                raise RuntimeError(f"concentration aggregate drift for {city}")
            bootstrap_dates = len({row["target_date"] for row in selected})
            if bootstrap_dates != int(legacy_bootstrap[city]["target_date_count"]):
                raise RuntimeError(f"bootstrap input-date drift for {city}")
    summary["immutable_aggregate_reconciliation"] = {
        "city_gates_verified": city_gates is not None,
        "date_concentration_verified": concentration is not None,
        "bootstrap_input_contract": "same canonical rows with paired_primary and non-null PnL grouped by target_date",
    }
    heads = summary["canonical_headlines_from_single_row_table"]
    if len(rows) != 841 or any(heads[key] != 89 for key in (
        "primary_oracle", "reaction_primary_aggregate", "date_concentration", "bootstrap", "city_gate"
    )) or by_city.get("Busan", {}).get("paired_primary") != 56 or len(mismatches) != 1:
        raise RuntimeError(f"unresolved primary/reaction/gate reconciliation: {summary}")
    return rows, summary


def build_validity_corrigendum(
    matrix: Sequence[Mapping[str, Any]],
    event_meta: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    source_rows = [
        row for row in matrix
        if row.get("checkpoint") == "source_t0"
        and row.get("outcome") == "no"
        and "prior_exact_bracket" in (row.get("roles") or [])
    ]
    taxonomy = collections.Counter()
    diagnostics = []
    for row in source_rows:
        status = str(row["primary_status"])
        known_statuses = {
            "ARCHIVE_MISSING", "CLOCK_UNCERTAINTY", "IDENTITY_MISMATCH", "NO_BASELINE",
            "OPEN_GAP", "STALE_BOOK", "VALID_BUT_ONE_SIDED",
            "VALID_BUT_INSUFFICIENT_DEPTH", "VALID_TWO_SIDED_DEPTH",
        }
        if status not in known_statuses:
            raise RuntimeError(f"unknown immutable primary status: {status}")
        reconstruction_valid = status in {
            "STALE_BOOK", "VALID_BUT_ONE_SIDED", "VALID_BUT_INSUFFICIENT_DEPTH", "VALID_TWO_SIDED_DEPTH"
        }
        identity_valid = status in {"STALE_BOOK", "NO_BASELINE", "OPEN_GAP", "VALID_BUT_ONE_SIDED", "VALID_BUT_INSUFFICIENT_DEPTH", "VALID_TWO_SIDED_DEPTH"}
        clock_valid = status in {"STALE_BOOK", "NO_BASELINE", "OPEN_GAP", "VALID_BUT_ONE_SIDED", "VALID_BUT_INSUFFICIENT_DEPTH", "VALID_TWO_SIDED_DEPTH"}
        gap_free = status in {"STALE_BOOK", "NO_BASELINE", "VALID_BUT_ONE_SIDED", "VALID_BUT_INSUFFICIENT_DEPTH", "VALID_TWO_SIDED_DEPTH"}
        active = bool(row.get("subscription_epoch_id"))
        baseline = bool(row.get("book_snapshot_id") and active)
        liveness: bool | None = None  # not frozen in rev2
        age = row.get("age_seconds")
        freshness = bool(
            reconstruction_valid and identity_valid and clock_valid and gap_free and active
            and baseline and liveness is True and age is not None
            and float(age) <= FRESHNESS_LIMIT_SECONDS
        )
        stale_reason = None
        if status == "STALE_BOOK":
            if not active:
                stale_reason = "TOKEN_NOT_ACTIVE_AT_CHECKPOINT"
            elif liveness is None:
                stale_reason = "CONNECTION_LIVENESS_UNPROVEN"
            elif age is not None and float(age) > FRESHNESS_LIMIT_SECONDS:
                stale_reason = "ACTIVE_CONNECTION_NO_RECENT_DELTA"
            else:
                stale_reason = "FRESHNESS_POLICY_EXCEEDED"
            taxonomy[stale_reason] += 1
        meta = event_meta.get(str(row["event_id"]), {})
        diagnostics.append({
            "event_id": row["event_id"],
            "city": meta.get("city"),
            "target_date": meta.get("target_date"),
            "subscription_epoch_id": row.get("subscription_epoch_id"),
            "strict_original_classification": status,
            "reconstruction_valid": reconstruction_valid,
            "identity_valid": identity_valid,
            "clock_valid": clock_valid,
            "gap_free": gap_free,
            "subscription_expected": None,
            "subscription_requested": None,
            "subscription_acknowledged": None,
            "subscription_active_at_checkpoint": active,
            "connection_liveness_proven": liveness,
            "baseline_received_for_active_epoch": baseline,
            "book_state_age_seconds": age,
            "freshness_policy_id": FRESHNESS_POLICY_ID,
            "freshness_eligible": freshness,
            "side_available": row.get("side_available"),
            "sweep_1_feasible": row.get("sweep_1_feasible"),
            "sweep_5_feasible": row.get("sweep_5_feasible"),
            "sweep_10_feasible": row.get("sweep_10_feasible"),
            "execution_eligible": bool(
                freshness
                and row.get("side_available", {}).get("buy")
                and row.get("sweep_5_feasible", {}).get("buy")
            ),
            "stale_diagnostic_reason": stale_reason,
        })
    status_counts = collections.Counter(row["strict_original_classification"] for row in diagnostics)
    def aggregate(keys: tuple[str, ...]) -> list[dict[str, Any]]:
        grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = collections.defaultdict(list)
        for row in diagnostics:
            grouped[tuple(row.get(key) for key in keys)].append(row)
        return [
            {
                **{key: group[index] for index, key in enumerate(keys)},
                "row_count": len(selected),
                "reconstruction_valid_count": sum(row["reconstruction_valid"] for row in selected),
                "freshness_eligible_count": sum(row["freshness_eligible"] for row in selected),
            }
            for group, selected in sorted(grouped.items(), key=lambda pair: str(pair[0]))
        ]
    return {
        "scope": "source_t0 prior exact bracket NO rows from immutable rev2 matrix",
        "row_count": len(diagnostics),
        "strict_original_status_counts": dict(sorted(status_counts.items())),
        "reconstruction_valid_count": sum(row["reconstruction_valid"] for row in diagnostics),
        "freshness_eligible_count": sum(row["freshness_eligible"] for row in diagnostics),
        "stale_reason_taxonomy_counts": {
            key: taxonomy.get(key, 0) for key in (
                "TOKEN_NOT_ACTIVE_AT_CHECKPOINT",
                "ACTIVE_CONNECTION_NO_RECENT_DELTA",
                "CONNECTION_LIVENESS_UNPROVEN",
                "FRESHNESS_POLICY_EXCEEDED",
            )
        },
        "liveness_evidence_disposition": (
            "FAIL_CLOSED_FOR_LIVENESS_CLAIMS: rev2 froze subscription_epoch_id and last frame, "
            "but not requested/acknowledged/heartbeat/connection liveness. Stale rows keep reconstruction validity "
            "when baseline/identity/clock/gap permit, but remain freshness-ineligible."
        ),
        "transport_quality": transport_quality(diagnostics),
        "by_city": aggregate(("city",)),
        "by_city_target_date": aggregate(("city", "target_date")),
        "by_active_epoch": aggregate(("subscription_epoch_id",)),
        "diagnostic_rows": diagnostics,
        "legacy_files_modified": False,
    }


def transport_quality(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def groups(key_fn):
        output = []
        grouped: dict[Any, list[Mapping[str, Any]]] = collections.defaultdict(list)
        for row in rows:
            grouped[key_fn(row)].append(row)
        for key, selected in sorted(grouped.items(), key=lambda pair: str(pair[0])):
            output.append({
                "group": key,
                "event_relevant_rows": len(selected),
                "reconstruction_valid_rows": sum(row["reconstruction_valid"] for row in selected),
                "freshness_eligible_rows": sum(row["freshness_eligible"] for row in selected),
                "liveness_proven_rows": sum(row["connection_liveness_proven"] is True for row in selected),
            })
        return output
    return {
        "connection_day_health": "NOT_IDENTIFIABLE_FROM_REV2_FROZEN_ROWS_WITHOUT_CONNECTION_ID_AND_LIVENESS",
        "event_relevant_token_window_health": groups(lambda row: row["subscription_epoch_id"] or "NO_ACTIVE_EPOCH"),
        "city_target_date_health": groups(lambda row: f"{row.get('city')}|{row.get('target_date')}"),
        "controlled_reconnect_recovery": "NOT_IDENTIFIABLE_WITHOUT connection_id + reset/baseline transition evidence",
        "whole_day_any_blocker_gate_used": False,
    }


def latency_diagnostics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[float]] = collections.defaultdict(list)
    missing: collections.Counter[tuple[str, str]] = collections.Counter()
    for row in rows:
        parts = str(row.get("event_key") or "").split("|")
        city = parts[0] if parts else "UNKNOWN"
        source = parts[2] if len(parts) > 2 else "UNKNOWN"
        value = row.get("observed_pipeline_latency_sec")
        if value is None:
            missing[(city, source)] += 1
        else:
            groups[(city, source)].append(float(value))

    def nearest(values: Sequence[float], q: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        # Preserve the rev2 conservative percentile implementation exactly.
        return ordered[math.ceil((len(ordered) - 1) * q)]
    per = []
    for key in sorted(set(groups) | set(missing)):
        values = groups[key]
        per.append({
            "city": key[0], "source": key[1], "observed_n": len(values), "missing_n": missing[key],
            "p50_seconds": nearest(values, .50), "p90_seconds": nearest(values, .90), "p95_seconds": nearest(values, .95),
        })
    all_values = [value for values in groups.values() for value in values]
    return {
        "start_clock": "source_detect_ts_utc / source first_seen receive wall clock",
        "end_clock": "runner decision-ready after fresh-book fetch receive wall clock",
        "clock_domain": "host UTC wall receive; monotonic span was not frozen and is a rev2 evidence limit",
        "collector_epoch": "not frozen per latency row; pooled value is diagnostic only",
        "missing_handling": "retain row, exclude from percentile numerator, report missing_n",
        "outlier_handling": "no deletion or winsorization; nearest-rank percentiles",
        "per_city_source": per,
        "global_pooled_diagnostic": {
            "observed_n": len(all_values),
            "p50_seconds": nearest(all_values, .50),
            "p90_seconds": nearest(all_values, .90),
            "p95_seconds": nearest(all_values, .95),
            "primary_policy_authorized": False,
        },
    }


def denominator_diagnostic(
    rows: Sequence[Mapping[str, Any]], oracle_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    reasons = collections.Counter()
    oracle = {str(row["event_id"]): row for row in oracle_rows}
    for row in rows:
        if row.get("same_row_intersection_eligible"):
            continue
        primary = oracle[str(row["event_id"])]
        action = primary.get("action") or {}
        if action.get("action") == "BUY_OUTCOME_TOKEN":
            if not _feasible(primary.get("entry_sweep")):
                reasons["missing entry book"] += 1
            elif not _feasible(primary.get("exit_sweep")):
                reasons["missing exit book"] += 1
        elif action.get("reason") == "no_semantic_candidate_has_feasible_entry":
            reasons["chosen token unavailable"] += 1
        if row.get("persistence", {}).get("status") != "available" or row.get("recent_slope", {}).get("status") != "available" or row.get("forecast_only", {}).get("status") != "available":
            reasons["feature unavailable"] += 1
        if row.get("market_only_features", {}).get("status") != "available":
            reasons["market-only OOF blocked"] += 1
    eligible = [row for row in rows if row.get("same_row_intersection_eligible")]
    full = list(rows)
    histogram = {key: reasons.get(key, 0) for key in (
        "missing entry book", "missing exit book", "chosen token unavailable",
        "feature unavailable", "market-only OOF blocked", "other",
    )}
    return {
        "full_event_denominator": {
            "raw_n": len(full),
            "official_print_n": len({row.get("official_print_id") for row in full if row.get("official_print_id")}),
            "target_date_n": len({row["target_date"] for row in full}),
            "effective_n": len({(row["target_date"], row.get("official_print_id")) for row in full}),
            "no_trade_and_abstain_retained_with_zero_pnl": True,
        },
        "all_baseline_exact_intersection": {
            "raw_n": len(eligible),
            "official_print_n": len({row.get("official_print_id") for row in eligible if row.get("official_print_id")}),
            "target_date_n": len({row["target_date"] for row in eligible}),
            "effective_n": len({(row["target_date"], row.get("official_print_id")) for row in eligible}),
        },
        "reason_histogram_nonexclusive": histogram,
        "alpha_or_futility_claim_authorized": False,
        "note": "Reasons are non-exclusive because a row can lack both market data and a feature.",
    }


def build_oracle_family_harness(
    events: Sequence[Mapping[str, Any]],
    universes: Sequence[Mapping[str, Any]],
    matrix: Sequence[Mapping[str, Any]],
    oracle_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Materialize the frozen-latency semantic policy family without selection."""
    universe = {str(row["event_id"]): row for row in universes}
    books = {
        (str(row["event_id"]), str(row["checkpoint"]), str(row["token_id"])): row
        for row in matrix
    }
    oracle = {str(row["event_id"]): row for row in oracle_rows}
    policies = (
        "CROSS_ONLY_SEMANTIC_ORACLE",
        "PRIOR_CURRENT_EXACT_NO_ON_UPWARD_CROSS",
        "ACTUAL_NEXT_PRINT_BRACKET_YES",
        "LOWER_IMPOSSIBLE_BRACKETS_NO_ON_MULTI_TICK_CROSS",
        "CURRENT_BRACKET_YES_NO_NEW_MAX_DIAGNOSTIC",
        "NO_TRADE",
    )
    rows = []
    for event in sorted(events, key=lambda row: str(row["event_id"])):
        event_id = str(event["event_id"])
        tokens = list(universe[event_id]["tokens"])
        prior = float(event["metar_running_max_round_c"])
        actual = float(event["official_round_c"])
        lead_ok = float(oracle[event_id]["effective_lead_seconds"] or -math.inf) > 0
        for policy in policies:
            candidates = []
            for token in tokens:
                roles = set(token.get("roles") or ())
                outcome = str(token["outcome"])
                bracket = float(token["bracket_order"])
                desired = False
                if policy == "CROSS_ONLY_SEMANTIC_ORACLE":
                    desired = actual > prior and (
                        (outcome == "no" and "prior_exact_bracket" in roles)
                        or (outcome == "yes" and "actual_next_print_bracket" in roles)
                    )
                elif policy == "PRIOR_CURRENT_EXACT_NO_ON_UPWARD_CROSS":
                    desired = actual > prior and outcome == "no" and "prior_exact_bracket" in roles
                elif policy == "ACTUAL_NEXT_PRINT_BRACKET_YES":
                    desired = actual > prior and outcome == "yes" and "actual_next_print_bracket" in roles
                elif policy == "LOWER_IMPOSSIBLE_BRACKETS_NO_ON_MULTI_TICK_CROSS":
                    desired = actual >= prior + 2 and outcome == "no" and bracket < actual
                elif policy == "CURRENT_BRACKET_YES_NO_NEW_MAX_DIAGNOSTIC":
                    desired = actual <= prior and outcome == "yes" and "prior_exact_bracket" in roles
                if desired:
                    candidates.append(token)
            for shares in (1, 5):
                ranked = []
                for token in candidates:
                    entry = books.get((event_id, "entry_after_p95_latency", str(token["token_id"])), {})
                    sweep = (entry.get("sweeps") or {}).get(f"buy_{shares}")
                    if lead_ok and entry.get("book_valid") and _feasible(sweep):
                        ranked.append((float(sweep["effective_value_usd"]), str(token["token_id"]), token, sweep))
                selected = min(ranked, key=lambda value: (value[0], value[1])) if ranked else None
                for horizon in (5, 15, 30, 60, 120):
                    no_trade = policy == "NO_TRADE"
                    token = selected[2] if selected else None
                    entry_sweep = selected[3] if selected else None
                    exit_row = books.get((event_id, f"official_plus_{horizon}s", str(token["token_id"])), {}) if token else {}
                    exit_sweep = (exit_row.get("sweeps") or {}).get(f"sell_{shares}")
                    paired = bool(selected and exit_row.get("book_valid") and _feasible(exit_sweep))
                    pnl = (
                        float(exit_sweep["effective_value_usd"]) - float(entry_sweep["effective_value_usd"])
                        if paired else (0.0 if no_trade else None)
                    )
                    rows.append({
                        "event_id": event_id, "city": event["city"], "target_date": event["target_date"],
                        "official_print_id": event.get("information_event_id"),
                        "entry_mode": "FROZEN_OPERATIONAL_LATENCY_SLO",
                        "policy": policy, "shares": shares, "horizon_seconds": horizon,
                        "denominator_included": True,
                        "action": "NO_TRADE" if no_trade or not selected else "BUY_OUTCOME_TOKEN",
                        "abstain_reason": (
                            None if selected else (
                                "POLICY_NO_TRADE" if no_trade else
                                "SEMANTIC_NOT_APPLICABLE_OR_NO_FRESH_EXECUTABLE_ENTRY"
                            )
                        ),
                        "token_id": str(token["token_id"]) if token else None,
                        "entry_eligible": bool(selected), "exit_eligible": bool(paired),
                        "paired_executable": paired, "net_pnl_or_null": pnl,
                    })
    return rows, {
        "event_count": len(events),
        "policy_count": len(policies),
        "sizes": [1, 5], "horizons_seconds": [5, 15, 30, 60, 120],
        "row_count": len(rows),
        "frozen_latency_entry_materialized": True,
        "first_valid_fresh_after_source_entry_materialized": False,
        "first_valid_missing_reason": "rev2 checkpoint matrix does not contain every state transition after source; contract frozen for future collector evidence",
        "policy_or_city_horizon_selection_authorized": False,
        "stage2_data_gate_closed": False,
        "ex_post_envelope_used_for_action": False,
    }


def build_code_environment_freeze(output: Path) -> None:
    tracked = (
        "scripts/analysis/forecast_quality/research_wcir_stage02_stage03_rev2.py",
        "scripts/analysis/forecast_quality/verify_wcir_stage23_rev2_replay.py",
        "scripts/analysis/forecast_quality/package_wcir_stage23_rev2_review.py",
        "scripts/analysis/forecast_quality/wcir_stage23_rev2_closure.py",
        "scripts/analysis/forecast_quality/package_wcir_stage23_rev2_closure.py",
        "scripts/analysis/forecast_quality/wcir_collector_clock_shadow.py",
        "src/platform/market_data/executable_book_truth.py",
        "src/platform/market_data/ws_incremental_book.py",
        "tests/research_tests/test_wcir_stage23_rev2.py",
        "tests/research_tests/test_wcir_stage23_rev2_closure.py",
    )

    def run(*args: str) -> str:
        return subprocess.run(args, cwd=ROOT, check=True, text=True, capture_output=True).stdout.strip()

    dependencies = run(str(ROOT / ".venv/bin/python"), "-m", "pip", "freeze") + "\n"
    dependency_path = output / "DEPENDENCY_FREEZE.txt"
    dependency_path.write_text(dependencies, encoding="utf-8")
    prior_zip = REVIEW_ROOT / "stage_02_03_rev2_review/wcir-stage-02-03-rev2-gpt-pro-review-20260827T155641Z.zip"
    freeze = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repository": {
            "stage23_closure_start_head": "4def0c0ce7e02b1e716c023f4ddda42df4bc4112",
            "current_head": run("git", "rev-parse", "HEAD"),
            "branch": run("git", "branch", "--show-current"),
            "git_status_porcelain": run("git", "status", "--porcelain").splitlines(),
            "submodule_status": run("git", "submodule", "status").splitlines(),
        },
        "code_and_test_identities": [identity(ROOT / relative, root=ROOT) for relative in tracked],
        "environment": {
            "python": platform.python_version(),
            "python_executable": str(ROOT / ".venv/bin/python"),
            "os": platform.platform(),
            "machine": platform.machine(),
            "timezone": "Asia/Shanghai (CST +0800)",
            "locale": locale.setlocale(locale.LC_ALL, None),
            "dependency_freeze": identity(dependency_path, root=output),
        },
        "prior_compact_review_input": {
            **identity(prior_zip),
            "required_sha256": "43d8c6e03fea23e91c46125a374558383cab35e3f4a608283f0e16f4a3205e4b",
            "verified": sha256_file(prior_zip) == "43d8c6e03fea23e91c46125a374558383cab35e3f4a608283f0e16f4a3205e4b",
        },
        "production_boundary": {
            "production_manifest_strict_postcheck": "healthy (unsandboxed read-only check, 2026-08-28 CST)",
            "production_files_modified_by_this_change": False,
            "production_deployment_performed": False,
        },
    }
    if not freeze["prior_compact_review_input"]["verified"]:
        raise RuntimeError("prior compact review zip hash drift")
    write_json(output / "FINAL_CODE_AND_ENVIRONMENT_FREEZE.json", freeze)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=CLOSURE)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    legacy = [verify_legacy_manifest(STAGE2), verify_legacy_manifest(STAGE3)]
    matrix = read_jsonl_gz(STAGE2 / "evidence/EVENT_BOOK_COVERAGE_MATRIX.jsonl.gz")
    universes = read_jsonl_gz(STAGE2 / "evidence/FROZEN_EVENT_MARKET_UNIVERSE.jsonl.gz")
    oracle = read_jsonl_gz(STAGE3 / "evidence/PRIMARY_ORACLE_ROWS.jsonl.gz")
    reaction = read_jsonl_gz(STAGE3 / "evidence/REACTION_WINDOW_ROWS.jsonl.gz")
    baseline = read_jsonl_gz(STAGE3 / "evidence/MATCHED_BASELINE_ROWS.jsonl.gz")
    latency = read_jsonl_gz(STAGE2 / "evidence/FROZEN_PIPELINE_LATENCY_ROWS.jsonl.gz")
    reconciled, summary = build_reconciliation(
        oracle, reaction, baseline,
        read_json(STAGE3 / "CITY_GATES.json"),
        read_json(STAGE3 / "REACTION_INFERENCE_AND_CONCENTRATION.json"),
    )
    evidence = args.output / "evidence/PRIMARY_REACTION_GATE_ROW_RECONCILIATION.jsonl.gz"
    write_jsonl_gz(evidence, reconciled)
    write_json(args.output / "PRIMARY_REACTION_GATE_RECONCILIATION_SUMMARY.json", summary)
    meta = {str(row["event_id"]): row for row in oracle}
    validity = build_validity_corrigendum(matrix, meta)
    write_json(args.output / "BOOK_VALIDITY_CORRIGENDUM_RESULTS.json", validity)
    write_json(args.output / "LATENCY_CONTRACT_DIAGNOSTICS.json", latency_diagnostics(latency))
    write_json(args.output / "MATCHED_DENOMINATOR_DIAGNOSTIC.json", denominator_diagnostic(baseline, oracle))
    events = read_jsonl_gz(REVIEW_ROOT / "stage_03/evidence/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz")
    harness_rows, harness_summary = build_oracle_family_harness(events, universes, matrix, oracle)
    harness_path = args.output / "evidence/ORACLE_FAMILY_MEASUREMENT_ROWS.jsonl.gz"
    write_jsonl_gz(harness_path, harness_rows)
    write_json(args.output / "ORACLE_FAMILY_MEASUREMENT_HARNESS.json", {
        **harness_summary, "row_file": identity(harness_path)
    })
    build_code_environment_freeze(args.output)
    required = [
        STAGE2 / "RAW_LINEAGE_RANDOM_SAMPLE.json",
        STAGE2 / "REST_WS_COMPARABLE_PARITY.json",
        *(STAGE2 / "evidence" / name for name in (
            "EVENT_BOOK_COVERAGE_MATRIX.jsonl.gz", "FROZEN_EVENT_MARKET_UNIVERSE.jsonl.gz",
            "FROZEN_MARKET_TOKEN_IDENTITIES.jsonl.gz", "FROZEN_PIPELINE_LATENCY_ROWS.jsonl.gz",
            "REPLAY_CHUNKED_RESULT.json", "REPLAY_REVERSE_RESULT.json")),
        STAGE3 / "INDEPENDENT_PNL_RECALCULATION.json",
        *(STAGE3 / "evidence" / name for name in (
            "EX_POST_ENVELOPE_ROWS.jsonl.gz", "FROZEN_FORECAST_BASELINE_ROWS.jsonl.gz",
            "MATCHED_BASELINE_ROWS.jsonl.gz", "PRIMARY_ORACLE_ROWS.jsonl.gz", "REACTION_WINDOW_ROWS.jsonl.gz")),
    ]
    audit = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "legacy_manifests": legacy,
        "required_full_evidence": [identity(path) for path in required],
        "all_required_files_verified_against_legacy_manifests": True,
        "reconciliation_row_file": identity(evidence),
        "legacy_stage_2_3_artifacts_modified": False,
        "status": "PASS",
    }
    write_json(args.output / "COMPLETE_REV2_EVIDENCE_PACKAGE_AUDIT.json", audit)
    print(json.dumps({"output": str(args.output), "status": "PASS", "headlines": summary["canonical_headlines_from_single_row_table"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
