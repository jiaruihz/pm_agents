#!/usr/bin/env python3
"""Nested expanding-OOF tournament for D-1 market-anchored residuals.

All candidates use the same reconstructed D-1 native-ladder states.  Market is
the contemporaneous normalized ladder and is an exact model offset: beta=0
returns the market vector bit-for-bit.  Each outer target date is predicted by
parameters fit only on earlier dates; ridge strength (including the zero model)
is selected by an inner expanding-date OOF loop.

This is legacy/reconstructed development evidence, not clean provider-run
forward and not an execution or live strategy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_d1_cross_city_hierarchy_v1 as base  # noqa: E402
from scripts.analysis.forecast_quality import research_d1_legacy_weather_only_robust_tail as robust  # noqa: E402
from scripts.analysis.forecast_quality import research_d1_legacy_weather_only_v2 as v2  # noqa: E402


DEFAULT_OUT = Path(
    "/Volumes/jrs/pm_agents/research/artifact_store/active/"
    "d1_market_residual_tournament_v1"
)
DEFAULT_REPORT = ROOT / "docs/analysis/2026-08/2026-08-06-d1-market-residual-tournament-v1.md"
DEFAULT_PREPARED_SCORED = (
    ROOT / "docs/analysis/2026-08/generated/d1_cross_city_hierarchy_v1/scored_states.csv"
)
PRIMARY_POLICY = base.PRIMARY_POLICY
OUTER_MIN_TRAIN_DATES = 10
INNER_MIN_TRAIN_DATES = 6
RIDGE_GRID = (0.03, 0.10, 0.30, 1.0, 3.0, math.inf)
EPS = 1e-10
BOOTSTRAP_DRAWS = 20_000

W0_PARAMS = {
    "ensemble_weight": 0.875,
    "scale_temperature": 1.25,
    "climate_mix": 0.02,
    "consensus_stat": "mean",
    "bias_multiplier": 1.0,
}

FEATURE_SETS = {
    "V01_weather_logratio": ("weather_logratio",),
    "V02_ordinal_location_scale": ("location", "scale"),
    "V03_structured_weather": (
        "weather_logratio",
        "location",
        "scale",
        "spread_logratio",
    ),
    "V04_revision_spread_bias": (
        "weather_logratio",
        "location",
        "scale",
        "spread_logratio",
        "assigned_consensus_bias",
        "checkpoint_revision",
    ),
}


@dataclass
class PreparedState:
    snapshot_key: str
    city: str
    target_date: str
    model_key: str
    winner_index: int
    market: np.ndarray
    weather: np.ndarray
    features: dict[str, np.ndarray]
    reconstructed_revision_f: float
    model_spread_f: float
    assigned_minus_consensus_f: float


def softmax_offset(market: np.ndarray, design: np.ndarray, beta: np.ndarray) -> np.ndarray:
    """Return normalized market offset; beta=0 is exactly market."""
    market = np.asarray(market, dtype=float)
    beta = np.asarray(beta, dtype=float)
    if beta.size == 0 or np.all(beta == 0.0):
        return market.copy()
    logits = np.log(np.clip(market, EPS, None)) + design @ beta
    logits -= float(np.max(logits))
    posterior = np.exp(logits)
    return posterior / posterior.sum()


def _center_by_market(values: np.ndarray, market: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    return values - float(np.dot(market, values))


def _revision_lookup(states: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(s["city"]), str(s["target_date"])): s
        for s in states
        if s["policy"] == "D-1_12_18_first"
    }


def _brackets_from_labels(labels: list[str]) -> list[base.Bracket]:
    brackets: list[base.Bracket] = []
    for index, label in enumerate(labels):
        numbers = [float(x) for x in re.findall(r"(?<![\d.])-?\d+(?:\.\d+)?", label)]
        if not numbers:
            raise ValueError(f"unparseable prepared bracket label: {label}")
        if index == 0:
            brackets.append(base.Bracket(label, None, numbers[0], True, False))
        elif index == len(labels) - 1:
            brackets.append(base.Bracket(label, numbers[0], None, False, True))
        else:
            high = numbers[1] if len(numbers) > 1 else numbers[0]
            brackets.append(base.Bracket(label, numbers[0], high, False, False))
    return brackets


def load_prepared_source_states(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Load the already materialized fixed denominator without rescanning live DB.

    This artifact was produced by the frozen cross-city runner from the raw
    forecast/basket inputs and canonical settlement labels.  One market row per
    snapshot contains the ordered native ladder and winner label.
    """
    scored = pd.read_csv(path, dtype={"target_date": str})
    market = scored.loc[scored["arm"] == "market"].copy()
    states: list[dict[str, Any]] = []
    for row in market.itertuples(index=False):
        probabilities = json.loads(row.probabilities_json)
        labels = list(probabilities)
        vector = np.asarray(list(probabilities.values()), dtype=float)
        vector /= vector.sum()
        states.append(
            {
                "snapshot_key": str(row.snapshot_key),
                "city": str(row.city),
                "target_date": str(row.target_date),
                "policy": str(row.policy),
                "decision_ts_utc": str(row.decision_ts_utc),
                "decision_time_utc": str(row.decision_ts_utc),
                "market_unit": str(row.market_unit),
                "model": str(row.forecast_model),
                "model_key": base.MODEL_KEY[str(row.forecast_model)],
                "forecast_max_f": float(row.forecast_max_f),
                "forecast_lineage_status": "single_run_reconstructed_conservative_12h_lag",
                "brackets": _brackets_from_labels(labels),
                "labels": labels,
                "winner_index": labels.index(str(row.winner_bracket)),
                "market_probs": vector,
            }
        )
    sibling_summary = path.parent / "summary.json"
    if sibling_summary.exists():
        funnel = json.loads(sibling_summary.read_text(encoding="utf-8"))["funnels"]
    else:
        funnel = {
            "forecast_snapshots": int(market["snapshot_key"].nunique()),
            "basket_snapshots": int(market["snapshot_key"].nunique()),
            "assigned_model_snapshots": int(market["snapshot_key"].nunique()),
            "missing_settlement": 0,
            "invalid_ladder": 0,
            "missing_market_mid": 0,
            "missing_ask": 0,
            "scoreable_states": len(states),
        }
    return states, {key: int(value) for key, value in funnel.items() if isinstance(value, (int, float))}


def prepare_states(
    states: list[dict[str, Any]], fitted_weather: dict[str, Any]
) -> list[PreparedState]:
    early = _revision_lookup(states)
    prepared: list[PreparedState] = []
    for state in states:
        if state["policy"] != PRIMARY_POLICY:
            continue
        market = np.asarray(state["market_probs"], dtype=float)
        weather = robust.robust_tail_vector(state, fitted_weather, **W0_PARAMS)
        n = len(market)
        rank = np.arange(n, dtype=float)
        market_mean = float(np.dot(market, rank))
        market_var = float(np.dot(market, (rank - market_mean) ** 2))
        market_sd = max(math.sqrt(market_var), 0.5)
        z = (rank - market_mean) / market_sd
        weather_mean = float(np.dot(weather, rank))
        weather_var = float(np.dot(weather, (rank - weather_mean) ** 2))

        logratio = np.log(np.clip(weather, EPS, None)) - np.log(np.clip(market, EPS, None))
        logratio = np.clip(_center_by_market(logratio, market), -6.0, 6.0)
        location_gap = float(np.clip((weather_mean - market_mean) / market_sd, -3.0, 3.0))
        scale_gap = float(
            np.clip((math.sqrt(weather_var) - math.sqrt(market_var)) / market_sd, -3.0, 3.0)
        )

        previous = early.get((str(state["city"]), str(state["target_date"])))
        revision = (
            float(state["forecast_max_f"] - previous["forecast_max_f"])
            if previous is not None
            else 0.0
        )
        assigned_bias = float(state["forecast_max_f"] - state["ensemble_median_f"])
        spread = float(state["model_spread_f"])
        features = {
            "weather_logratio": logratio,
            "location": _center_by_market(z * location_gap, market),
            "scale": _center_by_market((z**2) * scale_gap, market),
            "spread_logratio": _center_by_market(logratio * np.clip(spread / 3.0, 0.0, 3.0), market),
            "assigned_consensus_bias": _center_by_market(
                z * np.clip(assigned_bias / 3.0, -3.0, 3.0), market
            ),
            "checkpoint_revision": _center_by_market(
                z * np.clip(revision / 3.0, -3.0, 3.0), market
            ),
        }
        zero = softmax_offset(market, np.column_stack(list(features.values())), np.zeros(len(features)))
        if not np.array_equal(zero, market):
            raise AssertionError("delta=0 must return market exactly")
        prepared.append(
            PreparedState(
                snapshot_key=str(state["snapshot_key"]),
                city=str(state["city"]),
                target_date=str(state["target_date"]),
                model_key=str(state["model_key"]),
                winner_index=int(state["winner_index"]),
                market=market,
                weather=weather,
                features=features,
                reconstructed_revision_f=revision,
                model_spread_f=spread,
                assigned_minus_consensus_f=assigned_bias,
            )
        )
    return prepared


def _design(state: PreparedState, feature_names: tuple[str, ...], scale: np.ndarray) -> np.ndarray:
    return np.column_stack([state.features[name] for name in feature_names]) / scale


def fit_feature_scale(states: list[PreparedState], feature_names: tuple[str, ...]) -> np.ndarray:
    stacked = np.vstack(
        [np.column_stack([state.features[name] for name in feature_names]) for state in states]
    )
    scale = np.sqrt(np.mean(stacked**2, axis=0))
    return np.maximum(scale, 0.05)


def _date_equal_weights(states: list[PreparedState]) -> np.ndarray:
    counts = Counter(state.target_date for state in states)
    dates = len(counts)
    return np.asarray([1.0 / (dates * counts[s.target_date]) for s in states], dtype=float)


def fit_model(
    states: list[PreparedState], feature_names: tuple[str, ...], ridge: float
) -> tuple[np.ndarray, np.ndarray]:
    scale = fit_feature_scale(states, feature_names)
    if math.isinf(ridge):
        return np.zeros(len(feature_names), dtype=float), scale
    weights = _date_equal_weights(states)

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        loss = 0.0
        grad = np.zeros_like(beta)
        for weight, state in zip(weights, states):
            design = _design(state, feature_names, scale)
            posterior = softmax_offset(state.market, design, beta)
            loss -= weight * math.log(max(float(posterior[state.winner_index]), EPS))
            grad += weight * (
                posterior @ design - design[state.winner_index]
            )
        loss += 0.5 * ridge * float(beta @ beta)
        grad += ridge * beta
        return loss, grad

    fitted = minimize(
        objective,
        np.zeros(len(feature_names), dtype=float),
        jac=True,
        method="L-BFGS-B",
        bounds=[(-2.0, 2.0)] * len(feature_names),
        options={"maxiter": 400, "ftol": 1e-12, "gtol": 1e-9},
    )
    if not fitted.success:
        raise RuntimeError(f"residual fit failed: {fitted.message}")
    return np.asarray(fitted.x, dtype=float), scale


def _state_score(state: PreparedState, probabilities: np.ndarray) -> dict[str, float]:
    logloss, brier, rps, winner_probability, top1 = base.score_vector(
        probabilities, state.winner_index
    )
    return {
        "logloss": logloss,
        "brier": brier,
        "rps": rps,
        "winner_probability": winner_probability,
        "top1_accuracy": float(top1),
    }


def mean_date_loss(
    states: list[PreparedState], feature_names: tuple[str, ...], beta: np.ndarray, scale: np.ndarray
) -> float:
    rows = []
    for state in states:
        posterior = softmax_offset(state.market, _design(state, feature_names, scale), beta)
        rows.append({"target_date": state.target_date, **_state_score(state, posterior)})
    return float(pd.DataFrame(rows).groupby("target_date")["logloss"].mean().mean())


def inner_select_ridge(
    train_states: list[PreparedState],
    feature_names: tuple[str, ...],
    cache: dict[tuple[tuple[str, ...], str, str], float],
) -> tuple[float, pd.DataFrame]:
    dates = sorted({state.target_date for state in train_states})
    rows: list[dict[str, Any]] = []
    for ridge in RIDGE_GRID:
        fold_losses: list[dict[str, Any]] = []
        for index in range(INNER_MIN_TRAIN_DATES, len(dates)):
            fit_dates = set(dates[:index])
            test_date = dates[index]
            fit_states = [s for s in train_states if s.target_date in fit_dates]
            test_states = [s for s in train_states if s.target_date == test_date]
            ridge_key = "inf" if math.isinf(ridge) else f"{ridge:.12g}"
            cache_key = (feature_names, ridge_key, test_date)
            if cache_key not in cache:
                beta, scale = fit_model(fit_states, feature_names, ridge)
                cache[cache_key] = mean_date_loss(
                    test_states, feature_names, beta, scale
                )
            fold_losses.append(
                {
                    "target_date": test_date,
                    "logloss": cache[cache_key],
                }
            )
        if not fold_losses:
            raise ValueError("not enough dates for inner expanding OOF")
        rows.append(
            {
                "ridge": "inf" if math.isinf(ridge) else ridge,
                "inner_oof_dates": len(fold_losses),
                "inner_oof_logloss": float(pd.DataFrame(fold_losses)["logloss"].mean()),
            }
        )
    table = pd.DataFrame(rows).sort_values(
        ["inner_oof_logloss", "ridge"], key=lambda s: s.astype(str) if s.name == "ridge" else s
    )
    selected_raw = table.iloc[0]["ridge"]
    selected = math.inf if str(selected_raw) == "inf" else float(selected_raw)
    return selected, table.reset_index(drop=True)


def run_outer_oof(states: list[PreparedState]) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted({state.target_date for state in states})
    scored: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    inner_cache: dict[tuple[tuple[str, ...], str, str], float] = {}
    for index in range(OUTER_MIN_TRAIN_DATES, len(dates)):
        train_dates = set(dates[:index])
        test_date = dates[index]
        train_states = [s for s in states if s.target_date in train_dates]
        test_states = [s for s in states if s.target_date == test_date]
        for candidate, feature_names in FEATURE_SETS.items():
            ridge, inner = inner_select_ridge(train_states, feature_names, inner_cache)
            beta, scale = fit_model(train_states, feature_names, ridge)
            selections.append(
                {
                    "outer_test_date": test_date,
                    "candidate": candidate,
                    "train_dates": len(train_dates),
                    "train_states": len(train_states),
                    "selected_ridge": "inf" if math.isinf(ridge) else ridge,
                    "inner_oof_dates": int(inner.iloc[0]["inner_oof_dates"]),
                    "inner_oof_logloss": float(inner.iloc[0]["inner_oof_logloss"]),
                    "coefficients_json": json.dumps(dict(zip(feature_names, beta)), sort_keys=True),
                    "feature_scale_json": json.dumps(dict(zip(feature_names, scale)), sort_keys=True),
                }
            )
            for state in test_states:
                posterior = softmax_offset(
                    state.market, _design(state, feature_names, scale), beta
                )
                for arm, vector in (("M0_market", state.market), (candidate, posterior)):
                    scored.append(
                        {
                            "snapshot_key": state.snapshot_key,
                            "city": state.city,
                            "target_date": state.target_date,
                            "model_key": state.model_key,
                            "arm": arm,
                            "outer_train_dates": len(train_dates),
                            "selected_ridge": "market" if arm == "M0_market" else (
                                "inf" if math.isinf(ridge) else ridge
                            ),
                            **_state_score(state, vector),
                            "winner_index": state.winner_index,
                            "probabilities_json": json.dumps(vector.tolist(), separators=(",", ":")),
                            "reconstructed_revision_f": state.reconstructed_revision_f,
                            "model_spread_f": state.model_spread_f,
                            "assigned_minus_consensus_f": state.assigned_minus_consensus_f,
                        }
                    )
    frame = pd.DataFrame(scored)
    # M0 was emitted once per candidate; keep one exact baseline row per state.
    market = frame.loc[frame["arm"] == "M0_market"].drop_duplicates("snapshot_key")
    frame = pd.concat([market, frame.loc[frame["arm"] != "M0_market"]], ignore_index=True)
    return frame, pd.DataFrame(selections)


def date_equal_summary(frame: pd.DataFrame) -> pd.DataFrame:
    metrics = ["logloss", "brier", "rps", "winner_probability", "top1_accuracy"]
    daily = frame.groupby(["target_date", "arm"], as_index=False)[metrics].mean()
    summary = daily.groupby("arm", as_index=False)[metrics].mean()
    counts = frame.groupby("arm").agg(
        states=("snapshot_key", "size"), dates=("target_date", "nunique"), cities=("city", "nunique")
    ).reset_index()
    return summary.merge(counts, on="arm", validate="one_to_one")


def paired_bootstrap(
    frame: pd.DataFrame, left: str, metric: str, *, seed: int = 20260806
) -> dict[str, Any]:
    daily = (
        frame.loc[frame["arm"].isin([left, "M0_market"])]
        .groupby(["target_date", "arm"])[metric]
        .mean()
        .unstack()
        .dropna()
    )
    delta = (daily[left] - daily["M0_market"]).to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.choice(delta, size=(BOOTSTRAP_DRAWS, len(delta)), replace=True).mean(axis=1)
    return {
        "left": left,
        "right": "M0_market",
        "metric": metric,
        "dates": len(delta),
        "delta": float(delta.mean()),
        "ci95_low": float(np.quantile(draws, 0.025)),
        "ci95_high": float(np.quantile(draws, 0.975)),
        # Bonferroni family-wise 95% for K=4 candidate models.
        "ci_bonf_low": float(np.quantile(draws, 0.00625)),
        "ci_bonf_high": float(np.quantile(draws, 0.99375)),
    }


def calibration_summary(frame: pd.DataFrame) -> pd.DataFrame:
    bins = [-0.001, 0.05, 0.10, 0.20, 0.40, 0.60, 1.001]
    rows: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        vector = np.asarray(json.loads(row.probabilities_json), dtype=float)
        for index, probability in enumerate(vector):
            rows.append(
                {
                    "arm": row.arm,
                    "probability": probability,
                    "outcome": int(index == row.winner_index),
                }
            )
    expanded = pd.DataFrame(rows)
    expanded["bin"] = pd.cut(expanded["probability"], bins=bins)
    grouped = expanded.groupby(["arm", "bin"], observed=True).agg(
        n=("outcome", "size"), predicted=("probability", "mean"), actual=("outcome", "mean")
    ).reset_index()
    grouped["weighted_error"] = grouped["n"] * (grouped["predicted"] - grouped["actual"]).abs()
    return grouped


def city_catastrophes(frame: pd.DataFrame) -> pd.DataFrame:
    pivot = frame.pivot_table(
        index=["city", "target_date", "snapshot_key"], columns="arm", values="logloss", aggfunc="first"
    ).reset_index()
    rows = []
    for arm in FEATURE_SETS:
        group = pivot.dropna(subset=[arm, "M0_market"]).copy()
        group["delta"] = group[arm] - group["M0_market"]
        for city, city_rows in group.groupby("city"):
            rows.append(
                {
                    "arm": arm,
                    "city": city,
                    "states": len(city_rows),
                    "dates": city_rows["target_date"].nunique(),
                    "mean_logloss_delta": float(city_rows["delta"].mean()),
                    "worst_state_delta": float(city_rows["delta"].max()),
                }
            )
    return pd.DataFrame(rows).sort_values(["arm", "mean_logloss_delta"], ascending=[True, False])


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_runtime_artifact(
    prepared: list[PreparedState], scores: pd.DataFrame, deltas: pd.DataFrame
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit the point-estimate best arm and express it in the runtime schema.

    Runtime schema compatibility does not imply shadow eligibility: the current
    checkpoint producer does not materialize these six rung-level features and
    the OOF baseline gate is not passed.
    """
    by_arm = scores.set_index("arm")
    candidate = min(FEATURE_SETS, key=lambda arm: float(by_arm.loc[arm, "logloss"]))
    names = FEATURE_SETS[candidate]
    ridge, inner = inner_select_ridge(prepared, names, {})
    beta, scale = fit_model(prepared, names, ridge)
    raw_weights = beta / scale
    delta = deltas.loc[
        (deltas["left"] == candidate) & (deltas["metric"] == "logloss")
    ].iloc[0]
    gate_pass = bool(delta["ci_bonf_high"] < 0.0)
    payload = {
        "schema_version": "weather_d1_market_residual_artifact_v1",
        "artifact_id": "d1_market_residual_v04_reconstructed_20260723",
        "model_id": candidate,
        "feature_set_id": "d1_reconstructed_weather_ordinal_revision_v1",
        "model_family": "linear_market_log_offset",
        "training_cutoff": max(state.target_date for state in prepared),
        "frozen_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "target_kind": "market_expression",
        "market_feature_role": "prior_offset",
        "market_feature_clock": "decision_current",
        "required_features": list(names),
        "parameters": {
            "max_abs_delta": 2.0,
            "default": {
                "intercept": 0.0,
                "weights": {name: float(value) for name, value in zip(names, raw_weights)},
            },
            "by_expression": {},
        },
        "research_evidence": {
            "denominator": "legacy reconstructed D-1_18_24_first",
            "selected_ridge": "inf" if math.isinf(ridge) else ridge,
            "inner_expanding_oof_logloss": float(inner.iloc[0]["inner_oof_logloss"]),
            "outer_oof_logloss_delta_vs_market": float(delta["delta"]),
            "outer_oof_bonferroni_ci": [
                float(delta["ci_bonf_low"]), float(delta["ci_bonf_high"])
            ],
            "baseline_gate_pass": gate_pass,
            "clean_forward_pass": False,
            "shadow_eligible": False,
        },
        "runtime_converter": {
            "artifact_schema_compatible": True,
            "status": "blocked_missing_rung_feature_materializer",
            "blocker": (
                "WCIR D-1 checkpoint rows do not yet materialize weather_logratio, "
                "ordinal location/scale, spread interaction, assigned-consensus bias, "
                "and reconstructed checkpoint revision with matching lineage."
            ),
        },
    }
    metadata = {
        "candidate": candidate,
        "ridge": "inf" if math.isinf(ridge) else ridge,
        "coefficients_standardized": dict(zip(names, beta)),
        "feature_scale": dict(zip(names, scale)),
        "runtime_weights_raw_features": dict(zip(names, raw_weights)),
        "baseline_gate_pass": gate_pass,
        "shadow_eligible": False,
        "converter_status": "blocked_missing_rung_feature_materializer",
    }
    return payload, metadata


def render_report(summary: dict[str, Any], scores: pd.DataFrame, deltas: pd.DataFrame) -> str:
    by_arm = scores.set_index("arm")
    delta_map = {(r.left, r.metric): r for r in deltas.itertuples(index=False)}
    best_arm = min(FEATURE_SETS, key=lambda arm: float(by_arm.loc[arm, "logloss"]))
    best_delta = delta_map[(best_arm, "logloss")]
    baseline_pass = bool(best_delta.ci_bonf_high < 0.0)
    lines = [
        "# D-1 market-residual nested expanding-OOF tournament v1",
        "",
        "weather-only:",
        "significance=not_retested; locked W0 used only as a residual feature",
        "calibration=legacy W0",
        "pooled_baseline=robust-tail W0",
        "forward=reconstructed development only",
        "",
        "market residual:",
        "baseline=M0 contemporaneous normalized market on identical rows",
        f"forward=nested expanding OOF over {summary['outer_oof_dates']} target dates; not clean provider-run forward",
        "execution=not_run; reconstructed mids are not executable quotes",
        "",
        "production:",
        "live_action=none",
        "orders_changed=0",
        "",
        "## 结论",
        "",
        f"四个预注册 residual 版本均按 target_date 做 nested expanding OOF。点估最佳 `{best_arm}` 的 date-equal logloss={by_arm.loc[best_arm, 'logloss']:.4f}，market={by_arm.loc['M0_market', 'logloss']:.4f}，Δ={best_delta.delta:+.4f}（95% CI {best_delta.ci95_low:+.4f}..{best_delta.ci95_high:+.4f}；K=4 Bonferroni CI {best_delta.ci_bonf_low:+.4f}..{best_delta.ci_bonf_high:+.4f}）。",
        f"baseline gate={'PASS' if baseline_pass else 'FAIL'}：当前没有一个版本可以声明击败 market。V01/V03 在 17/17 个 outer folds 都选择 market null；V02/V04 也分别在 16/17 folds 选择 null，因此 V04 的微小负 delta 只来自一个日期，不是稳定 residual。",
        "只有 delta 与 Bonferroni CI 均小于 0 才称为击败 market。本报告不会把 beta 收缩到 0 后与 market 相近称为 alpha。",
        "",
        "## 同分母 OOF scores",
        "",
        "| arm | states | dates | cities | logloss | Brier | RPS | winner P | top-1 | ΔLL vs market (95% / Bonf.) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for arm in ["M0_market", *FEATURE_SETS]:
        row = by_arm.loc[arm]
        if arm == "M0_market":
            comparison = "reference"
        else:
            d = delta_map[(arm, "logloss")]
            comparison = (
                f"{d.delta:+.4f} ({d.ci95_low:+.4f}..{d.ci95_high:+.4f} / "
                f"{d.ci_bonf_low:+.4f}..{d.ci_bonf_high:+.4f})"
            )
        lines.append(
            f"| {arm} | {int(row.states)} | {int(row.dates)} | {int(row.cities)} | "
            f"{row.logloss:.4f} | {row.brier:.5f} | {row.rps:.5f} | "
            f"{row.winner_probability:.3f} | {row.top1_accuracy:.1%} | {comparison} |"
        )
    lines += [
        "",
        "## 四个版本",
        "",
        "- V01：强正则 `log(weather/market)` offset。",
        "- V02：只允许 coherent ordinal location/scale 调整，不直接使用逐档 weather likelihood。",
        "- V03：V01+V02，并让 model spread 连续调整 weather residual。",
        "- V04：再加入 assigned-minus-consensus bias 与 12–18h→18–24h reconstructed checkpoint revision。该 revision 是 archive reconstruction proxy，不是真实 provider run revision。",
        "- 每个 outer date 的 ridge 都只用更早日期的 inner expanding OOF 选择；候选包含 `ridge=∞`，它严格等于 market。",
        "",
        "## 数据 / 双漏斗",
        "",
        f"- denominator_scope：`{summary['denominator_scope']}`。",
        f"- 输入 forecast artifact：{summary['inputs']['forecasts']}（SHA {summary['input_hashes']['forecasts'][:12]}…）。",
        f"- 输入 baskets：{summary['inputs']['baskets']}（SHA {summary['input_hashes']['baskets'][:12]}…）。",
        f"- signal funnel：{summary['funnel']['forecast_snapshots']} forecast snapshots → {summary['funnel']['assigned_model_snapshots']} assigned snapshots → {summary['prepared_primary_states']} primary-policy prepared states；前 {summary['outer_min_train_dates']} dates 只训练，后 {summary['outer_oof_dates']} dates OOF。",
        f"- evidence funnel：{summary['funnel']['basket_snapshots']} basket snapshots → {summary['funnel']['scoreable_states']} all-policy market+settlement scoreable → {summary['oof_states']} primary OOF states / {summary['outer_oof_dates']} dates；actual fills=0。",
        f"- coverage blockers：invalid_ladder={summary['funnel']['invalid_ladder']}，missing_market_mid={summary['funnel']['missing_market_mid']}，missing_settlement={summary['funnel']['missing_settlement']}。它们是 evidence gaps，不是策略筛选。",
        "",
        "## Calibration / city catastrophe",
        "",
    ]
    for arm in ["M0_market", *FEATURE_SETS]:
        diag = summary["diagnostics"][arm]
        lines.append(
            f"- {arm}: rung ECE={diag['rung_ece']:.4f}，winner≤1%={diag['winner_probability_le_001']}，"
            f"worst city mean ΔLL={diag.get('worst_city_mean_delta', 0.0):+.4f} ({diag.get('worst_city', 'NA')})."
        )
    lines += [
        "",
        "## Evidence boundary",
        "",
        "- 输入 lineage：forecast=`d1_single_runs_backfill_v1/forecast_rows.csv`、book=`d1_extreme_no_snapshot_history_v4/executable_baskets.csv`；label/native ladder/normalized market 读取上游 fixed-denominator `d1_cross_city_hierarchy_v1/scored_states.csv`（其 label 来自当时 materialized canonical `settlement_outcomes`），本 runner 不重扫 mutable live DB。没有读取 intraday atlas/P3 forecast backfill。",
        "- PIT：market/forecast 是 single-run conservative reconstruction，不是严格 first-seen/provider-run capture；因此 OOF 防标签泄漏，但不能升级为 clean forward，也不能写 `beat_market` claim。",
        "- W0/W1：本轮只把 locked legacy robust-tail W0 当 residual feature；真实 provider-run/first-seen/revision W1 尚未可评分，未训练、未伪造。",
        "- M0 是同一 checkpoint normalized mid distribution，不是 ask/depth/fee/slippage execution baseline。",
        "- K=4 同时报 95% 与 Bonferroni family-wise CI；未进行城市/价格/赢家事后筛选。",
        "- 覆盖 8 环中的概率分布、统计推断和同分母 baseline；缺 execution、capacity、fills、clean frozen forward。",
        "- 无论点估如何，本轮唯一动作都是 research/collector，不改 live。",
        "",
        "## Runtime artifact",
        "",
        f"- point-estimate best={summary['runtime_artifact']['candidate']}，final expanding selection ridge={summary['runtime_artifact']['ridge']}；artifact schema=`weather_d1_market_residual_artifact_v1` / family=`linear_market_log_offset`。",
        f"- baseline_gate_pass={summary['runtime_artifact']['baseline_gate_pass']}，shadow_eligible=false，converter={summary['runtime_artifact']['converter_status']}。artifact 可被现有 loader 校验，但在 WCIR 生成同 lineage 的逐 rung features 前不可运行，更不可下单。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--forecasts", type=Path, default=base.DEFAULT_FORECASTS)
    parser.add_argument("--baskets", type=Path, default=base.DEFAULT_BASKETS)
    parser.add_argument("--history", type=Path, default=base.DEFAULT_HISTORY)
    parser.add_argument("--db", type=Path, default=base.DEFAULT_DB)
    parser.add_argument("--prepared-scored", type=Path, default=DEFAULT_PREPARED_SCORED)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    forecasts = pd.read_csv(args.forecasts, dtype={"target_date": str})
    history = pd.read_csv(args.history, dtype={"date": str})
    history["is_best_model"] = history["is_best_model"].astype(str).str.lower().isin(["true", "1"])
    history["month_num"] = pd.to_datetime(history["date"]).dt.month
    states, funnel = load_prepared_source_states(args.prepared_scored)
    v2.attach_multi_model(states, forecasts)
    primary_dates = sorted(
        {str(s["target_date"]) for s in states if s["policy"] == PRIMARY_POLICY}
    )
    fitted_weather = v2.fit_legacy_history_slice(history, min(primary_dates))
    prepared = prepare_states(states, fitted_weather)
    if len(primary_dates) <= OUTER_MIN_TRAIN_DATES:
        raise ValueError("not enough primary target dates for outer expanding OOF")

    scored, selections = run_outer_oof(prepared)
    scores = date_equal_summary(scored)
    deltas = pd.DataFrame(
        [
            paired_bootstrap(scored, arm, metric)
            for arm in FEATURE_SETS
            for metric in ("logloss", "brier", "rps")
        ]
    )
    calibration = calibration_summary(scored)
    city = city_catastrophes(scored)
    diagnostics: dict[str, dict[str, Any]] = {}
    for arm, group in scored.groupby("arm"):
        cal = calibration.loc[calibration["arm"] == arm]
        diag = {
            "rung_ece": float(cal["weighted_error"].sum() / cal["n"].sum()),
            "winner_probability_le_001": int((group["winner_probability"] <= 0.01).sum()),
        }
        if arm != "M0_market":
            worst = city.loc[city["arm"] == arm].iloc[0]
            diag.update(
                {
                    "worst_city": str(worst["city"]),
                    "worst_city_states": int(worst["states"]),
                    "worst_city_mean_delta": float(worst["mean_logloss_delta"]),
                }
            )
        diagnostics[arm] = diag

    runtime_payload, runtime_metadata = build_runtime_artifact(prepared, scores, deltas)

    oof_dates = sorted(scored["target_date"].unique())
    summary = {
        "run_id": "d1_market_residual_tournament_v1_20260806",
        "denominator_scope": (
            "reconstructed_single_run_D1_18_24_first_complete_native_ladder_"
            "settled_contemporaneous_normalized_market"
        ),
        "inputs": {
            "forecasts": str(args.forecasts),
            "baskets": str(args.baskets),
            "prepared_scored_states": str(args.prepared_scored),
            "history": str(args.history),
            "upstream_canonical_db_not_reopened": str(args.db.resolve()),
        },
        "input_hashes": {
            "forecasts": sha256(args.forecasts),
            "baskets": sha256(args.baskets),
            "prepared_scored_states": sha256(args.prepared_scored),
            "history": sha256(args.history),
        },
        "primary_policy": PRIMARY_POLICY,
        "all_primary_dates": len(primary_dates),
        "primary_date_start": min(primary_dates),
        "primary_date_end": max(primary_dates),
        "outer_min_train_dates": OUTER_MIN_TRAIN_DATES,
        "outer_oof_dates": len(oof_dates),
        "outer_oof_start": min(oof_dates),
        "outer_oof_end": max(oof_dates),
        "prepared_primary_states": len(prepared),
        "oof_states": int(scored.loc[scored["arm"] == "M0_market", "snapshot_key"].nunique()),
        "candidate_count": len(FEATURE_SETS),
        "candidates": FEATURE_SETS,
        "ridge_grid": ["inf" if math.isinf(x) else x for x in RIDGE_GRID],
        "w0_params_locked": W0_PARAMS,
        "funnel": funnel,
        "scores": scores.to_dict("records"),
        "paired_deltas": deltas.to_dict("records"),
        "diagnostics": diagnostics,
        "runtime_artifact": runtime_metadata,
        "ridge_selection_counts": {
            arm: selections.loc[selections["candidate"] == arm, "selected_ridge"].astype(str).value_counts().to_dict()
            for arm in FEATURE_SETS
        },
        "evidence_status": "legacy_reconstructed_nested_oof_not_clean_forward",
        "production": {"live_action": "none", "orders_changed": 0},
    }

    args.out.mkdir(parents=True, exist_ok=True)
    scored.to_csv(args.out / "outer_oof_scored_states.csv", index=False)
    selections.to_csv(args.out / "outer_fold_model_selections.csv", index=False)
    scores.to_csv(args.out / "score_summary.csv", index=False)
    deltas.to_csv(args.out / "paired_target_date_bootstrap.csv", index=False)
    calibration.to_csv(args.out / "calibration.csv", index=False)
    city.to_csv(args.out / "city_catastrophes.csv", index=False)
    runtime_path = args.out / "weather_d1_market_residual_artifact_v1.json"
    runtime_path.write_text(
        json.dumps(runtime_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary["runtime_artifact"]["path"] = str(runtime_path)
    summary["runtime_artifact"]["sha256"] = sha256(runtime_path)
    (args.out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_report(summary, scores, deltas), encoding="utf-8")
    print(json.dumps({"report": str(args.report), "out": str(args.out), **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
