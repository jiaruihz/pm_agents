import json
from datetime import datetime, timezone

import pytest

from src.strategies.weather_city_probability_shadow.tokyo import (
    _jma_history,
    _market_prices,
    _official_history,
)


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_jma_history_uses_earliest_exact_first_seen(tmp_path):
    path = tmp_path / "jma.jsonl"
    base = {
        "city": "Tokyo", "source": "jma_amedas", "source_status": "ok",
        "target_date": "2026-08-01", "observation_time_utc": "2026-08-01T01:00:00Z",
        "temp_c": 32.1,
    }
    _write_jsonl(path, [
        {**base, "source_first_seen_at_utc": "2026-08-01T01:07:00Z", "payload_hash": "first"},
        {**base, "source_first_seen_at_utc": "2026-08-01T01:09:00Z", "payload_hash": "late_backfill"},
    ])
    rows = _jma_history(path, "2026-08-01", datetime(2026, 8, 1, 1, tzinfo=timezone.utc))
    assert len(rows) == 1
    assert rows[0]["payload_hash"] == "first"


def test_official_history_excludes_snapshots_fetched_after_decision(tmp_path):
    path = tmp_path / "2026-08-01" / "observations.jsonl"
    base = {
        "city": "Tokyo", "target_date": "2026-08-01",
        "last_obs_utc": "2026-08-01T01:00:00Z", "current_temp_c": 32,
        "running_max_c": 32,
    }
    _write_jsonl(path, [
        {**base, "fetched_at_utc": "2026-08-01T01:05:00Z", "raw_metar": "PIT"},
        {**base, "fetched_at_utc": "2026-08-01T01:20:00Z", "raw_metar": "FUTURE"},
    ])
    rows = _official_history(
        tmp_path, "2026-08-01", datetime(2026, 8, 1, 1, 10, tzinfo=timezone.utc)
    )
    assert [row["raw_metar"] for row in rows] == ["PIT"]


def test_no_book_reconstructs_complementary_yes_prices():
    prices = _market_prices({"summary": {"best_ask": .42, "best_bid": .40}})
    assert prices["no_mid"] == pytest.approx(.41)
    assert prices["yes_mid"] == pytest.approx(.59)
    assert prices["yes_ask"] == pytest.approx(.60)
