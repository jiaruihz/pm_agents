from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEGACY_DIR = ROOT / "weather_data_feed_service/legacy_weather_predict"


def test_orderbook_budget_starts_with_first_book_request(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    monkeypatch.syspath_prepend(str(LEGACY_DIR))
    monkeypatch.syspath_prepend(str(ROOT))
    sys.modules.pop("paper_snapshot", None)
    paper_snapshot = importlib.import_module("paper_snapshot")

    assert (
        paper_snapshot.orderbook_budget_expired(
            None,
            60,
            now_monotonic=10_000,
        )
        is False
    )
    assert (
        paper_snapshot.orderbook_budget_expired(
            9_950,
            60,
            now_monotonic=10_000,
        )
        is False
    )
    assert (
        paper_snapshot.orderbook_budget_expired(
            9_900,
            60,
            now_monotonic=10_000,
        )
        is True
    )
