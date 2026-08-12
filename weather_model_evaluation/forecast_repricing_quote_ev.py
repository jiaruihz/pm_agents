"""Quote-level executable-value research for D-1 forecast repricing.

The legacy position head predicted a rung's *relative* 60 minute bid move and
then treated that prediction as absolute cash PnL at best bid.  This module
uses the economically relevant grain instead::

    forecast event x rung x post-only quote action

The archive does not contain our own order lifecycle.  Therefore ``ask_touch``
is deliberately named a proxy, actual fills are always reported as zero, and
the resulting policy may only be used for zero-notional scoring.  ``NO_QUOTE``
is a first-class and expected result.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import brier_score_loss, mean_absolute_error, roc_auc_score

from .forecast_repricing_position import (
    IDENTITY_COLUMNS,
    INTERACTION_FEATURES,
    MARKET_BASELINE_FEATURES,
    MICROSTRUCTURE_FEATURES,
    _native_entry_tick,
    add_full_ladder_position_features,
    weather_fee,
)


SCHEMA_VERSION = "forecast_repricing_quote_ev_policy_v1"
MODEL_ID = "forecast_repricing_quote_ev_v1"
QUOTE_CHECKPOINT_MIN = 30
LIQUIDATION_CHECKPOINT_MIN = 60
SIGNAL_TTL_MIN = 30
QUOTE_ACTIONS = (
    "best_bid",
    "bid_plus_tick",
    "bid_plus_three_ticks",
    "bid_plus_half_cent",
    "quarter_spread",
    "midpoint",
    "ask_minus_tick",
)
QUOTE_ACTION_FEATURES = (
    "native_entry_tick",
    "quote_price",
    "quote_improvement",
    "quote_improvement_ticks",
    "quote_spread_fraction",
    "quote_relative_to_mid",
)
STATIC_MICROSTRUCTURE_FEATURES = tuple(
    feature for feature in MICROSTRUCTURE_FEATURES if feature not in INTERACTION_FEATURES
)
MARKET_QUOTE_FEATURES = tuple(MARKET_BASELINE_FEATURES) + QUOTE_ACTION_FEATURES
MARKET_STATIC_QUOTE_FEATURES = (
    tuple(MARKET_BASELINE_FEATURES)
    + STATIC_MICROSTRUCTURE_FEATURES
    + QUOTE_ACTION_FEATURES
)
QUOTE_FEATURES = tuple(MICROSTRUCTURE_FEATURES) + QUOTE_ACTION_FEATURES


def _floor_to_tick(value: pd.Series, tick: pd.Series) -> pd.Series:
    return np.floor((value + 1e-10) / tick) * tick


def materialize_quote_actions(frame: pd.DataFrame) -> pd.DataFrame:
    """Expand rungs into unique, legal post-only quote actions.

    Labels are explicit proxies: a quote is touched when the minimum displayed
    ask in the first 30 minutes reaches the quote.  Its liquidation value is
    the executable bid at event+60m.  This is not an inferred fill and is not
    fill-time-relative PnL.
    """

    work = add_full_ladder_position_features(frame)
    inferred_tick = _native_entry_tick(work)
    if "native_tick_size" in work:
        supplied_tick = pd.to_numeric(work["native_tick_size"], errors="coerce")
        invalid = supplied_tick.notna() & supplied_tick.le(0.0)
        if invalid.any():
            raise ValueError("native_tick_size must be positive when supplied")
        work["native_entry_tick"] = supplied_tick.fillna(inferred_tick)
        work["tick_lineage"] = np.where(
            supplied_tick.notna(), "supplied_market_metadata", "historical_inferred"
        )
    else:
        work["native_entry_tick"] = inferred_tick
        work["tick_lineage"] = "historical_inferred"
    bid = pd.to_numeric(work["entry_bid"], errors="coerce")
    ask = pd.to_numeric(work["entry_ask"], errors="coerce")
    tick = work["native_entry_tick"]
    spread = ask - bid
    raw_actions = {
        "best_bid": bid,
        "bid_plus_tick": bid + tick,
        "bid_plus_three_ticks": bid + 3.0 * tick,
        "bid_plus_half_cent": _floor_to_tick(bid + 0.005, tick),
        "quarter_spread": _floor_to_tick(bid + 0.25 * spread, tick),
        "midpoint": _floor_to_tick((bid + ask) / 2.0, tick),
        "ask_minus_tick": ask - tick,
    }
    outputs: list[pd.DataFrame] = []
    for action_id, action in enumerate(QUOTE_ACTIONS):
        quoted = work.copy()
        quoted["quote_action"] = action
        quoted["quote_price"] = raw_actions[action]
        outputs.append(quoted)
    quotes = pd.concat(outputs, ignore_index=True)
    quotes = quotes.loc[
        quotes["quote_price"].notna()
        & quotes["entry_bid"].notna()
        & quotes["entry_ask"].notna()
    ].copy()
    quotes = quotes.loc[
        quotes["quote_price"].ge(quotes["entry_bid"] - 1e-12)
        & quotes["quote_price"].lt(quotes["entry_ask"] - 1e-12)
    ].copy()
    quotes = quotes.drop_duplicates(
        [*IDENTITY_COLUMNS, "quote_price"], keep="first"
    )
    quotes["quote_improvement"] = quotes["quote_price"] - quotes["entry_bid"]
    quotes["quote_improvement_ticks"] = (
        quotes["quote_improvement"] / quotes["native_entry_tick"]
    )
    quotes["quote_spread_fraction"] = (
        quotes["quote_improvement"] / quotes["entry_spread"].replace(0.0, np.nan)
    )
    quotes["quote_relative_to_mid"] = quotes["quote_price"] / (
        (quotes["entry_bid"] + quotes["entry_ask"]) / 2.0
    ).replace(0.0, np.nan)
    min_ask = pd.to_numeric(quotes.get("h30_window_min_ask"), errors="coerce")
    exit_bid = pd.to_numeric(quotes.get("h60_bid"), errors="coerce")
    quotes["ask_touch_30_proxy"] = (min_ask <= quotes["quote_price"] + 1e-12).where(
        min_ask.notna()
    )
    quotes["liquidation_net_bid_60"] = exit_bid - weather_fee(exit_bid)
    quotes["touch_conditional_net_pnl_60"] = (
        quotes["liquidation_net_bid_60"] - quotes["quote_price"]
    ).where(exit_bid.notna())
    quotes["proxy_expected_pnl_label"] = np.where(
        quotes["ask_touch_30_proxy"].eq(True),
        quotes["touch_conditional_net_pnl_60"],
        0.0,
    )
    quotes.loc[
        quotes["ask_touch_30_proxy"].isna()
        | quotes["touch_conditional_net_pnl_60"].isna(),
        "proxy_expected_pnl_label",
    ] = np.nan
    numeric = quotes.select_dtypes(include=[np.number]).columns
    quotes.loc[:, numeric] = quotes.loc[:, numeric].replace([np.inf, -np.inf], np.nan)
    return quotes


def _date_weights(frame: pd.DataFrame) -> np.ndarray:
    events = frame.groupby("target_date")["forecast_event_id"].transform("nunique")
    rungs = frame.groupby(["target_date", "forecast_event_id"])["condition_id"].transform("nunique")
    actions = frame.groupby([*IDENTITY_COLUMNS])["quote_price"].transform("nunique")
    weights = 1.0 / events.clip(lower=1) / rungs.clip(lower=1) / actions.clip(lower=1)
    return (weights / weights.mean()).to_numpy(float)


def _direct_model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        max_iter=200,
        learning_rate=0.04,
        max_leaf_nodes=15,
        min_samples_leaf=100,
        l2_regularization=30.0,
        loss="squared_error",
        random_state=20260812,
    )


def _fit_models(train: pd.DataFrame) -> dict[str, Any]:
    usable = train.dropna(subset=["proxy_expected_pnl_label"]).copy()
    if usable.empty:
        raise ValueError("no quote-level proxy labels available")
    weights = _date_weights(usable)
    direct = _direct_model()
    market_direct = _direct_model()
    market_static_direct = _direct_model()
    touch = HistGradientBoostingClassifier(
        max_iter=200,
        learning_rate=0.04,
        max_leaf_nodes=15,
        min_samples_leaf=100,
        l2_regularization=30.0,
        random_state=20260813,
    )
    direct.fit(usable[list(QUOTE_FEATURES)], usable["proxy_expected_pnl_label"], sample_weight=weights)
    market_direct.fit(
        usable[list(MARKET_QUOTE_FEATURES)],
        usable["proxy_expected_pnl_label"],
        sample_weight=weights,
    )
    market_static_direct.fit(
        usable[list(MARKET_STATIC_QUOTE_FEATURES)],
        usable["proxy_expected_pnl_label"],
        sample_weight=weights,
    )
    touch.fit(usable[list(QUOTE_FEATURES)], usable["ask_touch_30_proxy"].astype(int), sample_weight=weights)
    touched = usable.loc[usable["ask_touch_30_proxy"].eq(True)].copy()
    if touched.empty:
        raise ValueError("no touched quote proxies available")
    value = HistGradientBoostingRegressor(
        max_iter=200,
        learning_rate=0.04,
        max_leaf_nodes=15,
        min_samples_leaf=50,
        l2_regularization=30.0,
        loss="absolute_error",
        random_state=20260814,
    )
    value.fit(
        touched[list(QUOTE_FEATURES)],
        touched["touch_conditional_net_pnl_60"],
        sample_weight=_date_weights(touched),
    )
    return {
        "direct_model": direct,
        "market_direct_model": market_direct,
        "market_static_direct_model": market_static_direct,
        "touch_model": touch,
        "value_model": value,
    }


def _score(frame: pd.DataFrame, models: Mapping[str, Any]) -> pd.DataFrame:
    scored = frame.copy()
    features = scored[list(QUOTE_FEATURES)]
    scored["predicted_direct_proxy_ev"] = models["direct_model"].predict(features)
    scored["predicted_market_proxy_ev"] = models["market_direct_model"].predict(
        scored[list(MARKET_QUOTE_FEATURES)]
    )
    scored["predicted_market_static_proxy_ev"] = models[
        "market_static_direct_model"
    ].predict(scored[list(MARKET_STATIC_QUOTE_FEATURES)])
    scored["predicted_touch_probability"] = models["touch_model"].predict_proba(features)[:, 1]
    scored["predicted_touch_conditional_pnl"] = models["value_model"].predict(features)
    scored["predicted_two_head_proxy_ev"] = (
        scored["predicted_touch_probability"]
        * scored["predicted_touch_conditional_pnl"]
    )
    # Agreement between independently trained direct/two-head estimates is a
    # fixed heuristic negative control, not a calibrated confidence bound.
    scored["predicted_agreement_proxy_ev"] = scored[
        ["predicted_direct_proxy_ev", "predicted_two_head_proxy_ev"]
    ].min(axis=1)
    return scored


def _select(scored: pd.DataFrame) -> pd.DataFrame:
    eligible = scored.loc[scored["predicted_agreement_proxy_ev"].gt(0.0)].copy()
    if eligible.empty:
        return eligible
    eligible = eligible.sort_values(
        ["forecast_event_id", "predicted_agreement_proxy_ev"],
        ascending=[True, False],
    ).drop_duplicates("forecast_event_id")
    return eligible.sort_values(
        ["target_date", "city", "snapshot_epoch", "predicted_agreement_proxy_ev"],
        ascending=[True, True, True, False],
    ).drop_duplicates(["target_date", "city"], keep="first")


def _expanding_oof(
    development: pd.DataFrame,
    *,
    min_train_dates: int = 15,
    block_dates: int = 14,
) -> pd.DataFrame:
    """Score development dates strictly out of sample in date blocks."""

    dates = sorted(development["target_date"].astype(str).unique())
    outputs: list[pd.DataFrame] = []
    for start in range(min_train_dates, len(dates), block_dates):
        test_dates = dates[start : start + block_dates]
        train = development.loc[development["target_date"].isin(dates[:start])]
        test = development.loc[development["target_date"].isin(test_dates)]
        if train.empty or test.empty:
            continue
        outputs.append(_score(test, _fit_models(train)))
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()


def _metrics(scored: pd.DataFrame, selected: pd.DataFrame) -> dict[str, Any]:
    labelled = scored.dropna(subset=["proxy_expected_pnl_label"]).copy()
    labels = labelled["ask_touch_30_proxy"].astype(int)
    touched = selected.loc[selected["ask_touch_30_proxy"].eq(True)].copy()
    cost = float(touched["quote_price"].sum())
    pnl = float(touched["touch_conditional_net_pnl_60"].sum())
    dates = touched.groupby("target_date").agg(
        pnl=("touch_conditional_net_pnl_60", "sum"), cost=("quote_price", "sum")
    )
    roi_ci = [math.nan, math.nan]
    if len(dates) >= 2 and dates["cost"].sum() > 0:
        rng = np.random.default_rng(20260812)
        values = dates[["pnl", "cost"]].to_numpy(float)
        draws = []
        for _ in range(2000):
            sampled = values[rng.integers(0, len(values), len(values))].sum(axis=0)
            draws.append(sampled[0] / sampled[1] if sampled[1] > 0 else np.nan)
        roi_ci = list(np.nanquantile(draws, [0.025, 0.975]))
    return {
        "quote_rows": int(len(scored)),
        "target_dates": int(scored["target_date"].nunique()),
        "selected_quotes": int(len(selected)),
        "selected_target_dates": int(selected["target_date"].nunique()) if not selected.empty else 0,
        "proxy_touches": int(len(touched)),
        "actual_fills": 0,
        "proxy_touch_rate": float(len(touched) / len(selected)) if len(selected) else math.nan,
        "proxy_pnl": pnl,
        "proxy_cost": cost,
        "proxy_roi": pnl / cost if cost > 0 else math.nan,
        "proxy_roi_ci_low": float(roi_ci[0]),
        "proxy_roi_ci_high": float(roi_ci[1]),
        "positive_proxy_touches": int(touched["touch_conditional_net_pnl_60"].gt(0.0).sum()),
        "touch_base_rate": float(labels.mean()) if len(labels) else math.nan,
        "touch_brier": float(brier_score_loss(labels, labelled["predicted_touch_probability"])) if len(labels) else math.nan,
        "touch_auc": float(roc_auc_score(labels, labelled["predicted_touch_probability"])) if labels.nunique() == 2 else math.nan,
        "direct_ev_mae": float(mean_absolute_error(labelled["proxy_expected_pnl_label"], labelled["predicted_direct_proxy_ev"])) if len(labelled) else math.nan,
        "weather_vs_market_mse_delta": _paired_loss_delta(
            labelled, "predicted_direct_proxy_ev", "predicted_market_proxy_ev"
        ),
        "weather_vs_market_static_mse_delta": _paired_loss_delta(
            labelled,
            "predicted_direct_proxy_ev",
            "predicted_market_static_proxy_ev",
        ),
        "selected_low_tick_share": float(selected["native_entry_tick"].eq(0.001).mean()) if len(selected) else math.nan,
    }


def _paired_loss_delta(
    frame: pd.DataFrame, challenger: str, baseline: str
) -> dict[str, Any]:
    work = frame.dropna(
        subset=[challenger, baseline, "proxy_expected_pnl_label"]
    ).copy()
    work["delta"] = (
        (work[challenger] - work["proxy_expected_pnl_label"]) ** 2
        - (work[baseline] - work["proxy_expected_pnl_label"]) ** 2
    )
    work["weight"] = _date_weights(work)
    by_date = work.groupby("target_date").apply(
        lambda group: float(np.average(group["delta"], weights=group["weight"])),
        include_groups=False,
    )
    if by_date.empty:
        return {"rows": 0, "target_dates": 0, "mean": math.nan, "ci_low": math.nan, "ci_high": math.nan}
    rng = np.random.default_rng(20260815)
    values = by_date.to_numpy(float)
    samples = np.asarray(
        [rng.choice(values, len(values), replace=True).mean() for _ in range(2000)]
    )
    return {
        "rows": int(len(work)),
        "target_dates": int(len(by_date)),
        "mean": float(values.mean()),
        "ci_low": float(np.quantile(samples, 0.025)),
        "ci_high": float(np.quantile(samples, 0.975)),
    }


def _funnel(quotes: pd.DataFrame, selected: pd.DataFrame) -> dict[str, Any]:
    scoreable = quotes.dropna(subset=["proxy_expected_pnl_label"])
    positive = quotes.loc[quotes["predicted_agreement_proxy_ev"].gt(0.0)]
    event_best = positive.sort_values(
        ["forecast_event_id", "predicted_agreement_proxy_ev"],
        ascending=[True, False],
    ).drop_duplicates("forecast_event_id")

    def tick_rows(frame: pd.DataFrame) -> dict[str, int]:
        return {
            "tick_0_001": int(frame["native_entry_tick"].eq(0.001).sum()),
            "tick_0_01": int(frame["native_entry_tick"].eq(0.01).sum()),
        }

    return {
        "signal": {
            "rungs": int(quotes[list(IDENTITY_COLUMNS)].drop_duplicates().shape[0]),
            "forecast_events": int(quotes["forecast_event_id"].nunique()),
            "legal_quote_actions": int(len(quotes)),
            "legal_quote_actions_by_tick": tick_rows(quotes),
            "positive_ev_quote_actions": int(len(positive)),
            "positive_ev_events": int(positive["forecast_event_id"].nunique()),
            "event_best_quotes": int(len(event_best)),
            "selected_quotes": int(len(selected)),
            "selected_by_tick": tick_rows(selected),
            "no_admitted_quote_events": int(
                quotes["forecast_event_id"].nunique()
                - positive["forecast_event_id"].nunique()
            ),
            "event_best_removed_by_city_day_state": int(len(event_best) - len(selected)),
        },
        "evidence": {
            "proxy_scoreable_quotes": int(len(scoreable)),
            "proxy_touch_quotes": int(scoreable["ask_touch_30_proxy"].eq(True).sum()),
            "actual_fills": 0,
            "first_touch_clock_available": 0,
        },
    }


def train_quote_ev_policy(
    frame: pd.DataFrame,
    *,
    holdout_dates: int = 15,
) -> dict[str, Any]:
    quotes = materialize_quote_actions(frame)
    labelled = quotes.dropna(subset=["proxy_expected_pnl_label"]).copy()
    dates = sorted(labelled["target_date"].astype(str).unique())
    if len(dates) < holdout_dates + 15:
        raise ValueError(f"need at least {holdout_dates + 15} labelled target dates, got {len(dates)}")
    frozen_dates = dates[-holdout_dates:]
    development = labelled.loc[~labelled["target_date"].isin(frozen_dates)].copy()
    holdout = labelled.loc[labelled["target_date"].isin(frozen_dates)].copy()
    development_oof = _expanding_oof(development)
    development_oof_selected = _select(development_oof)
    models = _fit_models(development)
    holdout_scored = _score(holdout, models)
    holdout_selected = _select(holdout_scored)
    bundle = {
        "schema_version": SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "policy_kind": "quote_level_proxy_ev_no_trade_capable",
        "quote_actions": list(QUOTE_ACTIONS),
        "quote_features": list(QUOTE_FEATURES),
        "market_quote_features": list(MARKET_QUOTE_FEATURES),
        "market_static_quote_features": list(MARKET_STATIC_QUOTE_FEATURES),
        "entry_threshold_dollars_per_share": 0.0,
        "quote_checkpoint_min": QUOTE_CHECKPOINT_MIN,
        "signal_ttl_min": SIGNAL_TTL_MIN,
        "hard_exit_min_after_actual_fill": LIQUIDATION_CHECKPOINT_MIN,
        "fill_evidence": "ask_touch_proxy_only",
        "live_eligible": False,
        **models,
    }
    return {
        "quotes": quotes,
        "development_oof": development_oof,
        "holdout_scored": holdout_scored,
        "development_oof_selected": development_oof_selected,
        "holdout_selected": holdout_selected,
        "development_dates": dates[:-holdout_dates],
        "holdout_dates": frozen_dates,
        "development_oof_metrics": _metrics(development_oof, development_oof_selected),
        "holdout_metrics": _metrics(holdout_scored, holdout_selected),
        "development_funnel": _funnel(development_oof, development_oof_selected),
        "holdout_funnel": _funnel(holdout_scored, holdout_selected),
        "bundle": bundle,
    }


def score_runtime_quote_ev(
    rungs: Sequence[Mapping[str, Any]],
    bundle: Mapping[str, Any],
    *,
    event_identity: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if bundle.get("schema_version") != SCHEMA_VERSION or bundle.get("model_id") != MODEL_ID:
        raise ValueError("quote EV bundle identity mismatch")
    rows = []
    for rung in rungs:
        tick_size = rung.get("tick_size")
        if tick_size is None:
            tick_size = rung.get("native_tick_size")
        try:
            tick_value = float(tick_size)
        except (TypeError, ValueError):
            tick_value = math.nan
        if not math.isfinite(tick_value) or tick_value <= 0.0:
            raise ValueError("runtime quote scoring requires exchange native tick_size")
        rows.append(
            {
                **event_identity,
                **dict(rung),
                "condition_id": rung.get("condition_id"),
                "bracket": rung.get("bracket"),
                "model_probability_before": rung.get("model_probability_before"),
                "model_probability_after": rung.get("model_probability_after"),
                "market_probability_before": rung.get("market_probability_before"),
                "market_probability_after": rung.get("market_probability_after"),
                "entry_bid": rung.get("yes_bid"),
                "entry_ask": rung.get("yes_ask"),
                "entry_bid_size": rung.get("yes_bid_size"),
                "entry_ask_size": rung.get("yes_ask_size"),
                "native_tick_size": tick_value,
                "h30_bid": np.nan,
                "h60_bid": np.nan,
                "h30_window_min_ask": np.nan,
            }
        )
    quotes = materialize_quote_actions(pd.DataFrame(rows))
    tick_units = quotes["quote_price"] / quotes["native_entry_tick"]
    if not np.allclose(tick_units, tick_units.round(), atol=1e-8):
        raise ValueError("runtime quote price is not aligned to exchange tick_size")
    scored = _score(quotes, bundle)
    selected = _select(scored)
    records = scored.to_dict(orient="records")
    if selected.empty:
        return records, None
    chosen = selected.iloc[0].to_dict()
    chosen["maker_limit_price"] = chosen["quote_price"]
    chosen["predicted_quote_ev"] = chosen["predicted_agreement_proxy_ev"]
    chosen["signal_ttl_min"] = float(bundle["signal_ttl_min"])
    decision_epoch = float(event_identity["snapshot_epoch"])
    chosen["signal_expires_epoch"] = decision_epoch + 60.0 * float(
        bundle["signal_ttl_min"]
    )
    chosen["feature_book_snapshot_id"] = event_identity.get(
        "feature_book_snapshot_id"
    )
    return records, chosen


def score_runtime_pending_quote(
    pending: Mapping[str, Any],
    current_rungs: Sequence[Mapping[str, Any]],
    bundle: Mapping[str, Any],
    *,
    elapsed_signal_minutes: float,
) -> dict[str, Any]:
    """Re-score a resting quote; a trade-through never becomes a fill here."""

    if elapsed_signal_minutes >= float(bundle["signal_ttl_min"]):
        return {"action": "CANCEL_MAKER", "reason": "signal_ttl"}
    entry_ladder = list(pending.get("entry_ladder") or [])
    current = {str(row.get("condition_id")): row for row in current_rungs}
    if not entry_ladder or set(current) != {
        str(row.get("condition_id")) for row in entry_ladder
    }:
        return {"action": "CANCEL_MAKER", "reason": "incomplete_current_ladder"}
    runtime_rows = []
    mids: dict[str, float] = {}
    for condition, observed in current.items():
        bid = float(observed["yes_bid"])
        ask = float(observed["yes_ask"])
        mids[condition] = (bid + ask) / 2.0
    mid_sum = sum(mids.values())
    if mid_sum <= 0.0:
        return {"action": "CANCEL_MAKER", "reason": "invalid_current_ladder"}
    for entry in entry_ladder:
        observed = current[str(entry["condition_id"])]
        runtime_rows.append(
            {
                **entry,
                "yes_bid": observed.get("yes_bid"),
                "yes_ask": observed.get("yes_ask"),
                "yes_bid_size": observed.get("yes_bid_size"),
                "yes_ask_size": observed.get("yes_ask_size"),
                "tick_size": observed.get("tick_size") or entry.get("native_tick_size"),
                "market_probability_after": (
                    mids[str(entry["condition_id"])] / mid_sum
                ),
            }
        )
    _, selected = score_runtime_quote_ev(
        runtime_rows,
        bundle,
        event_identity={
            "forecast_event_id": pending["forecast_event_id"],
            "city": pending["city"],
            "target_date": pending["target_date"],
            "lead_days": pending.get("lead_days", 1),
            "snapshot_epoch": pending["decision_epoch"],
            "feature_book_snapshot_id": pending.get("feature_book_snapshot_id"),
        },
    )
    if selected is None:
        return {"action": "CANCEL_MAKER", "reason": "quote_ev_nonpositive"}
    if str(selected["condition_id"]) != str(pending["condition_id"]):
        return {"action": "CANCEL_MAKER", "reason": "ladder_opportunity_moved"}
    if float(selected["maker_limit_price"]) < float(pending["maker_limit_price"]) - 1e-12:
        return {"action": "CANCEL_MAKER", "reason": "reservation_price_deteriorated"}
    observed = current[str(pending["condition_id"])]
    possible_fill = float(observed["yes_ask"]) <= float(pending["maker_limit_price"])
    return {
        "action": "POSSIBLE_FILL" if possible_fill else "KEEP_MAKER",
        "reason": (
            "trade_through_requires_own_fill_evidence"
            if possible_fill
            else "quote_ev_still_positive"
        ),
        "predicted_quote_ev": selected["predicted_quote_ev"],
        "reservation_price": selected["maker_limit_price"],
    }


def score_runtime_quote_position(
    position: Mapping[str, Any],
    *,
    elapsed_fill_minutes: float,
) -> dict[str, Any]:
    """Closed-loop fallback exit until a fill-relative continuation head passes.

    The previous dynamic exit head failed its paired fixed-60 comparison.  A
    position therefore uses a fill-relative 60 minute hard exit; forecast
    signal age is tracked separately by the caller and never resets on fill.
    """

    if not position.get("actual_fill_id") or position.get("filled_at_utc") is None:
        raise ValueError("position clock requires actual fill evidence")
    if elapsed_fill_minutes >= LIQUIDATION_CHECKPOINT_MIN:
        return {"action": "EXIT", "reason": "fill_relative_hard_timeout"}
    return {"action": "HOLD", "reason": "before_fill_relative_hard_timeout"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(float(value)) else None
    return value


def write_outputs(result: Mapping[str, Any], input_path: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_columns = [
        *IDENTITY_COLUMNS,
        "snapshot_epoch",
        "native_entry_tick",
        "tick_lineage",
        "quote_action",
        "quote_price",
        "quote_improvement",
        "quote_spread_fraction",
        "ask_touch_30_proxy",
        "touch_conditional_net_pnl_60",
        "proxy_expected_pnl_label",
        "predicted_market_proxy_ev",
        "predicted_market_static_proxy_ev",
        "predicted_direct_proxy_ev",
        "predicted_touch_probability",
        "predicted_touch_conditional_pnl",
        "predicted_two_head_proxy_ev",
        "predicted_agreement_proxy_ev",
    ]
    result["development_oof"][audit_columns].to_csv(
        output_dir / "development_oof_quote_scores.csv", index=False
    )
    result["holdout_scored"][audit_columns].to_csv(
        output_dir / "holdout_quote_scores.csv", index=False
    )
    result["holdout_selected"].to_csv(output_dir / "holdout_selected_quotes.csv", index=False)
    result["development_oof_selected"].to_csv(output_dir / "development_oof_selected_quotes.csv", index=False)
    artifact = output_dir / "quote_ev_policy.joblib"
    joblib.dump(result["bundle"], artifact)
    holdout = result["holdout_metrics"]
    status = (
        "proxy_positive_but_execution_unverified"
        if holdout["selected_quotes"] > 0 and holdout["proxy_pnl"] > 0
        else "no_admitted_quote_rejected_for_expression"
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "denominator_scope": "D-1 forecast event x full-ladder rung x legal post-only quote action; 30m ask-touch proxy; event+60m executable bid liquidation",
        "input_path": str(input_path),
        "input_sha256": _sha256(input_path),
        "development_dates": result["development_dates"],
        "holdout_dates": result["holdout_dates"],
        "development_oof": result["development_oof_metrics"],
        "holdout": result["holdout_metrics"],
        "development_funnel": result["development_funnel"],
        "holdout_funnel": result["holdout_funnel"],
        "qualification": {
            "formal_forward": False,
            "secondary_holdout_seen_during_research": True,
            "live_eligible": False,
        },
        "execution_contract": {
            "actual_fills": 0,
            "touch_is_fill": False,
            "queue_model": "unavailable_in_historical_rest_archive",
            "position_clock": "must_start_from_actual_fill_at_runtime",
            "signal_clock": "starts_from_forecast_revision",
        },
        "artifact": {"path": str(artifact), "sha256": _sha256(artifact)},
        "production": {"live_action": "none", "orders_changed": 0},
    }
    (output_dir / "summary.json").write_text(
        json.dumps(_json_safe(summary), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--holdout-dates", type=int, default=15)
    args = parser.parse_args(argv)
    frame = pd.read_csv(args.input, low_memory=False)
    result = train_quote_ev_policy(frame, holdout_dates=args.holdout_dates)
    summary = write_outputs(result, args.input, args.output_dir)
    print(json.dumps(_json_safe(summary), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
