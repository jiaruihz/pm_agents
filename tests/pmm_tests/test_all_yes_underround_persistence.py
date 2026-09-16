from scripts.analysis.market_structure_edge.research_all_yes_underround_persistence_v0 import (
    aggregate_threshold_telemetry,
    snapshot_files,
)


def test_snapshot_files_supports_micro_snapshot_glob(tmp_path):
    date_dir = tmp_path / "2026-06-14"
    date_dir.mkdir()
    keep = date_dir / "all_yes_micro_orderbook_snapshot_20260614_060019.jsonl.gz"
    skip = date_dir / "notes.txt"
    keep.write_text("", encoding="utf-8")
    skip.write_text("", encoding="utf-8")

    assert snapshot_files(tmp_path, "2026-06-14", "all_yes_micro_orderbook_snapshot_*.jsonl.gz") == [keep]


def test_aggregate_threshold_telemetry_counts_snapshots_and_observations():
    snapshots = [
        {
            "snapshot_ts_utc": "2026-06-14T06:00:00Z",
            "underround_threshold_telemetry": [
                {"threshold": 0.005, "candidate_count": 2, "top_candidates": [{"city": "A"}]},
                {"threshold": 0.01, "candidate_count": 1, "top_candidates": [{"city": "A"}]},
            ],
        },
        {
            "snapshot_ts_utc": "2026-06-14T06:05:00Z",
            "underround_threshold_telemetry": [
                {"threshold": 0.005, "candidate_count": 0, "top_candidates": []},
                {"threshold": 0.01, "candidate_count": 1, "top_candidates": [{"city": "B"}]},
            ],
        },
    ]

    result = aggregate_threshold_telemetry(snapshots, [0.005, 0.01])

    assert result == [
        {
            "threshold": 0.005,
            "snapshots_with_candidates": 1,
            "candidate_observations": 2,
            "max_candidates_in_snapshot": 2,
            "top_examples": [
                {"snapshot_ts_utc": "2026-06-14T06:00:00Z", "top_candidates": [{"city": "A"}]},
            ],
        },
        {
            "threshold": 0.01,
            "snapshots_with_candidates": 2,
            "candidate_observations": 2,
            "max_candidates_in_snapshot": 1,
            "top_examples": [
                {"snapshot_ts_utc": "2026-06-14T06:00:00Z", "top_candidates": [{"city": "A"}]},
                {"snapshot_ts_utc": "2026-06-14T06:05:00Z", "top_candidates": [{"city": "B"}]},
            ],
        },
    ]
