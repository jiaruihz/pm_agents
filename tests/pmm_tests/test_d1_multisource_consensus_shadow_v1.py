import gzip
import json
from pathlib import Path

from datetime import datetime, timezone

from scripts.ops.d1_multisource_consensus_shadow_v1 import build_cycle, version_history


def write_jsonl_gz(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def test_version_history_reads_only_dated_shards(tmp_path) -> None:
    dated = tmp_path / "2026-07-28"
    dated.mkdir()
    row = {
        "city": "Amsterdam",
        "forecast_target_date": "2026-07-29",
        "model_label": "ECMWF",
        "available_at_utc": "2026-07-28T17:00:00Z",
    }
    (dated / "forecast_versions.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )
    (tmp_path / "forecast_versions.jsonl").write_text(
        json.dumps({**row, "model_label": "ROOT_DUPLICATE"}) + "\n",
        encoding="utf-8",
    )

    history = version_history(
        tmp_path, datetime(2026, 7, 28, 18, tzinfo=timezone.utc)
    )

    assert set(history) == {("Amsterdam", "2026-07-29", "ECMWF")}


def test_build_cycle_uses_forecast_available_before_endpoint_book(tmp_path) -> None:
    versions = tmp_path / "forecast_versions.jsonl"
    version_rows = []
    for model, value in (("ECMWF", 90.0), ("GFS", 86.0), ("ICON", 87.0)):
        version_rows.append(
            {
                "city": "Amsterdam",
                "forecast_target_date": "2026-07-29",
                "model_label": model,
                "forecast_max_f": value,
                "timezone_name": "Europe/Amsterdam",
                "available_at_utc": "2026-07-28T17:00:00+00:00",
            }
        )
    version_rows.append(
        {
            "city": "Amsterdam",
            "forecast_target_date": "2026-07-29",
            "model_label": "ECMWF",
            "forecast_max_f": 70.0,
            "timezone_name": "Europe/Amsterdam",
            "available_at_utc": "2026-07-28T19:00:00+00:00",
        }
    )
    versions.write_text(
        "".join(json.dumps(row) + "\n" for row in version_rows),
        encoding="utf-8",
    )
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "training_cutoff": "2026-07-07",
                "records": [
                    {
                        "city": "Amsterdam",
                        "model_label": model,
                        "bias_correction_f": 0.0,
                    }
                    for model in ("ECMWF", "GFS", "ICON")
                ],
            }
        ),
        encoding="utf-8",
    )
    books = tmp_path / "books.jsonl.gz"
    write_jsonl_gz(
        books,
        [
            {
                "city": "Amsterdam",
                "event_date": "2026-07-29",
                "outcome": "no",
                "bracket": bracket,
                "fetched_at_utc": fetched,
                "status": "ok",
                "summary": {"best_ask": ask, "ask_size": 10},
            }
            for bracket, fetched, ask in (
                ("20 or below", "2026-07-28T18:00:00+00:00", 0.9),
                ("25", "2026-07-28T18:00:01+00:00", 0.5),
                ("30 or higher", "2026-07-28T18:00:02+00:00", 0.8),
            )
        ],
    )

    payload = build_cycle(
        book_path=books,
        versions_path=versions,
        policy_path=policy,
    )

    assert payload["signal_funnel"]["endpoint_legs"] == 2
    high = next(row for row in payload["records"] if row["leg"] == "high_no")
    low = next(row for row in payload["records"] if row["leg"] == "low_no")
    assert high["assigned_corrected_f"] == 90.0
    assert high["consensus_corrected_f"] == 87.0
    assert high["directional_no_score_f"] == 3.0
    assert low["directional_no_score_f"] == -3.0
    assert high["decision_asof_utc"] == "2026-07-28T18:00:00+00:00"
    assert high["forecast_available_max_utc"] < high["decision_asof_utc"]
    assert payload["orders_submitted"] == 0
