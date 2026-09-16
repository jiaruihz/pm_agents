#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.live_state import pause_live, read_live_state, resume_live, status_text


DEFAULT_STATE_DIR = ROOT / "runtime" / "weather_edge_v1" / "live_cycle"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage weather live trading pause/resume state.")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--json", action="store_true", help="Print machine-readable state JSON.")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show current weather live state.")
    status.add_argument("--json", action="store_true", dest="command_json", help="Print machine-readable state JSON.")

    pause = sub.add_parser("pause", help="Pause live order submission.")
    pause.add_argument("--reason", default="", help="Human-readable pause reason.")
    pause.add_argument("--source", default="manual_cli", help="State change source label.")
    pause.add_argument("--json", action="store_true", dest="command_json", help="Print machine-readable state JSON.")

    resume = sub.add_parser("resume", help="Resume live order submission.")
    resume.add_argument("--json", action="store_true", dest="command_json", help="Print machine-readable state JSON.")
    return parser


def main() -> int:
    args = _parser().parse_args()
    state_dir = Path(args.state_dir)
    if args.command == "pause":
        state = pause_live(state_dir, reason=str(args.reason), source=str(args.source))
    elif args.command == "resume":
        state = resume_live(state_dir)
    else:
        state = read_live_state(state_dir)

    if args.json or bool(getattr(args, "command_json", False)):
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(status_text(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
