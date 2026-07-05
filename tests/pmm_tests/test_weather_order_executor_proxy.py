from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/ops/weather_order_executor.py"


def load_executor_module():
    spec = importlib.util.spec_from_file_location("weather_order_executor_for_proxy_test", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configure_market_proxy_env_prefers_data_feed_proxy(monkeypatch):
    mod = load_executor_module()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("WEATHER_DATA_FEED_MARKET_PROXY", "http://127.0.0.1:7890")

    status = mod.configure_market_proxy_env()

    assert status["mode"] == "explicit_market_proxy"
    assert status["source"] == "WEATHER_DATA_FEED_MARKET_PROXY"
    assert status["proxy"] == "http://127.0.0.1:7890"
    for key in mod.PROXY_ENV_KEYS:
        assert mod.os.environ[key] == "http://127.0.0.1:7890"


def test_configure_market_proxy_env_direct_clears_proxy(monkeypatch):
    mod = load_executor_module()
    for key in mod.PROXY_ENV_KEYS:
        monkeypatch.setenv(key, "http://127.0.0.1:7897")
    monkeypatch.setenv("WEATHER_EXECUTOR_MARKET_PROXY", "direct")

    status = mod.configure_market_proxy_env()

    assert status["mode"] == "explicit_direct"
    assert status["source"] == "WEATHER_EXECUTOR_MARKET_PROXY"
    assert status["proxy"] == ""
    for key in mod.PROXY_ENV_KEYS:
        assert key not in mod.os.environ
