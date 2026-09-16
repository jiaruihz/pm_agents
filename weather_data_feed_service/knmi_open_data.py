"""Persistent KNMI Schiphol Open Data collector."""

from __future__ import annotations

import argparse
import json
import time
from collections import deque
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
TEN_MINUTE_SECONDS = 600.0


def _previous_records(output_dir: Path) -> list[dict[str, Any]]:
    latest = read_json(output_dir / "latest.json", {})
    records = latest.get("records") if isinstance(latest, dict) else None
    if isinstance(records, list) and records:
        return [dict(row) for row in records if isinstance(row, dict)]
    history_paths = sorted(output_dir.glob("????-??-??/knmi_observations.jsonl"))
    if not history_paths:
        return []
    history_path = history_paths[-1]
    with history_path.open("r", encoding="utf-8", errors="ignore") as handle:
        last_lines = deque(handle, maxlen=1)
    if not last_lines:
        return []
    try:
        row = json.loads(last_lines[0])
    except json.JSONDecodeError:
        return []
    return [row] if isinstance(row, dict) else []


def adaptive_poll_delay(
    now: datetime,
    *,
    hot_window_start_sec: float = 205.0,
    hot_window_end_sec: float = 260.0,
    hot_interval_sec: float = 10.0,
    cold_interval_sec: float = 300.0,
) -> float:
    """Return a delay aligned to KNMI's observed +03:25..+04:20 release window."""
    phase = (
        (now.minute % 10) * 60
        + now.second
        + now.microsecond / 1_000_000
    )
    if hot_window_start_sec <= phase <= hot_window_end_sec:
        return max(1.0, hot_interval_sec)
    until_hot = (hot_window_start_sec - phase) % TEN_MINUTE_SECONDS
    return max(1.0, min(cold_interval_sec, until_hot or TEN_MINUTE_SECONDS))


def _trim_revisions(revisions: dict[str, str], limit: int = 1100) -> dict[str, str]:
    """Keep enough filename revisions for KNMI's documented seven-day repair window."""
    return dict(sorted(revisions.items())[-limit:])


def collect_once(
    *,
    output_dir: Path,
    timeout_sec: float = 15.0,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / "state.json"
    state = read_json(state_path, {})
    last_filename = str(state.get("last_success_filename") or "")
    seen_revisions = {
        str(key): str(value)
        for key, value in dict(state.get("seen_revisions") or {}).items()
    }
    result = fetch_knmi_open_data(
        settings=HighFrequencyFetchSettings(timeout_sec=timeout_sec),
        last_filename=last_filename,
        seen_revisions=seen_revisions,
    )
    new_rows = [dict(row) for row in result.records]
    previous_rows = _previous_records(output_dir)
    rows = new_rows or (
        previous_rows if result.status == "no_new_revision" else []
    )
    if new_rows:
        newest_obs = max(
            str(row.get("observation_time_utc") or "") for row in new_rows
        )
        rows = [
            row
            for row in new_rows
            if str(row.get("observation_time_utc") or "") == newest_obs
        ]
    now = datetime.now(timezone.utc).isoformat()
    filename = str(result.metadata.get("filename") or last_filename)

    if new_rows:
        append_jsonl(
            output_dir / now[:10] / "knmi_observations.jsonl",
            new_rows,
        )

    payload = {
        "schema_version": "weather_knmi_open_data_payload_v1",
        "generated_at_utc": now,
        "producer": "weather_data_feed_service.knmi_open_data",
        "status": result.status,
        "error": result.error,
        "filename": filename,
        "rows": len(rows),
        "append_rows": len(new_rows),
        "records": rows,
        "metadata": result.metadata,
    }
    write_json(output_dir / "latest.json", payload)
    updated_revisions = _trim_revisions(
        {
            **seen_revisions,
            **{
                str(key): str(value)
                for key, value in dict(
                    result.metadata.get("seen_revisions") or {}
                ).items()
            },
        }
    )
    success_filenames = [
        str(item.get("filename") or "")
        for item in result.metadata.get("changed_files", [])
        if isinstance(item, dict)
    ]
    newest_success = max(
        [last_filename, *success_filenames],
        default=last_filename,
    )
    write_json(
        state_path,
        {
            "schema_version": "weather_knmi_open_data_state_v2",
            "updated_at_utc": now,
            "last_attempt_status": result.status,
            "last_attempt_filename": filename,
            "last_success_filename": newest_success,
            "seen_revisions": updated_revisions,
            "last_success_at_utc": (
                now
                if result.status == "ok" and new_rows
                else state.get("last_success_at_utc")
            ),
        },
    )
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--hot-interval-sec", type=float, default=10.0)
    parser.add_argument("--cold-interval-sec", type=float, default=300.0)
    parser.add_argument("--hot-window-start-sec", type=float, default=205.0)
    parser.add_argument("--hot-window-end-sec", type=float, default=260.0)
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
        delay = adaptive_poll_delay(
            datetime.now(timezone.utc),
            hot_window_start_sec=args.hot_window_start_sec,
            hot_window_end_sec=args.hot_window_end_sec,
            hot_interval_sec=args.hot_interval_sec,
            cold_interval_sec=args.cold_interval_sec,
        )
        time.sleep(delay)


if __name__ == "__main__":
    raise SystemExit(main())
