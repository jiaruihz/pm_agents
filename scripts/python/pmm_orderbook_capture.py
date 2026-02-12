from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from pmm.backtest.recorder import LiveRecorder, convert_jsonl_to_scenario


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pmm_orderbook_capture",
        description="Independent capability: capture real Polymarket orderbook and persist reusable local datasets.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="Capture live orderbook stream and save scenario+jsonl")
    cap.add_argument("--tokens", required=True, help="Comma separated token IDs")
    cap.add_argument("--duration", type=int, default=120, help="Capture duration in seconds")
    cap.add_argument("--interval", type=float, default=1.0, help="Sampling interval in seconds")
    cap.add_argument("--warmup", type=float, default=4.0, help="Warmup seconds")
    cap.add_argument("--max-levels", type=int, default=20, help="Top levels per side")
    cap.add_argument(
        "--out-scenario",
        default="pmm/backtest/.artifacts/recorded/recorded_live.json",
        help="Output scenario JSON path",
    )
    cap.add_argument(
        "--out-jsonl",
        default="",
        help="Output jsonl path (default: same basename as scenario)",
    )
    cap.add_argument("--initial-usdc", type=float, default=100.0, help="Initial USDC")
    cap.add_argument(
        "--initial-positions-json",
        default="",
        help='Initial positions JSON, e.g. \'{"YES":50,"NO":50}\'',
    )

    conv = sub.add_parser("convert", help="Convert captured jsonl into replay scenario JSON")
    conv.add_argument("--jsonl", required=True, help="Input jsonl path")
    conv.add_argument("--out-scenario", required=True, help="Output scenario path")
    conv.add_argument("--tokens", default="", help="Optional token IDs override")
    conv.add_argument("--initial-usdc", type=float, default=100.0, help="Initial USDC")
    conv.add_argument(
        "--initial-positions-json",
        default="",
        help='Initial positions JSON, e.g. \'{"YES":50,"NO":50}\'',
    )
    return p


def _parse_positions(raw: str) -> dict:
    if not raw or not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("initial_positions_json must be a JSON object")
    return value


def _parse_tokens(raw: str) -> list[str]:
    return [x.strip() for x in str(raw).split(",") if x.strip()]


def main() -> None:
    args = _parser().parse_args()
    if args.command == "capture":
        token_ids = _parse_tokens(args.tokens)
        initial_positions = _parse_positions(args.initial_positions_json)
        recorder = LiveRecorder(
            token_ids=token_ids,
            interval=args.interval,
            max_levels=args.max_levels,
        )
        result = asyncio.run(
            recorder.run(
                duration_sec=args.duration,
                output_file=args.out_scenario,
                output_jsonl=(args.out_jsonl.strip() or None),
                warmup_sec=args.warmup,
                initial_usdc=args.initial_usdc,
                initial_positions=initial_positions,
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "convert":
        initial_positions = _parse_positions(args.initial_positions_json)
        token_ids = _parse_tokens(args.tokens) if args.tokens.strip() else None
        result = convert_jsonl_to_scenario(
            jsonl_file=args.jsonl,
            output_file=args.out_scenario,
            token_ids=token_ids,
            initial_usdc=args.initial_usdc,
            initial_positions=initial_positions,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
