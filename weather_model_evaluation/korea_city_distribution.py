"""Settlement-native Korea-city remaining-heat distribution research.

The physical head is trained on the long routine-observation history.  AMOS
preferred-runway state is only a PIT feature adapter; market data never enters
the weather fit.  A coherent market posterior is evaluated separately with a
coefficient-one market prior and a bounded weather likelihood-ratio weight.

This module is research-only.  It does not emit a SignalCandidate,
TradeIntent, order, or live authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import gzip
import io
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .korea_city_exact_no import (
    _iter_jsonl,
    effective_exact_quote,
    finite,
    load_checkpoint_frame,
    load_settlement_winners,
    parse_utc,
    sha256_file,
    stable_hash,
    stable_json,
)
from .probability import (
    date_block_bootstrap_delta,
    ordinal_loss_values,
    ordinal_score,
)


CLASSES = ("negative", "zero", "plus1", "plus2", "plus3")
CLASS_TO_INDEX = {name: index for index, name in enumerate(CLASSES)}
PHYSICAL_FEATURES = (
    "local_hour_sin",
    "local_hour_cos",
    "distance_below_running_max_c",
    "minutes_since_running_max",
    "delta_1h_c",
    "relative_humidity_pct",
    "dewpoint_depression_c",
    "day_of_year_sin",
    "day_of_year_cos",
)
ALPHA_GRID = (0.0, 0.125, 0.25, 0.5, 1.0)
OUTPUT_SCHEMA_VERSION = "korea_city_remaining_heat_distribution_research_v1"
ARTIFACT_SCHEMA_VERSION = "korea_city_remaining_heat_distribution_artifact_v1"


@dataclass(frozen=True)
class DistributionExperimentConfig:
    city: str
    physical_train_end: str
    physical_holdout_start: str
    physical_holdout_end: str
    amos_start: str
    development_end: str
    holdout_start: str
    end_date: str
    bootstrap_draws: int = 5000
    seed: int = 20260813
    market_tail_max_age_minutes: int = 40

    def __post_init__(self) -> None:
        if self.city != "Seoul":
            raise ValueError("distribution v1 is intentionally Seoul-specific")
        if not (
            self.physical_train_end < self.physical_holdout_start
            <= self.physical_holdout_end < self.amos_start
            <= self.development_end < self.holdout_start <= self.end_date
        ):
            raise ValueError("invalid chronological split")
        if self.bootstrap_draws < 100:
            raise ValueError("bootstrap_draws must be at least 100")


def _winner_value(value: Any) -> int | None:
    text = str(value or "").strip().casefold()
    if not text:
        return None
    digits = "".join(character for character in text if character.isdigit() or character == "-")
    try:
        return int(digits)
    except ValueError:
        return None


def remaining_heat_class(winner: Any, routine_rung: Any) -> str | None:
    winner_value = _winner_value(winner)
    rung = finite(routine_rung)
    if winner_value is None or rung is None or not float(rung).is_integer():
        return None
    delta = winner_value - int(rung)
    if delta < 0:
        return "negative"
    if delta == 0:
        return "zero"
    if delta == 1:
        return "plus1"
    if delta == 2:
        return "plus2"
    return "plus3"


def _date_equal_weights(frame: pd.DataFrame) -> np.ndarray:
    dates = frame["target_date"].astype(str)
    counts = dates.map(dates.value_counts()).to_numpy(float)
    return (1.0 / counts) / np.mean(1.0 / counts)


def _make_model() -> Pipeline:
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
                            list(PHYSICAL_FEATURES),
                        )
                    ],
                    remainder="drop",
                ),
            ),
            (
                "model",
                LogisticRegression(
                    C=0.1,
                    solver="lbfgs",
                    max_iter=3000,
                    random_state=20260813,
                ),
            ),
        ]
    )


def _fit_model(frame: pd.DataFrame) -> Pipeline:
    observed = sorted(frame["label_class"].dropna().unique())
    if observed != sorted(CLASSES):
        raise RuntimeError(f"physical training lacks classes: {observed}")
    model = _make_model()
    model.fit(
        frame[list(PHYSICAL_FEATURES)],
        frame["label_class"],
        model__sample_weight=_date_equal_weights(frame),
    )
    return model


def _predict_aligned(model: Pipeline, frame: pd.DataFrame) -> np.ndarray:
    raw = model.predict_proba(frame[list(PHYSICAL_FEATURES)])
    classes = model.named_steps["model"].classes_
    output = np.zeros((len(frame), len(CLASSES)), dtype=float)
    for source_index, label in enumerate(classes):
        output[:, CLASS_TO_INDEX[str(label)]] = raw[:, source_index]
    output = np.clip(output, 1e-8, None)
    return output / output.sum(axis=1, keepdims=True)


def load_physical_states(path: Path, *, city: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = pd.read_csv(path)
    frame = frame[frame["city"].eq(city)].copy()
    frame["label_class"] = frame["label"].astype(str)
    frame["label_index"] = frame["label_class"].map(CLASS_TO_INDEX)
    frame["local_hour_sin"] = np.sin(2.0 * np.pi * frame["local_hour"] / 24.0)
    frame["local_hour_cos"] = np.cos(2.0 * np.pi * frame["local_hour"] / 24.0)
    frame["distance_below_running_max_c"] = frame["running_max_c"] - frame["temp_c"]
    if frame["label_index"].isna().any():
        raise RuntimeError("unknown physical target class")
    stat = path.stat()
    identity = {
        "path": str(path),
        "sha256": sha256_file(path),
        "size_bytes": int(stat.st_size),
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        "lineage": "IEM historical observation-time proxy; first-seen unknown",
    }
    return frame.reset_index(drop=True), identity


def _physical_prior(frame: pd.DataFrame) -> np.ndarray:
    weights = _date_equal_weights(frame)
    counts = np.zeros(len(CLASSES), dtype=float)
    for index in range(len(CLASSES)):
        counts[index] = weights[frame["label_index"].to_numpy(int) == index].sum()
    counts = np.clip(counts, 1e-8, None)
    return counts / counts.sum()


def _amos_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    day = pd.to_datetime(output["target_date"]).dt.dayofyear.astype(float)
    output["day_of_year_sin"] = np.sin(2.0 * np.pi * day / 365.25)
    output["day_of_year_cos"] = np.cos(2.0 * np.pi * day / 365.25)
    output["distance_below_running_max_c"] = output[
        "preferred_distance_below_running_max_c"
    ]
    output["minutes_since_running_max"] = output[
        "preferred_minutes_since_running_max"
    ]
    # The 60-minute path slope is in C/hour, so on the regular AMOS path it is
    # the closest PIT-safe analogue to the IEM one-hour temperature delta.
    output["delta_1h_c"] = output["preferred_slope_60m_c_per_hour"]
    output["label_class"] = [
        remaining_heat_class(winner, rung)
        for winner, rung in zip(output["winner_bracket"], output["routine_rung"])
    ]
    output["label_index"] = output["label_class"].map(CLASS_TO_INDEX)
    output = output[output["local_hour"].between(9.0, 17.999999)].copy()
    decision = pd.to_datetime(output["decision_ts_utc"], utc=True)
    output["ten_minute_bin_utc"] = decision.dt.floor("10min")
    output["is_raw_state_entry"] = ~output.duplicated(
        ["target_date", "routine_rung"], keep="first"
    )
    output["is_raw_ten_minute_checkpoint"] = ~output.duplicated(
        ["target_date", "routine_rung", "ten_minute_bin_utc"], keep="first"
    )
    return output


def _effective_yes_quote(books: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not books:
        return None
    bracket = str(books[0].get("bracket") or "")
    condition = str(books[0].get("condition_id") or "")
    rung = _winner_value(bracket)
    if rung is None:
        return None
    # ``effective_exact_quote`` deliberately rejects a ``+`` bracket because
    # its original caller needs an exact rung.  Here the upper boundary is a
    # legitimate mutually-exclusive event outcome, so normalize only the
    # temporary lookup label and retain the native bracket in the result.
    normalized_books = [{**book, "bracket": str(rung)} for book in books]
    quote = effective_exact_quote(normalized_books, rung)
    if quote["market_yes_mid"] is None:
        return None
    return {
        "bracket": bracket,
        "bracket_value": rung,
        "condition_id": condition,
        "yes_mid": float(quote["market_yes_mid"]),
        "yes_ask": finite(quote["market_yes_ask"]),
        "yes_ask_size": finite(quote["yes_ask_size"]),
        "snapshot_id": quote["feature_book_snapshot_id"],
    }


def _snapshot_distribution(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    by_condition: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if str(row.get("status") or "") != "ok":
            continue
        condition = str(row.get("condition_id") or "")
        if condition:
            by_condition.setdefault(condition, []).append(row)
    outcomes = []
    for condition_rows in by_condition.values():
        quote = _effective_yes_quote(condition_rows)
        if quote is not None:
            outcomes.append(quote)
    if len(outcomes) < 5:
        return None
    outcomes.sort(key=lambda item: (int(item["bracket_value"]), item["bracket"]))
    raw = np.asarray([item["yes_mid"] for item in outcomes], dtype=float)
    raw_sum = float(raw.sum())
    if not 0.5 <= raw_sum <= 1.5:
        return None
    probabilities = raw / raw_sum
    for item, probability in zip(outcomes, probabilities):
        item["normalized_yes_p"] = float(probability)
    timestamps = [
        parse_utc(row.get("available_at_utc") or row.get("fetched_at_utc") or row.get("snapshot_ts_utc"))
        for row in rows
    ]
    timestamps = [value for value in timestamps if value is not None]
    if not timestamps:
        return None
    return {
        "market_available_at_utc": max(timestamps),
        "event_slug": str(rows[0].get("event_slug") or ""),
        "target_date": str(rows[0].get("event_date") or rows[0].get("market_local_date") or ""),
        "outcomes": outcomes,
        "raw_probability_sum": raw_sum,
        "base_snapshot_id": stable_hash(
            [
                {
                    "condition_id": item["condition_id"],
                    "snapshot_id": item["snapshot_id"],
                    "yes_mid": item["yes_mid"],
                }
                for item in outcomes
            ]
        ),
    }


def load_full_ladder_snapshots(
    root: Path, *, city: str, start_date: str, end_date: str
) -> tuple[pd.DataFrame, dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    for day in sorted(root.glob("????-??-??")):
        if not start_date <= day.name <= end_date:
            continue
        for path in sorted(day.glob("*.jsonl.gz")):
            stat = path.stat()
            inventory.append(
                {
                    "path": str(path),
                    "size_bytes": int(stat.st_size),
                    "mtime_ns": int(stat.st_mtime_ns),
                }
            )
            grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
            try:
                handle: Iterable[str]
                with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as handle:
                    for line in handle:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if row.get("city") != city:
                            continue
                        target_date = str(
                            row.get("event_date") or row.get("market_local_date") or ""
                        )
                        if not start_date <= target_date <= end_date:
                            continue
                        key = (target_date, str(row.get("event_slug") or ""))
                        grouped.setdefault(key, []).append(row)
            except (OSError, EOFError):
                continue
            for rows in grouped.values():
                snapshot = _snapshot_distribution(rows)
                if snapshot is not None:
                    snapshot["raw_ref"] = str(path)
                    snapshots.append(snapshot)
    if not snapshots:
        raise RuntimeError("no complete Seoul market ladder snapshots")
    frame = pd.DataFrame(snapshots)
    frame["market_available_at_utc"] = pd.to_datetime(
        frame["market_available_at_utc"], utc=True
    )
    frame = frame.sort_values(
        ["target_date", "market_available_at_utc"], kind="stable"
    ).drop_duplicates(["target_date", "market_available_at_utc"], keep="last")
    identity = {
        "root": str(root),
        "files": len(inventory),
        "inventory_sha256": stable_hash(inventory),
        "snapshot_rows": int(len(frame)),
        "target_dates": int(frame["target_date"].nunique()),
        "content_identity_basis": "ordered path,size,mtime_ns plus selected snapshot hashes",
    }
    return frame.reset_index(drop=True), identity


def load_active_book_overlays(
    checkpoint_root: Path, *, source_event_keys: set[str]
) -> dict[str, list[dict[str, Any]]]:
    overlays: dict[str, list[dict[str, Any]]] = {}
    if not source_event_keys:
        return overlays
    for path in sorted(checkpoint_root.glob("????-??-??.jsonl")):
        for _line_number, row in _iter_jsonl(path):
            key = str(row.get("source_event_key") or "")
            if key in source_event_keys:
                overlays[key] = list((row.get("market_capture") or {}).get("books") or [])
    return overlays


def aggregate_market_distribution(
    outcomes: Sequence[Mapping[str, Any]],
    *,
    routine_rung: int,
    overlay_books: Sequence[Mapping[str, Any]] = (),
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Map a complete exact-bracket ladder to remaining-heat classes."""

    by_condition = {str(item["condition_id"]): dict(item) for item in outcomes}
    overlay_grouped: dict[str, list[Mapping[str, Any]]] = {}
    for book in overlay_books:
        condition = str(book.get("condition_id") or "")
        if condition:
            overlay_grouped.setdefault(condition, []).append(book)
    overlay_count = 0
    for condition, books in overlay_grouped.items():
        quote = _effective_yes_quote(books)
        if quote is not None and condition in by_condition:
            by_condition[condition].update(quote)
            overlay_count += 1
    ordered = sorted(
        by_condition.values(), key=lambda item: (int(item["bracket_value"]), item["bracket"])
    )
    if not ordered:
        return None, {"status": "missing_market_outcomes"}
    raw = np.asarray([float(item["yes_mid"]) for item in ordered], dtype=float)
    if not np.isfinite(raw).all() or (raw < 0).any() or raw.sum() <= 0:
        return None, {"status": "invalid_market_probabilities"}
    raw /= raw.sum()
    result = np.zeros(len(CLASSES), dtype=float)
    present_values = {int(item["bracket_value"]) for item in ordered}
    needed = {routine_rung, routine_rung + 1, routine_rung + 2}
    if not needed.issubset(present_values):
        return None, {
            "status": "market_partition_missing_exact_center",
            "missing": sorted(needed - present_values),
        }
    for item, probability in zip(ordered, raw):
        value = int(item["bracket_value"])
        if value < routine_rung:
            index = 0
        elif value == routine_rung:
            index = 1
        elif value == routine_rung + 1:
            index = 2
        elif value == routine_rung + 2:
            index = 3
        else:
            index = 4
        result[index] += float(probability)
    if (result <= 0).any():
        return None, {"status": "market_partition_empty_class"}
    result /= result.sum()
    return result, {
        "status": "scorable",
        "active_overlay_conditions": overlay_count,
        "market_outcomes": len(ordered),
        "feature_book_snapshot_id": stable_hash(
            {
                "routine_rung": routine_rung,
                "outcomes": [
                    [item["condition_id"], item["yes_mid"]] for item in ordered
                ],
            }
        ),
    }


def join_market_prior(
    frame: pd.DataFrame,
    snapshots: pd.DataFrame,
    *,
    overlays: Mapping[str, Sequence[Mapping[str, Any]]],
    max_age_minutes: int,
) -> pd.DataFrame:
    pieces = []
    tolerance = pd.Timedelta(minutes=max_age_minutes)
    for target_date, group in frame.groupby("target_date", sort=True):
        market = snapshots[snapshots["target_date"].eq(str(target_date))]
        local = group.sort_values("source_available_at_utc", kind="stable").copy()
        if market.empty:
            local["market_join_status"] = "missing_full_ladder_date"
            pieces.append(local)
            continue
        # The periodic full ladder must already have been available before the
        # AMOS point.  The active five-rung capture is overlaid after the point.
        joined = pd.merge_asof(
            local,
            market.sort_values("market_available_at_utc", kind="stable"),
            left_on="source_available_at_utc",
            right_on="market_available_at_utc",
            direction="backward",
            tolerance=tolerance,
            suffixes=("", "_market"),
        )
        joined["market_join_status"] = np.where(
            joined["outcomes"].notna(), "joined_pre_event_tail", "missing_recent_full_ladder"
        )
        pieces.append(joined)
    output = pd.concat(pieces, ignore_index=True)
    market_values: list[np.ndarray | None] = []
    statuses: list[str] = []
    identities: list[str | None] = []
    overlay_counts: list[int] = []
    outcome_counts: list[int] = []
    for _, row in output.iterrows():
        if not isinstance(row.get("outcomes"), list):
            market_values.append(None)
            statuses.append(str(row.get("market_join_status") or "missing_full_ladder"))
            identities.append(None)
            overlay_counts.append(0)
            outcome_counts.append(0)
            continue
        probability, metadata = aggregate_market_distribution(
            row["outcomes"],
            routine_rung=int(row["routine_rung"]),
            overlay_books=overlays.get(str(row["source_event_key"]), ()),
        )
        market_values.append(probability)
        status = str(metadata["status"])
        if status == "scorable" and int(metadata.get("active_overlay_conditions", 0)) < 5:
            status = "missing_first_post_active_overlay"
        statuses.append(status)
        identities.append(metadata.get("feature_book_snapshot_id"))
        overlay_counts.append(int(metadata.get("active_overlay_conditions", 0)))
        outcome_counts.append(int(metadata.get("market_outcomes", 0)))
    output["market_distribution"] = market_values
    output["market_join_status"] = statuses
    output["feature_book_snapshot_id_distribution"] = identities
    output["active_overlay_conditions"] = overlay_counts
    output["market_outcomes"] = outcome_counts
    output["market_tail_age_seconds"] = (
        pd.to_datetime(output["source_available_at_utc"], utc=True)
        - pd.to_datetime(output["market_available_at_utc"], utc=True)
    ).dt.total_seconds()
    return output


def geometric_market_posterior(
    market: np.ndarray,
    weather: np.ndarray,
    climatology: np.ndarray,
    alpha: float,
) -> np.ndarray:
    market_value = np.clip(np.asarray(market, dtype=float), 1e-8, None)
    weather_value = np.clip(np.asarray(weather, dtype=float), 1e-8, None)
    prior_value = np.clip(np.asarray(climatology, dtype=float), 1e-8, None)
    log_score = np.log(market_value) + float(alpha) * (
        np.log(weather_value) - np.log(prior_value)[None, :]
    )
    log_score -= log_score.max(axis=1, keepdims=True)
    output = np.exp(log_score)
    return output / output.sum(axis=1, keepdims=True)


def _score(frame: pd.DataFrame, probabilities: np.ndarray) -> dict[str, Any]:
    return ordinal_score(frame, probabilities, label_column="label_index")


def _score_grains(
    frame: pd.DataFrame,
    candidates: Mapping[str, np.ndarray],
    *,
    bootstrap_draws: int,
    seed: int,
    baseline_name: str | None = None,
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    memberships = {
        "state_entry": frame["is_raw_state_entry"].astype(bool).to_numpy(),
        "ten_minute": frame["is_raw_ten_minute_checkpoint"].astype(bool).to_numpy(),
    }
    for grain_name, membership in memberships.items():
        grain = frame.loc[membership].copy()
        if grain.empty:
            output[grain_name] = {"status": "empty"}
            continue
        probabilities = {name: value[membership] for name, value in candidates.items()}
        scores = {name: _score(grain, value) for name, value in probabilities.items()}
        deltas: dict[str, Any] = {}
        if baseline_name is not None:
            baseline = probabilities[baseline_name]
            for name, value in probabilities.items():
                if name == baseline_name:
                    continue
                deltas[name] = {}
                for metric in ("brier", "logloss", "rps"):
                    deltas[name][metric] = date_block_bootstrap_delta(
                        grain,
                        ordinal_loss_values(grain["label_index"], value, metric=metric),
                        ordinal_loss_values(grain["label_index"], baseline, metric=metric),
                        draws=bootstrap_draws,
                        seed=seed + {"brier": 0, "logloss": 1, "rps": 2}[metric],
                    )
        output[grain_name] = {
            "status": "ok",
            "rows": int(len(grain)),
            "target_dates": int(grain["target_date"].nunique()),
            "scores": scores,
            "candidate_minus_baseline": deltas,
        }
    return output


def _json_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, np.ndarray):
        return [float(item) for item in value]
    return value


def _write_predictions(frame: pd.DataFrame, path: Path) -> None:
    columns = [
        "checkpoint_id",
        "city",
        "target_date",
        "source_event_key",
        "source_observation_ts_utc",
        "source_available_at_utc",
        "decision_ts_utc",
        "routine_rung",
        "winner_bracket",
        "label_class",
        "label_index",
        "split",
        "is_raw_state_entry",
        "is_raw_ten_minute_checkpoint",
        "weather_distribution",
        "market_distribution",
        "posterior_distribution",
        "market_join_status",
        "market_available_at_utc",
        "market_tail_age_seconds",
        "active_overlay_conditions",
        "market_outcomes",
        "feature_book_snapshot_id_distribution",
        *PHYSICAL_FEATURES,
    ]
    available = [column for column in columns if column in frame]
    with path.open("wb") as raw_handle:
        with gzip.GzipFile(fileobj=raw_handle, mode="wb", mtime=0) as gzip_handle:
            with io.TextIOWrapper(gzip_handle, encoding="utf-8") as handle:
                for record in frame[available].to_dict("records"):
                    handle.write(
                        stable_json({key: _json_value(value) for key, value in record.items()})
                        + "\n"
                    )


def run_distribution_experiment(
    *,
    config: DistributionExperimentConfig,
    physical_states_path: Path,
    checkpoint_root: Path,
    market_batches_root: Path,
    db_path: Path,
    output_dir: Path,
    code_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    physical, physical_identity = load_physical_states(
        physical_states_path, city=config.city
    )
    physical_train = physical[
        physical["target_date"].le(config.physical_train_end)
    ].copy()
    physical_holdout = physical[
        physical["target_date"].between(
            config.physical_holdout_start, config.physical_holdout_end
        )
    ].copy()
    if physical_train["target_date"].nunique() < 30:
        raise RuntimeError("physical training needs at least 30 target dates")
    if physical_holdout["target_date"].nunique() < 7:
        raise RuntimeError("physical holdout needs at least 7 target dates")
    physical_prior = _physical_prior(physical_train)
    physical_model = _fit_model(physical_train)
    physical_prediction = _predict_aligned(physical_model, physical_holdout)
    physical_prior_prediction = np.repeat(
        physical_prior[None, :], len(physical_holdout), axis=0
    )
    physical_scores = _score_grains(
        physical_holdout.assign(
            is_raw_state_entry=True, is_raw_ten_minute_checkpoint=True
        ),
        {"train_climatology": physical_prior_prediction, "weather": physical_prediction},
        bootstrap_draws=config.bootstrap_draws,
        seed=config.seed,
        baseline_name="train_climatology",
    )["state_entry"]

    winners, db_identity = load_settlement_winners(
        db_path,
        city=config.city,
        start_date=config.amos_start,
        end_date=config.end_date,
    )
    checkpoint_frame, checkpoint_identity = load_checkpoint_frame(
        checkpoint_root,
        city=config.city,
        start_date=config.amos_start,
        end_date=config.end_date,
        winners=winners,
    )
    amos = _amos_feature_frame(checkpoint_frame)
    amos["split"] = np.select(
        [
            amos["target_date"].le(config.development_end),
            amos["target_date"].between(config.holdout_start, config.end_date),
        ],
        ["development", "frozen_holdout"],
        default="outside_split",
    )
    labeled = amos[amos["label_index"].notna()].copy()
    labeled["label_index"] = labeled["label_index"].astype(int)
    labeled["weather_distribution"] = list(_predict_aligned(physical_model, labeled))

    market_snapshots, market_identity = load_full_ladder_snapshots(
        market_batches_root,
        city=config.city,
        start_date=config.amos_start,
        end_date=config.end_date,
    )
    market_candidate = labeled[
        labeled["is_raw_state_entry"] | labeled["is_raw_ten_minute_checkpoint"]
    ].copy()
    overlays = load_active_book_overlays(
        checkpoint_root,
        source_event_keys=set(market_candidate["source_event_key"].astype(str)),
    )
    joined = join_market_prior(
        market_candidate,
        market_snapshots,
        overlays=overlays,
        max_age_minutes=config.market_tail_max_age_minutes,
    )
    market_scorable = joined[
        joined["market_join_status"].eq("scorable")
        & joined["market_distribution"].notna()
    ].copy()
    development_market = market_scorable[
        market_scorable["split"].eq("development")
    ].copy()
    holdout_market = market_scorable[
        market_scorable["split"].eq("frozen_holdout")
    ].copy()
    if development_market["target_date"].nunique() < 3:
        raise RuntimeError("market alpha development needs at least 3 dates")
    if holdout_market["target_date"].nunique() < 4:
        raise RuntimeError("market frozen holdout needs at least 4 dates")

    development_weather = np.stack(development_market["weather_distribution"])
    development_market_p = np.stack(development_market["market_distribution"])
    alpha_search = []
    state_mask = development_market["is_raw_state_entry"].to_numpy(bool)
    if development_market.loc[state_mask, "target_date"].nunique() < 3:
        raise RuntimeError("market alpha state-entry development needs at least 3 dates")
    for alpha in ALPHA_GRID:
        posterior = geometric_market_posterior(
            development_market_p, development_weather, physical_prior, alpha
        )
        score = _score(development_market.loc[state_mask], posterior[state_mask])
        alpha_search.append({"alpha": alpha, **score})
    selected_alpha = float(
        min(
            alpha_search,
            key=lambda row: (float(row["multiclass_logloss"]), float(row["alpha"])),
        )["alpha"]
    )
    development_market["posterior_distribution"] = list(
        geometric_market_posterior(
            development_market_p, development_weather, physical_prior, selected_alpha
        )
    )
    holdout_weather = np.stack(holdout_market["weather_distribution"])
    holdout_market_p = np.stack(holdout_market["market_distribution"])
    holdout_market["posterior_distribution"] = list(
        geometric_market_posterior(
            holdout_market_p, holdout_weather, physical_prior, selected_alpha
        )
    )
    posterior_by_checkpoint = {
        str(row["checkpoint_id"]): row["posterior_distribution"]
        for _, row in pd.concat(
            [development_market, holdout_market], ignore_index=True
        ).iterrows()
    }
    joined["posterior_distribution"] = [
        posterior_by_checkpoint.get(str(checkpoint_id))
        for checkpoint_id in joined["checkpoint_id"]
    ]

    amos_weather_scores = {}
    for split, split_frame in labeled.groupby("split", sort=True):
        if split not in {"development", "frozen_holdout"}:
            continue
        prediction = np.stack(split_frame["weather_distribution"])
        prior = np.repeat(physical_prior[None, :], len(split_frame), axis=0)
        amos_weather_scores[split] = _score_grains(
            split_frame,
            {"physical_climatology": prior, "weather_transfer": prediction},
            bootstrap_draws=config.bootstrap_draws,
            seed=config.seed + (10 if split == "development" else 20),
            baseline_name="physical_climatology",
        )
    market_scores = {
        "development": _score_grains(
            development_market,
            {
                "market": development_market_p,
                "weather": development_weather,
                "posterior": np.stack(development_market["posterior_distribution"]),
            },
            bootstrap_draws=config.bootstrap_draws,
            seed=config.seed + 30,
            baseline_name="market",
        ),
        "frozen_holdout": _score_grains(
            holdout_market,
            {
                "market": holdout_market_p,
                "weather": holdout_weather,
                "posterior": np.stack(holdout_market["posterior_distribution"]),
            },
            bootstrap_draws=config.bootstrap_draws,
            seed=config.seed + 40,
            baseline_name="market",
        ),
    }

    final_model = _fit_model(physical)
    parity_sample = physical.tail(min(256, len(physical)))
    parity_before = _predict_aligned(final_model, parity_sample)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / "model.joblib"
    artifact = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "model_id": "seoul_intraday_remaining_heat_distribution",
        "city": config.city,
        "target_id": "final_settlement_remaining_heat_from_current_routine_rung",
        "target_kind": "settlement_outcome",
        "classes": list(CLASSES),
        "features": list(PHYSICAL_FEATURES),
        "physical_model": final_model,
        "physical_climatology": physical_prior,
        "selected_market_likelihood_ratio_weight": selected_alpha,
        "market_feature_role": "coefficient_one_prior",
        "market_feature_clock": "pre_event_full_ladder_plus_first_post_active_overlay",
        "physical_train_cutoff": str(physical["target_date"].max()),
        "historical_holdout_end": config.end_date,
        "clean_forward_boundary": (
            pd.Timestamp(config.end_date) + pd.Timedelta(days=1)
        ).date().isoformat(),
        "runtime_eligible": False,
        "live_eligible": False,
        "physical_input_sha256": physical_identity["sha256"],
        "checkpoint_inventory_sha256": checkpoint_identity["input_inventory_sha256"],
        "market_inventory_sha256": market_identity["inventory_sha256"],
        "settlement_evidence_sha256": db_identity["settlement_evidence_sha256"],
    }
    joblib.dump(artifact, artifact_path)
    reloaded = joblib.load(artifact_path)
    parity_after = _predict_aligned(reloaded["physical_model"], parity_sample)
    parity_error = float(np.max(np.abs(parity_before - parity_after)))

    output_predictions = labeled.copy()
    output_predictions["market_distribution"] = None
    output_predictions["posterior_distribution"] = None
    output_predictions["market_join_status"] = "not_market_grain"
    output_predictions["market_available_at_utc"] = pd.NaT
    output_predictions["market_tail_age_seconds"] = np.nan
    output_predictions["active_overlay_conditions"] = 0
    output_predictions["market_outcomes"] = 0
    output_predictions["feature_book_snapshot_id_distribution"] = None
    joined_by_checkpoint = joined.set_index("checkpoint_id")
    indexed = output_predictions.set_index("checkpoint_id")
    for column in (
        "market_distribution",
        "posterior_distribution",
        "market_join_status",
        "market_available_at_utc",
        "market_tail_age_seconds",
        "active_overlay_conditions",
        "market_outcomes",
        "feature_book_snapshot_id_distribution",
    ):
        lookup = joined_by_checkpoint[column].to_dict()
        indexed[column] = [
            lookup.get(str(checkpoint_id), current)
            for checkpoint_id, current in zip(indexed.index, indexed[column])
        ]
    output_predictions = indexed.reset_index()
    predictions_path = output_dir / "predictions.jsonl.gz"
    _write_predictions(output_predictions, predictions_path)

    coverage_status = {
        str(key): int(value)
        for key, value in joined["market_join_status"].value_counts().items()
    }
    summary: dict[str, Any] = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "observed_at_utc": datetime.now(tz=UTC).isoformat(),
        "brief": {
            "hypothesis": (
                "A Seoul-only long-history remaining-heat distribution transfers to "
                "preferred-runway AMOS state and can add a strongly shrunk likelihood "
                "ratio to a coefficient-one complete-ladder market prior."
            ),
            "denominator_scope": (
                f"Seoul IEM hourly physical states through {config.physical_holdout_end}; "
                f"AMOS grouped first-seen checkpoints {config.amos_start}..{config.end_date}; "
                "complete event-ladder pre-event prior with first-post active-rung overlay"
            ),
            "classes": list(CLASSES),
            "physical_train_end": config.physical_train_end,
            "physical_holdout_window": [
                config.physical_holdout_start,
                config.physical_holdout_end,
            ],
            "amos_development_window": [config.amos_start, config.development_end],
            "amos_frozen_holdout_window": [config.holdout_start, config.end_date],
            "primary_grain": "first target_date x current routine rung state entry",
            "primary_metric": "target-date-equal multiclass logloss delta vs market",
            "alpha_grid": list(ALPHA_GRID),
            "multiple_testing_k": len(ALPHA_GRID),
        },
        "identity": {
            "model_id": artifact["model_id"],
            "target_id": artifact["target_id"],
            "target_kind": artifact["target_kind"],
            "code": dict(code_identity or {}),
            "physical_input": physical_identity,
            "checkpoints": checkpoint_identity,
            "market": market_identity,
            "db": db_identity,
        },
        "readiness": {
            "physical_history": "READY_NON_FIRST_SEEN_PHYSICAL_ONLY",
            "amos_four_clocks": "READY",
            "settlement_labels": "READY_THROUGH_" + max(winners),
            "market_full_ladder": "READY_PRE_EVENT_TAIL_WITH_FIRST_POST_ACTIVE_OVERLAY",
            "synchronized_full_post_event_ladder": "BLOCKED_NOT_CAPTURED_FOR_HISTORICAL_WINDOW",
            "clean_frozen_forward": "BLOCKED_HISTORICAL_HOLDOUT_ONLY",
            "clean_forward_boundary": artifact["clean_forward_boundary"],
        },
        "physical_funnel": {
            "source_rows_all_cities": int(pd.read_csv(physical_states_path, usecols=["city"]).shape[0]),
            "seoul_rows": int(len(physical)),
            "seoul_target_dates": int(physical["target_date"].nunique()),
            "train_rows": int(len(physical_train)),
            "train_dates": int(physical_train["target_date"].nunique()),
            "holdout_rows": int(len(physical_holdout)),
            "holdout_dates": int(physical_holdout["target_date"].nunique()),
        },
        "signal_funnel": {
            "raw_city_checkpoint_rows": checkpoint_identity["raw_city_rows"],
            "unique_checkpoint_rows": checkpoint_identity["unique_checkpoint_rows"],
            "local_09_18_rows": int(len(amos)),
            "settled_rows": int(len(labeled)),
            "raw_state_entries": int(labeled["is_raw_state_entry"].sum()),
            "raw_ten_minute_checkpoints": int(
                labeled["is_raw_ten_minute_checkpoint"].sum()
            ),
            "expression_signals": 0,
        },
        "evidence_funnel": {
            "market_grain_rows": int(len(joined)),
            "market_scorable_rows": int(len(market_scorable)),
            "market_scorable_dates": int(market_scorable["target_date"].nunique()),
            "market_scorable_state_entries": int(
                market_scorable["is_raw_state_entry"].sum()
            ),
            "frozen_market_rows": int(len(holdout_market)),
            "frozen_market_dates": int(holdout_market["target_date"].nunique()),
            "frozen_market_state_entries": int(
                holdout_market["is_raw_state_entry"].sum()
            ),
            "actual_fills": 0,
        },
        "market_coverage_by_status": coverage_status,
        "market_tail_age_seconds": {
            "p50": finite(market_scorable["market_tail_age_seconds"].quantile(0.50)),
            "p95": finite(market_scorable["market_tail_age_seconds"].quantile(0.95)),
            "max": finite(market_scorable["market_tail_age_seconds"].max()),
        },
        "active_overlay_conditions": {
            "p50": finite(market_scorable["active_overlay_conditions"].quantile(0.50)),
            "p95": finite(market_scorable["active_overlay_conditions"].quantile(0.95)),
        },
        "physical_climatology": {
            label: float(physical_prior[index]) for index, label in enumerate(CLASSES)
        },
        "physical_holdout_scores": physical_scores,
        "amos_weather_scores": amos_weather_scores,
        "development_alpha_search": alpha_search,
        "selected_weather_likelihood_ratio_weight": selected_alpha,
        "market_scores": market_scores,
        "expression": {
            "status": "not_estimable",
            "reason": (
                "historical full ladder supplies a pre-event tail and only the active "
                "five rungs have first-post-event asks; synchronized full-post-event "
                "class-to-exact expression is unavailable"
            ),
            "signals": 0,
            "actual_fills": 0,
        },
        "artifact": {
            "path": str(artifact_path),
            "sha256": sha256_file(artifact_path),
            "predictions_path": str(predictions_path),
            "predictions_sha256": sha256_file(predictions_path),
            "parity_max_abs_error": parity_error,
            "runtime_eligible": False,
            "live_eligible": False,
            "clean_forward_boundary": artifact["clean_forward_boundary"],
        },
        "decision": {
            "significance": "FAIL",
            "baseline": "FAIL",
            "forward": "FAIL",
            "conclusion": "inconclusive_research_artifact",
            "action": (
                "keep Seoul coverage-only; accumulate synchronized full-post-event "
                "ladder and at least 30 new settled clean-forward dates"
            ),
        },
    }
    primary = (
        market_scores.get("frozen_holdout", {})
        .get("state_entry", {})
        .get("candidate_minus_baseline", {})
        .get("posterior", {})
    )
    if primary and all(
        float(primary[metric]["ci_high"]) < 0
        for metric in ("brier", "logloss", "rps")
    ):
        summary["decision"]["significance"] = "PASS_HISTORICAL_HOLDOUT"
        summary["decision"]["baseline"] = "PASS_HISTORICAL_HOLDOUT"
        summary["decision"]["conclusion"] = "research_candidate_clean_forward_required"
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary["summary_path"] = str(summary_path)
    summary["summary_sha256"] = sha256_file(summary_path)
    return summary


__all__ = [
    "ALPHA_GRID",
    "CLASSES",
    "DistributionExperimentConfig",
    "PHYSICAL_FEATURES",
    "aggregate_market_distribution",
    "geometric_market_posterior",
    "remaining_heat_class",
    "run_distribution_experiment",
]
