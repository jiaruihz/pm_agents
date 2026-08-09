#!/usr/bin/env python3
"""Reusable nested-OOF nonlinear tree challenger for the D-1 tournament.

The tree learns rung-level nonlinear interactions.  Its normalized probability
vector is combined with the contemporaneous market in log space.  Blend alpha
is chosen only on earlier inner-OOF dates and includes alpha=0, which returns
the market exactly.  This is reconstructed development evidence, not an
execution strategy.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import research_d1_cross_city_hierarchy_v1 as hierarchy
from scripts.analysis.forecast_quality import research_d1_legacy_weather_only_v2 as weather_v2
from scripts.analysis.forecast_quality import research_d1_market_residual_tournament_v1 as residual


DEFAULT_OUT = ROOT / "docs/analysis/2026-08/generated/d1_market_residual_tree_v3"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-08/2026-08-06-d1-market-residual-tree-v3.md"
ALPHA_GRID = (0.0, 0.10, 0.25, 0.50, 1.0)
TREE_SPECS = {
    "V09_shallow_gradient_boosting": {
        "max_iter": 80,
        "learning_rate": 0.05,
        "max_leaf_nodes": 5,
        "min_samples_leaf": 20,
        "l2_regularization": 1.0,
    },
    "V10_interaction_gradient_boosting": {
        "max_iter": 120,
        "learning_rate": 0.04,
        "max_leaf_nodes": 15,
        "min_samples_leaf": 10,
        "l2_regularization": 3.0,
    },
}
TREE_FEATURES = (
    "market_logit",
    "weather_logratio",
    "location",
    "scale",
    "spread_logratio",
    "assigned_consensus_bias",
    "checkpoint_revision",
    "rare_rung_weather",
    "weather_agreement",
    "revision_tail",
    "consensus_tail",
)


def state_matrix(state: residual.PreparedState) -> np.ndarray:
    market_logit = np.log(np.clip(state.market, residual.EPS, 1.0 - residual.EPS))
    columns = [market_logit]
    columns.extend(state.features[name] for name in TREE_FEATURES[1:])
    return np.column_stack(columns)


def stack_training(states: list[residual.PreparedState]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    date_counts = pd.Series([state.target_date for state in states]).value_counts().to_dict()
    x_rows: list[np.ndarray] = []
    y_rows: list[np.ndarray] = []
    weight_rows: list[np.ndarray] = []
    for state in states:
        matrix = state_matrix(state)
        labels = np.zeros(len(state.market), dtype=int)
        labels[state.winner_index] = 1
        # Every city-state and target date carries equal total weight.
        state_weight = 1.0 / (len(date_counts) * date_counts[state.target_date])
        weights = np.full(len(labels), state_weight, dtype=float)
        x_rows.append(matrix)
        y_rows.append(labels)
        weight_rows.append(weights)
    return np.vstack(x_rows), np.concatenate(y_rows), np.concatenate(weight_rows)


def fit_tree(states: list[residual.PreparedState], spec: dict[str, Any]) -> HistGradientBoostingClassifier:
    x, y, weights = stack_training(states)
    model = HistGradientBoostingClassifier(
        **spec,
        early_stopping=False,
        random_state=20260806,
        monotonic_cst=[1] + [0] * (len(TREE_FEATURES) - 1),
    )
    model.fit(x, y, sample_weight=weights)
    return model


def tree_vector(model: HistGradientBoostingClassifier, state: residual.PreparedState) -> np.ndarray:
    values = np.clip(model.predict_proba(state_matrix(state))[:, 1], residual.EPS, 1.0)
    return values / values.sum()


def blend_market(market: np.ndarray, tree: np.ndarray, alpha: float) -> np.ndarray:
    if alpha == 0.0:
        return market.copy()
    logits = (1.0 - alpha) * np.log(np.clip(market, residual.EPS, None))
    logits += alpha * np.log(np.clip(tree, residual.EPS, None))
    logits -= logits.max()
    values = np.exp(logits)
    return values / values.sum()


def mean_date_logloss(
    states: list[residual.PreparedState],
    model: HistGradientBoostingClassifier,
    alpha: float,
) -> float:
    rows = []
    for state in states:
        posterior = blend_market(state.market, tree_vector(model, state), alpha)
        rows.append(
            {
                "target_date": state.target_date,
                "logloss": -math.log(max(float(posterior[state.winner_index]), residual.EPS)),
            }
        )
    return float(pd.DataFrame(rows).groupby("target_date")["logloss"].mean().mean())


def select_alpha(
    train_states: list[residual.PreparedState], spec: dict[str, Any]
) -> tuple[float, list[dict[str, Any]]]:
    dates = sorted({state.target_date for state in train_states})
    losses = {alpha: [] for alpha in ALPHA_GRID}
    for index in range(residual.INNER_MIN_TRAIN_DATES, len(dates)):
        fit_dates = set(dates[:index])
        test_date = dates[index]
        fit_states = [state for state in train_states if state.target_date in fit_dates]
        test_states = [state for state in train_states if state.target_date == test_date]
        model = fit_tree(fit_states, spec)
        for alpha in ALPHA_GRID:
            losses[alpha].append(mean_date_logloss(test_states, model, alpha))
    table = [
        {
            "alpha": alpha,
            "inner_oof_dates": len(values),
            "inner_oof_logloss": float(np.mean(values)),
        }
        for alpha, values in losses.items()
    ]
    selected = min(table, key=lambda row: (row["inner_oof_logloss"], row["alpha"]))
    return float(selected["alpha"]), table


def run_oof(states: list[residual.PreparedState]) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = sorted({state.target_date for state in states})
    scored: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    for index in range(residual.OUTER_MIN_TRAIN_DATES, len(dates)):
        train_dates = set(dates[:index])
        test_date = dates[index]
        train_states = [state for state in states if state.target_date in train_dates]
        test_states = [state for state in states if state.target_date == test_date]
        for candidate, spec in TREE_SPECS.items():
            alpha, inner = select_alpha(train_states, spec)
            model = fit_tree(train_states, spec)
            selections.append(
                {
                    "outer_test_date": test_date,
                    "candidate": candidate,
                    "selected_alpha": alpha,
                    "inner_oof_dates": inner[0]["inner_oof_dates"],
                    "inner_oof_logloss": min(row["inner_oof_logloss"] for row in inner),
                }
            )
            for state in test_states:
                posterior = blend_market(state.market, tree_vector(model, state), alpha)
                for arm, vector in (("M0_market", state.market), (candidate, posterior)):
                    scored.append(
                        {
                            "snapshot_key": state.snapshot_key,
                            "city": state.city,
                            "target_date": state.target_date,
                            "arm": arm,
                            "selected_alpha": 0.0 if arm == "M0_market" else alpha,
                            **residual._state_score(state, vector),
                            "winner_index": state.winner_index,
                            "probabilities_json": json.dumps(vector.tolist(), separators=(",", ":")),
                        }
                    )
    frame = pd.DataFrame(scored)
    market = frame.loc[frame["arm"] == "M0_market"].drop_duplicates("snapshot_key")
    return pd.concat([market, frame.loc[frame["arm"] != "M0_market"]], ignore_index=True), pd.DataFrame(selections)


def prepare(args: argparse.Namespace) -> list[residual.PreparedState]:
    forecasts = pd.read_csv(args.forecasts, dtype={"target_date": str})
    history = pd.read_csv(args.history, dtype={"date": str})
    history["is_best_model"] = history["is_best_model"].astype(str).str.lower().isin(["true", "1"])
    history["month_num"] = pd.to_datetime(history["date"]).dt.month
    states, _ = residual.load_prepared_source_states(args.prepared_scored)
    weather_v2.attach_multi_model(states, forecasts)
    primary_dates = sorted({str(state["target_date"]) for state in states if state["policy"] == residual.PRIMARY_POLICY})
    fitted = weather_v2.fit_legacy_history_slice(
        history, min(primary_dates), assignment_policy=args.assignment_policy
    )
    return residual.prepare_states(states, fitted)


def render(summary: dict[str, Any]) -> str:
    lines = [
        "# D-1 market-residual tree tournament v3",
        "",
        "production:",
        "live_action=none",
        "orders_changed=0",
        "",
        "## 结论",
        "",
        f"同分母 nested expanding OOF：{summary['oof_states']} states / {summary['oof_dates']} target dates。",
        "",
        "| arm | logloss | Δ vs market | 95% CI | median max shift | p90 shift | α=0 folds |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    scores = {row["arm"]: row for row in summary["scores"]}
    deltas = {row["left"]: row for row in summary["deltas"]}
    shifts = {row["arm"]: row for row in summary["shifts"]}
    for arm in ("M0_market", *TREE_SPECS):
        score = scores[arm]
        if arm == "M0_market":
            lines.append(f"| {arm} | {score['logloss']:.6f} | reference | - | - | - | - |")
            continue
        delta, shift = deltas[arm], shifts[arm]
        lines.append(
            f"| {arm} | {score['logloss']:.6f} | {delta['delta']:+.6f} | "
            f"[{delta['ci95_low']:+.6f}, {delta['ci95_high']:+.6f}] | "
            f"{shift['median_max_abs_shift']:.2%} | {shift['p90_max_abs_shift']:.2%} | "
            f"{summary['alpha_zero_folds'][arm]}/{summary['oof_dates']} |"
        )
    lines += [
        "",
        "V09/V10 使用真正的 gradient-boosted nonlinear interactions；inner OOF 可选 α=0，因此失败时精确回到 market。重建 market 是 normalized mid，不是 executable ask，任何正结果仍须经过 clean first-seen forward 与 ask/fee/depth。",
        "",
    ]
    return "\n".join(lines)


def run_experiment() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecasts", type=Path, default=hierarchy.DEFAULT_FORECASTS)
    parser.add_argument("--history", type=Path, default=hierarchy.DEFAULT_HISTORY)
    parser.add_argument("--prepared-scored", type=Path, default=residual.DEFAULT_PREPARED_SCORED)
    parser.add_argument(
        "--assignment-policy",
        choices=("authoritative_city_model", "legacy_is_best_model"),
        default="legacy_is_best_model",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    prepared = prepare(args)
    scored, selections = run_oof(prepared)
    scores = residual.date_equal_summary(scored)
    deltas = pd.DataFrame(
        [residual.paired_bootstrap(scored, arm, "logloss", family_size=10) for arm in TREE_SPECS]
    )
    shifts = residual.posterior_shift_summary(scored)
    summary = {
        "schema_version": "d1_market_residual_tree_v3",
        "inputs": {
            "forecasts": str(args.forecasts),
            "prepared_scored": str(args.prepared_scored),
        },
        "oof_states": int(scored.loc[scored["arm"] == "M0_market", "snapshot_key"].nunique()),
        "oof_dates": int(scored["target_date"].nunique()),
        "features": list(TREE_FEATURES),
        "tree_specs": TREE_SPECS,
        "alpha_grid": list(ALPHA_GRID),
        "scores": scores.to_dict("records"),
        "deltas": deltas.to_dict("records"),
        "shifts": shifts.to_dict("records"),
        "alpha_zero_folds": {
            arm: int((selections.loc[selections["candidate"] == arm, "selected_alpha"] == 0.0).sum())
            for arm in TREE_SPECS
        },
        "production": {"live_action": "none", "orders_changed": 0},
    }
    args.out.mkdir(parents=True, exist_ok=True)
    scored.to_csv(args.out / "outer_oof_scored_states.csv", index=False)
    selections.to_csv(args.out / "outer_fold_model_selections.csv", index=False)
    scores.to_csv(args.out / "score_summary.csv", index=False)
    deltas.to_csv(args.out / "paired_target_date_bootstrap.csv", index=False)
    shifts.to_csv(args.out / "posterior_shift_summary.csv", index=False)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.report.write_text(render(summary), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0
