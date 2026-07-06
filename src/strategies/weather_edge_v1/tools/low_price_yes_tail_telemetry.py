"""Low-price YES tail telemetry helpers.

These fields are diagnostic only.  They must not be used as live selectors
unless a later research report explicitly promotes them.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from weather_feature_layer.bias import asof_bias_features as shared_asof_bias_features
from weather_feature_layer.market import (
    bracket_distance_features as shared_bracket_distance_features,
    parse_bracket_bounds as shared_parse_bracket_bounds,
)

ROOT = Path(__file__).resolve().parents[4]
MODEL_DEFAULT = ROOT / "src/strategies/weather_edge_v1/config/low_price_yes_tail_telemetry_model_v1.json"
BIAS_ROWS_DEFAULT = ROOT / "docs/analysis/2026-06/generated/historical_forecast_station_bias_v1/daily_error_rows.csv"


def safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def to_float(value: Any, default: float = math.nan) -> float:
    try:
        if value is None:
            return default
        out = float(value)
        if not math.isfinite(out):
            return default
        return out
    except Exception:
        return default


def logistic(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def forecast_model_from_source(source: Any, peak_source: Any = "") -> str:
    text = f"{safe_str(source)} {safe_str(peak_source)}".lower()
    if "ecmwf" in text:
        return "ecmwf"
    if "gfs" in text:
        return "gfs"
    return "other"


def parse_bracket_bounds(bracket: Any) -> tuple[float | None, float | None]:
    return shared_parse_bracket_bounds(bracket)


def bracket_distance_features(row: dict[str, Any]) -> dict[str, Any]:
    return shared_bracket_distance_features(row)


def decision_local_bucket(row: dict[str, Any]) -> dict[str, Any]:
    text = safe_str(row.get("decision_snapshot_ts_utc"))
    tz_name = safe_str(row.get("forecast_timezone"))
    if not text:
        return {"decision_hour_local_pit": None, "decision_local_bucket": "unknown"}
    try:
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        local = dt.astimezone(ZoneInfo(tz_name)) if tz_name else dt
    except Exception:
        return {"decision_hour_local_pit": None, "decision_local_bucket": "unknown"}
    hour = int(local.hour)
    if 0 <= hour < 6:
        bucket = "overnight_00_06"
    elif 6 <= hour < 12:
        bucket = "morning_06_12"
    elif 12 <= hour < 18:
        bucket = "afternoon_12_18"
    else:
        bucket = "evening_18_24"
    return {"decision_hour_local_pit": hour, "decision_local_bucket": bucket}


@dataclass
class TailTelemetryResources:
    model: dict[str, Any]
    bias_index: dict[tuple[str, str], list[tuple[str, float]]]
    model_path: str
    bias_path: str
    load_error: str = ""


def load_tail_telemetry_resources(
    *,
    model_path: Path = MODEL_DEFAULT,
    bias_rows_path: Path = BIAS_ROWS_DEFAULT,
) -> TailTelemetryResources:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    bias_index: dict[tuple[str, str], list[tuple[str, float]]] = {}
    with bias_rows_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            city = safe_str(row.get("city"))
            model_name = safe_str(row.get("model"))
            date = safe_str(row.get("date"))
            err = to_float(row.get("error_f_actual_minus_forecast"))
            if not city or not model_name or not date or not math.isfinite(err):
                continue
            bias_index.setdefault((city, model_name), []).append((date, err))
    for key in list(bias_index):
        bias_index[key].sort(key=lambda x: x[0])
    return TailTelemetryResources(
        model=model,
        bias_index=bias_index,
        model_path=str(model_path.relative_to(ROOT)),
        bias_path=str(bias_rows_path.relative_to(ROOT)),
    )


def load_tail_telemetry_resources_soft() -> TailTelemetryResources:
    try:
        return load_tail_telemetry_resources()
    except Exception as exc:  # noqa: BLE001
        return TailTelemetryResources(model={}, bias_index={}, model_path=str(MODEL_DEFAULT), bias_path=str(BIAS_ROWS_DEFAULT), load_error=f"{type(exc).__name__}: {exc}")


def asof_bias_features(resources: TailTelemetryResources, *, city: str, forecast_model: str, target_date: str) -> dict[str, Any]:
    return shared_asof_bias_features(resources, city=city, forecast_model=forecast_model, target_date=target_date)


def score_model(model_spec: dict[str, Any], features: dict[str, Any]) -> float | None:
    try:
        x = float(model_spec["intercept"])
        coeffs = list(model_spec["coefficients"])
        names = list(model_spec["feature_names"])
        values: list[float] = []
        numeric_stats = model_spec["encoder"]["numeric"]
        for col in model_spec["numeric_features"]:
            stats = numeric_stats[col]
            raw = to_float(features.get(col), float(stats["median"]))
            if not math.isfinite(raw):
                raw = float(stats["median"])
            values.append((raw - float(stats["mean"])) / float(stats["scale"]))
        cat_stats = model_spec["encoder"]["categorical"]
        for col in model_spec["cat_features"]:
            spec = cat_stats[col]
            cats = list(spec["categories"]) + [spec["other_token"]]
            raw = safe_str(features.get(col)) or spec["missing_token"]
            value = raw if raw in spec["categories"] else spec["other_token"]
            values.extend(1.0 if value == cat else 0.0 for cat in cats)
        if len(values) != len(coeffs) or len(names) != len(coeffs):
            return None
        x += sum(c * v for c, v in zip(coeffs, values, strict=True))
        return round(logistic(x), 6)
    except Exception:
        return None


def source_aware_v3_tag(forecast_model: str, ask: float) -> dict[str, Any]:
    if forecast_model == "gfs" and 0.05 <= ask <= 0.15:
        return {"source_aware_v3": True, "source_aware_v3_reason": "gfs_05_15"}
    if forecast_model == "ecmwf" and 0.10 <= ask <= 0.20:
        return {"source_aware_v3": True, "source_aware_v3_reason": "ecmwf_10_20"}
    return {"source_aware_v3": False, "source_aware_v3_reason": ""}


def build_low_price_yes_tail_telemetry(row: dict[str, Any], resources: TailTelemetryResources | None) -> dict[str, Any]:
    if resources is None:
        resources = load_tail_telemetry_resources_soft()
    if resources.load_error:
        return {
            "tail_telemetry_version": "low_price_yes_tail_telemetry_v1",
            "tail_telemetry_status": "error",
            "tail_telemetry_error": resources.load_error,
        }

    forecast_model = forecast_model_from_source(row.get("forecast_source"), row.get("forecast_peak_source"))
    ask = to_float(row.get("decision_entry_price"), to_float(row.get("snapshot_ask"), to_float(row.get("ask"))))
    target_date = safe_str(row.get("event_date")) or safe_str(row.get("target_date"))
    city = safe_str(row.get("city"))
    bias = asof_bias_features(resources, city=city, forecast_model=forecast_model, target_date=target_date)
    bracket = bracket_distance_features(row)
    local = decision_local_bucket(row)
    features = {
        "ask": ask,
        "model_p_yes": to_float(row.get("model_p_yes")),
        "edge": to_float(row.get("edge")),
        "logit_model_p": math.log(max(0.001, min(0.999, to_float(row.get("model_p_yes"), 0.001))) / (1 - max(0.001, min(0.999, to_float(row.get("model_p_yes"), 0.001))))),
        "logit_ask": math.log(max(0.001, min(0.999, ask)) / (1 - max(0.001, min(0.999, ask)))),
        "bias_n": bias["bias_n_asof"],
        "bias_mean": bias["bias_mean_asof"],
        "bias_p50": bias["bias_p50_asof"],
        "bias_p90": bias["bias_p90_asof"],
        "hot_tail_pct": bias["hot_tail_pct_asof"],
        "hot_tail2_pct": bias["hot_tail2_pct_asof"],
        "cold_tail_pct": bias["cold_tail_pct_asof"],
        "bias_mae": bias["bias_mae_asof"],
        "decision_hour_utc": None,
        "forecast_model": forecast_model,
        "city": city,
    }
    dt = safe_str(row.get("decision_snapshot_ts_utc"))
    if dt:
        try:
            text = f"{dt[:-1]}+00:00" if dt.endswith("Z") else dt
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            features["decision_hour_utc"] = parsed.astimezone(timezone.utc).hour
        except Exception:
            features["decision_hour_utc"] = None

    p_no_city = score_model(resources.model["models"]["v1_edge20_no_city"], features)
    p_city = score_model(resources.model["models"]["v1_edge20_city_diag"], features)
    out: dict[str, Any] = {
        "tail_telemetry_version": "low_price_yes_tail_telemetry_v1",
        "tail_telemetry_status": "ok",
        "tail_telemetry_model_artifact": resources.model_path,
        "tail_telemetry_bias_source": resources.bias_path,
        "forecast_model_tail": forecast_model,
        **bias,
        **bracket,
        **local,
        **source_aware_v3_tag(forecast_model, ask),
        "p_cal_no_city": p_no_city,
        "p_cal_no_city_ev": round(p_no_city / ask - 1.0, 6) if p_no_city is not None and ask > 0 else None,
        "p_cal_no_city_edge": round(p_no_city - ask, 6) if p_no_city is not None and ask > 0 else None,
        "p_cal_city_diag": p_city,
        "p_cal_city_diag_ev": round(p_city / ask - 1.0, 6) if p_city is not None and ask > 0 else None,
        "p_cal_city_diag_edge": round(p_city - ask, 6) if p_city is not None and ask > 0 else None,
        "tail_telemetry_selector_policy": "diagnostic_only_not_live_selector",
    }
    return out
