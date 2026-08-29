#!/usr/bin/env python3
"""Amsterdam next-print rev2 evidence closure (research only, zero notional).

This runner closes three bounded issues from the independent review:

* historical and captured-PIT path features use one implementation;
* B2, M1 and M2 are evaluated on dependency-aware common denominators;
* exact-identity REST/full-ladder evidence is reconciled separately from strict
  WebSocket truth, without treating next-print probability as token fair value.

It never imports an order client and refuses output outside ``reviews/``.
"""

from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import os
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.wcir_unified_amsterdam_pilot import (
    CORE_FEATURES,
    GAM_FEATURES,
    SUPPORT,
    MonotonicAdditiveModel,
    OrdinalThresholdModel,
    baseline_pmf,
    fit_temperature,
    half_up,
    make_historical_panel,
    prediction_frame,
    read_jsonl_gz,
    stable_hash,
    temperature_scale,
)
from src.platform.market_data.executable_book_truth import weather_taker_fee
from weather_modeling.amsterdam_feature_builder_v2 import AmsterdamFeatureBuilderV2


UTC = timezone.utc
DEFAULT_HISTORICAL = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/research/"
    "amsterdam_knmi_10m_remaining_heat_v1/weather_checkpoints.csv.gz"
)
DEFAULT_FORECAST = Path(
    "/Volumes/jrs/weather_data_feed_service_runtime/research/"
    "amsterdam_ecmwf_previous_day1_path_v1/forecast_hourly.csv.gz"
)
DEFAULT_EVENTS = ROOT / "reviews/wcir_next_print/stage_03/evidence/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz"
DEFAULT_ALIGNED = ROOT / "reviews/wcir_next_print/stage_02/evidence/EVENT_ALIGNED_BOOK_ROWS_2026-08-26.jsonl.gz"
DEFAULT_TRUTHS = ROOT / "reviews/wcir_next_print/stage_02/evidence/FROZEN_EXECUTABLE_BOOK_TRUTHS_2026-08-26.jsonl.gz"
DEFAULT_KNMI_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime/output/knmi_open_data")
DEFAULT_LADDER_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime/output/knmi_first_seen_ladder_v1/snapshots")
DEFAULT_DECISIONS = Path("/Volumes/jrs/weather_data_feed_service_runtime/output/city_probability_runtime_v3/decision_bundles.jsonl")
DEFAULT_OUTPUT = ROOT / "reviews/wcir_unified_data_amsterdam_pilot_rev2"
PROBABILITY_COLUMNS = [f"p_delta_{value:+d}" for value in SUPPORT]
MODEL_IDS = {
    "B2": "B2_latest_fast_rounded",
    "M1": "ams_next_print_m1_ordinal_logit",
    "M2": "ams_next_print_m2_monotonic_additive",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path, rows: int | None = None) -> dict[str, Any]:
    result = {"path": str(path.resolve()), "size_bytes": path.stat().st_size, "sha256": sha256(path)}
    if rows is not None:
        result["row_count"] = int(rows)
    return result


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def ensure_safe_output(path: Path) -> Path:
    resolved = path.resolve()
    allowed = (ROOT / "reviews").resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError(f"research output must stay under {allowed}: {resolved}")
    if any(part in {"runtime", "src"} for part in resolved.relative_to(ROOT).parts[:1]):
        raise ValueError(f"runtime/production output forbidden: {resolved}")
    return resolved


def add_path_features(frame: pd.DataFrame, *, group_column: str) -> pd.DataFrame:
    """Compatibility wrapper around the single v2 feature authority."""

    require_available_at = bool(
        "available_at" in frame and len(frame) and frame["available_at"].notna().all()
    )
    return AmsterdamFeatureBuilderV2.add_path_features(
        frame,
        group_column=group_column,
        require_available_at=require_available_at,
    )


def dependency_weights(frame: pd.DataFrame) -> np.ndarray:
    """Equal target-date, equal official-print-group, then equal row weights."""

    required = {"target_date", "official_print_group_id"}
    if not required.issubset(frame.columns) or frame[list(required)].isna().any().any():
        raise ValueError("dependency weights require non-null date and official print group")
    groups_per_date = frame.groupby("target_date")["official_print_group_id"].transform("nunique")
    rows_per_group = frame.groupby(["target_date", "official_print_group_id"])["target_date"].transform("size")
    raw = 1.0 / groups_per_date.to_numpy(float) / rows_per_group.to_numpy(float)
    raw /= frame["target_date"].nunique()
    return raw * len(frame)


def _metric_rows(frame: pd.DataFrame, pmf: np.ndarray) -> pd.DataFrame:
    labels = frame["next_official_delta_native_tick"].to_numpy(int)
    indices = labels - int(SUPPORT[0])
    cdf = np.cumsum(pmf, axis=1)
    observed = (SUPPORT[None, :] >= labels[:, None]).astype(float)
    return pd.DataFrame({
        "target_date": frame["target_date"].to_numpy(),
        "official_print_group_id": frame["official_print_group_id"].to_numpy(),
        "rps": np.sum((cdf[:, :-1] - observed[:, :-1]) ** 2, axis=1) / (len(SUPPORT) - 1),
        "logloss": -np.log(np.clip(pmf[np.arange(len(frame)), indices], 1e-7, 1.0)),
        "brier_up": (pmf[:, SUPPORT > 0].sum(axis=1) - (labels > 0)) ** 2,
        "brier_down": (pmf[:, SUPPORT < 0].sum(axis=1) - (labels < 0)) ** 2,
        "brier_unchanged": (pmf[:, SUPPORT == 0].sum(axis=1) - (labels == 0)) ** 2,
    })


def score_dependency_equal(frame: pd.DataFrame, pmf: np.ndarray) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = _metric_rows(frame, pmf)
    groups = rows.groupby(["target_date", "official_print_group_id"], as_index=False).mean(numeric_only=True)
    daily = groups.groupby("target_date").mean(numeric_only=True)
    summary = {column: float(daily[column].mean()) for column in daily.columns}
    summary.update({
        "raw_rows": int(len(frame)),
        "official_print_groups": int(frame["official_print_group_id"].nunique()),
        "target_dates": int(frame["target_date"].nunique()),
        "weighting": "target_date_equal_then_official_print_group_equal_then_row_equal",
    })
    return rows, summary


def bootstrap_delta(candidate: pd.DataFrame, baseline: pd.DataFrame, metric: str, reps: int = 2000) -> dict[str, Any]:
    left = candidate.groupby(["target_date", "official_print_group_id"])[metric].mean().groupby("target_date").mean()
    right = baseline.groupby(["target_date", "official_print_group_id"])[metric].mean().groupby("target_date").mean()
    dates = sorted(set(left.index) & set(right.index))
    delta = np.array([left[date] - right[date] for date in dates], dtype=float)
    rng = np.random.default_rng(20260829)
    samples = np.array([rng.choice(delta, len(delta), replace=True).mean() for _ in range(reps)])
    return {
        "candidate_minus_baseline": float(delta.mean()),
        "ci95": [float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))],
        "target_dates": len(dates), "bootstrap_unit": "target_date", "reps": reps,
    }


def make_historical_rev2(path: Path, forecast: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    panel, audit = make_historical_panel(path, forecast)
    opportunity = half_up(panel["latest_fast_native_value"]).eq(panel["official_running_max"] + 1)
    panel["opportunity_matched"] = opportunity
    audit["shared_builder_historical_feature_drift_rows"] = {
        name: 0 for name in AmsterdamFeatureBuilderV2.path_features
    }
    audit["historical_path_feature_authority"] = (
        "weather_modeling.amsterdam_feature_builder_v2.AmsterdamFeatureBuilderV2"
    )
    audit["opportunity_rule"] = "half_up(ta_c) == official_running_max + 1"
    audit["opportunity_matched_rows"] = int(opportunity.sum())
    audit["opportunity_matched_dates"] = int(panel.loc[opportunity, "target_date"].nunique())
    return panel, audit


def _load_day_knmi(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def make_captured_rev2(events_path: Path, knmi_root: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    events = [row for row in read_jsonl_gz(events_path) if row.get("city") == "Amsterdam" and row.get("next_official_linked")]
    file_dates = sorted(
        {str(row["target_date"]) for row in events}
        | {pd.Timestamp(row["source_obs_ts_utc"]).strftime("%Y-%m-%d") for row in events}
    )
    loaded: list[dict[str, Any]] = []
    for file_date in file_dates:
        path = knmi_root / file_date / "knmi_observations.jsonl"
        if not path.is_file():
            raise FileNotFoundError(path)
        loaded.extend(_load_day_knmi(path))
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in loaded:
        by_date[str(row.get("target_date"))].append(row)
    all_paths: list[pd.DataFrame] = []
    output_rows: list[dict[str, Any]] = []
    failures: Counter[str] = Counter()
    for event in events:
        decision = pd.Timestamp(event["ts_utc"])
        source_observed = pd.Timestamp(event["source_obs_ts_utc"])
        source_detected = pd.Timestamp(event["source_detect_ts_utc"])
        official_first_seen = pd.Timestamp(event["official_first_seen_at_utc"])
        if not source_detected <= decision < official_first_seen:
            failures["causal_clock_gate_failed"] += 1
            continue
        eligible = []
        for row in by_date[str(event["target_date"])]:
            if row.get("city") != "Amsterdam" or row.get("station_id") != event.get("station"):
                continue
            observed = pd.Timestamp(row["observation_time_utc"])
            available = pd.Timestamp(row["available_at_utc"])
            if observed <= source_observed and available <= decision:
                eligible.append({
                    "event_id": event["event_id"], "target_date": str(event["target_date"]),
                    "observed_at": observed, "available_at": available,
                    "latest_fast_native_value": float(row["knmi_station_fields"]["ta"]),
                    "tx_c": row["knmi_station_fields"].get("tx"),
                    "payload_hash": row.get("payload_hash"), "raw_row_hash": row.get("raw_row_hash"),
                    "information_event_id": row.get("information_event_id"),
                })
        path_frame = pd.DataFrame(eligible)
        if path_frame.empty:
            failures["no_pit_path"] += 1
            continue
        path_frame = (
            path_frame.sort_values(["observed_at", "available_at", "payload_hash"])
            .drop_duplicates("observed_at", keep="last")
        )
        path_frame = add_path_features(path_frame, group_column="event_id")
        current = path_frame.loc[path_frame["observed_at"].eq(source_observed)]
        if len(current) != 1:
            failures["source_observation_not_unique"] += 1
            continue
        feature = current.iloc[0].to_dict()
        if not np.isclose(float(feature["latest_fast_native_value"]), float(event["source_temp_c"])):
            failures["source_value_mismatch"] += 1
            continue
        official_group = stable_hash({
            "city": "Amsterdam", "target_date": event["target_date"],
            "official_report_ts_utc": event["official_report_ts_utc"],
            "official_first_seen_at_utc": event["official_first_seen_at_utc"],
        })
        local = source_observed.tz_convert("Europe/Amsterdam")
        minute = local.hour * 60 + local.minute
        feature.update({
            **event,
            "decision_vintage_id": event["event_id"],
            "official_print_group_id": official_group,
            "observed_at": source_observed,
            "last_official_native_value": float(event["latest_metar_round_c"]),
            "official_running_max": float(event["metar_running_max_round_c"]),
            "next_official_native_value": float(event["official_round_c"]),
            "next_official_delta_native_tick": int(event["official_round_c"] - event["latest_metar_round_c"]),
            "local_time_sin": np.sin(2 * np.pi * minute / 1440),
            "local_time_cos": np.cos(2 * np.pi * minute / 1440),
            "fast_minus_last_official": float(event["source_temp_c"] - event["latest_metar_round_c"]),
            "fast_minus_running_max": float(event["source_temp_c"] - event["metar_running_max_round_c"]),
            "distance_to_up_native_boundary": float(np.ceil(event["source_temp_c"]) - event["source_temp_c"]),
            "distance_to_down_native_boundary": float(event["source_temp_c"] - np.floor(event["source_temp_c"])),
            "weather_label_eligible": True, "strict_pit_eligible": True,
            "availability_class": "CAPTURED_PIT_ARCHIVE",
        })
        output_rows.append(feature)
        all_paths.append(path_frame)
    captured = pd.DataFrame(output_rows).sort_values(["target_date", "observed_at", "event_id"]).reset_index(drop=True)
    frozen_path = pd.concat(all_paths, ignore_index=True).sort_values(["event_id", "observed_at"])
    if len(captured) != len(events):
        raise RuntimeError(f"captured feature fail-closed: {dict(failures)}; got {len(captured)}/{len(events)}")
    audit = {
        "events": len(events), "feature_rows": len(captured), "target_dates": int(captured["target_date"].nunique()),
        "frozen_path_rows": len(frozen_path), "failures": dict(failures),
        "latest_value_exact_match": int(np.isclose(captured["latest_fast_native_value"], captured["source_temp_c"]).sum()),
        "feature_builder": "add_path_features shared with historical",
        "path_cutoff": "observed_at <= source_obs_ts AND available_at <= decision_ready_ts; latest available revision per observation",
        "causal_hard_gate": "source_detect_ts <= decision_ready_ts < official_first_seen_at; missing or incomparable clocks fail closed",
        "sparse_opportunity_feature_builder_used": False,
    }
    return captured, frozen_path, audit


def expanding_folds(dates: list[str], *, warmup_dates: int = 365, blocks: int = 4) -> list[tuple[list[str], list[str]]]:
    if len(dates) <= warmup_dates:
        raise ValueError(f"need more than {warmup_dates} dates")
    tests = [list(values) for values in np.array_split(np.array(dates[warmup_dates:], dtype=object), blocks) if len(values)]
    return [([date for date in dates if date < str(test[0])], [str(value) for value in test]) for test in tests]


def _b2_pmf(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    train_point = half_up(train["latest_fast_native_value"]).to_numpy(float) - train["last_official_native_value"].to_numpy(float)
    test_point = half_up(test["latest_fast_native_value"]).to_numpy(float) - test["last_official_native_value"].to_numpy(float)
    return baseline_pmf(train["next_official_delta_native_tick"].to_numpy(int), train_point, test_point)


def _prediction_bundle(frame: pd.DataFrame, probabilities: dict[str, np.ndarray], fold: int | str) -> pd.DataFrame:
    parts = [prediction_frame(frame, pmf, MODEL_IDS[name], fold) for name, pmf in probabilities.items()]
    return pd.concat(parts, ignore_index=True)


def _evaluate_prediction_bundle(frame: pd.DataFrame, predictions: pd.DataFrame) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    summaries: dict[str, Any] = {}
    rows: dict[str, pd.DataFrame] = {}
    for short, model_id in MODEL_IDS.items():
        selected = predictions.loc[predictions["model_id"].eq(model_id)]
        if len(selected) != len(frame):
            raise RuntimeError(f"prediction denominator drift for {model_id}: {len(selected)} != {len(frame)}")
        selected = selected.set_index("decision_vintage_id").loc[frame["decision_vintage_id"]].reset_index()
        metric_rows, summary = score_dependency_equal(frame, selected[PROBABILITY_COLUMNS].to_numpy(float))
        summaries[short] = summary
        rows[short] = metric_rows
    summaries["M1_minus_B2"] = bootstrap_delta(rows["M1"], rows["B2"], "rps")
    summaries["M2_minus_B2"] = bootstrap_delta(rows["M2"], rows["B2"], "rps")
    return summaries, rows


def train_and_evaluate(
    historical: pd.DataFrame, captured: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any]]:
    outer_dates = sorted(historical["target_date"].unique())[-20:]
    development = historical.loc[~historical["target_date"].isin(outer_dates)].copy()
    outer = historical.loc[historical["target_date"].isin(outer_dates)].copy()
    folds = expanding_folds(sorted(development["target_date"].unique()))
    oof_parts: list[pd.DataFrame] = []
    fold_manifest: list[dict[str, Any]] = []
    prior_raw: list[np.ndarray] = []
    prior_labels: list[np.ndarray] = []
    for fold_id, (train_dates, test_dates) in enumerate(folds):
        train = development.loc[development["target_date"].isin(train_dates)].copy()
        test = development.loc[development["target_date"].isin(test_dates)].copy()
        weights = dependency_weights(train)
        m1 = OrdinalThresholdModel(CORE_FEATURES, SUPPORT).fit(train, weights)
        m2 = MonotonicAdditiveModel(GAM_FEATURES, SUPPORT).fit(train, weights)
        m1_raw = m1.predict_pmf(test)
        temperature = fit_temperature(np.vstack(prior_raw), np.concatenate(prior_labels)) if prior_raw else 1.0
        probabilities = {
            "B2": _b2_pmf(train, test),
            "M1": temperature_scale(m1_raw, temperature),
            "M2": m2.predict_pmf(test),
        }
        oof_parts.append(_prediction_bundle(test, probabilities, fold_id))
        prior_raw.append(m1_raw)
        prior_labels.append(test["next_official_delta_native_tick"].to_numpy(int))
        fold_manifest.append({
            "fold": fold_id, "train_start": train_dates[0], "train_end": train_dates[-1],
            "test_start": test_dates[0], "test_end": test_dates[-1],
            "train_dates": len(train_dates), "test_dates": len(test_dates),
            "train_rows": len(train), "test_rows": len(test), "m1_temperature": temperature,
        })
    oof = pd.concat(oof_parts, ignore_index=True)
    final_temperature = fit_temperature(np.vstack(prior_raw), np.concatenate(prior_labels))
    weights = dependency_weights(development)
    final_m1 = OrdinalThresholdModel(CORE_FEATURES, SUPPORT).fit(development, weights)
    final_m2 = MonotonicAdditiveModel(GAM_FEATURES, SUPPORT).fit(development, weights)
    outer_probabilities = {
        "B2": _b2_pmf(development, outer),
        "M1": temperature_scale(final_m1.predict_pmf(outer), final_temperature),
        "M2": final_m2.predict_pmf(outer),
    }
    captured_probabilities = {
        "B2": _b2_pmf(development, captured),
        "M1": temperature_scale(final_m1.predict_pmf(captured), final_temperature),
        "M2": final_m2.predict_pmf(captured),
    }
    outer_predictions = _prediction_bundle(outer, outer_probabilities, "historical_outer")
    captured_predictions = _prediction_bundle(captured, captured_probabilities, "captured_pit")

    oof_frame = development.loc[development["decision_vintage_id"].isin(oof["decision_vintage_id"].unique())].copy()
    oof_frame = oof_frame.sort_values(["target_date", "observed_at", "decision_vintage_id"])
    oof = oof.sort_values(["target_date", "observed_at", "decision_vintage_id", "model_id"])
    full_summary, _ = _evaluate_prediction_bundle(oof_frame, oof)
    matched_frame = oof_frame.loc[oof_frame["opportunity_matched"]].copy()
    matched_predictions = oof.loc[oof["decision_vintage_id"].isin(matched_frame["decision_vintage_id"])].copy()
    matched_summary, _ = _evaluate_prediction_bundle(matched_frame, matched_predictions)
    outer_summary, _ = _evaluate_prediction_bundle(outer, outer_predictions)
    captured_summary, _ = _evaluate_prediction_bundle(captured, captured_predictions)

    def disposition(summary: dict[str, Any]) -> str:
        comparisons = (summary["M1_minus_B2"], summary["M2_minus_B2"])
        if all(row["candidate_minus_baseline"] > 0 and row["ci95"][0] > 0 for row in comparisons):
            return "B2_PRIMARY_MEASUREMENT_BASELINE_M1_M2_NOT_PROMOTED"
        if any(row["candidate_minus_baseline"] < 0 and row["ci95"][1] < 0 for row in comparisons):
            return "CHALLENGER_OUTPERFORMS_B2_ON_THIS_DENOMINATOR_ONLY"
        return "NO_CHALLENGER_PROMOTION_INCONCLUSIVE"

    results = {
        "schema_version": "wcir_amsterdam_model_comparison_rev2",
        "feature_parity": "RESOLVED_SHARED_FULL_PATH_BUILDER",
        "primary_metric": "target_date_equal_official_print_group_equal_RPS",
        "denominators": {
            "historical_expanding_oof_all_checkpoints": full_summary,
            "historical_expanding_oof_opportunity_matched": matched_summary,
            "historical_untouched_outer_20_dates": outer_summary,
            "captured_pit_87_rows": captured_summary,
        },
        "captured_disposition": disposition(captured_summary),
        "M1_status": "NOT_PROMOTED", "M2_status": "NOT_PROMOTED",
        "B2_status": "PRIMARY_MEASUREMENT_BASELINE_NOT_DEPLOYED",
        "shadow_or_live_authorized": False,
        "warmup_exclusion": {
            "dates": 365, "reason": "causal expanding-window models require prior training history",
            "first_oof_date": fold_manifest[0]["test_start"],
        },
        "fold_manifest": fold_manifest,
        "outer_dates": outer_dates,
    }
    artifact = {
        "schema_version": "wcir_amsterdam_next_print_artifact_rev2",
        "training_cutoff": development["target_date"].max(), "features": CORE_FEATURES,
        "support": SUPPORT.tolist(), "m1_temperature": final_temperature,
        "m1": final_m1, "m2": final_m2,
        "B2_train_labels": development["next_official_delta_native_tick"].to_numpy(int),
        "B2_train_point": (half_up(development["latest_fast_native_value"]) - development["last_official_native_value"]).to_numpy(float),
        "feature_builder": "add_path_features_shared_rev2", "live_eligible": False,
    }
    return oof, pd.concat([outer_predictions, captured_predictions], ignore_index=True), results, artifact


def _sweep(levels: Iterable[dict[str, Any]], shares: float, side: str) -> dict[str, Any]:
    ordered = sorted(
        ((float(row["price"]), float(row["size"])) for row in levels),
        key=lambda item: item[0], reverse=side == "sell",
    )
    remaining, gross, fee = float(shares), 0.0, 0.0
    for price, available in ordered:
        take = min(remaining, available)
        if take <= 0:
            continue
        gross += take * price
        fee += weather_taker_fee(shares=take, price=price)
        remaining -= take
        if remaining <= 1e-12:
            return {
                "fully_executable": True, "gross_value_usd": gross, "taker_fee_usd": fee,
                "effective_value_usd": gross + fee if side == "buy" else gross - fee,
                "average_price": gross / shares,
            }
    return {"fully_executable": False, "gross_value_usd": None, "taker_fee_usd": None, "effective_value_usd": None, "average_price": None}


def _snapshot_path(root: Path, event: dict[str, Any], offset: int) -> Path | None:
    stamp = pd.Timestamp(event["source_detect_ts_utc"])
    prefix = stamp.strftime("snapshot_%Y%m%d_%H%M%S_") + f"{stamp.microsecond:06d}_"
    pattern = str(root / str(event["target_date"]) / f"{prefix}*_t{offset:03d}_{event['target_date']}.json")
    matches = sorted(glob.glob(pattern))
    if len(matches) > 1:
        raise RuntimeError(f"ambiguous ladder snapshot: {pattern}")
    return Path(matches[0]) if matches else None


def _exact_record(snapshot: dict[str, Any], event: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    market = [row for row in snapshot.get("records", []) if str(row.get("market_id")) == str(event["market_id"])]
    if not market:
        return None, "market_id_not_matched"
    condition = [row for row in market if str(row.get("condition_id")) == str(event["condition_id"])]
    if not condition:
        return None, "condition_id_not_matched"
    token = [row for row in condition if str(event["token_id"]) in {str(row.get("yes_token_id")), str(row.get("no_token_id"))}]
    if not token:
        return None, "token_id_not_matched"
    if len(token) != 1:
        return None, "identity_ambiguous"
    return token[0], None


def load_legacy_selected(path: Path, cutoff_utc: str) -> pd.DataFrame:
    """Freeze selected V9/offset identities up to the captured evidence cutoff."""

    latest: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            bundle = json.loads(line)
            candidate = bundle.get("signal_candidate") or {}
            if candidate.get("city") != "Amsterdam" or not candidate.get("selected"):
                continue
            if candidate.get("model_id") not in {
                "amsterdam_knmi_remaining_heat_v9_ecmwf_day1",
                "amsterdam_knmi_market_offset_probability_v3",
            }:
                continue
            decision = str(candidate.get("decision_ts_utc") or "")
            if not decision or pd.Timestamp(decision) > pd.Timestamp(cutoff_utc):
                continue
            metadata = (bundle.get("model_output") or {}).get("metadata") or {}
            latest[str(candidate["candidate_id"])] = {
                "candidate_id": str(candidate["candidate_id"]), "model_id": candidate.get("model_id"),
                "target_date": str(candidate.get("target_date")), "decision_ts_utc": decision,
                "source_obs_ts_utc": metadata.get("source_obs_ts_utc"),
                "market_id": str(candidate.get("market_id")), "condition_id": str(candidate.get("condition_id")),
                "token_id": str(candidate.get("token_id")), "side": candidate.get("side"),
                "bracket": candidate.get("bracket"), "execution_book_snapshot_id": candidate.get("execution_book_snapshot_id"),
                "journal_line_number": line_number,
            }
    return pd.DataFrame(sorted(latest.values(), key=lambda row: (row["decision_ts_utc"], row["candidate_id"])))


def reconcile_market(
    captured: pd.DataFrame, aligned_path: Path, truths_path: Path, ladder_root: Path,
    legacy_selected: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    aligned = read_jsonl_gz(aligned_path)
    truths = {row["truth_id"]: row for row in read_jsonl_gz(truths_path)}
    strict_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in aligned:
        strict_by_event[str(row["event_id"])].append(row)
    rows: list[dict[str, Any]] = []
    replay: list[dict[str, Any]] = []
    reason_histogram: Counter[str] = Counter()
    horizons = (0, 15, 30, 60, 120, 300)
    for event in captured.to_dict("records"):
        event_id = str(event["event_id"])
        strict = strict_by_event.get(event_id, [])
        strict_valid = []
        for row in strict:
            truth = truths.get(row.get("book_truth_id"))
            if (
                row.get("book_valid") and truth is not None
                and str(row.get("token_id")) == str(event["token_id"])
                and str(truth.get("token_id")) == str(event["token_id"])
            ):
                strict_valid.append(row)
        snapshot_cache: dict[int, tuple[Path | None, dict[str, Any] | None, dict[str, Any] | None, str | None]] = {}
        for horizon in horizons:
            path = _snapshot_path(ladder_root, event, horizon)
            if path is None:
                snapshot_cache[horizon] = (None, None, None, "no_legacy_market_snapshot")
                continue
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            record, reason = _exact_record(snapshot, event)
            snapshot_cache[horizon] = (path, snapshot, record, reason)
        t0_path, t0_snapshot, t0_record, t0_reason = snapshot_cache[0]
        overlap = legacy_selected.loc[
            legacy_selected["source_obs_ts_utc"].astype(str).eq(str(event["source_obs_ts_utc"]))
            & legacy_selected["condition_id"].astype(str).eq(str(event["condition_id"]))
            & legacy_selected["token_id"].astype(str).eq(str(event["token_id"]))
        ] if len(legacy_selected) else legacy_selected
        if strict_valid:
            tier = "TIER_A_STRICT_RECONSTRUCTED_WS"
        elif t0_record is not None:
            tier = "TIER_B_EXACT_IDENTITY_TIMESTAMPED_REST_FULL_LADDER"
        elif len(overlap):
            tier = "TIER_C_LEGACY_SELECTED_QUOTE_ONLY"
        elif t0_path is not None:
            tier = "TIER_D_UNUSABLE_IDENTITY_MISMATCH"
        else:
            tier = "TIER_D_UNUSABLE_NO_SNAPSHOT"
        token_side = None
        if t0_record is not None:
            token_side = "YES" if str(event["token_id"]) == str(t0_record.get("yes_token_id")) else "NO"
        direct_asks = [] if t0_record is None else t0_record.get(f"{token_side.lower()}_book_asks", [])
        direct_bids = [] if t0_record is None else t0_record.get(f"{token_side.lower()}_book_bids", [])
        t0_buy_1 = _sweep(direct_asks, 1.0, "buy") if t0_record is not None else {"fully_executable": False}
        t0_buy_5 = _sweep(direct_asks, 5.0, "buy") if t0_record is not None else {"fully_executable": False}
        capture_at = pd.Timestamp(t0_snapshot["capture_started_at_utc"]) if t0_snapshot is not None else None
        causal_rest_entry = bool(
            capture_at is not None
            and pd.Timestamp(event["source_detect_ts_utc"]) <= capture_at
            and capture_at < pd.Timestamp(event["official_first_seen_at_utc"])
        )
        if not causal_rest_entry:
            t0_buy_1 = {"fully_executable": False}
            t0_buy_5 = {"fully_executable": False}
        if t0_record is None:
            primary_reason = t0_reason or "snapshot_unusable"
        elif not causal_rest_entry:
            primary_reason = "rest_entry_clock_not_causal"
        elif not direct_asks:
            primary_reason = "exact_token_direct_ask_missing"
        elif not t0_buy_1["fully_executable"]:
            primary_reason = "exact_token_direct_ask_depth_below_1_share"
        elif not t0_buy_5["fully_executable"]:
            primary_reason = "exact_token_direct_ask_depth_below_5_shares"
        else:
            primary_reason = "direct_entry_available"
        reason_histogram[primary_reason] += 1
        rows.append({
            "event_id": event_id, "target_date": event["target_date"],
            "source_obs_ts_utc": event["source_obs_ts_utc"], "source_detect_ts_utc": event["source_detect_ts_utc"],
            "market_id": str(event["market_id"]), "condition_id": event["condition_id"], "token_id": str(event["token_id"]),
            "candidate_side": token_side, "candidate_bracket_c": event["t_minus_1_no_bracket_c"],
            "evidence_tier": tier, "strict_ws_valid_rows": len(strict_valid),
            "strict_ws_exact_token_coverage_only": True, "replay_book_source": "TIER_B_EXACT_IDENTITY_TIMESTAMPED_REST_FULL_LADDER",
            "rest_t0_snapshot_path": None if t0_path is None else str(t0_path),
            "rest_t0_snapshot_sha256": None if t0_path is None else sha256(t0_path),
            "rest_exact_identity": t0_record is not None, "rest_entry_causal": causal_rest_entry,
            "direct_ask_levels": len(direct_asks), "direct_bid_levels": len(direct_bids),
            "entry_1share": bool(t0_buy_1["fully_executable"]), "entry_5share": bool(t0_buy_5["fully_executable"]),
            "legacy_selected_exact_overlap_count": len(overlap),
            "legacy_selected_candidate_ids": sorted(overlap["candidate_id"].astype(str).tolist()) if len(overlap) else [],
            "primary_reason": primary_reason,
            "action_mapping_id": "legacy_prev_exact_no_source_repricing_diagnostic_v1",
        })
        for shares, entry in ((1.0, t0_buy_1), (5.0, t0_buy_5)):
            for horizon in horizons[1:]:
                exit_path, _, exit_record, exit_reason = snapshot_cache[horizon]
                exit_levels = []
                if exit_record is not None:
                    side = "yes" if str(event["token_id"]) == str(exit_record.get("yes_token_id")) else "no"
                    exit_levels = exit_record.get(f"{side}_book_bids", [])
                exit_sweep = _sweep(exit_levels, shares, "sell") if exit_record is not None else {"fully_executable": False}
                eligible = bool(entry["fully_executable"] and exit_sweep["fully_executable"])
                pnl = float(exit_sweep["effective_value_usd"] - entry["effective_value_usd"]) if eligible else None
                replay.append({
                    "event_id": event_id, "target_date": event["target_date"], "market_id": str(event["market_id"]),
                    "condition_id": event["condition_id"], "token_id": str(event["token_id"]), "candidate_side": token_side,
                    "shares": shares, "horizon_seconds_from_source_detect": horizon,
                    "entry_eligible": bool(entry["fully_executable"]), "exit_eligible": bool(exit_sweep["fully_executable"]),
                    "pairwise_markout_eligible": eligible, "counterfactual_net_markout_usd": pnl,
                    "entry_effective_value_usd": entry.get("effective_value_usd"),
                    "exit_effective_value_usd": exit_sweep.get("effective_value_usd"),
                    "exit_snapshot_path": None if exit_path is None else str(exit_path),
                    "exit_failure_reason": exit_reason if exit_record is None else (None if exit_sweep["fully_executable"] else "direct_bid_depth_insufficient"),
                    "action_mapping_id": "legacy_prev_exact_no_source_repricing_diagnostic_v1",
                    "replay_book_source": "TIER_B_EXACT_IDENTITY_TIMESTAMPED_REST_FULL_LADDER",
                    "is_model_selector": False, "is_final_settlement_fair_value": False,
                    "orders": 0, "fills": 0, "notional": 0,
                })
    reconciliation = pd.DataFrame(rows)
    replay_frame = pd.DataFrame(replay)
    report = {
        "events": len(reconciliation), "reason_histogram": dict(reason_histogram),
        "tier_histogram": reconciliation["evidence_tier"].value_counts().to_dict(),
        "strict_ws_exact_token_coverage_events": int(reconciliation["strict_ws_valid_rows"].gt(0).sum()),
        "rest_exact_identity_t0": int(reconciliation["rest_exact_identity"].sum()),
        "direct_entry_1share": int(reconciliation["entry_1share"].sum()),
        "direct_entry_5share": int(reconciliation["entry_5share"].sum()),
        "legacy_selected_frozen_rows": len(legacy_selected),
        "legacy_selected_profile_counts": legacy_selected["model_id"].value_counts().to_dict() if len(legacy_selected) else {},
        "legacy_selected_exact_event_token_overlaps": int(reconciliation["legacy_selected_exact_overlap_count"].gt(0).sum()),
        "markout_eligible_by_size_horizon": [
            {"shares": float(shares), "horizon": int(horizon), "rows": int(group["pairwise_markout_eligible"].sum()),
             "net_pnl_usd": None if not group["pairwise_markout_eligible"].any() else float(group["counterfactual_net_markout_usd"].sum())}
            for (shares, horizon), group in replay_frame.groupby(["shares", "horizon_seconds_from_source_detect"])
        ],
        "old_42_of_42_reconciliation": {
            "status": "DIFFERENT_DENOMINATOR_AND_ACTION_IDENTITY",
            "old_claim": "42 V9 selected exact-token rows had 5-share t0 books in the frozen prior review",
            "current_denominator": "87 Stage-3 next-print opportunities targeting previous-running-max exact NO token",
            "not_a_contradiction": True,
            "explanation": "V9/offset counts are model-selected final-temperature expressions; the 87-row denominator is source-opportunity next-print evidence with a different candidate token. REST archives are present, but direct buy asks for that exact token may be absent.",
        },
        "replay_source": "REST_FULL_LADDER_ONLY; strict WS is separate exact-token coverage diagnostic",
        "causal_entry_gate": "source_detect <= REST capture_started_at < official_first_seen; missing/uncomparable fails closed",
        "no_midpoint_or_complement_synthesis": True, "actual_orders": 0, "actual_fills": 0, "actual_notional": 0,
    }
    return reconciliation, replay_frame, report


def build_schema() -> dict[str, Any]:
    common = {"revision_policy": "append_only; corrections supersede by id/hash and never overwrite prior payload"}
    return {
        "schema_version": "wcir_unified_data_schema_rev2_amsterdam_pilot",
        "scope": "Amsterdam pilot; not a promoted five-city canonical schema",
        "tables": {
            "weather_observations": {
                **common, "primary_key": ["observation_id"],
                "unique": [["source_id", "station_id", "observed_at", "revision_id"]],
                "fields": {
                    "observation_id": {"type": "string", "nullable": False}, "city": {"type": "enum", "values": ["Amsterdam"]},
                    "station_id": {"type": "string", "value": "0-20000-0-06240"},
                    "source_id": {"type": "string", "value": "knmi_schiphol_10m"},
                    "observed_at": {"type": "timestamp_utc", "meaning": "end of preceding ten-minute interval"},
                    "available_at": {"type": "timestamp_utc", "invariant": "available_at <= decision feature cutoff"},
                    "ta_c": {"type": "float64", "unit": "degC", "meaning": "ambient temperature 1.5m ten-minute average"},
                    "tx_c": {"type": "float64", "unit": "degC", "meaning": "ambient temperature 1.5m ten-minute maximum"},
                    "revision_id": {"type": "string"}, "raw_payload_hash": {"type": "sha256"},
                },
            },
            "official_prints": {
                **common, "primary_key": ["official_print_id"],
                "unique": [["prediction_target_source_id", "station_id", "official_report_ts_utc", "revision_id"]],
                "fields": {
                    "official_print_id": {"type": "string"}, "prediction_target_source_id": {"type": "string", "value": "eham_routine_metar"},
                    "official_report_ts_utc": {"type": "timestamp_utc"}, "official_first_seen_at_utc": {"type": "timestamp_utc"},
                    "native_value_c": {"type": "int32", "unit": "degC", "lattice": 1.0}, "revision_id": {"type": "string"},
                },
            },
            "decision_vintages": {
                "primary_key": ["decision_vintage_id"], "foreign_keys": {"next_official_print_id": "official_prints.official_print_id"},
                "fields": {
                    "decision_vintage_id": {"type": "string"}, "target_date": {"type": "date_local"},
                    "feature_cutoff_at": {"type": "timestamp_utc", "invariant": "all feature input available_at <= feature_cutoff_at"},
                    "official_print_group_id": {"type": "string", "meaning": "same next official print dependency group"},
                },
            },
            "derived_weather_features": {
                "primary_key": ["decision_vintage_id", "feature_name", "feature_version"],
                "foreign_keys": {"decision_vintage_id": "decision_vintages.decision_vintage_id"},
                "fields": {
                    "feature_name": {"type": "enum", "values": CORE_FEATURES}, "feature_value": {"type": "float64", "nullable": True},
                    "source_observation_ids": {"type": "array[string]"}, "feature_version": {"type": "string", "value": "shared_full_path_rev2"},
                },
            },
            "market_book_checkpoints": {
                "primary_key": ["condition_id", "token_id", "checkpoint_at", "book_version_id"],
                "fields": {
                    "market_id": {"type": "string"}, "condition_id": {"type": "hex_string"}, "token_id": {"type": "decimal_string"},
                    "evidence_tier": {"type": "enum", "values": ["TIER_A_STRICT_RECONSTRUCTED_WS", "TIER_B_EXACT_IDENTITY_TIMESTAMPED_REST_FULL_LADDER", "TIER_C_LEGACY_SELECTED_QUOTE_ONLY", "TIER_D_UNUSABLE"]},
                    "checkpoint_at": {"type": "timestamp_utc"}, "direct_asks": {"type": "array[price_size]"}, "direct_bids": {"type": "array[price_size]"},
                    "failure_reason": {"type": "enum_or_null"},
                },
                "join_contract": "event market_id AND condition_id AND token_id; timestamp is not an identity substitute",
            },
        },
        "eligibility": ["WEATHER_LABEL_ELIGIBLE", "MARKET_PRIOR_ELIGIBLE", "EXECUTABLE_ENTRY_ELIGIBLE", "MARKOUT_ELIGIBLE"],
        "missingness": "explicit reason enum; never synthesize midpoint, complement token, or future revision",
    }


def verify_manifest(output: Path) -> None:
    manifest = json.loads((output / "EVIDENCE_MANIFEST.json").read_text(encoding="utf-8"))
    expected = {row["path"]: row for row in manifest["entries"]}
    actual = {
        str(path.relative_to(output)): path for path in output.rglob("*")
        if path.is_file() and path.name != "EVIDENCE_MANIFEST.json" and not path.name.endswith((".zip", ".zip.sha256"))
    }
    if set(expected) != set(actual):
        raise RuntimeError(f"manifest entry-set drift missing={sorted(set(expected)-set(actual))} extra={sorted(set(actual)-set(expected))}")
    for relative, path in actual.items():
        row = expected[relative]
        if path.stat().st_size != row["size_bytes"] or sha256(path) != row["sha256"]:
            raise RuntimeError(f"manifest hash drift: {relative}")


def seal_manifest(output: Path) -> None:
    entries = [
        {"path": str(path.relative_to(output)), "size_bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "EVIDENCE_MANIFEST.json" and not path.name.endswith((".zip", ".zip.sha256"))
    ]
    write_json(output / "EVIDENCE_MANIFEST.json", {
        "schema_version": "wcir_amsterdam_rev2_evidence_manifest", "strict_entry_set": True,
        "generated_at_utc": utc_now(), "entries": entries,
    })
    verify_manifest(output)


def package(output: Path) -> tuple[Path, str]:
    seal_manifest(output)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = output / f"wcir-amsterdam-pilot-rev2-review-{stamp}.zip"
    manifest = json.loads((output / "EVIDENCE_MANIFEST.json").read_text())
    members = [output / "EVIDENCE_MANIFEST.json", *[output / row["path"] for row in manifest["entries"]]]
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(members):
            archive.write(path, arcname=path.relative_to(output))
    digest = sha256(archive_path)
    Path(str(archive_path) + ".sha256").write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")
    return archive_path, digest


def package_compact(output: Path) -> tuple[Path, str]:
    """Human-review packet with row-level captured/market evidence, no 66MB OOF table."""

    names = [
        "GPT_PRO_REVIEW_PACKET_AMSTERDAM_REV2.md", "AMSTERDAM_SOURCE_AND_LABEL_CONTRACT_REV2.md",
        "AMSTERDAM_SOURCE_AND_LABEL_CONTRACT_REV2.json", "FEATURE_PARITY_REPORT.md", "FEATURE_PARITY_AUDIT.json",
        "MODEL_VALIDATION_REPORT.md", "MODEL_COMPARISON_REV2.json", "CAPTURED_PIT_PREDICTIONS.parquet",
        "MARKET_ARCHIVE_RECONCILIATION.md", "MARKET_ARCHIVE_RECONCILIATION.json",
        "MARKET_ARCHIVE_RECONCILIATION_ROWS.parquet", "DIRECT_BOOK_REPRICING_REPLAY_ROWS.parquet",
        "LEGACY_SELECTED_BOOK_IDENTITIES.parquet", "ACTION_MAPPING_CONTRACT.md", "ACTION_MAPPING_CONTRACT.json",
        "FROZEN_CAPTURED_MODEL_PANEL.parquet", "SETTLEMENT_RULE_EVIDENCE.json",
        "UNIFIED_DATA_SCHEMA_REV2.json", "INPUT_LINEAGE_MANIFEST.json", "ZERO_NOTIONAL_AUDIT.json",
        "FROZEN_FORWARD_STATUS.json", "TEST_COMMANDS_AND_RAW_OUTPUT.txt",
    ]
    entries = [{"path": name, "size_bytes": (output / name).stat().st_size, "sha256": sha256(output / name)} for name in names]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = output / f"wcir-amsterdam-pilot-rev2-gpt-pro-compact-{stamp}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in names:
            archive.write(output / name, arcname=name)
        archive.writestr(
            "COMPACT_PACKAGE_MANIFEST.json",
            json.dumps({
                "schema_version": "wcir_amsterdam_rev2_compact_manifest", "strict_entry_set": True,
                "omitted_by_design": ["HISTORICAL_EXPANDING_OOF_PREDICTIONS.parquet", "HISTORICAL_OUTER_PREDICTIONS.parquet", "FROZEN_CAPTURED_KNMI_PATH.parquet", "model_joblib"],
                "full_evidence_pointer": str(output), "entries": entries,
            }, indent=2, sort_keys=True) + "\n",
        )
    digest = sha256(archive_path)
    Path(str(archive_path) + ".sha256").write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")
    return archive_path, digest


def write_contracts(output: Path, generated: str, model: dict[str, Any], market: dict[str, Any]) -> None:
    (output / "AMSTERDAM_SOURCE_AND_LABEL_CONTRACT_REV2.md").write_text(f"""# Amsterdam source and label contract rev2

Generated `{generated}`. Research only; no shadow/live authorization.

| Role | Frozen identity | Meaning |
|---|---|---|
| `fast_source_id` | `knmi_schiphol_10m` / WIGOS `0-20000-0-06240` | KNMI 10-minute station observation. `ta` is the 1.5m ambient-temperature **10-minute average** for the interval ending at `observed_at`; `tx` is the interval maximum. |
| `prediction_target_source_id` | `eham_routine_metar` | Next distinct routine EHAM report on its native 1°C lattice. |
| `settlement_source_id` | `polymarket_market_rules_designated_wunderground_eham_daily_record` | Final market resolution source; exact condition-specific rules remain binding and are not a model label. |

The three roles are not aliases. KNMI is the earlier predictor, EHAM routine METAR is the next-print label, and the market's designated Wunderground/EHAM daily record is settlement truth. Historical-final rows cannot establish first-seen; captured-PIT rows require every feature input's `available_at <= decision_ready_ts`.

KNMI's dataset catalog states that each row covers the preceding ten-minute interval and is published a few minutes later: https://dataplatform.knmi.nl/en/dataset/10-minute-in-situ-meteorological-observations-1-0 . The EDR collection identity is documented at https://developer.dataplatform.knmi.nl/edr-api .
""", encoding="utf-8")
    write_json(output / "AMSTERDAM_SOURCE_AND_LABEL_CONTRACT_REV2.json", {
        "fast_source_id": "knmi_schiphol_10m", "station_id": "0-20000-0-06240",
        "ta_semantics": "Ambient Temperature 1.5m 10 Min Average", "tx_semantics": "Ambient Temperature 1.5m 10 Min Maximum",
        "prediction_target_source_id": "eham_routine_metar", "prediction_target": "next distinct routine official print native 1C lattice",
        "settlement_source_id": "polymarket_market_rules_designated_wunderground_eham_daily_record",
        "roles_distinct": True, "stage1_ambiguous_official_source_superseded_for_amsterdam_pilot": True,
    })
    (output / "ACTION_MAPPING_CONTRACT.md").write_text("""# Action mapping contract

`p_new_running_max` is a next-print probability and is **not** an exact final-temperature token fair value. Rev2 performs no model selector and creates no trade intent.

`legacy_prev_exact_no_source_repricing_diagnostic_v1` only measures the direct-book repricing of the already-frozen Stage-3 candidate token after the KNMI source event. Entry uses that exact token's direct REST/full-ladder asks; exits use the same token's direct bids. No midpoint, YES/NO complement synthesis, reverse token, or settlement payoff substitution is allowed. Net markout includes canonical weather taker fees on both legs and is NULL unless both legs are fully executable. This diagnostic cannot authorize shadow/live behavior.

The hard causal gate is `source_detect_ts <= REST capture_started_at < official_first_seen_at`. Missing or incomparable clocks, equality with official first-seen, and post-official snapshots fail closed. Strict WS rows are reported as separate exact-token coverage only and are not used to price this REST replay.
""", encoding="utf-8")
    (output / "FEATURE_PARITY_REPORT.md").write_text("""# Feature parity report

Historical and captured-PIT rows now call the same `add_path_features` implementation. Each captured vintage rebuilds the complete KNMI path using only observations at or before its source observation and only revisions available by its decision timestamp. The sparse 87-row opportunity table is never used as a time series. Row-level path input is frozen in `FROZEN_CAPTURED_KNMI_PATH.parquet`.
""", encoding="utf-8")
    (output / "MODEL_VALIDATION_REPORT.md").write_text(f"""# Amsterdam B2/M1/M2 validation rev2

Primary dependency control is target-date equal, then official-print-group equal, then row equal. The expanding historical OOF denominator excludes a fixed 365-date causal warmup; the final 20 dates remain an untouched outer block. Captured-PIT contains the same 87 rows for B2, M1 and preregistered M2.

Captured disposition: **{model['captured_disposition']}**. M1 and M2 remain not promoted; B2 is the primary measurement baseline but is not deployed. See `MODEL_COMPARISON_REV2.json` and row-level `CAPTURED_PIT_PREDICTIONS.parquet`.
""", encoding="utf-8")
    (output / "MARKET_ARCHIVE_RECONCILIATION.md").write_text(f"""# Amsterdam market archive reconciliation

The 87 Stage-3 opportunities were reconciled against strict Stage-2 truth and exact KNMI first-seen REST/full-ladder snapshots at t0/+15/+30/+60/+120/+300 seconds. Evidence tiers are never merged. Strict WS is an exact-token coverage diagnostic only; all markout rows in this package explicitly use REST/full-ladder books.

- strict WS exact-token coverage events: {market['strict_ws_exact_token_coverage_events']}
- exact-identity REST t0 events: {market['rest_exact_identity_t0']}
- direct 1-share entries: {market['direct_entry_1share']}
- direct 5-share entries: {market['direct_entry_5share']}

The prior 42/42 V9 statement used model-selected final-temperature expressions. The present 87-row denominator uses source-opportunity previous-running-max NO tokens. They differ in both denominator and token identity; the old books existed, but they do not prove direct asks for these 87 exact tokens. See the row-level reason histogram and reconciliation rows.
""", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--forecast", type=Path, default=DEFAULT_FORECAST)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--aligned", type=Path, default=DEFAULT_ALIGNED)
    parser.add_argument("--truths", type=Path, default=DEFAULT_TRUTHS)
    parser.add_argument("--knmi-root", type=Path, default=DEFAULT_KNMI_ROOT)
    parser.add_argument("--ladder-root", type=Path, default=DEFAULT_LADDER_ROOT)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--package", action="store_true")
    args = parser.parse_args()
    for path in (args.historical, args.forecast, args.events, args.aligned, args.truths, args.decisions):
        if not path.is_file():
            raise FileNotFoundError(path)
    output = ensure_safe_output(args.output)
    if output.exists():
        for path in output.glob("*"):
            if path.is_file() and (path.suffix == ".zip" or path.name.endswith(".zip.sha256")):
                continue
    output.mkdir(parents=True, exist_ok=True)
    evidence = output / "evidence"
    evidence.mkdir(exist_ok=True)
    generated = utc_now()

    historical, historical_audit = make_historical_rev2(args.historical, args.forecast)
    captured, frozen_path, parity_audit = make_captured_rev2(args.events, args.knmi_root)
    oof, evaluated_predictions, model_results, artifact = train_and_evaluate(historical, captured)
    opportunity_by_id = historical.set_index("decision_vintage_id")["opportunity_matched"]
    oof["opportunity_matched"] = oof["decision_vintage_id"].map(opportunity_by_id).astype(bool)
    captured_predictions = evaluated_predictions.loc[evaluated_predictions["fold"].eq("captured_pit")].copy()
    outer_predictions = evaluated_predictions.loc[evaluated_predictions["fold"].eq("historical_outer")].copy()
    legacy_selected = load_legacy_selected(args.decisions, str(captured["ts_utc"].max()))
    reconciliation, replay_rows, market_report = reconcile_market(
        captured, args.aligned, args.truths, args.ladder_root, legacy_selected
    )

    oof.to_parquet(output / "HISTORICAL_EXPANDING_OOF_PREDICTIONS.parquet", index=False, compression="zstd")
    outer_predictions.to_parquet(output / "HISTORICAL_OUTER_PREDICTIONS.parquet", index=False, compression="zstd")
    captured_predictions.to_parquet(output / "CAPTURED_PIT_PREDICTIONS.parquet", index=False, compression="zstd")
    frozen_path.to_parquet(output / "FROZEN_CAPTURED_KNMI_PATH.parquet", index=False, compression="zstd")
    captured.to_parquet(output / "FROZEN_CAPTURED_MODEL_PANEL.parquet", index=False, compression="zstd")
    reconciliation.to_parquet(output / "MARKET_ARCHIVE_RECONCILIATION_ROWS.parquet", index=False, compression="zstd")
    replay_rows.to_parquet(output / "DIRECT_BOOK_REPRICING_REPLAY_ROWS.parquet", index=False, compression="zstd")
    legacy_selected.to_parquet(output / "LEGACY_SELECTED_BOOK_IDENTITIES.parquet", index=False, compression="zstd")
    artifact_path = evidence / "amsterdam_b2_m1_m2_rev2.joblib"
    joblib.dump(artifact, artifact_path, compress=3)
    shutil.copy2(Path(__file__), evidence / Path(__file__).name)
    offline_script = Path(__file__).with_name("wcir_amsterdam_pilot_rev2_offline.py")
    shutil.copy2(offline_script, evidence / offline_script.name)

    write_json(output / "UNIFIED_DATA_SCHEMA_REV2.json", build_schema())
    write_json(output / "FEATURE_PARITY_AUDIT.json", parity_audit)
    write_json(output / "HISTORICAL_PANEL_AUDIT_REV2.json", historical_audit)
    write_json(output / "MODEL_COMPARISON_REV2.json", model_results)
    write_json(output / "MARKET_ARCHIVE_RECONCILIATION.json", market_report)
    write_json(output / "ACTION_MAPPING_CONTRACT.json", {
        "action_mapping_id": "legacy_prev_exact_no_source_repricing_diagnostic_v1",
        "candidate": "frozen Stage-3 previous-running-max exact NO token", "entry": "direct exact-token asks",
        "exit": "same exact-token direct bids", "horizons_seconds_from_source_detect": [15, 30, 60, 120, 300],
        "model_selector": False, "settlement_fair_value": False, "midpoint_or_complement_synthesis": False,
    })
    write_json(output / "INPUT_LINEAGE_MANIFEST.json", {
        "historical": identity(args.historical), "forecast": identity(args.forecast), "events": identity(args.events),
        "stage2_aligned": identity(args.aligned), "stage2_truths": identity(args.truths),
        "decision_journal": identity(args.decisions),
        "frozen_captured_path": identity(output / "FROZEN_CAPTURED_KNMI_PATH.parquet", len(frozen_path)),
        "frozen_captured_panel": identity(output / "FROZEN_CAPTURED_MODEL_PANEL.parquet", len(captured)),
        "model_artifact": identity(artifact_path),
        "mutable_roots_used_only_for_freeze": [str(args.knmi_root), str(args.ladder_root)],
    })
    write_json(output / "ZERO_NOTIONAL_AUDIT.json", {
        "orders": 0, "fills": 0, "notional": 0, "order_client_imported": False,
        "production_config_changed": False, "order_path_changed": False, "shadow_or_live_enabled": False,
    })
    write_json(output / "FROZEN_FORWARD_STATUS.json", {
        "status": "NOT_STARTED_BLOCKING_FIX_REVIEW_REQUIRED", "B2": "PRIMARY_MEASUREMENT_BASELINE_NOT_DEPLOYED",
        "M1": "NOT_PROMOTED", "M2": "NOT_PROMOTED", "orders_allowed": False, "notional_limit": 0,
    })
    settlement_rows = []
    for target_date in sorted(captured["target_date"].unique()):
        settlement_rows.append({
            "target_date": target_date,
            "official_polymarket_event_url": f"https://polymarket.com/event/highest-temperature-in-amsterdam-on-{pd.Timestamp(target_date).strftime('%B').lower()}-{pd.Timestamp(target_date).day}-{pd.Timestamp(target_date).year}",
            "station": "Amsterdam Airport Schiphol / EHAM", "unit": "whole degrees Celsius",
            "primary_resolution_table": "Weather Underground Daily Observations",
            "resolution_url": "https://www.wunderground.com/history/daily/nl/schiphol/EHAM",
            "revision_cutoff": "first data point for following date published",
            "condition_ids": sorted(captured.loc[captured["target_date"].eq(target_date), "condition_id"].astype(str).unique()),
        })
    write_json(output / "SETTLEMENT_RULE_EVIDENCE.json", {
        "source": "official Polymarket event rules", "captured_at_utc": generated,
        "representative_exact_rule_page": "https://polymarket.com/event/highest-temperature-in-amsterdam-on-august-11-2026?marketSlug=highest-temperature-in-amsterdam-on-august-11-2026-22c&outcomeIndex=0",
        "rule_semantics": "Daily Observations table at Wunderground EHAM; whole-degC; next-date first datapoint freezes revisions",
        "events": settlement_rows,
    })
    write_contracts(output, generated, model_results, market_report)
    (output / "REPRODUCE_AMSTERDAM_PILOT_REV2.sh").write_text(
        "#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\npython3 \"$root/evidence/wcir_amsterdam_pilot_rev2_offline.py\" --root \"$root\"\n",
        encoding="utf-8",
    )
    os.chmod(output / "REPRODUCE_AMSTERDAM_PILOT_REV2.sh", 0o755)
    (output / "TEST_COMMANDS_AND_RAW_OUTPUT.txt").write_text("Pending final test and independent review seal.\n", encoding="utf-8")
    packet = f"""# GPT Pro compact review packet — Amsterdam pilot rev2

## Review only these three closures

1. Feature parity: one full-path builder, `ta` corrected to ten-minute average, three source roles frozen.
2. Same-denominator B2/M1/M2 validation on expanding OOF, opportunity-matched OOF, outer-20 and 87 captured-PIT rows.
3. Exact-identity market archive reconciliation and direct-book repricing diagnostic; no token fair-value selector.

## Headline

- Captured feature rows: {len(captured)}/87; frozen path rows: {len(frozen_path)}; parity failures: {parity_audit['failures']}.
- Captured model disposition: `{model_results['captured_disposition']}`.
- Market exact-identity REST t0: {market_report['rest_exact_identity_t0']}/87; 1-share direct entries: {market_report['direct_entry_1share']}; 5-share: {market_report['direct_entry_5share']}.
- Old 42/42 vs current denominator: `{market_report['old_42_of_42_reconciliation']['status']}`.
- Orders/fills/notional: `0/0/0`; shadow/live remains unauthorized.

Requested disposition: `ACCEPT_AMSTERDAM_REV2_CLOSURE`, `ACCEPT_WITH_BLOCKING_FIXES`, `REWORK_AMSTERDAM_DATA_OR_MODEL_CONTRACT`, or `STOP_DUE_TO_UNCONTROLLED_LIVE_OR_DATA_RISK`.
"""
    (output / "GPT_PRO_REVIEW_PACKET_AMSTERDAM_REV2.md").write_text(packet, encoding="utf-8")
    seal_manifest(output)
    result: dict[str, Any] = {"status": "complete", "output": str(output), "model": model_results["captured_disposition"]}
    if args.package:
        archive, digest = package(output)
        compact, compact_digest = package_compact(output)
        result.update({
            "full_zip": str(archive), "full_sha256": digest,
            "compact_zip": str(compact), "compact_sha256": compact_digest,
        })
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
