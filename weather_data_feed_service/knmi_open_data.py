"""Persistent KNMI Schiphol Open Data collector."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from weather_data_feed.high_frequency_observation_sources import (
    HighFrequencyFetchSettings,
)
from weather_data_feed.knmi_open_data import fetch_knmi_open_data
from weather_data_feed_service.cli import DEFAULT_RUNTIME_ROOT
from weather_data_feed_service.io_utils import append_jsonl, read_json, write_json


DEFAULT_OUTPUT_DIR = DEFAULT_RUNTIME_ROOT / "output" / "knmi_open_data"


def collect_once(
    *,
    output_dir: Path,
    timeout_sec: float = 15.0,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.json"
    state = read_json(state_path, {})
    last_filename = str(state.get("last_success_filename") or "")
    result = fetch_knmi_open_data(
        settings=HighFrequencyFetchSettings(timeout_sec=timeout_sec),
        last_filename=last_filename,
    )
    rows = [dict(row) for row in result.records]
    now = datetime.now(timezone.utc).isoformat()
    filename = str(result.metadata.get("filename") or last_filename)

    if rows:
        append_jsonl(output_dir / "knmi_observations.jsonl", rows)
        day = str(rows[0].get("target_date") or now[:10])
        append_jsonl(output_dir / day / "knmi_observations.jsonl", rows)

    payload = {
        "schema_version": "weather_knmi_open_data_payload_v1",
        "generated_at_utc": now,
        "producer": "weather_data_feed_service.knmi_open_data",
        "status": result.status,
        "error": result.error,
        "filename": filename,
        "rows": len(rows),
        "records": rows,
        "metadata": result.metadata,
    }
    write_json(output_dir / "latest.json", payload)
    write_json(
        state_path,
        {
            "schema_version": "weather_knmi_open_data_state_v1",
            "updated_at_utc": now,
            "last_attempt_status": result.status,
            "last_attempt_filename": filename,
            "last_success_filename": (
                filename if result.status == "ok" and rows else last_filename
            ),
            "last_success_at_utc": (
                now
                if result.status == "ok" and rows
                else state.get("last_success_at_utc")
            ),
        },
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--interval-sec", type=float, default=300.0)
    parser.add_argument("--timeout-sec", type=float, default=15.0)
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    while True:
        try:
            payload = collect_once(
                output_dir=output_dir,
                timeout_sec=max(1.0, args.timeout_sec),
            )
            print(
                json.dumps(
                    {key: value for key, value in payload.items() if key != "records"},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps(
                    {
                        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                        "status": "fetch_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(max(60.0, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
