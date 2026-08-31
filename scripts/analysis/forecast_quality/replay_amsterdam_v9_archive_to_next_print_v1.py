#!/usr/bin/env python3
"""Fail-closed bridge from legacy V9 checkpoints to frozen next-print weather scores.

The legacy checkpoint is an *opportunity index*, never a label or a model input.
It points at raw KNMI, EHAM and full-ladder files from which this program rebuilds
the captured path and the next distinct routine METAR label.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_modeling.amsterdam_feature_builder_v2 import AmsterdamFeatureBuilderV2
from weather_modeling.amsterdam_frozen_scorer_v2 import score_frozen_models

UTC = timezone.utc
MODELS = ("B2", "M1", "M2")
HORIZONS = (15, 30, 60, 120, 300)
OFFICIAL_MATCH_WINDOW_SECONDS = 3600
BOOTSTRAP_REPS = 10_000
BOOTSTRAP_SEED = 20_260_830
CORE_FEATURES = (
    "latest_fast_native_value", "last_official_native_value", "official_running_max",
    "fast_minus_last_official", "fast_minus_running_max", "distance_to_up_native_boundary",
    "distance_to_down_native_boundary", "local_time_sin", "local_time_cos",
    *AmsterdamFeatureBuilderV2.path_features,
)


def _ts(value: Any) -> pd.Timestamp | None:
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(parsed) else parsed


def half_up_tick(value: float) -> int:
    return int(Decimal(str(float(value))).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _rows(path: Path) -> list[dict[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    if not path.is_file():
        return []
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _metar_time(raw: str, target_date: str) -> pd.Timestamp | None:
    """Parse DDHHMMZ using target-day neighbourhood; raw report clock beats fetch clock."""
    match = re.search(r"\b(\d{2})(\d{2})(\d{2})Z\b", str(raw).upper())
    if not match:
        return None
    base = pd.Timestamp(target_date, tz="Europe/Amsterdam").tz_convert("UTC")
    day, hour, minute = map(int, match.groups())
    candidates = []
    for shift in (-1, 0, 1):
        month = base + pd.Timedelta(days=shift)
        try:
            candidates.append(pd.Timestamp(year=month.year, month=month.month, day=day, hour=hour, minute=minute, tz="UTC"))
        except ValueError:
            pass
    return min(candidates, key=lambda candidate: abs(candidate - base)) if candidates else None


def _routine_eham(row: Mapping[str, Any]) -> bool:
    raw = " ".join(str(row.get("raw_metar") or "").upper().split())
    kind = str(row.get("report_kind") or row.get("report_type") or "").upper()
    return (
        str(row.get("station") or row.get("station_id") or "") == "EHAM"
        and raw.split(maxsplit=1)[0:1] == ["METAR"]
        and kind != "SPECI"
    )


def _official_events(rows: Iterable[Mapping[str, Any]], target_date: str) -> tuple[list[dict[str, Any]], str | None]:
    """Collapse identical reports by their stable raw payload and earliest first-seen.

    An ingest hash is deliberately not an identity: repeated fetches of the same
    raw METAR can have different wrapper hashes.  Conflicting payloads are marked
    on their own report group so an unrelated conflict elsewhere in the day does
    not invalidate every checkpoint.
    """
    grouped: dict[pd.Timestamp, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if not _routine_eham(row):
            continue
        observed = _ts(row.get("source_report_ts_utc") or row.get("observation_time_utc")) or _metar_time(str(row.get("raw_metar") or ""), target_date)
        available = _ts(row.get("first_seen_at_utc") or row.get("local_detect_ts_utc") or row.get("fetched_at_utc"))
        temp = row.get("temp_c", row.get("current_temp_c"))
        if observed is None or available is None or temp is None:
            continue
        payload = json.dumps(
            {
                "raw_metar": " ".join(str(row.get("raw_metar") or "").split()),
                "temp_c": float(temp),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        event = {
            "observed_at": observed,
            "available_at": available,
            "temp_c": float(temp),
            "payload": payload,
        }
        existing = grouped.setdefault(observed, {}).get(payload)
        if existing is None or available < existing["available_at"]:
            grouped[observed][payload] = event
    events: list[dict[str, Any]] = []
    for observed, variants in sorted(grouped.items()):
        ordered = sorted(variants.values(), key=lambda event: (event["available_at"], event["payload"]))
        if len(ordered) == 1:
            events.append({**ordered[0], "ambiguous": False})
        else:
            events.append({
                "observed_at": observed,
                "available_at": ordered[0]["available_at"],
                "temp_c": None,
                "payload": None,
                "ambiguous": True,
            })
    return events, None


def _next_routine_label_from_events(
    events: Iterable[Mapping[str, Any]],
    *,
    decision_at: Any,
    source_observed_at: Any | None,
    last_official: float,
) -> dict[str, Any]:
    decision = _ts(decision_at)
    source_observed = _ts(source_observed_at) if source_observed_at is not None else None
    observed_candidates = [
        event for event in events
        if source_observed is None or event["observed_at"] > source_observed
    ]
    candidates = (
        observed_candidates
        if source_observed is not None
        else [event for event in observed_candidates if event["available_at"] > decision]
    )
    if not candidates:
        return {"label": None, "reason": "NEXT_ROUTINE_EHAM_METAR_NOT_CAPTURED"}
    event = min(candidates, key=lambda item: (item["observed_at"], item["available_at"], str(item.get("payload"))))
    if source_observed is not None:
        if (event["observed_at"] - source_observed).total_seconds() > OFFICIAL_MATCH_WINDOW_SECONDS:
            return {"label": None, "reason": "NEXT_ROUTINE_OUTSIDE_FROZEN_MATCH_WINDOW"}
        if event["available_at"] <= decision:
            return {"label": None, "reason": "NEXT_ROUTINE_ALREADY_AVAILABLE_AT_DECISION"}
    if event.get("ambiguous"):
        return {"label": None, "reason": "AMBIGUOUS_OFFICIAL_PRINT"}
    return {
        "label": half_up_tick(event["temp_c"]) - half_up_tick(last_official),
        "reason": None,
        "official_observed_at": event["observed_at"].isoformat(),
        "official_available_at": event["available_at"].isoformat(),
    }


def next_routine_label(
    rows: Iterable[Mapping[str, Any]],
    *,
    target_date: str,
    decision_at: Any,
    last_official: float,
    source_observed_at: Any | None = None,
) -> dict[str, Any]:
    """Return next *distinct* routine report. Same temperature is a valid delta zero."""
    events, error = _official_events(rows, target_date)
    if error:
        return {"label": None, "reason": error}
    return _next_routine_label_from_events(
        events,
        decision_at=decision_at,
        source_observed_at=source_observed_at,
        last_official=last_official,
    )


def prior_official_state(
    events: Iterable[Mapping[str, Any]],
    *,
    decision_at: Any,
    source_observed_at: Any,
) -> tuple[dict[str, Any] | None, str | None]:
    """Rebuild causal prior official state from raw events, never legacy aggregates."""
    decision = _ts(decision_at)
    source_observed = _ts(source_observed_at)
    eligible = [
        event for event in events
        if event["observed_at"] < source_observed
        and event["available_at"] <= decision
        and not event.get("ambiguous")
        and event.get("temp_c") is not None
    ]
    if not eligible:
        return None, "MISSING_CAUSAL_PRIOR_OFFICIAL_STATE"
    prior = max(eligible, key=lambda event: (event["observed_at"], event["available_at"], str(event.get("payload"))))
    running_max = max(float(event["temp_c"]) for event in eligible)
    return {
        "last_official_native_value": float(prior["temp_c"]),
        "official_running_max": running_max,
        "official_observed_at": prior["observed_at"].isoformat(),
        "official_available_at": prior["available_at"].isoformat(),
    }, None


def _prepare_source_rows(rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(list(rows))
    if frame.empty:
        return pd.DataFrame(columns=[
            "observed_at", "available_at", "latest_fast_native_value", "source_observation_id",
        ])
    if "station" in frame:
        frame = frame.loc[frame["station"].astype(str).eq("0-20000-0-06240")]
    observed = pd.to_datetime(
        frame.get("observation_time_utc", pd.Series(index=frame.index, dtype=object)),
        utc=True,
        errors="coerce",
    )
    if "measurement_interval_end_utc" in frame:
        observed = observed.fillna(pd.to_datetime(frame["measurement_interval_end_utc"], utc=True, errors="coerce"))
    available = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    for column in (
        "available_at_utc", "first_seen_at_utc", "knmi_first_seen_at_utc",
        "local_detect_ts_utc", "fetched_at_utc",
    ):
        if column in frame:
            available = available.fillna(pd.to_datetime(frame[column], utc=True, errors="coerce"))
    temp = pd.to_numeric(frame.get("temp_c", pd.Series(index=frame.index, dtype=float)), errors="coerce")
    identity = pd.Series(index=frame.index, dtype=object)
    for column in ("payload_hash", "raw_payload_hash", "knmi_filename"):
        if column in frame:
            identity = identity.fillna(frame[column].astype(object))
    identity = identity.fillna(observed.astype(str))
    prepared = pd.DataFrame({
        "observed_at": observed,
        "available_at": available,
        "latest_fast_native_value": temp,
        "source_observation_id": identity.astype(str),
    }).dropna(subset=["observed_at", "available_at", "latest_fast_native_value"])
    # Repeated collector fetches of one immutable payload keep its true earliest
    # availability.  A distinct payload at the same observation time remains a
    # revision and is selected as-of the decision below.
    return (
        prepared.sort_values(["observed_at", "source_observation_id", "available_at"])
        .drop_duplicates(["observed_at", "source_observation_id"], keep="first")
        .reset_index(drop=True)
    )


def _source_path(rows: Iterable[Mapping[str, Any]] | pd.DataFrame, *, target_date: str, decision_at: pd.Timestamp, observed_at: pd.Timestamp, last_official: float, running_max: float) -> tuple[pd.DataFrame, str | None]:
    start = pd.Timestamp(target_date, tz="Europe/Amsterdam").tz_convert("UTC")
    if isinstance(rows, pd.DataFrame) and {
        "observed_at", "available_at", "latest_fast_native_value", "source_observation_id",
    }.issubset(rows.columns):
        prepared = rows
    else:
        prepared = _prepare_source_rows(rows)
    mask = (
        prepared["observed_at"].between(start, observed_at, inclusive="both")
        & prepared["available_at"].le(decision_at)
    )
    path = prepared.loc[mask].copy()
    if path.empty:
        return pd.DataFrame(), "MISSING_CAUSAL_KNMI_PATH"
    path["target_date"] = target_date
    path = path.sort_values(["observed_at", "available_at", "source_observation_id"]).drop_duplicates("observed_at", keep="last")
    gaps = path["observed_at"].diff().dropna().dt.total_seconds().div(60)
    if (
        path["observed_at"].min() != start
        or path["observed_at"].max() != observed_at
        or not bool(gaps.eq(10).all())
    ):
        return path, "INCOMPLETE_CAPTURED_SOURCE_PATH"
    local = path["observed_at"].dt.tz_convert("Europe/Amsterdam")
    minute = local.dt.hour * 60 + local.dt.minute
    path["last_official_native_value"] = half_up_tick(last_official)
    path["official_running_max"] = half_up_tick(running_max)
    path["fast_minus_last_official"] = path.latest_fast_native_value - path.last_official_native_value
    path["fast_minus_running_max"] = path.latest_fast_native_value - path.official_running_max
    path["distance_to_up_native_boundary"] = np.ceil(path.latest_fast_native_value) - path.latest_fast_native_value
    path["distance_to_down_native_boundary"] = path.latest_fast_native_value - np.floor(path.latest_fast_native_value)
    path["local_time_sin"] = np.sin(2 * np.pi * minute / 1440)
    path["local_time_cos"] = np.cos(2 * np.pi * minute / 1440)
    return path, None


def exact_token_books(snapshot: Mapping[str, Any], *, condition_id: Any, token_id: Any) -> tuple[dict[str, Any] | None, str | None]:
    records = [row for row in snapshot.get("records", []) if str(row.get("condition_id")) == str(condition_id) and str(token_id) in {str(row.get("yes_token_id")), str(row.get("no_token_id"))}]
    if len(records) != 1:
        return None, "TOKEN_IDENTITY_AMBIGUOUS" if len(records) > 1 else "TOKEN_IDENTITY_NOT_FOUND"
    row = records[0]
    side = "yes" if str(token_id) == str(row.get("yes_token_id")) else "no"
    return {"condition_id": str(condition_id), "token_id": str(token_id), "side": side, "ask": row.get(f"{side}_best_ask"), "bid": row.get(f"{side}_best_bid"), "ask_size": row.get(f"{side}_ask_size"), "bid_size": row.get(f"{side}_bid_size")}, None


def horizon_book_coverage(snapshot_path: Path, *, condition_id: Any, token_id: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"book_t0_source_relative": None}
    if snapshot_path.is_file():
        snapshot = json.loads(snapshot_path.read_text())
        if snapshot.get("capture_status") not in {None, "complete"}:
            result["book_t0_reason"] = "SNAPSHOT_CAPTURE_INCOMPLETE"
        else:
            result["book_t0_source_relative"], result["book_t0_reason"] = exact_token_books(snapshot, condition_id=condition_id, token_id=token_id)
    else:
        result["book_t0_reason"] = "SNAPSHOT_NOT_FOUND"
    name = snapshot_path.name
    for horizon in HORIZONS:
        candidate = snapshot_path.with_name(re.sub(r"_t000_", f"_t{horizon:03d}_", name))
        key = f"book_t{horizon:03d}_source_relative"
        if not candidate.is_file():
            result[key], result[f"book_t{horizon:03d}_reason"] = None, "SNAPSHOT_NOT_FOUND"
        else:
            snapshot = json.loads(candidate.read_text())
            if snapshot.get("capture_status") not in {None, "complete"}:
                result[key], result[f"book_t{horizon:03d}_reason"] = None, "SNAPSHOT_CAPTURE_INCOMPLETE"
            else:
                result[key], result[f"book_t{horizon:03d}_reason"] = exact_token_books(snapshot, condition_id=condition_id, token_id=token_id)
    return result


def checkpoint_token_identity(item: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Recover a legacy selected token only from the t0 full-ladder identity.

    Older CSVs commonly retained bracket + selected_side but not token ids.  A
    single matching exact bracket is sufficient; anything else remains NULL.
    """
    condition, token = item.get("condition_id"), item.get("token_id")
    if condition and token:
        return str(condition), str(token), None
    path = Path(str(item.get("snapshot_path") or ""))
    if not path.is_file():
        return None, None, "SNAPSHOT_NOT_FOUND"
    bracket, side = item.get("current_bracket_c"), str(item.get("selected_side") or "").lower()
    try:
        bracket_key = str(int(float(bracket))) if float(bracket).is_integer() else str(float(bracket))
    except (TypeError, ValueError):
        bracket_key = str(bracket)
    matches = []
    for row in json.loads(path.read_text()).get("records", []):
        raw = row.get("bracket")
        try:
            row_key = str(int(float(raw))) if float(raw).is_integer() else str(float(raw))
        except (TypeError, ValueError):
            row_key = str(raw)
        if row_key == bracket_key:
            matches.append(row)
    if len(matches) != 1 or side not in {"yes", "no"}:
        return None, None, "LEGACY_TOKEN_IDENTITY_UNRESOLVED"
    row = matches[0]
    token = row.get(f"{side}_token_id")
    return (str(row.get("condition_id")), str(token), None) if token else (None, None, "LEGACY_TOKEN_IDENTITY_UNRESOLVED")


def _manifests(directory: Path) -> dict[str, Any]:
    return {name: json.loads((directory / f"{name}_MODEL_ARTIFACT_MANIFEST.json").read_text()) for name in MODELS}


def replay(checkpoint: pd.DataFrame, manifests: Mapping[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    raw_cache: dict[str, list[dict[str, Any]]] = {}
    source_cache: dict[str, pd.DataFrame] = {}
    official_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for index, item in checkpoint.iterrows():
        target_date = str(item["target_date"])
        decision, observed = _ts(item.get("source_first_seen_at_utc") or item.get("observed_at_utc")), _ts(item.get("observed_at_utc"))
        base = {"legacy_checkpoint_row": int(index), "target_date": target_date, "decision_at_utc": None if decision is None else decision.isoformat(), "source_observed_at_utc": None if observed is None else observed.isoformat(), "availability_class": "CAPTURED_PIT_ARCHIVE", "action_mapping_status": "NOT_FROZEN_NO_ROI"}
        if decision is None or observed is None:
            rows.append({**base, "status": "MISSING_DECISION_CLOCK"}); continue
        source_file, official_file = Path(str(item.get("source_path", ""))), Path(str(item.get("official_path", "")))
        source_key = str(source_file)
        if source_key not in source_cache:
            source_cache[source_key] = _prepare_source_rows(
                raw_cache.setdefault(source_key, _rows(source_file))
            )
        source = source_cache[source_key]
        official_key = (str(official_file), target_date)
        if official_key not in official_cache:
            events, _ = _official_events(
                raw_cache.setdefault(str(official_file), _rows(official_file)),
                target_date,
            )
            official_cache[official_key] = events
        official_events = official_cache[official_key]
        prior, prior_reason = prior_official_state(
            official_events,
            decision_at=decision,
            source_observed_at=observed,
        )
        if prior_reason:
            rows.append({**base, "status": prior_reason}); continue
        path, reason = _source_path(
            source,
            target_date=target_date,
            decision_at=decision,
            observed_at=observed,
            last_official=float(prior["last_official_native_value"]),
            running_max=float(prior["official_running_max"]),
        )
        if reason:
            rows.append({**base, "status": reason}); continue
        built = AmsterdamFeatureBuilderV2.build_vintage(path, decision_vintage_id=f"v9-bridge-{index}", feature_cutoff_at=decision, decision_ready_at=decision, observation_cutoff_at=observed, availability_class="CAPTURED_PIT_ARCHIVE", feature_names=CORE_FEATURES, source_path_complete=True)
        if built.status != "OK":
            rows.append({**base, "status": built.status, "source_path_row_count": built.source_path_row_count}); continue
        label = _next_routine_label_from_events(
            official_events,
            decision_at=decision,
            source_observed_at=observed,
            last_official=float(prior["last_official_native_value"]),
        )
        pmfs = score_frozen_models(manifests, built.feature_vector)
        row = {
            **base,
            "status": "OK" if label["label"] is not None else str(label["reason"]),
            "next_official_delta_native_tick": label["label"],
            "official_print_group_id": label.get("official_observed_at"),
            "official_available_at_utc": label.get("official_available_at"),
            "prior_official_observed_at_utc": prior["official_observed_at"],
            "prior_official_available_at_utc": prior["official_available_at"],
            "last_official_native_value": prior["last_official_native_value"],
            "official_running_max": prior["official_running_max"],
            "latest_fast_native_value": built.feature_vector["latest_fast_native_value"],
            "opportunity_matched": half_up_tick(built.feature_vector["latest_fast_native_value"])
            == int(built.feature_vector["official_running_max"] + 1),
            "source_path_row_count": built.source_path_row_count,
            "book_identity_role": "legacy_selected_token_coverage_only_not_model_action",
            **{
                f"{name}_pmf": json.dumps(pmf, separators=(",", ":"))
                for name, pmf in pmfs.items()
            },
        }
        for name, pmf in pmfs.items(): row[f"{name}_argmax_delta_native_tick"] = int(manifests[name]["support"][int(np.argmax(pmf))])
        condition, token, token_reason = checkpoint_token_identity(item)
        if token_reason:
            row["book_t0_source_relative"], row["book_t0_reason"] = None, token_reason
        else:
            row.update(horizon_book_coverage(Path(str(item.get("snapshot_path", ""))), condition_id=condition, token_id=token))
        rows.append(row)
    return pd.DataFrame(rows)


def summarize(
    frame: pd.DataFrame,
    manifests: Mapping[str, Any],
    *,
    include_opportunity_cohort: bool = True,
) -> dict[str, Any]:
    ok = frame.loc[frame.status.eq("OK")].copy() if "status" in frame else frame.iloc[0:0]
    result: dict[str, Any] = {
        "rows": int(len(frame)),
        "weather_label_rows": int(ok.next_official_delta_native_tick.notna().sum()) if len(ok) else 0,
        "status_counts": dict(Counter(frame.status.fillna("NULL"))) if "status" in frame else {},
        "model_trade_signals": None,
        "model_trade_signal_reason": "action mapping is not frozen; Layer-A PMFs are not token actions",
        "model_roi": None,
        "model_roi_reason": "no frozen token/side action mapping; source-relative books are coverage diagnostics only",
        "markout_horizon_semantics": "experimental/source_relative (not official+H; no ROI or PnL)",
    }
    def book_coverage_for(source: pd.DataFrame) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for horizon in (0, *HORIZONS):
            column = "book_t0_source_relative" if horizon == 0 else f"book_t{horizon:03d}_source_relative"
            values = source[column] if column in source else pd.Series(dtype=object)
            books = [value for value in values if isinstance(value, Mapping)]
            payload[f"t{horizon:03d}_source_relative"] = {
                "exact_token_rows": int(len(books)),
                "direct_ask_rows": int(sum(book.get("ask") is not None for book in books)),
                "direct_bid_rows": int(sum(book.get("bid") is not None for book in books)),
                "direct_ask_size_rows": int(sum(book.get("ask_size") is not None for book in books)),
                "direct_bid_size_rows": int(sum(book.get("bid_size") is not None for book in books)),
            }
        if "book_t0_source_relative" in source and "book_t030_source_relative" in source:
            entry_and_exit = source["book_t0_source_relative"].apply(
                lambda value: isinstance(value, Mapping) and value.get("ask") is not None
            ) & source["book_t030_source_relative"].apply(
                lambda value: isinstance(value, Mapping) and value.get("bid") is not None
            )
            payload["t0_direct_ask_and_t030_direct_bid"] = {
                "rows": int(entry_and_exit.sum()),
                "target_dates": int(source.loc[entry_and_exit, "target_date"].nunique()),
            }
        return payload

    result["legacy_selected_token_book_coverage"] = book_coverage_for(frame)
    result["strict_weather_and_legacy_selected_token_book_coverage"] = book_coverage_for(ok)
    model_date_scores: dict[str, pd.DataFrame] = {}
    for name in MODELS:
        column = f"{name}_argmax_delta_native_tick"
        eligible = ok.loc[ok.next_official_delta_native_tick.notna()]
        if len(eligible):
            predicted = eligible[column].astype(int); actual = eligible.next_official_delta_native_tick.astype(int)
            support = np.asarray(manifests[name]["support"], dtype=int)
            pmfs = np.stack([json.loads(value) for value in eligible[f"{name}_pmf"]])
            observed_cdf = (support[None, :] >= actual.to_numpy()[:, None]).astype(float)
            row_rps = np.square(
                np.cumsum(pmfs, axis=1)[:, :-1] - observed_cdf[:, :-1]
            ).sum(axis=1) / (len(support) - 1)
            scored = eligible[["target_date", "official_print_group_id"]].copy()
            scored["correct"] = predicted.eq(actual).to_numpy(float)
            scored["rps"] = row_rps
            group_scored = (
                scored.groupby(["target_date", "official_print_group_id"], dropna=False)[["correct", "rps"]]
                .mean()
                .reset_index()
            )
            date_scored = group_scored.groupby("target_date")[["correct", "rps"]].mean()
            model_date_scores[name] = date_scored
            result[name] = {
                "rows": int(len(eligible)),
                "official_print_groups": int(len(group_scored)),
                "target_dates": int(len(date_scored)),
                "accuracy": float(scored["correct"].mean()),
                "rps": float(scored["rps"].mean()),
                "primary_target_date_equal_group_mean_accuracy": float(date_scored["correct"].mean()),
                "primary_target_date_equal_group_mean_rps": float(date_scored["rps"].mean()),
                "ci_cluster": "official_print_group nested in target_date; checkpoint rows are not independent",
            }
        else: result[name] = {"rows": 0, "accuracy": None, "rps": None}
    if len(ok):
        support = np.asarray(manifests["B2"]["support"], dtype=int)
        actual = ok.next_official_delta_native_tick.astype(int)
        observed_cdf = (support[None, :] >= actual.to_numpy()[:, None]).astype(float)
        zero_pmf = np.zeros((len(ok), len(support)), dtype=float)
        zero_index = int(np.flatnonzero(support == 0)[0])
        zero_pmf[:, zero_index] = 1.0
        zero_scored = ok[["target_date", "official_print_group_id"]].copy()
        zero_scored["correct"] = actual.eq(0).to_numpy(float)
        zero_scored["rps"] = np.square(
            np.cumsum(zero_pmf, axis=1)[:, :-1] - observed_cdf[:, :-1]
        ).sum(axis=1) / (len(support) - 1)
        zero_groups = (
            zero_scored.groupby(["target_date", "official_print_group_id"], dropna=False)[["correct", "rps"]]
            .mean()
            .reset_index()
        )
        zero_dates = zero_groups.groupby("target_date")[["correct", "rps"]].mean()
        majority_tick = int(actual.value_counts().sort_values(ascending=False).index[0])
        result["weather_label_distribution"] = {
            str(int(key)): int(value)
            for key, value in actual.value_counts().sort_index().items()
        }
        result["baselines"] = {
            "always_zero_next_print": {
                "accuracy": float(zero_scored["correct"].mean()),
                "rps": float(zero_scored["rps"].mean()),
                "primary_target_date_equal_group_mean_accuracy": float(zero_dates["correct"].mean()),
                "primary_target_date_equal_group_mean_rps": float(zero_dates["rps"].mean()),
            },
            "empirical_checkpoint_majority_diagnostic": {
                "tick": majority_tick,
                "accuracy": float(actual.eq(majority_tick).mean()),
                "warning": "post-hoc descriptive baseline; not a frozen strategy",
            },
        }
        model_date_scores["ALWAYS_ZERO"] = zero_dates

        def paired_delta(candidate: str, baseline: str) -> dict[str, Any]:
            left, right = model_date_scores[candidate], model_date_scores[baseline]
            common = left.index.intersection(right.index)
            rng = np.random.default_rng(BOOTSTRAP_SEED)
            payload: dict[str, Any] = {
                "target_dates": int(len(common)),
                "bootstrap_reps": BOOTSTRAP_REPS,
                "cluster": "target_date after equal-weight official-print-group aggregation",
            }
            for metric in ("rps", "correct"):
                delta = left.loc[common, metric].to_numpy(float) - right.loc[common, metric].to_numpy(float)
                draws = delta[rng.integers(0, len(delta), size=(BOOTSTRAP_REPS, len(delta)))].mean(axis=1)
                label = "accuracy" if metric == "correct" else metric
                payload[f"candidate_minus_baseline_{label}"] = float(delta.mean())
                payload[f"candidate_minus_baseline_{label}_ci95"] = [
                    float(np.quantile(draws, 0.025)),
                    float(np.quantile(draws, 0.975)),
                ]
            return payload

        result["pairwise_target_date_bootstrap"] = {
            "M1_minus_B2": paired_delta("M1", "B2"),
            "M2_minus_B2": paired_delta("M2", "B2"),
            "M1_minus_M2": paired_delta("M1", "M2"),
            "B2_minus_always_zero": paired_delta("B2", "ALWAYS_ZERO"),
        }
    if include_opportunity_cohort and "opportunity_matched" in frame:
        opportunity = frame.loc[frame["opportunity_matched"].eq(True)].copy()
        nested = summarize(
            opportunity,
            manifests,
            include_opportunity_cohort=False,
        )
        result["opportunity_matched_cohort"] = nested
    return result


def summary_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# V9 archive → next-print bridge",
        "",
        f"- Archive rows: {summary['rows']}",
        f"- Strict weather-label rows: {summary['weather_label_rows']}",
        "- Legacy settlement labels were not read.",
        "- Source-relative book horizons are diagnostics only; B2/M1/M2 ROI/PnL is intentionally absent because no action mapping is frozen.",
        "",
        "| Model | Rows | Official groups | Target dates | Accuracy | RPS | Date-equal group accuracy | Date-equal group RPS |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in MODELS:
        item = summary[name]
        lines.append(
            f"| {name} | {item['rows']} | {item.get('official_print_groups', 0)} | "
            f"{item.get('target_dates', 0)} | {item['accuracy']:.4%} | {item['rps']:.6f} | "
            f"{item['primary_target_date_equal_group_mean_accuracy']:.4%} | "
            f"{item['primary_target_date_equal_group_mean_rps']:.6f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint-csv-gz", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    args = parser.parse_args()
    checkpoint = pd.read_csv(args.checkpoint_csv_gz)
    if args.start_date: checkpoint = checkpoint.loc[checkpoint.target_date.astype(str) >= args.start_date]
    if args.end_date: checkpoint = checkpoint.loc[checkpoint.target_date.astype(str) <= args.end_date]
    manifests = _manifests(args.artifact_dir)
    frame = replay(checkpoint.reset_index(drop=True), manifests)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try: frame.to_parquet(args.output_dir / "rows.parquet", index=False)
    except Exception: frame.to_csv(args.output_dir / "rows.csv.gz", index=False, compression="gzip")
    summary = summarize(frame, manifests)
    (args.output_dir / "coverage.json").write_text(json.dumps({
        "rows": summary["rows"],
        "status_counts": summary["status_counts"],
        "source_relative_horizons": list(HORIZONS),
        "legacy_selected_token_book_coverage": summary["legacy_selected_token_book_coverage"],
        "strict_weather_and_legacy_selected_token_book_coverage": summary["strict_weather_and_legacy_selected_token_book_coverage"],
    }, indent=2) + "\n")
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (args.output_dir / "summary.md").write_text(summary_markdown(summary))


if __name__ == "__main__":
    main()
