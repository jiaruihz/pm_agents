#!/usr/bin/env python3
"""Shared market-proxy helpers for Polymarket HTTP clients."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import httpx


DEFAULT_MARKET_PROXY = "http://127.0.0.1:7890"
PROXY_ENV_KEYS = (
    "WEATHER_DATA_FEED_MARKET_PROXY",
    "WEATHER_PREDICT_MARKET_PROXY",
    "POLYMARKET_PROXY_URL",
)


def normalize_proxy(value: str | None) -> str:
    raw = str(value or "").strip()
    return "" if raw.lower() in {"", "direct", "none", "off", "0"} else raw


def market_proxy_url(
    explicit_proxy: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    default: str = DEFAULT_MARKET_PROXY,
) -> str:
    if explicit_proxy is not None:
        return normalize_proxy(explicit_proxy)
    source = env if env is not None else os.environ
    for key in PROXY_ENV_KEYS:
        proxy = normalize_proxy(source.get(key))
        if proxy:
            return proxy
    return normalize_proxy(default)


def market_httpx_client(
    explicit_proxy: str | None = None,
    *,
    timeout: float | httpx.Timeout = 5.0,
    env: Mapping[str, str] | None = None,
    default_proxy: str = DEFAULT_MARKET_PROXY,
    **kwargs: Any,
) -> httpx.Client:
    proxy = market_proxy_url(explicit_proxy, env=env, default=default_proxy)
    return httpx.Client(proxy=proxy or None, timeout=timeout, trust_env=False, **kwargs)
