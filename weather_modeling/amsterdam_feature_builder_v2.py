"""Single Amsterdam path-feature authority for historical, PIT, and shadow use.

The builder is intentionally free of model, market, network, order, and runtime
side effects.  Historical-final callers may omit ``available_at`` but are then
explicitly non-PIT.  Captured/shadow callers must provide complete source paths
and availability clocks; missing lineage fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Iterable

import numpy as np
import pandas as pd


FEATURE_VERSION = "amsterdam_feature_builder_v2"
PATH_FEATURES = (
    "recent_slope",
    "recent_acceleration",
    "path_volatility",
    "running_fast_max",
    "time_since_high_minutes",
    "pullback_depth",
    "reheat_strength",
)


def _json_default(value: Any) -> Any:
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if pd.isna(value):
        return None
    return str(value)


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
        allow_nan=False,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FeatureBuildResult:
    decision_vintage_id: str
    status: str
    feature_vector: dict[str, float | None]
    feature_vector_hash: str
    lineage: pd.DataFrame
    source_path_row_count: int
    source_path_start: str | None
    source_path_end: str | None
    max_feature_available_at: str | None


class AmsterdamFeatureBuilderV2:
    """Build Amsterdam observation-path features with explicit PIT lineage."""

    version = FEATURE_VERSION
    path_features = PATH_FEATURES

    @staticmethod
    def _normalize_path(frame: pd.DataFrame, *, require_available_at: bool) -> pd.DataFrame:
        required = {"observed_at", "latest_fast_native_value"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"source path missing required columns: {sorted(missing)}")
        result = frame.copy()
        result["observed_at"] = pd.to_datetime(result["observed_at"], utc=True, errors="coerce")
        result["latest_fast_native_value"] = pd.to_numeric(
            result["latest_fast_native_value"], errors="coerce"
        )
        if "available_at" not in result:
            result["available_at"] = pd.NaT
        result["available_at"] = pd.to_datetime(result["available_at"], utc=True, errors="coerce")
        if require_available_at and result["available_at"].isna().any():
            raise ValueError("captured/shadow source path requires available_at for every row")
        if result["observed_at"].isna().any() or result["latest_fast_native_value"].isna().any():
            raise ValueError("source path contains invalid observed_at or temperature")
        if "source_observation_id" not in result:
            if "information_event_id" in result:
                result["source_observation_id"] = result["information_event_id"].astype(str)
            elif "raw_row_hash" in result:
                result["source_observation_id"] = result["raw_row_hash"].astype(str)
            elif require_available_at:
                raise ValueError("captured/shadow path requires source_observation_id")
            else:
                result["source_observation_id"] = [
                    stable_hash({"observed_at": value, "row": index})
                    for index, value in enumerate(result["observed_at"])
                ]
        result["source_observation_id"] = result["source_observation_id"].astype(str)
        return result

    @classmethod
    def add_path_features(
        cls,
        frame: pd.DataFrame,
        *,
        group_column: str,
        require_available_at: bool,
    ) -> pd.DataFrame:
        """Vectorized path feature implementation shared by every archive class."""

        if group_column not in frame:
            raise ValueError(f"group column missing: {group_column}")
        result = cls._normalize_path(frame, require_available_at=require_available_at)
        result = result.sort_values(
            [group_column, "observed_at", "available_at", "source_observation_id"],
            na_position="first",
        ).copy()
        group = result.groupby(group_column, sort=False)
        gap = group["observed_at"].diff().dt.total_seconds().div(60)
        delta = group["latest_fast_native_value"].diff()
        result["recent_slope"] = delta.where(gap.le(20))
        result["recent_acceleration"] = group["recent_slope"].diff().where(gap.le(20))
        result["path_volatility"] = group["latest_fast_native_value"].transform(
            lambda values: values.rolling(6, min_periods=2).std()
        )
        result["running_fast_max"] = group["latest_fast_native_value"].cummax()
        result["pullback_depth"] = (
            result["running_fast_max"] - result["latest_fast_native_value"]
        )
        rolling_low = group["latest_fast_native_value"].transform(
            lambda values: values.rolling(6, min_periods=1).min()
        )
        result["reheat_strength"] = result["latest_fast_native_value"] - rolling_low
        high_at = result["observed_at"].where(
            result["latest_fast_native_value"].eq(result["running_fast_max"])
        ).groupby(result[group_column]).ffill()
        result["time_since_high_minutes"] = (
            result["observed_at"] - high_at
        ).dt.total_seconds().div(60)
        return result

    @classmethod
    def build_vintage(
        cls,
        source_path: pd.DataFrame,
        *,
        decision_vintage_id: str,
        feature_cutoff_at: str | pd.Timestamp,
        decision_ready_at: str | pd.Timestamp,
        availability_class: str,
        feature_names: Iterable[str],
        source_path_complete: bool,
        observation_cutoff_at: str | pd.Timestamp | None = None,
    ) -> FeatureBuildResult:
        """Build one vintage; future rows are ignored and incomplete PIT paths fail."""

        strict_pit = availability_class in {"CAPTURED_PIT_ARCHIVE", "PROSPECTIVE_SHADOW"}
        cutoff = pd.Timestamp(feature_cutoff_at)
        decision = pd.Timestamp(decision_ready_at)
        if cutoff.tzinfo is None:
            cutoff = cutoff.tz_localize("UTC")
        else:
            cutoff = cutoff.tz_convert("UTC")
        if decision.tzinfo is None:
            decision = decision.tz_localize("UTC")
        else:
            decision = decision.tz_convert("UTC")
        if cutoff > decision:
            raise ValueError("feature_cutoff_at must be <= decision_ready_at")
        normalized = cls._normalize_path(source_path, require_available_at=strict_pit)
        observed_cutoff = pd.Timestamp(observation_cutoff_at or cutoff)
        if observed_cutoff.tzinfo is None:
            observed_cutoff = observed_cutoff.tz_localize("UTC")
        else:
            observed_cutoff = observed_cutoff.tz_convert("UTC")
        eligible = normalized.loc[normalized["observed_at"].le(observed_cutoff)].copy()
        if strict_pit:
            eligible = eligible.loc[eligible["available_at"].le(cutoff)].copy()
        eligible = (
            eligible.sort_values(["observed_at", "available_at", "source_observation_id"])
            .drop_duplicates("observed_at", keep="last")
        )
        status = "OK"
        if strict_pit and not source_path_complete:
            status = "INCOMPLETE_CAPTURED_SOURCE_PATH"
        elif eligible.empty:
            status = "INCOMPLETE_CAPTURED_SOURCE_PATH" if strict_pit else "EMPTY_HISTORICAL_PATH"
        elif strict_pit:
            gaps = eligible["observed_at"].diff().dropna().dt.total_seconds().div(60)
            target_start = None
            if "target_date" in eligible and eligible["target_date"].notna().any():
                target_dates = eligible["target_date"].dropna().astype(str).unique()
                if len(target_dates) != 1:
                    status = "INCOMPLETE_CAPTURED_SOURCE_PATH"
                else:
                    target_start = (
                        pd.Timestamp(target_dates[0], tz="Europe/Amsterdam").tz_convert("UTC")
                    )
            path_starts_at_boundary = target_start is None or eligible["observed_at"].min() == target_start
            path_ends_at_cutoff = eligible["observed_at"].max() == observed_cutoff
            cadence_complete = bool(gaps.eq(10).all())
            if not (path_starts_at_boundary and path_ends_at_cutoff and cadence_complete):
                status = "INCOMPLETE_CAPTURED_SOURCE_PATH"
        if status != "OK":
            vector = {name: None for name in feature_names}
            path_ids = eligible["source_observation_id"].astype(str).tolist() if not eligible.empty else []
            path_start = None if eligible.empty else eligible["observed_at"].min().isoformat()
            path_end = None if eligible.empty else eligible["observed_at"].max().isoformat()
            max_available = (
                None if eligible.empty or eligible["available_at"].isna().all()
                else eligible["available_at"].max().isoformat()
            )
            missing_lineage = pd.DataFrame([{
                "decision_vintage_id": str(decision_vintage_id),
                "feature_name": name,
                "feature_value": None,
                "feature_available_at": max_available,
                "source_observation_ids": json.dumps(path_ids, separators=(",", ":")),
                "source_path_start": path_start,
                "source_path_end": path_end,
                "source_path_row_count": int(len(eligible)),
                "feature_version": cls.version,
                "missing_reason": status,
            } for name in feature_names])
            return FeatureBuildResult(
                decision_vintage_id=str(decision_vintage_id), status=status,
                feature_vector=vector, feature_vector_hash=stable_hash(vector), lineage=missing_lineage,
                source_path_row_count=int(len(eligible)), source_path_start=path_start,
                source_path_end=path_end, max_feature_available_at=max_available,
            )
        eligible["_path_id"] = str(decision_vintage_id)
        featured = cls.add_path_features(
            eligible, group_column="_path_id", require_available_at=strict_pit
        )
        current = featured.iloc[-1]
        path_ids = featured["source_observation_id"].astype(str).tolist()
        current_id = [str(current["source_observation_id"])]
        path_start = featured["observed_at"].min().isoformat()
        path_end = featured["observed_at"].max().isoformat()
        max_available = (
            None if featured["available_at"].isna().all()
            else featured["available_at"].max().isoformat()
        )
        vector: dict[str, float | None] = {}
        lineage_rows: list[dict[str, Any]] = []
        for name in feature_names:
            raw_value = current.get(name, np.nan)
            value = None if pd.isna(raw_value) else float(raw_value)
            vector[name] = value
            path_dependent = name in cls.path_features
            ids = path_ids if path_dependent else current_id
            available = max_available if path_dependent else (
                None if pd.isna(current["available_at"]) else current["available_at"].isoformat()
            )
            missing_reason = None
            if value is None:
                missing_reason = "INSUFFICIENT_PATH_HISTORY" if path_dependent else "SOURCE_FIELD_MISSING"
            elif available is None:
                missing_reason = "HISTORICAL_AVAILABLE_AT_UNKNOWN"
            lineage_rows.append({
                "decision_vintage_id": str(decision_vintage_id),
                "feature_name": name,
                "feature_value": value,
                "feature_available_at": available,
                "source_observation_ids": json.dumps(ids, separators=(",", ":")),
                "source_path_start": path_start,
                "source_path_end": path_end,
                "source_path_row_count": int(len(featured)),
                "feature_version": cls.version,
                "missing_reason": missing_reason,
            })
        if strict_pit and max_available is not None and pd.Timestamp(max_available) > cutoff:
            raise AssertionError("max(feature_available_at) exceeds feature_cutoff_at")
        return FeatureBuildResult(
            decision_vintage_id=str(decision_vintage_id), status="OK",
            feature_vector=vector, feature_vector_hash=stable_hash(vector),
            lineage=pd.DataFrame(lineage_rows), source_path_row_count=int(len(featured)),
            source_path_start=path_start, source_path_end=path_end,
            max_feature_available_at=max_available,
        )
