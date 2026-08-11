#!/usr/bin/env python3
"""Materialize strict-PIT Tokyo current-bracket NO rows for market-prior research.

The adapter consumes the deployed WCIR decision journal, but it deliberately
uses only the weather-only probability carried in model metadata.  The
incumbent market-residual output is not a baseline or a feature.  Entry cost is
reconstructed from the exact active-ladder book referenced by the decision
clock and requires five shares of displayed ask depth.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import joblib
import numpy as np

from src.strategies.weather_city_probability_shadow.tokyo import (
    _weather_features,
    _weather_stay_probability,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/output"
)
DEFAULT_BUNDLES = (
    DEFAULT_RUNTIME_ROOT / "city_probability_runtime_v3/decision_bundles.jsonl"
)
DEFAULT_SOURCE_JOURNAL = DEFAULT_RUNTIME_ROOT / "live_cross_observations"
DEFAULT_OBSERVATIONS = DEFAULT_RUNTIME_ROOT / "observations"
DEFAULT_BOOKS = (
    DEFAULT_RUNTIME_ROOT
    / "tokyo_current_break_active_ladder_shadow/active_bracket_books"
)
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_WEATHER_ARTIFACT = (
    ROOT
    / "docs/analysis/2026-07/generated/tokyo_current_break_binary_v5/models"
    / "binary_multigrain_hgb_v5.joblib"
)
DEFAULT_WEATHER_SPEC = DEFAULT_WEATHER_ARTIFACT.with_suffix(".spec.json")
DEFAULT_FEATURE_ROWS = (
    ROOT
    / "docs/analysis/2026-07/generated/tokyo_continuous_ladder_probability_v1"
    / "continuous_feature_rows.csv.gz"
)
UTC = timezone.utc
TOKYO = ZoneInfo("Asia/Tokyo")
MODEL_ID = "tokyo_state_entry_routed_market_residual_v7"
SHARES = 5.0
FEE_RATE = 0.05


def parse_ts(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            yield value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def weather_fee_per_share(price: float) -> float:
    return round(FEE_RATE * price * (1.0 - price), 5)


def five_share_cost(book: dict[str, Any]) -> tuple[float | None, float | None]:
    asks = (book.get("summary") or {}).get("asks") or []
    remaining = SHARES
    cash = 0.0
    for level in sorted(asks, key=lambda row: float(row.get("price") or 0.0)):
        price = finite(level.get("price"))
        size = finite(level.get("size"))
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        quantity = min(remaining, size)
        cash += quantity * (price + weather_fee_per_share(price))
        remaining -= quantity
        if remaining <= 1e-9:
            return cash, cash / SHARES
    return None, None


def load_settlements(
    path: Path,
) -> tuple[dict[str, int], dict[tuple[str, str, str], int]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    try:
        rows = connection.execute(
            """
            SELECT city, target_date, bracket, condition_id, final_price
            FROM settlement_outcomes
            WHERE settlement_status = 'settled'
              AND (final_price >= 0.99 OR final_price <= 0.01)
            """
        ).fetchall()
    finally:
        connection.close()
    by_condition: dict[str, int] = {}
    by_source_key: dict[tuple[str, str, str], int] = {}
    for city, target_date, bracket, condition_id, final_price in rows:
        label = int(float(final_price) <= 0.01)
        source_key = (str(city), str(target_date), str(bracket))
        previous = by_source_key.setdefault(source_key, label)
        if previous != label:
            raise RuntimeError(f"conflicting settlement labels for {source_key}")
        if condition_id:
            condition_key = str(condition_id)
            previous = by_condition.setdefault(condition_key, label)
            if previous != label:
                raise RuntimeError(
                    f"conflicting settlement labels for {condition_key}"
                )
    return by_condition, by_source_key


def candidate_rows(
    path: Path,
    *,
    start_date: str,
    end_date: str,
    maximum_event_to_book_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    counts = {
        "journal_rows": 0,
        "tokyo_v7_no_rows": 0,
        "date_window_rows": 0,
        "collector_exact_rows": 0,
        "weather_probability_rows": 0,
        "two_sided_market_rows": 0,
        "causal_event_book_rows": 0,
        "event_book_lag_rows": 0,
    }
    for raw in iter_jsonl(path):
        counts["journal_rows"] += 1
        event = raw.get("information_event") or {}
        model = raw.get("model_output") or {}
        candidate = raw.get("signal_candidate") or {}
        checkpoint = raw.get("state_checkpoint") or {}
        if (
            checkpoint.get("city") != "Tokyo"
            or model.get("model_id") != MODEL_ID
            or candidate.get("side") != "NO"
        ):
            continue
        counts["tokyo_v7_no_rows"] += 1
        target_date = str(checkpoint.get("target_date") or model.get("target_date") or "")
        if not start_date <= target_date <= end_date:
            continue
        counts["date_window_rows"] += 1
        if event.get("pit_lineage_class") != "collector_exact":
            continue
        counts["collector_exact_rows"] += 1
        weather_stay = finite((model.get("metadata") or {}).get("weather_probability_stay"))
        if weather_stay is None or not 0 <= weather_stay <= 1:
            continue
        counts["weather_probability_rows"] += 1
        association = (model.get("metadata") or {}).get("book_association") or {}
        bid = finite(association.get("best_bid"))
        ask = finite(association.get("best_ask"))
        market_no = finite(candidate.get("market_p"))
        if (
            association.get("probability_status") != "two_sided_midpoint"
            or bid is None
            or ask is None
            or market_no is None
            or not 0 <= bid <= market_no <= ask <= 1
        ):
            continue
        counts["two_sided_market_rows"] += 1
        first_seen = parse_ts(event.get("first_seen_at_utc"))
        decision = parse_ts(model.get("decision_ts_utc"))
        if decision < first_seen:
            continue
        counts["causal_event_book_rows"] += 1
        lag_seconds = (decision - first_seen).total_seconds()
        if lag_seconds > maximum_event_to_book_seconds:
            continue
        counts["event_book_lag_rows"] += 1
        event_id = str(event.get("information_event_id") or "")
        bracket = str(candidate.get("bracket") or "")
        condition_id = str(candidate.get("condition_id") or "")
        if not event_id or not bracket or not condition_id:
            continue
        key = (event_id, bracket)
        record = {
            "city": "Tokyo",
            "target_date": target_date,
            "event_id": event_id,
            "event_source": "jma_amedas",
            "event_decision_ts_utc": first_seen.isoformat(),
            "quote_ts_utc": decision.isoformat(),
            "event_age_min": lag_seconds / 60.0,
            "bracket": bracket,
            "condition_id": condition_id,
            "relative_rung": 0,
            "model_no_probability": 1.0 - weather_stay,
            "market_no_probability": market_no,
            "no_best_bid": bid,
            "no_best_ask": ask,
            "decision_ts_raw": str(model.get("decision_ts_utc")),
            "source_obs_ts_utc": event.get("source_event_ts_utc"),
            "source_first_seen_at_utc": event.get("first_seen_at_utc"),
            "book_snapshot_id": association.get("book_snapshot_id"),
            "weather_probability_stay": weather_stay,
            "weather_model_id": "binary_multigrain_hgb_v5",
        }
        previous = rows.get(key)
        if previous is None or decision < parse_ts(previous["quote_ts_utc"]):
            rows[key] = record
    return list(rows.values()), counts


def _source_paths_for_target_date(root: Path, target_date: str) -> list[Path]:
    if root.is_file():
        return [root]
    local_date = datetime.fromisoformat(target_date).date()
    # Tokyo's local business day spans two UTC physical shards.  The shard
    # path is storage time, never the target-date authority.
    return [
        root / day.isoformat() / "high_frequency_observations.jsonl"
        for day in (local_date - timedelta(days=1), local_date)
    ]


def _exact_source_events(
    root: Path, *, start_date: str, end_date: str
) -> tuple[
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
    dict[str, dict[str, int]],
]:
    events: dict[str, list[dict[str, Any]]] = {}
    histories: dict[str, list[dict[str, Any]]] = {}
    coverage: dict[str, dict[str, int]] = {}
    current = datetime.fromisoformat(start_date).date()
    end = datetime.fromisoformat(end_date).date()
    while current <= end:
        target_date = current.isoformat()
        day_counts = {
            "raw_source_rows": 0,
            "collector_exact_unique_observations": 0,
            "explicit_collector_exact_observations": 0,
            "legacy_hash_verified_exact_observations": 0,
            "model_window_observations": 0,
        }
        earliest: dict[str, dict[str, Any]] = {}
        for path in _source_paths_for_target_date(root, target_date):
            if not path.exists():
                continue
            for row in iter_jsonl(path):
                if (
                    row.get("city") != "Tokyo"
                    or row.get("source") != "jma_amedas"
                    or row.get("source_status") != "ok"
                    or row.get("target_date") != target_date
                ):
                    continue
                day_counts["raw_source_rows"] += 1
                if (
                    row.get("original_first_seen_unknown") is True
                    or not row.get("observation_time_utc")
                    or not row.get("source_first_seen_at_utc")
                ):
                    continue
                explicit_exact = row.get("pit_lineage_class") == "collector_exact"
                legacy_exact = (
                    row.get("schema_version") == "weather_high_frequency_observation_v1"
                    and row.get("payload_hash")
                    and row.get("raw_payload_hash")
                    and row.get("fetched_at_utc")
                    and row.get("local_detect_ts_utc")
                    and parse_ts(row["source_first_seen_at_utc"])
                    == parse_ts(row["fetched_at_utc"])
                    == parse_ts(row["local_detect_ts_utc"])
                )
                if not explicit_exact and not legacy_exact:
                    continue
                row = {
                    **row,
                    "_exact_clock_class": (
                        "collector_exact"
                        if explicit_exact
                        else "collector_exact_legacy_hash_verified"
                    ),
                }
                observation = parse_ts(row["observation_time_utc"])
                first_seen = parse_ts(row["source_first_seen_at_utc"])
                key = observation.isoformat()
                previous = earliest.get(key)
                if previous is None or first_seen < parse_ts(
                    previous["source_first_seen_at_utc"]
                ):
                    earliest[key] = row
        day_counts["collector_exact_unique_observations"] = len(earliest)
        day_counts["explicit_collector_exact_observations"] = sum(
            row["_exact_clock_class"] == "collector_exact"
            for row in earliest.values()
        )
        day_counts["legacy_hash_verified_exact_observations"] = sum(
            row["_exact_clock_class"] == "collector_exact_legacy_hash_verified"
            for row in earliest.values()
        )
        all_events = sorted(
            earliest.values(), key=lambda row: parse_ts(row["observation_time_utc"])
        )
        in_window = []
        for row in all_events:
            local = parse_ts(row["observation_time_utc"]).astimezone(TOKYO)
            local_hour = local.hour + local.minute / 60.0
            if 6.0 <= local_hour < 18.0:
                in_window.append(row)
        in_window.sort(key=lambda row: parse_ts(row["observation_time_utc"]))
        day_counts["model_window_observations"] = len(in_window)
        events[target_date] = in_window
        histories[target_date] = all_events
        coverage[target_date] = day_counts
        current += timedelta(days=1)
    return events, histories, coverage


def _load_current_bracket_books(
    root: Path, target_dates: Iterable[str]
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict[str, int]]:
    books: dict[tuple[str, str], list[dict[str, Any]]] = {}
    raw_counts: dict[str, int] = {}
    for target_date in sorted(set(target_dates)):
        path = root / f"{target_date}.jsonl"
        raw_counts[target_date] = 0
        if not path.exists():
            continue
        for row in iter_jsonl(path):
            if (
                row.get("city") != "Tokyo"
                or row.get("source") != "jma_amedas"
                or row.get("outcome") != "no"
                or row.get("book_status") != "ok"
                or not row.get("source_obs_ts_utc")
                or not row.get("book_fetched_at_utc")
            ):
                continue
            raw_counts[target_date] += 1
            # `reference_market_value` is the active official bracket anchor.
            # The older `metar_running_max_market_value` field was null on
            # 2026-08-11 and was one rung below the expression on part of
            # 2026-08-01, so it is not a stable cross-version join key.
            anchor = row.get("reference_market_value")
            bracket = str(row.get("bracket") or "")
            if anchor is None or bracket != str(int(float(anchor))):
                continue
            key = (target_date, parse_ts(row["source_obs_ts_utc"]).isoformat())
            books.setdefault(key, []).append(row)
    for rows in books.values():
        rows.sort(key=lambda row: parse_ts(row["book_fetched_at_utc"]))
    return books, raw_counts


def _official_snapshots_by_target_date(
    root: Path, *, start_date: str, end_date: str
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    start = datetime.fromisoformat(start_date).date() - timedelta(days=1)
    end = datetime.fromisoformat(end_date).date() + timedelta(days=1)
    current = start
    while current <= end:
        path = root / current.isoformat() / "observations.jsonl"
        if path.exists():
            for row in iter_jsonl(path):
                target_date = str(row.get("target_date") or "")
                if (
                    row.get("city") == "Tokyo"
                    and start_date <= target_date <= end_date
                    and row.get("fetched_at_utc")
                    and row.get("last_obs_utc")
                ):
                    output.setdefault(target_date, []).append(row)
        current += timedelta(days=1)
    for rows in output.values():
        rows.sort(key=lambda row: parse_ts(row["fetched_at_utc"]))
    return output


def _official_history_from_rows(
    rows: list[dict[str, Any]], decision: datetime
) -> list[dict[str, Any]]:
    earliest_by_observation: dict[str, dict[str, Any]] = {}
    for row in rows:
        fetched = parse_ts(row["fetched_at_utc"])
        observed = parse_ts(row["last_obs_utc"])
        if fetched > decision or observed > decision:
            continue
        key = observed.isoformat()
        if key not in earliest_by_observation:
            earliest_by_observation[key] = row
    return sorted(
        earliest_by_observation.values(),
        key=lambda row: parse_ts(row["last_obs_utc"]),
    )


def _weather_features_at_book(
    jma: list[dict[str, Any]],
    official: list[dict[str, Any]],
    decision: datetime,
    book: dict[str, Any],
) -> tuple[dict[str, float | None], str]:
    if official:
        return _weather_features(jma, official, decision), "official_snapshot_exact"
    anchor = finite(book.get("reference_market_value"))
    if anchor is None:
        raise ValueError("book has no official reference anchor")
    source_obs = parse_ts(book["source_obs_ts_utc"])
    proxy = {
        "current_temp_c": anchor,
        "running_max_c": anchor,
        "last_obs_utc": source_obs.isoformat(),
        "fetched_at_utc": decision.isoformat(),
        "raw_metar": "",
        "relative_humidity_pct": None,
        "wind_speed_kt": None,
        "wind_dir_deg": None,
        "dwpf_now": None,
        "ceiling_ft_agl": None,
        "precip_observed": None,
    }
    features = _weather_features(jma, [proxy], decision)
    for name in (
        "prior_metar_temp_c",
        "jma_minus_prior_metar_c",
        "jma_lattice_minus_prior_metar_c",
        "prior_metar_age_min",
        "metar_dewpoint_c",
        "metar_relative_humidity_pct",
        "metar_dewpoint_depression_c",
        "metar_wind_speed_kt",
        "metar_wind_u_kt",
        "metar_wind_v_kt",
        "metar_pressure_hpa",
        "metar_pressure_delta",
        "metar_cloud_cover_fraction",
        "metar_ceiling_ft_agl",
        "metar_precipitating",
        "metar_visibility_m",
        "metar_pullback_from_current_bracket_c",
    ):
        features[name] = None
    # The official running-max bracket itself was captured in the raw book
    # request context and was therefore available at the quote clock.
    features["prior_metar_running_max_c"] = anchor
    features["current_bracket"] = anchor
    return features, "book_capture_official_anchor_only"


def materialize_raw_exact(
    *,
    source_journal: Path,
    observation_journal_dir: Path,
    books_root: Path,
    db_path: Path,
    weather_artifact: Path,
    weather_spec: Path,
    start_date: str,
    end_date: str,
    maximum_event_to_book_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build strict-PIT rows from raw clocks, bypassing migrated bundle clocks."""

    source_by_date, source_history_by_date, coverage = _exact_source_events(
        source_journal, start_date=start_date, end_date=end_date
    )
    books_by_event, raw_book_counts = _load_current_bracket_books(
        books_root, source_by_date
    )
    official_by_date = _official_snapshots_by_target_date(
        observation_journal_dir, start_date=start_date, end_date=end_date
    )
    artifact = joblib.load(weather_artifact)
    metadata = json.loads(weather_spec.read_text(encoding="utf-8"))
    settlements_by_condition, settlements_by_source_key = load_settlements(db_path)
    rows: list[dict[str, Any]] = []
    for target_date, events in source_by_date.items():
        counts = coverage[target_date]
        counts.update(
            {
                "raw_active_bracket_book_rows": raw_book_counts.get(target_date, 0),
                "events_with_current_bracket_book": 0,
                "events_with_causal_book": 0,
                "events_with_two_sided_book": 0,
                "events_scored_weather_v5": 0,
                "official_snapshot_feature_rows": 0,
                "book_anchor_only_feature_rows": 0,
                "settled_rows": 0,
                "five_share_depth_rows": 0,
            }
        )
        for event in events:
            source_obs = parse_ts(event["observation_time_utc"])
            first_seen = parse_ts(event["source_first_seen_at_utc"])
            candidates = books_by_event.get((target_date, source_obs.isoformat()), [])
            if not candidates:
                continue
            counts["events_with_current_bracket_book"] += 1
            causal = [
                book
                for book in candidates
                if 0
                <= (parse_ts(book["book_fetched_at_utc"]) - first_seen).total_seconds()
                <= maximum_event_to_book_seconds
            ]
            if not causal:
                continue
            counts["events_with_causal_book"] += 1
            two_sided = [
                book
                for book in causal
                if finite((book.get("summary") or {}).get("best_bid")) is not None
                and finite((book.get("summary") or {}).get("best_ask")) is not None
            ]
            if not two_sided:
                continue
            counts["events_with_two_sided_book"] += 1
            book = two_sided[0]
            decision = parse_ts(book["book_fetched_at_utc"])
            jma = [
                row
                for row in source_history_by_date[target_date]
                if parse_ts(row["observation_time_utc"]) <= source_obs
                and parse_ts(row["source_first_seen_at_utc"]) <= decision
            ]
            official = _official_history_from_rows(
                official_by_date.get(target_date, []), decision
            )
            if not jma or parse_ts(jma[-1]["observation_time_utc"]) != source_obs:
                continue
            features, official_feature_provenance = _weather_features_at_book(
                jma, official, decision, book
            )
            if official_feature_provenance == "official_snapshot_exact":
                counts["official_snapshot_feature_rows"] += 1
            else:
                counts["book_anchor_only_feature_rows"] += 1
            weather_stay, missing = _weather_stay_probability(
                artifact, metadata, features
            )
            counts["events_scored_weather_v5"] += 1
            summary = book.get("summary") or {}
            bid = float(summary["best_bid"])
            ask = float(summary["best_ask"])
            market_no = (bid + ask) / 2.0
            cash_cost, effective_cost = five_share_cost(book)
            bracket = str(book["bracket"])
            condition_id = str(book.get("condition_id") or "")
            condition_label = settlements_by_condition.get(condition_id)
            source_label = settlements_by_source_key.get(
                ("Tokyo", target_date, bracket)
            )
            if (
                condition_label is not None
                and source_label is not None
                and condition_label != source_label
            ):
                raise RuntimeError(
                    "condition/source-grain settlement conflict for "
                    f"{condition_id} {target_date} {bracket}"
                )
            won_no = condition_label if condition_label is not None else source_label
            if won_no in (0, 1):
                counts["settled_rows"] += 1
            if cash_cost is not None:
                counts["five_share_depth_rows"] += 1
            rows.append(
                {
                    "city": "Tokyo",
                    "target_date": target_date,
                    "event_id": str(
                        event.get("information_event_id")
                        or event.get("content_key")
                        or hashlib.sha256(
                            "|".join(
                                (
                                    "Tokyo",
                                    target_date,
                                    source_obs.isoformat(),
                                    str(event.get("payload_hash") or ""),
                                )
                            ).encode("utf-8")
                        ).hexdigest()
                    ),
                    "event_source": "jma_amedas",
                    "event_decision_ts_utc": first_seen.isoformat(),
                    "quote_ts_utc": decision.isoformat(),
                    "event_age_min": (decision - first_seen).total_seconds() / 60.0,
                    "bracket": bracket,
                    "condition_id": condition_id,
                    "relative_rung": 0,
                    "model_no_probability": 1.0 - weather_stay,
                    "market_no_probability": market_no,
                    "no_best_bid": bid,
                    "no_best_ask": ask,
                    "decision_ts_raw": str(book["book_fetched_at_utc"]),
                    "source_obs_ts_utc": source_obs.isoformat(),
                    "source_first_seen_at_utc": first_seen.isoformat(),
                    "book_snapshot_id": book.get("book_snapshot_id")
                    or book.get("capture_cycle_id"),
                    "weather_probability_stay": weather_stay,
                    "weather_model_id": "binary_multigrain_hgb_v5",
                    "weather_feature_coverage": 1.0 - len(missing) / len(metadata["features"]),
                    "official_feature_provenance": official_feature_provenance,
                    "cash_cost_5": cash_cost,
                    "effective_cost_5": effective_cost,
                    "displayed_ask_depth_5": int(cash_cost is not None),
                    "won_no": won_no,
                    "settlement_match_class": (
                        "condition_exact"
                        if condition_label is not None
                        else "source_grain_city_date_bracket"
                        if source_label is not None
                        else "missing"
                    ),
                    "availability_clock_class": event["_exact_clock_class"],
                    "evaluation_role": "strict_pit_forward",
                }
            )
    rows.sort(key=lambda row: (row["target_date"], row["quote_ts_utc"], row["bracket"]))
    summary = {
        "schema_version": "tokyo_raw_exact_market_prior_expression_v2",
        "city": "Tokyo",
        "model_target": "current_exact_bracket_no",
        "window": {"start": start_date, "end": end_date},
        "pit_policy": (
            "earliest verified collector first-seen per JMA observation; earliest "
            "causal two-sided current-official-bracket raw book within "
            f"{maximum_event_to_book_seconds:g}s; frozen v5 weather replay as of book"
        ),
        "coverage_by_target_date": coverage,
        "exact_rows": len(rows),
        "exact_dates": len({row["target_date"] for row in rows}),
        "settled_rows": sum(row["won_no"] in (0, 1) for row in rows),
        "settled_dates": len(
            {row["target_date"] for row in rows if row["won_no"] in (0, 1)}
        ),
        "inputs": {
            "source_journal": str(source_journal),
            "observation_journal_dir": str(observation_journal_dir),
            "books_root": str(books_root),
            "canonical_db": str(db_path.resolve()),
            "weather_artifact": str(weather_artifact),
            "weather_artifact_sha256": sha256_file(weather_artifact),
            "weather_spec": str(weather_spec),
            "weather_spec_sha256": sha256_file(weather_spec),
        },
        "migrated_bundle_clock_used": False,
        "late_backfill_or_issue_time_used_as_first_seen": False,
        "research_only_zero_notional": True,
        "live_behavior_changed": False,
    }
    return rows, summary


def attach_books(
    rows: list[dict[str, Any]], books_root: Path
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    wanted = {
        (
            str(row["target_date"]),
            str(row["bracket"]),
            parse_ts(row["decision_ts_raw"]).isoformat(),
        ): row
        for row in rows
    }
    matched: set[tuple[str, str, str]] = set()
    for target_date in sorted({str(row["target_date"]) for row in rows}):
        path = books_root / f"{target_date}.jsonl"
        if not path.exists():
            continue
        for book in iter_jsonl(path):
            if (
                book.get("city") != "Tokyo"
                or book.get("outcome") != "no"
                or book.get("book_status") != "ok"
            ):
                continue
            key = (
                str(book.get("target_date") or ""),
                str(book.get("bracket") or ""),
                parse_ts(book.get("book_fetched_at_utc")).isoformat(),
            )
            row = wanted.get(key)
            if row is None:
                continue
            summary = book.get("summary") or {}
            bid = finite(summary.get("best_bid"))
            ask = finite(summary.get("best_ask"))
            if bid != row["no_best_bid"] or ask != row["no_best_ask"]:
                raise RuntimeError(f"bundle/book top-of-book mismatch for {key}")
            cash_cost, effective_cost = five_share_cost(book)
            row["cash_cost_5"] = cash_cost
            row["effective_cost_5"] = effective_cost
            row["displayed_ask_depth_5"] = int(cash_cost is not None)
            matched.add(key)
    for key, row in wanted.items():
        if key not in matched:
            row["cash_cost_5"] = None
            row["effective_cost_5"] = None
            row["displayed_ask_depth_5"] = 0
    return rows, {
        "candidate_event_bracket_rows": len(rows),
        "exact_book_matches": len(matched),
        "five_share_depth_rows": sum(
            int(row["displayed_ask_depth_5"]) for row in rows
        ),
    }


def materialize(
    *,
    bundles: Path,
    books_root: Path,
    db_path: Path,
    start_date: str,
    end_date: str,
    maximum_event_to_book_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, candidate_counts = candidate_rows(
        bundles,
        start_date=start_date,
        end_date=end_date,
        maximum_event_to_book_seconds=maximum_event_to_book_seconds,
    )
    rows, book_counts = attach_books(rows, books_root)
    settlements_by_condition, settlements_by_source_key = load_settlements(db_path)
    for row in rows:
        condition_label = settlements_by_condition.get(str(row["condition_id"]))
        source_label = settlements_by_source_key.get(
            (str(row["city"]), str(row["target_date"]), str(row["bracket"]))
        )
        if (
            condition_label is not None
            and source_label is not None
            and condition_label != source_label
        ):
            raise RuntimeError(
                "condition/source-grain settlement conflict for "
                f"{row['condition_id']} {row['target_date']} {row['bracket']}"
            )
        row["won_no"] = (
            condition_label if condition_label is not None else source_label
        )
        row["settlement_match_class"] = (
            "condition_exact"
            if condition_label is not None
            else "source_grain_city_date_bracket"
            if source_label is not None
            else "missing"
        )
    rows.sort(key=lambda row: (row["target_date"], row["quote_ts_utc"], row["bracket"]))
    summary = {
        "schema_version": "tokyo_wcir_market_prior_expression_v1",
        "city": "Tokyo",
        "model_target": "current_exact_bracket_no",
        "weather_feature_source": (
            "production frozen binary_multigrain_hgb_v5; exact rows are read "
            "from v7 metadata.weather_probability_stay"
        ),
        "incumbent_residual_probability_used": False,
        "pit_policy": (
            "collector_exact first_seen <= exact referenced active-ladder book; "
            f"event-to-book lag <= {maximum_event_to_book_seconds:g}s"
        ),
        "window": {"start": start_date, "end": end_date},
        "candidate_funnel": candidate_counts,
        "book_funnel": book_counts,
        "settled_rows": sum(row["won_no"] in (0, 1) for row in rows),
        "settled_dates": len(
            {row["target_date"] for row in rows if row["won_no"] in (0, 1)}
        ),
        "settlement_match_class_counts": {
            match_class: sum(
                row["settlement_match_class"] == match_class for row in rows
            )
            for match_class in (
                "condition_exact",
                "source_grain_city_date_bracket",
                "missing",
            )
        },
        "inputs": {
            "bundles": str(bundles),
            "bundles_sha256": sha256_file(bundles),
            "books_root": str(books_root),
            "canonical_db": str(db_path.resolve()),
        },
        "research_only_zero_notional": True,
        "live_behavior_changed": False,
    }
    return rows, summary


def _logit_probability(value: float, temperature: float) -> float:
    clipped = min(max(float(value), 1e-8), 1.0 - 1e-8)
    return 1.0 / (
        1.0 + math.exp(-math.log(clipped / (1.0 - clipped)) / temperature)
    )


def load_frozen_v5_no_probabilities(
    *,
    feature_rows: Path,
    weather_artifact: Path,
    weather_spec: Path,
    wanted: set[tuple[str, str, int]],
) -> dict[tuple[str, str, int], float]:
    """Score development rows with the exact weather artifact used by WCIR."""

    spec = json.loads(weather_spec.read_text(encoding="utf-8"))
    artifact = joblib.load(weather_artifact)
    features = [str(value) for value in spec["features"]]
    temperature = float(artifact["temperature"])
    model = artifact["model"]
    matched: dict[tuple[str, str, int], dict[str, Any]] = {}
    opener = gzip.open if feature_rows.suffix == ".gz" else open
    with opener(feature_rows, "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (
                str(row["target_date"]),
                parse_ts(row["decision_ts_utc"]).isoformat(),
                int(float(row["current_bracket"])),
            )
            if key in wanted:
                if key in matched:
                    raise RuntimeError(f"duplicate Tokyo feature checkpoint {key}")
                matched[key] = row
    missing = wanted - matched.keys()
    if missing:
        raise RuntimeError(
            f"missing {len(missing)} deployed-weather feature checkpoints"
        )
    keys = sorted(matched)
    matrix = np.asarray(
        [
            [
                np.nan
                if finite(matched[key].get(name)) is None
                else float(matched[key][name])
                for name in features
            ]
            for key in keys
        ],
        dtype=float,
    )
    probabilities = model.predict_proba(matrix)
    classes = [int(value) for value in model.named_steps["model"].classes_]
    break_index = classes.index(1)
    return {
        key: _logit_probability(float(probabilities[index, break_index]), temperature)
        for index, key in enumerate(keys)
    }


def load_archive_development(
    path: Path,
    *,
    feature_rows: Path,
    weather_artifact: Path,
    weather_spec: Path,
) -> list[dict[str, Any]]:
    """Adapt the frozen replay using the same frozen weather head as WCIR."""
    opener = gzip.open if path.suffix == ".gz" else open
    source_rows: list[dict[str, Any]] = []
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if raw.get("availability_clock_class") != "archive_reconstructed_plus_15m":
                continue
            current = str(raw.get("current_bracket") or "")
            quotes = json.loads(str(raw.get("quotes_json") or "{}"))
            current_quote = quotes.get(current) or {}
            yes_bid = finite(current_quote.get("bid"))
            yes_ask = finite(current_quote.get("ask"))
            distribution = json.loads(str(raw.get("market_distribution_json") or "[]"))
            if (
                yes_bid is None
                or yes_ask is None
                or not distribution
            ):
                continue
            market_stay = finite(distribution[0])
            actual_delta = finite(raw.get("actual_delta"))
            if market_stay is None or actual_delta is None:
                continue
            decision = parse_ts(raw.get("availability_ts_utc"))
            quote = parse_ts(raw.get("snapshot_ts_utc"))
            if quote < decision:
                continue
            source_rows.append(
                {
                    "city": "Tokyo",
                    "target_date": str(raw["target_date"]),
                    "event_id": (
                        f"archive:{raw['state_id']}:{raw['snapshot_ts_utc']}"
                    ),
                    "event_source": "jma_archive_observation_clock",
                    "event_decision_ts_utc": decision.isoformat(),
                    "quote_ts_utc": quote.isoformat(),
                    "event_age_min": (quote - decision).total_seconds() / 60.0,
                    "bracket": current,
                    "condition_id": "",
                    "relative_rung": 0,
                    "market_no_probability": 1.0 - market_stay,
                    "no_best_bid": 1.0 - yes_ask,
                    "no_best_ask": 1.0 - yes_bid,
                    "decision_ts_raw": raw.get("snapshot_ts_utc"),
                    "source_obs_ts_utc": raw.get("decision_ts_utc"),
                    "source_first_seen_at_utc": raw.get("availability_ts_utc"),
                    "book_snapshot_id": "",
                    "cash_cost_5": None,
                    "effective_cost_5": None,
                    "displayed_ask_depth_5": 0,
                    "won_no": int(actual_delta != 0),
                    "availability_clock_class": "archive_reconstructed_plus_15m",
                    "evaluation_role": "development_only",
                }
            )
    wanted = {
        (
            str(row["target_date"]),
            parse_ts(row["source_obs_ts_utc"]).isoformat(),
            int(float(row["bracket"])),
        )
        for row in source_rows
    }
    probability_by_key = load_frozen_v5_no_probabilities(
        feature_rows=feature_rows,
        weather_artifact=weather_artifact,
        weather_spec=weather_spec,
        wanted=wanted,
    )
    output: list[dict[str, Any]] = []
    for row in source_rows:
        key = (
            str(row["target_date"]),
            parse_ts(row["source_obs_ts_utc"]).isoformat(),
            int(float(row["bracket"])),
        )
        model_no = probability_by_key[key]
        row["model_no_probability"] = model_no
        row["weather_probability_stay"] = 1.0 - model_no
        row["weather_model_id"] = "binary_multigrain_hgb_v5"
        output.append(row)
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundles", type=Path, default=DEFAULT_BUNDLES)
    parser.add_argument(
        "--source-journal", type=Path, default=DEFAULT_SOURCE_JOURNAL
    )
    parser.add_argument(
        "--observation-journal-dir", type=Path, default=DEFAULT_OBSERVATIONS
    )
    parser.add_argument("--books-root", type=Path, default=DEFAULT_BOOKS)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument("--start-date", default="2026-08-01")
    parser.add_argument("--end-date", default="2026-08-11")
    parser.add_argument("--maximum-event-to-book-seconds", type=float, default=180.0)
    parser.add_argument(
        "--legacy-bundle-input",
        action="store_true",
        help="Use the compatibility WCIR bundle adapter instead of raw exact replay.",
    )
    parser.add_argument(
        "--development-market-join",
        type=Path,
        help=(
            "Optional frozen archive market_join_rows.csv[.gz]. These rows are "
            "tagged development_only and never treated as exact first-seen."
        ),
    )
    parser.add_argument(
        "--development-feature-rows", type=Path, default=DEFAULT_FEATURE_ROWS
    )
    parser.add_argument(
        "--weather-artifact", type=Path, default=DEFAULT_WEATHER_ARTIFACT
    )
    parser.add_argument("--weather-spec", type=Path, default=DEFAULT_WEATHER_SPEC)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.legacy_bundle_input:
        rows, summary = materialize(
            bundles=args.bundles,
            books_root=args.books_root,
            db_path=args.db_path,
            start_date=args.start_date,
            end_date=args.end_date,
            maximum_event_to_book_seconds=args.maximum_event_to_book_seconds,
        )
        for row in rows:
            row["availability_clock_class"] = "collector_exact"
            row["evaluation_role"] = "strict_pit_forward"
    else:
        rows, summary = materialize_raw_exact(
            source_journal=args.source_journal,
            observation_journal_dir=args.observation_journal_dir,
            books_root=args.books_root,
            db_path=args.db_path,
            weather_artifact=args.weather_artifact,
            weather_spec=args.weather_spec,
            start_date=args.start_date,
            end_date=args.end_date,
            maximum_event_to_book_seconds=args.maximum_event_to_book_seconds,
        )
    development = (
        load_archive_development(
            args.development_market_join,
            feature_rows=args.development_feature_rows,
            weather_artifact=args.weather_artifact,
            weather_spec=args.weather_spec,
        )
        if args.development_market_join is not None
        else []
    )
    rows = sorted(
        development + rows,
        key=lambda row: (row["target_date"], row["quote_ts_utc"], row["bracket"]),
    )
    summary["development_input"] = (
        None
        if args.development_market_join is None
        else {
            "path": str(args.development_market_join),
            "sha256": sha256_file(args.development_market_join),
            "rows": len(development),
            "dates": len({row["target_date"] for row in development}),
            "clock_class": "archive_reconstructed_plus_15m",
            "role": "development_only",
            "weather_model_id": "binary_multigrain_hgb_v5",
            "weather_artifact": str(args.weather_artifact),
            "weather_artifact_sha256": sha256_file(args.weather_artifact),
            "weather_spec": str(args.weather_spec),
            "weather_spec_sha256": sha256_file(args.weather_spec),
            "feature_rows": str(args.development_feature_rows),
            "feature_rows_sha256": sha256_file(args.development_feature_rows),
        }
    )
    summary["combined_rows"] = len(rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "expressions.csv", rows)
    if summary.get("coverage_by_target_date"):
        write_csv(
            args.output_dir / "coverage_by_target_date.csv",
            [
                {"target_date": target_date, **counts}
                for target_date, counts in summary["coverage_by_target_date"].items()
            ],
        )
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
