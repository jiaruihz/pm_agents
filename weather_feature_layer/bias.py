"""Shared point-in-time forecast/source bias features."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


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


def _quantile(sorted_values: list[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] * (hi - pos) + sorted_values[hi] * (pos - lo)


def asof_error_bias_features(
    bias_index: Mapping[tuple[str, str], list[tuple[str, float]]],
    *,
    city: str,
    forecast_model: str,
    target_date: str,
) -> dict[str, Any]:
    rows = bias_index.get((city, forecast_model), [])
    vals = [err for date, err in rows if date < target_date]
    out: dict[str, Any] = {
        "bias_n_asof": len(vals),
        "bias_mean_asof": None,
        "bias_p50_asof": None,
        "bias_p90_asof": None,
        "hot_tail_pct_asof": None,
        "hot_tail2_pct_asof": None,
        "cold_tail_pct_asof": None,
        "bias_mae_asof": None,
    }
    if not vals:
        return out
    vals_sorted = sorted(vals)
    out.update(
        {
            "bias_mean_asof": sum(vals) / len(vals),
            "bias_p50_asof": _quantile(vals_sorted, 0.50),
            "bias_p90_asof": _quantile(vals_sorted, 0.90),
            "hot_tail_pct_asof": sum(1 for x in vals if x >= 1.0) / len(vals),
            "hot_tail2_pct_asof": sum(1 for x in vals if x >= 2.0) / len(vals),
            "cold_tail_pct_asof": sum(1 for x in vals if x <= -1.0) / len(vals),
            "bias_mae_asof": sum(abs(x) for x in vals) / len(vals),
        }
    )
    return {k: (round(v, 6) if isinstance(v, float) and math.isfinite(v) else v) for k, v in out.items()}


def asof_bias_features(resources: Any, *, city: str, forecast_model: str, target_date: str) -> dict[str, Any]:
    return asof_error_bias_features(resources.bias_index, city=city, forecast_model=forecast_model, target_date=target_date)


def classify_city_source_bias(row: Mapping[str, Any]) -> str:
    bias = to_float(row.get("bias"))
    p90 = to_float(row.get("p90"))
    p10 = to_float(row.get("p10"))
    hot = to_float(row.get("pct_actual_ge_forecast_plus_1"))
    cold = to_float(row.get("pct_forecast_ge_actual_plus_1"))
    mae = to_float(row.get("mae"))
    if bias >= 0.7 and hot >= 0.40 and cold <= 0.15:
        return "hot_underforecast_clean"
    if bias >= 0.5 and p90 >= 2.0 and hot >= 0.35:
        return "hot_underforecast_noisy"
    if bias <= -0.5 and cold >= 0.35 and hot <= 0.20:
        return "cold_overforecast_clean"
    if cold >= 0.25 and p10 <= -1.5:
        return "cold_overforecast_noisy"
    if mae <= 1.0 and hot < 0.25 and cold < 0.25 and abs(bias) < 0.35:
        return "balanced_tight"
    if hot >= 0.25 and cold >= 0.20:
        return "two_sided_noisy"
    return "mild_or_mixed"


def load_city_source_bias_lookup(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if not path.exists():
        return {}
    hist = pd.read_csv(path)
    if hist.empty:
        return {}
    hist["city_source_bias_regime"] = hist.apply(classify_city_source_bias, axis=1)
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for _, row in hist.iterrows():
        city = str(row.get("city") or "")
        model = str(row.get("model") or "")
        if not city or not model:
            continue
        lookup[(city, model)] = {
            "city_source_bias_regime": str(row.get("city_source_bias_regime") or "unclassified"),
            "city_source_bias_n": int(to_float(row.get("n"), 0.0)),
            "city_source_bias": to_float(row.get("bias")),
            "city_source_bias_mae": to_float(row.get("mae")),
            "city_source_bias_p90": to_float(row.get("p90")),
            "city_source_hot_underforecast_rate": to_float(row.get("pct_actual_ge_forecast_plus_1")),
            "city_source_cold_overforecast_rate": to_float(row.get("pct_forecast_ge_actual_plus_1")),
        }
    return lookup
