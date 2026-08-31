from __future__ import annotations

import ast
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"


def _parse_event_slug(market_url: str) -> str:
    parsed = urlparse(market_url.strip())
    path = parsed.path.strip("/")
    if not path:
        raise ValueError(f"invalid market_url: {market_url}")
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "event":
        return parts[1]
    raise ValueError(
        "this legacy snapshot tool accepts event URLs only; use "
        "polymarket-market-rule-audit for a market slug or condition id"
    )


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    return []


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _translation_settings() -> dict[str, str] | None:
    alipay_key = os.getenv("ALIPAY_API_KEY")
    if alipay_key:
        return {
            "provider": "alipay_openai_compatible",
            "api_key": alipay_key,
            "base_url": os.getenv("ALIPAY_HOST_URL", "https://antchat.alipay.com").rstrip("/"),
            "model": os.getenv("ALIPAY_MODEL", "Qwen3-VL-235B-A22B-Thinking"),
        }

    openai_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_MODEL")
    if openai_key and openai_model:
        return {
            "provider": "openai_compatible",
            "api_key": openai_key,
            "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/"),
            "model": openai_model,
        }
    return None


def _translate_to_zh(text: str, settings: dict[str, str] | None) -> str:
    content = (text or "").strip()
    if not content or settings is None:
        return ""

    endpoint = f"{settings['base_url']}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {settings['api_key']}", "Content-Type": "application/json"}
    payload = {
        "model": settings["model"],
        "temperature": 0.0,
        "messages": [
            {
                "role": "system",
                "content": "你是专业翻译。请把输入英文完整翻译成简体中文，保留事实和语义，不要做摘要，不要添加额外解释。",
            },
            {"role": "user", "content": content},
        ],
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(endpoint, headers=headers, json=payload)
            if resp.status_code != 200:
                return ""
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            translated = (message.get("content") or "").strip()
            return translated
    except Exception:
        return ""


def _normalize_levels(levels: Any, level_limit: int) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    if not levels:
        return out
    for lvl in levels:
        if isinstance(lvl, dict):
            p = _to_float(lvl.get("price"), 0.0)
            s = _to_float(lvl.get("size"), 0.0)
        elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
            p = _to_float(lvl[0], 0.0)
            s = _to_float(lvl[1], 0.0)
        else:
            continue
        if p > 0 and s > 0:
            out.append({"price": p, "size": s})
        if len(out) >= level_limit:
            break
    return out


def _fetch_orderbook(client: httpx.Client, token_id: str, level_limit: int) -> dict[str, Any]:
    candidates = [
        (f"{CLOB_BASE}/book", {"token_id": token_id}),
        (f"{CLOB_BASE}/book", {"asset_id": token_id}),
        (f"{CLOB_BASE}/book/{token_id}", None),
    ]
    for url, params in candidates:
        try:
            resp = client.get(url, params=params)
            if resp.status_code != 200:
                continue
            data = resp.json()
            bids = _normalize_levels(data.get("bids"), level_limit)
            asks = _normalize_levels(data.get("asks"), level_limit)
            best_bid = bids[0]["price"] if bids else 0.0
            best_ask = asks[0]["price"] if asks else 0.0
            spread = (best_ask - best_bid) if best_bid > 0 and best_ask > 0 else 0.0
            return {
                "token_id": token_id,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "spread": spread,
                "bids": bids,
                "asks": asks,
            }
        except Exception:
            continue
    return {
        "token_id": token_id,
        "best_bid": 0.0,
        "best_ask": 0.0,
        "spread": 0.0,
        "bids": [],
        "asks": [],
        "error": "orderbook_unavailable",
    }


def _fetch_event_by_slug(client: httpx.Client, slug: str) -> dict[str, Any]:
    # Prefer direct slug query.
    resp = client.get(f"{GAMMA_BASE}/events", params={"slug": slug})
    if resp.status_code == 200:
        rows = resp.json()
        if isinstance(rows, list) and rows:
            return rows[0]

    # Fallback: scan active events quickly by pages.
    offset = 0
    limit = 200
    for _ in range(10):
        resp = client.get(
            f"{GAMMA_BASE}/events",
            params={"active": "true", "closed": "false", "limit": limit, "offset": offset},
        )
        if resp.status_code != 200:
            break
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            break
        for event in rows:
            if str(event.get("slug", "")).strip("/") == slug:
                return event
        if len(rows) < limit:
            break
        offset += limit

    raise RuntimeError(f"event not found for slug={slug}")


def _fetch_market(client: httpx.Client, market_id: str) -> dict[str, Any]:
    resp = client.get(f"{GAMMA_BASE}/markets/{market_id}")
    if resp.status_code != 200:
        raise RuntimeError(f"market fetch failed id={market_id} status={resp.status_code}")
    return resp.json()


def _make_output_path(slug: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path("runtime/pmm/market_snapshots")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"market_snapshot_{slug}_{ts}.json"


def run(
    market_url: str,
    out: str,
    include_orderbook: bool,
    orderbook_limit: int,
    translate_zh: bool,
) -> None:
    slug = _parse_event_slug(market_url)
    timeout = httpx.Timeout(connect=8.0, read=25.0, write=20.0, pool=20.0)

    translation_settings = _translation_settings() if translate_zh else None
    translation_mode = "disabled"
    if translate_zh:
        translation_mode = "enabled" if translation_settings else "skipped_missing_provider_config"

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        event = _fetch_event_by_slug(client, slug)
        market_refs = event.get("markets") or []

        markets: list[dict[str, Any]] = []
        orderbooks: dict[str, dict[str, Any]] = {}

        for market_ref in market_refs:
            market_id = str(market_ref.get("id") or "").strip()
            if not market_id:
                continue
            market = _fetch_market(client, market_id)
            token_ids = [str(x) for x in _as_list(market.get("clobTokenIds")) if str(x)]
            outcomes = [str(x) for x in _as_list(market.get("outcomes"))]
            outcome_prices = [_to_float(x, 0.0) for x in _as_list(market.get("outcomePrices"))]
            market_desc = market.get("description") or ""

            market_obj = {
                "market_id": market_id,
                "question": market.get("question"),
                "description": market_desc,
                "description_zh": _translate_to_zh(market_desc, translation_settings),
                "active": bool(market.get("active")),
                "closed": bool(market.get("closed")),
                "end_date": market.get("endDate"),
                "best_bid": _to_float(market.get("bestBid"), 0.0),
                "best_ask": _to_float(market.get("bestAsk"), 0.0),
                "last_trade_price": _to_float(market.get("lastTradePrice"), 0.0),
                "spread": _to_float(market.get("spread"), 0.0),
                "outcomes": outcomes,
                "outcome_prices": outcome_prices,
                "token_ids": token_ids,
            }
            markets.append(market_obj)

            if include_orderbook:
                for token_id in token_ids:
                    if token_id in orderbooks:
                        continue
                    orderbooks[token_id] = _fetch_orderbook(client, token_id, orderbook_limit)

    snapshot = {
        "schema_version": "v1",
        "meta": {
            "source_url": market_url,
            "slug": slug,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "include_orderbook": include_orderbook,
            "orderbook_limit": orderbook_limit,
            "translation": {
                "enabled": bool(translate_zh),
                "mode": translation_mode,
                "provider": (translation_settings or {}).get("provider", ""),
                "host": (translation_settings or {}).get("base_url", ""),
                "model": (translation_settings or {}).get("model", ""),
            },
        },
        "event": {
            "event_id": str(event.get("id") or ""),
            "slug": event.get("slug"),
            "ticker": event.get("ticker"),
            "title": event.get("title"),
            "description": event.get("description"),
            "description_zh": (
                _translate_to_zh(str(event.get("description") or ""), translation_settings)
            ),
            "resolution_source": event.get("resolutionSource"),
            "rules": event.get("rules"),
            "end_date": event.get("endDate"),
            "active": bool(event.get("active")),
            "closed": bool(event.get("closed")),
            "market_count": len(markets),
        },
        "markets": markets,
        "orderbooks": orderbooks,
    }

    out_path = Path(out).expanduser() if out else _make_output_path(slug)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[market_data_fetcher] slug={slug} markets={len(markets)} "
        f"orderbooks={len(orderbooks)} out={out_path}"
    )


def _parse_bool(value: str) -> bool:
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch a legacy PMM event snapshot by URL")
    parser.add_argument("--market-url", required=True, help="Polymarket event URL")
    parser.add_argument("--out", default="", help="Output JSON path")
    parser.add_argument(
        "--include-orderbook",
        default="true",
        help="Whether to fetch orderbook (true/false)",
    )
    parser.add_argument(
        "--orderbook-limit",
        type=int,
        default=20,
        help="Orderbook levels per side",
    )
    parser.add_argument(
        "--translate-zh",
        default="false",
        help="Whether to add Chinese translation fields (true/false)",
    )
    args = parser.parse_args()
    run(
        market_url=args.market_url,
        out=args.out,
        include_orderbook=_parse_bool(args.include_orderbook),
        orderbook_limit=max(1, args.orderbook_limit),
        translate_zh=_parse_bool(args.translate_zh),
    )
