"""City-agnostic grain builders and proper probability scores.

The shared contract is intentionally thin:

* city adapters produce a PIT prediction table;
* these helpers construct checkpoint, state-transition and state-entry grains;
* every score is averaged within target date before dates are averaged;
* target-date bootstrap resamples whole dates;
* ordered outcomes retain logloss/Brier and add ranked probability score;
* discrete conditional hazards are converted into one coherent event-time
  distribution before scoring.

No function in this module selects a trade, reads a market, or mutates runtime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


EPS = 1e-8
BinaryMetric = Literal["brier", "logloss"]
OrdinalMetric = Literal["brier", "logloss", "rps"]


def _require_columns(frame: pd.DataFrame, columns: Sequence[str]) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")


def _sorted_frame(
    frame: pd.DataFrame,
    *,
    group_columns: Sequence[str],
    time_column: str,
) -> pd.DataFrame:
    _require_columns(frame, [*group_columns, time_column])
    result = frame.copy()
    result[time_column] = pd.to_datetime(result[time_column], utc=True)
    return result.sort_values(
        [*group_columns, time_column], kind="stable"
    ).reset_index(drop=True)


def build_checkpoint_grain(
    frame: pd.DataFrame,
    *,
    group_columns: Sequence[str],
    time_column: str = "decision_ts_utc",
) -> pd.DataFrame:
    """Return one row per group and decision checkpoint.

    Exact duplicate keys are invalid rather than silently aggregated because
    they generally indicate mixed model/source lineage.
    """

    result = _sorted_frame(
        frame, group_columns=group_columns, time_column=time_column
    )
    key = [*group_columns, time_column]
    duplicated = result.duplicated(key, keep=False)
    if duplicated.any():
        sample = result.loc[duplicated, key].head(5).to_dict("records")
        raise ValueError(f"duplicate checkpoint keys: {sample}")
    result["evaluation_grain"] = "checkpoint"
    return result


def build_transition_grain(
    frame: pd.DataFrame,
    *,
    group_columns: Sequence[str],
    state_columns: Sequence[str],
    time_column: str = "decision_ts_utc",
) -> pd.DataFrame:
    """Keep the first row and every later row where configured state changes."""

    if not state_columns:
        raise ValueError("state_columns must not be empty")
    result = build_checkpoint_grain(
        frame, group_columns=group_columns, time_column=time_column
    )
    _require_columns(result, state_columns)
    changed = pd.Series(False, index=result.index)
    group_key: Any = (
        group_columns[0]
        if len(group_columns) == 1
        else list(group_columns)
    )
    for _group, index in result.groupby(
        group_key, sort=False, dropna=False
    ).groups.items():
        positions = list(index)
        if not positions:
            continue
        changed.loc[positions[0]] = True
        current = result.loc[positions, list(state_columns)].astype(
            "string"
        )
        local_change = current.ne(current.shift()).any(axis=1)
        changed.loc[positions] = local_change.to_numpy()
        changed.loc[positions[0]] = True
    output = result.loc[changed].copy().reset_index(drop=True)
    output["evaluation_grain"] = "transition"
    return output


def build_state_entry_grain(
    frame: pd.DataFrame,
    *,
    group_columns: Sequence[str],
    state_columns: Sequence[str],
    time_column: str = "decision_ts_utc",
) -> pd.DataFrame:
    """Keep the first PIT row for every distinct state within a group.

    For remaining-heat research this is normally one row per
    ``city × target_date × confirmed current X``.  It does not depend on model
    edge, price or realized outcome.
    """

    if not state_columns:
        raise ValueError("state_columns must not be empty")
    result = build_checkpoint_grain(
        frame, group_columns=group_columns, time_column=time_column
    )
    _require_columns(result, state_columns)
    output = result.drop_duplicates(
        [*group_columns, *state_columns], keep="first"
    ).copy()
    output = output.reset_index(drop=True)
    output["evaluation_grain"] = "state_entry"
    return output


def composite_grain_weights(
    frame: pd.DataFrame,
    *,
    membership_columns: Sequence[str],
    date_column: str = "target_date",
) -> np.ndarray:
    """Equal-weight checkpoint and configured grain memberships by date.

    The checkpoint component gives every row weight ``1 / rows_in_date``.
    Each membership component independently gives its marked rows total weight
    one per date.  Components are averaged 1:1:... and the final vector is
    normalized to mean one for estimator compatibility.

    Membership must be determined without price, model edge or settlement
    outcome, for example ``is_transition`` and ``is_state_entry``.
    """

    _require_columns(frame, [date_column, *membership_columns])
    dates = frame[date_column].astype(str)
    components = []
    checkpoint_count = dates.map(dates.value_counts()).to_numpy(dtype=float)
    components.append(1.0 / checkpoint_count)
    for column in membership_columns:
        member = frame[column].astype(bool)
        count = (
            dates.loc[member]
            .value_counts()
        )
        weight = np.zeros(len(frame), dtype=float)
        if member.any():
            member_dates = dates.loc[member]
            weight[member.to_numpy()] = member_dates.map(
                lambda value: 1.0 / count[value]
            ).to_numpy(dtype=float)
        components.append(weight)
    combined = np.mean(np.column_stack(components), axis=1)
    if not np.isfinite(combined).all() or combined.sum() <= 0:
        raise ValueError("invalid composite grain weights")
    return combined / combined.mean()


def binary_loss_values(
    label: Sequence[float] | np.ndarray,
    probability: Sequence[float] | np.ndarray,
    *,
    metric: BinaryMetric,
) -> np.ndarray:
    y = np.asarray(label, dtype=float)
    p = np.clip(np.asarray(probability, dtype=float), EPS, 1 - EPS)
    if y.shape != p.shape:
        raise ValueError(f"shape mismatch label={y.shape} p={p.shape}")
    if not np.isin(y, [0.0, 1.0]).all():
        raise ValueError("binary labels must be 0/1")
    if metric == "brier":
        return (p - y) ** 2
    if metric == "logloss":
        return -(y * np.log(p) + (1 - y) * np.log(1 - p))
    raise ValueError(f"unsupported binary metric: {metric}")


def ordinal_loss_values(
    label: Sequence[int] | np.ndarray,
    probabilities: np.ndarray,
    *,
    metric: OrdinalMetric,
) -> np.ndarray:
    y = np.asarray(label, dtype=int)
    q = np.asarray(probabilities, dtype=float)
    if q.ndim != 2 or q.shape[0] != len(y):
        raise ValueError(f"invalid probability shape {q.shape}")
    if ((y < 0) | (y >= q.shape[1])).any():
        raise ValueError("ordinal label outside probability classes")
    if not np.isfinite(q).all() or (q < 0).any():
        raise ValueError("probabilities must be finite and non-negative")
    row_sum = q.sum(axis=1)
    if not np.allclose(row_sum, 1.0, atol=1e-6):
        raise ValueError("probability rows must sum to one")
    q = np.clip(q, EPS, None)
    q /= q.sum(axis=1, keepdims=True)
    one_hot = np.eye(q.shape[1])[y]
    if metric == "logloss":
        return -np.log(q[np.arange(len(y)), y])
    if metric == "brier":
        return np.mean((q - one_hot) ** 2, axis=1)
    if metric == "rps":
        return np.mean(
            (
                np.cumsum(q, axis=1)[:, :-1]
                - np.cumsum(one_hot, axis=1)[:, :-1]
            )
            ** 2,
            axis=1,
        )
    raise ValueError(f"unsupported ordinal metric: {metric}")


def _date_equal_mean(
    frame: pd.DataFrame,
    values: Sequence[float] | np.ndarray,
    *,
    date_column: str,
) -> float:
    _require_columns(frame, [date_column])
    value = np.asarray(values, dtype=float)
    if len(value) != len(frame):
        raise ValueError("value length does not match frame")
    daily = pd.DataFrame(
        {
            "target_date": frame[date_column].astype(str).to_numpy(),
            "value": value,
        }
    ).groupby("target_date", sort=True)["value"].mean()
    return float(daily.mean())


def _date_equal_weights(
    frame: pd.DataFrame,
    *,
    date_column: str,
) -> np.ndarray:
    _require_columns(frame, [date_column])
    dates = frame[date_column].astype(str)
    counts = dates.map(dates.value_counts()).to_numpy(dtype=float)
    return 1.0 / counts


def binary_calibration_table(
    frame: pd.DataFrame,
    probability: Sequence[float] | np.ndarray,
    *,
    label_column: str,
    date_column: str = "target_date",
    bins: int = 10,
) -> pd.DataFrame:
    """Fixed-width reliability table with target-date-equal row weights."""

    if bins < 2:
        raise ValueError("bins must be at least 2")
    _require_columns(frame, [label_column, date_column])
    y = frame[label_column].astype(int).to_numpy()
    p = np.clip(np.asarray(probability, dtype=float), 0.0, 1.0)
    if len(p) != len(frame):
        raise ValueError("probability length does not match frame")
    weight = _date_equal_weights(frame, date_column=date_column)
    bin_id = np.minimum((p * bins).astype(int), bins - 1)
    rows = []
    for index in range(bins):
        member = bin_id == index
        if not member.any():
            continue
        local_weight = weight[member]
        mean_probability = float(
            np.average(p[member], weights=local_weight)
        )
        observed_rate = float(
            np.average(y[member], weights=local_weight)
        )
        rows.append(
            {
                "bin": int(index),
                "bin_low": float(index / bins),
                "bin_high": float((index + 1) / bins),
                "rows": int(member.sum()),
                "target_dates": int(
                    frame.loc[member, date_column].astype(str).nunique()
                ),
                "date_equal_weight": float(local_weight.sum()),
                "mean_probability": mean_probability,
                "observed_rate": observed_rate,
                "calibration_gap": observed_rate - mean_probability,
            }
        )
    return pd.DataFrame(rows)


def binary_score(
    frame: pd.DataFrame,
    probability: Sequence[float] | np.ndarray,
    *,
    label_column: str,
    date_column: str = "target_date",
) -> dict[str, float | int]:
    _require_columns(frame, [label_column, date_column])
    y = frame[label_column].astype(int).to_numpy()
    p = np.asarray(probability, dtype=float)
    weight = _date_equal_weights(frame, date_column=date_column)
    calibration = binary_calibration_table(
        frame,
        p,
        label_column=label_column,
        date_column=date_column,
    )
    calibration_weight = calibration["date_equal_weight"].to_numpy()
    calibration_gap = calibration["calibration_gap"].to_numpy()
    return {
        "rows": int(len(frame)),
        "target_dates": int(frame[date_column].astype(str).nunique()),
        "positive_rate": _date_equal_mean(
            frame, y, date_column=date_column
        ),
        "brier": _date_equal_mean(
            frame,
            binary_loss_values(y, p, metric="brier"),
            date_column=date_column,
        ),
        "logloss": _date_equal_mean(
            frame,
            binary_loss_values(y, p, metric="logloss"),
            date_column=date_column,
        ),
        "threshold_accuracy": _date_equal_mean(
            frame, (p >= 0.5) == y, date_column=date_column
        ),
        "auc": (
            float(roc_auc_score(y, p, sample_weight=weight))
            if np.unique(y).size == 2
            else float("nan")
        ),
        "calibration_ece_10": float(
            np.average(
                np.abs(calibration_gap), weights=calibration_weight
            )
        ),
    }


def ordinal_score(
    frame: pd.DataFrame,
    probabilities: np.ndarray,
    *,
    label_column: str,
    date_column: str = "target_date",
) -> dict[str, float | int]:
    _require_columns(frame, [label_column, date_column])
    y = frame[label_column].astype(int).to_numpy()
    q = np.asarray(probabilities, dtype=float)
    predicted = np.argmax(q, axis=1)
    return {
        "rows": int(len(frame)),
        "target_dates": int(frame[date_column].astype(str).nunique()),
        "multiclass_brier": _date_equal_mean(
            frame,
            ordinal_loss_values(y, q, metric="brier"),
            date_column=date_column,
        ),
        "multiclass_logloss": _date_equal_mean(
            frame,
            ordinal_loss_values(y, q, metric="logloss"),
            date_column=date_column,
        ),
        "ranked_probability_score": _date_equal_mean(
            frame,
            ordinal_loss_values(y, q, metric="rps"),
            date_column=date_column,
        ),
        "exact_accuracy": _date_equal_mean(
            frame, predicted == y, date_column=date_column
        ),
        "within_one_accuracy": _date_equal_mean(
            frame, np.abs(predicted - y) <= 1, date_column=date_column
        ),
    }


def integrated_horizon_score(
    frame: pd.DataFrame,
    probabilities: np.ndarray,
    *,
    label_columns: Sequence[str],
    date_column: str = "target_date",
) -> dict[str, float | int]:
    """Average binary proper loss across horizons, rows, then target dates."""

    _require_columns(frame, [*label_columns, date_column])
    p = np.asarray(probabilities, dtype=float)
    if p.shape != (len(frame), len(label_columns)):
        raise ValueError(
            f"expected {(len(frame), len(label_columns))}, got {p.shape}"
        )
    y = frame[list(label_columns)].astype(int).to_numpy()
    brier = np.mean((np.clip(p, 0, 1) - y) ** 2, axis=1)
    clipped = np.clip(p, EPS, 1 - EPS)
    logloss = np.mean(
        -(y * np.log(clipped) + (1 - y) * np.log(1 - clipped)),
        axis=1,
    )
    return {
        "rows": int(len(frame)),
        "target_dates": int(frame[date_column].astype(str).nunique()),
        "horizons": int(len(label_columns)),
        "integrated_brier": _date_equal_mean(
            frame, brier, date_column=date_column
        ),
        "integrated_logloss": _date_equal_mean(
            frame, logloss, date_column=date_column
        ),
    }


def event_bin_from_cumulative_labels(labels: np.ndarray) -> np.ndarray:
    """Map monotone cumulative event labels to interval/event-free class.

    For K cumulative horizons the result has K+1 classes:
    first event in interval 0..K-1, or class K for no event by final horizon.
    """

    value = np.asarray(labels, dtype=int)
    if value.ndim != 2:
        raise ValueError("cumulative labels must be two-dimensional")
    if not np.isin(value, [0, 1]).all():
        raise ValueError("cumulative labels must be 0/1")
    if (np.diff(value, axis=1) < 0).any():
        raise ValueError("cumulative labels must be non-decreasing")
    event = np.full(len(value), value.shape[1], dtype=int)
    has_event = value[:, -1].astype(bool)
    event[has_event] = np.argmax(value[has_event] == 1, axis=1)
    return event


def hazards_to_event_probabilities(hazards: np.ndarray) -> np.ndarray:
    """Convert conditional interval hazards to event-time/no-event simplex."""

    h = np.clip(np.asarray(hazards, dtype=float), EPS, 1 - EPS)
    if h.ndim != 2:
        raise ValueError("hazards must be two-dimensional")
    survival = np.ones(len(h), dtype=float)
    bins = []
    for index in range(h.shape[1]):
        event_probability = survival * h[:, index]
        bins.append(event_probability)
        survival = survival * (1 - h[:, index])
    output = np.column_stack([*bins, survival])
    output /= output.sum(axis=1, keepdims=True)
    return output


def event_probabilities_to_cumulative(
    event_probabilities: np.ndarray,
) -> np.ndarray:
    """Convert K interval + no-event probabilities to K cumulative horizons."""

    q = np.asarray(event_probabilities, dtype=float)
    if q.ndim != 2 or q.shape[1] < 2:
        raise ValueError("event probabilities need interval and no-event bins")
    if not np.allclose(q.sum(axis=1), 1.0, atol=1e-6):
        raise ValueError("event probability rows must sum to one")
    return np.cumsum(q[:, :-1], axis=1)


def date_block_bootstrap_delta(
    frame: pd.DataFrame,
    candidate_loss: Sequence[float] | np.ndarray,
    baseline_loss: Sequence[float] | np.ndarray,
    *,
    date_column: str = "target_date",
    draws: int = 4000,
    seed: int = 20260731,
) -> dict[str, float | int]:
    """Paired target-date bootstrap of candidate minus baseline loss."""

    _require_columns(frame, [date_column])
    candidate = np.asarray(candidate_loss, dtype=float)
    baseline = np.asarray(baseline_loss, dtype=float)
    if candidate.shape != baseline.shape or len(candidate) != len(frame):
        raise ValueError("loss arrays must match frame rows")
    daily = pd.DataFrame(
        {
            "target_date": frame[date_column].astype(str).to_numpy(),
            "delta": candidate - baseline,
        }
    ).groupby("target_date", sort=True)["delta"].mean()
    values = daily.to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    sampled = rng.choice(
        values, size=(draws, len(values)), replace=True
    ).mean(axis=1)
    return {
        "delta": float(values.mean()),
        "ci_low": float(np.quantile(sampled, 0.025)),
        "ci_high": float(np.quantile(sampled, 0.975)),
        "draws": int(draws),
        "target_dates": int(len(values)),
    }


def composite_grain_date_bootstrap_delta(
    grains: Mapping[
        str,
        tuple[
            pd.DataFrame,
            Sequence[float] | np.ndarray,
            Sequence[float] | np.ndarray,
        ],
    ],
    *,
    grain_weights: Mapping[str, float] | None = None,
    date_column: str = "target_date",
    draws: int = 4000,
    seed: int = 20260731,
) -> dict[str, float | int]:
    """Bootstrap a fixed weighted composite of grain-level daily deltas.

    Each grain is reduced to one paired candidate-minus-baseline loss delta
    per target date before the grains are combined.  All grains must cover the
    same dates; silently taking an intersection would change the denominator.
    """

    if not grains:
        raise ValueError("grains must not be empty")
    names = list(grains)
    if grain_weights is None:
        weights = {name: 1.0 for name in names}
    else:
        if set(grain_weights) != set(names):
            raise ValueError("grain_weights must match grain names exactly")
        weights = {name: float(grain_weights[name]) for name in names}
    if any(not np.isfinite(value) or value < 0 for value in weights.values()):
        raise ValueError("grain weights must be finite and non-negative")
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        raise ValueError("grain weights must sum to a positive value")

    daily_by_grain: dict[str, pd.Series] = {}
    expected_dates: pd.Index | None = None
    for name, (frame, candidate_loss, baseline_loss) in grains.items():
        _require_columns(frame, [date_column])
        candidate = np.asarray(candidate_loss, dtype=float)
        baseline = np.asarray(baseline_loss, dtype=float)
        if candidate.shape != baseline.shape or len(candidate) != len(frame):
            raise ValueError(f"loss arrays must match {name} frame rows")
        daily = pd.DataFrame(
            {
                "target_date": frame[date_column].astype(str).to_numpy(),
                "delta": candidate - baseline,
            }
        ).groupby("target_date", sort=True)["delta"].mean()
        if expected_dates is None:
            expected_dates = daily.index
        elif not daily.index.equals(expected_dates):
            raise ValueError("all grains must cover identical target dates")
        daily_by_grain[name] = daily

    assert expected_dates is not None
    values = sum(
        daily_by_grain[name].to_numpy(dtype=float) * weights[name]
        for name in names
    ) / weight_sum
    rng = np.random.default_rng(seed)
    sampled = rng.choice(
        values, size=(draws, len(values)), replace=True
    ).mean(axis=1)
    return {
        "delta": float(values.mean()),
        "ci_low": float(np.quantile(sampled, 0.025)),
        "ci_high": float(np.quantile(sampled, 0.975)),
        "draws": int(draws),
        "target_dates": int(len(values)),
        "grains": int(len(names)),
    }
