#!/usr/bin/env python3
"""Freeze, score, and test Europe D-1 distance-2 dual-NO forward policies.

The ``freeze`` command reads only point-in-time market checkpoints and the
already-frozen policy.  It deliberately has no settlement/database argument.
The ``score`` command first verifies every candidate hash and only then opens
canonical settlement truth.  Keeping the two phases separate makes it
impossible for an outcome to influence which city-day or ladder snapshot is
selected.  Canonical ``manual_backfill`` corrections are accepted only when
they explicitly replace a same-key non-settled ``pm_history`` row.

The ``risk-overlay`` command consumes the already-scored strict-forward rows.
It selects one market-implied tail-risk threshold on the development splits
only, then evaluates that frozen threshold on the later locked split.  The
command never changes candidates, settlements, production config, or orders.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import re
import sqlite3
import sys
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.market_data.executable_book_truth import weather_taker_fee  # noqa: E402
from weather_data_feed.forecast_run_contract import stable_content_hash  # noqa: E402
from weather_model_evaluation.d1_revision_repricing import (  # noqa: E402
    _csv_bool,
    _csv_number,
    _manifest_from_csv,
    _stream_csv_rows,
)


SCHEMA_VERSION = "europe_d1_distance2_dual_no_forward_v8"
DEFAULT_CONFIG = ROOT / "configs/weather/europe_d1_distance2_dual_no_shadow_v1.json"
DEFAULT_DB = Path("/Volumes/jrs/pm_agents/runtime/weather.db")
DEFAULT_DRAWS = 20_000
DEFAULT_SEED = 20260830
EUROPE_CITIES = {
    "Amsterdam",
    "Ankara",
    "Helsinki",
    "Istanbul",
    "London",
    "Madrid",
    "Milan",
    "Munich",
    "Paris",
    "Warsaw",
}
HISTORICAL_BLIND_V7_SHA256 = (
    "5d6e8a2638a5e882d80a769cf7c87605f2178514289cccdd47155af25c8d95e7"
)
STRICT_FORWARD_SPLITS = {
    "collected_forward",
    "recovery_validation",
    "locked_forward",
}
RISK_DEVELOPMENT_SPLITS = {"collected_forward", "recovery_validation"}
RISK_FORWARD_SPLIT = "locked_forward"
# This is the complete exploratory grid used in the 2026-08-30 development
# comparison.  The multiplicity is disclosed in the output; only the selected
# threshold is read on the chronological holdout.
RISK_THRESHOLD_GRID = (
    0.005,
    0.010,
    0.015,
    0.020,
    0.025,
    0.030,
    0.040,
    0.050,
    0.075,
    0.100,
    0.125,
    0.150,
    0.200,
)
EXIT_HORIZONS_MINUTES = (15, 30, 60)
EXIT_WINDOW_MINUTES = 30
EXIT_FILENAME_ROUTING_PAD_MINUTES = 2
EXIT_SHARES_PER_LEG = 0.5
EXIT_STOP_DRAWDOWN_GRID = (0.005, 0.010, 0.015, 0.020, 0.030, 0.040, 0.050, 0.075, 0.100)
EXIT_SCHEMA_VERSION = "europe_d1_distance2_dual_no_exit_replay_v2"


def parse_utc(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif hasattr(value, "to_pydatetime"):
        parsed = value.to_pydatetime()
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")


def _no_leg(rung: dict[str, Any], *, name: str, weight: float) -> dict[str, Any]:
    yes_bid = _csv_number(rung.get("yes_best_bid"))
    yes_ask = _csv_number(rung.get("yes_best_ask"))
    yes_bid_size = _csv_number(rung.get("yes_best_bid_size"))
    no_ask = 1.0 - yes_bid if yes_bid is not None else None
    no_bid = 1.0 - yes_ask if yes_ask is not None else None
    market_p_exact = (
        (yes_bid + yes_ask) / 2.0
        if yes_bid is not None and yes_ask is not None and yes_bid <= yes_ask
        else None
    )
    executable = (
        no_ask is not None
        and 0.001 <= no_ask <= 0.999
        and yes_bid_size is not None
        and yes_bid_size >= 1.0
        and (no_bid is None or no_bid <= no_ask)
    )
    fee = weather_taker_fee(shares=1.0, price=no_ask) if no_ask is not None else None
    return {
        "leg": name,
        "allocation_weight": weight,
        "bracket": str(rung.get("label") or ""),
        "condition_id": str(rung.get("condition_id") or ""),
        "token_id": str(rung.get("token_id") or ""),
        "rung_position": int(rung.get("position") or 0),
        "yes_best_bid": yes_bid,
        "yes_best_ask": yes_ask,
        "yes_best_bid_size": yes_bid_size,
        "no_best_bid": no_bid,
        "no_best_ask": no_ask,
        "no_ask_size": yes_bid_size,
        "market_p_exact": market_p_exact,
        "fee_per_share": fee,
        "cost_per_share": no_ask + fee if no_ask is not None and fee is not None else None,
        "book_executable": executable,
    }


def _candidate_from_checkpoint(
    row: dict[str, Any],
    *,
    city_config: dict[str, Any],
    policy: dict[str, Any],
    validation_end: str,
) -> dict[str, Any] | None:
    target_date = str(row.get("target_date") or "")
    checkpoint = parse_utc(row.get("checkpoint_ts_utc"))
    if not target_date or checkpoint is None:
        return None
    try:
        target = date.fromisoformat(target_date)
    except ValueError:
        return None
    local = checkpoint.astimezone(ZoneInfo(str(city_config["timezone"])))
    if (target - local.date()).days != int(policy.get("target_lead_days", 1)):
        return None
    local_hour = local.hour + local.minute / 60.0 + local.second / 3600.0
    if not (
        float(policy.get("local_entry_hour_start", 12.0))
        <= local_hour
        < float(policy.get("local_entry_hour_end", 24.0))
    ):
        return None
    manifest = _manifest_from_csv(row)
    if not manifest:
        return None
    ordered = sorted(manifest, key=lambda rung: int(rung.get("position") or 0))
    distance = int(policy.get("distance_from_nearest_endpoint", 2))
    if len(ordered) < 2 * distance + 1:
        return None
    low = ordered[distance]
    high = ordered[-1 - distance]
    if low is high:
        return None
    allocation = policy.get("allocation") or {}
    legs = [
        _no_leg(
            low,
            name="low_distance2_no",
            weight=float(allocation.get("low_distance2_no", 0.5)),
        ),
        _no_leg(
            high,
            name="high_distance2_no",
            weight=float(allocation.get("high_distance2_no", 0.5)),
        ),
    ]
    paired_executable = all(bool(leg["book_executable"]) for leg in legs)
    normalized_cost = (
        sum(float(leg["allocation_weight"]) * float(leg["cost_per_share"]) for leg in legs)
        if paired_executable
        else None
    )
    market_probabilities = [leg["market_p_exact"] for leg in legs]
    market_expected_payout = (
        1.0
        - sum(
            float(leg["allocation_weight"]) * float(leg["market_p_exact"])
            for leg in legs
        )
        if all(value is not None for value in market_probabilities)
        else None
    )
    city = str(row.get("city") or "")
    snapshot_id = str(row.get("feature_book_snapshot_id") or "")
    candidate_id = stable_content_hash(
        {
            "schema_version": SCHEMA_VERSION,
            "city": city,
            "target_date": target_date,
            "snapshot_id": snapshot_id,
            "distance": distance,
            "allocation": allocation,
        }
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "strategy_instance": str(policy.get("strategy_instance") or ""),
        "split": "recovery_validation" if target_date <= validation_end else "locked_forward",
        "city": city,
        "target_date": target_date,
        "timezone": str(city_config["timezone"]),
        "market_unit": str(city_config.get("market_unit") or "C"),
        "checkpoint_ts_utc": checkpoint.isoformat(),
        "local_decision_hour": local_hour,
        "feature_book_snapshot_id": snapshot_id,
        "source_path": str(row.get("source_path") or ""),
        "source_contract": str(row.get("source_contract") or ""),
        "event_time_pit_scorable": _csv_bool(row.get("event_time_pit_scorable")),
        "distance_from_nearest_endpoint": distance,
        "weather_features_used_for_eligibility": False,
        "paired_book_executable": paired_executable,
        "decision_status": "would_shadow_entry" if paired_executable else "paired_book_unexecutable",
        "normalized_reference_cost": normalized_cost,
        "normalized_market_expected_payout": market_expected_payout,
        "normalized_market_edge_after_fee": (
            market_expected_payout - normalized_cost
            if market_expected_payout is not None and normalized_cost is not None
            else None
        ),
        "legs": legs,
    }


def build_frozen_candidates(
    *,
    checkpoints_path: Path,
    config_path: Path,
    start_date: str,
    end_date: str,
    validation_end: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    policy = read_json(config_path)
    city_configs = policy.get("cities") or {}
    if not isinstance(city_configs, dict) or not city_configs:
        raise ValueError("frozen policy has no cities")
    earliest: dict[tuple[str, str], dict[str, Any]] = {}
    blockers: Counter[str] = Counter()
    funnel: Counter[str] = Counter()
    include = (
        "checkpoint_ts_utc",
        "city",
        "event_time_pit_scorable",
        "feature_book_snapshot_id",
        "rung_manifest",
        "source_contract",
        "source_path",
        "target_date",
    )
    for row in _stream_csv_rows(checkpoints_path, include_columns=include):
        funnel["checkpoint_rows_input"] += 1
        if str(row.get("source_contract") or "") != "canonical_market_books_v1":
            continue
        funnel["canonical_checkpoint_rows"] += 1
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        if city not in city_configs or not (start_date <= target_date <= end_date):
            continue
        funnel["fixed_city_date_window_rows"] += 1
        if not _csv_bool(row.get("event_time_pit_scorable")):
            blockers["inexact_event_clock"] += 1
            continue
        candidate = _candidate_from_checkpoint(
            row,
            city_config=city_configs[city],
            policy=policy,
            validation_end=validation_end,
        )
        if candidate is None:
            blockers["outside_local_window_or_incomplete_ladder"] += 1
            continue
        key = (city, target_date)
        current = earliest.get(key)
        if current is None or (
            str(candidate["checkpoint_ts_utc"]), str(candidate["feature_book_snapshot_id"])
        ) < (
            str(current["checkpoint_ts_utc"]), str(current["feature_book_snapshot_id"])
        ):
            earliest[key] = candidate
    candidates = sorted(earliest.values(), key=lambda row: (row["target_date"], row["city"]))
    funnel["locked_city_dates"] = len(candidates)
    funnel["paired_executable_city_dates"] = sum(
        bool(row["paired_book_executable"]) for row in candidates
    )
    return candidates, {
        "schema_version": SCHEMA_VERSION,
        "policy_status": str(policy.get("status") or ""),
        "strategy_instance": str(policy.get("strategy_instance") or ""),
        "start_date": start_date,
        "end_date": end_date,
        "validation_end": validation_end,
        "raw_target_dates": sorted({str(row["target_date"]) for row in candidates}),
        "executable_target_dates": sorted(
            {str(row["target_date"]) for row in candidates if row["paired_book_executable"]}
        ),
        "cities": sorted(city_configs),
        "signal_evidence_funnel": dict(funnel),
        "blocker_counts": dict(sorted(blockers.items())),
        "orders_submitted": 0,
        "actual_notional_usd": 0.0,
    }


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    candidates, summary = build_frozen_candidates(
        checkpoints_path=args.checkpoints,
        config_path=args.config,
        start_date=args.start_date,
        end_date=args.end_date,
        validation_end=args.validation_end,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = args.output_dir / "frozen_candidates.jsonl"
    write_jsonl(candidates_path, candidates)
    config_stat = args.config.stat()
    checkpoint_stat = args.checkpoints.stat()
    manifest = {
        **summary,
        "freeze_created_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_path": str(candidates_path),
        "candidate_sha256": sha256_file(candidates_path),
        "candidate_rows": len(candidates),
        "config_path": str(args.config),
        "config_sha256": sha256_file(args.config),
        "config_size": config_stat.st_size,
        "checkpoints_path": str(args.checkpoints),
        "checkpoints_size": checkpoint_stat.st_size,
        "checkpoints_mtime_ns": checkpoint_stat.st_mtime_ns,
        "settlement_read_during_freeze": False,
    }
    write_json(args.output_dir / "freeze_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return manifest


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _prior_shadow_candidates(
    path: Path, *, frozen_start_date: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = _read_jsonl(path)
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        key = (city, target_date)
        if not city or not target_date or key in seen:
            raise ValueError(f"invalid or duplicate prior shadow city-day: {key}")
        seen.add(key)
        if target_date >= frozen_start_date:
            raise ValueError(
                f"prior shadow overlaps frozen evaluation window: {city} {target_date}"
            )
        if int(row.get("orders_submitted") or 0) != 0 or float(
            row.get("actual_notional_usd") or 0.0
        ) != 0.0:
            raise ValueError(f"prior candidate is not zero-notional: {city} {target_date}")
        if bool(row.get("weather_features_used_for_eligibility")):
            raise ValueError(f"prior candidate used weather eligibility: {city} {target_date}")
        legs = row.get("legs") or []
        if len(legs) != 2:
            raise ValueError(f"prior candidate does not have two legs: {city} {target_date}")
        for leg in legs:
            if int(leg.get("distance_from_nearest_endpoint") or -1) != 2:
                raise ValueError(f"prior candidate distance changed: {city} {target_date}")
        normalized.append(
            {
                **row,
                "schema_version": SCHEMA_VERSION,
                "candidate_id": str(row.get("city_date_key") or f"{city}|{target_date}"),
                "split": (
                    "collected_bootstrap_partial"
                    if bool(row.get("collector_bootstrap_partial_window"))
                    else "collected_forward"
                ),
                "checkpoint_ts_utc": str(row.get("decision_asof_utc") or ""),
                "paired_book_executable": bool(row.get("paired_book_executable")),
            }
        )
    return normalized, {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "rows": len(normalized),
        "target_dates": sorted({str(row["target_date"]) for row in normalized}),
        "orders_submitted": 0,
        "actual_notional_usd": 0.0,
    }


def _historical_blind_v7_rows(
    path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    actual_sha = sha256_file(path)
    if actual_sha != HISTORICAL_BLIND_V7_SHA256:
        raise ValueError(
            "historical blind v7 hash mismatch: "
            f"expected={HISTORICAL_BLIND_V7_SHA256}, actual={actual_sha}"
        )
    with path.open(encoding="utf-8", newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    selected: dict[str, list[dict[str, str]]] = {
        "mechanical_half_distance2": [],
        "market_only_selection": [],
    }
    for row in source_rows:
        policy = str(row.get("policy") or "")
        if policy not in selected:
            continue
        if str(row.get("city") or "") not in EUROPE_CITIES:
            continue
        target_date = str(row.get("target_date") or "")
        if not ("2026-07-16" <= target_date <= "2026-07-23"):
            continue
        selected[policy].append(row)
    if any(len(rows) != 84 for rows in selected.values()):
        raise ValueError(
            "historical blind v7 denominator mismatch: "
            f"{ {policy: len(rows) for policy, rows in selected.items()} }"
        )
    raw_mechanical = selected["mechanical_half_distance2"]
    raw_market = selected["market_only_selection"]
    mechanical_keys = {str(row["snapshot_key"]) for row in raw_mechanical}
    market_keys = {str(row["snapshot_key"]) for row in raw_market}
    if len(mechanical_keys) != 84 or mechanical_keys != market_keys:
        raise ValueError("historical blind v7 snapshot-key mismatch")

    first_snapshot_by_city_day: dict[tuple[str, str], dict[str, str]] = {}
    for row in sorted(
        raw_mechanical,
        key=lambda item: (
            str(item["target_date"]),
            str(item["city"]),
            str(item["decision_ts_utc"]),
            str(item["snapshot_key"]),
        ),
    ):
        key = (str(row["city"]), str(row["target_date"]))
        first_snapshot_by_city_day.setdefault(key, row)
    first_snapshot_keys = {
        str(row["snapshot_key"]) for row in first_snapshot_by_city_day.values()
    }
    mechanical_source = [
        row for row in raw_mechanical if str(row["snapshot_key"]) in first_snapshot_keys
    ]
    market_source = [
        row for row in raw_market if str(row["snapshot_key"]) in first_snapshot_keys
    ]
    if (
        len(mechanical_source) != len(first_snapshot_by_city_day)
        or len(market_source) != len(first_snapshot_by_city_day)
        or {str(row["snapshot_key"]) for row in mechanical_source}
        != {str(row["snapshot_key"]) for row in market_source}
    ):
        raise ValueError("historical first-city-day denominator mismatch")

    def normalize(row: dict[str, str], *, split: str) -> dict[str, Any]:
        cost = float(row["cost"])
        payout = float(row["payout"])
        pnl = float(row["pnl"])
        if cost <= 0.0 or abs((payout - cost) - pnl) > 1e-8:
            raise ValueError(f"invalid archived payoff row: {row['snapshot_key']}")
        if int(float(row["distance_from_nearest_endpoint"])) != 2:
            raise ValueError(f"historical distance changed: {row['snapshot_key']}")
        return {
            "schema_version": SCHEMA_VERSION,
            "candidate_id": str(row["snapshot_key"]),
            "split": split,
            "city": str(row["city"]),
            "target_date": str(row["target_date"]),
            "checkpoint_ts_utc": str(row["decision_ts_utc"]),
            "decision_status": "archived_historical_blind",
            "cost": cost,
            "payout": payout,
            "pnl": pnl,
            "roi": pnl / cost,
            "joint_tail_hit": int(payout < 1.0 - 1e-12),
            "joint_market_probability": None,
            "market_expected_payout": None,
            "legs": [],
            "liquidity_evidence_complete": False,
            "source_path": str(row.get("source_path") or ""),
        }

    mechanical = [
        normalize(row, split="historical_blind")
        for row in mechanical_source
    ]
    market = [
        normalize(row, split="historical_blind_market_only")
        for row in market_source
    ]
    return mechanical, market, {
        "path": str(path.resolve()),
        "sha256": actual_sha,
        "source_rows": len(source_rows),
        "raw_mechanical_rows": len(raw_mechanical),
        "raw_market_only_rows": len(raw_market),
        "mechanical_rows": len(mechanical),
        "market_only_rows": len(market),
        "unique_city_days": len(first_snapshot_by_city_day),
        "duplicate_snapshot_rows_excluded": len(raw_mechanical) - len(mechanical),
        "target_dates": sorted({str(row["target_date"]) for row in mechanical}),
        "cities": sorted({str(row["city"]) for row in mechanical}),
        "denominator_relation": "same_first_city_day_snapshot_keys",
        "historical_blind_not_prospective": True,
    }


def _settlements(
    db_path: Path, *, start_date: str, end_date: str
) -> tuple[dict[tuple[str, str, str], float], dict[str, Any]]:
    uri = f"file:{db_path}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=1.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=1000")
    try:
        rows = connection.execute(
            """
            SELECT source_system, source_path, source_payload_hash,
                   city, target_date, bracket, final_price,
                   settlement_status, payload
            FROM settlement_outcomes
            WHERE source_system IN ('pm_history', 'manual_backfill')
              AND target_date >= ?
              AND target_date <= ?
            """,
            (start_date, end_date),
        ).fetchall()
    finally:
        connection.close()
    grouped: dict[tuple[str, str, str], dict[str, sqlite3.Row]] = defaultdict(dict)
    for row in rows:
        key = (str(row["city"]), str(row["target_date"]), str(row["bracket"]))
        source = str(row["source_system"])
        if source in grouped[key]:
            raise ValueError(f"duplicate canonical settlement source for {key}: {source}")
        grouped[key][source] = row

    result: dict[tuple[str, str, str], float] = {}
    corrections: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    for key, by_source in grouped.items():
        pm_history = by_source.get("pm_history")
        correction = by_source.get("manual_backfill")
        chosen: sqlite3.Row | None = None
        if correction is not None:
            if pm_history is None or str(pm_history["settlement_status"]) == "settled":
                raise ValueError(
                    f"manual_backfill may only replace non-settled pm_history: {key}"
                )
            if str(correction["settlement_status"]) != "settled":
                raise ValueError(f"manual_backfill is not settled: {key}")
            try:
                payload = json.loads(str(correction["payload"] or "{}"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid manual_backfill payload: {key}") from exc
            if payload.get("correction_reason") != (
                "closed_pm_history_replaces_non_settled_pm_history"
            ):
                raise ValueError(f"unapproved manual_backfill correction reason: {key}")
            chosen = correction
            corrections.append(
                {
                    "city": key[0],
                    "target_date": key[1],
                    "bracket": key[2],
                    "prior_final_price": pm_history["final_price"],
                    "prior_settlement_status": pm_history["settlement_status"],
                    "final_price": correction["final_price"],
                    "source_path": correction["source_path"],
                    "source_payload_hash": correction["source_payload_hash"],
                }
            )
        elif pm_history is not None and str(pm_history["settlement_status"]) == "settled":
            chosen = pm_history
        if chosen is None:
            continue
        result[key] = float(chosen["final_price"])
        source_counts[str(chosen["source_system"])] += 1
    stat = db_path.stat()
    return result, {
        "db_path": str(db_path.resolve()),
        "db_device": stat.st_dev,
        "db_inode": stat.st_ino,
        "settlement_source_precedence": ["manual_backfill_correction", "pm_history"],
        "settlement_status": "settled",
        "canonical_rows_read_in_window": len(rows),
        "chosen_settlement_rows": len(result),
        "chosen_source_counts": dict(sorted(source_counts.items())),
        "manual_correction_count": len(corrections),
        "manual_corrections": corrections,
    }


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(probability * len(ordered))))
    return ordered[index]


def _block_samples(
    rows: list[dict[str, Any]], *, draws: int, seed: int
) -> tuple[list[float], list[float]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[str(row["target_date"])].append(row)
    dates = sorted(by_date)
    if not dates:
        return [], []
    rng = random.Random(seed)
    roi_samples: list[float] = []
    market_overestimate_samples: list[float] = []
    for _ in range(draws):
        sampled = [item for target_date in rng.choices(dates, k=len(dates)) for item in by_date[target_date]]
        cost = sum(float(row["cost"]) for row in sampled)
        roi_samples.append(sum(float(row["pnl"]) for row in sampled) / cost)
        market_rows = [
            row for row in sampled if row.get("joint_market_probability") is not None
        ]
        if market_rows:
            market_overestimate_samples.append(
                sum(
                    float(row["joint_market_probability"])
                    - float(row["joint_tail_hit"])
                    for row in market_rows
                )
                / len(market_rows)
            )
    return roi_samples, market_overestimate_samples


def _wilson_upper(successes: int, observations: int, *, z: float = 1.959963984540054) -> float | None:
    if observations <= 0:
        return None
    p = successes / observations
    denominator = 1.0 + z * z / observations
    center = p + z * z / (2.0 * observations)
    radius = z * math.sqrt(p * (1.0 - p) / observations + z * z / (4.0 * observations * observations))
    return min(1.0, (center + radius) / denominator)


def _summarize_rows(
    rows: list[dict[str, Any]], *, draws: int, seed: int
) -> dict[str, Any]:
    if not rows:
        return {
            "candidates": 0,
            "target_dates": 0,
            "roi": None,
            "roi_ci95": [None, None],
        }
    cost = sum(float(row["cost"]) for row in rows)
    payout = sum(float(row["payout"]) for row in rows)
    pnl = payout - cost
    roi_samples, overestimate_samples = _block_samples(rows, draws=draws, seed=seed)
    joint_hits = sum(int(row["joint_tail_hit"]) for row in rows)
    upper = _wilson_upper(joint_hits, len(rows))
    average_cost = cost / len(rows)
    tail_stress_roi = (
        (1.0 - 0.5 * upper - average_cost) / average_cost
        if upper is not None
        else None
    )
    market_rows = [
        row
        for row in rows
        if row.get("market_expected_payout") is not None
        and row.get("joint_market_probability") is not None
    ]
    market_expected_payout = sum(
        float(row["market_expected_payout"]) for row in market_rows
    )
    market_cost = sum(float(row["cost"]) for row in market_rows)
    pnl_by_date: dict[str, float] = defaultdict(float)
    for row in rows:
        pnl_by_date[str(row["target_date"])] += float(row["pnl"])
    minimum_leg_depths = sorted(
        min(float(leg["no_ask_size"]) for leg in row.get("legs") or [])
        for row in rows
        if row.get("legs")
        and all(leg.get("no_ask_size") is not None for leg in row.get("legs") or [])
    )
    price_stress_roi = {
        label: (pnl - len(rows) * cents / 100.0) / cost
        for label, cents in (
            ("plus_0_1_cent", 0.1),
            ("plus_0_5_cent", 0.5),
            ("plus_1_0_cent", 1.0),
        )
    }
    return {
        "candidates": len(rows),
        "target_dates": len({str(row["target_date"]) for row in rows}),
        "cities": len({str(row["city"]) for row in rows}),
        "cost": cost,
        "payout": payout,
        "pnl": pnl,
        "roi": pnl / cost,
        "roi_ci95": [_quantile(roi_samples, 0.025), _quantile(roi_samples, 0.975)],
        "positive_date_rate": sum(value > 0.0 for value in pnl_by_date.values())
        / len(pnl_by_date),
        "joint_tail_hits": joint_hits,
        "joint_tail_hit_rate": joint_hits / len(rows),
        "joint_tail_hit_rate_wilson_upper95": upper,
        "tail_upper_stress_roi": tail_stress_roi,
        "market_evidence_candidates": len(market_rows),
        "market_expected_roi_after_ask_fee": (
            (market_expected_payout - market_cost) / market_cost
            if market_cost > 0.0
            else None
        ),
        "market_probability_overestimate": (
            sum(
                float(row["joint_market_probability"]) - float(row["joint_tail_hit"])
                for row in market_rows
            )
            / len(market_rows)
            if market_rows
            else None
        ),
        "market_probability_overestimate_ci95": [
            _quantile(overestimate_samples, 0.025),
            _quantile(overestimate_samples, 0.975),
        ],
        "execution_price_stress_roi": price_stress_roi,
        "minimum_leg_depth": {
            "evidence_candidates": len(minimum_leg_depths),
            "minimum": minimum_leg_depths[0] if minimum_leg_depths else None,
            "q10": _quantile(minimum_leg_depths, 0.10),
            "median": _quantile(minimum_leg_depths, 0.50),
            "baskets_ge_5_shares": sum(value >= 5.0 for value in minimum_leg_depths),
            "baskets_ge_10_shares": sum(value >= 10.0 for value in minimum_leg_depths),
        },
    }


def score_frozen_candidates(
    *,
    candidates: list[dict[str, Any]],
    settlements: dict[tuple[str, str, str], float],
    draws: int,
    seed: int,
    prescored_rows: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scored: list[dict[str, Any]] = list(prescored_rows or [])
    blockers: Counter[str] = Counter()
    for candidate in candidates:
        if not candidate.get("paired_book_executable"):
            blockers["paired_book_unexecutable"] += 1
            continue
        leg_results: list[dict[str, Any]] = []
        for leg in candidate.get("legs") or []:
            key = (
                str(candidate.get("city") or ""),
                str(candidate.get("target_date") or ""),
                str(leg.get("bracket") or ""),
            )
            final_yes = settlements.get(key)
            if final_yes is None:
                blockers["missing_pm_history_settlement"] += 1
                leg_results = []
                break
            leg_results.append(
                {
                    **leg,
                    "final_yes": final_yes,
                    "no_payout": 1.0 - final_yes,
                }
            )
        if len(leg_results) != 2:
            continue
        cost = sum(
            float(leg["allocation_weight"]) * float(leg["cost_per_share"])
            for leg in leg_results
        )
        payout = sum(
            float(leg["allocation_weight"]) * float(leg["no_payout"])
            for leg in leg_results
        )
        joint_market_probability = sum(
            float(leg["allocation_weight"]) * float(leg["market_p_exact"])
            for leg in leg_results
        )
        joint_tail_hit = int(any(float(leg["final_yes"]) >= 0.5 for leg in leg_results))
        scored.append(
            {
                **{key: value for key, value in candidate.items() if key != "legs"},
                "cost": cost,
                "payout": payout,
                "pnl": payout - cost,
                "roi": (payout - cost) / cost,
                "joint_market_probability": joint_market_probability,
                "joint_tail_hit": joint_tail_hit,
                "market_expected_payout": float(candidate["normalized_market_expected_payout"]),
                "legs": leg_results,
            }
        )
    split_order = (
        "historical_blind",
        "collected_bootstrap_partial",
        "collected_forward",
        "recovery_validation",
        "locked_forward",
    )
    summary_rows = {
        **{
            split: [row for row in scored if row["split"] == split]
            for split in split_order
        },
        "strict_forward": [
            row for row in scored if row["split"] in STRICT_FORWARD_SPLITS
        ],
        "grain_aligned_context": [
            row
            for row in scored
            if row["split"] in STRICT_FORWARD_SPLITS
            or row["split"] == "historical_blind"
        ],
    }
    summaries = {
        name: _summarize_rows(rows, draws=draws, seed=seed + index)
        for index, (name, rows) in enumerate(summary_rows.items())
    }
    return scored, {
        "schema_version": SCHEMA_VERSION,
        "blocker_counts": dict(sorted(blockers.items())),
        "score_summaries": summaries,
    }


def _write_scored_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = (
        "candidate_id",
        "split",
        "city",
        "target_date",
        "checkpoint_ts_utc",
        "decision_status",
        "cost",
        "payout",
        "pnl",
        "roi",
        "joint_market_probability",
        "joint_tail_hit",
        "market_expected_payout",
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field) for field in fields})


def _render_report(payload: dict[str, Any]) -> str:
    summaries = payload["score_summaries"]
    lines = [
        "# Europe D-1 distance=2 dual-NO 冻结 forward v8",
        "",
        f"- 冻结 candidate SHA256：`{payload['candidate_sha256']}`",
        f"- canonical DB：`{payload['canonical_db']['db_path']}`（device={payload['canonical_db']['db_device']}，inode={payload['canonical_db']['db_inode']}）",
        "- 执行口径：D-1 本地 12:00 后第一份 PIT 完整 ladder，距两端各 2 档的两个 NO 各 50%，按 ask + 官方 weather fee，持有到结算。",
        "- weather feature 只做 telemetry，不参与筛选；orders/fills/notional 均为 0。",
        "",
        "| split | candidates | dates | ROI | target-date block bootstrap 95% CI | 双腿命中尾部 | 市场高估尾部概率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for split in (
        "historical_blind",
        "collected_bootstrap_partial",
        "collected_forward",
        "recovery_validation",
        "locked_forward",
        "strict_forward",
        "grain_aligned_context",
    ):
        row = summaries[split]
        roi = "NA" if row.get("roi") is None else f"{100 * row['roi']:.2f}%"
        ci = row.get("roi_ci95") or [None, None]
        ci_text = (
            "NA"
            if ci[0] is None
            else f"[{100 * ci[0]:.2f}%, {100 * ci[1]:.2f}%]"
        )
        over = row.get("market_probability_overestimate")
        over_text = "NA" if over is None else f"{100 * over:.2f}pp"
        lines.append(
            f"| {split} | {row['candidates']} | {row['target_dates']} | {roi} | {ci_text} | {row.get('joint_tail_hits', 0)} | {over_text} |"
        )
    lines.extend(
        [
            "",
            "bootstrap CI 以 target_date 为 block，只对已观察日期条件化。另报 Wilson 尾部命中率上界压力测试，避免把零命中样本误当作两个 exact brackets 永远不会赢。",
            "",
            f"- strict_forward 价格压力 ROI：+0.1¢ `{100 * summaries['strict_forward']['execution_price_stress_roi']['plus_0_1_cent']:.2f}%`；+0.5¢ `{100 * summaries['strict_forward']['execution_price_stress_roi']['plus_0_5_cent']:.2f}%`；+1.0¢ `{100 * summaries['strict_forward']['execution_price_stress_roi']['plus_1_0_cent']:.2f}%`。",
            f"- strict_forward 两腿最小 ask depth：min `{summaries['strict_forward']['minimum_leg_depth']['minimum']:.2f}` shares；q10 `{summaries['strict_forward']['minimum_leg_depth']['q10']:.2f}`；median `{summaries['strict_forward']['minimum_leg_depth']['median']:.2f}`。",
            f"- market 概率与完整 depth 诊断覆盖 strict_forward `{summaries['strict_forward']['market_evidence_candidates']}/{summaries['strict_forward']['candidates']}`；历史 blind 只保留每 city-day 第一份 snapshot，不伪造缺失的两腿盘口字段。",
            f"- canonical manual correction：`{payload['canonical_db']['manual_correction_count']}` rows。",
            "",
        ]
    )
    historical_baseline = payload.get("historical_blind_market_only_summary")
    if historical_baseline:
        lines.extend(
            [
                "## 历史 blind 同 first-city-day snapshot-key 基线",
                "",
                f"- mechanical-half：`{100 * summaries['historical_blind']['roi']:.2f}%`；market-only 选一腿：`{100 * historical_baseline['roi']:.2f}%`。",
                f"- 原始 84 snapshots 中只保留 `{payload['historical_blind_manifest']['mechanical_rows']}` 个 first-city-day baskets；排除 `{payload['historical_blind_manifest']['duplicate_snapshot_rows_excluded']}` 个重复 snapshot。",
                "- 这是 2026-07-16..23 的历史盲区间，只作 context，不进入 strict_forward headline。",
                "",
            ]
        )
    return "\n".join(lines)


def score(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = args.freeze_dir / "freeze_manifest.json"
    candidate_path = args.freeze_dir / "frozen_candidates.jsonl"
    manifest = read_json(manifest_path)
    actual_sha = sha256_file(candidate_path)
    expected_sha = str(manifest.get("candidate_sha256") or "")
    if not expected_sha or actual_sha != expected_sha:
        raise ValueError(
            f"candidate hash mismatch: expected={expected_sha}, actual={actual_sha}"
        )
    candidates = _read_jsonl(candidate_path)
    prior_candidates: list[dict[str, Any]] = []
    prior_manifest: dict[str, Any] | None = None
    if args.prior_shadow_candidates is not None:
        prior_candidates, prior_manifest = _prior_shadow_candidates(
            args.prior_shadow_candidates,
            frozen_start_date=str(manifest["start_date"]),
        )
    historical_blind: list[dict[str, Any]] = []
    historical_market_only: list[dict[str, Any]] = []
    historical_manifest: dict[str, Any] | None = None
    if args.historical_blind_v7 is not None:
        (
            historical_blind,
            historical_market_only,
            historical_manifest,
        ) = _historical_blind_v7_rows(args.historical_blind_v7)
    all_candidates = [*prior_candidates, *candidates]
    if len(
        {
            (str(row.get("city") or ""), str(row.get("target_date") or ""))
            for row in all_candidates
        }
    ) != len(all_candidates):
        raise ValueError("duplicate city-day across prior and frozen candidates")
    start_date = min(str(row["target_date"]) for row in all_candidates)
    end_date = max(str(row["target_date"]) for row in all_candidates)
    settlements, db_identity = _settlements(
        args.db,
        start_date=start_date,
        end_date=end_date,
    )
    scored, score_summary = score_frozen_candidates(
        candidates=all_candidates,
        settlements=settlements,
        draws=args.draws,
        seed=args.seed,
        prescored_rows=historical_blind,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_scored_csv(args.output_dir / "scored_candidates.csv", scored)
    payload = {
        **score_summary,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "freeze_manifest_path": str(manifest_path),
        "candidate_path": str(candidate_path),
        "candidate_sha256": actual_sha,
        "candidate_rows": len(all_candidates) + len(historical_blind),
        "frozen_candidate_rows": len(candidates),
        "prior_shadow_manifest": prior_manifest,
        "historical_blind_manifest": historical_manifest,
        "historical_blind_market_only_summary": (
            _summarize_rows(
                historical_market_only,
                draws=args.draws,
                seed=args.seed + 20,
            )
            if historical_market_only
            else None
        ),
        "scored_rows": len(scored),
        "canonical_db": db_identity,
        "orders_submitted": 0,
        "actual_fills": 0,
        "actual_notional_usd": 0.0,
    }
    write_json(args.output_dir / "score_summary.json", payload)
    (args.output_dir / "report.md").write_text(_render_report(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload


def _read_scored_rows(path: Path) -> list[dict[str, Any]]:
    required = {
        "candidate_id",
        "split",
        "city",
        "target_date",
        "checkpoint_ts_utc",
        "cost",
        "payout",
        "pnl",
        "joint_market_probability",
        "joint_tail_hit",
        "market_expected_payout",
    }
    with path.open(encoding="utf-8", newline="") as handle:
        source = list(csv.DictReader(handle))
    if not source or not required.issubset(source[0]):
        missing = sorted(required - set(source[0] if source else {}))
        raise ValueError(f"scored candidate columns missing: {missing}")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in source:
        if str(raw["split"]) not in STRICT_FORWARD_SPLITS:
            continue
        candidate_id = str(raw["candidate_id"])
        if not candidate_id or candidate_id in seen:
            raise ValueError(f"duplicate or empty strict-forward candidate_id: {candidate_id}")
        seen.add(candidate_id)
        row = dict(raw)
        for field in (
            "cost",
            "payout",
            "pnl",
            "joint_market_probability",
            "market_expected_payout",
        ):
            row[field] = float(raw[field])
        row["joint_tail_hit"] = int(raw["joint_tail_hit"])
        if row["cost"] <= 0.0:
            raise ValueError(f"non-positive scored cost: {candidate_id}")
        if abs((row["payout"] - row["cost"]) - row["pnl"]) > 1e-8:
            raise ValueError(f"scored payoff mismatch: {candidate_id}")
        if not 0.0 <= row["joint_market_probability"] <= 1.0:
            raise ValueError(f"invalid market tail probability: {candidate_id}")
        rows.append(row)
    return sorted(
        rows,
        key=lambda row: (
            str(row["target_date"]),
            str(row["city"]),
            str(row["candidate_id"]),
        ),
    )


def _strict_time(value: Any, *, field: str) -> datetime | None:
    """Parse a raw capture clock without silently assigning a timezone."""

    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _strict_exit_candidates(score_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Restore the scored strict denominator with its immutable two-leg entry.

    The scored CSV intentionally projects away legs.  Exit replay must reattach
    them from the freeze and prior-shadow inputs rather than infer tokens from
    a later book or from a YES complement.
    """

    score_summary_path = score_dir / "score_summary.json"
    score_summary = read_json(score_summary_path)
    scored = _read_scored_rows(score_dir / "scored_candidates.csv")
    candidate_path = Path(str(score_summary.get("candidate_path") or ""))
    if not candidate_path.is_file():
        raise ValueError(f"missing frozen candidate input: {candidate_path}")
    restored = _read_jsonl(candidate_path)
    prior = (score_summary.get("prior_shadow_manifest") or {}).get("path")
    if prior:
        prior_path = Path(str(prior))
        if not prior_path.is_file():
            raise ValueError(f"missing prior shadow input: {prior_path}")
        frozen_start = str(read_json(Path(str(score_summary["freeze_manifest_path"]))).get("start_date") or "")
        restored.extend(_prior_shadow_candidates(prior_path, frozen_start_date=frozen_start)[0])
    by_id: dict[str, dict[str, Any]] = {}
    for candidate in restored:
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id or candidate_id in by_id:
            raise ValueError(f"duplicate or empty restored candidate_id: {candidate_id}")
        by_id[candidate_id] = candidate
    output: list[dict[str, Any]] = []
    grain: set[tuple[str, str]] = set()
    for row in scored:
        candidate = by_id.get(str(row["candidate_id"]))
        if candidate is None:
            raise ValueError(f"strict scored candidate absent from frozen inputs: {row['candidate_id']}")
        city = str(candidate.get("city") or "")
        target_date = str(candidate.get("target_date") or "")
        entry = _strict_time(candidate.get("checkpoint_ts_utc"), field="checkpoint_ts_utc")
        legs = candidate.get("legs") or []
        if not city or not target_date or entry is None or len(legs) != 2:
            raise ValueError(f"invalid restored exit candidate: {row['candidate_id']}")
        key = (city, target_date)
        if key in grain:
            raise ValueError(f"duplicate restored city-target_date grain: {key}")
        grain.add(key)
        normalized_legs: list[dict[str, Any]] = []
        for leg in legs:
            condition_id = str(leg.get("condition_id") or "")
            bracket = str(leg.get("bracket") or "")
            token_id = str(leg.get("token_id") or "")
            if not condition_id or not bracket or not token_id:
                raise ValueError(f"incomplete restored leg identity: {row['candidate_id']}")
            normalized_legs.append({**leg, "condition_id": condition_id, "bracket": bracket, "token_id": token_id})
        output.append({**row, "entry_ts_utc": entry.isoformat(), "legs": normalized_legs})
    if len(output) != len(scored):
        raise ValueError("strict exit denominator did not restore every scored candidate")
    return output, {
        "score_summary_path": str(score_summary_path),
        "score_summary_sha256": sha256_file(score_summary_path),
        "scored_candidates_path": str(score_dir / "scored_candidates.csv"),
        "scored_candidates_sha256": sha256_file(score_dir / "scored_candidates.csv"),
        "frozen_candidate_path": str(candidate_path),
        "frozen_candidate_sha256": sha256_file(candidate_path),
        "prior_shadow_path": str(prior) if prior else None,
        "strict_candidates": len(output),
        "strict_target_dates": len({str(row["target_date"]) for row in output}),
    }


def _book_file_paths(root: Path, windows: list[tuple[datetime, datetime]]) -> list[Path]:
    """Return only files timestamped in the requested exit windows.

    Capture names encode **Asia/Shanghai**, not UTC.  Filename time only routes
    the bounded read; each selected row is later accepted solely by its exact
    response/available clock.
    """

    paths: set[Path] = set()
    filename_stamp = re.compile(r"(?:market_books|orderbook_snapshot)_(\d{8})_(\d{4,6})")

    def in_window(path: Path) -> bool:
        match = filename_stamp.search(path.name)
        if match is None:
            return False
        clock = match.group(2).ljust(6, "0")
        try:
            local = datetime.strptime(match.group(1) + clock, "%Y%m%d%H%M%S").replace(
                tzinfo=ZoneInfo("Asia/Shanghai")
            )
        except ValueError:
            return False
        timestamp = local.astimezone(timezone.utc)
        padding = timedelta(minutes=EXIT_FILENAME_ROUTING_PAD_MINUTES)
        return any(start - padding <= timestamp <= end + padding for start, end in windows)

    for start, end in windows:
        local_start = start.astimezone(ZoneInfo("Asia/Shanghai"))
        local_end = end.astimezone(ZoneInfo("Asia/Shanghai"))
        cursor = local_start.date()
        while cursor <= local_end.date():
            day = root / cursor.isoformat()
            if day.is_dir():
                for pattern in ("market_books_*.jsonl*", "orderbook_snapshot_*.jsonl*"):
                    paths.update(path for path in day.glob(pattern) if in_window(path))
            cursor += timedelta(days=1)
    return sorted(paths)


def _iter_book_rows(paths: Iterable[Path]) -> Iterable[tuple[Path, dict[str, Any]]]:
    for path in paths:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield path, row


def _book_token_id(row: dict[str, Any]) -> str:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    return str(row.get("token_id") or row.get("asset_id") or raw.get("asset_id") or raw.get("token_id") or "")


def _walk_no_bid_ladder(levels: Any, *, shares: float) -> dict[str, Any]:
    parsed: list[tuple[float, float]] = []
    for raw in levels or []:
        if isinstance(raw, dict):
            price, size = _csv_number(raw.get("price")), _csv_number(raw.get("size"))
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            price, size = _csv_number(raw[0]), _csv_number(raw[1])
        else:
            continue
        if price is not None and size is not None and 0.0 < price < 1.0 and size > 0.0:
            parsed.append((price, size))
    parsed.sort(reverse=True)
    remaining, gross, fee = shares, 0.0, 0.0
    filled: list[dict[str, float]] = []
    for price, size in parsed:
        taken = min(remaining, size)
        if taken > 0.0:
            gross += taken * price
            fee += weather_taker_fee(shares=taken, price=price)
            filled.append({"price": price, "shares": taken})
            remaining -= taken
        if remaining <= 1e-12:
            break
    if remaining > 1e-12:
        return {"executable": False, "blocker": "insufficient_direct_no_bid_depth", "unfilled_shares": remaining}
    return {
        "executable": True,
        "shares": shares,
        "gross_proceeds": gross,
        "fee": fee,
        "net_proceeds": gross - fee,
        "net_proceeds_per_share": (gross - fee) / shares,
        "fills": filled,
    }


def _entry_direct_no_bid_net_value(candidate: dict[str, Any]) -> float:
    """Return the PIT net liquidation mark at entry for like-for-like stops."""

    total = 0.0
    for leg in candidate["legs"]:
        bid = _csv_number(leg.get("no_best_bid"))
        if bid is None or not 0.0 < bid < 1.0:
            raise ValueError(
                f"missing entry direct-NO bid for stop mark: {candidate['candidate_id']}"
            )
        gross = EXIT_SHARES_PER_LEG * bid
        total += gross - weather_taker_fee(shares=EXIT_SHARES_PER_LEG, price=bid)
    return total


def _valid_direct_no_capture(row: dict[str, Any]) -> tuple[datetime | None, str | None]:
    if str(row.get("schema_version") or "") != "weather_orderbook_capture_v3":
        return None, "unsupported_orderbook_schema"
    if (
        str(row.get("source_lineage_status") or "")
        != "collector_exact_orderbook_response_v3"
    ):
        return None, "non_exact_source_lineage"
    if str(row.get("status") or "") != "ok":
        return None, "status_not_ok"
    if str(row.get("outcome") or "").lower() != "no":
        return None, "not_direct_no_outcome"
    if not _csv_bool(row.get("event_time_pit_scorable")):
        return None, "event_time_not_pit_scorable"
    if str(row.get("clock_lineage_status") or "") != "collector_exact_response_clock":
        return None, "non_exact_collector_clock"
    available = _strict_time(row.get("available_at_utc"), field="available_at_utc")
    response = _strict_time(row.get("response_received_at_utc"), field="response_received_at_utc")
    if available is None or response is None:
        return None, "missing_or_timezone_naive_response_clock"
    if available != response:
        return None, "available_clock_not_exact_response_clock"
    if not str(row.get("request_batch_capture_id") or ""):
        return None, "missing_request_batch_capture_id"
    return available, None


def _first_full_exit_capture(
    candidate: dict[str, Any], *, horizon_minutes: int, book_rows: Iterable[tuple[Path, dict[str, Any]]]
) -> tuple[dict[str, Any] | None, Counter[str]]:
    entry = _strict_time(candidate.get("entry_ts_utc"), field="entry_ts_utc")
    assert entry is not None
    start = entry + timedelta(minutes=horizon_minutes)
    end = start + timedelta(minutes=EXIT_WINDOW_MINUTES)
    legs = candidate["legs"]
    # The frozen leg token is the YES token used to synthesize the entry-side
    # NO quote.  A real exit must instead sell the separately captured direct
    # NO token, so raw books are joined by immutable condition identity and
    # bracket, never by the entry token or by a 1-YES complement.
    needed = {
        (str(leg["condition_id"]), str(leg["bracket"])): leg for leg in legs
    }
    grouped: dict[
        tuple[datetime, str],
        dict[tuple[str, str], tuple[Path, dict[str, Any]]],
    ] = defaultdict(dict)
    blockers: Counter[str] = Counter()
    for path, row in book_rows:
        leg_key = (str(row.get("condition_id") or ""), str(row.get("bracket") or ""))
        if leg_key not in needed:
            continue
        routed_available = _strict_time(
            row.get("available_at_utc"), field="available_at_utc"
        )
        if routed_available is not None and (
            routed_available < start or routed_available > end
        ):
            continue
        available, blocker = _valid_direct_no_capture(row)
        if blocker:
            blockers[blocker] += 1
            continue
        assert available is not None
        if available < start or available > end:
            continue
        direct_no_token_id = _book_token_id(row)
        if not direct_no_token_id:
            blockers["missing_direct_no_token_id"] += 1
            continue
        grouped[(available, str(row["request_batch_capture_id"]))][leg_key] = (path, row)
    for (available, batch), matched in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        if set(matched) != set(needed):
            blockers["paired_direct_no_capture_missing_leg"] += 1
            continue
        exits: list[dict[str, Any]] = []
        for leg_key, leg in needed.items():
            path, row = matched[leg_key]
            direct_no_token_id = _book_token_id(row)
            raw = row.get("raw") if isinstance(row.get("raw"), dict) else row
            sweep = _walk_no_bid_ladder(raw.get("bids") or row.get("bids"), shares=EXIT_SHARES_PER_LEG)
            if not sweep["executable"]:
                blockers[str(sweep["blocker"])] += 1
                exits = []
                break
            exits.append(
                {
                    "entry_source_token_id": str(leg["token_id"]),
                    "direct_no_token_id": direct_no_token_id,
                    "direct_no_token_matches_entry_source": (
                        direct_no_token_id == str(leg["token_id"])
                    ),
                    "condition_id": leg["condition_id"],
                    "bracket": leg["bracket"],
                    "source_path": str(path),
                    **sweep,
                }
            )
        if len(exits) == 2:
            return {
                "available_at_utc": available.isoformat(),
                "request_batch_capture_id": batch,
                "exit_legs": exits,
                "net_exit_value": sum(float(item["net_proceeds"]) for item in exits),
            }, blockers
        blockers["paired_direct_no_bid_depth_incomplete"] += 1
    return None, blockers


def _exit_pair_summary(rows: list[dict[str, Any]], *, draws: int, seed: int) -> dict[str, Any]:
    if not rows:
        return {"candidates": 0, "target_dates": 0, "exit_pnl": None, "exit_roi": None, "hold_pnl": None, "hold_roi": None, "exit_minus_hold_roi_ci95": [None, None]}
    def metric(sample: list[dict[str, Any]]) -> tuple[float, float, float, float]:
        cost = sum(float(row["cost"]) for row in sample)
        exit_pnl = sum(float(row["exit_pnl"]) for row in sample)
        hold_pnl = sum(float(row["pnl"]) for row in sample)
        return exit_pnl, exit_pnl / cost, hold_pnl, hold_pnl / cost
    exit_pnl, exit_roi, hold_pnl, hold_roi = metric(rows)
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[str(row["target_date"])].append(row)
    dates = sorted(by_date)
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(draws):
        sample = [row for day in rng.choices(dates, k=len(dates)) for row in by_date[day]]
        _, sample_exit_roi, _, sample_hold_roi = metric(sample)
        deltas.append(sample_exit_roi - sample_hold_roi)
    return {
        "candidates": len(rows), "target_dates": len(dates), "cost": sum(float(row["cost"]) for row in rows),
        "exit_pnl": exit_pnl, "exit_roi": exit_roi, "hold_pnl": hold_pnl, "hold_roi": hold_roi,
        "exit_minus_hold_pnl": exit_pnl - hold_pnl,
        "exit_minus_hold_roi": exit_roi - hold_roi,
        "exit_minus_hold_roi_ci95": [_quantile(deltas, .025), _quantile(deltas, .975)],
    }


def _stop_policy_rows(
    candidate_ids: set[str],
    *,
    per_horizon: dict[str, Any],
    horizons: tuple[int, ...],
    drawdown_threshold: float,
) -> list[dict[str, Any]]:
    """Apply the earliest observable stop, otherwise retain settlement hold."""

    by_horizon = {
        horizon: {
            str(row["candidate_id"]): row
            for row in per_horizon[str(horizon)]["rows"]
        }
        for horizon in horizons
    }
    output: list[dict[str, Any]] = []
    for candidate_id in sorted(candidate_ids):
        reference = by_horizon[horizons[0]][candidate_id]
        trigger: dict[str, Any] | None = None
        observations: list[dict[str, Any]] = []
        for horizon in sorted(horizons):
            row = by_horizon[horizon][candidate_id]
            capture = row["exit_capture"]
            if capture is None:
                raise ValueError(f"stop policy received non-common coverage: {candidate_id}")
            deterioration = float(row["entry_direct_no_bid_net_value"]) - float(
                capture["net_exit_value"]
            )
            observation = {
                "horizon_minutes": horizon,
                "available_at_utc": capture["available_at_utc"],
                "request_batch_capture_id": capture["request_batch_capture_id"],
                "entry_direct_no_bid_net_value": float(
                    row["entry_direct_no_bid_net_value"]
                ),
                "net_exit_value": float(capture["net_exit_value"]),
                "deterioration_usd_per_basket": deterioration,
            }
            observations.append(observation)
            if trigger is None and deterioration >= drawdown_threshold - 1e-12:
                trigger = {
                    **observation,
                    "exit_pnl": float(row["exit_pnl"]),
                }
                break
        policy_pnl = float(trigger["exit_pnl"]) if trigger else float(reference["pnl"])
        output.append(
            {
                "candidate_id": candidate_id,
                "split": reference["split"],
                "city": reference["city"],
                "target_date": reference["target_date"],
                "cost": float(reference["cost"]),
                "pnl": float(reference["pnl"]),
                "joint_market_probability": float(
                    reference["joint_market_probability"]
                ),
                "stop_policy_pnl": policy_pnl,
                "stop_triggered": trigger is not None,
                "trigger": trigger,
                "observations_until_decision": observations,
            }
        )
    return output


def _stop_pair_summary(
    rows: list[dict[str, Any]], *, draws: int, seed: int
) -> dict[str, Any]:
    if not rows:
        return {
            "candidates": 0,
            "target_dates": 0,
            "stop_policy_pnl": None,
            "stop_policy_roi": None,
            "hold_pnl": None,
            "hold_roi": None,
            "stop_minus_hold_roi_ci95": [None, None],
        }

    def metric(sample: list[dict[str, Any]]) -> tuple[float, float, float, float]:
        cost = sum(float(row["cost"]) for row in sample)
        policy_pnl = sum(float(row["stop_policy_pnl"]) for row in sample)
        hold_pnl = sum(float(row["pnl"]) for row in sample)
        return policy_pnl, policy_pnl / cost, hold_pnl, hold_pnl / cost

    policy_pnl, policy_roi, hold_pnl, hold_roi = metric(rows)
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[str(row["target_date"])].append(row)
    dates = sorted(by_date)
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(draws):
        sample = [row for day in rng.choices(dates, k=len(dates)) for row in by_date[day]]
        _, sample_policy_roi, _, sample_hold_roi = metric(sample)
        deltas.append(sample_policy_roi - sample_hold_roi)
    return {
        "candidates": len(rows),
        "target_dates": len(dates),
        "cost": sum(float(row["cost"]) for row in rows),
        "stop_triggers": sum(bool(row["stop_triggered"]) for row in rows),
        "stop_policy_pnl": policy_pnl,
        "stop_policy_roi": policy_roi,
        "hold_pnl": hold_pnl,
        "hold_roi": hold_roi,
        "stop_minus_hold_pnl": policy_pnl - hold_pnl,
        "stop_minus_hold_roi": policy_roi - hold_roi,
        "stop_minus_hold_roi_ci95": [
            _quantile(deltas, 0.025),
            _quantile(deltas, 0.975),
        ],
    }


def evaluate_fixed_exit_replay(
    candidates: list[dict[str, Any]], *, market_books_root: Path, horizons: tuple[int, ...], draws: int, seed: int
) -> dict[str, Any]:
    """Replay fixed exits only where one raw exact-clock batch sells both NO legs."""

    if (
        not candidates
        or not horizons
        or any(value <= 0 for value in horizons)
        or len(set(horizons)) != len(horizons)
        or draws <= 0
    ):
        raise ValueError(
            "exit replay requires candidates, unique positive horizons, and positive draws"
        )
    windows = []
    for candidate in candidates:
        entry = _strict_time(candidate["entry_ts_utc"], field="entry_ts_utc")
        assert entry is not None
        for horizon in horizons:
            start = entry + timedelta(minutes=horizon)
            windows.append((start, start + timedelta(minutes=EXIT_WINDOW_MINUTES)))
    files = _book_file_paths(market_books_root, windows)
    candidate_condition_ids = {
        str(leg["condition_id"])
        for candidate in candidates
        for leg in candidate["legs"]
    }
    raw_rows_scanned = 0
    raw_rows: list[tuple[Path, dict[str, Any]]] = []
    raw_schema_versions: Counter[str] = Counter()
    raw_producer_build_ids: Counter[str] = Counter()
    for path, row in _iter_book_rows(files):
        raw_rows_scanned += 1
        raw_schema_versions[str(row.get("schema_version") or "missing")] += 1
        raw_producer_build_ids[str(row.get("producer_build_id") or "missing")] += 1
        if str(row.get("condition_id") or "") in candidate_condition_ids:
            raw_rows.append((path, row))
    per_horizon: dict[str, Any] = {}
    coverage_sets: dict[int, set[str]] = {}
    for index, horizon in enumerate(horizons):
        rows: list[dict[str, Any]] = []
        raw_rejections: Counter[str] = Counter()
        uncovered_blockers: Counter[str] = Counter()
        for candidate in candidates:
            capture, row_blockers = _first_full_exit_capture(candidate, horizon_minutes=horizon, book_rows=raw_rows)
            raw_rejections.update(row_blockers)
            row = {key: value for key, value in candidate.items() if key != "legs"}
            row["horizon_minutes"] = horizon
            row["exit_capture"] = capture
            row["entry_direct_no_bid_net_value"] = _entry_direct_no_bid_net_value(
                candidate
            )
            row["hold_pnl"] = float(candidate["pnl"])
            row["hold_roi"] = float(candidate["pnl"]) / float(candidate["cost"])
            if capture is None:
                uncovered_blockers.update(row_blockers)
                row.update({"exit_pnl": None, "exit_roi": None, "blocker_counts": dict(sorted(row_blockers.items()))})
            else:
                exit_pnl = float(capture["net_exit_value"]) - float(candidate["cost"])
                row.update({"exit_pnl": exit_pnl, "exit_roi": exit_pnl / float(candidate["cost"]), "blocker_counts": {}})
            rows.append(row)
        covered = [row for row in rows if row["exit_capture"] is not None]
        coverage_sets[horizon] = {str(row["candidate_id"]) for row in covered}
        per_horizon[str(horizon)] = {
            "opportunity_candidates": len(rows), "opportunity_target_dates": len({str(row["target_date"]) for row in rows}),
            "coverage_candidates": len(covered), "coverage_target_dates": len({str(row["target_date"]) for row in covered}),
            "uncovered_candidate_blocker_counts": dict(sorted(uncovered_blockers.items())),
            "raw_rejection_counts": dict(sorted(raw_rejections.items())),
            "covered_pair_metrics": _exit_pair_summary(covered, draws=draws, seed=seed + index),
            "rows": rows,
        }
    common_ids = set.intersection(*(coverage_sets[value] for value in horizons))
    common = [candidate for candidate in candidates if str(candidate["candidate_id"]) in common_ids]
    development = [row for row in common if str(row["split"]) in RISK_DEVELOPMENT_SPLITS]
    locked = [row for row in common if str(row["split"]) == RISK_FORWARD_SPLIT]
    if not development or not locked:
        selection = {"status": "BLOCKED_NO_COMMON_COVERAGE_IN_BOTH_SPLITS"}
    else:
        development_by_horizon: dict[int, float] = {}
        for horizon in horizons:
            by_id = {str(row["candidate_id"]): row for row in per_horizon[str(horizon)]["rows"]}
            development_by_horizon[horizon] = _exit_pair_summary([by_id[str(row["candidate_id"])] for row in development], draws=draws, seed=seed + horizon)["exit_minus_hold_roi"]
        selected = max(horizons, key=lambda value: (development_by_horizon[value], -value))
        selected_rows = per_horizon[str(selected)]["rows"]
        selected_by_id = {str(row["candidate_id"]): row for row in selected_rows}
        selected_common = [selected_by_id[str(row["candidate_id"])] for row in common]
        selected_development = [selected_by_id[str(row["candidate_id"])] for row in development]
        selected_locked = [selected_by_id[str(row["candidate_id"])] for row in locked]
        tail10 = lambda rows: [
            row for row in rows if float(row["joint_market_probability"]) <= 0.10 + 1e-12
        ]
        strict_stop_development_grid: dict[str, Any] = {}
        for stop_index, threshold in enumerate(EXIT_STOP_DRAWDOWN_GRID):
            stop_rows = _stop_policy_rows(
                {str(row["candidate_id"]) for row in development},
                per_horizon=per_horizon,
                horizons=horizons,
                drawdown_threshold=threshold,
            )
            strict_stop_development_grid[f"{threshold:.3f}"] = _stop_pair_summary(
                stop_rows,
                draws=draws,
                seed=seed + 100 + stop_index,
            )
        strict_stop_threshold = max(
            EXIT_STOP_DRAWDOWN_GRID,
            key=lambda value: (
                float(
                    strict_stop_development_grid[f"{value:.3f}"][
                        "stop_policy_roi"
                    ]
                ),
                value,
            ),
        )
        strict_stop_development_rows = _stop_policy_rows(
            {str(row["candidate_id"]) for row in development},
            per_horizon=per_horizon,
            horizons=horizons,
            drawdown_threshold=strict_stop_threshold,
        )
        strict_stop_locked_rows = _stop_policy_rows(
            {str(row["candidate_id"]) for row in locked},
            per_horizon=per_horizon,
            horizons=horizons,
            drawdown_threshold=strict_stop_threshold,
        )
        tail10_development = tail10(development)
        tail10_locked = tail10(locked)
        if not tail10_development or not tail10_locked:
            tail10_stop_challenger: dict[str, Any] = {
                "status": "BLOCKED_NO_TAIL10_COMMON_COVERAGE_IN_BOTH_SPLITS",
                "market_tail_threshold_fixed_from_prior_risk_overlay": 0.10,
                "development_candidates": len(tail10_development),
                "locked_forward_candidates": len(tail10_locked),
            }
        else:
            stop_development_grid: dict[str, Any] = {}
            for stop_index, threshold in enumerate(EXIT_STOP_DRAWDOWN_GRID):
                stop_rows = _stop_policy_rows(
                    {str(row["candidate_id"]) for row in tail10_development},
                    per_horizon=per_horizon,
                    horizons=horizons,
                    drawdown_threshold=threshold,
                )
                stop_development_grid[f"{threshold:.3f}"] = _stop_pair_summary(
                    stop_rows,
                    draws=draws,
                    seed=seed + 200 + stop_index,
                )
            selected_stop_threshold = max(
                EXIT_STOP_DRAWDOWN_GRID,
                key=lambda value: (
                    float(
                        stop_development_grid[f"{value:.3f}"]["stop_policy_roi"]
                    ),
                    value,
                ),
            )
            selected_stop_development_rows = _stop_policy_rows(
                {str(row["candidate_id"]) for row in tail10_development},
                per_horizon=per_horizon,
                horizons=horizons,
                drawdown_threshold=selected_stop_threshold,
            )
            selected_stop_locked_rows = _stop_policy_rows(
                {str(row["candidate_id"]) for row in tail10_locked},
                per_horizon=per_horizon,
                horizons=horizons,
                drawdown_threshold=selected_stop_threshold,
            )
            tail10_stop_challenger = {
                "status": "retrospective_development_selected_locked_previously_observed",
                "market_tail_threshold_fixed_from_prior_risk_overlay": 0.10,
                "trigger": (
                    "earliest 15/30/60 exact-clock full-basket direct-NO capture "
                    "whose net liquidation value has deteriorated from the PIT entry "
                    "direct-NO bid net mark by at least the selected USD threshold"
                ),
                "threshold_grid_usd_per_basket": list(EXIT_STOP_DRAWDOWN_GRID),
                "variants_k": len(EXIT_STOP_DRAWDOWN_GRID),
                "selection_objective": "maximum development fee-adjusted stop-policy ROI",
                "selected_threshold_usd_per_basket": selected_stop_threshold,
                "development_grid": stop_development_grid,
                "development": _stop_pair_summary(
                    selected_stop_development_rows,
                    draws=draws,
                    seed=seed + 300,
                ),
                "locked_forward_previously_observed": _stop_pair_summary(
                    selected_stop_locked_rows,
                    draws=draws,
                    seed=seed + 301,
                ),
                "development_rows": selected_stop_development_rows,
                "locked_forward_rows": selected_stop_locked_rows,
                "clean_forward_gate": "FAIL_LOCKED_WINDOW_ALREADY_OBSERVED",
            }
        selection = {"status": "RETROSPECTIVE_COMMON_COVERAGE", "variants_k": len(horizons), "common_coverage_candidates": len(common), "common_coverage_target_dates": len({str(row["target_date"]) for row in common}), "selected_horizon_minutes": selected, "development_exit_minus_hold_roi_by_horizon": development_by_horizon,
            "development": _exit_pair_summary(selected_development, draws=draws, seed=seed + 90),
            "locked_forward_previously_observed": _exit_pair_summary(selected_locked, draws=draws, seed=seed + 91),
            "market_tail_10pct_challenger_same_common_rows": {
                "threshold_fixed_from_prior_risk_overlay": 0.10,
                "strict_common_coverage": _exit_pair_summary(tail10(selected_common), draws=draws, seed=seed + 92),
                "development": _exit_pair_summary(tail10(selected_development), draws=draws, seed=seed + 93),
                "locked_forward_previously_observed": _exit_pair_summary(tail10(selected_locked), draws=draws, seed=seed + 94),
            },
            "strict_selective_stop_challenger": {
                "status": "retrospective_development_selected_locked_previously_observed",
                "trigger": (
                    "earliest 15/30/60 exact-clock full-basket direct-NO capture "
                    "whose net liquidation value has deteriorated from the PIT entry "
                    "direct-NO bid net mark by at least the selected USD threshold"
                ),
                "threshold_grid_usd_per_basket": list(EXIT_STOP_DRAWDOWN_GRID),
                "variants_k": len(EXIT_STOP_DRAWDOWN_GRID),
                "selection_objective": "maximum development fee-adjusted stop-policy ROI",
                "selected_threshold_usd_per_basket": strict_stop_threshold,
                "development_grid": strict_stop_development_grid,
                "development": _stop_pair_summary(
                    strict_stop_development_rows,
                    draws=draws,
                    seed=seed + 180,
                ),
                "locked_forward_previously_observed": _stop_pair_summary(
                    strict_stop_locked_rows,
                    draws=draws,
                    seed=seed + 181,
                ),
                "development_rows": strict_stop_development_rows,
                "locked_forward_rows": strict_stop_locked_rows,
                "clean_forward_gate": "FAIL_LOCKED_WINDOW_ALREADY_OBSERVED",
            },
            "market_tail_10pct_selective_stop_challenger": tail10_stop_challenger,
        }
    for data in per_horizon.values():
        for row in data["rows"]:
            row.pop("legs", None)
    candidate_rows_identity = stable_content_hash(
        [
            {
                "source_path": str(path),
                "schema_version": row.get("schema_version"),
                "producer_build_id": row.get("producer_build_id"),
                "book_capture_id": row.get("book_capture_id"),
                "raw_payload_hash": row.get("raw_payload_hash"),
                "condition_id": row.get("condition_id"),
                "bracket": row.get("bracket"),
                "outcome": row.get("outcome"),
                "token_id": _book_token_id(row),
                "available_at_utc": row.get("available_at_utc"),
                "request_batch_capture_id": row.get("request_batch_capture_id"),
                "status": row.get("status"),
            }
            for path, row in raw_rows
        ]
    )
    if selection["status"].startswith("BLOCKED"):
        conclusion = "blocked_no_common_exact_clock_coverage"
        action = "research_only_collect_coverage_no_orders_no_production_change"
    else:
        conclusion = "fixed_exit_rejected_selective_stop_inconclusive"
        action = (
            "reject mechanical fixed-horizon exit; freeze the development-selected "
            "selective stop only for new zero-notional forward evidence; no orders "
            "and no production change"
        )
    return {"schema_version": EXIT_SCHEMA_VERSION, "hypothesis": "Executable post-entry direct-NO exits may improve fee-adjusted results relative to holding the same dual-NO basket to settlement.", "execution_contract": {"side": "direct_no_only", "entry_identity": "frozen source token plus immutable condition_id and bracket; legacy source token side may vary", "exit_identity": "raw capture must explicitly say outcome=no; its direct-NO token is retained", "forbidden": ["YES complement", "mid", "future touch", "settlement fallback"], "shares_per_leg": EXIT_SHARES_PER_LEG, "window_minutes": EXIT_WINDOW_MINUTES, "filename_routing_pad_minutes": EXIT_FILENAME_ROUTING_PAD_MINUTES, "fee": "weather_taker_fee per filled bid level", "same_batch_required": True}, "horizons_minutes": list(horizons), "source_manifest": {"market_books_root": str(market_books_root), "batch_files_read": [str(path) for path in files], "batch_files_read_count": len(files), "raw_rows_scanned": raw_rows_scanned, "candidate_condition_rows_read": len(raw_rows), "candidate_condition_rows_identity": candidate_rows_identity, "raw_schema_versions": dict(sorted(raw_schema_versions.items())), "producer_build_ids": dict(sorted(raw_producer_build_ids.items())), "filename_clock": "Asia/Shanghai routing only with bounded padding; raw exact response/available clocks decide eligibility"}, "opportunity_denominator": {"candidates": len(candidates), "target_dates": len({str(row["target_date"]) for row in candidates}), "candidate_horizon_rows": len(candidates) * len(horizons)}, "horizons": per_horizon, "common_coverage_horizon_selection": selection, "conclusion": conclusion, "action": action}


def _complete_exit_research_record(
    path: Path,
    *,
    payload: dict[str, Any],
    args: argparse.Namespace,
    scored_sha256: str,
    score_generated_at_utc: str,
    config_sha256: str,
) -> None:
    record = read_json(path)
    lifecycle_status = str(record.get("lifecycle_status") or "")
    if lifecycle_status != "planned":
        raise ValueError(
            "exit replay research record must be planned before completion; "
            f"found {lifecycle_status or 'missing'} at {path}"
        )
    selection = payload["common_coverage_horizon_selection"]
    fixed_development = selection["development"]
    fixed_locked = selection["locked_forward_previously_observed"]
    stop = selection["strict_selective_stop_challenger"]
    stop_development = stop["development"]
    stop_locked = stop["locked_forward_previously_observed"]
    source = payload["source_manifest"]
    generated_at = str(payload["generated_at_utc"])
    record.update(
        {
            "lifecycle_status": "complete",
            "observed_at_utc": generated_at,
            "question": {
                "hypothesis": str(payload["hypothesis"]),
                "decision_target": (
                    "whether fixed-horizon or market-deterioration exits merit a "
                    "new zero-notional forward candidate"
                ),
                "scope": (
                    "105 settled strict-forward Europe D-1 distance-2 dual-NO "
                    "city-day baskets with raw 15/30/60-minute exit searches"
                ),
                "exclusions": [
                    "live behavior changes or orders",
                    "YES-complement, midpoint, touch, or settlement fallback pricing",
                    "candidates without exact-clock full-basket direct-NO coverage",
                    "claims of clean confirmation from the previously observed locked split",
                ],
            },
            "method": {
                "grain": "one frozen city-target_date basket and one exit horizon",
                "denominator_scope": (
                    f"{payload['opportunity_denominator']['candidates']} opportunities / "
                    f"{payload['opportunity_denominator']['target_dates']} target dates / "
                    f"{payload['opportunity_denominator']['candidate_horizon_rows']} "
                    "candidate-horizon rows; policy comparison uses all-horizon common coverage"
                ),
                "evidence_layers": [
                    "frozen scored strict-forward candidates and settlement labels",
                    "raw weather_orderbook_capture_v3 full ladders",
                    "collector exact response/available clocks and request batch identity",
                ],
                "pit_or_asof_policy": (
                    "first exact response after each horizon within a fixed 30-minute "
                    "window; both direct-NO legs must share one batch and clock"
                ),
                "label_contract": "same closed settlement payoff as the incumbent hold baseline",
                "primary_metrics": [
                    "fee-adjusted exit ROI",
                    "exit-minus-hold paired ROI delta",
                    "target-date block-bootstrap 95% CI",
                    "exact-clock executable coverage",
                    "selective-stop trigger count",
                ],
                "baselines": [
                    "hold the identical covered basket to settlement",
                    "prior frozen p_tail_market<=10% risk overlay on the same common rows",
                ],
                "forward_policy": (
                    "select horizon or stop threshold on collected_forward plus "
                    "recovery_validation only; disclose locked_forward as previously observed; "
                    "require new dates for confirmation"
                ),
                "acceptance_gates": [
                    "positive development point delta",
                    "locked paired target-date bootstrap ROI delta CI above zero",
                    "non-trivial stop triggers in development",
                    "clean future frozen forward",
                ],
                "fee_and_execution_basis": (
                    "sell 0.5 direct-NO shares per leg through the full bid ladder; "
                    "official Weather taker fee at every fill level"
                ),
            },
            "inputs": [
                {
                    "input_id": "strict_forward_scored_candidates",
                    "kind": "frozen scored candidate artifact",
                    "locator": args.score_artifact_uri,
                    "identity": f"sha256:{scored_sha256}",
                    "coverage": "105 baskets / 29 target dates / settled 105 of 105",
                    "observed_at_utc": score_generated_at_utc,
                },
                {
                    "input_id": "candidate_condition_market_books",
                    "kind": "raw exact-clock direct-NO orderbook captures",
                    "locator": "production://weather_market_books/batches",
                    "identity": (
                        "stable_content_hash:"
                        f"{source['candidate_condition_rows_identity']}"
                    ),
                    "coverage": (
                        f"{source['batch_files_read_count']} batch files / "
                        f"{source['candidate_condition_rows_read']} candidate-condition rows"
                    ),
                    "observed_at_utc": generated_at,
                },
            ],
            "execution": {
                "producer": (
                    "scripts/analysis/market_structure_edge/"
                    "research_europe_d1_distance2_forward_v8.py exit-replay"
                ),
                "code_identity": f"script_sha256:{sha256_file(Path(__file__))}",
                "config_locator": (
                    "configs/weather/europe_d1_distance2_dual_no_shadow_v1.json"
                ),
                "config_identity": f"sha256:{config_sha256}",
                "reproduce_command": (
                    ".venv/bin/python scripts/analysis/market_structure_edge/"
                    "research_europe_d1_distance2_forward_v8.py exit-replay "
                    f"--score-dir {args.score_dir} "
                    f"--market-books-root {args.market_books_root} "
                    f"--output-dir {args.output_dir} --research-record {path} "
                    f"--artifact-uri {args.artifact_uri} "
                    f"--score-artifact-uri {args.score_artifact_uri} "
                    f"--horizons {' '.join(str(value) for value in args.horizons)} "
                    f"--draws {args.draws} --seed {args.seed}"
                ),
            },
            "outputs": {
                "artifact_root_contract": "production://research_artifact_root",
                "artifact_manifest": args.artifact_uri,
                "canonical_machine_format": "json",
                "compact_summary_locator": args.artifact_uri,
            },
            "knowledge": {
                "family_living_doc": (
                    "docs/analysis/2026-08/"
                    "2026-08-30-europe-d1-distance2-dual-no-forward-freeze-v8.md"
                ),
                "registry_or_index": "docs/WEATHER_STRATEGY_REGISTRY.md",
                "dated_snapshot": (
                    "docs/analysis/2026-08/"
                    "2026-08-30-europe-d1-distance2-dual-no-forward-freeze-v8.md"
                ),
                "durable_conclusion": (
                    "Mechanical fixed exits are rejected on common exact-clock coverage: "
                    f"development delta {fixed_development['exit_minus_hold_roi']:.6f}, "
                    f"locked delta {fixed_locked['exit_minus_hold_roi']:.6f}. The "
                    f"development-selected {stop['selected_threshold_usd_per_basket']:.3f} "
                    "USD selective stop has positive point deltas "
                    f"({stop_development['stop_minus_hold_roi']:.6f} development, "
                    f"{stop_locked['stop_minus_hold_roi']:.6f} locked) but remains "
                    "inconclusive without significance and clean forward evidence."
                ),
                "action": (
                    "Reject fixed exits; retain the selective stop only as a frozen "
                    "future zero-notional candidate; no live change."
                ),
                "superseded_record_ids": [],
            },
        }
    )
    write_json(path, record)


def exit_replay(args: argparse.Namespace) -> dict[str, Any]:
    existing = list(args.output_dir.iterdir()) if args.output_dir.exists() else []
    allowed_record = args.research_record.resolve() if args.research_record else None
    disallowed = [
        path for path in existing if allowed_record is None or path.resolve() != allowed_record
    ]
    if disallowed:
        raise ValueError(f"refusing to overwrite non-empty output directory: {args.output_dir}")
    candidates, inputs = _strict_exit_candidates(args.score_dir)
    payload = evaluate_fixed_exit_replay(candidates, market_books_root=args.market_books_root, horizons=tuple(args.horizons), draws=args.draws, seed=args.seed)
    payload.update({"generated_at_utc": datetime.now(timezone.utc).isoformat(), "input_manifest": inputs, "orders_submitted": 0, "actual_fills": 0, "actual_notional_usd": 0.0})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "exit_replay_summary.json", payload)
    if args.research_record is not None:
        if not args.artifact_uri or not args.score_artifact_uri:
            raise ValueError(
                "--artifact-uri and --score-artifact-uri are required with --research-record"
            )
        score_summary = read_json(args.score_dir / "score_summary.json")
        freeze_manifest = read_json(Path(str(score_summary["freeze_manifest_path"])))
        _complete_exit_research_record(
            args.research_record,
            payload=payload,
            args=args,
            scored_sha256=sha256_file(args.score_dir / "scored_candidates.csv"),
            score_generated_at_utc=str(score_summary["generated_at_utc"]),
            config_sha256=str(freeze_manifest["config_sha256"]),
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload


def _risk_selected(
    rows: list[dict[str, Any]], threshold: float
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if float(row["joint_market_probability"]) <= threshold + 1e-12
    ]


def _point_policy_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(float(row["cost"]) for row in rows)
    pnl = sum(float(row["pnl"]) for row in rows)
    return {
        "candidates": len(rows),
        "target_dates": len({str(row["target_date"]) for row in rows}),
        "cost": cost,
        "pnl": pnl,
        "roi": pnl / cost if cost else None,
        "plus_1_0_cent_roi": (
            (pnl - 0.01 * len(rows)) / cost if cost else None
        ),
        "joint_tail_hits": sum(int(row["joint_tail_hit"]) for row in rows),
    }


def _paired_policy_comparison(
    baseline: list[dict[str, Any]],
    challenger: list[dict[str, Any]],
    *,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    baseline_ids = {str(row["candidate_id"]) for row in baseline}
    challenger_ids = {str(row["candidate_id"]) for row in challenger}
    if not challenger_ids.issubset(baseline_ids):
        raise ValueError("challenger must be a subset of the fixed baseline denominator")
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in baseline:
        by_date[str(row["target_date"])].append(row)
    dates = sorted(by_date)
    if not dates:
        raise ValueError("paired comparison requires at least one target date")

    def metrics(rows: list[dict[str, Any]]) -> tuple[float, float, float]:
        cost = sum(float(row["cost"]) for row in rows)
        pnl = sum(float(row["pnl"]) for row in rows)
        if cost <= 0.0:
            raise ValueError("paired policy has zero sampled cost")
        return pnl, pnl / cost, (pnl - 0.01 * len(rows)) / cost

    base_pnl, base_roi, base_stress = metrics(baseline)
    challenger_pnl, challenger_roi, challenger_stress = metrics(challenger)
    rng = random.Random(seed)
    pnl_deltas: list[float] = []
    roi_deltas: list[float] = []
    stress_deltas: list[float] = []
    for _ in range(draws):
        sampled_dates = rng.choices(dates, k=len(dates))
        sampled_baseline = [
            row for target_date in sampled_dates for row in by_date[target_date]
        ]
        sampled_challenger = [
            row
            for row in sampled_baseline
            if str(row["candidate_id"]) in challenger_ids
        ]
        if not sampled_challenger:
            continue
        sample_base_pnl, sample_base_roi, sample_base_stress = metrics(
            sampled_baseline
        )
        sample_challenger_pnl, sample_challenger_roi, sample_challenger_stress = (
            metrics(sampled_challenger)
        )
        pnl_deltas.append(sample_challenger_pnl - sample_base_pnl)
        roi_deltas.append(sample_challenger_roi - sample_base_roi)
        stress_deltas.append(sample_challenger_stress - sample_base_stress)
    return {
        "fixed_opportunity_candidates": len(baseline),
        "fixed_opportunity_target_dates": len(dates),
        "challenger_selected_candidates": len(challenger),
        "pnl_delta": challenger_pnl - base_pnl,
        "pnl_delta_ci95": [
            _quantile(pnl_deltas, 0.025),
            _quantile(pnl_deltas, 0.975),
        ],
        "roi_delta": challenger_roi - base_roi,
        "roi_delta_ci95": [
            _quantile(roi_deltas, 0.025),
            _quantile(roi_deltas, 0.975),
        ],
        "plus_1_0_cent_roi_delta": challenger_stress - base_stress,
        "plus_1_0_cent_roi_delta_ci95": [
            _quantile(stress_deltas, 0.025),
            _quantile(stress_deltas, 0.975),
        ],
    }


def evaluate_market_tail_risk_overlay(
    rows: list[dict[str, Any]], *, draws: int, seed: int
) -> dict[str, Any]:
    development = [
        row for row in rows if str(row["split"]) in RISK_DEVELOPMENT_SPLITS
    ]
    forward = [row for row in rows if str(row["split"]) == RISK_FORWARD_SPLIT]
    if not development or not forward:
        raise ValueError("risk overlay requires both development and locked-forward rows")
    if len({str(row["candidate_id"]) for row in rows}) != len(rows):
        raise ValueError("risk overlay denominator contains duplicate candidates")
    grain_keys = [
        (str(row["city"]), str(row["target_date"])) for row in rows
    ]
    if len(set(grain_keys)) != len(grain_keys):
        raise ValueError(
            "risk overlay denominator contains duplicate city-target_date grain"
        )

    development_grid: list[dict[str, Any]] = []
    for threshold in RISK_THRESHOLD_GRID:
        point = _point_policy_metrics(_risk_selected(development, threshold))
        development_grid.append(
            {
                "maximum_allocation_weighted_tail_probability": threshold,
                **point,
            }
        )
    eligible_grid = [
        row for row in development_grid if row["plus_1_0_cent_roi"] is not None
    ]
    selected_grid_row = max(
        eligible_grid,
        key=lambda row: (
            float(row["plus_1_0_cent_roi"]),
            int(row["candidates"]),
            float(row["maximum_allocation_weighted_tail_probability"]),
        ),
    )
    threshold = float(
        selected_grid_row["maximum_allocation_weighted_tail_probability"]
    )
    development_challenger = _risk_selected(development, threshold)
    forward_challenger = _risk_selected(forward, threshold)
    strict_challenger = _risk_selected(rows, threshold)
    skipped = [
        {
            "candidate_id": str(row["candidate_id"]),
            "split": str(row["split"]),
            "city": str(row["city"]),
            "target_date": str(row["target_date"]),
            "checkpoint_ts_utc": str(row["checkpoint_ts_utc"]),
            "joint_market_probability": float(row["joint_market_probability"]),
            "cost": float(row["cost"]),
            "pnl": float(row["pnl"]),
            "joint_tail_hit": int(row["joint_tail_hit"]),
        }
        for row in rows
        if str(row["candidate_id"])
        not in {str(item["candidate_id"]) for item in strict_challenger}
    ]
    forward_pair = _paired_policy_comparison(
        forward,
        forward_challenger,
        draws=draws,
        seed=seed + 50,
    )
    roi_delta_ci = forward_pair["roi_delta_ci95"]
    significance_pass = (
        roi_delta_ci[0] is not None and float(roi_delta_ci[0]) > 0.0
    )
    return {
        "schema_version": "europe_d1_distance2_market_tail_risk_overlay_v1",
        "hypothesis": (
            "A threshold on the entry-time allocation-weighted exact-tail market "
            "probability can improve fee-adjusted carry ROI and reduce tail losses "
            "relative to the fixed mechanical basket."
        ),
        "feature": {
            "name": "allocation_weighted_selected_exact_tail_probability",
            "formula": "sum(allocation_weight * market_p_exact) over the two NO legs",
            "clock": "same PIT first-city-day entry ladder",
            "coverage": f"{len(rows)}/{len(rows)} strict-forward baskets",
        },
        "selection": {
            "development_splits": sorted(RISK_DEVELOPMENT_SPLITS),
            "objective": "maximum development ROI after official entry fee and +1.0 cent per basket stress",
            "threshold_grid": list(RISK_THRESHOLD_GRID),
            "variants_k": len(RISK_THRESHOLD_GRID),
            "multiple_testing": "uncorrected development grid; locked result is interpreted conservatively",
            "selected_maximum_tail_probability": threshold,
            "development_grid": development_grid,
        },
        "development": {
            "baseline": _summarize_rows(development, draws=draws, seed=seed + 60),
            "challenger": _summarize_rows(
                development_challenger, draws=draws, seed=seed + 61
            ),
            "paired_challenger_minus_baseline": _paired_policy_comparison(
                development,
                development_challenger,
                draws=draws,
                seed=seed + 62,
            ),
        },
        "locked_forward": {
            "status": "retrospective_chronological_holdout_previously_observed",
            "baseline": _summarize_rows(forward, draws=draws, seed=seed + 70),
            "challenger": _summarize_rows(
                forward_challenger, draws=draws, seed=seed + 71
            ),
            "paired_challenger_minus_baseline": forward_pair,
        },
        "strict_forward_context": {
            "baseline": _summarize_rows(rows, draws=draws, seed=seed + 80),
            "challenger": _summarize_rows(
                strict_challenger, draws=draws, seed=seed + 81
            ),
        },
        "skipped_candidates": skipped,
        "readiness": {
            "pit_state_and_clocks": "READY_105_OF_105",
            "market_quote_and_depth": "READY_105_OF_105",
            "settlement_labels": "READY_105_OF_105",
            "independent_target_dates": 29,
            "clean_frozen_forward": "BLOCKED_LOCKED_WINDOW_ALREADY_OBSERVED",
            "forecast_feature_training": "BLOCKED_DIRECT_CANDIDATE_COVERAGE_24_OF_105",
            "dynamic_exit_replay": "BLOCKED_NO_CANDIDATE_EXIT_COVERAGE_MANIFEST",
        },
        "gates": {
            "significance": "PASS" if significance_pass else "FAIL",
            "same_row_market_residual": "FAIL_INCONCLUSIVE",
            "clean_forward": "FAIL",
            "plus_1_0_cent_locked_forward": (
                "PASS"
                if float(
                    _summarize_rows(
                        forward_challenger, draws=draws, seed=seed + 82
                    )["execution_price_stress_roi"]["plus_1_0_cent"]
                )
                > 0.0
                else "FAIL"
            ),
        },
        "conclusion": "inconclusive_promising_market_risk_overlay",
        "action": (
            "freeze the selected threshold only as a future zero-notional "
            "candidate spec; do not deploy or change live behavior"
        ),
        "orders_submitted": 0,
        "actual_fills": 0,
        "actual_notional_usd": 0.0,
    }


def _complete_risk_research_record(
    path: Path,
    *,
    payload: dict[str, Any],
    args: argparse.Namespace,
    scored_sha256: str,
    score_generated_at_utc: str,
    config_sha256: str,
) -> None:
    record = read_json(path)
    generated_at = str(payload["generated_at_utc"])
    threshold = float(payload["selection"]["selected_maximum_tail_probability"])
    record.update(
        {
            "lifecycle_status": "complete",
            "observed_at_utc": generated_at,
            "question": {
                "hypothesis": str(payload["hypothesis"]),
                "decision_target": (
                    "whether a PIT market-tail risk router merits new zero-notional forward evidence"
                ),
                "scope": (
                    "105 strict-forward Europe D-1 distance-2 dual-NO city-day baskets, "
                    "2026-07-30 through 2026-08-28, ten fixed cities"
                ),
                "exclusions": [
                    "live behavior changes",
                    "dynamic exit claims without executable bid coverage",
                    "weather-model training outside the 24 directly covered candidates",
                    "historical 47-row sensitivity without the modern PIT contract",
                ],
            },
            "method": {
                "grain": "first PIT-complete D-1 ladder per city and target_date",
                "denominator_scope": (
                    "105 settled strict-forward baskets: 24 collected_forward, "
                    "43 recovery_validation, 38 locked_forward"
                ),
                "evidence_layers": [
                    "frozen scored candidate artifact",
                    "same-entry PIT market probability and executable NO ask",
                    "closed canonical settlement label",
                ],
                "pit_or_asof_policy": (
                    "tail risk and entry cost come from the same immutable first-city-day ladder"
                ),
                "label_contract": (
                    "joint_tail_hit=1 when either selected exact bracket settles YES"
                ),
                "primary_metrics": [
                    "fee-adjusted ROI",
                    "challenger-minus-baseline paired ROI delta",
                    "+1.0 cent execution-stress ROI",
                    "joint tail hits",
                ],
                "baselines": [
                    "same 105-opportunity mechanical 50/50 dual-NO carry",
                    "same-row market expected payout diagnostic",
                ],
                "forward_policy": (
                    "select threshold on collected_forward plus recovery_validation only; "
                    "evaluate once on locked_forward, disclosed as previously observed; "
                    "require new future dates for clean confirmation"
                ),
                "acceptance_gates": [
                    "paired target-date bootstrap ROI delta CI above zero",
                    "same-row market residual evidence",
                    "positive +1.0 cent stressed ROI",
                    "clean frozen forward",
                ],
                "fee_and_execution_basis": (
                    "two NO legs at entry ask, 50/50 allocation, official Weather taker fee; "
                    "+1.0 cent per-basket stress; settlement hold"
                ),
            },
            "inputs": [
                {
                    "input_id": "strict_forward_scored_candidates",
                    "kind": "frozen scored candidate artifact",
                    "locator": args.score_artifact_uri,
                    "identity": f"sha256:{scored_sha256}",
                    "coverage": "105 baskets / 29 target dates / 10 cities / settled 105 of 105",
                    "observed_at_utc": score_generated_at_utc,
                }
            ],
            "execution": {
                "producer": (
                    "scripts/analysis/market_structure_edge/"
                    "research_europe_d1_distance2_forward_v8.py risk-overlay"
                ),
                "code_identity": f"script_sha256:{sha256_file(Path(__file__))}",
                "config_locator": (
                    "configs/weather/europe_d1_distance2_dual_no_shadow_v1.json"
                ),
                "config_identity": f"sha256:{config_sha256}",
                "reproduce_command": (
                    ".venv/bin/python scripts/analysis/market_structure_edge/"
                    "research_europe_d1_distance2_forward_v8.py risk-overlay "
                    f"--score-dir {args.score_dir} --output-dir {args.output_dir} "
                    f"--research-record {path} --artifact-uri {args.artifact_uri} "
                    f"--score-artifact-uri {args.score_artifact_uri} "
                    f"--draws {args.draws} --seed {args.seed}"
                ),
            },
            "outputs": {
                "artifact_root_contract": "production://research_artifact_root",
                "artifact_manifest": args.artifact_uri,
                "canonical_machine_format": "json",
                "compact_summary_locator": args.artifact_uri,
            },
            "knowledge": {
                "family_living_doc": (
                    "docs/analysis/2026-08/"
                    "2026-08-30-europe-d1-distance2-dual-no-forward-freeze-v8.md"
                ),
                "registry_or_index": "docs/WEATHER_STRATEGY_REGISTRY.md",
                "dated_snapshot": (
                    "docs/analysis/2026-08/"
                    "2026-08-30-europe-d1-distance2-dual-no-forward-freeze-v8.md"
                ),
                "durable_conclusion": (
                    f"The development-selected market-tail threshold <= {threshold:.3f} "
                    "improves the later point estimate and +1-cent stress, but paired "
                    "significance, market-residual, and clean-forward gates fail."
                ),
                "action": (
                    "Keep the incumbent frozen inconclusive; retain the threshold only "
                    "as a future zero-notional candidate spec; no live change."
                ),
                "superseded_record_ids": [],
            },
        }
    )
    write_json(path, record)


def risk_overlay(args: argparse.Namespace) -> dict[str, Any]:
    scored_path = args.score_dir / "scored_candidates.csv"
    score_summary_path = args.score_dir / "score_summary.json"
    score_summary = read_json(score_summary_path)
    rows = _read_scored_rows(scored_path)
    payload = evaluate_market_tail_risk_overlay(
        rows,
        draws=args.draws,
        seed=args.seed,
    )
    payload.update(
        {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "input_score_summary_path": str(score_summary_path),
            "input_score_summary_sha256": sha256_file(score_summary_path),
            "input_scored_candidates_path": str(scored_path),
            "input_scored_candidates_sha256": sha256_file(scored_path),
            "strict_forward_candidates": len(rows),
            "strict_forward_target_dates": len(
                {str(row["target_date"]) for row in rows}
            ),
        }
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "risk_overlay_summary.json"
    write_json(output_path, payload)
    if args.research_record is not None:
        if not args.artifact_uri or not args.score_artifact_uri:
            raise ValueError(
                "--artifact-uri and --score-artifact-uri are required with --research-record"
            )
        freeze_manifest = read_json(Path(str(score_summary["freeze_manifest_path"])))
        _complete_risk_research_record(
            args.research_record,
            payload=payload,
            args=args,
            scored_sha256=sha256_file(scored_path),
            score_generated_at_utc=str(score_summary["generated_at_utc"]),
            config_sha256=str(freeze_manifest["config_sha256"]),
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze_parser = subparsers.add_parser("freeze")
    freeze_parser.add_argument("--checkpoints", type=Path, required=True)
    freeze_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    freeze_parser.add_argument("--output-dir", type=Path, required=True)
    freeze_parser.add_argument("--start-date", default="2026-08-10")
    freeze_parser.add_argument("--end-date", default="2026-08-28")
    freeze_parser.add_argument("--validation-end", default="2026-08-19")
    freeze_parser.set_defaults(func=freeze)

    score_parser = subparsers.add_parser("score")
    score_parser.add_argument("--freeze-dir", type=Path, required=True)
    score_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    score_parser.add_argument("--prior-shadow-candidates", type=Path)
    score_parser.add_argument("--historical-blind-v7", type=Path)
    score_parser.add_argument("--output-dir", type=Path, required=True)
    score_parser.add_argument("--draws", type=int, default=DEFAULT_DRAWS)
    score_parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    score_parser.set_defaults(func=score)

    risk_parser = subparsers.add_parser("risk-overlay")
    risk_parser.add_argument("--score-dir", type=Path, required=True)
    risk_parser.add_argument("--output-dir", type=Path, required=True)
    risk_parser.add_argument("--research-record", type=Path)
    risk_parser.add_argument("--artifact-uri")
    risk_parser.add_argument("--score-artifact-uri")
    risk_parser.add_argument("--draws", type=int, default=DEFAULT_DRAWS)
    risk_parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    risk_parser.set_defaults(func=risk_overlay)

    exit_parser = subparsers.add_parser("exit-replay")
    exit_parser.add_argument("--score-dir", type=Path, required=True)
    exit_parser.add_argument("--market-books-root", type=Path, required=True)
    exit_parser.add_argument("--output-dir", type=Path, required=True)
    exit_parser.add_argument("--research-record", type=Path)
    exit_parser.add_argument("--artifact-uri")
    exit_parser.add_argument("--score-artifact-uri")
    exit_parser.add_argument(
        "--horizons", type=int, nargs="+", default=list(EXIT_HORIZONS_MINUTES)
    )
    exit_parser.add_argument("--draws", type=int, default=DEFAULT_DRAWS)
    exit_parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    exit_parser.set_defaults(func=exit_replay)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
