#!/usr/bin/env python3
"""Append strict physical-artifact verdicts for unverified replay cases."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402
from weather_dashboard.db.replay_case_schema import (  # noqa: E402
    REPLAY_CASE_VIEW,
    REPLAY_CATALOG_BUILD_ID,
    record_replay_artifact_verification,
)
from weather_model_evaluation import verify_replay_artifacts  # noqa: E402
from weather_model_evaluation.replay_artifacts import (  # noqa: E402
    REPLAY_ARTIFACT_VERIFIER_BUILD_ID,
)


REPORT_SCHEMA_VERSION = "weather_replay_artifact_materialization_v1"


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_value(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _artifact_refs(value: Any) -> Iterable[dict[str, Any]]:
    """Yield only explicit path/hash evidence; never promote a row hash."""

    if isinstance(value, list):
        for item in value:
            yield from _artifact_refs(item)
        return
    if not isinstance(value, dict):
        return
    path = value.get("path") or value.get("physical_path") or value.get("source_path")
    if path:
        expected = value.get("sha256") or value.get("file_sha256")
        artifact = {
            "path": str(path),
            "sha256": str(expected) if expected else None,
        }
        for field in ("role", "physical_line", "physical_record_index"):
            if value.get(field) is not None:
                artifact[field] = value[field]
        yield artifact
    for item in value.values():
        if isinstance(item, (dict, list)):
            yield from _artifact_refs(item)


def artifacts_for_case(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    if row.get("replay_capability") == "legacy_summary_only":
        return [{"legacy": True, "kind": "legacy_summary"}]

    artifacts: list[dict[str, Any]] = []
    artifacts.extend(_artifact_refs(_json_value(row.get("input_refs_json"))))
    raw_path = row.get("trigger_event_raw_source_path")
    if raw_path and not any(item.get("path") == raw_path for item in artifacts):
        # raw_row_hash is deliberately not mapped to sha256: it identifies a
        # logical row, not the physical file.  This therefore fails closed as
        # unverifiable until capture records an actual file digest.
        artifacts.append(
            {
                "path": str(raw_path),
                "sha256": None,
                "raw_row_hash": row.get("trigger_event_raw_row_hash"),
                "kind": "trigger_raw_row_without_file_hash",
            }
        )
    if not artifacts:
        artifacts.append(
            {
                "path": str(row.get("model_artifact_id") or "unbound-model-artifact"),
                "sha256": None,
                "kind": "unbound_model_artifact",
            }
        )
    unique: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        key = json.dumps(artifact, sort_keys=True, separators=(",", ":"))
        unique[key] = artifact
    return [unique[key] for key in sorted(unique)]


def materialize(
    conn: sqlite3.Connection,
    *,
    managed_roots: tuple[Path, ...],
    max_cases: int,
    apply: bool,
    commit_every: int = 500,
) -> dict[str, Any]:
    if max_cases <= 0 or commit_every <= 0:
        raise ValueError("max_cases and commit_every must be positive")
    rows = conn.execute(
        f"""
        SELECT replay_case_id, replay_capability, input_refs_json,
               trigger_event_raw_source_path, trigger_event_raw_row_hash,
               model_artifact_id
        FROM {REPLAY_CASE_VIEW} AS replay
        WHERE NOT EXISTS (
          SELECT 1 FROM weather_replay_artifact_verifications AS verification
          WHERE verification.replay_case_id = replay.replay_case_id
            AND verification.catalog_build_id = ?
            AND verification.verifier_build_id = ?
        )
        ORDER BY decision_ts_utc, replay_case_id
        LIMIT ?
        """,
        (REPLAY_CATALOG_BUILD_ID, REPLAY_ARTIFACT_VERIFIER_BUILD_ID, max_cases),
    ).fetchall()
    counts: Counter[str] = Counter()
    inserted = 0
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for index, row in enumerate(rows, start=1):
        artifacts = artifacts_for_case(dict(row))
        verdict = verify_replay_artifacts(
            artifacts,
            managed_roots=managed_roots,
            catalog_build_id=REPLAY_CATALOG_BUILD_ID,
        )
        counts[str(verdict["status"])] += 1
        if apply and record_replay_artifact_verification(
            conn,
            replay_case_id=str(row["replay_case_id"]),
            verification=verdict,
            verified_at_utc=checked_at,
        ):
            inserted += 1
        if apply and index % commit_every == 0:
            conn.commit()
    if apply:
        conn.commit()
    remaining = conn.execute(
        f"""
        SELECT count(*) FROM {REPLAY_CASE_VIEW} AS replay
        WHERE NOT EXISTS (
          SELECT 1 FROM weather_replay_artifact_verifications AS verification
          WHERE verification.replay_case_id = replay.replay_case_id
            AND verification.catalog_build_id = ?
            AND verification.verifier_build_id = ?
        )
        """,
        (REPLAY_CATALOG_BUILD_ID, REPLAY_ARTIFACT_VERIFIER_BUILD_ID),
    ).fetchone()[0]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "catalog_build_id": REPLAY_CATALOG_BUILD_ID,
        "verifier_build_id": REPLAY_ARTIFACT_VERIFIER_BUILD_ID,
        "mode": "apply" if apply else "dry_run",
        "selected": len(rows),
        "inserted": inserted,
        "status_counts": dict(sorted(counts.items())),
        "remaining": int(remaining),
        "checked_at_utc": checked_at,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--expected-db", type=Path, required=True)
    parser.add_argument("--max-cases", type=int, default=5000)
    parser.add_argument("--commit-every", type=int, default=500)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    db_path = args.db.resolve(strict=True)
    expected_db = args.expected_db.resolve(strict=True)
    if not os.path.samefile(db_path, expected_db):
        raise ValueError("replay verification DB is not the canonical DB")
    spec = load_production_spec()
    managed_roots = tuple(
        root
        for root in (
            spec.production_storage_root,
            spec.archive_storage_root,
            spec.production_release_root,
            spec.operational_repo_root,
        )
        if root is not None
    )
    mode = "rw" if args.apply else "ro"
    conn = sqlite3.connect(f"file:{db_path}?mode={mode}", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        report = materialize(
            conn,
            managed_roots=managed_roots,
            max_cases=args.max_cases,
            apply=args.apply,
            commit_every=args.commit_every,
        )
    finally:
        conn.close()
    _write_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
