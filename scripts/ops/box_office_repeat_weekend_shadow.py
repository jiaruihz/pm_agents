#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.box_office_repeat_weekend.runtime import run_shadow  # noqa: E402


def parse_args() -> argparse.Namespace:
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parser = argparse.ArgumentParser(
        description="Read-only repeat-weekend book and industry-forecast shadow"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT
        / "runtime/box_office_repeat_weekend_shadow/runs"
        / run_stamp,
    )
    parser.add_argument(
        "--industry-artifact",
        type=Path,
        default=ROOT
        / "docs/analysis/2026-08/generated/box_office_repeat_weekend_v3",
    )
    parser.add_argument("--shares", type=float, nargs="+", default=[5.0, 10.0, 25.0])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_shadow(
        args.out.resolve(),
        shares_grid=tuple(args.shares),
        industry_artifact_dir=args.industry_artifact.resolve(),
    )
    print(json.dumps({**summary, "output_dir": str(args.out.resolve())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
