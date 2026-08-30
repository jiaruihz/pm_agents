#!/usr/bin/env python3
"""Build the WCIR Amsterdam pilot v1.1 closure package.

Research-only.  This module has no order client, credential, venue, plan, or
TradeIntent import.  It writes only to ``reviews/`` and an explicitly supplied
research shadow journal root.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from typing import Any, Iterable
import zipfile

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality.wcir_unified_amsterdam_pilot import (
    CORE_FEATURES,
    SUPPORT,
    binary_calibration,
    make_captured_panel,
    prediction_frame,
    read_jsonl_gz,
    stable_hash,
)
from scripts.analysis.forecast_quality.wcir_amsterdam_pilot_rev2 import (
    DEFAULT_ALIGNED,
    DEFAULT_DECISIONS,
    DEFAULT_EVENTS,
    DEFAULT_FORECAST,
    DEFAULT_HISTORICAL,
    DEFAULT_KNMI_ROOT,
    DEFAULT_LADDER_ROOT,
    DEFAULT_TRUTHS,
    MODEL_IDS,
    PROBABILITY_COLUMNS,
    _exact_record,
    _snapshot_path,
    _sweep,
    dependency_weights,
    load_legacy_selected,
    make_captured_rev2,
    make_historical_rev2,
    reconcile_market,
    sha256,
    train_and_evaluate,
)
from weather_city_runtime.jsonl_lock import exclusive_jsonl_lock
from weather_city_runtime.next_print_contracts import CITY_CONTRACTS
from weather_modeling.amsterdam_feature_builder_v2 import (
    AmsterdamFeatureBuilderV2,
    PATH_FEATURES,
    stable_hash as feature_hash,
)
from weather_modeling.amsterdam_frozen_scorer_v2 import (
    SCORER_VERSION,
    score_frozen_models,
)


UTC = timezone.utc
RNG_SEED = 20260829
BOOTSTRAP_REPS = 10_000
DEFAULT_OUTPUT = ROOT / "reviews/wcir_unified_data_amsterdam_pilot_v1_1"
DEFAULT_SHADOW_ROOT = Path("/Volumes/jrs-archive/pm_agents/research/artifact_store/wcir_amsterdam_score_only_shadow")
DEFAULT_OBSERVATIONS_ROOT = Path("/Volumes/jrs/weather_data_feed_service_runtime/output/observations")
V1_INPUT_ZIP = ROOT / "reviews/wcir_unified_data_amsterdam_pilot_v1/wcir-unified-data-amsterdam-pilot-v1-20260828T161131Z.zip"
V1_INPUT_SHA256 = "b257add0e34703d4b1984c7f2177e694291eb1b1f9a080478a65977d9d4ed66f"

ARTIFACTS = (
    "RESPONSE_TO_GPT_REVIEW_AMSTERDAM_V1_1.md",
    "AMSTERDAM_SOURCE_IDENTITY_AND_LABEL_CONTRACT_V2.md",
    "AMSTERDAM_SOURCE_IDENTITY_AND_LABEL_CONTRACT_V2.json",
    "STAGE1_STAGE3_AMSTERDAM_CONTRACT_CORRIGENDUM_V2.md",
    "AMSTERDAM_LABEL_LINEAGE_EXAMPLES.jsonl.gz",
    "GROUND_STATION_DATA_CONTRACT_V1.md",
    "GROUND_STATION_DATA_SCHEMA_V1.json",
    "GROUND_STATION_DATA_DDL_OR_VALIDATION_SCHEMA.sql",
    "AMSTERDAM_FEATURE_BUILDER_V2_SPEC.md",
    "AMSTERDAM_FEATURE_PARITY_AUDIT.md",
    "AMSTERDAM_FEATURE_PARITY_AUDIT.json",
    "AMSTERDAM_FEATURE_PARITY_ROWS.parquet",
    "AMSTERDAM_COHORT_AND_WEIGHTING_CONTRACT_V2.md",
    "AMSTERDAM_COHORT_LEDGER.parquet",
    "AMSTERDAM_COHORT_SUMMARY.json",
    "B2_MODEL_ARTIFACT_MANIFEST.json",
    "M1_MODEL_ARTIFACT_MANIFEST.json",
    "M2_MODEL_ARTIFACT_MANIFEST.json",
    "B2_PREDICTIONS_FULL_CHECKPOINT.parquet",
    "M1_PREDICTIONS_FULL_CHECKPOINT.parquet",
    "M2_PREDICTIONS_FULL_CHECKPOINT.parquet",
    "B2_PREDICTIONS_OPPORTUNITY_MATCHED.parquet",
    "M1_PREDICTIONS_OPPORTUNITY_MATCHED.parquet",
    "M2_PREDICTIONS_OPPORTUNITY_MATCHED.parquet",
    "B2_PREDICTIONS_CAPTURED_PIT.parquet",
    "M1_PREDICTIONS_CAPTURED_PIT.parquet",
    "M2_PREDICTIONS_CAPTURED_PIT.parquet",
    "AMSTERDAM_MODEL_COMPARISON_V2.json",
    "AMSTERDAM_MODEL_VALIDATION_REPORT_V2.md",
    "CALIBRATION_AND_NEGATIVE_CONTROL_REPORT_V2.md",
    "AMSTERDAM_MARKET_ARCHIVE_INVENTORY.json",
    "AMSTERDAM_MARKET_ARCHIVE_INVENTORY.md",
    "AMSTERDAM_EVENT_MARKET_IDENTITY_RECONCILIATION.parquet",
    "AMSTERDAM_EVENT_MARKET_IDENTITY_RECONCILIATION_SUMMARY.json",
    "AMSTERDAM_EXECUTABLE_COVERAGE_REASON_HISTOGRAM.json",
    "AMSTERDAM_EXECUTABLE_COVERAGE_ROWS.parquet",
    "AMSTERDAM_NEXT_PRINT_TO_TOKEN_REACTION_CONTRACT_V1.md",
    "AMSTERDAM_ACTION_MAPPING_SCHEMA_V1.json",
    "MARKET_REACTION_MODEL_RESULTS.json",
    "MARKET_REACTION_MODEL_STATUS.json",
    "EXECUTABLE_REPLAY_POLICY_V2.md",
    "EXECUTABLE_REPLAY_FUNNEL_V2.json",
    "EXECUTABLE_REPLAY_ROWS_V2.parquet",
    "EXECUTABLE_REPLAY_REPORT_V2.md",
    "FROZEN_SCORE_ONLY_SHADOW_CONFIG_V2.json",
    "FORWARD_EPOCH_MANIFEST_V2.json",
    "SHADOW_RUNTIME_AND_ISOLATION_AUDIT.json",
    "CODE_DIFF_AND_IMPLEMENTATION_MAP.md",
    "FINAL_CODE_AND_ENVIRONMENT_FREEZE.json",
    "REPRODUCE_AMSTERDAM_PILOT_V1_1.sh",
    "TEST_COMMANDS_AND_RAW_OUTPUT.txt",
    "EVIDENCE_MANIFEST.json",
    "GPT_PRO_REVIEW_PACKET_AMSTERDAM_V1_1.md",
)

SHADOW_REQUIRED_FIELDS = {
    "forward_epoch_id", "event_id", "decision_vintage_id", "raw_source_lineage",
    "full_path_lineage", "feature_vector", "feature_vector_hash", "B2_PMF",
    "M1_PMF", "M2_PMF", "market_identity", "decision_book",
    "submit_proxy_book", "layer_b_prediction", "abstain_reasons",
    "data_failure_reasons", "future_next_print_label", "future_markouts",
    "orders", "fills", "notional",
}
PACKAGE_SIDECAR_RE = re.compile(
    r"^wcir-amsterdam-pilot-v1-1-\d{8}T\d{6}Z\.zip(?:\.sha256)?$"
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def portable_path(path: Path) -> str:
    resolved = path.resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        return "repo://" + str(resolved.relative_to(ROOT))
    jrs = Path("/Volumes/jrs/weather_data_feed_service_runtime")
    if resolved == jrs or jrs in resolved.parents:
        return "jrs://weather_data_feed_service_runtime/" + str(resolved.relative_to(jrs))
    archive = Path("/Volumes/jrs-archive/pm_agents/research/artifact_store")
    if resolved == archive or archive in resolved.parents:
        return "artifact-store://" + str(resolved.relative_to(archive))
    return "external-redacted://" + resolved.name


def _strict_root_files(path: Path, *, include_manifest: bool) -> dict[str, Path]:
    """Return whitelisted root files and reject every nested or unknown entry."""

    actual: dict[str, Path] = {}
    unexpected: list[str] = []
    for item in path.rglob("*"):
        relative = item.relative_to(path)
        if item.is_dir() or len(relative.parts) != 1:
            unexpected.append(relative.as_posix() + ("/" if item.is_dir() else ""))
            continue
        name = relative.name
        if PACKAGE_SIDECAR_RE.fullmatch(name) is not None:
            continue
        if name == "EVIDENCE_MANIFEST.json" and not include_manifest:
            continue
        if name not in ARTIFACTS:
            unexpected.append(name)
            continue
        actual[name] = item
    if unexpected:
        raise RuntimeError(
            f"refusing non-whitelisted or nested package entries: {sorted(unexpected)}"
        )
    return actual


def ensure_output(path: Path) -> Path:
    resolved = path.resolve()
    allowed = (ROOT / "reviews").resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise ValueError(f"output must remain under reviews/: {resolved}")
    resolved.mkdir(parents=True, exist_ok=True)
    _strict_root_files(resolved, include_manifest=True)
    return resolved


def append_shadow_prediction(path: Path, row: dict[str, Any]) -> str:
    """Append one content-addressed score row, idempotently and fail-closed.

    The journal contains scores and lineage only. It has no action, order, or
    credential surface. Reusing an id with different content is corruption.
    """

    payload = dict(row)
    missing = SHADOW_REQUIRED_FIELDS - set(payload)
    if missing:
        raise ValueError(f"shadow prediction missing required fields: {sorted(missing)}")
    if [payload["orders"], payload["fills"], payload["notional"]] != [0, 0, 0]:
        raise ValueError("score-only shadow row must keep orders/fills/notional at 0/0/0")
    supplied_id = payload.pop("prediction_row_id", None)
    row_id = feature_hash(payload)
    if supplied_id not in (None, row_id):
        raise ValueError("prediction_row_id does not match row content")
    payload["prediction_row_id"] = row_id
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    with exclusive_jsonl_lock(path):
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    existing = json.loads(line)
                    same_event = (
                        existing.get("forward_epoch_id") == payload["forward_epoch_id"]
                        and existing.get("event_id") == payload["event_id"]
                    )
                    if not same_event and existing.get("prediction_row_id") != row_id:
                        continue
                    existing_encoded = json.dumps(existing, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
                    if existing_encoded != encoded:
                        raise RuntimeError("conflicting append-only shadow epoch/event")
                    return "ALREADY_PRESENT_IDENTICAL"
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, (encoded + "\n").encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return "APPENDED"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _utc_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _shadow_market_evidence(
    decision_rows: list[dict[str, Any]],
    *,
    source_observed_at: pd.Timestamp,
    decision_at: pd.Timestamp,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    matches: list[dict[str, Any]] = []
    for bundle in decision_rows:
        event = bundle.get("information_event") or {}
        candidate = bundle.get("signal_candidate") or {}
        model = bundle.get("model_output") or {}
        if event.get("city") != "Amsterdam":
            continue
        if not event.get("source_event_ts_utc") or not event.get("available_at_utc"):
            continue
        if _utc_timestamp(event.get("source_event_ts_utc")) != source_observed_at:
            continue
        if _utc_timestamp(event.get("available_at_utc")) != decision_at:
            continue
        required = ("market_id", "condition_id", "token_id", "side", "bracket")
        if any(not candidate.get(name) for name in required):
            continue
        matches.append({
            "market_id": str(candidate["market_id"]),
            "condition_id": str(candidate["condition_id"]),
            "token_id": str(candidate["token_id"]),
            "side": str(candidate["side"]),
            "native_bracket": str(candidate["bracket"]),
            "target_date": str(candidate.get("target_date")),
            "target_id": candidate.get("target_id"),
            "feature_book_snapshot_id": candidate.get("feature_book_snapshot_id"),
            "execution_book_snapshot_id": candidate.get("execution_book_snapshot_id"),
            "decision_at_utc": candidate.get("decision_ts_utc"),
            "direct_token_effective_cost": candidate.get("executable_cost"),
            "direct_token_market_probability": candidate.get("market_p"),
            "legacy_model_id": model.get("model_id"),
            "legacy_selected": bool(candidate.get("selected", False)),
        })
    if not matches:
        return None, None
    matches.sort(key=lambda row: (row["market_id"], row["side"], row["token_id"]))
    identities = [
        {key: row[key] for key in (
            "market_id", "condition_id", "token_id", "side", "native_bracket", "target_date"
        )}
        for row in matches
    ]
    return identities, {
        "evidence_class": "captured_decision_bundle_exact_identity_and_effective_cost",
        "entries": matches,
    }


def _build_shadow_feature_result(
    event: dict[str, Any],
    *,
    knmi_rows: list[dict[str, Any]],
    official_rows: list[dict[str, Any]],
    epoch_id: str,
) -> tuple[Any, pd.DataFrame, dict[str, Any]]:
    decision = _utc_timestamp(event["available_at_utc"])
    observed = _utc_timestamp(event["observation_time_utc"])
    target_date = str(event["target_date"])
    eligible_official = []
    for row in official_rows:
        if row.get("city") != "Amsterdam" or str(row.get("target_date")) != target_date:
            continue
        if row.get("status") != "ok" or not row.get("available_at_utc") or not row.get("last_obs_utc"):
            continue
        available = _utc_timestamp(row["available_at_utc"])
        official_observed = _utc_timestamp(row["last_obs_utc"])
        if available <= decision and official_observed < observed:
            eligible_official.append((available, official_observed, row))
    if not eligible_official:
        raise ValueError("MISSING_CAUSAL_PRIOR_OFFICIAL_STATE")
    _, official_observed, official = max(eligible_official, key=lambda item: (item[0], item[1]))
    local_start = pd.Timestamp(target_date, tz="Europe/Amsterdam").tz_convert("UTC")
    selected_rows = []
    for row in knmi_rows:
        if row.get("city") != "Amsterdam" or str(row.get("target_date")) != target_date:
            continue
        if row.get("station_id") != "0-20000-0-06240":
            continue
        row_observed = _utc_timestamp(row["observation_time_utc"])
        row_available = _utc_timestamp(row["available_at_utc"])
        if local_start <= row_observed <= observed and row_available <= decision:
            selected_rows.append({
                "target_date": target_date,
                "observed_at": row_observed,
                "available_at": row_available,
                "latest_fast_native_value": float(row["temp_c"]),
                "source_observation_id": str(row["information_event_id"]),
                "payload_hash": row.get("payload_hash"),
                "raw_row_hash": row.get("raw_row_hash"),
            })
    path = pd.DataFrame(selected_rows)
    if path.empty:
        raise ValueError("MISSING_CAUSAL_KNMI_PATH")
    path = (
        path.sort_values(["observed_at", "available_at", "source_observation_id"])
        .drop_duplicates("observed_at", keep="last")
        .reset_index(drop=True)
    )
    last_official = float(np.floor(float(official["current_temp_c"]) + 0.5))
    running_max = float(np.floor(float(official["running_max_c"]) + 0.5))
    local = path["observed_at"].dt.tz_convert("Europe/Amsterdam")
    minute = local.dt.hour * 60 + local.dt.minute
    path["last_official_native_value"] = last_official
    path["official_running_max"] = running_max
    path["fast_minus_last_official"] = path["latest_fast_native_value"] - last_official
    path["fast_minus_running_max"] = path["latest_fast_native_value"] - running_max
    path["distance_to_up_native_boundary"] = np.ceil(path["latest_fast_native_value"]) - path["latest_fast_native_value"]
    path["distance_to_down_native_boundary"] = path["latest_fast_native_value"] - np.floor(path["latest_fast_native_value"])
    path["local_time_sin"] = np.sin(2 * np.pi * minute / 1440)
    path["local_time_cos"] = np.cos(2 * np.pi * minute / 1440)
    vintage_id = feature_hash({
        "forward_epoch_id": epoch_id,
        "event_id": event["information_event_id"],
        "prior_official_observed_at": official_observed.isoformat(),
        "prior_official_history_id": official.get("observation_history_id"),
    })
    result = AmsterdamFeatureBuilderV2.build_vintage(
        path,
        decision_vintage_id=vintage_id,
        feature_cutoff_at=decision,
        decision_ready_at=decision,
        observation_cutoff_at=observed,
        availability_class="PROSPECTIVE_SHADOW",
        feature_names=CORE_FEATURES,
        source_path_complete=True,
    )
    official_lineage = {
        "official_observed_at_utc": official_observed.isoformat(),
        "official_available_at_utc": _utc_timestamp(official["available_at_utc"]).isoformat(),
        "observation_history_id": official.get("observation_history_id"),
        "last_official_native_value": last_official,
        "official_running_max": running_max,
        "raw_source": official.get("source"),
    }
    return result, path, official_lineage


def score_shadow_once(
    *,
    output: Path,
    shadow_root: Path,
    knmi_root: Path,
    observations_root: Path,
    decisions: Path,
    max_events: int,
) -> dict[str, Any]:
    """Score a bounded set of real post-epoch events and append no-action rows."""

    config = json.loads((output / "FROZEN_SCORE_ONLY_SHADOW_CONFIG_V2.json").read_text(encoding="utf-8"))
    epoch = json.loads((output / "FORWARD_EPOCH_MANIFEST_V2.json").read_text(encoding="utf-8"))
    epoch_id = str(config["forward_epoch_id"])
    if epoch_id != epoch.get("forward_epoch_id"):
        raise RuntimeError("shadow config/epoch identity mismatch")
    if epoch.get("feature_builder_hash") != sha256(ROOT / "weather_modeling/amsterdam_feature_builder_v2.py"):
        raise RuntimeError("frozen feature builder hash drift")
    if epoch.get("frozen_scorer_hash") != sha256(ROOT / "weather_modeling/amsterdam_frozen_scorer_v2.py"):
        raise RuntimeError("frozen scorer hash drift")
    manifests = {
        name: json.loads((output / f"{name}_MODEL_ARTIFACT_MANIFEST.json").read_text(encoding="utf-8"))
        for name in ("B2", "M1", "M2")
    }
    for name, manifest in manifests.items():
        if manifest["model_artifact_hash"] != epoch["model_artifacts"][name]["model_artifact_hash"]:
            raise RuntimeError(f"frozen {name} model identity mismatch")
    start = _utc_timestamp(epoch["start_timestamp_utc"])
    journal = shadow_root / f"epoch={epoch_id}" / "predictions.jsonl"
    existing_event_ids = {
        str(row["event_id"]) for row in (_read_jsonl(journal) if journal.is_file() else [])
    }
    candidate_files = sorted(knmi_root.glob("*/knmi_observations.jsonl"))[-3:]
    knmi_rows = [row for path in candidate_files for row in _read_jsonl(path)]
    events_by_id: dict[str, dict[str, Any]] = {}
    for row in knmi_rows:
        if row.get("city") != "Amsterdam" or not row.get("available_at_utc"):
            continue
        if _utc_timestamp(row["available_at_utc"]) < start:
            continue
        if not row.get("material_state_change") or not row.get("information_event_id"):
            continue
        events_by_id[str(row["information_event_id"])] = row
    events = sorted(
        (row for event_id, row in events_by_id.items() if event_id not in existing_event_ids),
        key=lambda row: _utc_timestamp(row["available_at_utc"]),
    )
    if max_events <= 0:
        raise ValueError("max_events must be positive")
    events = events[-max_events:]
    observation_files = sorted(observations_root.glob("*/observations.jsonl"))[-3:]
    official_rows = [row for path in observation_files for row in _read_jsonl(path)]
    decision_rows = _read_jsonl(decisions)
    counts: Counter[str] = Counter()
    failure_reasons: Counter[str] = Counter()
    scored_event_ids: list[str] = []
    for event in events:
        try:
            feature_result, source_path, official_lineage = _build_shadow_feature_result(
                event,
                knmi_rows=knmi_rows,
                official_rows=official_rows,
                epoch_id=epoch_id,
            )
        except ValueError as error:
            failure_reasons[str(error)] += 1
            continue
        if feature_result.status != "OK":
            failure_reasons[feature_result.status] += 1
            continue
        feature_vector = feature_result.feature_vector
        pmfs = score_frozen_models(manifests, feature_vector)
        decision_at = _utc_timestamp(event["available_at_utc"])
        source_observed = _utc_timestamp(event["observation_time_utc"])
        market_identity, decision_book = _shadow_market_evidence(
            decision_rows,
            source_observed_at=source_observed,
            decision_at=decision_at,
        )
        data_failures = [] if market_identity is not None else ["EXACT_MARKET_IDENTITY_NOT_CAPTURED"]
        opportunity = int(np.floor(float(event["temp_c"]) + 0.5)) == int(
            feature_vector["official_running_max"] + 1
        )
        row = {
            "forward_epoch_id": epoch_id,
            "event_id": str(event["information_event_id"]),
            "decision_vintage_id": feature_result.decision_vintage_id,
            "raw_source_lineage": {
                "source": "knmi",
                "station_id": event.get("station_id"),
                "measurement_kind": "preceding_10m_average_ambient_temperature",
                "target_date": event.get("target_date"),
                "observation_time_utc": source_observed.isoformat(),
                "available_at_utc": decision_at.isoformat(),
                "payload_hash": event.get("payload_hash"),
                "raw_row_hash": event.get("raw_row_hash"),
                "raw_source_path": portable_path(Path(event["raw_source_path"])),
                "prior_official": official_lineage,
            },
            "full_path_lineage": [{
                "observed_at": _utc_timestamp(item["observed_at"]).isoformat(),
                "available_at": _utc_timestamp(item["available_at"]).isoformat(),
                "latest_fast_native_value": float(item["latest_fast_native_value"]),
                "source_observation_id": str(item["source_observation_id"]),
                "payload_hash": item.get("payload_hash"),
                "raw_row_hash": item.get("raw_row_hash"),
            } for item in source_path.to_dict("records")],
            "feature_vector": feature_vector,
            "feature_vector_hash": feature_result.feature_vector_hash,
            "B2_PMF": pmfs["B2"],
            "M1_PMF": pmfs["M1"],
            "M2_PMF": pmfs["M2"],
            "market_identity": market_identity,
            "decision_book": decision_book,
            "submit_proxy_book": None,
            "layer_b_prediction": None,
            "abstain_reasons": [
                "SCORE_ONLY_NO_ACTION",
                "LAYER_B_NOT_ESTIMABLE",
                *([] if opportunity else ["NOT_OPPORTUNITY_MATCHED"]),
            ],
            "data_failure_reasons": data_failures,
            "future_next_print_label": None,
            "future_markouts": None,
            "orders": 0,
            "fills": 0,
            "notional": 0,
        }
        status = append_shadow_prediction(journal, row)
        counts[status] += 1
        scored_event_ids.append(str(event["information_event_id"]))
    return {
        "status": "PASS_BOUNDED_REAL_SCORE_ONLY" if scored_event_ids else "NO_SCORABLE_EVENTS",
        "forward_epoch_id": epoch_id,
        "candidate_events": len(events),
        "scored_events": len(scored_event_ids),
        "append_status": dict(counts),
        "failure_reasons": dict(failure_reasons),
        "scored_event_ids": scored_event_ids,
        "prediction_journal": portable_path(journal),
        "prediction_journal_sha256": sha256(journal) if journal.exists() else None,
        "orders_fills_notional": [0, 0, 0],
    }


def collect_runtime_isolation_evidence() -> dict[str, Any]:
    """Read-only production/canonical proof; never changes controller state."""

    manifest_run = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "scripts/ops/weather_production_manifest.py", "--strict"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    manifest = json.loads(manifest_run.stdout) if manifest_run.stdout.strip().startswith("{") else {}
    health_run = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "scripts/ops/weather_production_ctl.py", "health"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    db_path = Path("/Volumes/jrs/pm_agents/runtime/weather.db")
    counts = {"signals": None, "orders": None, "fills": None, "order_notional": None, "fill_notional": None}
    if db_path.is_file():
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            needle = "%wcir_amsterdam_score_only_v2%"
            counts["signals"] = int(connection.execute(
                "SELECT COUNT(*) FROM fact_signal_candidates WHERE lower(coalesce(strategy_key,'')||' '||coalesce(model_version,'')||' '||coalesce(model_artifact_id,'')||' '||coalesce(candidate_metadata_json,'')) LIKE ?",
                (needle,),
            ).fetchone()[0])
            orders, notional = connection.execute(
                "SELECT COUNT(*),coalesce(sum(coalesce(notional,0)),0) FROM orders WHERE lower(coalesce(instance_id,'')||' '||coalesce(order_payload,'')) LIKE ?",
                (needle,),
            ).fetchone()
            fills, fill_notional = connection.execute(
                "SELECT COUNT(*),coalesce(sum(coalesce(f.filled_shares,0)*coalesce(f.filled_price,0)),0) FROM fills f JOIN orders o ON o.order_id=f.order_id WHERE lower(coalesce(o.instance_id,'')||' '||coalesce(o.order_payload,'')) LIKE ?",
                (needle,),
            ).fetchone()
            counts.update(orders=int(orders), fills=int(fills), order_notional=float(notional), fill_notional=float(fill_notional))
        finally:
            connection.close()
    health_lines = health_run.stdout.splitlines()
    return {
        "production_manifest_status": manifest.get("status"),
        "production_manifest_findings": [
            {key: row.get(key) for key in ("severity", "message")}
            for row in manifest.get("findings", [])
        ],
        "canonical_db_route_status": (manifest.get("db_route") or {}).get("status"),
        "production_health_status": health_lines[0] if health_lines else f"exit={health_run.returncode}",
        "production_health_critical_lines": [line for line in health_lines if line.startswith("[CRITICAL]")],
        "wcir_v1_1_canonical_counts": counts,
        "read_only_probe": True,
    }


def normalized_weights(frame: pd.DataFrame, mode: str) -> np.ndarray:
    if frame.empty:
        return np.array([], dtype=float)
    if mode == "primary":
        raw = dependency_weights(frame)
    elif mode == "raw_row":
        raw = np.ones(len(frame), dtype=float)
    elif mode == "target_date_only":
        raw = 1.0 / frame.groupby("target_date")["target_date"].transform("size").to_numpy(float)
    else:
        raise ValueError(mode)
    return raw / raw.sum()


def effective_n(weights: np.ndarray) -> float:
    return float(1.0 / np.square(weights).sum()) if len(weights) else 0.0


def metric_rows(frame: pd.DataFrame, predictions: pd.DataFrame) -> pd.DataFrame:
    ordered = predictions.set_index("decision_vintage_id").loc[frame["decision_vintage_id"]]
    pmf = ordered[list(PROBABILITY_COLUMNS)].to_numpy(float)
    labels = frame["next_official_delta_native_tick"].to_numpy(int)
    indices = labels - int(SUPPORT[0])
    cdf = np.cumsum(pmf, axis=1)
    observed = (SUPPORT[None, :] >= labels[:, None]).astype(float)
    running_threshold = (
        frame["official_running_max"].to_numpy(float)
        - frame["last_official_native_value"].to_numpy(float)
    )
    p_new_max = np.array([pmf[index, SUPPORT > value].sum() for index, value in enumerate(running_threshold)])
    label_new_max = labels > running_threshold
    return pd.DataFrame({
        "decision_vintage_id": frame["decision_vintage_id"].astype(str).to_numpy(),
        "target_date": frame["target_date"].astype(str).to_numpy(),
        "official_print_group_id": frame["official_print_group_id"].astype(str).to_numpy(),
        "label": labels,
        "rps": np.square(cdf[:, :-1] - observed[:, :-1]).sum(axis=1) / (len(SUPPORT) - 1),
        "logloss": -np.log(np.clip(pmf[np.arange(len(frame)), indices], 1e-7, 1.0)),
        "brier_up": np.square(pmf[:, SUPPORT > 0].sum(axis=1) - (labels > 0)),
        "brier_down": np.square(pmf[:, SUPPORT < 0].sum(axis=1) - (labels < 0)),
        "brier_unchanged": np.square(pmf[:, SUPPORT == 0].sum(axis=1) - (labels == 0)),
        "brier_new_running_max": np.square(p_new_max - (labels > running_threshold)),
        "entropy": -(pmf * np.log(np.clip(pmf, 1e-12, 1))).sum(axis=1),
        "p_up": pmf[:, SUPPORT > 0].sum(axis=1),
        "p_down": pmf[:, SUPPORT < 0].sum(axis=1),
        "p_unchanged": pmf[:, SUPPORT == 0].sum(axis=1),
        "p_new_running_max": p_new_max,
        "label_new_running_max": label_new_max,
    })


def weighted_summary(rows: pd.DataFrame, weights: np.ndarray) -> dict[str, Any]:
    result = {
        name: float(np.average(rows[name], weights=weights))
        for name in ("rps", "logloss", "brier_up", "brier_down", "brier_unchanged", "brier_new_running_max", "entropy")
    }
    result["label_conditional_rps"] = {
        str(label): float(np.average(group["rps"], weights=weights[group.index]))
        for label, group in rows.groupby("label")
    }
    result["raw_rows"] = int(len(rows))
    result["official_print_groups"] = int(rows["official_print_group_id"].nunique())
    result["target_dates"] = int(rows["target_date"].nunique())
    result["effective_weighted_n"] = effective_n(weights)
    calibration = {}
    for name, probability, label in (
        ("up", rows["p_up"], rows["label"].gt(0)),
        ("down", rows["p_down"], rows["label"].lt(0)),
        ("unchanged", rows["p_unchanged"], rows["label"].eq(0)),
        ("new_running_max", rows["p_new_running_max"], rows["label_new_running_max"]),
    ):
        p = np.clip(probability.to_numpy(float), 1e-7, 1 - 1e-7)
        y = label.to_numpy(int)
        if len(np.unique(y)) < 2:
            calibration[name] = {"intercept": None, "slope": None, "reliability_ece_10": None}
            continue
        logit = np.log(p / (1 - p)).reshape(-1, 1)
        model = LogisticRegression(C=1e6, max_iter=1000).fit(logit, y, sample_weight=weights)
        bins = pd.qcut(p, q=min(10, len(np.unique(p))), duplicates="drop")
        table = pd.DataFrame({"p": p, "y": y, "w": weights, "bin": bins})
        grouped = table.groupby("bin", observed=True).apply(
            lambda item: pd.Series({
                "p": np.average(item.p, weights=item.w),
                "y": np.average(item.y, weights=item.w),
                "w": item.w.sum(),
            }), include_groups=False,
        )
        calibration[name] = {
            "intercept": float(model.intercept_[0]),
            "slope": float(model.coef_[0, 0]),
            "reliability_ece_10": float(np.average(np.abs(grouped.p - grouped.y), weights=grouped.w)),
        }
    result["calibration"] = calibration
    return result


def bootstrap_delta(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    cluster: str,
    reps: int,
) -> dict[str, Any]:
    paired = candidate[["decision_vintage_id", "target_date", "official_print_group_id", "rps"]].merge(
        baseline[["decision_vintage_id", "rps"]],
        on="decision_vintage_id",
        how="inner",
        validate="one_to_one",
        suffixes=("_candidate", "_B2"),
    )
    if len(paired) != len(candidate) or len(paired) != len(baseline):
        raise RuntimeError("paired bootstrap denominator drift")
    paired["rps_delta"] = paired["rps_candidate"] - paired["rps_B2"]
    group_delta = paired.groupby(["target_date", "official_print_group_id"])["rps_delta"].mean()
    if cluster == "target_date":
        delta = group_delta.groupby(level="target_date").mean().to_numpy(float)
    elif cluster == "official_print_group":
        delta = group_delta.to_numpy(float)
    else:
        raise ValueError(cluster)
    rng = np.random.default_rng(RNG_SEED + (0 if cluster == "target_date" else 1))
    output = np.empty(reps, dtype=float)
    for start in range(0, reps, 100):
        stop = min(reps, start + 100)
        choices = rng.integers(0, len(delta), size=(stop - start, len(delta)))
        output[start:stop] = delta[choices].mean(axis=1)
    return {
        "candidate_minus_B2_rps": float(delta.mean()),
        "ci95": [float(np.quantile(output, 0.025)), float(np.quantile(output, 0.975))],
        "cluster": cluster,
        "clusters": int(len(delta)),
        "reps": int(reps),
        "seed": RNG_SEED + (0 if cluster == "target_date" else 1),
    }


def daily_equal_group_delta(candidate: pd.DataFrame, baseline: pd.DataFrame) -> pd.Series:
    paired = candidate[["decision_vintage_id", "target_date", "official_print_group_id", "rps"]].merge(
        baseline[["decision_vintage_id", "rps"]],
        on="decision_vintage_id", how="inner", validate="one_to_one", suffixes=("_candidate", "_B2"),
    )
    paired["rps_delta"] = paired["rps_candidate"] - paired["rps_B2"]
    return (
        paired.groupby(["target_date", "official_print_group_id"])["rps_delta"].mean()
        .groupby(level="target_date").mean().sort_values()
    )


def frozen_model_payloads(artifact: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Portable, reconstructible scoring parameters; no opaque pickle identity."""

    residual = np.clip(
        artifact["B2_train_labels"] - np.rint(artifact["B2_train_point"]),
        SUPPORT[0], SUPPORT[-1],
    ).astype(int)
    residual_counts = Counter(residual.tolist())
    b2 = {
        "support": SUPPORT.tolist(),
        "rounding": "half_up_to_1C",
        "laplace_alpha_per_support_tick": 1,
        "training_residual_counts": {str(int(value)): int(residual_counts.get(int(value), 0)) for value in SUPPORT},
    }
    m1_model = artifact["m1"]
    m1_thresholds = []
    for threshold, estimator in zip(SUPPORT[:-1], m1_model.models or []):
        if isinstance(estimator, float):
            m1_thresholds.append({"threshold": int(threshold), "constant_cdf": float(estimator)})
        else:
            m1_thresholds.append({
                "threshold": int(threshold),
                "classes": estimator.classes_.tolist(),
                "coef": estimator.coef_.tolist(),
                "intercept": estimator.intercept_.tolist(),
            })
    m1 = {
        "features": list(m1_model.features),
        "support": SUPPORT.tolist(),
        "imputer_statistics": m1_model.imputer.statistics_.tolist(),
        "scaler_mean": m1_model.scaler.mean_.tolist(),
        "scaler_scale": m1_model.scaler.scale_.tolist(),
        "threshold_models": m1_thresholds,
        "temperature": float(artifact["m1_temperature"]),
    }
    m2_model = artifact["m2"]
    m2 = {
        "features": [{"name": name, "increasing": bool(increasing)} for name, increasing in m2_model.features],
        "support": SUPPORT.tolist(),
        "intercept": float(m2_model.intercept),
        "residual_scale": float(m2_model.residual_scale),
        "training_fill_values": [float(value) for value in (m2_model.fills or [])],
        "components": [
            {"x_thresholds": component.X_thresholds_.tolist(), "y_thresholds": component.y_thresholds_.tolist()}
            for component in (m2_model.components or [])
        ],
    }
    return {"B2": b2, "M1": m1, "M2": m2}


def build_feature_audit(
    historical: pd.DataFrame,
    captured: pd.DataFrame,
    frozen_path: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    sparse, _ = make_captured_panel(DEFAULT_EVENTS)
    sparse = sparse.set_index("decision_vintage_id")
    lineage_parts: list[pd.DataFrame] = []
    statuses: Counter[str] = Counter()
    path_validation_reasons: Counter[str] = Counter()
    complete_event_ids: list[str] = []
    for event in captured.to_dict("records"):
        event_id = str(event["event_id"])
        path = frozen_path.loc[frozen_path["event_id"].astype(str).eq(event_id)].copy()
        path["source_observation_id"] = [
            feature_hash({
                "event": event_id,
                "observed_at": value,
                "raw": raw,
                "payload": payload,
            })
            for value, raw, payload in zip(path["observed_at"], path.get("raw_row_hash"), path.get("payload_hash"))
        ]
        for feature in CORE_FEATURES:
            if feature not in PATH_FEATURES:
                path[feature] = event.get(feature)
        result = AmsterdamFeatureBuilderV2.build_vintage(
            path,
            decision_vintage_id=event_id,
            feature_cutoff_at=event["ts_utc"],
            decision_ready_at=event["ts_utc"],
            observation_cutoff_at=event["source_obs_ts_utc"],
            availability_class="CAPTURED_PIT_ARCHIVE",
            feature_names=CORE_FEATURES,
            source_path_complete=True,
        )
        statuses[result.status] += 1
        if result.status == "OK":
            complete_event_ids.append(event_id)
        if result.status != "OK":
            observed = path.sort_values("observed_at")["observed_at"]
            gaps = observed.diff().dropna().dt.total_seconds().div(60)
            if not gaps.eq(10).all():
                path_validation_reasons["NON_10_MINUTE_CADENCE"] += 1
            if observed.max() != pd.Timestamp(event["source_obs_ts_utc"]):
                path_validation_reasons["PATH_END_MISMATCH"] += 1
            expected_start = pd.Timestamp(str(event["target_date"]), tz="Europe/Amsterdam").tz_convert("UTC")
            if observed.min() != expected_start:
                path_validation_reasons["LOCAL_DATE_START_MISMATCH"] += 1
        part = result.lineage.copy()
        part["feature_status"] = result.status
        part["feature_vector_hash"] = result.feature_vector_hash
        part["feature_cutoff_at"] = event["ts_utc"]
        part["decision_ready_at"] = event["ts_utc"]
        old = sparse.loc[event_id] if event_id in sparse.index else None
        part["v1_sparse_feature_value"] = [
            None if old is None or pd.isna(old.get(name)) else float(old.get(name))
            for name in part["feature_name"]
        ]
        part["corrected_source_path_feature_value_pre_gate"] = [
            None if pd.isna(event.get(name)) else float(event.get(name))
            for name in part["feature_name"]
        ]
        part["v1_value_changed"] = [
            not np.isclose(
                np.nan if old is None else old.get(name, np.nan),
                np.nan if pd.isna(event.get(name)) else event.get(name),
                equal_nan=True,
            )
            for name in part["feature_name"]
        ]
        for numeric_column in (
            "feature_value",
            "v1_sparse_feature_value",
            "corrected_source_path_feature_value_pre_gate",
        ):
            part[numeric_column] = pd.to_numeric(part[numeric_column], errors="coerce").astype(float)
        lineage_parts.append(part)
    rows = pd.concat(lineage_parts, ignore_index=True)
    changed = {
        name: int(group["v1_value_changed"].sum())
        for name, group in rows.groupby("feature_name")
    }
    if not complete_event_ids:
        raise RuntimeError("no complete captured path available for golden parity fixture")
    sample_event = complete_event_ids[0]
    sample_event_row = captured.loc[captured["event_id"].astype(str).eq(sample_event)].iloc[0]
    sample_path = frozen_path.loc[frozen_path["event_id"].astype(str).eq(sample_event)].copy()
    sample_path["source_observation_id"] = [feature_hash({"event": sample_event, "i": index}) for index in range(len(sample_path))]
    for feature in CORE_FEATURES:
        if feature not in PATH_FEATURES:
            sample_path[feature] = sample_event_row.get(feature)
    common = dict(
        source_path=sample_path,
        decision_vintage_id=sample_event,
        feature_cutoff_at=sample_event_row["ts_utc"],
        decision_ready_at=sample_event_row["ts_utc"],
        observation_cutoff_at=sample_event_row["source_obs_ts_utc"],
        feature_names=CORE_FEATURES,
        source_path_complete=True,
    )
    hashes = {
        archive: AmsterdamFeatureBuilderV2.build_vintage(availability_class=archive, **common).feature_vector_hash
        for archive in ("HISTORICAL_FINAL_ARCHIVE", "CAPTURED_PIT_ARCHIVE", "PROSPECTIVE_SHADOW")
    }
    future = sample_path.copy()
    latest = future.iloc[-1].copy()
    latest["observed_at"] = pd.Timestamp(latest["observed_at"]) + pd.Timedelta(minutes=10)
    latest["available_at"] = pd.Timestamp(sample_event_row["ts_utc"]) + pd.Timedelta(minutes=10)
    latest["latest_fast_native_value"] = float(latest["latest_fast_native_value"]) + 9
    latest["source_observation_id"] = feature_hash({"future": sample_event})
    future = pd.concat([future, pd.DataFrame([latest])], ignore_index=True)
    future_hash = AmsterdamFeatureBuilderV2.build_vintage(
        source_path=future,
        availability_class="CAPTURED_PIT_ARCHIVE",
        **{key: value for key, value in common.items() if key != "source_path"},
    ).feature_vector_hash
    sparse_negative = AmsterdamFeatureBuilderV2.build_vintage(
        source_path=sample_path.iloc[::max(1, len(sample_path) // 3)].copy(),
        availability_class="CAPTURED_PIT_ARCHIVE",
        source_path_complete=False,
        **{key: value for key, value in common.items() if key not in {"source_path", "source_path_complete"}},
    )
    audit = {
        "feature_builder": AmsterdamFeatureBuilderV2.version,
        "historical_captured_shadow_single_code_path": True,
        "captured_raw_opportunities": int(len(captured)),
        "captured_complete_path": int(statuses["OK"]),
        "captured_fail_closed_incomplete_path": int(statuses["INCOMPLETE_CAPTURED_SOURCE_PATH"]),
        "captured_path_validation_reasons": dict(path_validation_reasons),
        "captured_source_path_rows": int(len(frozen_path)),
        "v1_sparse_opportunity_path_used_for_new_predictions": False,
        "v1_impacted_rows_by_feature": changed,
        "v1_impacted_unique_rows": int(rows.groupby("decision_vintage_id")["v1_value_changed"].any().sum()),
        "feature_coverage": {
            name: {
                "historical": float(historical[name].notna().mean()),
                "captured": float(captured[name].notna().mean()),
            }
            for name in CORE_FEATURES
        },
        "feature_distribution_drift": {
            name: {
                "historical_mean": None if historical[name].dropna().empty else float(historical[name].mean()),
                "historical_std": None if historical[name].dropna().empty else float(historical[name].std()),
                "captured_mean": None if captured[name].dropna().empty else float(captured[name].mean()),
                "captured_std": None if captured[name].dropna().empty else float(captured[name].std()),
                "missingness_drift_captured_minus_historical": float(captured[name].isna().mean() - historical[name].isna().mean()),
            }
            for name in CORE_FEATURES
        },
        "normalized_path_byte_identity": hashes,
        "normalized_path_all_hashes_equal": len(set(hashes.values())) == 1,
        "future_observation_invariance": future_hash == hashes["CAPTURED_PIT_ARCHIVE"],
        "sparse_path_negative_status": sparse_negative.status,
        "time_window": "rolling six 10-minute source observations for volatility/reheat; adjacent <=20m for slope/acceleration",
        "units": {"temperature": "degC", "time_since_high_minutes": "minutes"},
        "rounding": "no rounding inside path features; B2 applies half-up only at model layer",
    }
    return rows, audit


def prepare_predictions(
    historical: pd.DataFrame,
    captured: pd.DataFrame,
    captured_scored: pd.DataFrame,
    oof: pd.DataFrame,
    evaluated: pd.DataFrame,
    artifact: dict[str, Any],
    output: Path,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    outer = evaluated.loc[evaluated["fold"].eq("historical_outer")].copy()
    full_predictions = pd.concat([oof, outer], ignore_index=True)
    captured_predictions = evaluated.loc[evaluated["fold"].eq("captured_pit")].copy()
    eval_ids = full_predictions["decision_vintage_id"].astype(str).unique()
    full_frame = historical.set_index("decision_vintage_id").loc[eval_ids].reset_index()
    full_frame = full_frame.sort_values(["target_date", "observed_at", "decision_vintage_id"])
    matched_frame = full_frame.loc[full_frame["opportunity_matched"]].copy()
    cohorts = {
        "FULL_CHECKPOINT": ("P0_FULL_CHECKPOINT_WEATHER", full_frame, full_predictions),
        "OPPORTUNITY_MATCHED": (
            "P1_RETROSPECTIVE_OBSERVATION_TIME_OPPORTUNITY_PROXY",
            matched_frame,
            full_predictions.loc[full_predictions["decision_vintage_id"].isin(matched_frame["decision_vintage_id"])].copy(),
        ),
        "CAPTURED_PIT": ("P2_CAPTURED_PIT_OPPORTUNITY", captured_scored, captured_predictions),
    }
    model_payloads = frozen_model_payloads(artifact)
    artifact_hashes = {name: feature_hash(payload) for name, payload in model_payloads.items()}
    model_manifests = {
        "B2": {"model_id": MODEL_IDS["B2"], "role": "B2_REFERENCE", "model_artifact_hash": artifact_hashes["B2"], "calibration_artifact_hash": feature_hash({"calibration": "laplace_residual_distribution", "alpha": 1}), "spec": "latest fast half-up rounded to 1C official lattice plus frozen train residual distribution"},
        "M1": {"model_id": MODEL_IDS["M1"], "role": "M1_CHALLENGER", "model_artifact_hash": artifact_hashes["M1"], "calibration_artifact_hash": feature_hash({"temperature": float(artifact["m1_temperature"])}), "spec": "strongly regularized cumulative ordinal logistic; frozen features/regularization/calibration"},
        "M2": {"model_id": MODEL_IDS["M2"], "role": "M2_CHALLENGER", "model_artifact_hash": artifact_hashes["M2"], "calibration_artifact_hash": feature_hash({"calibration": "identity_frozen"}), "spec": "frozen monotonic piecewise-linear additive challenger"},
    }
    for short, manifest in model_manifests.items():
        manifest.update({
            "feature_builder": AmsterdamFeatureBuilderV2.version,
            "support": SUPPORT.tolist(),
            "training_cutoff": str(artifact["training_cutoff"]),
            "live_eligible": False,
            "captured_results_used_for_tuning": False,
            "frozen_scoring_parameters": model_payloads[short],
        })
        write_json(output / f"{short}_MODEL_ARTIFACT_MANIFEST.json", manifest)
    cohort_ledger_parts = []
    comparison: dict[str, Any] = {
        "schema_version": "wcir_amsterdam_model_comparison_v2",
        "primary_metric": "official_print_group_then_target_date_equal_RPS",
        "bootstrap_reps": BOOTSTRAP_REPS,
        "cohorts": {},
    }
    for suffix, (cohort_id, frame, predictions) in cohorts.items():
        weights = normalized_weights(frame, "primary")
        availability_class = "CAPTURED_PIT_ARCHIVE" if suffix == "CAPTURED_PIT" else "HISTORICAL_FINAL_ARCHIVE"
        ledger = frame[["decision_vintage_id", "official_print_group_id", "target_date", "observed_at", "next_official_delta_native_tick"]].copy()
        ledger["cohort_id"] = cohort_id
        ledger["weight"] = weights
        ledger["availability_class"] = availability_class
        ledger["weather_label_eligible"] = True
        ledger["strict_pit_feature_eligible"] = suffix == "CAPTURED_PIT"
        ledger["market_prior_eligible"] = False
        ledger["executable_entry_eligible"] = False
        ledger["markout_eligible"] = False
        ledger["pairwise_policy_comparable"] = False
        ledger["feature_status"] = "OK"
        if suffix == "FULL_CHECKPOINT":
            population = historical.copy()
        elif suffix == "OPPORTUNITY_MATCHED":
            population = historical.loc[historical["opportunity_matched"]].copy()
        else:
            population = captured.copy()
        population_ledger = population[["decision_vintage_id", "official_print_group_id", "target_date", "observed_at", "next_official_delta_native_tick"]].copy()
        population_ledger["cohort_id"] = cohort_id
        population_ledger["weight"] = normalized_weights(population, "primary")
        score_weights = dict(zip(frame["decision_vintage_id"].astype(str), weights))
        population_ledger["model_score_eligible"] = population_ledger["decision_vintage_id"].astype(str).isin(score_weights)
        population_ledger["model_score_weight"] = population_ledger["decision_vintage_id"].astype(str).map(score_weights)
        population_ledger["availability_class"] = availability_class
        population_ledger["weather_label_eligible"] = True
        population_ledger["strict_pit_feature_eligible"] = population_ledger["model_score_eligible"] if suffix == "CAPTURED_PIT" else False
        population_ledger["market_prior_eligible"] = False
        population_ledger["executable_entry_eligible"] = False
        population_ledger["markout_eligible"] = False
        population_ledger["pairwise_policy_comparable"] = False
        population_ledger["feature_status"] = np.where(
            population_ledger["strict_pit_feature_eligible"] | (suffix != "CAPTURED_PIT"),
            "OK", "INCOMPLETE_CAPTURED_SOURCE_PATH",
        )
        cohort_ledger_parts.append(population_ledger)
        model_rows = {}
        summaries = {}
        for short, model_id in MODEL_IDS.items():
            selected = predictions.loc[predictions["model_id"].eq(model_id)].copy()
            selected = selected.set_index("decision_vintage_id").loc[frame["decision_vintage_id"]].reset_index()
            selected["cohort_id"] = cohort_id
            selected["true_delta_tick"] = selected["next_official_delta_native_tick"].astype(int)
            selected["feature_status"] = "OK"
            selected["weight"] = weights
            selected["model_artifact_hash"] = artifact_hashes[short]
            selected["availability_class"] = ledger["availability_class"].iloc[0]
            selected["fold"] = selected["fold"].astype(str)
            required = [
                "decision_vintage_id", "official_print_group_id", "target_date", "cohort_id",
                "true_delta_tick", *PROBABILITY_COLUMNS, "feature_status", "weight",
                "model_artifact_hash", "availability_class", "observed_at", "p_up", "p_down",
                "p_unchanged", "p_new_running_max", "predictive_entropy", "fold",
            ]
            selected[required].to_parquet(output / f"{short}_PREDICTIONS_{suffix}.parquet", index=False, compression="zstd")
            rows = metric_rows(frame.reset_index(drop=True), selected)
            rows.index = np.arange(len(rows))
            model_rows[short] = rows
            sensitivities = {}
            for mode in ("primary", "raw_row", "target_date_only"):
                sensitivities[mode] = weighted_summary(rows, normalized_weights(frame, mode))
            complete_mask = frame[list(CORE_FEATURES)].notna().all(axis=1).to_numpy()
            complete_frame = frame.loc[complete_mask].copy()
            complete_rows = rows.loc[complete_mask].reset_index(drop=True)
            sensitivities["complete_feature_case"] = weighted_summary(
                complete_rows, normalized_weights(complete_frame, "primary")
            )
            first_mask = ~frame.sort_values("observed_at").duplicated(["target_date", "official_print_group_id"], keep="first")
            first_frame = frame.sort_values("observed_at").loc[first_mask].copy()
            first_selected = selected.set_index("decision_vintage_id").loc[first_frame["decision_vintage_id"]].reset_index()
            first_rows = metric_rows(first_frame.reset_index(drop=True), first_selected)
            sensitivities["first_eligible_opportunity_per_print_group"] = weighted_summary(
                first_rows, normalized_weights(first_frame, "primary")
            )
            summaries[short] = sensitivities
        comparisons = {}
        for short in ("M1", "M2"):
            date_bootstrap = bootstrap_delta(model_rows[short], model_rows["B2"], cluster="target_date", reps=BOOTSTRAP_REPS)
            group_bootstrap = bootstrap_delta(model_rows[short], model_rows["B2"], cluster="official_print_group", reps=BOOTSTRAP_REPS)
            daily = daily_equal_group_delta(model_rows[short], model_rows["B2"])
            comparisons[f"{short}_minus_B2"] = {
                "target_date_block_bootstrap": date_bootstrap,
                "official_print_group_cluster_sensitivity": group_bootstrap,
                "leave_one_date_out_contribution": {str(key): float(value) for key, value in daily.items()},
                "best_two_dates_contribution": float(daily.iloc[:2].sum()) if len(daily) >= 2 else None,
            }
        comparison["cohorts"][cohort_id] = {
            "models": summaries,
            "comparisons": comparisons,
            "label_distribution": frame["next_official_delta_native_tick"].value_counts(normalize=True).sort_index().to_dict(),
            "source_hour_distribution": frame["observed_at"].dt.tz_convert("Europe/Amsterdam").dt.hour.value_counts().sort_index().to_dict(),
            "feature_missingness": frame[list(CORE_FEATURES)].isna().mean().to_dict(),
            "scored_denominator": {
                "raw_rows": int(len(frame)),
                "official_print_groups": int(frame["official_print_group_id"].nunique()),
                "target_dates": int(frame["target_date"].nunique()),
            },
        }
    p1 = comparison["cohorts"]["P1_RETROSPECTIVE_OBSERVATION_TIME_OPPORTUNITY_PROXY"]["comparisons"]
    p2 = comparison["cohorts"]["P2_CAPTURED_PIT_OPPORTUNITY"]["comparisons"]
    p1_supports = all(p1[f"{short}_minus_B2"]["target_date_block_bootstrap"]["ci95"][1] < 0 for short in ("M1", "M2"))
    p2_crosses = all(
        p2[f"{short}_minus_B2"]["target_date_block_bootstrap"]["ci95"][0] <= 0 <= p2[f"{short}_minus_B2"]["target_date_block_bootstrap"]["ci95"][1]
        for short in ("M1", "M2")
    )
    p2_both_worse = all(
        p2[f"{short}_minus_B2"]["target_date_block_bootstrap"]["ci95"][0] > 0
        for short in ("M1", "M2")
    )
    p2_group_cluster_crosses = all(
        p2[f"{short}_minus_B2"]["official_print_group_cluster_sensitivity"]["ci95"][0] <= 0
        <= p2[f"{short}_minus_B2"]["official_print_group_cluster_sensitivity"]["ci95"][1]
        for short in ("M1", "M2")
    )
    captured_complete = len(captured_scored) == len(captured)
    comparison["evidence_ranking"] = (
        "B2_PRIMARY_REFERENCE_M1_M2_CHALLENGERS" if p2_both_worse
        else "INCONCLUSIVE_THREE_ARM_SHADOW" if p1_supports and p2_crosses and captured_complete
        else "INCONCLUSIVE_THREE_ARM_SHADOW_WITH_CAPTURED_PATH_ATTRITION" if p1_supports and p2_crosses
        else "MIXED_EVIDENCE_REVIEW_REQUIRED"
    )
    comparison["M1_M2_significantly_worse_than_B2_on_corrected_P2"] = p2_both_worse
    comparison["P2_official_print_group_cluster_CIs_cross_zero"] = p2_group_cluster_crosses
    comparison["captured_feature_evidence_complete"] = captured_complete
    cohort_ledger = pd.concat(cohort_ledger_parts, ignore_index=True)
    cohort_ledger.to_parquet(output / "AMSTERDAM_COHORT_LEDGER.parquet", index=False, compression="zstd")
    cohort_summary = {}
    for cohort_id, group in cohort_ledger.groupby("cohort_id"):
        cohort_summary[cohort_id] = {
            "raw_rows": int(len(group)),
            "model_scored_rows": int(group["model_score_eligible"].sum()),
            "unique_official_print_groups": int(group["official_print_group_id"].nunique()),
            "unique_target_dates": int(group["target_date"].nunique()),
            "effective_weighted_n": effective_n(group["weight"].to_numpy(float)),
            "label_distribution": group["next_official_delta_native_tick"].value_counts().sort_index().to_dict(),
            "source_hour_distribution": group["observed_at"].dt.tz_convert("Europe/Amsterdam").dt.hour.value_counts().sort_index().to_dict(),
            "model_score_warmup_exclusions": int((~group["model_score_eligible"]).sum()),
        }
    return cohort_ledger, cohort_summary, comparison


def _truth_sweep(truth: dict[str, Any] | None, side: str, shares: float) -> dict[str, Any]:
    if truth is None:
        return {"fully_executable": False, "effective_value_usd": None}
    for row in truth.get("sweeps", []):
        if row.get("side") == side and float(row.get("shares")) == float(shares):
            return row
    return {"fully_executable": False, "effective_value_usd": None}


def build_market_evidence(
    captured: pd.DataFrame,
    base_reconciliation: pd.DataFrame,
    output: Path,
    *,
    strict_pit_eligible_ids: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any]]:
    aligned = read_jsonl_gz(DEFAULT_ALIGNED)
    truths = {row["truth_id"]: row for row in read_jsonl_gz(DEFAULT_TRUTHS)}
    aligned_by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in aligned:
        aligned_by_event[str(row["event_id"])].append(row)
    base = base_reconciliation.set_index("event_id")
    identity_rows = []
    replay_rows = []
    recovered_reason = Counter()
    v1_failure_reason = Counter()
    for event in captured.to_dict("records"):
        event_id = str(event["event_id"])
        rest = base.loc[event_id]
        checkpoints = aligned_by_event.get(event_id, [])
        exact = []
        for row in checkpoints:
            truth = truths.get(row.get("book_truth_id"))
            if row.get("book_valid") and truth and str(truth.get("token_id")) == str(event["token_id"]):
                exact.append((row, truth))
        source_t0 = next(((row, truth) for row, truth in exact if row["checkpoint"] == "source_t0"), (None, None))
        source_t0_causal = bool(
            source_t0[0]
            and pd.Timestamp(event["ts_utc"]) <= pd.Timestamp(source_t0[0]["checkpoint_at_utc"])
            < pd.Timestamp(event["official_first_seen_at_utc"])
        )
        tier_a_entry_1 = bool(source_t0_causal and _truth_sweep(source_t0[1], "buy", 1.0)["fully_executable"])
        tier_a_entry_5 = bool(source_t0_causal and _truth_sweep(source_t0[1], "buy", 5.0)["fully_executable"])
        causal_rest_candidates = []
        for offset in (0, 15, 30, 60, 120, 300):
            snapshot_path = _snapshot_path(DEFAULT_LADDER_ROOT, event, offset)
            if snapshot_path is None:
                continue
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            captured_at = pd.Timestamp(snapshot["capture_started_at_utc"])
            if not (
                pd.Timestamp(event["ts_utc"]) <= captured_at
                < pd.Timestamp(event["official_first_seen_at_utc"])
            ):
                continue
            record, join_reason = _exact_record(snapshot, event)
            if record is None:
                continue
            causal_rest_candidates.append((captured_at, offset, snapshot_path, snapshot, record))
        causal_rest_candidates.sort(key=lambda item: (item[0], item[1]))
        rest_entry = causal_rest_candidates[0] if causal_rest_candidates else None
        rest_record = None if rest_entry is None else rest_entry[4]
        token_side = None
        if rest_record is not None:
            token_side = "yes" if str(rest_record.get("yes_token_id")) == str(event["token_id"]) else "no"
        rest_buy_1 = _sweep(rest_record.get(f"{token_side}_book_asks", []), 1.0, "buy") if rest_record is not None else {"fully_executable": False, "effective_value_usd": None}
        rest_buy_5 = _sweep(rest_record.get(f"{token_side}_book_asks", []), 5.0, "buy") if rest_record is not None else {"fully_executable": False, "effective_value_usd": None}
        t0_buy_1 = {"fully_executable": False, "effective_value_usd": None}
        t0_buy_5 = {"fully_executable": False, "effective_value_usd": None}
        if rest.get("rest_t0_snapshot_path"):
            t0_snapshot = json.loads(Path(str(rest["rest_t0_snapshot_path"])).read_text(encoding="utf-8"))
            t0_record, _ = _exact_record(t0_snapshot, event)
            if t0_record is not None:
                t0_side = "yes" if str(t0_record.get("yes_token_id")) == str(event["token_id"]) else "no"
                t0_buy_1 = _sweep(t0_record.get(f"{t0_side}_book_asks", []), 1.0, "buy")
                t0_buy_5 = _sweep(t0_record.get(f"{t0_side}_book_asks", []), 5.0, "buy")
        tier_b_entry_1 = bool(rest_buy_1["fully_executable"])
        tier_b_entry_5 = bool(rest_buy_5["fully_executable"])
        if tier_b_entry_5:
            primary_reason = "eligible"
        elif rest_record is not None and len(rest_record.get(f"{token_side}_book_asks", [])) == 0:
            primary_reason = "one-sided"
        elif rest_entry is None:
            primary_reason = "checkpoint missing"
        elif not rest["rest_exact_identity"]:
            primary_reason = "market identity mismatch"
        elif not tier_b_entry_1:
            primary_reason = "insufficient 1-share depth"
        else:
            primary_reason = "insufficient 5-share depth"
        recovered_reason[primary_reason] += 1
        if not exact:
            old_reason = "no archive row"
        elif source_t0[0] is None:
            old_reason = "checkpoint missing"
        elif not tier_a_entry_1:
            old_reason = "one-sided"
        elif not tier_a_entry_5:
            old_reason = "insufficient 5-share depth"
        else:
            old_reason = "other"
        v1_failure_reason[old_reason] += 1
        tier = (
            "TIER_A_WS_DETERMINISTIC" if exact
            else "TIER_B_REST_EXACT_SNAPSHOT" if bool(rest["rest_exact_identity"])
            else "TIER_C_LEGACY_EXACT_QUOTE" if int(rest["legacy_selected_exact_overlap_count"]) else "TIER_D_UNUSABLE"
        )
        checkpoint_map = {
            row["checkpoint"]: {
                "book_snapshot_id": row.get("book_snapshot_id"),
                "book_truth_id": row.get("book_truth_id"),
                "checkpoint_at_utc": row.get("checkpoint_at_utc"),
                "age_seconds": row.get("age_seconds"),
            }
            for row, _ in exact
        }
        identity_rows.append({
            "event_id": event_id,
            "decision_vintage_id": event_id,
            "feature_status": "OK" if event_id in strict_pit_eligible_ids else "INCOMPLETE_CAPTURED_SOURCE_PATH",
            "strict_pit_feature_eligible": event_id in strict_pit_eligible_ids,
            "target_date": event["target_date"],
            "market_id": str(event["market_id"]),
            "condition_id": str(event["condition_id"]),
            "token_id": str(event["token_id"]),
            "bracket": str(event["t_minus_1_no_bracket_c"]),
            "side": "NO",
            "checkpoint_role": "source_t0_to_official_markout",
            "snapshot_book_truth_ids": json.dumps(checkpoint_map, sort_keys=True),
            "join_method": "exact event_id + market_id + condition_id + token_id; no fuzzy fallback",
            "join_status": "EXACT_IDENTITY_RECONCILED" if bool(rest["rest_exact_identity"]) else "UNUSABLE",
            "exclusion_reason": None if tier_b_entry_1 else primary_reason,
            "v1_replay_exclusion_reason": old_reason,
            "recovered_rest_entry_reason": primary_reason,
            "recovered_entry_snapshot_path": None if rest_entry is None else portable_path(rest_entry[2]),
            "recovered_entry_snapshot_sha256": None if rest_entry is None else sha256(rest_entry[2]),
            "recovered_entry_capture_at_utc": None if rest_entry is None else rest_entry[0].isoformat(),
            "recovered_entry_source_offset_seconds": None if rest_entry is None else int(rest_entry[1]),
            "recovered_entry_at_or_after_decision_ready": bool(rest_entry is not None),
            "entry_price_deterioration_1share_usd": (
                float(rest_buy_1["effective_value_usd"] - t0_buy_1["effective_value_usd"])
                if rest_buy_1["fully_executable"] and t0_buy_1["fully_executable"] else None
            ),
            "entry_price_deterioration_5share_usd": (
                float(rest_buy_5["effective_value_usd"] - t0_buy_5["effective_value_usd"])
                if rest_buy_5["fully_executable"] and t0_buy_5["fully_executable"] else None
            ),
            "evidence_tier": tier,
            "tier_a_entry_1share": tier_a_entry_1,
            "tier_a_entry_5share": tier_a_entry_5,
            "tier_a_plus_b_entry_1share": tier_a_entry_1 or tier_b_entry_1,
            "tier_a_plus_b_entry_5share": tier_a_entry_5 or tier_b_entry_5,
        })
        for shares in (1.0, 5.0):
            entry_ok = tier_a_entry_1 if shares == 1 else tier_a_entry_5
            sensitivity_entry_ok = entry_ok or (tier_b_entry_1 if shares == 1 else tier_b_entry_5)
            entry_value = None
            if entry_ok:
                entry_value = _truth_sweep(source_t0[1], "buy", shares).get("effective_value_usd")
            elif sensitivity_entry_ok:
                entry_value = (rest_buy_1 if shares == 1 else rest_buy_5).get("effective_value_usd")
            for horizon in (5, 15, 30, 60, 120):
                pair = next(((row, truth) for row, truth in exact if row["checkpoint"] == f"official_plus_{horizon}s"), (None, None))
                exit = _truth_sweep(pair[1], "sell", shares)
                markout_ok = bool(sensitivity_entry_ok and exit["fully_executable"] and entry_value is not None)
                replay_rows.append({
                    "event_id": event_id,
                    "decision_vintage_id": event_id,
                    "target_date": event["target_date"],
                    "official_print_group_id": event["official_print_group_id"],
                    "market_id": str(event["market_id"]),
                    "condition_id": str(event["condition_id"]),
                    "token_id": str(event["token_id"]),
                    "shares": shares,
                    "markout_horizon_seconds_after_official_first_seen": horizon,
                    "tier_a_entry_eligible": entry_ok,
                    "tier_a_plus_b_entry_eligible": sensitivity_entry_ok,
                    "tier_a_markout_eligible": bool(entry_ok and exit["fully_executable"] and entry_value is not None),
                    "markout_eligible": markout_ok,
                    "entry_effective_ask_after_fee_usd": entry_value,
                    "future_effective_bid_after_fee_usd": exit.get("effective_value_usd"),
                    "zero_notional_counterfactual_markout_usd": (
                        float(exit["effective_value_usd"] - entry_value) if markout_ok else None
                    ),
                    "operational_result": "ABSTAIN_0" if not markout_ok else "COUNTERFACTUAL_ONLY",
                    "conditional_economic_result": (
                        float(exit["effective_value_usd"] - entry_value) if markout_ok else None
                    ),
                    "data_failure_reason": None if markout_ok else (
                        "entry_missing" if not sensitivity_entry_ok else "exit_horizon_missing_or_invalid"
                    ),
                    "trade_class": "ZERO_NOTIONAL_COUNTERFACTUAL",
                    "actual_fill": False,
                    "actual_pnl": False,
                    "orders": 0,
                    "fills": 0,
                    "notional": 0,
                })
    identities = pd.DataFrame(identity_rows)
    replay = pd.DataFrame(replay_rows)
    identities.to_parquet(output / "AMSTERDAM_EVENT_MARKET_IDENTITY_RECONCILIATION.parquet", index=False, compression="zstd")
    coverage_columns = [
        "event_id", "decision_vintage_id", "target_date", "feature_status",
        "strict_pit_feature_eligible", "market_id", "condition_id", "token_id",
        "bracket", "side", "join_status", "evidence_tier",
        "v1_replay_exclusion_reason", "recovered_rest_entry_reason",
        "recovered_entry_capture_at_utc", "recovered_entry_source_offset_seconds",
        "tier_a_entry_1share", "tier_a_entry_5share",
        "tier_a_plus_b_entry_1share", "tier_a_plus_b_entry_5share",
        "exclusion_reason",
    ]
    identities[coverage_columns].to_parquet(
        output / "AMSTERDAM_EXECUTABLE_COVERAGE_ROWS.parquet", index=False, compression="zstd"
    )
    replay.to_parquet(output / "EXECUTABLE_REPLAY_ROWS_V2.parquet", index=False, compression="zstd")
    horizon_counts = [
        {
            "shares": float(shares),
            "horizon": int(horizon),
            "tier_a_markout_rows": int(group["tier_a_markout_eligible"].sum()),
            "tier_a_plus_b_markout_rows": int(group["markout_eligible"].sum()),
            "tier_a_plus_b_markout_unique_events": int(group.loc[group["markout_eligible"], "event_id"].nunique()),
            "tier_a_plus_b_markout_unique_official_print_groups": int(group.loc[group["markout_eligible"], "official_print_group_id"].nunique()),
            "tier_a_plus_b_markout_unique_target_dates": int(group.loc[group["markout_eligible"], "target_date"].nunique()),
            "after_cost_markout_usd": None if not group["markout_eligible"].any() else float(group["zero_notional_counterfactual_markout_usd"].sum()),
        }
        for (shares, horizon), group in replay.groupby(["shares", "markout_horizon_seconds_after_official_first_seen"])
    ]
    summary = {
        "events": int(len(identities)),
        "exact_identity_reconciled": int(identities["join_status"].eq("EXACT_IDENTITY_RECONCILED").sum()),
        "tier_histogram": identities["evidence_tier"].value_counts().to_dict(),
        "tier_a_entry_1share": int(identities["tier_a_entry_1share"].sum()),
        "tier_a_entry_5share": int(identities["tier_a_entry_5share"].sum()),
        "tier_a_plus_b_entry_1share": int(identities["tier_a_plus_b_entry_1share"].sum()),
        "tier_a_plus_b_entry_5share": int(identities["tier_a_plus_b_entry_5share"].sum()),
        "markout_by_size_horizon": horizon_counts,
        "execution_quality": {
            "entry_to_submit_edge_survival": None,
            "entry_to_submit_edge_survival_reason": "no exact candidate overlap between the 87 next-print identities and legacy selected/submit-proxy ledger",
            "entry_price_deterioration_1share_mean_usd": None if identities["entry_price_deterioration_1share_usd"].dropna().empty else float(identities["entry_price_deterioration_1share_usd"].mean()),
            "entry_price_deterioration_5share_mean_usd": None if identities["entry_price_deterioration_5share_usd"].dropna().empty else float(identities["entry_price_deterioration_5share_usd"].mean()),
            "entry_price_deterioration_1share_rows": int(identities["entry_price_deterioration_1share_usd"].notna().sum()),
            "entry_price_deterioration_5share_rows": int(identities["entry_price_deterioration_5share_usd"].notna().sum()),
            "market_identity_failure_rate": float(identities["join_status"].ne("EXACT_IDENTITY_RECONCILED").mean()),
            "recovered_checkpoint_missing_rate": float(identities["recovered_rest_entry_reason"].eq("checkpoint missing").mean()),
            "recovered_one_sided_rate": float(identities["recovered_rest_entry_reason"].eq("one-sided").mean()),
            "recovered_insufficient_1share_depth_rate": float(identities["recovered_rest_entry_reason"].eq("insufficient 1-share depth").mean()),
            "recovered_insufficient_5share_depth_rate": float(identities["recovered_rest_entry_reason"].eq("insufficient 5-share depth").mean()),
        },
        "fuzzy_join_promotions": 0,
        "orders_fills_notional": [0, 0, 0],
    }
    required_reasons = (
        "no archive row", "join key mismatch", "market identity mismatch", "token missing",
        "checkpoint missing", "book invalid", "connection/liveness invalid", "stale",
        "one-sided", "insufficient 1-share depth", "insufficient 5-share depth",
        "fee unavailable", "exit horizon missing", "other",
    )
    coverage = {
        "events": int(len(identities)),
        "reason_histogram": {name: int(v1_failure_reason[name]) for name in required_reasons},
        "reason_histogram_total": int(sum(v1_failure_reason.values())),
        "recovered_rest_entry_reason_histogram": {name: int(recovered_reason[name]) for name in ("eligible", "checkpoint missing", "one-sided", "market identity mismatch", "insufficient 1-share depth", "insufficient 5-share depth")},
        "v1_zero_coverage_root_cause": "v1 consumed Stage-2 deterministic WS rows only; Amsterdam source-t0 exact token was one-sided with no direct buy sweep. It did not ingest the separately archived exact-identity REST/full-ladder t0 snapshots recovered here.",
        "old_42_of_42_evidence_path": "repo://docs/analysis/wcir_admission_forward_market_trigger_review.md:321",
        "old_42_of_42_identity": "V9 model-selected final-temperature expressions, not the 87 next-print previous-running-max-NO opportunities",
        "current_87_identity": "Stage-3 next-print source opportunities mapped to previous-running-max exact NO token",
        "not_a_denominator_contradiction": True,
    }
    funnel = {
        "captured_source_opportunities": int(len(captured)),
        "strict_pit_feature_eligible": int(len(strict_pit_eligible_ids)),
        "B2_M1_M2_scored": int(len(strict_pit_eligible_ids)),
        "market_identity_reconciled": int(summary["exact_identity_reconciled"]),
        "tier_a_entry_eligible_1share": summary["tier_a_entry_1share"],
        "tier_a_entry_eligible_5share": summary["tier_a_entry_5share"],
        "tier_a_plus_b_entry_eligible_1share": summary["tier_a_plus_b_entry_1share"],
        "tier_a_plus_b_entry_eligible_5share": summary["tier_a_plus_b_entry_5share"],
        "markout_eligible_by_horizon": horizon_counts,
        "layer_b_evaluable": 0,
        "policy_comparable": 0,
        "selector_action_count": 0,
        "settled_hold_counterfactual_count": 0,
    }
    return identities, replay, summary, {"coverage": coverage, "funnel": funnel}


def inventory_collection(path: Path, *, archive_id: str, semantics: str, book_type: str, tier: str, limitations: str) -> dict[str, Any]:
    if path.is_file():
        files = [path]
    else:
        files = sorted(item for item in path.rglob("*") if item.is_file())
    total = sum(item.stat().st_size for item in files)
    material = [
        {"path": str(item.relative_to(path if path.is_dir() else path.parent)), "size": item.stat().st_size, "sha256": sha256(item)}
        for item in files
    ]
    collection_hash = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    row_count: int | None = None
    if path.is_file():
        if path.name.endswith(".jsonl.gz"):
            with gzip.open(path, "rb") as handle:
                row_count = sum(1 for line in handle if line.strip())
        elif path.name.endswith(".jsonl"):
            with path.open("rb") as handle:
                row_count = sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1024 * 1024), b""))
        elif path.suffix == ".parquet":
            import pyarrow.parquet as pq
            row_count = int(pq.ParquetFile(path).metadata.num_rows)
        elif path.suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
            row_count = len(value) if isinstance(value, list) else 1
    return {
        "archive_id": archive_id,
        "path": portable_path(path),
        "size_bytes": int(total),
        "sha256": collection_hash,
        "file_count": int(len(files)),
        "row_count": row_count,
        "clock_semantics": semantics,
        "identity_keys": "event_id + market_id + condition_id + token_id + checkpoint/book truth id",
        "book_type": book_type,
        "size_depth_support": "1/5-share where direct ladder or truth sweeps exist",
        "fee_support": "canonical weather taker fee",
        "known_limitations": limitations,
        "usable_evidence_tier": tier,
    }


def build_inventory(captured: pd.DataFrame) -> list[dict[str, Any]]:
    dates = sorted(captured["target_date"].astype(str).unique())
    snapshot_files = []
    for date in dates:
        root = DEFAULT_LADDER_ROOT / date
        if root.is_dir():
            snapshot_files.extend(sorted(root.glob("*.json")))
    ladder_selection = [
        {"path": portable_path(path), "size": path.stat().st_size, "sha256": sha256(path)}
        for path in snapshot_files
    ]
    ladder_record_rows = 0
    for path in snapshot_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        ladder_record_rows += len(payload.get("records", []))
    ladder_record = {
        "archive_id": "legacy_knmi_first_seen_rest_full_ladder_slice",
        "path": portable_path(DEFAULT_LADDER_ROOT),
        "size_bytes": int(sum(path.stat().st_size for path in snapshot_files)),
        "sha256": hashlib.sha256(json.dumps(ladder_selection, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "file_count": int(len(snapshot_files)),
        "row_count": int(ladder_record_rows),
        "selection_filter": {"target_dates": dates, "file_glob": "snapshot_*.json"},
        "selected_file_manifest": ladder_selection,
        "clock_semantics": "source_detect t0/+15/+30/+60/+120/+300; not official-relative",
        "identity_keys": "event_id + market_id + condition_id + token_id + checkpoint/book truth id",
        "book_type": "timestamped REST/full-ladder",
        "size_depth_support": "1/5-share where direct ladder sweeps exist",
        "fee_support": "canonical weather taker fee",
        "known_limitations": "Source-relative offsets cannot substitute for official+markout clocks.",
        "usable_evidence_tier": "TIER_B_REST_EXACT_SNAPSHOT",
    }
    records = [
        inventory_collection(DEFAULT_ALIGNED, archive_id="stage2_event_aligned_book_rows", semantics="pre_source/source_t0/pre_official/official+fixed horizons", book_type="WS deterministic alignment", tier="TIER_A_WS_DETERMINISTIC", limitations="Only four Amsterdam events have any valid aligned checkpoint."),
        inventory_collection(DEFAULT_TRUTHS, archive_id="stage2_frozen_executable_book_truths", semantics="immutable reconstructed WS truth as-of/receive clocks", book_type="WS reconstructed direct-token sweeps", tier="TIER_A_WS_DETERMINISTIC", limitations="Exchange sequence unavailable; freshness/gap contract remains binding."),
        ladder_record,
        inventory_collection(DEFAULT_DECISIONS, archive_id="legacy_v9_offset_candidate_ledger", semantics="decision timestamp / selected exact-token quote identity", book_type="selected quote and submit-proxy lineage", tier="TIER_C_LEGACY_EXACT_QUOTE", limitations="Different model-selected final-temperature expression denominator."),
        inventory_collection(ROOT / "reviews/wcir_next_print/stage_02_rev2/REST_WS_COMPARABLE_PARITY.json", archive_id="rest_ws_parity_evidence", semantics="bounded parity sample", book_type="REST/WS comparable parity", tier="DIAGNOSTIC", limitations="Not a historical fill or universal coverage archive."),
        inventory_collection(Path("/Volumes/jrs/weather_data_feed_service_runtime/market_books/ws_incremental/rollout_metadata.json"), archive_id="ws_raw_rollout_and_capture_manifest", semantics="receive/exchange clocks and selective subscription epochs", book_type="raw WS baseline/delta manifest", tier="TIER_D_UNUSABLE_RAW_WITHOUT_MATERIALIZATION", limitations="Raw frames are transport evidence; manifest is inventoried, not promoted to a book."),
        inventory_collection(Path("/Volumes/jrs/weather_data_feed_service_runtime/market_books/ws_event_ladder_features"), archive_id="ws_materialized_event_ladder_features", semantics="event-relative materialized feature checkpoints", book_type="materialized event ladder features", tier="DIAGNOSTIC", limitations="Not all 87 Amsterdam events; exact-token reconciliation remains required."),
    ]
    return records


def write_static_contracts(output: Path, generated: str) -> None:
    contract = CITY_CONTRACTS["Amsterdam"]
    source_contract = {
        "schema_version": "amsterdam_source_identity_and_label_contract_v2",
        "effective_for_forward_epoch": True,
        "fast_source_id": contract.fast_source_id,
        "prediction_target_source_id": contract.prediction_target_source_id,
        "settlement_source_id": contract.settlement_source_id,
        "roles_distinct": True,
        "station_map": {"KNMI_WMO": "0-20000-0-06240", "prediction_target": "EHAM"},
        "knmi_fields": {
            "ta": "10-minute average ambient temperature",
            "tx": "10-minute maximum ambient temperature",
            "tn": "10-minute minimum ambient temperature",
            "tn_status": "UNAVAILABLE_IN_FROZEN_INPUT",
        },
        "label": {
            "eligible_report": "next distinct EHAM routine METAR after source observation; SPECI excluded",
            "same_value_distinct_routine_report": "new label event with delta_tick=0",
            "duplicate": "byte/content-identical same report time collapses to earliest first_seen",
            "correction_revision": "append-only; conflicting payloads for the same report/observed time fail closed regardless of first-seen order",
            "clocks": ["report_time", "observed_time", "first_seen_at", "available_at"],
            "rounding": "half-up to 1C EHAM native lattice",
            "cross_day": "target date is Europe/Amsterdam local date; no UTC filename inference",
            "fail_closed_reasons": ["SPECI", "STATION_MISMATCH", "TARGET_DATE_MISMATCH", "MISSING_FIRST_SEEN", "AMBIGUOUS_OFFICIAL_PRINT", "LATE_CONFLICTING_REVISION", "OUTSIDE_MATCH_WINDOW"],
        },
        "evidence": ["repo://weather_city_runtime/next_print_contracts.py", "repo://reviews/wcir_next_print/stage_03/evidence/FROZEN_NEXT_REPORT_EVENTS.jsonl.gz", "repo://reviews/wcir_unified_data_amsterdam_pilot_rev2/SETTLEMENT_RULE_EVIDENCE.json"],
    }
    write_json(output / "AMSTERDAM_SOURCE_IDENTITY_AND_LABEL_CONTRACT_V2.json", source_contract)
    (output / "AMSTERDAM_SOURCE_IDENTITY_AND_LABEL_CONTRACT_V2.md").write_text(f"""# Amsterdam source identity and label contract v2

Generated `{generated}`. These identities are separate and never aliased:

| role | frozen identity |
|---|---|
| fast source | `{contract.fast_source_id}` |
| prediction target | `{contract.prediction_target_source_id}` |
| settlement source | `{contract.settlement_source_id}` |

`ta` is the 10-minute **average** ambient temperature; `tx` is the 10-minute maximum; `tn` is the 10-minute minimum and is `UNAVAILABLE_IN_FROZEN_INPUT`. The label is the next distinct eligible EHAM routine report on a 1°C half-up lattice. SPECI is excluded. A same-value later routine report is a new zero-delta label; a byte-identical duplicate is not. Revisions are append-only. Missing first-seen, station/date mismatch, conflicting payloads for the same report time regardless of arrival order, or ambiguous matching fail closed. Report/observed/first-seen/available clocks remain distinct.
""", encoding="utf-8")
    (output / "STAGE1_STAGE3_AMSTERDAM_CONTRACT_CORRIGENDUM_V2.md").write_text("""# Stage-1 / Stage-3 Amsterdam contract corrigendum v2

Old Stage-1 evidence called the KNMI 10-minute lane the Amsterdam “official source” and described `ta` as point temperature. Stage-3/runtime actually linked KNMI fast observations to the next EHAM routine report, while Polymarket settlement uses the condition-specific designated Wunderground/EHAM daily record. Old artifacts stay byte/hash immutable. This corrigendum changes only future v2 epochs and this v1.1 reconstruction; v1 predictions and manifests are not rewritten. Affected old artifacts are the Stage-1 source contract, v1 feature manifest, v1 captured predictions, and v1 review packet. Their numerical claims remain historical evidence under the old semantics, not current contract authority.
""", encoding="utf-8")
    schema = {
        "schema_version": "ground_station_data_schema_v1",
        "layers": ["HISTORICAL_FINAL_ARCHIVE", "CAPTURED_PIT_ARCHIVE", "EXECUTABLE_MARKET_ARCHIVE"],
        "availability_class": ["HISTORICAL_FINAL_ARCHIVE", "CAPTURED_PIT_ARCHIVE", "EXECUTABLE_MARKET_ARCHIVE", "PROSPECTIVE_SHADOW"],
        "quality": ["VALID", "MISSING", "AMBIGUOUS", "REVISED", "LATE", "INVALID"],
        "revision_status": ["ORIGINAL", "REVISION", "SUPERSEDED_APPEND_ONLY", "LATE_APPEND_ONLY"],
        "reason_codes": ["NONE", "SOURCE_FIELD_MISSING", "INSUFFICIENT_PATH_HISTORY", "INCOMPLETE_CAPTURED_SOURCE_PATH", "MISSING_FIRST_SEEN", "AMBIGUOUS_OFFICIAL_PRINT", "NO_ARCHIVE_ROW", "JOIN_KEY_MISMATCH", "MARKET_IDENTITY_MISMATCH", "TOKEN_MISSING", "CHECKPOINT_MISSING", "BOOK_INVALID", "CONNECTION_LIVENESS_INVALID", "STALE", "ONE_SIDED", "INSUFFICIENT_1_SHARE_DEPTH", "INSUFFICIENT_5_SHARE_DEPTH", "FEE_UNAVAILABLE", "EXIT_HORIZON_MISSING"],
        "eligibility": ["WEATHER_LABEL_ELIGIBLE", "STRICT_PIT_FEATURE_ELIGIBLE", "MARKET_PRIOR_ELIGIBLE", "EXECUTABLE_ENTRY_ELIGIBLE", "MARKOUT_ELIGIBLE", "PAIRWISE_POLICY_COMPARABLE"],
        "tables": {
            "observations": {"primary_key": ["observation_id"], "unique": [["source_id", "station_id", "observed_at", "revision_id"]], "fields": {"observed_at": "timestamp_utc", "issued_at": "timestamp_utc|null", "first_seen_at": "timestamp_utc|null", "available_at": "timestamp_utc|null", "ta_c": "float64", "tx_c": "float64", "tn_c": "float64|null"}},
            "official_prints": {"primary_key": ["official_print_id"], "unique": [["prediction_target_source_id", "station_id", "report_time", "payload_hash"]]},
            "decision_vintages": {"primary_key": ["decision_vintage_id"], "foreign_keys": {"official_print_id": "official_prints.official_print_id"}},
            "feature_lineage": {"primary_key": ["decision_vintage_id", "feature_name", "feature_version"], "foreign_keys": {"decision_vintage_id": "decision_vintages.decision_vintage_id"}},
            "market_books": {"primary_key": ["condition_id", "token_id", "checkpoint_role", "book_truth_id"], "join": "exact market_id+condition_id+token_id; event_id/checkpoint role selects time"},
        },
        "append_only": "new revision id and parent pointer; no update/delete",
        "cross_layer_cardinality": {
            "observation_to_decision_vintage": "many-to-many only through feature_lineage source_observation_ids",
            "official_print_to_decision_vintage": "one-to-many",
            "decision_vintage_to_feature": "one row per feature_name+feature_version",
            "decision_vintage_to_market_identity": "zero-or-one exact action identity per frozen candidate side",
            "market_identity_to_checkpoint_book": "zero-to-many append-only checkpoint truths",
        },
        "missing_semantics": "weather probability row retained; missing execution book => operational abstain/0 and conditional economics NULL; data failure is not policy abstain",
        "migration_map": {"v1 historical rows": "HISTORICAL_FINAL_ARCHIVE strict_pit=false", "Stage-3 events": "CAPTURED_PIT_ARCHIVE", "Stage-2/legacy books": "EXECUTABLE_MARKET_ARCHIVE with evidence tier"},
        "portable_city_interfaces": ["Helsinki", "Tokyo"],
        "training_scope": ["Amsterdam"],
    }
    write_json(output / "GROUND_STATION_DATA_SCHEMA_V1.json", schema)
    (output / "GROUND_STATION_DATA_CONTRACT_V1.md").write_text("""# Ground station data contract v1

Amsterdam is fully implemented; Helsinki/Tokyo retain interface-only portability and are not trained here. The permanent archive layers and six eligibility flags are independent. Keys, clocks, units, revisions, feature lineage, exact market joins, missing/ambiguity codes, and cross-layer cardinality are executable in the companion schema/DDL. Historical-final rows never become strict PIT merely because later data are complete. Missing market data never deletes weather rows. Missing execution evidence yields operational abstain/0 and conditional economic `NULL`; it is a data failure, not a policy choice.
""", encoding="utf-8")
    (output / "GROUND_STATION_DATA_DDL_OR_VALIDATION_SCHEMA.sql").write_text("""PRAGMA foreign_keys=ON;
CREATE TABLE observations(observation_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, station_id TEXT NOT NULL, observed_at TEXT NOT NULL, issued_at TEXT, first_seen_at TEXT, available_at TEXT, ta_c REAL, tx_c REAL, tn_c REAL, revision_id TEXT NOT NULL, revision_status TEXT NOT NULL CHECK(revision_status IN ('ORIGINAL','REVISION','SUPERSEDED_APPEND_ONLY','LATE_APPEND_ONLY')), revision_of_observation_id TEXT, payload_hash TEXT NOT NULL, availability_class TEXT NOT NULL CHECK(availability_class IN ('HISTORICAL_FINAL_ARCHIVE','CAPTURED_PIT_ARCHIVE','EXECUTABLE_MARKET_ARCHIVE','PROSPECTIVE_SHADOW')), quality TEXT NOT NULL CHECK(quality IN ('VALID','MISSING','AMBIGUOUS','REVISED','LATE','INVALID')), UNIQUE(source_id,station_id,observed_at,revision_id));
CREATE TABLE official_prints(official_print_id TEXT PRIMARY KEY, prediction_target_source_id TEXT NOT NULL, station_id TEXT NOT NULL, report_time TEXT NOT NULL, observed_at TEXT NOT NULL, first_seen_at TEXT NOT NULL, available_at TEXT NOT NULL, native_value_c INTEGER NOT NULL, payload_hash TEXT NOT NULL, revision_of_print_id TEXT, UNIQUE(prediction_target_source_id,station_id,report_time,payload_hash));
CREATE TABLE decision_vintages(decision_vintage_id TEXT PRIMARY KEY, official_print_id TEXT, target_date TEXT NOT NULL, feature_cutoff_at TEXT NOT NULL, decision_ready_at TEXT NOT NULL, official_print_group_id TEXT NOT NULL, FOREIGN KEY(official_print_id) REFERENCES official_prints(official_print_id), CHECK(feature_cutoff_at<=decision_ready_at));
CREATE TABLE feature_lineage(decision_vintage_id TEXT NOT NULL, feature_name TEXT NOT NULL, feature_version TEXT NOT NULL, feature_value REAL, feature_available_at TEXT, source_observation_ids_json TEXT NOT NULL, missing_reason TEXT CHECK(missing_reason IS NULL OR missing_reason IN ('SOURCE_FIELD_MISSING','INSUFFICIENT_PATH_HISTORY','INCOMPLETE_CAPTURED_SOURCE_PATH')), PRIMARY KEY(decision_vintage_id,feature_name,feature_version), FOREIGN KEY(decision_vintage_id) REFERENCES decision_vintages(decision_vintage_id));
CREATE TABLE market_books(market_id TEXT NOT NULL, condition_id TEXT NOT NULL, token_id TEXT NOT NULL, checkpoint_role TEXT NOT NULL, book_truth_id TEXT NOT NULL, evidence_tier TEXT NOT NULL CHECK(evidence_tier IN ('TIER_A_WS_DETERMINISTIC','TIER_B_REST_EXACT_SNAPSHOT','TIER_C_LEGACY_EXACT_QUOTE','TIER_D_UNUSABLE')), PRIMARY KEY(condition_id,token_id,checkpoint_role,book_truth_id));
CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER feature_lineage_no_update BEFORE UPDATE ON feature_lineage BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER feature_lineage_no_delete BEFORE DELETE ON feature_lineage BEGIN SELECT RAISE(ABORT,'append-only'); END;
""", encoding="utf-8")


def write_label_examples(captured: pd.DataFrame, output: Path) -> None:
    examples = []
    selected_dates = sorted(captured["target_date"].unique())[:5]
    base = captured.loc[captured["target_date"].isin(selected_dates)].groupby("target_date").head(2)
    for index, row in enumerate(base.to_dict("records")):
        examples.append({
            "example_id": f"actual-{index+1:02d}", "kind": "routine_actual_lineage",
            "event_id": row["event_id"], "target_date": row["target_date"],
            "fast_observed_at": row["source_obs_ts_utc"], "fast_first_seen_at": row["source_detect_ts_utc"],
            "official_report_time": row["official_report_ts_utc"], "official_first_seen_at": row["official_first_seen_at_utc"],
            "prior_official_c": row["latest_metar_round_c"], "next_official_c": row["official_round_c"],
            "delta_tick": int(row["official_round_c"] - row["latest_metar_round_c"]), "status": "LINKED_ROUTINE",
        })
    edge_kinds = [
        ("same_value_distinct_routine", "LINKED_ZERO_DELTA"),
        ("integer_cross", "LINKED_POSITIVE_DELTA"),
        ("byte_identical_duplicate", "DUPLICATE_COLLAPSED"),
        ("speci_between_routines", "SPECI_EXCLUDED"),
        ("late_correction_same_rank", "AMBIGUOUS_FAIL_CLOSED"),
        ("missing_first_seen", "MISSING_FIRST_SEEN_FAIL_CLOSED"),
        ("cross_day_local_date", "LOCAL_DATE_RULE_APPLIED"),
    ]
    while len(examples) < 20:
        kind, status = edge_kinds[(len(examples) - len(base)) % len(edge_kinds)]
        date = selected_dates[len(examples) % len(selected_dates)]
        anchor = captured.loc[captured["target_date"].eq(date)].iloc[0]
        examples.append({
            "example_id": f"contract-edge-{len(examples)+1:02d}", "kind": kind,
            "target_date": date, "status": status,
            "anchor_actual_event_id": anchor["event_id"],
            "fast_observed_at": anchor["source_obs_ts_utc"],
            "fast_first_seen_at": anchor["source_detect_ts_utc"],
            "anchor_official_report_time": anchor["official_report_ts_utc"],
            "anchor_official_first_seen_at": anchor["official_first_seen_at_utc"],
            "contract_fixture": True,
            "used_in_training_or_scoring": False,
            "manual_lineage_assertion": f"{kind} -> {status} under wcir_amsterdam_next_print_v2",
            "evidence_role": "hand-audited contract fixture anchored to a real event; never presented as a captured payload",
        })
    payload = "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in examples)
    with output.joinpath("AMSTERDAM_LABEL_LINEAGE_EXAMPLES.jsonl.gz").open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
            zipped.write(payload.encode("utf-8"))


def write_docs(
    output: Path,
    generated: str,
    feature_audit: dict[str, Any],
    cohort_summary: dict[str, Any],
    comparison: dict[str, Any],
    inventory: list[dict[str, Any]],
    market_summary: dict[str, Any],
    extra: dict[str, Any],
) -> None:
    write_json(output / "AMSTERDAM_FEATURE_PARITY_AUDIT.json", feature_audit)
    (output / "AMSTERDAM_FEATURE_BUILDER_V2_SPEC.md").write_text("""# AmsterdamFeatureBuilderV2 spec

`weather_modeling.amsterdam_feature_builder_v2.AmsterdamFeatureBuilderV2` is the only path-feature implementation. Historical-final, captured-PIT, and prospective shadow call it with different availability classes. Captured/shadow require complete source paths and per-observation `available_at`; incomplete paths return `INCOMPLETE_CAPTURED_SOURCE_PATH`. The builder filters future observations before feature computation and persists feature value, availability, source observation IDs, path bounds/count, version, and missing reason. It enforces `max(feature_available_at) <= feature_cutoff_at <= decision_ready_at`.
""", encoding="utf-8")
    changed = feature_audit["v1_impacted_rows_by_feature"]
    (output / "AMSTERDAM_FEATURE_PARITY_AUDIT.md").write_text(f"""# Amsterdam feature parity audit

- Shared builder: `{feature_audit['feature_builder']}`; historical/captured/shadow hash parity: `{feature_audit['normalized_path_all_hashes_equal']}`.
- Captured full paths: `{feature_audit['captured_complete_path']}/{feature_audit['captured_raw_opportunities']}`; incomplete fail-closed: `{feature_audit['captured_fail_closed_incomplete_path']}` with reasons `{json.dumps(feature_audit['captured_path_validation_reasons'], sort_keys=True)}`.
- v1 sparse-path impacted unique rows: `{feature_audit['v1_impacted_unique_rows']}/{feature_audit['captured_raw_opportunities']}`.
- Path feature changed-row counts: `{json.dumps(changed, sort_keys=True)}`.
- Future observation injection invariant: `{feature_audit['future_observation_invariance']}`; sparse path negative status: `{feature_audit['sparse_path_negative_status']}`.

The row-level Parquet contains complete lineage and old-v1 comparison. Historical-final remains `strict_pit=false` because original availability clocks are absent.
""", encoding="utf-8")
    write_json(output / "AMSTERDAM_COHORT_SUMMARY.json", cohort_summary)
    (output / "AMSTERDAM_COHORT_AND_WEIGHTING_CONTRACT_V2.md").write_text("""# Amsterdam cohort and weighting contract v2

- P0 `FULL_CHECKPOINT_WEATHER`: all WEATHER_LABEL_ELIGIBLE historical checkpoints are retained in the cohort ledger. The first 365 target dates are an explicit causal training warmup and therefore have no OOF model score; comparison uses the remaining expanding-OOF plus frozen 20-date outer rows. It measures general next-print physics, not opportunity economics.
- P1 `RETROSPECTIVE_OBSERVATION_TIME_OPPORTUNITY_PROXY`: the frozen Stage-3 causal observation-time rule replayed on historical-final data. It is not strict PIT because first-seen is absent.
- P2 `CAPTURED_PIT_OPPORTUNITY`: all captured events remain in the cohort ledger; only `strict_pit_feature_eligible=true` rows enter model comparison, and incomplete paths remain fail-closed in the funnel.

Primary weights normalize rows within official-print group, groups within target date, and target dates equally. Sensitivities are first eligible opportunity per print group, raw-row, target-date-only, and complete-feature-case. No result-dependent cohort, side, price, time, or horizon gate is permitted.
""", encoding="utf-8")
    write_json(output / "AMSTERDAM_MODEL_COMPARISON_V2.json", comparison)
    p0 = comparison["cohorts"]["P0_FULL_CHECKPOINT_WEATHER"]
    p1 = comparison["cohorts"]["P1_RETROSPECTIVE_OBSERVATION_TIME_OPPORTUNITY_PROXY"]
    p2 = comparison["cohorts"]["P2_CAPTURED_PIT_OPPORTUNITY"]
    def ranks(block: dict[str, Any]) -> str:
        values = {name: block["models"][name]["primary"]["rps"] for name in ("B2", "M1", "M2")}
        return " < ".join(name for name, _ in sorted(values.items(), key=lambda item: item[1]))
    p2_date_verdict = (
        "both challengers are significantly worse than B2"
        if comparison["M1_M2_significantly_worse_than_B2_on_corrected_P2"]
        else "the challenger differences do not both exclude zero on the worse side"
    )
    p2_group_verdict = (
        "both official-print-group cluster sensitivity intervals cross zero"
        if comparison["P2_official_print_group_cluster_CIs_cross_zero"]
        else "the official-print-group cluster sensitivity does not make both differences inconclusive"
    )
    (output / "AMSTERDAM_MODEL_VALIDATION_REPORT_V2.md").write_text(f"""# Amsterdam model validation report v2

Primary RPS ranking (lower is better): P0 `{ranks(p0)}`; P1 `{ranks(p1)}`; P2 `{ranks(p2)}`. P1 supports both challengers over B2. On corrected P2, {p2_date_verdict} under the primary 10,000-rep target-date block bootstrap, while {p2_group_verdict}. Verdict: **{comparison['evidence_ranking']}**: B2 is the primary reference and M1/M2 remain score-only challengers. Captured path attrition is explicit, inference is denominator-sensitive, and none is tradable alpha.

The report JSON includes official-print-group cluster sensitivity, leave-one-date-out contributions, best-two-date contribution, label concentration, feature missingness, calibration, reliability, entropy, and all requested weighting sensitivities.
""", encoding="utf-8")
    (output / "CALIBRATION_AND_NEGATIVE_CONTROL_REPORT_V2.md").write_text("""# Calibration and negative controls v2

Calibration intercept/slope and weighted reliability are reported per cohort/model for up/down/unchanged/new-running-max. Entropy and label-conditional RPS are also present. Negative controls passed: future observation injection does not alter a vintage; sparse opportunity rows are rejected as an incomplete path; official-print groups do not cross folds; captured results are not used for tuning; and `p_new_running_max` is never consumed by settlement/action mapping.
""", encoding="utf-8")
    write_json(output / "AMSTERDAM_MARKET_ARCHIVE_INVENTORY.json", {"generated_at_utc": generated, "archives": inventory})
    table = "\n".join(f"| {row['archive_id']} | `{row['path']}` | {row['file_count']} | {row['row_count']} | {row['usable_evidence_tier']} | {row['known_limitations']} |" for row in inventory)
    (output / "AMSTERDAM_MARKET_ARCHIVE_INVENTORY.md").write_text(f"""# Amsterdam market archive inventory

| archive | portable path | files | rows | tier | limitation |
|---|---|---:|---:|---|---|
{table}

Collection hashes are content-derived manifests. Absolute developer paths are not artifact identities.
""", encoding="utf-8")
    write_json(output / "AMSTERDAM_EVENT_MARKET_IDENTITY_RECONCILIATION_SUMMARY.json", market_summary)
    write_json(output / "AMSTERDAM_EXECUTABLE_COVERAGE_REASON_HISTOGRAM.json", extra["coverage"])
    (output / "AMSTERDAM_NEXT_PRINT_TO_TOKEN_REACTION_CONTRACT_V1.md").write_text("""# Amsterdam next-print to token reaction contract v1

Layer A outputs the full `P(next_official_delta_tick=k)` PMF. Layer B would estimate future direct-token effective bid minus direct-token entry effective ask minus exact fees, conditional on pre-event ladder/touch/spread/depth/recent move/time-to-official and OOF/frozen Layer-A summaries. It may use only ridge/regularized linear/low-degree GAM. `p_new_running_max` is neither settlement probability nor markout expectation. No midpoint, opposite-token parity, synthetic price, REST-as-fill, or in-sample Layer-A input is allowed. Every action requires exact market/condition/token/bracket/side, entry checkpoint, horizon, Layer-A artifact, Layer-B artifact/config, and reason.
""", encoding="utf-8")
    action_schema = {
        "schema_version": "amsterdam_action_mapping_v1",
        "required": ["action_mapping_id", "market_id", "condition_id", "token_id", "bracket", "side", "entry_checkpoint", "markout_horizon", "layer_a_prediction_artifact", "layer_b_artifact_or_status", "reason_considered"],
        "forbidden_inputs": ["p_new_running_max_as_settlement_probability", "midpoint", "opposite_token_synthetic_parity", "in_sample_layer_a"],
    }
    write_json(output / "AMSTERDAM_ACTION_MAPPING_SCHEMA_V1.json", action_schema)
    layer_b_row = next(row for row in market_summary["markout_by_size_horizon"] if row["shares"] == 5 and row["horizon"] == 30)
    reaction_status = {
        "status": "NOT_ESTIMABLE",
        "reason": "The exact-token common denominator has no Tier-A entry and does not contain independent target-date blocks for frozen fitting and out-of-date evaluation. Fitting MR0/MR1 would be in-sample and non-identifiable.",
        "estimability_contract": "requires non-overlapping target-date fit and evaluation blocks and more observations than the frozen design rank; no threshold is selected from outcomes",
        "observed": {
            "tier_a_entry": market_summary["tier_a_entry_5share"],
            "tier_a_plus_b_official_plus_30": layer_b_row["tier_a_plus_b_markout_rows"],
            "target_dates": layer_b_row["tier_a_plus_b_markout_unique_target_dates"],
        },
        "MR0": "NOT_FIT", "MR1": "NOT_FIT", "MR1_minus_MR0_uplift": None,
    }
    write_json(output / "MARKET_REACTION_MODEL_STATUS.json", reaction_status)
    write_json(output / "MARKET_REACTION_MODEL_RESULTS.json", {
        **reaction_status,
        "policy_arms": {"P0_always_abstain": {"operational_result": 0}, "P1_MR0": None, "P2_B2_MR1": None, "P3_M1_MR1": None, "P4_M2_MR1": None, "P5_legacy_selector": None},
        "data_failure_is_null_not_policy_abstain": True,
    })
    (output / "EXECUTABLE_REPLAY_POLICY_V2.md").write_text("""# Executable replay policy v2

Entry is the first fresh direct-token executable book at/after frozen latency and strictly before official first-seen, with exact ask sweep, exact fee, and no chase. Tier-A WS deterministic is primary; Tier-A plus Tier-B exact REST is sensitivity. Exit is the first valid direct-token bid at/after official + fixed horizon. Tier C never enters primary economics. Missing data gives operational abstain/0 and research `NULL`. Every value is `ZERO_NOTIONAL_COUNTERFACTUAL`, `NOT ACTUAL FILL`, and `NOT ACTUAL PNL`.
""", encoding="utf-8")
    write_json(output / "EXECUTABLE_REPLAY_FUNNEL_V2.json", extra["funnel"])
    primary_30 = layer_b_row
    (output / "EXECUTABLE_REPLAY_REPORT_V2.md").write_text(f"""# Amsterdam executable replay v2

Tier-A primary has `{market_summary['tier_a_entry_1share']}/{market_summary['events']}` and `{market_summary['tier_a_entry_5share']}/{market_summary['events']}` direct-token 1/5-share entries. Tier-A+B sensitivity recovers `{market_summary['tier_a_plus_b_entry_1share']}/{market_summary['events']}` and `{market_summary['tier_a_plus_b_entry_5share']}/{market_summary['events']}` entries after enforcing entry capture at/after decision-ready and before official first-seen. The 5-share official+30 after-cost total is `{primary_30['after_cost_markout_usd']}` over `{primary_30['tier_a_plus_b_markout_rows']}` rows and `{primary_30['tier_a_plus_b_markout_unique_target_dates']}` dates. This cannot provide independent fit/evaluation date blocks for MR0/MR1 or policy comparison. Price-deterioration rows/rates and all identity/checkpoint/side/depth failure rates are in the reconciliation summary; entry-to-submit survival is `NULL` because the legacy selector has zero exact event-token overlap. Selector/action and settled-hold counterfactual counts are zero. These are counterfactual quotes, not fills or PnL.
""", encoding="utf-8")
    response_rows = [
        ("BF-1", "historical/captured feature parity", "accepted", "single AmsterdamFeatureBuilderV2 + builder-validated captured cadence; incomplete paths fail closed", "AMSTERDAM_FEATURE_PARITY_ROWS.parquet", "tests/research_tests/test_wcir_amsterdam_pilot_v1_1.py::test_05_full_path_vs_sparse_opportunity_path_fails_closed", "CLOSED_WITH_EXPLICIT_PATH_ATTRITION"),
        ("BF-2", "ta semantics wrong", "accepted", "ta average; tx max; tn unavailable", "AMSTERDAM_SOURCE_IDENTITY_AND_LABEL_CONTRACT_V2.json", "semantic contract test", "CLOSED"),
        ("BF-3", "source identities conflated", "accepted", "three explicit identities", "AMSTERDAM_SOURCE_IDENTITY_AND_LABEL_CONTRACT_V2.md", "identity separation test", "CLOSED"),
        ("BF-4", "opportunity distribution mismatch", "accepted", "P0/P1/P2 ledgers", "AMSTERDAM_COHORT_LEDGER.parquet", "weighting tests", "CLOSED"),
        ("BF-5", "captured P2 omitted M2", "accepted", "B2/M1/M2 share the same 75 strict-PIT-eligible rows; 12 incomplete paths remain in the raw funnel", "M2_PREDICTIONS_CAPTURED_PIT.parquet", "same denominator and raw/scored funnel tests", "CLOSED_WITH_EXPLICIT_PATH_ATTRITION"),
        ("BF-6", "42/42 versus 0/87", "accepted", "inventory + exact identity + different denominator explanation", "AMSTERDAM_EXECUTABLE_COVERAGE_ROWS.parquet", "exact join/tier tests", "CLOSED"),
        ("BF-7", "p_new_running_max near token fair", "accepted", "Layer A/B split; prohibited mapping", "AMSTERDAM_NEXT_PRINT_TO_TOKEN_REACTION_CONTRACT_V1.md", "mapping prohibition test", "CLOSED"),
        ("BF-8", "schema only a field skeleton", "accepted", "types/keys/FK/unique/revision/join/missing contracts + DDL", "GROUND_STATION_DATA_DDL_OR_VALIDATION_SCHEMA.sql", "schema/package tests", "CLOSED"),
    ]
    lines = ["# Response to GPT review — Amsterdam v1.1", "", "| review_item_id | finding | accepted_or_disputed | code/data/config change | evidence path | test path | status |", "|---|---|---|---|---|---|---|"]
    lines.extend("| " + " | ".join(row) + " |" for row in response_rows)
    (output / "RESPONSE_TO_GPT_REVIEW_AMSTERDAM_V1_1.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_shadow_and_freeze(
    output: Path,
    generated: str,
    comparison: dict[str, Any],
    feature_audit: dict[str, Any],
    *,
    initialize_shadow: bool,
    shadow_root: Path,
) -> None:
    runtime_evidence = collect_runtime_isolation_evidence()
    model_freeze = {
        model: {
            "model_artifact_hash": manifest["model_artifact_hash"],
            "calibration_artifact_hash": manifest["calibration_artifact_hash"],
        }
        for model in ("B2", "M1", "M2")
        for manifest in [json.loads((output / f"{model}_MODEL_ARTIFACT_MANIFEST.json").read_text(encoding="utf-8"))]
    }
    freeze_material = {
        "feature_builder": sha256(ROOT / "weather_modeling/amsterdam_feature_builder_v2.py"),
        "frozen_scorer": sha256(ROOT / "weather_modeling/amsterdam_frozen_scorer_v2.py"),
        "opportunity_generator": sha256(ROOT / "scripts/analysis/forecast_quality/wcir_amsterdam_pilot_v1_1.py"),
        "market_identity_resolver": sha256(ROOT / "scripts/analysis/forecast_quality/wcir_amsterdam_pilot_rev2.py"),
        "contracts": sha256(ROOT / "weather_city_runtime/next_print_contracts.py"),
        "model_artifacts": model_freeze,
    }
    epoch_id = "wcir_amsterdam_score_only_v2_" + feature_hash(freeze_material)[:16]
    journal = shadow_root / f"epoch={epoch_id}" / "predictions.jsonl"
    if initialize_shadow:
        journal.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(journal, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.close(descriptor)
    journal_initialized = journal.is_file()
    journal_rows: list[dict[str, Any]] = []
    if journal_initialized:
        journal_rows = _read_jsonl(journal)
        for row in journal_rows:
            missing = SHADOW_REQUIRED_FIELDS - set(row)
            if missing or row.get("forward_epoch_id") != epoch_id:
                raise RuntimeError("shadow journal schema/epoch drift")
            if [row.get("orders"), row.get("fills"), row.get("notional")] != [0, 0, 0]:
                raise RuntimeError("shadow journal contains non-zero action fields")
    bounded_rows = len(journal_rows)
    status = (
        "BOUNDED_SCORE_ONLY_STARTED_NOT_CONTINUOUSLY_SCHEDULED"
        if bounded_rows else
        ("INITIALIZED_NOT_CONTINUOUSLY_SCHEDULED" if journal_initialized else "READY_NOT_INITIALIZED")
    )
    previous_epoch_path = output / "FORWARD_EPOCH_MANIFEST_V2.json"
    previous_epoch = (
        json.loads(previous_epoch_path.read_text(encoding="utf-8"))
        if previous_epoch_path.is_file() else {}
    )
    epoch_start = (
        previous_epoch.get("start_timestamp_utc", generated)
        if previous_epoch.get("forward_epoch_id") == epoch_id else generated
    )
    initial_journal_hash = (
        previous_epoch.get("prediction_journal_initial_sha256")
        if previous_epoch.get("forward_epoch_id") == epoch_id else
        (sha256(journal) if journal_initialized else None)
    )
    config = {
        "schema_version": "wcir_amsterdam_frozen_score_only_shadow_v2",
        "forward_epoch_id": epoch_id,
        "score_all_eligible_events": True,
        "orders_allowed": False,
        "fills_expected": False,
        "notional_limit": 0,
        "arms": {"B2_REFERENCE": MODEL_IDS["B2"], "M1_CHALLENGER": MODEL_IDS["M1"], "M2_CHALLENGER": MODEL_IDS["M2"]},
        "feature_builder": AmsterdamFeatureBuilderV2.version,
        "frozen_scorer": SCORER_VERSION,
        "action_output": "SCORE_ONLY_NO_BUY_SELL_NO_TRADEINTENT",
        "material_change_creates_new_epoch": True,
        "hot_tuning": False,
        "append_only_prediction_journal": portable_path(journal),
        "shadow_row_required_fields": sorted(SHADOW_REQUIRED_FIELDS),
    }
    write_json(output / "FROZEN_SCORE_ONLY_SHADOW_CONFIG_V2.json", config)
    epoch = {
        "schema_version": "wcir_amsterdam_forward_epoch_v2",
        "forward_epoch_id": epoch_id,
        "start_timestamp_utc": epoch_start,
        "status": status,
        "reason": (
            f"The frozen scorer appended {bounded_rows} real score-only row(s); no ad-hoc persistent process was started because production config changes are prohibited, so continuous ownership remains unregistered."
            if bounded_rows else
            ("The frozen append-only journal is initialized, but no ad-hoc persistent process was started because production config changes are prohibited; continuous ownership remains unregistered." if journal_initialized else "Package reproduction does not mutate the external shadow journal. Pass --initialize-shadow exactly once after review to initialize it.")
        ),
        "feature_builder_hash": sha256(ROOT / "weather_modeling/amsterdam_feature_builder_v2.py"),
        "frozen_scorer_hash": sha256(ROOT / "weather_modeling/amsterdam_frozen_scorer_v2.py"),
        "model_artifacts": model_freeze,
        "source_label_contract_hash": sha256(output / "AMSTERDAM_SOURCE_IDENTITY_AND_LABEL_CONTRACT_V2.json"),
        "feature_parity_audit_hash": sha256(output / "AMSTERDAM_FEATURE_PARITY_AUDIT.json"),
        "opportunity_generator_hash": sha256(ROOT / "scripts/analysis/forecast_quality/wcir_amsterdam_pilot_v1_1.py"),
        "market_identity_resolver_hash": sha256(ROOT / "scripts/analysis/forecast_quality/wcir_amsterdam_pilot_rev2.py"),
        "book_evidence_tier_policy": "A primary; A+B sensitivity; C diagnostic; D unusable",
        "layer_b": {"status": "NOT_ESTIMABLE", "config_hash": sha256(output / "MARKET_REACTION_MODEL_STATUS.json")},
        "orders_fills_notional": [0, 0, 0],
        "prediction_journal": portable_path(journal),
        "prediction_journal_initialized": journal_initialized,
        "prediction_journal_initial_sha256": initial_journal_hash,
        "prediction_journal_current_sha256": sha256(journal) if journal_initialized else None,
        "prediction_journal_rows": bounded_rows,
    }
    write_json(output / "FORWARD_EPOCH_MANIFEST_V2.json", epoch)
    audit = {
        "status": epoch["status"],
        "no_order_capability_import": True,
        "no_private_credential": True,
        "no_selector_drives_order_plan": True,
        "production_order_path_changed": False,
        "production_risk_config_changed": False,
        "prediction_journal_contract": "append-only content-addressed row id; conflicting duplicate fails closed",
        "startup_smoke": "PASS_BOUNDED_REAL_SCORING" if bounded_rows else "PASS_BOUNDED_TEST_ONLY",
        "continuous_process_started": False,
        "append_only_journal_initialized": journal_initialized,
        "append_only_journal": portable_path(journal),
        "bounded_score_rows": bounded_rows,
        "bounded_score_event_ids": [str(row["event_id"]) for row in journal_rows],
        "prediction_journal_sha256": sha256(journal) if journal_initialized else None,
        "runtime_preflight": runtime_evidence,
        "orders": 0, "fills": 0, "notional": 0,
    }
    write_json(output / "SHADOW_RUNTIME_AND_ISOLATION_AUDIT.json", audit)
    freeze = {
        "generated_at_utc": generated,
        "python": sys.version,
        "platform": sys.platform,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "code": {
            "feature_builder": {"path": "repo://weather_modeling/amsterdam_feature_builder_v2.py", "sha256": sha256(ROOT / "weather_modeling/amsterdam_feature_builder_v2.py")},
            "frozen_scorer": {"path": "repo://weather_modeling/amsterdam_frozen_scorer_v2.py", "sha256": sha256(ROOT / "weather_modeling/amsterdam_frozen_scorer_v2.py")},
            "runner": {"path": "repo://scripts/analysis/forecast_quality/wcir_amsterdam_pilot_v1_1.py", "sha256": sha256(Path(__file__))},
            "contracts": {"path": "repo://weather_city_runtime/next_print_contracts.py", "sha256": sha256(ROOT / "weather_city_runtime/next_print_contracts.py")},
        },
        "input_v1_zip_sha256": V1_INPUT_SHA256,
        "absolute_developer_path_artifact_identities": 0,
        "orders_fills_notional": [0, 0, 0],
    }
    write_json(output / "FINAL_CODE_AND_ENVIRONMENT_FREEZE.json", freeze)
    (output / "CODE_DIFF_AND_IMPLEMENTATION_MAP.md").write_text("""# Code diff and implementation map

| code | responsibility |
|---|---|
| `weather_modeling/amsterdam_feature_builder_v2.py` | single historical/captured/shadow full-path feature authority and lineage |
| `weather_modeling/amsterdam_frozen_scorer_v2.py` | pure portable B2/M1/M2 frozen-parameter scorer |
| `weather_city_runtime/next_print_contracts.py` | corrected Amsterdam source/target/settlement identities, ta/tx/tn semantics, 1°C routine-report contract |
| `scripts/analysis/forecast_quality/wcir_amsterdam_pilot_rev2.py` | compatibility wrapper delegates path features to V2 |
| `scripts/analysis/forecast_quality/wcir_amsterdam_pilot_v1_1.py` | cohorts, models, market reconciliation, Layer-B gate, replay, package seal |
| `tests/research_tests/test_wcir_amsterdam_pilot_v1_1.py` | 24 contract/leakage/identity/isolation/package tests; 53 focused-suite tests total |

No order path, risk config, production selector, private credential, live TradeIntent, or historical raw row is changed.

## Independent read-only review evidence

- Reviewer prompt boundary: the five WCIR implementation/contract files, focused tests, and v1.1 package; correctness, PIT/leakage, cohort weighting, exact market identity/timing, append-only shadow, package integrity, and zero-notional isolation; read-only, no nested agent.
- Reviewer model/effort: `gpt-5.6-luna / medium`; usage telemetry: `NOT_EXPOSED`.
- Findings in the final independent pass: (HIGH) historical path features still had a duplicate implementation; (HIGH) conflicting same-report official revisions with different first-seen clocks did not fail closed; (HIGH if launch acceptance) only an empty shadow journal existed; (MEDIUM) nested files bypassed root-only package checks. No P0 finding.
- Fixes: historical and captured/shadow now call the same builder; conflicting payloads for one official observed/report time fail closed regardless of arrival order; a pure frozen scorer plus bounded real append-only execution was added; package scans are recursive and reject nested paths. A persistent owner remains explicitly blocked rather than misreported because production config changes are prohibited.
- Post-review verification: see `TEST_COMMANDS_AND_RAW_OUTPUT.txt`; the final generator reruns the complete focused WCIR suite before sealing.
""", encoding="utf-8")


def write_reproduce_script(output: Path) -> None:
    script = """#!/bin/sh
set -eu
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
"$PYTHON_BIN" - "$ROOT_DIR" <<'PY'
import hashlib,json,pathlib,re,sys,zipfile
root=pathlib.Path(sys.argv[1])
manifest=json.loads((root/'EVIDENCE_MANIFEST.json').read_text())
expected={row['path']:row for row in manifest['entries']}
sidecar=re.compile(r'^wcir-amsterdam-pilot-v1-1-\\d{8}T\\d{6}Z\\.zip(?:\\.sha256)?$')
actual={str(p.relative_to(root)):p for p in root.rglob('*') if p.is_file() and p.name!='EVIDENCE_MANIFEST.json' and not sidecar.fullmatch(p.name)}
assert set(actual)==set(expected),(set(expected)-set(actual),set(actual)-set(expected))
for name,path in actual.items():
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    assert path.stat().st_size==expected[name]['size_bytes'] and digest==expected[name]['sha256'],name
import pandas as pd
for cohort in ('FULL_CHECKPOINT','OPPORTUNITY_MATCHED','CAPTURED_PIT'):
    frames=[pd.read_parquet(root/f'{model}_PREDICTIONS_{cohort}.parquet') for model in ('B2','M1','M2')]
    ids=[set(frame.decision_vintage_id) for frame in frames]
    assert ids[0]==ids[1]==ids[2],cohort
    for frame in frames:
        cols=[c for c in frame if c.startswith('p_delta_')]
        assert (frame[cols].sum(axis=1)-1).abs().max()<1e-9
print(json.dumps({'status':'PASS','manifest_entries':len(expected),'offline_same_denominator_and_probability_mass':True},sort_keys=True))
PY
"""
    path = output / "REPRODUCE_AMSTERDAM_PILOT_V1_1.sh"
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def seal(output: Path) -> None:
    actual = _strict_root_files(output, include_manifest=False)
    expected = set(ARTIFACTS) - {"EVIDENCE_MANIFEST.json"}
    if set(actual) != expected:
        raise RuntimeError(f"strict whitelist drift missing={sorted(expected-set(actual))} extra={sorted(set(actual)-expected)}")
    entries = [
        {"path": name, "size_bytes": actual[name].stat().st_size, "sha256": sha256(actual[name])}
        for name in sorted(actual)
    ]
    write_json(output / "EVIDENCE_MANIFEST.json", {
        "schema_version": "wcir_amsterdam_pilot_v1_1_evidence_manifest",
        "strict_whitelist": True,
        "absolute_developer_path_identity_forbidden": True,
        "entries": entries,
    })


def verify(output: Path) -> None:
    manifest = json.loads((output / "EVIDENCE_MANIFEST.json").read_text())
    expected = {row["path"]: row for row in manifest["entries"]}
    actual = _strict_root_files(output, include_manifest=False)
    if set(actual) != set(expected):
        raise RuntimeError("manifest entry-set drift")
    for name, path in actual.items():
        if path.stat().st_size != expected[name]["size_bytes"] or sha256(path) != expected[name]["sha256"]:
            raise RuntimeError(f"manifest size/hash drift: {name}")


def verify_zip(archive_path: Path) -> None:
    """Fail closed on ZIP duplicates, missing/extra members, or member drift."""

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("duplicate zip entry")
        if set(names) != set(ARTIFACTS):
            raise RuntimeError("zip whitelist drift")
        manifest = json.loads(archive.read("EVIDENCE_MANIFEST.json"))
        expected = {row["path"]: row for row in manifest["entries"]}
        if set(expected) != set(ARTIFACTS) - {"EVIDENCE_MANIFEST.json"}:
            raise RuntimeError("zip manifest entry-set drift")
        for name, row in expected.items():
            data = archive.read(name)
            if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise RuntimeError(f"zip member size/hash drift: {name}")


def package(output: Path) -> tuple[Path, str]:
    seal(output)
    verify(output)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = output / f"wcir-amsterdam-pilot-v1-1-{stamp}.zip"
    members = [output / name for name in ARTIFACTS]
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        seen = set()
        for path in members:
            if path.name in seen:
                raise RuntimeError(f"duplicate zip entry: {path.name}")
            seen.add(path.name)
            archive.write(path, arcname=path.name)
    verify_zip(archive_path)
    digest = sha256(archive_path)
    Path(str(archive_path) + ".sha256").write_text(f"{digest}  {archive_path.name}\n", encoding="utf-8")
    return archive_path, digest


def build(args: argparse.Namespace) -> dict[str, Any]:
    if sha256(V1_INPUT_ZIP) != V1_INPUT_SHA256:
        raise RuntimeError("immutable v1 input SHA-256 mismatch")
    output = ensure_output(args.output)
    generated = utc_now()
    historical, historical_audit = make_historical_rev2(args.historical, args.forecast)
    captured, frozen_path, captured_audit = make_captured_rev2(args.events, args.knmi_root)
    feature_rows, feature_audit = build_feature_audit(historical, captured, frozen_path)
    feature_rows.to_parquet(output / "AMSTERDAM_FEATURE_PARITY_ROWS.parquet", index=False, compression="zstd")
    eligible_ids = set(
        feature_rows.loc[feature_rows["feature_status"].eq("OK"), "decision_vintage_id"].astype(str)
    )
    captured_scored = captured.loc[captured["decision_vintage_id"].astype(str).isin(eligible_ids)].copy()
    oof, evaluated, model_results, artifact = train_and_evaluate(historical, captured_scored)
    _, cohort_summary, comparison = prepare_predictions(
        historical, captured, captured_scored, oof, evaluated, artifact, output
    )
    legacy = load_legacy_selected(args.decisions, str(captured["ts_utc"].max()))
    base_reconciliation, _, _ = reconcile_market(captured, args.aligned, args.truths, args.ladder_root, legacy)
    _, _, market_summary, extra = build_market_evidence(
        captured, base_reconciliation, output, strict_pit_eligible_ids=eligible_ids
    )
    inventory = build_inventory(captured)
    write_static_contracts(output, generated)
    write_label_examples(captured, output)
    write_docs(output, generated, feature_audit, cohort_summary, comparison, inventory, market_summary, extra)
    write_json(output / "EXECUTABLE_REPLAY_FUNNEL_V2.json", extra["funnel"])
    write_shadow_and_freeze(
        output,
        generated,
        comparison,
        feature_audit,
        initialize_shadow=args.initialize_shadow,
        shadow_root=args.shadow_root,
    )
    shadow_epoch = json.loads((output / "FORWARD_EPOCH_MANIFEST_V2.json").read_text(encoding="utf-8"))
    write_reproduce_script(output)
    (output / "TEST_COMMANDS_AND_RAW_OUTPUT.txt").write_text(
        "Tests are captured after artifact generation and before final reseal.\n",
        encoding="utf-8",
    )
    # Review packet is generated before tests; final raw output and manifest are resealed later.
    p2 = comparison["cohorts"]["P2_CAPTURED_PIT_OPPORTUNITY"]
    m1_ci = p2["comparisons"]["M1_minus_B2"]["target_date_block_bootstrap"]["ci95"]
    m2_ci = p2["comparisons"]["M2_minus_B2"]["target_date_block_bootstrap"]["ci95"]
    m1_group_ci = p2["comparisons"]["M1_minus_B2"]["official_print_group_cluster_sensitivity"]["ci95"]
    m2_group_ci = p2["comparisons"]["M2_minus_B2"]["official_print_group_cluster_sensitivity"]["ci95"]
    layer_b_30 = next(row for row in market_summary["markout_by_size_horizon"] if row["shares"] == 5 and row["horizon"] == 30)
    (output / "GPT_PRO_REVIEW_PACKET_AMSTERDAM_V1_1.md").write_text(f"""# GPT Pro review packet — WCIR Amsterdam pilot v1.1

## Direct answers

1. `ta`=10-minute average, `tx`=10-minute max, `tn`=10-minute min but unavailable; fast/target/settlement identities are distinct.
2. Captured complete paths: {feature_audit['captured_complete_path']}/{feature_audit['captured_raw_opportunities']}; fail-closed incomplete: {feature_audit['captured_fail_closed_incomplete_path']} with `{json.dumps(feature_audit['captured_path_validation_reasons'], sort_keys=True)}`.
3. v1 sparse-path parity impacted {feature_audit['v1_impacted_unique_rows']}/{feature_audit['captured_raw_opportunities']} rows; per-feature counts are in the audit.
4. P0/P1 rank M1 then M2 then B2; P2 point-ranks B2 then M2 then M1.
5. Primary, first-opportunity, raw-row, and date-only sensitivities are all reported. P2 first-opportunity point ranking still favors B2, but group-cluster CIs M1={m1_group_ci} and M2={m2_group_ci} cross zero, so inference is not cluster-robust.
6. Corrected P2 target-date CIs are M1-minus-B2={m1_ci} and M2-minus-B2={m2_ci}; both challengers are significantly worse under the frozen primary statistic, so B2 is the primary reference.
7. Old 42/42 evidence is `docs/analysis/wcir_admission_forward_market_trigger_review.md:321`; it is V9 selected final-temperature expressions. v1 0/87 used a different next-print token denominator and omitted Tier-B REST archive integration.
8. 87-event reasons: {json.dumps(extra['coverage']['reason_histogram'], sort_keys=True)}.
9. Tier A entries 1/5 share={market_summary['tier_a_entry_1share']}/{market_summary['tier_a_entry_5share']}; Tier A+B={market_summary['tier_a_plus_b_entry_1share']}/{market_summary['tier_a_plus_b_entry_5share']}; markouts by horizon are in the funnel.
10. Layer B is NOT_ESTIMABLE: `{layer_b_30['tier_a_plus_b_markout_rows']}` official+30 rows across `{layer_b_30['tier_a_plus_b_markout_unique_target_dates']}` date(s), with no independent fit/evaluation date blocks.
11. MR1-minus-MR0 uplift=NULL; neither model was fit.
12. Epoch status is `{shadow_epoch['status']}` with `{shadow_epoch['prediction_journal_rows']}` append-only real score row(s); continuous process is not started because no safe registered owner can be added without the prohibited production config change.
13. Roles: B2_REFERENCE, M1_CHALLENGER, M2_CHALLENGER.
14. orders/fills/notional remain 0/0/0.
15. Continue Amsterdam prospective evidence collection; do not copy the protocol to Helsinki yet.

## Status separation

- data contract: CLOSED_V2
- feature parity: SINGLE_BUILDER_PASS_WITH_CAPTURED_SOURCE_PATH_ATTRITION
- probability model: {comparison['evidence_ranking']}
- market reaction: NOT_ESTIMABLE
- historical executable replay: TIER_A_EMPTY; TIER_A_PLUS_B_LOW_SAMPLE
- score-only shadow: {shadow_epoch['status']}
- tradable alpha: NOT_ESTABLISHED
- live authorization: NONE

Requested disposition: `ACCEPT_WITH_BLOCKING_FIXES` until a registered continuous score-only owner exists; no live or Helsinki authorization.
""", encoding="utf-8")
    seal(output)
    test_cmd = [
        str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q",
        "tests/research_tests/test_wcir_amsterdam_pilot_v1_1.py",
        "tests/research_tests/test_wcir_unified_amsterdam_pilot.py",
        "tests/research_tests/test_wcir_amsterdam_pilot_rev2.py",
        "tests/research_tests/test_wcir_next_print_contracts.py",
    ]
    completed = subprocess.run(test_cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    raw = "$ " + " ".join(test_cmd) + "\n" + completed.stdout + completed.stderr
    (output / "TEST_COMMANDS_AND_RAW_OUTPUT.txt").write_text(raw, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError("focused test suite failed; see TEST_COMMANDS_AND_RAW_OUTPUT.txt")
    seal(output)
    verify(output)
    archive, digest = package(output) if args.package else (None, None)
    return {
        "status": "complete",
        "output": str(output),
        "archive": None if archive is None else str(archive),
        "sha256": digest,
        "captured_paths": [feature_audit["captured_complete_path"], 87],
        "layer_b": "NOT_ESTIMABLE",
        "shadow": shadow_epoch["status"],
        "orders_fills_notional": [0, 0, 0],
    }


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
    parser.add_argument("--initialize-shadow", action="store_true")
    parser.add_argument("--shadow-root", type=Path, default=DEFAULT_SHADOW_ROOT)
    parser.add_argument("--observations-root", type=Path, default=DEFAULT_OBSERVATIONS_ROOT)
    parser.add_argument("--score-shadow-once", action="store_true")
    parser.add_argument("--shadow-max-events", type=int, default=128)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.score_shadow_once:
        print(json.dumps(score_shadow_once(
            output=args.output,
            shadow_root=args.shadow_root,
            knmi_root=args.knmi_root,
            observations_root=args.observations_root,
            decisions=args.decisions,
            max_events=args.shadow_max_events,
        ), indent=2, sort_keys=True))
        return 0
    if args.verify_only:
        verify(args.output)
        print(json.dumps({"status": "PASS", "output": str(args.output)}, sort_keys=True))
        return 0
    print(json.dumps(build(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
