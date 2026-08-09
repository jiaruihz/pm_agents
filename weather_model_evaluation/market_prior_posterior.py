"""Shared PIT evaluation for market-prior weather probability heads.

The market price is treated as a prior.  Weather probabilities and source/time
features may only explain a residual.  Walk-forward folds are blocked by
``target_date`` and all reported proper scores use equal target-date weight.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from scipy.optimize import minimize
from scipy.special import expit

from .probability import (
    binary_calibration_table,
    binary_loss_values,
    binary_score,
    date_block_bootstrap_delta,
)
from .ladder_microstructure import (
    LADDER_FEATURES,
    add_ladder_microstructure_features,
)


EPSILON = 1e-5
REQUIRED_COLUMNS = {
    "target_date",
    "event_id",
    "event_source",
    "event_decision_ts_utc",
    "quote_ts_utc",
    "event_age_min",
    "bracket",
    "relative_rung",
    "won_no",
    "model_no_probability",
    "market_no_probability",
    "no_best_bid",
    "no_best_ask",
    "cash_cost_5",
    "effective_cost_5",
}

NUMERIC_EXPRESSION_COLUMNS = (
    "event_age_min",
    "relative_rung",
    "won_no",
    "model_no_probability",
    "market_no_probability",
    "no_best_bid",
    "no_best_ask",
    "cash_cost_5",
    "effective_cost_5",
)


def _prepare_valid_expression_rows(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"missing expression columns: {missing}")
    work = frame.copy()
    work["target_date"] = work["target_date"].astype(str)
    work["event_decision_ts_utc"] = pd.to_datetime(
        work["event_decision_ts_utc"], utc=True, errors="coerce", format="mixed"
    )
    work["quote_ts_utc"] = pd.to_datetime(
        work["quote_ts_utc"], utc=True, errors="coerce", format="mixed"
    )
    for column in NUMERIC_EXPRESSION_COLUMNS:
        work[column] = pd.to_numeric(work[column], errors="coerce")

    identity = (
        work["event_id"].notna()
        & work["event_id"].astype(str).str.strip().ne("")
        & work["bracket"].notna()
        & work["bracket"].astype(str).str.strip().ne("")
    )
    clocks = work["event_decision_ts_utc"].notna() & work["quote_ts_utc"].notna()
    causal = clocks & work["quote_ts_utc"].ge(work["event_decision_ts_utc"])
    settled = work["won_no"].isin([0.0, 1.0])
    probabilities = (
        work["model_no_probability"].between(0.0, 1.0, inclusive="both")
        & work["market_no_probability"].between(0.0, 1.0, inclusive="both")
    )
    two_sided = (
        work["no_best_bid"].between(0.0, 1.0, inclusive="both")
        & work["no_best_ask"].between(0.0, 1.0, inclusive="both")
        & work["no_best_bid"].le(work["no_best_ask"])
    )
    scorable = identity & causal & settled & probabilities & two_sided
    executable = (
        scorable
        & work["cash_cost_5"].gt(0.0)
        & work["effective_cost_5"].gt(0.0)
    )
    counts = {
        "input_expression_rows": int(len(work)),
        "identity_rows": int(identity.sum()),
        "clock_complete_rows": int((identity & clocks).sum()),
        "causal_quote_rows": int((identity & causal).sum()),
        "settled_binary_rows": int((identity & causal & settled).sum()),
        "probability_valid_rows": int(
            (identity & causal & settled & probabilities).sum()
        ),
        "two_sided_scored_rows": int(scorable.sum()),
        "executable_cost_rows": int(executable.sum()),
        "noncausal_quote_rows": int((identity & clocks & ~causal).sum()),
    }
    return work.loc[scorable].copy(), counts


def _weather_fee_per_share(price: pd.Series) -> pd.Series:
    return (0.05 * price * (1.0 - price)).round(5)


def select_city_rows(frame: pd.DataFrame, city: str) -> pd.DataFrame:
    """Lock a city-level evaluation to the requested city without relabeling."""
    if "city" not in frame.columns:
        raise ValueError("city-level research input must contain a city column")
    city_match = frame["city"].astype(str).str.casefold().eq(city.casefold())
    selected = frame.loc[city_match].copy()
    if selected.empty:
        available = sorted(frame["city"].dropna().astype(str).unique())
        raise ValueError(
            f"input has no rows for city={city!r}; available cities={available}"
        )
    return selected


def replay_fmi_entry_metar_correction(
    frame: pd.DataFrame,
    *,
    bootstrap_draws: int = 4000,
    research_entry_cost_min_exclusive: float | None = None,
    research_entry_cost_max_exclusive: float | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Replay the causal source roles: FMI may enter; later METAR may only exit.

    Entry uses the first positive fee-adjusted A8 edge per target-date/bracket.
    A METAR event can never create a new position.  A held NO exits at the first
    later METAR where the taker-sell NO bid net of Weather fee exceeds the
    updated model probability.  Exit evidence is top-of-book only unless the
    supplied expression frame carries a separate depth contract.
    """

    if bootstrap_draws <= 0:
        raise ValueError("bootstrap_draws must be positive")
    if (
        research_entry_cost_min_exclusive is not None
        and research_entry_cost_max_exclusive is not None
        and research_entry_cost_min_exclusive >= research_entry_cost_max_exclusive
    ):
        raise ValueError("research entry cost bounds must be increasing")
    work, validation_counts = _prepare_valid_expression_rows(frame)
    work["event_source"] = work["event_source"].astype(str).str.lower()
    fmi = work.loc[
        work["event_source"].eq("fmi")
        & work["cash_cost_5"].gt(0.0)
        & work["effective_cost_5"].gt(0.0)
    ].copy()
    fmi["entry_edge_5"] = (
        fmi["model_no_probability"] - fmi["effective_cost_5"]
    )
    positive = fmi.loc[fmi["entry_edge_5"] > 0].sort_values(
        ["target_date", "quote_ts_utc", "event_decision_ts_utc", "bracket"]
    )
    entries = positive.drop_duplicates(["target_date", "bracket"], keep="first").copy()
    pre_slice_entries = len(entries)
    if research_entry_cost_min_exclusive is not None:
        entries = entries.loc[
            entries["effective_cost_5"] > research_entry_cost_min_exclusive
        ].copy()
    if research_entry_cost_max_exclusive is not None:
        entries = entries.loc[
            entries["effective_cost_5"] < research_entry_cost_max_exclusive
        ].copy()
    entries["hold_pnl_5"] = np.where(
        entries["won_no"].astype(int).eq(1),
        5.0 - entries["cash_cost_5"],
        -entries["cash_cost_5"],
    )
    rows = []
    for _, entry in entries.iterrows():
        later = work.loc[
            work["target_date"].eq(entry["target_date"])
            & work["bracket"].astype(str).eq(str(entry["bracket"]))
            & work["event_source"].eq("metar")
            & work["event_decision_ts_utc"].gt(entry["event_decision_ts_utc"])
            & work["quote_ts_utc"].gt(entry["quote_ts_utc"])
        ].sort_values(["event_decision_ts_utc", "quote_ts_utc"])
        later = later.copy()
        later["exit_fee_per_share"] = _weather_fee_per_share(
            later["no_best_bid"]
        )
        later["exit_net_per_share"] = (
            later["no_best_bid"] - later["exit_fee_per_share"]
        )
        exit_rows = later.loc[
            later["exit_net_per_share"] > later["model_no_probability"]
        ]
        output = entry.to_dict()
        output["exit_triggered"] = not exit_rows.empty
        if exit_rows.empty:
            output.update(
                {
                    "exit_event_id": None,
                    "exit_decision_ts_utc": pd.NaT,
                    "exit_quote_ts_utc": pd.NaT,
                    "exit_no_best_bid": np.nan,
                    "exit_model_no_probability": np.nan,
                    "exit_net_per_share": np.nan,
                    "exit_quote_lag_s": np.nan,
                    "overlay_pnl_5": float(entry["hold_pnl_5"]),
                }
            )
        else:
            exit_row = exit_rows.iloc[0]
            exit_net = 5.0 * float(exit_row["exit_net_per_share"])
            output.update(
                {
                    "exit_event_id": exit_row["event_id"],
                    "exit_decision_ts_utc": exit_row["event_decision_ts_utc"],
                    "exit_quote_ts_utc": exit_row["quote_ts_utc"],
                    "exit_no_best_bid": float(exit_row["no_best_bid"]),
                    "exit_model_no_probability": float(
                        exit_row["model_no_probability"]
                    ),
                    "exit_net_per_share": float(exit_row["exit_net_per_share"]),
                    "exit_quote_lag_s": float(
                        (
                            exit_row["quote_ts_utc"]
                            - exit_row["event_decision_ts_utc"]
                        ).total_seconds()
                    ),
                    "overlay_pnl_5": exit_net - float(entry["cash_cost_5"]),
                }
            )
        output["overlay_delta_5"] = (
            output["overlay_pnl_5"] - float(entry["hold_pnl_5"])
        )
        rows.append(output)
    replay = pd.DataFrame(rows)
    if replay.empty:
        return replay, {
            "entry_policy": "FMI-only first positive edge per target_date/bracket",
            "exit_policy": "later METAR correction only; never entry",
            "entries": 0,
            "input_validation": validation_counts,
        }
    hold_bootstrap = _pnl_bootstrap(
        replay.rename(columns={"hold_pnl_5": "pnl_5"}),
        draws=bootstrap_draws,
        seed=20260809,
    )
    overlay_bootstrap = _pnl_bootstrap(
        replay.rename(columns={"overlay_pnl_5": "pnl_5"}),
        draws=bootstrap_draws,
        seed=20260809,
    )
    daily = replay.groupby("target_date", sort=True).agg(
        cash_cost_5=("cash_cost_5", "sum"),
        hold_pnl_5=("hold_pnl_5", "sum"),
        overlay_pnl_5=("overlay_pnl_5", "sum"),
    )
    values = daily[["cash_cost_5", "hold_pnl_5", "overlay_pnl_5"]].to_numpy()
    rng = np.random.default_rng(20260809)
    sampled = values[
        rng.integers(0, len(values), size=(bootstrap_draws, len(values)))
    ].sum(axis=1)
    uplift = (sampled[:, 2] - sampled[:, 1]) / sampled[:, 0]
    exit_lags = replay.loc[
        replay["exit_triggered"], "exit_quote_lag_s"
    ].to_numpy(dtype=float)
    t0_30s = replay["exit_triggered"] & replay["exit_quote_lag_s"].le(30)
    strict_t0_pnl = np.where(
        t0_30s,
        replay["overlay_pnl_5"],
        replay["hold_pnl_5"],
    )
    summary = {
        "entry_policy": "FMI-only first positive fee-adjusted A8 edge per target_date/bracket",
        "exit_policy": "METAR correction only: sell held NO when net bid > updated P(NO); METAR cannot enter",
        "input_validation": validation_counts,
        "signal_funnel": {
            "fmi_executable_rows": int(len(fmi)),
            "fmi_positive_edge_rows": int(len(positive)),
            "first_date_bracket_entries": int(len(replay)),
            "first_date_bracket_entries_before_research_price_slice": int(
                pre_slice_entries
            ),
            "target_dates": int(replay["target_date"].nunique()),
        },
        "research_price_slice": {
            "min_exclusive": research_entry_cost_min_exclusive,
            "max_exclusive": research_entry_cost_max_exclusive,
            "eligibility_status": "research_interpretation_only_not_final_strategy_gate",
        },
        "evidence_funnel": {
            "settled_entries": int(replay["won_no"].notna().sum()),
            "metar_exit_triggers": int(replay["exit_triggered"].sum()),
            "exit_top_book_only": int(replay["exit_triggered"].sum()),
            "exit_depth_verified": 0,
            "exit_quote_within_30s": int(t0_30s.sum()),
            "exit_quote_within_60s": int(
                (
                    replay["exit_triggered"]
                    & replay["exit_quote_lag_s"].le(60)
                ).sum()
            ),
            "exit_quote_within_120s": int(
                (
                    replay["exit_triggered"]
                    & replay["exit_quote_lag_s"].le(120)
                ).sum()
            ),
            "exit_quote_lag_p50_s": (
                float(np.median(exit_lags)) if len(exit_lags) else float("nan")
            ),
            "exit_quote_lag_p90_s": (
                float(np.quantile(exit_lags, 0.9))
                if len(exit_lags)
                else float("nan")
            ),
        },
        "hold": {
            "wins": int(replay["won_no"].sum()),
            "losses": int((replay["won_no"] == 0).sum()),
            "cash_cost_5": float(replay["cash_cost_5"].sum()),
            "pnl_5": float(replay["hold_pnl_5"].sum()),
            "roi": hold_bootstrap["roi"],
            "roi_ci_low": hold_bootstrap["ci_low"],
            "roi_ci_high": hold_bootstrap["ci_high"],
        },
        "metar_exit_diagnostic": {
            "exits": int(replay["exit_triggered"].sum()),
            "losing_entries_exited": int(
                (replay["exit_triggered"] & replay["won_no"].eq(0)).sum()
            ),
            "winning_entries_exited": int(
                (replay["exit_triggered"] & replay["won_no"].eq(1)).sum()
            ),
            "pnl_5": float(replay["overlay_pnl_5"].sum()),
            "roi": overlay_bootstrap["roi"],
            "roi_ci_low": overlay_bootstrap["ci_low"],
            "roi_ci_high": overlay_bootstrap["ci_high"],
            "uplift_roi": float(
                replay["overlay_delta_5"].sum() / replay["cash_cost_5"].sum()
            ),
            "uplift_roi_ci_low": float(np.quantile(uplift, 0.025)),
            "uplift_roi_ci_high": float(np.quantile(uplift, 0.975)),
            "strict_t0_30s_exits": int(t0_30s.sum()),
            "strict_t0_30s_pnl_5_missing_as_hold": float(strict_t0_pnl.sum()),
            "strict_t0_30s_roi_missing_as_hold": float(
                strict_t0_pnl.sum() / replay["cash_cost_5"].sum()
            ),
            "qualification": "blocked_metar_t0_quote_and_exit_depth_missing_delayed_snapshot_diagnostic_only",
        },
    }
    return replay, summary


@dataclass(frozen=True)
class PosteriorResearchResult:
    predictions: pd.DataFrame
    scores: pd.DataFrame
    bootstrap: pd.DataFrame
    calibration: pd.DataFrame
    slice_scores: pd.DataFrame
    trades: pd.DataFrame
    trade_summary: pd.DataFrame
    trade_slices: pd.DataFrame
    folds: pd.DataFrame
    denominator: dict[str, Any]


def _logit(values: pd.Series | np.ndarray) -> np.ndarray:
    probability = np.clip(np.asarray(values, dtype=float), EPSILON, 1 - EPSILON)
    return np.log(probability / (1 - probability))


def prepare_expression_grain(
    frame: pd.DataFrame,
    *,
    timezone: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return one two-sided PIT row per source event and exact bracket."""

    scored, validation_counts = _prepare_valid_expression_rows(frame)
    scored = scored.sort_values(
        ["event_decision_ts_utc", "quote_ts_utc", "event_id", "bracket"]
    ).drop_duplicates(["event_id", "bracket"], keep="first")
    scored["market_logit"] = _logit(scored["market_no_probability"])
    scored["model_logit"] = _logit(scored["model_no_probability"])
    scored["weather_market_innovation"] = (
        scored["model_logit"] - scored["market_logit"]
    )
    local = scored["event_decision_ts_utc"].dt.tz_convert(timezone)
    hour = local.dt.hour + local.dt.minute / 60.0 + local.dt.second / 3600.0
    scored["local_hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
    scored["local_hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
    scored["local_clock"] = pd.cut(
        hour,
        bins=[-np.inf, 6, 10, 14, 18, np.inf],
        labels=["overnight", "morning", "midday", "afternoon", "evening"],
        right=False,
    ).astype(str)
    scored["quote_spread"] = scored["no_best_ask"] - scored["no_best_bid"]
    scored["event_source"] = scored["event_source"].astype(str).str.lower()
    denominator = {
        **validation_counts,
        "event_bracket_rows": int(len(scored)),
        "target_dates": int(scored["target_date"].nunique()),
        "denominator_scope": (
            "caller-scoped city/source event-expression rows with causal two-sided "
            "PIT NO quotes and binary settlement labels"
        ),
        "candidate_grain_version": "research_first_event_bracket_v1",
        "grain": "first two-sided PIT quote per (event_id, exact bracket)",
    }
    return scored.reset_index(drop=True), denominator


NUMERIC_MODEL_ONLY = [
    "model_logit",
    "local_hour_sin",
    "local_hour_cos",
    "relative_rung",
    "event_age_min",
]
NUMERIC_MARKET_POSTERIOR = [
    "market_logit",
    "model_logit",
    "weather_market_innovation",
    "local_hour_sin",
    "local_hour_cos",
    "relative_rung",
    "event_age_min",
    "quote_spread",
]
NUMERIC_MARKET_OFFSET = [
    "weather_market_innovation",
    "local_hour_sin",
    "local_hour_cos",
    "relative_rung",
    "event_age_min",
    "quote_spread",
]
LADDER_INTERACTION_CORE = [
    "weather_shock",
    "signed_mode_distance",
    "rung_relative_markout",
    "neighbor_propagation",
    "shock_x_mode_distance",
    "shock_x_neighbor_propagation",
    "shock_x_mode_x_neighbor_propagation",
]
NUMERIC_MARKET_OFFSET_LADDER = NUMERIC_MARKET_OFFSET + LADDER_INTERACTION_CORE
NUMERIC_MARKET_POSTERIOR_LADDER = NUMERIC_MARKET_POSTERIOR + LADDER_FEATURES
CATEGORICAL = ["event_source"]


def _preprocessor(numeric: list[str], *, scale: bool) -> ColumnTransformer:
    numeric_steps: list[tuple[str, Any]] = [("impute", SimpleImputer(strategy="median"))]
    if scale:
        numeric_steps.append(("scale", StandardScaler()))
    return ColumnTransformer(
        [
            ("numeric", Pipeline(numeric_steps), numeric),
            (
                "categorical",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        (
                            "onehot",
                            OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                        ),
                    ]
                ),
                CATEGORICAL,
            ),
        ],
        remainder="drop",
    )


def _candidate_models(
    *, include_ladder_features: bool = False
) -> dict[str, tuple[Pipeline, list[str]]]:
    logistic = lambda numeric: Pipeline(  # noqa: E731
        [
            ("features", _preprocessor(numeric, scale=True)),
            (
                "classifier",
                LogisticRegression(C=1.0, max_iter=1000, random_state=20260809),
            ),
        ]
    )
    hgb = Pipeline(
        [
            ("features", _preprocessor(NUMERIC_MARKET_POSTERIOR, scale=False)),
            (
                "classifier",
                HistGradientBoostingClassifier(
                    learning_rate=0.05,
                    max_iter=80,
                    max_leaf_nodes=7,
                    min_samples_leaf=30,
                    l2_regularization=3.0,
                    random_state=20260809,
                ),
            ),
        ]
    )
    models = {
        "compact_logistic_model_only": (
            logistic(NUMERIC_MODEL_ONLY),
            NUMERIC_MODEL_ONLY + CATEGORICAL,
        ),
        "compact_logistic_market_prior": (
            logistic(NUMERIC_MARKET_POSTERIOR),
            NUMERIC_MARKET_POSTERIOR + CATEGORICAL,
        ),
        "shallow_hgb_market_prior": (
            hgb,
            NUMERIC_MARKET_POSTERIOR + CATEGORICAL,
        ),
    }
    if include_ladder_features:
        ladder_hgb = Pipeline(
            [
                (
                    "features",
                    _preprocessor(NUMERIC_MARKET_POSTERIOR_LADDER, scale=False),
                ),
                (
                    "classifier",
                    HistGradientBoostingClassifier(
                        learning_rate=0.05,
                        max_iter=80,
                        max_leaf_nodes=7,
                        min_samples_leaf=30,
                        l2_regularization=3.0,
                        random_state=20260809,
                    ),
                ),
            ]
        )
        models.update(
            {
                "compact_logistic_market_prior_ladder": (
                    logistic(NUMERIC_MARKET_POSTERIOR_LADDER),
                    NUMERIC_MARKET_POSTERIOR_LADDER + CATEGORICAL,
                ),
                "shallow_hgb_market_prior_ladder": (
                    ladder_hgb,
                    NUMERIC_MARKET_POSTERIOR_LADDER + CATEGORICAL,
                ),
            }
        )
    return models


def _date_equal_fit_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame["target_date"].map(frame["target_date"].value_counts())
    weight = 1.0 / counts.to_numpy(dtype=float)
    return weight / weight.mean()


def _fit_market_offset_logistic(
    train: pd.DataFrame,
    test: pd.DataFrame,
    weights: np.ndarray,
    *,
    numeric_features: list[str] = NUMERIC_MARKET_OFFSET,
    l2_strength: float = 0.05,
) -> np.ndarray:
    """Fit a regularized residual while fixing the market-logit coefficient at 1."""

    transformer = _preprocessor(numeric_features, scale=True)
    train_design = transformer.fit_transform(
        train[numeric_features + CATEGORICAL]
    )
    test_design = transformer.transform(test[numeric_features + CATEGORICAL])
    train_design = np.column_stack([np.ones(len(train_design)), train_design])
    test_design = np.column_stack([np.ones(len(test_design)), test_design])
    y = train["won_no"].to_numpy(dtype=float)
    offset = train["market_logit"].to_numpy(dtype=float)

    def objective(beta: np.ndarray) -> tuple[float, np.ndarray]:
        linear = offset + train_design @ beta
        probability = expit(linear)
        loss = np.average(
            np.logaddexp(0.0, linear) - y * linear,
            weights=weights,
        ) + l2_strength * float(beta[1:] @ beta[1:])
        residual = weights * (probability - y) / weights.sum()
        gradient = train_design.T @ residual
        gradient[1:] += 2.0 * l2_strength * beta[1:]
        return float(loss), gradient

    fitted = minimize(
        objective,
        np.zeros(train_design.shape[1]),
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": 500},
    )
    if not fitted.success:
        raise RuntimeError(f"market offset fit failed: {fitted.message}")
    return expit(test["market_logit"].to_numpy(dtype=float) + test_design @ fitted.x)


def _pnl_bootstrap(
    trades: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> dict[str, float | int]:
    if draws <= 0:
        raise ValueError("bootstrap draws must be positive")
    if trades.empty:
        return {
            "target_dates": 0,
            "draws": int(draws),
            "roi": float("nan"),
            "ci_low": float("nan"),
            "ci_high": float("nan"),
        }
    daily = trades.groupby("target_date", sort=True).agg(
        cash_cost_5=("cash_cost_5", "sum"), pnl_5=("pnl_5", "sum")
    )
    values = daily[["cash_cost_5", "pnl_5"]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:, 0] <= 0).any():
        raise ValueError("trade bootstrap requires finite positive daily cash cost")
    rng = np.random.default_rng(seed)
    indexes = rng.integers(0, len(values), size=(draws, len(values)))
    sampled = values[indexes].sum(axis=1)
    roi = sampled[:, 1] / sampled[:, 0]
    return {
        "target_dates": int(len(values)),
        "draws": int(draws),
        "roi": float(values[:, 1].sum() / values[:, 0].sum()),
        "ci_low": float(np.quantile(roi, 0.025)),
        "ci_high": float(np.quantile(roi, 0.975)),
    }


def _trade_replay(
    predictions: pd.DataFrame,
    probability_column: str,
) -> pd.DataFrame:
    eligible = predictions.loc[
        predictions["cash_cost_5"].gt(0.0)
        & predictions["effective_cost_5"].gt(0.0)
    ].copy()
    eligible["candidate_probability"] = eligible[probability_column]
    eligible["candidate_edge_5"] = (
        eligible["candidate_probability"] - eligible["effective_cost_5"]
    )
    eligible = eligible.loc[eligible["candidate_edge_5"] > 0].sort_values(
        ["target_date", "quote_ts_utc", "event_decision_ts_utc", "bracket"]
    )
    trades = eligible.drop_duplicates(["target_date", "bracket"], keep="first").copy()
    trades["pnl_5"] = np.where(
        trades["won_no"].astype(int) == 1,
        5.0 - trades["cash_cost_5"],
        -trades["cash_cost_5"],
    )
    trades["probability_model"] = probability_column
    trades["price_bucket"] = pd.cut(
        trades["effective_cost_5"],
        bins=[-np.inf, 0.8, 0.9, 0.95, 0.98, np.inf],
        labels=["<0.80", "0.80-0.90", "0.90-0.95", "0.95-0.98", ">=0.98"],
        right=False,
    ).astype(str)
    return trades


def _slice_probability_scores(
    predictions: pd.DataFrame,
    probability_columns: list[str],
) -> pd.DataFrame:
    rows = []
    for dimension in ("event_source", "local_clock"):
        for value, subset in predictions.groupby(dimension, sort=True):
            for probability_column in probability_columns:
                rows.append(
                    {
                        "dimension": dimension,
                        "slice": str(value),
                        "model": probability_column.removeprefix("p_"),
                        **binary_score(
                            subset,
                            subset[probability_column],
                            label_column="won_no",
                        ),
                    }
                )
    return pd.DataFrame(rows)


def _slice_trade_summary(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for dimension in ("event_source", "local_clock", "price_bucket"):
        for (model, value), subset in trades.groupby(["model", dimension], sort=True):
            cash = float(subset["cash_cost_5"].sum())
            pnl = float(subset["pnl_5"].sum())
            rows.append(
                {
                    "dimension": dimension,
                    "slice": str(value),
                    "model": model,
                    "trades": int(len(subset)),
                    "target_dates": int(subset["target_date"].nunique()),
                    "wins": int(subset["won_no"].sum()),
                    "win_rate": float(subset["won_no"].mean()),
                    "cash_cost_5": cash,
                    "pnl_5": pnl,
                    "roi": pnl / cash if cash else float("nan"),
                }
            )
    return pd.DataFrame(rows)


def run_market_prior_posterior_research(
    frame: pd.DataFrame,
    *,
    timezone: str,
    min_train_dates: int = 3,
    bootstrap_draws: int = 4000,
    include_ladder_features: bool = False,
) -> PosteriorResearchResult:
    """Run expanding target-date OOF A/B on the supplied fixed denominator."""

    if min_train_dates < 1:
        raise ValueError("min_train_dates must be positive")
    if bootstrap_draws <= 0:
        raise ValueError("bootstrap_draws must be positive")
    prepared, denominator = prepare_expression_grain(frame, timezone=timezone)
    if include_ladder_features:
        prepared, ladder_coverage = add_ladder_microstructure_features(prepared)
        denominator["ladder_microstructure"] = ladder_coverage
    dates = sorted(prepared["target_date"].unique())
    if len(dates) <= min_train_dates:
        raise ValueError("not enough target dates for expanding OOF evaluation")
    models = _candidate_models(include_ladder_features=include_ladder_features)
    prediction_rows = []
    fold_rows = []
    for fold_index in range(min_train_dates, len(dates)):
        test_date = dates[fold_index]
        train_dates = dates[:fold_index]
        train = prepared.loc[prepared["target_date"].isin(train_dates)].copy()
        test = prepared.loc[prepared["target_date"] == test_date].copy()
        if train["won_no"].nunique() < 2:
            raise ValueError(f"training fold through {train_dates[-1]} has one label")
        output = test.copy()
        output["p_raw_market"] = output["market_no_probability"]
        output["p_raw_a8"] = output["model_no_probability"]
        weights = _date_equal_fit_weights(train)
        output["p_compact_logistic_market_offset"] = _fit_market_offset_logistic(
            train, test, weights
        )
        if include_ladder_features:
            output["p_compact_logistic_market_offset_ladder"] = (
                _fit_market_offset_logistic(
                    train,
                    test,
                    weights,
                    numeric_features=NUMERIC_MARKET_OFFSET_LADDER,
                    l2_strength=0.5,
                )
            )
        for name, (model, features) in models.items():
            model.fit(
                train[features],
                train["won_no"].astype(int),
                classifier__sample_weight=weights,
            )
            output[f"p_{name}"] = model.predict_proba(test[features])[:, 1]
        prediction_rows.append(output)
        fold_rows.append(
            {
                "fold": int(fold_index - min_train_dates + 1),
                "train_start": train_dates[0],
                "train_end": train_dates[-1],
                "train_dates": int(len(train_dates)),
                "train_rows": int(len(train)),
                "test_date": test_date,
                "test_rows": int(len(test)),
            }
        )
    predictions = pd.concat(prediction_rows, ignore_index=True)
    probability_columns = [
        "p_raw_market",
        "p_raw_a8",
        "p_compact_logistic_market_offset",
    ]
    if include_ladder_features:
        probability_columns.append("p_compact_logistic_market_offset_ladder")
    probability_columns += [
        f"p_{name}" for name in models
    ]
    score_rows = []
    bootstrap_rows = []
    calibration_rows = []
    market_loss = {
        metric: binary_loss_values(
            predictions["won_no"].astype(int),
            predictions["p_raw_market"],
            metric=metric,
        )
        for metric in ("brier", "logloss")
    }
    for probability_column in probability_columns:
        score = binary_score(
            predictions,
            predictions[probability_column],
            label_column="won_no",
        )
        score_rows.append({"model": probability_column.removeprefix("p_"), **score})
        calibration = binary_calibration_table(
            predictions,
            predictions[probability_column],
            label_column="won_no",
        )
        calibration.insert(0, "model", probability_column.removeprefix("p_"))
        calibration_rows.append(calibration)
        if probability_column == "p_raw_market":
            continue
        for metric in ("brier", "logloss"):
            candidate_loss = binary_loss_values(
                predictions["won_no"].astype(int),
                predictions[probability_column],
                metric=metric,
            )
            bootstrap_rows.append(
                {
                    "model": probability_column.removeprefix("p_"),
                    "baseline_model": "raw_market",
                    "metric": metric,
                    **date_block_bootstrap_delta(
                        predictions,
                        candidate_loss,
                        market_loss[metric],
                        draws=bootstrap_draws,
                        seed=20260809,
                    ),
                }
            )
    if include_ladder_features:
        ladder_pairs = [
            (
                "compact_logistic_market_offset_ladder",
                "compact_logistic_market_offset",
            ),
            (
                "compact_logistic_market_prior_ladder",
                "compact_logistic_market_prior",
            ),
            ("shallow_hgb_market_prior_ladder", "shallow_hgb_market_prior"),
        ]
        for candidate_name, baseline_name in ladder_pairs:
            for metric in ("brier", "logloss"):
                candidate_loss = binary_loss_values(
                    predictions["won_no"].astype(int),
                    predictions[f"p_{candidate_name}"],
                    metric=metric,
                )
                baseline_loss = binary_loss_values(
                    predictions["won_no"].astype(int),
                    predictions[f"p_{baseline_name}"],
                    metric=metric,
                )
                bootstrap_rows.append(
                    {
                        "model": candidate_name,
                        "baseline_model": baseline_name,
                        "metric": metric,
                        **date_block_bootstrap_delta(
                            predictions,
                            candidate_loss,
                            baseline_loss,
                            draws=bootstrap_draws,
                            seed=20260809,
                        ),
                    }
                )
    all_trades = []
    trade_summaries = []
    for probability_column in probability_columns:
        model_name = probability_column.removeprefix("p_")
        trades = _trade_replay(predictions, probability_column)
        trades["model"] = model_name
        all_trades.append(trades)
        bootstrap = _pnl_bootstrap(
            trades, draws=bootstrap_draws, seed=20260809
        )
        trade_summaries.append(
            {
                "model": model_name,
                "trades": int(len(trades)),
                "target_dates": int(trades["target_date"].nunique()),
                "wins": int(trades["won_no"].sum()),
                "win_rate": float(trades["won_no"].mean()) if len(trades) else float("nan"),
                "cash_cost_5": float(trades["cash_cost_5"].sum()),
                "pnl_5": float(trades["pnl_5"].sum()),
                "roi": bootstrap["roi"],
                "roi_ci_low": bootstrap["ci_low"],
                "roi_ci_high": bootstrap["ci_high"],
            }
        )
    denominator.update(
        {
            "oof_test_rows": int(len(predictions)),
            "oof_test_dates": int(predictions["target_date"].nunique()),
            "min_train_dates": int(min_train_dates),
            "fit_weighting": "equal total sample weight per target_date",
            "include_ladder_features": bool(include_ladder_features),
            "eligibility": (
                "valid causal event/book clocks, binary settlement, valid probabilities, "
                "two-sided ordered quote; caller-supplied source scope; no internal "
                "price/edge threshold gate"
            ),
            "trade_expression": "BUY NO when posterior > official-fee-adjusted 5-share cost; first per target_date/bracket",
        }
    )
    return PosteriorResearchResult(
        predictions=predictions,
        scores=pd.DataFrame(score_rows),
        bootstrap=pd.DataFrame(bootstrap_rows),
        calibration=pd.concat(calibration_rows, ignore_index=True),
        slice_scores=_slice_probability_scores(predictions, probability_columns),
        trades=pd.concat(all_trades, ignore_index=True),
        trade_summary=pd.DataFrame(trade_summaries),
        trade_slices=_slice_trade_summary(pd.concat(all_trades, ignore_index=True)),
        folds=pd.DataFrame(fold_rows),
        denominator=denominator,
    )
