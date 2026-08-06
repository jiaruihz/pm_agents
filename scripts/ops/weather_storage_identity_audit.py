#!/usr/bin/env python3
"""Read-only audit of weather storage identity and declared write targets.

This intentionally audits configured current-production paths.  It does not
walk historical research trees, hash multi-gigabyte data, or mutate SQLite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import WeatherProductionSpec, load_production_spec


SIDECAR_DB_NAMES = {"weather_decision_journal.db"}
SCHEMA_PROBE_TIMEOUT_SEC = 5.0
SCHEMA_PROBE_CODE = r"""
import json
import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    with path.open("rb") as handle:
        if handle.read(16) != b"SQLite format 3\x00":
            print(json.dumps({"error": "not_sqlite"}))
            raise SystemExit(0)
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=1.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1000")
        rows = conn.execute(
            "SELECT type, name, COALESCE(sql, '') FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    finally:
        conn.close()
    print(json.dumps({"rows": rows}, ensure_ascii=False, separators=(",", ":")))
except (OSError, sqlite3.Error) as exc:
    print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
"""


def file_identity(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "is_symlink": path.is_symlink(),
        "resolved_path": str(path.resolve(strict=False)),
    }
    if not path.exists():
        return result
    stat = path.stat()
    result.update(
        {
            "device": stat.st_dev,
            "inode": stat.st_ino,
            "size_bytes": stat.st_size,
            "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "process_writable": os.access(path, os.W_OK),
        }
    )
    return result


def sqlite_schema_fingerprint(
    path: Path, *, timeout_sec: float = SCHEMA_PROBE_TIMEOUT_SEC
) -> tuple[str | None, str | None]:
    """Fingerprint SQLite schema without letting a stalled volume hang the audit."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", SCHEMA_PROBE_CODE, str(path)],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"schema_probe_timeout_after_{timeout_sec:g}s"
    if proc.returncode != 0:
        return None, f"schema_probe_exit_{proc.returncode}: {proc.stderr.strip()}"
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return None, f"schema_probe_invalid_json: {exc}"
    if result.get("error"):
        return None, str(result["error"])
    rows = result.get("rows")
    if not isinstance(rows, list):
        return None, "schema_probe_missing_rows"
    try:
        payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest(), None
    except (TypeError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def iter_db_files(roots: Iterable[Path]) -> Iterable[Path]:
    seen: set[str] = set()
    patterns = (
        "*.db",
        "*.sqlite",
        "*.sqlite3",
        "_legacy/*.db",
        "weather_edge_v1/*.db",
        "weather_edge_v1/*/*.db",
    )
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            candidates = [root]
        else:
            candidates = [path for pattern in patterns for path in root.glob(pattern)]
        for path in candidates:
            key = str(path)
            if key not in seen:
                seen.add(key)
                yield path


def lsof_users(paths: Iterable[Path]) -> dict[str, list[dict[str, Any]]]:
    existing = [str(path) for path in paths if path.exists()]
    if not existing:
        return {}
    try:
        proc = subprocess.run(
            ["lsof", "-nP", "-Fpcn", "--", *existing],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    users: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pid: int | None = None
    command = ""
    for line in proc.stdout.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("c"):
            command = line[1:]
        elif line.startswith("n") and pid is not None:
            users[line[1:]].append({"pid": pid, "command": command})
    return dict(users)


def journal_shape(path: Path, tail_lines: int = 200) -> dict[str, Any]:
    result = {"path": str(path), "exists": path.is_file()}
    if not path.is_file():
        return result
    try:
        lines = path.read_bytes().splitlines()[-tail_lines:]
    except OSError as exc:
        return {**result, "error": f"{type(exc).__name__}: {exc}"}
    keysets: Counter[tuple[str, ...]] = Counter()
    schema_versions: Counter[str] = Counter()
    schema_versions_by_record_type: dict[str, Counter[str]] = defaultdict(Counter)
    parse_errors = 0
    seen_versioned_row = False
    missing_schema_after_versioned = 0
    latest_schema_version: str | None = None
    for raw in lines:
        try:
            row = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            parse_errors += 1
            continue
        if not isinstance(row, dict):
            parse_errors += 1
            continue
        keysets[tuple(sorted(row))] += 1
        schema_version = str(row.get("schema_version") or "<missing>")
        record_type = str(
            row.get("record_type") or row.get("event_type") or "<default>"
        )
        schema_versions[schema_version] += 1
        schema_versions_by_record_type[record_type][schema_version] += 1
        latest_schema_version = schema_version
        if schema_version == "<missing>":
            if seen_versioned_row:
                missing_schema_after_versioned += 1
        else:
            seen_versioned_row = True
    missing_count = schema_versions.get("<missing>", 0)
    legacy_unversioned_prefix_rows = (
        missing_count
        if missing_count
        and not missing_schema_after_versioned
        and latest_schema_version not in (None, "<missing>")
        else 0
    )
    return {
        **result,
        "checked_rows": len(lines),
        "parse_error_count": parse_errors,
        "schema_versions": dict(schema_versions),
        "schema_versions_by_record_type": {
            record_type: dict(versions)
            for record_type, versions in schema_versions_by_record_type.items()
        },
        "latest_schema_version": latest_schema_version,
        "legacy_unversioned_prefix_rows": legacy_unversioned_prefix_rows,
        "missing_schema_after_versioned": missing_schema_after_versioned,
        "distinct_key_shapes": len(keysets),
        "top_key_shapes": [
            {"count": count, "keys": list(keys)}
            for keys, count in keysets.most_common(3)
        ],
    }


def configured_artifacts(spec: WeatherProductionSpec) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for runtime in spec.managed_runtimes:
        for role, path in (
            ("health", runtime.health_path),
            ("live_orders", runtime.live_order_path),
        ):
            if path is None:
                continue
            if not path.is_absolute():
                path = (runtime.checkout_root or spec.operational_repo_root) / path
            artifacts.append(
                {"instance_id": runtime.instance_id, "role": role, **file_identity(path)}
            )
    return artifacts


def build_report(spec: WeatherProductionSpec) -> dict[str, Any]:
    canonical = spec.canonical_db_path
    compatibility = spec.resolved_compatibility_db_paths()
    db_roots = (
        spec.operational_repo_root / "runtime",
        spec.pm_runtime_root,
        spec.data_feed_runtime_root,
    )
    db_paths = sorted(iter_db_files((*db_roots, canonical, *compatibility)), key=str)
    open_users = lsof_users(db_paths)
    canonical_identity = file_identity(canonical)
    canonical_pair = (canonical_identity.get("device"), canonical_identity.get("inode"))
    canonical_schema, canonical_schema_error = sqlite_schema_fingerprint(canonical)
    schema_by_identity: dict[tuple[Any, Any], tuple[str | None, str | None]] = {}
    if canonical_pair[0] is not None:
        schema_by_identity[canonical_pair] = (canonical_schema, canonical_schema_error)
    dbs: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    compatibility_set = {str(path) for path in compatibility}
    for path in db_paths:
        item = file_identity(path)
        pair = (item.get("device"), item.get("inode"))
        schema_identity_reused = pair[0] is not None and pair in schema_by_identity
        if schema_identity_reused:
            schema, schema_error = schema_by_identity[pair]
        else:
            schema, schema_error = sqlite_schema_fingerprint(path)
            if pair[0] is not None:
                schema_by_identity[pair] = (schema, schema_error)
        if str(path) == str(canonical):
            classification = "physical_canonical"
        elif str(path) in compatibility_set and pair == canonical_pair:
            classification = "compatibility_alias"
        elif path.name in SIDECAR_DB_NAMES:
            classification = "declared_sidecar"
        elif "_legacy" in path.parts:
            classification = "historical_legacy"
        elif path.name == canonical.name and pair != canonical_pair:
            classification = "distinct_canonical_name"
            findings.append(
                {
                    "severity": "critical",
                    "kind": "distinct_weather_db",
                    "path": str(path),
                    "message": "distinct weather.db exists outside the physical canonical identity",
                }
            )
        else:
            classification = "other_sqlite"
        if item.get("size_bytes") == 0:
            findings.append(
                {
                    "severity": "warning",
                    "kind": "zero_byte_sqlite_artifact",
                    "path": str(path),
                    "message": "empty SQLite-named artifact in an active runtime root",
                }
            )
        if path.name.startswith(("tmp_", "temp_")):
            findings.append(
                {
                    "severity": "warning",
                    "kind": "temporary_sqlite_in_runtime",
                    "path": str(path),
                    "message": "temporary SQLite artifact is stored in the persistent runtime root",
                }
            )
        if schema and canonical_schema and schema == canonical_schema and pair != canonical_pair:
            findings.append(
                {
                    "severity": "warning",
                    "kind": "canonical_schema_copy",
                    "path": str(path),
                    "message": "distinct SQLite file has the canonical weather schema",
                }
            )
        item.update(
            {
                "classification": classification,
                "schema_fingerprint": schema,
                "schema_error": schema_error,
                "schema_identity_reused": schema_identity_reused,
                "open_by": open_users.get(str(path), []),
            }
        )
        dbs.append(item)

    artifacts = configured_artifacts(spec)
    declared_paths: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in artifacts:
        declared_paths[item["resolved_path"]].append(
            {"instance_id": item["instance_id"], "role": item["role"]}
        )
    for path, owners in declared_paths.items():
        roles = {owner["role"] for owner in owners}
        if len(owners) > 1 and "live_orders" in roles:
            findings.append(
                {
                    "severity": "critical",
                    "kind": "shared_live_order_target",
                    "path": path,
                    "owners": owners,
                    "message": "multiple production declarations share a live order journal",
                }
            )

    journals = [journal_shape(path) for path in spec.active_live_order_paths()]
    for journal in journals:
        if journal.get("parse_error_count", 0):
            findings.append(
                {
                    "severity": "critical",
                    "kind": "live_order_parse_error",
                    "path": journal["path"],
                    "count": journal["parse_error_count"],
                }
            )
        versions = journal.get("schema_versions") or {}
        if versions.get("<missing>", 0):
            legacy_prefix_rows = journal.get("legacy_unversioned_prefix_rows", 0)
            findings.append(
                {
                    "severity": "info" if legacy_prefix_rows else "warning",
                    "kind": (
                        "live_order_legacy_unversioned_prefix"
                        if legacy_prefix_rows
                        else "live_order_schema_missing"
                    ),
                    "path": journal["path"],
                    "row_count": versions["<missing>"],
                    "message": (
                        "append-only legacy prefix is preserved; current tail is versioned"
                        if legacy_prefix_rows
                        else "current or interleaved journal rows are missing schema_version"
                    ),
                }
            )
        by_record_type = journal.get("schema_versions_by_record_type") or {}
        for record_type, record_versions in by_record_type.items():
            declared_versions = [
                value for value in record_versions if value != "<missing>"
            ]
            if len(declared_versions) > 1:
                findings.append(
                    {
                        "severity": "warning",
                        "kind": "live_order_schema_version_drift",
                        "path": journal["path"],
                        "record_type": record_type,
                        "versions": record_versions,
                    }
                )

    critical = sum(item["severity"] == "critical" for item in findings)
    warning = sum(item["severity"] == "warning" for item in findings)
    physical_identities = {
        (item.get("device"), item.get("inode"))
        for item in dbs
        if item.get("exists") and item.get("device") is not None
    }
    return {
        "status": "critical" if critical else ("warning" if warning else "healthy"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "production_spec_version": spec.version,
        "canonical_db": {
            **canonical_identity,
            "schema_fingerprint": canonical_schema,
            "schema_error": canonical_schema_error,
        },
        "db_scan_roots": [str(path) for path in db_roots],
        "databases": dbs,
        "configured_artifacts": artifacts,
        "active_live_order_journals": journals,
        "findings": findings,
        "summary": {
            "database_file_count": len(dbs),
            "physical_database_identity_count": len(physical_identities),
            "critical_count": critical,
            "warning_count": warning,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-spec", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    report = build_report(load_production_spec(args.production_spec))
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(payload)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload + "\n", encoding="utf-8")
    return 1 if report["status"] == "critical" else 0


if __name__ == "__main__":
    raise SystemExit(main())
