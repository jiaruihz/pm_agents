#!/usr/bin/env python3
"""Tiny-live runner for regime-routed BUY_NO candidates.

This is the live-facing expression of:

- day_marginal_runway/day_open_runway -> current-bracket NO
- day_forecast_capped                 -> d2 NO

It intentionally skips soft-sized orders that fall below Polymarket's minimum
share size instead of rounding them up.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


OPS = Path(__file__).resolve().parent
ROOT = OPS.parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
if sys.prefix == sys.base_prefix and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(OPS) not in sys.path:
    sys.path.insert(0, str(OPS))
ANALYSIS_DIR = ROOT / "scripts/analysis/reheat_risk"
if str(ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(ANALYSIS_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import research_intraday_weather_regime_atlas_v1 as atlas  # noqa: E402
import research_regime_routed_no_expression_v1 as research  # noqa: E402
from research_reheat_feature_factory_v1 import bracket_contains, parse_bracket  # noqa: E402
from weather_data_feed.observation_cache import index_observation_cache, load_observation_cache, parse_utc  # noqa: E402
from weather_data_feed.snapshot_protocol import parse_market_event_date  # noqa: E402
from weather_data_feed.source_policy import load_city_configs  # noqa: E402
from weather_data_feed.weather_context import (  # noqa: E402
    city_wind_context,
    temperature_context_features,
    temperature_context_multiplier,
)

import weather_metar_cross_prev_no_shadow as metar  # noqa: E402


STRATEGY_ID = "regime_routed_no_tiny_live_v1"
STRATEGY_INSTANCE = "regime_routed_no_route_price_disciplined_tiny_live_v1"
RULE_ID = "route_price_disciplined_no_pullback_row_risk_soft_min5shares_v1"
RUNTIME_DIR = ROOT / "runtime/weather_edge_v1/regime_routed_no_tiny_live_v1"
PLAN_OUT = RUNTIME_DIR / "trade_plans.jsonl"
PAPER_OUT = RUNTIME_DIR / "paper_orders.jsonl"
LIVE_OUT = RUNTIME_DIR / "live_orders.jsonl"
BLOCKED_OUT = RUNTIME_DIR / "blocked_candidates.jsonl"
LATEST_CANDIDATES_OUT = RUNTIME_DIR / "latest_candidates.json"
SUMMARY_OUT = RUNTIME_DIR / "latest_summary.json"
HISTORY_OUT = RUNTIME_DIR / "summary_history.jsonl"
DEFAULT_SNAPSHOT_DIR = ROOT / "runtime/weather_edge_v1/market_data/paper_snapshots"
DEFAULT_OBSERVATION_CACHE_PATHS = [
    Path("/home/jiarui/projects/weather_data_feed_service_runtime/output/observations/latest.json"),
    ROOT / "runtime/weather_edge_v1/market_data/observations/latest.json",
    ROOT / "runtime/weather_edge_v1/observations/latest.json",
]
OBS_STALE_CADENCE_GRACE_MIN = 15.0
OBS_MAX_DYNAMIC_AGE_MIN = 90.0
CORE_LIVE_REGIME_COLS = [
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "relative_humidity_pct",
    "dewpoint_depression_f",
    "wind_speed_kt",
    "minutes_since_running_max",
]
CORE_LIVE_REGIME_LABELS = [
    "intraday_state",
    "moisture_cloud_regime",
    "wind_regime",
    "running_max_state",
]
WIND_CONTEXT_COLS = [
    "wind_dir_deg",
    "wind_sector",
    "geo_context",
    "coastal_flow_state",
    "is_coastal_context",
]
ROUTE_PRICE_CAPS = {
    "fresh_runway_current_no": 0.55,
    "capped_d2_no": 0.62,
    "false_fade_reheat_current_no": 0.65,
    "cheap_stale_tail_current_no": 0.40,
}
CURRENT_NO_ROUTE_LEGS = {
    "fresh_runway_current_no",
    "false_fade_reheat_current_no",
    "cheap_stale_tail_current_no",
    "runway_current_no",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return str(value)


def stable_hash(payload: Any, *, length: int = 24) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def latest_snapshot_path(snapshot_dir: Path, snapshot_path: str = "") -> Path:
    if snapshot_path:
        path = Path(snapshot_path)
        return path if path.is_absolute() else ROOT / path
    candidates = sorted(snapshot_dir.glob("snapshot_*.json"))
    if not candidates:
        raise FileNotFoundError(f"no paper snapshots under {snapshot_dir}")
    return candidates[-1]


def load_snapshot(path: Path) -> tuple[dict[str, Any], pd.DataFrame]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = pd.DataFrame(payload.get("records") or [])
    if records.empty:
        return payload, records
    records["target_date"] = records["target_date"].astype(str)
    records["city"] = records["city"].astype(str)
    return payload, records


def observation_cache_candidates(explicit: str = "") -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    for key in ("REGIME_ROUTED_NO_OBSERVATION_CACHE", "WEATHER_DATA_FEED_OBSERVATION_CACHE"):
        if os.environ.get(key):
            candidates.append(Path(str(os.environ[key])).expanduser())
    candidates.extend(DEFAULT_OBSERVATION_CACHE_PATHS)
    out: list[Path] = []
    for path in candidates:
        if path not in out:
            out.append(path)
    return out


def load_latest_observation_cache(explicit: str = "") -> tuple[dict[str, Any] | None, Path | None, str]:
    candidates = observation_cache_candidates(explicit)
    chosen = candidates[0] if explicit else next((path for path in candidates if path.exists()), None)
    if chosen is None or not chosen.exists():
        return None, chosen, "missing"
    try:
        return load_observation_cache(chosen), chosen, "ok"
    except Exception as exc:  # noqa: BLE001
        return None, chosen, f"error:{type(exc).__name__}:{exc}"


def effective_obs_age_limit(max_obs_age_min: float, cadence_min: float | None) -> float:
    if cadence_min is None or not math.isfinite(float(cadence_min)) or float(cadence_min) <= 0:
        return float(max_obs_age_min)
    return min(
        OBS_MAX_DYNAMIC_AGE_MIN,
        max(float(max_obs_age_min), float(cadence_min) + OBS_STALE_CADENCE_GRACE_MIN),
    )


def safe_float(value: Any, default: float = math.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def native_value(temp_c: float, unit: str) -> float:
    return temp_c * 9.0 / 5.0 + 32.0 if str(unit).upper() == "F" else temp_c


def temp_f(temp_c: float) -> float:
    return temp_c * 9.0 / 5.0 + 32.0


def relative_humidity_pct(temp_c: float, dewpoint_c: float) -> float:
    # Magnus approximation, good enough for regime labels and fail-closed if inputs are absent.
    numerator = math.exp((17.625 * dewpoint_c) / (243.04 + dewpoint_c))
    denominator = math.exp((17.625 * temp_c) / (243.04 + temp_c))
    return max(0.0, min(100.0, 100.0 * numerator / denominator))


def sky_cover_code(record: dict[str, Any]) -> float:
    cover_rank = {"CLR": 0, "SKC": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "VV": 4}
    covers: list[str] = []
    if record.get("cover"):
        covers.append(str(record.get("cover")).upper())
    for cloud in record.get("clouds") or []:
        if isinstance(cloud, dict) and cloud.get("cover"):
            covers.append(str(cloud.get("cover")).upper())
    ranks = [cover_rank[item] for item in covers if item in cover_rank]
    return float(max(ranks)) if ranks else math.nan


def cache_sky_cover_code(value: Any) -> float:
    numeric = safe_float(value)
    if math.isfinite(numeric):
        return numeric
    text = str(value or "").strip().upper()
    if not text:
        return math.nan
    cover_rank = {"CLR": 0, "SKC": 0, "FEW": 1, "SCT": 2, "BKN": 3, "OVC": 4, "VV": 4}
    return float(cover_rank[text]) if text in cover_rank else math.nan


def humidity_from_cache(record: dict[str, Any]) -> float:
    relh = safe_float(record.get("relative_humidity_pct"), safe_float(record.get("relh_now")))
    if math.isfinite(relh):
        return relh
    tmpf = safe_float(record.get("tmpf_now"))
    dwpf = safe_float(record.get("dwpf_now"))
    if not math.isfinite(tmpf) or not math.isfinite(dwpf):
        return math.nan
    temp_c_val = (tmpf - 32.0) * 5.0 / 9.0
    dewpoint_c_val = (dwpf - 32.0) * 5.0 / 9.0
    return relative_humidity_pct(temp_c_val, dewpoint_c_val)


def wind_dir_from_record(record: dict[str, Any]) -> float:
    for key in ("wind_dir_deg", "drct_now", "wind_direction_deg", "wdir", "drct"):
        value = safe_float(record.get(key))
        if math.isfinite(value):
            return value
    return math.nan


def observation_cache_summary(
    observation_cache: dict[str, Any] | None,
    *,
    city: str,
    target_date: str,
    cfg: Any,
    now_utc: datetime,
    max_obs_age_min: float,
) -> dict[str, Any]:
    if observation_cache is None:
        return {"status": "observation_cache_missing", "source": "weather_data_feed_observation_cache", "n_obs": 0}
    record = index_observation_cache(observation_cache).get((city, target_date))
    if not record:
        return {"status": "observation_cache_city_date_missing", "source": "weather_data_feed_observation_cache", "n_obs": 0}
    source = str(record.get("source") or "weather_data_feed_observation_cache")
    n_obs = int(max(0.0, safe_float(record.get("n_obs") or record.get("record_count"), 0.0)))
    if record.get("status") != "ok":
        return {
            "status": str(record.get("status") or "observation_cache_not_ok"),
            "source": source,
            "n_obs": n_obs,
            "error": str(record.get("error") or ""),
        }
    last_obs = parse_utc(record.get("last_obs_utc"))
    if last_obs is None:
        return {"status": "observation_cache_bad_ts", "source": source, "n_obs": n_obs}
    age_min = (now_utc - last_obs).total_seconds() / 60.0
    cadence_min = safe_float(record.get("cadence_min") or record.get("estimated_cadence_min"), math.nan)
    cadence_value = cadence_min if math.isfinite(cadence_min) else None
    effective_max_age = effective_obs_age_limit(max_obs_age_min, cadence_value)
    if age_min > effective_max_age:
        return {
            "status": "stale_obs",
            "source": source,
            "n_obs": n_obs,
            "age_min": round(age_min, 1),
            "effective_max_obs_age_min": round(effective_max_age, 1),
            "last_obs_utc": last_obs.isoformat(),
        }
    current_c = safe_float(record.get("current_temp_c"))
    running_c = safe_float(record.get("running_max_c"))
    if not math.isfinite(current_c) or not math.isfinite(running_c):
        return {"status": "observation_cache_bad_temp", "source": source, "n_obs": n_obs}
    tmpf_now = safe_float(record.get("tmpf_now"), temp_f(current_c))
    dwpf_now = safe_float(record.get("dwpf_now"))
    dewpoint_depression = safe_float(record.get("dewpoint_depression_f"))
    if not math.isfinite(dewpoint_depression) and math.isfinite(tmpf_now) and math.isfinite(dwpf_now):
        dewpoint_depression = tmpf_now - dwpf_now
    sky_value = record.get("sky_cover_code")
    if sky_value in (None, ""):
        sky_value = record.get("sky_now")
    if sky_value in (None, ""):
        sky_value = record.get("sky_code_now")
    return {
        "status": "ok",
        "source": source,
        "n_obs": n_obs,
        "age_min": round(age_min, 1),
        "last_obs_utc": last_obs.isoformat(),
        "running_max_c": running_c,
        "current_temp_c": current_c,
        "live_feature_status": "ok",
        "live_feature_source": "weather_data_feed_observation_cache",
        "live_feature_record_count": n_obs,
        "live_feature_last_obs_utc": last_obs.isoformat(),
        "observation_cache_generated_at_utc": str(observation_cache.get("generated_at_utc") or ""),
        "observation_cache_fetched_at_utc": str(record.get("fetched_at_utc") or ""),
        "relative_humidity_pct": humidity_from_cache(record),
        "sky_cover_code": cache_sky_cover_code(sky_value),
        "dewpoint_depression_f": dewpoint_depression,
        "wind_speed_kt": safe_float(record.get("wind_speed_kt"), safe_float(record.get("sknt_now"))),
        "wind_dir_deg": wind_dir_from_record(record),
        "temp_trend_1h_f": safe_float(record.get("temp_trend_1h_f"), safe_float(record.get("d_tmpf_1h"))),
        "temp_trend_3h_f": safe_float(record.get("temp_trend_3h_f"), safe_float(record.get("d_tmpf_3h"))),
        "minutes_since_running_max": safe_float(record.get("minutes_since_running_max")),
        "observation_cadence_min": cadence_value,
        "official_observation_station": str(record.get("station") or cfg.official_icao),
    }


def trend_f(records: list[tuple[datetime, float, dict[str, Any]]], latest_dt: datetime, latest_temp_c: float, hours: float) -> float:
    target = latest_dt - timedelta(hours=hours)
    candidates = [(dt, temp_c) for dt, temp_c, _raw in records if dt <= target]
    if not candidates:
        return math.nan
    prior_dt, prior_temp_c = candidates[-1]
    if (target - prior_dt).total_seconds() > 90.0 * 60.0:
        return math.nan
    return temp_f(latest_temp_c) - temp_f(prior_temp_c)


def aviationweather_live_regime_features(cfg: Any, tz: ZoneInfo, local_date: Any, *, hours: float) -> dict[str, Any]:
    """Fetch PIT live METAR context used by the regime atlas labels.

    The historical atlas has these fields nearly fully populated.  The live
    runner must fail closed rather than silently substituting unknowns.
    """
    data = metar.source.fetch_json(
        metar.source.METAR_API,
        {"ids": cfg.official_icao, "format": "json", "hours": str(hours)},
        max_rounds=1,
        timeout_sec=metar.FAST_HTTP_TIMEOUT_SEC,
        proxy_candidates=metar.WEATHER_PROXY_CANDIDATES,
    )
    if not isinstance(data, list):
        return {"live_feature_status": "bad_aviationweather_payload"}

    records: list[tuple[datetime, float, dict[str, Any]]] = []
    for rec in data:
        if not isinstance(rec, dict) or rec.get("temp") is None or not rec.get("reportTime"):
            continue
        try:
            dt = datetime.fromisoformat(str(rec["reportTime"]).replace("Z", "+00:00"))
            temp_c_val = float(rec["temp"])
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(tz).date() != local_date:
            continue
        records.append((dt, temp_c_val, rec))
    records.sort(key=lambda row: row[0])
    if not records:
        return {"live_feature_status": "no_same_day_aviationweather_records"}

    latest_dt, latest_temp_c, latest = records[-1]
    running_max_c = max(temp for _dt, temp, _raw in records)
    max_hits = [dt for dt, temp, _raw in records if temp >= running_max_c - 0.05]
    dewpoint_c = safe_float(latest.get("dewp"))
    wind_kt = safe_float(latest.get("wspd"))
    wind_dir = safe_float(latest.get("wdir"))
    out = {
        "live_feature_status": "ok",
        "relative_humidity_pct": relative_humidity_pct(latest_temp_c, dewpoint_c) if math.isfinite(dewpoint_c) else math.nan,
        "sky_cover_code": sky_cover_code(latest),
        "dewpoint_depression_f": temp_f(latest_temp_c - dewpoint_c) - 32.0 if math.isfinite(dewpoint_c) else math.nan,
        "wind_speed_kt": wind_kt,
        "wind_dir_deg": wind_dir,
        "temp_trend_1h_f": trend_f(records, latest_dt, latest_temp_c, 1.0),
        "temp_trend_3h_f": trend_f(records, latest_dt, latest_temp_c, 3.0),
        "minutes_since_running_max": (
            (latest_dt - max_hits[-1]).total_seconds() / 60.0 if max_hits else math.nan
        ),
        "live_feature_record_count": len(records),
        "live_feature_last_obs_utc": latest_dt.isoformat(),
    }
    return out


def tail_distance_from_running(bracket_low: float | None, running_native: float, unit: str) -> int | None:
    if bracket_low is None or float(bracket_low) <= running_native:
        return None
    if str(unit).upper() == "F":
        return int(math.ceil((float(bracket_low) - running_native) / 2.0))
    return int(round(float(bracket_low) - running_native))


def current_no_escape_threshold_native(bracket_text: Any) -> float:
    """Return the rounded-settlement escape threshold for a current-NO bracket."""
    bracket = parse_bracket(bracket_text)
    if bracket is None or bracket.high is None:
        return math.nan
    return float(bracket.high) + 0.5


def filter_current_local_day_records(records: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if records.empty:
        return records.copy(), {"input_rows": 0, "kept_rows": 0, "dropped_rows": 0}
    out = records.copy()
    if "city_local_date_at_snapshot" not in out.columns:
        out["city_local_date_at_snapshot"] = ""
    if "market_local_date" not in out.columns:
        out["market_local_date"] = out.get("target_date", "")
    target_date = out["target_date"].astype(str)
    local_date = out["city_local_date_at_snapshot"].astype(str)
    market_local_date = out["market_local_date"].astype(str)
    current_local_market = local_date.ne("") & (market_local_date.eq(local_date) | target_date.eq(local_date))
    kept = out[current_local_market].copy()
    return kept, {
        "input_rows": int(len(out)),
        "kept_rows": int(len(kept)),
        "dropped_rows": int((~current_local_market).sum()),
        "dropped_target_dates": sorted(target_date[~current_local_market].dropna().unique().tolist()),
    }


def market_event_date_for_record(record: dict[str, Any] | pd.Series) -> str:
    row = record.to_dict() if isinstance(record, pd.Series) else dict(record)
    target_date = str(row.get("target_date") or "")
    target_year = target_date[:4] if len(target_date) >= 4 else None
    return parse_market_event_date(row, target_year=target_year)


def market_record_date_ok(record: dict[str, Any] | pd.Series) -> bool:
    row = record.to_dict() if isinstance(record, pd.Series) else dict(record)
    target_date = str(row.get("target_date") or "")
    event_date = market_event_date_for_record(row)
    return bool(target_date and event_date and event_date == target_date)


def current_no_runway_state_ok(row: dict[str, Any] | pd.Series) -> bool:
    getter = row.get if isinstance(row, dict) else row.get
    route_leg = str(getter("route_leg") or "")
    if route_leg not in {"runway_current_no", "fresh_runway_current_no"}:
        return True
    return str(getter("running_max_state") or "") == "fresh_running_high" and str(getter("intraday_state") or "") in {
        "active_warming",
        "fresh_high",
    }


def route_price_cap(route_leg: Any) -> float:
    return float(ROUTE_PRICE_CAPS.get(str(route_leg or ""), research.ASK_CAPS["relaxed70"]))


def current_no_route_for_state(labelled: dict[str, Any]) -> str:
    running = str(labelled.get("running_max_state") or "")
    state = str(labelled.get("intraday_state") or "")
    if running == "fresh_running_high" and state in {"active_warming", "fresh_high"}:
        return "fresh_runway_current_no"
    if state in {"false_fade_risk", "reheating_after_dip"}:
        return "false_fade_reheat_current_no"
    if running == "mature_fade" and state == "mature_fade":
        return "cheap_stale_tail_current_no"
    return ""


def row_risk_soft_v1(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype="float64")
    ask = pd.to_numeric(frame.get("ask"), errors="coerce")
    route = frame["route_leg"].astype(str)
    peak_delta = pd.to_numeric(frame.get("forecast_peak_delta_hours_local"), errors="coerce")
    minutes_since = pd.to_numeric(frame.get("minutes_since_running_max"), errors="coerce")
    trend_1h = pd.to_numeric(frame.get("temp_trend_1h_f"), errors="coerce")
    wind = pd.to_numeric(frame.get("wind_speed_kt"), errors="coerce")
    humidity = pd.to_numeric(frame.get("relative_humidity_pct"), errors="coerce")

    route_mult = route.map(
        {
            "fresh_runway_current_no": 0.80,
            "capped_d2_no": 0.45,
            "false_fade_reheat_current_no": 0.65,
            "cheap_stale_tail_current_no": 0.30,
        }
    ).fillna(0.50)
    price_risk = ((ask - 0.45) / 0.25).clip(0, 1).fillna(0)
    price_mult = (1.0 - 0.55 * price_risk).clip(0.35, 1.0)
    peak_mult = pd.Series(1.0, index=frame.index)
    current_no = route.isin(["fresh_runway_current_no", "false_fade_reheat_current_no", "cheap_stale_tail_current_no"])
    peak_mult.loc[current_no] = pd.Series(
        np.select(
            [
                peak_delta.loc[current_no].le(-2.0),
                peak_delta.loc[current_no].le(0.0),
                peak_delta.loc[current_no].le(1.0),
            ],
            [1.0, 0.80, 0.50],
            default=0.25,
        ),
        index=frame.index[current_no],
    )
    freshness_mult = pd.Series(1.0, index=frame.index)
    fresh = route.eq("fresh_runway_current_no")
    freshness_mult.loc[fresh] = pd.Series(
        np.select(
            [minutes_since.loc[fresh].le(45), minutes_since.loc[fresh].le(90)],
            [1.0, 0.70],
            default=0.40,
        ),
        index=frame.index[fresh],
    )
    momentum_mult = pd.Series(1.0, index=frame.index)
    momentum_routes = route.isin(["fresh_runway_current_no", "false_fade_reheat_current_no"])
    momentum_mult.loc[momentum_routes] = pd.Series(
        np.select(
            [trend_1h.loc[momentum_routes].ge(0.5), trend_1h.loc[momentum_routes].ge(0.0)],
            [1.0, 0.80],
            default=0.50,
        ),
        index=frame.index[momentum_routes],
    )
    city_family = frame.get("city_family", pd.Series("", index=frame.index)).astype(str)
    moisture = frame.get("moisture_cloud_regime", pd.Series("", index=frame.index)).astype(str)
    weather_mult = (
        1.0
        - 0.10 * city_family.eq("humid_low_latitude").astype(float)
        - 0.08 * wind.ge(15).fillna(False).astype(float)
        - 0.06 * humidity.ge(70).fillna(False).astype(float)
        - 0.06 * moisture.str.contains("convective|humid|cloud", case=False, na=False).astype(float)
    ).clip(0.65, 1.0)
    return (route_mult * price_mult * peak_mult * freshness_mult * momentum_mult * weather_mult).clip(0.05, 1.0)


def order_spent_today(path: Path, target_date: str) -> float:
    total = 0.0
    for row in read_jsonl(path):
        if row.get("target_date") != target_date:
            continue
        status = str(row.get("status") or "")
        if status not in {"submitted", "simulated_open"}:
            continue
        total += safe_float(row.get("notional"), 0.0)
    return round(total, 6)


def prior_live_order_keys(path: Path) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for row in read_jsonl(path):
        if str(row.get("status") or "") not in {"submitted", "simulated_open"}:
            continue
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""), str(row.get("token_id") or ""))
        if all(key):
            keys.add(key)
    return keys


def live_order_spent_by_date(path: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in read_jsonl(path):
        if str(row.get("status") or "") not in {"submitted", "simulated_open"}:
            continue
        target_date = str(row.get("target_date") or "")
        if not target_date:
            continue
        out[target_date] = out.get(target_date, 0.0) + safe_float(row.get("notional"), 0.0)
    return {date: round(value, 6) for date, value in out.items()}


def market_rows_for_city(sub: pd.DataFrame, *, running_value: int, running_native: float, unit: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, record in sub.iterrows():
        bracket = parse_bracket(record.get("bracket"))
        if bracket is None:
            continue
        book_target_date = str(record.get("target_date") or "")
        book_market_event_date = market_event_date_for_record(record)
        for outcome, prefix in (("yes", "yes"), ("no", "no")):
            ask = safe_float(record.get(f"{prefix}_best_ask"))
            ask_size = safe_float(record.get(f"{prefix}_ask_size"))
            bid = safe_float(record.get(f"{prefix}_best_bid"))
            token_id = str(record.get(f"{prefix}_token_id") or "")
            if not math.isfinite(ask):
                continue
            rows.append(
                {
                    **record.to_dict(),
                    "outcome": outcome,
                    "bracket_low": bracket.low,
                    "bracket_high": bracket.high,
                    "book_ask": ask,
                    "book_ask_size": ask_size,
                    "book_bid": bid,
                    "book_token_id": token_id,
                    "book_target_date": book_target_date,
                    "book_market_event_date": book_market_event_date,
                    "book_market_date_match": bool(book_target_date and book_market_event_date == book_target_date),
                    "contains_running": bracket_contains(bracket, running_value),
                    "tail_distance": tail_distance_from_running(bracket.low, running_native, unit) if outcome == "no" else None,
                }
            )
    return pd.DataFrame(rows)


def build_city_state(
    *,
    city: str,
    cfg: Any,
    sub: pd.DataFrame,
    observation_cache: dict[str, Any] | None,
    obs_source: str,
    recent_hours: float,
    max_obs_age_min: float,
) -> tuple[dict[str, Any] | None, str]:
    first = sub.iloc[0]
    snapshot_ts = pd.to_datetime(str(first.get("snapshot_ts_utc")), utc=True).to_pydatetime()
    tz = ZoneInfo(cfg.timezone_name)
    local_ts = snapshot_ts.astimezone(tz)
    decision_hour = int(local_ts.hour)
    decision_hour_float = local_ts.hour + local_ts.minute / 60.0 + local_ts.second / 3600.0
    target_date = str(first.get("target_date"))
    target_dates = sorted(sub["target_date"].astype(str).dropna().unique().tolist())
    if len(target_dates) != 1:
        return None, "mixed_target_dates:" + ",".join(target_dates[:4])
    local_dates = sorted(sub.get("city_local_date_at_snapshot", pd.Series([], dtype=str)).astype(str).dropna().unique().tolist())
    market_local_dates = sorted(sub.get("market_local_date", pd.Series([], dtype=str)).astype(str).dropna().unique().tolist())
    if local_dates and market_local_dates and not set(local_dates).intersection(market_local_dates):
        return None, "not_current_local_market"
    if decision_hour not in research.DECISION_HOURS:
        return None, f"outside_decision_hours:{decision_hour}"
    if obs_source == "weather_data_feed_observation_cache":
        obs = observation_cache_summary(
            observation_cache,
            city=city,
            target_date=target_date,
            cfg=cfg,
            now_utc=datetime.now(timezone.utc),
            max_obs_age_min=max_obs_age_min,
        )
    else:
        try:
            obs = metar.observation_summary(
                cfg,
                tz,
                local_ts.date(),
                previous_value=None,
                recent_hours=recent_hours,
                obs_source=obs_source,
            )
        except Exception as exc:  # noqa: BLE001
            return None, f"obs_error:{type(exc).__name__}:{exc}"
    if obs.get("status") != "ok":
        return None, f"obs_not_ok:{obs.get('status')}"

    current_c = safe_float(obs.get("current_temp_c"))
    running_c = safe_float(obs.get("running_max_c"))
    if not math.isfinite(current_c) or not math.isfinite(running_c):
        return None, "obs_missing_temp"

    unit = str(cfg.unit)
    current_native = native_value(current_c, unit)
    running_native = native_value(running_c, unit)
    running_value = metar.market_value(running_c, unit)
    base = {
        "city": city,
        "target_date": target_date,
        "decision_snapshot_ts_utc": str(first.get("snapshot_ts_utc")),
        "decision_hour_local": decision_hour,
        "decision_hour_local_float": decision_hour_float,
        "timezone": cfg.timezone_name,
        "unit": unit,
        "icao": cfg.official_icao,
        "current_temp_c": current_c,
        "running_max_c": running_c,
        "current_native": current_native,
        "running_native": running_native,
        "decline_native": running_native - current_native,
        "running_value": running_value,
        "forecast_source": str(first.get("forecast_source") or ""),
        "forecast_clock_source": "paper_snapshot_live",
        "forecast_max_native": safe_float(first.get("forecast_max_native")),
        "forecast_peak_hour_local": safe_float(first.get("forecast_peak_hour_local")),
        "forecast_peak_delta_hours_local": decision_hour_float - safe_float(first.get("forecast_peak_hour_local")),
        "forecast_gap_to_running_native": safe_float(first.get("forecast_max_native")) - running_native,
        "relative_humidity_pct": math.nan,
        "sky_cover_code": math.nan,
        "dewpoint_depression_f": math.nan,
        "wind_speed_kt": math.nan,
        "wind_dir_deg": math.nan,
        "temp_trend_1h_f": math.nan,
        "temp_trend_3h_f": math.nan,
        "minutes_since_running_max": math.nan,
        "live_feature_status": "not_fetched",
        "obs_source": obs_source,
        "obs_last_obs_utc": obs.get("last_obs_utc"),
        "obs_age_min": obs.get("age_min"),
        "obs_count_day": obs.get("n_obs"),
    }
    if obs_source == "aviationweather_metar":
        try:
            base.update(aviationweather_live_regime_features(cfg, tz, local_ts.date(), hours=recent_hours))
        except Exception as exc:  # noqa: BLE001
            return None, f"live_feature_error:{type(exc).__name__}:{exc}"
    elif obs_source == "weather_data_feed_observation_cache":
        base.update(
            {
                key: obs.get(key)
                for key in [
                    "relative_humidity_pct",
                    "sky_cover_code",
                    "dewpoint_depression_f",
                    "wind_speed_kt",
                    "wind_dir_deg",
                    "temp_trend_1h_f",
                    "temp_trend_3h_f",
                    "minutes_since_running_max",
                    "live_feature_status",
                    "live_feature_source",
                    "live_feature_record_count",
                    "live_feature_last_obs_utc",
                    "observation_cache_generated_at_utc",
                    "observation_cache_fetched_at_utc",
                    "observation_cadence_min",
                    "official_observation_station",
                ]
            }
        )
    base.update(city_wind_context(city, base.get("wind_dir_deg")))
    labelled = atlas.add_regime_labels(pd.DataFrame([base])).iloc[0].to_dict()
    books = market_rows_for_city(sub, running_value=running_value, running_native=running_native, unit=unit)
    if books.empty:
        return None, "no_book_rows"
    books = books[
        books["book_target_date"].astype(str).eq(target_date) & books["book_market_date_match"].astype(bool)
    ].copy()
    if books.empty:
        return None, "market_date_mismatch_or_no_same_day_book_rows"

    cur_yes = books[books["outcome"].eq("yes") & books["contains_running"]]
    cur_no = books[books["outcome"].eq("no") & books["contains_running"]]
    d2_no = books[books["outcome"].eq("no") & books["tail_distance"].eq(2)]
    if not cur_yes.empty:
        row = cur_yes.sort_values("book_ask").iloc[0]
        labelled.update(
            {
                "current_bracket": str(row["bracket"]),
                "current_yes_ask": row["book_ask"],
                "current_yes_ask_size": row["book_ask_size"],
            }
        )
    if not cur_no.empty:
        row = cur_no.sort_values("book_ask").iloc[0]
        labelled.update(
            {
                "current_no_ask": row["book_ask"],
                "current_no_ask_size": row["book_ask_size"],
                "current_no_bid": row["book_bid"],
                "current_no_token_id": row["book_token_id"],
                "current_no_market_id": str(row.get("market_id") or ""),
                "current_no_event_slug": str(row.get("event_slug") or ""),
                "current_no_question": str(row.get("question") or ""),
                "current_no_market_event_date": str(row.get("book_market_event_date") or ""),
                "current_no_market_date_match": bool(row.get("book_market_date_match")),
            }
        )
    if not d2_no.empty:
        row = d2_no.sort_values("book_ask").iloc[0]
        labelled.update(
            {
                "d2_no_bracket": str(row["bracket"]),
                "d2_no_ask": row["book_ask"],
                "d2_no_ask_size": row["book_ask_size"],
                "d2_no_bid": row["book_bid"],
                "d2_no_token_id": row["book_token_id"],
                "d2_no_market_id": str(row.get("market_id") or ""),
                "d2_no_event_slug": str(row.get("event_slug") or ""),
                "d2_no_question": str(row.get("question") or ""),
                "d2_no_market_event_date": str(row.get("book_market_event_date") or ""),
                "d2_no_market_date_match": bool(row.get("book_market_date_match")),
            }
        )

    regime = str(labelled.get("day_regime") or "")
    current_no_route = current_no_route_for_state(labelled) if regime in {"day_open_runway", "day_marginal_runway"} else ""
    if current_no_route and "current_no_ask" in labelled:
        labelled.update(
            {
                "expression": "current_bracket_no",
                "route_leg": current_no_route,
                "ask": labelled["current_no_ask"],
                "ask_size": labelled["current_no_ask_size"],
                "bid": labelled.get("current_no_bid"),
                "token_id": labelled["current_no_token_id"],
                "market_id": labelled["current_no_market_id"],
                "event_slug": labelled["current_no_event_slug"],
                "question": labelled["current_no_question"],
                "market_event_date": labelled.get("current_no_market_event_date"),
                "market_date_match": labelled.get("current_no_market_date_match"),
                "bracket": labelled.get("current_bracket"),
            }
        )
        return labelled, "routed"
    if regime in {"day_open_runway", "day_marginal_runway"} and "current_no_ask" in labelled:
        return None, (
            "no_route_current_no_state_not_no_pullback:"
            f"{labelled.get('running_max_state') or ''}:{labelled.get('intraday_state') or ''}"
        )
    if regime == "day_forecast_capped" and "d2_no_ask" in labelled:
        labelled.update(
            {
                "expression": "d2_no",
                "route_leg": "capped_d2_no",
                "ask": labelled["d2_no_ask"],
                "ask_size": labelled["d2_no_ask_size"],
                "bid": labelled.get("d2_no_bid"),
                "token_id": labelled["d2_no_token_id"],
                "market_id": labelled["d2_no_market_id"],
                "event_slug": labelled["d2_no_event_slug"],
                "question": labelled["d2_no_question"],
                "market_event_date": labelled.get("d2_no_market_event_date"),
                "market_date_match": labelled.get("d2_no_market_date_match"),
                "bracket": labelled.get("d2_no_bracket"),
            }
        )
        return labelled, "routed"
    return None, f"no_route_or_missing_book:{regime}"


def build_candidates(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    snapshot_path = latest_snapshot_path(Path(args.snapshot_dir), args.snapshot)
    payload, records = load_snapshot(snapshot_path)
    now_utc = datetime.now(timezone.utc)
    snapshot_ts = parse_utc(payload.get("ts_utc"))
    snapshot_age_min = (now_utc - snapshot_ts).total_seconds() / 60.0 if snapshot_ts else math.nan
    observation_cache, observation_cache_path, observation_cache_status = load_latest_observation_cache(args.observation_cache)
    base_meta = {
        "snapshot": str(snapshot_path.relative_to(ROOT) if snapshot_path.is_relative_to(ROOT) else snapshot_path),
        "snapshot_ts_utc": payload.get("ts_utc"),
        "snapshot_rows": int(len(records)),
        "snapshot_age_min": None if not math.isfinite(snapshot_age_min) else round(snapshot_age_min, 3),
        "max_snapshot_age_min": float(args.max_snapshot_age_min),
        "observation_cache_path": str(observation_cache_path) if observation_cache_path else "",
        "observation_cache_status": observation_cache_status,
        "observation_cache_generated_at_utc": str((observation_cache or {}).get("generated_at_utc") or ""),
    }
    if records.empty:
        return pd.DataFrame(), {**base_meta, "audit_counts": {"empty_snapshot": 1}, "audits": []}
    if math.isfinite(snapshot_age_min) and snapshot_age_min > float(args.max_snapshot_age_min):
        return pd.DataFrame(), {
            **base_meta,
            "audit_counts": {"stale_snapshot": 1},
            "audits": [{"city": "*", "status": f"stale_snapshot:{round(snapshot_age_min, 1)}"}],
        }
    if args.target_date:
        records = records[records["target_date"].eq(str(args.target_date))].copy()
    records, current_local_meta = filter_current_local_day_records(records)
    base_meta["current_local_day_filter"] = current_local_meta
    configs = {cfg.city: cfg for cfg in load_city_configs(include_station_diff=bool(args.include_station_diff))}
    if args.cities:
        configs = {city: cfg for city, cfg in configs.items() if city in set(args.cities)}

    audits: list[dict[str, Any]] = []
    routed: list[dict[str, Any]] = []
    for city, cfg in sorted(configs.items()):
        city_rows = records[records["city"].eq(city)].copy()
        if city_rows.empty:
            audits.append({"city": city, "status": "no_snapshot_records"})
            continue
        for target_date, sub in city_rows.groupby("target_date", sort=True):
            row, status = build_city_state(
                city=city,
                cfg=cfg,
                sub=sub.copy(),
                observation_cache=observation_cache,
                obs_source=args.obs_source,
                recent_hours=float(args.recent_hours),
                max_obs_age_min=float(args.max_obs_age_min),
            )
            audits.append({"city": city, "target_date": str(target_date), "status": status})
            if row is not None:
                routed.append(row)

    selected = pd.DataFrame(routed)
    if not selected.empty:
        selected = research.add_soft_weights(selected)
        wind_speed = pd.to_numeric(selected.get("wind_speed_kt"), errors="coerce")
        coastal_flow = selected.get("coastal_flow_state", pd.Series("", index=selected.index)).astype(str)
        geo_context = selected.get("geo_context", pd.Series("", index=selected.index)).astype(str)
        selected["wind_only_multiplier_shadow"] = pd.Series(
            [0.80 if value >= 18 else 0.95 if value >= 10 else 1.00 for value in wind_speed.fillna(-1)],
            index=selected.index,
            dtype="float64",
        )
        selected["wind_context_multiplier_shadow"] = pd.Series(
            [
                0.70
                if math.isfinite(value) and value >= 18 and flow == "onshore_marine_flow"
                else 0.80
                if math.isfinite(value) and value >= 18 and geo.startswith("coastal")
                else 0.88
                if math.isfinite(value) and value >= 18
                else 0.90
                if math.isfinite(value) and value >= 10 and flow == "onshore_marine_flow"
                else 0.96
                if math.isfinite(value) and value >= 10
                else 1.00
                for value, flow, geo in zip(wind_speed, coastal_flow, geo_context, strict=False)
            ],
            index=selected.index,
            dtype="float64",
        )
        selected["soft_wind_only_shadow"] = (
            pd.to_numeric(selected["soft_balanced"], errors="coerce") * selected["wind_only_multiplier_shadow"]
        ).clip(0.0, 1.0)
        selected["soft_wind_context_shadow"] = (
            pd.to_numeric(selected["soft_balanced"], errors="coerce") * selected["wind_context_multiplier_shadow"]
        ).clip(0.0, 1.0)
        temp_context = selected.apply(lambda row: temperature_context_features(row.to_dict()), axis=1, result_type="expand")
        for col in temp_context.columns:
            selected[col] = temp_context[col]
        selected["temp_context_multiplier_light_shadow"] = selected.apply(
            lambda row: temperature_context_multiplier(row.to_dict(), strength="light"),
            axis=1,
        )
        selected["temp_context_multiplier_medium_shadow"] = selected.apply(
            lambda row: temperature_context_multiplier(row.to_dict(), strength="medium"),
            axis=1,
        )
        selected["soft_temp_context_light_shadow"] = (
            pd.to_numeric(selected["soft_balanced"], errors="coerce")
            * pd.to_numeric(selected["temp_context_multiplier_light_shadow"], errors="coerce")
        ).clip(0.0, 1.2)
        selected["soft_temp_context_medium_shadow"] = (
            pd.to_numeric(selected["soft_balanced"], errors="coerce")
            * pd.to_numeric(selected["temp_context_multiplier_medium_shadow"], errors="coerce")
        ).clip(0.0, 1.2)
        prior_keys = prior_live_order_keys(LIVE_OUT)
        selected["live_duplicate_key"] = selected.apply(
            lambda row: (
                str(row.get("city") or ""),
                str(row.get("target_date") or ""),
                str(row.get("token_id") or ""),
            )
            in prior_keys,
            axis=1,
        )
        selected["live_feature_parity_ok"] = (
            selected["live_feature_status"].astype(str).eq("ok")
            & selected[CORE_LIVE_REGIME_COLS].apply(lambda col: pd.to_numeric(col, errors="coerce").notna()).all(axis=1)
            & ~selected[CORE_LIVE_REGIME_LABELS].astype(str).apply(lambda row: any("unknown" in item for item in row), axis=1)
        )
        peak_delta = pd.to_numeric(selected.get("forecast_peak_delta_hours_local"), errors="coerce")
        route_leg = selected["route_leg"].astype(str)
        is_current_no_route = route_leg.isin(CURRENT_NO_ROUTE_LEGS) | selected["expression"].astype(str).eq("current_bracket_no")
        is_fresh_current_no_route = route_leg.isin({"runway_current_no", "fresh_runway_current_no"})
        selected["current_no_peak_clock_ok"] = (~is_fresh_current_no_route) | peak_delta.le(0.0)
        selected["current_no_escape_threshold_native"] = selected["bracket"].apply(current_no_escape_threshold_native)
        selected.loc[~is_current_no_route, "current_no_escape_threshold_native"] = pd.NA
        selected["current_no_escape_margin_native"] = pd.to_numeric(
            selected.get("forecast_max_native"), errors="coerce"
        ) - pd.to_numeric(selected["current_no_escape_threshold_native"], errors="coerce")
        selected["current_no_escape_ok"] = (~is_current_no_route) | pd.to_numeric(
            selected["current_no_escape_margin_native"], errors="coerce"
        ).gt(float(args.min_current_no_escape_margin_native))
        selected["market_date_match_ok"] = (
            selected.get("market_date_match", pd.Series(False, index=selected.index)).fillna(False).astype(bool)
        )
        selected["current_no_runway_state_ok"] = selected.apply(current_no_runway_state_ok, axis=1).fillna(False).astype(bool)
        selected["route_price_cap"] = selected["route_leg"].map(ROUTE_PRICE_CAPS).astype(float)
        selected["route_price_ok"] = pd.to_numeric(selected["ask"], errors="coerce").le(selected["route_price_cap"])
        selected["row_risk_soft_v1"] = row_risk_soft_v1(selected)
        selected["base_notional_usd"] = float(args.base_notional)
        selected["legacy_soft_balanced_notional_usd"] = selected["base_notional_usd"] * pd.to_numeric(
            selected["soft_balanced"], errors="coerce"
        )
        selected["soft_notional_usd"] = selected["base_notional_usd"] * pd.to_numeric(
            selected["row_risk_soft_v1"], errors="coerce"
        )
        selected["soft_shares"] = selected["soft_notional_usd"] / pd.to_numeric(selected["ask"], errors="coerce")
        selected["soft_wind_only_shadow_notional_usd"] = selected["base_notional_usd"] * pd.to_numeric(
            selected["soft_wind_only_shadow"], errors="coerce"
        )
        selected["soft_wind_only_shadow_shares"] = selected["soft_wind_only_shadow_notional_usd"] / pd.to_numeric(
            selected["ask"], errors="coerce"
        )
        selected["soft_wind_context_shadow_notional_usd"] = selected["base_notional_usd"] * pd.to_numeric(
            selected["soft_wind_context_shadow"], errors="coerce"
        )
        selected["soft_wind_context_shadow_shares"] = selected["soft_wind_context_shadow_notional_usd"] / pd.to_numeric(
            selected["ask"], errors="coerce"
        )
        selected["soft_temp_context_light_shadow_notional_usd"] = selected["base_notional_usd"] * pd.to_numeric(
            selected["soft_temp_context_light_shadow"], errors="coerce"
        )
        selected["soft_temp_context_light_shadow_shares"] = selected["soft_temp_context_light_shadow_notional_usd"] / pd.to_numeric(
            selected["ask"], errors="coerce"
        )
        selected["soft_temp_context_medium_shadow_notional_usd"] = selected["base_notional_usd"] * pd.to_numeric(
            selected["soft_temp_context_medium_shadow"], errors="coerce"
        )
        selected["soft_temp_context_medium_shadow_shares"] = selected[
            "soft_temp_context_medium_shadow_notional_usd"
        ] / pd.to_numeric(selected["ask"], errors="coerce")
        selected["ask_notional"] = pd.to_numeric(selected["ask"], errors="coerce") * pd.to_numeric(selected["ask_size"], errors="coerce")
        selected["execution_eligible"] = (
            pd.to_numeric(selected["ask"], errors="coerce").ge(research.ASK_MIN)
            & selected["route_price_ok"].astype(bool)
            & pd.to_numeric(selected["soft_shares"], errors="coerce").ge(float(args.min_order_shares))
            & pd.to_numeric(selected["ask_size"], errors="coerce").ge(pd.to_numeric(selected["soft_shares"], errors="coerce"))
            & selected["token_id"].astype(str).ne("")
            & selected["live_feature_parity_ok"].astype(bool)
            & selected["current_no_peak_clock_ok"].astype(bool)
            & selected["current_no_escape_ok"].astype(bool)
            & selected["market_date_match_ok"].fillna(False).astype(bool)
            & ~selected["live_duplicate_key"].astype(bool)
        )
        def skip_reason(row: pd.Series) -> str:
            if bool(row.get("execution_eligible")):
                return ""
            reasons: list[str] = []
            ask = safe_float(row.get("ask"))
            soft_shares = safe_float(row.get("soft_shares"))
            ask_size = safe_float(row.get("ask_size"))
            if math.isfinite(ask) and ask < research.ASK_MIN:
                reasons.append("ask_below_min")
            if not bool(row.get("route_price_ok", True)):
                reasons.append("ask_above_route_price_cap")
            if math.isfinite(soft_shares) and soft_shares < float(args.min_order_shares):
                reasons.append("soft_size_below_min_shares")
            if math.isfinite(ask_size) and math.isfinite(soft_shares) and ask_size < soft_shares:
                reasons.append("insufficient_top_ask_size")
            if str(row.get("token_id") or "") == "":
                reasons.append("missing_token_id")
            if not bool(row.get("live_feature_parity_ok")):
                reasons.append("live_feature_parity_failed")
            if not bool(row.get("current_no_peak_clock_ok", True)):
                reasons.append("current_no_peak_clock_past_or_missing")
            if not bool(row.get("current_no_escape_ok", True)):
                reasons.append("current_no_forecast_escape_margin_nonpositive")
            if not bool(row.get("market_date_match_ok", True)):
                reasons.append("market_event_date_mismatch")
            if bool(row.get("live_duplicate_key")):
                reasons.append("duplicate_live_city_date_token")
            return "|".join(reasons) if reasons else "not_execution_eligible"

        selected["execution_skip_reason"] = selected.apply(skip_reason, axis=1)
    meta = {
        **base_meta,
        "candidate_input_rows": int(len(records)),
        "audit_counts": pd.Series([a["status"].split(":", 1)[0] for a in audits]).value_counts().to_dict() if audits else {},
        "audits": audits,
    }
    return selected, meta


def candidate_record(row: pd.Series, *, meta: dict[str, Any], accepted: bool) -> dict[str, Any]:
    payload = {
        "record_type": "regime_routed_no_candidate",
        "created_at_utc": utc_now_iso(),
        "candidate_id": "regime-no-candidate-" + stable_hash(
            {
                "city": row.get("city"),
                "target_date": row.get("target_date"),
                "token_id": row.get("token_id"),
                "decision_snapshot_ts_utc": row.get("decision_snapshot_ts_utc"),
                "route_leg": row.get("route_leg"),
            }
        ),
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "rule_id": RULE_ID,
        "candidate_status": "accepted" if accepted else "blocked",
        "execution_skip_reason": "" if accepted else str(row.get("execution_skip_reason") or ""),
        "city": row.get("city"),
        "target_date": row.get("target_date"),
        "decision_snapshot_ts_utc": row.get("decision_snapshot_ts_utc"),
        "decision_hour_local": row.get("decision_hour_local"),
        "decision_hour_local_float": row.get("decision_hour_local_float"),
        "day_regime": row.get("day_regime"),
        "intraday_state": row.get("intraday_state"),
        "moisture_cloud_regime": row.get("moisture_cloud_regime"),
        "wind_regime": row.get("wind_regime"),
        "running_max_state": row.get("running_max_state"),
        "expression": row.get("expression"),
        "route_leg": row.get("route_leg"),
        "bracket": row.get("bracket"),
        "ask": row.get("ask"),
        "bid": row.get("bid"),
        "ask_size": row.get("ask_size"),
        "token_id": row.get("token_id"),
        "market_id": row.get("market_id"),
        "event_slug": row.get("event_slug"),
        "question": row.get("question"),
        "market_event_date": row.get("market_event_date"),
        "market_date_match": row.get("market_date_match"),
        "market_date_match_ok": row.get("market_date_match_ok"),
        "base_notional_usd": row.get("base_notional_usd"),
        "soft_balanced": row.get("soft_balanced"),
        "legacy_soft_balanced_notional_usd": row.get("legacy_soft_balanced_notional_usd"),
        "row_risk_soft_v1": row.get("row_risk_soft_v1"),
        "soft_notional_usd": row.get("soft_notional_usd"),
        "soft_shares": row.get("soft_shares"),
        "route_price_cap": row.get("route_price_cap"),
        "route_price_ok": row.get("route_price_ok"),
        "wind_only_multiplier_shadow": row.get("wind_only_multiplier_shadow"),
        "wind_context_multiplier_shadow": row.get("wind_context_multiplier_shadow"),
        "soft_wind_only_shadow": row.get("soft_wind_only_shadow"),
        "soft_wind_only_shadow_notional_usd": row.get("soft_wind_only_shadow_notional_usd"),
        "soft_wind_only_shadow_shares": row.get("soft_wind_only_shadow_shares"),
        "soft_wind_context_shadow": row.get("soft_wind_context_shadow"),
        "soft_wind_context_shadow_notional_usd": row.get("soft_wind_context_shadow_notional_usd"),
        "soft_wind_context_shadow_shares": row.get("soft_wind_context_shadow_shares"),
        "temp_context_multiplier_light_shadow": row.get("temp_context_multiplier_light_shadow"),
        "temp_context_multiplier_medium_shadow": row.get("temp_context_multiplier_medium_shadow"),
        "soft_temp_context_light_shadow": row.get("soft_temp_context_light_shadow"),
        "soft_temp_context_light_shadow_notional_usd": row.get("soft_temp_context_light_shadow_notional_usd"),
        "soft_temp_context_light_shadow_shares": row.get("soft_temp_context_light_shadow_shares"),
        "soft_temp_context_medium_shadow": row.get("soft_temp_context_medium_shadow"),
        "soft_temp_context_medium_shadow_notional_usd": row.get("soft_temp_context_medium_shadow_notional_usd"),
        "soft_temp_context_medium_shadow_shares": row.get("soft_temp_context_medium_shadow_shares"),
        "wind_thermal_state": row.get("wind_thermal_state"),
        "marine_thermal_state": row.get("marine_thermal_state"),
        "sky_state": row.get("sky_state"),
        "moisture_state": row.get("moisture_state"),
        "warming_state": row.get("warming_state"),
        "cloud_warming_interaction": row.get("cloud_warming_interaction"),
        "moisture_cloud_interaction": row.get("moisture_cloud_interaction"),
        "forecast_peak_clock_state": row.get("forecast_peak_clock_state"),
        "temperature_context_regime": row.get("temperature_context_regime"),
        "ask_notional": row.get("ask_notional"),
        "live_feature_status": row.get("live_feature_status"),
        "live_feature_source": row.get("live_feature_source"),
        "live_feature_parity_ok": row.get("live_feature_parity_ok"),
        "current_no_peak_clock_ok": row.get("current_no_peak_clock_ok"),
        "current_no_escape_threshold_native": row.get("current_no_escape_threshold_native"),
        "current_no_escape_margin_native": row.get("current_no_escape_margin_native"),
        "current_no_escape_ok": row.get("current_no_escape_ok"),
        "current_no_runway_state_ok": row.get("current_no_runway_state_ok"),
        "live_duplicate_key": row.get("live_duplicate_key"),
        "temp_trend_1h_f": row.get("temp_trend_1h_f"),
        "temp_trend_3h_f": row.get("temp_trend_3h_f"),
        "relative_humidity_pct": row.get("relative_humidity_pct"),
        "dewpoint_depression_f": row.get("dewpoint_depression_f"),
        "wind_speed_kt": row.get("wind_speed_kt"),
        "wind_dir_deg": row.get("wind_dir_deg"),
        "wind_sector": row.get("wind_sector"),
        "geo_context": row.get("geo_context"),
        "coastal_flow_state": row.get("coastal_flow_state"),
        "is_coastal_context": row.get("is_coastal_context"),
        "minutes_since_running_max": row.get("minutes_since_running_max"),
        "forecast_source": row.get("forecast_source"),
        "forecast_max_native": row.get("forecast_max_native"),
        "forecast_peak_hour_local": row.get("forecast_peak_hour_local"),
        "forecast_peak_delta_hours_local": row.get("forecast_peak_delta_hours_local"),
        "peak_clock_state": row.get("peak_clock_state"),
        "peak_clock_multiplier": row.get("peak_clock_multiplier"),
        "forecast_gap_to_running_native": row.get("forecast_gap_to_running_native"),
        "running_native": row.get("running_native"),
        "current_native": row.get("current_native"),
        "snapshot": meta.get("snapshot"),
        "snapshot_ts_utc": meta.get("snapshot_ts_utc"),
        "snapshot_age_min": meta.get("snapshot_age_min"),
        "observation_cache_path": meta.get("observation_cache_path"),
        "observation_cache_status": meta.get("observation_cache_status"),
        "observation_cache_generated_at_utc": meta.get("observation_cache_generated_at_utc"),
    }
    return json_safe(payload)


def write_candidate_audit(candidates: pd.DataFrame, meta: dict[str, Any]) -> tuple[int, int]:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if candidates.empty:
        write_json(
            LATEST_CANDIDATES_OUT,
            {
                "generated_at_utc": utc_now_iso(),
                "strategy_id": STRATEGY_ID,
                "strategy_instance": STRATEGY_INSTANCE,
                "meta": meta,
                "candidates": [],
            },
        )
        return 0, 0
    records = [
        candidate_record(row, meta=meta, accepted=bool(row.get("execution_eligible")))
        for _, row in candidates.iterrows()
    ]
    blocked = [row for row in records if row.get("candidate_status") == "blocked"]
    write_json(
        LATEST_CANDIDATES_OUT,
        {
            "generated_at_utc": utc_now_iso(),
            "strategy_id": STRATEGY_ID,
            "strategy_instance": STRATEGY_INSTANCE,
            "meta": meta,
            "candidates": records,
        },
    )
    for row in blocked:
        append_jsonl(BLOCKED_OUT, row)
    return len(records), len(blocked)


def build_plan(row: pd.Series, *, live_enabled: bool, ttl_min: float) -> dict[str, Any]:
    ask = safe_float(row.get("ask"))
    soft_notional = safe_float(row.get("soft_notional_usd"))
    size = safe_float(row.get("soft_shares"))
    expires_at = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + ttl_min * 60.0, tz=timezone.utc)
    signal_base = {
        "strategy_id": STRATEGY_ID,
        "rule_id": RULE_ID,
        "city": row.get("city"),
        "target_date": row.get("target_date"),
        "decision_snapshot_ts_utc": row.get("decision_snapshot_ts_utc"),
        "expression": row.get("expression"),
        "bracket": row.get("bracket"),
        "token_id": row.get("token_id"),
    }
    signal_id = "regime-no-" + stable_hash(signal_base)
    plan_base = {**signal_base, "signal_id": signal_id, "live_enabled": bool(live_enabled)}
    plan_id = "plan-" + stable_hash(plan_base)
    return {
        "record_type": "weather_edge_trade_plan",
        "plan_id": plan_id,
        "signal_id": signal_id,
        "created_at_utc": utc_now_iso(),
        "status": "accepted",
        "risk_status": "passed",
        "strategy": "weather_edge_v1",
        "strategy_instance": STRATEGY_INSTANCE,
        "source_strategy_instance": STRATEGY_INSTANCE,
        "strategy_id": STRATEGY_ID,
        "strategy_family": "reheat_risk_regime_routed_no",
        "probability_source": "intraday_weather_regime_atlas_v1_route_price_row_risk_soft",
        "decision_mode": "regime_routed_no_route_price_disciplined",
        "entry_profile": str(row.get("day_regime") or ""),
        "expression": str(row.get("expression") or ""),
        "route_leg": str(row.get("route_leg") or ""),
        "execution_mode": "tiny_live_taker_skip_below_min_shares",
        "profile": "route_price_disciplined_no_pullback_row_risk_soft",
        "combo": RULE_ID,
        "city": str(row.get("city") or ""),
        "city_pool": "source_policy_live_eligible",
        "target_date": str(row.get("target_date") or ""),
        "market_id": str(row.get("market_id") or ""),
        "event_slug": str(row.get("event_slug") or ""),
        "market_slug": str(row.get("event_slug") or ""),
        "question": str(row.get("question") or ""),
        "market_event_date": str(row.get("market_event_date") or ""),
        "market_date_match_ok": bool(row.get("market_date_match_ok")),
        "bracket": str(row.get("bracket") or ""),
        "token_id": str(row.get("token_id") or ""),
        "signal_side": "BUY_NO",
        "order_side": "BUY",
        "market_price": round(ask, 6),
        "best_bid": round(safe_float(row.get("bid"), 0.0), 6),
        "best_ask": round(ask, 6),
        "spread": round(max(0.0, ask - safe_float(row.get("bid"), 0.0)), 6),
        "limit_price": round(ask, 6),
        "quote_status": "accepted",
        "quote_reason": "regime_routed_no_top_ask_taker",
        "quote_edge": 0.0,
        "required_quote_edge": 0.0,
        "model_token_probability": 0.0,
        "quote_best_bid": round(safe_float(row.get("bid"), 0.0), 6),
        "quote_best_ask": round(ask, 6),
        "quote_spread": round(max(0.0, ask - safe_float(row.get("bid"), 0.0)), 6),
        "quote_tick_size": 0.001,
        "quote_mode": "top_ask_taker_live_probe",
        "child_order_role": "single",
        "maker_only": False,
        "notional_fraction": 1.0,
        "size_multiplier": round(safe_float(row.get("row_risk_soft_v1"), 0.0), 6),
        "order_notional_cap": round(soft_notional, 6),
        "entry_price_window": "0.10-0.70",
        "execution_policy": "regime_routed_no_taker_v1",
        "tick_size": 0.001,
        "sizing_mode": "notional",
        "fixed_order_shares": 0.0,
        "max_order_shares": round(size, 6),
        "size": round(size, 6),
        "notional": round(size * ask, 6),
        "edge": 0.0,
        "min_edge": 0.0,
        "paper_enabled": True,
        "live_enabled": bool(live_enabled),
        "shadow_decision": RULE_ID,
        "shadow_reason": "tiny_live_user_approved_route_price_disciplined_probe_skip_below_min_shares",
        "model_version": "regime_routed_expression_router_v3_route_price_disciplined",
        "obs_source": str(row.get("obs_source") or ""),
        "expires_at_utc": expires_at.isoformat(timespec="seconds"),
        "order_ttl_min": round(float(ttl_min), 6),
        "expiry_policy": "fixed_tiny_live_probe_ttl",
        "day_regime": str(row.get("day_regime") or ""),
        "intraday_state": str(row.get("intraday_state") or ""),
        "moisture_cloud_regime": str(row.get("moisture_cloud_regime") or ""),
        "wind_regime": str(row.get("wind_regime") or ""),
        "running_max_state": str(row.get("running_max_state") or ""),
        "soft_balanced_multiplier": round(safe_float(row.get("soft_balanced"), 0.0), 6),
        "legacy_soft_balanced_notional_usd": round(safe_float(row.get("legacy_soft_balanced_notional_usd"), 0.0), 6),
        "row_risk_soft_v1": round(safe_float(row.get("row_risk_soft_v1"), 0.0), 6),
        "route_price_cap": round(safe_float(row.get("route_price_cap"), 0.0), 6),
        "route_price_ok": bool(row.get("route_price_ok")),
        "wind_only_multiplier_shadow": round(safe_float(row.get("wind_only_multiplier_shadow"), 1.0), 6),
        "wind_context_multiplier_shadow": round(safe_float(row.get("wind_context_multiplier_shadow"), 1.0), 6),
        "soft_wind_only_shadow": round(safe_float(row.get("soft_wind_only_shadow"), 0.0), 6),
        "soft_wind_context_shadow": round(safe_float(row.get("soft_wind_context_shadow"), 0.0), 6),
        "temp_context_multiplier_light_shadow": round(safe_float(row.get("temp_context_multiplier_light_shadow"), 1.0), 6),
        "temp_context_multiplier_medium_shadow": round(safe_float(row.get("temp_context_multiplier_medium_shadow"), 1.0), 6),
        "soft_temp_context_light_shadow": round(safe_float(row.get("soft_temp_context_light_shadow"), 0.0), 6),
        "soft_temp_context_medium_shadow": round(safe_float(row.get("soft_temp_context_medium_shadow"), 0.0), 6),
        "base_notional_usd": round(safe_float(row.get("base_notional_usd"), 0.0), 6),
        "soft_notional_usd": round(soft_notional, 6),
        "soft_shares": round(size, 6),
        "soft_wind_only_shadow_notional_usd": round(safe_float(row.get("soft_wind_only_shadow_notional_usd"), 0.0), 6),
        "soft_wind_only_shadow_shares": round(safe_float(row.get("soft_wind_only_shadow_shares"), 0.0), 6),
        "soft_wind_context_shadow_notional_usd": round(safe_float(row.get("soft_wind_context_shadow_notional_usd"), 0.0), 6),
        "soft_wind_context_shadow_shares": round(safe_float(row.get("soft_wind_context_shadow_shares"), 0.0), 6),
        "soft_temp_context_light_shadow_notional_usd": round(
            safe_float(row.get("soft_temp_context_light_shadow_notional_usd"), 0.0), 6
        ),
        "soft_temp_context_light_shadow_shares": round(
            safe_float(row.get("soft_temp_context_light_shadow_shares"), 0.0), 6
        ),
        "soft_temp_context_medium_shadow_notional_usd": round(
            safe_float(row.get("soft_temp_context_medium_shadow_notional_usd"), 0.0), 6
        ),
        "soft_temp_context_medium_shadow_shares": round(
            safe_float(row.get("soft_temp_context_medium_shadow_shares"), 0.0), 6
        ),
        "wind_thermal_state": str(row.get("wind_thermal_state") or ""),
        "marine_thermal_state": str(row.get("marine_thermal_state") or ""),
        "sky_state": str(row.get("sky_state") or ""),
        "moisture_state": str(row.get("moisture_state") or ""),
        "warming_state": str(row.get("warming_state") or ""),
        "cloud_warming_interaction": str(row.get("cloud_warming_interaction") or ""),
        "moisture_cloud_interaction": str(row.get("moisture_cloud_interaction") or ""),
        "forecast_peak_clock_state": str(row.get("forecast_peak_clock_state") or ""),
        "temperature_context_regime": str(row.get("temperature_context_regime") or ""),
        "forecast_source": str(row.get("forecast_source") or ""),
        "forecast_max_native": safe_float(row.get("forecast_max_native"), None),
        "forecast_peak_hour_local": safe_float(row.get("forecast_peak_hour_local"), None),
        "forecast_peak_delta_hours_local": safe_float(row.get("forecast_peak_delta_hours_local"), None),
        "current_no_escape_threshold_native": safe_float(row.get("current_no_escape_threshold_native"), None),
        "current_no_escape_margin_native": safe_float(row.get("current_no_escape_margin_native"), None),
        "current_no_escape_ok": bool(row.get("current_no_escape_ok", True)),
        "current_no_runway_state_ok": bool(row.get("current_no_runway_state_ok", True)),
        "decision_hour_local": safe_float(row.get("decision_hour_local"), None),
        "decision_hour_local_float": safe_float(row.get("decision_hour_local_float"), None),
        "peak_clock_state": str(row.get("peak_clock_state") or ""),
        "peak_clock_multiplier": safe_float(row.get("peak_clock_multiplier"), None),
        "running_native": safe_float(row.get("running_native"), None),
        "current_native": safe_float(row.get("current_native"), None),
        "live_feature_status": str(row.get("live_feature_status") or ""),
        "live_feature_parity_ok": bool(row.get("live_feature_parity_ok")),
        "temp_trend_1h_f": safe_float(row.get("temp_trend_1h_f"), None),
        "temp_trend_3h_f": safe_float(row.get("temp_trend_3h_f"), None),
        "relative_humidity_pct": safe_float(row.get("relative_humidity_pct"), None),
        "sky_cover_code": safe_float(row.get("sky_cover_code"), None),
        "dewpoint_depression_f": safe_float(row.get("dewpoint_depression_f"), None),
        "wind_speed_kt": safe_float(row.get("wind_speed_kt"), None),
        "wind_dir_deg": safe_float(row.get("wind_dir_deg"), None),
        "wind_sector": str(row.get("wind_sector") or ""),
        "geo_context": str(row.get("geo_context") or ""),
        "coastal_flow_state": str(row.get("coastal_flow_state") or ""),
        "is_coastal_context": bool(row.get("is_coastal_context")),
        "minutes_since_running_max": safe_float(row.get("minutes_since_running_max"), None),
    }


def write_plans(candidates: pd.DataFrame, args: argparse.Namespace) -> list[dict[str, Any]]:
    live_enabled = bool(args.live and args.confirm_live)
    if candidates.empty or "execution_eligible" not in candidates.columns:
        eligible = pd.DataFrame()
    else:
        eligible = candidates[candidates["execution_eligible"].astype(bool)].copy()
    spent_by_date = live_order_spent_by_date(LIVE_OUT)
    prior_keys = prior_live_order_keys(LIVE_OUT)
    plans: list[dict[str, Any]] = []
    running_spent_by_date = dict(spent_by_date)
    if eligible.empty:
        PLAN_OUT.parent.mkdir(parents=True, exist_ok=True)
        PLAN_OUT.write_text("", encoding="utf-8")
        return plans
    for _, row in eligible.sort_values(["target_date", "decision_snapshot_ts_utc", "city"]).iterrows():
        key = (str(row.get("city") or ""), str(row.get("target_date") or ""), str(row.get("token_id") or ""))
        if key in prior_keys:
            continue
        cost = safe_float(row.get("soft_notional_usd"), 0.0)
        target_date = str(row.get("target_date") or "")
        running_spent = running_spent_by_date.get(target_date, 0.0)
        if running_spent + cost > float(args.daily_gross_cap) + 1e-9:
            continue
        plans.append(build_plan(row, live_enabled=live_enabled, ttl_min=float(args.order_ttl_min)))
        running_spent_by_date[target_date] = running_spent + cost
        prior_keys.add(key)
        if len(plans) >= int(args.max_orders):
            break
    PLAN_OUT.parent.mkdir(parents=True, exist_ok=True)
    PLAN_OUT.write_text("\n".join(json.dumps(p, ensure_ascii=False, sort_keys=True) for p in plans) + ("\n" if plans else ""), encoding="utf-8")
    return plans


def run_executor(args: argparse.Namespace) -> dict[str, Any] | None:
    if not args.live:
        return None
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
        "--cancel-expired",
        "--no-telegram",
    ]
    proc = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    parsed = None
    try:
        start = proc.stdout.find("{")
        end = proc.stdout.rfind("}")
        parsed = json.loads(proc.stdout[start : end + 1]) if start >= 0 and end > start else None
    except Exception:
        parsed = None
    return {"cmd": cmd, "returncode": proc.returncode, "output_tail": proc.stdout[-8000:], "parsed": parsed}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--target-date", default="")
    parser.add_argument("--cities", nargs="*", default=[])
    parser.add_argument("--include-station-diff", action="store_true")
    parser.add_argument(
        "--obs-source",
        choices=[
            "weather_data_feed_observation_cache",
            "aviationweather_metar",
            "synopticdata_timeseries",
            "noaa_tgftp_station_txt",
        ],
        default="weather_data_feed_observation_cache",
    )
    parser.add_argument("--observation-cache", default="")
    parser.add_argument("--recent-hours", type=float, default=30.0)
    parser.add_argument("--max-obs-age-min", type=float, default=20.0)
    parser.add_argument("--max-snapshot-age-min", type=float, default=45.0)
    parser.add_argument("--base-notional", type=float, default=5.0)
    parser.add_argument("--daily-gross-cap", type=float, default=5.0)
    parser.add_argument("--min-order-shares", type=float, default=5.0)
    parser.add_argument("--min-current-no-escape-margin-native", type=float, default=0.0)
    parser.add_argument("--max-orders", type=int, default=1)
    parser.add_argument("--order-ttl-min", type=float, default=30.0)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    return parser.parse_args()


def shadow_policy_counts(candidates: pd.DataFrame, *, min_order_shares: float) -> dict[str, dict[str, Any]]:
    if candidates.empty:
        return {}
    policies = {
        "route_price_row_risk_live_policy": "row_risk_soft_v1",
        "legacy_soft_balanced_shadow": "soft_balanced",
        "soft_wind_only_shadow": "soft_wind_only_shadow",
        "soft_wind_context_shadow": "soft_wind_context_shadow",
        "soft_temp_context_light_shadow": "soft_temp_context_light_shadow",
        "soft_temp_context_medium_shadow": "soft_temp_context_medium_shadow",
    }
    ask = pd.to_numeric(candidates["ask"], errors="coerce")
    out: dict[str, dict[str, Any]] = {}
    base_notional = pd.to_numeric(candidates["base_notional_usd"], errors="coerce").fillna(0)
    for name, weight_col in policies.items():
        if weight_col not in candidates:
            continue
        weight = pd.to_numeric(candidates[weight_col], errors="coerce").fillna(0).clip(lower=0)
        notional = base_notional * weight
        shares = notional / ask
        executable = (
            ask.ge(research.ASK_MIN)
            & candidates["route_price_ok"].fillna(False).astype(bool)
            & shares.ge(float(min_order_shares))
            & pd.to_numeric(candidates["ask_size"], errors="coerce").ge(shares)
            & candidates["token_id"].astype(str).ne("")
            & candidates["live_feature_parity_ok"].astype(bool)
            & candidates["current_no_peak_clock_ok"].astype(bool)
            & candidates["current_no_escape_ok"].astype(bool)
            & candidates["market_date_match_ok"].fillna(False).astype(bool)
            & ~candidates["live_duplicate_key"].astype(bool)
        )
        out[name] = {
            "weight_col": weight_col,
            "avg_weight": round(float(weight.mean()), 6) if len(weight) else 0.0,
            "shadow_notional_usd": round(float(notional.sum()), 6),
            "shadow_executable_rows": int(executable.sum()),
            "shadow_executable_notional_usd": round(float(notional[executable].sum()), 6),
        }
    return out


def main() -> int:
    args = parse_args()
    candidates, meta = build_candidates(args)
    candidate_rows, blocked_candidate_rows = write_candidate_audit(candidates, meta)
    plans = write_plans(candidates, args)
    executor_result = run_executor(args)
    summary = {
        "generated_at_utc": utc_now_iso(),
        "strategy_id": STRATEGY_ID,
        "strategy_instance": STRATEGY_INSTANCE,
        "rule_id": RULE_ID,
        "live_requested": bool(args.live),
        "live_enabled": bool(args.live and args.confirm_live),
        "no_order_placed": not bool(args.live and args.confirm_live and plans),
        "base_notional": float(args.base_notional),
        "daily_gross_cap": float(args.daily_gross_cap),
        "min_order_shares": float(args.min_order_shares),
        "routed_candidates": int(len(candidates)),
        "execution_eligible": int(candidates["execution_eligible"].sum()) if not candidates.empty else 0,
        "shadow_policy_counts": shadow_policy_counts(candidates, min_order_shares=float(args.min_order_shares)),
        "plans_written": len(plans),
        "candidate_rows": candidate_rows,
        "blocked_candidate_rows": blocked_candidate_rows,
        "candidate_by_regime": candidates["day_regime"].value_counts(dropna=False).to_dict() if not candidates.empty else {},
        "skip_reasons": candidates["execution_skip_reason"].value_counts(dropna=False).to_dict() if not candidates.empty else {},
        "plans_path": str(PLAN_OUT.relative_to(ROOT)),
        "paper_out": str(PAPER_OUT.relative_to(ROOT)),
        "live_out": str(LIVE_OUT.relative_to(ROOT)),
        "blocked_out": str(BLOCKED_OUT.relative_to(ROOT)),
        "latest_candidates_out": str(LATEST_CANDIDATES_OUT.relative_to(ROOT)),
        "meta": meta,
        "executor_result": executor_result,
    }
    write_json(SUMMARY_OUT, summary)
    append_jsonl(HISTORY_OUT, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
