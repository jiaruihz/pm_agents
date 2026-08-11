from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
import sqlite3


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_model_evaluation import tokyo_market_prior_adapter as module


def test_five_share_cost_uses_depth_and_weather_fee() -> None:
    book = {
        "summary": {
            "asks": [
                {"price": 0.40, "size": 2.0},
                {"price": 0.42, "size": 4.0},
            ]
        }
    }
    cash, effective = module.five_share_cost(book)
    expected = 2 * (0.40 + module.weather_fee_per_share(0.40)) + 3 * (
        0.42 + module.weather_fee_per_share(0.42)
    )
    assert cash == expected
    assert effective == expected / 5


def test_candidate_rows_uses_weather_head_and_rejects_delayed_replay(tmp_path) -> None:
    path = tmp_path / "bundles.jsonl"

    def bundle(event_id: str, decision: str) -> dict:
        return {
            "information_event": {
                "information_event_id": event_id,
                "first_seen_at_utc": "2026-08-02T01:07:00+00:00",
                "source_event_ts_utc": "2026-08-02T01:00:00+00:00",
                "pit_lineage_class": "collector_exact",
            },
            "model_output": {
                "model_id": module.MODEL_ID,
                "decision_ts_utc": decision,
                "metadata": {
                    "weather_probability_stay": 0.7,
                    "book_association": {
                        "probability_status": "two_sided_midpoint",
                        "best_bid": 0.28,
                        "best_ask": 0.32,
                        "book_snapshot_id": event_id,
                    },
                },
            },
            "signal_candidate": {
                "side": "NO",
                "bracket": "31",
                "condition_id": "condition",
                "market_p": 0.30,
            },
            "state_checkpoint": {"city": "Tokyo", "target_date": "2026-08-02"},
        }

    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                bundle("exact", "2026-08-02T01:07:05Z"),
                bundle("delayed", "2026-08-02T02:07:00Z"),
            )
        )
        + "\n"
    )
    rows, counts = module.candidate_rows(
        path,
        start_date="2026-08-01",
        end_date="2026-08-11",
        maximum_event_to_book_seconds=30,
    )
    assert len(rows) == 1
    assert rows[0]["model_no_probability"] == pytest.approx(0.3)
    assert rows[0]["market_no_probability"] == 0.3
    assert counts["causal_event_book_rows"] == 2
    assert counts["event_book_lag_rows"] == 1


def test_load_settlements_supports_source_grain_without_condition_id(tmp_path) -> None:
    path = tmp_path / "weather.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE settlement_outcomes (
            city TEXT,
            target_date TEXT,
            bracket TEXT,
            condition_id TEXT,
            final_price REAL,
            settlement_status TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO settlement_outcomes VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("Tokyo", "2026-08-10", "30", "condition-30", 1.0, "settled"),
            ("Tokyo", "2026-08-10", "31", None, 0.0, "settled"),
        ],
    )
    connection.commit()
    connection.close()

    by_condition, by_source_key = module.load_settlements(path)

    assert by_condition == {"condition-30": 0}
    assert by_source_key[("Tokyo", "2026-08-10", "30")] == 0
    assert by_source_key[("Tokyo", "2026-08-10", "31")] == 1
