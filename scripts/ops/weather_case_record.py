#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.case_record import CaseRecordWriter, case_record_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or append human-readable weather case records.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    path_p = sub.add_parser("path")
    path_p.add_argument("--city-key", required=True)
    path_p.add_argument("--local-date", required=True)

    append_p = sub.add_parser("append")
    append_p.add_argument("--payload-file", required=True)

    args = parser.parse_args()
    if args.cmd == "path":
        print(case_record_path(args.city_key, args.local_date))
        return 0

    payload_path = Path(args.payload_file)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("payload file must be a JSON object")
    writer = CaseRecordWriter()
    out_path = writer.append_entry(payload)
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
