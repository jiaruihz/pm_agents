"""CLOB API client for prices and orderbooks."""

import asyncio
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from ..config import get_settings
from .http import HttpClient


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_price(data: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    bid = data.get("bestBid") or data.get("bid") or data.get("best_bid")
    ask = data.get("bestAsk") or data.get("ask") or data.get("best_ask")
    try:
        bid_f = float(bid) if bid is not None else None
    except Exception:
        bid_f = None
    try:
        ask_f = float(ask) if ask is not None else None
    except Exception:
        ask_f = None
    return bid_f, ask_f


def _parse_mid(data: Any) -> Optional[float]:
    if isinstance(data, dict):
        mid = data.get("mid") or data.get("midprice") or data.get("price") or data.get("value")
    else:
        mid = data
    try:
        return float(mid)
    except Exception:
        return None


async def fetch_midprice(token_id: str) -> Optional[Dict[str, Any]]:
    settings = get_settings()
    client = HttpClient()
    base = settings.clob_base_url
    fetched_at = _now_utc_iso()
    try:
        mid_json = await client.get_json(f"{base}/midprice", params={"token_id": token_id})
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        return None
    except httpx.RequestError:
        return None
    finally:
        await client.aclose()

    mid_val = _parse_mid(mid_json)
    if mid_val is None:
        return None
    return {
        "token_id": token_id,
        "fetched_at_utc": fetched_at,
        "mid": mid_val,
        "best_bid": None,
        "best_ask": None,
        "spread": None,
        "spread_pct_mid": None,
    }


async def fetch_price_and_book(
    token_id: str,
    top_n: int,
    archive_books: bool,
    depth: Optional[int] = None,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], Optional[str]]:
    settings = get_settings()
    client = HttpClient()
    base = settings.clob_base_url
    fetched_at = _now_utc_iso()
    archive_path: Optional[str] = None
    params = {"token_id": token_id}
    if depth is not None:
        params["depth"] = depth
    try:
        book_json = await client.get_json(f"{base}/book", params=params)
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None, [], None
        raise
    except httpx.RequestError:
        return None, [], None
    finally:
        await client.aclose()

    bids = book_json.get("bids") if isinstance(book_json, dict) else []
    asks = book_json.get("asks") if isinstance(book_json, dict) else []

    def _first_price(entries: Iterable[Any]) -> Optional[float]:
        for entry in entries:
            if isinstance(entry, dict):
                price = entry.get("price") or entry.get("p")
            elif isinstance(entry, (list, tuple)) and len(entry) >= 1:
                price = entry[0]
            else:
                continue
            try:
                return float(price)
            except Exception:
                continue
        return None

    best_bid = _first_price(bids)
    best_ask = _first_price(asks)
    mid_val = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
    spread = None
    spread_pct_mid = None
    if best_bid is not None and best_ask is not None:
        spread = best_ask - best_bid
        if mid_val and mid_val > 0:
            spread_pct_mid = spread / mid_val

    price_row = {
        "token_id": token_id,
        "fetched_at_utc": fetched_at,
        "mid": mid_val,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "spread_pct_mid": spread_pct_mid,
    }

    levels: List[Dict[str, Any]] = []
    def _normalize_side(side: str, entries: Iterable[Any]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for idx, entry in enumerate(entries):
            if idx >= top_n:
                break
            if isinstance(entry, dict):
                price = entry.get("price") or entry.get("p")
                size = entry.get("size") or entry.get("q") or entry.get("quantity")
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                price, size = entry[0], entry[1]
            else:
                continue
            try:
                price_f = float(price)
                size_f = float(size)
            except Exception:
                continue
            rows.append(
                {
                    "token_id": token_id,
                    "fetched_at_utc": fetched_at,
                    "side": side,
                    "level": idx + 1,
                    "price": price_f,
                    "size": size_f,
                }
            )
        return rows

    levels.extend(_normalize_side("bid", bids))
    levels.extend(_normalize_side("ask", asks))

    if archive_books:
        archive_path = archive_book(token_id, book_json, fetched_at)

    return price_row, levels, archive_path


def archive_book(token_id: str, book_json: Any, fetched_at_utc: str) -> str:
    settings = get_settings()
    date_str = fetched_at_utc.split("T")[0]
    dir_path = Path(settings.archive_dir) / token_id
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"{date_str}.json.gz"
    with gzip.open(file_path, "wt", encoding="utf-8") as f:
        json.dump(book_json, f)
    return str(file_path)


async def fetch_midprice_batch(token_ids: List[str]) -> List[Optional[Dict[str, Any]]]:
    tasks = [fetch_midprice(tid) for tid in token_ids]
    return await asyncio.gather(*tasks)


async def enrich_token_batch(
    token_ids: List[str],
    top_n: int,
    archive_books: bool,
    depth: Optional[int] = None,
) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]], Optional[str]]]:
    tasks = [fetch_price_and_book(tid, top_n=top_n, archive_books=archive_books, depth=depth) for tid in token_ids]
    return await asyncio.gather(*tasks)
