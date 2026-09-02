#!/usr/bin/env python3
"""Export a sealed candidate-level replay projection for dataset splitting."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402
from weather_dashboard.db.replay_case_schema import (  # noqa: E402
    REPLAY_CASE_VIEW,
    REPLAY_CATALOG_BUILD_ID,
)
from weather_model_evaluation import stable_json  # noqa: E402
from weather_model_evaluation.dataset_manifest import sha256_file  # noqa: E402


SCHEMA_VERSION = "weather_replay_dataset_projection_v1"


def _allowed_output(path: Path) -> Path:
    resolved = path.resolve()
    allowed = [
        Path(tempfile.gettempdir()).resolve(),
        (ROOT / "runtime/research").resolve(),
        (ROOT / "research_outputs").resolve(),
    ]
    try:
        allowed.append(load_production_spec().research_artifact_root.resolve(strict=False))
    except (OSError, ValueError):
        pass
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise ValueError("projection output must use a temp or registered research root")
    return resolved


def _seal_file(temporary: Path, target: Path) -> Path:
    if target.exists():
        if sha256_file(target) != sha256_file(temporary):
            raise FileExistsError(f"conflicting sealed projection artifact: {target}")
        temporary.unlink()
        return target
    os.link(temporary, target)
    temporary.unlink()
    target.chmod(0o444)
    return target


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _split_boundaries(target_dates: list[str]) -> dict[str, str]:
    dates = sorted(set(target_dates))
    if len(dates) < 4:
        raise ValueError("replay projection requires at least four target-date blocks")
    count = len(dates)
    train_index = max(0, min(count - 4, int(count * 0.60) - 1))
    validation_index = max(train_index + 1, min(count - 3, int(count * 0.75) - 1))
    test_index = max(validation_index + 1, min(count - 2, int(count * 0.90) - 1))
    return {
        "train_end": dates[train_index],
        "validation_end": dates[validation_index],
        "test_end": dates[test_index],
        "frozen_forward_start": dates[test_index + 1],
    }


def export_projection(
    conn: sqlite3.Connection,
    *,
    output_dir: Path,
    measurement_namespace: str,
    start_date: str | None,
    end_date: str | None,
    max_rows: int | None,
    cutoff_utc: str,
    code_sha: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    clauses = [
        "labels.measurement_namespace = ?",
        "labels.label_availability_status = 'known'",
        "labels.label_available_at_utc IS NOT NULL",
        "replay.candidate_grain_version = 'v2_event_checkpoint'",
    ]
    params: list[Any] = [measurement_namespace]
    if start_date:
        clauses.append("replay.event_date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("replay.event_date <= ?")
        params.append(end_date)
    limit = ""
    if max_rows is not None:
        if max_rows <= 0:
            raise ValueError("max_rows must be positive")
        limit = " LIMIT ?"
        params.append(max_rows)
    query = f"""
        SELECT
          replay.replay_case_id AS row_id,
          replay.event_date AS target_date,
          replay.city,
          replay.strategy_key,
          replay.decision_ts_utc,
          replay.trigger_event_id,
          replay.state_checkpoint_id,
          replay.feature_store_frame_id,
          replay.feature_row_id,
          replay.model_artifact_id,
          replay.target_id,
          replay.target_kind,
          replay.side,
          replay.model_p_yes,
          replay.model_probability_after,
          replay.market_probability,
          replay.decision_entry_price,
          replay.replay_capability,
          replay.model_replay_blocker,
          replay.policy_replay_blocker,
          replay.replay_artifact_verification_status,
          labels.measurement_namespace,
          labels.settlement_yes AS label_value,
          labels.label_available_at_utc,
          labels.settlement_source_id,
          labels.source_payload_hash
        FROM {REPLAY_CASE_VIEW} AS replay
        JOIN weather_candidate_settlement_labels AS labels
          ON labels.candidate_id = replay.replay_case_id
        WHERE {' AND '.join(clauses)}
        ORDER BY replay.event_date, replay.decision_ts_utc, replay.replay_case_id
        {limit}
    """
    cursor = conn.execute(query, params)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=output_dir, prefix=".source.", suffix=".jsonl", delete=False
    ) as source_handle, tempfile.NamedTemporaryFile(
        mode="wb", dir=output_dir, prefix=".rows.", suffix=".jsonl", delete=False
    ) as rows_handle, tempfile.NamedTemporaryFile(
        dir=output_dir, prefix=".projection.", suffix=".db", delete=False
    ) as db_handle:
        source_tmp = Path(source_handle.name)
        rows_tmp = Path(rows_handle.name)
        db_tmp = Path(db_handle.name)
    projection = sqlite3.connect(db_tmp)
    projection.execute("PRAGMA journal_mode=DELETE")
    projection.execute(
        "CREATE TABLE replay_projection (row_id TEXT PRIMARY KEY, source_json TEXT NOT NULL, label_value REAL NOT NULL, label_available_at_utc TEXT NOT NULL)"
    )
    projection.execute("CREATE TABLE projection_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    count = 0
    target_dates: list[str] = []
    capability_counts: dict[str, int] = {}
    try:
        with source_tmp.open("wb") as source_handle, rows_tmp.open("wb") as rows_handle:
            for result in cursor:
                raw = dict(result)
                row_id = str(raw["row_id"])
                target_dates.append(str(raw["target_date"]))
                capability = str(raw.get("replay_capability") or "unknown")
                capability_counts[capability] = capability_counts.get(capability, 0) + 1
                source_row = {
                    key: value
                    for key, value in raw.items()
                    if key
                    not in {
                        "label_value",
                        "label_available_at_utc",
                        "settlement_source_id",
                        "source_payload_hash",
                    }
                }
                source_row["available_at_utc"] = raw["decision_ts_utc"]
                dataset_row = {
                    **source_row,
                    "label_value": raw["label_value"],
                    "label_available_at_utc": raw["label_available_at_utc"],
                    "settlement_source_id": raw["settlement_source_id"],
                    "settlement_source_payload_hash": raw["source_payload_hash"],
                    "input_refs": [
                        {"source_id": "canonical_replay_projection", "row_id": row_id}
                    ],
                    "dataset_level": "canonical_candidate_projection",
                }
                source_text = stable_json(source_row)
                source_handle.write((source_text + "\n").encode("utf-8"))
                rows_handle.write((stable_json(dataset_row) + "\n").encode("utf-8"))
                projection.execute(
                    "INSERT INTO replay_projection VALUES (?, ?, ?, ?)",
                    (
                        row_id,
                        source_text,
                        raw["label_value"],
                        raw["label_available_at_utc"],
                    ),
                )
                count += 1
                if count % 1000 == 0:
                    projection.commit()
        if count == 0:
            raise ValueError("replay projection selected no rows")
        boundaries = _split_boundaries(target_dates)
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "catalog_build_id": REPLAY_CATALOG_BUILD_ID,
            "measurement_namespace": measurement_namespace,
            "cutoff_utc": cutoff_utc,
            "code_sha": code_sha,
            "row_count": count,
            "target_date_min": min(target_dates),
            "target_date_max": max(target_dates),
            "split_boundaries": boundaries,
            "capability_counts": capability_counts,
        }
        for key, value in metadata.items():
            projection.execute(
                "INSERT INTO projection_metadata VALUES (?, ?)",
                (key, json.dumps(value, sort_keys=True)),
            )
        projection.commit()
        projection.close()
        source_hash = sha256_file(source_tmp)
        rows_hash = sha256_file(rows_tmp)
        db_hash = sha256_file(db_tmp)
        source_path = _seal_file(
            source_tmp, output_dir / f"projection_source.{source_hash}.jsonl"
        )
        rows_path = _seal_file(rows_tmp, output_dir / f"projection_rows.{rows_hash}.jsonl")
        db_path = _seal_file(db_tmp, output_dir / f"projection_snapshot.{db_hash}.db")
        source_config = {
            "sources": [
                {
                    "source_id": "canonical_replay_projection",
                    "path": str(source_path),
                    "available_at_field": "available_at_utc",
                    "cutoff_utc": cutoff_utc,
                }
            ]
        }
        config_payload = _json_bytes(source_config)
        config_hash = hashlib.sha256(config_payload).hexdigest()
        config_tmp = output_dir / f".source_config.{os.getpid()}.json"
        config_tmp.write_bytes(config_payload)
        config_path = _seal_file(
            config_tmp, output_dir / f"projection_source_config.{config_hash}.json"
        )
        receipt = {
            **metadata,
            "source_path": str(source_path),
            "source_sha256": source_hash,
            "rows_path": str(rows_path),
            "rows_sha256": rows_hash,
            "db_snapshot_path": str(db_path),
            "db_snapshot_sha256": db_hash,
            "source_config_path": str(config_path),
            "source_config_sha256": config_hash,
        }
        receipt_payload = _json_bytes(receipt)
        receipt_hash = hashlib.sha256(receipt_payload).hexdigest()
        receipt_tmp = output_dir / f".projection_receipt.{os.getpid()}.json"
        receipt_tmp.write_bytes(receipt_payload)
        receipt_path = _seal_file(
            receipt_tmp, output_dir / f"projection_receipt.{receipt_hash}.json"
        )
        return {"receipt_path": str(receipt_path), **receipt}
    except Exception:
        projection.close()
        for temporary in (source_tmp, rows_tmp, db_tmp):
            temporary.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--expected-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--measurement-namespace", choices=("tmax", "tmin"), required=True)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--cutoff-utc", required=True)
    parser.add_argument("--code-sha", required=True)
    args = parser.parse_args(argv)
    db_path = args.db.resolve(strict=True)
    expected = args.expected_db.resolve(strict=True)
    if not os.path.samefile(db_path, expected):
        raise ValueError("projection source is not the expected canonical DB")
    output_dir = _allowed_output(args.output_dir)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        receipt = export_projection(
            conn,
            output_dir=output_dir,
            measurement_namespace=args.measurement_namespace,
            start_date=args.start_date,
            end_date=args.end_date,
            max_rows=args.max_rows,
            cutoff_utc=args.cutoff_utc,
            code_sha=args.code_sha,
        )
    finally:
        conn.close()
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
