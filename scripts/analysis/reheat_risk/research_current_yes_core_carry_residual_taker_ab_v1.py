#!/usr/bin/env python3
"""Replay relaxed Core Carry residuals with the actual five-share taker cost.

The frozen v3 research loader still materializes peak-clock telemetry even
though the production v3 probability head excludes that feature.  This script
rebuilds the identical no-clock expanding-OOF denominator directly, then
compares the current positive-taker-EV selector with relaxed positive-mid-
residual taker expressions.
"""

from __future__ import annotations

import glob
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[3]
RESEARCH_ID = "current_yes_core_carry_residual_taker_ab_v1"
OUT_DIR = ROOT / "docs/analysis/2026-07/generated" / RESEARCH_ID
OUT_JSON = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-30-current-yes-core-carry-residual-taker-ab-v1.json"
)
SHARD_GLOB = str(
    ROOT
    / "docs/analysis/2026-06/generated/intraday_weather_regime_atlas_v1/"
    "feature_factory_*/reheat_feature_rows.csv"
)
COSTED_OOF = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "current_yes_core_carry_no_obs_age_freeze_pre_live_v5/"
    "oof_states_five_share_cost.csv"
)

FEATURES = [
    "market_logit",
    "decision_hour_local",
    "dewpoint_depression_f",
    "wind_speed_kt",
]
CONTAMINATED = {"2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05"}
MIN_TRAIN_DATES = 14
MID_FLOOR = 0.80
MID_CEILING = 0.9895
QUANTITY = 5.0
BOOTSTRAP_REPS = 5_000
SEED = 20260730


def json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def load_universe() -> pd.DataFrame:
    wanted = [
        "city",
        "target_date",
        "decision_hour_local",
        "bracket",
        "current_bracket",
        "outcome",
        "settlement_status",
        "current_yes_ask",
        "current_yes_bid",
        "current_bracket_held",
        "dewpoint_depression_f",
        "wind_speed_kt",
        "decision_snapshot_ts_utc",
    ]
    frames: list[pd.DataFrame] = []
    for raw_path in sorted(glob.glob(SHARD_GLOB)):
        path = Path(raw_path)
        available = set(pd.read_csv(path, nrows=0).columns)
        frame = pd.read_csv(
            path,
            usecols=[column for column in wanted if column in available],
            low_memory=False,
        )
        frame["target_date"] = frame["target_date"].astype(str)
        frames.append(
            frame[
                frame["bracket"].astype(str).eq(
                    frame["current_bracket"].astype(str)
                )
                & frame["outcome"].astype(str).str.lower().eq("yes")
            ]
        )
    raw = pd.concat(frames, ignore_index=True).drop_duplicates(
        ["city", "target_date", "decision_hour_local"]
    )
    raw = raw[~raw["target_date"].isin(CONTAMINATED)].copy()
    numeric = [
        "decision_hour_local",
        "current_yes_ask",
        "current_yes_bid",
        "current_bracket_held",
        "dewpoint_depression_f",
        "wind_speed_kt",
    ]
    for column in numeric:
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    universe = raw[
        raw["decision_hour_local"].between(13, 17)
        & raw["current_yes_ask"].between(0.01, 0.99)
        & raw["current_yes_bid"].between(0.01, 0.99)
        & raw["current_bracket_held"].notna()
        & raw["settlement_status"].eq("settled")
    ].copy()
    universe["label"] = universe["current_bracket_held"].astype(int)
    universe["market_mid"] = (
        universe["current_yes_bid"] + universe["current_yes_ask"]
    ) / 2.0
    clipped = universe["market_mid"].clip(1e-5, 1 - 1e-5)
    universe["market_logit"] = np.log(clipped / (1 - clipped))
    universe["decision_snapshot_dt"] = pd.to_datetime(
        universe["decision_snapshot_ts_utc"],
        utc=True,
        errors="coerce",
    )
    return universe.sort_values(
        ["target_date", "city", "decision_snapshot_dt"]
    ).reset_index(drop=True)


def make_model() -> Pipeline:
    preprocess = ColumnTransformer(
        [
            (
                "numeric",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                FEATURES,
            )
        ]
    )
    return Pipeline(
        [
            ("pre", preprocess),
            (
                "model",
                LogisticRegression(
                    C=0.1,
                    max_iter=3_000,
                    random_state=20260722,
                ),
            ),
        ]
    )


def expanding_oof(universe: pd.DataFrame) -> pd.DataFrame:
    output: list[pd.DataFrame] = []
    dates = sorted(universe["target_date"].unique())
    for index, target_date in enumerate(dates):
        if index < MIN_TRAIN_DATES:
            continue
        train = universe[universe["target_date"].lt(target_date)]
        test = universe[universe["target_date"].eq(target_date)]
        weights = (
            1.0
            / train.groupby(["city", "target_date"])["label"]
            .transform("size")
            .clip(lower=1)
            .to_numpy(float)
        )
        model = make_model()
        model.fit(train[FEATURES], train["label"], model__sample_weight=weights)
        result = test[
            [
                "city",
                "target_date",
                "decision_snapshot_dt",
                "decision_snapshot_ts_utc",
                "decision_hour_local",
                "label",
                "market_mid",
                "current_bracket",
            ]
        ].copy()
        result["p_v3_no_peak_clock"] = model.predict_proba(test[FEATURES])[:, 1]
        output.append(result)
    return pd.concat(output, ignore_index=True)


def load_costed_oof() -> pd.DataFrame:
    universe = load_universe()
    if len(universe) != 4_061:
        raise RuntimeError(
            f"v3 historical denominator drift: expected 4061, got {len(universe)}"
        )
    oof = expanding_oof(universe)
    if len(oof) != 2_758 or oof["target_date"].nunique() != 32:
        raise RuntimeError(
            "v3 OOF parity failed: "
            f"rows={len(oof)} dates={oof['target_date'].nunique()}"
        )
    costed = pd.read_csv(COSTED_OOF, low_memory=False)
    keys = [
        "city",
        "target_date",
        "decision_snapshot_ts_utc",
        "current_bracket",
    ]
    columns = [
        *keys,
        "five_share_executable",
        "five_share_cost_per_share",
        "five_share_cost_source",
    ]
    merged = oof.merge(
        costed[columns].drop_duplicates(keys),
        on=keys,
        how="left",
        validate="one_to_one",
    )
    missing = int(merged["five_share_executable"].isna().sum())
    if missing:
        raise RuntimeError(f"five-share cost merge missing {missing} OOF rows")
    merged["five_share_executable"] = merged["five_share_executable"].astype(bool)
    return merged


def exact_bounded(frame: pd.DataFrame) -> pd.Series:
    bracket = frame["current_bracket"].astype(str).str.lower()
    return ~bracket.str.contains(r"\+|below|under|higher|above", regex=True)


def block_roi_draws(entries: pd.DataFrame) -> list[float]:
    if entries.empty:
        return []
    daily = (
        entries.assign(
            _pnl=entries["label"].astype(float)
            - entries["five_share_cost_per_share"].astype(float),
            _cost=entries["five_share_cost_per_share"].astype(float),
        )
        .groupby("target_date")[["_pnl", "_cost"]]
        .sum()
        .to_numpy(float)
    )
    rng = np.random.default_rng(SEED)
    draws: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        sample = daily[rng.integers(0, len(daily), len(daily))]
        cost = float(sample[:, 1].sum())
        if cost > 0:
            draws.append(float(sample[:, 0].sum() / cost))
    return draws


def metrics(entries: pd.DataFrame) -> dict[str, Any]:
    if entries.empty:
        return {"entries": 0}
    cost = entries["five_share_cost_per_share"].astype(float)
    pnl = entries["label"].astype(float) - cost
    draws = block_roi_draws(entries)
    return {
        "entries": int(len(entries)),
        "city_days": int(entries.groupby(["city", "target_date"]).ngroups),
        "dates": int(entries["target_date"].nunique()),
        "cities": int(entries["city"].nunique()),
        "wins": int(entries["label"].sum()),
        "losses": int(entries["label"].eq(0).sum()),
        "win_rate": float(entries["label"].mean()),
        "avg_effective_cost_per_share": float(cost.mean()),
        "cost_usd_at_5_shares": float(QUANTITY * cost.sum()),
        "pnl_usd_at_5_shares": float(QUANTITY * pnl.sum()),
        "fee_adjusted_roi": float(pnl.sum() / cost.sum()),
        "target_date_block_ci95": [
            float(np.quantile(draws, 0.025)),
            float(np.quantile(draws, 0.975)),
        ],
        "entries_per_covered_date": float(
            len(entries) / entries["target_date"].nunique()
        ),
    }


def metrics_at_price(entries: pd.DataFrame, price_column: str) -> dict[str, Any]:
    """Diagnostic all-fill economics at a supplied per-share price."""

    if entries.empty:
        return {"entries": 0}
    price = entries[price_column].astype(float)
    pnl = entries["label"].astype(float) - price
    return {
        "entries": int(len(entries)),
        "city_days": int(entries.groupby(["city", "target_date"]).ngroups),
        "dates": int(entries["target_date"].nunique()),
        "wins": int(entries["label"].sum()),
        "losses": int(entries["label"].eq(0).sum()),
        "win_rate": float(entries["label"].mean()),
        "avg_price_per_share": float(price.mean()),
        "cost_usd_at_5_shares": float(QUANTITY * price.sum()),
        "pnl_usd_at_5_shares": float(QUANTITY * pnl.sum()),
        "roi": float(pnl.sum() / price.sum()),
        "warning": (
            "all rows hypothetically fill at market midpoint with zero fee; "
            "not an executable maker backtest"
        ),
    }


def first_city_day(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.sort_values(["target_date", "city", "decision_snapshot_dt"])
        .drop_duplicates(["city", "target_date"], keep="first")
        .reset_index(drop=True)
    )


def slice_rows(
    frame: pd.DataFrame,
    definitions: list[tuple[str, pd.Series]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for name, mask in definitions:
        row = {"slice": name, **metrics(frame[mask.fillna(False)])}
        output.append(row)
    return output


def main() -> int:
    frame = load_costed_oof()
    base = frame[
        frame["five_share_executable"]
        & exact_bounded(frame)
        & frame["market_mid"].between(
            MID_FLOOR,
            MID_CEILING,
            inclusive="both",
        )
    ].copy()
    base["model_minus_mid"] = (
        base["p_v3_no_peak_clock"] - base["market_mid"]
    )
    base["model_minus_taker_cost"] = (
        base["p_v3_no_peak_clock"] - base["five_share_cost_per_share"]
    )
    mid_positive = base[base["model_minus_mid"].gt(0)].copy()
    cost_positive = base[base["model_minus_taker_cost"].gt(0)].copy()
    friction_filtered = mid_positive[
        mid_positive["model_minus_taker_cost"].le(0)
    ].copy()
    current_city_days = {
        (str(row.city), str(row.target_date))
        for row in cost_positive.itertuples(index=False)
    }
    friction_filtered["is_incremental_city_day"] = [
        (str(row.city), str(row.target_date)) not in current_city_days
        for row in friction_filtered.itertuples(index=False)
    ]
    friction_filtered["taker_shortfall"] = (
        friction_filtered["five_share_cost_per_share"]
        - friction_filtered["p_v3_no_peak_clock"]
    )
    friction_filtered["cost_minus_mid"] = (
        friction_filtered["five_share_cost_per_share"]
        - friction_filtered["market_mid"]
    )
    incremental = friction_filtered[
        friction_filtered["is_incremental_city_day"]
    ].copy()
    overlap = friction_filtered[
        ~friction_filtered["is_incremental_city_day"]
    ].copy()
    first_incremental = first_city_day(incremental)
    first_overlap = first_city_day(overlap)

    policies = {
        "current_first_positive_taker_ev": first_city_day(cost_positive),
        "relaxed_first_positive_mid_residual": first_city_day(mid_positive),
        "all_positive_mid_residual_checkpoints": mid_positive,
        "all_friction_filtered_checkpoints": friction_filtered,
        "first_friction_filtered_city_day": first_city_day(friction_filtered),
    }
    forward_dates = sorted(frame["target_date"].unique())[-8:]
    shortfall_slices = slice_rows(
        friction_filtered,
        [
            ("0_to_0.5c", friction_filtered["taker_shortfall"].le(0.005)),
            (
                "0.5_to_1c",
                friction_filtered["taker_shortfall"].gt(0.005)
                & friction_filtered["taker_shortfall"].le(0.01),
            ),
            (
                "1_to_2c",
                friction_filtered["taker_shortfall"].gt(0.01)
                & friction_filtered["taker_shortfall"].le(0.02),
            ),
            (
                "2_to_5c",
                friction_filtered["taker_shortfall"].gt(0.02)
                & friction_filtered["taker_shortfall"].le(0.05),
            ),
            ("above_5c", friction_filtered["taker_shortfall"].gt(0.05)),
        ],
    )
    mid_slices = slice_rows(
        friction_filtered,
        [
            ("0.80_to_0.85", friction_filtered["market_mid"].le(0.85)),
            (
                "0.85_to_0.90",
                friction_filtered["market_mid"].gt(0.85)
                & friction_filtered["market_mid"].le(0.90),
            ),
            (
                "0.90_to_0.95",
                friction_filtered["market_mid"].gt(0.90)
                & friction_filtered["market_mid"].le(0.95),
            ),
            ("above_0.95", friction_filtered["market_mid"].gt(0.95)),
        ],
    )
    hour_slices = [
        {
            "slice": f"hour_{hour}",
            **metrics(
                friction_filtered[
                    friction_filtered["decision_hour_local"].eq(hour)
                ]
            ),
        }
        for hour in range(13, 18)
    ]
    city_slices = [
        {"city": str(city), **metrics(group)}
        for city, group in friction_filtered.groupby("city", sort=True)
    ]
    result = {
        "research_id": RESEARCH_ID,
        "data": {
            "universe_rows": 4_061,
            "oof_rows": int(len(frame)),
            "oof_dates": int(frame["target_date"].nunique()),
            "oof_city_days": int(
                frame.groupby(["city", "target_date"]).ngroups
            ),
            "executable_exact_in_domain_rows": int(len(base)),
            "positive_mid_residual_rows": int(len(mid_positive)),
            "positive_taker_ev_rows": int(len(cost_positive)),
            "friction_filtered_rows": int(len(friction_filtered)),
            "forward_dates": forward_dates,
            "cost_basis": (
                "full five-share historical ask ladder plus official Weather fee"
            ),
            "label": "final exact current bracket held",
        },
        "overall": {name: metrics(rows) for name, rows in policies.items()},
        "frozen_last_8_dates": {
            name: metrics(rows[rows["target_date"].isin(forward_dates)])
            for name, rows in policies.items()
        },
        "filtered_delta_only": {
            "all_filtered_checkpoints": metrics(friction_filtered),
            "incremental_city_day_checkpoints": metrics(incremental),
            "first_incremental_city_day": metrics(first_incremental),
            "overlap_with_current_policy_checkpoints": metrics(overlap),
            "first_overlap_city_day": metrics(first_overlap),
            "hypothetical_all_fill_at_mid": {
                "all_filtered_checkpoints": metrics_at_price(
                    friction_filtered,
                    "market_mid",
                ),
                "first_incremental_city_day": metrics_at_price(
                    first_incremental,
                    "market_mid",
                ),
            },
            "shortfall_slices": shortfall_slices,
            "market_mid_slices": mid_slices,
            "hour_slices": hour_slices,
            "city_slices": city_slices,
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base.to_csv(OUT_DIR / "oof_policy_parent.csv", index=False)
    for name, rows in policies.items():
        rows.to_csv(OUT_DIR / f"{name}.csv", index=False)
    friction_filtered.to_csv(OUT_DIR / "filtered_delta_only.csv", index=False)
    first_incremental.to_csv(
        OUT_DIR / "filtered_delta_first_incremental_city_day.csv",
        index=False,
    )
    pd.DataFrame(shortfall_slices).to_csv(
        OUT_DIR / "filtered_by_taker_shortfall.csv",
        index=False,
    )
    pd.DataFrame(mid_slices).to_csv(
        OUT_DIR / "filtered_by_market_mid.csv",
        index=False,
    )
    pd.DataFrame(hour_slices).to_csv(
        OUT_DIR / "filtered_by_hour.csv",
        index=False,
    )
    pd.DataFrame(city_slices).to_csv(
        OUT_DIR / "filtered_by_city.csv",
        index=False,
    )
    OUT_JSON.write_text(
        json.dumps(json_ready(result), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(json_ready(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
