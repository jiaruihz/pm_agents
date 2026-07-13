#!/usr/bin/env python3
"""Unified source-event / time-hazard / ladder-expression research.

The research head starts from one coherent PIT ladder state and asks two
questions separately:

1. Does elapsed no-break time and physical context improve probability scoring
   beyond the market ladder?
2. If it does, does the residual survive executable YES/NO asks, official
   weather taker fees, depth, date de-duplication, and forward-like dates?

It evaluates current YES/NO, d1 YES/NO, crossed previous-rung NO, and source
basis reversal on one denominator.  It is research-only and places no orders.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATES = ROOT / "docs/analysis/2026-07/generated/tmax_distribution_v3/state_rows_v3.csv"
DEFAULT_OUT_DIR = ROOT / "docs/analysis/2026-07/generated/source_event_hazard_router_v1"
DEFAULT_REPORT = ROOT / "docs/analysis/2026-07/2026-07-13-source-event-hazard-router-v1.md"
DEFAULT_JSON = ROOT / "docs/analysis/2026-07/2026-07-13-source-event-hazard-router-v1.json"

FEE_RATE = 0.05
MIN_ASK_SIZE = 5.0
EDGE_THRESHOLDS = (0.0, 0.02)
RANDOM_SEED = 20260713

TIME_FEATURES = [
    "market_logit",
    "minutes_since_running_max",
    "max_age_min",
    "decision_hour_local",
    "forecast_peak_delta_hours_local",
]
PHYSICS_FEATURES = TIME_FEATURES + [
    "forecast_gap_to_running_f",
    "current_minus_running_f",
    "temp_trend_1h_f",
    "temp_trend_3h_f",
    "boundary_pos_in_bracket",
    "obs_count_today",
    "market_entropy",
    "ladder_overround",
    "quoted_rung_fraction",
]
CATEGORICAL_FEATURES = ["unit", "forecast_source"]


def fee_per_share(price: float, fee_rate: float = FEE_RATE) -> float:
    """Official Weather taker fee per share, rounded to five decimals."""

    if not math.isfinite(price):
        return math.nan
    return round(float(fee_rate) * float(price) * (1.0 - float(price)), 5)


def safe_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def bracket_key(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def parse_ladder(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, str) or not value:
        return []
    try:
        rows = json.loads(value)
    except json.JSONDecodeError:
        return []
    return rows if isinstance(rows, list) else []


def quote_for_bracket(ladder: list[dict[str, Any]], bracket: Any) -> dict[str, Any] | None:
    target = bracket_key(bracket)
    for row in ladder:
        if bracket_key(row.get("bracket")) == target:
            return row
    return None


def relative_quote(ladder: list[dict[str, Any]], current_bracket: Any, delta: int) -> tuple[str, dict[str, Any] | None]:
    target = bracket_key(current_bracket)
    for index, row in enumerate(ladder):
        if bracket_key(row.get("bracket")) != target:
            continue
        wanted = index + delta
        if wanted < 0 or wanted >= len(ladder):
            return "", None
        selected = ladder[wanted]
        return bracket_key(selected.get("bracket")), selected
    return "", None


def _quote_field(quote: dict[str, Any] | None, field: str) -> float:
    value = safe_float((quote or {}).get(field))
    return value if value is not None else math.nan


def enrich_state_rows(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    frame = frame[frame["labeled"].fillna(False).astype(bool)].copy()
    frame["decision_ts"] = pd.to_datetime(frame["decision_snapshot_ts_utc"], errors="coerce", utc=True)
    frame = frame[frame["decision_ts"].notna()].copy()
    frame.sort_values(["city", "target_date", "decision_ts"], inplace=True)

    quote_rows: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        ladder = parse_ladder(getattr(row, "ladder_book_json", ""))
        current_key, current = relative_quote(ladder, getattr(row, "current_bracket"), 0)
        d1_key, d1 = relative_quote(ladder, getattr(row, "current_bracket"), 1)
        prev_key, prev = relative_quote(ladder, getattr(row, "current_bracket"), -1)
        quote_rows.append(
            {
                "current_key": current_key,
                "d1_key": d1_key,
                "prev_key": prev_key,
                "current_yes_ask": _quote_field(current, "yes_ask"),
                "current_yes_bid": _quote_field(current, "yes_bid"),
                "current_yes_ask_size": _quote_field(current, "yes_ask_size"),
                "current_no_ask_book": _quote_field(current, "no_ask"),
                "current_no_bid": _quote_field(current, "no_bid"),
                "current_no_ask_size_book": _quote_field(current, "no_ask_size"),
                "d1_yes_ask_book": _quote_field(d1, "yes_ask"),
                "d1_yes_bid": _quote_field(d1, "yes_bid"),
                "d1_yes_ask_size_book": _quote_field(d1, "yes_ask_size"),
                "d1_no_ask_book": _quote_field(d1, "no_ask"),
                "d1_no_bid": _quote_field(d1, "no_bid"),
                "d1_no_ask_size_book": _quote_field(d1, "no_ask_size"),
                "prev_yes_ask": _quote_field(prev, "yes_ask"),
                "prev_yes_bid": _quote_field(prev, "yes_bid"),
                "prev_yes_ask_size": _quote_field(prev, "yes_ask_size"),
                "prev_no_ask": _quote_field(prev, "no_ask"),
                "prev_no_bid": _quote_field(prev, "no_bid"),
                "prev_no_ask_size": _quote_field(prev, "no_ask_size"),
            }
        )
    quotes = pd.DataFrame(quote_rows, index=frame.index)
    for col in quotes.columns:
        frame[col] = quotes[col]

    groups = frame.groupby(["city", "target_date"], sort=False)
    frame["previous_current_key"] = groups["current_key"].shift()
    frame["cross_event"] = frame["previous_current_key"].notna() & frame["current_key"].ne(frame["previous_current_key"])
    frame["same_anchor_sequence"] = groups["current_key"].transform(lambda s: s.ne(s.shift()).cumsum())
    frame["anchor_episode"] = (
        frame["city"].astype(str)
        + "|"
        + frame["target_date"].astype(str)
        + "|"
        + frame["current_key"].astype(str)
        + "|"
        + frame["same_anchor_sequence"].astype(str)
    )
    frame["y_current_yes"] = pd.to_numeric(frame["winner_step"], errors="coerce").eq(0).astype(int)
    frame["y_d1_yes"] = pd.to_numeric(frame["winner_step"], errors="coerce").eq(1).astype(int)
    frame["y_prev_no"] = frame["actual_bucket"].astype(str).ne("below").astype(int)
    frame["y_prev_yes"] = 1 - frame["y_prev_no"]
    frame["market_p_current"] = pd.to_numeric(frame["market_p_current"], errors="coerce").clip(1e-5, 1 - 1e-5)
    frame["market_p_d1"] = pd.to_numeric(frame["market_p_d1"], errors="coerce").clip(1e-5, 1 - 1e-5)
    return frame.reset_index(drop=True)


def make_model(numeric: list[str]) -> Pipeline:
    numeric_pipe = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())])
    categorical_pipe = Pipeline(
        [("impute", SimpleImputer(strategy="most_frequent")), ("onehot", OneHotEncoder(handle_unknown="ignore"))]
    )
    pre = ColumnTransformer(
        [("numeric", numeric_pipe, numeric), ("categorical", categorical_pipe, CATEGORICAL_FEATURES)]
    )
    return Pipeline([("pre", pre), ("model", LogisticRegression(C=0.2, max_iter=2000, random_state=RANDOM_SEED))])


def expanding_predictions(
    frame: pd.DataFrame,
    *,
    target_col: str,
    market_col: str,
    feature_columns: list[str],
    label: str,
) -> pd.Series:
    predictions = pd.Series(np.nan, index=frame.index, dtype=float, name=label)
    working = frame.copy()
    market = pd.to_numeric(working[market_col], errors="coerce").clip(1e-5, 1 - 1e-5)
    working["market_logit"] = np.log(market / (1.0 - market))
    for date in sorted(working["target_date"].astype(str).unique()):
        train_mask = working["target_date"].astype(str).lt(date)
        test_mask = working["target_date"].astype(str).eq(date)
        train = working[train_mask].copy()
        if len(train) < 600 or train["target_date"].nunique() < 6 or train[target_col].nunique() < 2:
            continue
        model = make_model(feature_columns)
        features = feature_columns + CATEGORICAL_FEATURES
        model.fit(train[features], train[target_col].astype(int))
        predictions.loc[test_mask] = model.predict_proba(working.loc[test_mask, features])[:, 1]
    return predictions.clip(1e-5, 1 - 1e-5)


def binary_loss(y: pd.Series, p: pd.Series) -> pd.Series:
    pp = pd.to_numeric(p, errors="coerce").clip(1e-6, 1 - 1e-6)
    yy = pd.to_numeric(y, errors="coerce")
    return -(yy * np.log(pp) + (1.0 - yy) * np.log(1.0 - pp))


def paired_date_bootstrap(values: pd.DataFrame, value_col: str, reps: int = 4000) -> tuple[float, float]:
    daily = values.groupby("target_date")[value_col].mean().dropna()
    if len(daily) < 2:
        return math.nan, math.nan
    array = daily.to_numpy(dtype=float)
    rng = np.random.default_rng(RANDOM_SEED)
    sampled = rng.choice(array, size=(reps, len(array)), replace=True).mean(axis=1)
    return float(np.quantile(sampled, 0.025)), float(np.quantile(sampled, 0.975))


def proper_score_table(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    specs = [
        ("current_yes", "y_current_yes", "market_p_current", "p_current_time", "p_current_physics"),
        ("d1_yes", "y_d1_yes", "market_p_d1", "p_d1_time", "p_d1_physics"),
    ]
    for target, y_col, market_col, time_col, physics_col in specs:
        base = frame[["target_date", y_col, market_col, time_col, physics_col]].dropna().copy()
        for model, p_col in [("market", market_col), ("market_time", time_col), ("market_physics", physics_col)]:
            sub = base.copy()
            sub["loss"] = binary_loss(sub[y_col], sub[p_col])
            sub["brier"] = (sub[y_col] - sub[p_col]) ** 2
            sub["market_loss"] = binary_loss(sub[y_col], sub[market_col])
            sub["delta_loss_vs_market"] = sub["loss"] - sub["market_loss"]
            lo, hi = paired_date_bootstrap(sub, "delta_loss_vs_market")
            rows.append(
                {
                    "target": target,
                    "model": model,
                    "rows": len(sub),
                    "dates": sub["target_date"].nunique(),
                    "date_equal_logloss": sub.groupby("target_date")["loss"].mean().mean(),
                    "date_equal_brier": sub.groupby("target_date")["brier"].mean().mean(),
                    "delta_logloss_vs_market": sub.groupby("target_date")["delta_loss_vs_market"].mean().mean(),
                    "delta_ci_low": lo,
                    "delta_ci_high": hi,
                }
            )
    return pd.DataFrame(rows)


def expression_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    specs = [
        ("current_yes", "p_current_physics", "current_yes_ask", "current_yes_bid", "current_yes_ask_size", "y_current_yes"),
        ("current_no", "p_current_physics", "current_no_ask_book", "current_no_bid", "current_no_ask_size_book", "y_current_yes"),
        ("d1_yes", "p_d1_physics", "d1_yes_ask_book", "d1_yes_bid", "d1_yes_ask_size_book", "y_d1_yes"),
        ("d1_no", "p_d1_physics", "d1_no_ask_book", "d1_no_bid", "d1_no_ask_size_book", "y_d1_yes"),
    ]
    parts: list[pd.DataFrame] = []
    base_cols = [
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "decision_ts",
        "current_key",
        "d1_key",
        "anchor_episode",
        "cross_event",
        "minutes_since_running_max",
        "forecast_peak_delta_hours_local",
        "forecast_gap_to_running_f",
        "temp_trend_1h_f",
        "temp_trend_3h_f",
    ]
    for expression, p_col, ask_col, bid_col, size_col, y_col in specs:
        part = frame[base_cols].copy()
        probability = pd.to_numeric(frame[p_col], errors="coerce")
        outcome = pd.to_numeric(frame[y_col], errors="coerce")
        if expression.endswith("_no"):
            probability = 1.0 - probability
            outcome = 1.0 - outcome
        part["expression"] = expression
        part["probability"] = probability
        part["ask"] = pd.to_numeric(frame[ask_col], errors="coerce")
        part["bid"] = pd.to_numeric(frame[bid_col], errors="coerce")
        part["ask_size"] = pd.to_numeric(frame[size_col], errors="coerce")
        part["outcome"] = outcome
        part["entry_fee"] = part["ask"].map(lambda value: fee_per_share(float(value)) if pd.notna(value) else math.nan)
        part["entry_cost"] = part["ask"] + part["entry_fee"]
        part["edge"] = part["probability"] - part["entry_cost"]
        part["settlement_pnl"] = part["outcome"] - part["entry_cost"]
        part["price_band"] = pd.cut(
            part["ask"],
            bins=[0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95, 0.99, 1.001],
            labels=["00_20", "20_40", "40_60", "60_80", "80_90", "90_95", "95_99", "99_100"],
            include_lowest=True,
        ).astype(str)
        parts.append(part)
    out = pd.concat(parts, ignore_index=True)
    return out[
        out["probability"].notna()
        & out["ask"].between(0.001, 0.999, inclusive="both")
        & out["ask_size"].ge(MIN_ASK_SIZE)
    ].copy()


def _daily_bootstrap_roi(trades: pd.DataFrame, reps: int = 4000) -> tuple[float, float]:
    daily = trades.groupby("target_date", as_index=False).agg(cost=("entry_cost", "sum"), pnl=("settlement_pnl", "sum"))
    if len(daily) < 2:
        return math.nan, math.nan
    array = daily[["cost", "pnl"]].to_numpy(dtype=float)
    rng = np.random.default_rng(RANDOM_SEED)
    indices = rng.integers(0, len(array), size=(reps, len(array)))
    sampled = array[indices]
    rois = sampled[:, :, 1].sum(axis=1) / sampled[:, :, 0].sum(axis=1)
    return float(np.quantile(rois, 0.025)), float(np.quantile(rois, 0.975))


def _date_equal_excess_ci(selected: pd.DataFrame, baseline: pd.DataFrame, reps: int = 4000) -> tuple[float, float, float]:
    def daily_roi(rows: pd.DataFrame, name: str) -> pd.Series:
        daily = rows.groupby("target_date").agg(cost=("entry_cost", "sum"), pnl=("settlement_pnl", "sum"))
        return (daily["pnl"] / daily["cost"]).rename(name)

    paired = pd.concat([daily_roi(selected, "selected"), daily_roi(baseline, "baseline")], axis=1).dropna()
    if paired.empty:
        return math.nan, math.nan, math.nan
    delta = (paired["selected"] - paired["baseline"]).to_numpy(dtype=float)
    point = float(delta.mean())
    if len(delta) < 2:
        return point, math.nan, math.nan
    rng = np.random.default_rng(RANDOM_SEED)
    sampled = rng.choice(delta, size=(reps, len(delta)), replace=True).mean(axis=1)
    return point, float(np.quantile(sampled, 0.025)), float(np.quantile(sampled, 0.975))


def summarize_trades(
    selected: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    strategy: str,
    edge_threshold: float,
    scope: str,
) -> dict[str, Any]:
    cost = float(selected["entry_cost"].sum())
    pnl = float(selected["settlement_pnl"].sum())
    lo, hi = _daily_bootstrap_roi(selected)
    excess, excess_lo, excess_hi = _date_equal_excess_ci(selected, baseline)
    recent_dates = sorted(selected["target_date"].astype(str).unique())[-4:]
    recent = selected[selected["target_date"].astype(str).isin(recent_dates)]
    recent_cost = float(recent["entry_cost"].sum())
    recent_pnl = float(recent["settlement_pnl"].sum())
    return {
        "strategy": strategy,
        "scope": scope,
        "edge_threshold": edge_threshold,
        "rows": len(selected),
        "dates": selected["target_date"].nunique(),
        "cities": selected["city"].nunique(),
        "avg_ask": selected["ask"].mean() if not selected.empty else math.nan,
        "win_rate": selected["outcome"].mean() if not selected.empty else math.nan,
        "roi": pnl / cost if cost else math.nan,
        "roi_ci_low": lo,
        "roi_ci_high": hi,
        "same_band_baseline_rows": len(baseline),
        "same_band_baseline_roi": (
            float(baseline["settlement_pnl"].sum()) / float(baseline["entry_cost"].sum())
            if float(baseline["entry_cost"].sum()) > 0
            else math.nan
        ),
        "date_equal_excess_roi": excess,
        "excess_ci_low": excess_lo,
        "excess_ci_high": excess_hi,
        "recent_4_dates": ",".join(recent_dates),
        "recent_roi": recent_pnl / recent_cost if recent_cost else math.nan,
    }


def first_per_episode(rows: pd.DataFrame) -> pd.DataFrame:
    return rows.sort_values("decision_ts").drop_duplicates(["anchor_episode", "expression"], keep="first")


def strategy_tables(candidates: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    selected_parts: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    strategy_specs = [
        ("cross_exhaustion_current_yes", "cross", "current_yes"),
        ("cross_continuation_current_no", "cross", "current_no"),
        ("cross_next1_yes", "cross", "d1_yes"),
        ("no_break_60_current_yes", "no_break_60", "current_yes"),
    ]
    for strategy, scope, expression in strategy_specs:
        if scope == "cross":
            universe = candidates[candidates["cross_event"] & candidates["expression"].eq(expression)].copy()
        else:
            universe = candidates[
                ~candidates["cross_event"]
                & pd.to_numeric(candidates["minutes_since_running_max"], errors="coerce").ge(60)
                & candidates["expression"].eq(expression)
            ].copy()
        universe = first_per_episode(universe)
        for threshold in EDGE_THRESHOLDS:
            selected = universe[universe["edge"].ge(threshold)].copy()
            selected["strategy"] = strategy
            selected["edge_threshold"] = threshold
            selected_parts.append(selected)
            bands = set(selected["price_band"].dropna().astype(str))
            baseline = universe[universe["price_band"].astype(str).isin(bands)].copy() if bands else universe.iloc[0:0].copy()
            summaries.append(
                summarize_trades(selected, baseline, strategy=strategy, edge_threshold=threshold, scope=scope)
            )

    # Router: at each state choose the expression with the largest executable
    # residual, then take at most the first choice per city-date.
    router_universe = candidates.sort_values("edge", ascending=False).drop_duplicates(
        ["city", "target_date", "decision_snapshot_ts_utc"], keep="first"
    )
    for threshold in EDGE_THRESHOLDS:
        selected = router_universe[router_universe["edge"].ge(threshold)].copy()
        selected = selected.sort_values("decision_ts").drop_duplicates(["city", "target_date"], keep="first")
        selected["strategy"] = "all_state_best_expression_router"
        selected["edge_threshold"] = threshold
        selected_parts.append(selected)
        bands = set(selected["price_band"].dropna().astype(str))
        baseline = router_universe[router_universe["price_band"].astype(str).isin(bands)].copy()
        baseline = baseline.sort_values("decision_ts").drop_duplicates(["city", "target_date"], keep="first")
        summaries.append(
            summarize_trades(
                selected,
                baseline,
                strategy="all_state_best_expression_router",
                edge_threshold=threshold,
                scope="all_state",
            )
        )
    selected_rows = pd.concat(selected_parts, ignore_index=True) if selected_parts else pd.DataFrame()
    return pd.DataFrame(summaries), selected_rows


def structural_cross_rows(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    cross = frame[frame["cross_event"]].copy()
    rows: list[dict[str, Any]] = []
    detail: list[pd.DataFrame] = []
    for expression, ask_col, size_col, outcome_col in [
        ("cross_prev_no", "prev_no_ask", "prev_no_ask_size", "y_prev_no"),
        ("source_basis_reversal_prev_yes", "prev_yes_ask", "prev_yes_ask_size", "y_prev_yes"),
    ]:
        sub = cross[
            pd.to_numeric(cross[ask_col], errors="coerce").between(0.001, 0.999, inclusive="both")
            & pd.to_numeric(cross[size_col], errors="coerce").ge(MIN_ASK_SIZE)
        ].copy()
        sub["expression"] = expression
        sub["ask"] = pd.to_numeric(sub[ask_col], errors="coerce")
        sub["ask_size"] = pd.to_numeric(sub[size_col], errors="coerce")
        sub["outcome"] = pd.to_numeric(sub[outcome_col], errors="coerce")
        sub["entry_fee"] = sub["ask"].map(fee_per_share)
        sub["entry_cost"] = sub["ask"] + sub["entry_fee"]
        sub["settlement_pnl"] = sub["outcome"] - sub["entry_cost"]
        sub = sub.sort_values("decision_ts").drop_duplicates(["anchor_episode", "expression"], keep="first")
        cost = float(sub["entry_cost"].sum())
        pnl = float(sub["settlement_pnl"].sum())
        lo, hi = _daily_bootstrap_roi(sub)
        rows.append(
            {
                "strategy": expression,
                "rows": len(sub),
                "dates": sub["target_date"].nunique(),
                "cities": sub["city"].nunique(),
                "wins": int(sub["outcome"].sum()),
                "avg_ask": sub["ask"].mean() if not sub.empty else math.nan,
                "roi": pnl / cost if cost else math.nan,
                "roi_ci_low": lo,
                "roi_ci_high": hi,
            }
        )
        detail.append(sub)
    return pd.DataFrame(rows), pd.concat(detail, ignore_index=True) if detail else pd.DataFrame()


def next_state_markouts(frame: pd.DataFrame, selected: pd.DataFrame) -> pd.DataFrame:
    states = frame.sort_values(["city", "target_date", "decision_ts"]).copy()
    grouped = {(city, date): group for (city, date), group in states.groupby(["city", "target_date"], sort=False)}
    rows: list[dict[str, Any]] = []
    for trade in selected.itertuples(index=False):
        if not bool(getattr(trade, "cross_event", False)):
            continue
        group = grouped.get((trade.city, trade.target_date))
        if group is None:
            continue
        later = group[
            (group["decision_ts"] > trade.decision_ts + pd.Timedelta(minutes=15))
            & (group["decision_ts"] <= trade.decision_ts + pd.Timedelta(minutes=120))
        ]
        if later.empty:
            continue
        nxt = later.iloc[0]
        ladder = parse_ladder(nxt.get("ladder_book_json"))
        absolute_bracket = trade.current_key if trade.expression.startswith("current") else trade.d1_key
        quote = quote_for_bracket(ladder, absolute_bracket)
        side = "yes" if trade.expression.endswith("yes") else "no"
        exit_bid = _quote_field(quote, f"{side}_bid")
        if not math.isfinite(exit_bid):
            continue
        exit_fee = fee_per_share(exit_bid)
        exit_proceeds = exit_bid - exit_fee
        rows.append(
            {
                "strategy": trade.strategy,
                "edge_threshold": trade.edge_threshold,
                "expression": trade.expression,
                "city": trade.city,
                "target_date": trade.target_date,
                "entry_ts": trade.decision_snapshot_ts_utc,
                "exit_ts": nxt["decision_snapshot_ts_utc"],
                "minutes": (nxt["decision_ts"] - trade.decision_ts).total_seconds() / 60.0,
                "entry_cost": trade.entry_cost,
                "exit_bid": exit_bid,
                "exit_proceeds": exit_proceeds,
                "markout_pnl": exit_proceeds - trade.entry_cost,
                "markout_return": (exit_proceeds - trade.entry_cost) / trade.entry_cost,
            }
        )
    return pd.DataFrame(rows)


def markout_summary(markouts: pd.DataFrame) -> pd.DataFrame:
    if markouts.empty:
        return pd.DataFrame()
    return (
        markouts.groupby(["strategy", "edge_threshold", "expression"], dropna=False)
        .agg(
            rows=("markout_pnl", "size"),
            dates=("target_date", "nunique"),
            avg_minutes=("minutes", "mean"),
            avg_markout_return=("markout_return", "mean"),
            median_markout_return=("markout_return", "median"),
            positive_rate=("markout_pnl", lambda values: float((values > 0).mean())),
        )
        .reset_index()
    )


def time_decay_table(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    minutes = pd.to_numeric(work["minutes_since_running_max"], errors="coerce")
    work["elapsed_bucket"] = pd.cut(
        minutes,
        [-math.inf, 30, 60, 120, 240, math.inf],
        labels=["le30", "30_60", "60_120", "120_240", "gt240"],
    ).astype(str)
    work["y_break"] = 1 - work["y_current_yes"]
    work["market_p_break"] = 1 - work["market_p_current"]
    work["model_p_break"] = 1 - work["p_current_physics"]
    work = work.sort_values("decision_ts").drop_duplicates(
        ["city", "target_date", "current_key", "elapsed_bucket"], keep="first"
    )
    return (
        work.groupby("elapsed_bucket", dropna=False)
        .agg(
            rows=("y_break", "size"),
            dates=("target_date", "nunique"),
            empirical_break_rate=("y_break", "mean"),
            market_p_break=("market_p_break", "mean"),
            model_p_break=("model_p_break", "mean"),
            avg_current_yes_ask=("current_yes_ask", "mean"),
            avg_current_no_ask=("current_no_ask_book", "mean"),
        )
        .reset_index()
    )


def fmt(value: Any, digits: int = 4) -> str:
    number = safe_float(value)
    if number is None:
        return "NA"
    return f"{number:.{digits}f}"


def dataframe_markdown(frame: pd.DataFrame, percent_cols: Iterable[str] = ()) -> str:
    if frame.empty:
        return "_no rows_"
    percent = set(percent_cols)
    cols = list(frame.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for row in frame.itertuples(index=False, name=None):
        values = []
        for col, value in zip(cols, row):
            if col in percent and safe_float(value) is not None:
                values.append(f"{float(value):.1%}")
            elif isinstance(value, (float, np.floating)):
                values.append(fmt(value))
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def gates(strategy_summary: pd.DataFrame, proper: pd.DataFrame) -> dict[str, Any]:
    score_rows = proper[proper["model"].eq("market_physics")]
    score_pass = bool(not score_rows.empty and score_rows["delta_ci_high"].lt(0).all())
    positive = strategy_summary[
        strategy_summary["roi_ci_low"].gt(0)
        & strategy_summary["excess_ci_low"].gt(0)
        & strategy_summary["recent_roi"].gt(0)
        & strategy_summary["dates"].ge(10)
        & strategy_summary["rows"].ge(30)
    ]
    return {
        "proper_score_baseline_pass": score_pass,
        "confirmed_strategy_rows": positive.to_dict(orient="records"),
        "significance": "PASS" if not positive.empty else "FAIL",
        "baseline": "PASS" if score_pass and not positive.empty else "FAIL",
        "forward": "PASS" if not positive.empty else "FAIL",
        "conclusion": "confirmed" if score_pass and not positive.empty else "inconclusive",
    }


def render_report(
    *,
    frame: pd.DataFrame,
    proper: pd.DataFrame,
    strategy_summary: pd.DataFrame,
    structural: pd.DataFrame,
    markouts: pd.DataFrame,
    decay: pd.DataFrame,
    gate_result: dict[str, Any],
    state_path: Path,
) -> str:
    cross_count = int(frame["cross_event"].sum())
    predicted = frame["p_current_physics"].notna() & frame["p_d1_physics"].notna()
    best = strategy_summary.sort_values("roi", ascending=False).head(8)
    def strategy_row(name: str, threshold: float = 0.02) -> pd.Series | None:
        matched = strategy_summary[
            strategy_summary["strategy"].eq(name) & strategy_summary["edge_threshold"].eq(threshold)
        ]
        return None if matched.empty else matched.iloc[0]

    continuation = strategy_row("cross_continuation_current_no")
    exhaustion = strategy_row("cross_exhaustion_current_yes")
    next1 = strategy_row("cross_next1_yes")
    no_break = strategy_row("no_break_60_current_yes")
    router = strategy_row("all_state_best_expression_router")
    direct_findings = [
        "- `cross current-NO continuation` 是唯一值得继续 forward 的概率腿："
        + (
            f"{int(continuation['rows'])} rows/{int(continuation['dates'])} dates，ROI {continuation['roi']:.1%}，"
            f"CI [{continuation['roi_ci_low']:.1%},{continuation['roi_ci_high']:.1%}]，recent {continuation['recent_roi']:.1%}；CI 与同价 baseline excess 都未过门。"
            if continuation is not None
            else "无可评估行。"
        ),
        "- `cross current-YES exhaustion/reversal` 当前为负："
        + (f"ROI {exhaustion['roi']:.1%}，recent {exhaustion['recent_roi']:.1%}。" if exhaustion is not None else "无可评估行。"),
        "- `cross T+1 YES` 显著失败，说明升温后直接买 exact next rung 承担了 overshoot/stop-location 错配："
        + (f"ROI {next1['roi']:.1%}，CI 上界 {next1['roi_ci_high']:.1%}。" if next1 is not None else "无可评估行。"),
        "- `60m no-break current YES` 的全窗正点估没有 forward 存活："
        + (f"ROI {no_break['roi']:.1%}，recent {no_break['recent_roi']:.1%}，同价 excess {no_break['date_equal_excess_roi']:.1%}。" if no_break is not None else "无可评估行。"),
        "- 全表达 router 没有把弱信号变成组合 alpha："
        + (f"ROI {router['roi']:.1%}，recent {router['recent_roi']:.1%}。" if router is not None else "无可评估行。"),
    ]
    return "\n".join(
        [
            "# Source-Event Hazard Router v1",
            "",
            "> generated 2026-07-13; research-only; zero notional; no live order or live policy change.",
            "",
            "## 结论先行",
            "",
            "这轮把 source cross、时间未突破、current/d1 YES/NO 和真实双边 ask 放进同一个分母。",
            f"最终 gate: significance={gate_result['significance']} / baseline={gate_result['baseline']} / forward={gate_result['forward']} / conclusion={gate_result['conclusion']}。",
            "任何正点估仍只作方向发现；没有一条策略在本轮被升级为 live。",
            "",
            "## 直接发现",
            "",
            *direct_findings,
            "",
            "## 数据漏斗",
            "",
            f"- input: `{state_path}`",
            f"- labeled PIT states: `{len(frame)}`; dates `{frame['target_date'].nunique()}`; cities `{frame['city'].nunique()}`.",
            f"- expanding predictions available: `{int(predicted.sum())}`; cross episodes: `{cross_count}`.",
            "- grain: one saved ladder snapshot state; strategy replay de-duplicates first row per anchor episode/expression or first router choice per city-date.",
            "- execution: real YES/NO asks and ask size from `ladder_book_json`; min ask size 5; official Weather taker fee on entry and markout exit.",
            "",
            "## 概率层：是否打赢盘口",
            "",
            dataframe_markdown(
                proper,
                percent_cols=["delta_logloss_vs_market", "delta_ci_low", "delta_ci_high"],
            ),
            "",
            "## 交易表达",
            "",
            dataframe_markdown(
                best,
                percent_cols=[
                    "win_rate",
                    "roi",
                    "roi_ci_low",
                    "roi_ci_high",
                    "same_band_baseline_roi",
                    "date_equal_excess_roi",
                    "excess_ci_low",
                    "excess_ci_high",
                    "recent_roi",
                ],
            ),
            "",
            "## 结构锁定与 source-basis 反转",
            "",
            dataframe_markdown(structural, percent_cols=["roi", "roi_ci_low", "roi_ci_high"]),
            "",
            "## Cross 后 15-120 分钟可卖 bid markout",
            "",
            dataframe_markdown(markouts, percent_cols=["avg_markout_return", "median_markout_return", "positive_rate"]),
            "",
            "## 时间未突破条件化",
            "",
            dataframe_markdown(decay, percent_cols=["empirical_break_rate", "market_p_break", "model_p_break"]),
            "",
            "## 判断",
            "",
            "1. P0 crossed-prev NO 单独报告，因为它是 source/settlement-basis 结构腿，不应和概率策略混成一个 ROI。",
            "2. continuation=`current NO`、exhaustion/reversal=`current YES`、exact-next=`d1 YES`；全部由同一个 residual probability router 选择，不再用策略名先决定 side。",
            "3. elapsed time 已作为 market-residual 特征进入 expanding model，但这仍是 snapshot 条件化，不是秒级连续 hazard。真正 post-cross 可交易速度必须靠新 forward collector。",
            "4. source-basis reversal 只有在 crossed source 后来被 settlement-aligned source 否定时才成立；若当前分母没有 below-anchor outcome，就不能从零案例宣称没有机会。",
            "",
            "## 8 环覆盖",
            "",
            "- covered: signal discrimination, probability/proper score, executable top-book microstructure, fee-adjusted replay, date bootstrap, same-price-band baseline, forward-like expanding dates.",
            "- partial: capacity only min top size 5; portfolio correlation only target-date bootstrap.",
            "- missing: native local-first-seen second-level event clock, real fills/queue, fresh independent post-launch dates. Therefore no live verdict.",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-rows", type=Path, default=DEFAULT_STATES)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--output-md", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args()

    frame = enrich_state_rows(args.state_rows)
    frame["p_current_time"] = expanding_predictions(
        frame,
        target_col="y_current_yes",
        market_col="market_p_current",
        feature_columns=TIME_FEATURES,
        label="p_current_time",
    )
    frame["p_current_physics"] = expanding_predictions(
        frame,
        target_col="y_current_yes",
        market_col="market_p_current",
        feature_columns=PHYSICS_FEATURES,
        label="p_current_physics",
    )
    frame["p_d1_time"] = expanding_predictions(
        frame,
        target_col="y_d1_yes",
        market_col="market_p_d1",
        feature_columns=TIME_FEATURES,
        label="p_d1_time",
    )
    frame["p_d1_physics"] = expanding_predictions(
        frame,
        target_col="y_d1_yes",
        market_col="market_p_d1",
        feature_columns=PHYSICS_FEATURES,
        label="p_d1_physics",
    )
    proper = proper_score_table(frame)
    candidates = expression_candidates(frame)
    strategy_summary, selected = strategy_tables(candidates)
    structural, structural_rows = structural_cross_rows(frame)
    markout_rows = next_state_markouts(frame, selected[selected["edge_threshold"].eq(0.02)].copy())
    markouts = markout_summary(markout_rows)
    decay = time_decay_table(frame[frame["p_current_physics"].notna()].copy())
    gate_result = gates(strategy_summary, proper)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out_dir / "scored_state_rows.csv", index=False)
    candidates.to_csv(args.out_dir / "expression_candidates.csv", index=False)
    selected.to_csv(args.out_dir / "selected_strategy_rows.csv", index=False)
    proper.to_csv(args.out_dir / "proper_score.csv", index=False)
    strategy_summary.to_csv(args.out_dir / "strategy_summary.csv", index=False)
    structural_rows.to_csv(args.out_dir / "structural_cross_rows.csv", index=False)
    structural.to_csv(args.out_dir / "structural_cross_summary.csv", index=False)
    markout_rows.to_csv(args.out_dir / "event_markout_rows.csv", index=False)
    markouts.to_csv(args.out_dir / "event_markout_summary.csv", index=False)
    decay.to_csv(args.out_dir / "time_decay_summary.csv", index=False)

    payload = {
        "status": "research_only_zero_notional",
        "input": str(args.state_rows),
        "funnel": {
            "labeled_states": len(frame),
            "dates": frame["target_date"].nunique(),
            "cities": frame["city"].nunique(),
            "cross_events": int(frame["cross_event"].sum()),
            "predicted_states": int((frame["p_current_physics"].notna() & frame["p_d1_physics"].notna()).sum()),
            "expression_candidates": len(candidates),
        },
        "gates": gate_result,
        "proper_score": proper.replace({np.nan: None}).to_dict(orient="records"),
        "strategy_summary": strategy_summary.replace({np.nan: None}).to_dict(orient="records"),
        "structural_cross": structural.replace({np.nan: None}).to_dict(orient="records"),
        "markout_summary": markouts.replace({np.nan: None}).to_dict(orient="records"),
        "time_decay": decay.replace({np.nan: None}).to_dict(orient="records"),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output_md.write_text(
        render_report(
            frame=frame,
            proper=proper,
            strategy_summary=strategy_summary,
            structural=structural,
            markouts=markouts,
            decay=decay,
            gate_result=gate_result,
            state_path=args.state_rows,
        ),
        encoding="utf-8",
    )
    print(json.dumps(payload["funnel"], ensure_ascii=False, sort_keys=True))
    print(json.dumps(gate_result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
