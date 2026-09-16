#!/usr/bin/env python3
"""Rebuild the Amsterdam KNMI frozen-forward shadow scorecard.

The evaluator keeps four denominators separate:

* WCIR final-settlement probability checkpoints;
* WCIR zero-notional first-entry intents;
* next-routine EHAM METAR predictions;
* KNMI first-seen full-ladder repricing and .7-cross opportunities.

It never submits orders. Displayed-book trades are counterfactual taker replays.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo


UTC = timezone.utc
AMSTERDAM_TZ = ZoneInfo("Europe/Amsterdam")
DEFAULT_RUNTIME_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime")
DEFAULT_DB = Path("runtime/weather.db")
DEFAULT_OUTPUT = Path(
    "docs/analysis/2026-08/generated/amsterdam_knmi_shadow_forward_scorecard_v2"
)
POLLUTION_END_UTC = "2026-08-10T08:27:54Z"
WEATHER_FEE_RATE = 0.05


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bootstrap-samples", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260811)
    return parser.parse_args()


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def arithmetic_round(value: float) -> int:
    return math.floor(value + 0.5)


def bracket_equal(left: Any, right: Any) -> bool:
    try:
        return int(float(left)) == int(float(right))
    except (TypeError, ValueError):
        return str(left) == str(right)


def taker_fee(shares: float, price: float) -> float:
    return round(shares * WEATHER_FEE_RATE * price * (1.0 - price), 5)


def quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(probability * (len(ordered) - 1))))
    return ordered[index]


def auc_score(probabilities: list[float], labels: list[int]) -> float | None:
    positives = [p for p, y in zip(probabilities, labels) if y == 1]
    negatives = [p for p, y in zip(probabilities, labels) if y == 0]
    if not positives or not negatives:
        return None
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            wins += float(positive > negative) + 0.5 * float(positive == negative)
    return wins / (len(positives) * len(negatives))


def probability_metrics(probabilities: list[float], labels: list[int]) -> dict[str, Any]:
    if not probabilities:
        return {"rows": 0}
    epsilon = 1e-12
    return {
        "rows": len(probabilities),
        "positive_rate": sum(labels) / len(labels),
        "mean_probability": sum(probabilities) / len(probabilities),
        "direction_accuracy_0_5": sum(
            (probability >= 0.5) == bool(label)
            for probability, label in zip(probabilities, labels)
        )
        / len(labels),
        "brier": sum(
            (probability - label) ** 2
            for probability, label in zip(probabilities, labels)
        )
        / len(labels),
        "logloss": -sum(
            label * math.log(max(probability, epsilon))
            + (1 - label) * math.log(max(1 - probability, epsilon))
            for probability, label in zip(probabilities, labels)
        )
        / len(labels),
        "auc": auc_score(probabilities, labels),
    }


def calibration_bins(
    probabilities: list[float], labels: list[int], *, width: float = 0.2
) -> list[dict[str, Any]]:
    bins: list[dict[str, Any]] = []
    count = int(round(1.0 / width))
    for index in range(count):
        lower = index * width
        upper = 1.0 if index == count - 1 else (index + 1) * width
        selected = [
            (p, y)
            for p, y in zip(probabilities, labels)
            if (lower <= p <= upper if index == count - 1 else lower <= p < upper)
        ]
        if not selected:
            continue
        bins.append(
            {
                "lower": lower,
                "upper": upper,
                "rows": len(selected),
                "mean_probability": statistics.mean(p for p, _ in selected),
                "positive_rate": statistics.mean(y for _, y in selected),
            }
        )
    return bins


def bootstrap_date_means(
    daily_values: dict[str, float], *, samples: int, seed: int
) -> dict[str, Any]:
    if not daily_values:
        return {"target_dates": 0, "mean": None, "ci95": [None, None]}
    dates = sorted(daily_values)
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(samples):
        sampled = [rng.choice(dates) for _ in dates]
        draws.append(statistics.mean(daily_values[target_date] for target_date in sampled))
    return {
        "target_dates": len(dates),
        "mean": statistics.mean(daily_values.values()),
        "ci95": [quantile(draws, 0.025), quantile(draws, 0.975)],
        "unit": "target_date_equal",
    }


def load_settlements(db_path: Path) -> dict[str, str]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=1000")
    rows = connection.execute(
        """
        SELECT target_date, bracket
        FROM settlement_outcomes
        WHERE city='Amsterdam'
          AND settlement_status='settled'
          AND final_price=1
        """
    ).fetchall()
    connection.close()
    return {str(target_date): str(bracket) for target_date, bracket in rows}


def dedupe_bundles(path: Path) -> list[dict[str, Any]]:
    """Keep the earliest decision for each physical observation and target."""
    selected: dict[tuple[Any, ...], dict[str, Any]] = {}
    for bundle in read_jsonl(path):
        output = bundle.get("model_output") or {}
        if output.get("city") != "Amsterdam":
            continue
        event = bundle.get("information_event") or {}
        observation_identity = (
            event.get("provider_item_id")
            or event.get("content_key")
            or output.get("trigger_event_id")
        )
        key = (
            output.get("model_id"),
            output.get("target_date"),
            observation_identity,
            output.get("target_id"),
        )
        existing = selected.get(key)
        if existing is None or output.get("decision_ts_utc", "") < (
            existing.get("model_output") or {}
        ).get("decision_ts_utc", ""):
            selected[key] = bundle
    return list(selected.values())


def clean_forward(bundle: dict[str, Any]) -> bool:
    output = bundle.get("model_output") or {}
    event = bundle.get("information_event") or {}
    if event.get("pit_lineage_class") != "collector_exact":
        return False
    return not (
        output.get("target_date") == "2026-08-10"
        and output.get("decision_ts_utc", "") < POLLUTION_END_UTC
    )


def score_probability_model(
    bundles: list[dict[str, Any]],
    settlements: dict[str, str],
    *,
    model_id: str,
    target_filter: Callable[[str], bool],
    bootstrap_samples: int,
    seed: int,
) -> dict[str, Any]:
    rows: list[tuple[str, float, float, int]] = []
    exclusions = Counter()
    for bundle in bundles:
        output = bundle.get("model_output") or {}
        if output.get("model_id") != model_id:
            continue
        if not target_filter(str(output.get("target_id") or "")):
            continue
        target_date = str(output.get("target_date") or "")
        if target_date not in settlements:
            exclusions["unsettled"] += 1
            continue
        if not clean_forward(bundle):
            event = bundle.get("information_event") or {}
            reason = (
                "wrong_local_day_anchor"
                if target_date == "2026-08-10"
                and output.get("decision_ts_utc", "") < POLLUTION_END_UTC
                else str(event.get("pit_lineage_class") or "unknown_lineage")
            )
            exclusions[reason] += 1
            continue
        candidate = bundle.get("signal_candidate") or {}
        if candidate.get("market_p") is None:
            exclusions["market_missing"] += 1
            continue
        label = int(
            not bracket_equal(settlements[target_date], candidate.get("bracket"))
        )
        rows.append(
            (
                target_date,
                float(output["p_model"]),
                float(candidate["market_p"]),
                label,
            )
        )
    model_probabilities = [row[1] for row in rows]
    market_probabilities = [row[2] for row in rows]
    labels = [row[3] for row in rows]
    daily_brier: dict[str, list[float]] = defaultdict(list)
    daily_logloss: dict[str, list[float]] = defaultdict(list)
    epsilon = 1e-12
    for target_date, model_probability, market_probability, label in rows:
        daily_brier[target_date].append(
            (model_probability - label) ** 2
            - (market_probability - label) ** 2
        )
        daily_logloss[target_date].append(
            -(
                label * math.log(max(model_probability, epsilon))
                + (1 - label) * math.log(max(1 - model_probability, epsilon))
            )
            + (
                label * math.log(max(market_probability, epsilon))
                + (1 - label) * math.log(max(1 - market_probability, epsilon))
            )
        )
    daily_brier_mean = {
        target_date: statistics.mean(values)
        for target_date, values in daily_brier.items()
    }
    daily_logloss_mean = {
        target_date: statistics.mean(values)
        for target_date, values in daily_logloss.items()
    }
    return {
        "model_id": model_id,
        "grain": "distinct KNMI initial observation × target",
        "target_dates": sorted(set(row[0] for row in rows)),
        "rows": len(rows),
        "exclusions": dict(exclusions),
        "model": probability_metrics(model_probabilities, labels),
        "market": probability_metrics(market_probabilities, labels),
        "model_calibration": calibration_bins(model_probabilities, labels),
        "market_calibration": calibration_bins(market_probabilities, labels),
        "model_minus_market": {
            "brier": bootstrap_date_means(
                daily_brier_mean, samples=bootstrap_samples, seed=seed
            ),
            "logloss": bootstrap_date_means(
                daily_logloss_mean, samples=bootstrap_samples, seed=seed + 1
            ),
            "daily": {
                target_date: {
                    "rows": len(daily_brier[target_date]),
                    "brier_delta": daily_brier_mean[target_date],
                    "logloss_delta": daily_logloss_mean[target_date],
                }
                for target_date in sorted(daily_brier)
            },
        },
    }


def score_intents(
    bundles: list[dict[str, Any]],
    intents_path: Path,
    settlements: dict[str, str],
    *,
    model_id: str,
) -> dict[str, Any]:
    candidate_map: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    for bundle in bundles:
        candidate = bundle.get("signal_candidate") or {}
        if candidate.get("candidate_id"):
            candidate_map[str(candidate["candidate_id"])] = (
                candidate,
                bundle.get("model_output") or {},
                bundle,
            )
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for intent in read_jsonl(intents_path):
        intent_id = str(intent.get("intent_id") or "")
        if not intent_id or intent_id in seen:
            continue
        seen.add(intent_id)
        match = candidate_map.get(str(intent.get("candidate_id") or ""))
        if match is None:
            continue
        candidate, output, bundle = match
        if output.get("model_id") != model_id:
            continue
        target_date = str(output.get("target_date") or "")
        metadata = candidate.get("metadata") or {}
        review = metadata.get("holding_review_observation") or {}
        best_ask = review.get("best_ask")
        if best_ask is None:
            association = (output.get("metadata") or {}).get("book_association") or {}
            best_ask = association.get("best_ask")
        if best_ask is None:
            continue
        shares = 5.0
        price = float(best_ask)
        fee = taker_fee(shares, price)
        cost = shares * price + fee
        record: dict[str, Any] = {
            "target_date": target_date,
            "decision_ts_utc": output.get("decision_ts_utc"),
            "bracket": candidate.get("bracket"),
            "side": str(output.get("target_id") or "").split(":")[-1].upper(),
            "p_model": output.get("p_model"),
            "market_p": candidate.get("market_p"),
            "best_ask": price,
            "shares": shares,
            "fee": fee,
            "cost": cost,
            "clean_forward": clean_forward(bundle),
            "execution_mode": intent.get("mode"),
            "actual_fill": False,
        }
        if target_date in settlements:
            no_won = not bracket_equal(
                settlements[target_date], candidate.get("bracket")
            )
            won = no_won if record["side"] == "NO" else not no_won
            record.update(
                {
                    "settlement_winner": settlements[target_date],
                    "won": won,
                    "pnl": (shares if won else 0.0) - cost,
                }
            )
        records.append(record)

    def summarize(selected: list[dict[str, Any]]) -> dict[str, Any]:
        settled = [record for record in selected if record.get("won") is not None]
        total_cost = sum(record["cost"] for record in settled)
        pnl = sum(record["pnl"] for record in settled)
        return {
            "intents": len(selected),
            "settled": len(settled),
            "open": len(selected) - len(settled),
            "target_dates": len(set(record["target_date"] for record in settled)),
            "wins": sum(bool(record["won"]) for record in settled),
            "cost": total_cost,
            "pnl": pnl,
            "roi": pnl / total_cost if total_cost else None,
            "actual_fills": 0,
        }

    return {
        "model_id": model_id,
        "policy": "first zero-notional intent under deployed dedupe policy; 5-share taker replay",
        "all": summarize(records),
        "clean_forward": summarize(
            [record for record in records if record["clean_forward"]]
        ),
        "records": records,
    }


def score_next_routine(path: Path) -> dict[str, Any]:
    outcomes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in read_jsonl(path):
        outcome_id = str(row.get("outcome_id") or "")
        if not outcome_id or outcome_id in seen:
            continue
        seen.add(outcome_id)
        outcomes.append(row)
    probabilities = [float(row["p_next_routine_confirms"]) for row in outcomes]
    labels = [int(row["label_next_routine_confirms"]) for row in outcomes]
    by_date: dict[str, Any] = {}
    for target_date in sorted(set(str(row["target_date"]) for row in outcomes)):
        selected = [row for row in outcomes if str(row["target_date"]) == target_date]
        metrics = probability_metrics(
            [float(row["p_next_routine_confirms"]) for row in selected],
            [int(row["label_next_routine_confirms"]) for row in selected],
        )
        metrics["always_no_accuracy"] = 1.0 - metrics["positive_rate"]
        metrics["always_no_brier"] = metrics["positive_rate"]
        by_date[target_date] = metrics
    by_minute: dict[str, Any] = {}
    for minute in sorted(
        set(parse_ts(str(row["source_obs_ts_utc"])).minute for row in outcomes)
    ):
        selected = [
            row
            for row in outcomes
            if parse_ts(str(row["source_obs_ts_utc"])).minute == minute
        ]
        metrics = probability_metrics(
            [float(row["p_next_routine_confirms"]) for row in selected],
            [int(row["label_next_routine_confirms"]) for row in selected],
        )
        lead_minutes = [
            (
                parse_ts(str(row["next_routine_first_seen_at_utc"]))
                - parse_ts(str(row["source_first_seen_at_utc"]))
            ).total_seconds()
            / 60.0
            for row in selected
        ]
        metrics.update(
            {
                "first_seen_to_next_routine_first_seen_minutes_median": statistics.median(
                    lead_minutes
                ),
                "first_seen_to_next_routine_first_seen_minutes_p10": quantile(
                    lead_minutes, 0.10
                ),
            }
        )
        by_minute[f":{minute:02d}"] = metrics
    confusion = Counter(
        (
            "predicted_yes" if probability >= 0.5 else "predicted_no",
            "actual_yes" if label else "actual_no",
        )
        for probability, label in zip(probabilities, labels)
    )
    overall = probability_metrics(probabilities, labels)
    overall["always_no_accuracy"] = 1.0 - overall["positive_rate"]
    overall["always_no_brier"] = overall["positive_rate"]
    return {
        "model_id": "amsterdam_knmi_next_routine_v5",
        "grain": "distinct KNMI initial observation → next routine EHAM METAR",
        "target_dates": sorted(set(str(row["target_date"]) for row in outcomes)),
        "overall": overall,
        "confusion": {"|".join(key): value for key, value in sorted(confusion.items())},
        "by_target_date": by_date,
        "by_source_observation_minute": by_minute,
        "trade_signals": 0,
        "fee_adjusted_roi": None,
    }


def yes_probability_interval_midpoint(record: dict[str, Any]) -> float | None:
    lower: list[float] = []
    upper: list[float] = []
    if record.get("yes_best_bid") is not None:
        lower.append(float(record["yes_best_bid"]))
    if record.get("no_best_ask") is not None:
        lower.append(1.0 - float(record["no_best_ask"]))
    if record.get("yes_best_ask") is not None:
        upper.append(float(record["yes_best_ask"]))
    if record.get("no_best_bid") is not None:
        upper.append(1.0 - float(record["no_best_bid"]))
    probability_lower = max(lower) if lower else 0.0
    probability_upper = min(upper) if upper else 1.0
    if probability_lower > probability_upper + 1e-6:
        return None
    return (probability_lower + probability_upper) / 2.0


class LadderPanel:
    def __init__(self, runtime_root: Path):
        self.root = runtime_root / "output" / "knmi_first_seen_ladder_v1"
        self.event_role: dict[str, str] = {}
        for row in read_jsonl(self.root / "events.jsonl"):
            if row.get("information_event_id"):
                self.event_role[str(row["information_event_id"])] = str(
                    row.get("event_role") or ""
                )
        self.captures: dict[tuple[str, int], dict[str, Any]] = {}
        for row in read_jsonl(self.root / "captures.jsonl"):
            event_id = row.get("source_event_id")
            offset = row.get("scheduled_offset_seconds")
            if event_id is None or offset is None:
                continue
            key = (str(event_id), int(offset))
            existing = self.captures.get(key)
            if existing is None or row.get("capture_status") == "complete":
                self.captures[key] = row
        self.source: dict[str, dict[str, Any]] = {}
        source_root = runtime_root / "output" / "knmi_open_data"
        for path in sorted(source_root.glob("20??-??-??/knmi_observations.jsonl")):
            for row in read_jsonl(path):
                if row.get("information_event_id"):
                    self.source[str(row["information_event_id"])] = row
        self._snapshot_cache: dict[str, dict[str, dict[str, Any]]] = {}

    def snapshot(self, event_id: str, offset: int) -> dict[str, dict[str, Any]]:
        path = str(self.captures[(event_id, offset)]["snapshot_path"])
        if path not in self._snapshot_cache:
            snapshot_path = Path(path)
            if not snapshot_path.exists():
                self._snapshot_cache[path] = {}
                return self._snapshot_cache[path]
            try:
                payload = json.loads(snapshot_path.read_text())
            except (OSError, json.JSONDecodeError):
                self._snapshot_cache[path] = {}
                return self._snapshot_cache[path]
            self._snapshot_cache[path] = {
                str(record.get("bracket")): record
                for record in payload.get("records") or []
                if record.get("bracket") is not None
            }
        return self._snapshot_cache[path]

    def capture_summary(self) -> dict[str, Any]:
        rows = list(self.captures.values())
        complete = [row for row in rows if row.get("capture_status") == "complete"]
        by_offset: dict[str, Any] = {}
        for offset in sorted(set(int(row["scheduled_offset_seconds"]) for row in rows)):
            selected = [
                row
                for row in rows
                if int(row["scheduled_offset_seconds"]) == offset
                and row.get("actual_start_offset_seconds") is not None
            ]
            actual = [float(row["actual_start_offset_seconds"]) for row in selected]
            by_offset[str(offset)] = {
                "captures": len(selected),
                "actual_start_seconds_median": statistics.median(actual),
                "actual_start_seconds_p95": quantile(actual, 0.95),
            }
        return {
            "unique_events": len(set(event_id for event_id, _ in self.captures)),
            "event_offsets": len(rows),
            "complete_event_offsets": len(complete),
            "complete_rate": len(complete) / len(rows) if rows else None,
            "new_content_events": sum(
                role == "new_content" for role in self.event_role.values()
            ),
            "revision_events": sum(role == "revision" for role in self.event_role.values()),
            "by_offset": by_offset,
        }


def score_ladder_microstructure(panel: LadderPanel) -> dict[str, Any]:
    offsets = (0, 15, 30, 60)
    event_metrics: list[dict[str, Any]] = []
    for event_id, role in panel.event_role.items():
        if role != "new_content" or event_id not in panel.source:
            continue
        if not all(
            (event_id, offset) in panel.captures
            and panel.captures[(event_id, offset)].get("capture_status") == "complete"
            for offset in offsets
        ):
            continue
        snapshots = {offset: panel.snapshot(event_id, offset) for offset in offsets}
        common = set.intersection(*(set(snapshot) for snapshot in snapshots.values()))
        if not common:
            continue
        source = panel.source[event_id]
        observation_time = parse_ts(str(source["observation_time_utc"]))
        row: dict[str, Any] = {
            "event_id": event_id,
            "target_date": source.get("target_date"),
            "local_hour": observation_time.astimezone(AMSTERDAM_TZ).hour,
            "source_minute": observation_time.minute,
        }
        for offset in offsets[1:]:
            deltas = []
            for bracket in common:
                left = yes_probability_interval_midpoint(snapshots[0][bracket])
                right = yes_probability_interval_midpoint(snapshots[offset][bracket])
                if left is not None and right is not None:
                    deltas.append(abs(right - left))
            if deltas:
                row[f"max_abs_delta_{offset}s"] = max(deltas)
                row[f"total_variation_{offset}s"] = 0.5 * sum(deltas)
        event_metrics.append(row)

    def summarize(selected: list[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "events": len(selected),
            "target_dates": len(set(str(row["target_date"]) for row in selected)),
        }
        for offset in offsets[1:]:
            values = [
                float(row[f"max_abs_delta_{offset}s"])
                for row in selected
                if row.get(f"max_abs_delta_{offset}s") is not None
            ]
            total_variation = [
                float(row[f"total_variation_{offset}s"])
                for row in selected
                if row.get(f"total_variation_{offset}s") is not None
            ]
            result[f"t0_to_{offset}s"] = {
                "rows": len(values),
                "max_abs_delta_median": statistics.median(values) if values else None,
                "max_abs_delta_p90": quantile(values, 0.90),
                "max_abs_delta_p95": quantile(values, 0.95),
                "fraction_any_rung_ge_1c": (
                    sum(value >= 0.01 - 1e-9 for value in values) / len(values)
                    if values
                    else None
                ),
                "fraction_any_rung_ge_2c": (
                    sum(value >= 0.02 - 1e-9 for value in values) / len(values)
                    if values
                    else None
                ),
                "total_variation_median": (
                    statistics.median(total_variation) if total_variation else None
                ),
            }
        return result

    return {
        "grain": "new-content KNMI event × complete full-ladder t0/+15/+30/+60",
        "all": summarize(event_metrics),
        "local_06_18": summarize(
            [row for row in event_metrics if 6 <= int(row["local_hour"]) < 18]
        ),
        "local_12_15": summarize(
            [row for row in event_metrics if 12 <= int(row["local_hour"]) < 15]
        ),
    }


def score_cross07(
    panel: LadderPanel, settlements: dict[str, str]
) -> dict[str, Any]:
    offsets = (0, 15, 30, 60, 120, 300)
    candidates: list[dict[str, Any]] = []
    for event_id, source in panel.source.items():
        if panel.event_role.get(event_id) != "new_content":
            continue
        if not all((event_id, offset) in panel.captures for offset in offsets):
            continue
        snapshots = {offset: panel.snapshot(event_id, offset) for offset in offsets}
        if not snapshots[0]:
            continue
        representative = next(iter(snapshots[0].values()))
        metar_max_f = representative.get("metar_current_max_f")
        source_temp = source.get("temp_c")
        if metar_max_f is None or source_temp is None:
            continue
        previous_bracket = arithmetic_round((float(metar_max_f) - 32.0) * 5.0 / 9.0)
        if float(source_temp) < previous_bracket + 0.7 - 1e-9:
            continue
        bracket = str(previous_bracket)
        if bracket not in snapshots[0]:
            continue
        entry = snapshots[0][bracket]
        candidate: dict[str, Any] = {
            "event_id": event_id,
            "target_date": str(source.get("target_date")),
            "source_observation_ts_utc": source.get("observation_time_utc"),
            "source_first_seen_at_utc": source.get("first_seen_at_utc"),
            "source_temp_c": float(source_temp),
            "previous_bracket": previous_bracket,
            "margin_c": float(source_temp) - previous_bracket,
            "entry_no_best_ask": entry.get("no_best_ask"),
            "entry_no_ask_size": entry.get("no_ask_size"),
            "t0_actual_start_offset_seconds": panel.captures[(event_id, 0)].get(
                "actual_start_offset_seconds"
            ),
        }
        for offset in offsets[1:]:
            future = snapshots[offset].get(bracket) or {}
            candidate[f"no_best_bid_{offset}s"] = future.get("no_best_bid")
            candidate[f"no_bid_size_{offset}s"] = future.get("no_bid_size")
        candidates.append(candidate)

    first: dict[tuple[str, int], dict[str, Any]] = {}
    for candidate in sorted(candidates, key=lambda row: str(row["source_first_seen_at_utc"])):
        first.setdefault(
            (str(candidate["target_date"]), int(candidate["previous_bracket"])), candidate
        )
    signals = list(first.values())
    executable = [
        row
        for row in signals
        if row.get("entry_no_best_ask") is not None
        and float(row["entry_no_best_ask"]) <= 0.97
        and float(row.get("entry_no_ask_size") or 0.0) >= 5.0
    ]
    for row in executable:
        shares = min(10.0, float(row["entry_no_ask_size"]))
        price = float(row["entry_no_best_ask"])
        cost = shares * price + taker_fee(shares, price)
        row.update(
            {
                "shares": shares,
                "entry_fee": taker_fee(shares, price),
                "entry_cost": cost,
                "selected_side_is_market_favorite": price > 0.5,
            }
        )
        target_date = str(row["target_date"])
        if target_date in settlements:
            won = not bracket_equal(
                settlements[target_date], row["previous_bracket"]
            )
            row.update(
                {
                    "settlement_winner": settlements[target_date],
                    "won": won,
                    "hold_pnl": (shares if won else 0.0) - cost,
                }
            )
        for offset in offsets[1:]:
            bid = row.get(f"no_best_bid_{offset}s")
            bid_size = row.get(f"no_bid_size_{offset}s")
            if bid is None or not bid_size:
                continue
            exit_shares = min(shares, float(bid_size))
            entry_cost = exit_shares * price + taker_fee(exit_shares, price)
            proceeds = exit_shares * float(bid) - taker_fee(exit_shares, float(bid))
            row[f"roundtrip_{offset}s"] = {
                "shares": exit_shares,
                "future_bid": float(bid),
                "entry_cost": entry_cost,
                "pnl": proceeds - entry_cost,
                "roi": (proceeds - entry_cost) / entry_cost,
            }

    settled = [row for row in executable if row.get("won") is not None]
    total_cost = sum(float(row["entry_cost"]) for row in settled)
    total_pnl = sum(float(row["hold_pnl"]) for row in settled)
    roundtrip: dict[str, Any] = {}
    for offset in offsets[1:]:
        selected = [
            row[f"roundtrip_{offset}s"]
            for row in executable
            if row.get(f"roundtrip_{offset}s")
        ]
        roundtrip[f"{offset}s"] = {
            "rows": len(selected),
            "positive": sum(float(row["pnl"]) > 0 for row in selected),
            "pnl": sum(float(row["pnl"]) for row in selected),
            "roi": (
                sum(float(row["pnl"]) for row in selected)
                / sum(float(row["entry_cost"]) for row in selected)
                if selected
                else None
            ),
        }
    return {
        "policy": "first .7C cross per target_date × previous bracket; t0 ask<=.97; min(10, ask depth), at least 5 shares",
        "signals": len(signals),
        "signal_target_dates": len(set(row["target_date"] for row in signals)),
        "settled_signal_wins": sum(
            str(settlements.get(str(row["target_date"])))
            != str(row["previous_bracket"])
            for row in signals
            if str(row["target_date"]) in settlements
        ),
        "settled_signals": sum(
            str(row["target_date"]) in settlements for row in signals
        ),
        "executable": len(executable),
        "executable_target_dates": len(set(row["target_date"] for row in executable)),
        "settled_executable": len(settled),
        "wins": sum(bool(row["won"]) for row in settled),
        "open": len(executable) - len(settled),
        "hold_cost": total_cost,
        "hold_pnl": total_pnl,
        "hold_roi": total_pnl / total_cost if total_cost else None,
        "selected_side_market_favorite": sum(
            bool(row["selected_side_is_market_favorite"]) for row in executable
        ),
        "roundtrip_taker_exit": roundtrip,
        "records": executable,
        "actual_fills": 0,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("")
        return
    fields = sorted(set().union(*(row.keys() for row in rows)))
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                    for key, value in row.items()
                }
            )


def main() -> None:
    args = parse_args()
    runtime_root = args.runtime_root.resolve()
    city_runtime = runtime_root / "output" / "city_probability_runtime_v3"
    bundles = dedupe_bundles(city_runtime / "decision_bundles.jsonl")
    settlements = load_settlements(args.db.resolve())
    weather_model = score_probability_model(
        bundles,
        settlements,
        model_id="amsterdam_knmi_remaining_heat_freeze_v7",
        target_filter=lambda target: target.startswith("legacy_exact_bracket_"),
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    market_prior = score_probability_model(
        bundles,
        settlements,
        model_id="amsterdam_market_prior_a_v1",
        target_filter=lambda target: target == "leave_current_exact_bracket:no",
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed + 10,
    )
    weather_intents = score_intents(
        bundles,
        city_runtime / "trade_intents.jsonl",
        settlements,
        model_id="amsterdam_knmi_remaining_heat_freeze_v7",
    )
    market_prior_intents = score_intents(
        bundles,
        city_runtime / "trade_intents.jsonl",
        settlements,
        model_id="amsterdam_market_prior_a_v1",
    )
    next_routine = score_next_routine(city_runtime / "next_metar_outcomes.jsonl")
    panel = LadderPanel(runtime_root)
    capture = panel.capture_summary()
    microstructure = score_ladder_microstructure(panel)
    cross07 = score_cross07(panel, settlements)

    source_files = [
        city_runtime / "decision_bundles.jsonl",
        city_runtime / "trade_intents.jsonl",
        city_runtime / "next_metar_outcomes.jsonl",
        panel.root / "events.jsonl",
        panel.root / "captures.jsonl",
        args.db.resolve(),
    ]
    snapshot_time = max(
        datetime.fromtimestamp(path.stat().st_mtime, UTC) for path in source_files
    )
    summary = {
        "schema_version": "amsterdam_knmi_shadow_forward_scorecard_v2",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "data_snapshot_mtime_utc": snapshot_time.isoformat(),
        "denominator_scope": {
            "city": "Amsterdam",
            "probability": "distinct KNMI initial observation × target; collector_exact; settled; same-row market; excludes known 2026-08-10 wrong-anchor interval",
            "intent": "deployed zero-notional first-entry intent; 5-share displayed-ask taker replay; no fill assumption",
            "cross07": "new-content first-seen .7C cross; first target_date×previous bracket; t0 displayed book",
            "microstructure": "new-content first-seen event with complete t0/+15/+30/+60 full ladder",
        },
        "known_exclusion": {
            "wrong_anchor_start": "2026-08-10T00:00:00Z",
            "wrong_anchor_end": POLLUTION_END_UTC,
            "reason": "previous-local-day EHAM padding record entered August 10 running max",
        },
        "probability": {
            "weather_v7": weather_model,
            "market_prior_a": market_prior,
        },
        "selected_intents": {
            "weather_v7": weather_intents,
            "market_prior_a": market_prior_intents,
        },
        "next_routine": next_routine,
        "first_seen_ladder_capture": capture,
        "first_seen_ladder_microstructure": microstructure,
        "cross07": cross07,
        "execution": {
            "mode": "zero_notional_counterfactual",
            "orders_submitted": 0,
            "actual_fills": 0,
            "live_crossno_excluded": True,
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    write_csv(
        args.output_dir / "selected_intents.csv",
        weather_intents["records"] + market_prior_intents["records"],
    )
    write_csv(args.output_dir / "cross07_executable.csv", cross07["records"])
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
