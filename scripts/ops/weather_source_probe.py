#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_theta_no_v1.tools.source_probe import probe_city_sources, render_markdown_table


DEFAULT_CITIES = ["shanghai", "seoul", "tokyo", "hong_kong", "taipei", "osaka", "singapore", "dubai", "doha"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe configured weather data sources and render a table.")
    parser.add_argument("--cities", nargs="*", default=DEFAULT_CITIES)
    parser.add_argument("--local-date", default=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    rows: List[dict] = []
    for city in [str(x).strip().lower() for x in args.cities if str(x).strip()]:
        rows.extend(probe_city_sources(city, args.local_date))

    if args.format == "json":
        rendered = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    else:
        rendered = render_markdown_table(rows) + "\n"

    if args.out.strip():
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        print(str(out_path))
        return 0

    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
