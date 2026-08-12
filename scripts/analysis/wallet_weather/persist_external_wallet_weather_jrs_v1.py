#!/usr/bin/env python3
"""Persist an external weather-wallet snapshot to the JRS file research store.

The supported format is immutable compressed JSONL plus resumable checkpoints.
This script never writes the canonical weather DB.  When the destination is
under ``/Volumes/jrs`` it must run inside the canonical permission-bearing JRS
tmux context with ``WEATHER_JRS_IMPORT_CONTEXT=1``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.runtime.production import load_production_spec  # noqa: E402


SCHEMA_VERSION = "external_wallet_weather_research_v1"
REQUIRED_FILES = (
    "manifest.json",
    "leaderboard.json",
    "weather_activity.jsonl.gz",
    "weather_open_positions.jsonl.gz",
    "weather_closed_positions.jsonl.gz",
    "event_metadata.jsonl.gz",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def snapshot_id_from_manifest(manifest: dict[str, Any]) -> str:
    timestamp = datetime.fromisoformat(str(manifest["coverage"]["query_end_utc"]))
    return timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def copy_verified(source: Path, destination: Path) -> dict[str, Any]:
    # Direct-to-JRS collection intentionally persists from the exact destination
    # path.  The collector has already atomically written and checksummed the
    # aggregate artifacts/checkpoints, so there is no copy boundary to verify.
    if source == destination:
        return {
            "path": str(destination),
            "bytes": 0,
            "sha256": None,
            "copied": False,
            "verification": "source_is_exact_destination_no_copy",
            "size_accounting": "not_recounted; collector manifest/checkpoint owns size",
        }
    source_hash = file_sha256(source)
    if source.resolve() == destination.resolve():
        return {
            "path": str(destination),
            "bytes": source.stat().st_size,
            "sha256": source_hash,
            "copied": False,
            "verification": "source_destination_alias_sha256_verified",
        }
    if destination.exists():
        destination_hash = file_sha256(destination)
        if destination_hash != source_hash:
            raise RuntimeError(
                f"immutable JRS artifact differs: {destination}; "
                f"source={source_hash} destination={destination_hash}"
            )
        return {
            "path": str(destination),
            "bytes": destination.stat().st_size,
            "sha256": destination_hash,
            "copied": False,
        }

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.copyfile(source, temporary)
    copied_hash = file_sha256(temporary)
    if copied_hash != source_hash:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"copy verification failed: {source} -> {destination}")
    os.replace(temporary, destination)
    return {
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": copied_hash,
        "copied": True,
    }


def checkpoint_files(source: Path) -> list[Path]:
    daily = source / "daily_activity"
    metadata = source / "event_metadata_by_slug"
    if not daily.is_dir():
        raise FileNotFoundError(daily)
    if not metadata.is_dir():
        raise FileNotFoundError(metadata)
    rows = sorted(
        Path(entry.path)
        for entry in os.scandir(daily)
        if entry.is_file(follow_symlinks=False)
        and (entry.name.endswith(".jsonl.gz") or entry.name.endswith(".meta.json"))
    )
    rows.extend(
        sorted(
            Path(entry.path)
            for entry in os.scandir(metadata)
            if entry.is_file(follow_symlinks=False)
        )
    )
    if not rows:
        raise RuntimeError(f"checkpoint directories are empty: {source}")
    return rows


def relative_artifact_path(source: Path, path: Path) -> Path:
    return path.relative_to(source)


def persist_snapshot(source: Path, destination: Path) -> list[dict[str, Any]]:
    artifacts: list[Path] = []
    for name in REQUIRED_FILES:
        path = source / name
        if not path.is_file():
            raise FileNotFoundError(path)
        artifacts.append(path)
    if source == destination:
        rows = [copy_verified(path, path) for path in artifacts]
        for name in ("daily_activity", "event_metadata_by_slug"):
            directory = source / name
            if not directory.is_dir():
                raise FileNotFoundError(directory)
            with os.scandir(directory) as entries:
                if not any(entry.is_file(follow_symlinks=False) for entry in entries):
                    raise RuntimeError(f"checkpoint directory is empty: {directory}")
            rows.append(
                {
                    "path": str(directory),
                    "bytes": 0,
                    "sha256": None,
                    "copied": False,
                    "verification": "source_is_exact_destination_directory_registered",
                    "size_accounting": (
                        "not_recounted; collector manifest/checkpoint owns contents"
                    ),
                }
            )
        return rows
    artifacts.extend(checkpoint_files(source))
    return [
        copy_verified(path, destination / relative_artifact_path(source, path))
        for path in artifacts
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--jrs-root",
        type=Path,
        default=(
            load_production_spec().archive_storage_root
            / "pm_agents/research/external_wallet_weather"
        ),
    )
    parser.add_argument("--wallet")
    args = parser.parse_args()

    source = args.source.resolve()
    jrs_root = args.jrs_root.resolve()
    if str(jrs_root).startswith("/Volumes/jrs") and os.environ.get(
        "WEATHER_JRS_IMPORT_CONTEXT"
    ) != "1":
        raise RuntimeError(
            "writes under /Volumes/jrs require the canonical JRS tmux context "
            "and WEATHER_JRS_IMPORT_CONTEXT=1"
        )

    source_manifest_path = source / "manifest.json"
    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    wallet = str(args.wallet or manifest["wallet"]).lower()
    if wallet != str(manifest["wallet"]).lower():
        raise RuntimeError(f"wallet mismatch: argument={wallet} manifest={manifest['wallet']}")
    snapshot_id = snapshot_id_from_manifest(manifest)
    source_manifest_sha256 = file_sha256(source_manifest_path)
    destination = jrs_root / "raw" / f"wallet={wallet}" / f"snapshot={snapshot_id}"
    copied = persist_snapshot(source, destination)

    counts = {
        "wallet_activity": int(manifest["coverage"]["weather_activity_rows"]),
        "wallet_positions_snapshot": int(
            manifest["supporting_endpoints"]["open_weather_position_rows"]
        ),
        "wallet_closed_positions": int(
            manifest["supporting_endpoints"]["closed_weather_position_rows"]
        ),
        "weather_events": int(manifest["supporting_endpoints"]["event_metadata_rows"]),
        "weather_markets": int(manifest["supporting_endpoints"]["gamma_market_rows"]),
    }
    import_manifest = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "wallet": wallet,
        "imported_at_utc": utc_now(),
        "source_path": str(source),
        "source_manifest_sha256": source_manifest_sha256,
        "raw_destination": str(destination),
        "storage_format": "immutable_json_jsonl_gzip_checkpoints",
        "sqlite_built": False,
        "database_counts": counts,
        "copied_files": len(copied),
        "copied_bytes": sum(int(row["bytes"]) for row in copied),
        "newly_copied_files": sum(bool(row["copied"]) for row in copied),
        "artifacts": copied,
        "canonical_boundary": (
            "external research evidence only; not canonical fact_trades or "
            "fact_signal_candidates"
        ),
    }
    atomic_write_json(destination / "jrs_import_manifest.json", import_manifest)
    atomic_write_json(
        jrs_root / "latest" / f"{wallet}.json",
        {
            "schema_version": SCHEMA_VERSION,
            "wallet": wallet,
            "snapshot_id": snapshot_id,
            "raw_destination": str(destination),
            "storage_format": import_manifest["storage_format"],
            "updated_at_utc": utc_now(),
        },
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "wallet": wallet,
                "snapshot_id": snapshot_id,
                "raw_destination": str(destination),
                "counts": counts,
                "copied_files": len(copied),
                "copied_bytes": import_manifest["copied_bytes"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
