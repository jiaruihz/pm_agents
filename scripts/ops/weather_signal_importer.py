#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.execution_pipeline import DEFAULT_RUNTIME_ROOT, import_signals


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import weather_edge_v1 paper decisions into immutable signals.")
    parser.add_argument("inputs", nargs="+", help="Input JSONL files from weather-predict or weather_edge_paper.")
    parser.add_argument(
        "--out",
        default=str(DEFAULT_RUNTIME_ROOT / "signals" / "signals.jsonl"),
        help="Output signal JSONL.",
    )
    parser.add_argument("--source-system", default="weather-predict")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = import_signals(
        input_paths=[Path(x) for x in args.inputs],
        out_path=Path(args.out),
        source_system=args.source_system,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
