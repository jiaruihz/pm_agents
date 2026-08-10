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
import math
from pathlib import Path
import random
import re
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
)


UTC = timezone.utc
DEFAULT_BOOK = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/"
    "tokyo_current_break_active_ladder_shadow/active_bracket_books/2026-08-01.jsonl"
)
DEFAULT_JMA = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/"
    "live_cross_observations"
)
DEFAULT_OFFICIAL = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output/observations"
)
DEFAULT_ARTIFACT = ROOT / (
    "docs/analysis/2026-08/generated/tokyo_overshoot_market_residual_v2/"
    "tokyo_overshoot_market_residual_v2.joblib"
)
DEFAULT_SETTLEMENT_DIR = (
    ROOT / "runtime/weather_edge_v1/market_data/cache/pm_history"
)
DEFAULT_OUT = ROOT / (
    "docs/analysis/2026-08/generated/tokyo_overshoot_intraday_replay_v2"
)
FEE_RATE = 0.05
REPLAY_SHARES = 5.0


def official_fee_per_share(price: float) -> float:
    return round(FEE_RATE * price * (1.0 - price), 5)


def fee_adjusted_binary_pnl(payout: int, ask: float) -> tuple[float, float]:
    """Return taker entry cost and settled PnL for one binary share."""
    entry_cost = ask + official_fee_per_share(ask)
    return entry_cost, float(payout) - entry_cost


def binary_logloss(probability: float, label: int) -> float:
    clipped = min(max(probability, 1e-6), 1.0 - 1e-6)
    return -(label * math.log(clipped) + (1 - label) * math.log(1.0 - clipped))


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


def _jma_rows_as_of(
    rows: Iterable[dict[str, Any]],
    observation_through: datetime,
    available_through: datetime,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if _parse_ts(str(row["observation_time_utc"])) <= observation_through
        and _parse_ts(str(row["source_first_seen_at_utc"])) <= available_through
    ]


def timely_books_after_first_seen(
    candidates: Iterable[dict[str, Any]],
    source_first_seen: datetime,
    max_delay_seconds: float,
) -> list[dict[str, Any]]:
    """Exclude books captured before first-seen or after a stale-source delay."""
    return [
        row
        for row in candidates
        if 0
        <= (
            _parse_ts(str(row["book_fetched_at_utc"])) - source_first_seen
        ).total_seconds()
        <= max_delay_seconds
    ]


@dataclass(frozen=True)
class SelectedCheckpoint:
    book: dict[str, Any]
    official: list[dict[str, Any]]
    official_anchor: int
    official_anchor_provenance: str


def _captured_official_anchor(book: dict[str, Any]) -> int | None:
    anchors = book.get("capture_anchor_values") or {}
    value = _finite(anchors.get("official"))
    return None if value is None else _round_native_c(value)


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
        anchor = _captured_official_anchor(book)
        if anchor is not None:
            official: list[dict[str, Any]] = []
            provenance = "book_capture_anchor_values"
        else:
            official = _official_history(official_journal_dir, target_date, decision)
            if not official:
                continue
            anchor = _round_native_c(float(official[-1]["running_max_c"]))
            provenance = "official_observation_journal"
        if not _book_contains_anchor(book, anchor):
            continue
        selected = SelectedCheckpoint(
            book=book,
            official=official,
            official_anchor=anchor,
            official_anchor_provenance=provenance,
        )
        if _market_prices(book)["no_mid"] is not None:
            return selected
        if one_sided_fallback is None:
            one_sided_fallback = selected
    return one_sided_fallback


def _jma_v2_features(
    jma: list[dict[str, Any]], supported_names: Iterable[str]
) -> dict[str, float | None]:
    """Build the frozen v2's JMA-only features without inventing METAR rows."""
    source_time = _parse_ts(str(jma[-1]["observation_time_utc"]))
    temperatures = [float(row["temp_c"]) for row in jma]
    current_temp = temperatures[-1]
    running_max = max(temperatures)
    strict_high_index = next(
        index for index, value in enumerate(temperatures) if value == running_max
    )
    strict_high_time = _parse_ts(str(jma[strict_high_index]["observation_time_utc"]))
    lag60 = next(
        (
            float(row["temp_c"])
            for row in jma
            if source_time - timedelta(minutes=60)
            <= _parse_ts(str(row["observation_time_utc"]))
            < source_time
        ),
        None,
    )
    local = source_time.astimezone(TOKYO)
    local_hour = local.hour + local.minute / 60.0
    values = {
        "distance_to_next_jma_lattice_c": _round_native_c(running_max)
        + 0.5
        - running_max,
        "jma_temp_slope_60m_cph": (
            None if lag60 is None else current_temp - lag60
        ),
        "minutes_since_jma_strict_high": (
            source_time - strict_high_time
        ).total_seconds()
        / 60.0,
        "remaining_to_18h": 18.0 - local_hour,
    }
    unsupported = sorted(set(supported_names) - set(values))
    if unsupported:
        raise RuntimeError(
            "frozen artifact requires unavailable PIT METAR features: "
            + ",".join(unsupported)
        )
    return values


def _pm_history_final_bracket(
    settlement_dir: Path, target_date: str
) -> tuple[int, str, str]:
    path = settlement_dir / f"Tokyo_{target_date}.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    brackets = payload.get("brackets") or []
    winners = [
        row
        for row in brackets
        if _finite(row.get("final_price")) is not None
        and float(row["final_price"]) >= 0.99
    ]
    if len(winners) != 1:
        raise RuntimeError(
            f"expected one settled Tokyo bracket for {target_date}, got {len(winners)}"
        )
    match = re.search(r"-?\d+", str(winners[0].get("label") or ""))
    if not match:
        raise RuntimeError(f"unparseable settlement label: {winners[0].get('label')}")
    return int(match.group(0)), "pm_history_near_binary", str(path)


def replay(
    *,
    target_date: str,
    start_hour_jst: float,
    end_hour_jst: float,
    edge_threshold: float,
    max_book_after_first_seen_seconds: float,
    book_path: Path,
    jma_path: Path,
    official_journal_dir: Path,
    artifact_path: Path,
    settlement_dir: Path = DEFAULT_SETTLEMENT_DIR,
    settlement_bracket: int | None = None,
    settlement_evidence: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    books = _eligible_book_rows(book_path, target_date)
    grouped = _group_by_source_observation(books)
    observations = sorted(
        observed
        for observed in grouped
        if start_hour_jst <= _local_hour(observed) < end_hour_jst
    )
    if not observations:
        raise RuntimeError(f"no source observations in replay window for {target_date}")
    max_candidate_decision = max(
        _parse_ts(str(row["book_fetched_at_utc"])) for row in books
    )
    all_jma_first_seen = _jma_history(
        jma_path, target_date, max(observations), max_candidate_decision
    )
    artifact = joblib.load(artifact_path)
    no_floor_artifact = {**artifact, "market_logit_floor": 1e-6}

    # Settlement is a detached post-event label.  Never derive it from a
    # potentially incomplete PIT observation journal: use market settlement,
    # or an explicit label-only late backfill with named evidence.
    if settlement_bracket is None:
        final_bracket, settlement_source, settlement_ref = _pm_history_final_bracket(
            settlement_dir, target_date
        )
        settlement_availability = "post_event_settlement"
    else:
        if not settlement_evidence:
            raise RuntimeError(
                "explicit settlement bracket requires --settlement-evidence"
            )
        final_bracket = int(settlement_bracket)
        settlement_source = "explicit_late_backfill_label_only"
        settlement_ref = settlement_evidence
        settlement_availability = "late_backfill_label_only_not_first_seen"

    output: list[dict[str, Any]] = []
    missing_current_book = 0
    one_sided_current_book = 0
    for source_obs in observations:
        all_observation_books = grouped[source_obs]
        latest_candidate_decision = max(
            _parse_ts(str(row["book_fetched_at_utc"]))
            for row in all_observation_books
        )
        available_jma = _jma_rows_as_of(
            all_jma_first_seen, source_obs, latest_candidate_decision
        )
        if not available_jma or _parse_ts(
            str(available_jma[-1]["observation_time_utc"])
        ) != source_obs:
            raise RuntimeError(f"no exact first-seen JMA row for {source_obs.isoformat()}")
        source_first_seen = _parse_ts(
            str(available_jma[-1]["source_first_seen_at_utc"])
        )
        timely_books = timely_books_after_first_seen(
            all_observation_books,
            source_first_seen,
            max_book_after_first_seen_seconds,
        )
        selected = select_first_pit_current_book(
            timely_books, official_journal_dir, target_date
        )
        if selected is None:
            missing_current_book += 1
            first_book = min(
                timely_books or all_observation_books,
                key=lambda row: _parse_ts(str(row["book_fetched_at_utc"])),
            )
            decision = _parse_ts(str(first_book["book_fetched_at_utc"]))
            source_jma = _jma_rows_as_of(all_jma_first_seen, source_obs, decision)
            source = source_jma[-1] if source_jma else {}
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
                    "not_scorable_reason": (
                        "anchor_capture_gap"
                        if timely_books
                        else "timely_book_capture_gap"
                    ),
                    "captured_brackets": "|".join(
                        sorted({str(row.get("bracket")) for row in all_observation_books})
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
        jma = _jma_rows_as_of(all_jma_first_seen, source_obs, decision)
        if not jma or _parse_ts(str(jma[-1]["observation_time_utc"])) != source_obs:
            raise RuntimeError(f"no exact first-seen JMA row for {source_obs.isoformat()}")
        source = jma[-1]
        source_first_seen = _parse_ts(str(source["source_first_seen_at_utc"]))
        if source_first_seen > decision:
            raise RuntimeError("source first-seen is after selected decision clock")
        latest_official = selected.official[-1] if selected.official else None
        if latest_official is not None:
            official_fetched = _parse_ts(str(latest_official["fetched_at_utc"]))
            official_observed = _parse_ts(str(latest_official["last_obs_utc"]))
            if official_fetched > decision or official_observed > decision:
                raise RuntimeError("future official observation entered PIT checkpoint")

        prices = _market_prices(book)
        no_mid = _finite(prices["no_mid"])
        no_ask = _finite(prices["no_ask"])
        yes_ask = _finite(prices["yes_ask"])
        model_features = _jma_v2_features(jma, artifact["features"])
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
        no_label = int(final_bracket != bracket)
        if scorable:
            entry_cost, hypothetical_pnl = fee_adjusted_binary_pnl(no_label, no_ask)
            yes_label = 1 - no_label
            yes_fee = official_fee_per_share(yes_ask)
            yes_edge = (1.0 - p_no_v2) - yes_ask - yes_fee
            yes_entry_cost, hypothetical_yes_pnl = fee_adjusted_binary_pnl(
                yes_label, yes_ask
            )
        else:
            entry_cost, hypothetical_pnl = None, None
            yes_label, yes_fee, yes_edge = None, None, None
            yes_entry_cost, hypothetical_yes_pnl = None, None
        model_direction_buy = int(p_no_v2 >= 0.5) if scorable else 0
        would_enter = int(edge >= edge_threshold) if scorable else 0
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
            "jma_temp_c": float(source["temp_c"]),
            "jma_running_max_c": max(float(item["temp_c"]) for item in jma),
            "jma_slope_60m_cph": model_features["jma_temp_slope_60m_cph"],
            "minutes_since_jma_strict_high": model_features[
                "minutes_since_jma_strict_high"
            ],
            "prior_metar_temp_c": (
                latest_official.get("current_temp_c") if latest_official else None
            ),
            "prior_metar_running_max_c": (
                latest_official.get("running_max_c")
                if latest_official
                else selected.official_anchor
            ),
            "official_last_obs_utc": (
                latest_official.get("last_obs_utc") if latest_official else None
            ),
            "official_snapshot_fetched_at_utc": (
                latest_official.get("fetched_at_utc") if latest_official else None
            ),
            "official_anchor_provenance": selected.official_anchor_provenance,
            "no_bid": prices["no_bid"],
            "no_ask": no_ask,
            "no_mid": no_mid,
            "yes_ask": yes_ask,
            "fee_per_share": fee,
            "model_p_no_v2": p_no_v2,
            "edge_after_fee_v2": edge,
            "would_enter_v2": would_enter,
            "model_p_no_no_floor": p_no_no_floor,
            "edge_after_fee_no_floor": edge_no_floor,
            "would_enter_no_floor": (
                int(edge_no_floor >= edge_threshold) if scorable else 0
            ),
            "final_bracket": final_bracket,
            "no_label": no_label,
            "yes_label": yes_label,
            "model_p_yes_v2": 1.0 - p_no_v2 if scorable else None,
            "yes_fee_per_share": yes_fee,
            "yes_edge_after_fee_v2": yes_edge,
            "would_enter_yes_v2": (
                int(yes_edge >= edge_threshold) if scorable else 0
            ),
            "hypothetical_yes_entry_cost_1share": yes_entry_cost,
            "hypothetical_yes_pnl_1share_fee_adjusted": hypothetical_yes_pnl,
            "hypothetical_yes_pnl_5shares_fee_adjusted": (
                hypothetical_yes_pnl * REPLAY_SHARES if scorable else None
            ),
            "hypothetical_no_entry_cost_1share": entry_cost,
            "hypothetical_no_pnl_1share_fee_adjusted": hypothetical_pnl,
            "hypothetical_no_pnl_5shares_fee_adjusted": (
                hypothetical_pnl * REPLAY_SHARES if scorable else None
            ),
            "model_direction_buy_no": model_direction_buy,
            "model_direction_pnl_1share_fee_adjusted": (
                hypothetical_pnl if model_direction_buy else 0.0
            ),
            "model_direction_pnl_5shares_fee_adjusted": (
                hypothetical_pnl * REPLAY_SHARES if model_direction_buy else 0.0
            ),
            "v2_policy_pnl_1share_fee_adjusted": (
                hypothetical_pnl if would_enter else 0.0
            ),
            "v2_policy_pnl_5shares_fee_adjusted": (
                hypothetical_pnl * REPLAY_SHARES if would_enter else 0.0
            ),
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
    triggered_yes = [row for row in output if row.get("would_enter_yes_v2")]
    triggered_no_floor = [row for row in output if row.get("would_enter_no_floor")]
    scored = [row for row in output if row.get("evaluation_status") == "scored"]
    model_direction_selected = [row for row in scored if row["model_direction_buy_no"]]
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
        "expected_10m_observations_in_window": int(
            round((end_hour_jst - start_hour_jst) * 6)
        ),
        "source_observation_coverage": len(observations)
        / int(round((end_hour_jst - start_hour_jst) * 6)),
        "max_book_after_first_seen_seconds": max_book_after_first_seen_seconds,
        "checkpoint_rows_preserved": len(output),
        "pit_current_book_rows": len(output) - missing_current_book,
        "scored_two_sided_rows": (
            len(output) - missing_current_book - one_sided_current_book
        ),
        "one_sided_current_book_rows": one_sided_current_book,
        "missing_current_book_rows": missing_current_book,
        "edge_threshold_after_fee": edge_threshold,
        "v2_trigger_count": len(triggered),
        "v2_trigger_wins": sum(row["no_label"] for row in triggered),
        "v2_yes_trigger_count": len(triggered_yes),
        "v2_yes_trigger_wins": sum(row["yes_label"] for row in triggered_yes),
        "no_floor_trigger_count": len(triggered_no_floor),
        "no_floor_trigger_wins": sum(row["no_label"] for row in triggered_no_floor),
        "v2_brier_on_scored_rows": model_brier,
        "market_brier_on_scored_rows": market_brier,
        "v2_edge_after_fee_min": min(row["edge_after_fee_v2"] for row in scored),
        "v2_edge_after_fee_max": max(row["edge_after_fee_v2"] for row in scored),
        "unconditional_buy_every_scored_checkpoint": {
            "shares_per_order": REPLAY_SHARES,
            "orders": len(scored),
            "wins": sum(int(row["no_label"]) for row in scored),
            "cost_1share_each": sum(
                row["hypothetical_no_entry_cost_1share"] for row in scored
            ),
            "fee_adjusted_pnl_1share_each": sum(
                row["hypothetical_no_pnl_1share_fee_adjusted"] for row in scored
            ),
            "cost_5shares_each": REPLAY_SHARES
            * sum(row["hypothetical_no_entry_cost_1share"] for row in scored),
            "fee_adjusted_pnl_5shares_each": sum(
                row["hypothetical_no_pnl_5shares_fee_adjusted"] for row in scored
            ),
        },
        "model_direction_p_no_gte_0_5": {
            "shares_per_order": REPLAY_SHARES,
            "orders": len(model_direction_selected),
            "wins": sum(int(row["no_label"]) for row in model_direction_selected),
            "cost_1share_each": sum(
                row["hypothetical_no_entry_cost_1share"]
                for row in model_direction_selected
            ),
            "fee_adjusted_pnl_1share_each": sum(
                row["model_direction_pnl_1share_fee_adjusted"]
                for row in model_direction_selected
            ),
            "cost_5shares_each": REPLAY_SHARES
            * sum(
                row["hypothetical_no_entry_cost_1share"]
                for row in model_direction_selected
            ),
            "fee_adjusted_pnl_5shares_each": sum(
                row["model_direction_pnl_5shares_fee_adjusted"]
                for row in model_direction_selected
            ),
        },
        "v2_edge_policy": {
            "shares_per_order": REPLAY_SHARES,
            "orders": len(triggered),
            "wins": sum(int(row["no_label"]) for row in triggered),
            "cost_1share_each": sum(
                row["hypothetical_no_entry_cost_1share"] for row in triggered
            ),
            "fee_adjusted_pnl_1share_each": sum(
                row["v2_policy_pnl_1share_fee_adjusted"] for row in scored
            ),
            "cost_5shares_each": REPLAY_SHARES
            * sum(row["hypothetical_no_entry_cost_1share"] for row in triggered),
            "fee_adjusted_pnl_5shares_each": sum(
                row["v2_policy_pnl_5shares_fee_adjusted"] for row in scored
            ),
        },
        "v2_yes_edge_policy_all_checkpoints": {
            "shares_per_order": REPLAY_SHARES,
            "orders": len(triggered_yes),
            "wins": sum(int(row["yes_label"]) for row in triggered_yes),
            "cost_5shares_each": REPLAY_SHARES
            * sum(row["hypothetical_yes_entry_cost_1share"] for row in triggered_yes),
            "fee_adjusted_pnl_5shares_each": sum(
                row["hypothetical_yes_pnl_5shares_fee_adjusted"]
                for row in triggered_yes
            ),
        },
        "final_official_bracket": final_bracket,
        "settlement_source": settlement_source,
        "settlement_ref": settlement_ref,
        "settlement_availability_clock_class": settlement_availability,
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
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _csv_value(value: str) -> Any:
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return value


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * probability))))
    return ordered[index]


def _probability_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise RuntimeError("no scored rows for aggregate metrics")
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_date.setdefault(str(row["target_date"]), []).append(row)

    def row_values(row: dict[str, Any]) -> tuple[float, float, int]:
        return float(row["model_p_no_v2"]), float(row["no_mid"]), int(row["no_label"])

    model_brier = sum((row_values(row)[0] - row_values(row)[2]) ** 2 for row in rows) / len(rows)
    market_brier = sum((row_values(row)[1] - row_values(row)[2]) ** 2 for row in rows) / len(rows)
    model_logloss = sum(binary_logloss(row_values(row)[0], row_values(row)[2]) for row in rows) / len(rows)
    market_logloss = sum(binary_logloss(row_values(row)[1], row_values(row)[2]) for row in rows) / len(rows)
    date_deltas = {
        target_date: sum(
            (row_values(row)[0] - row_values(row)[2]) ** 2
            - (row_values(row)[1] - row_values(row)[2]) ** 2
            for row in date_rows
        )
        / len(date_rows)
        for target_date, date_rows in by_date.items()
    }
    dates = sorted(date_deltas)
    rng = random.Random(20260810)
    bootstrap = [
        sum(date_deltas[rng.choice(dates)] for _ in dates) / len(dates)
        for _ in range(20000)
    ]
    by_outcome: dict[str, Any] = {}
    for label, name in ((0, "final_stays_current_yes"), (1, "final_overshoots_current_no")):
        label_rows = [row for row in rows if row_values(row)[2] == label]
        by_outcome[name] = {
            "rows": len(label_rows),
            "model_accuracy_at_0_5": (
                None
                if not label_rows
                else sum(
                    int((row_values(row)[0] >= 0.5) == bool(label))
                    for row in label_rows
                )
                / len(label_rows)
            ),
            "market_accuracy_at_0_5": (
                None
                if not label_rows
                else sum(
                    int((row_values(row)[1] >= 0.5) == bool(label))
                    for row in label_rows
                )
                / len(label_rows)
            ),
        }
    return {
        "rows": len(rows),
        "target_dates": dates,
        "model_brier": model_brier,
        "market_brier": market_brier,
        "model_minus_market_brier": model_brier - market_brier,
        "date_equal_model_minus_market_brier": sum(date_deltas.values()) / len(dates),
        "date_block_bootstrap_95ci_model_minus_market_brier": [
            _percentile(bootstrap, 0.025),
            _percentile(bootstrap, 0.975),
        ],
        "model_logloss": model_logloss,
        "market_logloss": market_logloss,
        "model_minus_market_logloss": model_logloss - market_logloss,
        "model_accuracy_at_0_5": sum(
            int((row_values(row)[0] >= 0.5) == bool(row_values(row)[2]))
            for row in rows
        )
        / len(rows),
        "market_accuracy_at_0_5": sum(
            int((row_values(row)[1] >= 0.5) == bool(row_values(row)[2]))
            for row in rows
        )
        / len(rows),
        "classification_by_realized_outcome": by_outcome,
        "by_date_brier_delta": date_deltas,
    }


def aggregate_replays(
    checkpoint_paths: list[Path], edge_threshold: float
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    day_summaries: list[dict[str, Any]] = []
    for path in checkpoint_paths:
        with path.open(newline="", encoding="utf-8") as handle:
            rows.extend(
                {key: _csv_value(value) for key, value in row.items()}
                for row in csv.DictReader(handle)
            )
        summary_path = path.parent / "summary.json"
        day_summaries.append(json.loads(summary_path.read_text(encoding="utf-8")))
    scored = [row for row in rows if row.get("evaluation_status") == "scored"]
    ordered = sorted(scored, key=lambda row: str(row["decision_jst"]))
    state_entries: list[dict[str, Any]] = []
    seen_state: set[tuple[str, int]] = set()
    for row in ordered:
        key = (str(row["target_date"]), int(row["current_bracket"]))
        if key not in seen_state:
            seen_state.add(key)
            state_entries.append(row)

    all_triggers = 0
    selected: list[dict[str, Any]] = []
    seen_trade: set[tuple[str, int, str]] = set()
    for row in ordered:
        for side in ("NO", "YES"):
            if side == "NO":
                probability = float(row["model_p_no_v2"])
                ask = float(row["no_ask"])
                payout = int(row["no_label"])
            else:
                probability = 1.0 - float(row["model_p_no_v2"])
                ask = float(row["yes_ask"])
                payout = int(row["yes_label"])
            fee = official_fee_per_share(ask)
            edge = probability - ask - fee
            if edge < edge_threshold:
                continue
            all_triggers += 1
            key = (str(row["target_date"]), int(row["current_bracket"]), side)
            if key in seen_trade:
                continue
            seen_trade.add(key)
            cost, pnl = fee_adjusted_binary_pnl(payout, ask)
            selected.append(
                {
                    "target_date": row["target_date"],
                    "decision_jst": row["decision_jst"],
                    "current_bracket": int(row["current_bracket"]),
                    "side": side,
                    "model_probability": probability,
                    "market_probability": (
                        float(row["no_mid"])
                        if side == "NO"
                        else 1.0 - float(row["no_mid"])
                    ),
                    "ask": ask,
                    "fee_per_share": fee,
                    "edge_after_fee": edge,
                    "payout": payout,
                    "shares": REPLAY_SHARES,
                    "cost_5shares": cost * REPLAY_SHARES,
                    "pnl_5shares_fee_adjusted": pnl * REPLAY_SHARES,
                }
            )
    cost = sum(float(row["cost_5shares"]) for row in selected)
    pnl = sum(float(row["pnl_5shares_fee_adjusted"]) for row in selected)
    dates = sorted({str(row["target_date"]) for row in rows})
    daily_trade = {
        target_date: {
            "cost": sum(
                float(row["cost_5shares"])
                for row in selected
                if row["target_date"] == target_date
            ),
            "pnl": sum(
                float(row["pnl_5shares_fee_adjusted"])
                for row in selected
                if row["target_date"] == target_date
            ),
        }
        for target_date in dates
    }
    rng = random.Random(20260810)
    roi_bootstrap: list[float] = []
    for _ in range(20000):
        sampled = [daily_trade[rng.choice(dates)] for _ in dates]
        sampled_cost = sum(row["cost"] for row in sampled)
        if sampled_cost > 0:
            roi_bootstrap.append(sum(row["pnl"] for row in sampled) / sampled_cost)
    by_side = {}
    for side in ("NO", "YES"):
        side_rows = [row for row in selected if row["side"] == side]
        side_cost = sum(float(row["cost_5shares"]) for row in side_rows)
        side_pnl = sum(float(row["pnl_5shares_fee_adjusted"]) for row in side_rows)
        by_side[side] = {
            "orders": len(side_rows),
            "wins": sum(int(row["payout"]) for row in side_rows),
            "cost_5shares": side_cost,
            "pnl_5shares_fee_adjusted": side_pnl,
            "roi": None if side_cost == 0 else side_pnl / side_cost,
        }
    return {
        "denominator_scope": "Tokyo frozen v2; 10:00-17:59 JST exact JMA first-seen checkpoints; same-row two-sided current-bracket book",
        "target_dates": sorted({str(row["target_date"]) for row in rows}),
        "signal_funnel": {
            "expected_10m_checkpoints": sum(
                int(row["expected_10m_observations_in_window"])
                for row in day_summaries
            ),
            "captured_source_checkpoints": sum(
                int(row["source_observations_in_window"]) for row in day_summaries
            ),
            "scored_two_sided_checkpoints": len(scored),
            "state_entry_rows": len(state_entries),
            "all_checkpoint_two_sided_triggers": all_triggers,
            "selected_first_per_date_bracket_side": len(selected),
        },
        "checkpoint_probability": _probability_metrics(scored),
        "state_entry_probability": _probability_metrics(state_entries),
        "market_uncertain_probability": {
            "market_mid_10_to_90pct": _probability_metrics(
                [row for row in scored if 0.1 <= float(row["no_mid"]) <= 0.9]
            ),
            "market_mid_20_to_80pct": _probability_metrics(
                [row for row in scored if 0.2 <= float(row["no_mid"]) <= 0.8]
            ),
        },
        "trade_policy": {
            "edge_threshold_after_fee": edge_threshold,
            "selection": "first qualifying checkpoint per target_date,current_bracket,side",
            "shares_per_order": REPLAY_SHARES,
            "orders": len(selected),
            "wins": sum(int(row["payout"]) for row in selected),
            "trade_hit_rate": (
                None
                if not selected
                else sum(int(row["payout"]) for row in selected) / len(selected)
            ),
            "model_direction_accuracy_on_selected": (
                None
                if not selected
                else sum(
                    int(
                        (float(row["model_probability"]) >= 0.5)
                        == bool(row["payout"])
                    )
                    for row in selected
                )
                / len(selected)
            ),
            "market_direction_accuracy_on_selected": (
                None
                if not selected
                else sum(
                    int(
                        (float(row["market_probability"]) >= 0.5)
                        == bool(row["payout"])
                    )
                    for row in selected
                )
                / len(selected)
            ),
            "cost_5shares": cost,
            "pnl_5shares_fee_adjusted": pnl,
            "roi": None if cost == 0 else pnl / cost,
            "target_date_block_bootstrap_95ci_roi": (
                None
                if not roi_bootstrap
                else [
                    _percentile(roi_bootstrap, 0.025),
                    _percentile(roi_bootstrap, 0.975),
                ]
            ),
            "by_target_date": daily_trade,
            "by_side": by_side,
        },
        "daily_coverage": day_summaries,
        "bootstrap_seed": 20260810,
        "bootstrap_replicates": 20000,
    }, selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date", default="2026-08-01")
    parser.add_argument("--start-hour-jst", type=float, default=10.0)
    parser.add_argument("--end-hour-jst", type=float, default=18.0)
    parser.add_argument("--edge-threshold", type=float, default=0.02)
    parser.add_argument(
        "--max-book-after-first-seen-seconds", type=float, default=900.0
    )
    parser.add_argument("--book-path", type=Path, default=DEFAULT_BOOK)
    parser.add_argument("--jma-path", type=Path, default=DEFAULT_JMA)
    parser.add_argument("--official-journal-dir", type=Path, default=DEFAULT_OFFICIAL)
    parser.add_argument("--artifact-path", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--settlement-dir", type=Path, default=DEFAULT_SETTLEMENT_DIR)
    parser.add_argument("--settlement-bracket", type=int)
    parser.add_argument("--settlement-evidence", default="")
    parser.add_argument("--aggregate-checkpoints", type=Path, nargs="+")
    parser.add_argument("--aggregate-out-dir", type=Path)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if args.aggregate_checkpoints:
        if args.aggregate_out_dir is None:
            raise SystemExit("--aggregate-out-dir is required with --aggregate-checkpoints")
        summary, selected = aggregate_replays(
            args.aggregate_checkpoints, args.edge_threshold
        )
        args.aggregate_out_dir.mkdir(parents=True, exist_ok=True)
        (args.aggregate_out_dir / "aggregate_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if selected:
            _write_csv(args.aggregate_out_dir / "selected_trades.csv", selected)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    rows, summary = replay(
        target_date=args.target_date,
        start_hour_jst=args.start_hour_jst,
        end_hour_jst=args.end_hour_jst,
        edge_threshold=args.edge_threshold,
        max_book_after_first_seen_seconds=args.max_book_after_first_seen_seconds,
        book_path=args.book_path,
        jma_path=args.jma_path,
        official_journal_dir=args.official_journal_dir,
        artifact_path=args.artifact_path,
        settlement_dir=args.settlement_dir,
        settlement_bracket=args.settlement_bracket,
        settlement_evidence=args.settlement_evidence,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(args.out_dir / "checkpoints.csv", rows)
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
