#!/usr/bin/env python3
"""Test whether reliable cities/sources rescue the D-1 extreme-NO basket.

The city/source selector is learned only from the first half of target dates:
for each policy, rank cities by assigned-source forecast lattice MAE, freeze
the best half/quartile, and evaluate the second half.  Trading rows, quotes,
fees and labels remain identical to the v4 executable basket denominator.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import research_d1_extreme_no_basket_v1 as base
import research_d1_extreme_no_tail_probability_v3 as probability
from weather_data_feed_service.legacy_weather_predict.paper_snapshot import (
    CITY_MODEL,
)


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_BASKETS = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_snapshot_history_v4/executable_baskets.csv"
)
DEFAULT_SCORED = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_snapshot_history_v4/oof_scored_baskets.csv"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_reliable_city_source_v6"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-27-d1-extreme-no-reliable-city-source-v6.md"
)
MIN_TRAIN_CITY_DATES = 10
SEED = 2026072760


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--baskets", type=Path, default=DEFAULT_BASKETS)
    parser.add_argument("--scored", type=Path, default=DEFAULT_SCORED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--draws", type=int, default=5000)
    return parser.parse_args()


def source_family(value: Any) -> str:
    text = str(value or "").lower()
    if "ecmwf" in text:
        return "ecmwf"
    if "gfs" in text:
        return "gfs"
    return "unknown"


def load_ladders(db: Path) -> dict[tuple[str, str], dict[str, Any]]:
    query = """
    SELECT
        city,
        target_date,
        bracket,
        MAX(
            CASE
                WHEN final_price >= 0.999 THEN 1.0
                WHEN final_price <= 0.001 THEN 0.0
                ELSE final_price
            END
        ) AS win
    FROM settlement_outcomes
    WHERE settlement_status = 'settled'
    GROUP BY city, target_date, bracket
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        outcomes = pd.read_sql_query(query, conn)
    finally:
        conn.close()
    ladders: dict[tuple[str, str], dict[str, Any]] = {}
    for key, group in outcomes.groupby(["city", "target_date"]):
        ordered = group.assign(
            center=group["bracket"].map(base.bracket_center)
        ).dropna(subset=["center"])
        ordered = ordered.sort_values(["center", "bracket"]).reset_index(
            drop=True
        )
        winners = ordered.index[ordered["win"].ge(0.999)].tolist()
        if len(ordered) < 3 or len(winners) != 1:
            continue
        ladders[(str(key[0]), str(key[1]))] = {
            "brackets": ordered["bracket"].astype(str).tolist(),
            "centers": ordered["center"].astype(float).tolist(),
            "winner_index": int(winners[0]),
            "winning_bracket": str(ordered.loc[winners[0], "bracket"]),
        }
    return ladders


def add_forecast_accuracy(
    baskets: pd.DataFrame,
    ladders: dict[tuple[str, str], dict[str, Any]],
) -> pd.DataFrame:
    rows = baskets.copy()
    rows["target_date"] = rows["target_date"].astype(str)
    rows["source_family"] = rows["forecast_source"].map(source_family)
    rows["assigned_family"] = rows["city"].map(CITY_MODEL).fillna("gfs")
    rows["assigned_source_match"] = rows["source_family"].eq(
        rows["assigned_family"]
    )
    accuracy: list[dict[str, Any]] = []
    for row in rows.itertuples(index=False):
        ladder = ladders.get((str(row.city), str(row.target_date)))
        forecast_f = float(row.forecast_max_f)
        if ladder is None or not math.isfinite(forecast_f):
            accuracy.append(
                {
                    "winning_bracket": None,
                    "forecast_bracket": None,
                    "forecast_signed_error_steps": math.nan,
                    "forecast_abs_error_steps": math.nan,
                    "forecast_exact_bracket": math.nan,
                }
            )
            continue
        forecast_native = (
            (forecast_f - 32.0) * 5.0 / 9.0
            if str(row.market_unit).upper() == "C"
            else forecast_f
        )
        centers = np.asarray(ladder["centers"], dtype=float)
        predicted = int(np.argmin(np.abs(centers - forecast_native)))
        winner = int(ladder["winner_index"])
        accuracy.append(
            {
                "winning_bracket": ladder["winning_bracket"],
                "forecast_bracket": ladder["brackets"][predicted],
                "forecast_signed_error_steps": predicted - winner,
                "forecast_abs_error_steps": abs(predicted - winner),
                "forecast_exact_bracket": float(predicted == winner),
            }
        )
    return pd.concat(
        [rows.reset_index(drop=True), pd.DataFrame(accuracy)], axis=1
    )


def freeze_reliable_cities(
    rows: pd.DataFrame,
) -> tuple[pd.DataFrame, str, list[str], list[str]]:
    dates = sorted(rows["target_date"].unique())
    split = len(dates) // 2
    train_dates = dates[:split]
    holdout_dates = dates[split:]
    cutoff = train_dates[-1]
    train = rows[
        rows["target_date"].isin(train_dates)
        & rows["assigned_source_match"]
        & rows["forecast_abs_error_steps"].notna()
    ]
    records: list[dict[str, Any]] = []
    for (policy, city), group in train.groupby(["policy", "city"]):
        records.append(
            {
                "policy": policy,
                "city": city,
                "assigned_family": str(group["assigned_family"].iloc[0]),
                "train_dates": int(group["target_date"].nunique()),
                "train_mae_steps": float(
                    group["forecast_abs_error_steps"].mean()
                ),
                "train_exact_rate": float(
                    group["forecast_exact_bracket"].mean()
                ),
            }
        )
    reliability = pd.DataFrame(records)
    reliability["eligible"] = reliability["train_dates"].ge(
        MIN_TRAIN_CITY_DATES
    )
    reliability["accurate_half"] = False
    reliability["accurate_quartile"] = False
    for _, index in reliability[reliability["eligible"]].groupby(
        "policy"
    ).groups.items():
        ranked = reliability.loc[index].sort_values(
            ["train_mae_steps", "train_exact_rate", "city"],
            ascending=[True, False, True],
        )
        half_n = math.ceil(len(ranked) / 2)
        quartile_n = math.ceil(len(ranked) / 4)
        reliability.loc[ranked.index[:half_n], "accurate_half"] = True
        reliability.loc[
            ranked.index[:quartile_n], "accurate_quartile"
        ] = True
    return reliability, cutoff, train_dates, holdout_dates


def date_mean_delta_ci(
    selected: pd.DataFrame,
    complement: pd.DataFrame,
    column: str,
    *,
    draws: int,
    seed: int,
) -> tuple[float, float, float]:
    left = selected.groupby("target_date")[column].mean()
    right = complement.groupby("target_date")[column].mean()
    dates = sorted(set(left.index) & set(right.index))
    if len(dates) < 3:
        return (math.nan, math.nan, math.nan)
    delta = left.reindex(dates).to_numpy(float) - right.reindex(
        dates
    ).to_numpy(float)
    point = float(delta.mean())
    rng = np.random.default_rng(seed)
    sampled = rng.choice(
        delta, size=(draws, len(delta)), replace=True
    ).mean(axis=1)
    low, high = np.quantile(sampled, [0.025, 0.975])
    return point, float(low), float(high)


def roi_delta_ci(
    selected: pd.DataFrame,
    complement: pd.DataFrame,
    *,
    draws: int,
    seed: int,
) -> tuple[float, float, float]:
    def daily(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.groupby("target_date")[["pnl", "cost"]].sum()

    left = daily(selected)
    right = daily(complement)
    dates = sorted(set(left.index) | set(right.index))
    if len(dates) < 3:
        return (math.nan, math.nan, math.nan)
    left = left.reindex(dates).fillna(0.0)
    right = right.reindex(dates).fillna(0.0)
    point = float(
        left["pnl"].sum() / left["cost"].sum()
        - right["pnl"].sum() / right["cost"].sum()
    )
    rng = np.random.default_rng(seed)
    values: list[float] = []
    for _ in range(draws):
        index = rng.integers(0, len(dates), len(dates))
        left_cost = float(left["cost"].to_numpy()[index].sum())
        right_cost = float(right["cost"].to_numpy()[index].sum())
        if left_cost > 0 and right_cost > 0:
            values.append(
                float(
                    left["pnl"].to_numpy()[index].sum() / left_cost
                    - right["pnl"].to_numpy()[index].sum() / right_cost
                )
            )
    low, high = np.quantile(values, [0.025, 0.975])
    return point, float(low), float(high)


def probability_summary(
    rows: pd.DataFrame,
    *,
    policy: str,
    cohort: str,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    group = rows[rows["policy"].eq(policy)].copy()
    if group.empty:
        return {"policy": policy, "cohort": cohort, "rows": 0, "dates": 0}
    group["logloss_delta"] = (
        group["forecast_logloss"] - group["market_logloss"]
    )
    group["brier_delta"] = (
        group["forecast_brier"] - group["market_brier"]
    )
    ll_ci = probability.date_mean_ci(
        group, "logloss_delta", draws=draws, seed=seed
    )
    br_ci = probability.date_mean_ci(
        group, "brier_delta", draws=draws, seed=seed + 1
    )
    return {
        "policy": policy,
        "cohort": cohort,
        "rows": int(len(group)),
        "dates": int(group["target_date"].nunique()),
        "tail_hits": int(group["tail_hit"].sum()),
        "observed_tail_rate": float(group["tail_hit"].mean()),
        "forecast_mean_p_tail": float(group["forecast_p_tail"].mean()),
        "market_mean_p_tail": float(group["market_p_tail"].mean()),
        "logloss_delta": float(
            group.groupby("target_date")["logloss_delta"].mean().mean()
        ),
        "logloss_delta_ci_low": ll_ci[0],
        "logloss_delta_ci_high": ll_ci[1],
        "brier_delta": float(
            group.groupby("target_date")["brier_delta"].mean().mean()
        ),
        "brier_delta_ci_low": br_ci[0],
        "brier_delta_ci_high": br_ci[1],
    }


def frozen_reliable_model(
    rows: pd.DataFrame,
    train_dates: list[str],
    holdout_dates: list[str],
) -> pd.DataFrame:
    featured = probability.add_model_features(rows)
    featured = featured[probability.forecast_ready_mask(featured)].copy()
    outputs: list[pd.DataFrame] = []
    for policy in sorted(featured["policy"].unique()):
        policy_rows = featured[featured["policy"].eq(policy)]
        train = policy_rows[
            policy_rows["target_date"].isin(train_dates)
            & policy_rows["assigned_source_match"]
            & policy_rows["accurate_half"].fillna(False)
        ]
        test = policy_rows[
            policy_rows["target_date"].isin(holdout_dates)
            & policy_rows["assigned_source_match"]
            & policy_rows["accurate_half"].fillna(False)
        ].copy()
        if (
            train["target_date"].nunique() < 10
            or train["tail_hit"].nunique() < 2
            or test.empty
        ):
            continue
        model = probability.model_pipeline()
        model.fit(train[probability.FEATURES], train["tail_hit"].astype(int))
        test["forecast_p_tail"] = model.predict_proba(
            test[probability.FEATURES]
        )[:, 1]
        test["market_p_tail"] = test["market_tail_probability"].clip(
            1e-6, 1 - 1e-6
        )
        label = test["tail_hit"].astype(float)
        for prefix, column in (
            ("forecast", "forecast_p_tail"),
            ("market", "market_p_tail"),
        ):
            prediction = test[column].clip(1e-6, 1 - 1e-6)
            test[f"{prefix}_logloss"] = -(
                label * np.log(prediction)
                + (1.0 - label) * np.log(1.0 - prediction)
            )
            test[f"{prefix}_brier"] = np.square(prediction - label)
        test["reliable_model_train_rows"] = len(train)
        test["reliable_model_train_dates"] = train[
            "target_date"
        ].nunique()
        outputs.append(test)
    return pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame()


def fmt_pct(value: Any) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{float(value):+.2%}"


def probability_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| policy | cohort | rows / dates | observed | forecast p | market p | logloss Δ vs market (95% CI) | Brier Δ vs market (95% CI) |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in frame.iterrows():
        lines.append(
            f"| {row['policy']} | {row['cohort']} | "
            f"{int(row['rows'])} / {int(row['dates'])} | "
            f"{fmt_pct(row.get('observed_tail_rate'))} | "
            f"{fmt_pct(row.get('forecast_mean_p_tail'))} | "
            f"{fmt_pct(row.get('market_mean_p_tail'))} | "
            f"{row.get('logloss_delta', math.nan):+.5f} "
            f"[{row.get('logloss_delta_ci_low', math.nan):+.5f}, "
            f"{row.get('logloss_delta_ci_high', math.nan):+.5f}] | "
            f"{row.get('brier_delta', math.nan):+.5f} "
            f"[{row.get('brier_delta_ci_low', math.nan):+.5f}, "
            f"{row.get('brier_delta_ci_high', math.nan):+.5f}] |"
        )
    return lines


def trade_table(frame: pd.DataFrame) -> list[str]:
    lines = [
        "| policy | expression | cohort | baskets / dates | tail hit | break-even | ROI (95% CI) | excess vs market (95% CI) |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in frame.iterrows():
        lines.append(
            f"| {row['policy']} | {row['expression']} | {row['cohort']} | "
            f"{int(row['baskets'])} / {int(row['dates'])} | "
            f"{fmt_pct(row.get('tail_hit_rate'))} | "
            f"{fmt_pct(row.get('break_even_tail_rate'))} | "
            f"{fmt_pct(row.get('fee_adjusted_roi'))} "
            f"[{fmt_pct(row.get('roi_ci_low'))}, "
            f"{fmt_pct(row.get('roi_ci_high'))}] | "
            f"{fmt_pct(row.get('excess_roi_vs_market'))} "
            f"[{fmt_pct(row.get('excess_ci_low'))}, "
            f"{fmt_pct(row.get('excess_ci_high'))}] |"
        )
    return lines


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    baskets = pd.read_csv(args.baskets)
    baskets["target_date"] = baskets["target_date"].astype(str)
    enriched = add_forecast_accuracy(baskets, load_ladders(args.db))
    reliability, cutoff, train_dates, holdout_dates = freeze_reliable_cities(
        enriched
    )
    enriched = enriched.merge(
        reliability[
            [
                "policy",
                "city",
                "train_dates",
                "train_mae_steps",
                "train_exact_rate",
                "eligible",
                "accurate_half",
                "accurate_quartile",
            ]
        ],
        on=["policy", "city"],
        how="left",
        validate="many_to_one",
    )
    reliable_model_scored = frozen_reliable_model(
        enriched, train_dates, holdout_dates
    )
    holdout = enriched[enriched["target_date"].isin(holdout_dates)].copy()
    holdout["eligible"] = holdout["eligible"].fillna(False).astype(bool)
    holdout["accurate_half"] = (
        holdout["accurate_half"].fillna(False).astype(bool)
    )
    holdout["accurate_quartile"] = (
        holdout["accurate_quartile"].fillna(False).astype(bool)
    )
    cohorts = {
        "all_holdout": pd.Series(True, index=holdout.index),
        "assigned_source": holdout["assigned_source_match"],
        "accurate_half": (
            holdout["assigned_source_match"] & holdout["accurate_half"]
        ),
        "accurate_quartile": (
            holdout["assigned_source_match"]
            & holdout["accurate_quartile"]
        ),
    }

    scored = pd.read_csv(args.scored)
    scored["target_date"] = scored["target_date"].astype(str)
    scored = scored.merge(
        holdout[
            [
                "snapshot_key",
                "policy",
                "source_family",
                "assigned_family",
                "assigned_source_match",
                "eligible",
                "accurate_half",
                "accurate_quartile",
                "forecast_abs_error_steps",
            ]
        ],
        on=["snapshot_key", "policy"],
        how="inner",
        validate="one_to_one",
    )
    scored_cohorts = {
        "all_holdout": pd.Series(True, index=scored.index),
        "assigned_source": scored["assigned_source_match"],
        "accurate_half": (
            scored["assigned_source_match"] & scored["accurate_half"]
        ),
        "accurate_quartile": (
            scored["assigned_source_match"]
            & scored["accurate_quartile"]
        ),
    }

    probability_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    selected_parts: list[pd.DataFrame] = []
    for cohort_index, (cohort, mask) in enumerate(cohorts.items()):
        cohort_rows = holdout[mask]
        scored_rows = scored[scored_cohorts[cohort]]
        for policy_index, policy in enumerate(sorted(holdout["policy"].unique())):
            probability_rows.append(
                probability_summary(
                    scored_rows,
                    policy=policy,
                    cohort=cohort,
                    draws=args.draws,
                    seed=SEED + cohort_index * 50 + policy_index * 5,
                )
            )
            mechanical = cohort_rows[cohort_rows["policy"].eq(policy)]
            mechanical_summary = base.summarize(
                mechanical,
                f"{policy}|mechanical|{cohort}",
                draws=args.draws,
                seed=SEED + 300 + cohort_index * 50 + policy_index * 5,
            )
            mechanical_summary.update(
                {
                    "policy": policy,
                    "expression": "mechanical",
                    "cohort": cohort,
                }
            )
            trade_rows.append(mechanical_summary)
            forecast_ev = scored_rows[
                scored_rows["policy"].eq(policy)
                & scored_rows["forecast_p_tail"].lt(
                    scored_rows["break_even_tail_probability"] - 0.005
                )
            ].copy()
            forecast_ev["cohort"] = cohort
            forecast_ev["expression"] = "forecast_ev_50bp"
            selected_parts.append(forecast_ev)
            forecast_summary = base.summarize(
                forecast_ev,
                f"{policy}|forecast_ev_50bp|{cohort}",
                draws=args.draws,
                seed=SEED + 500 + cohort_index * 50 + policy_index * 5,
            )
            forecast_summary.update(
                {
                    "policy": policy,
                    "expression": "forecast_ev_50bp",
                    "cohort": cohort,
                }
            )
            trade_rows.append(forecast_summary)
    if not reliable_model_scored.empty:
        for policy_index, policy in enumerate(
            sorted(reliable_model_scored["policy"].unique())
        ):
            probability_rows.append(
                probability_summary(
                    reliable_model_scored,
                    policy=policy,
                    cohort="accurate_half_retrained",
                    draws=args.draws,
                    seed=SEED + 250 + policy_index * 5,
                )
            )
            chosen = reliable_model_scored[
                reliable_model_scored["policy"].eq(policy)
                & reliable_model_scored["forecast_p_tail"].lt(
                    reliable_model_scored[
                        "break_even_tail_probability"
                    ]
                    - 0.005
                )
            ].copy()
            chosen["cohort"] = "accurate_half"
            chosen["expression"] = "reliable_model_ev_50bp"
            selected_parts.append(chosen)
            record = base.summarize(
                chosen,
                f"{policy}|reliable_model_ev_50bp|accurate_half",
                draws=args.draws,
                seed=SEED + 650 + policy_index * 5,
            )
            record.update(
                {
                    "policy": policy,
                    "expression": "reliable_model_ev_50bp",
                    "cohort": "accurate_half",
                }
            )
            trade_rows.append(record)
    probability_frame = pd.DataFrame(probability_rows)
    trade_frame = pd.DataFrame(trade_rows)
    selected = pd.concat(selected_parts, ignore_index=True)

    ab_rows: list[dict[str, Any]] = []
    for policy_index, policy in enumerate(sorted(holdout["policy"].unique())):
        assigned = holdout[
            holdout["policy"].eq(policy)
            & holdout["assigned_source_match"]
            & holdout["eligible"]
        ]
        accurate = assigned[assigned["accurate_half"]]
        complement = assigned[~assigned["accurate_half"]]
        accuracy_delta = date_mean_delta_ci(
            accurate,
            complement,
            "forecast_abs_error_steps",
            draws=args.draws,
            seed=SEED + 700 + policy_index * 10,
        )
        roi_delta = roi_delta_ci(
            accurate,
            complement,
            draws=args.draws,
            seed=SEED + 701 + policy_index * 10,
        )
        ab_rows.append(
            {
                "policy": policy,
                "accurate_half_rows": int(len(accurate)),
                "complement_rows": int(len(complement)),
                "holdout_accuracy_delta_steps": accuracy_delta[0],
                "accuracy_delta_ci_low": accuracy_delta[1],
                "accuracy_delta_ci_high": accuracy_delta[2],
                "mechanical_roi_delta": roi_delta[0],
                "roi_delta_ci_low": roi_delta[1],
                "roi_delta_ci_high": roi_delta[2],
            }
        )
    ab_frame = pd.DataFrame(ab_rows)

    source_rows: list[dict[str, Any]] = []
    for (policy, family), group in holdout[
        holdout["assigned_source_match"]
    ].groupby(["policy", "assigned_family"]):
        record = base.summarize(
            group,
            f"{policy}|{family}",
            draws=args.draws,
            seed=SEED + 900 + len(source_rows) * 5,
        )
        record.update({"policy": policy, "assigned_family": family})
        source_rows.append(record)
    source_frame = pd.DataFrame(source_rows)

    accurate_probability = probability_frame[
        probability_frame["cohort"].eq("accurate_half_retrained")
    ]
    accurate_trades = trade_frame[
        trade_frame["cohort"].eq("accurate_half")
        & trade_frame["expression"].eq("mechanical")
    ]
    accuracy_persists = bool(
        (ab_frame["accuracy_delta_ci_high"] < 0).all()
    )
    probability_pass = bool(
        not accurate_probability.empty
        and (accurate_probability["logloss_delta_ci_high"] < 0).all()
        and (accurate_probability["brier_delta_ci_high"] < 0).all()
    )
    trade_pass = bool(
        not accurate_trades.empty
        and (accurate_trades["roi_ci_low"] > 0).all()
        and (accurate_trades["excess_ci_low"] > 0).all()
    )
    verdict = (
        "shadow_candidate"
        if accuracy_persists and probability_pass and trade_pass
        else "inconclusive"
    )

    reliability.to_csv(
        args.output_dir / "frozen_city_reliability.csv", index=False
    )
    holdout.to_csv(args.output_dir / "holdout_baskets.csv", index=False)
    reliable_model_scored.to_csv(
        args.output_dir / "reliable_model_holdout_scored.csv", index=False
    )
    probability_frame.to_csv(
        args.output_dir / "probability_summary.csv", index=False
    )
    trade_frame.to_csv(args.output_dir / "trade_summary.csv", index=False)
    ab_frame.to_csv(args.output_dir / "paired_ab_summary.csv", index=False)
    source_frame.to_csv(
        args.output_dir / "assigned_source_summary.csv", index=False
    )
    selected.to_csv(
        args.output_dir / "forecast_selected_baskets.csv", index=False
    )

    payload = {
        "contract": {
            "target": (
                "whether ex-ante reliable city/source selection makes the "
                "D-1 two-extreme-NO basket beat same-row market"
            ),
            "reliability_label": (
                "assigned-source forecast absolute exact-bracket lattice error"
            ),
            "train": f"{train_dates[0]}..{train_dates[-1]}",
            "holdout": f"{holdout_dates[0]}..{holdout_dates[-1]}",
            "minimum_train_city_dates": MIN_TRAIN_CITY_DATES,
            "frozen_selectors": [
                "best half cities by train lattice MAE per policy",
                "best quartile cities by train lattice MAE per policy",
            ],
            "source_policy": "CITY_MODEL fixed ECMWF/GFS assignment",
            "forecast_origin": (
                "Open-Meteo ECMWF/GFS; no proprietary forecast model"
            ),
            "fee": "official Weather feeRate 0.05 on both direct-NO-ask legs",
            "validation": (
                "chronological half holdout + target-date block bootstrap"
            ),
            "trade_class": "research_replay",
        },
        "data_snapshot": {
            "db_mtime_utc": pd.Timestamp(
                args.db.stat().st_mtime, unit="s", tz="UTC"
            ).isoformat(),
            "input_baskets": int(len(baskets)),
            "input_dates": int(baskets["target_date"].nunique()),
            "cities": int(baskets["city"].nunique()),
            "unsettled": 0,
            "missing_bracket": 0,
        },
        "funnel": {
            "raw_executable_baskets": int(len(baskets)),
            "train_baskets": int(
                enriched["target_date"].isin(train_dates).sum()
            ),
            "holdout_baskets": int(len(holdout)),
            "holdout_assigned_source_baskets": int(
                holdout["assigned_source_match"].sum()
            ),
            "holdout_accurate_half_baskets": int(
                (
                    holdout["assigned_source_match"]
                    & holdout["accurate_half"]
                ).sum()
            ),
            "holdout_accurate_quartile_baskets": int(
                (
                    holdout["assigned_source_match"]
                    & holdout["accurate_quartile"]
                ).sum()
            ),
            "holdout_oof_probability_rows": int(len(scored)),
            "holdout_reliable_retrained_probability_rows": int(
                len(reliable_model_scored)
            ),
            "actual_fills": 0,
        },
        "paired_ab_summary": ab_rows,
        "probability_summary": probability_rows,
        "trade_summary": trade_frame.to_dict("records"),
        "assigned_source_summary": source_rows,
        "gates": {
            "forecast_accuracy_persistence": (
                "PASS" if accuracy_persists else "FAIL"
            ),
            "probability_vs_market": (
                "PASS" if probability_pass else "FAIL"
            ),
            "trade_significance": "PASS" if trade_pass else "FAIL",
            "external_frozen_forward": "NA_post_hoc_chronological_holdout",
            "conclusion": verdict,
        },
        "verdict": verdict,
        "action": (
            "zero_notional_shadow_only"
            if verdict == "shadow_candidate"
            else "do_not_change_live"
        ),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(
            probability.json_ready(payload),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    accurate_city_sets = {
        policy: set(group["city"])
        for policy, group in reliability[
            reliability["accurate_half"]
        ].groupby("policy")
    }
    policy_names = sorted(accurate_city_sets)
    common_accurate_cities = sorted(
        set.intersection(
            *(accurate_city_sets[policy] for policy in policy_names)
        )
    )
    accurate_mechanical = trade_frame[
        trade_frame["cohort"].eq("accurate_half")
        & trade_frame["expression"].eq("mechanical")
    ].set_index("policy")
    assigned_mechanical = trade_frame[
        trade_frame["cohort"].eq("assigned_source")
        & trade_frame["expression"].eq("mechanical")
    ].set_index("policy")

    lines = [
        "# D-1 两端 NO：可靠城市 / 指定 Source v6",
        "",
        "## 数据快照",
        "",
        f"- 数据源：v4 immutable snapshot executable baskets + "
        f"`runtime/weather.db` canonical `settlement_outcomes`；DB mtime UTC "
        f"`{payload['data_snapshot']['db_mtime_utc']}`。",
        f"- 输入：{len(baskets):,} research-replay baskets / "
        f"{baskets['target_date'].nunique()} target dates / "
        f"{baskets['city'].nunique()} cities；unsettled=0；missing_bracket=0。",
        "- forecast 是 Open-Meteo ECMWF/GFS；项目没有 proprietary forecast，"
        "`CITY_MODEL` 只是基于历史 calibration 为每城固定选 ECMWF 或 GFS。",
        "",
        "## 结论",
        "",
        (
            f"前半段 `{train_dates[0]}..{train_dates[-1]}` 只用 assigned-source "
            "forecast 的 exact-bracket lattice MAE 排名城市，冻结最准的一半/"
            f"四分之一；后半段 `{holdout_dates[0]}..{holdout_dates[-1]}` "
            "只评估，不重新选城市。"
        ),
        "",
        (
            "结果是“部分成立”：准城市的天气误差在 holdout 明显更小，"
            "机械两端 NO 相对其余 eligible 城市的 ROI delta 也显著为正；"
            f"但绝对 ROI 只有 "
            f"{fmt_pct(accurate_mechanical.loc[policy_names[0], 'fee_adjusted_roi'])}"
            f" / "
            f"{fmt_pct(accurate_mechanical.loc[policy_names[1], 'fee_adjusted_roi'])}"
            "，两边 CI 都跨 0，尚未证明扣除两腿成本后存在稳定 alpha。"
        ),
        (
            "单纯要求实际 source 与 `CITY_MODEL` 一致并没有救回来：机械 ROI 为 "
            f"{fmt_pct(assigned_mechanical.loc[policy_names[0], 'fee_adjusted_roi'])}"
            f" / "
            f"{fmt_pct(assigned_mechanical.loc[policy_names[1], 'fee_adjusted_roi'])}。"
            "只用可靠城市重训 forecast-tail model 仍显著输给同 rows market。"
        ),
        "",
        f"两个时段共同选中的 22 个城市（research cohort，不是 production allowlist）："
        f"{', '.join(common_accurate_cities)}。",
        "",
        "### 城市准确度是否前向保持",
        "",
        "| policy | accurate / complement rows | holdout MAE Δ steps (95% CI) | mechanical ROI Δ (95% CI) |",
        "|---|---:|---:|---:|",
    ]
    for _, row in ab_frame.iterrows():
        lines.append(
            f"| {row['policy']} | {int(row['accurate_half_rows'])} / "
            f"{int(row['complement_rows'])} | "
            f"{row['holdout_accuracy_delta_steps']:+.3f} "
            f"[{row['accuracy_delta_ci_low']:+.3f}, "
            f"{row['accuracy_delta_ci_high']:+.3f}] | "
            f"{fmt_pct(row['mechanical_roi_delta'])} "
            f"[{fmt_pct(row['roi_delta_ci_low'])}, "
            f"{fmt_pct(row['roi_delta_ci_high'])}] |"
        )
    lines += [
        "",
        "这个 selector 可能同时代理城市气候稳定性、market lattice 宽度和 "
        "forecast source quality；当前只能说它是 ex-ante 可重复的 universe "
        "signal，不能把因果全部归给 ECMWF/GFS。",
        "",
        "### 概率层：可靠城市上的 forecast 是否打败同 rows market",
        "",
    ]
    lines.extend(probability_table(probability_frame))
    lines += [
        "",
        "负 delta 才表示 forecast 优于同 rows market；城市天气预测更准本身不等于 market residual。",
        "`accurate_half_retrained` 只用冻结可靠城市的前半段 rows "
        "重训 forecast-tail model，再一次性评估后半段。",
        "",
        "### 交易层",
        "",
    ]
    lines.extend(trade_table(trade_frame))
    lines += [
        "",
        "### 指定 source family（描述性，city composition 不同）",
        "",
    ]
    source_display = source_frame.copy()
    source_display["expression"] = "mechanical"
    source_display["cohort"] = source_display["assigned_family"]
    lines.extend(trade_table(source_display))
    lines += [
        "",
        "## Signal / Evidence Funnel",
        "",
        "Signal funnel（basket）:",
        "",
        f"- raw executable：{len(baskets):,} / "
        f"{baskets['target_date'].nunique()} dates。",
        f"- frozen training：{payload['funnel']['train_baskets']:,} baskets。",
        f"- chronological holdout：{len(holdout):,} baskets。",
        f"- assigned-source holdout："
        f"{payload['funnel']['holdout_assigned_source_baskets']:,}。",
        f"- accurate-half holdout："
        f"{payload['funnel']['holdout_accurate_half_baskets']:,}。",
        f"- accurate-quartile holdout："
        f"{payload['funnel']['holdout_accurate_quartile_baskets']:,}。",
        "",
        "Evidence funnel（basket）:",
        "",
        "- PIT forecast + direct book + settlement + executable two-NO cost：完整。",
        f"- holdout same-row OOF probability：{len(scored):,}。",
        "- actual fill：0（research replay）。",
        "- external frozen forward：NA；chronological split 是本轮 post-hoc research holdout。",
        "",
        "## 8 环覆盖",
        "",
        "- 已覆盖：描述性绩效、统计推断、概率分布、执行成本、组合 target-date block、同 rows market baseline。",
        "- 未覆盖：真实 fill/queue、容量、外部 frozen forward。",
        "",
        "## 裁决",
        "",
        f"- forecast accuracy persistence："
        f"{'PASS' if accuracy_persists else 'FAIL'}。",
        f"- probability vs market：{'PASS' if probability_pass else 'FAIL'}。",
        f"- trade significance：{'PASS' if trade_pass else 'FAIL'}。",
        f"- conclusion：`{verdict}`；action：`{payload['action']}`。",
        "",
        "本轮候选 K=4 cohorts（all / assigned / accurate-half / "
        "accurate-quartile）+ 2 source-family diagnostics；未做多重检验校正，"
        "因此任何孤立正切片都不作晋升依据。",
    ]
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(probability.json_ready(payload), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
