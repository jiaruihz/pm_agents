"""Tmax distribution v3: full-ladder hazard-chain probability model.

This module is the versioned model package for ``tmax_distribution_v3``:

- feature layer contract (pre-registered layers and columns, no per-city
  free parameters);
- discrete competing-risk hazard chain over the absolute bracket ladder
  (``P(reach k+1 | reached k)`` / ``P(stop at k | reached k)``), with an
  explicit smoothed ``below`` head for settlement-basis mismatch and a
  geometric continuation hazard for the open tail;
- cross-hour temporal coherence: the previous posterior is conditioned on
  the observed "broke / did not break" evidence (anchor movement) before it
  is pooled with the new fit, so the hourly posterior cannot flip
  arbitrarily against its own history;
- market fusion by log-linear pool with the pre-registered alpha grid;
- probability artifact schema and model card builder.

The package deliberately contains no I/O against live systems.  Fit/predict
consume already-materialized PIT state rows (see
``scripts/analysis/reheat_risk/research_tmax_distribution_v3_model_v1.py``)
whose every feature satisfies ``available_at_utc <= decision_ts_utc``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.strategies.weather_edge_v1.tools.tmax_feature_contract_v2 import FEATURE_CONTRACT_VERSION

MODEL_VERSION = "tmax_distribution_v3"
EPS = 1e-9
FEE_RATE = 0.05

BUCKETS = ("below", "current", "d1", "d2", "tail")

# Pre-registered fusion grid: weight on the market prior in the log-linear
# pool.  Selected per walk-forward training window by proper score only.
ALPHA_CANDIDATES = (0.0, 0.25, 0.5, 0.75, 1.0)
# Pre-registered temporal-coherence grid: weight on the conditioned previous
# posterior.  Selected on the training window by proper score only.
COHERENCE_CANDIDATES = (0.0, 0.25, 0.5)
RECALIBRATION_C_CANDIDATES = (0.01, 0.03, 0.1)
MARKET_LOG_FEATURES = tuple(f"log_market_p_{bucket}" for bucket in BUCKETS)
RECAL_PATH_FEATURES = (
    "forecast_gap_to_running_f",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "forecast_peak_delta_hours_local",
    "max_age_min",
    "remaining_heat_integral_f",
    "forecast_curve_available",
)

# ---------------------------------------------------------------------------
# Feature layer contract.  Columns must already be point-in-time safe in the
# state table; the model never recomputes or backfills them.
# ---------------------------------------------------------------------------
FEATURE_LAYERS: dict[str, tuple[str, ...]] = {
    "market_prior": (
        "market_p_below",
        "market_p_current",
        "market_p_d1",
        "market_p_d2",
        "market_p_tail",
        "market_entropy",
        "ladder_overround",
        "quoted_rung_fraction",
        "current_yes_spread",
        "d1_yes_spread",
        "d2_yes_spread",
        "log_depth_min_anchor",
        "rungs_above_count",
        "boundary_pos_in_bracket",
    ),
    "path_energy": (
        "forecast_gap_to_running_f",
        "current_minus_running_f",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
        "max_age_min",
        "minutes_since_running_max",
        "forecast_peak_delta_hours_local",
        "forecast_ceiling_margin_f",
        "remaining_heat_integral_f",
        "forecast_curve_available",
        "tracking_residual_f",
        "obs_count_today",
    ),
    "source_calibration": (
        "source_bias_shrunk_f",
        "source_mae_shrunk_f",
        "family_source_bias_shrunk_f",
        "unit_is_c",
        "source_is_ecmwf",
    ),
    # Archive-reconstructed weather context (humidity/cloud/wind/dewpoint) is
    # NOT strict live-first-seen lineage.  Routes using this layer are an
    # upper bound and must never enter a live artifact.
    "weather_mechanism": (
        "relative_humidity_pct",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "sky_cover_num",
        "mech_available",
        "rh_x_hours_to_peak",
        "cloud_x_hours_to_peak",
        "wind_x_coastal",
        "solar_noon_distance_h",
    ),
}

ROUTE_SPECS: dict[str, dict[str, Any]] = {
    "market_recal_global": {
        "layers": ("market_prior",),
        "fusion": "constrained_multinomial_recalibration",
        "coherence": False,
        "lineage": "strict_pit",
        "model_kind": "market_recal_global",
    },
    "market_recal_path": {
        "layers": ("market_prior", "path_energy"),
        "fusion": "constrained_multinomial_recalibration",
        "coherence": False,
        "lineage": "strict_pit",
        "model_kind": "market_recal_path",
    },
    "weather_only": {
        "layers": ("path_energy", "source_calibration"),
        "fusion": "fixed_alpha_0",
        "coherence": False,
        "lineage": "strict_pit",
    },
    "market_path": {
        "layers": ("market_prior", "path_energy"),
        "fusion": "train_select",
        "coherence": False,
        "lineage": "strict_pit",
    },
    "market_path_source": {
        "layers": ("market_prior", "path_energy", "source_calibration"),
        "fusion": "train_select",
        "coherence": False,
        "lineage": "strict_pit",
    },
    "strict_pit_full": {
        "layers": ("market_prior", "path_energy", "source_calibration"),
        "fusion": "train_select",
        "coherence": True,
        "lineage": "strict_pit",
    },
    "archive_upper_bound": {
        "layers": (
            "market_prior",
            "path_energy",
            "source_calibration",
            "weather_mechanism",
        ),
        "fusion": "train_select",
        "coherence": True,
        "lineage": "archive_reconstructed_upper_bound",
    },
}

RECALIBRATION_ROUTES = ("market_recal_global", "market_recal_path")


def route_feature_columns(route: str) -> list[str]:
    spec = ROUTE_SPECS[route]
    out: list[str] = []
    for layer in spec["layers"]:
        out.extend(FEATURE_LAYERS[layer])
    return out


def official_fee(price: float) -> float:
    return FEE_RATE * price * (1.0 - price)


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _binary_pipeline(feature_columns: list[str], c_value: float) -> Pipeline:
    return Pipeline(
        [
            (
                "features",
                ColumnTransformer(
                    [
                        (
                            "num",
                            Pipeline(
                                [
                                    ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                                    ("scale", StandardScaler()),
                                ]
                            ),
                            feature_columns,
                        )
                    ]
                ),
            ),
            ("clf", LogisticRegression(C=c_value, max_iter=1000)),
        ]
    )


@dataclass
class _HazardHead:
    """One conditional hazard ``P(continue | at risk)`` with explicit fallback."""

    name: str
    pipeline: Pipeline | None = None
    fallback_rate: float = math.nan
    train_rows: int = 0
    mode: str = "unfit"

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        if self.mode == "model" and self.pipeline is not None:
            return np.clip(self.pipeline.predict_proba(frame)[:, 1], EPS, 1.0 - EPS)
        if self.mode == "rate" and math.isfinite(self.fallback_rate):
            return np.full(len(frame), np.clip(self.fallback_rate, EPS, 1.0 - EPS))
        raise RuntimeError(f"hazard head {self.name} is unfit; refuse to guess")


@dataclass
class TmaxHazardChainV3:
    """Discrete hazard chain over relative rung steps.

    Labels come from ``winner_step = winner_rung_index - anchor_rung_index``:
    ``<0`` below (settlement-basis mismatch), ``0`` stop at current,
    ``1`` d1, ``2`` d2, ``>=3`` tail.  Heads:

    - ``h0`` = P(step >= 1 | step >= 0)
    - ``h1`` = P(step >= 2 | step >= 1)
    - ``h2`` = P(step >= 3 | step >= 2)
    - ``h_cont`` = pooled geometric continuation hazard inside the tail
    - ``p_below`` = Laplace-smoothed train frequency (explicit basis head)
    """

    feature_columns: list[str]
    c_value: float = 0.1
    min_head_rows: int = 30
    below_laplace: tuple[float, float] = (0.5, 25.0)
    heads: dict[str, _HazardHead] = field(default_factory=dict)
    p_below: float = math.nan
    h_cont: float = math.nan
    train_rows: int = 0
    train_dates: int = 0
    train_through: str | None = None

    def fit(self, frame: pd.DataFrame) -> "TmaxHazardChainV3":
        if "winner_step" not in frame.columns:
            raise ValueError("training frame must contain winner_step")
        steps = frame["winner_step"].astype(int)
        self.train_rows = len(frame)
        self.train_dates = int(frame["target_date"].nunique())
        self.train_through = str(frame["target_date"].max())
        a, b = self.below_laplace
        self.p_below = float((int((steps < 0).sum()) + a) / (len(frame) + b))
        not_below = frame[steps >= 0]
        specs = [
            ("h0", not_below, (not_below["winner_step"] >= 1)),
            ("h1", not_below[not_below["winner_step"] >= 1], None),
            ("h2", not_below[not_below["winner_step"] >= 2], None),
        ]
        for name, subset, target in specs:
            if target is None:
                threshold = {"h1": 2, "h2": 3}[name]
                target = subset["winner_step"] >= threshold
            head = _HazardHead(name=name, train_rows=len(subset))
            positives = int(target.sum())
            if len(subset) >= self.min_head_rows and 0 < positives < len(subset):
                pipeline = _binary_pipeline(self.feature_columns, self.c_value)
                pipeline.fit(subset, target.astype(int))
                head.pipeline = pipeline
                head.mode = "model"
            elif len(subset) > 0:
                head.fallback_rate = (positives + 0.5) / (len(subset) + 1.0)
                head.mode = "rate"
            else:
                head.fallback_rate = 0.05
                head.mode = "rate"
            self.heads[name] = head
        # Pooled geometric continuation hazard inside the tail with shrinkage.
        tail_steps = steps[steps >= 3] - 3
        at_risk = float(tail_steps.size + tail_steps.sum())
        continuations = float(tail_steps.sum())
        self.h_cont = float((continuations + 1.0) / (at_risk + 4.0))
        return self

    def predict_five_bucket(self, frame: pd.DataFrame) -> pd.DataFrame:
        h0 = self.heads["h0"].predict(frame)
        h1 = self.heads["h1"].predict(frame)
        h2 = self.heads["h2"].predict(frame)
        stay = 1.0 - self.p_below
        out = pd.DataFrame(index=frame.index)
        out["p_below"] = self.p_below
        out["p_current"] = stay * (1.0 - h0)
        out["p_d1"] = stay * h0 * (1.0 - h1)
        out["p_d2"] = stay * h0 * h1 * (1.0 - h2)
        out["p_tail"] = stay * h0 * h1 * h2
        total = out.sum(axis=1)
        assert np.allclose(total, 1.0, atol=1e-8), "hazard chain must sum to 1"
        out["h0"], out["h1"], out["h2"] = h0, h1, h2
        out["h_cont"] = self.h_cont
        return out

    def tail_rung_split(self, p_tail: float, n_tail_rungs: int) -> list[float]:
        """Distribute tail mass over d3.. rungs; last rung absorbs the rest."""
        if n_tail_rungs <= 0:
            return []
        if n_tail_rungs == 1:
            return [p_tail]
        h = float(np.clip(self.h_cont, EPS, 1.0 - EPS))
        masses = [p_tail * (1.0 - h) * (h**k) for k in range(n_tail_rungs - 1)]
        masses.append(max(0.0, p_tail - sum(masses)))
        return masses

    def describe(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "feature_columns": list(self.feature_columns),
            "c_value": self.c_value,
            "train_rows": self.train_rows,
            "train_dates": self.train_dates,
            "train_through": self.train_through,
            "p_below": self.p_below,
            "h_cont": self.h_cont,
            "heads": {
                name: {"mode": head.mode, "train_rows": head.train_rows}
                for name, head in self.heads.items()
            },
        }


def _market_recalibration_frame(frame: pd.DataFrame, path_features: tuple[str, ...]) -> pd.DataFrame:
    out = frame.copy()
    for bucket in BUCKETS:
        values = pd.to_numeric(out[f"market_p_{bucket}"], errors="coerce")
        out[f"log_market_p_{bucket}"] = np.log(values.clip(EPS, 1.0))
    for column in path_features:
        if column not in out:
            out[column] = math.nan
    return out


@dataclass
class ConstrainedMarketRecalibratorV3:
    """Strongly regularized global market recalibration with no city terms."""

    include_path: bool = False
    c_value: float = 0.03
    pipeline: Pipeline | None = None
    classes_: tuple[str, ...] = ()
    class_prior_: np.ndarray | None = None
    h_cont: float = 0.3
    train_rows: int = 0
    train_dates: int = 0
    train_through: str | None = None

    @property
    def feature_columns(self) -> list[str]:
        return [*MARKET_LOG_FEATURES, *(RECAL_PATH_FEATURES if self.include_path else ())]

    def fit(self, frame: pd.DataFrame) -> "ConstrainedMarketRecalibratorV3":
        valid = frame[frame[[f"market_p_{b}" for b in BUCKETS]].notna().all(axis=1)].copy()
        if valid.empty or valid["actual_bucket"].nunique() < 2:
            raise ValueError("market recalibrator needs complete market probabilities and >=2 classes")
        transformed = _market_recalibration_frame(valid, RECAL_PATH_FEATURES if self.include_path else ())
        self.pipeline = _binary_pipeline(self.feature_columns, self.c_value)
        self.pipeline.set_params(clf=LogisticRegression(C=self.c_value, max_iter=2000, solver="lbfgs"))
        self.pipeline.fit(transformed, valid["actual_bucket"].astype(str))
        self.classes_ = tuple(str(value) for value in self.pipeline.named_steps["clf"].classes_)
        counts = valid["actual_bucket"].value_counts()
        self.class_prior_ = np.asarray([(counts.get(bucket, 0) + 0.5) for bucket in BUCKETS], dtype=float)
        self.class_prior_ /= self.class_prior_.sum()
        steps = pd.to_numeric(valid.get("winner_step"), errors="coerce").dropna().astype(int)
        tail_steps = steps[steps >= 3] - 3
        at_risk = float(tail_steps.size + tail_steps.sum())
        self.h_cont = float((float(tail_steps.sum()) + 1.0) / (at_risk + 4.0)) if at_risk >= 0 else 0.3
        self.train_rows = len(valid)
        self.train_dates = int(valid["target_date"].nunique())
        self.train_through = str(valid["target_date"].max())
        return self

    def predict_five_bucket(self, frame: pd.DataFrame) -> pd.DataFrame:
        if self.pipeline is None or self.class_prior_ is None:
            raise RuntimeError("market recalibrator is unfit")
        transformed = _market_recalibration_frame(frame, RECAL_PATH_FEATURES if self.include_path else ())
        complete = transformed[[f"market_p_{b}" for b in BUCKETS]].notna().all(axis=1).to_numpy()
        probs = np.tile(self.class_prior_, (len(frame), 1))
        if complete.any():
            raw = self.pipeline.predict_proba(transformed.loc[complete])
            for position, bucket in enumerate(BUCKETS):
                if bucket in self.classes_:
                    probs[complete, position] = raw[:, self.classes_.index(bucket)]
                else:
                    probs[complete, position] = EPS
        probs = np.clip(probs, EPS, None)
        probs /= probs.sum(axis=1, keepdims=True)
        out = pd.DataFrame({f"p_{bucket}": probs[:, i] for i, bucket in enumerate(BUCKETS)}, index=frame.index)
        non_below = np.maximum(EPS, 1.0 - probs[:, 0])
        reach_d1 = probs[:, 2] + probs[:, 3] + probs[:, 4]
        reach_d2 = probs[:, 3] + probs[:, 4]
        out["h0"] = np.clip(reach_d1 / non_below, EPS, 1.0 - EPS)
        out["h1"] = np.clip(reach_d2 / np.maximum(EPS, reach_d1), EPS, 1.0 - EPS)
        out["h2"] = np.clip(probs[:, 4] / np.maximum(EPS, reach_d2), EPS, 1.0 - EPS)
        out["h_cont"] = self.h_cont
        return out

    def describe(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "model_kind": "market_recal_path" if self.include_path else "market_recal_global",
            "feature_columns": self.feature_columns,
            "c_value": self.c_value,
            "train_rows": self.train_rows,
            "train_dates": self.train_dates,
            "train_through": self.train_through,
            "h_cont": self.h_cont,
        }


# ---------------------------------------------------------------------------
# Fusion and temporal coherence
# ---------------------------------------------------------------------------

def fuse_log_linear(
    model_probs: np.ndarray, market_probs: np.ndarray, alpha: float
) -> np.ndarray:
    """Log-linear pool; ``alpha`` is the market weight.  Rows renormalized.

    Missing market rows (any non-finite value) fall back to the model
    distribution — a missing rung/ladder is never treated as probability 0.
    """
    model_arr = np.clip(np.asarray(model_probs, dtype=float), EPS, 1.0)
    market_arr = np.asarray(market_probs, dtype=float)
    ok = np.isfinite(market_arr).all(axis=1)
    fused = model_arr.copy()
    if alpha > 0 and ok.any():
        mk = np.clip(market_arr[ok], EPS, 1.0)
        pooled = np.exp((1.0 - alpha) * np.log(model_arr[ok]) + alpha * np.log(mk))
        fused[ok] = pooled
    fused /= fused.sum(axis=1, keepdims=True)
    return fused


def condition_posterior_on_anchor_shift(
    prev_probs: np.ndarray, anchor_shift: int, h_cont: float
) -> np.ndarray:
    """Condition the previous five-bucket posterior on observed evidence.

    ``anchor_shift`` is how many rungs the running-max anchor advanced since
    the previous decision.  Buckets that are now impossible (the day has
    already reached a higher rung) get zero mass; the tail is re-expanded
    using the geometric continuation hazard.  ``anchor_shift == 0`` means the
    hour passed without a break: the posterior is unchanged (the hazard model
    itself carries the "time passed without breakout" evidence).
    """
    prev = np.clip(np.asarray(prev_probs, dtype=float), 0.0, 1.0)
    if anchor_shift <= 0:
        total = prev.sum()
        return prev / total if total > 0 else prev
    h = float(np.clip(h_cont, EPS, 1.0 - EPS))
    # Relative frame shift: old bucket index k maps to new index k - shift.
    # below stays below-impossible (already superseded by evidence).
    index = {"below": 0, "current": 1, "d1": 2, "d2": 3, "tail": 4}
    out = np.zeros(5, dtype=float)
    for old_pos in range(5):
        mass = prev[old_pos]
        if mass <= 0:
            continue
        if old_pos == index["tail"]:
            # Decompose old tail geometrically from old d3.. and shift.
            for k in range(12):
                rung_mass = mass * (1.0 - h) * (h**k) if k < 11 else mass * (h**11)
                old_step = 3 + k
                new_step = old_step - anchor_shift
                if new_step < 0:
                    continue
                out[min(new_step, 3) + 1] += rung_mass
        else:
            old_step = old_pos - 1  # current=0, d1=1, d2=2; below=-1
            if old_step < 0:
                continue
            new_step = old_step - anchor_shift
            if new_step < 0:
                continue
            out[min(new_step, 3) + 1] += mass
    total = out.sum()
    if total <= 0:
        # Prior contradicted entirely; fall back to uniform over reachable.
        out[1:] = 0.25
        total = 1.0
    return out / total


def apply_temporal_coherence(
    current_probs: np.ndarray,
    prev_posterior: np.ndarray | None,
    anchor_shift: int,
    h_cont: float,
    weight: float,
) -> np.ndarray:
    """Pool the conditioned previous posterior with the current distribution."""
    cur = np.clip(np.asarray(current_probs, dtype=float), EPS, 1.0)
    cur = cur / cur.sum()
    if prev_posterior is None or weight <= 0:
        return cur
    conditioned = condition_posterior_on_anchor_shift(prev_posterior, anchor_shift, h_cont)
    conditioned = np.clip(conditioned, EPS, 1.0)
    pooled = np.exp((1.0 - weight) * np.log(cur) + weight * np.log(conditioned))
    return pooled / pooled.sum()


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def multiclass_logloss(probs: np.ndarray, actual_index: np.ndarray) -> np.ndarray:
    winner = probs[np.arange(len(actual_index)), actual_index]
    return -np.log(np.maximum(EPS, winner))


def brier(probs: np.ndarray, actual_index: np.ndarray) -> np.ndarray:
    target = np.eye(probs.shape[1])[actual_index]
    return np.square(probs - target).sum(axis=1)


def date_equal_mean(frame: pd.DataFrame, column: str) -> float:
    if frame.empty:
        return math.nan
    return float(frame.groupby("target_date")[column].mean().mean())


def select_alpha_by_train_score(
    model_probs: np.ndarray,
    market_probs: np.ndarray,
    actual_index: np.ndarray,
    target_dates: pd.Series,
    candidates: tuple[float, ...] = ALPHA_CANDIDATES,
) -> tuple[float, dict[float, float]]:
    """Pick the pre-registered alpha with the best date-equal train logloss."""
    scores: dict[float, float] = {}
    frame = pd.DataFrame({"target_date": np.asarray(target_dates)})
    for alpha in candidates:
        fused = fuse_log_linear(model_probs, market_probs, alpha)
        frame["logloss"] = multiclass_logloss(fused, actual_index)
        scores[alpha] = date_equal_mean(frame, "logloss")
    best = min(scores, key=lambda a: (scores[a], a))
    return best, scores


# ---------------------------------------------------------------------------
# Probability artifact schema + model card
# ---------------------------------------------------------------------------

PROBABILITY_ARTIFACT_SCHEMA_V3: dict[str, str] = {
    "artifact_version": "tmax_distribution_v3_probability_row_v1",
    "city": "market city",
    "target_date": "settlement target date (local)",
    "decision_ts_utc": "PIT decision timestamp; every feature available_at <= this",
    "route": "one of ROUTE_SPECS keys or 'market'",
    "lineage": "strict_pit | archive_reconstructed_upper_bound | market_quotes",
    "anchor_bracket": "bracket containing the PIT running max",
    "ladder_rungs_json": "ordered absolute rung labels visible in this snapshot",
    "p_full_ladder_json": "probability per absolute rung incl. below aggregate; sums to 1",
    "p_below/p_current/p_d1/p_d2/p_tail": "five-bucket aggregate; sums to 1",
    "hazard_reach_d1/hazard_reach_d2/hazard_reach_tail": "sequential conditional continuation hazards h0/h1/h2",
    "hazard_tail_continue": "geometric continuation hazard within d3+ tail",
    "fusion_alpha": "market weight selected on training window (pre-registered grid)",
    "coherence_weight": "previous-posterior weight (pre-registered grid)",
    "anchor_shift_from_prev": "rungs the anchor advanced since previous decision",
    "train_through_date": "last training date; strictly < target_date in walk-forward",
    "market_available": "bool; missing ladder never coerced to probability 0",
    "model_version": MODEL_VERSION,
}


def probability_artifact_row(
    *,
    city: str,
    target_date: str,
    decision_ts_utc: str,
    route: str,
    lineage: str,
    anchor_bracket: str,
    ladder_rungs: list[str],
    full_ladder_probs: list[float],
    five_bucket: dict[str, float],
    fusion_alpha: float,
    coherence_weight: float,
    anchor_shift_from_prev: int,
    train_through_date: str | None,
    market_available: bool,
    sequential_hazards: dict[str, float] | None = None,
) -> dict[str, Any]:
    total = float(sum(full_ladder_probs))
    if not math.isclose(total, 1.0, abs_tol=1e-6):
        raise ValueError(f"full ladder probabilities must sum to 1, got {total}")
    bucket_total = float(sum(five_bucket.get(b, 0.0) for b in BUCKETS))
    if not math.isclose(bucket_total, 1.0, abs_tol=1e-6):
        raise ValueError(f"five-bucket probabilities must sum to 1, got {bucket_total}")
    hazards = sequential_hazards or {}
    hazard_fields = {
        "hazard_reach_d1": float(hazards.get("h0", math.nan)),
        "hazard_reach_d2": float(hazards.get("h1", math.nan)),
        "hazard_reach_tail": float(hazards.get("h2", math.nan)),
        "hazard_tail_continue": float(hazards.get("h_cont", math.nan)),
    }
    for name, value in hazard_fields.items():
        if math.isfinite(value) and not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be in [0, 1], got {value}")
    return {
        "artifact_version": "tmax_distribution_v3_probability_row_v1",
        "model_version": MODEL_VERSION,
        "city": city,
        "target_date": target_date,
        "decision_ts_utc": decision_ts_utc,
        "route": route,
        "lineage": lineage,
        "anchor_bracket": anchor_bracket,
        "ladder_rungs_json": json.dumps(ladder_rungs, ensure_ascii=False),
        "p_full_ladder_json": json.dumps([float(p) for p in full_ladder_probs]),
        **{f"p_{bucket}": float(five_bucket[bucket]) for bucket in BUCKETS},
        **hazard_fields,
        "fusion_alpha": float(fusion_alpha),
        "coherence_weight": float(coherence_weight),
        "anchor_shift_from_prev": int(anchor_shift_from_prev),
        "train_through_date": train_through_date,
        "market_available": bool(market_available),
    }


def build_model_card(
    *,
    primary_route: str,
    verdict: dict[str, Any],
    denominators: dict[str, Any],
    frozen_policy: dict[str, Any],
    known_gaps: list[str],
) -> dict[str, Any]:
    return {
        "model_version": MODEL_VERSION,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "model_family": "full_ladder_hazard_chain_with_market_fusion",
        "primary_route": primary_route,
        "routes": {
            name: {
                "layers": list(spec["layers"]),
                "fusion": spec["fusion"],
                "coherence": spec["coherence"],
                "lineage": spec["lineage"],
            }
            for name, spec in ROUTE_SPECS.items()
        },
        "outcome_space": "absolute bracket ladder expanded from PIT anchor; "
        "five-bucket below/current/d1/d2/tail aggregate for legacy comparison",
        "pre_registered": {
            "alpha_candidates": list(ALPHA_CANDIDATES),
            "coherence_candidates": list(COHERENCE_CANDIDATES),
            "market_recalibration_c_candidates": list(RECALIBRATION_C_CANDIDATES),
            "selection_metric": "date-equal multiclass logloss on training window only",
        },
        "frozen_execution_policy": frozen_policy,
        "denominators": denominators,
        "verdict": verdict,
        "known_gaps": known_gaps,
        "hard_boundaries": [
            "no live restore, no live config change, no real order",
            "archive_upper_bound routes never enter a live artifact",
            "missing rung/ladder kept as missingness flag, never probability 0",
            "no per-city free alpha or one-hot memory",
        ],
    }
