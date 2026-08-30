"""PIT research runner for Korea city current-rung exact-NO probabilities.

The module keeps the common Korea AMOS event/book/settlement plumbing shared,
while fitting a separate city basis.  It is research-only: artifacts produced
here do not create SignalCandidate, TradeIntent, order, or live authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .probability import (
    binary_calibration_table,
    binary_loss_values,
    binary_score,
    composite_grain_weights,
    date_block_bootstrap_delta,
)
from weather_clock_contract import parse_utc_or_none


UTC_ZONE = ZoneInfo("UTC")
SEOUL_ZONE = ZoneInfo("Asia/Seoul")
EPS = 1e-6
CHECKPOINT_SCHEMA_VERSION = "korea_amos_first_seen_state_v1"
OUTPUT_SCHEMA_VERSION = "korea_city_exact_no_probability_research_v1"
ARTIFACT_SCHEMA_VERSION = "korea_city_exact_no_probability_artifact_v1"
MODEL_ID_BY_CITY = {
    "Seoul": "seoul_intraday_exact_no",
    "Busan": "busan_intraday_exact_no",
}
ALPHA_GRID = (0.0, 0.125, 0.25, 0.5)


PREFERRED_FEATURES = (
    "local_hour_sin",
    "local_hour_cos",
    "routine_rung",
    "preferred_margin_to_rung_c",
    "preferred_running_margin_to_rung_c",
    "preferred_distance_below_running_max_c",
    "preferred_minutes_since_running_max",
    "preferred_slope_15m_c_per_hour",
    "preferred_slope_60m_c_per_hour",
    "runway_max_minus_preferred_c",
    "runway_temp_spread_c",
    "runway_count",
    "forecast_ceiling_margin_c",
    "hours_to_forecast_peak",
    "relative_humidity_pct",
    "dewpoint_depression_c",
    "forecast_cloud_cover_remaining_3h_mean_pct",
    "forecast_precip_probability_remaining_3h_max_pct",
    "forecast_wind_speed_remaining_3h_max_kt",
)


MAX_RUNWAY_FEATURES = (
    "local_hour_sin",
    "local_hour_cos",
    "routine_rung",
    "max_runway_margin_to_rung_c",
    "max_runway_running_margin_to_rung_c",
    "max_runway_distance_below_running_max_c",
    "max_runway_minutes_since_running_max",
    "max_runway_slope_15m_c_per_hour",
    "max_runway_slope_60m_c_per_hour",
    "runway_temp_spread_c",
    "runway_count",
    "forecast_ceiling_margin_c",
    "hours_to_forecast_peak",
    "relative_humidity_pct",
    "dewpoint_depression_c",
    "forecast_cloud_cover_remaining_3h_mean_pct",
    "forecast_precip_probability_remaining_3h_max_pct",
    "forecast_wind_speed_remaining_3h_max_kt",
)


@dataclass(frozen=True)
class ExperimentConfig:
    city: str
    start_date: str
    development_end: str
    holdout_start: str
    end_date: str
    warmup_dates: int = 7
    bootstrap_draws: int = 5000
    seed: int = 20260813

    def __post_init__(self) -> None:
        if self.city not in MODEL_ID_BY_CITY:
            raise ValueError(f"unsupported Korea city: {self.city}")
        if not (
            self.start_date <= self.development_end < self.holdout_start <= self.end_date
        ):
            raise ValueError("invalid chronological split")
        if self.warmup_dates < 3:
            raise ValueError("warmup_dates must be at least 3")


def parse_utc(value: Any) -> datetime | None:
    return parse_utc_or_none(value, field="korea_exact_no_clock")


def finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield line_number, row


def _exact_bracket(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or "+" in text or "below" in text.casefold() or "higher" in text.casefold():
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not number.is_integer():
        return None
    return str(int(number))


def _best(summary: Mapping[str, Any], side: str) -> tuple[float | None, float | None]:
    price = finite(summary.get(f"best_{side}"))
    size = finite(summary.get(f"{side}_size"))
    return price, size


def effective_exact_quote(
    books: Sequence[Mapping[str, Any]], rung: int
) -> dict[str, Any]:
    """Build one coherent YES/NO top-of-book from mirrored outcome tokens."""

    wanted = str(int(rung))
    selected = [
        dict(book)
        for book in books
        if _exact_bracket(book.get("bracket")) == wanted
        and str(book.get("outcome") or "").casefold() in {"yes", "no"}
        and str(book.get("status") or "") == "ok"
    ]
    by_outcome = {
        str(book.get("outcome") or "").casefold(): book for book in selected
    }
    yes_summary = (by_outcome.get("yes") or {}).get("summary") or {}
    no_summary = (by_outcome.get("no") or {}).get("summary") or {}
    yes_bid, yes_bid_size = _best(yes_summary, "bid")
    yes_ask, yes_ask_size = _best(yes_summary, "ask")
    no_bid, no_bid_size = _best(no_summary, "bid")
    no_ask, no_ask_size = _best(no_summary, "ask")

    effective_yes_bids = [
        item
        for item in (
            (yes_bid, yes_bid_size, "yes_bid"),
            (None if no_ask is None else 1.0 - no_ask, no_ask_size, "one_minus_no_ask"),
        )
        if item[0] is not None
    ]
    effective_yes_asks = [
        item
        for item in (
            (yes_ask, yes_ask_size, "yes_ask"),
            (None if no_bid is None else 1.0 - no_bid, no_bid_size, "one_minus_no_bid"),
        )
        if item[0] is not None
    ]
    best_yes_bid = max(effective_yes_bids, key=lambda item: float(item[0])) if effective_yes_bids else (None, None, None)
    best_yes_ask = min(effective_yes_asks, key=lambda item: float(item[0])) if effective_yes_asks else (None, None, None)
    bid = finite(best_yes_bid[0])
    ask = finite(best_yes_ask[0])
    yes_mid = (
        (bid + ask) / 2.0
        if bid is not None and ask is not None and ask >= bid
        else None
    )
    fetched = sorted(
        str(book.get("fetched_at_utc") or "") for book in selected if book.get("fetched_at_utc")
    )
    identity_payload = [
        {
            "condition_id": book.get("condition_id"),
            "token_id": book.get("token_id"),
            "outcome": book.get("outcome"),
            "fetched_at_utc": book.get("fetched_at_utc"),
            "summary": book.get("summary"),
        }
        for book in sorted(selected, key=lambda item: str(item.get("outcome") or ""))
    ]
    return {
        "market_yes_bid": bid,
        "market_yes_ask": ask,
        "market_yes_mid": yes_mid,
        "market_no_p": None if yes_mid is None else 1.0 - yes_mid,
        "effective_no_ask": None if bid is None else 1.0 - bid,
        "effective_no_ask_size": best_yes_bid[1],
        "effective_no_ask_source": best_yes_bid[2],
        "no_ask": no_ask,
        "no_ask_size": no_ask_size,
        "yes_ask_size": best_yes_ask[1],
        "book_fetched_at_utc": fetched[-1] if fetched else None,
        "feature_book_snapshot_id": stable_hash(identity_payload) if identity_payload else None,
        "condition_id": next((book.get("condition_id") for book in selected if book.get("condition_id")), None),
        "no_token_id": (by_outcome.get("no") or {}).get("token_id"),
        "book_status": (
            "scorable"
            if yes_mid is not None
            else "missing_two_sided_current_rung_mid"
            if selected
            else "missing_current_rung_book"
        ),
    }


def load_settlement_winners(
    db_path: Path, *, city: str, start_date: str, end_date: str
) -> tuple[dict[str, str], dict[str, Any]]:
    realpath = db_path.resolve()
    stat = realpath.stat()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=2000")
    rows = conn.execute(
        """
        SELECT target_date, bracket, source_system, source_payload_hash,
               producer_build_id, created_at_utc
        FROM settlement_outcomes
        WHERE city=? AND target_date BETWEEN ? AND ?
          AND settlement_status='settled' AND final_price>=0.999
        ORDER BY target_date, bracket
        """,
        (city, start_date, end_date),
    ).fetchall()
    fact_build = conn.execute(
        "SELECT MAX(fact_built_at_utc) FROM fact_signal_candidates"
    ).fetchone()[0]
    conn.close()
    grouped: dict[str, list[str]] = {}
    for target_date, bracket, *_rest in rows:
        grouped.setdefault(str(target_date), []).append(str(bracket))
    duplicates = {key: values for key, values in grouped.items() if len(values) != 1}
    if duplicates:
        raise RuntimeError(f"settlement winner is not unique: {duplicates}")
    winners = {key: values[0] for key, values in grouped.items()}
    evidence_hash = stable_hash([list(row) for row in rows])
    identity = {
        "requested_path": str(db_path),
        "realpath": str(realpath),
        "device": int(stat.st_dev),
        "inode": int(stat.st_ino),
        "size_bytes": int(stat.st_size),
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        "fact_build_at_utc": fact_build,
        "settlement_rows": len(rows),
        "settlement_evidence_sha256": evidence_hash,
    }
    return winners, identity


def _base_checkpoint_row(
    row: Mapping[str, Any], *, path: Path, line_number: int
) -> dict[str, Any] | None:
    observed = parse_utc(row.get("source_observation_ts_utc"))
    source_available = parse_utc(
        row.get("source_available_at_utc") or row.get("source_first_seen_ts_utc")
    )
    rung = finite(row.get("routine_running_max_market_value"))
    if observed is None or source_available is None or rung is None or not float(rung).is_integer():
        return None
    rung_int = int(rung)
    quote = effective_exact_quote((row.get("market_capture") or {}).get("books") or [], rung_int)
    book_at = parse_utc(quote.get("book_fetched_at_utc"))
    decision = max(value for value in (source_available, book_at) if value is not None)
    local = decision.astimezone(SEOUL_ZONE)
    forecast = row.get("forecast_context") or {}
    forecast_max_f = finite(forecast.get("forecast_max_f"))
    forecast_max_c = None if forecast_max_f is None else (forecast_max_f - 32.0) * 5.0 / 9.0
    forecast_peak_hour = finite(forecast.get("forecast_peak_hour_local"))
    preferred = finite(row.get("preferred_runway_temp_c"))
    source_max = finite(row.get("source_temp_c") or row.get("runway_temp_max_c"))
    if preferred is None:
        preferred = source_max
    max_temp = finite(row.get("runway_temp_max_c"))
    if max_temp is None:
        max_temp = source_max
    path_windows = row.get("path_windows") or {}
    event_key = str(row.get("source_event_key") or "")
    checkpoint_id = stable_hash(
        {
            "city": row.get("city"),
            "target_date": row.get("target_date"),
            "source_event_key": event_key,
            "source_available_at_utc": source_available.isoformat(),
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
        }
    )
    return {
        "checkpoint_id": checkpoint_id,
        "city": str(row.get("city") or ""),
        "target_date": str(row.get("target_date") or ""),
        "source_event_key": event_key,
        "source_observation_ts_utc": observed,
        "source_available_at_utc": source_available,
        "decision_ts_utc": decision,
        "raw_ref": f"{path}#{line_number}",
        "routine_rung": rung_int,
        "preferred_runway_temp_c": preferred,
        "max_runway_temp_c": max_temp,
        "runway_temp_spread_c": finite(row.get("runway_temp_spread_c")),
        "runway_count": finite(row.get("runway_count")),
        "preferred_runway_available": row.get("preferred_runway_temp_c") is not None,
        "max_runway_running_max_c": finite(row.get("source_running_max_c")),
        "max_runway_distance_below_running_max_c": finite(row.get("distance_below_source_running_max_c")),
        "max_runway_minutes_since_running_max": finite(row.get("minutes_since_source_running_max")),
        "max_runway_slope_15m_c_per_hour": finite((path_windows.get("15m") or {}).get("temp_slope_c_per_hour")),
        "max_runway_slope_60m_c_per_hour": finite((path_windows.get("60m") or {}).get("temp_slope_c_per_hour")),
        "forecast_max_c": forecast_max_c,
        "forecast_peak_hour_local": forecast_peak_hour,
        "forecast_ceiling_margin_c": None if forecast_max_c is None else forecast_max_c - rung_int,
        "hours_to_forecast_peak": None if forecast_peak_hour is None else forecast_peak_hour - (local.hour + local.minute / 60.0),
        "relative_humidity_pct": finite(row.get("relative_humidity_pct")),
        "dewpoint_depression_c": finite(row.get("dewpoint_depression_c")),
        "forecast_cloud_cover_remaining_3h_mean_pct": finite(forecast.get("forecast_cloud_cover_remaining_3h_mean_pct")),
        "forecast_precip_probability_remaining_3h_max_pct": finite(forecast.get("forecast_precip_probability_remaining_3h_max_pct")),
        "forecast_wind_speed_remaining_3h_max_kt": finite(forecast.get("forecast_wind_speed_remaining_3h_max_kt")),
        "local_hour": local.hour + local.minute / 60.0,
        "local_hour_sin": math.sin(2.0 * math.pi * (local.hour + local.minute / 60.0) / 24.0),
        "local_hour_cos": math.cos(2.0 * math.pi * (local.hour + local.minute / 60.0) / 24.0),
        "source_to_book_lag_seconds": None if book_at is None else (book_at - source_available).total_seconds(),
        "market_feature_clock": "first_post_event",
        **quote,
    }


def _prior_at_or_before(group: pd.DataFrame, cutoff: pd.Timestamp) -> pd.Series | None:
    eligible = group[group["source_observation_ts_utc"] <= cutoff]
    return None if eligible.empty else eligible.iloc[-1]


def add_preferred_path_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Derive Seoul preferred-runway path independently of group-max path."""

    output = frame.copy()
    output["source_observation_ts_utc"] = pd.to_datetime(
        output["source_observation_ts_utc"], utc=True
    )
    output["decision_ts_utc"] = pd.to_datetime(output["decision_ts_utc"], utc=True)
    output = output.sort_values(
        ["city", "target_date", "decision_ts_utc", "source_observation_ts_utc"],
        kind="stable",
    )
    columns = {
        "preferred_running_max_c": [],
        "preferred_distance_below_running_max_c": [],
        "preferred_minutes_since_running_max": [],
        "preferred_slope_15m_c_per_hour": [],
        "preferred_slope_60m_c_per_hour": [],
    }
    for _key, group in output.groupby(["city", "target_date"], sort=False):
        history = group.reset_index()
        running_max = -math.inf
        latest_high: pd.Timestamp | None = None
        for position, row in history.iterrows():
            current = finite(row["preferred_runway_temp_c"])
            observed = pd.Timestamp(row["source_observation_ts_utc"])
            if current is not None and current >= running_max:
                running_max = current
                latest_high = observed
            columns["preferred_running_max_c"].append(running_max if math.isfinite(running_max) else None)
            columns["preferred_distance_below_running_max_c"].append(None if current is None or not math.isfinite(running_max) else running_max - current)
            columns["preferred_minutes_since_running_max"].append(None if latest_high is None else (observed - latest_high).total_seconds() / 60.0)
            for minutes in (15, 60):
                prior = _prior_at_or_before(
                    history.iloc[: position + 1],
                    observed - pd.Timedelta(minutes=minutes),
                )
                prior_temp = None if prior is None else finite(prior["preferred_runway_temp_c"])
                prior_ts = None if prior is None else pd.Timestamp(prior["source_observation_ts_utc"])
                elapsed = None if prior_ts is None else (observed - prior_ts).total_seconds() / 60.0
                slope = None if current is None or prior_temp is None or not elapsed else (current - prior_temp) * 60.0 / elapsed
                columns[f"preferred_slope_{minutes}m_c_per_hour"].append(slope)
    if any(len(values) != len(output) for values in columns.values()):
        raise RuntimeError("preferred path feature alignment failed")
    for name, values in columns.items():
        output[name] = values
    output["preferred_margin_to_rung_c"] = output["preferred_runway_temp_c"] - output["routine_rung"]
    output["preferred_running_margin_to_rung_c"] = output["preferred_running_max_c"] - output["routine_rung"]
    output["max_runway_margin_to_rung_c"] = output["max_runway_temp_c"] - output["routine_rung"]
    output["max_runway_running_margin_to_rung_c"] = output["max_runway_running_max_c"] - output["routine_rung"]
    output["runway_max_minus_preferred_c"] = output["max_runway_temp_c"] - output["preferred_runway_temp_c"]
    return output


def load_checkpoint_frame(
    checkpoint_root: Path,
    *,
    city: str,
    start_date: str,
    end_date: str,
    winners: Mapping[str, str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    inventory = []
    raw_city_rows = 0
    for path in sorted(checkpoint_root.glob("????-??-??.jsonl")):
        if not start_date <= path.stem <= end_date:
            continue
        stat = path.stat()
        inventory.append(
            {
                "path": str(path),
                "size_bytes": int(stat.st_size),
                "mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                "sha256": sha256_file(path),
            }
        )
        for line_number, raw in _iter_jsonl(path):
            if raw.get("city") != city or raw.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
                continue
            raw_city_rows += 1
            base = _base_checkpoint_row(raw, path=path, line_number=line_number)
            if base is not None:
                rows.append(base)
    if not rows:
        raise RuntimeError(f"no {city} Korea checkpoints in requested window")
    frame = pd.DataFrame(rows).sort_values(
        ["target_date", "source_observation_ts_utc", "decision_ts_utc"], kind="stable"
    )
    duplicate_rows = int(frame.duplicated("source_event_key", keep=False).sum())
    frame = frame.drop_duplicates("source_event_key", keep="first").reset_index(drop=True)
    frame = add_preferred_path_features(frame)
    frame["winner_bracket"] = frame["target_date"].map(winners)
    frame["label_no"] = np.where(
        frame["winner_bracket"].notna(),
        (frame["winner_bracket"].astype(str) != frame["routine_rung"].astype(str)).astype(float),
        np.nan,
    )
    frame["market_no_p"] = pd.to_numeric(frame["market_no_p"], errors="coerce")
    frame["scorable_status"] = np.select(
        [
            frame["winner_bracket"].isna(),
            frame["market_no_p"].isna(),
            frame["preferred_runway_temp_c"].isna(),
        ],
        ["label_not_available", frame["book_status"], "preferred_runway_not_available"],
        default="scorable",
    )
    decision = pd.to_datetime(frame["decision_ts_utc"], utc=True)
    frame["ten_minute_bin_utc"] = decision.dt.floor("10min")
    frame["is_signal_state_entry"] = ~frame.duplicated(
        ["target_date", "routine_rung"], keep="first"
    )
    frame["is_signal_ten_minute_checkpoint"] = ~frame.duplicated(
        ["target_date", "routine_rung", "ten_minute_bin_utc"], keep="first"
    )
    frame["is_state_entry"] = False
    frame["is_ten_minute_checkpoint"] = False
    scorable_index = frame.index[frame["scorable_status"].eq("scorable")]
    scorable_frame = frame.loc[scorable_index]
    frame.loc[scorable_index, "is_state_entry"] = ~scorable_frame.duplicated(
        ["target_date", "routine_rung"], keep="first"
    )
    frame.loc[scorable_index, "is_ten_minute_checkpoint"] = (
        ~scorable_frame.duplicated(
            ["target_date", "routine_rung", "ten_minute_bin_utc"],
            keep="first",
        )
    )
    manifest = {
        "checkpoint_root": str(checkpoint_root),
        "files": inventory,
        "input_inventory_sha256": stable_hash(inventory),
        "raw_city_rows": raw_city_rows,
        "duplicate_source_event_rows": duplicate_rows,
        "unique_checkpoint_rows": int(len(frame)),
    }
    return frame, manifest


def make_weather_model(features: Sequence[str]) -> Pipeline:
    return Pipeline(
        [
            (
                "features",
                ColumnTransformer(
                    [
                        (
                            "numeric",
                            Pipeline(
                                [
                                    ("impute", SimpleImputer(strategy="median")),
                                    ("scale", StandardScaler()),
                                ]
                            ),
                            list(features),
                        )
                    ],
                    remainder="drop",
                ),
            ),
            (
                "model",
                LogisticRegression(C=0.1, max_iter=3000, solver="lbfgs"),
            ),
        ]
    )


def _fit_model(frame: pd.DataFrame, features: Sequence[str]) -> Pipeline:
    if frame["label_no"].nunique() < 2:
        raise RuntimeError("training rows need both labels")
    weights = composite_grain_weights(
        frame,
        membership_columns=("is_state_entry", "is_ten_minute_checkpoint"),
    )
    model = make_weather_model(features)
    model.fit(frame[list(features)], frame["label_no"].astype(int), model__sample_weight=weights)
    return model


def _logit(probability: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(probability, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1.0 - p))


def bounded_market_posterior(
    market_probability: Sequence[float] | np.ndarray,
    weather_probability: Sequence[float] | np.ndarray,
    alpha: float,
) -> np.ndarray:
    market = np.asarray(market_probability, dtype=float)
    weather = np.asarray(weather_probability, dtype=float)
    value = _logit(market) + float(alpha) * (_logit(weather) - _logit(market))
    return 1.0 / (1.0 + np.exp(-value))


def expanding_oof_predictions(
    development: pd.DataFrame,
    *,
    features: Sequence[str],
    warmup_dates: int,
) -> np.ndarray:
    dates = sorted(development["target_date"].astype(str).unique())
    output = np.full(len(development), np.nan, dtype=float)
    for index, target_date in enumerate(dates):
        if index < warmup_dates:
            continue
        train_mask = development["target_date"].astype(str).isin(dates[:index])
        test_mask = development["target_date"].astype(str).eq(target_date)
        train = development.loc[train_mask]
        if train["label_no"].nunique() < 2:
            continue
        model = _fit_model(train, features)
        output[test_mask.to_numpy()] = model.predict_proba(
            development.loc[test_mask, list(features)]
        )[:, 1]
    return output


def _grain(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "state_entry":
        return frame[frame["is_state_entry"]].copy()
    if name == "ten_minute":
        return frame[frame["is_ten_minute_checkpoint"]].copy()
    if name == "checkpoint":
        return frame.copy()
    raise ValueError(name)


def score_models(frame: pd.DataFrame, columns: Mapping[str, str], *, draws: int, seed: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for grain_name in ("state_entry", "ten_minute", "checkpoint"):
        grain = _grain(frame, grain_name)
        grain = grain.dropna(subset=["label_no", *columns.values()]).copy()
        if grain.empty:
            result[grain_name] = {"status": "not_available"}
            continue
        scores = {
            model_name: binary_score(
                grain,
                grain[column].to_numpy(float),
                label_column="label_no",
            )
            for model_name, column in columns.items()
        }
        deltas: dict[str, Any] = {}
        for model_name, column in columns.items():
            if model_name == "market":
                continue
            deltas[model_name] = {}
            for metric in ("brier", "logloss"):
                deltas[model_name][metric] = date_block_bootstrap_delta(
                    grain,
                    binary_loss_values(grain["label_no"], grain[column], metric=metric),
                    binary_loss_values(grain["label_no"], grain[columns["market"]], metric=metric),
                    draws=draws,
                    seed=seed + (0 if metric == "brier" else 1),
                )
        result[grain_name] = {
            "status": "ok",
            "rows": int(len(grain)),
            "target_dates": int(grain["target_date"].nunique()),
            "scores": scores,
            "candidate_minus_market": deltas,
            "calibration": {
                model_name: binary_calibration_table(
                    grain,
                    grain[column].to_numpy(float),
                    label_column="label_no",
                ).to_dict("records")
                for model_name, column in columns.items()
            },
        }
    return result


def choose_alpha(development_oof: pd.DataFrame) -> tuple[float, list[dict[str, Any]]]:
    primary = development_oof[
        development_oof["is_state_entry"]
        & development_oof["preferred_weather_p"].notna()
        & development_oof["market_no_p"].notna()
    ].copy()
    if primary.empty or primary["target_date"].nunique() < 3:
        raise RuntimeError("insufficient development OOF state-entry dates for alpha selection")
    rows = []
    for alpha in ALPHA_GRID:
        probability = bounded_market_posterior(
            primary["market_no_p"], primary["preferred_weather_p"], alpha
        )
        score = binary_score(primary, probability, label_column="label_no")
        rows.append({"alpha": alpha, **score})
    winner = min(rows, key=lambda row: (float(row["logloss"]), float(row["alpha"])))
    return float(winner["alpha"]), rows


def _fee(shares: float, price: float) -> float:
    return round(float(shares) * 0.05 * float(price) * (1.0 - float(price)), 5)


def expression_replay(frame: pd.DataFrame, *, probability_column: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    rows = []
    primary = frame[frame["is_state_entry"]].sort_values("decision_ts_utc", kind="stable")
    for (_date, _rung), group in primary.groupby(["target_date", "routine_rung"], sort=False):
        for _, row in group.iterrows():
            probability = finite(row.get(probability_column))
            ask = finite(row.get("no_ask"))
            size = finite(row.get("no_ask_size"))
            label = finite(row.get("label_no"))
            if probability is None or ask is None or size is None or label is None:
                continue
            shares = min(5.0, size)
            if shares <= 0:
                continue
            fee = _fee(shares, ask)
            effective_cost = ask + fee / shares
            edge = probability - effective_cost
            if edge <= 0:
                continue
            cost = shares * ask + fee
            pnl = shares * label - cost
            rows.append(
                {
                    "target_date": str(row["target_date"]),
                    "routine_rung": int(row["routine_rung"]),
                    "decision_ts_utc": pd.Timestamp(row["decision_ts_utc"]).isoformat(),
                    "p_no": probability,
                    "market_no_p": float(row["market_no_p"]),
                    "no_ask": ask,
                    "shares": shares,
                    "fee_usd": fee,
                    "cost_usd": cost,
                    "edge_per_share": edge,
                    "label_no": int(label),
                    "pnl_usd": pnl,
                    "condition_id": row.get("condition_id"),
                    "token_id": row.get("no_token_id"),
                    "feature_book_snapshot_id": row.get("feature_book_snapshot_id"),
                }
            )
            break
    trades = pd.DataFrame(rows)
    if trades.empty:
        return trades, {
            "signals": 0,
            "target_dates": 0,
            "wins": 0,
            "cost_usd": 0.0,
            "pnl_usd": 0.0,
            "roi": None,
            "roi_ci95_target_date_block": None,
            "fill_assumption": "displayed direct NO ask/depth, taker, max 5 shares",
        }
    daily = trades.groupby("target_date")[["pnl_usd", "cost_usd"]].sum().to_numpy(float)
    rng = np.random.default_rng(20260813)
    indexes = rng.integers(0, len(daily), size=(5000, len(daily)))
    sampled = daily[indexes]
    roi = sampled[:, :, 0].sum(axis=1) / sampled[:, :, 1].sum(axis=1)
    cost = float(trades["cost_usd"].sum())
    summary = {
        "signals": int(len(trades)),
        "target_dates": int(trades["target_date"].nunique()),
        "wins": int(trades["label_no"].sum()),
        "cost_usd": cost,
        "pnl_usd": float(trades["pnl_usd"].sum()),
        "roi": float(trades["pnl_usd"].sum() / cost),
        "roi_ci95_target_date_block": [float(value) for value in np.quantile(roi, [0.025, 0.975])],
        "fill_assumption": "displayed direct NO ask/depth, taker, max 5 shares",
    }
    return trades, summary


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def write_jsonl_gz(frame: pd.DataFrame, path: Path) -> None:
    columns = [
        "checkpoint_id",
        "city",
        "target_date",
        "routine_rung",
        "source_observation_ts_utc",
        "source_available_at_utc",
        "decision_ts_utc",
        "raw_ref",
        "winner_bracket",
        "label_no",
        "scorable_status",
        "is_signal_state_entry",
        "is_signal_ten_minute_checkpoint",
        "is_state_entry",
        "is_ten_minute_checkpoint",
        "market_feature_clock",
        "feature_book_snapshot_id",
        "condition_id",
        "no_token_id",
        "source_to_book_lag_seconds",
        "market_no_p",
        "effective_no_ask",
        "effective_no_ask_size",
        "effective_no_ask_source",
        "no_ask",
        "no_ask_size",
        "preferred_weather_p",
        "max_runway_weather_p",
        "preferred_posterior_p",
        "max_runway_posterior_p",
        "split",
        *PREFERRED_FEATURES,
        *[name for name in MAX_RUNWAY_FEATURES if name not in PREFERRED_FEATURES],
    ]
    available = [column for column in dict.fromkeys(columns) if column in frame]
    with path.open("wb") as raw_handle:
        with gzip.GzipFile(fileobj=raw_handle, mode="wb", mtime=0) as gzip_handle:
            with io.TextIOWrapper(gzip_handle, encoding="utf-8") as handle:
                for record in frame[available].to_dict("records"):
                    clean = {
                        key: _json_value(value) for key, value in record.items()
                    }
                    handle.write(stable_json(clean) + "\n")


def run_experiment(
    *,
    config: ExperimentConfig,
    checkpoint_root: Path,
    db_path: Path,
    output_dir: Path,
    code_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    winners, db_identity = load_settlement_winners(
        db_path,
        city=config.city,
        start_date=config.start_date,
        end_date=config.end_date,
    )
    frame, input_manifest = load_checkpoint_frame(
        checkpoint_root,
        city=config.city,
        start_date=config.start_date,
        end_date=config.end_date,
        winners=winners,
    )
    frame["split"] = np.select(
        [
            frame["target_date"].le(config.development_end),
            frame["target_date"].between(config.holdout_start, config.end_date)
            & frame["winner_bracket"].notna(),
        ],
        ["development", "frozen_holdout"],
        default="unsettled_or_coverage",
    )
    scorable = frame[frame["scorable_status"].eq("scorable")].copy()
    development = scorable[scorable["split"].eq("development")].copy()
    holdout = scorable[scorable["split"].eq("frozen_holdout")].copy()
    if development["target_date"].nunique() <= config.warmup_dates:
        raise RuntimeError("development window does not exceed warmup")
    if holdout["target_date"].nunique() < 3:
        raise RuntimeError("frozen holdout needs at least 3 independent dates")

    development["preferred_weather_p"] = expanding_oof_predictions(
        development,
        features=PREFERRED_FEATURES,
        warmup_dates=config.warmup_dates,
    )
    development["max_runway_weather_p"] = expanding_oof_predictions(
        development,
        features=MAX_RUNWAY_FEATURES,
        warmup_dates=config.warmup_dates,
    )
    alpha, alpha_search = choose_alpha(development)
    development["preferred_posterior_p"] = np.where(
        development["preferred_weather_p"].notna(),
        bounded_market_posterior(
            development["market_no_p"],
            development["preferred_weather_p"].fillna(0.5),
            alpha,
        ),
        np.nan,
    )
    development["max_runway_posterior_p"] = np.where(
        development["max_runway_weather_p"].notna(),
        bounded_market_posterior(
            development["market_no_p"],
            development["max_runway_weather_p"].fillna(0.5),
            alpha,
        ),
        np.nan,
    )

    preferred_dev_model = _fit_model(development, PREFERRED_FEATURES)
    max_dev_model = _fit_model(development, MAX_RUNWAY_FEATURES)
    holdout["preferred_weather_p"] = preferred_dev_model.predict_proba(
        holdout[list(PREFERRED_FEATURES)]
    )[:, 1]
    holdout["max_runway_weather_p"] = max_dev_model.predict_proba(
        holdout[list(MAX_RUNWAY_FEATURES)]
    )[:, 1]
    holdout["preferred_posterior_p"] = bounded_market_posterior(
        holdout["market_no_p"], holdout["preferred_weather_p"], alpha
    )
    holdout["max_runway_posterior_p"] = bounded_market_posterior(
        holdout["market_no_p"], holdout["max_runway_weather_p"], alpha
    )

    prediction_columns = [
        "preferred_weather_p",
        "max_runway_weather_p",
        "preferred_posterior_p",
        "max_runway_posterior_p",
    ]
    for column in prediction_columns:
        frame[column] = np.nan
    frame.loc[development.index, prediction_columns] = development[prediction_columns]
    frame.loc[holdout.index, prediction_columns] = holdout[prediction_columns]

    development_scores = score_models(
        development.dropna(subset=["preferred_weather_p"]),
        {
            "market": "market_no_p",
            "preferred_weather_only": "preferred_weather_p",
            "max_runway_weather_only": "max_runway_weather_p",
            "preferred_posterior": "preferred_posterior_p",
            "max_runway_posterior": "max_runway_posterior_p",
        },
        draws=config.bootstrap_draws,
        seed=config.seed,
    )
    holdout_scores = score_models(
        holdout,
        {
            "market": "market_no_p",
            "preferred_weather_only": "preferred_weather_p",
            "max_runway_weather_only": "max_runway_weather_p",
            "preferred_posterior": "preferred_posterior_p",
            "max_runway_posterior": "max_runway_posterior_p",
        },
        draws=config.bootstrap_draws,
        seed=config.seed + 100,
    )
    trades, expression_summary = expression_replay(
        holdout, probability_column="preferred_posterior_p"
    )

    all_labeled = scorable[scorable["winner_bracket"].notna()].copy()
    clean_forward_boundary = (
        pd.Timestamp(config.end_date) + pd.Timedelta(days=1)
    ).date().isoformat()
    preferred_final_model = _fit_model(all_labeled, PREFERRED_FEATURES)
    max_final_model = _fit_model(all_labeled, MAX_RUNWAY_FEATURES)
    parity_sample = all_labeled.tail(min(256, len(all_labeled)))
    before = preferred_final_model.predict_proba(parity_sample[list(PREFERRED_FEATURES)])[:, 1]

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / "model.joblib"
    artifact = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_id": MODEL_ID_BY_CITY[config.city],
        "city": config.city,
        "target_id": "final_exact_current_routine_rung_no",
        "target_kind": "settlement_outcome",
        "market_feature_role": "prior_offset",
        "market_feature_clock": "first_post_event",
        "selected_weather_weight": alpha,
        "preferred_features": list(PREFERRED_FEATURES),
        "max_runway_features": list(MAX_RUNWAY_FEATURES),
        "preferred_model": preferred_final_model,
        "max_runway_model": max_final_model,
        "train_start": config.start_date,
        "train_cutoff": max(all_labeled["target_date"].astype(str)),
        "clean_forward_boundary": clean_forward_boundary,
        "runtime_eligible": False,
        "live_eligible": False,
        "input_inventory_sha256": input_manifest["input_inventory_sha256"],
        "settlement_evidence_sha256": db_identity["settlement_evidence_sha256"],
    }
    joblib.dump(artifact, artifact_path)
    reloaded = joblib.load(artifact_path)
    after = reloaded["preferred_model"].predict_proba(
        parity_sample[list(PREFERRED_FEATURES)]
    )[:, 1]
    parity_max_abs = float(np.max(np.abs(before - after))) if len(before) else 0.0

    predictions_path = output_dir / "predictions.jsonl.gz"
    write_jsonl_gz(frame, predictions_path)
    if not trades.empty:
        trade_records = trades.to_dict("records")
    else:
        trade_records = []
    observed_at = datetime.now(tz=UTC).isoformat()
    raw_rows = int(len(frame))
    signal_state_entries = frame[frame["is_signal_state_entry"]]
    signal_ten_minute = frame[frame["is_signal_ten_minute_checkpoint"]]
    summary = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "observed_at_utc": observed_at,
        "brief": {
            "hypothesis": (
                f"{config.city} preferred-runway source-basis and remaining-heat state "
                "add a bounded probability residual to the same-row market prior for "
                "final exact current-rung NO."
            ),
            "denominator_scope": (
                f"{config.city} grouped AMOS first-seen checkpoints {config.start_date}.."
                f"{config.end_date}; first-post-event exact current-rung book; canonical "
                "settlement label when available"
            ),
            "primary_grain": "first target_date x current routine rung state entry",
            "primary_metric": "target-date-equal logloss delta vs raw market",
            "development_window": [config.start_date, config.development_end],
            "frozen_holdout_window": [config.holdout_start, config.end_date],
            "alpha_grid": list(ALPHA_GRID),
            "multiple_testing_k": len(ALPHA_GRID),
        },
        "identity": {
            "model_id": MODEL_ID_BY_CITY[config.city],
            "target_id": "final_exact_current_routine_rung_no",
            "target_kind": "settlement_outcome",
            "market_feature_role": "prior_offset",
            "market_feature_clock": "first_post_event",
            "code": dict(code_identity or {}),
            "db": db_identity,
            "input": input_manifest,
        },
        "readiness": {
            "pit_state_four_clocks": "READY",
            "canonical_db_build_identity": "READY",
            "market_quote_depth": "READY_WITH_COVERAGE_GAPS",
            "settlement_labels": "READY_THROUGH_" + (max(winners) if winners else "NONE"),
            "independent_target_dates": int(all_labeled["target_date"].nunique()),
            "clean_frozen_forward": "BLOCKED_HISTORICAL_HOLDOUT_ONLY",
            "clean_forward_boundary": clean_forward_boundary,
            "ws_capture": "N/A_NOT_USED",
            "sampling_grain": "READY_DISTINCT_SOURCE_OBSERVATION_WITH_STATE_ENTRY_PRIMARY",
        },
        "signal_funnel": {
            "raw_city_checkpoint_rows": input_manifest["raw_city_rows"],
            "unique_source_event_checkpoints": raw_rows,
            "state_entry_checkpoints": int(len(signal_state_entries)),
            "ten_minute_checkpoints": int(len(signal_ten_minute)),
            "selected_expression_signals_holdout": expression_summary["signals"],
        },
        "evidence_funnel": {
            "pit_source_rows": raw_rows,
            "preferred_runway_rows": int(frame["preferred_runway_temp_c"].notna().sum()),
            "pit_current_rung_market_mid_rows": int(frame["market_no_p"].notna().sum()),
            "settled_label_rows": int(frame["label_no"].notna().sum()),
            "scorable_rows": int(frame["scorable_status"].eq("scorable").sum()),
            "scorable_state_entries": int(
                (frame["scorable_status"].eq("scorable") & frame["is_state_entry"]).sum()
            ),
            "executable_direct_no_rows": int(frame["no_ask"].notna().sum()),
            "actual_fills": 0,
        },
        "coverage_by_status": {
            str(key): int(value) for key, value in frame["scorable_status"].value_counts().items()
        },
        "source_to_book_lag_seconds": {
            "p50": finite(frame["source_to_book_lag_seconds"].quantile(0.50)),
            "p95": finite(frame["source_to_book_lag_seconds"].quantile(0.95)),
            "max": finite(frame["source_to_book_lag_seconds"].max()),
        },
        "preferred_runway_coverage": float(frame["preferred_runway_available"].mean()),
        "selected_weather_weight": alpha,
        "development_alpha_search": alpha_search,
        "development_scores": development_scores,
        "frozen_holdout_scores": holdout_scores,
        "expression": expression_summary,
        "expression_trades": trade_records,
        "artifact": {
            "path": str(artifact_path),
            "sha256": sha256_file(artifact_path),
            "predictions_path": str(predictions_path),
            "predictions_sha256": sha256_file(predictions_path),
            "parity_max_abs_error": parity_max_abs,
            "train_cutoff": artifact["train_cutoff"],
            "clean_forward_boundary": clean_forward_boundary,
            "runtime_eligible": False,
            "live_eligible": False,
        },
        "decision": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "conclusion": "inconclusive_research_artifact",
            "action": "keep Seoul coverage-only; collect clean forward before any runtime adapter",
        },
    }
    primary = holdout_scores.get("state_entry") or {}
    if primary.get("status") == "ok":
        preferred_delta = (
            primary.get("candidate_minus_market", {})
            .get("preferred_posterior", {})
            .get("logloss", {})
        )
        if preferred_delta and float(preferred_delta.get("ci_high", 1.0)) < 0:
            summary["decision"]["significance"] = "PASS_HISTORICAL_HOLDOUT"
            summary["decision"]["baseline"] = "PASS_HISTORICAL_HOLDOUT"
            summary["decision"]["conclusion"] = "research_candidate_clean_forward_required"
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    summary["summary_sha256"] = sha256_file(summary_path)
    return summary


__all__ = [
    "ALPHA_GRID",
    "ExperimentConfig",
    "MAX_RUNWAY_FEATURES",
    "PREFERRED_FEATURES",
    "add_preferred_path_features",
    "bounded_market_posterior",
    "effective_exact_quote",
    "load_checkpoint_frame",
    "run_experiment",
]
