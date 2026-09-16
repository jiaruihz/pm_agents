import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "weather_data_feed_service/legacy_weather_predict/paper_snapshot.py"


def load_paper_snapshot():
    spec = importlib.util.spec_from_file_location("paper_snapshot_entry_scope", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_orderbook_scope_status_uses_each_entry_label(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WEATHER_DATA_FEED_OUTPUT_ROOT", str(tmp_path / "out"))
    monkeypatch.setenv("WEATHER_DATA_FEED_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("WEATHER_DATA_FEED_ROOT", str(ROOT))
    paper_snapshot = load_paper_snapshot()

    targets = {("31", "yes"), ("32", "no")}
    cache = {
        "yes-31": {"status": "ok", "summary": {"best_ask": 0.4}},
        "no-32": {"status": "ok", "summary": {"best_ask": 0.6}},
    }

    assert paper_snapshot.orderbook_for_entry(
        cache, "yes-31", label="31", outcome="yes", targets=targets
    )["status"] == "ok"
    assert paper_snapshot.orderbook_for_entry(
        cache, "yes-30", label="30", outcome="yes", targets=targets
    )["status"] == "orderbook_scope_skipped"
    assert paper_snapshot.orderbook_for_entry(
        cache, "no-31", label="31", outcome="no", targets=targets
    )["status"] == "orderbook_scope_skipped"
    assert paper_snapshot.orderbook_for_entry(
        cache, "no-32", label="32", outcome="no", targets=targets
    )["status"] == "ok"
