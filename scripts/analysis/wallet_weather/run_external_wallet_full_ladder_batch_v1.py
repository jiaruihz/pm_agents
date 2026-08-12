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
    parser.add_argument("--wallet-file", type=Path, required=True)
    parser.add_argument("--jrs-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--runner",
        type=Path,
        default=Path(__file__).with_name(
            "research_external_wallet_full_ladder_history_v1.py"
        ),
    )
    args = parser.parse_args()

    wallets = read_wallets(args.wallet_file.resolve())
    jrs_root = args.jrs_root.resolve()
    runner = args.runner.resolve()
    output = args.output.resolve()
    results: list[dict[str, Any]] = []

    for index, wallet in enumerate(wallets, start=1):
        latest_path = jrs_root / "latest" / f"{wallet}.json"
        try:
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
                "wallet_file": str(args.wallet_file.resolve()),
                "jrs_root": str(jrs_root),
                "runner": str(runner),
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
