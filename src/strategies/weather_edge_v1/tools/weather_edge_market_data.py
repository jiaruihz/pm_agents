from __future__ import annotations

import asyncio
import gzip
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import httpx

from src.platform.clients import clob as clob_client
from src.platform.clients.polymarket_gamma import PolymarketGammaClient
from src.strategies.rule_lawyer.services.common import normalize_json_list


DEFAULT_DATA_ROOT = Path("runtime/weather_edge_v1/market_data")
DEFAULT_CLOB_BASE = "https://clob.polymarket.com"

WEATHER_CITIES: Dict[str, Dict[str, str]] = {
    "Chicago": {"slug": "chicago"},
    "Miami": {"slug": "miami"},
    "Phoenix": {"slug": "phoenix"},
    "Austin": {"slug": "austin"},
    "Boston": {"slug": "boston"},
    "NYC": {"slug": "nyc"},
    "LA": {"slug": "los-angeles"},
    "London": {"slug": "london"},
    "Paris": {"slug": "paris"},
    "Madrid": {"slug": "madrid"},
    "Warsaw": {"slug": "warsaw"},
    "Tokyo": {"slug": "tokyo"},
    "Shanghai": {"slug": "shanghai"},
    "Seoul": {"slug": "seoul"},
    "Beijing": {"slug": "beijing"},
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def date_range(start: date, end: date) -> List[date]:
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def weather_event_slug(city_slug: str, target_date: date) -> str:
    month = target_date.strftime("%B").lower()
    return f"highest-temperature-in-{city_slug}-on-{month}-{target_date.day}-{target_date.year}"


def safe_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl_gz(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "at", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def file_size_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def collect_market_data_status(data_root: Path = DEFAULT_DATA_ROOT, pid_file: Path = Path("runtime/weather_edge_market_data_live.pid")) -> Dict[str, Any]:
    data_root = Path(data_root)
    live_files = sorted((data_root / "live_orderbook").rglob("*.jsonl.gz")) if (data_root / "live_orderbook").exists() else []
    pid = ""
    live_running = False
    if pid_file.exists():
        pid = pid_file.read_text(encoding="utf-8").strip()
        if pid:
            live_running = Path(f"/proc/{pid}").exists()
    summary_path = data_root / "backfill_30d_summary.json"
    summary: Dict[str, Any] = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = {"error": "failed to parse backfill_30d_summary.json"}
    return {
        "data_root": str(data_root),
        "generated_at_utc": utc_now_iso(),
        "gamma_event_files": count_files(data_root / "gamma_events"),
        "clob_price_history_files": count_files(data_root / "clob_price_history"),
        "live_orderbook_files": len(live_files),
        "live_orderbook_bytes": sum(file_size_bytes(path) for path in live_files),
        "latest_live_orderbook_file": str(live_files[-1]) if live_files else "",
        "live_pid": pid,
        "live_running": live_running,
        "backfill_30d_summary": summary,
    }


def token_ids_from_event(event: Dict[str, Any]) -> List[str]:
    token_ids: List[str] = []
    for market in event.get("markets", []) or []:
        if not isinstance(market, dict):
            continue
        token_ids.extend(str(x) for x in normalize_json_list(market.get("clobTokenIds")) if str(x))
    return list(dict.fromkeys(token_ids))


def normalize_event_summary(event: Dict[str, Any], *, city: str, target_date: str, slug: str) -> Dict[str, Any]:
    markets = event.get("markets") if isinstance(event.get("markets"), list) else []
    return {
        "city": city,
        "target_date": target_date,
        "slug": slug,
        "event_id": str(event.get("id") or event.get("eventId") or ""),
        "event_slug": str(event.get("slug") or ""),
        "title": str(event.get("title") or ""),
        "active": bool(event.get("active", True)),
        "closed": bool(event.get("closed", False)),
        "market_count": len(markets),
        "token_ids": token_ids_from_event(event),
    }


@dataclass
class WeatherEdgeMarketData:
    data_root: Path = DEFAULT_DATA_ROOT
    clob_base_url: str = DEFAULT_CLOB_BASE

    def __post_init__(self) -> None:
        self.data_root = Path(self.data_root)
        self.gamma = PolymarketGammaClient()

    def event_path(self, target_date: str, city: str) -> Path:
        return self.data_root / "gamma_events" / target_date / f"{city}.json"

    def history_path(self, target_date: str, city: str, token_id: str) -> Path:
        return self.data_root / "clob_price_history" / target_date / city / f"{token_id[-20:]}.json"

    def manifest_path(self) -> Path:
        return self.data_root / "manifest.jsonl"

    def fetch_event(self, *, city: str, target_date: date, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        cfg = WEATHER_CITIES[city]
        date_str = target_date.strftime("%Y-%m-%d")
        slug = weather_event_slug(cfg["slug"], target_date)
        path = self.event_path(date_str, city)
        if use_cache and path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            return cached.get("event") if isinstance(cached.get("event"), dict) else None

        event = self.gamma.fetch_event_by_id_or_slug(slug=slug)
        payload = {
            "fetched_at_utc": utc_now_iso(),
            "city": city,
            "target_date": date_str,
            "slug": slug,
            "found": isinstance(event, dict) and bool(event),
            "event": event or {},
            "summary": normalize_event_summary(event, city=city, target_date=date_str, slug=slug) if event else {},
        }
        safe_json_write(path, payload)
        append_jsonl(self.manifest_path(), {"type": "gamma_event", **payload["summary"], "found": payload["found"], "path": str(path)})
        return event if isinstance(event, dict) and event else None

    async def fetch_price_history(
        self,
        *,
        token_id: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        fidelity: int = 60,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"market": token_id, "fidelity": str(fidelity)}
        if start_ts is not None and end_ts is not None:
            params.update({"startTs": str(start_ts), "endTs": str(end_ts)})
        else:
            params["interval"] = "all"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{self.clob_base_url}/prices-history", params=params)
            resp.raise_for_status()
            payload = resp.json()
        return {
            "token_id": token_id,
            "fetched_at_utc": utc_now_iso(),
            "params": params,
            "history": payload.get("history", []) if isinstance(payload, dict) else payload,
        }

    async def backfill_price_history_for_event(
        self,
        *,
        city: str,
        target_date: date,
        event: Dict[str, Any],
        overwrite: bool = False,
        fidelity: int = 60,
        concurrency: int = 8,
    ) -> Dict[str, int]:
        date_str = target_date.strftime("%Y-%m-%d")
        token_ids = token_ids_from_event(event)
        stats = {"tokens": len(token_ids), "fetched": 0, "cached": 0, "errors": 0}
        lock = asyncio.Lock()
        sem = asyncio.Semaphore(max(1, concurrency))

        async def fetch_one(token_id: str) -> None:
            out = self.history_path(date_str, city, token_id)
            if out.exists() and not overwrite:
                async with lock:
                    stats["cached"] += 1
                return
            async with sem:
                try:
                    history = await self.fetch_price_history(token_id=token_id, fidelity=fidelity)
                    safe_json_write(out, history)
                    append_jsonl(
                        self.manifest_path(),
                        {
                            "type": "clob_price_history",
                            "city": city,
                            "target_date": date_str,
                            "token_id": token_id,
                            "points": len(history.get("history", []) or []),
                            "path": str(out),
                            "fetched_at_utc": history["fetched_at_utc"],
                        },
                    )
                    async with lock:
                        stats["fetched"] += 1
                except Exception as exc:
                    append_jsonl(
                        self.manifest_path(),
                        {
                            "type": "clob_price_history_error",
                            "city": city,
                            "target_date": date_str,
                            "token_id": token_id,
                            "error": f"{type(exc).__name__}: {exc}",
                            "fetched_at_utc": utc_now_iso(),
                        },
                    )
                    async with lock:
                        stats["errors"] += 1

        await asyncio.gather(*(fetch_one(token_id) for token_id in token_ids))
        return stats

    async def backfill_30d(
        self,
        *,
        end_date: date,
        days: int = 30,
        cities: Optional[List[str]] = None,
        overwrite: bool = False,
        fidelity: int = 60,
        history_concurrency: int = 8,
    ) -> Dict[str, Any]:
        selected = cities or list(WEATHER_CITIES.keys())
        start_date = end_date - timedelta(days=max(0, days - 1))
        summary: Dict[str, Any] = {
            "started_at_utc": utc_now_iso(),
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "cities": selected,
            "events_found": 0,
            "events_missing": 0,
            "price_history_fetched": 0,
            "price_history_cached": 0,
            "errors": 0,
        }
        for d in date_range(start_date, end_date):
            for city in selected:
                event = self.fetch_event(city=city, target_date=d, use_cache=not overwrite)
                if not event:
                    summary["events_missing"] += 1
                    continue
                summary["events_found"] += 1
                stats = await self.backfill_price_history_for_event(
                    city=city,
                    target_date=d,
                    event=event,
                    overwrite=overwrite,
                    fidelity=fidelity,
                    concurrency=history_concurrency,
                )
                summary["price_history_fetched"] += stats["fetched"]
                summary["price_history_cached"] += stats["cached"]
                summary["errors"] += stats["errors"]
        summary["finished_at_utc"] = utc_now_iso()
        safe_json_write(self.data_root / "backfill_30d_summary.json", summary)
        return summary

    def discover_events(
        self,
        *,
        dates: Iterable[date],
        cities: Optional[List[str]] = None,
        use_cache: bool = True,
    ) -> List[Dict[str, Any]]:
        selected = cities or list(WEATHER_CITIES.keys())
        events: List[Dict[str, Any]] = []
        for d in dates:
            for city in selected:
                event = self.fetch_event(city=city, target_date=d, use_cache=use_cache)
                if event:
                    cfg = WEATHER_CITIES[city]
                    date_str = d.strftime("%Y-%m-%d")
                    events.append(
                        {
                            "city": city,
                            "target_date": date_str,
                            "slug": weather_event_slug(cfg["slug"], d),
                            "event": event,
                            "summary": normalize_event_summary(
                                event,
                                city=city,
                                target_date=date_str,
                                slug=weather_event_slug(cfg["slug"], d),
                            ),
                        }
                    )
        return events


def _levels_to_book(level_rows: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, float]]]:
    bids: List[Dict[str, float]] = []
    asks: List[Dict[str, float]] = []
    for row in level_rows:
        item = {
            "level": int(row.get("level") or 0),
            "price": float(row.get("price") or 0.0),
            "size": float(row.get("size") or 0.0),
        }
        if row.get("side") == "bid":
            bids.append(item)
        elif row.get("side") == "ask":
            asks.append(item)
    return {"bids": bids, "asks": asks}


async def capture_live_orderbooks(
    *,
    token_ids: List[str],
    token_meta: Dict[str, Any],
    out_jsonl_gz: Path,
    duration_sec: int,
    interval_sec: float,
    top_n: int,
    concurrency: int = 25,
) -> Dict[str, Any]:
    started = time.time()
    samples = 0
    errors = 0
    while True:
        now = time.time()
        if duration_sec > 0 and (now - started) >= duration_sec:
            break
        try:
            sem = asyncio.Semaphore(max(1, concurrency))

            async def fetch_one(token_id: str):
                async with sem:
                    return await clob_client.fetch_price_and_book(token_id, top_n=top_n, archive_books=False)

            results = await asyncio.gather(*(fetch_one(token_id) for token_id in token_ids), return_exceptions=True)
            orderbooks: Dict[str, Any] = {}
            for token_id, result in zip(token_ids, results):
                if isinstance(result, Exception):
                    orderbooks[token_id] = {
                        "token_id": token_id,
                        "error": f"{type(result).__name__}: {result}",
                    }
                    continue
                price_row, level_rows, archive_path = result
                price_row = dict(price_row or {})
                book = _levels_to_book(level_rows or [])
                orderbooks[token_id] = {
                    "token_id": token_id,
                    "best_bid": price_row.get("best_bid"),
                    "best_ask": price_row.get("best_ask"),
                    "mid": price_row.get("mid"),
                    "spread": price_row.get("spread"),
                    "spread_pct_mid": price_row.get("spread_pct_mid"),
                    "archive_path": archive_path,
                    "bids": book["bids"],
                    "asks": book["asks"],
                }
            append_jsonl_gz(
                out_jsonl_gz,
                {
                    "type": "weather_edge_orderbook_snapshot",
                    "ts_event": now,
                    "fetched_at_utc": utc_now_iso(),
                    "token_meta": token_meta,
                    "orderbooks": orderbooks,
                },
            )
            samples += 1
        except Exception as exc:
            errors += 1
            append_jsonl_gz(
                out_jsonl_gz,
                {
                    "type": "weather_edge_orderbook_error",
                    "ts_event": now,
                    "fetched_at_utc": utc_now_iso(),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
        await asyncio.sleep(max(0.1, interval_sec))
    return {
        "out_jsonl_gz": str(out_jsonl_gz),
        "tokens": len(token_ids),
        "samples": samples,
        "errors": errors,
        "started_at_utc": datetime.fromtimestamp(started, tz=timezone.utc).isoformat(timespec="seconds"),
        "finished_at_utc": utc_now_iso(),
    }
