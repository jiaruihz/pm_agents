"""PIT development panel and W0 residual baseline for daily minimum temperature.

This module intentionally stops before producing a frozen/live probability
artifact.  Observation-cache minima are useful development labels but are not
exchange settlement truth, and the canonical Tmin full-ladder history starts
only after the corresponding market-books rollout.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import pandas as pd


SCHEMA_VERSION = "weather_daily_minimum_development_v1"
MECHANISM_ID = "daily_low_temperature_exact_bracket_v1"
LABEL_BASIS = "observation_cache_intraday_min_proxy_not_settlement"


def _parse_ts(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _jsonl_rows(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    yield row


def _checkpoint_utc(target_date: str, timezone_name: str, hour_local: int) -> datetime:
    local_day = date.fromisoformat(target_date) - timedelta(days=1)
    local = datetime.combine(local_day, time(hour=hour_local), ZoneInfo(timezone_name))
    return local.astimezone(UTC)


def _curve_features(row: dict[str, Any]) -> dict[str, Any] | None:
    curve = [item for item in row.get("hourly_curve") or [] if isinstance(item, dict)]
    with_temp = [item for item in curve if item.get("temperature_f") is not None]
    if not with_temp:
        return None
    minimum = min(with_temp, key=lambda item: float(item["temperature_f"]))
    morning = [
        item
        for item in with_temp
        if 0 <= int(str(item.get("time_local") or "T00:00")[11:13]) <= 9
    ]
    evening = [
        item
        for item in with_temp
        if 18 <= int(str(item.get("time_local") or "T00:00")[11:13]) <= 23
    ]

    def window_min(rows: list[dict[str, Any]]) -> float | None:
        return min(float(item["temperature_f"]) for item in rows) if rows else None

    return {
        "forecast_min_f": float(minimum["temperature_f"]),
        "forecast_min_time_local": str(minimum.get("time_local") or ""),
        "forecast_morning_min_f": window_min(morning),
        "forecast_evening_min_f": window_min(evening),
        "forecast_min_cloud_cover_pct": minimum.get("cloud_cover_pct"),
        "forecast_min_precip_probability_pct": minimum.get(
            "precipitation_probability_pct"
        ),
        "forecast_min_wind_speed_10m_kt": minimum.get("wind_speed_10m_kt"),
    }


def load_forecast_checkpoints(
    forecast_root: Path,
    *,
    cities: set[str],
    checkpoint_hour_local: int,
) -> list[dict[str, Any]]:
    selected: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
    paths = sorted(forecast_root.glob("????-??-??/*.jsonl"))
    for row in _jsonl_rows(paths):
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        timezone_name = str(row.get("forecast_timezone") or "")
        available = _parse_ts(row.get("available_at_utc"))
        if city not in cities or not target_date or not timezone_name or available is None:
            continue
        checkpoint = _checkpoint_utc(target_date, timezone_name, checkpoint_hour_local)
        if available > checkpoint:
            continue
        features = _curve_features(row)
        if features is None:
            continue
        key = (city, target_date)
        candidate = {
            "schema_version": SCHEMA_VERSION,
            "mechanism_id": MECHANISM_ID,
            "city": city,
            "target_date": target_date,
            "checkpoint_name": f"d_minus_1_{checkpoint_hour_local:02d}00_local",
            "decision_ts_utc": checkpoint.isoformat(),
            "forecast_available_at_utc": available.isoformat(),
            "forecast_model": row.get("forecast_model"),
            "forecast_source": row.get("forecast_source"),
            "forecast_values_hash": row.get("forecast_values_hash"),
            "forecast_model_fallback": bool(row.get("forecast_model_fallback")),
            **features,
        }
        if key not in selected or available > selected[key][0]:
            selected[key] = (available, candidate)
    return [selected[key][1] for key in sorted(selected)]


def load_observation_proxy_labels(
    observation_root: Path, *, cities: set[str]
) -> dict[tuple[str, str], float]:
    minima: dict[tuple[str, str], float] = {}
    paths = sorted(observation_root.glob("????-??-??/observations.jsonl"))
    for row in _jsonl_rows(paths):
        city = str(row.get("city") or "")
        target_date = str(row.get("target_date") or "")
        if city not in cities or not target_date:
            continue
        value = row.get("running_min_c")
        if value is None:
            value = row.get("current_temp_c")
        try:
            temp_c = float(value)
        except (TypeError, ValueError):
            continue
        key = (city, target_date)
        minima[key] = min(temp_c, minima.get(key, temp_c))
    return minima


def count_minimum_market_dates(ladder_root: Path, *, cities: set[str]) -> dict[str, int]:
    dates: dict[str, set[str]] = defaultdict(set)
    for path in sorted(ladder_root.glob("????-??-??/market_ladder_snapshot_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in payload.get("records") or []:
            city = str(row.get("city") or "")
            if city in cities and str(row.get("extreme_kind") or "max") == "min":
                dates[city].add(str(row.get("target_date") or ""))
    return {city: len(dates.get(city, set())) for city in sorted(cities)}


def _walk_forward_w0(frame: pd.DataFrame, min_train_dates: int) -> tuple[pd.DataFrame, dict[str, Any]]:
    labeled = frame.dropna(subset=["observed_min_proxy_c"]).copy()
    if labeled.empty:
        return labeled, {"status": "blocked_no_proxy_labels", "scored_rows": 0}
    labeled["forecast_min_c"] = (labeled["forecast_min_f"] - 32.0) * 5.0 / 9.0
    labeled["label_tick_c"] = labeled["observed_min_proxy_c"].round().astype(int)
    labeled["forecast_tick_c"] = labeled["forecast_min_c"].round().astype(int)
    labeled["residual_tick_c"] = labeled["label_tick_c"] - labeled["forecast_tick_c"]
    rows: list[dict[str, Any]] = []
    ordered_dates = sorted(labeled["target_date"].unique())
    for target_date in ordered_dates:
        train = labeled[labeled["target_date"] < target_date]
        test = labeled[labeled["target_date"] == target_date]
        train_dates = int(train["target_date"].nunique())
        if train_dates < min_train_dates:
            continue
        pooled = Counter(int(value) for value in train["residual_tick_c"])
        support = list(range(min(pooled) - 2, max(pooled) + 3))
        denominator = sum(pooled.values()) + len(support)
        for _, row in test.iterrows():
            actual_residual = int(row["residual_tick_c"])
            probability = (pooled.get(actual_residual, 0) + 1) / denominator
            expected_residual = sum(
                value * (pooled.get(value, 0) + 1) / denominator for value in support
            )
            rows.append(
                {
                    **row.to_dict(),
                    "w0_actual_probability": probability,
                    "w0_log_loss": -math.log(max(probability, 1e-12)),
                    "w0_expected_min_c": float(row["forecast_tick_c"] + expected_residual),
                    "w0_train_dates": train_dates,
                }
            )
    scored = pd.DataFrame(rows)
    if scored.empty:
        return scored, {
            "status": "blocked_insufficient_walk_forward_dates",
            "scored_rows": 0,
            "available_label_dates": int(labeled["target_date"].nunique()),
        }
    return scored, {
        "status": "development_proxy_only",
        "scored_rows": len(scored),
        "scored_dates": int(scored["target_date"].nunique()),
        "mean_log_loss": float(scored["w0_log_loss"].mean()),
        "mean_absolute_error_c": float(
            (scored["w0_expected_min_c"] - scored["observed_min_proxy_c"]).abs().mean()
        ),
    }


def run_daily_minimum_development(
    *,
    forecast_root: Path,
    observation_root: Path,
    ladder_root: Path,
    output_dir: Path,
    cities: list[str],
    checkpoint_hour_local: int = 18,
    min_train_dates: int = 7,
    promotion_min_dates: int = 30,
) -> dict[str, Any]:
    city_set = set(cities)
    panel = pd.DataFrame(
        load_forecast_checkpoints(
            forecast_root,
            cities=city_set,
            checkpoint_hour_local=checkpoint_hour_local,
        )
    )
    labels = load_observation_proxy_labels(observation_root, cities=city_set)
    if not panel.empty:
        panel["observed_min_proxy_c"] = [
            labels.get((str(row.city), str(row.target_date)))
            for row in panel.itertuples(index=False)
        ]
        panel["label_basis"] = LABEL_BASIS
    market_dates = count_minimum_market_dates(ladder_root, cities=city_set)
    scored, model = _walk_forward_w0(panel, min_train_dates=min_train_dates)
    labeled_dates = (
        panel.dropna(subset=["observed_min_proxy_c"])
        .groupby("city")["target_date"]
        .nunique()
        .to_dict()
        if not panel.empty
        else {}
    )
    market_ready = all(market_dates.get(city, 0) >= promotion_min_dates for city in cities)
    if sum(market_dates.values()) == 0:
        market_blocker = "same_row_tmin_market_baseline_not_yet_available"
    elif not market_ready:
        market_blocker = "same_row_tmin_market_baseline_below_promotion_dates"
    else:
        market_blocker = "same_row_market_score_not_yet_evaluated"
    summary = {
        "schema_version": SCHEMA_VERSION,
        "mechanism_id": MECHANISM_ID,
        "status": "research_only_blocked_for_fit",
        "cities": cities,
        "checkpoint": f"d_minus_1_{checkpoint_hour_local:02d}00_local",
        "denominator_scope": "latest PIT forecast available by checkpoint per city-target_date",
        "inputs": {
            "forecast_root": str(forecast_root),
            "observation_root": str(observation_root),
            "ladder_root": str(ladder_root),
        },
        "forecast_rows": int(len(panel)),
        "forecast_dates_by_city": (
            {str(k): int(v) for k, v in panel.groupby("city")["target_date"].nunique().to_dict().items()}
            if not panel.empty
            else {}
        ),
        "proxy_label_dates_by_city": {str(k): int(v) for k, v in labeled_dates.items()},
        "minimum_full_ladder_dates_by_city": market_dates,
        "signal_funnel": {
            "forecast_checkpoint_rows": int(len(panel)),
            "weather_proxy_labeled_rows": int(panel["observed_min_proxy_c"].notna().sum())
            if not panel.empty
            else 0,
            "w0_walk_forward_rows": int(len(scored)),
        },
        "evidence_funnel": {
            "settlement_truth_rows": 0,
            "same_row_market_baseline_dates": int(sum(market_dates.values())),
            "executable_expression_rows": 0,
            "fills": 0,
        },
        "w0_weather_only_development": model,
        "promotion_min_clean_dates": promotion_min_dates,
        "promotion_ready": False,
        "blockers": [
            "exchange_settlement_labels_not_joined",
            market_blocker,
            "minimum_clean_forward_dates_not_met",
        ],
        "production": {
            "probability_artifact_emitted": False,
            "orders_submitted": 0,
            "execution_mode": "research_only",
        },
        "artifacts": {
            "panel": str(output_dir / "daily_minimum_checkpoint_panel.csv"),
            "walk_forward": str(output_dir / "daily_minimum_w0_walk_forward.csv"),
            "summary": str(output_dir / "summary.json"),
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    panel.to_csv(output_dir / "daily_minimum_checkpoint_panel.csv", index=False)
    scored.to_csv(output_dir / "daily_minimum_w0_walk_forward.csv", index=False)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary
