#!/usr/bin/env python3
"""Zero-notional integrated shadow for the low-price YES tail research.

This runner records the V2 research fields over the current V1 low-price YES
candidate stream.  It never submits orders and does not alter the tiny-live V1
selector.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.strategies.weather_edge_v1.tools.low_price_yes_tail_telemetry import (
    TailTelemetryResources,
    build_low_price_yes_tail_telemetry,
    load_tail_telemetry_resources_soft,
)
from weather_data_feed.observation_cache import index_observation_cache, load_observation_cache, parse_utc


DB_DEFAULT = ROOT / "runtime/weather.db"
OBS_DEFAULT = Path.home() / "projects/weather_data_feed_service_runtime/output/observations/latest.json"
RUNTIME_DIR = ROOT / os.environ.get(
    "LOW_PRICE_YES_INTEGRATED_TAIL_SHADOW_RUNTIME_DIR",
    "runtime/weather_edge_v1/low_price_yes_integrated_tail_shadow_v2",
)
JOURNAL_OUT = RUNTIME_DIR / "shadow_decisions.jsonl"
LATEST_OUT = RUNTIME_DIR / "latest_candidates.json"
SUMMARY_OUT = RUNTIME_DIR / "latest_summary.json"
SUMMARY_HISTORY_OUT = RUNTIME_DIR / "summary_history.jsonl"

STRATEGY_ID = "low_price_yes_integrated_tail_shadow_v2"
RULE_ID = "v1_candidate_with_source_station_metar_shadow_tags_v2"
SOURCE_REPORT = "docs/analysis/2026-07/2026-07-02-low-price-yes-integrated-tail-v2.md"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "loop"], nargs="?", default="run")
    parser.add_argument("--db", default=str(DB_DEFAULT))
    parser.add_argument("--observation-cache", default=str(OBS_DEFAULT))
    parser.add_argument("--min-event-date", default=None)
    parser.add_argument("--max-event-date", default=None)
    parser.add_argument("--min-ask", type=float, default=0.05)
    parser.add_argument("--max-ask", type=float, default=0.20)
    parser.add_argument("--min-edge", type=float, default=0.20)
    parser.add_argument("--max-candidates-per-run", type=int, default=80)
    parser.add_argument("--max-obs-age-min", type=float, default=120.0)
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    parser.add_argument("--allow-settled", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def now_utc_dt() -> datetime:
    return datetime.now(timezone.utc)


def now_utc() -> str:
    return now_utc_dt().isoformat(timespec="seconds").replace("+00:00", "Z")


def safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def json_ready(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_ready(v) for v in value]
    return value


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=1000")
    conn.row_factory = sqlite3.Row
    return conn


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_ready(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(json_ready(payload), ensure_ascii=False, sort_keys=True) + "\n")


def effective_min_event_date(conn: sqlite3.Connection, args: argparse.Namespace) -> str | None:
    if args.min_event_date:
        return str(args.min_event_date)
    status_filter = "" if args.allow_settled else "AND COALESCE(settlement_status, '') <> 'settled' AND final_yes IS NULL"
    query = f"""
        SELECT MAX(event_date)
        FROM fact_signal_candidates
        WHERE side = 'BUY_YES'
          AND decision_entry_price BETWEEN :min_ask AND :max_ask
          AND edge >= :min_edge
          {status_filter}
    """
    return conn.execute(
        query,
        {"min_ask": args.min_ask, "max_ask": args.max_ask, "min_edge": args.min_edge},
    ).fetchone()[0]


def count_raw(conn: sqlite3.Connection, args: argparse.Namespace, min_event_date: str | None) -> dict[str, Any]:
    status_filter = "" if args.allow_settled else "AND COALESCE(settlement_status, '') <> 'settled' AND final_yes IS NULL"
    max_filter = "AND event_date <= :max_event_date" if args.max_event_date else ""
    query = f"""
        SELECT
          COUNT(*) AS rows,
          COUNT(DISTINCT event_date) AS dates,
          COUNT(DISTINCT city) AS cities,
          MIN(event_date) AS min_event_date,
          MAX(event_date) AS max_event_date,
          AVG(decision_entry_price) AS avg_ask,
          AVG(edge) AS avg_edge,
          MAX(fact_built_at_utc) AS fact_built_at_utc
        FROM fact_signal_candidates
        WHERE side = 'BUY_YES'
          AND decision_entry_price BETWEEN :min_ask AND :max_ask
          AND edge >= :min_edge
          AND (:min_event_date IS NULL OR event_date >= :min_event_date)
          {max_filter}
          {status_filter}
    """
    return dict(
        conn.execute(
            query,
            {
                "min_ask": args.min_ask,
                "max_ask": args.max_ask,
                "min_edge": args.min_edge,
                "min_event_date": min_event_date,
                "max_event_date": args.max_event_date,
            },
        ).fetchone()
    )


def load_candidates(conn: sqlite3.Connection, args: argparse.Namespace, min_event_date: str | None) -> list[sqlite3.Row]:
    status_filter = "" if args.allow_settled else "AND COALESCE(settlement_status, '') <> 'settled' AND final_yes IS NULL"
    max_filter = "AND event_date <= :max_event_date" if args.max_event_date else ""
    query = f"""
        WITH base AS (
          SELECT
            *,
            ROW_NUMBER() OVER (
              PARTITION BY event_date, city
              ORDER BY decision_snapshot_ts_utc ASC, decision_entry_price ASC, edge DESC, bracket ASC, candidate_id ASC
            ) AS city_date_rank
          FROM fact_signal_candidates
          WHERE side = 'BUY_YES'
            AND decision_entry_price BETWEEN :min_ask AND :max_ask
            AND edge >= :min_edge
            AND (:min_event_date IS NULL OR event_date >= :min_event_date)
            {max_filter}
            {status_filter}
        )
        SELECT *
        FROM base
        WHERE city_date_rank = 1
        ORDER BY event_date ASC, decision_snapshot_ts_utc ASC, city ASC
        LIMIT :limit
    """
    return list(
        conn.execute(
            query,
            {
                "min_ask": args.min_ask,
                "max_ask": args.max_ask,
                "min_edge": args.min_edge,
                "min_event_date": min_event_date,
                "max_event_date": args.max_event_date,
                "limit": args.max_candidates_per_run,
            },
        )
    )


def load_obs_index(path: Path) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    if not path.exists():
        return {}, {"status": "missing", "path": str(path)}
    try:
        cache = load_observation_cache(path)
    except Exception as exc:  # noqa: BLE001
        return {}, {"status": "error", "path": str(path), "error": f"{type(exc).__name__}: {exc}"}
    return index_observation_cache(cache), {
        "status": "ok",
        "path": str(path),
        "generated_at_utc": safe_str(cache.get("generated_at_utc")),
        "records": len(cache.get("records") or []),
        "summary": cache.get("summary") if isinstance(cache.get("summary"), dict) else {},
    }


def obs_native(record: dict[str, Any], unit: str, key_c: str) -> float:
    val_c = to_float(record.get(key_c))
    if not math.isfinite(val_c):
        return math.nan
    return val_c * 9.0 / 5.0 + 32.0 if unit.upper() == "F" else val_c


def classify_live_metar(row: dict[str, Any], obs: dict[str, Any] | None, args: argparse.Namespace) -> dict[str, Any]:
    if not obs:
        return {"live_obs_status": "missing"}
    if obs.get("status") != "ok":
        return {"live_obs_status": safe_str(obs.get("status")) or "not_ok"}
    now = now_utc_dt()
    last_obs = parse_utc(obs.get("last_obs_utc"))
    age_min = (now - last_obs).total_seconds() / 60.0 if last_obs else math.nan
    if math.isfinite(age_min) and age_min > float(args.max_obs_age_min):
        status = "stale"
    else:
        status = "ok"
    unit = safe_str(row.get("unit")) or safe_str(obs.get("unit"))
    running_native = obs_native(obs, unit, "running_max_c")
    current_native = obs_native(obs, unit, "current_temp_c")
    forecast_max_native = to_float(row.get("forecast_max_native"))
    forecast_gap = forecast_max_native - running_native if math.isfinite(forecast_max_native) and math.isfinite(running_native) else math.nan
    decline_native = running_native - current_native if math.isfinite(running_native) and math.isfinite(current_native) else math.nan
    temp_trend_1h_f = to_float(obs.get("d_tmpf_1h"), to_float(obs.get("temp_trend_1h_f")))
    temp_trend_3h_f = to_float(obs.get("d_tmpf_3h"), to_float(obs.get("temp_trend_3h_f")))
    wind_kt = to_float(obs.get("sknt_now"), to_float(obs.get("wind_speed_kt")))
    dewpoint_depression_f = to_float(obs.get("dewpoint_depression_f"))
    minutes_since_running_max = to_float(obs.get("minutes_since_running_max"))

    if not math.isfinite(forecast_gap):
        day_regime = "live_day_unknown"
    elif forecast_gap >= 1.0:
        day_regime = "live_day_open_runway"
    elif forecast_gap >= 0.25:
        day_regime = "live_day_marginal_runway"
    elif forecast_gap >= -0.5:
        day_regime = "live_day_forecast_capped"
    else:
        day_regime = "live_day_forecast_busted"

    if not math.isfinite(decline_native):
        intraday_state = "live_intraday_unknown"
    elif decline_native <= 0.1 and math.isfinite(temp_trend_1h_f) and temp_trend_1h_f >= 0.0:
        intraday_state = "live_fresh_high_or_warming"
    elif decline_native >= 0.5 and math.isfinite(temp_trend_1h_f) and temp_trend_1h_f >= 0.5:
        intraday_state = "live_false_fade_risk"
    elif decline_native >= 0.5 and math.isfinite(minutes_since_running_max) and minutes_since_running_max >= 90:
        intraday_state = "live_mature_fade"
    elif math.isfinite(temp_trend_1h_f) and temp_trend_1h_f > 0:
        intraday_state = "live_active_warming"
    else:
        intraday_state = "live_stalled_or_unknown"

    if not math.isfinite(wind_kt):
        wind_regime = "live_wind_unknown"
    elif wind_kt <= 8:
        wind_regime = "live_light_wind"
    elif wind_kt <= 15:
        wind_regime = "live_moderate_wind"
    else:
        wind_regime = "live_windy_mixing"

    if not math.isfinite(dewpoint_depression_f):
        moisture_regime = "live_moisture_unknown"
    elif dewpoint_depression_f <= 10:
        moisture_regime = "live_humid"
    elif dewpoint_depression_f >= 25:
        moisture_regime = "live_dry"
    else:
        moisture_regime = "live_mixed_moisture"

    score = 0
    score += int(day_regime in {"live_day_open_runway", "live_day_marginal_runway"})
    score += int(math.isfinite(forecast_gap) and forecast_gap >= 1.0)
    score += int(intraday_state in {"live_false_fade_risk", "live_fresh_high_or_warming", "live_active_warming"})
    score += int(wind_regime == "live_light_wind")
    score += int(day_regime not in {"live_day_forecast_capped", "live_day_forecast_busted"})
    score += int(math.isfinite(temp_trend_3h_f) and temp_trend_3h_f > 0)

    return {
        "live_obs_status": status,
        "live_obs_source": safe_str(obs.get("source")),
        "live_obs_station": safe_str(obs.get("station")),
        "live_obs_last_obs_utc": safe_str(obs.get("last_obs_utc")),
        "live_obs_age_min": round(age_min, 3) if math.isfinite(age_min) else None,
        "live_obs_n": int(to_float(obs.get("n_obs"), 0.0)),
        "live_obs_current_native": round(current_native, 3) if math.isfinite(current_native) else None,
        "live_obs_running_native": round(running_native, 3) if math.isfinite(running_native) else None,
        "live_forecast_gap_to_running_native": round(forecast_gap, 3) if math.isfinite(forecast_gap) else None,
        "live_decline_native": round(decline_native, 3) if math.isfinite(decline_native) else None,
        "live_temp_trend_1h_f": round(temp_trend_1h_f, 3) if math.isfinite(temp_trend_1h_f) else None,
        "live_temp_trend_3h_f": round(temp_trend_3h_f, 3) if math.isfinite(temp_trend_3h_f) else None,
        "live_wind_speed_kt": round(wind_kt, 3) if math.isfinite(wind_kt) else None,
        "live_dewpoint_depression_f": round(dewpoint_depression_f, 3) if math.isfinite(dewpoint_depression_f) else None,
        "live_minutes_since_running_max": round(minutes_since_running_max, 3) if math.isfinite(minutes_since_running_max) else None,
        "live_day_regime": day_regime,
        "live_intraday_state": intraday_state,
        "live_wind_regime": wind_regime,
        "live_moisture_regime": moisture_regime,
        "live_metar_regime_score": score,
    }


PCAL_V2_JSON = ROOT / "docs/analysis/2026-07/2026-07-03-low-price-yes-tail-pcal-v2.json"
BIAS_ROWS_CSV = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv"


def load_pcal_v2_resources() -> dict[str, Any] | None:
    """frozen pcal_v2 selector (research_low_price_yes_tail_pcal_v2.py); soft-fail."""
    try:
        frozen = json.loads(PCAL_V2_JSON.read_text(encoding="utf-8"))["frozen_selector"]
        import csv as _csv

        index: dict[tuple[str, str], list[tuple[str, float]]] = {}
        with BIAS_ROWS_CSV.open(encoding="utf-8") as f:
            for rec in _csv.DictReader(f):
                try:
                    err = float(rec["error_f_actual_minus_forecast"])
                except (TypeError, ValueError):
                    continue
                index.setdefault((rec["city"], rec["model"]), []).append((rec["date"], err))
        for key in index:
            index[key].sort()
        return {"frozen": frozen, "bias_index": index}
    except Exception:
        return None


def pcal_v2_tags(row: dict[str, Any], res: dict[str, Any] | None) -> dict[str, Any]:
    if not res:
        return {"pcal_v2_status": "resources_missing"}
    frozen = res["frozen"]
    source = safe_str(row.get("forecast_source")).lower()
    model_name = "ecmwf" if "ecmwf" in source else ("gfs" if "gfs" in source else "other")
    errors = res["bias_index"].get((safe_str(row.get("city")), model_name)) or res["bias_index"].get((safe_str(row.get("city")), "gfs"))
    target = safe_str(row.get("event_date"))
    xs = [e for d, e in errors if d < target] if errors else []
    if not xs:
        return {"pcal_v2_status": "no_bias_history"}
    xs_sorted = sorted(xs)
    p90 = xs_sorted[min(len(xs_sorted) - 1, int(round(0.90 * (len(xs_sorted) - 1))))]
    model_p = to_float(row.get("model_p_yes"))
    ask = to_float(row.get("decision_entry_price"))
    if not (math.isfinite(model_p) and math.isfinite(ask)):
        return {"pcal_v2_status": "missing_inputs"}
    clip = lambda p: min(max(p, 0.001), 0.999)
    feats = {
        "logit_model_p": math.log(clip(model_p) / (1 - clip(model_p))),
        "logit_ask": math.log(clip(ask) / (1 - clip(ask))),
        "bias_mean": sum(xs) / len(xs),
        "bias_p90": p90,
        "hot_tail_pct": sum(1 for e in xs if e >= 1.0) / len(xs),
        "cold_tail_pct": sum(1 for e in xs if e <= -1.0) / len(xs),
    }
    ts = safe_str(row.get("decision_snapshot_ts_utc"))
    hour = int(ts[11:13]) if len(ts) >= 13 and ts[11:13].isdigit() else -1
    bucket = "h00_05" if 0 <= hour <= 5 else "h06_11" if hour <= 11 else "h12_17" if hour <= 17 else "h18_23" if hour <= 23 else "nan"
    z = frozen["intercept"]
    for name, mu, sd, coef in zip(frozen["num_features"], frozen["scaler_mu"], frozen["scaler_sd"], frozen["coef"]):
        z += coef * (feats[name] - mu) / sd
    for cat_col, coef in zip(frozen["cat_columns"], frozen["coef"][len(frozen["num_features"]):]):
        active = cat_col == f"forecast_model_{model_name}" or cat_col == f"dec_hour_bucket_{bucket}"
        z += coef * float(active)
    p_cal = 1.0 / (1.0 + math.exp(-z))
    theta = float(frozen["theta"])
    return {
        "pcal_v2_status": "ok",
        "pcal_v2_p": round(p_cal, 4),
        "pcal_v2_ev": round(p_cal - ask, 4),
        "pcal_v2_theta": theta,
        "pcal_v2_selected_shadow": bool(p_cal - ask >= theta),
        "pcal_v2_policy": "frozen_selector_shadow_only_acceptance_failed_vs_v1",
    }


def source_aware_v3(row: dict[str, Any]) -> tuple[bool, str]:
    source = safe_str(row.get("forecast_source")).lower()
    ask = to_float(row.get("decision_entry_price"), to_float(row.get("ask")))
    is_gfs = "gfs" in source
    is_ecmwf = "ecmwf" in source
    if is_gfs and 0.05 <= ask <= 0.15:
        return True, "gfs_ask_05_15"
    if is_ecmwf and 0.10 <= ask <= 0.20:
        return True, "ecmwf_ask_10_20"
    return False, "outside_source_price_band"


def decision_id(row: sqlite3.Row, cycle_id: str) -> str:
    raw = "|".join(
        [
            STRATEGY_ID,
            cycle_id,
            safe_str(row["event_date"]),
            safe_str(row["city"]),
            safe_str(row["decision_snapshot_ts_utc"]),
            safe_str(row["bracket"]),
            safe_str(row["candidate_id"]),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def bracket_low_native(bracket: str) -> float:
    text = safe_str(bracket)
    if not text:
        return math.nan
    if text.endswith("+"):
        text = text[:-1]
    if "-" in text:
        text = text.split("-", 1)[0]
    return to_float(text)


def hot_tail_boundary_tags(row: dict[str, Any]) -> dict[str, Any]:
    """E1 pre-registered telemetry (2026-07-04 audit doc).

    bracket_dist_br_v1: 票的 bracket 下沿高于决策时 forecast_max 几个 bracket 宽度。
    hot_tail_boundary_v1: dist>0 才是 hot-tail 表达；dist<=0 是"预报向下 bust"票（train 上 -10.6%）。
    book_state_v1: 决策时 book 状态；train 上 feasible 行 ROI≈0，边际集中在 missing/thin_wide，
    forward 需要用这个字段裁决"stale-quote 假边际 vs 无人照看的真错价"。
    """
    unit = safe_str(row.get("unit")).upper()
    is_f = unit.startswith("F")
    low_native = bracket_low_native(row.get("bracket"))
    low_f = low_native if is_f else (low_native * 9.0 / 5.0 + 32.0 if math.isfinite(low_native) else math.nan)
    width_f = 2.0 if is_f else 1.8
    fmax_f = to_float(row.get("forecast_max_f"))
    dist_br = (low_f - fmax_f) / width_f if math.isfinite(low_f) and math.isfinite(fmax_f) else math.nan
    spread = to_float(row.get("yes_spread"))
    depth = to_float(row.get("yes_depth_ask_5c"))
    if not math.isfinite(spread) or not math.isfinite(depth):
        book_state = "missing"
    elif spread <= 0.03 and depth >= 25.0:
        book_state = "feasible"
    else:
        book_state = "thin_wide"
    return {
        "bracket_low_native": low_native if math.isfinite(low_native) else None,
        "bracket_low_f": round(low_f, 2) if math.isfinite(low_f) else None,
        "bracket_dist_br_v1": round(dist_br, 3) if math.isfinite(dist_br) else None,
        "hot_tail_boundary_v1": (dist_br > 0.0) if math.isfinite(dist_br) else None,
        "dec_yes_spread": spread if math.isfinite(spread) else None,
        "dec_yes_depth_ask_5c": depth if math.isfinite(depth) else None,
        "book_state_v1": book_state,
    }


def build_shadow_row(
    row: sqlite3.Row,
    *,
    cycle_id: str,
    args: argparse.Namespace,
    tail_resources: TailTelemetryResources | None,
    obs_index: dict[tuple[str, str], dict[str, Any]],
    obs_meta: dict[str, Any],
    pcal_v2_resources: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row_dict = dict(row)
    telemetry = build_low_price_yes_tail_telemetry(row_dict, tail_resources)
    pcal_v2 = pcal_v2_tags(row_dict, pcal_v2_resources)
    obs = obs_index.get((safe_str(row["city"]), safe_str(row["event_date"])))
    live_metar = classify_live_metar(row_dict, obs, args)
    source_ok, source_reason = source_aware_v3(row_dict)
    station_bias_p90_high = to_float(telemetry.get("bias_p90_asof")) >= 3.9
    station_hot_tail_high = to_float(telemetry.get("hot_tail_pct_asof")) >= 0.621
    pcal_no_city_ev = to_float(telemetry.get("p_cal_no_city_ev"))
    pcal_city_diag_ev = to_float(telemetry.get("p_cal_city_diag_ev"))
    live_score = int(to_float(live_metar.get("live_metar_regime_score"), 0.0))
    integrated_score = (
        int(source_ok)
        + int(station_bias_p90_high)
        + int(station_hot_tail_high)
        + int(math.isfinite(pcal_no_city_ev) and pcal_no_city_ev >= 0.2)
        + int(math.isfinite(pcal_city_diag_ev) and pcal_city_diag_ev >= 0.5)
        + int(live_score >= 4)
    )
    return {
        "record_type": "low_price_yes_integrated_tail_shadow_v2_decision",
        "journal_schema_version": 1,
        "created_at_utc": now_utc(),
        "cycle_id": cycle_id,
        "strategy_id": STRATEGY_ID,
        "strategy_family": "forecast_quality.low_price_yes_integrated_tail",
        "rule_id": RULE_ID,
        "shadow_decision_id": decision_id(row, cycle_id),
        "execution_mode": "zero_notional_shadow",
        "no_order_placed": True,
        "shadow_notional_usd": 0.0,
        "source_report": SOURCE_REPORT,
        "city": row["city"],
        "target_date": row["event_date"],
        "event_date": row["event_date"],
        "bracket": row["bracket"],
        "side": row["side"],
        "candidate_id": row["candidate_id"],
        "condition_id": row["condition_id"],
        "market_id": row["market_id"],
        "unit": row["unit"],
        "decision_snapshot_ts_utc": row["decision_snapshot_ts_utc"],
        "first_seen_ts_utc": row["first_seen_ts_utc"],
        "last_seen_ts_utc": row["last_seen_ts_utc"],
        "decision_hours_to_settle": row["decision_hours_to_settle"],
        "ask": row["decision_entry_price"],
        "decision_entry_price": row["decision_entry_price"],
        "model_p_yes": row["model_p_yes"],
        "market_yes_price": row["market_yes_price"],
        "edge": row["edge"],
        "forecast_source": row["forecast_source"],
        "forecast_peak_source": row["forecast_peak_source"],
        "forecast_max_native": row["forecast_max_native"],
        "forecast_max_f": row["forecast_max_f"],
        "forecast_peak_hour_local": row["forecast_peak_hour_local"],
        "forecast_peak_delta_hours_local": row["forecast_peak_delta_hours_local"],
        "forecast_max_in_bracket": row["forecast_max_in_bracket"],
        "forecast_max_above_bracket_f": row["forecast_max_above_bracket_f"],
        "forecast_max_below_bracket_f": row["forecast_max_below_bracket_f"],
        **hot_tail_boundary_tags(row_dict),
        "source_aware_v3_shadow": source_ok,
        "source_aware_v3_shadow_reason": source_reason,
        "station_bias_p90_high_shadow": station_bias_p90_high,
        "station_hot_tail_high_shadow": station_hot_tail_high,
        "pcal_no_city_ev_ge_0_2_shadow": math.isfinite(pcal_no_city_ev) and pcal_no_city_ev >= 0.2,
        "pcal_city_diag_ev_ge_0_5_diag_shadow": math.isfinite(pcal_city_diag_ev) and pcal_city_diag_ev >= 0.5,
        "integrated_tail_shadow_score_v2": integrated_score,
        "integrated_tail_shadow_policy": "diagnostic_only_not_live_selector",
        "integrated_tail_shadow_candidate": integrated_score >= 3 or source_ok,
        **telemetry,
        **live_metar,
        **pcal_v2,
        "observation_cache_status": obs_meta.get("status"),
        "observation_cache_path": obs_meta.get("path"),
        "observation_cache_generated_at_utc": obs_meta.get("generated_at_utc"),
        "settlement_status_at_capture": row["settlement_status"],
        "final_yes_at_capture": row["final_yes"],
        "bracket_hit_at_capture": row["bracket_hit"],
        "fact_built_at_utc": row["fact_built_at_utc"],
        "source_db": rel(Path(args.db)),
    }


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    cycle_id = now_utc()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tail_resources = load_tail_telemetry_resources_soft()
    obs_index, obs_meta = load_obs_index(Path(args.observation_cache).expanduser())
    with connect(Path(args.db)) as conn:
        min_event_date = effective_min_event_date(conn, args)
        raw_counts = count_raw(conn, args, min_event_date)
        candidates = load_candidates(conn, args, min_event_date)

    pcal_v2_resources = load_pcal_v2_resources()
    rows = [
        build_shadow_row(
            row,
            cycle_id=cycle_id,
            args=args,
            tail_resources=tail_resources,
            obs_index=obs_index,
            obs_meta=obs_meta,
            pcal_v2_resources=pcal_v2_resources,
        )
        for row in candidates
    ]
    if not args.dry_run:
        for item in rows:
            append_jsonl(JOURNAL_OUT, item)
    latest = {"generated_at_utc": cycle_id, "strategy_id": STRATEGY_ID, "rows": rows}
    summary = {
        "generated_at_utc": cycle_id,
        "strategy_id": STRATEGY_ID,
        "rule_id": RULE_ID,
        "execution_mode": "zero_notional_shadow",
        "dry_run": bool(args.dry_run),
        "effective_min_event_date": min_event_date,
        "effective_max_event_date": args.max_event_date,
        "raw_matching_rows": raw_counts,
        "decision_count": len(rows),
        "shadow_rows_written": 0 if args.dry_run else len(rows),
        "source_aware_v3_count": sum(1 for row in rows if row.get("source_aware_v3_shadow")),
        "pcal_v2_selected_count": sum(1 for row in rows if row.get("pcal_v2_selected_shadow")),
        "integrated_tail_shadow_candidate_count": sum(1 for row in rows if row.get("integrated_tail_shadow_candidate")),
        "tail_telemetry_status_counts": {
            status: sum(1 for row in rows if safe_str(row.get("tail_telemetry_status")) == status)
            for status in sorted({safe_str(row.get("tail_telemetry_status")) for row in rows})
        },
        "live_obs_status_counts": {
            status: sum(1 for row in rows if safe_str(row.get("live_obs_status")) == status)
            for status in sorted({safe_str(row.get("live_obs_status")) for row in rows})
        },
        "live_day_regime_counts": {
            status: sum(1 for row in rows if safe_str(row.get("live_day_regime")) == status)
            for status in sorted({safe_str(row.get("live_day_regime")) for row in rows})
        },
        "observation_cache": obs_meta,
        "files": {
            "journal": rel(JOURNAL_OUT),
            "latest": rel(LATEST_OUT),
            "summary": rel(SUMMARY_OUT),
            "summary_history": rel(SUMMARY_HISTORY_OUT),
        },
        "config": {
            "min_ask": float(args.min_ask),
            "max_ask": float(args.max_ask),
            "min_edge": float(args.min_edge),
            "max_candidates_per_run": int(args.max_candidates_per_run),
            "max_obs_age_min": float(args.max_obs_age_min),
            "allow_settled": bool(args.allow_settled),
        },
    }
    if not args.dry_run:
        write_json(LATEST_OUT, latest)
        write_json(SUMMARY_OUT, summary)
        append_jsonl(SUMMARY_HISTORY_OUT, summary)
    else:
        print(json.dumps(json_ready({"latest": latest, "summary": summary}), ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def main() -> int:
    os.chdir(ROOT)
    args = parse_args()
    if args.command == "run":
        summary = run_once(args)
        if not args.dry_run:
            print(json.dumps(json_ready(summary), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    while True:
        started = now_utc()
        print(f"[low_price_yes_integrated_tail_shadow_v2] cycle_start_utc={started}", flush=True)
        try:
            summary = run_once(args)
            print(
                "[low_price_yes_integrated_tail_shadow_v2] "
                f"decisions={summary.get('decision_count')} "
                f"source_aware={summary.get('source_aware_v3_count')} "
                f"integrated_candidates={summary.get('integrated_tail_shadow_candidate_count')}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[low_price_yes_integrated_tail_shadow_v2] runner_failed {type(exc).__name__}: {exc}", flush=True)
        time.sleep(max(5.0, float(args.interval_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
