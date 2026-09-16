#!/usr/bin/env python3
"""Summarize a saved Polymarket position snapshot JSON."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: weather_polymarket_snapshot_summary.py SNAPSHOT_JSON")
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for key in ["all", "weather", "other"]:
        print(f"{key}: {payload.get(key)}")
    closed = payload.get("closed") or {}
    print(f"closed_all: {closed.get('all')}")
    print(f"closed_weather: {closed.get('weather')}")
    print("weather_by_market_date_tail:")
    for row in (payload.get("weather_by_market_date") or [])[-10:]:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
