#!/usr/bin/env python3
"""Shared market-proxy helpers for Polymarket HTTP clients."""

from __future__ import annotations

import os
import json
from collections.abc import Mapping
from typing import Any

import httpx

from src.strategies.runtime.production import load_production_spec


PROXY_ENV_KEYS = (
    "WEATHER_DATA_FEED_MARKET_PROXY",
)


def normalize_proxy(value: str | None) -> str:
    raw = str(value or "").strip()
    return "" if raw.lower() in {"", "direct", "none", "off", "0"} else raw


def production_market_proxy_url() -> str:
    spec = load_production_spec()
    state_path = spec.market_proxy_state_path
    if not state_path.exists():
        return normalize_proxy(spec.market_proxy_default_url)
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "proxy_url" not in payload:
        raise ValueError(f"invalid market proxy state: {state_path}")
    return normalize_proxy(str(payload["proxy_url"]))


def market_proxy_url(
    explicit_proxy: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    default: str | None = None,
) -> str:
    if explicit_proxy is not None:
        return normalize_proxy(explicit_proxy)
    source = env if env is not None else os.environ
    for key in PROXY_ENV_KEYS:
        proxy = normalize_proxy(source.get(key))
        if proxy:
            return proxy
    return normalize_proxy(default) if default is not None else production_market_proxy_url()


def market_httpx_client(
    explicit_proxy: str | None = None,
    *,
    timeout: float | httpx.Timeout = 5.0,
    env: Mapping[str, str] | None = None,
    default_proxy: str | None = None,
    **kwargs: Any,
) -> httpx.Client:
    proxy = market_proxy_url(explicit_proxy, env=env, default=default_proxy)
    return httpx.Client(proxy=proxy or None, timeout=timeout, trust_env=False, **kwargs)
