from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills/pmm-market-data-fetcher/scripts/fetch_market_data.py"
SPEC = importlib.util.spec_from_file_location("pmm_market_data_fetcher_skill", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
fetcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetcher)


def test_openai_key_never_uses_alipay_default_host(monkeypatch) -> None:
    monkeypatch.delenv("ALIPAY_API_KEY", raising=False)
    monkeypatch.delenv("ALIPAY_HOST_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("OPENAI_MODEL", "configured-model")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    settings = fetcher._translation_settings()

    assert settings is not None
    assert settings["provider"] == "openai_compatible"
    assert settings["base_url"] == "https://api.openai.com"
    assert "antchat.alipay.com" not in settings["base_url"]


def test_openai_translation_requires_explicit_model(monkeypatch) -> None:
    monkeypatch.delenv("ALIPAY_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    assert fetcher._translation_settings() is None


def test_legacy_fetcher_rejects_market_url_instead_of_treating_it_as_event() -> None:
    with pytest.raises(ValueError, match="event URLs only"):
        fetcher._parse_event_slug("https://polymarket.com/market/some-market")


def test_event_url_parser() -> None:
    assert (
        fetcher._parse_event_slug("https://polymarket.com/event/some-event")
        == "some-event"
    )
