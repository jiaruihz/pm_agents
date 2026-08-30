#!/usr/bin/env python3
"""Zero-notional Europe D-1 distance-2 dual-NO carry shadow."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_brackets import parse_market_bracket
from weather_data_feed.jsonl_partitions import dated_jsonl_paths, iter_jsonl_lines
from weather_data_feed_service.io_utils import append_jsonl, read_json, write_json
from src.strategies.runtime.production import load_production_spec


RUNTIME_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_BOOK_ROOT = load_production_spec().resolved_market_books_root() / "batches"
DEFAULT_VERSIONS = (
    RUNTIME_ROOT / "output/forecast_enrichment"
)
DEFAULT_CONFIG = (
    ROOT / "configs/weather/europe_d1_distance2_dual_no_shadow_v1.json"
)
DEFAULT_FEATURE_POLICY = (
    ROOT / "configs/weather/d1_multisource_consensus_shadow_v1.json"
)
DEFAULT_OUTPUT = (
    RUNTIME_ROOT / "output/europe_d1_distance2_dual_no_shadow_v1"
)
STRATEGY_INSTANCE = "europe_d1_distance2_dual_no_shadow_v1"


def utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def load_market_tail_threshold(config: dict[str, Any]) -> float:
    """Return the frozen market-implied tail-risk ceiling.

    This is intentionally a config-level policy rather than a weather feature:
    the weighted probability is calculated from the paired market mids.
    """
    frozen_forward = config.get("frozen_forward")
    if not isinstance(frozen_forward, dict):
        raise ValueError("frozen_forward configuration is required")
    threshold = finite(frozen_forward.get("joint_market_probability_max"))
    if threshold is None or not 0 <= threshold <= 1:
        raise ValueError(
            "frozen_forward.joint_market_probability_max must be finite and within [0, 1]"
        )
    return threshold


def latest_file(root: Path) -> Path | None:
    paths = list(root.glob("**/*.jsonl.gz"))
    return max(paths, key=lambda path: path.stat().st_mtime) if paths else None


def load_no_books(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("outcome") or "").lower() == "no":
                rows.append(row)
    return rows


def bracket_sort_key(row: dict[str, Any]) -> tuple[float, str]:
    label = str(row.get("bracket") or "")
    parsed = parse_market_bracket(label, str(row.get("question") or ""))
    if parsed is None:
        return math.inf, label
    if parsed.bottom:
        value = float(parsed.high)
    elif parsed.top:
        value = float(parsed.low)
    else:
        value = (float(parsed.low) + float(parsed.high)) / 2
    return value, label


def percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def normal_cdf(value: float, mean: float, std: float) -> float:
    if std <= 0:
        return float(value >= mean)
    return 0.5 * (1 + math.erf((value - mean) / (std * math.sqrt(2))))


def native_to_f(value: float, unit: str) -> float:
    return value * 9 / 5 + 32 if unit.upper() == "C" else value


def exact_probability(
    *,
    bracket_label: str,
    question: str,
    market_unit: str,
    mean_f: float,
    std_f: float,
) -> float | None:
    bracket = parse_market_bracket(bracket_label, question)
    if bracket is None:
        return None
    if bracket.bottom:
        upper = native_to_f(float(bracket.high) + 0.5, market_unit)
        return normal_cdf(upper, mean_f, std_f)
    if bracket.top:
        lower = native_to_f(float(bracket.low) - 0.5, market_unit)
        return 1 - normal_cdf(lower, mean_f, std_f)
    lower = native_to_f(float(bracket.low) - 0.5, market_unit)
    upper = native_to_f(float(bracket.high) + 0.5, market_unit)
    return max(
        0.0,
        min(
            1.0,
            normal_cdf(upper, mean_f, std_f)
            - normal_cdf(lower, mean_f, std_f),
        ),
    )


def load_versions(
    path: Path, asof: datetime
) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    history: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    paths = dated_jsonl_paths(
        path,
        filename="forecast_versions.jsonl",
        allow_missing=True,
    )
    for line in iter_jsonl_lines(paths):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        available = utc(row.get("available_at_utc"))
        if available is None or available > asof:
            continue
        key = (
            str(row.get("city") or ""),
            str(row.get("forecast_target_date") or ""),
            str(row.get("model_label") or ""),
        )
        history[key].append(row)
    for rows in history.values():
        rows.sort(key=lambda row: str(row.get("available_at_utc") or ""))
    return history


def load_calibration(
    path: Path,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    payload = read_json(path, {})
    index = {
        (str(row["city"]), str(row["model_label"])): row
        for row in payload.get("records") or []
        if row.get("city") and row.get("model_label")
    }
    return index, payload


def book_fields(row: dict[str, Any]) -> dict[str, Any]:
    summary = row.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    ask = finite(summary.get("best_ask"))
    bid = finite(summary.get("best_bid"))
    ask_size = finite(summary.get("ask_size"))
    executable = (
        ask is not None
        and 0.001 <= ask <= 0.999
        and ask_size is not None
        and ask_size >= 1
        and (bid is None or bid <= ask)
    )
    no_mid = (
        (bid + ask) / 2
        if bid is not None and ask is not None and bid <= ask
        else None
    )
    fee = 0.05 * ask * (1 - ask) if ask is not None else None
    return {
        "no_best_bid": bid,
        "no_best_ask": ask,
        "no_ask_size": ask_size,
        "no_mid": no_mid,
        "market_p_exact": 1 - no_mid if no_mid is not None else None,
        "fee_per_share": fee,
        "cost_per_share": ask + fee if ask is not None else None,
        "book_executable": executable,
    }


def forecast_features(
    *,
    city: str,
    target_date: str,
    decision_asof: datetime,
    legs: list[dict[str, Any]],
    market_unit: str,
    versions: dict[tuple[str, str, str], list[dict[str, Any]]],
    calibration: dict[tuple[str, str], dict[str, Any]],
    model_labels: list[str],
    training_cutoff: Any,
) -> dict[str, Any]:
    model_rows: list[dict[str, Any]] = []
    for model_label in model_labels:
        history = [
            row
            for row in versions.get((city, target_date, model_label), [])
            if (utc(row.get("available_at_utc")) or datetime.max.replace(
                tzinfo=timezone.utc
            ))
            <= decision_asof
        ]
        forecast = history[-1] if history else None
        calibration_row = calibration.get((city, model_label))
        if forecast is None or calibration_row is None:
            continue
        forecast_max_f = finite(forecast.get("forecast_max_f"))
        bias_f = finite(calibration_row.get("bias_correction_f"))
        residual_std_f = finite(calibration_row.get("residual_std_f"))
        if (
            forecast_max_f is None
            or bias_f is None
            or residual_std_f is None
            or residual_std_f <= 0
        ):
            continue
        corrected_f = forecast_max_f + bias_f
        probabilities = [
            exact_probability(
                bracket_label=str(leg["bracket"]),
                question=str(leg.get("question") or ""),
                market_unit=market_unit,
                mean_f=corrected_f,
                std_f=residual_std_f,
            )
            for leg in legs
        ]
        model_rows.append(
            {
                "model_label": model_label,
                "available_at_utc": forecast.get("available_at_utc"),
                "forecast_max_f": forecast_max_f,
                "bias_correction_f": bias_f,
                "corrected_forecast_f": corrected_f,
                "residual_std_f": residual_std_f,
                "distance2_exact_probabilities": probabilities,
                "combined_distance2_exact_mass": (
                    sum(float(value) for value in probabilities)
                    if all(value is not None for value in probabilities)
                    else None
                ),
            }
        )
    corrected = [float(row["corrected_forecast_f"]) for row in model_rows]
    combined = [
        float(row["combined_distance2_exact_mass"])
        for row in model_rows
        if row["combined_distance2_exact_mass"] is not None
    ]
    return {
        "feature_status": (
            "available" if len(model_rows) >= 3 else "insufficient_models"
        ),
        "feature_is_eligibility_gate": False,
        "policy_training_cutoff": training_cutoff,
        "eligible_model_count": len(model_rows),
        "model_rows": model_rows,
        "ensemble_corrected_median_f": (
            median(corrected) if corrected else None
        ),
        "ensemble_spread_iqr_f": (
            percentile(corrected, 0.75) - percentile(corrected, 0.25)
            if corrected
            else None
        ),
        "forecast_combined_distance2_exact_mass": (
            sum(combined) / len(combined) if combined else None
        ),
        "forecast_available_max_utc": (
            max(str(row["available_at_utc"]) for row in model_rows)
            if model_rows
            else None
        ),
        "decision_asof_utc": decision_asof.isoformat(),
    }


def build_cycle(
    *,
    book_path: Path,
    versions_path: Path,
    config_path: Path,
    feature_policy_path: Path,
    repo_sha: str = "",
) -> dict[str, Any]:
    config = read_json(config_path, {})
    joint_market_probability_max = load_market_tail_threshold(config)
    city_configs = config.get("cities") or {}
    books = load_no_books(book_path)
    book_asof = max(
        (utc(row.get("fetched_at_utc")) for row in books),
        default=None,
    )
    if book_asof is None:
        return {"status": "missing_book_timestamp", "records": []}
    versions = load_versions(versions_path, book_asof)
    calibration, calibration_payload = load_calibration(feature_policy_path)
    model_labels = [
        str(value) for value in config.get("feature_model_labels") or []
    ]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in books:
        groups[(str(row.get("city")), str(row.get("event_date")))].append(row)

    records: list[dict[str, Any]] = []
    europe_groups = 0
    d1_groups = 0
    full_ladder_groups = 0
    for (city, target_date), group in sorted(groups.items()):
        city_config = city_configs.get(city)
        if not isinstance(city_config, dict):
            continue
        europe_groups += 1
        group_asof = max(
            (utc(row.get("fetched_at_utc")) for row in group),
            default=None,
        )
        if group_asof is None:
            continue
        timezone_name = str(city_config["timezone"])
        local = group_asof.astimezone(ZoneInfo(timezone_name))
        try:
            target = datetime.fromisoformat(target_date).date()
        except ValueError:
            continue
        lead_days = (target - local.date()).days
        local_hour = local.hour + local.minute / 60 + local.second / 3600
        if lead_days != int(config.get("target_lead_days", 1)):
            continue
        if not (
            float(config.get("local_entry_hour_start", 12))
            <= local_hour
            < float(config.get("local_entry_hour_end", 24))
        ):
            continue
        d1_groups += 1
        ordered = sorted(group, key=bracket_sort_key)
        distance = int(config.get("distance_from_nearest_endpoint", 2))
        if len(ordered) < 2 * distance + 1:
            continue
        low_row = ordered[distance]
        high_row = ordered[-1 - distance]
        if low_row is high_row:
            continue
        full_ladder_groups += 1
        selected_rows = [low_row, high_row]
        decision_times = [
            utc(row.get("fetched_at_utc")) for row in selected_rows
        ]
        if any(value is None for value in decision_times):
            continue
        decision_asof = min(
            value for value in decision_times if value is not None
        )
        allocation = config.get("allocation") or {}
        leg_names = ("low_distance2_no", "high_distance2_no")
        legs: list[dict[str, Any]] = []
        for name, row in zip(leg_names, selected_rows, strict=True):
            quote = book_fields(row)
            legs.append(
                {
                    "leg": name,
                    "allocation_weight": float(allocation.get(name, 0.5)),
                    "distance_from_nearest_endpoint": distance,
                    "rung_index": ordered.index(row),
                    "rung_count": len(ordered),
                    "bracket": row.get("bracket"),
                    "question": row.get("question"),
                    "condition_id": row.get("condition_id"),
                    "token_id": row.get("token_id"),
                    "book_status": row.get("status"),
                    "book_fetched_at_utc": row.get("fetched_at_utc"),
                    **quote,
                }
            )
        paired_executable = all(
            bool(leg["book_executable"]) for leg in legs
        )
        market_exact = [leg["market_p_exact"] for leg in legs]
        normalized_cost = (
            sum(
                float(leg["allocation_weight"])
                * float(leg["cost_per_share"])
                for leg in legs
            )
            if paired_executable
            else None
        )
        market_combined_mass = (
            sum(float(value) for value in market_exact)
            if all(value is not None for value in market_exact)
            else None
        )
        joint_market_probability = (
            sum(
                float(leg["allocation_weight"])
                * float(leg["market_p_exact"])
                for leg in legs
            )
            if all(value is not None for value in market_exact)
            else None
        )
        normalized_expected_payout = (
            1
            - sum(
                float(leg["allocation_weight"])
                * float(leg["market_p_exact"])
                for leg in legs
            )
            if all(value is not None for value in market_exact)
            else None
        )
        features = forecast_features(
            city=city,
            target_date=target_date,
            decision_asof=decision_asof,
            legs=legs,
            market_unit=str(city_config.get("market_unit", "C")),
            versions=versions,
            calibration=calibration,
            model_labels=model_labels,
            training_cutoff=calibration_payload.get("training_cutoff"),
        )
        market_tail_eligible = (
            paired_executable
            and joint_market_probability is not None
            and joint_market_probability
            <= joint_market_probability_max + 1e-12
        )
        if not paired_executable:
            decision_status = "paired_book_unexecutable"
        elif joint_market_probability is None:
            decision_status = "market_joint_probability_unavailable"
        elif not market_tail_eligible:
            decision_status = "market_joint_probability_above_threshold"
        else:
            decision_status = "would_shadow_entry"
        records.append(
            {
                "schema_version": (
                    "europe_d1_distance2_dual_no_shadow_v1"
                ),
                "strategy_instance": STRATEGY_INSTANCE,
                "execution_mode": "zero_notional_shadow",
                "orders_submitted": 0,
                "actual_notional_usd": 0.0,
                "repo_sha": repo_sha,
                "city": city,
                "target_date": target_date,
                "city_date_key": f"{city}|{target_date}",
                "market_unit": city_config.get("market_unit", "C"),
                "timezone": timezone_name,
                "local_decision_hour": local_hour,
                "book_snapshot_path": str(book_path),
                "book_asof_utc": book_asof.isoformat(),
                "decision_asof_utc": decision_asof.isoformat(),
                "decision_status": decision_status,
                "paired_book_executable": paired_executable,
                "joint_market_probability_max": joint_market_probability_max,
                "joint_market_probability": joint_market_probability,
                "market_tail_eligible": market_tail_eligible,
                "normalized_reference_cost": normalized_cost,
                "market_combined_distance2_exact_mass": (
                    market_combined_mass
                ),
                "normalized_market_expected_payout": (
                    normalized_expected_payout
                ),
                "normalized_market_edge_after_fee": (
                    normalized_expected_payout - normalized_cost
                    if normalized_expected_payout is not None
                    and normalized_cost is not None
                    else None
                ),
                "weather_features": features,
                "weather_features_used_for_eligibility": False,
                "legs": legs,
            }
        )
    return {
        "status": "ok",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_instance": STRATEGY_INSTANCE,
        "execution_mode": "zero_notional_shadow",
        "orders_submitted": 0,
        "actual_notional_usd": 0.0,
        "joint_market_probability_max": joint_market_probability_max,
        "book_path": str(book_path),
        "book_asof_utc": book_asof.isoformat(),
        "signal_funnel": {
            "book_city_dates": len(groups),
            "fixed_europe_city_dates": europe_groups,
            "d1_local_window_city_dates": d1_groups,
            "full_ladder_distance2_city_dates": full_ladder_groups,
            "candidate_city_dates": len(records),
        },
        "evidence_funnel": {
            "paired_executable_city_dates": sum(
                row["paired_book_executable"] for row in records
            ),
            "market_tail_eligible_city_dates": sum(
                row["market_tail_eligible"] for row in records
            ),
            "forecast_feature_city_dates": sum(
                row["weather_features"]["feature_status"] == "available"
                for row in records
            ),
            "settled_city_dates": 0,
            "actual_orders": 0,
            "actual_fills": 0,
        },
        "records": records,
    }


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    book_path = latest_file(args.book_root)
    if book_path is None:
        return {"status": "missing_orderbook", "records": []}
    state_path = args.output_dir / "state.json"
    state = read_json(state_path, {})
    if args.dedup and state.get("last_book_path") == str(book_path):
        return {"status": "unchanged", "records": []}
    collector_bootstrap = not bool(state.get("collector_started_at_utc"))
    payload = build_cycle(
        book_path=book_path,
        versions_path=args.versions,
        config_path=args.config,
        feature_policy_path=args.feature_policy,
        repo_sha=args.repo_sha,
    )
    if payload.get("status") != "ok":
        return payload

    locked = set(str(value) for value in state.get("locked_city_dates") or [])
    new_records = [
        row
        for row in payload.get("records") or []
        if row["city_date_key"] not in locked
    ]
    for row in new_records:
        row["collector_bootstrap_partial_window"] = collector_bootstrap
    locked.update(row["city_date_key"] for row in new_records)
    payload["records"] = new_records
    payload["new_locked_city_dates"] = len(new_records)
    payload["collector_bootstrap_partial_window"] = collector_bootstrap
    payload["signal_funnel"]["new_first_lock_city_dates"] = len(new_records)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "latest.json", payload)
    if new_records:
        append_jsonl(args.output_dir / "baskets.jsonl", new_records)
        leg_rows: list[dict[str, Any]] = []
        for basket in new_records:
            for leg in basket["legs"]:
                leg_rows.append(
                    {
                        key: value
                        for key, value in basket.items()
                        if key not in {"legs", "weather_features"}
                    }
                    | {
                        "weather_features": basket["weather_features"],
                        **leg,
                    }
                )
        append_jsonl(args.output_dir / "legs.jsonl", leg_rows)
    write_json(
        state_path,
        {
            "schema_version": (
                "europe_d1_distance2_dual_no_shadow_state_v1"
            ),
            "strategy_instance": STRATEGY_INSTANCE,
            "execution_mode": "zero_notional_shadow",
            "orders_submitted": 0,
            "collector_started_at_utc": state.get(
                "collector_started_at_utc"
            )
            or payload["generated_at_utc"],
            "last_generated_at_utc": payload["generated_at_utc"],
            "last_book_path": str(book_path),
            "locked_city_dates": sorted(locked),
        },
    )
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-root", type=Path, default=DEFAULT_BOOK_ROOT)
    parser.add_argument("--versions", type=Path, default=DEFAULT_VERSIONS)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--feature-policy", type=Path, default=DEFAULT_FEATURE_POLICY
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo-sha", default="")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--dedup", action="store_true")
    parser.add_argument("--interval-sec", type=float, default=60)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    while True:
        payload = run_once(args)
        print(
            json.dumps(
                {
                    key: value
                    for key, value in payload.items()
                    if key != "records"
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        if not args.loop:
            return 0
        time.sleep(max(1, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
