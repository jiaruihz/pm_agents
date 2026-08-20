#!/usr/bin/env python3
"""Generate a source-frozen Chain.Love bounty Harness run configuration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.weather_agent_harness.scenarios.chainlove_bounty import (
    DEFAULT_CATEGORIES,
    DEFAULT_NETWORKS,
    build_bundle,
    capture_collision_snapshot,
    snapshot_sha256,
    write_bundle,
)


def _git(repo_path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repo", default="Chain-Love/chain-love")
    parser.add_argument("--repo-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--network", action="append", dest="networks")
    parser.add_argument("--category", action="append", dest="categories")
    parser.add_argument("--reward-address")
    parser.add_argument(
        "--extended",
        action="store_true",
        help="Add the stale_sweep and deferred_recheck work orders (post-2026-08 seams).",
    )
    parser.add_argument(
        "--category-modifiers",
        help='JSON dict of current weekly category modifiers, e.g. \'{"security": 1.5}\'.',
    )
    parser.add_argument(
        "--deferred-queue",
        type=Path,
        help="Path to the deferred-candidates queue JSON (read by deferred_recheck, written by evidence_review).",
    )
    parser.add_argument(
        "--snapshot-json",
        type=Path,
        help="Reuse an existing open-PR snapshot instead of calling gh.",
    )
    args = parser.parse_args()

    repo_path = args.repo_path.resolve()
    base_sha = _git(repo_path, "rev-parse", "origin/main")
    if args.snapshot_json:
        snapshot = json.loads(args.snapshot_json.read_text(encoding="utf-8"))
    else:
        snapshot = capture_collision_snapshot(args.repo)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = args.output_dir / "open_pr_snapshot.json"
    snapshot_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    bundle = build_bundle(
        run_id=args.run_id,
        repo=args.repo,
        repo_path=repo_path,
        base_sha=base_sha,
        snapshot_path=snapshot_path,
        snapshot_sha256=snapshot_sha256(snapshot),
        snapshot_captured_at_utc=snapshot["captured_at_utc"],
        networks=tuple(args.networks or DEFAULT_NETWORKS),
        categories=tuple(args.categories or DEFAULT_CATEGORIES),
        reward_address=args.reward_address,
        extended=args.extended,
        category_modifiers=json.loads(args.category_modifiers) if args.category_modifiers else None,
        deferred_queue_path=args.deferred_queue,
    )
    write_bundle(bundle, args.output_dir)
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "base_sha": base_sha,
                "open_pr_count": snapshot["open_pr_count"],
                "models": {
                    item.name: item.requested_model for item in bundle["roles"]
                },
                "work_orders": [item.work_order_id for item in bundle["work_orders"]],
                "usage_required": all(item.require_usage for item in bundle["roles"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
