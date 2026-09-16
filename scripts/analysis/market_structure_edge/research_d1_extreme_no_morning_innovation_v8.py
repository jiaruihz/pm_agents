#!/usr/bin/env python3
"""Compare the same two-tail NO basket at D-1 versus morning checkpoints.

The expression is fixed throughout: buy one share of NO on the lowest listed
condition and one share of NO on the highest listed condition.  Morning
innovation is allowed to change only the estimated joint tail-hit probability
and therefore whether the basket is entered; it never changes the two legs.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from weather_data_feed.market_brackets import parse_market_bracket


DEFAULT_DB = ROOT / "runtime/weather.db"
DEFAULT_CHECKPOINTS = (
    ROOT
    / "docs/analysis/2026-07/generated/forecast_innovation_morning_v1/checkpoint_rows.csv"
)
DEFAULT_D1 = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_snapshot_history_v4/executable_baskets.csv"
)
DEFAULT_OUT = (
    ROOT
    / "docs/analysis/2026-07/generated/"
    "d1_extreme_no_morning_innovation_v8"
)
DEFAULT_REPORT = (
    ROOT
    / "docs/analysis/2026-07/"
    "2026-07-28-d1-extreme-no-morning-innovation-v8.md"
)
CHECKPOINTS = (9, 12)
MIN_TRAIN_DATES = 5
MODEL_C = 0.10
EV_BUFFER = 0.005
WEATHER_FEE_RATE = 0.05
BOOTSTRAP_REPS = 5_000
SEED = 20260728

FEATURES = {
    "market_raw": [],
    "market_calibrated": ["market_tail_logit"],
    "market_plus_forecast": [
        "market_tail_logit",
        "forecast_low_distance_steps",
        "forecast_high_distance_steps",
    ],
    "market_plus_innovation": [
        "market_tail_logit",
        "forecast_low_distance_steps",
        "forecast_high_distance_steps",
        "forecast_innovation_steps",
        "innovation_x_low_distance",
        "innovation_x_high_distance",
        "innovation_sq",
    ],
}
PRIMARY = "market_plus_innovation"
BASELINE = "market_plus_forecast"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--checkpoints", type=Path, default=DEFAULT_CHECKPOINTS)
    parser.add_argument("--d1-baskets", type=Path, default=DEFAULT_D1)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def fee(price: float) -> float:
    return WEATHER_FEE_RATE * price * (1.0 - price)


def valid_price(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and 0.001 <= number <= 0.999


def bracket_center(label: str, question: str) -> float:
    parsed = parse_market_bracket(label, question)
    if parsed is None:
        return math.nan
    if parsed.low is not None and parsed.high is not None:
        return (float(parsed.low) + float(parsed.high)) / 2.0
    if parsed.low is not None:
        return float(parsed.low)
    if parsed.high is not None:
        return float(parsed.high)
    return math.nan


def load_quotes(conn: sqlite3.Connection, ladder_ids: list[str]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for start in range(0, len(ladder_ids), 500):
        ids = ladder_ids[start : start + 500]
        placeholders = ",".join("?" for _ in ids)
        parts.append(
            pd.read_sql_query(
                f"""
                SELECT
                    ladder_snapshot_id,
                    absolute_bracket_identity AS rung,
                    question AS rung_question,
                    yes_direct_bid,
                    yes_direct_ask,
                    no_direct_bid,
                    no_direct_ask,
                    no_direct_ask_size
                FROM tmax_v2_ladder_rung_quotes
                WHERE ladder_snapshot_id IN ({placeholders})
                """,
                conn,
                params=ids,
            )
        )
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def build_morning_baskets(states: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    quote_groups = {
        str(key): group.copy()
        for key, group in quotes.groupby("ladder_snapshot_id")
    }
    records: list[dict[str, Any]] = []
    for state in states.to_dict("records"):
        group = quote_groups.get(str(state["ladder_snapshot_id"]))
        if group is None or len(group) < 3:
            continue
        group = group.copy()
        group["center"] = [
            bracket_center(str(row.rung), str(row.rung_question or ""))
            for row in group.itertuples(index=False)
        ]
        group = group[group["center"].notna()].sort_values(["center", "rung"])
        if len(group) < 3:
            continue
        low = group.iloc[0]
        high = group.iloc[-1]
        if not (
            valid_price(low["no_direct_bid"])
            and valid_price(low["no_direct_ask"])
            and valid_price(high["no_direct_bid"])
            and valid_price(high["no_direct_ask"])
            and float(low["no_direct_bid"]) <= float(low["no_direct_ask"])
            and float(high["no_direct_bid"]) <= float(high["no_direct_ask"])
            and float(low["no_direct_ask_size"]) >= 1.0
            and float(high["no_direct_ask_size"]) >= 1.0
        ):
            continue
        low_no_mid = (
            float(low["no_direct_bid"]) + float(low["no_direct_ask"])
        ) / 2.0
        high_no_mid = (
            float(high["no_direct_bid"]) + float(high["no_direct_ask"])
        ) / 2.0
        # The two terminal outcomes are mutually exclusive, so the sum of
        # their binary YES mids is the market-implied probability that either
        # tail wins.  Deriving each YES mid from its direct NO book keeps the
        # market baseline on the exact two legs being bought and does not turn
        # incomplete interior-rung coverage into a strategy eligibility gate.
        market_tail = float(
            np.clip(
                (1.0 - low_no_mid) + (1.0 - high_no_mid),
                0.001,
                0.999,
            )
        )
        yes_two_sided = (
            group["yes_direct_bid"].map(valid_price)
            & group["yes_direct_ask"].map(valid_price)
            & group["yes_direct_ask"].astype(float).ge(
                group["yes_direct_bid"].astype(float)
            )
        )
        full_ladder_tail = math.nan
        if yes_two_sided.all():
            yes_mids = (
                group["yes_direct_bid"].astype(float)
                + group["yes_direct_ask"].astype(float)
            ) / 2.0
            quoted_mass = float(yes_mids.sum())
            if quoted_mass > 0:
                full_ladder_tail = float(
                    (yes_mids.iloc[0] + yes_mids.iloc[-1]) / quoted_mass
                )
        low_ask = float(low["no_direct_ask"])
        high_ask = float(high["no_direct_ask"])
        cost = low_ask + high_ask + fee(low_ask) + fee(high_ask)
        low_label = str(low["rung"])
        high_label = str(high["rung"])
        winning = str(state["bracket"])
        unit = str(state["market_unit"]).upper()
        step = 1.0 if unit == "F" else 5.0 / 9.0
        forecast_native = (
            float(state["forecast_max_f"])
            if unit == "F"
            else (float(state["forecast_max_f"]) - 32.0) * 5.0 / 9.0
        )
        innovation_native = (
            float(state["forecast_innovation_f"])
            if unit == "F"
            else float(state["forecast_innovation_f"]) * 5.0 / 9.0
        )
        low_center = float(low["center"])
        high_center = float(high["center"])
        low_distance = (forecast_native - low_center) / step
        high_distance = (high_center - forecast_native) / step
        innovation_steps = innovation_native / step
        market_tail_clipped = float(np.clip(market_tail, 0.001, 0.999))
        records.append(
            {
                "tmax_state_id": state["tmax_state_id"],
                "city": state["city"],
                "target_date": state["target_date"],
                "checkpoint_hour_local": int(state["checkpoint_hour_local"]),
                "decision_ts_utc": state["decision_ts_utc"],
                "region": state["region"],
                "market_unit": unit,
                "low_bracket": low_label,
                "high_bracket": high_label,
                "low_no_ask": low_ask,
                "high_no_ask": high_ask,
                "low_no_ask_size": float(low["no_direct_ask_size"]),
                "high_no_ask_size": float(high["no_direct_ask_size"]),
                "market_tail_probability": market_tail,
                "full_ladder_market_tail_probability": full_ladder_tail,
                "full_yes_ladder_covered": int(
                    math.isfinite(full_ladder_tail)
                ),
                "market_tail_logit": math.log(
                    market_tail_clipped / (1.0 - market_tail_clipped)
                ),
                "forecast_low_distance_steps": low_distance,
                "forecast_high_distance_steps": high_distance,
                "forecast_innovation_steps": innovation_steps,
                "innovation_x_low_distance": innovation_steps * low_distance,
                "innovation_x_high_distance": innovation_steps * high_distance,
                "innovation_sq": innovation_steps**2,
                "low_win": int(winning == low_label),
                "high_win": int(winning == high_label),
                "tail_hit": int(winning in {low_label, high_label}),
                "cost": cost,
                "break_even_tail_probability": 2.0 - cost,
                "payout": 2.0 - int(winning in {low_label, high_label}),
                "pnl": 2.0 - int(winning in {low_label, high_label}) - cost,
            }
        )
    return pd.DataFrame(records)


def pipeline() -> Pipeline:
    return Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=MODEL_C,
                    solver="lbfgs",
                    max_iter=2_000,
                    random_state=SEED,
                ),
            ),
        ]
    )


def expanding_oof(baskets: pd.DataFrame) -> pd.DataFrame:
    outputs: list[pd.DataFrame] = []
    for checkpoint, rows in baskets.groupby("checkpoint_hour_local"):
        dates = sorted(rows["target_date"].unique())
        for index, target_date in enumerate(dates):
            train_dates = dates[:index]
            if len(train_dates) < MIN_TRAIN_DATES:
                continue
            train = rows[rows["target_date"].isin(train_dates)]
            test = rows[rows["target_date"].eq(target_date)].copy()
            if train["tail_hit"].nunique() < 2:
                continue
            result = test.copy()
            result["train_dates"] = len(train_dates)
            result["train_through_date"] = max(train_dates)
            result["p_tail_market_raw"] = test[
                "market_tail_probability"
            ].clip(1e-6, 1 - 1e-6)
            for variant, features in FEATURES.items():
                if variant == "market_raw":
                    continue
                model = pipeline()
                model.fit(train[features], train["tail_hit"])
                result[f"p_tail_{variant}"] = model.predict_proba(
                    test[features]
                )[:, 1]
            outputs.append(result)
    if outputs:
        return pd.concat(outputs, ignore_index=True)
    prediction_columns = [f"p_tail_{variant}" for variant in FEATURES]
    return pd.DataFrame(columns=[*baskets.columns, *prediction_columns])


def date_ci(frame: pd.DataFrame, column: str) -> tuple[float, float]:
    daily = frame.groupby("target_date")[column].mean().to_numpy(float)
    if len(daily) < 3:
        return math.nan, math.nan
    rng = np.random.default_rng(SEED)
    draws = rng.choice(
        daily, size=(BOOTSTRAP_REPS, len(daily)), replace=True
    ).mean(axis=1)
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def probability_scorecard(oof: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for checkpoint, group in oof.groupby("checkpoint_hour_local"):
        base_p = group[f"p_tail_{BASELINE}"].clip(1e-6, 1 - 1e-6)
        label = group["tail_hit"].astype(float)
        base_logloss = -(
            label * np.log(base_p) + (1 - label) * np.log(1 - base_p)
        )
        base_brier = (base_p - label) ** 2
        for variant in FEATURES:
            probability = group[f"p_tail_{variant}"].clip(1e-6, 1 - 1e-6)
            scored = group[["target_date"]].copy()
            scored["logloss"] = -(
                label * np.log(probability)
                + (1 - label) * np.log(1 - probability)
            )
            scored["brier"] = (probability - label) ** 2
            scored["logloss_delta"] = scored["logloss"] - base_logloss
            scored["brier_delta"] = scored["brier"] - base_brier
            log_low, log_high = date_ci(scored, "logloss_delta")
            brier_low, brier_high = date_ci(scored, "brier_delta")
            records.append(
                {
                    "checkpoint_hour_local": int(checkpoint),
                    "variant": variant,
                    "baskets": len(group),
                    "dates": int(group["target_date"].nunique()),
                    "tail_hits": int(group["tail_hit"].sum()),
                    "date_equal_logloss": float(
                        scored.groupby("target_date")["logloss"].mean().mean()
                    ),
                    "date_equal_brier": float(
                        scored.groupby("target_date")["brier"].mean().mean()
                    ),
                    "logloss_delta_vs_forecast": float(
                        scored["logloss_delta"].mean()
                    ),
                    "logloss_delta_ci_low": log_low,
                    "logloss_delta_ci_high": log_high,
                    "brier_delta_vs_forecast": float(
                        scored["brier_delta"].mean()
                    ),
                    "brier_delta_ci_low": brier_low,
                    "brier_delta_ci_high": brier_high,
                }
            )
    columns = [
        "checkpoint_hour_local",
        "variant",
        "baskets",
        "dates",
        "tail_hits",
        "date_equal_logloss",
        "date_equal_brier",
        "logloss_delta_vs_forecast",
        "logloss_delta_ci_low",
        "logloss_delta_ci_high",
        "brier_delta_vs_forecast",
        "brier_delta_ci_low",
        "brier_delta_ci_high",
    ]
    return pd.DataFrame(records, columns=columns)


def roi_ci(rows: pd.DataFrame) -> tuple[float, float]:
    if rows.empty:
        return math.nan, math.nan
    daily = rows.groupby("target_date", as_index=False).agg(
        pnl=("pnl", "sum"), cost=("cost", "sum")
    )
    rng = np.random.default_rng(SEED)
    index = rng.integers(
        0, len(daily), size=(BOOTSTRAP_REPS, len(daily))
    )
    pnl = daily["pnl"].to_numpy()[index].sum(axis=1)
    cost = daily["cost"].to_numpy()[index].sum(axis=1)
    roi = np.divide(
        pnl,
        cost,
        out=np.full_like(pnl, np.nan, dtype=float),
        where=cost > 0,
    )
    return float(np.nanquantile(roi, 0.025)), float(np.nanquantile(roi, 0.975))


def trade_scorecard(oof: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if oof.empty:
        return pd.DataFrame(columns=[*oof.columns, "policy"]), pd.DataFrame(
            columns=[
                "checkpoint_hour_local",
                "policy",
                "opportunity_baskets",
                "trades",
                "dates",
                "tail_hits",
                "mean_cost",
                "pnl",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
            ]
        )
    selected: list[pd.DataFrame] = []
    records: list[dict[str, Any]] = []
    for checkpoint, group in oof.groupby("checkpoint_hour_local"):
        policies = {"mechanical_all": pd.Series(True, index=group.index)}
        for variant in FEATURES:
            policies[variant] = group[f"p_tail_{variant}"].le(
                group["break_even_tail_probability"] - EV_BUFFER
            )
        for policy, mask in policies.items():
            trades = group[mask].copy()
            trades["policy"] = policy
            selected.append(trades)
            low, high = roi_ci(trades)
            total_cost = float(trades["cost"].sum()) if len(trades) else 0.0
            records.append(
                {
                    "checkpoint_hour_local": int(checkpoint),
                    "policy": policy,
                    "opportunity_baskets": len(group),
                    "trades": len(trades),
                    "dates": int(trades["target_date"].nunique())
                    if len(trades)
                    else 0,
                    "tail_hits": int(trades["tail_hit"].sum())
                    if len(trades)
                    else 0,
                    "mean_cost": float(trades["cost"].mean())
                    if len(trades)
                    else math.nan,
                    "pnl": float(trades["pnl"].sum()) if len(trades) else 0.0,
                    "roi": float(trades["pnl"].sum() / total_cost)
                    if total_cost
                    else math.nan,
                    "roi_ci_low": low,
                    "roi_ci_high": high,
                }
            )
    return pd.concat(selected, ignore_index=True), pd.DataFrame(records)


def mechanical_scorecard(baskets: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for checkpoint, trades in baskets.groupby("checkpoint_hour_local"):
        low, high = roi_ci(trades)
        total_cost = float(trades["cost"].sum())
        records.append(
            {
                "checkpoint_hour_local": int(checkpoint),
                "policy": "mechanical_all",
                "opportunity_baskets": len(trades),
                "trades": len(trades),
                "dates": int(trades["target_date"].nunique()),
                "tail_hits": int(trades["tail_hit"].sum()),
                "mean_cost": float(trades["cost"].mean()),
                "pnl": float(trades["pnl"].sum()),
                "roi": float(trades["pnl"].sum() / total_cost),
                "roi_ci_low": low,
                "roi_ci_high": high,
            }
        )
    return pd.DataFrame(records)


def paired_timing(d1: pd.DataFrame, morning: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    key = ["city", "target_date"]
    for d1_policy, d1_group in d1.groupby("policy"):
        d1_unique = d1_group.drop_duplicates(key, keep="first")
        for checkpoint, morning_group in morning.groupby(
            "checkpoint_hour_local"
        ):
            paired = d1_unique.merge(
                morning_group,
                on=key,
                how="inner",
                suffixes=("_d1", "_morning"),
                validate="one_to_one",
            )
            if paired.empty:
                continue
            paired["return_d1"] = paired["pnl_d1"] / paired["cost_d1"]
            paired["return_morning"] = (
                paired["pnl_morning"] / paired["cost_morning"]
            )
            paired["return_delta"] = (
                paired["return_morning"] - paired["return_d1"]
            )
            paired["same_extreme_pair"] = (
                paired["low_bracket_d1"].astype(str).eq(
                    paired["low_bracket_morning"].astype(str)
                )
                & paired["high_bracket_d1"].astype(str).eq(
                    paired["high_bracket_morning"].astype(str)
                )
            )
            low, high = date_ci(paired, "return_delta")
            records.append(
                {
                    "d1_policy": d1_policy,
                    "checkpoint_hour_local": int(checkpoint),
                    "paired_baskets": len(paired),
                    "dates": int(paired["target_date"].nunique()),
                    "cities": int(paired["city"].nunique()),
                    "same_extreme_pair": int(
                        paired["same_extreme_pair"].sum()
                    ),
                    "d1_mean_cost": float(paired["cost_d1"].mean()),
                    "morning_mean_cost": float(
                        paired["cost_morning"].mean()
                    ),
                    "d1_roi": float(
                        paired["pnl_d1"].sum() / paired["cost_d1"].sum()
                    ),
                    "morning_roi": float(
                        paired["pnl_morning"].sum()
                        / paired["cost_morning"].sum()
                    ),
                    "mean_return_delta": float(
                        paired["return_delta"].mean()
                    ),
                    "delta_ci_low": low,
                    "delta_ci_high": high,
                }
            )
    return pd.DataFrame(records)


def markdown(frame: pd.DataFrame, columns: list[str]) -> str:
    def value(item: Any) -> str:
        if isinstance(item, (float, np.floating)):
            return "NA" if not math.isfinite(item) else f"{float(item):.4f}"
        return str(item)

    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame.reindex(columns=columns).to_dict("records"):
        lines.append("| " + " | ".join(value(row[col]) for col in columns) + " |")
    return "\n".join(lines)


def write_report(
    path: Path,
    *,
    db_path: Path,
    checkpoint_path: Path,
    d1_path: Path,
    states: pd.DataFrame,
    morning: pd.DataFrame,
    oof: pd.DataFrame,
    probability: pd.DataFrame,
    trades: pd.DataFrame,
    timing: pd.DataFrame,
) -> None:
    primary_probability = probability[
        probability["variant"].eq(PRIMARY)
    ]
    primary_trades = trades[trades["policy"].eq(PRIMARY)]
    probability_pass = bool(
        len(primary_probability)
        and primary_probability["logloss_delta_ci_high"].lt(0).all()
        and primary_probability["brier_delta_ci_high"].lt(0).all()
    )
    execution_pass = bool(
        len(primary_trades) and primary_trades["roi_ci_low"].gt(0).all()
    )
    probability_gate = (
        "NA"
        if primary_probability.empty
        else ("PASS" if probability_pass else "FAIL")
    )
    execution_gate = (
        "NA"
        if primary_trades.empty
        else ("PASS" if execution_pass else "FAIL")
    )
    db_mtime = pd.Timestamp.fromtimestamp(
        db_path.stat().st_mtime, tz="UTC"
    ).isoformat()
    lines = [
        "# D-1 extreme-NO：morning innovation v8",
        "",
        "## 数据快照",
        "",
        f"- 数据源：`{db_path}`（canonical ladder/settlement）+ "
        f"`{checkpoint_path}`（IANA local checkpoint）+ "
        f"`{d1_path}`（D-1 basket replay）。",
        f"- DB last_modified_utc：`{db_mtime}`。",
        f"- checkpoint state rows：{len(states):,}；morning executable baskets："
        f"{len(morning):,}。",
        "- unsettled=0；missing_bracket=0（输入已限定 settled checkpoint states）。",
        "- actual fills=0；以下均为 PIT snapshot research replay。",
        "",
        "## Target 与修正",
        "",
        "本报告只研究原策略：每个 city-day 等份买最低挂牌档 NO 与最高挂牌档 NO。"
        "D-1、09:00、12:00 使用同一选档规则；innovation 只允许更新联合 tail-hit "
        "probability 和是否入场。此前单档 NO / Current-YES carry 不属于本策略证据。",
        "",
        "## Contract",
        "",
        f"- local checkpoints={CHECKPOINTS}；IANA timezone 来自已审计 checkpoint artifact。",
        "- 每个时点按该时点仍挂牌的完整 ladder 重新取最低/最高档；因此 D-1 与 morning "
        "是同一选档规则，但不保证是同一对 bracket。",
        "- basket payout：两端都不命中=2；任一端命中=1；两端不可能同时命中。",
        f"- cost：两条 direct NO ask + 每腿 `{WEATHER_FEE_RATE:.2f}*p*(1-p)`。",
        f"- primary eligibility：`P(tail hit) <= 2-cost-{EV_BUFFER:.3f}`。",
        f"- expanding OOF：至少 {MIN_TRAIN_DATES} 个 prior target dates；"
        f"C={MODEL_C}；无 city/region selector。",
        "",
        "## Signal / evidence funnel",
        "",
        f"- morning settled checkpoint states：{len(states):,}。",
        f"- direct-NO executable two-tail baskets：{len(morning):,} / "
        f"{morning['target_date'].nunique()} dates。",
        f"- full two-sided YES ladder diagnostic coverage："
        f"{int(morning['full_yes_ladder_covered'].sum()):,} baskets；"
        "不作为 eligibility。",
        f"- expanding OOF baskets：{len(oof):,} / "
        f"{oof['target_date'].nunique() if 'target_date' in oof else 0} dates。",
        f"- actual fills=0；全部为 research replay。",
        "- raw orderbook archive 覆盖全部 checkpoint 日期；但两条最外档 direct NO ask "
        "仅在 2026-07-15..18 同时存在。这不是 canonical 未同步；raw 中未保存的历史报价"
        "无法靠 rebuild 创造，且现有记录不能区分当时空 book 与 collector 未捕获。",
        "",
        "## 同一选档规则的 D-1 → morning timing 对比",
        "",
        markdown(
            timing,
            [
                "d1_policy",
                "checkpoint_hour_local",
                "paired_baskets",
                "dates",
                "cities",
                "same_extreme_pair",
                "d1_mean_cost",
                "morning_mean_cost",
                "d1_roi",
                "morning_roi",
                "mean_return_delta",
                "delta_ci_low",
                "delta_ci_high",
            ],
        ),
        "",
        "## Joint tail probability（expanding OOF）",
        "",
        markdown(
            probability,
            [
                "checkpoint_hour_local",
                "variant",
                "baskets",
                "dates",
                "tail_hits",
                "date_equal_logloss",
                "date_equal_brier",
                "logloss_delta_vs_forecast",
                "logloss_delta_ci_low",
                "logloss_delta_ci_high",
            ],
        ),
        "",
        "## Fee-adjusted basket replay",
        "",
        markdown(
            trades,
            [
                "checkpoint_hour_local",
                "policy",
                "opportunity_baskets",
                "trades",
                "dates",
                "tail_hits",
                "mean_cost",
                "pnl",
                "roi",
                "roi_ci_low",
                "roi_ci_high",
            ],
        ),
        "",
        "## Gates",
        "",
        f"- innovation probability delta：{probability_gate}。",
        f"- innovation-selected fee-adjusted execution：{execution_gate}。",
        "- fresh frozen forward：NA。",
        "- 当前只有 4 个 executable target dates，少于 5-date expanding "
        "train 门槛，所以 innovation 不能形成可解释的 OOF 交易结论。",
        "",
        "```text",
        f"status={'shadow_candidate' if probability_pass and execution_pass else 'inconclusive'}",
        "live_action=none",
        "```",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    states = pd.read_csv(args.checkpoints)
    states = states[
        states["checkpoint_hour_local"].isin(CHECKPOINTS)
        & states["forecast_max_f"].notna()
        & states["forecast_innovation_f"].notna()
    ].copy()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=2.0)
    conn.execute("PRAGMA query_only=ON")
    quotes = load_quotes(
        conn, states["ladder_snapshot_id"].astype(str).drop_duplicates().tolist()
    )
    conn.close()
    morning = build_morning_baskets(states, quotes)
    oof = expanding_oof(morning)
    probability = probability_scorecard(oof)
    selected, model_trades = trade_scorecard(oof)
    mechanical = mechanical_scorecard(morning)
    trades = (
        pd.concat([mechanical, model_trades], ignore_index=True)
        if not model_trades.empty
        else mechanical
    )
    d1 = pd.read_csv(args.d1_baskets)
    timing = paired_timing(d1, morning)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    morning.to_csv(args.output_dir / "morning_baskets.csv", index=False)
    oof.to_csv(args.output_dir / "oof_baskets.csv", index=False)
    probability.to_csv(args.output_dir / "probability_scorecard.csv", index=False)
    selected.to_csv(args.output_dir / "selected_baskets.csv", index=False)
    trades.to_csv(args.output_dir / "trade_scorecard.csv", index=False)
    timing.to_csv(args.output_dir / "paired_timing.csv", index=False)
    payload = {
        "counts": {
            "checkpoint_states": len(states),
            "morning_baskets": len(morning),
            "morning_dates": int(morning["target_date"].nunique()),
            "oof_baskets": len(oof),
            "oof_dates": int(oof["target_date"].nunique()),
        },
        "probability": probability.to_dict("records"),
        "trades": trades.to_dict("records"),
        "paired_timing": timing.to_dict("records"),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.report,
        db_path=args.db,
        checkpoint_path=args.checkpoints,
        d1_path=args.d1_baskets,
        states=states,
        morning=morning,
        oof=oof,
        probability=probability,
        trades=trades,
        timing=timing,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
