#!/usr/bin/env python3
"""Shared market-proxy helpers for Polymarket HTTP clients."""

from __future__ import annotations

import os
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


def production_market_proxy_url(route_key: str = "default") -> str:
    spec = load_production_spec()
    key = str(route_key or "default").strip()
    routes = {route.route_key: route for route in spec.market_proxy_routes}
    try:
        route = routes[key]
    except KeyError as exc:
        raise ValueError(f"unknown market proxy route_key: {key}") from exc
    return normalize_proxy(route.proxy_url)


def market_proxy_url(
    explicit_proxy: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    default: str | None = None,
    route_key: str = "default",
) -> str:
    if explicit_proxy is not None:
        return normalize_proxy(explicit_proxy)
    if route_key != "default":
        return production_market_proxy_url(route_key=route_key)
    source = env if env is not None else os.environ
    for key in PROXY_ENV_KEYS:
        proxy = normalize_proxy(source.get(key))
        if proxy:
            return proxy
    return (
        normalize_proxy(default)
        if default is not None
        else production_market_proxy_url()
    )


def market_httpx_client(
    explicit_proxy: str | None = None,
    *,
    timeout: float | httpx.Timeout = 5.0,
    env: Mapping[str, str] | None = None,
    default_proxy: str | None = None,
    route_key: str = "default",
    **kwargs: Any,
) -> httpx.Client:
    proxy = market_proxy_url(
        explicit_proxy,
        env=env,
        default=default_proxy,
        route_key=route_key,
    )
    return httpx.Client(proxy=proxy or None, timeout=timeout, trust_env=False, **kwargs)
