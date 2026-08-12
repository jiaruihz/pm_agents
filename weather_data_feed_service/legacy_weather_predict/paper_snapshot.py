"""
Paper trade snapshot recorder.
每次运行拉取当前所有可交易事件的完整快照：最优模型概率 + PM盘口价 + edge。
按城市自动判定所处入场窗口（pre_t24 / t24 / between / t12 / post_t12）。
按城市选最优模型（GFS/ECMWF），基于 v4 校准 RMSE 对比。
所有信号都记录（不只记 edge>5% 的），方便事后用任意策略/阈值回算。
记录完整交易截面字段：time_bucket, METAR 实况, 模型周期, 市场 ID 等。

用法: python3 paper_snapshot.py [--target-date 2026-04-30] [--shares 10]
默认: 自动扫描今天和明天的事件，取距结算 4-50h 内的。
"""
import gzip
import hashlib
import json, math, os, sys, re, time
import subprocess
import urllib.parse
import httpx
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, date, timezone
from xml.etree import ElementTree

PROJECT_DIR = Path(__file__).resolve().parent
for candidate in (
    os.environ.get("WEATHER_DATA_FEED_ROOT"),
    PROJECT_DIR.parents[1],
    PROJECT_DIR.parent / "pm_agent",
    PROJECT_DIR.parent / "pm_agents",
):
    if not candidate:
        continue
    candidate_path = Path(candidate)
    if (candidate_path / "weather_data_feed").exists() and str(candidate_path) not in sys.path:
        sys.path.insert(0, str(candidate_path))
        break

sys.path.insert(0, str(PROJECT_DIR))
from city_pools import CITY_POOL_BY_CITY, TRADING_T1_CITIES, city_pool, eligible_for_paper_order
from pm_edge_compare import CITIES, PROXY, CACHE_DIR, _extract_bracket_label, compute_error_distribution, compute_bracket_probs
from calibration_backtest import load_wu_obs, load_gfs_daily
from weather_data_feed import (
    SNAPSHOT_SCHEMA_VERSION,
    city_local_date as data_feed_city_local_date,
    city_local_datetime,
    city_scan_dates as data_feed_city_scan_dates,
    city_timezone_name,
    local_settle_utc,
    load_city_configs,
    parse_now_utc,
    resolve_market_end_utc,
    unique_city_scan_dates as data_feed_unique_city_scan_dates,
)
from weather_data_feed.forecast_hourly_curves import (
    build_curve_row,
    build_hourly_curve,
    summarize_source_models,
    write_forecast_hourly_curve_capture,
)
from weather_data_feed.assigned_forecast_models import CITY_MODEL
from weather_data_feed.information_events import canonical_json_hash
from weather_data_feed.market_ladder_lineage import annotate_market_ladder_snapshot
from weather_data_feed.market_book_contract import (
    materialize_orderbook_capture,
    utc_now_text,
)
from weather_data_feed.observation_cache import (
    index_observation_cache,
    load_observation_cache,
    parse_utc as parse_observation_utc,
)
from weather_data_feed.source_lineage import producer_build_id
from weather_data_feed.forecast_history import forecast_hourly_daily_max_local

PM_GAMMA_URL = "https://gamma-api.polymarket.com"
PM_CLOB_URL = "https://clob.polymarket.com"
DEFAULT_RUNTIME_DIR = PROJECT_DIR.parent / "runtime"
OUTPUT_ROOT = Path(os.environ.get("WEATHER_DATA_FEED_OUTPUT_ROOT", DEFAULT_RUNTIME_DIR / "output"))
CACHE_ROOT = Path(os.environ.get("WEATHER_DATA_FEED_CACHE_ROOT", DEFAULT_RUNTIME_DIR / "cache"))
OUTPUT_DIR = OUTPUT_ROOT / "paper_snapshots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PARTIAL_OUTPUT_DIR = OUTPUT_ROOT / "paper_snapshots_partial"
PARTIAL_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ORDERBOOK_OUTPUT_DIR = OUTPUT_ROOT / "orderbook_snapshots"
ORDERBOOK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
FORECAST_CURVE_ROOT = Path(
    os.environ.get(
        "WEATHER_DATA_FEED_FORECAST_CURVE_ROOT",
        OUTPUT_ROOT / "forecast_hourly_curves",
    )
)
PRODUCER_BUILD_ID, PRODUCER_BUILD_ID_BASIS = producer_build_id(Path(__file__).resolve().parents[2])
PM_HTTP_TIMEOUT = httpx.Timeout(
    connect=float(os.environ.get("WEATHER_DATA_FEED_PM_CONNECT_TIMEOUT_SEC", "2.0")),
    read=float(os.environ.get("WEATHER_DATA_FEED_PM_READ_TIMEOUT_SEC", "4.0")),
    write=float(os.environ.get("WEATHER_DATA_FEED_PM_WRITE_TIMEOUT_SEC", "2.0")),
    pool=float(os.environ.get("WEATHER_DATA_FEED_PM_POOL_TIMEOUT_SEC", "4.0")),
)
WEATHER_HTTP_TIMEOUT = httpx.Timeout(5.0, connect=2.0, read=5.0, write=2.0, pool=2.0)
PM_HTTP_LIMITS = httpx.Limits(
    max_connections=int(os.environ.get("WEATHER_DATA_FEED_PM_MAX_CONNECTIONS", "16")),
    max_keepalive_connections=int(os.environ.get("WEATHER_DATA_FEED_PM_MAX_KEEPALIVE", "8")),
)
WEATHER_HTTP_LIMITS = httpx.Limits(max_connections=8, max_keepalive_connections=0)
DEFAULT_ORDERBOOK_SCOPE = os.environ.get("WEATHER_DATA_FEED_ORDERBOOK_SCOPE", "strategy_live")
DEFAULT_ORDERBOOK_BUDGET_SEC = float(os.environ.get("WEATHER_DATA_FEED_ORDERBOOK_BUDGET_SEC", "30"))
DEFAULT_ORDERBOOK_WORKERS = int(os.environ.get("WEATHER_DATA_FEED_ORDERBOOK_WORKERS", "1"))
DEFAULT_ORDERBOOK_RETRIES = int(os.environ.get("WEATHER_DATA_FEED_ORDERBOOK_RETRIES", "0"))
ORDERBOOK_BATCH_MAX_TOKENS = int(
    os.environ.get("WEATHER_DATA_FEED_ORDERBOOK_BATCH_MAX_TOKENS", "500")
)
ORDERBOOK_BATCH_RETRIES = int(
    os.environ.get("WEATHER_DATA_FEED_ORDERBOOK_BATCH_RETRIES", "2")
)
ORDERBOOK_BATCH_RETRY_BACKOFF_SEC = float(
    os.environ.get("WEATHER_DATA_FEED_ORDERBOOK_BATCH_RETRY_BACKOFF_SEC", "0.25")
)
ORDERBOOK_CURL_TIMEOUT_SEC = float(os.environ.get("WEATHER_DATA_FEED_ORDERBOOK_CURL_TIMEOUT_SEC", "4.0"))
ORDERBOOK_CURL_CONNECT_TIMEOUT_SEC = float(os.environ.get("WEATHER_DATA_FEED_ORDERBOOK_CURL_CONNECT_TIMEOUT_SEC", "2.0"))
WEATHER_CURL_TIMEOUT_SEC = float(os.environ.get("WEATHER_DATA_FEED_WEATHER_CURL_TIMEOUT_SEC", "5.0"))
WEATHER_CURL_CONNECT_TIMEOUT_SEC = float(os.environ.get("WEATHER_DATA_FEED_WEATHER_CURL_CONNECT_TIMEOUT_SEC", "2.0"))
FORECAST_CURVE_CACHE_MAX_AGE_SEC = float(
    os.environ.get("WEATHER_DATA_FEED_FORECAST_CURVE_CACHE_MAX_AGE_SEC", "43200")
)
FORECAST_LIVE_REFRESH_SEC = float(
    os.environ.get("WEATHER_DATA_FEED_FORECAST_LIVE_REFRESH_SEC", "3600")
)
FORECAST_PROXY = os.environ.get("WEATHER_DATA_FEED_FORECAST_PROXY") or None
FORECAST_CURVE_CACHE_MAX_FILES = int(
    os.environ.get("WEATHER_DATA_FEED_FORECAST_CURVE_CACHE_MAX_FILES", "8")
)
PM_CURL_TIMEOUT_SEC = float(os.environ.get("WEATHER_DATA_FEED_PM_CURL_TIMEOUT_SEC", "5.0"))
PM_CURL_CONNECT_TIMEOUT_SEC = float(os.environ.get("WEATHER_DATA_FEED_PM_CURL_CONNECT_TIMEOUT_SEC", "2.0"))
ALLOW_EMPTY_SNAPSHOT = os.environ.get("WEATHER_DATA_FEED_ALLOW_EMPTY_SNAPSHOT", "0") == "1"
MIN_SNAPSHOT_RECORDS = int(os.environ.get("WEATHER_DATA_FEED_MIN_SNAPSHOT_RECORDS", "100"))
MIN_SNAPSHOT_CITIES = int(os.environ.get("WEATHER_DATA_FEED_MIN_SNAPSHOT_CITIES", "35"))
MIN_SNAPSHOT_CITY_DATE_PAIRS = int(os.environ.get("WEATHER_DATA_FEED_MIN_SNAPSHOT_CITY_DATE_PAIRS", "35"))

BASE_SHARES = 10
_FORECAST_CURVE_CACHE: dict[tuple[str, str, str], dict] | None = None
_FORECAST_LIVE_DISABLED_REASON: str | None = None
_FORECAST_LIVE_FAILURES: dict[tuple[str, str, str], dict] = {}


def _forecast_live_failure_key(city, target_date, model):
    return str(city), str(target_date), str(model).lower()


def _record_forecast_live_failure(
    city,
    target_date,
    model,
    *,
    reason,
    status_code=None,
    error=None,
    attempted=True,
):
    _FORECAST_LIVE_FAILURES[_forecast_live_failure_key(city, target_date, model)] = {
        "reason": str(reason),
        "status_code": status_code,
        "error": str(error or "")[:500] or None,
        "attempted": bool(attempted),
    }


def snapshot_publish_quality(records):
    cities = sorted({str(r.get("city") or "") for r in records if r.get("city")})
    event_dates = sorted({str(r.get("event_date") or r.get("target_date") or "") for r in records if r.get("event_date") or r.get("target_date")})
    city_date_pairs = sorted({
        (str(r.get("city") or ""), str(r.get("event_date") or r.get("target_date") or ""))
        for r in records
        if r.get("city") and (r.get("event_date") or r.get("target_date"))
    })
    records_by_event_date = {}
    for r in records:
        key = str(r.get("event_date") or r.get("target_date") or "")
        if not key:
            continue
        records_by_event_date[key] = records_by_event_date.get(key, 0) + 1
    reasons = []
    if len(records) < MIN_SNAPSHOT_RECORDS:
        reasons.append(f"total_records_lt_{MIN_SNAPSHOT_RECORDS}")
    if len(cities) < MIN_SNAPSHOT_CITIES:
        reasons.append(f"unique_cities_lt_{MIN_SNAPSHOT_CITIES}")
    if len(city_date_pairs) < MIN_SNAPSHOT_CITY_DATE_PAIRS:
        reasons.append(f"city_date_pairs_lt_{MIN_SNAPSHOT_CITY_DATE_PAIRS}")
    publishable = bool(ALLOW_EMPTY_SNAPSHOT or not reasons)
    return {
        "publishable": publishable,
        "reasons": [] if publishable else reasons,
        "total_records": len(records),
        "unique_cities": len(cities),
        "city_date_pairs": len(city_date_pairs),
        "event_dates": event_dates,
        "records_by_event_date": records_by_event_date,
        "min_snapshot_records": MIN_SNAPSHOT_RECORDS,
        "min_snapshot_cities": MIN_SNAPSHOT_CITIES,
        "min_snapshot_city_date_pairs": MIN_SNAPSHOT_CITY_DATE_PAIRS,
        "partial_archive_dir": str(PARTIAL_OUTPUT_DIR),
    }


def summarize_orderbook_enrichment(records, *, scope, budget_sec, spent_sec):
    status_counts = {}
    target_status_counts = {}
    for side in ("yes", "no"):
        counts = {}
        for row in records:
            status = str(row.get(f"{side}_book_status") or "missing")
            counts[status] = counts.get(status, 0) + 1
            if status != "orderbook_scope_skipped":
                target_status_counts[status] = target_status_counts.get(status, 0) + 1
        status_counts[side] = counts
    incomplete = sum(
        count
        for status, count in target_status_counts.items()
        if status != "ok"
    )
    return {
        "status": "ok" if not incomplete else "incomplete",
        "scope": scope,
        "budget_sec": budget_sec,
        "spent_sec": round(float(spent_sec), 3),
        "target_count": sum(target_status_counts.values()),
        "target_ok_count": int(target_status_counts.get("ok", 0)),
        "target_incomplete_count": incomplete,
        "target_status_counts": dict(sorted(target_status_counts.items())),
        "side_status_counts": status_counts,
    }


def curl_json_get(url, params=None, *, proxy=None, timeout_sec=5.0, connect_timeout_sec=2.0):
    """Fetch JSON with curl so flaky network paths cannot pin the Python process."""
    if params:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    tmp = Path(f"/tmp/weather_http_{os.getpid()}_{hashlib.sha1(url.encode()).hexdigest()[:12]}.json")
    try:
        cmd = [
            "curl",
            "--connect-timeout",
            str(connect_timeout_sec),
            "--max-time",
            str(timeout_sec),
            "-sS",
            "-w",
            "\n%{http_code}",
            "-o",
            str(tmp),
        ]
        if proxy:
            cmd.extend(["-x", proxy])
        cmd.append(url)
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=max(float(timeout_sec) + 1.0, float(connect_timeout_sec) + 1.0),
        )
        status_line = (proc.stdout or "").strip().splitlines()[-1] if (proc.stdout or "").strip() else ""
        status_code = int(status_line) if status_line.isdigit() else 0
        if status_code != 200:
            return status_code, None, f"curl_status={status_code} returncode={proc.returncode} stderr={(proc.stderr or '').strip()[:180]}"
        return status_code, json.loads(tmp.read_text(encoding="utf-8")), ""
    except Exception as exc:
        return 0, None, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def fetch_aviationweather_metar_json(client, url, params):
    """Fetch AviationWeather METAR JSON.

    Production uses curl_json_get so network stalls cannot pin the Python
    process. Tests may pass a small injected client; keep that path available so
    timestamp parsing and local-date filtering stay directly testable.
    """
    if client is not None and not isinstance(client, httpx.Client):
        try:
            response = client.get(url, params=params)
            return getattr(response, "status_code", None), response.json(), None
        except Exception as exc:
            return None, None, str(exc)
    return curl_json_get(
        url,
        params=params,
        timeout_sec=WEATHER_CURL_TIMEOUT_SEC,
        connect_timeout_sec=WEATHER_CURL_CONNECT_TIMEOUT_SEC,
    )


def city_scan_dates(now_utc, city, explicit_target_date=None):
    return data_feed_city_scan_dates(city, now_utc, explicit_target_date=explicit_target_date)


def unique_city_scan_dates(cities, now_utc, explicit_target_date=None):
    return data_feed_unique_city_scan_dates(cities.keys(), now_utc, explicit_target_date=explicit_target_date)


def load_official_observation_configs():
    """Official observation station map for snapshot METAR enrichment."""
    return {cfg.city: cfg for cfg in load_city_configs(include_station_diff=True)}


def resolve_observation_station(city, cfg, official_configs=None):
    configured_icao = str(cfg.get("icao", "") or "").upper()
    official_cfg = (official_configs or {}).get(city)
    if official_cfg is None:
        return {
            "configured_icao": configured_icao,
            "metar_icao": configured_icao,
            "official_observation_station": configured_icao,
            "settlement_source_class": "",
            "live_observation_source": "",
            "source_profile_registry_class": "legacy_city_pool",
            "source_profile_alignment_days": None,
            "source_profile_alignment_rate": None,
        }
    metar_icao = str(official_cfg.official_icao or configured_icao).upper()
    return {
        "configured_icao": configured_icao,
        "metar_icao": metar_icao,
        "official_observation_station": metar_icao,
        "settlement_source_class": official_cfg.settlement_source_class,
        "live_observation_source": official_cfg.live_observation_source,
        "source_profile_registry_class": official_cfg.registry_class,
        "source_profile_alignment_days": official_cfg.alignment_days,
        "source_profile_alignment_rate": official_cfg.alignment_rate,
    }


def normalize_json_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def extract_market_tokens(mkt):
    outcomes = [str(x).strip().lower() for x in normalize_json_list(mkt.get("outcomes"))]
    token_ids = [str(x).strip() for x in normalize_json_list(mkt.get("clobTokenIds")) if str(x).strip()]
    out = {"yes": "", "no": ""}
    for idx, outcome in enumerate(outcomes):
        if idx >= len(token_ids):
            continue
        if outcome == "yes":
            out["yes"] = token_ids[idx]
        elif outcome == "no":
            out["no"] = token_ids[idx]
    if token_ids:
        out["yes"] = out["yes"] or token_ids[0]
        if len(token_ids) >= 2:
            out["no"] = out["no"] or token_ids[1]
    return out


def gamma_market_ladder(markets):
    """Preserve the complete event ladder; price eligibility belongs downstream."""
    bracket_list = []
    market_entries = []
    for mkt in markets:
        question = mkt.get("question", "")
        label = _extract_bracket_label(question)
        if label is None:
            continue
        prices_raw = mkt.get("outcomePrices")
        if not prices_raw:
            continue
        try:
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            yes_price = float(prices[0])
        except (IndexError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if not math.isfinite(yes_price) or not 0.0 <= yes_price <= 1.0:
            continue
        tokens = extract_market_tokens(mkt)
        bracket_list.append((label, yes_price))
        market_entries.append(
            {
                "label": label,
                "yes_price": yes_price,
                "last_trade": float(mkt.get("lastTradePrice", 0) or 0),
                "question": question,
                "market_id": mkt.get("id", ""),
                "condition_id": mkt.get("conditionId", ""),
                "yes_token_id": tokens["yes"],
                "no_token_id": tokens["no"],
            }
        )
    return bracket_list, market_entries


def _to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def normalize_book_levels(entries, side, top_n):
    rows = []
    for entry in entries or []:
        if isinstance(entry, dict):
            price = entry.get("price") or entry.get("p")
            size = entry.get("size") or entry.get("q") or entry.get("quantity")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            price, size = entry[0], entry[1]
        else:
            continue
        price_f = _to_float(price)
        size_f = _to_float(size)
        if price_f > 0 and size_f > 0:
            rows.append({"price": price_f, "size": size_f})
    rows.sort(key=lambda x: x["price"], reverse=(side == "bid"))
    return rows[:top_n]


def depth_within(levels, threshold_fn):
    return round(sum(x["size"] for x in levels if threshold_fn(x["price"])), 6)


def summarize_orderbook(book_json, top_n=20):
    bids = normalize_book_levels(book_json.get("bids") if isinstance(book_json, dict) else [], "bid", top_n)
    asks = normalize_book_levels(book_json.get("asks") if isinstance(book_json, dict) else [], "ask", top_n)
    best_bid = bids[0]["price"] if bids else None
    best_ask = asks[0]["price"] if asks else None
    bid_size = bids[0]["size"] if bids else None
    ask_size = asks[0]["size"] if asks else None
    spread = round(best_ask - best_bid, 6) if best_bid is not None and best_ask is not None else None

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": spread,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "depth_bid_5c": depth_within(bids, lambda p: best_bid is not None and p >= best_bid - 0.05),
        "depth_ask_5c": depth_within(asks, lambda p: best_ask is not None and p <= best_ask + 0.05),
        "depth_bid_10c": depth_within(bids, lambda p: best_bid is not None and p >= best_bid - 0.10),
        "depth_ask_10c": depth_within(asks, lambda p: best_ask is not None and p <= best_ask + 0.10),
        "bids": bids,
        "asks": asks,
    }


def fetch_token_orderbook(client, token_id, top_n=20, retries=DEFAULT_ORDERBOOK_RETRIES):
    if not token_id:
        return {
            "status": "missing_token",
            "token_id": token_id,
            "fetched_at_utc": None,
            "summary": {},
            "raw": {},
        }
    last_error = None
    attempts = max(1, int(retries or 0) + 1)
    for attempt in range(attempts):
        request_started_at_utc = utc_now_text()
        tmp = Path(f"/tmp/weather_orderbook_{os.getpid()}_{hashlib.sha1(str(token_id).encode()).hexdigest()[:12]}.json")
        try:
            url = f"{PM_CLOB_URL}/book?token_id={urllib.parse.quote(str(token_id), safe='')}"
            cmd = [
                "curl",
                "--connect-timeout",
                str(ORDERBOOK_CURL_CONNECT_TIMEOUT_SEC),
                "--max-time",
                str(ORDERBOOK_CURL_TIMEOUT_SEC),
                "-sS",
                "-w",
                "\n%{http_code}",
                "-o",
                str(tmp),
            ]
            if PROXY:
                cmd.extend(["-x", PROXY])
            cmd.append(url)
            proc = subprocess.run(cmd, text=True, capture_output=True)
            response_received_at_utc = utc_now_text()
            status_line = (proc.stdout or "").strip().splitlines()[-1] if (proc.stdout or "").strip() else ""
            status_code = int(status_line) if status_line.isdigit() else 0
            if status_code == 404:
                return {
                    "status": "not_found",
                    "token_id": token_id,
                    "request_started_at_utc": request_started_at_utc,
                    "response_received_at_utc": response_received_at_utc,
                    "parsed_at_utc": utc_now_text(),
                    "fetched_at_utc": response_received_at_utc,
                    "summary": {},
                    "raw": {},
                }
            if status_code == 200:
                raw = json.loads(tmp.read_text(encoding="utf-8"))
                summary = summarize_orderbook(raw, top_n=top_n)
                parsed_at_utc = utc_now_text()
                lineage = materialize_orderbook_capture(
                    token_id=str(token_id),
                    raw_book=raw,
                    request_started_at_utc=request_started_at_utc,
                    response_received_at_utc=response_received_at_utc,
                    parsed_at_utc=parsed_at_utc,
                    request_batch_capture_id=canonical_json_hash(
                        {
                            "mode": "single_book",
                            "token_id": str(token_id),
                            "request_started_at_utc": request_started_at_utc,
                        }
                    ),
                )
                return {
                    **lineage,
                    "status": "ok",
                    "summary": summary,
                    "raw": {
                        "bids": summary["bids"],
                        "asks": summary["asks"],
                        "timestamp": raw.get("timestamp"),
                        "hash": raw.get("hash"),
                    },
                }
            if status_code >= 500 or status_code == 429:
                last_error = RuntimeError(
                    f"curl_status={status_code} returncode={proc.returncode} stderr={(proc.stderr or '').strip()[:180]}"
                )
            else:
                last_error = RuntimeError(
                    f"curl_status={status_code} returncode={proc.returncode} stderr={(proc.stderr or '').strip()[:180]}"
                )
                break
            if attempt < attempts - 1:
                time.sleep(0.2 * (attempt + 1))
                continue
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.2 * (attempt + 1))
                continue
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
    failed_at_utc = utc_now_text()
    return {
        "status": "error",
        "token_id": token_id,
        "request_started_at_utc": request_started_at_utc,
        "response_received_at_utc": failed_at_utc,
        "parsed_at_utc": None,
        "fetched_at_utc": failed_at_utc,
        "clock_lineage_status": "single_request_failed",
        "event_time_pit_scorable": False,
        "error": f"{type(last_error).__name__}: {last_error}",
        "summary": {},
        "raw": {},
    }


def append_orderbook_archive(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(row)
    raw_payload_hash = canonical_json_hash(row.get("raw") or {})
    row.setdefault("schema_version", "weather_orderbook_capture_v3")
    row.setdefault("producer", "weather_data_feed_service.legacy_weather_predict.paper_snapshot")
    row.setdefault("producer_build_id", PRODUCER_BUILD_ID)
    row.setdefault("producer_build_id_basis", PRODUCER_BUILD_ID_BASIS)
    row.setdefault("raw_payload_hash", raw_payload_hash)
    row.setdefault(
        "book_capture_id",
        canonical_json_hash(
            {
                "token_id": row.get("token_id"),
                "fetched_at_utc": row.get("fetched_at_utc"),
                "raw_payload_hash": raw_payload_hash,
            }
        ),
    )
    row.setdefault("detected_at_utc", row.get("response_received_at_utc"))
    row.setdefault("first_seen_at_utc", row.get("response_received_at_utc"))
    row.setdefault("available_at_utc", row.get("response_received_at_utc"))
    row.setdefault("source_lineage_status", "collector_exact_orderbook_response_v3")
    with gzip.open(path, "at", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def publish_json_atomic(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_canonical_orderbook_latest(path, *, now_utc, max_age_sec):
    """Load one canonical market-books batch for view materialization."""
    source = Path(path)
    if not source.exists():
        return {}, {"status": "missing", "reason": "canonical_market_books_missing", "path": str(source)}
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, {
            "status": "invalid",
            "reason": f"canonical_market_books_invalid:{type(exc).__name__}",
            "path": str(source),
        }
    available_raw = payload.get("available_at_utc")
    try:
        available = datetime.fromisoformat(str(available_raw).replace("Z", "+00:00"))
        if available.tzinfo is None:
            available = available.replace(tzinfo=timezone.utc)
        age_sec = max(0.0, (now_utc - available.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return {}, {
            "status": "invalid",
            "reason": "canonical_market_books_missing_available_clock",
            "path": str(source),
        }
    if max_age_sec >= 0 and age_sec > max_age_sec:
        return {}, {
            "status": "stale",
            "reason": "canonical_market_books_stale",
            "path": str(source),
            "age_sec": age_sec,
            "max_age_sec": max_age_sec,
            "batch_capture_id": payload.get("batch_capture_id"),
        }
    books = {}
    book_keys = (
        "city", "event_date", "event_id", "event_slug", "market_id",
        "condition_id", "bracket", "outcome",
        "status", "token_id", "request_started_at_utc", "response_received_at_utc",
        "parsed_at_utc", "fetched_at_utc", "request_batch_capture_id",
        "clock_lineage_status", "event_time_pit_scorable", "exchange_book_timestamp",
        "exchange_book_hash", "raw_payload_hash", "book_capture_id", "summary", "raw", "error",
    )
    for row in payload.get("records") or []:
        if not isinstance(row, dict):
            continue
        token_id = str(row.get("token_id") or "")
        if token_id:
            books[token_id] = {key: row.get(key) for key in book_keys if key in row}
    return books, {
        "status": "ok",
        "path": str(source),
        "age_sec": age_sec,
        "max_age_sec": max_age_sec,
        "batch_capture_id": payload.get("batch_capture_id"),
        "archive_path": payload.get("archive_path"),
        "available_at_utc": available_raw,
        "book_count": len(books),
    }


def canonical_market_ladders_from_books(books):
    """Build event ladders from canonical book identity without Gamma I/O."""
    grouped = {}
    for book in books.values():
        city = str(book.get("city") or "")
        event_date = str(book.get("event_date") or "")
        bracket = str(book.get("bracket") or "")
        outcome = str(book.get("outcome") or "").lower()
        if not city or not event_date or not bracket or outcome not in {"yes", "no"}:
            continue
        event = grouped.setdefault(
            (city, event_date),
            {
                "event_id": str(book.get("event_id") or ""),
                "event_slug": str(book.get("event_slug") or ""),
                "markets": {},
            },
        )
        market = event["markets"].setdefault(
            bracket,
            {
                "label": bracket,
                "market_id": str(book.get("market_id") or ""),
                "condition_id": str(book.get("condition_id") or ""),
                "yes_token_id": "",
                "no_token_id": "",
                "yes_price": None,
            },
        )
        market[f"{outcome}_token_id"] = str(book.get("token_id") or "")
        if outcome == "yes":
            summary = book.get("summary") if isinstance(book.get("summary"), dict) else {}
            bid = _to_float(summary.get("best_bid"), None)
            ask = _to_float(summary.get("best_ask"), None)
            if bid is not None and ask is not None:
                market["yes_price"] = (bid + ask) / 2.0
            else:
                market["yes_price"] = ask if ask is not None else bid

    ladders = {}
    for key, event in grouped.items():
        entries = []
        for bracket, market in event["markets"].items():
            yes_price = market.get("yes_price")
            if yes_price is None or not 0.0 <= float(yes_price) <= 1.0:
                continue
            if bracket.endswith("+"):
                question = f"Will the highest temperature be {bracket[:-1]}°F or higher?"
            elif "-" in bracket:
                question = f"Will the highest temperature be between {bracket}°F?"
            else:
                question = f"Will the highest temperature be {bracket}°F?"
            entries.append(
                {
                    **market,
                    "yes_price": float(yes_price),
                    "last_trade": 0.0,
                    "question": question,
                }
            )
        entries.sort(
            key=lambda item: float((re.search(r"-?\d+(?:\.\d+)?", item["label"]) or [float("inf")])[0])
        )
        ladders[key] = {
            "event_id": event["event_id"],
            "event_slug": event["event_slug"],
            "bracket_list": [(item["label"], item["yes_price"]) for item in entries],
            "market_entries": entries,
        }
    return ladders


def publish_legacy_orderbook_alias(source_archive, destination):
    """Expose a canonical capture at a legacy path without duplicating bytes."""
    if not source_archive:
        return None
    source = Path(source_archive)
    destination = Path(destination)
    if not source.exists():
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return destination
    try:
        os.link(source, destination)
    except OSError:
        return None
    return destination


def stamp_snapshot_availability(payload, available_at_utc=None):
    available = available_at_utc or datetime.now(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    collection_started = str(
        payload.get("collection_started_at_utc") or payload.get("ts_utc") or ""
    )
    payload["collection_started_at_utc"] = collection_started
    payload["available_at_utc"] = available
    payload["published_at_utc"] = available
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        record["collection_started_at_utc"] = str(
            record.get("collection_started_at_utc")
            or record.get("snapshot_ts_utc")
            or collection_started
        )
        record["available_at_utc"] = available
        record["published_at_utc"] = available
    return available


def orderbook_budget_book(token_id, reason="orderbook_budget_exhausted"):
    return {
        "status": reason,
        "token_id": token_id,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "summary": {},
        "raw": {},
    }


def orderbook_budget_expired(started_at, budget_sec, *, now_monotonic=None):
    """Return whether actual orderbook work has exhausted its wall-clock budget.

    ``started_at=None`` means no token book has been requested yet.  Forecast,
    Gamma and historical-error preparation before the first book must not
    consume the orderbook budget.
    """

    if started_at is None or budget_sec < 0:
        return False
    now = time.monotonic() if now_monotonic is None else float(now_monotonic)
    return now - float(started_at) > float(budget_sec)


def fetch_token_orderbook_batch(
    client,
    token_archive_rows,
    *,
    top_n=20,
    max_workers=1,
    deadline_monotonic=None,
):
    if not token_archive_rows:
        return {}
    rows_by_token = dict(token_archive_rows)
    results: dict[str, tuple[dict, dict]] = {}

    # Retain the public helper's single-book mode for tests and callers that do
    # not own an HTTP batch client. Production canonical capture always passes
    # one shared client and uses the /books endpoint below.
    if client is None:
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
            futures = {
                executor.submit(fetch_token_orderbook, None, token_id, top_n=top_n): token_id
                for token_id in rows_by_token
            }
            for future in as_completed(futures):
                token_id = futures[future]
                results[token_id] = (rows_by_token[token_id], future.result())
        return results

    def budget_remaining():
        if deadline_monotonic is None:
            return None
        return max(0.0, deadline_monotonic - time.monotonic())

    def mark_budget_exhausted(tokens):
        for token_id in tokens:
            results[token_id] = (rows_by_token[token_id], orderbook_budget_book(token_id))

    def retryable_batch_error(exc):
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            return status_code in {408, 425, 429} or status_code >= 500
        return isinstance(exc, httpx.TransportError)

    tokens = list(rows_by_token)
    chunk_size = max(1, min(int(ORDERBOOK_BATCH_MAX_TOKENS), 500))
    for offset in range(0, len(tokens), chunk_size):
        chunk = tokens[offset : offset + chunk_size]
        remaining = budget_remaining()
        if remaining is not None and remaining <= 0:
            mark_budget_exhausted(tokens[offset:])
            break
        attempt_errors = []
        max_attempts = max(1, ORDERBOOK_BATCH_RETRIES + 1)
        for attempt in range(1, max_attempts + 1):
            remaining = budget_remaining()
            if remaining is not None and remaining <= 0:
                mark_budget_exhausted(chunk)
                break
            request_started_at_utc = utc_now_text()
            batch_capture_id = canonical_json_hash(
                {
                    "endpoint": "/books",
                    "request_started_at_utc": request_started_at_utc,
                    "token_ids": sorted(chunk),
                    "attempt": attempt,
                }
            )
            try:
                response = client.post(
                    f"{PM_CLOB_URL}/books",
                    json=[{"token_id": token_id} for token_id in chunk],
                    timeout=min(float(remaining), ORDERBOOK_CURL_TIMEOUT_SEC)
                    if remaining is not None
                    else ORDERBOOK_CURL_TIMEOUT_SEC,
                )
                response_received_at_utc = utc_now_text()
                response.raise_for_status()
                raw_books = response.json()
                if not isinstance(raw_books, list):
                    raise ValueError("CLOB /books response is not a list")
                parsed_at_utc = utc_now_text()
                by_asset = {
                    str(raw.get("asset_id") or ""): raw
                    for raw in raw_books
                    if isinstance(raw, dict)
                }
                for token_id in chunk:
                    raw = by_asset.get(str(token_id))
                    if raw is None:
                        book = {
                            "status": "missing_from_batch_response",
                            "token_id": token_id,
                            "request_started_at_utc": request_started_at_utc,
                            "response_received_at_utc": response_received_at_utc,
                            "parsed_at_utc": parsed_at_utc,
                            "fetched_at_utc": response_received_at_utc,
                            "request_batch_capture_id": batch_capture_id,
                            "clock_lineage_status": "collector_exact_response_clock",
                            "event_time_pit_scorable": False,
                            "request_attempt_count": attempt,
                            "prior_attempt_errors": attempt_errors,
                            "summary": {},
                            "raw": {},
                        }
                    else:
                        summary = summarize_orderbook(raw, top_n=top_n)
                        lineage = materialize_orderbook_capture(
                            token_id=str(token_id),
                            raw_book=raw,
                            request_started_at_utc=request_started_at_utc,
                            response_received_at_utc=response_received_at_utc,
                            parsed_at_utc=parsed_at_utc,
                            request_batch_capture_id=batch_capture_id,
                        )
                        book = {
                            **lineage,
                            "status": "ok",
                            "request_attempt_count": attempt,
                            "prior_attempt_errors": attempt_errors,
                            "summary": summary,
                            "raw": {
                                "bids": summary["bids"],
                                "asks": summary["asks"],
                                "timestamp": raw.get("timestamp"),
                                "hash": raw.get("hash"),
                            },
                        }
                    results[token_id] = (rows_by_token[token_id], book)
                break
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                attempt_errors.append(error)
                retryable = retryable_batch_error(exc)
                remaining = budget_remaining()
                if retryable and attempt < max_attempts and (remaining is None or remaining > 0):
                    delay = ORDERBOOK_BATCH_RETRY_BACKOFF_SEC * (2 ** (attempt - 1))
                    if remaining is not None:
                        delay = min(delay, remaining)
                    if delay > 0:
                        time.sleep(delay)
                    continue
                failed_at_utc = utc_now_text()
                for token_id in chunk:
                    results[token_id] = (
                        rows_by_token[token_id],
                        {
                            "status": "batch_error",
                            "token_id": token_id,
                            "request_started_at_utc": request_started_at_utc,
                            "response_received_at_utc": failed_at_utc,
                            "parsed_at_utc": None,
                            "fetched_at_utc": failed_at_utc,
                            "request_batch_capture_id": batch_capture_id,
                            "clock_lineage_status": "batch_request_failed",
                            "event_time_pit_scorable": False,
                            "request_attempt_count": attempt,
                            "prior_attempt_errors": attempt_errors[:-1],
                            "error": error,
                            "error_retryable": retryable,
                            "summary": {},
                            "raw": {},
                        },
                    )
                break
    return results


def prefixed_book_fields(prefix, token_id, book, archive_path):
    summary = book.get("summary") or {}
    return {
        f"{prefix}_token_id": token_id,
        f"{prefix}_best_bid": summary.get("best_bid"),
        f"{prefix}_best_ask": summary.get("best_ask"),
        f"{prefix}_spread": summary.get("spread"),
        f"{prefix}_bid_size": summary.get("bid_size"),
        f"{prefix}_ask_size": summary.get("ask_size"),
        f"{prefix}_depth_bid_5c": summary.get("depth_bid_5c"),
        f"{prefix}_depth_ask_5c": summary.get("depth_ask_5c"),
        f"{prefix}_depth_bid_10c": summary.get("depth_bid_10c"),
        f"{prefix}_depth_ask_10c": summary.get("depth_ask_10c"),
        f"{prefix}_book_status": book.get("status"),
        f"{prefix}_book_fetched_at_utc": book.get("fetched_at_utc"),
        f"{prefix}_book_exchange_ts_utc": book.get("exchange_book_ts_utc"),
        f"{prefix}_book_exchange_hash": book.get("exchange_book_hash"),
        f"{prefix}_book_request_started_at_utc": book.get("request_started_at_utc"),
        f"{prefix}_book_response_received_at_utc": book.get("response_received_at_utc"),
        f"{prefix}_book_parsed_at_utc": book.get("parsed_at_utc"),
        f"{prefix}_book_batch_capture_id": book.get("request_batch_capture_id"),
        f"{prefix}_book_clock_lineage_status": book.get("clock_lineage_status"),
        f"{prefix}_book_event_time_pit_scorable": book.get("event_time_pit_scorable", False),
        f"{prefix}_book_archive_path": str(archive_path) if archive_path else "",
    }

# Per-city best model based on v4 calibration RMSE comparison
# Source: calibration_results_v4.json, 354 days × 51 cities
def _forecast_values_hash(hourly_curve):
    payload = list(hourly_curve or [])
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _forecast_details_from_open_meteo(payload, *, source_model):
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    times = hourly.get("time", []) or []
    temps = hourly.get("temperature_2m", []) or []
    precip = hourly.get("precipitation_probability", []) or []
    radiation = hourly.get("shortwave_radiation", []) or []
    cloud = hourly.get("cloud_cover", []) or []
    wind_speed = hourly.get("wind_speed_10m", []) or []
    wind_dir = hourly.get("wind_direction_10m", []) or []
    pairs = []
    for t, temp in zip(times, temps):
        if temp is None:
            continue
        try:
            pairs.append((str(t), float(temp)))
        except Exception:
            continue
    if not pairs:
        return None

    max_f = max(temp for _, temp in pairs)
    peak_local_time = min(t for t, temp in pairs if abs(temp - max_f) < 1e-9)
    peak_hour_local = int(peak_local_time[11:13])
    utc_offset_seconds = int(payload.get("utc_offset_seconds") or 0)
    try:
        peak_local_dt = datetime.fromisoformat(peak_local_time)
        peak_utc_dt = (peak_local_dt - timedelta(seconds=utc_offset_seconds)).replace(tzinfo=timezone.utc)
        peak_time_utc = peak_utc_dt.isoformat().replace("+00:00", "Z")
        peak_hour_utc = peak_utc_dt.hour
    except Exception:
        peak_time_utc = None
        peak_hour_utc = None

    forecast_detected_at_utc = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    hourly_curve = build_hourly_curve(
        times,
        temps,
        precipitation_probability_pct=precip,
        shortwave_radiation_wm2=radiation,
        cloud_cover_pct=cloud,
        wind_speed_10m_kt=wind_speed,
        wind_direction_10m_deg=wind_dir,
    )
    return {
        "max_f": max_f,
        "peak_time_local": peak_local_time,
        "peak_hour_local": peak_hour_local,
        "peak_time_utc": peak_time_utc,
        "peak_hour_utc": peak_hour_utc,
        "hourly_count": len(pairs),
        "values_hash": _forecast_values_hash(hourly_curve),
        "hourly_curve": hourly_curve,
        "source_model": source_model,
        "source_api": f"open_meteo_live_{source_model}",
        "timezone": payload.get("timezone"),
        "timezone_abbreviation": payload.get("timezone_abbreviation"),
        "utc_offset_seconds": utc_offset_seconds,
        "generationtime_ms": payload.get("generationtime_ms"),
        "detected_at_utc": forecast_detected_at_utc,
    }


def _forecast_details_from_curve_row(row, *, cache_age_sec, archive_path=None):
    hourly_curve = row.get("hourly_curve") if isinstance(row.get("hourly_curve"), list) else []
    if not hourly_curve or row.get("forecast_max_f") is None:
        return None
    source_model = str(row.get("forecast_model") or "").lower()
    source_api = str(row.get("forecast_source") or f"open_meteo_live_{source_model}")
    return {
        "max_f": float(row["forecast_max_f"]),
        "peak_time_local": row.get("forecast_peak_time_local"),
        "peak_hour_local": row.get("forecast_peak_hour_local"),
        "peak_time_utc": row.get("forecast_peak_time_utc"),
        "peak_hour_utc": row.get("forecast_peak_hour_utc"),
        "hourly_count": int(row.get("forecast_hourly_count") or len(hourly_curve)),
        "values_hash": str(row.get("forecast_values_hash") or _forecast_values_hash(hourly_curve)),
        "hourly_curve": hourly_curve,
        "source_model": source_model,
        "source_api": f"{source_api}_cached_curve",
        "timezone": row.get("forecast_timezone"),
        "timezone_abbreviation": row.get("forecast_timezone_abbreviation"),
        "utc_offset_seconds": int(row.get("forecast_utc_offset_seconds") or 0),
        "generationtime_ms": row.get("forecast_generationtime_ms"),
        "detected_at_utc": row.get("snapshot_ts_utc"),
        "cache_fallback": True,
        "cache_age_sec": round(float(cache_age_sec), 3),
        "curve_archive_path": str(archive_path) if archive_path else None,
    }


def _load_forecast_curve_cache():
    global _FORECAST_CURVE_CACHE
    if _FORECAST_CURVE_CACHE is not None:
        return _FORECAST_CURVE_CACHE
    cache = {}
    root = FORECAST_CURVE_ROOT
    now_ts = time.time()
    files = sorted(
        root.rglob("forecast_hourly_curves*.jsonl") if root.exists() else [],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[: max(1, FORECAST_CURVE_CACHE_MAX_FILES)]
    for path in files:
        age_sec = max(0.0, now_ts - path.stat().st_mtime)
        if age_sec > FORECAST_CURVE_CACHE_MAX_AGE_SEC:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            key = (
                str(row.get("city") or ""),
                str(row.get("target_date") or ""),
                str(row.get("forecast_model") or "").lower(),
            )
            if not all(key) or key in cache:
                continue
            row_age_sec = age_sec
            try:
                row_ts = datetime.fromisoformat(
                    str(row.get("snapshot_ts_utc") or "").replace("Z", "+00:00")
                )
                row_age_sec = max(
                    0.0,
                    datetime.now(timezone.utc).timestamp() - row_ts.timestamp(),
                )
            except (TypeError, ValueError):
                pass
            if row_age_sec > FORECAST_CURVE_CACHE_MAX_AGE_SEC:
                continue
            details = _forecast_details_from_curve_row(
                row,
                cache_age_sec=row_age_sec,
                archive_path=path,
            )
            if details is not None:
                cache[key] = details
    _FORECAST_CURVE_CACHE = cache
    return cache


def _cached_live_forecast(city, target_date, model):
    cached = _load_forecast_curve_cache().get((str(city), str(target_date), str(model).lower()))
    return dict(cached) if cached is not None else None


def should_capture_forecast_curve(forecast_info):
    """Cached PIT curves are references, not a new source observation."""
    return bool(forecast_info.get("hourly_curve")) and not bool(
        forecast_info.get("cache_fallback")
    )


def _forecast_curve_evidence_key(row):
    return (
        str(row.get("city") or ""),
        str(row.get("target_date") or row.get("event_date") or ""),
        str(row.get("forecast_model") or row.get("model") or "").lower(),
        str(row.get("forecast_values_hash") or ""),
    )


def forecast_curve_publish_evidence(records, fresh_curve_rows, cached_curve_refs):
    """Prove every published city/date forecast has durable curve lineage.

    A cached curve is valid PIT evidence when its age was already checked by
    ``_load_forecast_curve_cache`` and the source archive still exists.  It is
    not a new forecast observation, so it must not be copied into the current
    capture merely to satisfy the batch commit guard.
    """
    required = {
        _forecast_curve_evidence_key(row)
        for row in records
        if all(_forecast_curve_evidence_key(row))
    }
    fresh = {
        _forecast_curve_evidence_key(row)
        for row in fresh_curve_rows
        if all(_forecast_curve_evidence_key(row))
    }
    cached = {
        _forecast_curve_evidence_key(row)
        for row in cached_curve_refs
        if all(_forecast_curve_evidence_key(row))
        and row.get("curve_archive_path")
        and Path(str(row["curve_archive_path"])).is_file()
    }
    covered = fresh | cached
    missing = sorted(required - covered)
    return {
        "schema_version": "forecast_curve_publish_evidence_v1",
        "status": "ok" if required and not missing else "incomplete",
        "required_city_target_forecasts": len(required),
        "fresh_curve_matches": len(required & fresh),
        "cached_curve_matches": len(required & cached),
        "missing_count": len(missing),
        "missing_examples": [
            {
                "city": city,
                "target_date": target_date,
                "forecast_model": model,
                "forecast_values_hash": values_hash,
            }
            for city, target_date, model, values_hash in missing[:10]
        ],
        "publishable": bool(required and not missing),
    }


def _fetch_live_forecast(client, model, city, cfg, target_date):
    """Read the durable forecast curve cache without performing network I/O.

    Market snapshots run every few minutes and are not forecast producers.
    Keeping this function cache-only makes that ownership boundary true even
    when the legacy runner is invoked outside the production wrapper.
    """
    return _cached_live_forecast(city, target_date, model)


def _refresh_live_forecast(client, model, city, cfg, target_date):
    """Refresh one forecast curve for the dedicated forecast collector."""
    global _FORECAST_LIVE_DISABLED_REASON
    failure_key = _forecast_live_failure_key(city, target_date, model)
    _FORECAST_LIVE_FAILURES.pop(failure_key, None)
    cached = _cached_live_forecast(city, target_date, model)
    cached_age = cached.get("cache_age_sec") if isinstance(cached, dict) else None
    if (
        cached is not None
        and cached_age is not None
        and 0 <= float(cached_age) <= FORECAST_LIVE_REFRESH_SEC
    ):
        # Forecast models update on an hours-scale. Reuse durable PIT evidence
        # inside the refresh window instead of spending one API call per
        # city/target on every 10-minute market snapshot.
        return cached
    url = f"https://api.open-meteo.com/v1/{model}"
    params = {
        "latitude": cfg["lat"], "longitude": cfg["lon"],
        "hourly": "temperature_2m,shortwave_radiation,precipitation_probability,cloud_cover,wind_speed_10m,wind_direction_10m",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "kn",
        "timezone": "auto",
        "start_date": target_date, "end_date": target_date,
    }
    if _FORECAST_LIVE_DISABLED_REASON is None:
        try:
            status_code, payload, error = curl_json_get(
                url,
                params=params,
                proxy=FORECAST_PROXY,
                timeout_sec=WEATHER_CURL_TIMEOUT_SEC,
                connect_timeout_sec=WEATHER_CURL_CONNECT_TIMEOUT_SEC,
            )
            if status_code == 200 and payload is not None:
                return _forecast_details_from_open_meteo(payload, source_model=model)
            if status_code == 429:
                _FORECAST_LIVE_DISABLED_REASON = "open_meteo_http_429"
                reason = "open_meteo_http_429"
            elif status_code == 200:
                reason = "open_meteo_empty_payload"
            elif status_code:
                reason = f"open_meteo_http_{status_code}"
            else:
                reason = "open_meteo_transport_error"
            _record_forecast_live_failure(
                city,
                target_date,
                model,
                reason=reason,
                status_code=status_code,
                error=error,
            )
        except Exception as exc:
            _record_forecast_live_failure(
                city,
                target_date,
                model,
                reason="open_meteo_request_exception",
                error=f"{type(exc).__name__}: {exc}",
            )
    else:
        _record_forecast_live_failure(
            city,
            target_date,
            model,
            reason="open_meteo_request_suppressed",
            error=f"provider disabled after {_FORECAST_LIVE_DISABLED_REASON}",
            attempted=False,
        )
    return cached or _cached_live_forecast(city, target_date, model)


def fetch_live_gfs(client, city, cfg, target_date):
    return _fetch_live_forecast(client, "gfs", city, cfg, target_date)


def fetch_live_ecmwf(client, city, cfg, target_date):
    return _fetch_live_forecast(client, "ecmwf", city, cfg, target_date)


def compute_ecmwf_error_distribution(city: str, cfg: dict):
    """Compute historical ECMWF forecast errors (WU_actual - ECMWF_forecast) from cache."""
    aliases = [city]
    if city == "LA":
        aliases.append("LosAngeles")
    ecmwf_files = []
    for alias in aliases:
        ecmwf_files = list(CACHE_DIR.glob(f"ecmwf_v4_{alias}_*.json"))
        if ecmwf_files:
            break
    if not ecmwf_files:
        return None
    ecmwf_data = json.loads(ecmwf_files[0].read_text())
    ecmwf_daily_max = forecast_hourly_daily_max_local(ecmwf_data, city=city)

    wu_file = CACHE_DIR / "wu_obs" / f"wu_obs_{cfg['icao']}.csv"
    if not wu_file.exists():
        return None
    wu_daily_max = {}
    with open(wu_file) as f:
        header = f.readline().strip().split(",")
        temp_idx = header.index("temp")
        date_idx = header.index("date_local")
        for line in f:
            parts = line.strip().split(",")
            if len(parts) <= max(temp_idx, date_idx):
                continue
            try:
                temp = int(parts[temp_idx])
                d = parts[date_idx]
                wu_daily_max[d] = max(wu_daily_max.get(d, -999), temp)
            except (ValueError, IndexError):
                continue

    errors = []
    for d in sorted(ecmwf_daily_max.keys()):
        if d in wu_daily_max:
            errors.append(wu_daily_max[d] - ecmwf_daily_max[d])
    if len(errors) < 30:
        return None
    return np.array(errors)


def parse_bracket_bounds(label, unit):
    lo, hi = None, None
    m = re.match(r"^(\d+)-(\d+)$", label)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
    if lo is None:
        m = re.match(r"^(\d+)$", label)
        if m:
            lo = hi = float(m.group(1))
    if lo is None:
        m = re.match(r"^(\d+)\+$", label)
        if m:
            lo, hi = float(m.group(1)), 999
    if lo is None:
        return None, None
    if unit == "C":
        lo_f = lo * 9 / 5 + 32 if lo > -900 else -999
        hi_f = hi * 9 / 5 + 32 if hi < 900 else 999
    else:
        lo_f, hi_f = lo, hi
    return lo_f, hi_f


def round_half_up_float(value):
    return math.floor(float(value) + 0.5)


def orderbook_disabled_book(status):
    return {"status": status, "summary": {}, "raw": {}, "fetched_at_utc": None}


def orderbook_for_entry(orderbook_cache, token_id, *, label, outcome, targets, disabled_reason=None):
    """Return a fetched book or the correct status for this ladder entry."""
    targeted = targets is None or (label, outcome) in targets
    if not targeted:
        return orderbook_disabled_book("orderbook_scope_skipped")
    book = orderbook_cache.get(token_id)
    if book is not None:
        return book
    return orderbook_disabled_book(disabled_reason or "orderbook_missing")


def orderbook_targets_for_current_yes(markets, unit, metar_state):
    """Return (label, outcome) pairs needed by current-YES.

    The live strategy only needs current-bracket YES ask and next-higher-bracket
    NO ask. Fetching every token for every weather bracket makes the snapshot
    service miss its production cadence.
    """
    metar_max_f = metar_state.get("metar_current_max_f")
    if metar_max_f is None:
        return set()

    if unit == "C":
        running_native = (float(metar_max_f) - 32.0) * 5.0 / 9.0
        running_compare_f = round_half_up_float(running_native) * 9.0 / 5.0 + 32.0
    else:
        running_compare_f = round_half_up_float(float(metar_max_f))

    parsed = []
    for mkt in markets:
        label = _extract_bracket_label(mkt.get("question", ""))
        if label is None:
            continue
        lo_f, hi_f = parse_bracket_bounds(label, unit)
        if lo_f is None or hi_f is None:
            continue
        parsed.append((label, lo_f, hi_f))

    targets = set()
    current = [(label, hi_f) for label, lo_f, hi_f in parsed if lo_f <= running_compare_f <= hi_f]
    if current:
        current_label, _ = sorted(current, key=lambda item: item[1])[0]
        targets.add((current_label, "yes"))

    d1 = [(label, lo_f) for label, lo_f, _ in parsed if lo_f > running_compare_f]
    if d1:
        d1_label, _ = sorted(d1, key=lambda item: item[1])[0]
        targets.add((d1_label, "no"))
    return targets


def orderbook_targets_for_strategy_live(markets, unit, metar_state):
    """Return the compact orderbook set used by active live/near-live strategies.

    This is intentionally wider than the old current_d1 scope:
    - current bracket YES: current-YES / higher-no state features
    - current bracket NO: regime-routed current-NO route
    - next higher bracket NO: current-YES / higher-no d1 route
    - second higher bracket NO: regime-routed d2 route

    Range/basket research still needs the full all-bracket snapshot.
    """
    metar_max_f = metar_state.get("metar_current_max_f")
    if metar_max_f is None:
        return set()

    if unit == "C":
        running_native = (float(metar_max_f) - 32.0) * 5.0 / 9.0
        running_compare_f = round_half_up_float(running_native) * 9.0 / 5.0 + 32.0
    else:
        running_compare_f = round_half_up_float(float(metar_max_f))

    parsed = []
    for mkt in markets:
        label = _extract_bracket_label(mkt.get("question", ""))
        if label is None:
            continue
        lo_f, hi_f = parse_bracket_bounds(label, unit)
        if lo_f is None or hi_f is None:
            continue
        parsed.append((label, lo_f, hi_f))

    targets = set()
    current = [(label, hi_f) for label, lo_f, hi_f in parsed if lo_f <= running_compare_f <= hi_f]
    if current:
        current_label, _ = sorted(current, key=lambda item: item[1])[0]
        targets.add((current_label, "yes"))
        targets.add((current_label, "no"))

    higher = [(label, lo_f) for label, lo_f, _ in parsed if lo_f > running_compare_f]
    for label, _ in sorted(higher, key=lambda item: item[1])[:2]:
        targets.add((label, "no"))
    return targets


def classify_window(hours_to_settle):
    """Legacy window classification (kept for backward compatibility)."""
    if hours_to_settle < 0:
        return "settled"
    if abs(hours_to_settle - 24) <= 1.0:
        return "t24"
    if abs(hours_to_settle - 12) <= 1.0:
        return "t12"
    if hours_to_settle > 25:
        return "pre_t24"
    if hours_to_settle > 13:
        return "between"
    if hours_to_settle < 11:
        return "post_t12"
    return "near_t12"


def classify_time_bucket(hours_to_settle):
    """Trading time bucket for cross-section error calibration.

    These are hand-picked observation buckets reflecting when the strategy
    evaluates trades, NOT forecast lead time.
    """
    if hours_to_settle < 0:
        return "settled"
    if abs(hours_to_settle - 24) <= 2:
        return "t24"
    if abs(hours_to_settle - 18) <= 2:
        return "t18"
    if abs(hours_to_settle - 12) <= 2:
        return "t12"
    if abs(hours_to_settle - 6) <= 2:
        return "t6"
    if hours_to_settle > 26:
        return "pre_t24"
    if hours_to_settle > 20:
        return "between_t24_t18"
    if hours_to_settle > 14:
        return "between_t18_t12"
    if hours_to_settle > 8:
        return "between_t12_t6"
    return "post_t6"


# GFS/ECMWF model update cycles (UTC hours)
GFS_CYCLES = [0, 6, 12, 18]
ECMWF_CYCLES = [0, 12]
# Delay from cycle start to data availability (conservative estimate)
MODEL_AVAILABILITY_DELAY_HOURS = 4


def estimate_model_cycle(now_utc_hour, model="gfs"):
    """Estimate the latest available model initialization time.

    Returns (cycle_hour_utc, model_run_age_hours).
    cycle_hour_utc: the UTC hour of the latest available model run.
    model_run_age_hours: hours since that cycle initialized.

    Selects the cycle with the SMALLEST positive age (most recent available run),
    not the oldest. A cycle is available if enough time has passed since it
    initialized (MODEL_AVAILABILITY_DELAY_HOURS).
    """
    cycles = GFS_CYCLES if model == "gfs" else ECMWF_CYCLES
    # Find all cycles that have had enough time to become available,
    # with their ages (hours since that cycle initialized)
    avail_with_age = []
    for c in cycles:
        age = (now_utc_hour - c) % 24
        if age < MODEL_AVAILABILITY_DELAY_HOURS:
            # This cycle hasn't finished yet, it's from the previous day
            age += 24
        avail_with_age.append((c, age))

    # Filter to cycles that are actually available (age >= delay)
    avail = [(c, age) for c, age in avail_with_age if age >= MODEL_AVAILABILITY_DELAY_HOURS]

    if not avail:
        # All cycles are too recent — use the one from the previous day with smallest age
        # This shouldn't normally happen with 4h delay and 6h GFS cycles
        cycle = cycles[-1]
        age = (now_utc_hour - cycle) % 24
        if age < MODEL_AVAILABILITY_DELAY_HOURS:
            age += 24
        return cycle, age

    # Pick the cycle with the SMALLEST age (= most recent available run)
    cycle, age = min(avail, key=lambda x: x[1])
    return cycle, age


def estimate_forecast_lead_hours(cycle_hour_utc, settle_utc_hour):
    """Estimate forecast lead time (hours from model init to settlement).

    This is NOT the same as hours_to_settle.
    forecast_lead = time from model initialization to settlement cutoff.
    """
    lead = (settle_utc_hour - cycle_hour_utc) % 24
    if lead == 0:
        lead = 24
    return lead


def fetch_live_metar_state(client, icao, target_date_local, city, now_utc):
    """Fetch live METAR observations for the target city/date.

    Covers the full day-to-date window (from local 00:00 to now),
    not just the last 6 hours. Merges live METAR with WU cache to
    maximize coverage.

    Returns dict with:
      metar_current_max_f: max observed temp today (int °F or None)
      metar_latest_temp_f: most recent observed temp (int °F or None)
      metar_latest_ts_utc: timestamp of most recent observation (str or None)
      metar_obs_count_today: number of observations today (int)
      metar_source: "aviationweather_live" | "cache" | "merged" | "none"
    """
    result = {
        "metar_current_max_f": None,
        "metar_latest_temp_f": None,
        "metar_latest_ts_utc": None,
        "metar_obs_count_today": 0,
        "metar_source": "none",
    }

    timezone_name = city_timezone_name(city)
    if not timezone_name:
        raise ValueError(f"missing timezone for city={city!r}")
    target_date = date.fromisoformat(str(target_date_local))
    target_local_midnight_utc = datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        0,
        0,
        0,
        tzinfo=city_local_datetime(city, now_utc).tzinfo,
    ).astimezone(timezone.utc)
    hours_since_midnight = max(1, (now_utc - target_local_midnight_utc).total_seconds() / 3600)
    # Cap at 30 hours to avoid excessive API load (covers up to ~30h for far-west timezones)
    metar_hours = min(int(hours_since_midnight) + 2, 30)

    # Collect all day-to-date observations from both sources
    all_obs = {}  # key: obs timestamp UTC string, value: (temp_f, source)

    # Source 1: aviationweather.gov METAR endpoint
    live_ok = False
    try:
        url = "https://aviationweather.gov/api/data/metar"
        params = {
            "ids": icao,
            "format": "json",
            "taf": "false",
            "hours": metar_hours,
        }
        status_code, data, _error = fetch_aviationweather_metar_json(client, url, params)
        if status_code == 200:
            if isinstance(data, list) and len(data) > 0:
                target_local = str(target_date_local)
                for metar in data:
                    temp_c = metar.get("temp")
                    if temp_c is None:
                        continue
                    temp_f = int(round(temp_c * 9 / 5 + 32))
                    obs_time = metar.get("obsTime") or metar.get("reportTime")
                    if obs_time is None:
                        continue
                    try:
                        if isinstance(obs_time, str):
                            obs_dt = datetime.fromisoformat(obs_time.replace("Z", "+00:00"))
                            obs_key = obs_time
                        elif isinstance(obs_time, (int, float)):
                            obs_dt = datetime.fromtimestamp(float(obs_time), timezone.utc)
                            obs_key = obs_dt.isoformat().replace("+00:00", "Z")
                        else:
                            continue
                        obs_local = city_local_datetime(city, obs_dt)
                        obs_local_date = obs_local.strftime("%Y-%m-%d")
                    except (ValueError, TypeError):
                        continue
                    if obs_local_date == target_local:
                        all_obs[obs_key] = (temp_f, "live")
                        live_ok = True
    except Exception:
        pass

    # Source 2: WU obs cache (fills gaps, especially for earlier hours)
    try:
        wu_file = CACHE_DIR / "wu_obs" / f"wu_obs_{icao}.csv"
        if wu_file.exists():
            today_local = target_date_local
            with open(wu_file) as f:
                header = f.readline().strip().split(",")
                temp_idx = header.index("temp")
                date_idx = header.index("date_local")
                valid_idx = header.index("valid_utc")
                for line in f:
                    parts = line.strip().split(",")
                    if len(parts) <= max(temp_idx, date_idx, valid_idx):
                        continue
                    try:
                        d = parts[date_idx]
                        if d == today_local:
                            temp = int(parts[temp_idx])
                            ts = parts[valid_idx]
                            # Don't overwrite live data with cache
                            if ts not in all_obs:
                                all_obs[ts] = (temp, "cache")
                    except (ValueError, IndexError):
                        continue
    except Exception:
        pass

    # Compute day-to-date max from merged observations
    if all_obs:
        temps = [v[0] for v in all_obs.values()]
        # Find latest observation (by timestamp string sort, which works for ISO format)
        latest_key = max(all_obs.keys())
        latest_temp, latest_source = all_obs[latest_key]
        result["metar_current_max_f"] = max(temps)
        result["metar_latest_temp_f"] = latest_temp
        result["metar_latest_ts_utc"] = latest_key
        result["metar_obs_count_today"] = len(all_obs)
        # Source label: merged if both contributed, otherwise whichever worked
        sources = set(v[1] for v in all_obs.values())
        if sources == {"live"}:
            result["metar_source"] = "aviationweather_live"
        elif sources == {"cache"}:
            result["metar_source"] = "cache"
        else:
            result["metar_source"] = "merged"

    return result


def load_strategy_observation_index(path, now_utc, max_age_sec):
    cache = load_observation_cache(Path(path))
    generated_at = parse_observation_utc(cache.get("generated_at_utc"))
    if generated_at is None:
        raise RuntimeError("observation cache missing generated_at_utc")
    age_sec = (now_utc - generated_at).total_seconds()
    if age_sec < -1 or age_sec > float(max_age_sec):
        raise RuntimeError(
            f"observation cache stale: age_sec={age_sec:.1f} max_age_sec={float(max_age_sec):.1f}"
        )
    return index_observation_cache(cache)


def metar_state_from_observation_cache(observation_index, city, target_date):
    result = {
        "metar_current_max_f": None,
        "metar_latest_temp_f": None,
        "metar_latest_ts_utc": None,
        "metar_obs_count_today": 0,
        "metar_source": "none",
    }
    row = observation_index.get((str(city), str(target_date)))
    if not row or str(row.get("status") or "") != "ok":
        return result

    running_max_c = row.get("running_max_c")
    current_temp_c = row.get("current_temp_c")
    try:
        result["metar_current_max_f"] = int(round(float(running_max_c) * 9 / 5 + 32))
    except (TypeError, ValueError):
        pass
    try:
        result["metar_latest_temp_f"] = int(round(float(current_temp_c) * 9 / 5 + 32))
    except (TypeError, ValueError):
        try:
            result["metar_latest_temp_f"] = int(round(float(row.get("tmpf_now"))))
        except (TypeError, ValueError):
            pass
    result["metar_latest_ts_utc"] = row.get("last_obs_utc")
    try:
        result["metar_obs_count_today"] = int(row.get("n_obs") or row.get("record_count") or 0)
    except (TypeError, ValueError):
        pass
    result["metar_source"] = f"observation_cache:{row.get('source') or 'unknown'}"
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date", default=None)
    parser.add_argument("--shares", type=int, default=BASE_SHARES)
    parser.add_argument("--orderbook-top-n", type=int, default=20)
    parser.add_argument(
        "--orderbook-scope",
        choices=("strategy_live", "current_d1", "all"),
        default=DEFAULT_ORDERBOOK_SCOPE if DEFAULT_ORDERBOOK_SCOPE in {"strategy_live", "current_d1", "all"} else "strategy_live",
        help="Fetch compact live-strategy orderbooks by default; use all for canonical research snapshots.",
    )
    parser.add_argument(
        "--orderbook-budget-sec",
        type=float,
        default=DEFAULT_ORDERBOOK_BUDGET_SEC,
        help="Maximum wall-clock seconds spent on orderbook enrichment before continuing snapshot generation.",
    )
    parser.add_argument(
        "--orderbook-workers",
        type=int,
        default=DEFAULT_ORDERBOOK_WORKERS,
        help="Concurrent token orderbook workers per city/event; keep at 1 for legacy serial behavior.",
    )
    parser.add_argument("--no-orderbook", action="store_true", help="Disable CLOB orderbook enrichment.")
    parser.add_argument(
        "--orderbook-source-latest",
        default="",
        help="Read canonical market_books/latest.json instead of issuing CLOB requests.",
    )
    parser.add_argument(
        "--orderbook-source-max-age-sec",
        type=float,
        default=420.0,
        help="Maximum accepted age for the canonical market-books batch.",
    )
    parser.add_argument(
        "--observation-cache",
        default="",
        help="Join canonical observation-cache state instead of fetching METAR in this view.",
    )
    parser.add_argument(
        "--observation-cache-max-age-sec",
        type=float,
        default=900.0,
        help="Maximum accepted age for the canonical observation cache.",
    )
    parser.add_argument("--now-utc", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    now_utc = parse_now_utc(args.now_utc)
    observation_index = (
        load_strategy_observation_index(
            args.observation_cache,
            now_utc,
            args.observation_cache_max_age_sec,
        )
        if args.observation_cache
        else {}
    )
    now_beijing = now_utc + timedelta(hours=8)
    now_utc_naive = now_utc.replace(tzinfo=None)
    now_utc_hour = now_utc_naive.hour + now_utc_naive.minute / 60.0
    orderbook_archive = (
        ORDERBOOK_OUTPUT_DIR
        / now_beijing.strftime("%Y-%m-%d")
        / f"orderbook_snapshot_{now_beijing.strftime('%Y%m%d_%H%M')}.jsonl.gz"
    )

    scan_dates = unique_city_scan_dates(CITIES, now_utc, args.target_date)

    pm_client = httpx.Client(
        proxy=PROXY,
        timeout=PM_HTTP_TIMEOUT,
        limits=PM_HTTP_LIMITS,
        follow_redirects=True,
        trust_env=False,
    )
    weather_client = httpx.Client(
        timeout=WEATHER_HTTP_TIMEOUT,
        limits=WEATHER_HTTP_LIMITS,
        follow_redirects=True,
        trust_env=False,
    )
    orderbook_cache = {}
    canonical_orderbook_source = None
    canonical_market_ladders = {}
    external_orderbook_only = bool(args.orderbook_source_latest)
    if external_orderbook_only:
        orderbook_cache, canonical_orderbook_source = load_canonical_orderbook_latest(
            args.orderbook_source_latest,
            now_utc=now_utc,
            max_age_sec=args.orderbook_source_max_age_sec,
        )
        canonical_market_ladders = canonical_market_ladders_from_books(orderbook_cache)
        publish_legacy_orderbook_alias(
            canonical_orderbook_source.get("archive_path"),
            orderbook_archive,
        )
    # Count only time spent inside CLOB batches. Forecast/Gamma work between
    # cities must not consume the orderbook enrichment budget.
    orderbook_spent_sec = 0.0
    orderbook_disabled_reason = "disabled" if args.no_orderbook else None
    if external_orderbook_only and canonical_orderbook_source.get("status") != "ok":
        orderbook_disabled_reason = canonical_orderbook_source.get("reason")

    print(f"{'='*90}")
    print(f" Paper Snapshot | Beijing {now_beijing.strftime('%Y-%m-%d %H:%M:%S')} | Shares: {args.shares}")
    print(f" Scanning dates: {scan_dates}")
    print(f" City pools: T1 trading={len(TRADING_T1_CITIES)} | T2 research={len(CITIES) - len(TRADING_T1_CITIES)} | T3=0")
    print(f"{'='*90}")

    official_observation_configs = load_official_observation_configs()

    # Pre-load historical errors per city using the best model
    all_errors = {}
    all_models = {}
    for city, cfg in CITIES.items():
        model = CITY_MODEL.get(city, "gfs")
        if model == "ecmwf":
            errs = compute_ecmwf_error_distribution(city, cfg)
        else:
            errs = compute_error_distribution(city, cfg)
        if errs is not None:
            all_errors[city] = errs
            all_models[city] = model
        else:
            # Fallback: try the other model
            if model == "ecmwf":
                errs = compute_error_distribution(city, cfg)
                if errs is not None:
                    all_errors[city] = errs
                    all_models[city] = "gfs"
            else:
                errs = compute_ecmwf_error_distribution(city, cfg)
                if errs is not None:
                    all_errors[city] = errs
                    all_models[city] = "ecmwf"

    all_records = []
    forecast_curve_rows = []
    forecast_curve_seen = set()
    cached_forecast_refs = []
    cached_forecast_seen = set()
    forecast_city_target_expected = 0

    for city, cfg in CITIES.items():
        for target_date in city_scan_dates(now_utc, city, args.target_date):
            tz = cfg["tz_offset"]
            market_local_date = target_date
            city_local_date_at_snapshot = data_feed_city_local_date(city, now_utc).isoformat()
            approximate_settle_utc = local_settle_utc(city, target_date)
            settle_utc = approximate_settle_utc
            market_end_source = "city_local_22h_approximation"
            hours_to_settle = (settle_utc - now_utc).total_seconds() / 3600

            if hours_to_settle < 0 or hours_to_settle > 50:
                continue
            forecast_city_target_expected += 1

            window = classify_window(hours_to_settle)
            time_bucket = classify_time_bucket(hours_to_settle)

            # Estimate model cycle metadata (initial, may be updated after fallback)
            assigned_model = CITY_MODEL.get(city, "gfs")
            model = all_models.get(city, assigned_model)
            fallback_reasons = []
            if model != assigned_model:
                fallback_reasons.append("assigned_error_distribution_unavailable")
            cycle_hour, model_run_age = estimate_model_cycle(now_utc_hour, model)
            settle_utc_hour = settle_utc.hour
            forecast_lead = estimate_forecast_lead_hours(cycle_hour, settle_utc_hour)

            # Fetch forecast using per-city best model
            errors = None
            if model == "ecmwf":
                forecast_info = fetch_live_ecmwf(weather_client, city, cfg, target_date)
            else:
                forecast_info = fetch_live_gfs(weather_client, city, cfg, target_date)
            if forecast_info is None:
                # Fallback to GFS if ECMWF unavailable
                if model == "ecmwf":
                    forecast_info = fetch_live_gfs(weather_client, city, cfg, target_date)
                    if forecast_info is not None:
                        model = "gfs"
                        fallback_reasons.append("forecast_fetch_unavailable")
                        errors = compute_error_distribution(city, cfg)
                        cycle_hour, model_run_age = estimate_model_cycle(now_utc_hour, model)
                        forecast_lead = estimate_forecast_lead_hours(cycle_hour, settle_utc_hour)
                if forecast_info is None:
                    continue
            else:
                # Use the pre-loaded errors for this city's model
                errors = all_errors.get(city)
            fcst_f = forecast_info["max_f"]

            # If errors is still None, try the other model.
            # To avoid forecast/errors mismatch, we must also re-fetch the forecast.
            if errors is None:
                if model == "ecmwf":
                    # Try GFS: fetch forecast AND errors together
                    gfs_forecast = fetch_live_gfs(weather_client, city, cfg, target_date)
                    gfs_errors = compute_error_distribution(city, cfg)
                    if gfs_forecast is not None and gfs_errors is not None:
                        forecast_info = gfs_forecast
                        fcst_f = forecast_info["max_f"]
                        errors = gfs_errors
                        model = "gfs"
                        fallback_reasons.append("error_distribution_unavailable")
                        cycle_hour, model_run_age = estimate_model_cycle(now_utc_hour, model)
                        forecast_lead = estimate_forecast_lead_hours(cycle_hour, settle_utc_hour)
                else:
                    # Try ECMWF: fetch forecast AND errors together
                    ecmwf_forecast = fetch_live_ecmwf(weather_client, city, cfg, target_date)
                    ecmwf_errors = compute_ecmwf_error_distribution(city, cfg)
                    if ecmwf_forecast is not None and ecmwf_errors is not None:
                        forecast_info = ecmwf_forecast
                        fcst_f = forecast_info["max_f"]
                        errors = ecmwf_errors
                        model = "ecmwf"
                        fallback_reasons.append("error_distribution_unavailable")
                        cycle_hour, model_run_age = estimate_model_cycle(now_utc_hour, model)
                        forecast_lead = estimate_forecast_lead_hours(cycle_hour, settle_utc_hour)
            if forecast_info.get("cache_fallback"):
                fallback_reasons.append("forecast_live_fetch_unavailable_cached_curve")
                cache_key = (city, target_date, str(forecast_info.get("source_model") or model))
                if cache_key not in cached_forecast_seen:
                    cached_forecast_seen.add(cache_key)
                    cached_forecast_refs.append(
                        {
                            "city": city,
                            "target_date": target_date,
                            "forecast_model": cache_key[2],
                            "forecast_source": forecast_info.get("source_api"),
                            "forecast_values_hash": forecast_info.get("values_hash"),
                            "forecast_detected_at_utc": forecast_info.get("detected_at_utc"),
                            "cache_age_sec": forecast_info.get("cache_age_sec"),
                            "curve_archive_path": forecast_info.get("curve_archive_path"),
                        }
                    )
            probability_status = (
                "cached_forecast_market_snapshot_only"
                if forecast_info.get("cache_fallback")
                else ("ok" if errors is not None else "missing_error_distribution")
            )
            # Resolve market identity. A strategy view fed by canonical
            # market-books must never rediscover the same event over Gamma.
            city_slug = cfg.get("slug", city.lower())
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            date_slug = dt.strftime("%B-%-d-%Y").lower()
            slug = f"highest-temperature-in-{city_slug}-on-{date_slug}"
            canonical_ladder = canonical_market_ladders.get((city, target_date))
            if external_orderbook_only:
                ev_raw = {
                    "id": (canonical_ladder or {}).get("event_id", ""),
                    "slug": (canonical_ladder or {}).get("event_slug", slug),
                }
                markets = []
            else:
                try:
                    status_code, ev_raw, _error = curl_json_get(
                        f"{PM_GAMMA_URL}/events",
                        params={"slug": slug},
                        proxy=PROXY,
                        timeout_sec=PM_CURL_TIMEOUT_SEC,
                        connect_timeout_sec=PM_CURL_CONNECT_TIMEOUT_SEC,
                    )
                    if status_code != 200 or ev_raw is None:
                        ev_raw = {}
                    if isinstance(ev_raw, list) and len(ev_raw) > 0:
                        ev_raw = ev_raw[0]
                    markets = ev_raw.get("markets", []) if isinstance(ev_raw, dict) else []
                except Exception:
                    ev_raw = {}
                    markets = []

            settle_utc, market_end_source = resolve_market_end_utc(
                ev_raw if isinstance(ev_raw, dict) else None,
                fallback=approximate_settle_utc,
            )
            hours_to_settle = (settle_utc - now_utc).total_seconds() / 3600
            window = classify_window(hours_to_settle)
            time_bucket = classify_time_bucket(hours_to_settle)
            settle_utc_hour = settle_utc.hour
            forecast_lead = estimate_forecast_lead_hours(cycle_hour, settle_utc_hour)

            actual_model = str(forecast_info.get("source_model") or model)
            curve_key = (city, target_date, actual_model, forecast_info.get("values_hash"))
            if (
                should_capture_forecast_curve(forecast_info)
                and curve_key not in forecast_curve_seen
            ):
                forecast_curve_seen.add(curve_key)
                forecast_curve_rows.append(
                    build_curve_row(
                        snapshot_ts_utc=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        city=city,
                        target_date=target_date,
                        forecast_source=forecast_info["source_api"],
                        forecast_model=actual_model,
                        forecast_assigned_model=assigned_model,
                        forecast_values_hash=forecast_info["values_hash"],
                        hourly_curve=forecast_info["hourly_curve"],
                        forecast_max_f=forecast_info["max_f"],
                        forecast_peak_hour_local=forecast_info["peak_hour_local"],
                        forecast_peak_time_local=forecast_info["peak_time_local"],
                        forecast_peak_hour_utc=forecast_info["peak_hour_utc"],
                        forecast_peak_time_utc=forecast_info["peak_time_utc"],
                        forecast_timezone=forecast_info["timezone"],
                        forecast_timezone_abbreviation=forecast_info.get("timezone_abbreviation"),
                        forecast_utc_offset_seconds=forecast_info["utc_offset_seconds"],
                        forecast_generationtime_ms=forecast_info.get("generationtime_ms"),
                        forecast_model_fallback_reason=";".join(dict.fromkeys(fallback_reasons)) or None,
                        forecast_detected_at_utc=forecast_info.get("detected_at_utc"),
                        latitude=cfg.get("lat"),
                        longitude=cfg.get("lon"),
                    )
                )

            if (
                hours_to_settle < 0
                or hours_to_settle > 50
                or (external_orderbook_only and not canonical_ladder)
                or (not external_orderbook_only and not markets)
            ):
                continue

            now_local = city_local_datetime(city, now_utc)

            unit = cfg["unit"]

            # Fetch METAR state (live or cache)
            icao = cfg.get("icao", "")
            observation_station = resolve_observation_station(city, cfg, official_observation_configs)
            if args.observation_cache:
                metar_state = metar_state_from_observation_cache(
                    observation_index,
                    city,
                    target_date,
                )
            else:
                metar_state = fetch_live_metar_state(
                    weather_client,
                    observation_station["metar_icao"],
                    target_date,
                    city,
                    now_utc,
                )
            target_markets = (
                (canonical_ladder or {}).get("market_entries", [])
                if external_orderbook_only
                else markets
            )
            if args.orderbook_scope == "strategy_live":
                orderbook_targets = orderbook_targets_for_strategy_live(target_markets, unit, metar_state)
            elif args.orderbook_scope == "current_d1":
                orderbook_targets = orderbook_targets_for_current_yes(target_markets, unit, metar_state)
            else:
                orderbook_targets = None

            # Extract event-level market IDs
            event_id = ev_raw.get("id", "") if isinstance(ev_raw, dict) else ""
            n_recorded = 0

            # Build bracket list for compute_bracket_probs
            market_map = {}
            if external_orderbook_only:
                bracket_list = canonical_ladder["bracket_list"]
                market_entries = canonical_ladder["market_entries"]
            else:
                bracket_list, market_entries = gamma_market_ladder(markets)

            if not args.no_orderbook:
                if (
                    orderbook_disabled_reason is None
                    and args.orderbook_budget_sec >= 0
                    and orderbook_spent_sec >= args.orderbook_budget_sec
                ):
                    orderbook_disabled_reason = "orderbook_budget_exhausted"

                token_archive_rows = {}
                if orderbook_disabled_reason is None and not external_orderbook_only:
                    for entry in market_entries:
                        label = entry["label"]
                        for outcome_name, token_id in (("yes", entry["yes_token_id"]), ("no", entry["no_token_id"])):
                            if not token_id:
                                continue
                            if orderbook_targets is not None and (label, outcome_name) not in orderbook_targets:
                                continue
                            if token_id in orderbook_cache or token_id in token_archive_rows:
                                continue
                            token_archive_rows[token_id] = {
                                "type": "weather_paper_snapshot_orderbook",
                                "capture_reason": "scheduled_full_ladder_snapshot",
                                "trigger_event_id": None,
                                "snapshot_ts_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "snapshot_ts_beijing": now_beijing.strftime("%Y-%m-%d %H:%M:%S"),
                                "city": city,
                                "event_date": target_date,
                                "market_local_date": market_local_date,
                                "city_local_date_at_snapshot": city_local_date_at_snapshot,
                                "event_slug": slug,
                                "market_id": entry["market_id"],
                                "condition_id": entry["condition_id"],
                                "bracket": label,
                                "outcome": outcome_name,
                                "token_id": token_id,
                                "top_n": args.orderbook_top_n,
                            }
                    batch_started_at = time.monotonic()
                    remaining_budget_sec = (
                        max(0.0, args.orderbook_budget_sec - orderbook_spent_sec)
                        if args.orderbook_budget_sec >= 0
                        else None
                    )
                    fetched_books = fetch_token_orderbook_batch(
                        pm_client,
                        token_archive_rows,
                        top_n=args.orderbook_top_n,
                        max_workers=args.orderbook_workers,
                        deadline_monotonic=batch_started_at + remaining_budget_sec
                        if remaining_budget_sec is not None
                        else None,
                    )
                    orderbook_spent_sec += time.monotonic() - batch_started_at
                    for token_id, (archive_row, book) in fetched_books.items():
                        orderbook_cache[token_id] = book
                        append_orderbook_archive(orderbook_archive, {**archive_row, **book})

            for entry in market_entries:
                yes_book = {"status": "disabled", "summary": {}, "raw": {}, "fetched_at_utc": None}
                no_book = {"status": "disabled", "summary": {}, "raw": {}, "fetched_at_utc": None}
                if not args.no_orderbook:
                    yes_book = orderbook_for_entry(
                        orderbook_cache,
                        entry["yes_token_id"],
                        label=entry["label"],
                        outcome="yes",
                        targets=orderbook_targets,
                        disabled_reason=orderbook_disabled_reason,
                    )
                    no_book = orderbook_for_entry(
                        orderbook_cache,
                        entry["no_token_id"],
                        label=entry["label"],
                        outcome="no",
                        targets=orderbook_targets,
                        disabled_reason=orderbook_disabled_reason,
                    )
                market_map[entry["label"]] = {
                    **entry,
                    "yes_book": yes_book,
                    "no_book": no_book,
                }

            if not bracket_list:
                continue

            if errors is not None:
                model_results = compute_bracket_probs(fcst_f, errors, bracket_list, unit)
            else:
                model_results = [
                    {
                        "bracket": label,
                        "model_pct": None,
                        "market_pct": yes_price,
                        "edge": 0.0,
                    }
                    for label, yes_price in bracket_list
                ]

            for res in model_results:
                label = res["bracket"]
                model_p = res["model_pct"]
                yes_price = res["market_pct"]
                edge = res["edge"]
                info = market_map.get(label, {})
                if not info:
                    continue
                last_trade = info["last_trade"]
                question = info["question"]
                yes_token_id = info.get("yes_token_id", "")
                no_token_id = info.get("no_token_id", "")
                yes_book = info.get("yes_book", {})
                no_book = info.get("no_book", {})
                if probability_status == "ok":
                    side = "BUY_YES" if edge > 0 else "BUY_NO"
                    entry_price = yes_price if side == "BUY_YES" else (1.0 - yes_price)
                else:
                    side = "NO_SIGNAL"
                    entry_price = 0.0

                lo_f, hi_f = parse_bracket_bounds(label, unit)
                forecast_max_native = (fcst_f - 32) * 5 / 9 if unit == "C" else fcst_f
                forecast_max_in_bracket = (
                    int(lo_f <= fcst_f <= hi_f)
                    if lo_f is not None and hi_f is not None
                    else None
                )
                forecast_max_above_bracket_f = (
                    max(0.0, fcst_f - hi_f)
                    if hi_f is not None
                    else None
                )
                forecast_max_below_bracket_f = (
                    max(0.0, lo_f - fcst_f)
                    if lo_f is not None
                    else None
                )
                metar_max_f = metar_state["metar_current_max_f"]
                forecast_max_above_metar_max_f = (
                    fcst_f - metar_max_f
                    if metar_max_f is not None
                    else None
                )
                forecast_peak_delta_hours_local = (
                    now_local.hour + now_local.minute / 60.0 - forecast_info["peak_hour_local"]
                    if forecast_info.get("peak_hour_local") is not None
                    else None
                )

                record = {
                    "record_type": "edge_signal" if probability_status == "ok" else "market_snapshot_only",
                    "probability_status": probability_status,
                    # Existing fields (backward compatible)
                    "city": city,
                    "city_pool": city_pool(city),
                    "eligible_for_paper_order": eligible_for_paper_order(city),
                    "event_date": target_date,
                    "target_date": target_date,
                    "market_local_date": market_local_date,
                    "city_local_date_at_snapshot": city_local_date_at_snapshot,
                    "bracket": label,
                    "unit": unit,
                    "window": window,
                    "hours_to_settle": round(hours_to_settle, 1),
                    "gfs_forecast_f": round(fcst_f, 1),
                    "model": model,
                    "model_prob": round(model_p, 4) if model_p is not None else None,
                    "market_yes_price": round(yes_price, 4),
                    "edge": round(edge, 4),
                    "abs_edge": round(abs(edge), 4),
                    "side": side,
                    "entry_price": round(entry_price, 4),
                    "shares": args.shares,
                    "cost_usd": round(entry_price * args.shares, 2),
                    "last_trade_price": round(last_trade, 4),
                    "snapshot_ts_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "ts_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "ts_beijing": now_beijing.strftime("%Y-%m-%d %H:%M:%S"),
                    "ts_local": now_local.strftime("%Y-%m-%d %H:%M:%S"),
                    "tz_offset": tz,
                    "timezone_name": city_timezone_name(city),
                    "question": question,
                    "outcome": None,
                    # New cross-section fields
                    "time_bucket": time_bucket,
                    "icao": icao,
                    "configured_icao": observation_station["configured_icao"],
                    "metar_icao": observation_station["metar_icao"],
                    "official_observation_station": observation_station["official_observation_station"],
                    "settlement_source_class": observation_station["settlement_source_class"],
                    "live_observation_source": observation_station["live_observation_source"],
                    "source_profile_registry_class": observation_station["source_profile_registry_class"],
                    "source_profile_alignment_days": observation_station["source_profile_alignment_days"],
                    "source_profile_alignment_rate": observation_station["source_profile_alignment_rate"],
                    "forecast_max_f": round(fcst_f, 1),
                    "forecast_max_native": round(forecast_max_native, 1),
                    "forecast_peak_hour_local": forecast_info["peak_hour_local"],
                    "forecast_peak_time_local": forecast_info["peak_time_local"],
                    "forecast_peak_hour_utc": forecast_info["peak_hour_utc"],
                    "forecast_peak_time_utc": forecast_info["peak_time_utc"],
                    "forecast_hourly_count": forecast_info["hourly_count"],
                    "forecast_values_hash": forecast_info["values_hash"],
                    "forecast_peak_source": forecast_info["source_api"],
                    "forecast_timezone": forecast_info["timezone"],
                    "forecast_utc_offset_seconds": forecast_info["utc_offset_seconds"],
                    "forecast_peak_delta_hours_local": (
                        round(forecast_peak_delta_hours_local, 2)
                        if forecast_peak_delta_hours_local is not None
                        else None
                    ),
                    "forecast_max_in_bracket": forecast_max_in_bracket,
                    "forecast_max_above_bracket_f": (
                        round(forecast_max_above_bracket_f, 2)
                        if forecast_max_above_bracket_f is not None
                        else None
                    ),
                    "forecast_max_below_bracket_f": (
                        round(forecast_max_below_bracket_f, 2)
                        if forecast_max_below_bracket_f is not None
                        else None
                    ),
                    "forecast_max_above_metar_max_f": (
                        round(forecast_max_above_metar_max_f, 2)
                        if forecast_max_above_metar_max_f is not None
                        else None
                    ),
                    "forecast_source": f"open_meteo_live_{model}",
                    "forecast_model": actual_model,
                    "forecast_curve_evidence": (
                        "cached_durable_curve"
                        if forecast_info.get("cache_fallback")
                        else "fresh_curve_capture"
                    ),
                    "forecast_curve_archive_path": forecast_info.get("curve_archive_path"),
                    "forecast_curve_cache_age_sec": forecast_info.get("cache_age_sec"),
                    "model_init_utc_estimated": f"{cycle_hour:02d}Z",
                    "model_run_age_hours_estimated": round(model_run_age, 1),
                    "forecast_target_lead_hours_estimated": forecast_lead,
                    "settle_utc": settle_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "settle_local": city_local_datetime(city, settle_utc).isoformat(),
                    "market_end_source": market_end_source,
                    "metar_current_max_f": metar_state["metar_current_max_f"],
                    "metar_latest_temp_f": metar_state["metar_latest_temp_f"],
                    "metar_latest_ts_utc": metar_state["metar_latest_ts_utc"],
                    "metar_obs_count_today": metar_state["metar_obs_count_today"],
                    "metar_source": metar_state["metar_source"],
                    "event_slug": slug,
                    "market_id": market_map.get(label, {}).get("market_id", ""),
                    "condition_id": market_map.get(label, {}).get("condition_id", ""),
                    "clob_price_source": (
                        "disabled"
                        if args.no_orderbook
                        else f"clob_top_of_book_{args.orderbook_scope}"
                    ),
                    **prefixed_book_fields("yes", yes_token_id, yes_book, orderbook_archive),
                    **prefixed_book_fields("no", no_token_id, no_book, orderbook_archive),
                }
                all_records.append(record)
                n_recorded += 1

            if n_recorded > 0:
                print(f"  {city:<10} {target_date} | {window:<8} | {hours_to_settle:>5.1f}h | {model.upper()} {fcst_f:.0f}F | {n_recorded} brackets")

        time.sleep(0.3)

    # Save
    stamp = now_beijing.strftime("%Y%m%d_%H%M")
    fname = f"snapshot_{stamp}.json"
    partial_fname = f"partial_snapshot_{stamp}.json"
    publish_quality = snapshot_publish_quality(all_records)
    orderbook_enrichment_summary = summarize_orderbook_enrichment(
        all_records,
        scope=args.orderbook_scope,
        budget_sec=args.orderbook_budget_sec,
        spent_sec=orderbook_spent_sec,
    )
    source_model_summary = summarize_source_models(
        forecast_curve_rows,
        expected_city_target_count=forecast_city_target_expected,
    )
    source_model_summary["cached_curve_fallback_count"] = len(cached_forecast_refs)
    source_model_summary["cached_curve_fallback_examples"] = cached_forecast_refs[:10]
    source_model_summary["effective_city_target_count"] = (
        source_model_summary["captured_city_target_count"] + len(cached_forecast_refs)
    )
    source_model_summary["missing_count"] = max(
        0,
        forecast_city_target_expected - source_model_summary["effective_city_target_count"],
    )
    curve_publish_evidence = forecast_curve_publish_evidence(
        all_records,
        forecast_curve_rows,
        cached_forecast_refs,
    )
    out_file = OUTPUT_DIR / fname if publish_quality["publishable"] else PARTIAL_OUTPUT_DIR / partial_fname
    output = {
        "ts_beijing": now_beijing.strftime("%Y-%m-%d %H:%M:%S"),
        "ts_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "shares_per_trade": args.shares,
        "city_models": all_models,
        "source_model_summary": source_model_summary,
        "forecast_curve_publish_evidence": curve_publish_evidence,
        "city_pools": CITY_POOL_BY_CITY,
        "trading_t1_cities": sorted(TRADING_T1_CITIES),
        "research_t2_cities": sorted(set(CITIES) - set(TRADING_T1_CITIES)),
        "total_records": len(all_records),
        "records": all_records,
        "schema_version": "v3_cross_section_forecast_peak_clock",
        "data_feed_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_publish_quality": publish_quality,
        "orderbook_enrichment_summary": orderbook_enrichment_summary,
        "canonical_orderbook_source": canonical_orderbook_source,
    }
    forecast_curve_archive = None
    if publish_quality["publishable"] and not curve_publish_evidence["publishable"]:
        raise RuntimeError(
            "refusing to publish snapshot without matching forecast curve evidence: "
            f"missing_count={curve_publish_evidence['missing_count']}"
        )
    if publish_quality["publishable"] and forecast_curve_rows:
        forecast_curve_archive = write_forecast_hourly_curve_capture(OUTPUT_ROOT, forecast_curve_rows)
    stamp_snapshot_availability(output)
    annotate_market_ladder_snapshot(
        output,
        producer="weather_data_feed_service.legacy_weather_predict.paper_snapshot",
        producer_build_id=PRODUCER_BUILD_ID,
    )
    # The final snapshot path is the batch commit marker. Orderbook archive and
    # forecast curves must be durable before live consumers can discover it.
    publish_json_atomic(out_file, output)

    # Print summary
    edge_trades = [r for r in all_records if r["abs_edge"] >= 0.05]
    print(f"\n{'='*90}")
    print(f" Saved: {out_file}")
    if forecast_curve_archive is not None:
        print(f" Forecast curves: {forecast_curve_archive} ({len(forecast_curve_rows)} city-dates)")
    print(f" Publishable: {publish_quality['publishable']} | reasons: {publish_quality['reasons']}")
    if not publish_quality["publishable"]:
        print(" Partial snapshot archived without replacing live snapshot_*.json")
    print(f" Total brackets: {len(all_records)} | Edge>=5%: {len(edge_trades)}")
    print(f"{'='*90}")

    if edge_trades:
        edge_trades.sort(key=lambda x: -x["abs_edge"])
        print(f"\n Edge>=5% trades ({args.shares} shares each):")
        print(f" {'City':<10}{'Date':<12}{'Brkt':<7}{'Bucket':<16}{'Model':<6}{'Side':<8}{'Mdl':>5}{'Mkt':>5}{'Edge':>7}{'Cost$':>7}{'H2S':>6}{'Lead':>5}{'METAR':>7}")
        print(f" {'-'*105}")
        for r in edge_trades:
            mdl = r.get('model', 'gfs').upper()
            lead = r.get('forecast_target_lead_hours_estimated', '')
            lead_str = f"{lead}h" if lead else ""
            metar_max = r.get('metar_current_max_f')
            metar_str = f"{metar_max}F" if metar_max is not None else "-"
            bucket = r.get('time_bucket', r.get('window', ''))
            print(f" {r['city']:<10}{r['event_date']:<12}{r['bracket']:<7}{bucket:<16}{mdl:<6}{r['side']:<8}{r['model_prob']*100:>4.1f}%{r['market_yes_price']*100:>4.1f}%{r['edge']*100:>+6.1f}%{r['cost_usd']:>7.2f}{r['hours_to_settle']:>5.1f}h{lead_str:>5}{metar_str:>7}")

        by = [r for r in edge_trades if r["side"] == "BUY_YES"]
        bn = [r for r in edge_trades if r["side"] == "BUY_NO"]
        total_cost = sum(r["cost_usd"] for r in edge_trades)
        print(f"\n BUY_YES={len(by)} | BUY_NO={len(bn)} | Total cost=${total_cost:.2f}")

        # By window and time_bucket
        from collections import Counter
        wc = Counter(r["window"] for r in edge_trades)
        bc = Counter(r.get("time_bucket", "?") for r in edge_trades)
        print(f" By window: {dict(wc)}")
        print(f" By bucket: {dict(bc)}")


if __name__ == "__main__":
    main()
