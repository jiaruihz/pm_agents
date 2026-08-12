from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.ops.weather_korea_first_seen_collector import (
    LATEST_SCHEMA_FINGERPRINT,
    LATEST_SCHEMA_VERSION,
    build_parser,
    build_producer_identity,
    read_appended_rows,
)


ROOT = Path(__file__).resolve().parents[2]


def _write_row(path, sequence: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sequence": sequence}) + "\n", encoding="utf-8")


def test_read_appended_rows_follows_dated_shard_rollover(tmp_path) -> None:
    root = tmp_path / "live_cross_observations"
    first = root / "2026-08-09" / "high_frequency_observations.jsonl"
    _write_row(first, 1)

    rows, cursor, audit = read_appended_rows(root, None)
    assert [row["sequence"] for row in rows] == [1]
    assert audit["physical_path"] == str(first)

    with first.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"sequence": 2}) + "\n")
    rows, cursor, _audit = read_appended_rows(root, cursor)
    assert [row["sequence"] for row in rows] == [2]

    second = root / "2026-08-10" / "high_frequency_observations.jsonl"
    _write_row(second, 3)
    rows, cursor, audit = read_appended_rows(root, cursor)
    assert [row["sequence"] for row in rows] == [3]
    assert cursor["source_path"] == str(second)
    assert audit["reset_reason"] == "source_path_changed"


def test_korea_collector_producer_identity_matches_latest_contract(tmp_path) -> None:
    args = build_parser().parse_args(
        [
            "--source-jsonl",
            str(tmp_path / "source"),
            "--forecast-root",
            str(tmp_path / "forecast"),
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )
    identity = build_producer_identity(
        args,
        {
            "cities": ["Busan", "Seoul"],
            "source": "amos_runway",
            "path_window_minutes": [15, 60],
            "market_capture": {},
            "mode": "research_shadow",
        },
    )

    assert identity["runtime_instance_id"]
    assert identity["output_schema_version"] == LATEST_SCHEMA_VERSION
    assert identity["output_schema_fingerprint"] == LATEST_SCHEMA_FINGERPRINT
    assert identity["loaded_module_sha256"]


def test_korea_collector_direct_script_entrypoint_builds_identity(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ops/weather_korea_first_seen_collector.py"),
            "--config",
            str(ROOT / "configs/weather/korea_first_seen_collector_v1.json"),
            "--source-jsonl",
            str(tmp_path / "missing-source"),
            "--forecast-root",
            str(tmp_path / "forecast"),
            "--output-dir",
            str(tmp_path / "output"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == LATEST_SCHEMA_VERSION
    assert payload["producer_identity"]["runtime_instance_id"]
