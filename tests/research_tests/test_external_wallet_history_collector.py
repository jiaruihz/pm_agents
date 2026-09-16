from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = ROOT / "scripts/analysis/wallet_weather"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "collect_external_wallet_weather_history_v1",
    SCRIPT_DIR / "collect_external_wallet_weather_history_v1.py",
)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def weather_row(index: int) -> dict[str, object]:
    return {
        "conditionId": f"condition-{index}",
        "title": "Will the highest temperature in Test City be 20°C?",
    }


def test_closed_positions_cap_is_explicit_supporting_truncation(monkeypatch) -> None:
    calls = 0

    def fake_get_list(*args, **kwargs):
        nonlocal calls
        calls += 1
        return [weather_row(calls * 50 + index) for index in range(50)]

    monkeypatch.setattr(collector, "get_list", fake_get_list)

    rows, truncated = collector.collect_closed_positions("0x" + "1" * 40)

    assert truncated is True
    assert len(rows) == 10_000
    assert calls == 200


def test_closed_positions_short_page_is_complete(monkeypatch) -> None:
    pages = [
        [weather_row(index) for index in range(50)],
        [weather_row(50 + index) for index in range(17)],
    ]

    monkeypatch.setattr(
        collector,
        "get_list",
        lambda *args, **kwargs: pages.pop(0),
    )

    rows, truncated = collector.collect_closed_positions("0x" + "2" * 40)

    assert truncated is False
    assert len(rows) == 67
