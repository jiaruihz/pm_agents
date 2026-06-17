#!/usr/bin/env python3
"""Tiny-live runner for the no-reheat current-bucket YES rule.

This is an independent live branch for the frozen v9 rule:

  BUY_YES current running-max bracket
  local hour 13-15, decline_c >= 0.5
  yes_ask >= 0.55, p_yes_win >= 0.5, p_yes_win - yes_ask >= 0.05
  fresh CLOB ask rechecked before execution, fresh_ask <= snapshot_ask + 0.02
  d1 NO sibling quote visible
  $5/order, $10/city-day cap, top-of-book available notional >= $5

It writes standard weather_edge_trade_plan JSONL rows and can hand them to the
existing weather_order_executor.  Default mode is plan-only; live submission
requires both --live and --confirm-live.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.execution_pipeline import read_jsonl, stable_hash
from src.strategies.weather_edge_v1.tools.live_state import read_live_state
from src.strategies.weather_edge_v1.official_observation_feed.market_brackets import (
    bracket_contains,
    parse_label_dict,
)
from src.strategies.weather_edge_v1.tools.official_observation_clock import (
    ObservationClockConfig,
    city_timezone_name,
    observation_clock_guard,
    station_timezone,
    timezone_label,
)


STRATEGY_INSTANCE = "theta_current_yes_tiny_live_v1"
STRATEGY_ID = "theta_current_yes_no_reheat_live5_v1"
TRAIN_FEATURES = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_full_replay_v8/feature_rows.csv"
MODEL_ARTIFACT = ROOT / "docs/analysis/2026-06/generated/theta_yes_current_live_gate_v9/live_model.json"
STATION_SUMMARY = ROOT / "docs/analysis/2026-06/generated/theta_no_wu_obs_patch_v1/summary.json"
RUNTIME_DIR = ROOT / "runtime/weather_edge_v1/theta_current_yes_tiny_live_v1"
PLAN_OUT = RUNTIME_DIR / "trade_plans.jsonl"
SUMMARY_OUT = RUNTIME_DIR / "latest_summary.json"
HISTORY_OUT = RUNTIME_DIR / "summary_history.jsonl"
PAPER_OUT = RUNTIME_DIR / "paper_orders.jsonl"
LIVE_OUT = ROOT / "runtime/weather_edge_v1/live/theta_current_yes_tiny_live_v1_orders.jsonl"

METAR_API = "https://aviationweather.gov/api/data/metar"
IEM_ASOS_API = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
CLOB_BOOK_API = "https://clob.polymarket.com/book"

BASE_FEATURES = [
    "decision_hour_local",
    "month",
    "decline_c",
    "decline_native",
    "decline_band",
    "gap_running_to_d1_low_native",
    "gap_current_to_d1_low_native",
    "running_value",
    "current_native",
    "running_native",
    "tmpf_now",
    "dwpf_now",
    "dewpoint_depression_f",
    "relh_now",
    "sknt_now",
    "sky_now",
    "d_tmpf_1h",
    "d_tmpf_3h",
    "d_dwpf_3h",
    "d_relh_3h",
]
PRICE_FEATURES = ["yes_current_ask", "log_yes_size", "d1_no_ask", "ask_gap_d1_no_minus_yes"]
CAT_FEATURES = ["city", "unit"]
MODEL_FEATURES = BASE_FEATURES + PRICE_FEATURES + CAT_FEATURES

SKY_CODE = {"CLR": 0, "SKC": 0, "NSC": 0, "NCD": 0, "CAVOK": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "VV": 4}
MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


@dataclass(frozen=True)
class Station:
    city: str
    icao: str
    unit: str
    utc_offset: int
    timezone_name: str | None = None


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def parse_env_value(raw_value: str) -> str:
    try:
        return shlex.split(raw_value, comments=False, posix=True)[0] if raw_value.strip() else ""
    except Exception:
        return raw_value.strip().strip("\"'")


def proxy_candidates() -> list[str | None]:
    candidates: list[str | None] = []
    for key in ("WEATHER_PREDICT_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        value = os.environ.get(key)
        if value:
            candidates.append(value)
    env_path = Path(os.environ.get("WEATHER_PREDICT_DIR", "/home/jiarui/projects/weather-predict")) / ".env"
    if env_path.exists():
        values: dict[str, str] = {}
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if key in {"WEATHER_PREDICT_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"}:
                values[key] = parse_env_value(raw_value)
        for key in ("WEATHER_PREDICT_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
            if values.get(key):
                candidates.append(values[key])
    candidates.extend(["http://127.0.0.1:7897", "http://127.0.0.1:7890", None])
    out: list[str | None] = []
    for item in candidates:
        if item not in out:
            out.append(item)
    return out


PROXIES = proxy_candidates()


def fetch_json(url: str, params: dict | None = None, max_rounds: int = 2) -> Any:
    last = None
    for rnd in range(max_rounds):
        for proxy in PROXIES:
            try:
                r = httpx.get(url, params=params, proxy=proxy, timeout=20)
                r.raise_for_status()
                return r.json()
            except Exception as exc:  # noqa: BLE001
                last = f"{proxy}: {type(exc).__name__}: {exc}"
                time.sleep(0.4 + rnd)
    raise RuntimeError(f"fetch failed {url}: {last}")


def fetch_text(url: str, params: list[tuple[str, Any]], max_rounds: int = 2) -> str:
    last = None
    for rnd in range(max_rounds):
        for proxy in PROXIES:
            try:
                r = httpx.get(url, params=params, proxy=proxy, timeout=20)
                r.raise_for_status()
                return r.text
            except Exception as exc:  # noqa: BLE001
                last = f"{proxy}: {type(exc).__name__}: {exc}"
                time.sleep(0.4 + rnd)
    raise RuntimeError(f"fetch failed {url}: {last}")


def book_levels(book: Any, side: str) -> list[tuple[float, float]]:
    rows = book.get(f"{side}s") if isinstance(book, dict) else []
    out: list[tuple[float, float]] = []
    for item in rows or []:
        try:
            price = float(item.get("price"))
            size = float(item.get("size"))
        except Exception:
            continue
        if price > 0 and size > 0:
            out.append((price, size))
    return sorted(out, reverse=(side == "bid"))


def fresh_taker_quote(row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    book = fetch_json(CLOB_BOOK_API, {"token_id": str(row["token_id"])}, max_rounds=1)
    asks = book_levels(book, "ask")
    bids = book_levels(book, "bid")
    if not asks:
        return {"status": "rejected", "reason": "fresh_book_no_ask", "best_bid": bids[0][0] if bids else 0.0}
    fresh_ask, fresh_ask_size = asks[0]
    p_yes = float(row["p_yes_win"])
    snapshot_ask = float(row["yes_current_ask"])
    max_by_cushion = snapshot_ask + float(args.max_taker_cushion)
    max_price = min(max_by_cushion, 0.999)
    limit_price = min(fresh_ask + float(args.cross_tick_buffer), max_price)
    fresh_available = fresh_ask * fresh_ask_size
    if fresh_ask > max_price + 1e-9:
        return {
            "status": "rejected",
            "reason": "fresh_ask_exceeds_cushion",
            "best_bid": bids[0][0] if bids else 0.0,
            "fresh_ask": fresh_ask,
            "fresh_ask_size": fresh_ask_size,
            "fresh_available_notional": fresh_available,
            "max_taker_price": max_price,
            "edge_at_fresh_ask": p_yes - fresh_ask,
        }
    if fresh_available + 1e-9 < float(args.max_order_notional):
        return {
            "status": "rejected",
            "reason": "fresh_ask_insufficient_size",
            "best_bid": bids[0][0] if bids else 0.0,
            "fresh_ask": fresh_ask,
            "fresh_ask_size": fresh_ask_size,
            "fresh_available_notional": fresh_available,
            "max_taker_price": max_price,
            "edge_at_fresh_ask": p_yes - fresh_ask,
        }
    return {
        "status": "accepted",
        "best_bid": bids[0][0] if bids else 0.0,
        "fresh_ask": fresh_ask,
        "fresh_ask_size": fresh_ask_size,
        "fresh_available_notional": fresh_available,
        "max_taker_price": max_price,
        "limit_price": limit_price,
        "edge_at_fresh_ask": p_yes - fresh_ask,
        "edge_at_limit": p_yes - limit_price,
        "expected_profit_usd": float(args.max_order_notional) * (p_yes / limit_price - 1.0),
        "derived_min_edge_after_full_cushion": max(0.0, 0.05 - float(args.max_taker_cushion)),
        "cushion_paid_vs_snapshot": limit_price - snapshot_ask,
    }


def parse_utc(value: Any) -> datetime | None:
    text = safe_str(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def snapshot_dir() -> Path:
    candidates: list[Path] = []
    for key in ("THETA_CURRENT_YES_SNAPSHOT_DIR", "WEATHER_PREDICT_PAPER_SNAPSHOT_DIR"):
        if os.environ.get(key):
            candidates.append(Path(str(os.environ[key])).expanduser())
    if os.environ.get("WEATHER_PREDICT_DIR"):
        candidates.append(Path(str(os.environ["WEATHER_PREDICT_DIR"])).expanduser() / "output/paper_snapshots")
    candidates.append(Path("/home/jiarui/projects/weather-predict/output/paper_snapshots"))
    candidates.append(ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots")
    for path in candidates:
        if path.exists():
            return path
    return candidates[-1]


def latest_snapshot() -> Path | None:
    files = sorted(snapshot_dir().glob("snapshot_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def load_stations() -> dict[str, Station]:
    data = json.loads(STATION_SUMMARY.read_text(encoding="utf-8"))
    return {
        str(item["city"]): Station(
            city=str(item["city"]),
            icao=str(item["icao"]).upper(),
            unit=str(item["unit"]).upper(),
            utc_offset=int(item["utc_offset"]),
            timezone_name=safe_str(item.get("timezone")) or city_timezone_name(str(item["city"])),
        )
        for item in data["stations"]
    }


def load_model_artifact() -> dict[str, Any]:
    return json.loads(MODEL_ARTIFACT.read_text(encoding="utf-8"))


def score_rows(rows: pd.DataFrame, artifact: dict[str, Any]) -> np.ndarray:
    numeric_features = list(artifact["numeric_features"])
    categorical_features = list(artifact["categorical_features"])
    numeric = rows[numeric_features].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    medians = np.asarray(artifact["numeric_medians"], dtype=float)
    means = np.asarray(artifact["numeric_means"], dtype=float)
    scales = np.asarray(artifact["numeric_scales"], dtype=float)
    numeric = np.where(np.isfinite(numeric), numeric, medians)
    numeric = (numeric - means) / scales

    cat_parts = []
    categories = artifact["categories"]
    for idx, feature in enumerate(categorical_features):
        values = rows[feature].astype(str).to_numpy()
        cats = [str(x) for x in categories[idx]]
        mat = np.zeros((len(rows), len(cats)), dtype=float)
        lookup = {cat: i for i, cat in enumerate(cats)}
        for row_idx, value in enumerate(values):
            col_idx = lookup.get(str(value))
            if col_idx is not None:
                mat[row_idx, col_idx] = 1.0
        cat_parts.append(mat)
    transformed = np.concatenate([numeric, *cat_parts], axis=1)
    coef = np.asarray(artifact["coef"], dtype=float)
    logits = transformed @ coef + float(artifact["intercept"])
    return 1.0 / (1.0 + np.exp(-logits))


def parse_label(label: str, question: str = "") -> dict[str, Any] | None:
    return parse_label_dict(label, question, include_label=True)


def round_half_up(x: float) -> int:
    return math.floor(float(x) + 0.5)


def aviationweather_obs(icao: str, tz: ZoneInfo, local_date) -> list[dict[str, Any]]:
    data = fetch_json(METAR_API, {"ids": icao, "format": "json", "hours": "30"})
    out = []
    for rec in data:
        ts = parse_utc(rec.get("reportTime"))
        if ts is None or ts.astimezone(tz).date() != local_date:
            continue
        temp_c = rec.get("temp")
        if temp_c is None:
            continue
        out.append(
            {
                "ts": ts,
                "tmpc": to_float(temp_c, np.nan),
                "dwpc": to_float(rec.get("dewp"), np.nan),
                "relh": to_float(rec.get("relh"), np.nan),
                "sknt": to_float(rec.get("wspd"), np.nan),
                "sky": np.nan,
            }
        )
    return out


def iem_obs(icao: str, tz: ZoneInfo, local_date) -> list[dict[str, Any]]:
    local_start = datetime.combine(local_date, datetime.min.time(), tzinfo=tz)
    start_utc = local_start.astimezone(timezone.utc)
    end_utc = (local_start + timedelta(days=1)).astimezone(timezone.utc) + timedelta(days=1)
    params = [("station", icao)]
    for col in ("tmpc", "dwpc", "relh", "sknt", "skyc1"):
        params.append(("data", col))
    params.extend(
        [
            ("year1", start_utc.year),
            ("month1", start_utc.month),
            ("day1", start_utc.day),
            ("year2", end_utc.year),
            ("month2", end_utc.month),
            ("day2", end_utc.day),
            ("tz", "Etc/UTC"),
            ("format", "onlycomma"),
            ("latlon", "no"),
            ("elev", "no"),
            ("missing", "M"),
            ("trace", "T"),
            ("direct", "no"),
            ("report_type", "1"),
            ("report_type", "2"),
            ("report_type", "3"),
            ("report_type", "4"),
        ]
    )
    text = fetch_text(IEM_ASOS_API, params)
    rows = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    out = []
    for row in csv.DictReader(io.StringIO("\n".join(rows))):
        ts = parse_utc(str(row.get("valid", "")).replace(" ", "T"))
        if ts is None or ts.astimezone(tz).date() != local_date:
            continue
        raw_tmp = row.get("tmpc")
        if raw_tmp in {None, "", "M"}:
            continue
        sky_raw = safe_str(row.get("skyc1")).upper()
        out.append(
            {
                "ts": ts,
                "tmpc": to_float(raw_tmp, np.nan),
                "dwpc": to_float(row.get("dwpc"), np.nan),
                "relh": to_float(row.get("relh"), np.nan),
                "sknt": to_float(row.get("sknt"), np.nan),
                "sky": SKY_CODE.get(sky_raw, np.nan),
            }
        )
    return out


def fetch_obs(
    station: Station,
    now: datetime,
    *,
    max_obs_age_min: float,
    pre_update_blackout_min: float,
) -> dict[str, Any]:
    tz = station_timezone(station)
    local_date = now.astimezone(tz).date()
    try:
        obs = aviationweather_obs(station.icao, tz, local_date)
        source = "aviationweather_metar"
    except Exception as exc:  # noqa: BLE001
        obs = []
        source = f"aviationweather_failed:{type(exc).__name__}"
    if len(obs) < 6:
        try:
            obs = iem_obs(station.icao, tz, local_date)
            source = "iem_asos"
        except Exception as exc:  # noqa: BLE001
            return {"status": "obs_fetch_failed", "source": source, "error": f"{type(exc).__name__}: {exc}", "n_obs": len(obs)}
    status, common, obs = observation_clock_guard(
        obs,
        now,
        station=station,
        source=source,
        config=ObservationClockConfig(
            max_obs_age_min=max_obs_age_min,
            pre_update_blackout_min=pre_update_blackout_min,
        ),
    )
    if status != "ok":
        return {"status": status, **common}
    last = obs[-1]
    temps = [float(x["tmpc"]) for x in obs if math.isfinite(float(x["tmpc"]))]
    if not temps:
        return {"status": "missing_temp", **common}
    def asof(minutes: int, key: str) -> float:
        target = now - timedelta(minutes=minutes)
        prev = [x for x in obs if x["ts"] <= target]
        if not prev:
            return np.nan
        return to_float(prev[-1].get(key), np.nan)

    tmpc_now = to_float(last.get("tmpc"), np.nan)
    dwpc_now = to_float(last.get("dwpc"), np.nan)
    tmpf_now = tmpc_now * 9.0 / 5.0 + 32.0 if math.isfinite(tmpc_now) else np.nan
    dwpf_now = dwpc_now * 9.0 / 5.0 + 32.0 if math.isfinite(dwpc_now) else np.nan
    tmpc_1h = asof(60, "tmpc")
    tmpc_3h = asof(180, "tmpc")
    dwpc_3h = asof(180, "dwpc")
    relh_3h = asof(180, "relh")
    return {
        "status": "ok",
        **common,
        "running_max_c": max(temps),
        "current_temp_c": tmpc_now,
        "decline_c": max(temps) - tmpc_now,
        "tmpf_now": tmpf_now,
        "dwpf_now": dwpf_now,
        "dewpoint_depression_f": tmpf_now - dwpf_now if math.isfinite(tmpf_now) and math.isfinite(dwpf_now) else np.nan,
        "relh_now": to_float(last.get("relh"), np.nan),
        "sknt_now": to_float(last.get("sknt"), np.nan),
        "sky_now": to_float(last.get("sky"), np.nan),
        "d_tmpf_1h": (tmpc_now - tmpc_1h) * 9.0 / 5.0 if math.isfinite(tmpc_now) and math.isfinite(tmpc_1h) else np.nan,
        "d_tmpf_3h": (tmpc_now - tmpc_3h) * 9.0 / 5.0 if math.isfinite(tmpc_now) and math.isfinite(tmpc_3h) else np.nan,
        "d_dwpf_3h": (dwpc_now - dwpc_3h) * 9.0 / 5.0 if math.isfinite(dwpc_now) and math.isfinite(dwpc_3h) else np.nan,
        "d_relh_3h": to_float(last.get("relh"), np.nan) - relh_3h if math.isfinite(to_float(last.get("relh"), np.nan)) and math.isfinite(relh_3h) else np.nan,
    }


def snapshot_records(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records = [r for r in data.get("records", []) if isinstance(r, dict)]
    return data, records


def records_by_city(records: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    seen_markets: set[str] = set()
    for record in records:
        city = safe_str(record.get("city"))
        date = safe_str(record.get("event_date"))
        market_id = safe_str(record.get("market_id")) or safe_str(record.get("condition_id"))
        if not city or not date or not market_id or market_id in seen_markets:
            continue
        seen_markets.add(market_id)
        out.setdefault((city, date), []).append(record)
    return out


def build_current_rows(
    snapshot: dict[str, Any],
    records: list[dict[str, Any]],
    stations: dict[str, Station],
    now: datetime,
    *,
    max_obs_age_min: float,
    pre_update_blackout_min: float,
    min_gap_to_next_bracket_c: float,
    min_local_hour: int = 13,
    max_local_hour: int = 15,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    grouped = records_by_city(records)
    rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for (city, target_date), city_records in grouped.items():
        station = stations.get(city)
        if station is None:
            continue
        tz = station_timezone(station)
        local_now = now.astimezone(tz)
        local_date = local_now.date().isoformat()
        if target_date != local_date:
            audits.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "status": "target_date_not_local_date",
                    "local_date": local_date,
                    "local_time": local_now.isoformat(timespec="seconds"),
                    "timezone": timezone_label(tz),
                }
            )
            continue
        hour = local_now.hour
        if hour < min_local_hour or hour > max_local_hour:
            audits.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "status": "outside_hour",
                    "hour_local": hour,
                    "min_local_hour": min_local_hour,
                    "max_local_hour": max_local_hour,
                    "local_time": local_now.isoformat(timespec="seconds"),
                    "timezone": timezone_label(tz),
                }
            )
            continue
        obs = fetch_obs(
            station,
            now,
            max_obs_age_min=max_obs_age_min,
            pre_update_blackout_min=pre_update_blackout_min,
        )
        if obs.get("status") != "ok":
            audits.append({"city": city, "target_date": target_date, "status": obs.get("status"), "obs": obs, "hour_local": hour})
            continue
        unit = station.unit
        current_native = obs["current_temp_c"] * 9.0 / 5.0 + 32.0 if unit == "F" else obs["current_temp_c"]
        running_native = obs["running_max_c"] * 9.0 / 5.0 + 32.0 if unit == "F" else obs["running_max_c"]
        running_value = round_half_up(running_native)
        parsed_records = []
        for record in city_records:
            parsed = parse_label(safe_str(record.get("bracket")), safe_str(record.get("question")))
            if parsed is None or parsed.get("bottom"):
                continue
            parsed_records.append((record, parsed))
        current = [(r, p) for r, p in parsed_records if bracket_contains(p, running_value)]
        if not current:
            audits.append({"city": city, "target_date": target_date, "status": "no_current_bracket", "running_value": running_value})
            continue
        current_record, current_parsed = sorted(current, key=lambda rp: (rp[1].get("top", False), to_float(rp[0].get("yes_best_ask"), 9.0)))[0]
        d1 = [
            (r, p)
            for r, p in parsed_records
            if p.get("low") is not None and float(p["low"]) > running_value
        ]
        if not d1:
            audits.append({"city": city, "target_date": target_date, "status": "no_d1_sibling", "running_value": running_value})
            continue
        d1_record, d1_parsed = sorted(d1, key=lambda rp: float(rp[1]["low"]))[0]
        gap_running = float(d1_parsed["low"]) - running_native
        gap_running_c = gap_running * 5.0 / 9.0 if unit == "F" else gap_running
        if gap_running_c <= min_gap_to_next_bracket_c + 1e-9:
            audits.append(
                {
                    "city": city,
                    "target_date": target_date,
                    "status": "too_close_to_next_bracket",
                    "running_value": running_value,
                    "current_bracket": safe_str(current_record.get("bracket")),
                    "d1_bracket": safe_str(d1_record.get("bracket")),
                    "gap_running_to_d1_low_native": round(gap_running, 3),
                    "gap_running_to_d1_low_c": round(gap_running_c, 3),
                    "min_gap_to_next_bracket_c": min_gap_to_next_bracket_c,
                    "hour_local": hour,
                }
            )
            continue
        yes_ask = to_float(current_record.get("yes_best_ask"), 0.0)
        yes_size = to_float(current_record.get("yes_ask_size"), 0.0)
        no_ask = to_float(d1_record.get("no_best_ask"), 0.0)
        no_size = to_float(d1_record.get("no_ask_size"), 0.0)
        if yes_ask <= 0 or no_ask <= 0:
            audits.append({"city": city, "target_date": target_date, "status": "missing_ask", "yes_ask": yes_ask, "d1_no_ask": no_ask})
            continue
        row = {
            "city": city,
            "target_date": target_date,
            "unit": unit,
            "timezone": timezone_label(tz),
            "local_time": local_now.isoformat(timespec="seconds"),
            "decision_hour_local": hour,
            "month": int(str(target_date)[5:7]),
            "decline_c": obs["decline_c"],
            "decline_native": running_native - current_native,
            "decline_band": (running_native - current_native) / 2.0 if unit == "F" else running_native - current_native,
            "gap_running_to_d1_low_native": gap_running,
            "gap_running_to_d1_low_c": gap_running_c,
            "gap_current_to_d1_low_native": gap_running + (running_native - current_native),
            "running_value": running_value,
            "current_native": current_native,
            "running_native": running_native,
            "yes_current_ask": yes_ask,
            "yes_current_size": yes_size,
            "d1_no_ask": no_ask,
            "d1_no_size": no_size,
            "ask_gap_d1_no_minus_yes": no_ask - yes_ask,
            "log_yes_size": math.log1p(max(0.0, yes_size)),
            "log_no_size": math.log1p(max(0.0, no_size)),
            "current_bracket": safe_str(current_record.get("bracket")),
            "d1_no_bracket": safe_str(d1_record.get("bracket")),
            "condition_id": safe_str(current_record.get("condition_id")),
            "market_id": safe_str(current_record.get("market_id")),
            "event_slug": safe_str(current_record.get("event_slug")),
            "question": safe_str(current_record.get("question")),
            "token_id": safe_str(current_record.get("yes_token_id")),
            "snapshot_ts_utc": snapshot.get("ts_utc"),
            "snapshot_path": str(latest_snapshot() or ""),
            "obs": obs,
            **{k: obs.get(k, np.nan) for k in ("tmpf_now", "dwpf_now", "dewpoint_depression_f", "relh_now", "sknt_now", "sky_now", "d_tmpf_1h", "d_tmpf_3h", "d_dwpf_3h", "d_relh_3h")},
        }
        rows.append(row)
    return pd.DataFrame(rows), audits


def prior_city_day_notional(strategy_instance: str) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    live_dirs = [ROOT / "runtime/weather_edge_v1/live", ROOT / "runtime/weather_edge_v1/remote_pm_agent/live"]
    for live_dir in live_dirs:
        if not live_dir.exists():
            continue
        for path in live_dir.glob("*.jsonl"):
            for row in read_jsonl(path):
                if safe_str(row.get("strategy_instance")) != strategy_instance or safe_str(row.get("status")) != "submitted":
                    continue
                key = (safe_str(row.get("city")), safe_str(row.get("target_date")))
                out[key] = out.get(key, 0.0) + max(0.0, to_float(row.get("posted_notional", row.get("notional")), 0.0))
    return out


def build_plan(row: dict[str, Any], *, notional: float, live_enabled: bool) -> dict[str, Any]:
    snapshot_price = float(row["yes_current_ask"])
    price = float(row.get("taker_limit_price") or snapshot_price)
    fresh_ask = float(row.get("fresh_best_ask") or snapshot_price)
    fresh_bid = float(row.get("fresh_best_bid") or 0.0)
    derived_min_edge = float(row.get("derived_min_edge_after_full_cushion") or 0.03)
    size = round(notional / price, 6)
    base = {
        "strategy": "weather_edge_v1",
        "strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "strategy_family": "theta_current_yes",
        "probability_source": "theta_current_yes_v8_train_logistic",
        "decision_mode": "current_running_max_yes_no_reheat",
        "execution_mode": "tiny_live_taker",
        "profile": "weather_plus_price_train_period_frozen",
        "combo": "current_yes_fixed_rule_v9",
        "city": row["city"],
        "city_pool": "source_aligned_theta",
        "target_date": row["target_date"],
        "market_id": row["market_id"],
        "event_slug": row["event_slug"],
        "question": row["question"],
        "bracket": row["current_bracket"],
        "token_id": row["token_id"],
        "signal_side": "BUY_YES",
        "order_side": "BUY",
        "market_price": round(price, 6),
        "best_bid": round(fresh_bid, 6),
        "best_ask": round(fresh_ask, 6),
        "spread": round(max(0.0, fresh_ask - fresh_bid), 6) if fresh_bid > 0 and fresh_ask > 0 else 0.0,
        "limit_price": round(price, 6),
        "quote_status": "accepted",
        "quote_reason": "theta_current_yes_fresh_book_guarded_taker",
        "quote_edge": round(float(row["p_yes_win"]) - price, 6),
        "required_quote_edge": round(derived_min_edge, 6),
        "model_token_probability": round(float(row["p_yes_win"]), 6),
        "quote_best_bid": round(fresh_bid, 6),
        "quote_best_ask": round(fresh_ask, 6),
        "quote_spread": round(max(0.0, fresh_ask - fresh_bid), 6) if fresh_bid > 0 and fresh_ask > 0 else 0.0,
        "quote_tick_size": 0.001,
        "quote_mode": "fresh_book_guarded_taker",
        "child_order_role": "single",
        "maker_only": False,
        "notional_fraction": 1.0,
        "size_multiplier": 1.0,
        "order_notional_cap": round(notional, 6),
        "size": size,
        "notional": round(size * price, 6),
        "execution_policy": "theta_current_yes_taker_v1",
        "tick_size": 0.001,
        "entry_price_window": "0.55-1.00",
        "sizing_mode": "notional",
        "fixed_order_shares": 0.0,
        "max_order_shares": size,
        "edge": round(float(row["p_yes_win"]) - price, 6),
        "min_edge": round(derived_min_edge, 6),
        "model_p_yes_used": round(float(row["p_yes_win"]), 6),
        "market_implied_p_yes": round(price, 6),
        "shadow_decision": "tiny_live_confirmed_v9",
        "shadow_reason": "significance_baseline_forward_execution_pass",
        "obs_source": safe_str((row.get("obs") or {}).get("source")),
        "model_version": "theta_current_yes_v8_train_logistic",
        "paper_enabled": True,
        "live_enabled": bool(live_enabled),
        "snapshot_yes_ask": round(snapshot_price, 6),
        "fresh_best_ask": round(fresh_ask, 6),
        "fresh_best_bid": round(fresh_bid, 6),
        "available_notional_at_ask": round(float(row.get("fresh_available_notional") or row["available_notional_at_ask"]), 6),
        "taker_max_price": round(float(row.get("taker_max_price") or price), 6),
        "taker_cushion_paid_vs_snapshot": round(float(row.get("taker_cushion_paid_vs_snapshot") or 0.0), 6),
        "derived_min_edge_after_full_cushion": round(derived_min_edge, 6),
        "expected_profit_usd_model": round(float(row.get("expected_profit_usd") or 0.0), 6),
        "d1_no_ask": round(float(row["d1_no_ask"]), 6),
        "d1_no_bracket": row["d1_no_bracket"],
        "decline_c": round(float(row["decline_c"]), 6),
        "obs_age_min": round(float((row.get("obs") or {}).get("age_min") or 0.0), 6),
        "minutes_to_next_obs": round(float((row.get("obs") or {}).get("minutes_to_next_obs") or -1.0), 6),
        "gap_running_to_d1_low_c": round(float(row.get("gap_running_to_d1_low_c") or 0.0), 6),
        "decision_hour_local": int(row["decision_hour_local"]),
        "decision_local_time": safe_str(row.get("local_time")),
        "decision_timezone": safe_str(row.get("timezone")),
        "snapshot_ts_utc": safe_str(row.get("snapshot_ts_utc")),
        "source_snapshot_path": safe_str(row.get("snapshot_path")),
    }
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": stable_hash(base),
        "signal_id": stable_hash({"strategy_instance": STRATEGY_INSTANCE, "city": row["city"], "target_date": row["target_date"], "bracket": row["current_bracket"]}),
        "created_at_utc": now_utc(),
        "status": "accepted",
        "risk_status": "passed",
        "risk_reason": "",
        **base,
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    if isinstance(value, tuple):
        return [json_ready(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        val = float(value)
        return None if not math.isfinite(val) else val
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    state = read_live_state(ROOT / "runtime/weather_edge_v1/live_cycle")
    if state.get("paused"):
        result = {"generated_at_utc": now_utc(), "status": "paused", "reason": state.get("reason", "")}
        SUMMARY_OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        append_jsonl(HISTORY_OUT, result)
        return result

    snap_path = Path(args.snapshot) if args.snapshot else latest_snapshot()
    if snap_path is None:
        raise RuntimeError("no paper snapshot found")
    snapshot, records = snapshot_records(snap_path)
    snapshot_ts = parse_utc(snapshot.get("ts_utc")) or datetime.now(timezone.utc)
    age_min = (datetime.now(timezone.utc) - snapshot_ts).total_seconds() / 60.0
    if age_min > args.max_snapshot_age_min:
        result = {
            "generated_at_utc": now_utc(),
            "status": "stale_snapshot",
            "snapshot": str(snap_path),
            "snapshot_ts_utc": snapshot.get("ts_utc"),
            "age_min": round(age_min, 1),
            "max_snapshot_age_min": args.max_snapshot_age_min,
        }
        SUMMARY_OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        append_jsonl(HISTORY_OUT, result)
        return result

    model_artifact = load_model_artifact()
    current, audits = build_current_rows(
        snapshot,
        records,
        load_stations(),
        snapshot_ts,
        max_obs_age_min=args.max_obs_age_min,
        pre_update_blackout_min=args.pre_metar_update_blackout_min,
        min_gap_to_next_bracket_c=args.min_gap_to_next_bracket_c,
        min_local_hour=args.min_local_hour,
        max_local_hour=args.max_local_hour,
    )
    candidates: list[dict[str, Any]] = []
    if not current.empty:
        current["p_yes_win"] = score_rows(current[MODEL_FEATURES], model_artifact)
        current["ev"] = current["p_yes_win"] - current["yes_current_ask"]
        current["available_notional_at_ask"] = current["yes_current_ask"] * current["yes_current_size"]
        mask = (
            current["decline_c"].ge(0.5)
            & current["yes_current_ask"].ge(0.55)
            & current["p_yes_win"].ge(0.5)
            & current["ev"].ge(0.05)
            & current["available_notional_at_ask"].ge(args.min_available_notional)
            & current["token_id"].astype(str).ne("")
        )
        selected = current[mask].copy()
        prior = prior_city_day_notional(STRATEGY_INSTANCE)
        for _, row in selected.sort_values(["ev", "available_notional_at_ask"], ascending=[False, False]).iterrows():
            key = (str(row["city"]), str(row["target_date"]))
            row_dict = row.to_dict()
            try:
                taker_quote = fresh_taker_quote(row_dict, args)
            except Exception as exc:  # noqa: BLE001
                audits.append({"city": key[0], "target_date": key[1], "status": "fresh_book_fetch_failed", "error": f"{type(exc).__name__}: {exc}"})
                continue
            if taker_quote.get("status") != "accepted":
                audits.append({"city": key[0], "target_date": key[1], "status": taker_quote.get("reason", "fresh_book_rejected"), **taker_quote})
                continue
            row_dict.update(
                {
                    "fresh_best_bid": taker_quote.get("best_bid", 0.0),
                    "fresh_best_ask": taker_quote["fresh_ask"],
                    "fresh_ask_size": taker_quote["fresh_ask_size"],
                    "fresh_available_notional": taker_quote["fresh_available_notional"],
                    "taker_max_price": taker_quote["max_taker_price"],
                    "taker_limit_price": taker_quote["limit_price"],
                    "edge_at_fresh_ask": taker_quote["edge_at_fresh_ask"],
                    "edge_at_limit": taker_quote["edge_at_limit"],
                    "expected_profit_usd": taker_quote["expected_profit_usd"],
                    "derived_min_edge_after_full_cushion": taker_quote["derived_min_edge_after_full_cushion"],
                    "taker_cushion_paid_vs_snapshot": taker_quote["cushion_paid_vs_snapshot"],
                }
            )
            if prior.get(key, 0.0) + args.max_order_notional > args.max_city_day_notional + 1e-9:
                audits.append({"city": key[0], "target_date": key[1], "status": "city_day_cap", "prior_notional": prior.get(key, 0.0)})
                continue
            if len(candidates) >= args.max_orders:
                audits.append({"city": key[0], "target_date": key[1], "status": "max_orders_reached"})
                continue
            candidates.append(row_dict)
            prior[key] = prior.get(key, 0.0) + args.max_order_notional

    live_enabled = bool(args.live and args.confirm_live)
    plans = [build_plan(row, notional=args.max_order_notional, live_enabled=live_enabled) for row in candidates]
    write_jsonl(PLAN_OUT, plans)
    result = {
        "generated_at_utc": now_utc(),
        "status": "planned",
        "strategy_instance": STRATEGY_INSTANCE,
        "snapshot": str(snap_path),
        "snapshot_dir": str(snapshot_dir()),
        "snapshot_ts_utc": snapshot.get("ts_utc"),
        "snapshot_age_min": round(age_min, 1),
        "current_rows": int(0 if current.empty else len(current)),
        "candidate_rows": len(candidates),
        "plans": len(plans),
        "live_enabled": live_enabled,
        "caps": {
            "max_order_notional": args.max_order_notional,
            "max_city_day_notional": args.max_city_day_notional,
            "min_available_notional": args.min_available_notional,
            "max_taker_cushion": args.max_taker_cushion,
            "cross_tick_buffer": args.cross_tick_buffer,
            "max_obs_age_min": args.max_obs_age_min,
            "pre_metar_update_blackout_min": args.pre_metar_update_blackout_min,
            "min_gap_to_next_bracket_c": args.min_gap_to_next_bracket_c,
            "min_local_hour": args.min_local_hour,
            "max_local_hour": args.max_local_hour,
            "max_orders": args.max_orders,
        },
        "plan_out": str(PLAN_OUT),
        "live_out": str(LIVE_OUT),
        "candidates": [
            {
                "city": r["city"],
                "target_date": r["target_date"],
                "hour": int(r["decision_hour_local"]),
                "bracket": r["current_bracket"],
                "ask": round(float(r["yes_current_ask"]), 4),
                "fresh_ask": round(float(r["fresh_best_ask"]), 4),
                "limit_price": round(float(r["taker_limit_price"]), 4),
                "p_yes_win": round(float(r["p_yes_win"]), 4),
                "ev": round(float(r["ev"]), 4),
                "edge_at_limit": round(float(r["edge_at_limit"]), 4),
                "expected_profit_usd": round(float(r["expected_profit_usd"]), 4),
                "taker_cushion_paid_vs_snapshot": round(float(r["taker_cushion_paid_vs_snapshot"]), 4),
                "available_notional_at_ask": round(float(r["available_notional_at_ask"]), 4),
                "fresh_available_notional": round(float(r["fresh_available_notional"]), 4),
                "decline_c": round(float(r["decline_c"]), 3),
                "obs_age_min": round(float((r.get("obs") or {}).get("age_min") or 0.0), 1),
                "minutes_to_next_obs": round(float((r.get("obs") or {}).get("minutes_to_next_obs") or -1.0), 1),
                "gap_running_to_d1_low_c": round(float(r.get("gap_running_to_d1_low_c") or 0.0), 3),
            }
            for r in candidates
        ],
        "audit_counts": dict(pd.Series([safe_str(a.get("status")) for a in audits]).value_counts()) if audits else {},
    }
    executor_result = None
    if args.live and plans:
        if not args.confirm_live:
            raise RuntimeError("--live requires --confirm-live")
        cmd = [
            sys.executable,
            "scripts/ops/weather_order_executor.py",
            "--plans",
            str(PLAN_OUT),
            "--paper-out",
            str(PAPER_OUT),
            "--live-out",
            str(LIVE_OUT),
            "--live",
            "--confirm-live",
            "--allow-taker",
        ]
        if args.no_telegram:
            cmd.append("--no-telegram")
        proc = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        result["executor_cmd"] = cmd
        result["executor_returncode"] = proc.returncode
        result["executor_output"] = proc.stdout[-8000:]
        try:
            start = proc.stdout.find("{")
            end = proc.stdout.rfind("}")
            executor_result = json.loads(proc.stdout[start : end + 1]) if start >= 0 and end > start else None
        except Exception:
            executor_result = None
        result["executor_result"] = executor_result
    elif args.live:
        if not args.confirm_live:
            raise RuntimeError("--live requires --confirm-live")
        result["executor_result"] = {"status": "skipped", "reason": "no_plans"}
    result = json_ready(result)
    SUMMARY_OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_jsonl(HISTORY_OUT, result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "loop"])
    parser.add_argument("--snapshot")
    parser.add_argument("--max-order-notional", type=float, default=5.0)
    parser.add_argument("--max-city-day-notional", type=float, default=10.0)
    parser.add_argument("--min-available-notional", type=float, default=5.0)
    parser.add_argument("--max-taker-cushion", type=float, default=0.02)
    parser.add_argument("--cross-tick-buffer", type=float, default=0.001)
    parser.add_argument("--max-orders", type=int, default=20)
    parser.add_argument("--max-snapshot-age-min", type=float, default=45.0)
    parser.add_argument("--max-obs-age-min", type=float, default=20.0)
    parser.add_argument("--pre-metar-update-blackout-min", type=float, default=6.0)
    parser.add_argument("--min-gap-to-next-bracket-c", type=float, default=0.0)
    parser.add_argument("--min-local-hour", type=int, default=13)
    parser.add_argument("--max-local-hour", type=int, default=15)
    parser.add_argument("--interval-seconds", type=float, default=900.0)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--no-telegram", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ModuleNotFoundError:
        pass
    args = parse_args()
    if args.command == "run":
        print(json.dumps(run_once(args), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    while True:
        try:
            result = run_once(args)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        except Exception as exc:  # noqa: BLE001
            err = {"generated_at_utc": now_utc(), "status": "error", "error": f"{type(exc).__name__}: {exc}"}
            print(json.dumps(err, ensure_ascii=False, sort_keys=True), flush=True)
            append_jsonl(HISTORY_OUT, err)
        time.sleep(max(30.0, float(args.interval_seconds)))


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
