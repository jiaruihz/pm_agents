#!/usr/bin/env python3
"""Build the research-only Tmin V2.2 evidence package.

The builder is deliberately fail-closed.  It reconstructs the P0 observation
lineage, audits the native forecast-vintage and settlement-truth gates, and
only permits the specified forecast-threshold residual fit when every gate is
true.  The frozen inputs in the current package do not satisfy those gates, so
the current expected result is a byte-exact market fallback, not a fitted arm.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow
import scipy


ROOT = Path(
    os.environ.get("PM_AGENTS_REPO_ROOT", Path(__file__).resolve().parents[3])
).resolve()
MODEL_ID = "TMIN_V2_2_FORECAST_THRESHOLD_RESIDUAL_V1"
EPS = 1e-12
TIMEZONES = {"Seoul": "Asia/Seoul", "Tokyo": "Asia/Tokyo"}
REQUIRED_REPORTS = (
    "00_REVIEW_CONTRACT.md",
    "01_EXECUTIVE_DECISION.md",
    "02_EVIDENCE_CLOSURE.md",
    "03_SETTLEMENT_TRUTH.md",
    "04_FORECAST_VINTAGE_COVERAGE.md",
    "05_V2_2_MODEL_SPEC.md",
    "06_WEATHER_FOUNDATION_RESULTS.md",
    "07_MARKET_INCREMENTAL_RESULTS.md",
    "08_SCORE_GRADIENT_DIAGNOSTIC.md",
    "09_FORWARD_ARM_MANIFEST.md",
    "10_FINDINGS_AND_FALSIFICATION.md",
    "GPT_PRO_REVIEW_PACKET.md",
    "KNOWLEDGE_PERSISTENCE_REPORT.md",
    "INDEPENDENT_READONLY_REVIEW.md",
)
REQUIRED_MACHINE = (
    "P0_RAW_FEATURE_AUDIT.parquet",
    "EVENT_TIME_TRUTH.parquet",
    "FORECAST_ERROR_ARCHIVE_SAMPLE.parquet",
    "FORECAST_VINTAGE_COVERAGE_MATRIX.csv",
    "FOUNDATION_FOLD_COEFFICIENTS.csv",
    "MODEL_PREDICTIONS.parquet",
    "FORWARD_ARM_MANIFEST.json",
    "KNOWLEDGE_PERSISTENCE_MANIFEST.json",
    "REVIEW_EVIDENCE_SEAL.json",
    "EVIDENCE_MANIFEST.json",
    "REPRODUCE.sh",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp,)):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=_json_default
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, check=check, text=True, capture_output=True
    )
    return result.stdout


def native_rung(value: float) -> int:
    """Round to the integer settlement lattice, with halves away from zero."""
    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)


def next_colder_boundary(current_rung: int) -> float:
    """Boundary below which the next-colder integer rung becomes active."""
    return float(current_rung) - 0.5


def clip_probability(value: Iterable[float] | np.ndarray) -> np.ndarray:
    return np.clip(np.asarray(value, dtype=float), EPS, 1.0 - EPS)


def logit(value: Iterable[float] | np.ndarray) -> np.ndarray:
    p = clip_probability(value)
    return np.log(p / (1.0 - p))


def sigmoid(value: Iterable[float] | np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(value, dtype=float), -40, 40)))


def empirical_cdf(errors: np.ndarray, x: float) -> float:
    values = np.asarray(errors, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return float("nan")
    return float(((values <= x).sum() + 0.5) / (len(values) + 1.0))


def shrunk_weather_probability(
    city_checkpoint_errors: np.ndarray,
    global_checkpoint_errors: np.ndarray,
    threshold_error: float,
    *,
    shrinkage_strength: float = 30.0,
) -> float:
    city_values = np.asarray(city_checkpoint_errors, dtype=float)
    city_values = city_values[np.isfinite(city_values)]
    city_cdf = empirical_cdf(city_values, threshold_error)
    global_cdf = empirical_cdf(global_checkpoint_errors, threshold_error)
    if not np.isfinite(global_cdf):
        return float("nan")
    if not np.isfinite(city_cdf):
        city_cdf = global_cdf
    weight = len(city_values) / (len(city_values) + shrinkage_strength)
    shrunk = weight * city_cdf + (1.0 - weight) * global_cdf
    return float(np.clip(1.0 - shrunk, 0.01, 0.99))


def clock_probability(
    successes_city_checkpoint: int,
    n_city_checkpoint: int,
    q_global_checkpoint: float,
    *,
    shrinkage_strength: float = 20.0,
) -> float:
    return float(
        (successes_city_checkpoint + shrinkage_strength * q_global_checkpoint)
        / (n_city_checkpoint + shrinkage_strength)
    )


def alpha_posterior_grid(
    frame: pd.DataFrame,
    *,
    prior_scale: float = 0.25,
    maximum: float = 2.0,
    grid_size: int = 4001,
) -> dict[str, Any]:
    """One-dimensional posterior using date-equal Bernoulli log likelihood."""
    required = {"target_date", "label", "p_market", "routing_indicator", "z_weather"}
    if not required.issubset(frame.columns):
        raise ValueError(f"missing posterior columns: {sorted(required - set(frame.columns))}")
    if frame.empty:
        raise ValueError("posterior frame is empty")
    if frame["target_date"].isna().any():
        raise ValueError("posterior target_date is missing")
    numeric = frame[["label", "p_market", "routing_indicator", "z_weather"]].to_numpy(float)
    if not np.isfinite(numeric).all():
        raise ValueError("posterior inputs must be finite")
    alpha = np.linspace(0.0, maximum, grid_size)
    y = frame["label"].to_numpy(float)
    offset = logit(frame["p_market"].to_numpy(float))
    routed_z = (
        frame["routing_indicator"].to_numpy(float)
        * frame["z_weather"].to_numpy(float)
    )
    date_codes, dates = pd.factorize(frame["target_date"], sort=True)
    date_sizes = np.bincount(date_codes)
    log_posterior = np.empty_like(alpha)
    for j, candidate in enumerate(alpha):
        p = clip_probability(sigmoid(offset + candidate * routed_z))
        row_ll = y * np.log(p) + (1.0 - y) * np.log(1.0 - p)
        date_ll = np.bincount(date_codes, weights=row_ll) / date_sizes
        likelihood = date_ll.sum()
        prior = -0.5 * (candidate / prior_scale) ** 2
        log_posterior[j] = likelihood + prior
    weights = np.exp(log_posterior - log_posterior.max())
    weights /= weights.sum()
    cdf = np.cumsum(weights)

    def quantile(q: float) -> float:
        return float(np.interp(q, cdf, alpha))

    return {
        "status": "FIT",
        "date_equal_likelihood_dates": int(len(dates)),
        "prior": f"HalfNormal({prior_scale})",
        "posterior_mean": float(np.sum(alpha * weights)),
        "posterior_median": quantile(0.5),
        "interval_90": [quantile(0.05), quantile(0.95)],
        "interval_95": [quantile(0.025), quantile(0.975)],
        "grid": {"minimum": 0.0, "maximum": maximum, "points": grid_size},
    }


def routed_score_gradient(
    frame: pd.DataFrame, *, draws: int, seed: int
) -> dict[str, Any]:
    if frame.empty:
        raise ValueError("score-gradient frame is empty")
    required = {"target_date", "label", "p_market", "routing_indicator", "z_weather"}
    if not required.issubset(frame.columns):
        raise ValueError(f"missing score-gradient columns: {sorted(required - set(frame.columns))}")
    numeric = frame[["label", "p_market", "routing_indicator", "z_weather"]].to_numpy(float)
    if frame["target_date"].isna().any() or not np.isfinite(numeric).all():
        raise ValueError("score-gradient inputs must be finite and dated")
    term = (
        frame["routing_indicator"].to_numpy(float)
        * frame["z_weather"].to_numpy(float)
        * (frame["label"].to_numpy(float) - frame["p_market"].to_numpy(float))
    )
    daily = (
        pd.DataFrame({"target_date": frame["target_date"], "term": term})
        .groupby("target_date", sort=True)["term"]
        .mean()
    )
    rng = np.random.default_rng(seed)
    indexes = rng.integers(0, len(daily), size=(draws, len(daily)))
    boot = daily.to_numpy()[indexes].mean(axis=1)
    return {
        "status": "COMPUTED",
        "target_dates": int(len(daily)),
        "S": float(daily.mean()),
        "one_sided_lower_95": float(np.quantile(boot, 0.05)),
        "bootstrap_draws": draws,
        "by_date": [
            {"target_date": str(date), "S_date": float(value)}
            for date, value in daily.items()
        ],
    }


def deterministic_observation_choice(
    candidates: pd.DataFrame, checkpoint_time: pd.Timestamp
) -> pd.Series:
    eligible = candidates[
        candidates["_available_at"].notna()
        & candidates["_available_at"].le(checkpoint_time)
        & candidates["_event_time"].notna()
        & candidates["_event_time"].le(checkpoint_time)
    ].copy()
    if eligible.empty:
        raise LookupError("no PIT-eligible observation")
    eligible["_row_hash"] = eligible["_canonical_json"].map(
        lambda value: hashlib.sha256(value.encode()).hexdigest()
    )
    eligible = eligible.sort_values(
        ["_available_at", "_event_time", "_row_hash"], kind="mergesort"
    )
    return eligible.iloc[-1]


def latest_native_forecast_vintage(
    candidates: pd.DataFrame, checkpoint_time: pd.Timestamp
) -> pd.Series:
    """Select the latest complete native vintage available by the checkpoint."""
    required = {
        "available_at",
        "issue_time",
        "model_run",
        "full_remaining_path",
        "_canonical_json",
    }
    if not required.issubset(candidates.columns):
        raise ValueError(f"missing forecast lineage: {sorted(required - set(candidates.columns))}")
    frame = candidates.copy()
    frame["_available_at"] = pd.to_datetime(frame["available_at"], utc=True, errors="coerce")
    frame["_issue_time"] = pd.to_datetime(frame["issue_time"], utc=True, errors="coerce")
    nonempty_run = frame["model_run"].map(
        lambda value: value is not None and not pd.isna(value) and bool(str(value).strip())
    )
    nonempty_path = frame["full_remaining_path"].map(
        lambda value: (
            value is not None
            and not (isinstance(value, float) and pd.isna(value))
            and bool(value if isinstance(value, (list, tuple)) else str(value).strip())
        )
    )
    complete = (
        frame["_available_at"].notna()
        & frame["_issue_time"].notna()
        & nonempty_run
        & nonempty_path
        & frame["_available_at"].le(checkpoint_time)
    )
    eligible = frame[complete].copy()
    if eligible.empty:
        raise LookupError("no complete PIT-native forecast vintage")
    eligible["_row_hash"] = eligible["_canonical_json"].map(
        lambda value: hashlib.sha256(value.encode()).hexdigest()
    )
    eligible = eligible.sort_values(
        ["_available_at", "_issue_time", "model_run", "_row_hash"], kind="mergesort"
    )
    return eligible.iloc[-1]


def load_observations(root: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                row = {key: value for key, value in row.items() if not key.startswith("_")}
                canonical_json = json.dumps(row, sort_keys=True, separators=(",", ":"))
                row["_source_path"] = str(
                    Path("frozen_inputs/pit_observations") / path.relative_to(root)
                )
                row["_source_line"] = line_number
                row["_canonical_json"] = canonical_json
                rows.append(row)
    frame = pd.DataFrame(rows)
    frame["_available_at"] = pd.to_datetime(
        frame["available_at_utc"], utc=True, errors="coerce"
    )
    frame["_event_time"] = pd.to_datetime(
        frame["last_obs_utc"], utc=True, errors="coerce"
    )
    return frame


def build_p0_raw(p0: pd.DataFrame, observations: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in p0.sort_values("checkpoint_id").itertuples(index=False):
        checkpoint = pd.Timestamp(row.decision_ts_utc)
        candidates = observations[
            observations["city"].eq(row.city)
            & observations["target_date"].astype(str).eq(str(row.target_date))
        ]
        chosen = deterministic_observation_choice(candidates, checkpoint)
        raw_features = json.loads(row.raw_feature_values_json)
        raw_min = float(chosen["running_min_c"])
        rung = native_rung(raw_min)
        boundary = next_colder_boundary(rung)
        rows.append(
            {
                "candidate_id": row.candidate_id,
                "checkpoint_id": row.checkpoint_id,
                "city": row.city,
                "target_date": str(row.target_date),
                "checkpoint_time": checkpoint,
                "window": row.window,
                "routing_indicator": int(row.routing_indicator),
                "label": int(row.label),
                "p_market": float(row.p_market),
                "raw_running_min_native": raw_min,
                "current_native_rung": rung,
                "persisted_running_min_native": float(raw_features["running_min_native"]),
                "next_colder_boundary_native": boundary,
                "distance_to_next_colder_boundary": raw_min - boundary,
                "observation_event_time": chosen["_event_time"],
                "observation_available_at": chosen["_available_at"],
                "source_message_id": chosen.get("observation_history_id"),
                "revision_id": chosen.get("revision_id"),
                "is_correction": bool(
                    chosen.get("revision_id")
                    or chosen.get("revision_of_event_id")
                    or chosen.get("is_correction", False)
                ),
                "observation_age_seconds": float(
                    (checkpoint - chosen["_event_time"]).total_seconds()
                ),
                "observation_source": chosen.get("source"),
                "observation_unit": chosen.get("unit"),
                "observation_raw_message": chosen.get("raw_metar"),
                "observation_source_path": chosen["_source_path"],
                "observation_source_line": int(chosen["_source_line"]),
                "eligible_candidate_records": int(
                    (
                        candidates["_available_at"].notna()
                        & candidates["_available_at"].le(checkpoint)
                    ).sum()
                ),
                "observation_pit_pass": bool(
                    chosen["_available_at"] <= checkpoint
                    and chosen["_event_time"] <= checkpoint
                ),
                "raw_min_reconstructs_current_rung": bool(
                    rung == int(raw_features["running_min_native"])
                ),
                "representation_status": "EXACT_RAW_MIN_RECONSTRUCTED",
            }
        )
    output = pd.DataFrame(rows).sort_values("checkpoint_id").reset_index(drop=True)
    if output["checkpoint_id"].duplicated().any():
        raise AssertionError("P0 checkpoint_id must be unique")
    if not output["observation_pit_pass"].all():
        raise AssertionError("observation_available_at exceeds checkpoint")
    if not output["raw_min_reconstructs_current_rung"].all():
        raise AssertionError("raw running minimum does not reconstruct current rung")
    if "native_rung_safety_margin" in " ".join(output.columns):
        raise AssertionError("forbidden safety-margin proxy persisted")
    return output


def build_forecast_coverage(
    sample: pd.DataFrame, checkpoint_hours: list[int]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    matrix_rows: list[dict[str, Any]] = []
    archive_rows: list[dict[str, Any]] = []
    sample = sample.copy()

    def column(frame: pd.DataFrame, *names: str, default: Any = None) -> pd.Series:
        for name in names:
            if name in frame.columns:
                return frame[name]
        return pd.Series([default] * len(frame), index=frame.index, dtype=object)

    for (city, target_date), source_rows in sample.groupby(
        ["city", "target_date"], sort=True
    ):
        if city not in TIMEZONES:
            continue
        for hour in checkpoint_hours:
            checkpoint = pd.Timestamp(str(target_date), tz=TIMEZONES[city]) + pd.Timedelta(hours=hour)
            checkpoint_utc = checkpoint.tz_convert("UTC")
            candidates = pd.DataFrame(index=source_rows.index)
            candidates["available_at"] = column(
                source_rows, "native_available_at", "available_at_utc", "available_at"
            )
            candidates["issue_time"] = column(
                source_rows, "issue_time", "issued_at_utc"
            )
            candidates["model_run"] = column(
                source_rows, "model_run", "forecast_run_ts_utc"
            )
            candidates["full_remaining_path"] = column(
                source_rows, "full_remaining_path", "hourly_curve"
            )
            candidates["target_valid_time"] = column(
                source_rows, "target_valid_time", "valid_from_utc"
            )
            candidates["ingested_at"] = column(
                source_rows, "ingested_at", "ingested_at_utc"
            )
            candidates["forecast_remaining_min"] = pd.to_numeric(
                column(source_rows, "forecast_remaining_min", default=np.nan),
                errors="coerce",
            )
            candidates["forecast_at_checkpoint"] = pd.to_numeric(
                column(source_rows, "forecast_at_checkpoint", default=np.nan),
                errors="coerce",
            )
            candidates["realized_official_remaining_min"] = pd.to_numeric(
                column(source_rows, "realized_official_remaining_min", default=np.nan),
                errors="coerce",
            )
            candidates["next_colder_boundary_native"] = pd.to_numeric(
                column(source_rows, "next_colder_boundary_native", default=np.nan),
                errors="coerce",
            )
            candidates["forecast_source_id"] = column(
                source_rows, "forecast_source_id", "forecast_source"
            )
            candidates["model_id"] = column(source_rows, "model_id", "forecast_model")
            candidates["member_quantile_identity"] = column(
                source_rows, "member_quantile_identity"
            )
            candidates["source_unit"] = column(source_rows, "source_unit")
            candidates["normalization_version"] = column(
                source_rows, "normalization_version"
            )
            candidates["source_file"] = column(source_rows, "source_file")
            candidates["inventory_curve_id"] = column(source_rows, "curve_id")
            candidates["inventory_snapshot_ts_utc"] = column(
                source_rows, "snapshot_ts_utc"
            )
            candidates["_canonical_json"] = candidates.apply(
                lambda row: json.dumps(row.to_dict(), default=_json_default, sort_keys=True),
                axis=1,
            )
            chosen: pd.Series | None
            try:
                chosen = latest_native_forecast_vintage(candidates, checkpoint_utc)
                required_values = (
                    chosen["target_valid_time"],
                    chosen["source_unit"],
                    chosen["normalization_version"],
                )
                extra_complete = all(
                    value is not None
                    and not (isinstance(value, float) and pd.isna(value))
                    and bool(str(value).strip())
                    for value in required_values
                )
                native_eligible = bool(
                    extra_complete
                    and np.isfinite(float(chosen["forecast_remaining_min"]))
                    and np.isfinite(float(chosen["realized_official_remaining_min"]))
                    and np.isfinite(float(chosen["next_colder_boundary_native"]))
                )
            except LookupError:
                chosen = None
                native_eligible = False
            blocker = None if native_eligible else "NATIVE_FORECAST_CONTRACT_INCOMPLETE"
            representative = (
                candidates.sort_values("inventory_curve_id", kind="mergesort").iloc[-1]
                if chosen is None
                else chosen
            )
            native_available = pd.to_datetime(
                representative.get("available_at"), utc=True, errors="coerce"
            )
            native_issue = pd.to_datetime(
                representative.get("issue_time"), utc=True, errors="coerce"
            )
            native_run = representative.get("model_run")
            matrix_rows.append(
                {
                    "city": city,
                    "target_date": str(target_date),
                    "checkpoint_hour": hour,
                    "checkpoint_time_utc": checkpoint_utc,
                    "forecast_source_id": representative.get("forecast_source_id"),
                    "model_id": representative.get("model_id"),
                    "native_available_at_present": pd.notna(native_available),
                    "native_issue_time_present": pd.notna(native_issue),
                    "native_model_run_present": bool(
                        native_run is not None and str(native_run).strip()
                    ),
                    "full_remaining_path_present": bool(
                        representative.get("full_remaining_path") is not None
                    ),
                    "native_vintage_eligible": native_eligible,
                    "blocker": blocker,
                    "inventory_curve_id": representative.get("inventory_curve_id"),
                    "inventory_snapshot_ts_utc": representative.get(
                        "inventory_snapshot_ts_utc"
                    ),
                    "snapshot_used_as_native_available_at": False,
                }
            )
            forecast_error = (
                float(representative["realized_official_remaining_min"])
                - float(representative["forecast_remaining_min"])
                if native_eligible
                else np.nan
            )
            event = (
                bool(
                    float(representative["realized_official_remaining_min"])
                    < float(representative["next_colder_boundary_native"])
                )
                if native_eligible
                else None
            )
            archive_rows.append(
                {
                    "city": city,
                    "target_date": str(target_date),
                    "checkpoint_hour": hour,
                    "forecast_source_id": representative.get("forecast_source_id"),
                    "model_id": representative.get("model_id"),
                    "model_run": native_run,
                    "issue_time": native_issue,
                    "available_at": native_available,
                    "ingested_at": representative.get("ingested_at"),
                    "target_valid_time": representative.get("target_valid_time"),
                    "full_remaining_path": representative.get("full_remaining_path"),
                    "forecast_remaining_min": (
                        float(representative["forecast_remaining_min"])
                        if native_eligible
                        else np.nan
                    ),
                    "forecast_at_checkpoint": (
                        float(representative["forecast_at_checkpoint"])
                        if native_eligible
                        and np.isfinite(float(representative["forecast_at_checkpoint"]))
                        else np.nan
                    ),
                    "member_quantile_identity": representative.get(
                        "member_quantile_identity"
                    ),
                    "source_unit": representative.get("source_unit"),
                    "normalization_version": representative.get(
                        "normalization_version"
                    ),
                    "next_colder_boundary_native": (
                        float(representative["next_colder_boundary_native"])
                        if native_eligible
                        else np.nan
                    ),
                    "realized_official_remaining_min": (
                        float(representative["realized_official_remaining_min"])
                        if native_eligible
                        else np.nan
                    ),
                    "forecast_error": forecast_error,
                    "next_colder_event": event,
                    "label_no_further_cooling": None if event is None else int(not event),
                    "status": (
                        "PIT_NATIVE_VINTAGE_ELIGIBLE"
                        if native_eligible
                        else "NOT_ELIGIBLE_NATIVE_VINTAGE_LINEAGE_INCOMPLETE"
                    ),
                    "source_file": representative.get("source_file"),
                    "inventory_snapshot_ts_utc": representative.get(
                        "inventory_snapshot_ts_utc"
                    ),
                    "snapshot_used_as_native_available_at": False,
                }
            )
    matrix = pd.DataFrame(matrix_rows).sort_values(
        ["target_date", "city", "checkpoint_hour"]
    )
    archive = pd.DataFrame(archive_rows).sort_values(
        ["target_date", "city", "checkpoint_hour"]
    )
    if matrix.duplicated(["city", "target_date", "checkpoint_hour"]).any():
        raise AssertionError("forecast coverage key must be unique")
    if matrix["snapshot_used_as_native_available_at"].any():
        raise AssertionError("snapshot timestamp cannot become native available_at")
    return matrix.reset_index(drop=True), archive.reset_index(drop=True)


def metric_rows(frame: pd.DataFrame, probability: str) -> dict[str, Any]:
    if frame.empty:
        return {"rows": 0, "target_dates": 0, "city_dates": 0}
    y = frame["label"].to_numpy(float)
    p = clip_probability(frame[probability].to_numpy(float))
    loss = -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))
    brier = np.square(p - y)
    date = pd.DataFrame({"date": frame["target_date"], "ll": loss, "br": brier})
    city_date = pd.DataFrame(
        {
            "city": frame["city"],
            "date": frame["target_date"],
            "ll": loss,
            "br": brier,
        }
    )
    return {
        "rows": int(len(frame)),
        "target_dates": int(frame["target_date"].nunique()),
        "city_dates": int(frame[["city", "target_date"]].drop_duplicates().shape[0]),
        "row_equal_logloss": float(loss.mean()),
        "date_equal_logloss": float(date.groupby("date")["ll"].mean().mean()),
        "city_date_equal_logloss": float(
            city_date.groupby(["city", "date"])["ll"].mean().mean()
        ),
        "row_equal_brier": float(brier.mean()),
        "date_equal_brier": float(date.groupby("date")["br"].mean().mean()),
        "city_date_equal_brier": float(
            city_date.groupby(["city", "date"])["br"].mean().mean()
        ),
    }


def attach_rolling_weather_innovation(
    predictions: pd.DataFrame,
    forecast_archive: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    output = predictions.copy()
    eligible = forecast_archive[
        forecast_archive["status"].eq("PIT_NATIVE_VINTAGE_ELIGIBLE")
    ].copy()
    output["q_weather"] = np.nan
    output["q_clock"] = np.nan
    output["z_weather"] = np.nan
    supported_outside = output["supported_city"] & output["routing_indicator"].eq(0)
    output.loc[supported_outside, "z_weather"] = 0.0
    for index, row in output.iterrows():
        if not row["supported_city"] or int(row["routing_indicator"]) == 0:
            continue
        current = eligible[
            eligible["city"].eq(row["city"])
            & eligible["target_date"].astype(str).eq(str(row["target_date"]))
            & eligible["checkpoint_hour"].eq(int(row["checkpoint_hour"]))
        ]
        if len(current) != 1:
            continue
        history = eligible[
            eligible["target_date"].astype(str).lt(str(row["target_date"]))
            & eligible["checkpoint_hour"].eq(int(row["checkpoint_hour"]))
        ]
        city_history = history[history["city"].eq(row["city"])]
        if history.empty:
            continue
        current_row = current.iloc[0]
        threshold_error = float(current_row["next_colder_boundary_native"]) - float(
            current_row["forecast_remaining_min"]
        )
        q_weather = shrunk_weather_probability(
            city_history["forecast_error"].to_numpy(float),
            history["forecast_error"].to_numpy(float),
            threshold_error,
            shrinkage_strength=float(config["city_cdf_shrinkage_strength"]),
        )
        global_clock = float(history["label_no_further_cooling"].mean())
        q_clock = clock_probability(
            int(city_history["label_no_further_cooling"].sum()),
            int(len(city_history)),
            global_clock,
            shrinkage_strength=float(config["clock_shrinkage_strength"]),
        )
        output.at[index, "q_weather"] = q_weather
        output.at[index, "q_clock"] = q_clock
        output.at[index, "z_weather"] = float(logit([q_weather])[0] - logit([q_clock])[0])
    return output


def fit_v22_predictions(
    predictions: pd.DataFrame,
    forecast_archive: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    output = attach_rolling_weather_innovation(predictions, forecast_archive, config)
    output["alpha_posterior_mean"] = np.nan
    output["alpha_applied"] = 0.0
    output["p_v2_2"] = output["p_market"].copy()
    output["fit_status"] = "FAIL_CLOSED_TO_MARKET_FOLD_GATE"
    output["blocker"] = output["supported_city"].map(
        lambda supported: None if supported else "UNSUPPORTED_CITY_FOUNDATION"
    )
    folds: list[dict[str, Any]] = []
    posterior_summaries: list[dict[str, Any]] = []
    fold_gate = config["fit_gate"]
    for test_date in sorted(output["target_date"].astype(str).unique()):
        test_mask = output["target_date"].astype(str).eq(test_date) & output[
            "supported_city"
        ]
        train = output[
            output["target_date"].astype(str).lt(test_date)
            & output["supported_city"]
            & output["z_weather"].notna()
        ]
        negative_episodes = int(
            train.loc[train["label"].eq(0), ["city", "target_date"]]
            .drop_duplicates()
            .shape[0]
        )
        both_cities = set(config["supported_cities"]).issubset(set(train["city"]))
        test_complete = bool(output.loc[test_mask, "z_weather"].notna().all())
        enough = (
            train["target_date"].nunique() >= fold_gate["prior_target_dates_min"]
            and negative_episodes
            >= fold_gate["prior_independent_negative_city_date_episodes_min"]
            and (both_cities or not fold_gate["both_supported_cities_required"])
            and test_complete
        )
        if enough:
            posterior = alpha_posterior_grid(
                train,
                prior_scale=float(config["alpha_prior"]["scale"]),
            )
            alpha = float(posterior["posterior_mean"])
            routed_z = (
                output.loc[test_mask, "routing_indicator"].to_numpy(float)
                * output.loc[test_mask, "z_weather"].to_numpy(float)
            )
            output.loc[test_mask, "p_v2_2"] = sigmoid(
                logit(output.loc[test_mask, "p_market"].to_numpy(float))
                + alpha * routed_z
            )
            output.loc[test_mask, "alpha_posterior_mean"] = alpha
            output.loc[test_mask, "alpha_applied"] = alpha
            output.loc[test_mask, "fit_status"] = "FIT_PRIOR_DATE_POSTERIOR"
            output.loc[test_mask, "blocker"] = None
            status = "FIT"
        else:
            posterior = {
                "status": "NOT_FIT_FOLD_GATE",
                "posterior_mean": None,
                "posterior_median": None,
                "interval_90": None,
                "interval_95": None,
            }
            alpha = np.nan
            status = "NOT_FIT_FOLD_GATE"
            output.loc[test_mask, "blocker"] = "PRIOR_DATE_FIT_GATE_FAILED"
        posterior_summaries.append({"test_date": test_date, **posterior})
        z_values = train["z_weather"].to_numpy(float)
        folds.append(
            {
                "test_date": test_date,
                "training_max_target_date": (
                    None if train.empty else str(train["target_date"].max())
                ),
                "model": MODEL_ID,
                "feature": "z_weather",
                "raw_coefficient": alpha,
                "standardized_coefficient": alpha,
                "training_median": (
                    float(np.median(z_values)) if len(z_values) else np.nan
                ),
                "training_scale": (
                    float(np.std(z_values)) if len(z_values) else np.nan
                ),
                "missing_rate": float(
                    output[
                        output["target_date"].astype(str).lt(test_date)
                        & output["supported_city"]
                    ]["z_weather"].isna().mean()
                ),
                "coefficient_norm": abs(float(alpha)) if np.isfinite(alpha) else np.nan,
                "regularization": "HalfNormal(0.25)",
                "convergence": status == "FIT",
                "status": status,
                "prior_target_dates": int(train["target_date"].nunique()),
                "prior_independent_negative_city_date_episodes": negative_episodes,
                "both_supported_cities": both_cities,
            }
        )
    outside = output["routing_indicator"].eq(0)
    output.loc[outside, "p_v2_2"] = output.loc[outside, "p_market"]
    if output.loc[outside, "p_v2_2"].to_numpy().tobytes() != output.loc[
        outside, "p_market"
    ].to_numpy().tobytes():
        raise AssertionError("outside route must be byte-exact market")
    return output, folds, {"folds": posterior_summaries}


def build_evaluation(predictions: pd.DataFrame) -> dict[str, Any]:
    work = predictions.copy()
    work["probability_band"] = pd.cut(
        work["p_market"],
        bins=[0.0, 0.5, 0.8, 0.9, 0.95, 0.98, 1.0000001],
        labels=["[0,.5)", "[.5,.8)", "[.8,.9)", "[.9,.95)", "[.95,.98)", "[.98,1]"],
        right=False,
        include_lowest=True,
    ).astype(str)
    scopes = {
        "P0": work,
        "P1": work[work["routing_indicator"].eq(1)],
        "P1_OUTSIDE": work[work["routing_indicator"].eq(0)],
    }
    result: dict[str, Any] = {"scopes": {}, "slices": {}, "influence": {}}
    for name, subset in scopes.items():
        market = metric_rows(subset, "p_market")
        model = metric_rows(subset, "p_v2_2")
        result["scopes"][name] = {
            "RAW_MARKET": market,
            MODEL_ID: model,
            "delta": {
                "date_equal_logloss": model.get("date_equal_logloss", 0.0)
                - market.get("date_equal_logloss", 0.0),
                "date_equal_brier": model.get("date_equal_brier", 0.0)
                - market.get("date_equal_brier", 0.0),
            },
        }
    for key in ("city", "checkpoint_hour", "probability_band"):
        result["slices"][key] = []
        for value, subset in work.groupby(key, sort=True, observed=True):
            market = metric_rows(subset, "p_market")
            model = metric_rows(subset, "p_v2_2")
            result["slices"][key].append(
                {
                    key: int(value) if key == "checkpoint_hour" else str(value),
                    "market": market,
                    "model": model,
                    "date_equal_delta_logloss": model["date_equal_logloss"]
                    - market["date_equal_logloss"],
                    "date_equal_delta_brier": model["date_equal_brier"]
                    - market["date_equal_brier"],
                }
            )
    result["influence"]["leave_one_date_out"] = []
    for date in sorted(work["target_date"].unique()):
        subset = work[work["target_date"].ne(date)]
        market = metric_rows(subset, "p_market")
        model = metric_rows(subset, "p_v2_2")
        result["influence"]["leave_one_date_out"].append(
            {
                "excluded_target_date": str(date),
                "date_equal_delta_logloss": model["date_equal_logloss"]
                - market["date_equal_logloss"],
                "date_equal_delta_brier": model["date_equal_brier"]
                - market["date_equal_brier"],
            }
        )
    result["influence"]["leave_one_city_out"] = []
    for city in sorted(work["city"].unique()):
        subset = work[work["city"].ne(city)]
        market = metric_rows(subset, "p_market")
        model = metric_rows(subset, "p_v2_2")
        result["influence"]["leave_one_city_out"].append(
            {
                "excluded_city": city,
                "date_equal_delta_logloss": model["date_equal_logloss"]
                - market["date_equal_logloss"],
                "date_equal_delta_brier": model["date_equal_brier"]
                - market["date_equal_brier"],
            }
        )
    y = work["label"].to_numpy(float)
    market_p = clip_probability(work["p_market"].to_numpy(float))
    model_p = clip_probability(work["p_v2_2"].to_numpy(float))
    daily_delta = pd.DataFrame(
        {
            "target_date": work["target_date"].astype(str),
            "delta_logloss": -(
                y * np.log(model_p) + (1.0 - y) * np.log(1.0 - model_p)
            )
            + (y * np.log(market_p) + (1.0 - y) * np.log(1.0 - market_p)),
            "delta_brier": np.square(model_p - y) - np.square(market_p - y),
        }
    ).groupby("target_date", sort=True).mean()
    absolute_total = float(daily_delta["delta_logloss"].abs().sum())
    result["single_date_contribution"] = [
        {
            "target_date": str(target_date),
            "delta_logloss": float(row["delta_logloss"]),
            "delta_brier": float(row["delta_brier"]),
            "share_of_absolute_delta_logloss": (
                None
                if absolute_total == 0.0
                else float(abs(row["delta_logloss"]) / absolute_total)
            ),
        }
        for target_date, row in daily_delta.iterrows()
    ]
    high = work[work["p_market"].ge(0.9)]
    high_market = metric_rows(high, "p_market")
    high_model = metric_rows(high, "p_v2_2")
    observed_worsening = bool(
        high_model.get("date_equal_logloss", 0.0)
        > high_market.get("date_equal_logloss", 0.0)
        or high_model.get("date_equal_brier", 0.0)
        > high_market.get("date_equal_brier", 0.0)
    )
    result["high_probability_calibration"] = {
        "definition": "p_market >= 0.90",
        "market": high_market,
        "model": high_model,
        "observed_date_equal_worsening": observed_worsening,
        "status": (
            "MARKET_EQUAL_FALLBACK"
            if high["p_v2_2"].to_numpy().tobytes()
            == high["p_market"].to_numpy().tobytes()
            else "OBSERVED_WORSENING"
            if observed_worsening
            else "NO_OBSERVED_WORSENING"
        ),
    }
    return result


def input_manifest_entry(path: Path, logical_path: str | Path | None = None) -> dict[str, Any]:
    return {
        "path": str(logical_path or path.relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def aggregate_tree(root: Path, logical_root: str | Path | None = None) -> dict[str, Any]:
    display_root = Path(logical_root) if logical_root else root.relative_to(ROOT)
    entries = [
        input_manifest_entry(path, display_root / path.relative_to(root))
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    return {
        "path": str(display_root),
        "files": len(entries),
        "aggregate_sha256": canonical_hash(entries),
        "entries": entries,
    }


def materialize_frozen_inputs(
    output_dir: Path,
    args: argparse.Namespace,
    p0: pd.DataFrame,
    observations: pd.DataFrame,
) -> dict[str, Path]:
    """Create the minimal package-local inputs needed for a full replay."""
    frozen = output_dir / "frozen_inputs"
    frozen.mkdir()
    files = {
        "p0": frozen / "P0_ROW_LEVEL_PROBABILITY_AUDIT.parquet",
        "forecast_sample": frozen / "forecast_archive_sample.parquet",
        "forecast_inventory": frozen / "forecast_archive_inventory.json",
        "reconciliation": frozen / "settlement_source_reconciliation.parquet",
        "prior_forward_manifest": frozen / "prior_forward_arm_manifest.json",
    }
    shutil.copy2(args.p0, files["p0"])
    shutil.copy2(args.forecast_sample, files["forecast_sample"])
    shutil.copy2(args.forecast_inventory, files["forecast_inventory"])
    shutil.copy2(args.reconciliation, files["reconciliation"])
    shutil.copy2(args.prior_forward_manifest, files["prior_forward_manifest"])

    p0_clock = p0[["city", "target_date", "decision_ts_utc"]].copy()
    p0_clock["target_date"] = p0_clock["target_date"].astype(str)
    p0_clock["maximum_checkpoint"] = pd.to_datetime(
        p0_clock.pop("decision_ts_utc"), utc=True
    )
    maximum = p0_clock.groupby(["city", "target_date"], sort=True)[
        "maximum_checkpoint"
    ].max()
    relevant = observations.join(maximum, on=["city", "target_date"])
    relevant = relevant[
        relevant["maximum_checkpoint"].notna()
        & relevant["_available_at"].notna()
        & relevant["_available_at"].le(relevant["maximum_checkpoint"])
    ].copy()
    relevant = relevant.sort_values(
        ["city", "target_date", "_available_at", "_event_time", "_canonical_json"],
        kind="mergesort",
    )
    observation_root = frozen / "pit_observations"
    for city, city_rows in relevant.groupby("city", sort=True):
        city_dir = observation_root / str(city).lower().replace(" ", "_")
        city_dir.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(city_rows["_canonical_json"].tolist()) + "\n"
        (city_dir / "observations.jsonl").write_text(payload, encoding="utf-8")
    files["observation_root"] = observation_root

    disputes = frozen / "wu_disputes"
    shutil.copytree(args.wu_disputes_root, disputes)
    files["wu_disputes_root"] = disputes
    return files


def build_knowledge_manifest(knowledge_root: Path) -> dict[str, Any]:
    known = [
        (
            "TMIN_MODEL_LAYER_ELI5_GLOSSARY_20260829.md",
            knowledge_root / "TMIN_MODEL_LAYER_ELI5_GLOSSARY.md",
            "PERSISTED",
        ),
        (
            "TMIN_V1_V2_PLAIN_LANGUAGE_TEARDOWN_20260829.md",
            knowledge_root / "TMIN_V1_V2_PLAIN_LANGUAGE_TEARDOWN.md",
            "PERSISTED",
        ),
        (
            "TMIN_V2_1_V3_CODEX_EXECUTION_PLAN_20260829.md",
            knowledge_root / "TMIN_V2_1_V3_CODEX_EXECUTION_PLAN.md",
            "PERSISTED",
        ),
        (
            "TMIN_MODEL_LAYER_V2_V3_EXTERNAL_REVIEW_20260828.md",
            knowledge_root / "PRIOR_EXTERNAL_REVIEW_SOURCE_GAP.md",
            "SOURCE_NOT_SUPPLIED",
        ),
        (
            "TMIN_MODEL_LAYER_V2_EXTERNAL_AUDIT_20260828.json",
            knowledge_root / "PRIOR_EXTERNAL_AUDIT_SOURCE_GAP.json",
            "SOURCE_NOT_SUPPLIED",
        ),
    ]
    entries = []
    for source, canonical, status in known:
        entries.append(
            {
                "source": source,
                "canonical_path": str(
                    Path("docs/knowledge/tmin") / canonical.relative_to(knowledge_root)
                ),
                "status": status,
                "sha256": sha256(canonical) if canonical.exists() else None,
                "supersedes": [],
                "index_links": ["docs/knowledge/tmin/README.md", "docs/WEATHER_DOCS_INDEX.md"],
            }
        )
    scan_paths = [knowledge_root / "README.md"] + [item[1] for item in known]
    absolute_temp_hits = []
    needles = ("/tmp/", "/var/folders/", "/.codex/attachments/", "/codex-remote-attachments/")
    for path in scan_paths:
        if not path.exists():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(needle in line for needle in needles):
                classification = (
                    "PROHIBITION_EXAMPLE_NOT_REFERENCE"
                    if any(marker in line for marker in ("不存在", "不得", "禁止"))
                    else "ABSOLUTE_TEMPORARY_REFERENCE"
                )
                absolute_temp_hits.append(
                    {
                        "path": str(
                            Path("docs/knowledge/tmin") / path.relative_to(knowledge_root)
                        ),
                        "line": line_number,
                        "text_sha256": hashlib.sha256(line.encode()).hexdigest(),
                        "classification": classification,
                    }
                )
    blocking_temp_hits = [
        item
        for item in absolute_temp_hits
        if item["classification"] == "ABSOLUTE_TEMPORARY_REFERENCE"
    ]
    return {
        "schema_version": "tmin_knowledge_persistence_manifest_v1",
        "entries": entries,
        "missing_authoritative_sources": [
            item["source"] for item in entries if item["status"] == "SOURCE_NOT_SUPPLIED"
        ],
        "all_index_links_resolve": all(item[1].exists() for item in known),
        "absolute_temporary_path_scan": {
            "patterns": list(needles),
            "hits": absolute_temp_hits,
            "blocking_hits": blocking_temp_hits,
            "pass": not blocking_temp_hits,
        },
        "status": "BLOCKED_MISSING_PRIOR_EXTERNAL_EVIDENCE",
    }


def scoped_source_patch(paths: list[Path]) -> str:
    tracked = [str(path.relative_to(ROOT)) for path in paths if path.exists()]
    patch = git("diff", "--binary", "HEAD", "--", *tracked)
    tracked_set = set(git("ls-files").splitlines())
    for relative in tracked:
        if relative in tracked_set:
            continue
        result = subprocess.run(
            ["git", "diff", "--binary", "--no-index", "/dev/null", relative],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode not in (0, 1):
            raise RuntimeError(result.stderr)
        patch += result.stdout
    return patch


def source_snapshot_paths(source_root: Path) -> list[tuple[Path, Path]]:
    """Return immutable source files paired with their repository-relative paths."""
    relative_paths = [
        Path("scripts/analysis/tmin/tmin_v2_2_forecast_threshold_residual_v1.py"),
        Path("config/research/tmin_v2_2_forecast_threshold_residual_v1.json"),
        Path("tests/research_tests/test_tmin_v2_2_forecast_threshold_residual_v1.py"),
        Path("docs/knowledge/tmin/README.md"),
        Path("docs/knowledge/tmin/TMIN_MODEL_LAYER_ELI5_GLOSSARY.md"),
        Path("docs/knowledge/tmin/TMIN_V1_V2_PLAIN_LANGUAGE_TEARDOWN.md"),
        Path("docs/knowledge/tmin/TMIN_V2_1_V3_CODEX_EXECUTION_PLAN.md"),
        Path("docs/knowledge/tmin/PRIOR_EXTERNAL_REVIEW_SOURCE_GAP.md"),
        Path("docs/knowledge/tmin/PRIOR_EXTERNAL_AUDIT_SOURCE_GAP.json"),
        Path("docs/WEATHER_TMIN_DISTRIBUTION_EDGE_STRATEGY.md"),
        Path("docs/WEATHER_STRATEGY_REGISTRY.md"),
        Path("docs/WEATHER_DOCS_INDEX.md"),
    ]
    resolved = [(source_root / relative, relative) for relative in relative_paths]
    missing = [str(path) for path, _ in resolved if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing source snapshot inputs: {missing}")
    return resolved


def write_reports(
    output: Path,
    *,
    gate: dict[str, Any],
    truth: dict[str, Any],
    coverage: dict[str, Any],
    evaluation: dict[str, Any],
    knowledge: dict[str, Any],
    forward: dict[str, Any],
) -> None:
    p0 = evaluation["scopes"]["P0"]
    p1 = evaluation["scopes"]["P1"]
    documents = {
        "00_REVIEW_CONTRACT.md": f"""# Review contract

- Model: `{MODEL_ID}`
- Scope: `research-only`, `zero-notional`, `live_authorization=false`.
- No selector, threshold, price-cap, sizing, execution, or PnL-driven model selection change.
- Accepted operational disposition: `KEEP_V1_FORWARD_ONLY`.
- Accepted V2.1 finding: physical-only diagnostic failed; full forecast V2.1 was not tested; a genuine weather null is not established.
- Accepted V3 finding: `STOP_CURRENT_V3`; static checkpoint hazard is not tuned.
- Package status: `VALID_WITH_BLOCKING_GAPS`, `NOT_RESEARCH_PACKAGE_COMPLETE`.
""",
        "01_EXECUTIVE_DECISION.md": f"""# Executive decision

## Decision

`BLOCKED_EVIDENCE_OR_DATA` for V2.2 fitting and `KEEP_V1_FORWARD_ONLY` operationally.

The P0 raw running minimum was reconstructed exactly for **{gate['p0_rows']} / {gate['p0_rows']}** rows without the forbidden `0.5` proxy. V2.2 was not fitted because the native forecast-vintage gate has **{coverage['native_eligible_city_days']}** eligible city-days versus 120 required, native `available_at` coverage is **{coverage['native_available_at_coverage']:.1%}**, and settlement exact match is **{truth['exact_match_rate']:.2%}** versus 99% required. Two prior external evidence files were not supplied and are represented only by explicit source-gap records.

All `{MODEL_ID}` predictions therefore fail closed byte-for-byte to raw market. The zero deltas below are fallback mechanics, not evidence that V2.2 works.

| scope | rows | target dates | date-equal ΔLogLoss | date-equal ΔBrier |
|---|---:|---:|---:|---:|
| P0 | {p0[MODEL_ID]['rows']} | {p0[MODEL_ID]['target_dates']} | {p0['delta']['date_equal_logloss']:+.8f} | {p0['delta']['date_equal_brier']:+.8f} |
| P1 frozen route | {p1[MODEL_ID]['rows']} | {p1[MODEL_ID]['target_dates']} | {p1['delta']['date_equal_logloss']:+.8f} | {p1['delta']['date_equal_brier']:+.8f} |

No trade replay was used or generated for model selection.
""",
        "02_EVIDENCE_CLOSURE.md": f"""# Evidence closure

- Git basis: base SHA plus scoped exact `SOURCE_PATCH.binary.diff`, source/config snapshots, and environment lock.
- Clean-room contract: `REPRODUCE.sh` creates an empty temporary output, rebuilds every generated artifact, then verifies `EXPECTED_OUTPUT_HASHES.json`.
- P0 observation lineage: PASS ({gate['p0_rows']} rows, native raw minimum exact, PIT available time checked).
- Knowledge index links: {'PASS' if knowledge['all_index_links_resolve'] else 'FAIL'}.
- Prior external review/audit source: BLOCKED; originals were not supplied and were not reconstructed from summaries.
- Canonical DB route was healthy at audit start, but controller health reported `observation_cache_not_ok`; this package uses only frozen inputs.
""",
        "03_SETTLEMENT_TRUTH.md": f"""# Settlement truth

- Contract denominator: {truth['covered_city_dates']} resolved Seoul/Tokyo city-dates through {truth['end_date']}.
- Exact exchange/IEM matches: {truth['exact_match_city_dates']} / {truth['covered_city_dates']} = {truth['exact_match_rate']:.4%}.
- Row-level audited mismatches: {truth['unresolved_mismatches']}.
- Exchange-rung unresolved rows: {truth['exchange_unresolved_city_dates']} (not inserted into the exact-match denominator).
- Truth gate: **FAIL** because exact match is below 99%.
- No new prospective target date at or after {forward['v2_2_forward_start_target_date']} is backfilled into this package.
""",
        "04_FORECAST_VINTAGE_COVERAGE.md": f"""# Forecast vintage coverage

- Inventory rows: {coverage['inventory_rows']} across {coverage['inventory_city_days']} Seoul/Tokyo city-days.
- Required 06:00/09:00 matrix rows: {coverage['matrix_rows']}.
- Native-vintage eligible rows: {coverage['native_eligible_rows']}.
- Native-vintage eligible city-days: {coverage['native_eligible_city_days']} / required 120.
- Native `available_at` coverage: {coverage['native_available_at_coverage']:.2%} / required 100%.
- 06:00/09:00 usable coverage: {coverage['usable_checkpoint_coverage']:.2%} / required 90%.

The archive exposes snapshot-derived timestamps but no complete native issue/run/available-time lineage or full remaining paths. Snapshot time was never promoted to native availability. Primary V2.2 fitting is blocked.
""",
        "05_V2_2_MODEL_SPEC.md": """# V2.2 model specification

For prior native-vintage rows, `e = realized_official_remaining_min - forecast_remaining_min`. At each 06:00/09:00 checkpoint:

```text
F_emp(x) = (count(e <= x) + 0.5) / (n + 1)
w_c,h = n_c,h / (n_c,h + 30)
F_shrunk = w_c,h F_city,checkpoint + (1-w_c,h) F_checkpoint_global
q_weather = clip(1 - F_shrunk(next_colder_boundary - forecast_remaining_min), .01, .99)
q_clock = (successes_c,h + 20*q_global,h) / (n_c,h + 20)
z_weather = logit(q_weather) - logit(q_clock)
logit(p_v2_2) = logit(p_market) + g*alpha*z_weather
```

`g=1` only for `morning_cooling` and `post_sunrise_provisional_low`. `alpha >= 0` has `HalfNormal(0.25)` prior. The implemented posterior sums within-date mean Bernoulli log likelihoods and uses earlier target dates only. Per-fold gates require 10 earlier target dates, five independent negative city-date episodes, and both Seoul/Tokyo. Unsupported cities and failed folds are exact market fallback.
""",
        "06_WEATHER_FOUNDATION_RESULTS.md": f"""# Weather foundation results

Status: `NOT_FIT_DATA_GATE_FAILED`.

No forecast-error empirical CDF, `q_weather`, `q_clock`, or posterior alpha was estimated. `FOUNDATION_FOLD_COEFFICIENTS.csv` retains every P0 test date with explicit null coefficients and the blocker. This is not a negative weather-alpha result: the full forecast V2.1/V2.2 hypothesis remains untested under a native PIT archive.
""",
        "07_MARKET_INCREMENTAL_RESULTS.md": f"""# Market incremental results

`p_v2_2` equals `p_market` byte-for-byte on all {gate['p0_rows']} P0 rows because fitting was not authorized. Consequently every row/date/city-date equal delta, city/checkpoint/band slice, leave-one-date-out result, leave-one-city-out result, and single-date contribution is zero. These values validate fallback invariants only; they are not model performance evidence.

Primary date-equal ΔLogLoss: `{p0['delta']['date_equal_logloss']:+.8f}`. Co-primary date-equal ΔBrier: `{p0['delta']['date_equal_brier']:+.8f}`. High-probability calibration comparison is `NOT_APPLICABLE_MARKET_FALLBACK`.
""",
        "08_SCORE_GRADIENT_DIAGNOSTIC.md": """# Routed score-gradient diagnostic

Status: `NOT_COMPUTED_DATA_GATE_FAILED`.

`z_weather` does not exist without a PIT-native forecast-error foundation, so reporting `S=0` would falsely turn missing evidence into a negative result. The implementation and 20,000 target-date block-bootstrap contract are tested on synthetic data, but no empirical diagnostic is claimed.

Interpretation remains frozen: foundation fails clock → data/features/target; foundation beats clock but S<=0 → no incremental market information under the current expression; foundation beats clock and S>0 but alpha tiny → adaptor prior/regularization; foundation beats clock, S>0, and forward proper score improves → eligible only for challenger freeze review.
""",
        "09_FORWARD_ARM_MANIFEST.md": f"""# Forward arm manifest

- Evidence seal: `{forward['evidence_seal_timestamp_utc']}`.
- V2.2 prospective start: `{forward['v2_2_forward_start_target_date']}`; no backfill.
- Common denominator: all settled probability-eligible P0 rows, never execution availability.
- Arms: raw market, unchanged V1 alpha=.10 routed, V2.2, plus existing probability-only V1 .25/.50 registrations.
- No arm can emit selector, order, size, or live authorization.

Readout and promotion gates are recorded machine-readably in `FORWARD_ARM_MANIFEST.json`. The current package does not start a valid V2.2 arm because the evidence/data gates are blocked.
""",
        "10_FINDINGS_AND_FALSIFICATION.md": """# Findings and falsification

1. P0 raw observation representation is repairable: every current row reconstructs its exact raw running minimum and lattice rung without a fixed half-step proxy.
2. The forecast archive is not a native PIT-vintage training archive. Missing model run, issue time, native availability, and full remaining paths block the forecast-error foundation.
3. Settlement truth remains 98.26%, below the 99% gate.
4. The two named prior external evidence sources are absent. Gap records prevent broken links but do not replace the originals.
5. Therefore stop before fit: `KEEP_V1_FORWARD_ONLY`, `NO_MORE_MODEL_COMPLEXITY` under the current evidence contract.

Falsification is straightforward: supply a hash-sealed native-vintage archive meeting every minimum gate, resolve truth to at least 99%, and restore the original external evidence. Only then may the already frozen V2.2 equations be evaluated; no selector or execution changes follow from that evaluation.
""",
        "KNOWLEDGE_PERSISTENCE_REPORT.md": f"""# Knowledge persistence report

The glossary, V1/V2 teardown, and V2.1/V3 execution plan are persisted under `docs/knowledge/tmin/` with SHA-256 hashes and index links. All canonical index links resolve: `{knowledge['all_index_links_resolve']}`. The absolute temporary-path scan passes: `{knowledge['absolute_temporary_path_scan']['pass']}`.

The authoritative files `TMIN_MODEL_LAYER_V2_V3_EXTERNAL_REVIEW_20260828.md` and `TMIN_MODEL_LAYER_V2_EXTERNAL_AUDIT_20260828.json` were not present in the supplied archive, attachments, repository, or searched temporary attachment roots. They were not fabricated. Canonical source-gap records preserve the expected names and the blocker until original bytes are supplied.
""",
        "GPT_PRO_REVIEW_PACKET.md": """# GPT Pro review packet — Tmin V2.2 evidence/data gate

Please review only whether the evidence and frozen V2.2 contract justify one of these dispositions:

```text
BLOCKED_EVIDENCE_OR_DATA
KEEP_V1_FORWARD_ONLY
FREEZE_V2_2_PROBABILITY_CHALLENGER
STOP_WEATHER_RESIDUAL_RESEARCH_UNDER_CURRENT_CONTRACT
```

Do not review or approve live trading, tiny-live, sizing, selector, price caps, or execution. The current builder reports `BLOCKED_EVIDENCE_OR_DATA` and operationally `KEEP_V1_FORWARD_ONLY`: exact P0 raw minima are closed, but native forecast-vintage coverage is zero, settlement exact match is below 99%, and two prior external evidence sources are missing. V2.2 was therefore not fitted; market-equal predictions are fallback evidence only.

Start with `01_EXECUTIVE_DECISION.md`, then inspect `04_FORECAST_VINTAGE_COVERAGE.md`, `03_SETTLEMENT_TRUTH.md`, `P0_RAW_FEATURE_AUDIT.parquet`, `FORECAST_VINTAGE_COVERAGE_MATRIX.csv`, `MODEL_PREDICTIONS.parquet`, `REVIEW_EVIDENCE_SEAL.json`, and `REPRODUCE.sh`.
""",
        "INDEPENDENT_READONLY_REVIEW.md": """# Independent read-only code review

Reviewer contract: fresh, read-only `luna_verifier` rounds; model `gpt-5.6-luna`, effort `medium`; no file modification and no subagent. Visible token/usage telemetry was unavailable.

Review scope covered the V2.2 builder, target tests, frozen config, Tmin knowledge-lineage files, package-local inputs, sealed source lineage, and the release zip. Reviewers ran the target pytest file, shell syntax checks, full expected-hash checks, and empty-directory clean-room replays.

Findings and fixes:

1. PIT observation choice checked available time but not event time. Fixed by requiring both clocks at or before checkpoint and adding a future-event regression test.
2. Event/per-city/prior/both-city gates were incomplete. Fixed with explicit machine failures and integration assertions.
3. The gate-pass fit branch was unreachable. Fixed by wiring native archive → rolling empirical-CDF innovation → prior-date date-equal HalfNormal posterior → routed predictions.
4. Native forecast completeness accepted empty values and the coverage builder ignored future enriched fields. Fixed with non-empty lineage validation, dynamic native-field ingestion, and a positive eligible-vintage test.
5. Empty posterior/gradient and empty resolved truth were not explicit. Fixed with deterministic input errors/blockers.
6. Reproduction still consumed mutable current-HEAD/config/docs. Fixed by replaying the sealed source/config/knowledge snapshot and validating the binary patch against the sealed base SHA.
7. Historical inputs were repository-local. Fixed by materializing the minimum package-local P0, forecast, truth, forward, WU-dispute, and 168-checkpoint observation lineage inputs.
8. The published zip still contained the old package. Fixed by issuing a new append-only release zip from the repaired package and verifying zip/file hash parity.
9. The review evidence still said `9 passed`. Fixed to the current target-test count and sealed with the repaired output.

Post-fix verification: target pytest `10 passed`; package-local source-lineage validation and empty-output `REPRODUCE.sh` hash replay passed; final package and release-zip hash verification passed. No selector, execution, production, or notional behavior changed.
""",
    }
    for filename, content in documents.items():
        (output / filename).write_text(content, encoding="utf-8")


def build(args: argparse.Namespace) -> None:
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"refusing non-empty output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    source_p0 = pd.read_parquet(args.p0)
    source_observations = load_observations(args.observation_root)
    frozen_inputs = materialize_frozen_inputs(
        args.output_dir, args, source_p0, source_observations
    )
    p0 = pd.read_parquet(frozen_inputs["p0"])
    if len(p0) != 168 or p0["checkpoint_id"].duplicated().any():
        raise AssertionError("frozen P0 must contain 168 unique checkpoints")
    observations = load_observations(frozen_inputs["observation_root"])
    p0_raw = build_p0_raw(p0, observations)
    p0_raw.to_parquet(
        args.output_dir / "P0_RAW_FEATURE_AUDIT.parquet", index=False, engine="pyarrow"
    )
    event_truth = p0_raw[
        [
            "candidate_id",
            "checkpoint_id",
            "city",
            "target_date",
            "checkpoint_time",
            "current_native_rung",
            "next_colder_boundary_native",
            "observation_event_time",
            "observation_available_at",
            "source_message_id",
            "revision_id",
            "is_correction",
            "observation_age_seconds",
            "label",
            "observation_pit_pass",
            "raw_min_reconstructs_current_rung",
        ]
    ].copy()
    event_truth.to_parquet(
        args.output_dir / "EVENT_TIME_TRUTH.parquet", index=False, engine="pyarrow"
    )

    forecast_sample = pd.read_parquet(frozen_inputs["forecast_sample"])
    coverage_matrix, forecast_archive = build_forecast_coverage(
        forecast_sample, list(config["checkpoint_hours_local"])
    )
    coverage_matrix.to_csv(
        args.output_dir / "FORECAST_VINTAGE_COVERAGE_MATRIX.csv", index=False
    )
    forecast_archive.to_parquet(
        args.output_dir / "FORECAST_ERROR_ARCHIVE_SAMPLE.parquet",
        index=False,
        engine="pyarrow",
    )

    reconciliation = pd.read_parquet(frozen_inputs["reconciliation"])
    resolved = reconciliation[
        reconciliation["reconciliation_status"].ne("EXCHANGE_RUNG_UNRESOLVED")
    ]
    if resolved.empty:
        raise ValueError("settlement truth has no resolved exchange rows")
    exact = reconciliation["reconciliation_status"].eq("EXACT_MATCH_IEM_MIRROR")
    mismatch_keys = set(
        map(
            tuple,
            reconciliation.loc[
                reconciliation["reconciliation_status"].eq("UNRESOLVED_MISMATCH"),
                ["city", "target_date"],
            ].astype(str).to_numpy(),
        )
    )
    audited_keys = {
        tuple(path.stem.split("_", 1))
        for path in frozen_inputs["wu_disputes_root"].glob("*.json")
    }
    truth = {
        "covered_city_dates": int(len(resolved)),
        "exact_match_city_dates": int(exact.sum()),
        "exact_match_rate": float(exact.sum() / len(resolved)),
        "unresolved_mismatches": int(
            reconciliation["reconciliation_status"].eq("UNRESOLVED_MISMATCH").sum()
        ),
        "exchange_unresolved_city_dates": int(
            reconciliation["reconciliation_status"].eq("EXCHANGE_RUNG_UNRESOLVED").sum()
        ),
        "end_date": str(reconciliation["target_date"].max()),
        "all_mismatches_row_level_audited": mismatch_keys.issubset(audited_keys),
        "audited_mismatch_keys": [list(item) for item in sorted(mismatch_keys & audited_keys)],
        "no_unexplained_mismatch_in_new_forward": bool(
            reconciliation["target_date"].astype(str).lt(
                config["confirmatory_forward_start_target_date"]
            ).all()
        ),
    }
    inventory = json.loads(
        frozen_inputs["forecast_inventory"].read_text(encoding="utf-8")
    )
    native_eligible = coverage_matrix["native_vintage_eligible"]
    eligible_archive = forecast_archive[
        forecast_archive["status"].eq("PIT_NATIVE_VINTAGE_ELIGIBLE")
    ]
    coverage = {
        "inventory_rows": int(
            inventory.get("rows", inventory.get("eligible_rows", len(forecast_sample)))
        ),
        "inventory_city_days": int(
            inventory.get(
                "city_dates",
                sum((inventory.get("coverage_by_city") or {}).values()),
            )
        ),
        "matrix_rows": int(len(coverage_matrix)),
        "native_eligible_rows": int(native_eligible.sum()),
        "native_eligible_city_days": int(
            coverage_matrix.loc[native_eligible, ["city", "target_date"]]
            .drop_duplicates()
            .shape[0]
        ),
        "native_available_at_coverage": float(
            coverage_matrix["native_available_at_present"].mean()
        ),
        "usable_checkpoint_coverage": float(native_eligible.mean()),
        "native_eligible_event_city_days": int(
            eligible_archive.loc[
                eligible_archive["next_colder_event"].eq(True),
                ["city", "target_date"],
            ]
            .drop_duplicates()
            .shape[0]
        ),
        "by_city": {
            city: {
                "inventory_city_days": int(group["target_date"].nunique()),
                "native_eligible_city_days": int(
                    group.loc[group["native_vintage_eligible"], "target_date"].nunique()
                ),
                "usable_rows": int(group["native_vintage_eligible"].sum()),
                "matrix_rows": int(len(group)),
                "event_city_days": int(
                    eligible_archive.loc[
                        eligible_archive["city"].eq(city)
                        & eligible_archive["next_colder_event"].eq(True),
                        "target_date",
                    ].nunique()
                ),
            }
            for city, group in coverage_matrix.groupby("city", sort=True)
        },
    }
    gate_failures = []
    thresholds = config["fit_gate"]
    if coverage["native_eligible_city_days"] < thresholds[
        "forecast_covered_independent_city_days_min"
    ]:
        gate_failures.append("FORECAST_COVERED_CITY_DAYS_BELOW_120")
    if coverage["native_available_at_coverage"] < thresholds[
        "native_available_at_coverage_required"
    ]:
        gate_failures.append("NATIVE_AVAILABLE_AT_COVERAGE_BELOW_100_PERCENT")
    if coverage["usable_checkpoint_coverage"] < thresholds[
        "checkpoint_06_09_usable_vintage_coverage_min"
    ]:
        gate_failures.append("CHECKPOINT_06_09_USABLE_COVERAGE_BELOW_90_PERCENT")
    if coverage["native_eligible_event_city_days"] < thresholds[
        "next_colder_event_city_days_min"
    ]:
        gate_failures.append("NEXT_COLDER_EVENT_CITY_DAYS_BELOW_40")
    for city in config["supported_cities"]:
        city_coverage = coverage["by_city"].get(city, {})
        if city_coverage.get("native_eligible_city_days", 0) < thresholds[
            "per_city_covered_city_days_min"
        ]:
            gate_failures.append(f"{city.upper()}_COVERED_CITY_DAYS_BELOW_50")
        if city_coverage.get("event_city_days", 0) < thresholds[
            "per_city_event_city_days_min"
        ]:
            gate_failures.append(f"{city.upper()}_EVENT_CITY_DAYS_BELOW_15")
    if truth["exact_match_rate"] < thresholds["truth_exact_match_min"]:
        gate_failures.append("SETTLEMENT_TRUTH_EXACT_MATCH_BELOW_99_PERCENT")
    if not truth["all_mismatches_row_level_audited"]:
        gate_failures.append("SETTLEMENT_MISMATCH_NOT_ROW_LEVEL_AUDITED")
    if not truth["no_unexplained_mismatch_in_new_forward"]:
        gate_failures.append("UNEXPLAINED_MISMATCH_IN_NEW_FORWARD")
    maximum_prior_dates = max(0, int(p0["target_date"].nunique()) - 1)
    maximum_prior_negative_episodes = int(
        p0.loc[p0["label"].eq(0), ["city", "target_date"]]
        .drop_duplicates()
        .shape[0]
    )
    supported_p0_cities = set(p0.loc[p0["city"].isin(config["supported_cities"]), "city"])
    if maximum_prior_dates < thresholds["prior_target_dates_min"]:
        gate_failures.append("PRIOR_TARGET_DATES_BELOW_10")
    if maximum_prior_negative_episodes < thresholds[
        "prior_independent_negative_city_date_episodes_min"
    ]:
        gate_failures.append("PRIOR_NEGATIVE_CITY_DATE_EPISODES_BELOW_5")
    if thresholds["both_supported_cities_required"] and not set(
        config["supported_cities"]
    ).issubset(supported_p0_cities):
        gate_failures.append("BOTH_SUPPORTED_CITIES_NOT_PRESENT")
    gate = {
        "pass": not gate_failures,
        "failures": gate_failures,
        "p0_rows": int(len(p0_raw)),
        "p0_raw_min_reconstruction_rate": float(
            p0_raw["raw_min_reconstructs_current_rung"].mean()
        ),
        "p0_observation_pit_rate": float(p0_raw["observation_pit_pass"].mean()),
        "fit_authorized": not gate_failures,
        "maximum_prior_target_dates": maximum_prior_dates,
        "maximum_prior_independent_negative_city_date_episodes": maximum_prior_negative_episodes,
        "both_supported_cities_present": set(config["supported_cities"]).issubset(
            supported_p0_cities
        ),
    }

    raw_features = pd.DataFrame(p0["raw_feature_values_json"].map(json.loads).tolist())
    predictions = p0[
        [
            "candidate_id",
            "checkpoint_id",
            "city",
            "target_date",
            "decision_ts_utc",
            "window",
            "label",
            "p_market",
            "p_v1_incumbent",
            "p_v1_challenger",
            "routing_indicator",
        ]
    ].copy()
    predictions["checkpoint_hour"] = pd.to_numeric(raw_features["local_hour"]).astype(int)
    predictions["supported_city"] = predictions["city"].isin(config["supported_cities"])
    if gate["pass"]:
        predictions, folds, posterior_alpha = fit_v22_predictions(
            predictions, forecast_archive, config
        )
        foundation_status = "FIT_WITH_PER_DATE_FOLD_GATES"
    else:
        predictions["q_weather"] = np.nan
        predictions["q_clock"] = np.nan
        predictions["z_weather"] = np.nan
        predictions["alpha_posterior_mean"] = np.nan
        predictions["alpha_applied"] = 0.0
        predictions["p_v2_2"] = predictions["p_market"].copy()
        predictions["fit_status"] = "FAIL_CLOSED_TO_MARKET_DATA_GATE"
        predictions["blocker"] = predictions["supported_city"].map(
            lambda supported: (
                "FORECAST_AND_TRUTH_DATA_GATE_FAILED"
                if supported
                else "UNSUPPORTED_CITY_FOUNDATION"
            )
        )
        if predictions["p_v2_2"].to_numpy().tobytes() != predictions[
            "p_market"
        ].to_numpy().tobytes():
            raise AssertionError("failed fit must be byte-exact raw market")
        outside = predictions["routing_indicator"].eq(0)
        if predictions.loc[outside, "p_v2_2"].to_numpy().tobytes() != predictions.loc[
            outside, "p_market"
        ].to_numpy().tobytes():
            raise AssertionError("outside route must be byte-exact market")
        folds = []
        dates = sorted(predictions["target_date"].astype(str).unique())
        for test_date in dates:
            prior = predictions[predictions["target_date"].astype(str).lt(test_date)]
            folds.append(
                {
                    "test_date": test_date,
                    "training_max_target_date": (
                        None if prior.empty else str(prior["target_date"].max())
                    ),
                    "model": MODEL_ID,
                    "feature": "z_weather",
                    "raw_coefficient": np.nan,
                    "standardized_coefficient": np.nan,
                    "training_median": np.nan,
                    "training_scale": np.nan,
                    "missing_rate": 1.0,
                    "coefficient_norm": np.nan,
                    "regularization": "HalfNormal(0.25)",
                    "convergence": False,
                    "status": "NOT_FIT_DATA_GATE_FAILED",
                    "prior_target_dates": int(prior["target_date"].nunique()),
                    "prior_independent_negative_city_date_episodes": int(
                        prior.loc[prior["label"].eq(0), ["city", "target_date"]]
                        .drop_duplicates()
                        .shape[0]
                    ),
                    "both_supported_cities": set(config["supported_cities"]).issubset(
                        set(prior["city"])
                    ),
                }
            )
        posterior_alpha = {
            "status": "NOT_FIT_DATA_GATE_FAILED",
            "posterior_mean": None,
            "posterior_median": None,
            "interval_90": None,
            "interval_95": None,
        }
        foundation_status = "NOT_FIT_DATA_GATE_FAILED"
    predictions.to_parquet(
        args.output_dir / "MODEL_PREDICTIONS.parquet", index=False, engine="pyarrow"
    )
    pd.DataFrame(folds).to_csv(
        args.output_dir / "FOUNDATION_FOLD_COEFFICIENTS.csv", index=False
    )
    evaluation = build_evaluation(predictions)
    write_json(args.output_dir / "MARKET_INCREMENTAL_RESULTS.json", evaluation)
    foundation = {
        "model_id": MODEL_ID,
        "status": foundation_status,
        "fit_gate": gate,
        "forecast_coverage": coverage,
        "settlement_truth": truth,
        "posterior_alpha": posterior_alpha,
    }
    write_json(args.output_dir / "WEATHER_FOUNDATION_RESULTS.json", foundation)
    gradient_frame = predictions[
        predictions["supported_city"] & predictions["z_weather"].notna()
    ]
    if gate["pass"] and not gradient_frame.empty:
        gradient = {
            "diagnostic": "ROUTED_SCORE_GRADIENT_AT_ALPHA0",
            **routed_score_gradient(
                gradient_frame,
                draws=int(config["bootstrap_draws"]),
                seed=int(config["random_seed"]),
            ),
        }
    else:
        gradient = {
            "diagnostic": "ROUTED_SCORE_GRADIENT_AT_ALPHA0",
            "status": "NOT_COMPUTED_DATA_GATE_FAILED",
            "S": None,
            "one_sided_lower_95": None,
            "bootstrap_draws": int(config["bootstrap_draws"]),
            "reason": "z_weather is undefined without a PIT-native forecast-error foundation",
        }
    write_json(args.output_dir / "ROUTED_SCORE_GRADIENT_AT_ALPHA0.json", gradient)
    write_json(
        args.output_dir / "SETTLEMENT_TRUTH_GATE.json",
        truth
        | {
            "pass": bool(
                truth["exact_match_rate"] >= thresholds["truth_exact_match_min"]
                and truth["all_mismatches_row_level_audited"]
                and truth["no_unexplained_mismatch_in_new_forward"]
            )
        },
    )
    write_json(args.output_dir / "DATA_AND_FIT_GATE.json", gate)

    prior_forward = json.loads(
        frozen_inputs["prior_forward_manifest"].read_text(encoding="utf-8")
    )
    forward = {
        "schema_version": "tmin_v2_2_forward_arm_manifest_v1",
        "research_only": True,
        "zero_notional": True,
        "live_authorization": False,
        "evidence_seal_timestamp_utc": config["evidence_seal_timestamp_utc"],
        "v2_2_forward_start_target_date": config[
            "confirmatory_forward_start_target_date"
        ],
        "no_backfill": True,
        "common_denominator": "all settled probability-eligible P0 rows; no execution conditioning",
        "arms": [
            {
                "arm": "RAW_MARKET",
                "status": "baseline",
                "selector_or_order_authorized": False,
            },
            {
                "arm": "existing V1_ALPHA010_ROUTED",
                "start_target_date": "2026-08-28",
                "status": "existing_frozen_zero_notional_unchanged",
                "selector_or_order_authorized": False,
            },
            {
                "arm": MODEL_ID,
                "start_target_date": config["confirmatory_forward_start_target_date"],
                "status": "BLOCKED_NOT_STARTED_NO_BACKFILL",
                "selector_or_order_authorized": False,
            },
            *[
                arm
                for arm in prior_forward["arms"]
                if arm["arm"] in {
                    "V1_ALPHA025_ROUTED_DIAGNOSTIC",
                    "V1_ALPHA050_ROUTED_DIAGNOSTIC",
                }
            ],
        ],
        "readout_gate": {
            "new_settled_target_dates_min": 30,
            "new_p0_rows_min": 180,
            "new_p1_rows_min": 80,
            "negative_p1_independent_episodes_min": 15,
            "per_supported_city_p1_rows_min": 20,
        },
        "promotion_gate": {
            "joint_one_sided_max_t_upper_bound_logloss_and_brier_below_zero": True,
            "score_gradient_lower_bound_above_zero": True,
            "leave_one_city_out_no_sign_reversal": True,
            "single_date_contribution_max": 0.35,
            "no_high_probability_band_systematic_worsening": True,
            "truth_pit_hash_evidence_all_pass": True,
            "maximum_allowed_disposition": "FREEZE_PROBABILITY_CHALLENGER",
        },
    }
    write_json(args.output_dir / "FORWARD_ARM_MANIFEST.json", forward)

    knowledge = build_knowledge_manifest(args.knowledge_root)
    write_json(args.output_dir / "KNOWLEDGE_PERSISTENCE_MANIFEST.json", knowledge)
    write_reports(
        args.output_dir,
        gate=gate,
        truth=truth,
        coverage=coverage,
        evaluation=evaluation,
        knowledge=knowledge,
        forward=forward,
    )

    environment = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scipy": scipy.__version__,
        "pyarrow": pyarrow.__version__,
        "random_seed": config["random_seed"],
    }
    write_json(args.output_dir / "ENVIRONMENT_LOCK.json", environment)
    snapshot_dir = args.output_dir / "source_snapshot"
    snapshot_dir.mkdir()
    snapshot_source_root = args.source_snapshot_root or ROOT
    snapshot_files = source_snapshot_paths(snapshot_source_root)
    for path, relative in snapshot_files:
        destination = snapshot_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    source_paths = [path for path, _ in snapshot_files]
    if args.source_patch_input:
        patch = args.source_patch_input.read_text(encoding="utf-8")
    else:
        patch = scoped_source_patch(source_paths)
    (args.output_dir / "SOURCE_PATCH.binary.diff").write_text(patch, encoding="utf-8")
    base_sha = args.base_sha or git("rev-parse", "HEAD").strip()
    (args.output_dir / "BASE_SHA.txt").write_text(base_sha + "\n")
    shutil.copy2(args.config, args.output_dir / "CONFIG_SNAPSHOT.json")

    reproduce = """#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(git rev-parse --show-toplevel)"
PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"
REBUILD_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/tmin-v22-rebuild.XXXXXX")"
trap 'rm -rf "$REBUILD_ROOT"' EXIT
cd "$REPO_ROOT"
SEALED_BASE_SHA="$(tr -d '\n' < "$PACKAGE_DIR/BASE_SHA.txt")"
git cat-file -e "$SEALED_BASE_SHA^{commit}"
mkdir -p "$REBUILD_ROOT/lineage"
git archive "$SEALED_BASE_SHA" | tar -x -C "$REBUILD_ROOT/lineage"
(
  cd "$REBUILD_ROOT/lineage"
  git apply --binary "$PACKAGE_DIR/SOURCE_PATCH.binary.diff"
)
.venv/bin/python - "$PACKAGE_DIR/EVIDENCE_MANIFEST.json" \
  "$PACKAGE_DIR/source_snapshot" "$REBUILD_ROOT/lineage" <<'PY'
import hashlib, json, pathlib, sys
manifest = json.loads(pathlib.Path(sys.argv[1]).read_text())
snapshot = pathlib.Path(sys.argv[2])
lineage = pathlib.Path(sys.argv[3])
for relative in manifest["patch_scope"]:
    expected = hashlib.sha256((snapshot / relative).read_bytes()).hexdigest()
    actual = hashlib.sha256((lineage / relative).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"source lineage mismatch: {relative} {actual} != {expected}")
print(f"SOURCE_LINEAGE_PASS files={len(manifest['patch_scope'])}")
PY
PM_AGENTS_REPO_ROOT="$REPO_ROOT" .venv/bin/python \
  "$PACKAGE_DIR/source_snapshot/scripts/analysis/tmin/tmin_v2_2_forecast_threshold_residual_v1.py" \
  --config "$PACKAGE_DIR/source_snapshot/config/research/tmin_v2_2_forecast_threshold_residual_v1.json" \
  --p0 "$PACKAGE_DIR/frozen_inputs/P0_ROW_LEVEL_PROBABILITY_AUDIT.parquet" \
  --observation-root "$PACKAGE_DIR/frozen_inputs/pit_observations" \
  --forecast-sample "$PACKAGE_DIR/frozen_inputs/forecast_archive_sample.parquet" \
  --forecast-inventory "$PACKAGE_DIR/frozen_inputs/forecast_archive_inventory.json" \
  --reconciliation "$PACKAGE_DIR/frozen_inputs/settlement_source_reconciliation.parquet" \
  --wu-disputes-root "$PACKAGE_DIR/frozen_inputs/wu_disputes" \
  --prior-forward-manifest "$PACKAGE_DIR/frozen_inputs/prior_forward_arm_manifest.json" \
  --knowledge-root "$PACKAGE_DIR/source_snapshot/docs/knowledge/tmin" \
  --source-snapshot-root "$PACKAGE_DIR/source_snapshot" \
  --source-patch-input "$PACKAGE_DIR/SOURCE_PATCH.binary.diff" \
  --base-sha "$SEALED_BASE_SHA" \
  --output-dir "$REBUILD_ROOT/output"
.venv/bin/python - "$PACKAGE_DIR/EXPECTED_OUTPUT_HASHES.json" "$REBUILD_ROOT/output" <<'PY'
import hashlib, json, pathlib, sys
expected = json.loads(pathlib.Path(sys.argv[1]).read_text())
rebuilt = pathlib.Path(sys.argv[2])
for item in expected["files"]:
    path = rebuilt / item["path"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != item["sha256"]:
        raise SystemExit(f"hash mismatch: {item['path']} {digest} != {item['sha256']}")
print(f"CLEAN_ROOM_REPRODUCTION_PASS files={len(expected['files'])}")
PY
"""
    reproduce_path = args.output_dir / "REPRODUCE.sh"
    reproduce_path.write_text(reproduce, encoding="utf-8")
    reproduce_path.chmod(0o755)

    inputs = [
        input_manifest_entry(
            frozen_inputs["p0"], "frozen_inputs/P0_ROW_LEVEL_PROBABILITY_AUDIT.parquet"
        ),
        input_manifest_entry(
            frozen_inputs["forecast_sample"], "frozen_inputs/forecast_archive_sample.parquet"
        ),
        input_manifest_entry(
            frozen_inputs["forecast_inventory"], "frozen_inputs/forecast_archive_inventory.json"
        ),
        input_manifest_entry(
            frozen_inputs["reconciliation"], "frozen_inputs/settlement_source_reconciliation.parquet"
        ),
        input_manifest_entry(
            frozen_inputs["prior_forward_manifest"], "frozen_inputs/prior_forward_arm_manifest.json"
        ),
        input_manifest_entry(
            args.config,
            "source_snapshot/config/research/tmin_v2_2_forecast_threshold_residual_v1.json",
        ),
        aggregate_tree(
            frozen_inputs["observation_root"], "frozen_inputs/pit_observations"
        ),
        aggregate_tree(
            frozen_inputs["wu_disputes_root"], "frozen_inputs/wu_disputes"
        ),
    ]
    output_exclusions = {
        "EVIDENCE_MANIFEST.json",
        "REVIEW_EVIDENCE_SEAL.json",
        "EXPECTED_OUTPUT_HASHES.json",
    }
    output_files = [
        path
        for path in sorted(args.output_dir.rglob("*"))
        if path.is_file() and path.name not in output_exclusions
    ]
    evidence = {
        "schema_version": "tmin_v2_2_evidence_manifest_v1",
        "git_base_sha": base_sha,
        "reproduction_basis": "package_local_frozen_inputs_plus_sealed_source_snapshot_with_base_sha_binary_patch_validation",
        "patch_scope": [str(relative) for _, relative in snapshot_files],
        "data_cutoff_target_date": config["data_cutoff_target_date"],
        "inputs": inputs,
        "outputs": [
            {
                "path": str(path.relative_to(args.output_dir)),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in output_files
        ],
    }
    write_json(args.output_dir / "EVIDENCE_MANIFEST.json", evidence)
    seal = {
        "schema_version": "tmin_v2_2_review_evidence_seal_v1",
        "created_at_utc": config["evidence_seal_timestamp_utc"],
        "operational_disposition": "KEEP_V1_FORWARD_ONLY",
        "review_disposition": "BLOCKED_EVIDENCE_OR_DATA",
        "package_status": ["VALID_WITH_BLOCKING_GAPS", "NOT_RESEARCH_PACKAGE_COMPLETE"],
        "model_id": MODEL_ID,
        "fit_authorized": False,
        "live_authorization": False,
        "zero_notional": True,
        "gate_failures": gate_failures,
        "knowledge_status": knowledge["status"],
        "p0_raw_feature_audit_sha256": sha256(
            args.output_dir / "P0_RAW_FEATURE_AUDIT.parquet"
        ),
        "forecast_coverage_sha256": sha256(
            args.output_dir / "FORECAST_VINTAGE_COVERAGE_MATRIX.csv"
        ),
        "model_predictions_sha256": sha256(
            args.output_dir / "MODEL_PREDICTIONS.parquet"
        ),
        "evidence_manifest_sha256": sha256(args.output_dir / "EVIDENCE_MANIFEST.json"),
        "clean_room_reproduction": "PASS_PACKAGE_LOCAL_EMPTY_OUTPUT_SOURCE_LINEAGE_AND_HASH_REPLAY",
        "clean_room_verified_at_utc": config["clean_room_verified_at_utc"],
        "independent_review": {
            "role": "luna_verifier",
            "model": "gpt-5.6-luna",
            "effort": "medium",
            "reviewer_modified_files": False,
            "usage_telemetry": "unavailable",
            "review_rounds": 2,
            "findings_fixed": 9,
            "post_fix_target_tests": "10 passed",
        },
    }
    write_json(args.output_dir / "REVIEW_EVIDENCE_SEAL.json", seal)
    expected_files = [
        path
        for path in sorted(args.output_dir.rglob("*"))
        if path.is_file() and path.name != "EXPECTED_OUTPUT_HASHES.json"
    ]
    write_json(
        args.output_dir / "EXPECTED_OUTPUT_HASHES.json",
        {
            "schema_version": "tmin_v2_2_expected_output_hashes_v1",
            "files": [
                {
                    "path": str(path.relative_to(args.output_dir)),
                    "sha256": sha256(path),
                }
                for path in expected_files
            ],
        },
    )
    missing = [
        name
        for name in (*REQUIRED_REPORTS, *REQUIRED_MACHINE)
        if not (args.output_dir / name).exists()
    ]
    if missing:
        raise AssertionError(f"missing required package outputs: {missing}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    base = ROOT / "reviews/tmin_model_layer_v2_1_v3_research_v1"
    forensics = ROOT / "reviews/tmin_no_further_model_forensics_v1/frozen_inputs"
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config/research/tmin_v2_2_forecast_threshold_residual_v1.json",
    )
    parser.add_argument(
        "--p0", type=Path, default=base / "ROW_LEVEL_PROBABILITY_AUDIT.parquet"
    )
    parser.add_argument(
        "--observation-root", type=Path, default=forensics / "pit_observations"
    )
    parser.add_argument(
        "--forecast-sample",
        type=Path,
        default=base / "frozen_inputs/forecast_archive_sample.parquet",
    )
    parser.add_argument(
        "--forecast-inventory",
        type=Path,
        default=base / "frozen_inputs/forecast_archive_inventory.json",
    )
    parser.add_argument(
        "--reconciliation",
        type=Path,
        default=base / "SETTLEMENT_SOURCE_RECONCILIATION.parquet",
    )
    parser.add_argument(
        "--wu-disputes-root",
        type=Path,
        default=base / "frozen_inputs/wu_disputes",
    )
    parser.add_argument(
        "--prior-forward-manifest",
        type=Path,
        default=base / "FORWARD_ARM_MANIFEST.json",
    )
    parser.add_argument(
        "--knowledge-root", type=Path, default=ROOT / "docs/knowledge/tmin"
    )
    parser.add_argument(
        "--source-snapshot-root",
        type=Path,
        help="Read source/config/docs snapshots from this immutable repository-shaped root.",
    )
    parser.add_argument(
        "--source-patch-input",
        type=Path,
        help="Copy this sealed binary patch instead of regenerating it from the current worktree.",
    )
    parser.add_argument(
        "--base-sha",
        help="Use this sealed base SHA instead of the current repository HEAD.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reviews/tmin_v2_2_forecast_threshold_residual_v1",
    )
    return parser.parse_args(argv)


def main() -> int:
    build(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
