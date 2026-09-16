#!/usr/bin/env python3
"""Compact market-residual model and current/d1/no-trade router.

This is deliberately narrower than the regime atlas model.  It uses the
market's current and d1 rung probabilities as the prior, then asks whether a
small set of path features improves the three-state distribution:

    final=current, final=d1, final=d2+

All historical predictions are date-expanding OOF.  The frozen 218-row
d1>=0.80 cohort and clean live rows are evaluation-only.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analysis.market_structure_edge import (
    research_d1_overshoot_hazard_v1 as hz,
)


ROOT = hz.ROOT
OUTPUT = (
    ROOT
    / "docs/analysis/2026-07/generated/market_residual_exact_router_v1"
)
REPORT = (
    ROOT
    / "docs/analysis/2026-07/2026-07-23-research-market-residual-exact-router-v1.md"
)

MIN_TRAIN_DATES = 12
MODEL_C = 0.03
EDGE_BUFFERS = [0.0, 0.01, 0.02, 0.03]
SEED = 20260723
BOOTSTRAP_DRAWS = 4000

MARKET_FEATURES = ["two_rung_h0_logit", "two_rung_h1_logit"]
COMPACT_FEATURES = [
    "forecast_bust_native",
    "forecast_runway_native",
    "forecast_ceiling_margin_to_d2",
    "hours_to_forecast_peak",
    "hours_past_forecast_peak",
    "positive_trend_1h_f",
    "negative_trend_1h_f",
    "positive_trend_3h_f",
    "post_high_confirmed_min",
    "high_clock_censored",
    "decline_native",
    "relative_humidity_pct",
    "wind_speed_kt",
    "sky_cover_code",
    "obs_age_min",
    "d1_required_gap_native",
    "d2_required_gap_native",
    "forecast_bust_x_positive_trend",
]
CATEGORICAL_FEATURES = ["city", "forecast_assigned_model", "unit"]
MODEL_SPECS = {
    "market_calibrated": (MARKET_FEATURES, []),
    "market_plus_compact": (MARKET_FEATURES + COMPACT_FEATURES, []),
    "market_plus_compact_city": (
        MARKET_FEATURES + COMPACT_FEATURES,
        CATEGORICAL_FEATURES,
    ),
}


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "无 rows。"
    display = frame.copy()
    for column in display.select_dtypes(include=["float"]).columns:
        display[column] = display[column].map(
            lambda value: "" if pd.isna(value) else f"{value:.4f}"
        )
    columns = [str(column) for column in display.columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join(["---"] * len(columns)) + " |",
    ]
    for row in display.astype(str).itertuples(index=False, name=None):
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _pipeline(numeric: list[str], categorical: list[str]) -> Pipeline:
    transformers: list[tuple[str, Any, list[str]]] = [
        (
            "numeric",
            Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
                    ("scale", StandardScaler()),
                ]
            ),
            numeric,
        )
    ]
    if categorical:
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        (
                            "onehot",
                            OneHotEncoder(handle_unknown="ignore"),
                        ),
                    ]
                ),
                categorical,
            )
        )
    return Pipeline(
        [
            ("features", ColumnTransformer(transformers)),
            (
                "model",
                LogisticRegression(
                    C=MODEL_C,
                    solver="liblinear",
                    max_iter=1000,
                    random_state=SEED,
                ),
            ),
        ]
    )


def _positive_probability(model: Pipeline, rows: pd.DataFrame) -> np.ndarray:
    probabilities = model.predict_proba(rows)
    classes = list(model.named_steps["model"].classes_)
    return probabilities[:, classes.index(1)]


@dataclass
class SequentialModel:
    numeric: list[str]
    categorical: list[str]
    h0: Pipeline | None = None
    h1: Pipeline | None = None

    def fit(self, rows: pd.DataFrame) -> "SequentialModel":
        self.h0 = _pipeline(self.numeric, self.categorical)
        self.h0.fit(rows, rows["reach_d1"].astype(int))
        reached = rows[rows["reach_d1"].eq(1)]
        if reached["reach_d2"].nunique() < 2:
            raise ValueError("d2 conditional head has fewer than two classes")
        self.h1 = _pipeline(self.numeric, self.categorical)
        self.h1.fit(reached, reached["reach_d2"].astype(int))
        return self

    def predict(self, rows: pd.DataFrame) -> pd.DataFrame:
        if self.h0 is None or self.h1 is None:
            raise RuntimeError("model is not fitted")
        h0 = np.clip(_positive_probability(self.h0, rows), 1e-6, 1 - 1e-6)
        h1 = np.clip(_positive_probability(self.h1, rows), 1e-6, 1 - 1e-6)
        return pd.DataFrame(
            {
                "p_stall": 1.0 - h0,
                "p_exact": h0 * (1.0 - h1),
                "p_overshoot": h0 * h1,
            },
            index=rows.index,
        )


def engineer(rows: pd.DataFrame) -> pd.DataFrame:
    frame = rows.copy()
    current_mid = 1.0 - (
        pd.to_numeric(frame["current_no_bid"], errors="coerce")
        + pd.to_numeric(frame["current_no_ask"], errors="coerce")
    ) / 2.0
    d1_mid = 1.0 - (
        pd.to_numeric(frame["d1_no_bid"], errors="coerce")
        + pd.to_numeric(frame["d1_no_ask"], errors="coerce")
    ) / 2.0
    tail = (1.0 - current_mid - d1_mid).clip(lower=0.005)
    total = current_mid.clip(lower=0.005) + d1_mid.clip(lower=0.005) + tail
    frame["two_rung_p_stall"] = current_mid.clip(lower=0.005) / total
    frame["two_rung_p_exact"] = d1_mid.clip(lower=0.005) / total
    frame["two_rung_p_overshoot"] = tail / total
    h0 = 1.0 - frame["two_rung_p_stall"]
    h1 = frame["two_rung_p_overshoot"] / (
        frame["two_rung_p_exact"] + frame["two_rung_p_overshoot"]
    )
    frame["two_rung_h0_logit"] = hz._logit(h0)
    frame["two_rung_h1_logit"] = hz._logit(h1)
    frame["two_rung_ready"] = (
        frame[["current_no_bid", "current_no_ask", "d1_no_bid", "d1_no_ask"]]
        .notna()
        .all(axis=1)
    )

    gap = pd.to_numeric(frame["forecast_gap_to_running_native"], errors="coerce")
    peak_delta = pd.to_numeric(
        frame["forecast_peak_delta_hours_local"], errors="coerce"
    )
    trend1 = pd.to_numeric(frame["temp_trend_1h_f"], errors="coerce")
    trend3 = pd.to_numeric(frame["temp_trend_3h_f"], errors="coerce")
    since_high = pd.to_numeric(
        frame["minutes_since_running_max"], errors="coerce"
    )
    obs_age = pd.to_numeric(frame["obs_age_min"], errors="coerce")
    decline = pd.to_numeric(frame["decline_native"], errors="coerce")
    frame["forecast_bust_native"] = (-gap).clip(lower=0)
    frame["forecast_runway_native"] = gap.clip(lower=0)
    frame["hours_to_forecast_peak"] = (-peak_delta).clip(lower=0)
    frame["hours_past_forecast_peak"] = peak_delta.clip(lower=0)
    frame["positive_trend_1h_f"] = trend1.clip(lower=0)
    frame["negative_trend_1h_f"] = (-trend1).clip(lower=0)
    frame["positive_trend_3h_f"] = trend3.clip(lower=0)
    frame["post_high_confirmed_min"] = (since_high - obs_age).clip(lower=0)
    frame["high_clock_censored"] = (
        since_high.notna()
        & obs_age.notna()
        & since_high.sub(obs_age).abs().le(2.0)
        & decline.fillna(0).le(0)
    ).astype(int)
    frame["forecast_bust_x_positive_trend"] = (
        frame["forecast_bust_native"] * frame["positive_trend_1h_f"]
    )
    frame["current_yes_ask_exec"] = pd.to_numeric(
        frame["current_yes_ask"], errors="coerce"
    )
    frame["current_cost"] = frame["current_yes_ask_exec"] + hz._weather_fee(
        frame["current_yes_ask_exec"]
    )
    frame["d1_cost"] = pd.to_numeric(frame["d1_yes_ask_exec"], errors="coerce")
    frame["d1_cost"] = frame["d1_cost"] + hz._weather_fee(frame["d1_cost"])
    return frame


def expanding_oof(rows: pd.DataFrame) -> pd.DataFrame:
    eligible = rows[rows["model_eligible"] & rows["two_rung_ready"]].copy()
    dates = sorted(eligible["target_date"].unique())
    outputs: list[pd.DataFrame] = []
    for index, date in enumerate(dates):
        if index < MIN_TRAIN_DATES:
            continue
        train = eligible[eligible["target_date"].lt(date)]
        test = eligible[eligible["target_date"].eq(date)]
        if (
            train["reach_d1"].nunique() < 2
            or train.loc[train["reach_d1"].eq(1), "reach_d2"].nunique() < 2
        ):
            continue
        raw = test[hz.KEY + ["label"]].copy()
        raw["model"] = "market_two_rung_raw"
        raw["p_stall"] = test["two_rung_p_stall"].to_numpy()
        raw["p_exact"] = test["two_rung_p_exact"].to_numpy()
        raw["p_overshoot"] = test["two_rung_p_overshoot"].to_numpy()
        outputs.append(raw)
        if test["market_score_ready"].eq(True).any():
            full = test[test["market_score_ready"].eq(True)]
            raw_full = full[hz.KEY + ["label"]].copy()
            raw_full["model"] = "market_full_ladder_raw"
            raw_full["p_stall"] = full["market_p_stall"].to_numpy()
            raw_full["p_exact"] = full["market_p_exact"].to_numpy()
            raw_full["p_overshoot"] = full["market_p_overshoot"].to_numpy()
            outputs.append(raw_full)
        for name, (numeric, categorical) in MODEL_SPECS.items():
            model = SequentialModel(numeric, categorical).fit(train)
            block = test[hz.KEY + ["label"]].copy()
            block["model"] = name
            prediction = model.predict(test)
            for column in prediction:
                block[column] = prediction[column].to_numpy()
            outputs.append(block)
    return pd.concat(outputs, ignore_index=True)


def score_rows(rows: pd.DataFrame) -> pd.DataFrame:
    scored = rows.copy()
    class_index = scored["label"].map(dict(zip(hz.CLASSES, range(3))))
    probabilities = scored[["p_stall", "p_exact", "p_overshoot"]].to_numpy()
    truth = np.eye(3)[class_index.to_numpy()]
    scored["logloss"] = -np.log(
        np.clip(probabilities[np.arange(len(scored)), class_index], 1e-9, 1)
    )
    scored["brier"] = np.square(probabilities - truth).sum(axis=1)
    return scored


def paired_score_summary(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for baseline in ["market_two_rung_raw", "market_full_ladder_raw"]:
        base = scored[scored["model"].eq(baseline)][
            hz.KEY + ["logloss", "brier"]
        ].rename(columns={"logloss": "base_logloss", "brier": "base_brier"})
        for model in MODEL_SPECS:
            candidate = scored[scored["model"].eq(model)].merge(
                base, on=hz.KEY, validate="one_to_one"
            )
            if candidate.empty:
                continue
            daily = candidate.groupby("target_date").agg(
                logloss=("logloss", "mean"),
                brier=("brier", "mean"),
                base_logloss=("base_logloss", "mean"),
                base_brier=("base_brier", "mean"),
            )
            daily["ll_delta"] = daily["logloss"] - daily["base_logloss"]
            daily["br_delta"] = daily["brier"] - daily["base_brier"]
            rng = np.random.default_rng(SEED + len(rows))
            ll_boot = np.empty(BOOTSTRAP_DRAWS)
            br_boot = np.empty(BOOTSTRAP_DRAWS)
            values = daily[["ll_delta", "br_delta"]].to_numpy()
            for draw in range(BOOTSTRAP_DRAWS):
                sample = values[rng.integers(0, len(values), len(values))]
                ll_boot[draw] = sample[:, 0].mean()
                br_boot[draw] = sample[:, 1].mean()
            rows.append(
                {
                    "baseline": baseline,
                    "model": model,
                    "rows": len(candidate),
                    "dates": len(daily),
                    "baseline_logloss": daily["base_logloss"].mean(),
                    "model_logloss": daily["logloss"].mean(),
                    "logloss_delta": daily["ll_delta"].mean(),
                    "logloss_delta_ci_low": np.quantile(ll_boot, 0.025),
                    "logloss_delta_ci_high": np.quantile(ll_boot, 0.975),
                    "baseline_brier": daily["base_brier"].mean(),
                    "model_brier": daily["brier"].mean(),
                    "brier_delta": daily["br_delta"].mean(),
                    "brier_delta_ci_low": np.quantile(br_boot, 0.025),
                    "brier_delta_ci_high": np.quantile(br_boot, 0.975),
                }
            )
    return pd.DataFrame(rows)


def attach_frozen(
    predictions: pd.DataFrame, first: pd.DataFrame, states: pd.DataFrame
) -> pd.DataFrame:
    state_columns = hz.KEY + ["current_cost", "d1_cost", "model_eligible"]
    cohort = first.merge(
        states[state_columns],
        on=hz.KEY,
        how="left",
        validate="one_to_one",
    )
    cohort["d1_cost"] = cohort["cost"]
    return predictions.merge(
        cohort[
            hz.KEY
            + ["label", "current_cost", "d1_cost", "model_eligible"]
        ],
        on=hz.KEY + ["label"],
        validate="many_to_one",
    )


def route(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    detail_blocks = []
    summary = []
    for buffer in EDGE_BUFFERS:
        block = rows.copy()
        block["current_edge"] = block["p_stall"] - block["current_cost"]
        block["d1_edge"] = block["p_exact"] - block["d1_cost"]
        current_available = block["current_cost"].notna()
        choose_current = (
            current_available
            & block["current_edge"].gt(buffer)
            & block["current_edge"].ge(block["d1_edge"])
        )
        choose_d1 = (
            block["d1_cost"].notna()
            & block["d1_edge"].gt(buffer)
            & (~choose_current)
        )
        block["action"] = np.select(
            [choose_current, choose_d1],
            ["buy_current_yes", "buy_d1_yes"],
            default="no_trade",
        )
        block["selected_cost"] = np.select(
            [choose_current, choose_d1],
            [block["current_cost"], block["d1_cost"]],
            default=0.0,
        )
        block["selected_win"] = np.select(
            [choose_current, choose_d1],
            [
                block["label"].eq(hz.CLASSES[0]).astype(int),
                block["label"].eq(hz.CLASSES[1]).astype(int),
            ],
            default=0,
        )
        block["pnl"] = block["selected_win"] - block["selected_cost"]
        block["edge_buffer"] = buffer
        detail_blocks.append(block)
        for period, part in [
            ("all_oof", block),
            ("pre_forward", block[block["target_date"].lt(hz.FORWARD_START)]),
            (
                "historical_forward",
                block[block["target_date"].ge(hz.FORWARD_START)],
            ),
        ]:
            traded = part[part["action"].ne("no_trade")]
            cost = float(traded["selected_cost"].sum())
            summary.append(
                {
                    "model": str(block["model"].iloc[0]) if len(block) else "",
                    "edge_buffer": buffer,
                    "period": period,
                    "rows": len(part),
                    "trades": len(traded),
                    "current_trades": int(
                        traded["action"].eq("buy_current_yes").sum()
                    ),
                    "d1_trades": int(traded["action"].eq("buy_d1_yes").sum()),
                    "wins": int(traded["selected_win"].sum()),
                    "cost": cost,
                    "pnl": float(traded["pnl"].sum()),
                    "roi": float(traded["pnl"].sum() / cost)
                    if cost > 0
                    else math.nan,
                }
            )
    return pd.concat(detail_blocks, ignore_index=True), pd.DataFrame(summary)


def frozen_router_summary(rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    details = []
    summaries = []
    for model, group in rows.groupby("model"):
        detail, summary = route(group)
        details.append(detail)
        summaries.append(summary)
    return pd.concat(details, ignore_index=True), pd.concat(
        summaries, ignore_index=True
    )


def live_predictions(
    states: pd.DataFrame, live: pd.DataFrame
) -> pd.DataFrame:
    if live.empty:
        return live
    live = live.copy()
    live["forecast_assigned_model"] = live["city"].map(hz.CITY_MODEL).fillna("gfs")
    # Full ladder is available for every retained clean-live row.  Mirror it
    # into the two-rung prior so live tests the residual features, not an
    # archive-specific missing-quote reconstruction.
    live["two_rung_p_stall"] = live["market_p_stall"]
    live["two_rung_p_exact"] = live["market_p_exact"]
    live["two_rung_p_overshoot"] = live["market_p_overshoot"]
    live["two_rung_h0_logit"] = hz._logit(1.0 - live["market_p_stall"])
    live["two_rung_h1_logit"] = hz._logit(
        live["market_p_overshoot"]
        / (live["market_p_exact"] + live["market_p_overshoot"])
    )
    # engineer() needs quote columns; preserve the already-built prior.
    for column in [
        "current_no_bid",
        "current_no_ask",
        "current_yes_ask",
        "d1_no_bid",
        "d1_no_ask",
    ]:
        live[column] = math.nan
    live = engineer(live)
    live["two_rung_p_stall"] = live["market_p_stall"]
    live["two_rung_p_exact"] = live["market_p_exact"]
    live["two_rung_p_overshoot"] = live["market_p_overshoot"]
    live["two_rung_h0_logit"] = hz._logit(1.0 - live["market_p_stall"])
    live["two_rung_h1_logit"] = hz._logit(
        live["market_p_overshoot"]
        / (live["market_p_exact"] + live["market_p_overshoot"])
    )
    outputs = []
    raw = live[hz.KEY + ["label"]].copy()
    raw["model"] = "market_full_ladder_raw"
    raw["p_stall"] = live["market_p_stall"].to_numpy()
    raw["p_exact"] = live["market_p_exact"].to_numpy()
    raw["p_overshoot"] = live["market_p_overshoot"].to_numpy()
    outputs.append(raw)
    train = states[states["model_eligible"] & states["two_rung_ready"]]
    for name, (numeric, categorical) in MODEL_SPECS.items():
        model = SequentialModel(numeric, categorical).fit(train)
        block = live[hz.KEY + ["label"]].copy()
        block["model"] = name
        prediction = model.predict(live)
        for column in prediction:
            block[column] = prediction[column].to_numpy()
        outputs.append(block)
    return score_rows(pd.concat(outputs, ignore_index=True))


def write_report(
    path: Path,
    audit: dict[str, Any],
    score_summary: pd.DataFrame,
    frozen_summary: pd.DataFrame,
    live_scored: pd.DataFrame,
) -> None:
    best = score_summary.sort_values("logloss_delta").iloc[0]
    frozen_view = frozen_summary[
        frozen_summary["edge_buffer"].eq(0.02)
        & frozen_summary["period"].isin(["pre_forward", "historical_forward"])
    ]
    live_view = (
        live_scored.groupby("model")
        .agg(rows=("label", "size"), logloss=("logloss", "mean"), brier=("brier", "mean"))
        .reset_index()
    )
    text = f"""# Market residual exact router v1（2026-07-23）

## 结论

本轮已经形成可复跑的三动作候选：`buy_current_yes / buy_d1_yes / no_trade`。
它不是 hard regime filter；market 是 prior，forecast bust、剩余峰值窗口、
post-high confirmation/censoring 和路径趋势只修正 residual。

**Verdict: rejected_as_strategy。**完整 ladder 同分母上的所有 residual
模型都显著差于 market；冻结 first-signal 上还出现了错误的低价 current
YES 路由，`market_plus_compact` historical forward 9/9 全亏。该版本只保留
为 selection-shift negative control，不进入 shadow。

当前最好的 paired proper-score 结果是 `{best['model']}` 对
`{best['baseline']}`：date-equal logloss delta
`{best['logloss_delta']:+.4f}`，95% date bootstrap CI
`[{best['logloss_delta_ci_low']:+.4f}, {best['logloss_delta_ci_high']:+.4f}]`。
delta < 0 才代表优于 market。

## 固定口径

- 训练：历史机制 rows，按 target date expanding OOF；C={MODEL_C}，不使用 frozen/live 调参。
- 严格分母：完整 market ladder。
- 扩覆盖分母：current+d1 两档中价构造三态 prior；coverage gap 单列，不算策略过滤。
- 策略评估：冻结 218 个 `d1 YES mid >= 0.80` first signals。
- forward：`{hz.FORWARD_START}` 起历史 forward；clean live 只评分，不参与拟合。
- fee：`0.05*p*(1-p)`，动作必须有可执行 ask；edge buffer 预先列出，不按 forward 挑阈值。

## 覆盖

```json
{json.dumps(_json_ready(audit), ensure_ascii=False, indent=2)}
```

## Paired proper score

{_markdown_table(score_summary)}

## 冻结 first-signal router（edge buffer=0.02）

{_markdown_table(frozen_view)}

## Clean live probability score

{_markdown_table(live_view)}

## 策略定义与可行性

1. 先从盘口得到 `P(current), P(d1), P(d2+)`。
2. compact residual 更新三态概率；forecast 已被打穿且仍升温是 overshoot hazard，
   真正 post-high 的观测确认是 stall/exact 的证据，只有 obs-age 相等则标为 censored。
3. 分别计算 `P(current)-current ask-fee` 与 `P(d1)-d1 ask-fee`；
   最大值超过 edge buffer 才买，否则不交易。
4. city/source 只允许强收缩校准；若 paired score 没有稳定优于 market，就不把它放进候选。

本版本没有资格进入 zero-notional shadow，不改变 live 配置。
"""
    path.write_text(text, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    parser.add_argument("--report", type=Path, default=REPORT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    states_raw, source_audit = hz.build_historical_states(hz.ATLAS, hz.FACTORY)
    states = engineer(states_raw)
    first = hz.first_signal_rows(hz.FROZEN_FIRST)
    predictions = expanding_oof(states)
    scored = score_rows(predictions)
    score_summary = paired_score_summary(scored)
    frozen = attach_frozen(predictions, first, states)
    frozen_detail, frozen_summary = frozen_router_summary(frozen)
    city_family = (
        states[["city", "city_family"]]
        .dropna()
        .drop_duplicates("city")
        .set_index("city")["city_family"]
        .to_dict()
    )
    live = hz.build_live_rows(hz.LIVE_RAW, city_family, hz.DB_PATH)
    live_scored = live_predictions(states, live)
    audit = {
        **source_audit,
        "two_rung_model_rows": int(
            (states["model_eligible"] & states["two_rung_ready"]).sum()
        ),
        "two_rung_model_dates": int(
            states.loc[
                states["model_eligible"] & states["two_rung_ready"],
                "target_date",
            ].nunique()
        ),
        "oof_rows_by_model": scored.groupby("model").size().to_dict(),
        "frozen_rows": len(first),
        "frozen_state_matches": int(
            first.merge(
                states[hz.KEY + ["model_eligible"]],
                on=hz.KEY,
                how="left",
            )["model_eligible"].notna().sum()
        ),
        "frozen_oof_rows_by_model": frozen.groupby("model").size().to_dict(),
        "clean_live_rows": len(live),
    }
    states.loc[
        states["model_eligible"],
        hz.KEY
        + ["label", "two_rung_ready"]
        + MARKET_FEATURES
        + COMPACT_FEATURES,
    ].to_csv(args.output_dir / "mechanism_features.csv", index=False)
    scored.to_csv(args.output_dir / "historical_oof_predictions.csv", index=False)
    score_summary.to_csv(args.output_dir / "proper_score_summary.csv", index=False)
    frozen_detail.to_csv(args.output_dir / "frozen_router_detail.csv", index=False)
    frozen_summary.to_csv(args.output_dir / "frozen_router_summary.csv", index=False)
    live_scored.to_csv(args.output_dir / "clean_live_predictions.csv", index=False)
    (args.output_dir / "audit.json").write_text(
        json.dumps(_json_ready(audit), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(args.report, audit, score_summary, frozen_summary, live_scored)
    print(json.dumps(_json_ready(audit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
