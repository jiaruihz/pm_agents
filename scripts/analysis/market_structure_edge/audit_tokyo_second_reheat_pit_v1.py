#!/usr/bin/env python3
"""Audit whether Tokyo probability shadows recognized a second JMA warm-up PIT.

The audit is read-only.  It uses collector-exact JMA first-seen clocks, the
already-written WCIR decision journal, and raw current-bracket books.  It does
not rebuild first-seen from issue times and does not emit intents or orders.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Iterable


UTC = timezone.utc
DEFAULT_RUNTIME = Path("/Volumes/jrs/weather_data_feed_service_runtime/output")
MODEL_IDS = (
    "tokyo_state_entry_routed_market_residual_v7",
    "tokyo_overshoot_market_residual_v2",
)


def parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def load_jma(path: Path, target_date: str) -> list[dict[str, Any]]:
    earliest: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(path):
        if (
            row.get("city") != "Tokyo"
            or row.get("source") != "jma_amedas"
            or row.get("source_status") != "ok"
            or row.get("target_date") != target_date
            or row.get("original_first_seen_unknown") is True
        ):
            continue
        observed = str(row.get("observation_time_utc") or "")
        first_seen = str(row.get("source_first_seen_at_utc") or "")
        if not observed or not first_seen:
            continue
        old = earliest.get(observed)
        if old is None or parse_ts(first_seen) < parse_ts(str(old["source_first_seen_at_utc"])):
            earliest[observed] = row
    return sorted(earliest.values(), key=lambda row: parse_ts(str(row["observation_time_utc"])))


def find_second_cross_episode(
    rows: list[dict[str, Any]], *, bracket: int, cross_margin_c: float
) -> dict[str, Any]:
    threshold = bracket + cross_margin_c
    temperatures = [float(row["temp_c"]) for row in rows]
    first = next((index for index, value in enumerate(temperatures) if value >= threshold), None)
    if first is None:
        raise ValueError("no first cross in JMA rows")
    below = next(
        (index for index in range(first + 1, len(rows)) if temperatures[index] < threshold),
        None,
    )
    if below is None:
        raise ValueError("first cross never pulled back below threshold")
    second = next(
        (index for index in range(below + 1, len(rows)) if temperatures[index] >= threshold),
        None,
    )
    if second is None:
        raise ValueError("no second cross after pullback")
    trough = min(range(below, second), key=lambda index: temperatures[index])
    return {
        "threshold_c": threshold,
        "first_index": first,
        "below_index": below,
        "trough_index": trough,
        "second_index": second,
    }


def lag_temperature(
    rows: list[dict[str, Any]], index: int, minutes: int
) -> float | None:
    current = parse_ts(str(rows[index]["observation_time_utc"]))
    cutoff = current.timestamp() - minutes * 60
    eligible = [
        float(row["temp_c"])
        for row in rows[:index]
        if parse_ts(str(row["observation_time_utc"])).timestamp() >= cutoff
    ]
    return eligible[0] if eligible else None


def path_features(
    rows: list[dict[str, Any]], index: int, *, trough_index: int, prior_peak_c: float
) -> dict[str, float | None]:
    temps = [float(row["temp_c"]) for row in rows[: index + 1]]
    current = temps[-1]
    observed = parse_ts(str(rows[index]["observation_time_utc"]))
    running_max = max(temps)
    strict_high_index = temps.index(running_max)
    strict_high = parse_ts(str(rows[strict_high_index]["observation_time_utc"]))
    warming_run = 0
    for cursor in range(index, 0, -1):
        if float(rows[cursor]["temp_c"]) > float(rows[cursor - 1]["temp_c"]):
            warming_run += 1
        else:
            break
    lag30 = lag_temperature(rows, index, 30)
    lag60 = lag_temperature(rows, index, 60)
    trough_c = float(rows[trough_index]["temp_c"])
    recovery_denominator = prior_peak_c - trough_c
    return {
        "jma_temp_c": current,
        "jma_temp_delta_10m": None if index == 0 else current - float(rows[index - 1]["temp_c"]),
        "jma_temp_slope_30m_cph": None if lag30 is None else (current - lag30) * 2.0,
        "jma_temp_slope_60m_cph": None if lag60 is None else current - lag60,
        "jma_running_max_c": running_max,
        "minutes_since_jma_strict_high": (observed - strict_high).total_seconds() / 60.0,
        "jma_warming_run_count": float(warming_run),
        "jma_pullback_from_running_max_c": current - running_max,
        "rise_from_episode_trough_c": current - trough_c,
        "prior_peak_recovery_fraction": (
            None if recovery_denominator <= 0 else (current - trough_c) / recovery_denominator
        ),
    }


def load_model_rows(
    path: Path, target_date: str, bracket: int
) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}
    for row in iter_jsonl(path):
        model = row.get("model_output") or {}
        candidate = row.get("signal_candidate") or {}
        event = row.get("information_event") or {}
        if (
            model.get("city") != "Tokyo"
            or model.get("target_date") != target_date
            or model.get("model_id") not in MODEL_IDS
            or candidate.get("side") != "NO"
            or int(candidate.get("bracket") or -999) != bracket
        ):
            continue
        observed = str(event.get("source_event_ts_utc") or model.get("metadata", {}).get("source_obs_ts_utc") or "")
        if not observed:
            continue
        key = (observed, str(model["model_id"]))
        old = output.get(key)
        if old is None or parse_ts(str(model["decision_ts_utc"])) < parse_ts(str(old["decision_ts_utc"])):
            output[key] = {
                "decision_ts_utc": model.get("decision_ts_utc"),
                "p_no": finite(model.get("p_model")),
                "market_no_mid": finite(candidate.get("market_p")),
                "no_effective_cost": finite(candidate.get("executable_cost")),
                "edge_after_fee": finite((candidate.get("metadata") or {}).get("edge_after_fee")),
                "selected": bool(candidate.get("selected")),
                "candidate_status": candidate.get("candidate_status"),
                "weather_probability_stay": finite((model.get("metadata") or {}).get("weather_probability_stay")),
            }
    return output


def quote_thresholds(
    path: Path, *, target_date: str, bracket: int, second_obs: datetime
) -> dict[str, dict[str, Any] | None]:
    books = []
    for row in iter_jsonl(path):
        if (
            row.get("target_date") == target_date
            and row.get("city") == "Tokyo"
            and row.get("outcome") == "no"
            and str(row.get("bracket")) == str(bracket)
            and int(row.get("reference_market_value") or -999) == bracket
            and row.get("book_status") == "ok"
        ):
            fetched = parse_ts(str(row["book_fetched_at_utc"]))
            if fetched >= second_obs:
                books.append(row)
    books.sort(key=lambda row: parse_ts(str(row["book_fetched_at_utc"])))

    def first_at(threshold: float) -> dict[str, Any] | None:
        for row in books:
            bid = finite((row.get("summary") or {}).get("best_bid"))
            if bid is not None and bid >= threshold:
                return {
                    "book_fetched_at_utc": row["book_fetched_at_utc"],
                    "best_bid": bid,
                    "best_ask": finite((row.get("summary") or {}).get("best_ask")),
                    "source_obs_ts_utc": row.get("source_obs_ts_utc"),
                }
        return None

    return {"bid_ge_0_10": first_at(0.10), "bid_ge_0_50": first_at(0.50), "bid_ge_0_99": first_at(0.99)}


def audit(
    *, target_date: str, bracket: int, cross_margin_c: float, jma_path: Path,
    books_path: Path, bundles_path: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    jma = load_jma(jma_path, target_date)
    episode = find_second_cross_episode(jma, bracket=bracket, cross_margin_c=cross_margin_c)
    first_index = int(episode["first_index"])
    trough_index = int(episode["trough_index"])
    second_index = int(episode["second_index"])
    prior_peak_c = max(float(row["temp_c"]) for row in jma[: trough_index + 1])
    model_rows = load_model_rows(bundles_path, target_date, bracket)
    checkpoints: list[dict[str, Any]] = []
    for index in range(trough_index, second_index + 1):
        source = jma[index]
        observed = str(source["observation_time_utc"])
        record: dict[str, Any] = {
            "source_obs_ts_utc": observed,
            "source_first_seen_at_utc": source["source_first_seen_at_utc"],
            **path_features(jma, index, trough_index=trough_index, prior_peak_c=prior_peak_c),
            "is_second_cross": index == second_index,
        }
        for model_id in MODEL_IDS:
            prefix = "v7" if model_id.endswith("v7") else "overshoot_v2"
            values = model_rows.get((observed, model_id), {})
            for key in (
                "decision_ts_utc", "p_no", "market_no_mid", "no_effective_cost",
                "edge_after_fee", "selected", "candidate_status", "weather_probability_stay",
            ):
                record[f"{prefix}_{key}"] = values.get(key)
        checkpoints.append(record)

    second = jma[second_index]
    second_obs = parse_ts(str(second["observation_time_utc"]))
    thresholds = quote_thresholds(
        books_path,
        target_date=target_date,
        bracket=bracket,
        second_obs=second_obs,
    )
    second_seen = parse_ts(str(second["source_first_seen_at_utc"]))
    for value in thresholds.values():
        if value is not None:
            value["lead_vs_local_second_cross_first_seen_seconds"] = (
                second_seen - parse_ts(str(value["book_fetched_at_utc"]))
            ).total_seconds()

    pre_cross = [row for row in checkpoints if not row["is_second_cross"]]
    model_summary: dict[str, Any] = {}
    for prefix, model_id in (("v7", MODEL_IDS[0]), ("overshoot_v2", MODEL_IDS[1])):
        scored = [row for row in pre_cross if row.get(f"{prefix}_p_no") is not None]
        model_summary[model_id] = {
            "pre_cross_scored_checkpoints": len(scored),
            "max_pre_cross_p_no": max((float(row[f"{prefix}_p_no"]) for row in scored), default=None),
            "max_pre_cross_edge_after_fee": max(
                (float(row[f"{prefix}_edge_after_fee"]) for row in scored if row.get(f"{prefix}_edge_after_fee") is not None),
                default=None,
            ),
            "any_pre_cross_positive_edge": any(
                row.get(f"{prefix}_edge_after_fee") is not None
                and float(row[f"{prefix}_edge_after_fee"]) > 0
                for row in scored
            ),
            "any_pre_cross_selected": any(bool(row.get(f"{prefix}_selected")) for row in scored),
        }

    trough_obs = str(jma[trough_index]["observation_time_utc"])
    trough_book_anchors = sorted({
        int(row.get("reference_market_value"))
        for row in iter_jsonl(books_path)
        if row.get("target_date") == target_date
        and row.get("source_obs_ts_utc") == trough_obs
        and row.get("reference_market_value") is not None
    })
    feature_visible = any(
        (row.get("jma_temp_delta_10m") or 0) > 0
        and (row.get("jma_temp_slope_30m_cph") or 0) > 0
        and (row.get("jma_warming_run_count") or 0) >= 1
        for row in pre_cross
    )
    summary = {
        "schema_version": "tokyo_second_reheat_pit_audit_v1",
        "target_date": target_date,
        "bracket": bracket,
        "cross_definition": f"JMA temp >= bracket + {cross_margin_c:.1f}C; diagnostic heuristic, not settlement truth",
        "pit_provenance": "collector_exact_only",
        "first_cross": {
            "source_obs_ts_utc": jma[first_index]["observation_time_utc"],
            "source_first_seen_at_utc": jma[first_index]["source_first_seen_at_utc"],
            "temp_c": float(jma[first_index]["temp_c"]),
        },
        "trough": {
            "source_obs_ts_utc": jma[trough_index]["observation_time_utc"],
            "source_first_seen_at_utc": jma[trough_index]["source_first_seen_at_utc"],
            "temp_c": float(jma[trough_index]["temp_c"]),
        },
        "second_cross": {
            "source_obs_ts_utc": second["observation_time_utc"],
            "source_first_seen_at_utc": second["source_first_seen_at_utc"],
            "temp_c": float(second["temp_c"]),
        },
        "feature_layer_saw_pre_cross_reheat": feature_visible,
        "models": model_summary,
        "market_repricing": thresholds,
        "runtime_anchor_audit": {
            "trough_source_obs_ts_utc": trough_obs,
            "captured_reference_market_values": trough_book_anchors,
            "held_or_official_bracket_captured_at_trough": bracket in trough_book_anchors,
            "current_bracket_model_bundle_at_trough": any(key[0] == trough_obs for key in model_rows),
        },
        "verdict": (
            "features_visible_but_no_pre_cross_probability_or_edge_recognition"
            if feature_visible and not any(item["any_pre_cross_positive_edge"] for item in model_summary.values())
            else "review_required"
        ),
    }
    return summary, checkpoints


def scan_exact_episode_census(
    *, runtime_root: Path, start_date: str, end_date: str, cross_margin_c: float
) -> list[dict[str, Any]]:
    """Return exact-capture second-cross episodes with pre-cross model evidence."""
    output: list[dict[str, Any]] = []
    bundles_path = runtime_root / "city_probability_runtime_v3" / "decision_bundles.jsonl"
    for day_dir in sorted((runtime_root / "live_cross_observations").glob("20??-??-??")):
        target_date = day_dir.name
        if target_date < start_date or target_date > end_date:
            continue
        jma_path = day_dir / "high_frequency_observations.jsonl"
        if not jma_path.is_file():
            continue
        rows = load_jma(jma_path, target_date)
        if not rows:
            continue
        temperatures = [float(row["temp_c"]) for row in rows]
        for bracket in range(math.floor(min(temperatures)) - 1, math.ceil(max(temperatures)) + 1):
            try:
                episode = find_second_cross_episode(
                    rows, bracket=bracket, cross_margin_c=cross_margin_c
                )
            except ValueError:
                continue
            model_rows = load_model_rows(bundles_path, target_date, bracket)
            trough_index = int(episode["trough_index"])
            second_index = int(episode["second_index"])
            record: dict[str, Any] = {
                "target_date": target_date,
                "bracket": bracket,
                "first_cross_obs_ts_utc": rows[int(episode["first_index"])]["observation_time_utc"],
                "trough_obs_ts_utc": rows[trough_index]["observation_time_utc"],
                "trough_temp_c": float(rows[trough_index]["temp_c"]),
                "second_cross_obs_ts_utc": rows[second_index]["observation_time_utc"],
                "second_cross_temp_c": float(rows[second_index]["temp_c"]),
            }
            any_scored = False
            for prefix, model_id in (("v7", MODEL_IDS[0]), ("overshoot_v2", MODEL_IDS[1])):
                evidence = []
                for index in range(trough_index + 1, second_index):
                    observed = str(rows[index]["observation_time_utc"])
                    model_row = model_rows.get((observed, model_id))
                    if model_row and model_row.get("p_no") is not None:
                        evidence.append(model_row)
                any_scored = any_scored or bool(evidence)
                record[f"{prefix}_pre_cross_scored"] = len(evidence)
                record[f"{prefix}_max_pre_cross_p_no"] = max(
                    (float(row["p_no"]) for row in evidence), default=None
                )
                record[f"{prefix}_max_pre_cross_edge_after_fee"] = max(
                    (
                        float(row["edge_after_fee"])
                        for row in evidence
                        if row.get("edge_after_fee") is not None
                    ),
                    default=None,
                )
                record[f"{prefix}_recognized_at_p50"] = any(
                    float(row["p_no"]) >= 0.5 for row in evidence
                )
            if any_scored:
                output.append(record)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--bracket", type=int, required=True)
    parser.add_argument("--cross-margin-c", type=float, default=0.7)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--scan-start")
    parser.add_argument("--scan-end")
    args = parser.parse_args()
    summary, checkpoints = audit(
        target_date=args.target_date,
        bracket=args.bracket,
        cross_margin_c=args.cross_margin_c,
        jma_path=args.runtime_root / "live_cross_observations" / args.target_date / "high_frequency_observations.jsonl",
        books_path=args.runtime_root / "tokyo_current_break_active_ladder_shadow" / "active_bracket_books" / f"{args.target_date}.jsonl",
        bundles_path=args.runtime_root / "city_probability_runtime_v3" / "decision_bundles.jsonl",
    )
    census: list[dict[str, Any]] = []
    if args.scan_start or args.scan_end:
        if not args.scan_start or not args.scan_end:
            parser.error("--scan-start and --scan-end must be supplied together")
        census = scan_exact_episode_census(
            runtime_root=args.runtime_root,
            start_date=args.scan_start,
            end_date=args.scan_end,
            cross_margin_c=args.cross_margin_c,
        )
        summary["exact_episode_census"] = {
            "start_date": args.scan_start,
            "end_date": args.scan_end,
            "episodes_with_pre_cross_model_evidence": len(census),
            "v7_recognized_at_p50": sum(bool(row["v7_recognized_at_p50"]) for row in census),
            "overshoot_v2_recognized_at_p50": sum(
                bool(row["overshoot_v2_recognized_at_p50"]) for row in census
            ),
            "episodes_with_positive_fee_edge": sum(
                any(
                    row.get(key) is not None and float(row[key]) > 0
                    for key in (
                        "v7_max_pre_cross_edge_after_fee",
                        "overshoot_v2_max_pre_cross_edge_after_fee",
                    )
                )
                for row in census
            ),
        }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if checkpoints:
        with (args.output_dir / "checkpoints.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(checkpoints[0]))
            writer.writeheader()
            writer.writerows(checkpoints)
    if census:
        with (args.output_dir / "exact_episode_census.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(census[0]))
            writer.writeheader()
            writer.writerows(census)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
