#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_theta_no_v1.tools.source_backtest import run_source_backtest, write_backtest_outputs


def _default_out_dir() -> Path:
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%dT%H%M%S%z")
    return ROOT / "runtime" / "reports" / f"weather_source_backtest_{stamp}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backtest weather forecast sources against settlement truth and METAR intraday paths."
    )
    parser.add_argument(
        "--watch-path",
        default=str(ROOT / "src" / "strategies" / "weather_theta_no_v1" / "plan" / "watch"),
        help="Directory containing weather watch JSON and archive files.",
    )
    parser.add_argument("--cities", nargs="*", default=[], help="Optional city keys to include.")
    parser.add_argument("--date-from", default="", help="Inclusive local-date lower bound, YYYY-MM-DD.")
    parser.add_argument("--date-to", default="", help="Inclusive local-date upper bound, YYYY-MM-DD.")
    parser.add_argument("--metar-hours", type=int, default=48, help="Historical METAR lookback window in hours.")
    parser.add_argument("--proxy-url", default="", help="Optional proxy URL for outbound HTTP requests.")
    parser.add_argument(
        "--out-dir",
        default=str(_default_out_dir()),
        help="Output directory for summary.json and report.md.",
    )
    args = parser.parse_args()

    result = run_source_backtest(
        watch_path=Path(args.watch_path).expanduser().resolve(),
        city_keys=args.cities,
        date_from=str(args.date_from or "").strip(),
        date_to=str(args.date_to or "").strip(),
        metar_hours=max(24, int(args.metar_hours)),
        proxy_url=str(args.proxy_url or "").strip(),
    )
    outputs = write_backtest_outputs(result, Path(args.out_dir).expanduser().resolve())
    print(outputs["report_path"])
    print(outputs["summary_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
