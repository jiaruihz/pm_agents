#!/usr/bin/env python3
"""Reusable prospective zero-notional Core Carry transition-card collector."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_INPUT = Path(
    "/Volumes/jrs/pm_agents/runtime/weather_edge_v1/"
    "current_yes_core_carry_tiny_live_v2/pre_live_scores.jsonl"
)
DEFAULT_OUT = Path(
    "/Volumes/jrs-archive/pm_agents/research/artifact_store/active/"
    "core_carry_llm_transition_card_v1"
)
PREREG = ROOT / (
    "docs/analysis/2026-08/"
    "2026-08-07-core-carry-llm-transition-card-v1-preregistration.json"
)
DEFAULT_DB = ROOT / "runtime/weather.db"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def bounded_exact(bracket: Any) -> bool:
    text = str(bracket or "")
    return bool(text) and "+" not in text and "or below" not in text.lower()


def fixed_denominator(
    rows: list[dict[str, Any]],
    *,
    start_date: str,
    end_date: str,
    cities: set[str],
    denominator_mode: str = "frozen_domain",
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts = Counter(raw_rows=len(rows))
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        target_date = str(row.get("target_date") or "")
        if not start_date <= target_date <= end_date:
            continue
        counts["date_scope"] += 1
        city = str(row.get("city") or "")
        if cities and city not in cities:
            continue
        counts["city_scope"] += 1
        if row.get("probability_status") != "scored_by_current_yes_core_artifact":
            continue
        counts["core_scored"] += 1
        if denominator_mode == "all_core_scored":
            key = (
                city,
                target_date,
                str(row.get("decision_snapshot_ts_utc") or ""),
                str(row.get("current_bracket") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            output.append(dict(row))
            continue
        market = finite(row.get("market_mid"))
        if market is None or not 0.80 <= market <= 0.9895:
            continue
        counts["frozen_market_domain"] += 1
        if not bounded_exact(row.get("current_bracket")):
            continue
        counts["bounded_exact"] += 1
        if not bool(row.get("in_research_window")):
            continue
        counts["research_window"] += 1
        key = (
            city,
            target_date,
            str(row.get("decision_snapshot_ts_utc") or ""),
            str(row.get("current_bracket") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(dict(row))
    output.sort(
        key=lambda row: (
            str(row.get("target_date") or ""),
            str(row.get("city") or ""),
            str(row.get("decision_snapshot_ts_utc") or ""),
        )
    )
    counts["fixed_denominator"] = len(output)
    return output, dict(counts)


def first_city_day(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""))
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
    return selected


def policy_bucket(row: dict[str, Any]) -> str:
    if row.get("decision_status") == "positive_taker_ev" and bool(row.get("eligible")):
        return "policy_selected"
    reasons = set(row.get("reasons") or [])
    priority = (
        ("non_positive_taker_ev", "rejected_non_positive_taker_ev"),
        ("insufficient_ask_ladder_depth", "rejected_depth"),
        ("missing_required_model_feature", "rejected_missing_model_feature"),
        ("outside_frozen_model_support", "rejected_model_support"),
        ("outside_frozen_market_mid_support", "rejected_market_support"),
        ("outside_carry_market_mid_domain", "rejected_market_domain"),
        ("open_ended_not_exact_bracket", "rejected_non_exact"),
    )
    for prefix, bucket in priority:
        if any(reason.startswith(prefix) for reason in reasons):
            return bucket
    return "rejected_other"


def _match_distance(anchor: dict[str, Any], candidate: dict[str, Any]) -> float:
    anchor_market = finite(anchor.get("market_mid"))
    candidate_market = finite(candidate.get("market_mid"))
    anchor_hour = finite(anchor.get("decision_hour_local"))
    candidate_hour = finite(candidate.get("decision_hour_local"))
    market_distance = (
        abs(anchor_market - candidate_market)
        if anchor_market is not None and candidate_market is not None
        else 1.0
    )
    hour_distance = (
        abs(anchor_hour - candidate_hour)
        if anchor_hour is not None and candidate_hour is not None
        else 12.0
    )
    same_city_penalty = 0.0 if anchor.get("city") == candidate.get("city") else 20.0
    return same_city_penalty + 5.0 * market_distance + hour_distance / 6.0


def policy_contrast_selection(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Select every policy hit plus pre-label near-miss and never-hit controls."""
    selected_rows = [row for row in rows if policy_bucket(row) == "policy_selected"]
    selected_city_days = {
        (str(row.get("city") or ""), str(row.get("target_date") or ""))
        for row in selected_rows
    }
    roles = {checkpoint_id(row): "policy_selected" for row in selected_rows}

    # Same-city-day near misses isolate the EV/price transition around a real hit.
    for anchor in selected_rows:
        anchor_ts = str(anchor.get("decision_snapshot_ts_utc") or "")
        candidates = [
            row
            for row in rows
            if row.get("city") == anchor.get("city")
            and row.get("target_date") == anchor.get("target_date")
            and policy_bucket(row) == "rejected_non_positive_taker_ev"
            and str(row.get("decision_snapshot_ts_utc") or "") <= anchor_ts
        ]
        if candidates:
            match = min(candidates, key=lambda row: _match_distance(anchor, row))
            roles.setdefault(checkpoint_id(match), "same_city_day_near_miss")

    # Never-selected city-days are chosen without settlement labels and matched on
    # city, market probability and local clock. A control is used at most once.
    control_pool = [
        row
        for row in rows
        if (str(row.get("city") or ""), str(row.get("target_date") or ""))
        not in selected_city_days
        and policy_bucket(row) == "rejected_non_positive_taker_ev"
        and bounded_exact(row.get("current_bracket"))
    ]
    used_controls: set[str] = set()
    for anchor in selected_rows:
        available = [row for row in control_pool if checkpoint_id(row) not in used_controls]
        if not available:
            break
        match = min(available, key=lambda row: _match_distance(anchor, row))
        cid = checkpoint_id(match)
        used_controls.add(cid)
        roles.setdefault(cid, "never_selected_matched_control")
    return roles


def checkpoint_id(row: dict[str, Any]) -> str:
    raw = "|".join(
        str(row.get(field) or "")
        for field in ("city", "target_date", "decision_snapshot_ts_utc", "current_bracket")
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def existing_cards(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return {
        str(row.get("checkpoint_id") or ""): row
        for row in read_jsonl(path)
        if row.get("checkpoint_id")
    }


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()


def canonical_settlement_labels(
    db_path: Path, keys: set[tuple[str, str, str]]
) -> dict[tuple[str, str, str], int]:
    if not db_path.exists() or not keys:
        return {}
    labels: dict[tuple[str, str, str], set[int]] = {}
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1000")
        for city, target_date, bracket, final_price in conn.execute(
            """
            SELECT city, target_date, bracket, final_price
            FROM settlement_outcomes
            WHERE settlement_status = 'settled' AND final_price IN (0.0, 1.0)
            """
        ):
            key = (str(city), str(target_date), str(bracket))
            if key in keys:
                labels.setdefault(key, set()).add(int(final_price))
    conflicts = {key: values for key, values in labels.items() if len(values) != 1}
    if conflicts:
        raise RuntimeError(f"conflicting canonical settlement labels: {conflicts}")
    return {key: next(iter(values)) for key, values in labels.items()}


def binary_metrics(rows: list[tuple[float, int]]) -> dict[str, float | int] | None:
    if not rows:
        return None
    clipped = [(min(1 - 1e-12, max(1e-12, probability)), label) for probability, label in rows]
    return {
        "n": len(clipped),
        "brier": sum((probability - label) ** 2 for probability, label in clipped) / len(clipped),
        "logloss": -sum(
            label * math.log(probability) + (1 - label) * math.log(1 - probability)
            for probability, label in clipped
        )
        / len(clipped),
    }


def summarize_policy_buckets(
    rows: list[dict[str, Any]], labels: dict[tuple[str, str, str], int]
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(policy_bucket(row), []).append(row)
    result: dict[str, dict[str, Any]] = {}
    for bucket, bucket_rows in sorted(grouped.items()):
        labelled: list[tuple[dict[str, Any], int]] = []
        for row in bucket_rows:
            key = (
                str(row.get("city") or ""),
                str(row.get("target_date") or ""),
                str(row.get("current_bracket") or ""),
            )
            if key in labels:
                labelled.append((row, labels[key]))
        market = [
            (probability, label)
            for row, label in labelled
            if (probability := finite(row.get("market_mid"))) is not None
        ]
        core = [
            (probability, label)
            for row, label in labelled
            if (probability := finite(row.get("model_probability_hold"))) is not None
        ]
        result[bucket] = {
            "rows": len(bucket_rows),
            "city_days": len(
                {(str(row.get("city") or ""), str(row.get("target_date") or "")) for row in bucket_rows}
            ),
            "settled_rows": len(labelled),
            "settled_target_dates": len(
                {str(row.get("target_date") or "") for row, _ in labelled}
            ),
            "hold_rate": (
                sum(label for _, label in labelled) / len(labelled) if labelled else None
            ),
            "mean_market_mid": (
                sum(probability for probability, _ in market) / len(market) if market else None
            ),
            "mean_frozen_core": (
                sum(probability for probability, _ in core) / len(core) if core else None
            ),
            "market_metrics": binary_metrics(market),
            "frozen_core_metrics": binary_metrics(core),
        }
    return result


def main() -> int:
    from src.agents.llm.codex_cli_client import run_codex_exec_json
    from src.strategies.weather_edge_v1.tools.intraday_transition_card import (
        CARD_SCHEMA_VERSION,
        PROMPT_VERSION,
        IntradayTransitionCard,
        build_transition_packet,
        transition_card_prompt,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument("--preregistration", type=Path, default=PREREG)
    parser.add_argument("--start-date", default="2026-08-07")
    parser.add_argument("--end-date", default=datetime.now().date().isoformat())
    parser.add_argument("--cities", nargs="*", default=[])
    parser.add_argument("--codex", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default="")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument(
        "--denominator-mode",
        choices=("frozen_domain", "all_core_scored"),
        default="frozen_domain",
    )
    parser.add_argument(
        "--selection-mode",
        choices=("first_city_day", "policy_contrast"),
        default="first_city_day",
    )
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    args = parser.parse_args()

    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("shard-index must be in [0, shard-count)")

    prereg_path = args.preregistration.resolve()
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    if not prereg.get("frozen_before_formal_run"):
        raise RuntimeError("preregistration is not frozen")
    rows, signal_funnel = fixed_denominator(
        read_jsonl(args.input),
        start_date=args.start_date,
        end_date=args.end_date,
        cities=set(args.cities),
        denominator_mode=args.denominator_mode,
    )
    if args.selection_mode == "policy_contrast":
        selection_roles = policy_contrast_selection(rows)
    else:
        selected = first_city_day(rows)
        selection_roles = {checkpoint_id(row): "first_city_day" for row in selected}
    selected_ids = set(selection_roles)
    selected = [row for row in rows if checkpoint_id(row) in selected_ids]
    packets = []
    for row in rows:
        packet = build_transition_packet(row)
        packets.append(
            {
                "checkpoint_id": checkpoint_id(row),
                "selected_for_llm": checkpoint_id(row) in selected_ids,
                "llm_selection_role": selection_roles.get(checkpoint_id(row)),
                "packet": packet,
            }
        )

    label_keys = {
        (str(row.get("city") or ""), str(row.get("target_date") or ""), str(row.get("current_bracket") or ""))
        for row in rows
    }
    settlement_labels = canonical_settlement_labels(args.db_path, label_keys)
    policy_bucket_summary = summarize_policy_buckets(rows, settlement_labels)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    packet_path = args.output_dir / "pit_packets.jsonl"
    with packet_path.open("w", encoding="utf-8") as handle:
        for record in packets:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    card_path = args.output_dir / "cards.jsonl"
    error_path = args.output_dir / "card_errors.jsonl"
    cards = existing_cards(card_path)
    generated = 0
    failures = 0
    if args.codex:
        assigned_ids = {
            cid
            for index, cid in enumerate(sorted(selected_ids))
            if index % args.shard_count == args.shard_index
        }
        for record in packets:
            cid = str(record["checkpoint_id"])
            if cid not in assigned_ids or cid in cards:
                continue
            if args.limit is not None and generated >= args.limit:
                break
            try:
                parsed = run_codex_exec_json(
                    prompt=transition_card_prompt(record["packet"]),
                    output_model=IntradayTransitionCard,
                    model=args.model or None,
                    reasoning_effort=args.reasoning_effort,
                    cwd=ROOT,
                    timeout_seconds=180,
                )
            except Exception as exc:  # Explicit evidence; never silently substitute a card.
                failures += 1
                append_jsonl(
                    error_path,
                    {
                        "schema_version": "core_carry_llm_transition_card_error_v1",
                        "checkpoint_id": cid,
                        "input_hash": record["packet"]["input_hash"],
                        "created_at_utc": datetime.now(timezone.utc).isoformat(),
                        "llm_model": args.model or "codex_cli_default",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                continue
            item = {
                "schema_version": CARD_SCHEMA_VERSION,
                "prompt_version": PROMPT_VERSION,
                "checkpoint_id": cid,
                "input_hash": record["packet"]["input_hash"],
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "review_mode": "prospective_unsettled",
                "llm_backend": "codex_cli",
                "llm_model": args.model or "codex_cli_default",
                "llm_reasoning_effort": args.reasoning_effort,
                "llm_selection_role": record["llm_selection_role"],
                "card": parsed,
            }
            append_jsonl(card_path, item)
            cards[cid] = item
            generated += 1

    ledger_path = args.output_dir / "challenger_ledger.jsonl"
    scored_market: list[tuple[float, int]] = []
    scored_core: list[tuple[float, int]] = []
    settled_selected_dates: set[str] = set()
    with ledger_path.open("w", encoding="utf-8") as handle:
        for row, record in zip(rows, packets):
            cid = str(record["checkpoint_id"])
            label_key = (
                str(row.get("city") or ""),
                str(row.get("target_date") or ""),
                str(row.get("current_bracket") or ""),
            )
            label = settlement_labels.get(label_key)
            market_mid = finite(row.get("market_mid"))
            p_frozen_core = finite(row.get("model_probability_hold"))
            if record["selected_for_llm"] and cid in cards and label is not None:
                settled_selected_dates.add(label_key[1])
                if market_mid is not None:
                    scored_market.append((market_mid, label))
                if p_frozen_core is not None:
                    scored_core.append((p_frozen_core, label))
            item = {
                "schema_version": "core_carry_llm_transition_challenger_ledger_v1",
                "checkpoint_id": cid,
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "decision_snapshot_ts_utc": row.get("decision_snapshot_ts_utc"),
                "current_bracket": row.get("current_bracket"),
                "market_mid": market_mid,
                "p_frozen_core": p_frozen_core,
                "current_yes_ask": finite(row.get("current_yes_ask")),
                "current_yes_ask_size": finite(row.get("current_yes_ask_size")),
                "current_yes_effective_cost": finite(
                    row.get("current_yes_effective_cost")
                ),
                "model_edge_after_fee_and_depth": finite(
                    row.get("model_edge_after_fee_and_depth")
                ),
                "decision_status": row.get("decision_status"),
                "decision_reasons": row.get("reasons") or [],
                "deterministic_state": {
                    "intraday_state": row.get("intraday_state"),
                    "warming_state": row.get("warming_state"),
                    "forecast_peak_clock_state": row.get("forecast_peak_clock_state"),
                    "wind_thermal_state": row.get("wind_thermal_state"),
                    "moisture_cloud_regime": row.get("moisture_cloud_regime"),
                    "precip_state": row.get("precip_state"),
                },
                "llm_selected": bool(record["selected_for_llm"]),
                "llm_selection_role": record["llm_selection_role"],
                "policy_bucket": policy_bucket(row),
                "llm_card_status": "complete" if cid in cards else "not_collected",
                "llm_card": (cards.get(cid) or {}).get("card"),
                "settlement_label": label,
                "zero_notional": True,
            }
            handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")

    selected_complete = sum(checkpoint_id(row) in cards for row in selected)
    settled_labels_complete = sum(
        checkpoint_id(row) in cards
        and (
            str(row.get("city") or ""),
            str(row.get("target_date") or ""),
            str(row.get("current_bracket") or ""),
        )
        in settlement_labels
        for row in selected
    )
    formal_same_row_ab_ready = len(settled_selected_dates) >= 10
    summary = {
        "schema_version": "core_carry_llm_transition_card_summary_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_id": prereg["research_id"],
        "input": str(args.input),
        "input_sha256": file_sha256(args.input),
        "preregistration": str(prereg_path.relative_to(ROOT)),
        "preregistration_sha256": file_sha256(prereg_path),
        "scope": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "cities": sorted(args.cities),
            "denominator_mode": args.denominator_mode,
            "selection_mode": args.selection_mode,
            "shard_index": args.shard_index,
            "shard_count": args.shard_count,
        },
        "signal_funnel": signal_funnel,
        "settlement_coverage": {
            "labelled_checkpoints": sum(
                int(summary["settled_rows"]) for summary in policy_bucket_summary.values()
            ),
            "labelled_target_dates": len(
                {
                    str(row.get("target_date") or "")
                    for row in rows
                    if (
                        str(row.get("city") or ""),
                        str(row.get("target_date") or ""),
                        str(row.get("current_bracket") or ""),
                    )
                    in settlement_labels
                }
            ),
        },
        "policy_bucket_summary": policy_bucket_summary,
        "evidence_funnel": {
            "pit_packets": len(packets),
            "first_city_day_llm_candidates": len(selected),
            "llm_candidate_roles": dict(Counter(selection_roles.values())),
            "llm_cards_complete": selected_complete,
            "settled_labels": settled_labels_complete,
            "settled_target_dates": len(settled_selected_dates),
            "formal_same_row_ab_ready": formal_same_row_ab_ready,
        },
        "same_row_seed_baselines": {
            "market_mid": binary_metrics(scored_market),
            "frozen_core": binary_metrics(scored_core),
            "interpretation": (
                "Eligible for the preregistered same-row A/B."
                if formal_same_row_ab_ready
                else "Seed diagnostics only; fewer than 10 settled target dates is not frozen-forward evidence."
            ),
        },
        "card_schema_version": CARD_SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "cards_generated_this_run": generated,
        "card_failures_this_run": failures,
        "verdict": "forward_collection_started_no_live_action",
        "blockers": [
            "Need at least 10 new settled target dates before frozen-forward proper-score comparison.",
            "Historical TAF/upstream/pressure first-seen coverage is insufficient for honest retrospective LLM cards.",
        ],
        "artifacts": {
            "packets": str(packet_path),
            "cards": str(card_path),
            "card_errors": str(error_path),
            "ledger": str(ledger_path),
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
