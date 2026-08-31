#!/usr/bin/env python3
"""Replay the latest immutable snapshot for a list of external wallets.

This is orchestration only: every wallet is still evaluated by
``research_external_wallet_full_ladder_history_v1.py`` with its own immutable
snapshot and output directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_wallets(path: Path) -> list[str]:
    wallets: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        wallet = raw.strip().lower()
        if not wallet or wallet.startswith("#"):
            continue
        if len(wallet) != 42 or not wallet.startswith("0x"):
            raise ValueError(f"invalid wallet in {path}: {wallet}")
        int(wallet[2:], 16)
        if wallet not in seen:
            wallets.append(wallet)
            seen.add(wallet)
    if not wallets:
        raise ValueError(f"wallet list is empty: {path}")
    return wallets


def read_peer_scan_wallets(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") not in {"complete", "complete_with_blockers"}:
        raise ValueError(f"peer scan is not complete: {path} status={payload.get('status')}")
    wallets = [str(value).strip().lower() for value in payload.get("selected_wallets") or []]
    if not wallets:
        raise ValueError(f"peer scan selected_wallets is empty: {path}")
    for wallet in wallets:
        if len(wallet) != 42 or not wallet.startswith("0x"):
            raise ValueError(f"invalid selected wallet in {path}: {wallet}")
        int(wallet[2:], 16)
    return list(dict.fromkeys(wallets))


def snapshot_id_from_end(value: str) -> str:
    normalized = value.strip().replace("Z", "+00:00")
    timestamp = datetime.fromisoformat(normalized)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--wallet-file", type=Path)
    source.add_argument("--peer-scan", type=Path)
    parser.add_argument("--jrs-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collect-missing", action="store_true")
    parser.add_argument("--collection-end")
    parser.add_argument("--collection-workers", type=int, default=8)
    parser.add_argument(
        "--collector",
        type=Path,
        default=Path(__file__).with_name(
            "collect_external_wallet_weather_history_v1.py"
        ),
    )
    parser.add_argument(
        "--persister",
        type=Path,
        default=Path(__file__).with_name("persist_external_wallet_weather_jrs_v1.py"),
    )
    parser.add_argument(
        "--runner",
        type=Path,
        default=Path(__file__).with_name(
            "research_external_wallet_full_ladder_history_v1.py"
        ),
    )
    args = parser.parse_args()

    wallets = (
        read_wallets(args.wallet_file.resolve())
        if args.wallet_file
        else read_peer_scan_wallets(args.peer_scan.resolve())
    )
    jrs_root = args.jrs_root.resolve()
    runner = args.runner.resolve()
    collector = args.collector.resolve()
    persister = args.persister.resolve()
    output = args.output.resolve()
    results: list[dict[str, Any]] = []
    if args.collect_missing and not args.collection_end:
        parser.error("--collect-missing requires --collection-end")
    collection_snapshot_id = (
        snapshot_id_from_end(args.collection_end) if args.collection_end else None
    )

    for index, wallet in enumerate(wallets, start=1):
        latest_path = jrs_root / "latest" / f"{wallet}.json"
        try:
            if args.collect_missing and not latest_path.exists():
                source = (
                    jrs_root
                    / "raw"
                    / f"wallet={wallet}"
                    / f"snapshot={collection_snapshot_id}"
                )
                print(
                    f"collect {index}/{len(wallets)} wallet={wallet} "
                    f"snapshot={collection_snapshot_id}",
                    flush=True,
                )
                collected = subprocess.run(
                    [
                        sys.executable,
                        str(collector),
                        "--wallet",
                        wallet,
                        "--output",
                        str(source),
                        "--end",
                        args.collection_end,
                        "--workers",
                        str(max(1, args.collection_workers)),
                    ],
                    check=False,
                )
                if collected.returncode != 0:
                    raise RuntimeError(
                        f"collector failed returncode={collected.returncode}"
                    )
                persist_env = dict(os.environ)
                persist_env["WEATHER_JRS_IMPORT_CONTEXT"] = "1"
                persisted = subprocess.run(
                    [
                        sys.executable,
                        str(persister),
                        "--source",
                        str(source),
                        "--jrs-root",
                        str(jrs_root),
                        "--wallet",
                        wallet,
                    ],
                    check=False,
                    env=persist_env,
                )
                if persisted.returncode != 0:
                    raise RuntimeError(
                        f"persister failed returncode={persisted.returncode}"
                    )
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            snapshot_id = str(latest["snapshot_id"])
            source = Path(str(latest["raw_destination"])).resolve()
            expected = (
                jrs_root / "raw" / f"wallet={wallet}" / f"snapshot={snapshot_id}"
            ).resolve()
            if source != expected:
                raise RuntimeError(
                    f"latest destination mismatch: source={source} expected={expected}"
                )
            analysis = source / "analysis" / "full_ladder_history_v1"
            print(
                f"replay {index}/{len(wallets)} wallet={wallet} "
                f"snapshot={snapshot_id}",
                flush=True,
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(runner),
                    "--source",
                    str(source),
                    "--output",
                    str(analysis),
                    "--wallet",
                    wallet,
                    "--snapshot-id",
                    snapshot_id,
                ],
                check=False,
            )
            status = "complete" if completed.returncode == 0 else "failed"
            results.append(
                {
                    "wallet": wallet,
                    "snapshot_id": snapshot_id,
                    "source": str(source),
                    "analysis": str(analysis),
                    "status": status,
                    "returncode": completed.returncode,
                }
            )
        except Exception as exc:  # preserve every wallet result in the batch manifest
            results.append(
                {
                    "wallet": wallet,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        atomic_write_json(
            output,
            {
                "schema_version": "external_wallet_full_ladder_batch_v1",
                "generated_at_utc": utc_now(),
                "wallet_source": (
                    str(args.wallet_file.resolve())
                    if args.wallet_file
                    else str(args.peer_scan.resolve())
                ),
                "jrs_root": str(jrs_root),
                "runner": str(runner),
                "collector": str(collector) if args.collect_missing else None,
                "persister": str(persister) if args.collect_missing else None,
                "collection_end": args.collection_end,
                "results": results,
            },
        )

    failures = [row for row in results if row["status"] != "complete"]
    print(
        json.dumps(
            {
                "status": "complete" if not failures else "partial",
                "wallets": len(results),
                "complete": len(results) - len(failures),
                "failed": len(failures),
                "output": str(output),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
