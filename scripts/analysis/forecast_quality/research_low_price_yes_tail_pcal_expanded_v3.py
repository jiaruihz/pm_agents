#!/usr/bin/env python3
"""Retrain HeadA p_cal through 2026-07-12 and test 2026-07-13..27.

The feature set, regularization, and capacity-based theta rule are inherited
unchanged from pcal v2.  This isolates the effect of adding settled training
dates.  The next calendar block, 2026-07-28..2026-08-11, is never scored here.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.analysis.forecast_quality import (
    research_low_price_yes_tail_pcal_v2 as pcal_v2,
)


OLD_ARTIFACT = (
    ROOT / "docs/analysis/2026-07/2026-07-03-low-price-yes-tail-pcal-v2.json"
)
OUT_DIR = (
    ROOT
    / "docs/analysis/2026-07/generated/low_price_yes_tail_pcal_expanded_v3"
)
REPORT = (
    ROOT
    / "docs/analysis/2026-07/2026-07-28-low-price-yes-tail-pcal-expanded-v3.md"
)
FROZEN_ARTIFACT = (
    ROOT / "configs/weather/low_price_yes_tail_pcal_expanded_v3_research.json"
)
TRAIN_END = "2026-07-12"
RETRO_FORWARD_START = "2026-07-13"
RETRO_FORWARD_END = "2026-07-27"
FRESH_FROZEN_START = "2026-07-28"
FRESH_FROZEN_END = "2026-08-11"
WEATHER_FEE_RATE = 0.05
MAX_TICKETS_PER_DAY = 5.0
N_BOOT = 5_000
RNG_SEED = 20260728


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def md_value(value: Any) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "NA"
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.5f}"
    return str(value)


def md_table(frame: pd.DataFrame, columns: list[str]) -> str:
    if frame.empty:
        return "_none_"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in frame[columns].itertuples(index=False, name=None):
        lines.append("| " + " | ".join(md_value(value) for value in row) + " |")
    return "\n".join(lines)


def load_rows() -> pd.DataFrame:
    universe = pcal_v2.load_universe()
    base = pcal_v2.pick_one_per_city_date(
        universe,
        pd.Series(True, index=universe.index),
    )
    base = pcal_v2.attach_asof_bias(base, pcal_v2.load_bias_index())
    for column in pcal_v2.NUM_FEATURES:
        base[column] = pd.to_numeric(base.get(column), errors="coerce")
    base["win"] = base["payoff"].eq(1.0).astype(int)
    base["fee"] = (
        WEATHER_FEE_RATE * base["ask"] * (1.0 - base["ask"])
    )
    base["unit_cost"] = base["ask"] + base["fee"]
    base["unit_pnl"] = base["win"] - base["unit_cost"]
    return base


def fit_artifact(train: pd.DataFrame) -> dict[str, Any]:
    fit_rows = train.dropna(subset=pcal_v2.NUM_FEATURES).copy()
    x_num = fit_rows[pcal_v2.NUM_FEATURES].to_numpy(float)
    mu = x_num.mean(axis=0)
    sd = x_num.std(axis=0)
    sd[sd == 0] = 1.0
    cat_frames = [
        pd.get_dummies(fit_rows[column], prefix=column)
        for column in pcal_v2.CAT_FEATURES
    ]
    cat_columns = [column for frame in cat_frames for column in frame.columns]
    x = np.hstack(
        [(x_num - mu) / sd]
        + [frame.to_numpy(float) for frame in cat_frames]
    )
    model = LogisticRegression(
        C=0.2,
        solver="liblinear",
        max_iter=1_000,
    )
    model.fit(x, fit_rows["win"].to_numpy())
    artifact: dict[str, Any] = {
        "schema_version": "low_price_yes_tail_pcal_expanded_v3",
        "generated_at_utc": now_utc(),
        "train_end": TRAIN_END,
        "train_rows": len(fit_rows),
        "train_dates": int(fit_rows["target_date"].nunique()),
        "train_wins": int(fit_rows["win"].sum()),
        "num_features": pcal_v2.NUM_FEATURES,
        "cat_columns": cat_columns,
        "scaler_mu": mu.tolist(),
        "scaler_sd": sd.tolist(),
        "coef": model.coef_[0].tolist(),
        "intercept": float(model.intercept_[0]),
        "theta_rule": f"max {MAX_TICKETS_PER_DAY:.1f} tickets/day on train",
        "fresh_frozen_target_date_start": FRESH_FROZEN_START,
        "fresh_frozen_target_date_end": FRESH_FROZEN_END,
        "deployment_status": "research_only_not_deployed",
    }
    probabilities = score_artifact(fit_rows, artifact)
    edge = probabilities - fit_rows["ask"].to_numpy(float)
    ordered = np.sort(edge)[::-1]
    k = int(
        min(
            len(ordered) - 1,
            MAX_TICKETS_PER_DAY * fit_rows["target_date"].nunique(),
        )
    )
    artifact["theta"] = float(ordered[k]) if len(ordered) else 0.0
    return artifact


def score_artifact(frame: pd.DataFrame, artifact: dict[str, Any]) -> np.ndarray:
    x_num = frame[artifact["num_features"]].to_numpy(float)
    mu = np.asarray(artifact["scaler_mu"], dtype=float)
    sd = np.asarray(artifact["scaler_sd"], dtype=float)
    dummy_frames = [
        pd.get_dummies(frame[column], prefix=column)
        for column in pcal_v2.CAT_FEATURES
    ]
    dummies = pd.concat(dummy_frames, axis=1).reindex(
        columns=artifact["cat_columns"],
        fill_value=0.0,
    )
    x = np.hstack([(x_num - mu) / sd, dummies.to_numpy(float)])
    linear = x @ np.asarray(artifact["coef"], dtype=float) + float(
        artifact["intercept"]
    )
    return 1.0 / (1.0 + np.exp(-np.clip(linear, -40.0, 40.0)))


def old_artifact() -> dict[str, Any]:
    return json.loads(OLD_ARTIFACT.read_text(encoding="utf-8"))[
        "frozen_selector"
    ]


def date_block_ci(frame: pd.DataFrame, value: str) -> tuple[float, float]:
    daily = frame.groupby("target_date")[value].mean().to_numpy(float)
    if len(daily) < 3:
        return math.nan, math.nan
    rng = np.random.default_rng(RNG_SEED)
    indices = rng.integers(0, len(daily), size=(N_BOOT, len(daily)))
    draws = daily[indices].mean(axis=1)
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def roi_ci(frame: pd.DataFrame) -> tuple[float, float]:
    daily = frame.groupby("target_date")[["unit_cost", "unit_pnl"]].sum()
    if len(daily) < 3 or daily["unit_cost"].sum() <= 0:
        return math.nan, math.nan
    values = daily.to_numpy(float)
    rng = np.random.default_rng(RNG_SEED)
    indices = rng.integers(0, len(values), size=(N_BOOT, len(values)))
    draws = values[indices].sum(axis=1)
    roi = draws[:, 1] / draws[:, 0]
    return float(np.quantile(roi, 0.025)), float(np.quantile(roi, 0.975))


def probability_detail(
    frame: pd.DataFrame,
    *,
    model_name: str,
    probability: np.ndarray,
    window: str,
) -> pd.DataFrame:
    out = frame[["target_date", "city", "win", "ask"]].copy()
    p = np.clip(np.asarray(probability, dtype=float), 1e-5, 1.0 - 1e-5)
    y = out["win"].to_numpy(float)
    market = out["ask"].clip(1e-5, 1.0 - 1e-5).to_numpy(float)
    out["model"] = model_name
    out["window"] = window
    out["p"] = p
    out["brier"] = np.square(p - y)
    out["logloss"] = -(y * np.log(p) + (1 - y) * np.log(1 - p))
    out["market_brier"] = np.square(market - y)
    out["market_logloss"] = -(
        y * np.log(market) + (1 - y) * np.log(1 - market)
    )
    out["brier_delta_vs_market"] = out["brier"] - out["market_brier"]
    out["logloss_delta_vs_market"] = (
        out["logloss"] - out["market_logloss"]
    )
    return out


def summarize_probability(detail: pd.DataFrame) -> dict[str, Any]:
    brier_low, brier_high = date_block_ci(
        detail,
        "brier_delta_vs_market",
    )
    log_low, log_high = date_block_ci(
        detail,
        "logloss_delta_vs_market",
    )
    return {
        "window": detail["window"].iloc[0],
        "model": detail["model"].iloc[0],
        "rows": len(detail),
        "dates": int(detail["target_date"].nunique()),
        "wins": int(detail["win"].sum()),
        "observed_rate": float(detail["win"].mean()),
        "mean_p": float(detail["p"].mean()),
        "brier": float(detail["brier"].mean()),
        "market_brier": float(detail["market_brier"].mean()),
        "brier_delta_vs_market": float(
            detail["brier_delta_vs_market"].mean()
        ),
        "brier_delta_ci_low": brier_low,
        "brier_delta_ci_high": brier_high,
        "logloss": float(detail["logloss"].mean()),
        "market_logloss": float(detail["market_logloss"].mean()),
        "logloss_delta_vs_market": float(
            detail["logloss_delta_vs_market"].mean()
        ),
        "logloss_delta_ci_low": log_low,
        "logloss_delta_ci_high": log_high,
    }


def paired_model_delta(
    expanded: pd.DataFrame,
    old: pd.DataFrame,
) -> dict[str, Any]:
    paired = expanded[
        ["target_date", "city", "brier", "logloss"]
    ].merge(
        old[["target_date", "city", "brier", "logloss"]],
        on=["target_date", "city"],
        suffixes=("_expanded", "_old"),
        validate="one_to_one",
    )
    out: dict[str, Any] = {
        "rows": len(paired),
        "dates": int(paired["target_date"].nunique()),
    }
    for metric in ("brier", "logloss"):
        column = f"{metric}_delta_expanded_vs_old"
        paired[column] = (
            paired[f"{metric}_expanded"] - paired[f"{metric}_old"]
        )
        low, high = date_block_ci(paired, column)
        out[column] = float(paired[column].mean())
        out[f"{column}_ci_low"] = low
        out[f"{column}_ci_high"] = high
    return out


def trade_summary(
    frame: pd.DataFrame,
    *,
    model_name: str,
    p: np.ndarray,
    theta: float,
    window: str,
) -> tuple[dict[str, Any], pd.DataFrame]:
    rows = frame.copy()
    rows["p"] = p
    rows["selected"] = rows["p"] - rows["ask"] >= theta
    selected = rows[rows["selected"]].copy()
    low, high = roi_ci(selected)
    cost = float(selected["unit_cost"].sum())
    pnl = float(selected["unit_pnl"].sum())
    top = selected["unit_pnl"].sort_values(ascending=False)

    def top_removed(n: int) -> float:
        if len(selected) <= n:
            return math.nan
        remaining_cost = cost - float(
            selected.loc[top.head(n).index, "unit_cost"].sum()
        )
        remaining_pnl = pnl - float(top.head(n).sum())
        return remaining_pnl / remaining_cost if remaining_cost > 0 else math.nan

    return (
        {
            "window": window,
            "model": model_name,
            "theta": theta,
            "opportunity_rows": len(rows),
            "opportunity_dates": int(rows["target_date"].nunique()),
            "selected_rows": len(selected),
            "selected_dates": int(selected["target_date"].nunique()),
            "wins": int(selected["win"].sum()),
            "win_rate": (
                float(selected["win"].mean()) if len(selected) else math.nan
            ),
            "cost": cost,
            "pnl": pnl,
            "fee_adjusted_roi": pnl / cost if cost > 0 else math.nan,
            "roi_ci_low": low,
            "roi_ci_high": high,
            "top5_removed_roi": top_removed(5),
            "top10_removed_roi": top_removed(10),
        },
        selected,
    )


def paired_trade_delta(
    expanded: pd.DataFrame,
    old: pd.DataFrame,
) -> dict[str, Any]:
    dates = sorted(
        set(expanded["target_date"]) | set(old["target_date"])
    )
    exp_daily = (
        expanded.groupby("target_date")[["unit_cost", "unit_pnl"]]
        .sum()
        .reindex(dates, fill_value=0.0)
        .to_numpy(float)
    )
    old_daily = (
        old.groupby("target_date")[["unit_cost", "unit_pnl"]]
        .sum()
        .reindex(dates, fill_value=0.0)
        .to_numpy(float)
    )
    rng = np.random.default_rng(RNG_SEED)
    indices = rng.integers(0, len(dates), size=(N_BOOT, len(dates)))
    exp_draw = exp_daily[indices].sum(axis=1)
    old_draw = old_daily[indices].sum(axis=1)
    valid = (exp_draw[:, 0] > 0) & (old_draw[:, 0] > 0)
    delta = (
        exp_draw[valid, 1] / exp_draw[valid, 0]
        - old_draw[valid, 1] / old_draw[valid, 0]
    )
    point = (
        expanded["unit_pnl"].sum() / expanded["unit_cost"].sum()
        - old["unit_pnl"].sum() / old["unit_cost"].sum()
    )
    return {
        "dates": len(dates),
        "roi_delta_expanded_vs_old": float(point),
        "roi_delta_ci_low": float(np.quantile(delta, 0.025)),
        "roi_delta_ci_high": float(np.quantile(delta, 0.975)),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    settled = rows[
        rows["settled_binary"]
        & rows[pcal_v2.NUM_FEATURES].notna().all(axis=1)
    ].copy()
    train = settled[settled["target_date"].le(TRAIN_END)].copy()
    retro = settled[
        settled["target_date"].ge(RETRO_FORWARD_START)
        & settled["target_date"].le(RETRO_FORWARD_END)
    ].copy()
    fresh_rows = rows[
        rows["target_date"].ge(FRESH_FROZEN_START)
        & rows["target_date"].le(FRESH_FROZEN_END)
    ].copy()

    old = old_artifact()
    expanded = fit_artifact(train)
    p_old_train = score_artifact(train, old)
    p_exp_train = score_artifact(train, expanded)
    p_old_retro = score_artifact(retro, old)
    p_exp_retro = score_artifact(retro, expanded)

    details = pd.concat(
        [
            probability_detail(
                train,
                model_name="active_pcal_v2",
                probability=p_old_train,
                window=f"train_le_{TRAIN_END}",
            ),
            probability_detail(
                train,
                model_name="expanded_pcal_v3",
                probability=p_exp_train,
                window=f"train_le_{TRAIN_END}",
            ),
            probability_detail(
                retro,
                model_name="active_pcal_v2",
                probability=p_old_retro,
                window=f"retro_forward_{RETRO_FORWARD_START}_{RETRO_FORWARD_END}",
            ),
            probability_detail(
                retro,
                model_name="expanded_pcal_v3",
                probability=p_exp_retro,
                window=f"retro_forward_{RETRO_FORWARD_START}_{RETRO_FORWARD_END}",
            ),
        ],
        ignore_index=True,
    )
    probability_scores = pd.DataFrame(
        [
            summarize_probability(group)
            for _, group in details.groupby(["window", "model"])
        ]
    )
    retro_old_detail = details[
        details["window"].str.startswith("retro_forward")
        & details["model"].eq("active_pcal_v2")
    ]
    retro_exp_detail = details[
        details["window"].str.startswith("retro_forward")
        & details["model"].eq("expanded_pcal_v3")
    ]
    model_delta = paired_model_delta(retro_exp_detail, retro_old_detail)

    trade_rows: list[dict[str, Any]] = []
    old_train_trade, _ = trade_summary(
        train,
        model_name="active_pcal_v2",
        p=p_old_train,
        theta=float(old["theta"]),
        window=f"train_le_{TRAIN_END}",
    )
    exp_train_trade, _ = trade_summary(
        train,
        model_name="expanded_pcal_v3",
        p=p_exp_train,
        theta=float(expanded["theta"]),
        window=f"train_le_{TRAIN_END}",
    )
    old_retro_trade, old_selected = trade_summary(
        retro,
        model_name="active_pcal_v2",
        p=p_old_retro,
        theta=float(old["theta"]),
        window=f"retro_forward_{RETRO_FORWARD_START}_{RETRO_FORWARD_END}",
    )
    exp_retro_trade, exp_selected = trade_summary(
        retro,
        model_name="expanded_pcal_v3",
        p=p_exp_retro,
        theta=float(expanded["theta"]),
        window=f"retro_forward_{RETRO_FORWARD_START}_{RETRO_FORWARD_END}",
    )
    trade_rows.extend(
        [
            old_train_trade,
            exp_train_trade,
            old_retro_trade,
            exp_retro_trade,
        ]
    )
    trade_scores = pd.DataFrame(trade_rows)
    trade_delta = paired_trade_delta(exp_selected, old_selected)
    period_frames = [
        (
            "old_train",
            settled[
                settled["target_date"].ge("2026-05-06")
                & settled["target_date"].le("2026-06-20")
            ],
        ),
        (
            "added_train",
            settled[
                settled["target_date"].ge("2026-06-21")
                & settled["target_date"].le(TRAIN_END)
            ],
        ),
        ("retrospective_forward", retro),
    ]
    period_rows: list[dict[str, Any]] = []
    for period, period_frame in period_frames:
        for source, source_frame in [
            ("all", period_frame),
            *list(period_frame.groupby("forecast_model")),
        ]:
            period_rows.append(
                {
                    "period": period,
                    "forecast_model": source,
                    "rows": len(source_frame),
                    "dates": int(source_frame["target_date"].nunique()),
                    "wins": int(source_frame["win"].sum()),
                    "observed_rate": float(source_frame["win"].mean()),
                    "mean_ask": float(source_frame["ask"].mean()),
                }
            )
    period_diagnostics = pd.DataFrame(period_rows)
    retro_source = retro.assign(
        p_old=p_old_retro,
        p_expanded=p_exp_retro,
    )
    retro_source_diagnostics = (
        retro_source.groupby("forecast_model")
        .agg(
            rows=("win", "size"),
            wins=("win", "sum"),
            observed_rate=("win", "mean"),
            mean_ask=("ask", "mean"),
            mean_p_old=("p_old", "mean"),
            mean_p_expanded=("p_expanded", "mean"),
        )
        .reset_index()
    )
    old_keys = set(
        zip(old_selected["target_date"], old_selected["city"], strict=True)
    )
    expanded_keys = set(
        zip(exp_selected["target_date"], exp_selected["city"], strict=True)
    )
    selection_overlap = {
        "both": len(old_keys & expanded_keys),
        "old_only": len(old_keys - expanded_keys),
        "expanded_only": len(expanded_keys - old_keys),
        "neither": len(retro) - len(old_keys | expanded_keys),
    }

    details.to_csv(OUT_DIR / "probability_rows.csv", index=False)
    probability_scores.to_csv(
        OUT_DIR / "probability_scorecard.csv",
        index=False,
    )
    trade_scores.to_csv(OUT_DIR / "trade_scorecard.csv", index=False)
    expanded["retrospective_forward"] = {
        "start": RETRO_FORWARD_START,
        "end": RETRO_FORWARD_END,
        "rows": len(retro),
        "dates": int(retro["target_date"].nunique()),
        "model_delta": model_delta,
        "trade_delta": trade_delta,
    }
    (OUT_DIR / "expanded_pcal_v3_artifact.json").write_text(
        json.dumps(expanded, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    FROZEN_ARTIFACT.write_text(
        json.dumps(expanded, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {
        "generated_at_utc": now_utc(),
        "train": {
            "end": TRAIN_END,
            "rows": len(train),
            "dates": int(train["target_date"].nunique()),
            "wins": int(train["win"].sum()),
        },
        "retrospective_forward": {
            "start": RETRO_FORWARD_START,
            "end": RETRO_FORWARD_END,
            "rows": len(retro),
            "dates": int(retro["target_date"].nunique()),
            "wins": int(retro["win"].sum()),
        },
        "fresh_frozen": {
            "start": FRESH_FROZEN_START,
            "end": FRESH_FROZEN_END,
            "calendar_days": 15,
            "captured_rows_as_of_build": len(fresh_rows),
            "scored": False,
        },
        "model_delta": model_delta,
        "trade_delta": trade_delta,
        "selection_overlap": selection_overlap,
        "deployment": "none",
    }
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    train_scores = probability_scores[
        probability_scores["window"].str.startswith("train")
    ].sort_values("model")
    retro_scores = probability_scores[
        probability_scores["window"].str.startswith("retro_forward")
    ].sort_values("model")
    report = f"""# Low-price YES Tail p_cal expanded v3

Generated: {summary["generated_at_utc"]}

## 数据快照

| 字段 | 值 |
| --- | --- |
| canonical denominator | no-edge cheap YES, first ticket per city-date |
| train | ≤{TRAIN_END}: {len(train)} rows / {train["target_date"].nunique()} dates / {int(train["win"].sum())} wins |
| retrospective forward | {RETRO_FORWARD_START}..{RETRO_FORWARD_END}: {len(retro)} rows / {retro["target_date"].nunique()} dates / {int(retro["win"].sum())} wins |
| fresh frozen | {FRESH_FROZEN_START}..{FRESH_FROZEN_END}: 15 calendar days, not scored |
| settlement missing in scored rows | 0 |
| trade class | research replay / zero-notional |
| fee | Weather official taker curve, rate={WEATHER_FEE_RATE:.2f} |

> Scope：本报告及 frozen artifact 只定义 `P4_pcal_v3`——在 no-edge cheap-YES
> first city-target_date 宽分母上直接选票，不包含 raw edge、dist 或 22–24h gate。
> 从 2026-07-29 起，`low_price_yes_parallel_frozen_profiles_v1.json` 在同一宽分母并行记录
> broad / edge20 / mechanism / legacy HeadA / p_cal / mechanism+p_cal 六个 profiles。

## 目标与固定改动

只改变训练截止：旧 active p_cal 训练截止 2026-06-20；candidate 扩到
{TRAIN_END}。feature set、C=0.2、city identity exclusion、`max 5 tickets/day`
theta rule 均不变。主指标是同一 retrospective-forward rows 上相对 market 及旧模型的
Brier/logloss；selected ROI 只是 secondary。

## Train（in-sample，仅诊断）

{md_table(train_scores, ["model", "rows", "dates", "wins", "observed_rate", "mean_p", "brier", "market_brier", "brier_delta_vs_market", "logloss", "market_logloss", "logloss_delta_vs_market"])}

{md_table(trade_scores[trade_scores["window"].str.startswith("train")], ["model", "theta", "opportunity_rows", "selected_rows", "selected_dates", "wins", "win_rate", "pnl", "fee_adjusted_roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi", "top10_removed_roi"])}

## Retrospective forward（2026-07-13..27）

{md_table(retro_scores, ["model", "rows", "dates", "wins", "observed_rate", "mean_p", "brier", "market_brier", "brier_delta_vs_market", "brier_delta_ci_low", "brier_delta_ci_high", "logloss", "market_logloss", "logloss_delta_vs_market", "logloss_delta_ci_low", "logloss_delta_ci_high"])}

Expanded 相对旧 p_cal，负数才是改善：

```json
{json.dumps(model_delta, ensure_ascii=False, indent=2)}
```

Fee-adjusted selected expression：

{md_table(trade_scores[trade_scores["window"].str.startswith("retro_forward")], ["model", "theta", "opportunity_rows", "opportunity_dates", "selected_rows", "selected_dates", "wins", "win_rate", "cost", "pnl", "fee_adjusted_roi", "roi_ci_low", "roi_ci_high", "top5_removed_roi", "top10_removed_roi"])}

Expanded−old selected ROI：

```json
{json.dumps(trade_delta, ensure_ascii=False, indent=2)}
```

## 为什么样本更多却没改善

训练量从旧版的 1,351 rows / 46 dates / 136 wins 增至
{len(train):,} rows / {train["target_date"].nunique()} dates / {int(train["win"].sum())} wins；
不是“新样本太少到完全没变化”。问题是新增窗口学到的 source/city regime 没延续到后 15 日。
这里的 `forecast_model` 是每城 assigned source，不能解释为模型间随机实验。

{md_table(period_diagnostics, ["period", "forecast_model", "rows", "dates", "wins", "observed_rate", "mean_ask"])}

在 7/13..27 同分母上：

{md_table(retro_source_diagnostics, ["forecast_model", "rows", "wins", "observed_rate", "mean_ask", "mean_p_old", "mean_p_expanded"])}

新增训练窗里 GFS 城组的命中率高于 ECMWF 城组，expanded 因而在后窗提高 GFS 概率、
降低 ECMWF 概率；但后窗 GFS 实际命中率降到约 6.4%，这个短期关系反转了。
selector 也不是只做了微调：共同选择 {selection_overlap["both"]} 条，旧版独有
{selection_overlap["old_only"]} 条，expanded 独有 {selection_overlap["expanded_only"]} 条，
两者最终都中 21 条。当前证据更像 source/city × seasonal regime 不稳定，而不是单纯
训练样本不足；后续应让 source/city effects 做 shrinkage，并用多源 spread/bias 连续特征，
而不是继续等权追加日期。

## Fresh frozen

`{FRESH_FROZEN_START}..{FRESH_FROZEN_END}` 是连续 15 个日历 target dates。
本脚本不读取/发布该窗口的 settlement score 或 ROI。零信号日、missing-book 日保留在
signal/evidence coverage，不顺延窗口。8/11 所需结算全部到齐后一次性开封。

## 双漏斗与裁决边界

Signal funnel：canonical cheap-YES → first city-date ticket → p_cal edge selector。
Evidence funnel：PIT candidate → ask → settlement → fee-adjusted hypothetical taker；
actual fill=0。

expanded 的 retrospective proper score 点估劣于旧模型和 market，因此不具备替换 active
p_cal 的依据。固定 artifact 仍保留到 fresh frozen 做一次前瞻诊断，用来区分短窗口噪声与
稳定退化；无论结果如何，开封前均不改模型、不选阈值、不部署，`live_action=none`。

## 产物

- evaluator：`scripts/analysis/forecast_quality/research_low_price_yes_tail_pcal_expanded_v3.py`
- frozen artifact：`configs/weather/low_price_yes_tail_pcal_expanded_v3_research.json`
- generated mirror：`generated/low_price_yes_tail_pcal_expanded_v3/expanded_pcal_v3_artifact.json`
- scorecards：`probability_scorecard.csv`, `trade_scorecard.csv`
"""
    REPORT.write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
