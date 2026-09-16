#!/usr/bin/env python3
"""Pre-registered weather-only v2 training gate over the clean run-aware dataset.

Model fitting is intentionally fail-closed until the clean dataset contains
enough distinct settled target dates.  This prevents legacy daily cache or the
one-shot probe from silently becoming training evidence.  The registered W0
artifact is a locked legacy reference, not a frozen W1 candidate or forward
model.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_model_evaluation.d1_d2_probability import (
    attach_empirical_physical_prior,
    block_bootstrap_delta,
    fit_city_shifts,
    fit_model_bundle,
    prepare_probability_rows,
    predict_rows,
    score_model_bundle,
    serialize_bundle,
    summarize_scores,
)
from scripts.analysis.forecast_quality import research_d1_cross_city_hierarchy_v1 as legacy_base
from scripts.analysis.forecast_quality import research_d1_legacy_weather_only_robust_tail as legacy_w0
from scripts.analysis.forecast_quality import research_d1_legacy_weather_only_v2 as legacy_weather


MODELS = (
    ("A", "climatology_source_season"),
    ("B", "pooled_normal_negative_control"),
    ("C", "bias_corrected_pooled"),
    ("D", "coherent_pooled_ordinal_survival"),
    ("E", "partial_hierarchy_v2"),
    ("F", "hierarchy_plus_run_spread_lead_season"),
    ("G", "hierarchy_plus_physical_width"),
)
DEFAULT_D1_CHALLENGER_SPEC = (
    ROOT / "docs/analysis/2026-08/2026-08-05-d1-weather-only-clean-forward-freeze-v1.json"
)
LOCKED_W0_DEVELOPMENT_DATES = (
    "2026-06-17", "2026-06-18", "2026-06-19", "2026-06-20",
    "2026-06-21", "2026-06-22", "2026-06-23", "2026-06-24",
    "2026-06-25", "2026-06-26", "2026-06-27", "2026-06-28",
    "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-04",
    "2026-07-05", "2026-07-06",
)
LOCKED_W0_SECONDARY_DATES = (
    "2026-07-07", "2026-07-16", "2026-07-17", "2026-07-18",
    "2026-07-19", "2026-07-20", "2026-07-21", "2026-07-22",
    "2026-07-23",
)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_challenger_spec(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if payload.get("model_identity") != "d1_weather_only_probability_challenger":
        raise ValueError("unexpected D-1 challenger model_identity")
    if payload.get("gates", {}).get("market_residual") != "not_run_by_contract":
        raise ValueError("D-1 challenger spec must keep market residual blocked")
    return {
        "model_identity": payload["model_identity"],
        "schema_version": payload.get("schema_version"),
        "spec_sha256": hashlib.sha256(raw).hexdigest(),
        "frozen_at_utc": payload.get("frozen_at_utc"),
        "artifact_role": payload.get("artifact_role"),
    }


def build_gate(
    rows: list[dict[str, Any]],
    *,
    minimum_settled_target_dates: int,
    d1_challenger: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scoreable = [row for row in rows if row.get("oof_scoreable") is True]
    target_dates = sorted({str(row["target_date"]) for row in scoreable})
    horizons = {
        horizon: sorted({str(row["target_date"]) for row in scoreable if row.get("horizon_days_local") == horizon})
        for horizon in (1, 2)
    }
    status_by_horizon = {
        horizon: (
            "ready_for_inner_train"
            if len(horizons[horizon]) >= minimum_settled_target_dates
            else "blocked_insufficient_clean_run_aware_history"
        )
        for horizon in (1, 2)
    }
    if all(status == "ready_for_inner_train" for status in status_by_horizon.values()):
        overall_status = "ready_for_inner_train"
    elif any(status == "ready_for_inner_train" for status in status_by_horizon.values()):
        overall_status = "partially_ready_by_horizon"
    else:
        overall_status = "blocked_insufficient_clean_run_aware_history"
    model_rows = [
        {
            "horizon_days_local": horizon,
            "model_code": code,
            "model_id": model_id,
            "status": status_by_horizon[horizon],
            "exact_bracket_logloss": None,
            "rung_brier": None,
            "rps": None,
            "winner_probability_mean": None,
            "calibration": None,
            "forward_status": "not_started",
        }
        for horizon in (1, 2)
        for code, model_id in MODELS
    ]
    model_rows.append(
        {
            "horizon_days_local": 1,
            "model_code": "L0",
            "model_id": d1_challenger["model_identity"],
            "status": "locked_reference_only",
            "exact_bracket_logloss": None,
            "rung_brier": None,
            "rps": None,
            "winner_probability_mean": None,
            "calibration": None,
            "forward_status": "reference_only_not_forward_candidate",
        }
    )
    training_phase_by_horizon = {
        str(horizon): (
            "inner_train_and_blocked_target_date_validation"
            if status_by_horizon[horizon] == "ready_for_inner_train"
            else "clean_development_accumulation"
        )
        for horizon in (1, 2)
    }
    learning_curve_by_horizon: dict[str, dict[str, Any]] = {}
    for horizon in (1, 2):
        observed = len(horizons[horizon])
        milestones = sorted(
            {minimum_settled_target_dates}
            | {milestone for milestone in (7, 14, 21) if milestone < minimum_settled_target_dates}
        )
        next_milestone = next(
            (milestone for milestone in milestones if observed < milestone),
            None,
        )
        learning_curve_by_horizon[str(horizon)] = {
            "settled_target_dates": observed,
            "diagnostic_milestones": [7, 14, 21],
            "formal_inner_train_milestone": minimum_settled_target_dates,
            "next_milestone": next_milestone,
            "status": (
                "formal_inner_train_ready"
                if next_milestone is None
                else "diagnostic_accumulating"
            ),
        }
    summary = {
        "schema_version": "d1_d2_weather_only_v2_preregistered_gate",
        "weather_only_status": overall_status,
        "weather_only_status_by_horizon": {str(key): value for key, value in status_by_horizon.items()},
        "scoreable_rows": len(scoreable),
        "distinct_scoreable_target_dates": len(target_dates),
        "scoreable_target_dates_by_horizon": {str(key): len(value) for key, value in horizons.items()},
        "minimum_settled_target_dates_per_horizon": minimum_settled_target_dates,
        "d1_frozen_challenger": d1_challenger,
        "d1_locked_w0_reference": d1_challenger,
        "training_phase_by_horizon": training_phase_by_horizon,
        "learning_curve_by_horizon": learning_curve_by_horizon,
        "w1_freeze_status": "not_frozen_pending_clean_development_results",
        "untouched_forward_status": "not_started_until_w1_freeze_timestamp",
        "d1_blocked_by_d2": False,
        "legacy_daily_cache_used": False,
        "estimated_run_timestamp_used": False,
        "market_features_used": False,
        "market_residual_status": "not_run_by_contract",
        "production_action": "none",
    }
    return model_rows, summary


def _date_equal_arm_logloss(scored: pd.DataFrame) -> dict[str, float]:
    daily = scored.groupby(["lead_days", "target_date", "arm"])["logloss"].mean().reset_index()
    by_lead = daily.groupby(["lead_days", "arm"])["logloss"].mean().reset_index()
    return {
        str(arm): float(group["logloss"].mean())
        for arm, group in by_lead.groupby("arm")
    }


def _locked_w0_brackets(labels: list[Any]) -> tuple[list[legacy_base.Bracket], str]:
    """Translate a native exact ladder into the locked W0 bracket contract."""
    clean = [
        str(value).replace("°F", "").replace("°C", "").replace("°", "").strip()
        for value in labels
    ]
    unit = "F" if any("-" in label[1:] for label in clean[1:-1]) else "C"
    brackets: list[legacy_base.Bracket] = []
    for index, label in enumerate(clean):
        values = [float(value) for value in re.findall(r"(?<![\d.])-?\d+(?:\.\d+)?", label)]
        if not values:
            raise ValueError(f"unparseable locked-W0 bracket={label!r}")
        if index == 0:
            brackets.append(legacy_base.Bracket(label, None, values[-1], True, False))
        elif index == len(clean) - 1:
            brackets.append(legacy_base.Bracket(label, values[0], None, False, True))
        elif len(values) >= 2:
            brackets.append(legacy_base.Bracket(label, values[0], values[1], False, False))
        else:
            brackets.append(legacy_base.Bracket(label, values[0], values[0], False, False))
    legacy_base.validate_ladder(brackets)
    return brackets, unit


def attach_locked_w0_robust_tail_prior(
    raw_probability_rows: pd.DataFrame,
    multimodel_forecasts: pd.DataFrame,
    history: pd.DataFrame,
    *,
    maximum_asof_lag_hours: float = 12.0,
) -> pd.DataFrame:
    """Attach the historical locked W0 D-1 distribution without future runs.

    The old W0 was defined only for D-1. D-2 stays on incumbent telemetry in
    the hybrid base arm. Reconstructed multi-model batches are joined strictly
    as-of the probability checkpoint; a later decision is never backfilled.
    """
    required = {"city", "target_date", "snapshot_ts_utc", "lead_days", "brackets_json"}
    missing = sorted(required - set(raw_probability_rows.columns))
    if missing:
        raise ValueError(f"probability rows missing locked-W0 columns: {missing}")
    forecast_required = {
        "snapshot_key",
        "city",
        "target_date",
        "decision_time_utc",
        "model_key",
        "forecast_max_f",
    }
    missing = sorted(forecast_required - set(multimodel_forecasts.columns))
    if missing:
        raise ValueError(f"multimodel forecasts missing columns: {missing}")

    historical = history.copy()
    historical["is_best_model"] = (
        historical["is_best_model"].astype(str).str.lower().isin(["true", "1"])
    )
    historical["month_num"] = pd.to_datetime(historical["date"]).dt.month
    test_start = str(raw_probability_rows["target_date"].astype(str).min())
    fitted = legacy_weather.fit_legacy_history_slice(
        historical,
        test_start,
        history_policy="summer_best",
    )

    forecasts = multimodel_forecasts.copy()
    forecasts["decision_time_utc"] = pd.to_datetime(
        forecasts["decision_time_utc"], utc=True, errors="coerce"
    )
    forecasts["forecast_max_f"] = pd.to_numeric(forecasts["forecast_max_f"], errors="coerce")
    forecasts = forecasts[
        forecasts["decision_time_utc"].notna() & np.isfinite(forecasts["forecast_max_f"])
    ].copy()
    batches: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for snapshot_key, group in forecasts.groupby("snapshot_key", sort=False):
        identities = group[["city", "target_date", "decision_time_utc"]].drop_duplicates()
        if len(identities) != 1:
            raise ValueError(f"multimodel snapshot identity is not unique: {snapshot_key}")
        identity = identities.iloc[0]
        model_values = {
            str(row.model_key): float(row.forecast_max_f)
            for row in group.itertuples(index=False)
        }
        batches.setdefault((str(identity.city), str(identity.target_date)), []).append(
            {
                "decision_time_utc": identity.decision_time_utc,
                "model_values_f": model_values,
            }
        )
    for values in batches.values():
        values.sort(key=lambda row: row["decision_time_utc"])

    output = raw_probability_rows.copy()
    w0_vectors: list[str | None] = []
    asof_lags: list[float] = []
    blockers: dict[str, int] = {}

    def block(reason: str) -> None:
        blockers[reason] = blockers.get(reason, 0) + 1
        w0_vectors.append(None)

    model_key_by_family = {"gfs": "gfs_global", "ecmwf": "ecmwf_ifs025"}
    for row in output.itertuples(index=False):
        if int(row.lead_days) != 1:
            block("w0_not_defined_for_d2")
            continue
        checkpoint = pd.to_datetime(row.snapshot_ts_utc, utc=True, errors="coerce")
        if pd.isna(checkpoint):
            block("invalid_probability_checkpoint_clock")
            continue
        candidates = [
            batch
            for batch in batches.get((str(row.city), str(row.target_date)), [])
            if batch["decision_time_utc"] <= checkpoint
        ]
        if not candidates:
            block("no_prior_multimodel_batch")
            continue
        batch = candidates[-1]
        lag_hours = float((checkpoint - batch["decision_time_utc"]).total_seconds() / 3600.0)
        if lag_hours < 0 or lag_hours > maximum_asof_lag_hours:
            block("multimodel_batch_too_old")
            continue
        spec = fitted["specs"].get(str(row.city))
        if spec is None:
            block("missing_locked_w0_city_spec")
            continue
        assigned_key = model_key_by_family.get(str(spec["model"]).lower())
        model_values = batch["model_values_f"]
        if assigned_key not in model_values or len(model_values) < 2:
            block("assigned_or_ensemble_model_missing")
            continue
        try:
            brackets, unit = _locked_w0_brackets(json.loads(row.brackets_json))
        except (TypeError, ValueError):
            # These rows are also rejected by prepare_probability_rows.  Keep
            # the coverage loss explicit instead of mislabelling it as a W0
            # model failure.
            block("non_native_ladder_order_or_gap")
            continue
        try:
            state = {
                "city": str(row.city),
                "target_date": str(row.target_date),
                "market_unit": unit,
                "brackets": brackets,
                "forecast_max_f": float(model_values[assigned_key]),
                "ensemble_mean_f": float(np.mean(list(model_values.values()))),
            }
            vector = legacy_w0.robust_tail_vector(
                state,
                fitted,
                ensemble_weight=0.875,
                scale_temperature=1.25,
                climate_mix=0.02,
                consensus_stat="mean",
                bias_multiplier=1.0,
            )
        except (KeyError, TypeError, ValueError) as exc:
            block(f"locked_w0_vector_error:{type(exc).__name__}")
            continue
        w0_vectors.append(json.dumps(vector.tolist(), separators=(",", ":")))
        asof_lags.append(lag_hours)

    output["w0_robust_tail_probs_json"] = w0_vectors
    available = output["w0_robust_tail_probs_json"].notna()
    output.attrs["locked_w0"] = {
        "lineage": "single_run_reconstructed_conservative_12h_lag",
        "selection_dates": list(LOCKED_W0_DEVELOPMENT_DATES),
        "previously_viewed_secondary_dates": list(LOCKED_W0_SECONDARY_DATES),
        "parameters": {
            "consensus_stat": "mean",
            "ensemble_weight": 0.875,
            "bias_multiplier": 1.0,
            "residual_scale": 1.25,
            "climatology_mix": 0.02,
            "kernel_sd_f": legacy_weather.KERNEL_SD_F,
        },
        "d1_available_rows": int(available.sum()),
        "d1_available_dates": int(output.loc[available, "target_date"].nunique()),
        "maximum_asof_lag_hours": float(maximum_asof_lag_hours),
        "median_asof_lag_hours": float(np.median(asof_lags)) if asof_lags else None,
        "blockers": blockers,
    }
    return output


def _market_offset_score(
    scored: pd.DataFrame,
    *,
    weather_arm: str,
    beta_by_lead: dict[int, float],
    output_arm: str = "market_weather_residual",
) -> pd.DataFrame:
    weather = scored[scored["arm"] == weather_arm].set_index("source_index")
    market = scored[scored["arm"] == "market"].set_index("source_index")
    common = weather.index.intersection(market.index)
    records: list[dict[str, Any]] = []
    for source_index in common:
        w = weather.loc[source_index]
        m = market.loc[source_index]
        weather_probability = pd.Series(json.loads(w["probabilities_json"]), dtype=float).to_numpy()
        market_probability = pd.Series(json.loads(m["probabilities_json"]), dtype=float).to_numpy()
        beta = float(beta_by_lead[int(w["lead_days"])])
        if beta == 0.0:
            probability = market_probability.copy()
        elif beta == 1.0:
            probability = weather_probability.copy()
        else:
            probability = np.exp(
                (1.0 - beta) * np.log(market_probability.clip(1e-9, 1.0))
                + beta * np.log(weather_probability.clip(1e-9, 1.0))
            )
            probability /= probability.sum()
        winner = int(w["winner_index"])
        target = np.zeros(len(probability), dtype=float)
        target[winner] = 1.0
        cumulative_error = np.cumsum(probability)[:-1] - np.cumsum(target)[:-1]
        records.append(
            {
                "source_index": int(source_index),
                "city": str(w["city"]),
                "target_date": str(w["target_date"]),
                "lead_days": int(w["lead_days"]),
                "arm": output_arm,
                "logloss": float(-np.log(max(1e-9, probability[winner]))),
                "brier": float(np.square(probability - target).mean()),
                "rps": float(np.square(cumulative_error).mean()),
                "winner_probability": float(probability[winner]),
                "top1_accuracy": float(int(np.argmax(probability) == winner)),
                "probabilities_json": json.dumps(probability.tolist(), separators=(",", ":")),
                "winner_index": winner,
            }
        )
    return pd.DataFrame(records)


def _select_market_offset(
    scored: pd.DataFrame,
    *,
    weather_arm: str,
) -> tuple[str, dict[int, float], list[dict[str, Any]]]:
    candidates = (0.0, 0.025, 0.05, 0.10, 0.20, 0.35, 0.50, 0.75, 1.0)
    leads = sorted(int(value) for value in scored["lead_days"].unique())
    rows: list[dict[str, Any]] = []
    for beta in candidates:
        posterior = _market_offset_score(
            scored,
            weather_arm=weather_arm,
            beta_by_lead={lead: beta for lead in leads},
        )
        loss = float(
            posterior.groupby(["lead_days", "target_date"])["logloss"].mean()
            .groupby("lead_days").mean().mean()
        )
        rows.append({"mode": "shared", "lead_days": "all", "beta": beta, "logloss": loss})
    shared_best = min((row for row in rows if row["mode"] == "shared"), key=lambda row: row["logloss"])
    lead_betas: dict[int, float] = {}
    for lead in leads:
        lead_scored = scored[scored["lead_days"] == lead]
        for beta in candidates:
            posterior = _market_offset_score(
                lead_scored,
                weather_arm=weather_arm,
                beta_by_lead={lead: beta},
            )
            loss = float(posterior.groupby("target_date")["logloss"].mean().mean())
            rows.append({"mode": "lead_specific", "lead_days": lead, "beta": beta, "logloss": loss})
        lead_betas[lead] = float(
            min(
                (row for row in rows if row["mode"] == "lead_specific" and row["lead_days"] == lead),
                key=lambda row: row["logloss"],
            )["beta"]
        )
    lead_posterior = _market_offset_score(
        scored,
        weather_arm=weather_arm,
        beta_by_lead=lead_betas,
    )
    lead_loss = float(
        lead_posterior.groupby(["lead_days", "target_date"])["logloss"].mean()
        .groupby("lead_days").mean().mean()
    )
    if lead_loss < float(shared_best["logloss"]):
        return "lead_specific", lead_betas, rows
    shared_beta = float(shared_best["beta"])
    return "shared", {lead: shared_beta for lead in leads}, rows


def run_legacy_shared_training(
    probability_path: Path,
    output_dir: Path,
    *,
    development_end: str,
    shared_holdout_end: str,
    event_rungs_path: Path | None = None,
    history_path: Path | None = None,
    multimodel_forecasts_path: Path | None = None,
) -> dict[str, Any]:
    """Train the shared D-1/D-2 structure on the recovered PIT-like panel.

    This is a development/secondary-holdout model, not the strict provider-run
    frozen forward.  The market vector is used only as a same-row score
    baseline.  Dates after the common D-2 holdout are retained as a D-1-only
    temporal stress test.
    """

    raw = pd.read_csv(probability_path)
    input_files = {
        "probability_rows": probability_path,
        "event_rungs": event_rungs_path,
        "historical_errors": history_path,
        "multimodel_forecasts": multimodel_forecasts_path,
    }

    def input_identity(path: Path | None) -> dict[str, Any] | None:
        if path is None:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "path": str(path),
            "sha256": digest.hexdigest(),
            "bytes": path.stat().st_size,
        }
    history_metadata: dict[str, Any] | None = None
    locked_w0_metadata: dict[str, Any] | None = None
    history: pd.DataFrame | None = None
    if event_rungs_path is not None and history_path is not None:
        event_rungs = pd.read_csv(
            event_rungs_path,
            usecols=["snapshot_id", "forecast_max_f", "forecast_model"],
            low_memory=False,
        )
        history = pd.read_csv(history_path)
        raw = attach_empirical_physical_prior(raw, event_rungs, history)
        history_metadata = dict(raw.attrs.get("historical_error_bank") or {})
    if multimodel_forecasts_path is not None:
        if history_path is None:
            raise ValueError("--history is required with --multimodel-forecasts")
        if history is None:
            history = pd.read_csv(history_path)
        raw = attach_locked_w0_robust_tail_prior(
            raw,
            pd.read_csv(multimodel_forecasts_path),
            history,
        )
        locked_w0_metadata = dict(raw.attrs.get("locked_w0") or {})
    rows = prepare_probability_rows(raw)
    native_ladder_scoreable_rows = int(len(rows))
    empirical_prior_available_rows = 0
    locked_w0_available_rows = 0
    base_frames = {"legacy_telemetry": rows}
    if "physical_probs" in rows and rows["physical_probs"].notna().any():
        physical = rows[rows["physical_probs"].notna()].copy()
        empirical_prior_available_rows = int(len(physical))
        physical["model_probs"] = physical["physical_probs"]
        base_frames["empirical_physical"] = physical
    if "w0_robust_tail_probs" in rows and rows["w0_robust_tail_probs"].notna().any():
        # W0 is a D-1 model. The combined base keeps D-2 incumbent telemetry,
        # then lets the shared/lead calibrators decide whether that hybrid is
        # superior to a common telemetry base.
        w0_hybrid = rows[
            (rows["lead_days"] == 2) | rows["w0_robust_tail_probs"].notna()
        ].copy()
        locked_w0_available_rows = int(
            ((w0_hybrid["lead_days"] == 1) & w0_hybrid["w0_robust_tail_probs"].notna()).sum()
        )
        w0_hybrid["model_probs"] = [
            w0 if int(lead) == 1 else legacy
            for lead, w0, legacy in zip(
                w0_hybrid["lead_days"],
                w0_hybrid["w0_robust_tail_probs"],
                w0_hybrid["model_probs"],
            )
        ]
        base_frames["locked_w0_d1_legacy_d2"] = w0_hybrid

    # Fixed-denominator comparison across every enabled base model.
    common_sources = set.intersection(
        *(set(frame["source_index"]) for frame in base_frames.values())
    )
    base_frames = {
        name: frame[frame["source_index"].isin(common_sources)].reset_index(drop=True)
        for name, frame in base_frames.items()
    }
    rows = base_frames["legacy_telemetry"]
    development = rows[rows["target_date"] <= development_end].copy()
    shared_holdout = rows[
        (rows["target_date"] > development_end)
        & (rows["target_date"] <= shared_holdout_end)
    ].copy()
    late_d1 = rows[
        (rows["lead_days"] == 1) & (rows["target_date"] > shared_holdout_end)
    ].copy()
    if development["lead_days"].nunique() != 2 or shared_holdout["lead_days"].nunique() != 2:
        raise ValueError("development and shared holdout must both contain D-1 and D-2")

    development_dates = sorted(development["target_date"].unique())
    if len(development_dates) < 20:
        raise ValueError("insufficient development dates for inner validation")
    inner_cut = development_dates[-7]
    inner_train = development[development["target_date"] < inner_cut].copy()
    inner_validation = development[development["target_date"] >= inner_cut].copy()

    selection_rows: list[dict[str, Any]] = []
    weather_arms = (
        "raw_weather",
        "shared_calibration",
        "lead_calibration",
        "lead_partial_city",
    )
    for base_model, base_rows in base_frames.items():
        base_inner_train = base_rows[base_rows["source_index"].isin(set(inner_train["source_index"]))].copy()
        base_inner_validation = base_rows[base_rows["source_index"].isin(set(inner_validation["source_index"]))].copy()
        inner_base_bundle = fit_model_bundle(base_inner_train, city_shrinkage=60.0)
        for shrinkage in (30.0, 60.0, 120.0):
            inner_bundle = dict(inner_base_bundle)
            inner_bundle["city_shrinkage"] = shrinkage
            inner_bundle["city_shifts"] = fit_city_shifts(
                base_inner_train,
                inner_bundle["lead_parameters"],
                shrinkage=shrinkage,
            )
            inner_scored = score_model_bundle(base_inner_validation, inner_bundle)
            losses = _date_equal_arm_logloss(inner_scored)
            for arm in weather_arms:
                selection_rows.append(
                    {
                        "base_model": base_model,
                        "city_shrinkage": shrinkage,
                        "arm": arm,
                        "inner_validation_logloss": losses[arm],
                    }
                )
    selection = pd.DataFrame(selection_rows)
    selected = selection.sort_values(
        ["inner_validation_logloss", "base_model", "city_shrinkage", "arm"]
    ).iloc[0]
    selected_arm = str(selected["arm"])
    selected_base_model = str(selected["base_model"])
    selected_shrinkage = float(selected["city_shrinkage"])

    base_selected_specs = {
        str(base_model): group.sort_values(
            ["inner_validation_logloss", "city_shrinkage", "arm"]
        ).iloc[0]
        for base_model, group in selection.groupby("base_model")
    }
    candidate_bundles: dict[str, dict[str, Any]] = {}
    candidate_output_arms: dict[str, str] = {}
    for base_model, base_spec in base_selected_specs.items():
        base_development = base_frames[base_model]
        base_development = base_development[
            base_development["source_index"].isin(set(development["source_index"]))
        ].copy()
        candidate_bundles[base_model] = fit_model_bundle(
            base_development,
            city_shrinkage=float(base_spec["city_shrinkage"]),
        )
        candidate_output_arms[base_model] = (
            f"candidate:{base_model}:{str(base_spec['arm'])}"
        )

    selected_inner_rows = base_frames[selected_base_model]
    selected_inner_train = selected_inner_rows[
        selected_inner_rows["source_index"].isin(set(inner_train["source_index"]))
    ].copy()
    selected_inner_validation = selected_inner_rows[
        selected_inner_rows["source_index"].isin(set(inner_validation["source_index"]))
    ].copy()
    selected_inner_bundle = fit_model_bundle(
        selected_inner_train,
        city_shrinkage=selected_shrinkage,
    )
    selected_inner_scored = score_model_bundle(
        selected_inner_validation,
        selected_inner_bundle,
    )
    offset_mode, beta_by_lead, offset_selection_rows = _select_market_offset(
        selected_inner_scored,
        weather_arm=selected_arm,
    )

    selected_rows = base_frames[selected_base_model]
    selected_development = selected_rows[selected_rows["source_index"].isin(set(development["source_index"]))].copy()
    bundle = fit_model_bundle(selected_development, city_shrinkage=selected_shrinkage)
    artifact = serialize_bundle(bundle)
    artifact.update(
        {
            "selected_arm": selected_arm,
            "selected_base_model": selected_base_model,
            "selection_metric": "lead_equal_target_date_equal_exact_bracket_logloss",
            "training_lineage": "legacy_strategy_snapshot_probability_panel_recovered_union",
            "strict_provider_run_forward": False,
            "development_end": development_end,
            "shared_holdout_end": shared_holdout_end,
        }
    )

    scored_slices: list[pd.DataFrame] = []
    score_tables: list[pd.DataFrame] = []
    delta_rows: list[dict[str, Any]] = []
    for slice_name, frame in (
        ("legacy_w0_seen_shared_compatibility", shared_holdout),
        ("legacy_w0_seen_d1_compatibility", late_d1),
    ):
        if frame.empty:
            continue
        selected_frame = selected_rows[selected_rows["source_index"].isin(set(frame["source_index"]))].copy()
        scored = score_model_bundle(selected_frame, bundle)
        rename = {
            "raw_weather": f"{selected_base_model}_raw",
            "shared_calibration": f"{selected_base_model}_shared_calibration",
            "lead_calibration": f"{selected_base_model}_lead_calibration",
            "lead_partial_city": f"{selected_base_model}_lead_partial_city",
        }
        scored["arm"] = scored["arm"].replace(rename)
        selected_output_arm = rename[selected_arm]
        posterior = _market_offset_score(
            scored,
            weather_arm=selected_output_arm,
            beta_by_lead=beta_by_lead,
        )
        scored = pd.concat([scored, posterior], ignore_index=True)
        if selected_base_model != "legacy_telemetry":
            incumbent_frame = base_frames["legacy_telemetry"]
            incumbent_frame = incumbent_frame[
                incumbent_frame["source_index"].isin(set(selected_frame["source_index"]))
            ]
            incumbent = predict_rows(incumbent_frame, arm="raw_weather")
            incumbent["arm"] = "legacy_telemetry_raw"
            scored = pd.concat([scored, incumbent], ignore_index=True)
        for base_model, base_spec in base_selected_specs.items():
            candidate_frame = base_frames[base_model]
            candidate_frame = candidate_frame[
                candidate_frame["source_index"].isin(set(frame["source_index"]))
            ].copy()
            candidate = score_model_bundle(
                candidate_frame,
                candidate_bundles[base_model],
            )
            candidate = candidate[candidate["arm"] == str(base_spec["arm"])].copy()
            candidate["arm"] = candidate_output_arms[base_model]
            scored = pd.concat([scored, candidate], ignore_index=True)
        scored["evaluation_slice"] = slice_name
        scored_slices.append(scored)
        table = summarize_scores(scored)
        table["evaluation_slice"] = slice_name
        score_tables.append(table)
        for baseline in ("raw_weather", "market"):
            baseline_arm = (
                f"{selected_base_model}_raw" if baseline == "raw_weather" else baseline
            )
            for delta in block_bootstrap_delta(scored, left=selected_output_arm, right=baseline_arm):
                delta["evaluation_slice"] = slice_name
                delta_rows.append(delta)
        for delta in block_bootstrap_delta(
            scored,
            left="market_weather_residual",
            right="market",
        ):
            delta["evaluation_slice"] = slice_name
            delta_rows.append(delta)
        for candidate_arm in candidate_output_arms.values():
            for delta in block_bootstrap_delta(
                scored,
                left=candidate_arm,
                right="market",
            ):
                delta["evaluation_slice"] = slice_name
                delta_rows.append(delta)
        if "locked_w0_d1_legacy_d2" in candidate_output_arms:
            for delta in block_bootstrap_delta(
                scored,
                left=candidate_output_arms["locked_w0_d1_legacy_d2"],
                right=candidate_output_arms["legacy_telemetry"],
            ):
                delta["evaluation_slice"] = slice_name
                delta_rows.append(delta)

    all_scored = pd.concat(scored_slices, ignore_index=True)
    score_table = pd.concat(score_tables, ignore_index=True)
    deltas = pd.DataFrame(delta_rows)
    selected_compatibility = score_table[
        (score_table["evaluation_slice"] == "legacy_w0_seen_shared_compatibility")
        & (score_table["arm"] == selected_output_arm)
    ]
    market_compatibility = score_table[
        (score_table["evaluation_slice"] == "legacy_w0_seen_shared_compatibility")
        & (score_table["arm"] == "market")
    ]
    candidate_compatibility = score_table[
        (score_table["evaluation_slice"] == "legacy_w0_seen_shared_compatibility")
        & score_table["arm"].isin(candidate_output_arms.values())
    ]
    residual_compatibility_evidence = deltas[
        (deltas["evaluation_slice"] == "legacy_w0_seen_shared_compatibility")
        & (deltas["left"] == "market_weather_residual")
        & (deltas["right"] == "market")
    ].copy()
    nonzero_residual_leads = {
        int(lead) for lead, beta in beta_by_lead.items() if float(beta) > 0.0
    }
    confirmed_residual_leads = set(
        residual_compatibility_evidence.loc[
            residual_compatibility_evidence["ci_high"] < 0.0, "lead_days"
        ].astype(int)
    )
    if not nonzero_residual_leads:
        market_residual_status = "development_rejected_beta_zero_exact_market"
    elif nonzero_residual_leads <= confirmed_residual_leads:
        market_residual_status = "legacy_compatibility_replay_predictive_information_only"
    else:
        market_residual_status = "no_confirmed_market_residual_on_legacy_compatibility_replay"

    d1_evaluation_dates = set(
        rows.loc[rows["lead_days"] == 1, "target_date"].astype(str)
    ) - set(development["target_date"].astype(str))
    locked_w0_seen_dates = set(LOCKED_W0_DEVELOPMENT_DATES) | set(LOCKED_W0_SECONDARY_DATES)
    d1_seen_overlap = sorted(d1_evaluation_dates & locked_w0_seen_dates)

    summary = {
        "schema_version": "d1_d2_weather_only_v2_training_result_v1",
        "model_identity": "d1_d2_weather_only_probability",
        "selected_arm": selected_arm,
        "selected_output_arm": selected_output_arm,
        "selected_base_model": selected_base_model,
        "selected_city_shrinkage": selected_shrinkage,
        "candidate_output_arms": candidate_output_arms,
        "market_offset": {
            "mode": offset_mode,
            "beta_by_lead": {str(key): value for key, value in beta_by_lead.items()},
            "inner_selection": offset_selection_rows,
            "meaning": "beta=0_is_exact_market_beta=1_is_weather_only",
        },
        "market_features_used_for_weather_model": False,
        "input": str(probability_path),
        "input_identities": {
            name: input_identity(path) for name, path in input_files.items()
        },
        "denominator_scope": "recovered_strategy_snapshot_complete_D1_D2_ladders_with_settlement_and_same_row_market",
        "raw_rows": int(len(raw)),
        "scoreable_rows": int(len(rows)),
        "signal_funnel": {
            "raw_probability_rows": int(len(raw)),
            "native_ladder_and_winner_scoreable": native_ladder_scoreable_rows,
            "empirical_physical_prior_available": empirical_prior_available_rows,
            "locked_w0_d1_available": locked_w0_available_rows,
            "common_model_comparison": int(len(rows)),
        },
        "coverage": {
            str(lead): {
                "states": int(len(group)),
                "dates": int(group["target_date"].nunique()),
                "cities": int(group["city"].nunique()),
                "start": str(group["target_date"].min()),
                "end": str(group["target_date"].max()),
            }
            for lead, group in rows.groupby("lead_days")
        },
        "historical_error_bank": history_metadata,
        "locked_w0": locked_w0_metadata,
        "splits": {
            "development": {
                "end": development_end,
                "states": int(len(development)),
                "dates": int(development["target_date"].nunique()),
            },
            "legacy_w0_seen_shared_compatibility": {
                "start_exclusive": development_end,
                "end": shared_holdout_end,
                "states": int(len(shared_holdout)),
                "dates": int(shared_holdout["target_date"].nunique()),
            },
            "legacy_w0_seen_d1_compatibility": {
                "start_exclusive": shared_holdout_end,
                "states": int(len(late_d1)),
                "dates": int(late_d1["target_date"].nunique()),
            },
            "locked_w0_d1_evaluation_overlap": {
                "evaluation_dates": len(d1_evaluation_dates),
                "previously_used_or_viewed_dates": len(d1_seen_overlap),
                "dates": d1_seen_overlap,
                "untouched_forward_dates": len(d1_evaluation_dates - locked_w0_seen_dates),
            },
        },
        "selection": selection.to_dict("records"),
        "parameters": artifact,
        "selected_compatibility_scores": selected_compatibility.to_dict("records"),
        "candidate_compatibility_scores": candidate_compatibility.to_dict("records"),
        "market_compatibility_scores": market_compatibility.to_dict("records"),
        "market_residual_compatibility_evidence": residual_compatibility_evidence.to_dict("records"),
        "weather_only_status": "legacy_compatibility_replay_only_exact_run_forward_insufficient",
        "market_residual_status": market_residual_status,
        "production_action": "none",
        "orders_changed": 0,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    selection.to_csv(output_dir / "inner_model_selection.csv", index=False)
    pd.DataFrame(offset_selection_rows).to_csv(
        output_dir / "inner_market_offset_selection.csv", index=False
    )
    score_table.to_csv(output_dir / "model_score_table.csv", index=False)
    deltas.to_csv(output_dir / "paired_target_date_bootstrap.csv", index=False)
    all_scored.to_csv(output_dir / "scored_probability_rows.csv", index=False)
    (output_dir / "model_artifact.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--probability-rows", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--minimum-settled-target-dates", type=int, default=30)
    parser.add_argument("--d1-frozen-challenger-spec", type=Path, default=DEFAULT_D1_CHALLENGER_SPEC)
    parser.add_argument("--development-end", default="2026-06-12")
    parser.add_argument("--shared-holdout-end", default="2026-06-20")
    parser.add_argument("--event-rungs", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--multimodel-forecasts", type=Path)
    args = parser.parse_args(argv)
    if args.probability_rows is not None:
        summary = run_legacy_shared_training(
            args.probability_rows,
            args.output_dir,
            development_end=args.development_end,
            shared_holdout_end=args.shared_holdout_end,
            event_rungs_path=args.event_rungs,
            history_path=args.history,
            multimodel_forecasts_path=args.multimodel_forecasts,
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    if args.dataset is None:
        raise SystemExit("--dataset or --probability-rows is required")
    rows = _jsonl(args.dataset)
    model_rows, summary = build_gate(
        rows,
        minimum_settled_target_dates=args.minimum_settled_target_dates,
        d1_challenger=load_challenger_spec(args.d1_frozen_challenger_spec),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "model_score_table.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(model_rows[0]))
        writer.writeheader()
        writer.writerows(model_rows)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
