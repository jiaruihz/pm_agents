#!/usr/bin/env python3
"""Evaluate the frozen Tmin no-further-cooling zero-notional shadow.

The runtime journal is snapshotted at read time and filtered to one immutable
model artifact.  Settlement truth is joined by condition_id from the canonical
weather DB; city/date/bracket joins are deliberately not supported.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.city_calendar import CITY_TIMEZONE
from weather_model_evaluation.probability import (
    binary_loss_values,
    date_block_bootstrap_delta,
)


SCHEMA_VERSION = "weather_tmin_no_further_cooling_shadow_performance_v2"
FEE_RATE = 0.05
BOOTSTRAP_DRAWS = 20_000
BOOTSTRAP_SEED = 20260824
CHALLENGER_POLICY_ID = "tmin_no_further_cooling_window_routed_alpha010_v1"
CHALLENGER_ALPHA = 0.10
CHALLENGER_ACTIVE_WINDOWS = frozenset(
    {"morning_cooling", "post_sunrise_provisional_low"}
)
CHALLENGER_DEVELOPMENT_END_TARGET_DATE = "2026-08-26"
CHALLENGER_FORWARD_START_TARGET_DATE = "2026-08-28"
CHALLENGER_TEMPORAL_SPLITS = {
    "early_2026-08-12_to_2026-08-20": ("2026-08-12", "2026-08-20"),
    "late_2026-08-21_to_2026-08-26": ("2026-08-21", "2026-08-26"),
}


def _snapshot_jsonl(
    path: Path, *, snapshot_size_bytes: int | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_size = path.stat().st_size
    size = source_size if snapshot_size_bytes is None else min(source_size, snapshot_size_bytes)
    with path.open("rb") as handle:
        payload = handle.read(size)
    complete_size = len(payload) if payload.endswith(b"\n") else payload.rfind(b"\n") + 1
    complete = payload[:complete_size]
    rows = [json.loads(line) for line in complete.decode("utf-8").splitlines() if line]
    return rows, {
        "path": str(path),
        "source_size_at_read_bytes": source_size,
        "snapshot_size_bytes": size,
        "complete_size_bytes": complete_size,
        "complete_sha256": hashlib.sha256(complete).hexdigest(),
        "rows": len(rows),
    }


def _db_identity(path: Path, connection: sqlite3.Connection) -> dict[str, Any]:
    stat = os.stat(path)
    max_fact = connection.execute("SELECT MAX(fact_built_at_utc) FROM fact_trades").fetchone()[0]
    max_settlement = connection.execute("SELECT MAX(target_date) FROM settlements").fetchone()[0]
    return {
        "path": str(path),
        "realpath": str(path.resolve()),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size_bytes": stat.st_size,
        "fact_trades_max_fact_built_at_utc": max_fact,
        "settlements_max_target_date": max_settlement,
    }


def _settlement_map(
    connection: sqlite3.Connection, condition_ids: list[str]
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    unique = sorted(set(condition_ids))
    for start in range(0, len(unique), 500):
        chunk = unique[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"""SELECT condition_id, target_date, bracket, final_price,
                       settlement_status, created_at_utc
                FROM settlements WHERE condition_id IN ({placeholders})""",
            chunk,
        ).fetchall()
        for condition_id, target_date, bracket, final_price, status, created_at in rows:
            output[str(condition_id)] = {
                "target_date": str(target_date),
                "bracket": str(bracket),
                "label": float(final_price),
                "status": str(status),
                "created_at_utc": str(created_at),
            }
    return output


def _fee(price: float) -> float:
    if not np.isfinite(price) or not 0.0 <= price <= 1.0:
        raise ValueError(f"price must be finite and within [0, 1], got {price}")
    return FEE_RATE * price * (1.0 - price)


def _window_routed_probability(
    market_probability: pd.Series,
    physical_innovation_logit: pd.Series,
    cooling_window_state: pd.Series,
) -> pd.Series:
    """Apply a small physical residual only while cooling still controls Tmin.

    Outside the two pre-registered cooling windows the challenger is exactly
    the market probability.  This prevents the incumbent's physical residual
    from overriding the market after daytime warming or late finalization.
    """

    market_raw = market_probability.astype(float).to_numpy()
    innovation = physical_innovation_logit.astype(float).to_numpy()
    if (
        not np.isfinite(market_raw).all()
        or (market_raw < 0.0).any()
        or (market_raw > 1.0).any()
    ):
        raise ValueError("market probability must be finite and within [0, 1]")
    if not np.isfinite(innovation).all():
        raise ValueError("physical innovation logit must be finite")
    market = np.clip(market_raw, 1e-8, 1 - 1e-8)
    active = cooling_window_state.astype(str).isin(CHALLENGER_ACTIVE_WINDOWS).to_numpy()
    logit = np.log(market / (1.0 - market)) + CHALLENGER_ALPHA * innovation * active
    return pd.Series(1.0 / (1.0 + np.exp(-logit)), index=market_probability.index)


def _probability_comparison(
    frame: pd.DataFrame,
    *,
    candidate_column: str,
    baseline_column: str,
    seed: int,
) -> dict[str, Any]:
    if frame.empty:
        return {
            "rows": 0,
            "target_dates": 0,
            "candidate_logloss": None,
            "baseline_logloss": None,
            "candidate_brier": None,
            "baseline_brier": None,
            "candidate_minus_baseline_logloss": None,
            "candidate_minus_baseline_brier": None,
        }
    labels = frame["label"].astype(int).to_numpy()
    candidate_probability = frame[candidate_column].astype(float).to_numpy()
    baseline_probability = frame[baseline_column].astype(float).to_numpy()
    candidate_logloss = binary_loss_values(
        labels, candidate_probability, metric="logloss"
    )
    baseline_logloss = binary_loss_values(
        labels, baseline_probability, metric="logloss"
    )
    candidate_brier = binary_loss_values(labels, candidate_probability, metric="brier")
    baseline_brier = binary_loss_values(labels, baseline_probability, metric="brier")
    return {
        "rows": int(len(frame)),
        "target_dates": int(frame["target_date"].nunique()),
        "candidate_logloss": float(np.mean(candidate_logloss)),
        "baseline_logloss": float(np.mean(baseline_logloss)),
        "candidate_brier": float(np.mean(candidate_brier)),
        "baseline_brier": float(np.mean(baseline_brier)),
        "candidate_minus_baseline_logloss": date_block_bootstrap_delta(
            frame,
            candidate_logloss,
            baseline_logloss,
            draws=BOOTSTRAP_DRAWS,
            seed=seed,
        ),
        "candidate_minus_baseline_brier": date_block_bootstrap_delta(
            frame,
            candidate_brier,
            baseline_brier,
            draws=BOOTSTRAP_DRAWS,
            seed=seed + 1,
        ),
    }


def _execution_book_evidence(row: pd.Series, shares: float = 5.0) -> dict[str, Any]:
    """Reconstruct the exact PIT YES book referenced by one candidate."""

    references = [
        item
        for item in (row.get("input_refs") or [])
        if str(item.get("kind") or "") == "market_book_batch"
    ]
    if not references:
        return {"book_evidence_status": "missing_market_book_ref"}
    path = Path(str(references[-1].get("path") or ""))
    if not path.exists():
        return {"book_evidence_status": "missing_market_book_file", "book_path": str(path)}

    decision = pd.Timestamp(row["decision_ts_utc"])
    matches: list[tuple[pd.Timestamp, dict[str, Any]]] = []
    with gzip.open(path, mode="rt", encoding="utf-8") as handle:
        for line in handle:
            candidate = json.loads(line)
            if (
                str(candidate.get("condition_id") or "") != str(row.get("condition_id") or "")
                or str(candidate.get("outcome") or "").lower() != "yes"
            ):
                continue
            available_raw = candidate.get("available_at_utc") or candidate.get("fetched_at_utc")
            if not available_raw:
                continue
            available = pd.Timestamp(available_raw)
            if available.tzinfo is None:
                available = available.tz_localize("UTC")
            else:
                available = available.tz_convert("UTC")
            if available <= decision:
                matches.append((available, candidate))
    if not matches:
        return {"book_evidence_status": "no_pit_yes_book_match", "book_path": str(path)}

    available, source = max(matches, key=lambda item: item[0])
    summary = source.get("summary") or {}
    levels = summary.get("asks") or (source.get("raw") or {}).get("asks") or []
    asks = sorted(
        (
            {"price": float(level["price"]), "size": float(level["size"])}
            for level in levels
            if level.get("price") is not None and level.get("size") is not None
        ),
        key=lambda level: level["price"],
    )
    remaining = float(shares)
    notional = 0.0
    fees = 0.0
    for level in asks:
        take = min(remaining, level["size"])
        notional += take * level["price"]
        fees += take * _fee(level["price"])
        remaining -= take
        if remaining <= 1e-12:
            break
    fillable = remaining <= 1e-12
    best_ask = None if not asks else asks[0]["price"]
    candidate_ask = float(row["executable_cost"])
    top_matches_candidate = best_ask is not None and abs(best_ask - candidate_ask) <= 1e-12
    return {
        "book_evidence_status": "available",
        "book_path": str(path),
        "book_capture_id": source.get("book_capture_id"),
        "execution_snapshot_id_matches_book_capture_id": (
            str(row.get("execution_book_snapshot_id") or "")
            == str(source.get("book_capture_id") or "")
        ),
        "book_available_at_utc": available.isoformat(),
        "book_age_minutes": float((decision - available).total_seconds() / 60.0),
        "best_ask": best_ask,
        "best_ask_size": None if not asks else asks[0]["size"],
        "depth_ask_5c": summary.get("depth_ask_5c"),
        "depth_ask_10c": summary.get("depth_ask_10c"),
        "spread": summary.get("spread"),
        "candidate_ask_matches_book": top_matches_candidate,
        "five_share_fillable": fillable,
        "five_share_vwap": notional / shares if fillable else None,
        "five_share_fee_per_share": fees / shares if fillable else None,
        "five_share_slippage_vs_candidate_ask": (
            notional / shares - candidate_ask if fillable else None
        ),
    }


def _attach_execution_book_evidence(frame: pd.DataFrame) -> pd.DataFrame:
    defaults: dict[str, Any] = {
        "book_evidence_status": "missing_market_book_ref",
        "execution_snapshot_id_matches_book_capture_id": False,
        "candidate_ask_matches_book": False,
        "five_share_fillable": False,
        "five_share_vwap": None,
        "five_share_fee_per_share": None,
        "five_share_slippage_vs_candidate_ask": None,
        "best_ask_size": None,
        "book_age_minutes": None,
    }
    output = frame.copy()
    if output.empty:
        for column, default in defaults.items():
            output[column] = pd.Series(index=output.index, dtype="object")
            if default is not None:
                output[column] = output[column].fillna(default)
        return output
    evidence_rows = []
    for _, row in output.iterrows():
        evidence = defaults | _execution_book_evidence(row)
        evidence_rows.append(evidence)
    return output.join(pd.DataFrame(evidence_rows, index=output.index))


def _local_hour(row: pd.Series) -> int:
    timestamp = pd.Timestamp(row["decision_ts_utc"])
    return int(timestamp.tz_convert(ZoneInfo(CITY_TIMEZONE[str(row["city"])])).hour)


def _wilson_interval(wins: int, total: int) -> list[float | None]:
    if total == 0:
        return [None, None]
    z = 1.959963984540054
    p = wins / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    half = z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total)) / denominator
    return [center - half, center + half]


def _roi_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {
            "rows": 0,
            "active_target_dates": 0,
            "wins": 0,
            "losses": 0,
            "cost_per_share": 0.0,
            "fee_per_share": 0.0,
            "pnl_per_share": 0.0,
            "roi": None,
            "target_date_bootstrap_ci95": [None, None],
        }
    cost = float(frame["cost_per_share"].sum())
    pnl = float(frame["pnl_per_share"].sum())
    if not np.isfinite(cost) or cost <= 0:
        return {
            "rows": int(len(frame)),
            "active_target_dates": int(frame["target_date"].nunique()),
            "wins": int(frame["label"].sum()),
            "losses": int(len(frame) - frame["label"].sum()),
            "cost_per_share": cost,
            "fee_per_share": float(frame["fee_per_share"].sum()),
            "pnl_per_share": pnl,
            "roi": None,
            "target_date_bootstrap_ci95": [None, None],
            "evaluation_status": "not_evaluable_nonpositive_cost",
        }
    daily = frame.groupby("target_date", sort=True)[["pnl_per_share", "cost_per_share"]].sum()
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    indices = rng.integers(0, len(daily), size=(BOOTSTRAP_DRAWS, len(daily)))
    sampled_pnl = daily["pnl_per_share"].to_numpy()[indices].sum(axis=1)
    sampled_cost = daily["cost_per_share"].to_numpy()[indices].sum(axis=1)
    draws = sampled_pnl / sampled_cost
    low, high = np.quantile(draws, [0.025, 0.975])
    wins = int(frame["label"].sum())
    return {
        "rows": int(len(frame)),
        "active_target_dates": int(frame["target_date"].nunique()),
        "city_dates": int(frame[["city", "target_date"]].drop_duplicates().shape[0]),
        "wins": wins,
        "losses": int(len(frame) - wins),
        "win_rate": wins / len(frame),
        "win_rate_wilson_ci95": _wilson_interval(wins, len(frame)),
        "cost_per_share": cost,
        "fee_per_share": float(frame["fee_per_share"].sum()),
        "pnl_per_share": pnl,
        "five_share_cost_usd": 5.0 * cost,
        "five_share_pnl_usd": 5.0 * pnl,
        "roi": pnl / cost,
        "target_date_bootstrap_ci95": [float(low), float(high)],
    }


def _breakdown(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    output = []
    for value, group in frame.groupby(column, sort=True, dropna=False, observed=True):
        summary = _roi_summary(group)
        output.append({column: str(value), **summary})
    return output


def _require_candidate_frame_schema(frame: pd.DataFrame) -> None:
    required = {
        "candidate_id",
        "checkpoint_id",
        "city",
        "target_date",
        "decision_ts_utc",
        "condition_id",
        "market_id",
        "token_id",
        "bracket",
        "feature_book_snapshot_id",
        "candidate_status",
        "blocker_reason",
        "p_model",
        "market_p",
        "selected",
        "executable_cost",
        "market_evidence_status",
        "model_id",
        "policy_id",
        "input_refs",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"candidate journal missing required columns: {missing}")
    for identity_column in ("candidate_id", "checkpoint_id"):
        if frame[identity_column].astype(str).duplicated().any():
            duplicate = frame.loc[
                frame[identity_column].astype(str).duplicated(keep=False), identity_column
            ].iloc[0]
            raise ValueError(f"duplicate {identity_column}: {duplicate}")


def _pit_probability_identity_legal(frame: pd.DataFrame) -> pd.Series:
    """Book-independent PIT/identity contract for probability scoring."""

    identity_columns = [
        "candidate_id",
        "checkpoint_id",
        "condition_id",
        "market_id",
        "token_id",
        "bracket",
        "feature_book_snapshot_id",
    ]
    identity_ok = frame[identity_columns].notna().all(axis=1)
    def refs_are_legal(row: pd.Series) -> bool:
        try:
            decision = pd.Timestamp(row["decision_ts_utc"])
        except (TypeError, ValueError):
            return False
        if decision.tzinfo is None:
            return False
        required = {"observation_archive", "forecast_curve"}
        seen: set[str] = set()
        for item in row.get("input_refs") or []:
            kind = str(item.get("kind") or "")
            if kind not in required:
                continue
            try:
                available = pd.Timestamp(item.get("available_at_utc"))
            except (TypeError, ValueError):
                return False
            if available.tzinfo is None or available > decision:
                return False
            seen.add(kind)
        return seen == required

    pit_refs_ok = frame.apply(refs_are_legal, axis=1)
    return identity_ok & pit_refs_ok


def evaluate(
    *,
    candidates_path: Path,
    db_path: Path,
    artifact_id: str,
    snapshot_size_bytes: int | None = None,
    development_snapshot_size_bytes: int | None = None,
    model_outputs_path: Path | None = None,
    model_outputs_snapshot_size_bytes: int | None = None,
) -> dict[str, Any]:
    raw_rows, snapshot = _snapshot_jsonl(
        candidates_path, snapshot_size_bytes=snapshot_size_bytes
    )
    development_raw_rows, development_snapshot = _snapshot_jsonl(
        candidates_path,
        snapshot_size_bytes=(
            development_snapshot_size_bytes
            if development_snapshot_size_bytes is not None
            else snapshot_size_bytes
        ),
    )
    rows = [row for row in raw_rows if str(row.get("model_artifact_id") or "") == artifact_id]
    development_candidate_ids = {
        str(row.get("candidate_id") or "")
        for row in development_raw_rows
        if str(row.get("model_artifact_id") or "") == artifact_id
        and str(row.get("target_date") or "")
        <= CHALLENGER_DEVELOPMENT_END_TARGET_DATE
    }
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError(f"model artifact not present in journal: {artifact_id}")
    _require_candidate_frame_schema(frame)
    try:
        frame["decision_ts_utc"] = pd.to_datetime(
            frame["decision_ts_utc"], utc=True, errors="raise"
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate journal contains invalid decision_ts_utc") from exc
    frame["local_hour"] = frame.apply(_local_hour, axis=1)
    frame["beijing_hour"] = frame["decision_ts_utc"].dt.tz_convert("Asia/Shanghai").dt.hour

    model_output_snapshot = None
    if model_outputs_path is not None:
        model_rows, model_output_snapshot = _snapshot_jsonl(
            model_outputs_path,
            snapshot_size_bytes=model_outputs_snapshot_size_bytes,
        )
        model_rows = [
            row
            for row in model_rows
            if str(row.get("model_artifact_id") or "") == artifact_id
        ]
        model_frame = pd.DataFrame(
            [
                {
                    "checkpoint_id": row.get("checkpoint_id"),
                    "output_p_model": row.get("p_model"),
                    "physical_innovation_logit": (row.get("metadata") or {}).get(
                        "physical_innovation_logit"
                    ),
                    "cooling_window_state": (row.get("metadata") or {}).get(
                        "cooling_window_state"
                    ),
                }
                for row in model_rows
            ]
        )
        if model_frame.empty:
            raise ValueError(
                f"model artifact not present in model output journal: {artifact_id}"
            )
        if model_frame["checkpoint_id"].duplicated().any():
            duplicates = sorted(
                str(value)
                for value in model_frame.loc[
                    model_frame["checkpoint_id"].duplicated(keep=False), "checkpoint_id"
                ].unique()
            )
            raise ValueError(f"duplicate model output checkpoint ids: {duplicates[:5]}")
        frame = frame.merge(
            model_frame,
            on="checkpoint_id",
            how="left",
            validate="one_to_one",
        )

    condition_ids = [str(value) for value in frame["condition_id"].dropna().unique()]
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 1000")
    try:
        identity = _db_identity(db_path, connection)
        settlements = _settlement_map(connection, condition_ids)
    finally:
        connection.close()

    frame["label"] = frame["condition_id"].map(
        lambda value: settlements.get(str(value), {}).get("label") if pd.notna(value) else None
    )
    frame["settlement_status"] = frame["condition_id"].map(
        lambda value: settlements.get(str(value), {}).get("status") if pd.notna(value) else None
    )
    probability_complete = frame[
        frame["p_model"].notna() & frame["market_p"].notna()
    ].copy()
    for column in ("p_model", "market_p"):
        values = probability_complete[column].astype(float)
        if not np.isfinite(values).all() or not values.between(0.0, 1.0).all():
            raise ValueError(f"probability-complete {column} must be finite and within [0, 1]")
    executable_cost = pd.to_numeric(frame["executable_cost"], errors="coerce")
    invalid_cost = frame["executable_cost"].notna() & (
        ~np.isfinite(executable_cost) | ~executable_cost.between(0.0, 1.0)
    )
    if invalid_cost.any():
        raise ValueError("executable_cost must be finite and within [0, 1]")
    probability_complete["pit_probability_identity_legal"] = (
        _pit_probability_identity_legal(probability_complete)
    )
    paired = probability_complete[
        probability_complete["settlement_status"].eq("settled")
        & probability_complete["label"].isin([0.0, 1.0])
        & probability_complete["pit_probability_identity_legal"]
    ].copy()
    if paired.empty:
        raise ValueError("no canonical settled probability denominator is evaluable")
    active_probability = (
        paired[paired["cooling_window_state"].isin(CHALLENGER_ACTIVE_WINDOWS)].copy()
        if "cooling_window_state" in paired.columns
        else paired.iloc[0:0].copy()
    )
    legacy_headline = paired[paired["candidate_status"].eq("scored")].copy()
    execution_clean = legacy_headline[
        legacy_headline["market_evidence_status"].eq("available")
        & pd.to_numeric(legacy_headline["executable_cost"], errors="coerce").notna()
    ].copy()
    labels = paired["label"].astype(int).to_numpy()
    model_probability = paired["p_model"].astype(float).to_numpy()
    market_probability = paired["market_p"].astype(float).to_numpy()
    model_logloss = binary_loss_values(labels, model_probability, metric="logloss")
    market_logloss = binary_loss_values(labels, market_probability, metric="logloss")
    model_brier = binary_loss_values(labels, model_probability, metric="brier")
    market_brier = binary_loss_values(labels, market_probability, metric="brier")

    probability = {
        "rows": int(len(paired)),
        "target_dates": int(paired["target_date"].nunique()),
        "cities": sorted(str(value) for value in paired["city"].unique()),
        "model_logloss": float(np.mean(model_logloss)),
        "market_logloss": float(np.mean(market_logloss)),
        "model_brier": float(np.mean(model_brier)),
        "market_brier": float(np.mean(market_brier)),
        "model_minus_market_logloss": date_block_bootstrap_delta(
            paired, model_logloss, market_logloss, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED
        ),
        "model_minus_market_brier": date_block_bootstrap_delta(
            paired, model_brier, market_brier, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED + 1
        ),
    }

    challenger: dict[str, Any]
    if model_outputs_path is None:
        challenger = {
            "policy_id": CHALLENGER_POLICY_ID,
            "status": "not_evaluated_missing_model_outputs",
            "forward_start_target_date": CHALLENGER_FORWARD_START_TARGET_DATE,
        }
    else:
        required_challenger_columns = [
            "output_p_model",
            "physical_innovation_logit",
            "cooling_window_state",
        ]
        missing_challenger_metadata = probability_complete[
            required_challenger_columns
        ].isna().any(axis=1)
        if missing_challenger_metadata.any():
            sample = probability_complete.loc[
                missing_challenger_metadata, ["checkpoint_id", *required_challenger_columns]
            ].head(5)
            raise ValueError(
                "scored rows missing challenger model-output lineage: "
                f"{sample.to_dict('records')}"
            )
        output_mismatch = (
            probability_complete["output_p_model"].astype(float)
            - probability_complete["p_model"].astype(float)
        ).abs().gt(1e-12)
        if output_mismatch.any():
            sample = probability_complete.loc[
                output_mismatch, ["checkpoint_id", "p_model", "output_p_model"]
            ].head(5)
            raise ValueError(
                "candidate/model-output probability mismatch: "
                f"{sample.to_dict('records')}"
            )

        paired["challenger_p"] = _window_routed_probability(
            paired["market_p"],
            paired["physical_innovation_logit"],
            paired["cooling_window_state"],
        )
        development = paired[
            paired["candidate_id"].astype(str).isin(development_candidate_ids)
            & paired["target_date"].astype(str).le(
                CHALLENGER_DEVELOPMENT_END_TARGET_DATE
            )
        ].copy()
        versus_market = _probability_comparison(
            development,
            candidate_column="challenger_p",
            baseline_column="market_p",
            seed=BOOTSTRAP_SEED + 10,
        )
        versus_incumbent = _probability_comparison(
            development,
            candidate_column="challenger_p",
            baseline_column="p_model",
            seed=BOOTSTRAP_SEED + 20,
        )
        temporal_stability = {}
        for offset, (name, (start_date, end_date)) in enumerate(
            CHALLENGER_TEMPORAL_SPLITS.items()
        ):
            split = development[
                development["target_date"].astype(str).between(start_date, end_date)
            ]
            temporal_stability[name] = _probability_comparison(
                split,
                candidate_column="challenger_p",
                baseline_column="market_p",
                seed=BOOTSTRAP_SEED + 30 + offset * 2,
            )

        overall_point_improves = all(
            versus_market[key] is not None
            and versus_market[key]["delta"] < 0
            for key in (
                "candidate_minus_baseline_logloss",
                "candidate_minus_baseline_brier",
            )
        )
        temporal_point_improves = all(
            comparison[key] is not None and comparison[key]["delta"] < 0
            for comparison in temporal_stability.values()
            for key in (
                "candidate_minus_baseline_logloss",
                "candidate_minus_baseline_brier",
            )
        )

        development_policy = development[
            development["cooling_window_state"].isin(CHALLENGER_ACTIVE_WINDOWS)
            & development["candidate_status"].eq("scored")
            & development["market_evidence_status"].eq("available")
            & development["executable_cost"].notna()
        ].copy()
        development_policy["ask"] = development_policy["executable_cost"].astype(float)
        development_policy["model_fee_per_share"] = development_policy["ask"].map(_fee)
        development_policy["challenger_net_edge"] = (
            development_policy["challenger_p"]
            - development_policy["ask"]
            - development_policy["model_fee_per_share"]
        )
        development_policy = development_policy[
            development_policy["ask"].gt(0)
            & development_policy["challenger_net_edge"].gt(0)
        ].sort_values(["decision_ts_utc", "candidate_id"])
        development_policy = development_policy.drop_duplicates(
            ["city", "target_date"], keep="first"
        )
        if development_policy.empty:
            development_policy = _attach_execution_book_evidence(development_policy)
            executable_development_policy = development_policy.copy()
        else:
            development_policy = _attach_execution_book_evidence(development_policy)
            executable_development_policy = development_policy[
                development_policy["book_evidence_status"].eq("available")
                & development_policy["candidate_ask_matches_book"].eq(True)
                & development_policy["five_share_fillable"].eq(True)
            ].copy()
            executable_development_policy["fee_per_share"] = (
                executable_development_policy["five_share_fee_per_share"].astype(float)
            )
            executable_development_policy["cost_per_share"] = (
                executable_development_policy["five_share_vwap"].astype(float)
                + executable_development_policy["fee_per_share"]
            )
            executable_development_policy["pnl_per_share"] = (
                executable_development_policy["label"]
                - executable_development_policy["cost_per_share"]
            )

        forward = probability_complete[
            probability_complete["target_date"].astype(str).ge(
                CHALLENGER_FORWARD_START_TARGET_DATE
            )
        ].copy()
        if not forward.empty:
            forward["challenger_p"] = _window_routed_probability(
                forward["market_p"],
                forward["physical_innovation_logit"],
                forward["cooling_window_state"],
            )
            forward["ask"] = pd.to_numeric(
                forward["executable_cost"], errors="coerce"
            )
            forward["model_fee_per_share"] = forward["ask"].map(
                lambda value: _fee(float(value)) if pd.notna(value) else np.nan
            )
            forward["challenger_net_edge"] = (
                forward["challenger_p"]
                - forward["ask"]
                - forward["model_fee_per_share"]
            )
            forward_signals = forward[
                forward["cooling_window_state"].isin(CHALLENGER_ACTIVE_WINDOWS)
                & forward["candidate_status"].eq("scored")
                & forward["market_evidence_status"].eq("available")
                & forward["ask"].gt(0)
                & forward["challenger_net_edge"].gt(0)
            ].sort_values(["decision_ts_utc", "candidate_id"])
            forward_signals = forward_signals.drop_duplicates(
                ["city", "target_date"], keep="first"
            )
            if forward_signals.empty:
                forward_signals = _attach_execution_book_evidence(forward_signals)
                forward_selected = forward_signals.copy()
            else:
                forward_signals = _attach_execution_book_evidence(forward_signals)
                forward_selected = forward_signals[
                    forward_signals["book_evidence_status"].eq("available")
                    & forward_signals["candidate_ask_matches_book"].eq(True)
                    & forward_signals["five_share_fillable"].eq(True)
                ].copy()
        else:
            forward_signals = forward
            forward_selected = forward

        eligible_to_freeze = overall_point_improves and temporal_point_improves
        challenger = {
            "policy_id": CHALLENGER_POLICY_ID,
            "status": (
                "eligible_for_prospective_zero_notional_frozen_forward"
                if eligible_to_freeze
                else "development_gate_failed_do_not_freeze"
            ),
            "mechanism": (
                "market-anchored physical residual alpha=0.10 only in morning_cooling "
                "or post_sunrise_provisional_low; market probability elsewhere"
            ),
            "alpha": CHALLENGER_ALPHA,
            "active_cooling_windows": sorted(CHALLENGER_ACTIVE_WINDOWS),
            "entry_policy": (
                "positive fee-adjusted challenger edge at the PIT direct YES ask; "
                "first eligible checkpoint per city and target_date; 5 shares"
            ),
            "development_end_target_date": CHALLENGER_DEVELOPMENT_END_TARGET_DATE,
            "forward_start_target_date": CHALLENGER_FORWARD_START_TARGET_DATE,
            "model_output_snapshot": model_output_snapshot,
            "lineage": {
                "probability_complete_rows": int(len(probability_complete)),
                "probability_complete_rows_with_challenger_metadata": int(
                    (~missing_challenger_metadata).sum()
                ),
                "candidate_model_output_probability_matches": int(
                    (~output_mismatch).sum()
                ),
            },
            "development_probability_vs_market": versus_market,
            "development_probability_vs_incumbent": versus_incumbent,
            "development_temporal_stability_vs_market": temporal_stability,
            "development_trade_replay": _roi_summary(executable_development_policy),
            "development_trade_selected_rows": int(len(development_policy)),
            "development_trade_with_static_5_share_depth": int(
                len(executable_development_policy)
            ),
            "multiple_testing": {
                "new_window_routed_alphas_tested": [0.10, 0.25, 0.50],
                "new_challenger_comparisons": 3,
                "incumbent_training_alpha_grid_size": 5,
                "adjustment": (
                    "none; development selection is not a significance claim and all future "
                    "promotion evidence begins at the frozen forward boundary"
                ),
            },
            "freeze_qualification": {
                "overall_logloss_and_brier_point_improve_market": overall_point_improves,
                "both_fixed_temporal_splits_point_improve_both_metrics": (
                    temporal_point_improves
                ),
                "eligible": eligible_to_freeze,
                "note": (
                    "eligibility freezes a prospective zero-notional test; it does not pass "
                    "statistical, fill, or tiny-live gates"
                ),
            },
            "forward_funnel": {
                "checkpoint_rows": int(len(forward)),
                "positive_edge_signal_rows": int(len(forward_signals)),
                "static_book_executable_selected_rows": int(len(forward_selected)),
                "selected_rows": int(len(forward_selected)),
                "target_dates": int(forward["target_date"].nunique())
                if not forward.empty
                else 0,
                "status": "running" if not forward.empty else "not_started",
            },
        }

    selected = paired[paired["selected"].eq(True)].copy()
    selected["ask"] = selected["executable_cost"].astype(float)
    selected = _attach_execution_book_evidence(selected)
    executable_selected = selected[
        selected["book_evidence_status"].eq("available")
        & selected["candidate_ask_matches_book"].eq(True)
        & selected["five_share_fillable"].eq(True)
    ].copy()
    executable_selected["fee_per_share"] = executable_selected["five_share_fee_per_share"].astype(float)
    executable_selected["cost_per_share"] = (
        executable_selected["five_share_vwap"].astype(float)
        + executable_selected["fee_per_share"]
    )
    executable_selected["pnl_per_share"] = (
        executable_selected["label"] - executable_selected["cost_per_share"]
    )
    executable_selected["price_bucket"] = pd.cut(
        executable_selected["ask"],
        bins=[-np.inf, 0.60, 0.95, 0.98, 1.0 + 1e-9],
        labels=["<=0.60", "0.60-0.95", "0.95-0.98", ">0.98"],
        right=True,
    )
    trade = _roi_summary(executable_selected)
    trade["execution_evidence_class"] = "pit_rest_full_book_static_snapshot"
    trade["selected_with_reconstructable_book"] = int(
        selected["book_evidence_status"].eq("available").sum()
    )
    trade["selected_with_raw_book_capture_id_persisted_as_execution_snapshot_id"] = int(
        selected["execution_snapshot_id_matches_book_capture_id"].eq(True).sum()
    )
    trade["selected_five_share_fillable_at_snapshot"] = int(len(executable_selected))
    trade["selected_five_share_at_best_ask_without_slippage"] = int(
        executable_selected["five_share_slippage_vs_candidate_ask"].abs().le(1e-12).sum()
    )
    trade["min_best_ask_size"] = (
        None if executable_selected.empty else float(executable_selected["best_ask_size"].min())
    )
    trade["median_best_ask_size"] = (
        None if executable_selected.empty else float(executable_selected["best_ask_size"].median())
    )
    trade["max_book_age_minutes"] = (
        None if executable_selected.empty else float(executable_selected["book_age_minutes"].max())
    )
    trade["actual_fills"] = 0
    trade["execution_limit"] = (
        "static PIT depth supports the hypothetical 5-share sweep; zero-notional shadow does not "
        "establish post-decision liquidity, queue, latency, or an actual fill"
    )
    if not executable_selected.empty:
        top = executable_selected.sort_values("pnl_per_share", ascending=False).iloc[0]
        without_top = executable_selected.drop(index=top.name)
        trade["largest_winner"] = {
            "city": str(top["city"]),
            "target_date": str(top["target_date"]),
            "local_hour": int(top["local_hour"]),
            "ask": float(top["ask"]),
            "pnl_per_share": float(top["pnl_per_share"]),
            "share_of_total_pnl": float(
                top["pnl_per_share"] / executable_selected["pnl_per_share"].sum()
            ),
        }
        trade["top_winner_removed"] = _roi_summary(without_top)
        stressed_pnl = float(executable_selected["pnl_per_share"].sum()) - 1.0
        trade["one_additional_loss_stress"] = {
            "pnl_per_share": stressed_pnl,
            "roi": stressed_pnl / float(executable_selected["cost_per_share"].sum()),
            "note": "changing any one binary win to a loss reduces portfolio PnL by exactly $1/share",
        }

    selected_entries = []
    for _, row in executable_selected.sort_values(["decision_ts_utc", "candidate_id"]).iterrows():
        selected_entries.append(
            {
                "city": str(row["city"]),
                "target_date": str(row["target_date"]),
                "decision_ts_utc": row["decision_ts_utc"].isoformat(),
                "decision_ts_beijing": row["decision_ts_utc"].tz_convert("Asia/Shanghai").isoformat(),
                "local_hour": int(row["local_hour"]),
                "bracket": str(row["bracket"]),
                "condition_id": str(row["condition_id"]),
                "market_p": float(row["market_p"]),
                "p_model": float(row["p_model"]),
                "ask": float(row["ask"]),
                "best_ask_size": float(row["best_ask_size"]),
                "depth_ask_5c": float(row["depth_ask_5c"]),
                "book_capture_id": str(row["book_capture_id"]),
                "candidate_execution_book_snapshot_id": str(
                    row["execution_book_snapshot_id"]
                ),
                "execution_snapshot_id_matches_book_capture_id": bool(
                    row["execution_snapshot_id_matches_book_capture_id"]
                ),
                "book_age_minutes": float(row["book_age_minutes"]),
                "five_share_vwap": float(row["five_share_vwap"]),
                "five_share_slippage_vs_candidate_ask": float(
                    row["five_share_slippage_vs_candidate_ask"]
                ),
                "fee_per_share": float(row["fee_per_share"]),
                "label": int(row["label"]),
                "pnl_per_share": float(row["pnl_per_share"]),
            }
        )

    all_dates = sorted(str(value) for value in frame["target_date"].unique())
    settled_scored_dates = sorted(str(value) for value in paired["target_date"].unique())
    selected_dates = sorted(str(value) for value in selected["target_date"].unique())
    blocker_counts = Counter(str(value) for value in frame.loc[frame["candidate_status"].eq("blocked"), "blocker_reason"])
    market_available = frame["market_evidence_status"].eq("available")
    duplicate_city_dates = (
        selected.groupby(["city", "target_date"]).size().loc[lambda values: values > 1]
        if not selected.empty
        else pd.Series(dtype=int)
    )
    execution_clean_all = probability_complete[
        probability_complete["candidate_status"].eq("scored")
        & probability_complete["market_evidence_status"].eq("available")
        & pd.to_numeric(
            probability_complete["executable_cost"], errors="coerce"
        ).notna()
    ].copy()

    logloss_ci = probability["model_minus_market_logloss"]
    brier_ci = probability["model_minus_market_brier"]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "strategy_identity": {
            "instance_id": "weather_tmin_no_further_cooling_shadow_v1",
            "strategy_key": "weather.tmin.no_further_cooling",
            "model_id": str(frame["model_id"].iloc[0]),
            "model_artifact_id": artifact_id,
            "policy_id": str(frame["policy_id"].iloc[0]),
            "execution_mode": "zero_notional_shadow",
        },
        "input_snapshot": snapshot,
        "challenger_development_input_snapshot": development_snapshot,
        "canonical_db": identity,
        "denominator_scope": {
            "unit": "fixed local checkpoint current-exact-YES expression",
            "start_target_date": min(all_dates),
            "end_target_date": max(all_dates),
            "rows": int(len(frame)),
            "target_dates": len(all_dates),
            "settled_scored_target_dates": len(settled_scored_dates),
            "cities": sorted(str(value) for value in frame["city"].unique()),
            "active_trade_dates": len(selected_dates),
            "trade_class": "zero_notional_shadow_hypothetical",
            "settlement_join": "condition_id",
        },
        "denominator_lineage": {
            "P0_CANONICAL_PROBABILITY": {
                "rows": int(len(paired)),
                "target_dates": int(paired["target_date"].nunique()),
                "contract": (
                    "p_market+p_model+settled binary label+PIT observation/forecast refs+"
                    "candidate/checkpoint/condition/market/token/bracket/feature-book identity; "
                    "no execution quote requirement"
                ),
            },
            "P1_ACTIVE_WINDOW_PROBABILITY": {
                "rows": int(len(active_probability)),
                "target_dates": int(active_probability["target_date"].nunique()),
                "active_windows": sorted(CHALLENGER_ACTIVE_WINDOWS),
            },
            "LEGACY_111_HEADLINE": {
                "rows": int(len(legacy_headline)),
                "target_dates": int(legacy_headline["target_date"].nunique()),
                "contract": "P0 additionally conditioned on candidate_status=scored",
            },
            "E0_EXECUTION_CLEAN": {
                "rows": int(len(execution_clean)),
                "target_dates": int(execution_clean["target_date"].nunique()),
                "all_settlement_states_rows": int(len(execution_clean_all)),
                "contract": "P0 plus valid direct quote/evidence status; never used to define P0",
            },
            "T0_SELECTED_TRADE": {
                "rows": int(len(selected)),
                "target_dates": int(selected["target_date"].nunique()),
                "contract": "frozen incumbent selector selected=true within settled P0",
            },
        },
        "signal_funnel": {
            "artifact_checkpoint_rows": int(len(frame)),
            "probability_complete_rows": int(len(probability_complete)),
            "execution_clean_rows": int(len(execution_clean_all)),
            "positive_edge_policy_selected_rows": int(frame["selected"].eq(True).sum()),
            "selected_settled_rows": int(len(selected)),
            "selected_city_date_duplicates_within_artifact": int(len(duplicate_city_dates)),
        },
        "evidence_funnel": {
            "pit_observation_and_forecast_refs": int(
                frame["input_refs"].map(
                    lambda refs: {str(item.get("kind")) for item in (refs or [])}
                    >= {"observation_archive", "forecast_curve"}
                ).sum()
            ),
            "market_evidence_available": int(market_available.sum()),
            "canonical_probability_with_binary_settlement": int(len(paired)),
            "legacy_scored_with_binary_settlement": int(len(legacy_headline)),
            "selected_with_direct_ask": int(selected["ask"].notna().sum()),
            "selected_with_reconstructable_full_book": int(
                selected["book_evidence_status"].eq("available").sum()
            ),
            "selected_with_recorded_depth": int(
                selected["best_ask_size"].notna().sum()
            ),
            "selected_five_share_fillable_at_snapshot": int(len(executable_selected)),
            "actual_fills": 0,
            "unsettled_execution_clean_rows": int(
                len(execution_clean_all) - len(execution_clean)
            ),
        },
        "blocker_reason_counts": dict(sorted(blocker_counts.items())),
        "probability_quality": probability,
        "fee_adjusted_trade_performance": trade,
        "prospective_challenger": challenger,
        "entry_breakdown": {
            "by_city": _breakdown(executable_selected, "city"),
            "by_local_hour": _breakdown(executable_selected, "local_hour"),
            "by_beijing_hour": _breakdown(executable_selected, "beijing_hour"),
            "by_ask_bucket": _breakdown(executable_selected, "price_bucket"),
        },
        "selected_entries": selected_entries,
        "multiple_testing": {
            "policy_variants_tested_in_this_review": 1,
            "descriptive_slices": int(
                executable_selected["city"].nunique()
                + executable_selected["local_hour"].nunique()
                + executable_selected["beijing_hour"].nunique()
                + executable_selected["price_bucket"].nunique()
            ),
            "correction": "none; slices are attribution only and are not promotion selectors",
        },
        "gates": {
            "same_denominator_probability_baseline": (
                "PASS" if logloss_ci["ci_high"] < 0 and brier_ci["ci_high"] < 0 else "FAIL"
            ),
            "fee_adjusted_significance": (
                "PROVISIONAL_PASS_DEGENERATE_ALL_WIN_SAMPLE"
                if trade["target_date_bootstrap_ci95"][0] is not None
                and trade["target_date_bootstrap_ci95"][0] > 0
                else "FAIL"
            ),
            "frozen_forward_min_30_settled_target_dates": (
                "PASS"
                if len(settled_scored_dates) >= 30
                else f"FAIL_{len(settled_scored_dates)}_OF_30_SETTLED_TARGET_DATES"
            ),
            "direct_execution_depth": (
                f"PASS_STATIC_PIT_5_SHARE_DEPTH_{len(executable_selected)}_OF_{len(selected)}"
                if len(executable_selected) == len(selected) and len(selected) > 0
                else f"FAIL_STATIC_PIT_5_SHARE_DEPTH_{len(executable_selected)}_OF_{len(selected)}"
            ),
            "actual_fill_or_post_decision_execution": (
                "FAIL_ZERO_NOTIONAL_NO_ACTUAL_FILL_OR_POST_DECISION_EXECUTION_BOOK"
            ),
            "execution_book_lineage_identity": (
                "PARTIAL_BATCH_HASH_AND_INPUT_REF_RESOLVE_RAW_BOOK_BUT_BOOK_CAPTURE_ID_NOT_PERSISTED"
            ),
        },
        "decision": "inconclusive_keep_zero_notional_shadow_do_not_promote_live",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument(
        "--snapshot-size-bytes",
        type=int,
        help="Optional full evaluation journal byte boundary.",
    )
    parser.add_argument(
        "--development-snapshot-size-bytes",
        type=int,
        help=(
            "Immutable candidate journal byte boundary used for challenger development; "
            "future runs may leave --snapshot-size-bytes unset."
        ),
    )
    parser.add_argument(
        "--model-outputs",
        type=Path,
        help=(
            "Append-only model output journal containing physical innovation and "
            "cooling-window metadata for the prospective challenger."
        ),
    )
    parser.add_argument(
        "--model-outputs-snapshot-size-bytes",
        type=int,
        help="Optional byte boundary for the append-only model output journal.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = evaluate(
        candidates_path=args.candidates,
        db_path=args.db,
        artifact_id=args.artifact_id,
        snapshot_size_bytes=args.snapshot_size_bytes,
        development_snapshot_size_bytes=args.development_snapshot_size_bytes,
        model_outputs_path=args.model_outputs,
        model_outputs_snapshot_size_bytes=args.model_outputs_snapshot_size_bytes,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
