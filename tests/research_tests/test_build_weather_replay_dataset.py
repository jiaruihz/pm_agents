from __future__ import annotations

import json
from pathlib import Path

from scripts.analysis.build_weather_replay_dataset_v1 import main


def test_dataset_cli_seals_all_four_target_date_blocks_idempotently(
    tmp_path: Path,
) -> None:
    rows = [
        {
            "row_id": f"row-{day}",
            "target_date": f"2026-01-0{day}",
            "label": day % 2,
            "label_available_at_utc": f"2026-01-0{day}T12:00:00Z",
            "measurement_namespace": "tmax",
            "available_at_utc": f"2026-01-0{day}T01:00:00Z",
        }
        for day in range(1, 5)
    ]
    rows_path = tmp_path / "rows.jsonl"
    rows_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    source_config = tmp_path / "sources.json"
    source_config.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "joined_clob_weather",
                        "path": str(rows_path),
                        "available_at_field": "available_at_utc",
                        "cutoff_utc": "2026-01-05T00:00:00Z",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    db = tmp_path / "weather.snapshot.db"
    db.write_bytes(b"sealed sqlite snapshot")
    output = tmp_path / "artifacts"
    argv = [
        "--rows-jsonl",
        str(rows_path),
        "--source-config",
        str(source_config),
        "--db-snapshot",
        str(db),
        "--db-schema-version",
        "21",
        "--catalog-build-id",
        "catalog-build",
        "--code-sha",
        "a" * 40,
        "--measurement-namespace",
        "tmax",
        "--label-value-field",
        "label",
        "--label-available-at-field",
        "label_available_at_utc",
        "--cutoff-utc",
        "2026-01-05T00:00:00Z",
        "--train-end",
        "2026-01-01",
        "--validation-end",
        "2026-01-02",
        "--test-end",
        "2026-01-03",
        "--output-dir",
        str(output),
    ]
    assert main(argv) == 0
    assert main(argv) == 0
    seals = list(output.glob("dataset_seal.*.json"))
    assert len(seals) == 1
    seal = json.loads(seals[0].read_text())
    assert seal["split_counts"] == {
        "train": 1,
        "validation": 1,
        "test": 1,
        "frozen_forward": 1,
    }
    assert seal["canonical_db_snapshot"]["sha256"]
    assert seal["source_file_snapshots"][0]["sha256"]
