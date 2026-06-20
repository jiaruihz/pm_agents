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
import httpx
import numpy as np
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
    unique_city_scan_dates as data_feed_unique_city_scan_dates,
)

PM_GAMMA_URL = "https://gamma-api.polymarket.com"
PM_CLOB_URL = "https://clob.polymarket.com"
DEFAULT_RUNTIME_DIR = PROJECT_DIR.parent / "runtime"
OUTPUT_ROOT = Path(os.environ.get("WEATHER_DATA_FEED_OUTPUT_ROOT", DEFAULT_RUNTIME_DIR / "output"))
CACHE_ROOT = Path(os.environ.get("WEATHER_DATA_FEED_CACHE_ROOT", DEFAULT_RUNTIME_DIR / "cache"))
OUTPUT_DIR = OUTPUT_ROOT / "paper_snapshots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
ORDERBOOK_OUTPUT_DIR = OUTPUT_ROOT / "orderbook_snapshots"
ORDERBOOK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PM_HTTP_TIMEOUT = httpx.Timeout(5.0, connect=2.0, read=5.0, write=2.0, pool=2.0)
WEATHER_HTTP_TIMEOUT = httpx.Timeout(5.0, connect=2.0, read=5.0, write=2.0, pool=2.0)
PM_HTTP_LIMITS = httpx.Limits(max_connections=8, max_keepalive_connections=0)
WEATHER_HTTP_LIMITS = httpx.Limits(max_connections=8, max_keepalive_connections=0)
DEFAULT_ORDERBOOK_SCOPE = os.environ.get("WEATHER_DATA_FEED_ORDERBOOK_SCOPE", "current_d1")
DEFAULT_ORDERBOOK_BUDGET_SEC = float(os.environ.get("WEATHER_DATA_FEED_ORDERBOOK_BUDGET_SEC", "30"))

BASE_SHARES = 10


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


def fetch_token_orderbook(client, token_id, top_n=20):
    if not token_id:
        return {
            "status": "missing_token",
            "token_id": token_id,
            "fetched_at_utc": None,
            "summary": {},
            "raw": {},
        }
    fetched_at_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        r = client.get(f"{PM_CLOB_URL}/book", params={"token_id": token_id})
        if r.status_code == 404:
            return {
                "status": "not_found",
                "token_id": token_id,
                "fetched_at_utc": fetched_at_utc,
                "summary": {},
                "raw": {},
            }
        r.raise_for_status()
        raw = r.json()
        summary = summarize_orderbook(raw, top_n=top_n)
        return {
            "status": "ok",
            "token_id": token_id,
            "fetched_at_utc": fetched_at_utc,
            "summary": summary,
            "raw": {
                "bids": summary["bids"],
                "asks": summary["asks"],
            },
        }
    except Exception as exc:
        return {
            "status": "error",
            "token_id": token_id,
            "fetched_at_utc": fetched_at_utc,
            "error": f"{type(exc).__name__}: {exc}",
            "summary": {},
            "raw": {},
        }


def append_orderbook_archive(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "at", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


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
        f"{prefix}_book_archive_path": str(archive_path) if archive_path else "",
    }

# Per-city best model based on v4 calibration RMSE comparison
# Source: calibration_results_v4.json, 354 days × 51 cities
CITY_MODEL = {
    "Amsterdam": "ecmwf",
    "Ankara": "ecmwf",
    "Atlanta": "gfs",
    "BuenosAires": "ecmwf",
    "Busan": "ecmwf",
    "CapeTown": "ecmwf",
    "Chengdu": "ecmwf",
    "Chicago": "gfs", "Miami": "gfs", "Austin": "gfs", "NYC": "gfs",
    "Chongqing": "ecmwf",
    "Dallas": "ecmwf",
    "Denver": "gfs",
    "Guangzhou": "gfs",
    "Helsinki": "ecmwf",
    "HongKong": "ecmwf",
    "Houston": "gfs",
    "Istanbul": "ecmwf",
    "Jakarta": "ecmwf",
    "Jeddah": "ecmwf",
    "Karachi": "ecmwf",
    "KualaLumpur": "ecmwf",
    "LA": "gfs", "Boston": "gfs", "Phoenix": "gfs",
    "Lagos": "ecmwf",
    "London": "ecmwf", "Madrid": "ecmwf", "Warsaw": "ecmwf",
    "Lucknow": "ecmwf",
    "Manila": "gfs",
    "MexicoCity": "ecmwf",
    "Milan": "ecmwf",
    "Minneapolis": "gfs",
    "Moscow": "ecmwf",
    "Munich": "ecmwf",
    "PanamaCity": "gfs",
    "Beijing": "ecmwf", "Seoul": "ecmwf",
    "Paris": "gfs", "Tokyo": "gfs", "Shanghai": "gfs",
    "SanFrancisco": "ecmwf",
    "SaoPaulo": "ecmwf",
    "Seattle": "gfs",
    "Shenzhen": "ecmwf",
    "Singapore": "gfs",
    "Taipei": "gfs",
    "TelAviv": "gfs",
    "Wellington": "gfs",
    "Wuhan": "ecmwf",
}


def _forecast_values_hash(times, temps):
    payload = [
        [str(t), None if temp is None else round(float(temp), 3)]
        for t, temp in zip(times or [], temps or [])
    ]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _forecast_details_from_open_meteo(payload, *, source_model):
    hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
    times = hourly.get("time", []) or []
    temps = hourly.get("temperature_2m", []) or []
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

    return {
        "max_f": max_f,
        "peak_time_local": peak_local_time,
        "peak_hour_local": peak_hour_local,
        "peak_time_utc": peak_time_utc,
        "peak_hour_utc": peak_hour_utc,
        "hourly_count": len(pairs),
        "values_hash": _forecast_values_hash(times, temps),
        "source_model": source_model,
        "source_api": f"open_meteo_live_{source_model}",
        "timezone": payload.get("timezone"),
        "timezone_abbreviation": payload.get("timezone_abbreviation"),
        "utc_offset_seconds": utc_offset_seconds,
        "generationtime_ms": payload.get("generationtime_ms"),
    }


def _fetch_live_forecast(client, model, city, cfg, target_date):
    url = f"https://api.open-meteo.com/v1/{model}"
    params = {
        "latitude": cfg["lat"], "longitude": cfg["lon"],
        "hourly": "temperature_2m", "temperature_unit": "fahrenheit",
        "timezone": "auto",
        "start_date": target_date, "end_date": target_date,
    }
    try:
        r = client.get(url, params=params)
        if r.status_code == 200:
            return _forecast_details_from_open_meteo(r.json(), source_model=model)
    except:
        pass
    return None


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
    ecmwf_hourly = ecmwf_data["hourly"]
    ecmwf_times = ecmwf_hourly["time"]
    ecmwf_temps = ecmwf_hourly["temperature_2m"]

    ecmwf_daily_max = {}
    for t, temp in zip(ecmwf_times, ecmwf_temps):
        if temp is None:
            continue
        d = t[:10]
        ecmwf_daily_max[d] = max(ecmwf_daily_max.get(d, -999), temp)

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
        r = client.get(url, params=params)
        if r.status_code == 200:
            data = r.json()
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


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date", default=None)
    parser.add_argument("--shares", type=int, default=BASE_SHARES)
    parser.add_argument("--orderbook-top-n", type=int, default=20)
    parser.add_argument(
        "--orderbook-scope",
        choices=("current_d1", "all"),
        default=DEFAULT_ORDERBOOK_SCOPE if DEFAULT_ORDERBOOK_SCOPE in {"current_d1", "all"} else "current_d1",
        help="Fetch orderbooks only for current-YES required brackets by default; use all for research snapshots.",
    )
    parser.add_argument(
        "--orderbook-budget-sec",
        type=float,
        default=DEFAULT_ORDERBOOK_BUDGET_SEC,
        help="Maximum wall-clock seconds spent on orderbook enrichment before continuing snapshot generation.",
    )
    parser.add_argument("--no-orderbook", action="store_true", help="Disable CLOB orderbook enrichment.")
    parser.add_argument("--now-utc", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    now_utc = parse_now_utc(args.now_utc)
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
    orderbook_started_at = time.monotonic()
    orderbook_disabled_reason = "disabled" if args.no_orderbook else None

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

    for city, cfg in CITIES.items():
        for target_date in city_scan_dates(now_utc, city, args.target_date):
            tz = cfg["tz_offset"]
            market_local_date = target_date
            city_local_date_at_snapshot = data_feed_city_local_date(city, now_utc).isoformat()
            settle_utc = local_settle_utc(city, target_date)
            hours_to_settle = (settle_utc - now_utc).total_seconds() / 3600

            if hours_to_settle < 0 or hours_to_settle > 50:
                continue

            window = classify_window(hours_to_settle)
            time_bucket = classify_time_bucket(hours_to_settle)

            # Estimate model cycle metadata (initial, may be updated after fallback)
            model = all_models.get(city, "gfs")
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
                        cycle_hour, model_run_age = estimate_model_cycle(now_utc_hour, model)
                        forecast_lead = estimate_forecast_lead_hours(cycle_hour, settle_utc_hour)
            probability_status = "ok" if errors is not None else "missing_error_distribution"

            # Fetch PM event
            city_slug = cfg.get("slug", city.lower())
            dt = datetime.strptime(target_date, "%Y-%m-%d")
            date_slug = dt.strftime("%B-%-d-%Y").lower()
            slug = f"highest-temperature-in-{city_slug}-on-{date_slug}"

            try:
                r = pm_client.get(f"{PM_GAMMA_URL}/events", params={"slug": slug})
                ev_raw = r.json()
                if isinstance(ev_raw, list) and len(ev_raw) > 0:
                    ev_raw = ev_raw[0]
                markets = ev_raw.get("markets", []) if isinstance(ev_raw, dict) else []
            except:
                continue

            if not markets:
                continue

            now_local = city_local_datetime(city, now_utc)

            unit = cfg["unit"]

            # Fetch METAR state (live or cache)
            icao = cfg.get("icao", "")
            observation_station = resolve_observation_station(city, cfg, official_observation_configs)
            metar_state = fetch_live_metar_state(
                weather_client,
                observation_station["metar_icao"],
                target_date,
                city,
                now_utc,
            )
            orderbook_targets = (
                orderbook_targets_for_current_yes(markets, unit, metar_state)
                if args.orderbook_scope == "current_d1"
                else None
            )

            # Extract event-level market IDs
            event_id = ev_raw.get("id", "") if isinstance(ev_raw, dict) else ""
            n_recorded = 0

            # Build bracket list for compute_bracket_probs
            bracket_list = []
            market_map = {}
            for mkt in markets:
                question = mkt.get("question", "")
                label = _extract_bracket_label(question)
                if label is None:
                    continue
                op = mkt.get("outcomePrices")
                if op:
                    try:
                        prices = json.loads(op) if isinstance(op, str) else op
                        yes_price = float(prices[0])
                    except:
                        continue
                else:
                    continue
                if yes_price <= 0.001 or yes_price >= 0.999:
                    continue
                bracket_list.append((label, yes_price))
                last_trade = float(mkt.get("lastTradePrice", 0) or 0)
                market_id = mkt.get("id", "")
                tokens = extract_market_tokens(mkt)
                yes_token_id = tokens["yes"]
                no_token_id = tokens["no"]
                yes_book = {"status": "disabled", "summary": {}, "raw": {}, "fetched_at_utc": None}
                no_book = {"status": "disabled", "summary": {}, "raw": {}, "fetched_at_utc": None}
                if not args.no_orderbook:
                    if (
                        orderbook_disabled_reason is None
                        and args.orderbook_budget_sec >= 0
                        and time.monotonic() - orderbook_started_at > args.orderbook_budget_sec
                    ):
                        orderbook_disabled_reason = "orderbook_budget_exhausted"
                    for outcome_name, token_id in (("yes", yes_token_id), ("no", no_token_id)):
                        if not token_id:
                            continue
                        if orderbook_disabled_reason is not None:
                            continue
                        if orderbook_targets is not None and (label, outcome_name) not in orderbook_targets:
                            continue
                        if token_id not in orderbook_cache:
                            orderbook_cache[token_id] = fetch_token_orderbook(
                                pm_client,
                                token_id,
                                top_n=args.orderbook_top_n,
                            )
                            append_orderbook_archive(
                                orderbook_archive,
                                {
                                    "type": "weather_paper_snapshot_orderbook",
                                    "snapshot_ts_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                    "snapshot_ts_beijing": now_beijing.strftime("%Y-%m-%d %H:%M:%S"),
                                    "city": city,
                                    "event_date": target_date,
                                    "market_local_date": market_local_date,
                                    "city_local_date_at_snapshot": city_local_date_at_snapshot,
                                    "event_slug": slug,
                                    "market_id": market_id,
                                    "condition_id": mkt.get("conditionId", ""),
                                    "bracket": label,
                                    "outcome": outcome_name,
                                    "token_id": token_id,
                                    "top_n": args.orderbook_top_n,
                                    **orderbook_cache[token_id],
                                },
                            )
                    yes_book = orderbook_cache.get(yes_token_id, yes_book)
                    no_book = orderbook_cache.get(no_token_id, no_book)
                    if yes_book.get("status") == "disabled":
                        yes_book = orderbook_disabled_book(orderbook_disabled_reason or "orderbook_scope_skipped")
                    if no_book.get("status") == "disabled":
                        no_book = orderbook_disabled_book(orderbook_disabled_reason or "orderbook_scope_skipped")
                market_map[label] = {
                    "yes_price": yes_price,
                    "last_trade": last_trade,
                    "question": question,
                    "market_id": market_id,
                    "condition_id": mkt.get("conditionId", ""),
                    "yes_token_id": yes_token_id,
                    "no_token_id": no_token_id,
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
                    "model_init_utc_estimated": f"{cycle_hour:02d}Z",
                    "model_run_age_hours_estimated": round(model_run_age, 1),
                    "forecast_target_lead_hours_estimated": forecast_lead,
                    "settle_utc": settle_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "settle_local": f"{target_date}T22:00:00",
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

    pm_client.close()
    weather_client.close()

    # Save
    fname = f"snapshot_{now_beijing.strftime('%Y%m%d_%H%M')}.json"
    out_file = OUTPUT_DIR / fname
    output = {
        "ts_beijing": now_beijing.strftime("%Y-%m-%d %H:%M:%S"),
        "ts_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "shares_per_trade": args.shares,
        "city_models": all_models,
        "city_pools": CITY_POOL_BY_CITY,
        "trading_t1_cities": sorted(TRADING_T1_CITIES),
        "research_t2_cities": sorted(set(CITIES) - set(TRADING_T1_CITIES)),
        "total_records": len(all_records),
        "records": all_records,
        "schema_version": "v3_cross_section_forecast_peak_clock",
        "data_feed_schema_version": SNAPSHOT_SCHEMA_VERSION,
    }
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Print summary
    edge_trades = [r for r in all_records if r["abs_edge"] >= 0.05]
    print(f"\n{'='*90}")
    print(f" Saved: {out_file}")
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
