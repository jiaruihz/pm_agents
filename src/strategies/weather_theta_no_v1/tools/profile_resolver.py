from __future__ import annotations

import argparse
import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
CONFIG_ROOT = ROOT / "config"


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = _safe_yaml_load(raw)
    return data if isinstance(data, dict) else {}


def _safe_yaml_load(raw: str) -> Any:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(raw)
    except Exception:
        code = (
            "import json, sys, yaml; "
            "print(json.dumps(yaml.safe_load(sys.stdin.read()), ensure_ascii=False))"
        )
        out = subprocess.check_output(["python3", "-c", code], input=raw, text=True)
        return json.loads(out)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_date(value: str) -> date:
    return date.fromisoformat(value)


def _listify(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


@dataclass
class ProfileBundle:
    city_key: str
    station: Dict[str, Any]
    risk: Dict[str, Any]
    trading: Dict[str, Any]
    season: str


def infer_season(city_key: str, local_date: str, hemisphere: str = "north") -> str:
    dt = _safe_date(local_date)
    month = dt.month
    key = city_key.strip().lower()
    if key == "lucknow" and month in {6, 7, 8, 9}:
        return "monsoon"
    if hemisphere.lower() == "south":
        if month in {12, 1, 2}:
            return "summer"
        if month in {3, 4, 5}:
            return "autumn"
        if month in {6, 7, 8}:
            return "winter"
        return "spring"
    if month in {12, 1, 2}:
        return "winter"
    if month in {3, 4, 5}:
        return "spring"
    if month in {6, 7, 8}:
        return "summer"
    return "autumn"


def load_profiles(city_key: str, local_date: str) -> ProfileBundle:
    station_root = _load_yaml(CONFIG_ROOT / "station_profile.yml")
    risk_root = _load_yaml(CONFIG_ROOT / "risk_profile.yml")
    trading_root = _load_yaml(CONFIG_ROOT / "trading_profile.yml")

    key = city_key.strip().lower()
    station = _deep_merge(
        station_root.get("global_defaults", {}) if isinstance(station_root.get("global_defaults"), dict) else {},
        (station_root.get("profiles", {}) or {}).get(key, {}),
    )
    risk = _deep_merge(
        risk_root.get("global_defaults", {}) if isinstance(risk_root.get("global_defaults"), dict) else {},
        (risk_root.get("profiles", {}) or {}).get(key, {}),
    )
    trading = _deep_merge(
        trading_root.get("global_defaults", {}) if isinstance(trading_root.get("global_defaults"), dict) else {},
        (trading_root.get("profiles", {}) or {}).get(key, {}),
    )
    season = infer_season(key, local_date, str(station.get("hemisphere") or "north"))
    regime_overrides = trading.get("regime_overrides", {})
    if isinstance(regime_overrides, dict) and isinstance(regime_overrides.get(season), dict):
        trading = _deep_merge(trading, regime_overrides[season])
    return ProfileBundle(city_key=key, station=station, risk=risk, trading=trading, season=season)


def _normalized_flags(*payloads: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("risk_flags", "flags", "conditions"):
            out.extend(x.lower() for x in _listify(payload.get(key)))
    return out


def _extract_forecast_temp(payload: Dict[str, Any], unit: str) -> Optional[float]:
    if not isinstance(payload, dict):
        return None
    want_f = unit.upper() == "F"
    if want_f:
        for key in ("max_temp_f", "daily_max_temp_f", "forecast_max_temp_f"):
            if key in payload:
                return _safe_float(payload.get(key), 0.0)
    for key in ("max_temp_c", "daily_max_temp_c", "forecast_max_temp_c"):
        if key in payload:
            value_c = _safe_float(payload.get(key), 0.0)
            return (value_c * 9.0 / 5.0) + 32.0 if want_f else value_c
    return None


def resolve_daily_plan(
    *,
    city_key: str,
    local_date: str,
    baseline_forecast: Dict[str, Any],
    latest_forecast: Dict[str, Any],
    latest_observation: Dict[str, Any],
    latest_orderbook: Dict[str, Any],
) -> Dict[str, Any]:
    bundle = load_profiles(city_key=city_key, local_date=local_date)
    trading = bundle.trading
    risk = bundle.risk
    station = bundle.station
    season = bundle.season

    risk_score = _safe_float(risk.get("base_risk_score"), 0.35)
    if season in _listify(risk.get("safer_seasons")):
        risk_score = min(risk_score, _safe_float(risk.get("safer_risk_score"), 0.25))
    if season in _listify(risk.get("dangerous_seasons")):
        risk_score = max(risk_score, _safe_float(risk.get("dangerous_risk_score"), 0.65))

    seen_flags = _normalized_flags(latest_forecast, latest_observation)
    penalty_flags = [x.lower() for x in _listify(trading.get("confidence_penalty_flags"))]
    dangerous_conditions = [x.lower() for x in _listify(risk.get("dangerous_conditions"))]
    matched_penalties = sorted({flag for flag in seen_flags if flag in penalty_flags or flag in dangerous_conditions})
    risk_score += min(0.30, 0.08 * len(matched_penalties))

    precip = latest_forecast.get("precipitation_probability_max")
    precip = None if precip is None else _safe_float(precip, 0.0)
    if precip is not None and precip >= 60:
        risk_score += 0.12

    latest_spread = max(
        0.0,
        _safe_float(latest_orderbook.get("best_ask"), 0.0) - _safe_float(latest_orderbook.get("best_bid"), 0.0),
    )
    if latest_spread >= 0.04:
        risk_score += 0.05

    market_unit = str(station.get("market_unit") or "C")
    baseline_temp = _extract_forecast_temp(baseline_forecast, market_unit)
    latest_temp = _extract_forecast_temp(latest_forecast, market_unit)
    forecast_shift = None
    if baseline_temp is not None and latest_temp is not None:
        forecast_shift = latest_temp - baseline_temp
        if abs(forecast_shift) >= 2.0:
            risk_score += 0.08

    main_range_shift_bins = abs(int(_safe_float(latest_forecast.get("main_range_shift_bins"), 0.0)))
    peak_hour_shift_minutes = abs(int(_safe_float(latest_forecast.get("peak_hour_shift_minutes"), 0.0)))
    edge_to_bucket = latest_forecast.get("edge_to_bucket")
    edge_to_bucket = None if edge_to_bucket is None else _safe_float(edge_to_bucket, 0.0)
    tail_distance_bins = latest_forecast.get("tail_distance_bins")
    tail_distance_bins = None if tail_distance_bins is None else int(_safe_float(tail_distance_bins, 0.0))

    position_multiplier = _safe_float(trading.get("base_position_multiplier"), 1.0)
    if risk_score >= 0.80:
        position_multiplier *= 0.25
    elif risk_score >= 0.65:
        position_multiplier *= 0.50
    elif risk_score >= 0.50:
        position_multiplier *= 0.75

    min_tail_distance_bins = int(_safe_float(trading.get("min_tail_distance_bins"), 2))
    if risk_score >= 0.65:
        min_tail_distance_bins += 1

    daily_overrides = {
        "city_key": bundle.city_key,
        "local_date": local_date,
        "season": season,
        "station_name": station.get("station_name", ""),
        "timezone": station.get("timezone", ""),
        "market_unit": market_unit,
        "base_risk_score": round(min(risk_score, 1.0), 3),
        "position_multiplier": round(position_multiplier, 3),
        "min_tail_distance_bins": min_tail_distance_bins,
        "allow_new_position_until_local": trading.get("allow_new_position_until_local") or trading.get("default_no_entry_after_local"),
        "accelerate_exit_after_local": trading.get("accelerate_exit_after_local", ""),
        "hard_flat_local": trading.get("hard_flat_local") or trading.get("hard_flat_by_local"),
        "matched_risk_flags": matched_penalties,
        "preferred_trade_types": _listify(trading.get("preferred_trade_types")),
        "avoid_trade_types": _listify(trading.get("avoid_trade_types")),
        "review_schedule": deepcopy(trading.get("review_schedule", {})),
    }

    reasons: List[str] = []
    action = "review_only"

    if risk_score >= 0.80:
        action = "avoid_new_entry"
        reasons.append("base risk too high for clean tail-NO entry")
    elif edge_to_bucket is not None and edge_to_bucket <= 1.0:
        action = "tighten_exit_or_avoid_new_entry"
        reasons.append("forecast edge to bucket compressed to 1.0 or less")
    elif tail_distance_bins is not None and tail_distance_bins < min_tail_distance_bins:
        action = "avoid_new_entry"
        reasons.append("tail distance is below city/risk-adjusted minimum")
    elif main_range_shift_bins >= 1 or peak_hour_shift_minutes >= 90:
        action = "size_down_and_review"
        reasons.append("forecast structure moved materially versus baseline")
    elif latest_spread >= 0.04:
        action = "wait_for_tighter_book"
        reasons.append("orderbook spread is too wide for clean maker entry")
    elif risk_score >= 0.65:
        action = "size_down"
        reasons.append("risk regime is elevated")
    else:
        action = "maker_entry_ok"
        reasons.append("risk regime and market structure are acceptable for maker-only tail-NO")

    if forecast_shift is not None:
        reasons.append(f"forecast shift vs baseline: {forecast_shift:+.1f}{market_unit}")
    if edge_to_bucket is not None:
        reasons.append(f"forecast edge to bucket: {edge_to_bucket:.1f}{market_unit}")
    if tail_distance_bins is not None:
        reasons.append(f"tail distance bins: {tail_distance_bins}")

    return {
        "city_key": bundle.city_key,
        "local_date": local_date,
        "station_profile": station,
        "risk_profile": risk,
        "trading_profile": trading,
        "daily_overrides": daily_overrides,
        "action_suggestion": {
            "action": action,
            "reason": "; ".join(reasons),
            "confidence": "high" if risk_score < 0.50 else "medium" if risk_score < 0.70 else "low",
        },
    }


def _load_json_arg(raw: str) -> Dict[str, Any]:
    text = raw.strip()
    if not text:
        return {}
    path = Path(text)
    if path.exists() and path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve city/day weather trading overrides from station/risk/trading profiles.")
    parser.add_argument("--city-key", required=True)
    parser.add_argument("--local-date", required=True, help="City-local date in YYYY-MM-DD format")
    parser.add_argument("--baseline-forecast", default="{}")
    parser.add_argument("--latest-forecast", default="{}")
    parser.add_argument("--latest-observation", default="{}")
    parser.add_argument("--latest-orderbook", default="{}")
    args = parser.parse_args()

    payload = resolve_daily_plan(
        city_key=args.city_key,
        local_date=args.local_date,
        baseline_forecast=_load_json_arg(args.baseline_forecast),
        latest_forecast=_load_json_arg(args.latest_forecast),
        latest_observation=_load_json_arg(args.latest_observation),
        latest_orderbook=_load_json_arg(args.latest_orderbook),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
