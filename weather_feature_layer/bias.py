"""Shared point-in-time forecast/source bias features."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


BIAS_REFERENCE_SCHEMA_VERSION = "bias_reference_v1"
DEFAULT_BIAS_SETTLEMENT_SOURCE = "historical_forecast_station_bias_v1"
DEFAULT_BIAS_SOURCE_POLICY = "weather_data_feed.source_policy"


@dataclass(frozen=True)
class BiasReferenceMetadata:
    schema_version: str
    generated_at_utc: str
    build_window_start: str | None
    build_window_end: str | None
    settlement_source: str
    source_policy: str
    input_path: str
    input_sha256: str
    input_row_count: int
    city_count: int
    model_count: int
    snapshot_id: str


@dataclass(frozen=True)
class CitySourceBiasReference:
    metadata: BiasReferenceMetadata
    lookup: dict[tuple[str, str], dict[str, Any]]


@dataclass(frozen=True)
class ErrorBiasIndexReference:
    metadata: BiasReferenceMetadata
    bias_index: dict[tuple[str, str], list[tuple[str, float]]]


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


def load_city_source_bias_reference(
    path: Path,
    *,
    generated_at_utc: str | None = None,
    settlement_source: str = DEFAULT_BIAS_SETTLEMENT_SOURCE,
    source_policy: str = DEFAULT_BIAS_SOURCE_POLICY,
    snapshot_id: str | None = None,
) -> CitySourceBiasReference:
    if not path.exists():
        return CitySourceBiasReference(
            metadata=_empty_metadata(
                path,
                generated_at_utc=generated_at_utc,
                settlement_source=settlement_source,
                source_policy=source_policy,
                snapshot_id=snapshot_id,
            ),
            lookup={},
        )
    hist = pd.read_csv(path)
    if hist.empty:
        return CitySourceBiasReference(
            metadata=_metadata_from_frame(
                path,
                hist,
                generated_at_utc=generated_at_utc,
                settlement_source=settlement_source,
                source_policy=source_policy,
                snapshot_id=snapshot_id,
            ),
            lookup={},
        )
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
    return CitySourceBiasReference(
        metadata=_metadata_from_frame(
            path,
            hist,
            generated_at_utc=generated_at_utc,
            settlement_source=settlement_source,
            source_policy=source_policy,
            snapshot_id=snapshot_id,
        ),
        lookup=lookup,
    )


def load_city_source_bias_lookup(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    return load_city_source_bias_reference(path).lookup


def load_error_bias_index_reference(
    path: Path,
    *,
    generated_at_utc: str | None = None,
    settlement_source: str = DEFAULT_BIAS_SETTLEMENT_SOURCE,
    source_policy: str = DEFAULT_BIAS_SOURCE_POLICY,
    snapshot_id: str | None = None,
) -> ErrorBiasIndexReference:
    if not path.exists():
        return ErrorBiasIndexReference(
            metadata=_empty_metadata(
                path,
                generated_at_utc=generated_at_utc,
                settlement_source=settlement_source,
                source_policy=source_policy,
                snapshot_id=snapshot_id,
            ),
            bias_index={},
        )
    rows = pd.read_csv(path)
    index: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for _, row in rows.iterrows():
        city = str(row.get("city") or "")
        model = str(row.get("model") or "")
        date = str(row.get("date") or "")
        err = to_float(row.get("error_f_actual_minus_forecast"))
        if not city or not model or not date or not math.isfinite(err):
            continue
        index.setdefault((city, model), []).append((date, err))
    for key in index:
        index[key].sort()
    return ErrorBiasIndexReference(
        metadata=_metadata_from_frame(
            path,
            rows,
            generated_at_utc=generated_at_utc,
            settlement_source=settlement_source,
            source_policy=source_policy,
            snapshot_id=snapshot_id,
        ),
        bias_index=index,
    )


def bias_reference_metadata_dict(metadata: BiasReferenceMetadata) -> dict[str, Any]:
    return {
        "schema_version": metadata.schema_version,
        "generated_at_utc": metadata.generated_at_utc,
        "build_window_start": metadata.build_window_start,
        "build_window_end": metadata.build_window_end,
        "settlement_source": metadata.settlement_source,
        "source_policy": metadata.source_policy,
        "input_path": metadata.input_path,
        "input_sha256": metadata.input_sha256,
        "input_row_count": metadata.input_row_count,
        "city_count": metadata.city_count,
        "model_count": metadata.model_count,
        "snapshot_id": metadata.snapshot_id,
    }


def _metadata_from_frame(
    path: Path,
    frame: pd.DataFrame,
    *,
    generated_at_utc: str | None,
    settlement_source: str,
    source_policy: str,
    snapshot_id: str | None,
) -> BiasReferenceMetadata:
    build_start, build_end = _build_window(frame)
    input_sha256 = _file_sha256(path) if path.exists() else ""
    return BiasReferenceMetadata(
        schema_version=BIAS_REFERENCE_SCHEMA_VERSION,
        generated_at_utc=generated_at_utc or _utc_now_iso(),
        build_window_start=build_start,
        build_window_end=build_end,
        settlement_source=settlement_source,
        source_policy=source_policy,
        input_path=str(path),
        input_sha256=input_sha256,
        input_row_count=int(len(frame)),
        city_count=_nunique(frame, "city"),
        model_count=_nunique(frame, "model"),
        snapshot_id=snapshot_id or input_sha256[:16],
    )


def _empty_metadata(
    path: Path,
    *,
    generated_at_utc: str | None,
    settlement_source: str,
    source_policy: str,
    snapshot_id: str | None,
) -> BiasReferenceMetadata:
    return BiasReferenceMetadata(
        schema_version=BIAS_REFERENCE_SCHEMA_VERSION,
        generated_at_utc=generated_at_utc or _utc_now_iso(),
        build_window_start=None,
        build_window_end=None,
        settlement_source=settlement_source,
        source_policy=source_policy,
        input_path=str(path),
        input_sha256="",
        input_row_count=0,
        city_count=0,
        model_count=0,
        snapshot_id=snapshot_id or "",
    )


def _build_window(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    if "date" in frame:
        values = frame["date"].dropna().astype(str)
    elif "first_date" in frame and "last_date" in frame:
        starts = frame["first_date"].dropna().astype(str)
        ends = frame["last_date"].dropna().astype(str)
        if starts.empty or ends.empty:
            return None, None
        return str(starts.min()), str(ends.max())
    else:
        return None, None
    if values.empty:
        return None, None
    return str(values.min()), str(values.max())


def _nunique(frame: pd.DataFrame, column: str) -> int:
    if column not in frame:
        return 0
    return int(frame[column].dropna().astype(str).nunique())


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
