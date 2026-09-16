from pathlib import Path

from scripts.ops import low_price_yes_lottery_tiny_live as runner


def test_market_proxy_falls_back_to_canonical_weather_proxy(monkeypatch) -> None:
    monkeypatch.delenv("LOW_PRICE_YES_LOTTERY_MARKET_PROXY", raising=False)
    monkeypatch.setenv("WEATHER_DATA_FEED_MARKET_PROXY", "http://127.0.0.1:17890")

    assert runner.market_proxy_url() == "http://127.0.0.1:17890"


def test_strategy_proxy_can_explicitly_disable_shared_proxy(monkeypatch) -> None:
    monkeypatch.setenv("LOW_PRICE_YES_LOTTERY_MARKET_PROXY", "direct")
    monkeypatch.setenv("WEATHER_DATA_FEED_MARKET_PROXY", "http://127.0.0.1:17890")

    assert runner.market_proxy_url() == ""


def test_shadow_launcher_exports_normalized_market_proxy() -> None:
    root = Path(__file__).resolve().parents[2]
    launcher = (root / "scripts/ops/start_low_price_yes_lottery_tiny_live.sh").read_text(encoding="utf-8")

    assert 'source "$ROOT/scripts/ops/weather_market_proxy_env.sh"' in launcher
    assert "export LOW_PRICE_YES_LOTTERY_MARKET_PROXY" in launcher
    assert 'weather_export_market_proxy_env "$LOW_PRICE_YES_LOTTERY_MARKET_PROXY"' in launcher
