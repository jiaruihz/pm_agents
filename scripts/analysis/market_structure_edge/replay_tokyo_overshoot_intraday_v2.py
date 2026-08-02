#!/usr/bin/env python3
"""Replay Tokyo overshoot v2 at every exact JMA first-seen checkpoint.

The replay grain is one row per JMA observation.  For each observation it uses
the first subsequently captured, two-sided current-exact-bracket book whose
official METAR state was available at that book timestamp.  Settlement is
joined only after all PIT features and probabilities have been computed.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any, Iterable

import joblib


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_city_probability_shadow.tokyo import (  # noqa: E402
    TOKYO,
    _book_contains_anchor,
    _finite,
    _jma_history,
    _jsonl,
    _market_prices,
    _offset_probability,
    _official_history,
    _parse_ts,
    _round_native_c,
    _weather_features,
)


UTC = timezone.utc
DEFAULT_BOOK = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/"
    "tokyo_current_break_active_ladder_shadow/active_bracket_books/2026-08-01.jsonl"
)
DEFAULT_JMA = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/"
    "live_cross_observations/high_frequency_observations.jsonl"
)
DEFAULT_OFFICIAL = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/observations"
)
DEFAULT_ARTIFACT = ROOT / (
    "docs/analysis/2026-08/generated/tokyo_overshoot_market_residual_v2/"
    "tokyo_overshoot_market_residual_v2.joblib"
)
DEFAULT_OUT = ROOT / (
    "docs/analysis/2026-08/generated/tokyo_overshoot_intraday_replay_v2"
)
FEE_RATE = 0.05


def official_fee_per_share(price: float) -> float:
    return round(FEE_RATE * price * (1.0 - price), 5)


def _local_hour(timestamp: datetime) -> float:
    local = timestamp.astimezone(TOKYO)
    return local.hour + local.minute / 60.0 + local.second / 3600.0


def _eligible_book_rows(path: Path, target_date: str) -> list[dict[str, Any]]:
    rows = [
        row
        for row in _jsonl(path)
        if row.get("city") == "Tokyo"
        and row.get("source") == "jma_amedas"
        and row.get("target_date") == target_date
        and row.get("outcome") == "no"
        and row.get("book_status") == "ok"
        and row.get("source_obs_ts_utc")
        and row.get("book_fetched_at_utc")
    ]
    return sorted(rows, key=lambda row: _parse_ts(str(row["book_fetched_at_utc"])))


def _group_by_source_observation(
    rows: Iterable[dict[str, Any]],
) -> dict[datetime, list[dict[str, Any]]]:
    grouped: dict[datetime, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_parse_ts(str(row["source_obs_ts_utc"])), []).append(row)
    return grouped


@dataclass(frozen=True)
class SelectedCheckpoint:
    book: dict[str, Any]
    official: list[dict[str, Any]]
    official_anchor: int


def select_first_pit_current_book(
    candidates: Iterable[dict[str, Any]],
    official_journal_dir: Path,
    target_date: str,
) -> SelectedCheckpoint | None:
    """Select first two-sided current book, retaining one-sided as coverage fallback."""
    one_sided_fallback: SelectedCheckpoint | None = None
    for book in sorted(
        candidates, key=lambda row: _parse_ts(str(row["book_fetched_at_utc"]))
    ):
        decision = _parse_ts(str(book["book_fetched_at_utc"]))
        official = _official_history(official_journal_dir, target_date, decision)
        if not official:
            continue
        anchor = _round_native_c(float(official[-1]["running_max_c"]))
        if not _book_contains_anchor(book, anchor):
            continue
        selected = SelectedCheckpoint(book=book, official=official, official_anchor=anchor)
        if _market_prices(book)["no_mid"] is not None:
            return selected
        if one_sided_fallback is None:
            one_sided_fallback = selected
    return one_sided_fallback


def replay(
    *,
    target_date: str,
    start_hour_jst: float,
    end_hour_jst: float,
    edge_threshold: float,
    book_path: Path,
    jma_path: Path,
    official_journal_dir: Path,
    artifact_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    books = _eligible_book_rows(book_path, target_date)
    grouped = _group_by_source_observation(books)
    observations = sorted(
        observed
        for observed in grouped
        if start_hour_jst <= _local_hour(observed) < end_hour_jst
    )
    artifact = joblib.load(artifact_path)
    no_floor_artifact = {**artifact, "market_logit_floor": 1e-6}

    # Settlement is deliberately loaded before the loop only as a detached
    # label.  It is never passed into selection, features, or model scoring.
    settlement_decision = datetime.fromisoformat(target_date).replace(tzinfo=TOKYO)
    settlement_decision = settlement_decision + timedelta(days=1, hours=12)
    final_official = _official_history(
        official_journal_dir, target_date, settlement_decision.astimezone(UTC)
    )
    if not final_official:
        raise RuntimeError(f"no final official observations for {target_date}")
    final_bracket = _round_native_c(float(final_official[-1]["running_max_c"]))

    output: list[dict[str, Any]] = []
    missing_current_book = 0
    one_sided_current_book = 0
    for source_obs in observations:
        selected = select_first_pit_current_book(
            grouped[source_obs], official_journal_dir, target_date
        )
        if selected is None:
            missing_current_book += 1
            first_book = min(
                grouped[source_obs],
                key=lambda row: _parse_ts(str(row["book_fetched_at_utc"])),
            )
            decision = _parse_ts(str(first_book["book_fetched_at_utc"]))
            source_jma = _jma_history(jma_path, target_date, source_obs, decision)
            source = source_jma[-1] if source_jma else {}
            source_first_seen = _parse_ts(str(source["source_first_seen_at_utc"]))
            official = _official_history(official_journal_dir, target_date, decision)
            official_anchor = (
                _round_native_c(float(official[-1]["running_max_c"]))
                if official
                else None
            )
            output.append(
                {
                    "target_date": target_date,
                    "jma_observation_jst": source_obs.astimezone(TOKYO).isoformat(),
                    "jma_first_seen_jst": source_first_seen.astimezone(TOKYO).isoformat(),
                    "decision_jst": decision.astimezone(TOKYO).isoformat(),
                    "first_seen_latency_seconds": round(
                        (source_first_seen - source_obs).total_seconds(), 3
                    ),
                    "book_after_first_seen_seconds": round(
                        (decision - source_first_seen).total_seconds(), 3
                    ),
                    "current_bracket": official_anchor,
                    "evaluation_status": "not_scorable",
                    "not_scorable_reason": "anchor_capture_gap",
                    "captured_brackets": "|".join(
                        sorted({str(row.get("bracket")) for row in grouped[source_obs]})
                    ),
                    "final_bracket": final_bracket,
                    "no_label": (
                        None
                        if official_anchor is None
                        else int(final_bracket != official_anchor)
                    ),
                    "availability_clock_class": "collector_exact_hash_verified",
                    "source_payload_hash": source.get("payload_hash"),
                }
            )
            continue
        book = selected.book
        decision = _parse_ts(str(book["book_fetched_at_utc"]))
        jma = _jma_history(jma_path, target_date, source_obs, decision)
        if not jma or _parse_ts(str(jma[-1]["observation_time_utc"])) != source_obs:
            raise RuntimeError(f"no exact first-seen JMA row for {source_obs.isoformat()}")
        source = jma[-1]
        source_first_seen = _parse_ts(str(source["source_first_seen_at_utc"]))
        if source_first_seen > decision:
            raise RuntimeError("source first-seen is after selected decision clock")
        latest_official = selected.official[-1]
        official_fetched = _parse_ts(str(latest_official["fetched_at_utc"]))
        official_observed = _parse_ts(str(latest_official["last_obs_utc"]))
        if official_fetched > decision or official_observed > decision:
            raise RuntimeError("future official observation entered PIT checkpoint")

        prices = _market_prices(book)
        no_mid = _finite(prices["no_mid"])
        no_ask = _finite(prices["no_ask"])
        features = _weather_features(jma, selected.official, decision)
        model_features = {name: features.get(name) for name in artifact["features"]}
        scorable = no_mid is not None and no_ask is not None
        if not scorable:
            one_sided_current_book += 1
        p_no_v2 = (
            _offset_probability(artifact, model_features, no_mid)
            if scorable
            else None
        )
        p_no_no_floor = (
            _offset_probability(no_floor_artifact, model_features, no_mid)
            if scorable
            else None
        )
        fee = official_fee_per_share(no_ask) if scorable else None
        edge = p_no_v2 - no_ask - fee if scorable else None
        edge_no_floor = p_no_no_floor - no_ask - fee if scorable else None
        bracket = selected.official_anchor
        row = {
            "target_date": target_date,
            "jma_observation_jst": source_obs.astimezone(TOKYO).isoformat(),
            "jma_first_seen_jst": source_first_seen.astimezone(TOKYO).isoformat(),
            "decision_jst": decision.astimezone(TOKYO).isoformat(),
            "first_seen_latency_seconds": round(
                (source_first_seen - source_obs).total_seconds(), 3
            ),
            "book_after_first_seen_seconds": round(
                (decision - source_first_seen).total_seconds(), 3
            ),
            "current_bracket": bracket,
            "evaluation_status": "scored" if scorable else "not_scorable",
            "not_scorable_reason": "" if scorable else "one_sided_current_book",
            "captured_brackets": "",
            "jma_temp_c": features["jma_temp_c"],
            "jma_running_max_c": features["jma_running_max_c"],
            "jma_slope_60m_cph": features["jma_temp_slope_60m_cph"],
            "minutes_since_jma_strict_high": features[
                "minutes_since_jma_strict_high"
            ],
            "prior_metar_temp_c": features["prior_metar_temp_c"],
            "prior_metar_running_max_c": features["prior_metar_running_max_c"],
            "official_last_obs_utc": latest_official["last_obs_utc"],
            "official_snapshot_fetched_at_utc": latest_official["fetched_at_utc"],
            "no_bid": prices["no_bid"],
            "no_ask": no_ask,
            "no_mid": no_mid,
            "fee_per_share": fee,
            "model_p_no_v2": p_no_v2,
            "edge_after_fee_v2": edge,
            "would_enter_v2": int(edge >= edge_threshold) if scorable else 0,
            "model_p_no_no_floor": p_no_no_floor,
            "edge_after_fee_no_floor": edge_no_floor,
            "would_enter_no_floor": (
                int(edge_no_floor >= edge_threshold) if scorable else 0
            ),
            "final_bracket": final_bracket,
            "no_label": int(final_bracket != bracket),
            "v2_prediction_correct_at_050": (
                int((p_no_v2 >= 0.5) == (final_bracket != bracket))
                if scorable
                else None
            ),
            "availability_clock_class": "collector_exact_hash_verified",
            "book_snapshot_id": book.get("book_snapshot_id"),
            "capture_cycle_id": book.get("capture_cycle_id"),
            "source_payload_hash": source.get("payload_hash"),
        }
        output.append(row)

    triggered = [row for row in output if row.get("would_enter_v2")]
    triggered_no_floor = [row for row in output if row.get("would_enter_no_floor")]
    scored = [row for row in output if row.get("evaluation_status") == "scored"]
    model_brier = sum(
        (float(row["model_p_no_v2"]) - int(row["no_label"])) ** 2
        for row in scored
    ) / len(scored)
    market_brier = sum(
        (float(row["no_mid"]) - int(row["no_label"])) ** 2 for row in scored
    ) / len(scored)
    summary = {
        "target_date": target_date,
        "grain": "one_row_per_jma_observation_first_seen_checkpoint",
        "window_jst": [start_hour_jst, end_hour_jst],
        "raw_book_rows": len(books),
        "source_observations_all_day": len(grouped),
        "source_observations_in_window": len(observations),
        "checkpoint_rows_preserved": len(output),
        "pit_current_book_rows": len(output) - missing_current_book,
        "scored_two_sided_rows": len(output) - missing_current_book - one_sided_current_book,
        "one_sided_current_book_rows": one_sided_current_book,
        "missing_current_book_rows": missing_current_book,
        "edge_threshold_after_fee": edge_threshold,
        "v2_trigger_count": len(triggered),
        "v2_trigger_wins": sum(row["no_label"] for row in triggered),
        "no_floor_trigger_count": len(triggered_no_floor),
        "no_floor_trigger_wins": sum(row["no_label"] for row in triggered_no_floor),
        "v2_brier_on_scored_rows": model_brier,
        "market_brier_on_scored_rows": market_brier,
        "v2_edge_after_fee_min": min(row["edge_after_fee_v2"] for row in scored),
        "v2_edge_after_fee_max": max(row["edge_after_fee_v2"] for row in scored),
        "final_official_bracket": final_bracket,
        "model_training_end": artifact.get("training_end"),
        "model_market_logit_floor": artifact.get("market_logit_floor"),
        "label_joined_after_scoring": True,
    }
    return output, summary


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError("replay produced no rows")
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date", default="2026-08-01")
    parser.add_argument("--start-hour-jst", type=float, default=10.0)
    parser.add_argument("--end-hour-jst", type=float, default=18.0)
    parser.add_argument("--edge-threshold", type=float, default=0.02)
    parser.add_argument("--book-path", type=Path, default=DEFAULT_BOOK)
    parser.add_argument("--jma-path", type=Path, default=DEFAULT_JMA)
    parser.add_argument("--official-journal-dir", type=Path, default=DEFAULT_OFFICIAL)
    parser.add_argument("--artifact-path", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    rows, summary = replay(
        target_date=args.target_date,
        start_hour_jst=args.start_hour_jst,
        end_hour_jst=args.end_hour_jst,
        edge_threshold=args.edge_threshold,
        book_path=args.book_path,
        jma_path=args.jma_path,
        official_journal_dir=args.official_journal_dir,
        artifact_path=args.artifact_path,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(args.out_dir / "checkpoints.csv", rows)
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
