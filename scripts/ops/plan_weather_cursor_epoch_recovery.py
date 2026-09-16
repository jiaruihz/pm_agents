#!/usr/bin/env python3
"""Plan and seal (only with --write-receipt) an append-only cursor rollover."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from weather_data_feed.cursor_epochs import (
    CursorEpoch,
    materializer_successor_state,
    plan_rollover,
    write_immutable_receipt,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predecessor", required=True, type=Path)
    parser.add_argument("--successor", required=True, type=Path)
    parser.add_argument("--predecessor-boundary", required=True, type=Path)
    parser.add_argument("--successor-boundary", required=True, type=Path)
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--successor-state-out", type=Path)
    args = parser.parse_args()
    predecessor = CursorEpoch(**json.loads(args.predecessor.read_text()))
    successor = CursorEpoch(**json.loads(args.successor.read_text()))
    receipt = plan_rollover(
        predecessor, successor,
        predecessor_boundary=args.predecessor_boundary.read_bytes().splitlines(keepends=True),
        successor_boundary=args.successor_boundary.read_bytes().splitlines(keepends=True),
    )
    if args.write_receipt:
        write_immutable_receipt(Path(successor.receipt_path), receipt)
    if args.successor_state_out:
        if not args.write_receipt:
            raise ValueError("--successor-state-out requires --write-receipt")
        if args.successor_state_out.resolve() != Path(successor.state_path).resolve():
            raise ValueError("successor state output must equal successor.state_path")
        state = materializer_successor_state(
            predecessor,
            successor,
            receipt_path=Path(successor.receipt_path),
        )
        args.successor_state_out.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=args.successor_state_out.parent,
                prefix=f".{args.successor_state_out.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(state, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, args.successor_state_out)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
